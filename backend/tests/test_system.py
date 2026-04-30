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


class TestStartStopWithMock:
    def test_start_proxy(self, client, admin_user, auth_headers, default_settings, sample_node):
        client.post(
            "/api/system/active-node", json={"node_id": sample_node.id}, headers=auth_headers
        )
        with (
            patch("app.core.config_gen.generate_config", return_value={}),
            patch("app.core.config_gen.write_config", new_callable=AsyncMock),
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
