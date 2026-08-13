"""Connection-lifetime policy must reach every xray config we generate.

Xray's defaults (connIdle 300, uplinkOnly 2, downlinkOnly 5) kill idle
pooled connections and cut half-closed streams — the failure mode that
looks like "long-lived connections drop for no reason". Nothing set these
before: chain templates carried stats flags only, PiTun's own config had
no `levels` at all, and a plain panel had no template whatsoever.
"""
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from app.core.xray_policy import (
    RECOMMENDED_TIMEOUTS,
    level_zero,
    merge_timeouts,
)

TIMEOUT_KEYS = ("handshake", "connIdle", "uplinkOnly", "downlinkOnly")


class TestPolicyValues:
    def test_half_close_timers_are_disabled(self):
        # 0 = "never cut a half-closed connection", which is what a
        # streaming response needs. Anything >0 reintroduces the bug.
        assert RECOMMENDED_TIMEOUTS["uplinkOnly"] == 0
        assert RECOMMENDED_TIMEOUTS["downlinkOnly"] == 0

    def test_idle_timeout_is_far_above_a_pool_keepalive(self):
        assert RECOMMENDED_TIMEOUTS["connIdle"] >= 1800

    def test_buffer_size_is_left_alone(self):
        # Raising it multiplies per-connection memory — wrong trade on a
        # Pi or a 1 GB VPS.
        assert "bufferSize" not in RECOMMENDED_TIMEOUTS

    def test_level_zero_merges_extra_keys(self):
        lv = level_zero(statsUserUplink=True)
        assert lv["statsUserUplink"] is True
        for key in TIMEOUT_KEYS:
            assert lv[key] == RECOMMENDED_TIMEOUTS[key]


class TestMergeTimeouts:
    """Adopting a panel's existing template must not trample it."""

    def test_creates_level_zero_when_missing(self):
        policy, changed = merge_timeouts({"system": {"statsInboundUplink": True}})
        assert changed
        assert policy["levels"]["0"]["connIdle"] == RECOMMENDED_TIMEOUTS["connIdle"]
        # Untouched neighbours survive.
        assert policy["system"] == {"statsInboundUplink": True}

    def test_preserves_existing_stats_flags(self):
        policy, changed = merge_timeouts({
            "levels": {"0": {"statsUserOnline": True, "statsUserUplink": True}},
        })
        assert changed
        lv = policy["levels"]["0"]
        assert lv["statsUserOnline"] is True
        assert lv["statsUserUplink"] is True
        assert lv["downlinkOnly"] == 0

    def test_applies_to_every_level_not_just_zero(self):
        policy, _ = merge_timeouts({"levels": {"0": {}, "7": {"statsUserUplink": True}}})
        for name in ("0", "7"):
            assert policy["levels"][name]["connIdle"] == 3600

    def test_is_idempotent(self):
        first, changed_first = merge_timeouts({})
        second, changed_second = merge_timeouts(first)
        assert changed_first is True
        assert changed_second is False
        assert first == second

    def test_survives_a_missing_or_malformed_policy(self):
        for junk in (None, "", [], "not-a-dict"):
            policy, changed = merge_timeouts(junk)
            assert changed
            assert policy["levels"]["0"]["uplinkOnly"] == 0


class TestGeneratedConfigsCarryThePolicy:
    def test_pitun_local_config_sets_level_zero(self):
        from app.core.config_gen import generate_config
        from app.models import Node

        node = Node(
            id=1, name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="tcp", tls="none", enabled=True,
        )
        cfg = generate_config(node, [node], [], "rules", {
            "mode": "rules", "log_level": "warning", "dns_port": "5353",
            "tproxy_port_tcp": "7893", "tproxy_port_udp": "7894",
            "socks_port": "1080", "http_port": "8080",
            "bypass_private": "true", "fakedns_enabled": "false",
            "dns_sniffing": "true", "inbound_mode": "tproxy",
            "dns_upstream": "8.8.8.8", "dns_mode": "plain",
            "dns_upstream_secondary": "", "dns_fallback": "",
            "bypass_cn_dns": "false", "bypass_ru_dns": "false",
        })
        level = cfg["policy"]["levels"]["0"]
        for key in TIMEOUT_KEYS:
            assert level[key] == RECOMMENDED_TIMEOUTS[key]

    def test_chain_template_sets_level_zero(self):
        from app.core.xui_chain import build_xray_template_config
        from app.models import ChainChannel, ProxyChain

        chain = ProxyChain(
            id=3, name="c", exit_xui_server_id=1, relay_xui_server_id=2,
            exit_sni="cover.example.net",
        )
        channel = ChainChannel(
            id=10, chain_id=3, name="alpha", order=0,
            exit_port=10443, relay_port=443, exit_xhttp_path="/api/v1/alpha",
            client_sni="example.com", exit_uuid="U", exit_pbk="P",
            exit_pvk="V", exit_sid="S",
            relay_pbk="rp", relay_pvk="rv", relay_sid="rs",
        )
        tpl = build_xray_template_config(
            chain=chain, channels=[channel], exit_host="1.2.3.4",
        )
        level = tpl["policy"]["levels"]["0"]
        for key in TIMEOUT_KEYS:
            assert level[key] == RECOMMENDED_TIMEOUTS[key]
        # Stats flags the panel and the UI rely on stay put.
        assert level["statsUserUplink"] is True


