"""Tests for system API: mode, active-node, settings, start/stop/status with mocks."""
import pytest
from unittest.mock import AsyncMock, patch, PropertyMock


class TestMode:
    def test_set_mode_rules(self, client, admin_user, auth_headers, default_settings):
        resp = client.post("/api/system/mode", json={"mode": "rules"}, headers=auth_headers)
        assert resp.status_code == 204

    def test_set_mode_global(self, client, admin_user, auth_headers, default_settings):
        resp = client.post("/api/system/mode", json={"mode": "global"}, headers=auth_headers)
        assert resp.status_code == 204

    def test_set_mode_bypass(self, client, admin_user, auth_headers, default_settings):
        resp = client.post("/api/system/mode", json={"mode": "bypass"}, headers=auth_headers)
        assert resp.status_code == 204

    def test_set_mode_invalid(self, client, admin_user, auth_headers, default_settings):
        resp = client.post("/api/system/mode", json={"mode": "invalid"}, headers=auth_headers)
        assert resp.status_code == 422

    def test_mode_persists(self, client, admin_user, auth_headers, default_settings):
        client.post("/api/system/mode", json={"mode": "global"}, headers=auth_headers)
        resp = client.get("/api/system/settings", headers=auth_headers)
        assert resp.json()["mode"] == "global"


