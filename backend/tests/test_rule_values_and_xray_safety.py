"""A routing rule with values of the wrong kind must not take xray down.

Seen on a real box: 16 domains saved as a dst_ip rule -> xray refused the
config, died, and TPROXY kept sending the LAN into a dead port.
"""
import json
from types import SimpleNamespace
from unittest import mock

import pytest


# ── value checks ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rule_type,value", [
    ("dst_ip", "203.0.113.5,198.51.100.0/24,geoip:ru,2001:db8::/32"),
    ("src_ip", "192.168.50.20"),
    ("domain", "example.com,пример.рф,geosite:cn,full:a.example.com,keyword:video,regexp:^x\\.example$"),
    ("port", "443,1000-2000"),
    ("protocol", "bittorrent,quic"),
    ("geoip", "ru,private"),
    ("geosite", "category-ads-all"),
    ("mac", "aa:bb:cc:dd:ee:ff"),
])
def test_valid_values_pass(rule_type, value):
    from app.core.rule_values import check
    assert check(rule_type, value) is None


@pytest.mark.parametrize("rule_type,value,hint", [
    ("dst_ip", "video.example.com,203.0.113.5", "Choose the rule type 'domain'"),
    ("domain", "203.0.113.5", "rule type 'dst_ip'"),
    ("domain", "https://example.com/watch", "without https://"),
    ("port", "70000", "ports or ranges"),
    ("port", "2000-1000", "ports or ranges"),
    ("protocol", "ftp", "http, tls, quic, bittorrent"),
    ("mac", "aa:bb", "MAC"),
    ("dst_ip", "", "no values"),
])
def test_wrong_values_are_explained(rule_type, value, hint):
    from app.core.rule_values import check
    assert hint in check(rule_type, value)


# ── API ───────────────────────────────────────────────────────────────────────

def test_api_refuses_domains_in_an_ip_rule(client, admin_user, auth_headers):
    body = {"name": "Video", "rule_type": "dst_ip", "match_value": "video.example.com,cdn.example.net",
            "action": "proxy", "order": 30, "enabled": True}
    r = client.post("/api/routing/rules", json=body, headers=auth_headers)
    assert r.status_code == 400 and "rule type 'domain'" in r.json()["detail"]
    r = client.post("/api/routing/rules", json={**body, "rule_type": "domain"}, headers=auth_headers)
    assert r.status_code == 201
    rid = r.json()["id"]
    # switching the type without fixing the values is refused too
    r = client.patch(f"/api/routing/rules/{rid}", json={"rule_type": "dst_ip"}, headers=auth_headers)
    assert r.status_code == 400
    # other fields can still be patched
    assert client.patch(f"/api/routing/rules/{rid}", json={"enabled": False}, headers=auth_headers).status_code == 200
    r = client.post("/api/routing/rules/bulk", json={"rule_type": "dst_ip", "action": "direct",
                                                     "values": "example.com\n203.0.113.9"}, headers=auth_headers)
    assert r.status_code == 400


# ── config generation ─────────────────────────────────────────────────────────

def test_generator_skips_values_xray_would_refuse():
    from app.core.config_gen import _routing_rule_to_xray
    rule = SimpleNamespace(id=7, name="Video", rule_type="dst_ip", action="proxy",
                           match_value="video.example.com,203.0.113.5,cdn.example.net")
    out = _routing_rule_to_xray(rule, active_node_id=3)
    assert out["ip"] == ["203.0.113.5"] and out["outboundTag"] == "node-3"
    rule.match_value = "video.example.com,cdn.example.net"
    assert _routing_rule_to_xray(rule, active_node_id=3) is None


@pytest.mark.asyncio
async def test_rejected_config_keeps_the_previous_one(tmp_path, monkeypatch):
    from app.core import config_gen
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"good": True}))
    binary = tmp_path / "xray"
    binary.write_text("")
    monkeypatch.setattr(config_gen.settings, "xray_config_path", str(cfg))
    monkeypatch.setattr(config_gen.settings, "xray_binary", str(binary))

    class Proc:
        returncode = 23

        async def communicate(self):
            return (b"Failed to start: main: failed to load config files > illegal ip rule: x.example\n", b"")

    async def fake_exec(*a, **kw):
        return Proc()

    saved = []
    monkeypatch.setattr(config_gen, "_persist_validation_error", mock.AsyncMock(side_effect=saved.append))
    monkeypatch.setattr(config_gen.asyncio, "create_subprocess_exec", fake_exec)
    await config_gen.write_config({"good": False}, validate=True, settings_map={})
    assert json.loads(cfg.read_text()) == {"good": True}
    assert saved and "illegal ip rule" in str(saved[-1])


# ── reload brings a crashed xray back, not a stopped one ─────────────────────

@pytest.mark.asyncio
async def test_reload_restarts_a_crashed_xray_but_not_a_stopped_one(client):
    from app.core import dataplane
    from app.core.xray import xray_manager
    started = mock.AsyncMock()
    with mock.patch.object(type(xray_manager), "is_running", new_callable=mock.PropertyMock, return_value=False), \
            mock.patch.object(xray_manager, "start", started), \
            mock.patch("app.api.system._apply_nftables", mock.AsyncMock()):
        xray_manager.wanted = False
        await dataplane.apply_dataplane("reload")
        assert started.await_count == 0
        xray_manager.wanted = True
        await dataplane.apply_dataplane("reload")
        assert started.await_count == 1
    xray_manager.wanted = False
