"""AI assistant (1.8.0-beta.5): MCP server, tokens, plans waiting for approval."""
from unittest import mock

from tests.test_assistant_b2 import _device, _events, _node


def _rpc(client, token, method, params=None, rid=1, **headers):
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    h = {"Accept": "application/json, text/event-stream", **headers}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return client.post("/api/mcp", json=body, headers=h)


def _setup(client, auth_headers, scope="plan"):
    r = client.patch("/api/assistant/settings", json={"mcp_enabled": True}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["mcp_enabled"] is True
    r = client.post("/api/assistant/mcp/tokens", json={"name": "laptop", "scope": scope}, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_mcp_is_off_by_default_and_needs_a_valid_token(client, admin_user, auth_headers):
    assert _rpc(client, "x", "ping").status_code == 404
    tok = _setup(client, auth_headers)
    assert tok["token"].startswith("ptmcp_") and tok["prefix"] == tok["token"][:10]
    assert _rpc(client, None, "ping").status_code == 401
    assert _rpc(client, "ptmcp_wrong", "ping").status_code == 401
    # the panel's own login is not an MCP token
    jwt = auth_headers["Authorization"].split()[1]
    assert _rpc(client, jwt, "ping").status_code == 401
    assert _rpc(client, tok["token"], "ping", Origin="http://evil.example.com").status_code == 403
    assert _rpc(client, tok["token"], "ping").json() == {"jsonrpc": "2.0", "id": 1, "result": {}}

    listed = client.get("/api/assistant/mcp/tokens", headers=auth_headers).json()
    assert [t["name"] for t in listed] == ["laptop"] and "token" not in listed[0]
    assert listed[0]["last_used_at"] is not None
    assert client.delete(f"/api/assistant/mcp/tokens/{tok['id']}", headers=auth_headers).status_code == 204
    assert _rpc(client, tok["token"], "ping").status_code == 401
    assert client.get("/api/assistant/mcp/tokens", headers=auth_headers).json() == []
    assert client.post("/api/assistant/mcp/tokens", json={"name": "x", "scope": "root"},
                       headers=auth_headers).status_code == 400


def test_mcp_handshake_and_tool_lists_follow_the_scope(client, admin_user, auth_headers):
    tok = _setup(client, auth_headers, scope="read")
    r = _rpc(client, tok["token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                  "clientInfo": {"name": "t", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and res["serverInfo"]["name"] == "pitun"
    assert "tools" in res["capabilities"] and "approve" in res["instructions"]
    assert _rpc(client, tok["token"], "notifications/initialized", rid=None).status_code == 202
    names = {t["name"] for t in _rpc(client, tok["token"], "tools/list").json()["result"]["tools"]}
    assert {"search", "read", "find_nodes", "diagnose", "plan_status"} <= names
    assert not names & {"act", "route_device", "deploy_server", "remember", "web_search"}
    err = _rpc(client, tok["token"], "tools/call", {"name": "act", "arguments": {}}).json()["error"]
    assert err["code"] == -32602 and "read-only" in err["message"]
    assert _rpc(client, tok["token"], "nope").json()["error"]["code"] == -32601

    tok2 = _setup(client, auth_headers, scope="plan")
    tools = _rpc(client, tok2["token"], "tools/list").json()["result"]["tools"]
    by = {t["name"]: t for t in tools}
    assert "act" in by and "route_device" in by and by["act"]["annotations"]["readOnlyHint"] is False
    assert by["route_device"]["inputSchema"]["required"] == ["device"]


def test_mcp_reads_are_redacted(client, admin_user, auth_headers, session):
    tok = _setup(client, auth_headers, scope="read")
    _node(session, name="🇩🇪 de-1", address="203.0.113.77")
    r = _rpc(client, tok["token"], "tools/call", {"name": "read", "arguments": {"calls": [{"path": "/nodes"}]}})
    text = r.json()["result"]["content"][0]["text"]
    assert "de-1" in text and "203.0.113.77" not in text and "11111111-2222" not in text


def test_mcp_plan_waits_for_approval_then_reports(client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import AssistantConversation, RoutingRule
    tok = _setup(client, auth_headers)
    _device(session)
    _node(session, name="🇩🇪 de-1")
    r = _rpc(client, tok["token"], "tools/call",
             {"name": "route_device", "arguments": {"device": "tv", "country": "DE"}})
    res = r.json()["result"]
    assert res["isError"] is False and "waiting for a person to approve" in res["content"][0]["text"]
    pending = client.get("/api/assistant/plans", headers=auth_headers).json()
    assert len(pending) == 1 and pending[0]["source"] == "mcp" and pending[0]["conversation_title"] == "MCP: laptop"
    pid = pending[0]["id"]
    assert session.exec(select(RoutingRule)).all() == []

    st = _rpc(client, tok["token"], "tools/call", {"name": "plan_status", "arguments": {"plan_id": pid}})
    assert '"status": "pending"' in st.json()["result"]["content"][0]["text"]

    conv = session.exec(select(AssistantConversation).where(AssistantConversation.source == "mcp")).one()
    items = client.get(f"/api/assistant/conversations/{conv.id}", headers=auth_headers).json()["items"]
    assert any(i["kind"] == "plan" and i["plan"]["id"] == pid for i in items)

    # the panel's assistant is off: MCP plans can still be decided, without a model turn
    boom = mock.Mock(side_effect=AssertionError("no model for MCP plans"))
    with mock.patch("app.assistant.agent.make_provider", boom):
        evs = _events(client.post(f"/api/assistant/plans/{pid}/approve", headers=auth_headers))
    assert evs[0]["plan"]["status"] == "executed" and evs[-1] == {"type": "done"}
    assert len(session.exec(select(RoutingRule)).all()) == 1
    st = _rpc(client, tok["token"], "tools/call", {"name": "plan_status", "arguments": {"plan_id": pid}})
    assert '"status": "executed"' in st.json()["result"]["content"][0]["text"]
    assert client.get("/api/assistant/plans", headers=auth_headers).json() == []

    # another token cannot see this plan
    other = _setup(client, auth_headers)
    st = _rpc(client, other["token"], "tools/call", {"name": "plan_status", "arguments": {"plan_id": pid}})
    assert st.json()["result"]["isError"] is True


def test_mcp_cannot_reach_denied_routes(client, admin_user, auth_headers):
    tok = _setup(client, auth_headers)
    r = _rpc(client, tok["token"], "tools/call", {"name": "act", "arguments": {
        "title": "x", "summary": "x", "steps": [{"method": "POST", "path": "/assistant/mcp/tokens",
                                                 "body": {"name": "evil", "scope": "plan"}}]}})
    res = r.json()["result"]
    assert res["isError"] is True and "not available" in res["content"][0]["text"]
    r = _rpc(client, tok["token"], "tools/call", {"name": "read", "arguments": {"calls": [{"path": "/mcp"}]}})
    assert "not available" in r.json()["result"]["content"][0]["text"]
