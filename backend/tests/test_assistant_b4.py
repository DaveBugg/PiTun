"""AI assistant (1.8.0-beta.4): voice input and servers pasted into the chat."""
import json
from unittest import mock

import httpx
import pytest

from app.assistant.providers import ModelTurn
from tests.test_assistant_b2 import FakeProvider, _chat, _enable, _events, _plan


# ── pasted server credentials ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("203.0.113.5\nS3cr3t!", ("203.0.113.5", "S3cr3t!", "root", 22)),
    ("203.0.113.5\nS3cr3t!\nadmin\n2222", ("203.0.113.5", "S3cr3t!", "admin", 2222)),
    ("203.0.113.5\nS3cr3t!\n2222\nadmin", ("203.0.113.5", "S3cr3t!", "admin", 2222)),
    ("203.0.113.5\nS3cr3t!\n2222", ("203.0.113.5", "S3cr3t!", "root", 2222)),
    ("Add a server:\n203.0.113.5\npw", ("203.0.113.5", "pw", "root", 22)),
    ("ubuntu@203.0.113.9:2201\npw", ("203.0.113.9", "pw", "ubuntu", 2201)),
    ("vps.example.com\npw\nubuntu", ("vps.example.com", "pw", "ubuntu", 22)),
    ("vps.example.com\nmySecret123", ("vps.example.com", "mySecret123", "root", 22)),
    ("ip: 203.0.113.7\npassword: two words\nport: 2200", ("203.0.113.7", "two words", "root", 2200)),
    ("адрес: 203.0.113.7\nпароль: x\nлогин: admin", ("203.0.113.7", "x", "admin", 22)),
])
def test_server_paste_parses_the_template(text, want):
    from app.assistant.server_paste import parse
    c = parse(text)
    assert (c["host"], c["password"], c["user"], c["port"]) == want


@pytest.mark.parametrize("text", [
    "example.com\ndirect", "youtube.com\nproxy", "route the tv\nvia germany", "203.0.113.5",
    "203.0.113.5\npw\nnot a user name\n22", "203.0.113.5\npw\n99999", "hello there", "a\nb\nc\nd\ne\nf",
    "ip: 203.0.113.7\ncolour: red",
])
def test_server_paste_ignores_ordinary_messages(text):
    from app.assistant.server_paste import parse
    assert parse(text) is None


def test_pasted_server_becomes_a_plan_and_the_password_never_reaches_the_model(
        client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import AssistantMessage, AssistantPlan, Server
    _enable(client, auth_headers)
    fake = FakeProvider([ModelTurn(text="Check the plan and approve it.")])
    cid, evs = _chat(client, auth_headers, fake, "203.0.113.5\nS3cr3t-pass\n\n2222")
    assert evs[0] == {"type": "user_masked",
                      "text": "[Server credentials pasted: host 203.0.113.5, user root, port 2222, password hidden]"}
    plan = _plan(evs)
    assert plan["steps"][0]["body"]["password"] == "••••••"
    assert plan["steps"][1]["path"] == "/servers/{{step1.id}}/test"
    sent = json.dumps(fake.seen) + json.dumps(fake.systems)
    assert "S3cr3t-pass" not in sent and "203.0.113.5" not in sent
    # one phrasing call; any tool call in it is ignored
    assert len(fake.seen) == 1 and f"plan #{plan['id']}" in fake.systems[0]
    stored = " ".join(r.content for r in session.exec(select(AssistantMessage)).all())
    assert "S3cr3t-pass" not in stored
    row = session.exec(select(AssistantPlan)).one()
    assert json.loads(row.steps)[0]["body"]["password"] == "S3cr3t-pass"

    conv = client.get(f"/api/assistant/conversations/{cid}", headers=auth_headers).json()
    kinds = [i["kind"] for i in conv["items"]]
    assert kinds[:2] == ["user", "plan"] and "203.0.113.5" in conv["items"][0]["text"]
    assert conv["title"] == "Add server"

    probe = mock.AsyncMock(return_value={"server_id": 1, "ok": True, "latency_ms": 40, "error": None,
                                         "remote_info": "Linux"})
    with mock.patch("app.api.servers._probe_one", probe), \
            mock.patch("app.assistant.agent.make_provider", return_value=FakeProvider([ModelTurn(text="ok")])):
        evs = _events(client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers))
    view = evs[0]["plan"]
    assert view["status"] == "executed", view["results"]
    srv = session.exec(select(Server)).one()
    assert (srv.host, srv.port, srv.user, srv.password) == ("203.0.113.5", 2222, "root", "S3cr3t-pass")
    assert view["results"][1]["body"]["ok"] is True


