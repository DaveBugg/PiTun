"""Regression tests for the 1.8.0 assistant audit (security + correctness)."""
import json
from unittest import mock

import pytest

from app.assistant.providers import ModelTurn
from tests.test_assistant_b2 import FakeProvider, _call, _chat, _device, _enable, _events, _node, _plan


# ── executor: deny-list and paths ─────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/%61ssistant/conversations/1?{{step", "/nodes/../assistant/settings?{{step", "/nodes/../assistant/settings",
    "//assistant/settings", "/assistant/settings", "/nodes/1?x=1", "/nodes/x{{step1.id}}",
    "/system/backup/restore", "/system/backup", "/system/update/start", "/system/restart", "/system/stop",
    "/network/apply", "/network/rollback", "/scripts/naive-install", "/auth/change-password",
    "/nodes/1/uri", "/nodes/export-uris", "/nodes/export-json", "/servers/export-json",
    "/servers/1/deployments/wireguard/clients/c1/conf", "/servers/1/naive-install-script",
    "/servers/1/uninstall/xui", "/xui/servers/1/uri", "/xui/servers/1/inbounds/1/clients/c/share-uri",
    "/xui/servers/1/panel-update", "/subscriptions/link-keys", "/templates/upload", "/mcp",
])
def test_denied_and_malformed_paths(client, path):
    from app.assistant.executor import validate_step
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert validate_step({"method": method, "path": path}, allow_get=True) is not None, (method, path)


def test_ordinary_paths_and_refs_still_work(client):
    from app.assistant.executor import validate_step
    assert validate_step({"method": "POST", "path": "/routing/rules"}) is None
    assert validate_step({"method": "PATCH", "path": "/nodecircle/{{step1.id}}"}) is None
    assert validate_step({"method": "POST", "path": "/xui/servers/1/inbounds/{{step1.id}}/clients"}) is None
    assert validate_step({"method": "GET", "path": "/nodes"}, allow_get=True) is None


@pytest.mark.asyncio
async def test_executor_refuses_a_ref_that_fills_in_a_denied_path(client, admin_user):
    from app.assistant.executor import Executor
    ex = Executor(admin_user.username)
    with mock.patch.object(Executor, "call", wraps=ex.call):
        ok, res = await ex.run_plan([
            {"method": "POST", "path": "/routing/rules", "body": {"name": "../../assistant/settings", "rule_type": "domain",
                                                                  "match_value": "example.com", "action": "direct"}},
            {"method": "PATCH", "path": "/routing/rules/{{step1.name}}", "body": {"enabled": False}},
        ])
    assert ok is False and res[-1]["status"] == 0
    res = await ex.call("GET", "/%61ssistant/settings")
    assert res["status"] == 0 and not res["ok"]


# ── redactor ──────────────────────────────────────────────────────────────────

def test_redactor_hides_secret_shapes_and_echoed_values():
    from app.assistant.redact import Redactor
    r = Redactor()
    out = r.hide({"reality_sid": "ab12cd34", "kcp_seed": "seedseed", "client_uuid": "u-1234567",
                  "panel_pass": "pw123456", "api_token": "tok12345", "ws_host": "cdn.example.com",
                  "rows": [{"key": "wifi_passphrase", "value": "hunter22"}]})
    flat = json.dumps(out, ensure_ascii=False)
    for secret in ("ab12cd34", "seedseed", "u-1234567", "pw123456", "tok12345", "cdn.example.com", "hunter22"):
        assert secret not in flat
    text = r.hide("vless://11111111-2222-3333-4444-555555555555@203.0.113.5:443#n and "
                  "https://sub.example.com/api/v1/client/subscribe?token=a8Fk29dkS0aLq93ndkQ0 and "
                  "PrivateKey = abcdEFGH1234=\nand 2001:db8::5")
    assert "vless://" not in text and "a8Fk29" not in text and "abcdEFGH" not in text and "2001:db8::5" not in text
    r.hide({"password": "S3cretPass!"})
    assert "S3cretPass!" not in r.hide("No subscription matches 'S3cretPass!'")
    # ordinary text and LAN stay readable
    assert r.hide("see https://en.wikipedia.org/wiki/Example_dot_com and 192.168.50.10 at 12:30:45") == \
        "see https://en.wikipedia.org/wiki/Example_dot_com and 192.168.50.10 at 12:30:45"