class TestApplyPolicyEndpoint:
    """The action patches whatever the panel already has — a bare panel
    has no template of ours, so inventing one would clobber its config."""

    def _panel(self, template):
        inst = mock.MagicMock()
        inst.get_xray_setting = AsyncMock(return_value={
            "xraySetting": template,
            "outboundTestUrl": "https://example.test/generate_204",
        })
        inst.push_xray_setting = AsyncMock(return_value=None)
        inst.restart_xray = AsyncMock(return_value=None)
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        return inst

    def _seed(self, session):
        from app.models import Server, XuiServer

        srv = Server(name="p", host="1.2.3.4", port=22, user="root", auth_type="key")
        session.add(srv)
        session.commit()
        session.refresh(srv)
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
            panel_port=12345, panel_basepath="/t", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)
        return xs

    def test_patches_a_panel_default_template(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        template = {
            "log": {"loglevel": "warning"},
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "policy": {"levels": {"0": {"statsUserOnline": True}}},
        }
        panel = self._panel(template)
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/apply-policy", headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["changed"] is True

        pushed = panel.push_xray_setting.await_args.args[0]
        level = pushed["policy"]["levels"]["0"]
        assert level["connIdle"] == 3600
        assert level["downlinkOnly"] == 0
        # The operator's own config is untouched.
        assert level["statsUserOnline"] is True
        assert pushed["outbounds"] == [{"tag": "direct", "protocol": "freedom"}]
        panel.restart_xray.assert_awaited_once()

    def test_second_run_is_a_no_op(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        template = {"policy": {"levels": {"0": dict(RECOMMENDED_TIMEOUTS)}}}
        panel = self._panel(template)
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/apply-policy", headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["changed"] is False
        panel.push_xray_setting.assert_not_awaited()
        panel.restart_xray.assert_not_awaited()

    def test_restart_can_be_skipped(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        panel = self._panel({"policy": {}})
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/apply-policy?restart=false",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        panel.push_xray_setting.assert_awaited_once()
        panel.restart_xray.assert_not_awaited()
        assert "restart Xray" in resp.json()["detail"]

    def test_unusable_template_is_reported_not_replaced(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        panel = self._panel(None)
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/apply-policy", headers=auth_headers,
            )
        assert resp.status_code == 502
        panel.push_xray_setting.assert_not_awaited()


class TestPolicyIsAppliedAutomatically:
    """A freshly deployed panel starts on xray's defaults. If nothing
    applies the policy for it, every new server silently reintroduces the
    connection-drop problem — so deploy and registration do it for you."""

    def _panel(self, template=None):
        inst = mock.MagicMock()
        inst.get_xray_setting = AsyncMock(return_value={
            "xraySetting": template if template is not None else {"policy": {}},
            "outboundTestUrl": "https://example.test/generate_204",
        })
        inst.push_xray_setting = AsyncMock(return_value=None)
        inst.restart_xray = AsyncMock(return_value=None)
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        return inst

    def test_helper_patches_and_restarts(self):
        import asyncio

        from app.core.xui_policy import apply_policy_to_panel

        panel = self._panel({"outbounds": [{"tag": "direct"}], "policy": {}})
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            res = asyncio.run(apply_policy_to_panel(
                base_url="http://panel.test/x", api_token="t",
            ))
        assert res.changed and res.restarted
        pushed = panel.push_xray_setting.await_args.args[0]
        assert pushed["policy"]["levels"]["0"]["connIdle"] == 3600
        assert pushed["outbounds"] == [{"tag": "direct"}]

    def test_helper_raises_so_the_action_can_report_it(self):
        import asyncio

        from app.core.xui_api import XuiAPIError
        from app.core.xui_policy import apply_policy_to_panel

        panel = self._panel()
        panel.get_xray_setting = AsyncMock(
            side_effect=XuiAPIError("panel down", kind="connect"),
        )
        with mock.patch("app.core.xui_policy.XuiClient", return_value=panel):
            with pytest.raises(XuiAPIError):
                asyncio.run(apply_policy_to_panel(
                    base_url="http://panel.test/x", api_token="t",
                ))

    def test_registration_applies_it(
        self, client, session, admin_user, auth_headers,
    ):
        from app.models import Server

        srv = Server(
            name="p", host="1.2.3.4", port=22, user="root", auth_type="key",
        )
        session.add(srv)
        session.commit()
        session.refresh(srv)

        probe = mock.MagicMock()
        probe.probe = AsyncMock(return_value=None)
        probe.__aenter__ = AsyncMock(return_value=probe)
        probe.__aexit__ = AsyncMock(return_value=False)

        applied = AsyncMock(return_value=mock.MagicMock(detail="ok"))
        with (
            mock.patch("app.api.xui.XuiClient", return_value=probe),
            mock.patch("app.core.xui_policy.apply_policy_to_panel", applied),
        ):
            resp = client.post(
                "/api/xui/servers/import",
                json={
                    "server_id": srv.id, "api_token": "tok",
                    "panel_user": "u", "panel_pass": "p",
                    "panel_port": 12345, "panel_basepath": "/t", "mode": "bare",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 201, resp.text
        # Registering a panel is the natural moment to fix its timeouts.
        applied.assert_awaited_once()

    def test_registration_survives_an_unreachable_panel(
        self, client, session, admin_user, auth_headers,
    ):
        import asyncio

        from app.api.xui import _best_effort_policy
        from app.models import Server, XuiServer

        srv = Server(
            name="p", host="1.2.3.4", port=22, user="root", auth_type="key",
        )
        session.add(srv)
        session.commit()
        session.refresh(srv)
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
            panel_port=12345, panel_basepath="/t", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)

        with mock.patch(
            "app.core.xui_policy.apply_policy_to_panel",
            new_callable=AsyncMock, side_effect=OSError("no route to host"),
        ):
            # Must not raise — a dead panel cannot break registration.
            asyncio.run(_best_effort_policy(xs, srv))


class TestSettingsDriveThePolicy:
    """The values moved into Settings so an operator can tune them without
    a code change — and so one place feeds both the local xray and the
    panels. Out-of-range values are rejected at the API boundary because
    the symptom of a bad one (an occasional drop) points nowhere near it."""

    def test_operator_values_win_over_the_defaults(self):
        from app.core.xray_policy import timeouts_from_settings

        got = timeouts_from_settings({"xray_conn_idle": "600", "xray_handshake": "4"})
        assert got["connIdle"] == 600
        assert got["handshake"] == 4
        # Untouched keys keep the recommended value.
        assert got["downlinkOnly"] == RECOMMENDED_TIMEOUTS["downlinkOnly"]

    @pytest.mark.parametrize("junk", ["", "abc", None, "-5", "999999999"])
    def test_unusable_values_fall_back_instead_of_breaking_the_config(self, junk):
        from app.core.xray_policy import timeouts_from_settings

        got = timeouts_from_settings({"xray_conn_idle": junk})
        assert got["connIdle"] == RECOMMENDED_TIMEOUTS["connIdle"]

    def test_keepalive_is_off_when_both_are_zero(self):
        from app.core.xray_policy import inbound_keepalive

        assert inbound_keepalive({
            "xray_tcp_keepalive_idle": "0", "xray_tcp_keepalive_interval": "0",
        }) == {}

    def test_keepalive_reaches_the_lan_proxy_inbounds_only(self):
        from app.core.config_gen import generate_config
        from app.models import Node

        node = Node(
            id=1, name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="tcp", tls="none", enabled=True,
        )
        cfg = generate_config(node, [node], [], "rules", {
            "mode": "rules", "log_level": "warning", "dns_port": "5353",
            "tproxy_port_tcp": "7893", "tproxy_port_udp": "7894",
            "socks_port": "1080", "http_port": "8080",
            "bypass_private": "true", "fakedns_enabled": "false",
            "dns_sniffing": "true", "inbound_mode": "tproxy",
            "dns_upstream": "8.8.8.8", "dns_mode": "plain",
            "dns_upstream_secondary": "", "dns_fallback": "",
            "bypass_cn_dns": "false", "bypass_ru_dns": "false",
            "xray_tcp_keepalive_idle": "100",
            "xray_tcp_keepalive_interval": "15",
        })
        by_tag = {i["tag"]: i for i in cfg["inbounds"]}
        for tag in ("socks-in", "http-in"):
            sock = by_tag[tag]["streamSettings"]["sockopt"]
            assert sock["tcpKeepAliveIdle"] == 100
            assert sock["tcpKeepAliveInterval"] == 15
        # Outbounds keep xray's own 45s/45s — overriding them would make
        # dead-path detection slower, not faster.
        for ob in cfg["outbounds"]:
            sock = (ob.get("streamSettings") or {}).get("sockopt") or {}
            assert "tcpKeepAliveIdle" not in sock

    @pytest.mark.parametrize(("key", "value"), [
        ("xray_conn_idle", 5),           # below the floor
        ("xray_conn_idle", 999999),      # above the ceiling
        ("xray_handshake", 0),
        ("xray_downlink_only", 99999),
    ])
    def test_api_rejects_out_of_range(
        self, client, admin_user, auth_headers, default_settings, key, value,
    ):
        resp = client.patch(
            "/api/system/settings", json={key: value}, headers=auth_headers,
        )
        assert resp.status_code == 400
        assert key in resp.json()["detail"]

    def test_api_accepts_a_sane_value(
        self, client, admin_user, auth_headers, default_settings,
    ):
        resp = client.patch(
            "/api/system/settings", json={"xray_conn_idle": 600},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 204), resp.text
        assert client.get(
            "/api/system/settings", headers=auth_headers,
        ).json()["xray_conn_idle"] == 600
