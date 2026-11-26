"""AI assistant (1.8.0-beta.2): macros, undo, facts, summary, screenshots,
small-model mode, LAN discovery."""
import json
from unittest import mock

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
    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.systems = []
        self.tools = []

    async def chat(self, system, messages, tools):
        self.systems.append(system)
        self.tools.append([t["name"] for t in tools])
        self.seen.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else ModelTurn(text="done")
        return step(messages) if callable(step) else step

    async def close(self):
        pass


def _call(name, args, cid="c1"):
    return {"id": cid, "name": name, "arguments": args}


def _mk(session, model, **kw):
    obj = model(**kw)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj


def _node(session, **kw):
    from app.models import Node
    data = dict(name="n", protocol="vless", address="203.0.113.10", port=443, uuid="11111111-2222",
                transport="tcp", enabled=True, is_online=True)
    data.update(kw)
    return _mk(session, Node, **data)


def _device(session, **kw):
    from app.models import Device
    data = dict(mac="aa:bb:cc:dd:ee:01", ip="192.168.50.20", name="Living room TV", vendor="Samsung")
    data.update(kw)
    return _mk(session, Device, **data)


def _chat(client, auth_headers, fake, text, **body):
    with mock.patch("app.assistant.agent.make_provider", return_value=fake):
        cid = body.pop("cid", None) or client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
        r = client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                        json={"text": text, **body})
        assert r.status_code == 200, r.text
        return cid, _events(r)


def _plan(evs):
    return next(e["plan"] for e in evs if e["type"] == "plan")


# ── macros ────────────────────────────────────────────────────────────────────