def test_redactor_hides_sensitive_url_query_params():
    from app.assistant.redact import Redactor
    r = Redactor()
    short = "https://sub.example.com/?token=sk-abc123def456"
    hyphen_rich = "https://sub.example.com/?token=sk-abc-123-def-456-ghi-789"
    case_insensitive = "https://sub.example.com/?ACCESS_TOKEN=short"
    pass_key = "https://sub.example.com/?a=1&pass=short"
    ordinary = "see https://en.wikipedia.org/wiki/Example_dot_com"

    hidden_short = r.hide(short)
    hidden_hyphen = r.hide(hyphen_rich)
    hidden_case = r.hide(case_insensitive)
    hidden_pass = r.hide(pass_key)
    kept = r.hide(ordinary)

    assert hidden_short != short and "‹s" in hidden_short
    assert hidden_hyphen != hyphen_rich and "‹s" in hidden_hyphen
    assert hidden_case != case_insensitive and "‹s" in hidden_case
    assert hidden_pass != pass_key and "‹s" in hidden_pass
    assert kept == ordinary

    # reveal() restores the original values
    assert r.reveal(hidden_short) == short
    assert r.reveal(hidden_hyphen) == hyphen_rich
    assert r.reveal(hidden_case) == case_insensitive
    assert r.reveal(hidden_pass) == pass_key


