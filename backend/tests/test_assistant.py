"""AI assistant (1.8.0-beta.1): settings, redaction, executor, search, tools, agent loop, plans."""
import json
import re
from pathlib import Path
from unittest import mock

import httpx
import pytest

from app.assistant.providers import ModelTurn


def _events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def _enable(client, auth_headers, **extra):
    body = {"enabled": True, "provider": "openrouter", "model": "test/model", "api_key": "sk-test-123"}
    body.update(extra)
    r = client.patch("/api/assistant/settings", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


class FakeProvider:
    """Plays back scripted turns; records what the model was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def chat(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else ModelTurn(text="done")
        return step(messages) if callable(step) else step

    async def close(self):
        pass


def _call(name, args, cid="c1"):
    return {"id": cid, "name": name, "arguments": args}


def _mk_node(session, **kw):
    from app.models import Node
    data = dict(name="n", protocol="vless", address="203.0.113.10", port=443, uuid="11111111-2222",
                transport="tcp", enabled=True, is_online=True)
    data.update(kw)
    n = Node(**data)
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _mk_device(session, **kw):
    from app.models import Device
    data = dict(mac="aa:bb:cc:dd:ee:01", ip="192.168.50.20", name="Living room TV", vendor="Samsung")
    data.update(kw)
    d = Device(**data)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


# ── settings ──────────────────────────────────────────────────────────────────

def test_settings_keep_keys_secret_and_encrypted(client, admin_user, auth_headers, session):
    from app.models import Settings as DBSettings
    from sqlmodel import select
    view = _enable(client, auth_headers)
    assert view["has_key"]["openrouter"] is True and "sk-test-123" not in json.dumps(view)
    stored = session.exec(select(DBSettings).where(DBSettings.key == "assistant_keys")).first().value
    assert "sk-test-123" not in stored
    # switching provider keeps the other key, clears the model
    view = client.patch("/api/assistant/settings", json={"provider": "anthropic"}, headers=auth_headers).json()
    assert view["provider"] == "anthropic" and view["model"] == "" and view["has_key"]["openrouter"]
    assert client.patch("/api/assistant/settings", json={"provider": "nope"}, headers=auth_headers).status_code == 400
    assert client.patch("/api/assistant/settings", json={"base_url": "ftp://x"}, headers=auth_headers).status_code == 400


def test_turned_off_assistant_refuses_chats(client, admin_user, auth_headers):
    assert client.post("/api/assistant/conversations", headers=auth_headers).status_code == 409


def test_requires_auth(client):
    assert client.get("/api/assistant/settings").status_code == 401


# ── redaction ─────────────────────────────────────────────────────────────────

def test_redactor_hides_secrets_and_public_addresses_and_restores_them():
    from app.assistant.redact import Redactor
    r = Redactor()
    data = {"uuid": "abcd-1234", "address": "node.example.com", "ip": "192.168.1.5",
            "note": "exit 198.51.100.7 ok", "nested": [{"password": "hunter22"}], "port": 443,
            "lan": {"address": "192.168.1.9"}}
    hidden = r.hide(data)
    text = json.dumps(hidden)
    for secret in ("abcd-1234", "node.example.com", "198.51.100.7", "hunter22"):
        assert secret not in text
    assert hidden["ip"] == "192.168.1.5" and hidden["lan"]["address"] == "192.168.1.9"
    assert r.hide({"uuid": "abcd-1234"})["uuid"] == hidden["uuid"]  # stable placeholder
    assert r.reveal(hidden) == data
    assert r.reveal({"path": f"/x/{hidden['uuid']}"}) == {"path": "/x/abcd-1234"}


# ── executor ──────────────────────────────────────────────────────────────────

def test_dangerous_routes_are_off_limits():
    from app.assistant.executor import validate_step
    for path in ("/auth/change-password", "/assistant/settings", "/system/update/start", "/backup/restore"):
        assert validate_step({"method": "POST", "path": path}) is not None, path
    assert validate_step({"method": "GET", "path": "/routing/rules"}) is not None  # GET not in act
    assert validate_step({"method": "POST", "path": "/routing/rules"}) is None
    assert "no API operation" in validate_step({"method": "POST", "path": "/no/such/thing"})


def test_step_references_are_filled_from_earlier_results():
    from app.assistant.executor import fill_refs
    results = [{"body": {"id": 7, "items": [{"x": "y"}]}}]
    assert fill_refs({"path": "/nodecircle/{{step1.id}}", "n": "{{step1.id}}", "v": "{{step1.items[0].x}}"},
                     results) == {"path": "/nodecircle/7", "n": 7, "v": "y"}


# ── search / playbooks ────────────────────────────────────────────────────────

def test_search_returns_the_playbook_and_operations(client):
    from app.assistant.api_index import search
    out = search("route device through node")
    assert out.startswith("PLAYBOOK device_via_node")
    out = search("create routing rule")
    assert "POST /routing/rules" in out and "match_value*" in out
    assert "/assistant/" not in search("assistant settings conversation")


def test_every_operation_named_in_a_playbook_exists(client):
    from app.assistant.executor import match_route, denied
    pat = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE) (/[A-Za-z0-9_\-/{}]+)")
    for f in (Path(__file__).parent.parent / "app" / "assistant" / "playbooks").glob("*.md"):
        for method, path in pat.findall(f.read_text(encoding="utf-8")):
            concrete = re.sub(r"\{[^}]+\}", "1", path).rstrip("/")
            assert match_route(method, concrete), f"{f.name}: {method} {path}"
            assert not denied(concrete)


# ── find tools ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_nodes_filters_by_country_and_sorts_by_speed(client, admin_user, session):
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.tools import find_nodes
    from app.database import get_async_engine
    _mk_node(session, name="🇩🇪 tt-2", speed_mbps=60, latency_ms=80)
    _mk_node(session, name="🇩🇪 tt-4", speed_mbps=120, latency_ms=90)
    _mk_node(session, name="🇳🇱 nl-1", speed_mbps=200, latency_ms=40)
    async with AsyncSession(get_async_engine()) as s:
        res = await find_nodes(s, {"countries": ["de"], "sort": "speed"})
        assert [n["name"] for n in res] == ["🇩🇪 tt-4", "🇩🇪 tt-2"]
        assert "address" not in res[0] and "uuid" not in res[0]
        res = await find_nodes(s, {"text": "nl"})
        assert res[0]["name"] == "🇳🇱 nl-1"


@pytest.mark.asyncio
async def test_find_devices_matches_loose_descriptions(client, admin_user, session):
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.tools import find_devices
    from app.database import get_async_engine
    _mk_device(session)
    _mk_device(session, mac="aa:bb:cc:dd:ee:02", ip="192.168.50.21", name="Anna phone", vendor="Apple")
    async with AsyncSession(get_async_engine()) as s:
        assert (await find_devices(s, {"text": "tv"}))[0]["name"] == "Living room TV"
        assert (await find_devices(s, {"text": "192.168.50.21"}))[0]["name"] == "Anna phone"


# ── agent loop + plans ────────────────────────────────────────────────────────

def test_device_via_node_end_to_end(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import AssistantPlan, RoutingRule
    _enable(client, auth_headers)
    node = _mk_node(session, name="🇩🇪 tt-4", speed_mbps=120, latency_ms=90)
    dev = _mk_device(session)

    def plan_turn(messages):
        # The model saw find results and proposes the rule.
        return ModelTurn(tool_calls=[_call("act", {
            "title": "TV through tt-4", "summary": "Route the TV via the German node.",
            "steps": [{"method": "POST", "path": "/routing/rules", "note": "add rule", "body": {
                "name": "TV via tt-4", "rule_type": "src_ip", "match_value": dev.ip,
                "action": f"node:{node.id}", "order": 10}}]}, "c3")])

    fake = FakeProvider([
        ModelTurn(text="Looking it up.", tool_calls=[
            _call("find_devices", {"text": "tv"}, "c1"),
            _call("find_nodes", {"countries": ["DE"], "sort": "speed"}, "c2")]),
        plan_turn,
        ModelTurn(text="I prepared a plan: approve it to route the TV."),
        ModelTurn(text="Done: the TV now goes through tt-4."),
    ])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        r = client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                        json={"text": "пусти телевизор через самую быструю немецкую ноду"})
        evs = _events(r)
        types = [e["type"] for e in evs]
        assert "plan" in types and types[-1] == "done", evs
        plan = next(e["plan"] for e in evs if e["type"] == "plan")
        assert plan["status"] == "pending" and plan["steps"][0]["path"] == "/routing/rules"
        assert session.exec(select(RoutingRule)).all() == []  # nothing ran yet

        r = client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers)
        evs = _events(r)
        assert evs[0]["plan"]["status"] == "executed", evs[0]
        assert any(e["type"] == "message" and "Done" in e["text"] for e in evs)
    rule = session.exec(select(RoutingRule)).one()
    assert (rule.rule_type, rule.match_value, rule.action) == ("src_ip", dev.ip, f"node:{node.id}")
    # the model was told the results in a note
    assert "approved and executed" in fake.seen[-1][-1]["content"]
    # second approval is refused
    assert client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers).status_code == 409
    # the conversation renders with the plan card
    conv = client.get(f"/api/assistant/conversations/{cid}", headers=auth_headers).json()
    kinds = [i["kind"] for i in conv["items"]]
    assert kinds[0] == "user" and "plan" in kinds and "note" in kinds
    assert session.exec(select(AssistantPlan)).one().decided_by == "admin"


def test_the_model_never_sees_secrets_but_plans_use_real_values(client, admin_user, auth_headers, session):
    _enable(client, auth_headers)
    node = _mk_node(session, name="secret-node", uuid="real-uuid-value-1", address="198.51.100.44")

    def act_with_placeholder(messages):
        tool_result = messages[-1]["content"]
        assert "real-uuid-value-1" not in tool_result and "198.51.100.44" not in tool_result
        ph = json.loads(tool_result)["body"]["uuid"]
        assert ph.startswith("‹s")
        return ModelTurn(tool_calls=[_call("act", {"title": "t", "summary": "s", "steps": [
            {"method": "PATCH", "path": f"/nodes/{node.id}", "body": {"note": f"uuid {ph}"}}]}, "c2")])

    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("read", {"calls": [{"path": f"/nodes/{node.id}"}]}, "c1")]),
        act_with_placeholder,
        ModelTurn(text="ok"),
    ])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        evs = _events(client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                                  json={"text": "note the uuid"}))
    plan = next(e["plan"] for e in evs if e["type"] == "plan")
    assert plan["steps"][0]["body"]["note"] == "uuid real-uuid-value-1"
    assert plan["before"][0]["name"] == "secret-node"  # current state captured for the card


def test_bad_plans_are_bounced_back_to_the_model(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("act", {"title": "x", "summary": "y", "steps": [
            {"method": "POST", "path": "/system/update/start", "body": {}}]})]),
        ModelTurn(text="I cannot do that."),
    ])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        evs = _events(client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                                  json={"text": "update yourself"}))
    assert not any(e["type"] == "plan" for e in evs)
    assert any(e["type"] == "tool_done" and e["ok"] is False for e in evs)
    assert "not available to the assistant" in fake.seen[1][-1]["content"]


def test_rejected_plan_changes_nothing(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import RoutingRule
    _enable(client, auth_headers)
    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("act", {"title": "x", "summary": "y", "steps": [
            {"method": "POST", "path": "/routing/rules", "body": {
                "name": "r", "rule_type": "domain", "match_value": "example.com", "action": "direct"}}]})]),
        ModelTurn(text="Approve?"),
    ])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        evs = _events(client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                                  json={"text": "example.com direct"}))
        pid = next(e["plan"]["id"] for e in evs if e["type"] == "plan")
        evs = _events(client.post(f"/api/assistant/plans/{pid}/reject", headers=auth_headers))
    assert evs[0]["plan"]["status"] == "rejected"
    assert session.exec(select(RoutingRule)).all() == []


def test_failed_step_stops_the_plan_and_is_reported(client, admin_user, auth_headers, session):
    _enable(client, auth_headers)
    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("act", {"title": "x", "summary": "y", "steps": [
            {"method": "POST", "path": "/routing/rules", "body": {
                "name": "r", "rule_type": "src_ip", "match_value": "192.168.50.20", "action": "node:9999"}},
            {"method": "DELETE", "path": "/routing/rules/1"}]})]),
        ModelTurn(text="plan ready"),
        ModelTurn(text="It failed: node 9999 does not exist."),
    ])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        evs = _events(client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                                  json={"text": "x"}))
        pid = next(e["plan"]["id"] for e in evs if e["type"] == "plan")
        evs = _events(client.post(f"/api/assistant/plans/{pid}/approve", headers=auth_headers))
    plan = evs[0]["plan"]
    assert plan["status"] == "failed" and len(plan["results"]) == 1
    note = fake.seen[-1][-1]["content"]
    assert "FAILED" in note and "did not run" in note


def test_monthly_limit_stops_the_assistant(client, admin_user, auth_headers):
    _enable(client, auth_headers, monthly_token_limit=10)
    fake = FakeProvider([ModelTurn(text="hi", input_tokens=50, output_tokens=5), ModelTurn(text="again")])
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers, json={"text": "a"})
        evs = _events(client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                                  json={"text": "b"}))
    assert evs[0]["type"] == "error" and "limit" in evs[0]["message"]
    usage = client.get("/api/assistant/settings", headers=auth_headers).json()["usage"]
    assert usage["input"] == 50 and usage["requests"] == 1


def test_interrupted_tool_calls_are_repaired_in_history():
    from app.assistant.agent import _trim
    h = [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "search", "arguments": {}}]},
         {"role": "user", "content": "y"}]
    out = _trim(h)
    assert out[2]["role"] == "tool" and out[2]["tool_call_id"] == "a" and out[3]["content"] == "y"


# ── providers ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_compat_round_trip_and_tool_calls():
    from app.assistant.providers import OpenAICompatProvider
    from app.assistant.settings import AssistantSettings
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "search", "arguments": '{"query": "x"}'}}]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.0001}})

    s = AssistantSettings(provider="openrouter", model="m", keys={"openrouter": "k"}, egress="direct")
    p = OpenAICompatProvider(s)
    p.client = httpx.AsyncClient(base_url=s.base_url, transport=httpx.MockTransport(handler),
                                 headers=p.client.headers)
    turn = await p.chat("sys", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "read", "arguments": {"calls": []}}]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
    ], [{"name": "search", "description": "d", "parameters": {"type": "object"}}])
    await p.close()
    assert turn.tool_calls == [{"id": "t1", "name": "search", "arguments": {"query": "x"}}]
    assert (turn.input_tokens, turn.output_tokens, turn.cost) == (12, 3, 0.0001)
    msgs = seen["body"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"calls": []}'
    assert msgs[3] == {"role": "tool", "tool_call_id": "a", "content": "{}"}
    assert seen["headers"]["authorization"] == "Bearer k" and seen["headers"]["x-title"] == "PiTun"
    assert seen["body"]["usage"] == {"include": True}


def test_anthropic_messages_group_tool_results_and_replay_native_blocks():
    from app.assistant.providers import AnthropicProvider
    from app.assistant.settings import AssistantSettings
    p = AnthropicProvider(AssistantSettings(provider="anthropic", keys={"anthropic": "k"}, egress="direct"))
    native = [{"type": "thinking", "thinking": "", "signature": "sig"},
              {"type": "tool_use", "id": "a", "name": "search", "input": {}}]
    out = p._messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "search", "arguments": {}},
                                                            {"id": "b", "name": "read", "arguments": {}}],
         "raw": {"provider": "anthropic", "model": p.model, "content": native}},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "b", "content": "r2", "is_error": True},
        {"role": "user", "content": "next"},
    ])
    assert out[1]["content"] is native
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["a", "b"] and out[2]["content"][1]["is_error"]
    assert out[3] == {"role": "user", "content": "next"}
    # a turn from another model is rebuilt from text + tool_use, without its thinking
    out = p._messages([{"role": "user", "content": "x"}, {"role": "assistant", "content": "t", "tool_calls": [],
                       "raw": {"provider": "anthropic", "model": "other", "content": native}}])
    assert out[1]["content"] == [{"type": "text", "text": "t"}]
    assert p.model == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_probe_reports_missing_key_and_tool_calling():
    from app.assistant.providers import probe
    from app.assistant.settings import AssistantSettings
    r = await probe(AssistantSettings(provider="openai", model="m"))
    assert r["ok"] is False and "API key" in r["error"]
    r = await probe(AssistantSettings(provider="local", model="m"))
    assert r["ok"] is False and "URL" in r["error"]