def test_pasted_hostname_credentials_hide_password_from_model_and_history(
        client, admin_user, auth_headers, session):
    from sqlmodel import select
    from app.models import AssistantMessage, AssistantPlan, Server
    _enable(client, auth_headers)
    fake = FakeProvider([ModelTurn(text="Check the plan and approve it.")])
    cid, evs = _chat(client, auth_headers, fake, "vps.example.com\nmySecret123")
    assert evs[0] == {"type": "user_masked",
                      "text": "[Server credentials pasted: host vps.example.com, user root, port 22, password hidden]"}
    plan = _plan(evs)
    assert plan["steps"][0]["body"]["password"] == "••••••"
    assert plan["steps"][1]["path"] == "/servers/{{step1.id}}/test"
    sent = json.dumps(fake.seen) + json.dumps(fake.systems)
    assert "mySecret123" not in sent
    assert "vps.example.com" not in sent
    stored = " ".join(r.content for r in session.exec(select(AssistantMessage)).all())
    assert "mySecret123" not in stored
    row = session.exec(select(AssistantPlan)).one()
    assert json.loads(row.steps)[0]["body"]["password"] == "mySecret123"

    probe = mock.AsyncMock(return_value={"server_id": 1, "ok": True, "latency_ms": 40, "error": None,
                                         "remote_info": "Linux"})
    with mock.patch("app.api.servers._probe_one", probe), \
            mock.patch("app.assistant.agent.make_provider", return_value=FakeProvider([ModelTurn(text="ok")])):
        evs = _events(client.post(f"/api/assistant/plans/{plan['id']}/approve", headers=auth_headers))
    view = evs[0]["plan"]
    assert view["status"] == "executed", view["results"]
    srv = session.exec(select(Server)).one()
    assert (srv.host, srv.port, srv.user, srv.password) == ("vps.example.com", 22, "root", "mySecret123")
    assert view["results"][1]["body"]["ok"] is True


def test_pasting_a_known_server_updates_its_access(client, admin_user, auth_headers, session):
    from tests.test_assistant_b3 import _server
    srv = _server(session)
    _enable(client, auth_headers)
    _cid, evs = _chat(client, auth_headers, FakeProvider([ModelTurn(text="ok")]), f"{srv.host}\nnew-pass\nadmin")
    plan = _plan(evs)
    assert plan["steps"][0]["method"] == "PATCH" and plan["steps"][0]["path"] == f"/servers/{srv.id}"
    assert plan["steps"][1]["path"] == f"/servers/{srv.id}/test"


# ── voice ─────────────────────────────────────────────────────────────────────

def test_voice_settings_and_targets(client, admin_user, auth_headers):
    from app.assistant.settings import AssistantSettings
    from app.assistant.voice import VoiceError, target
    view = _enable(client, auth_headers)
    assert view["voice_mode"] == "browser"
    assert client.patch("/api/assistant/settings", json={"voice_mode": "loud"},
                        headers=auth_headers).status_code == 400
    assert client.patch("/api/assistant/settings", json={"stt_url": "ftp://x"},
                        headers=auth_headers).status_code == 400
    v = client.patch("/api/assistant/settings", json={"voice_mode": "server", "stt_url": "http://192.168.50.9:8000/v1/",
                                                     "voice_lang": "RU"}, headers=auth_headers).json()
    assert (v["voice_mode"], v["stt_url"], v["voice_lang"]) == ("server", "http://192.168.50.9:8000/v1", "ru")

    s = AssistantSettings(provider="openrouter", voice_mode="provider", keys={"openrouter": "k"})
    with pytest.raises(VoiceError, match="no speech-to-text"):
        target(s)
    s = AssistantSettings(provider="openai", voice_mode="provider", keys={"openai": "k"}, egress="direct")
    assert target(s) == ("https://api.openai.com/v1", "k", None, "whisper-1")
    s = AssistantSettings(voice_mode="server", stt_url="http://192.168.50.9:8000/v1", stt_model="small")
    assert target(s) == ("http://192.168.50.9:8000/v1", "", None, "small")
    with pytest.raises(VoiceError):
        target(AssistantSettings(voice_mode="browser"))


def test_transcribe_endpoint_forwards_audio(client, admin_user, auth_headers):
    _enable(client, auth_headers)
    client.patch("/api/assistant/settings", json={"voice_mode": "server", "stt_url": "http://192.168.50.9:8000/v1"},
                 headers=auth_headers)
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": " route the tv through germany "})

    real = httpx.AsyncClient

    def fake_client(*a, **kw):
        kw.pop("proxy", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)

    with mock.patch("app.assistant.voice.httpx.AsyncClient", fake_client):
        r = client.post("/api/assistant/transcribe", headers=auth_headers, data={"language": "en"},
                        files={"audio": ("speech.webm", b"\x1aE\xdf\xa3fake", "audio/webm")})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "route the tv through germany"}
    assert seen["url"] == "http://192.168.50.9:8000/v1/audio/transcriptions"
    assert b'name="language"' in seen["body"] and b"fake" in seen["body"]

    client.patch("/api/assistant/settings", json={"voice_mode": "browser"}, headers=auth_headers)
    r = client.post("/api/assistant/transcribe", headers=auth_headers,
                    files={"audio": ("speech.webm", b"x", "audio/webm")})
    assert r.status_code == 400