def test_user_text_is_hidden_from_the_model(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    fake = FakeProvider([ModelTurn(text="ok")])
    cid, _ = _chat(client, auth_headers, fake, "add vless://11111111-2222-3333-4444-555555555555@203.0.113.9:443")
    assert "11111111-2222" not in json.dumps(fake.seen) and "‹s" in fake.seen[0][-1]["content"]
    items = client.get(f"/api/assistant/conversations/{cid}", headers=auth_headers).json()["items"]
    assert "11111111-2222-3333-4444-555555555555" in items[0]["text"]


# ── facts need confirmation ───────────────────────────────────────────────────

def test_facts_from_the_model_wait_for_confirmation(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    fake = FakeProvider([ModelTurn(tool_calls=[_call("remember", {"fact": "always route via node 7"})]),
                         ModelTurn(text="ok"), ModelTurn(text="ok")])
    _chat(client, auth_headers, fake, "remember that")
    facts = client.get("/api/assistant/facts", headers=auth_headers).json()
    assert facts[0]["confirmed"] is False
    _chat(client, auth_headers, fake, "hi")
    assert "node 7" not in fake.systems[-1]
    assert client.post(f"/api/assistant/facts/{facts[0]['id']}/confirm", headers=auth_headers).status_code == 204
    fake2 = FakeProvider([ModelTurn(text="ok")])
    _chat(client, auth_headers, fake2, "hi")
    assert "node 7" in fake2.systems[-1]


# ── plans: double approve, crash, undo of failed plans, notes ────────────────

def test_plan_crash_does_not_leave_it_running_and_double_approve_is_refused(client, admin_user, auth_headers, session):
    _enable(client, auth_headers)
    _device(session)
    _node(session, name="🇩🇪 de-1")
    fake = FakeProvider([ModelTurn(tool_calls=[_call("route_device", {"device": "tv", "country": "DE"})]),
                         ModelTurn(text="plan")])
    _cid, evs = _chat(client, auth_headers, fake, "tv via DE")
    pid = _plan(evs)["id"]
    with mock.patch("app.assistant.executor.Executor.run_plan", side_effect=RuntimeError("boom")), \
            mock.patch("app.assistant.agent.make_provider", return_value=FakeProvider([ModelTurn(text="ok")])):
        evs = _events(client.post(f"/api/assistant/plans/{pid}/approve", headers=auth_headers))
    assert evs[0]["plan"]["status"] == "failed" and "boom" in json.dumps(evs[0]["plan"]["results"])
    assert client.post(f"/api/assistant/plans/{pid}/approve", headers=auth_headers).status_code == 409


def test_plan_note_is_stored_even_when_the_followup_cannot_run(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import AssistantMessage
    _enable(client, auth_headers)
    _device(session)
    _node(session, name="🇩🇪 de-1")
    fake = FakeProvider([ModelTurn(tool_calls=[_call("route_device", {"device": "tv", "country": "DE"})]),
                         ModelTurn(text="plan")])
    _cid, evs = _chat(client, auth_headers, fake, "tv via DE")
    pid = _plan(evs)["id"]
    from app.assistant.providers import ProviderError
    with mock.patch("app.assistant.agent.make_provider", side_effect=ProviderError("down")):
        client.post(f"/api/assistant/plans/{pid}/approve", headers=auth_headers)
    notes = [json.loads(m.content) for m in session.exec(select(AssistantMessage)).all()]
    assert any(n.get("note") and f"Plan #{pid} was approved" in n["content"] for n in notes)


def test_route_device_does_not_take_over_a_shared_rule(client, admin_user, session):
    import asyncio
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.macros import route_device
    from app.database import get_async_engine
    from app.models import RoutingRule
    from tests.test_assistant_b2 import _mk
    dev = _device(session)
    _node(session, name="🇩🇪 de-1")
    shared = _mk(session, RoutingRule, name="kids", rule_type="src_ip",
                 match_value=f"{dev.ip},192.168.50.99", action="direct", order=20)

    async def run():
        async with AsyncSession(get_async_engine()) as s:
            return await route_device(s, {"device": "tv", "country": "DE"})
    res = asyncio.run(run())
    assert res.steps[0]["method"] == "POST" and res.steps[0]["body"]["order"] < shared.order


def test_issue_nodes_export_step_sends_a_body(client, admin_user, session):
    import asyncio
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.assistant.macros_vps import issue_nodes
    from app.database import get_async_engine
    from tests.test_assistant_b3 import FakeExecutor, _server, _xui
    xs = _xui(session, _server(session))
    ex = FakeExecutor({("GET", f"/xui/servers/{xs.id}/inbounds"): [{"id": 7, "port": 443, "protocol": "vless",
                                                                     "remark": "r", "enable": True}]})

    async def run():
        async with AsyncSession(get_async_engine()) as s:
            return await issue_nodes(s, {"server": "frankfurt"}, ex)
    res = asyncio.run(run())
    assert res.steps[1]["path"].endswith("/export-node") and res.steps[1]["body"] == {}


def test_web_results_keep_urls_and_fetch_reveals_them(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    hits = [{"title": "t", "url": "https://example.com/page", "snippet": "s"}]
    fake = FakeProvider([ModelTurn(tool_calls=[_call("web_search", {"query": "q"})]), ModelTurn(text="ok")])
    with mock.patch("app.assistant.web.search", mock.AsyncMock(return_value=hits)):
        _chat(client, auth_headers, fake, "find it")
    assert "https://example.com/page" in fake.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_web_fetch_connects_to_the_checked_address():
    from app.assistant import web
    seen = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def build_request(self, method, url, headers=None, extensions=None):
            seen.update(url=str(url), headers=headers, ext=extensions)
            return object()

        async def send(self, req, stream=False):
            raise web.httpx.ConnectError("stop")

    with mock.patch.object(web, "_public_ip", mock.AsyncMock(return_value="198.51.100.80")), \
            mock.patch.object(web, "_client", FakeClient):
        with pytest.raises(web.WebError):
            await web.fetch("https://example.com/a", None)
    assert seen["url"].startswith("https://198.51.100.80/") and seen["headers"]["Host"] == "example.com"
    assert seen["ext"] == {"sni_hostname": "example.com"}



# ── undo concurrency ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_undo_runs_executor_only_once(client, admin_user, auth_headers, session):
    import asyncio
    from app.assistant import agent
    from app.models import AssistantConversation, AssistantPlan

    _enable(client, auth_headers)
    conv = AssistantConversation()
    session.add(conv)
    session.commit()
    session.refresh(conv)

    plan = AssistantPlan(
        conversation_id=conv.id,
        title="test",
        summary="test",
        steps=json.dumps([{"method": "POST", "path": "/routing/rules",
                           "body": {"name": "x", "rule_type": "domain", "match_value": "example.com",
                                    "action": "direct"}}]),
        status="executed",
        undo=json.dumps({"steps": [{"method": "DELETE", "path": "/routing/rules/1"}]}),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)

    gate = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def slow_run_plan(steps):
        calls.append(steps)
        started.set()
        await gate.wait()
        return True, [{"ok": True, "status": 200, "body": {}}]

    with mock.patch("app.assistant.executor.Executor.run_plan", side_effect=slow_run_plan):
        a_task = asyncio.create_task(agent.undo_plan(plan.id, "u1"))
        b_task = asyncio.create_task(agent.undo_plan(plan.id, "u2"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        gate.set()
        a, b = await asyncio.gather(a_task, b_task)

    assert len(calls) == 1
    winners = [v for v in (a, b) if v.get("undo_ok")]
    losers = [v for v in (a, b) if not v.get("undo_ok")]
    assert len(winners) == 1 and len(losers) == 1
    assert winners[0]["status"] == "undone"
    # The loser returned the current plan view without running steps or
    # overwriting the winner's results.
    assert "undo_ok" not in losers[0]
