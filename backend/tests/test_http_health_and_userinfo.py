"""The active node is checked for real traffic, not only an open port; and a
panel that hands out placeholders says why."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.api.subscriptions import userinfo_problem
from app.core.healthcheck import HealthChecker as _HC

# Captured at import, before the conftest autouse stub replaces it.
_REAL_HTTP_OK = _HC._http_ok


def test_used_up_quota_is_named():
    gib = 1024 ** 3
    msg = userinfo_problem(f"upload=0; download={601 * gib}; total={600 * gib}; expire=0")
    assert "quota used up" in msg and "601 of 600" in msg


def test_expired_plan_is_named():
    past = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
    assert "expired" in userinfo_problem(f"upload=0; download=1; total=0; expire={past}")


def test_healthy_or_missing_header_says_nothing():
    future = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    assert userinfo_problem(f"upload=1; download=1; total=1000; expire={future}") == ""
    assert userinfo_problem("") == ""
    assert userinfo_problem("garbage") == ""


def _mk(session, **kw):
    from app.models import Node
    d = dict(name="N", protocol="vless", address="192.0.2.1", port=443, uuid="u",
             transport="tcp", enabled=True)
    d.update(kw)
    n = Node(**d)
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _setting(session, key, value):
    from app.models import Settings
    session.add(Settings(key=key, value=value))
    session.commit()


@pytest.mark.asyncio
async def test_a_node_with_an_open_port_but_no_traffic_triggers_failover(client, admin_user, session):
    from app.core.healthcheck import HealthChecker
    n = _mk(session)
    _setting(session, "active_node_id", str(n.id))
    _setting(session, "failover_enabled", "true")
    hc = HealthChecker()
    failovers = []

    async def fake_failover(self, node_id):
        failovers.append(node_id)

    with (
        patch.object(HealthChecker, "_probe_node", AsyncMock(return_value={"is_online": True, "latency_ms": 10})),
        patch.object(HealthChecker, "_http_ok", AsyncMock(return_value=False)),
        patch.object(HealthChecker, "_failover", fake_failover),
    ):
        from app.config import settings
        for _ in range(settings.health_fail_threshold):
            await hc._check_active_node()
    assert failovers == [n.id]


@pytest.mark.asyncio
async def test_a_working_node_is_not_failed_over_and_the_check_is_not_repeated_every_tick(client, admin_user, session):
    from app.core.healthcheck import HealthChecker
    n = _mk(session)
    _setting(session, "active_node_id", str(n.id))
    _setting(session, "failover_enabled", "true")
    hc = HealthChecker()
    http = AsyncMock(return_value=True)
    with (
        patch.object(HealthChecker, "_probe_node", AsyncMock(return_value={"is_online": True, "latency_ms": 10})),
        patch.object(HealthChecker, "_http_ok", http),
        patch.object(HealthChecker, "_failover", AsyncMock()) as fo,
    ):
        for _ in range(5):
            await hc._check_active_node()
    assert http.await_count == 1 and fo.await_count == 0


@pytest.mark.asyncio
async def test_without_failover_enabled_no_internet_check_is_run(client, admin_user, session):
    from app.core.healthcheck import HealthChecker
    n = _mk(session)
    _setting(session, "active_node_id", str(n.id))
    hc = HealthChecker()
    http = AsyncMock(return_value=False)
    with (
        patch.object(HealthChecker, "_probe_node", AsyncMock(return_value={"is_online": True, "latency_ms": 10})),
        patch.object(HealthChecker, "_http_ok", http),
    ):
        await hc._check_active_node()
    assert http.await_count == 0


@pytest.mark.asyncio
async def test_failover_skips_a_candidate_that_carries_no_traffic(client, admin_user, session):
    from app.core.healthcheck import HealthChecker
    from app.models import Settings
    dead = _mk(session, name="dead", address="192.0.2.2")
    live = _mk(session, name="live", address="192.0.2.3")
    failed = _mk(session, name="failed", address="192.0.2.4")
    _setting(session, "active_node_id", str(failed.id))
    _setting(session, "failover_enabled", "true")
    _setting(session, "failover_node_ids", json.dumps([dead.id, live.id]))
    hc = HealthChecker()

    async def http_ok(self, node_id):
        return node_id != dead.id

    with (
        patch.object(HealthChecker, "_probe_node", AsyncMock(return_value={"is_online": True, "latency_ms": 10})),
        patch.object(HealthChecker, "_http_ok", http_ok),
        patch.object(HealthChecker, "_reload_xray", AsyncMock()),
    ):
        await hc._failover(failed.id)
    session.expire_all()
    from sqlmodel import select
    row = session.exec(select(Settings).where(Settings.key == "active_node_id")).first()
    assert row.value == str(live.id)


def test_garbage_numbers_in_the_header_do_not_raise():
    assert userinfo_problem("upload=1; download=inf; total=10") == ""
    assert userinfo_problem("upload=1e400; download=nan; total=-5") == ""


def test_small_quotas_keep_decimals():
    mib = 1024 ** 2
    assert "0.50 of 0.50 GB" in userinfo_problem(f"upload=0; download={512 * mib}; total={512 * mib}")


@pytest.mark.asyncio
async def test_http_ok_blames_the_node_only_for_a_tunnel_that_carried_nothing(client, admin_user, session):
    from app.core.healthcheck import HealthChecker
    n = _mk(session)
    cases = [
        ({"ok": False, "detail": "cloudflare: ConnectError"}, False),
        ({"ok": False, "detail": "xray: SOCKS port never opened"}, True),
        ({"ok": True, "detail": "google"}, True),
    ]
    for res, expected in cases:
        with patch("app.core.speedtest.reachability_check", new=AsyncMock(return_value=res)):
            assert await _REAL_HTTP_OK(HealthChecker(), n.id) is expected