class TestActiveNode:
    def test_set_active_node(self, client, admin_user, auth_headers, default_settings, sample_node):
        resp = client.post(
            "/api/system/active-node",
            json={"node_id": sample_node.id},
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_set_active_node_not_found(self, client, admin_user, auth_headers, default_settings):
        resp = client.post(
            "/api/system/active-node",
            json={"node_id": 9999},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSettings:
    def test_get_settings(self, client, admin_user, auth_headers, default_settings):
        resp = client.get("/api/system/settings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "rules"
        assert data["dns_mode"] == "plain"
        assert data["dns_upstream"] == "8.8.8.8"
        assert data["tproxy_port_tcp"] == 7893
        assert data["block_quic"] is True
        assert data["device_routing_mode"] == "all"

    def test_update_settings(self, client, admin_user, auth_headers, default_settings):
        resp = client.patch(
            "/api/system/settings",
            json={"socks_port": 1090, "block_quic": False, "device_routing_mode": "include_only"},
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp2 = client.get("/api/system/settings", headers=auth_headers)
        data = resp2.json()
        assert data["socks_port"] == 1090
        assert data["block_quic"] is False
        assert data["device_routing_mode"] == "include_only"

    def test_update_failover_settings(self, client, admin_user, auth_headers, default_settings, sample_node):
        resp = client.patch(
            "/api/system/settings",
            json={"failover_enabled": True, "failover_node_ids": [sample_node.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp2 = client.get("/api/system/settings", headers=auth_headers)
        assert resp2.json()["failover_enabled"] is True
        assert resp2.json()["failover_node_ids"] == [sample_node.id]


class TestLanProxyAuth:
    """LAN proxy auth on the explicit SOCKS5 + HTTP inbounds —
    settings round-trip, validation, and config_gen account injection.
    Added in v1.3.0-beta.6."""

    def test_default_off(self, client, admin_user, auth_headers, default_settings):
        resp = client.get("/api/system/settings", headers=auth_headers)
        body = resp.json()
        assert body["lan_proxy_auth_enabled"] is False
        assert body["lan_proxy_auth_user"] == ""
        assert body["lan_proxy_auth_pass"] == ""

    def test_enable_with_creds_succeeds(
        self, client, admin_user, auth_headers, default_settings,
    ):
        resp = client.patch(
            "/api/system/settings",
            json={
                "lan_proxy_auth_enabled": True,
                "lan_proxy_auth_user": "alice",
                "lan_proxy_auth_pass": "wonderland",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 204
        body = client.get("/api/system/settings", headers=auth_headers).json()
        assert body["lan_proxy_auth_enabled"] is True
        assert body["lan_proxy_auth_user"] == "alice"
        assert body["lan_proxy_auth_pass"] == "wonderland"

    def test_enable_without_creds_rejected(
        self, client, admin_user, auth_headers, default_settings,
    ):
        # Empty user → 400. Don't let xray start with auth=password +
        # empty accounts (would crash with an opaque error).
        resp = client.patch(
            "/api/system/settings",
            json={"lan_proxy_auth_enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "lan_proxy_auth_user" in resp.json()["detail"].lower() or \
               "non-empty" in resp.json()["detail"].lower()

    def test_enable_then_disable_roundtrip(
        self, client, admin_user, auth_headers, default_settings,
    ):
        # Enable with creds.
        client.patch(
            "/api/system/settings",
            json={
                "lan_proxy_auth_enabled": True,
                "lan_proxy_auth_user": "u",
                "lan_proxy_auth_pass": "p",
            },
            headers=auth_headers,
        )
        # Disable (no creds change needed).
        resp = client.patch(
            "/api/system/settings",
            json={"lan_proxy_auth_enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 204
        body = client.get("/api/system/settings", headers=auth_headers).json()
        assert body["lan_proxy_auth_enabled"] is False
        # Saved creds are kept around so re-enabling doesn't force the
        # user to re-type — same DX as the Dashboard inline widget.
        assert body["lan_proxy_auth_user"] == "u"
        assert body["lan_proxy_auth_pass"] == "p"

    def test_config_gen_injects_accounts_when_enabled(self):
        """generate_config wraps the SOCKS5 + HTTP inbounds with
        `accounts` and flips SOCKS to `auth: password` when LAN auth
        is on."""
        from app.core.config_gen import generate_config

        cfg = generate_config(
            active_node=None,
            all_nodes=[],
            rules=[],
            mode="rules",
            settings_map={
                "mode": "rules",
                "lan_proxy_auth_enabled": "true",
                "lan_proxy_auth_user": "alice",
                "lan_proxy_auth_pass": "secret",
            },
        )
        socks_in = next(i for i in cfg["inbounds"] if i.get("tag") == "socks-in")
        http_in = next(i for i in cfg["inbounds"] if i.get("tag") == "http-in")
        assert socks_in["settings"]["auth"] == "password"
        assert socks_in["settings"]["accounts"] == [{"user": "alice", "pass": "secret"}]
        assert http_in["settings"]["accounts"] == [{"user": "alice", "pass": "secret"}]

    def test_config_gen_passwordless_when_disabled(self):
        from app.core.config_gen import generate_config
        cfg = generate_config(
            active_node=None,
            all_nodes=[],
            rules=[],
            mode="rules",
            settings_map={"mode": "rules"},
        )
        socks_in = next(i for i in cfg["inbounds"] if i.get("tag") == "socks-in")
        http_in = next(i for i in cfg["inbounds"] if i.get("tag") == "http-in")
        # SOCKS stays noauth, HTTP `settings` empty — same shape as
        # before the LAN-auth feature shipped.
        assert socks_in["settings"]["auth"] == "noauth"
        assert "accounts" not in socks_in["settings"]
        assert http_in["settings"] == {}

    def test_config_gen_raises_on_enabled_with_empty_creds(self):
        """Defence in depth — even if a bad PATCH slips past the API
        validator (or someone hand-edits the DB), config_gen refuses
        to build xray config with auth-on + empty accounts."""
        import pytest
        from app.core.config_gen import generate_config
        with pytest.raises(ValueError, match="lan_proxy_auth"):
            generate_config(
                active_node=None,
                all_nodes=[],
                rules=[],
                mode="rules",
                settings_map={
                    "mode": "rules",
                    "lan_proxy_auth_enabled": "true",
                    # user + pass empty
                },
            )


class TestStatusWithMock:
    def test_get_status(self, client, admin_user, auth_headers, default_settings):
        with (
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
        ):
            resp = client.get("/api/system/status", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["running"] is False
            assert data["mode"] == "rules"

    def test_status_includes_app_version(self, client, admin_user, auth_headers, default_settings):
        # `app_version` is sourced from `app.config.APP_VERSION` and
        # surfaced for the version popover. v1.2.7 also added
        # `last_xray_validation_error`; cover both fields together
        # since they share the same handler path.
        with (
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
        ):
            resp = client.get("/api/system/status", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert "app_version" in data
            # Either set to a real version string or null; never missing
            assert "last_xray_validation_error" in data

    def test_status_surfaces_xray_validation_error(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # When `config_gen.write_config` has persisted a
        # `last_xray_validation_error` Settings row (because
        # `xray run -test` rejected the last config), `/system/status`
        # must echo it so the frontend banner can render. v1.2.7.
        from app.models import Settings as DBSettings
        session.add(DBSettings(
            key="last_xray_validation_error",
            value="Routing rule references geosite tag 'category-telemetry' which is NOT present...",
        ))
        session.commit()

        with (
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
        ):
            resp = client.get("/api/system/status", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["last_xray_validation_error"] is not None
            assert "category-telemetry" in data["last_xray_validation_error"]

    def test_status_omits_validation_error_when_clean(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # Empty Settings value → response field is None, not the empty
        # string. Frontend conditional renders the banner only when
        # truthy.
        from app.models import Settings as DBSettings
        session.add(DBSettings(key="last_xray_validation_error", value=""))
        session.commit()

        with (
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
        ):
            resp = client.get("/api/system/status", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["last_xray_validation_error"] is None


class TestStartStopWithMock:
    def test_start_proxy(self, client, admin_user, auth_headers, default_settings, sample_node):
        client.post(
            "/api/system/active-node", json={"node_id": sample_node.id}, headers=auth_headers
        )
        with (
            patch("app.core.config_gen.generate_config", return_value={}),
            # `write_config` returns Optional[(kind, tag)] since v1.2.7 —
            # None means validation passed. Default AsyncMock returns a
            # MagicMock (truthy), which would trick `_regenerate_and_write`
            # into self-heal recursion. Pin to None for happy-path tests.
            patch("app.core.config_gen.write_config", new_callable=AsyncMock, return_value=None),
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
            patch("app.core.device_scanner.get_device_macs_for_mode", _make_mock_device_macs()),
        ):
            resp = client.post("/api/system/start", headers=auth_headers)
            assert resp.status_code == 204

    def test_stop_proxy(self, client, admin_user, auth_headers, default_settings):
        with (
            patch("app.core.xray.xray_manager", _make_mock_xray()),
            patch("app.core.nftables.nftables_manager", _make_mock_nftables()),
        ):
            resp = client.post("/api/system/stop", headers=auth_headers)
            assert resp.status_code == 204


def _make_mock_xray():
    m = AsyncMock()
    m.is_running = False
    m.pid = None
    m.uptime = 0
    m.version = "1.8.0"
    m.get_version = AsyncMock(return_value="1.8.0")
    m.start = AsyncMock()
    m.stop = AsyncMock()
    m.restart = AsyncMock()
    m.reload = AsyncMock()
    return m


def _make_mock_nftables():
    m = AsyncMock()
    m.is_active = AsyncMock(return_value=False)
    m.apply_rules = AsyncMock()
    m.flush = AsyncMock()
    return m


def _make_mock_device_macs():
    async def fn(*args, **kwargs):
        return {"mode": "all", "include_macs": [], "exclude_macs": []}
    return fn


class TestMetrics:
    def test_get_metrics_empty(self, client, admin_user, auth_headers, default_settings):
        resp = client.get("/api/system/metrics", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_metrics_with_data(self, client, admin_user, auth_headers, default_settings, session):
        from datetime import datetime, timezone
        from app.models import SystemMetric

        for i in range(3):
            m = SystemMetric(
                ts=datetime.now(timezone.utc),
                cpu_percent=10.0 + i,
                ram_used_mb=500.0,
                ram_total_mb=1024.0,
                disk_used_gb=5.0,
                disk_total_gb=32.0,
                net_sent_bytes=1000 * (i + 1),
                net_recv_bytes=2000 * (i + 1),
            )
            session.add(m)
        session.commit()

        # Clear cache so our fresh data is returned
        from app.api.system import _metrics_cache
        _metrics_cache["ts"] = 0.0

        resp = client.get("/api/system/metrics?period=1h", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert "ts" in data[0]
        assert "cpu" in data[0]
        assert data[0]["ram_total"] == 1024.0

    def test_get_metrics_invalid_period_defaults(self, client, admin_user, auth_headers, default_settings):
        resp = client.get("/api/system/metrics?period=invalid", headers=auth_headers)
        assert resp.status_code == 200  # falls back to 1h

    def test_get_metrics_all_periods(self, client, admin_user, auth_headers, default_settings):
        for period in ["15m", "1h", "3h", "6h", "12h", "1d", "3d"]:
            resp = client.get(f"/api/system/metrics?period={period}", headers=auth_headers)
            assert resp.status_code == 200