def test_route_device_macro_then_undo(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import RoutingRule
    _enable(client, auth_headers)
    node = _node(session, name="🇩🇪 Frankfurt-2", latency_ms=80, speed_mbps=200)
    _node(session, name="🇳🇱 Amsterdam", latency_ms=60, address="203.0.113.11")
    dev = _device(session)
    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("route_device", {"device": "tv", "node": "frankfurt"})]),
        ModelTurn(text="Plan ready."),
    ])
    cid, evs = _chat(client, auth_headers, fake, "TV through Frankfurt")
    plan = _plan(evs)
    assert plan["steps"][0]["body"] == {"name": "Living room TV via 🇩🇪 Frankfurt-2", "rule_type": "src_ip",
                                        "match_value": dev.ip, "action": f"node:{node.id}",
                                        "order": 10, "enabled": True}
    assert "no DHCP reservation" in plan["summary"]
    # the model is told what the plan does, with addresses hidden
    tool_msg = fake.seen[-1][-1]
    assert tool_msg["role"] == "tool" and "Frankfurt" in tool_msg["content"]

    with mock.patch("app.assistant.agent.make_provider", return_value=FakeProvider([ModelTurn(text="ok")])):
        evs = _events(client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers))
    view = evs[0]["plan"]
    assert view["status"] == "executed" and view["can_undo"]
    assert len(session.exec(select(RoutingRule)).all()) == 1

    r = client.post(f"/api/assistant/plans/{plan['id']}/undo", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "undone" and r.json()["undo_ok"]
    session.expire_all()
    assert session.exec(select(RoutingRule)).all() == []
    assert client.post(f"/api/assistant/plans/{plan['id']}/undo", headers=auth_headers).status_code == 409
    conv = client.get(f"/api/assistant/conversations/{cid}", headers=auth_headers).json()
    assert any(i["kind"] == "note" and "undid" in i["text"] for i in conv["items"])


@pytest.mark.asyncio
async def test_route_device_reuses_rule_fixes_policy_and_asks_on_ties(client, admin_user, session):
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.macros import route_device
    from app.database import get_async_engine
    from app.models import RoutingRule, Settings as DBSettings
    node = _node(session, name="🇩🇪 de-1")
    dev = _device(session)
    rule = _mk(session, RoutingRule, name="old", rule_type="src_ip", match_value=dev.ip, action="direct")
    _mk(session, DBSettings, key="device_routing_mode", value="include_only")
    async with AsyncSession(get_async_engine()) as s:
        res = await route_device(s, {"device": "Living room TV", "country": "DE"})
    assert res.question is None
    assert res.steps[0]["method"] == "PATCH" and res.steps[0]["path"] == f"/routing/rules/{rule.id}"
    assert res.steps[0]["body"]["action"] == f"node:{node.id}"
    assert res.steps[1] == {"method": "PATCH", "path": f"/devices/{dev.id}", "body": {"routing_policy": "include"},
                            "note": res.steps[1]["note"]}

    _device(session, mac="aa:bb:cc:dd:ee:02", ip="192.168.50.21", name="Bedroom TV")
    async with AsyncSession(get_async_engine()) as s:
        res = await route_device(s, {"device": "tv", "node": "de"})
    assert res.question and "Several devices" in res.question and "Bedroom TV" in res.question


@pytest.mark.asyncio
async def test_build_circle_excludes_countries_and_switches_off_other_circles(client, admin_user, session):
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.macros import build_circle
    from app.database import get_async_engine
    from app.models import NodeCircle, Subscription
    sub = _mk(session, Subscription, name="Main sub", url="https://example.com/sub")
    ids = [_node(session, name=f"🇩🇪 de-{i}", subscription_id=sub.id).id for i in range(2)]
    _node(session, name="🇷🇺 ru-1", subscription_id=sub.id)
    other = _mk(session, NodeCircle, name="Old", enabled=True)
    async with AsyncSession(get_async_engine()) as s:
        res = await build_circle(s, {"subscription": "main", "exclude_countries": ["ru"]})
    assert res.question is None
    body = res.steps[0]["body"]
    assert res.steps[0]["method"] == "POST" and sorted(body["node_ids"]) == sorted(ids)
    assert body["excluded_countries"] == ["RU"] and body["subscription_ids"] == [sub.id]
    assert res.steps[1]["path"] == f"/nodecircle/{other.id}" and res.steps[1]["body"] == {"enabled": False}
    assert "RU (1)" in res.summary
    async with AsyncSession(get_async_engine()) as s:
        res = await build_circle(s, {"subscription": "main", "exclude_countries": ["DE"]})
    assert res.question and "at least 2" in res.question
    async with AsyncSession(get_async_engine()) as s:
        res = await build_circle(s, {"subscription": "nothing like it"})
    assert res.question and "Main sub" in res.question


def test_build_circle_plan_runs_and_undo_restores_the_other_circle(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import NodeCircle, Subscription
    _enable(client, auth_headers)
    sub = _mk(session, Subscription, name="Main", url="https://example.com/sub")
    for i in range(3):
        _node(session, name=f"🇩🇪 de-{i}", subscription_id=sub.id, address=f"203.0.113.{20 + i}")
    other = _mk(session, NodeCircle, name="Old", enabled=True, node_ids="[]")
    fake = FakeProvider([ModelTurn(tool_calls=[_call("build_circle", {"subscription": "Main"})]),
                         ModelTurn(text="ready")])
    _cid, evs = _chat(client, auth_headers, fake, "circle from Main")
    plan = _plan(evs)
    with mock.patch("app.assistant.agent.make_provider", return_value=FakeProvider([ModelTurn(text="ok")])):
        evs = _events(client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers))
    assert evs[0]["plan"]["status"] == "executed", evs[0]
    session.expire_all()
    assert session.get(NodeCircle, other.id).enabled is False
    assert len(session.exec(select(NodeCircle)).all()) == 2
    r = client.post(f"/api/assistant/plans/{plan['id']}/undo", headers=auth_headers)
    assert r.json()["status"] == "undone", r.json()
    session.expire_all()
    assert session.get(NodeCircle, other.id).enabled is True
    assert [c.name for c in session.exec(select(NodeCircle)).all()] == ["Old"]


@pytest.mark.asyncio
async def test_add_site_rule_uses_lan_lookups_and_merges_existing(client, admin_user, session):
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.macros import add_site_rule
    from app.database import get_async_engine
    from app.models import DomainStat, RoutingRule
    for d, h in (("api.examplevideo.com", 9), ("cdn.examplevideo.net", 5), ("unrelated.org", 50)):
        _mk(session, DomainStat, domain=d, hits=h)
    async with AsyncSession(get_async_engine()) as s:
        res = await add_site_rule(s, {"site": "examplevideo", "action": "proxy",
                                      "domains": ["examplevideo.com", "www.examplevideo.com", "bad domain"]})
    assert res.question is None
    assert res.steps[0]["body"]["match_value"] == "examplevideo.com,examplevideo.net"
    assert res.steps[0]["body"]["action"] == "proxy"

    rule = _mk(session, RoutingRule, name="examplevideo", rule_type="domain", match_value="old.example",
               action="direct")
    node = _node(session, name="🇫🇮 Helsinki")
    async with AsyncSession(get_async_engine()) as s:
        res = await add_site_rule(s, {"site": "examplevideo", "action": "node", "node": "helsinki",
                                      "domains": ["examplevideo.com"]})
    st = res.steps[0]
    assert st["method"] == "PATCH" and st["path"] == f"/routing/rules/{rule.id}"
    assert st["body"]["action"] == f"node:{node.id}"
    assert st["body"]["match_value"].startswith("old.example,examplevideo.com")
    async with AsyncSession(get_async_engine()) as s:
        res = await add_site_rule(s, {"site": "zz", "action": "block"})
    assert res.question and "domains" in res.question


def test_undo_steps_cover_patch_post_delete_and_actions():
    from app.assistant.executor import undo_steps
    steps = [
        {"method": "PATCH", "path": "/devices/3", "body": {"routing_policy": "include"}},
        {"method": "POST", "path": "/routing/rules", "body": {"name": "x"}},
        {"method": "DELETE", "path": "/routing/rules/9"},
        {"method": "POST", "path": "/devices/scan"},
        {"method": "PATCH", "path": "/devices/4", "body": {"name": "y"}},
    ]
    before = [{"id": 3, "routing_policy": "default", "name": "tv"}, None,
              {"id": 9, "name": "old", "rule_type": "domain", "match_value": "a.example", "action": "direct"},
              None, None]
    results = [{"ok": True, "path": "/devices/3"}, {"ok": True, "path": "/routing/rules", "body": {"id": 12}},
               {"ok": True, "path": "/routing/rules/9"}, {"ok": True, "path": "/devices/scan", "body": {}},
               {"ok": False}]
    undo, skipped = undo_steps(steps, before, results)
    assert [(u["method"], u["path"]) for u in undo] == [
        ("POST", "/routing/rules"), ("DELETE", "/routing/rules/12"), ("PATCH", "/devices/3")]
    assert undo[0]["body"] == {"name": "old", "rule_type": "domain", "match_value": "a.example", "action": "direct"}
    assert undo[2]["body"] == {"routing_policy": "default"}
    assert len(skipped) == 1 and "/devices/scan" in skipped[0]


# ── facts ─────────────────────────────────────────────────────────────────────

def test_facts_are_saved_shown_injected_and_deletable(client, admin_user, auth_headers, session):
    _enable(client, auth_headers)
    fake = FakeProvider([
        ModelTurn(tool_calls=[_call("remember", {"fact": "The TV is the Samsung in the living room"}, "a"),
                              _call("remember", {"fact": "node ‹s1› is best"}, "b")]),
        ModelTurn(text="Noted."),
    ])
    _chat(client, auth_headers, fake, "запомни: телевизор — это Samsung в гостиной")
    results = [m for m in fake.seen[-1] if m["role"] == "tool"]
    assert "Proposed as fact" in results[0]["content"] and results[1]["is_error"]
    facts = client.get("/api/assistant/facts", headers=auth_headers).json()
    assert [f["text"] for f in facts] == ["The TV is the Samsung in the living room"]
    assert client.post(f"/api/assistant/facts/{facts[0]['id']}/confirm", headers=auth_headers).status_code == 204

    r = client.post("/api/assistant/facts", json={"text": "Never use exits at 203.0.113.50"}, headers=auth_headers)
    assert r.status_code == 201
    fake2 = FakeProvider([ModelTurn(text="hi")])
    _chat(client, auth_headers, fake2, "hello")
    system = fake2.systems[0]
    assert "Samsung in the living room" in system and "203.0.113.50" not in system and "‹s" in system
    client.delete(f"/api/assistant/facts/{facts[0]['id']}", headers=auth_headers)
    assert len(client.get("/api/assistant/facts", headers=auth_headers).json()) == 1
    assert client.post("/api/assistant/facts", json={"text": ""}, headers=auth_headers).status_code == 400


# ── summary ───────────────────────────────────────────────────────────────────

def test_long_chats_are_folded_into_a_summary(client, admin_user, auth_headers, session):
    from app.assistant import agent
    from app.models import AssistantConversation, AssistantMessage
    _enable(client, auth_headers)
    cid = client.post("/api/assistant/conversations", headers=auth_headers).json()["id"]
    for i in range(agent.SUMMARY_TRIGGER + 4):
        role = "user" if i % 2 == 0 else "assistant"
        session.add(AssistantMessage(conversation_id=cid, role=role,
                                     content=json.dumps({"role": role, "content": f"m{i}"})))
    session.commit()
    fake = FakeProvider([ModelTurn(text="SUMMARY: talked about m0..m27"), ModelTurn(text="answer")])
    _cid, evs = _chat(client, auth_headers, fake, "next", cid=cid)
    assert any(e["type"] == "tool" and e["name"] == "summary" for e in evs)
    assert "m0" in fake.seen[0][0]["content"]          # the summary call got the old part
    assert "SUMMARY: talked" in fake.systems[1]         # the real turn gets the summary
    sent = [m["content"] for m in fake.seen[1]]
    assert "m0" not in sent and sent[-1] == "next" and len(sent) <= agent.SUMMARY_KEEP + 2
    session.expire_all()
    conv = session.get(AssistantConversation, cid)
    assert conv.summary.startswith("SUMMARY") and conv.summary_upto > 0


# ── screenshots ───────────────────────────────────────────────────────────────

def test_screenshots_reach_the_model_and_old_ones_are_dropped(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    img = {"media_type": "image/png", "data": "iVBORw0KGgo="}
    fake = FakeProvider([ModelTurn(text="I see an error.")])
    cid, _ = _chat(client, auth_headers, fake, "what is this?", images=[img])
    assert fake.seen[0][-1]["images"] == [img]
    for t in ("two", "three"):
        _chat(client, auth_headers, FakeProvider([ModelTurn(text="ok")]), t, cid=cid, images=[img])
    fake = FakeProvider([ModelTurn(text="ok")])
    _chat(client, auth_headers, fake, "four", cid=cid)
    users = [m for m in fake.seen[0] if m["role"] == "user"]
    assert "images" not in users[0] and "not shown again" in users[0]["content"]
    assert users[1].get("images") and users[2].get("images")
    conv = client.get(f"/api/assistant/conversations/{cid}", headers=auth_headers).json()
    assert conv["items"][0]["images"][0].startswith("data:image/png;base64,")
    r = client.post(f"/api/assistant/conversations/{cid}/messages", headers=auth_headers,
                    json={"text": "x", "images": [{"media_type": "text/html", "data": "x"}]})
    assert r.status_code == 400


def test_provider_formats_for_screenshots():
    from app.assistant.providers import AnthropicProvider, OpenAICompatProvider
    from app.assistant.settings import AssistantSettings
    msg = [{"role": "user", "content": "look", "images": [{"media_type": "image/jpeg", "data": "AAA"}]}]
    out = OpenAICompatProvider._messages("sys", msg)
    assert out[1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}
    p = AnthropicProvider(AssistantSettings(provider="anthropic", keys={"anthropic": "k"}, egress="direct"))
    out = p._messages(msg)
    assert out[0]["content"][0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}
    assert out[0]["content"][-1] == {"type": "text", "text": "look"}


# ── small-model mode ──────────────────────────────────────────────────────────

def test_small_mode_uses_macros_only_and_parses_json_tool_calls(client, admin_user, auth_headers, session):
    view = _enable(client, auth_headers, provider="local", base_url="http://192.168.50.9:11434/v1", model="qwen")
    assert view["small_mode"] == "auto" and view["small_active"] is True
    _device(session)
    _node(session, name="🇩🇪 de-1")
    fake = FakeProvider([
        ModelTurn(text='Sure: {"tool": "route_device", "arguments": {"device": "tv", "country": "DE"}}'),
        ModelTurn(text="Plan ready."),
    ])
    _cid, evs = _chat(client, auth_headers, fake, "tv via germany")
    assert "search" not in fake.tools[0] and "act" not in fake.tools[0] and "route_device" in fake.tools[0]
    assert "ONE tool" in fake.systems[0]
    assert _plan(evs)["steps"][0]["body"]["rule_type"] == "src_ip"
    assert client.patch("/api/assistant/settings", json={"small_mode": "off"},
                        headers=auth_headers).json()["small_active"] is False
    assert client.patch("/api/assistant/settings", json={"small_mode": "x"}, headers=auth_headers).status_code == 400


def test_text_tool_call_parser():
    from app.assistant.agent import text_tool_call
    names = {"route_device"}
    assert text_tool_call('{"name": "route_device", "arguments": "{\\"device\\": \\"tv\\"}"}', names)["arguments"] == {"device": "tv"}
    assert text_tool_call('{"tool": "rm_rf", "arguments": {}}', names) is None
    assert text_tool_call("no json here", names) is None


# ── discovery ─────────────────────────────────────────────────────────────────

def test_discovery_probes_private_hosts_only(client, admin_user, auth_headers, session):
    _device(session, ip="192.168.50.30", is_online=True)
    _device(session, mac="aa:bb:cc:dd:ee:09", ip="203.0.113.5", is_online=True)
    probed = []

    async def fake_open(host, port):
        probed.append(host)
        return host == "192.168.50.30" and port == 11434

    async def fake_models(client_, base):
        return ["qwen3:14b"]

    with mock.patch("app.assistant.discover._open", fake_open), \
            mock.patch("app.assistant.discover._models", fake_models):
        r = client.get("/api/assistant/discover", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == [{"base_url": "http://192.168.50.30:11434/v1", "kind": "Ollama", "host": "192.168.50.30",
                         "port": 11434, "models": ["qwen3:14b"]}]
    assert "203.0.113.5" not in probed and "127.0.0.1" in probed



# ── web_search execution boundary ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_web_search_blocks_search_and_fetch():
    from app.assistant import web
    from app.assistant.tools import ToolContext, run_tool

    ctx = ToolContext(session=None, redactor=None, executor=None, save_plan=None, web_search=False)

    with mock.patch.object(web, "search", new_callable=mock.AsyncMock) as m_search, \
            mock.patch.object(web, "fetch", new_callable=mock.AsyncMock) as m_fetch:
        res, err, _ = await run_tool(ctx, "web_search", {"query": "x"})
        assert err is True and "disabled" in res
        m_search.assert_not_awaited()

        res, err, _ = await run_tool(ctx, "web_fetch", {"url": "https://example.com"})
        assert err is True and "disabled" in res
        m_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_web_search_still_calls_search_and_fetch():
    from app.assistant import web
    from app.assistant.redact import Redactor
    from app.assistant.tools import ToolContext, run_tool

    ctx = ToolContext(session=None, redactor=Redactor(), executor=None, save_plan=None, web_search=True)

    with mock.patch.object(web, "search", new_callable=mock.AsyncMock,
                           return_value=[{"title": "t", "url": "https://example.com", "snippet": "s"}]) as m:
        res, err, _ = await run_tool(ctx, "web_search", {"query": "x"})
        assert err is False
        m.assert_awaited_once()

    with mock.patch.object(web, "fetch", new_callable=mock.AsyncMock,
                           return_value={"url": "https://example.com", "status": 200, "text": "hi"}) as m:
        res, err, _ = await run_tool(ctx, "web_fetch", {"url": "https://example.com"})
        assert err is False
        m.assert_awaited_once()
