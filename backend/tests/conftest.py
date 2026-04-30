"""Shared fixtures for PiTun tests."""
import json
import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest"
os.environ["XRAY_BINARY"] = "/bin/false"

from app.models import (
    User, Node, RoutingRule, Subscription, DNSRule, BalancerGroup,
    Settings, NodeCircle, Device, DNSQueryLog,
)
from app.core.auth import hash_password, create_access_token

# Pre-import singleton modules so `mock.patch("app.core.X.X.method")` can
# resolve. `app/core/__init__.py` only exports a few of them eagerly;
# without these imports, patch() fails with AttributeError on Windows/3.13
# because the submodule attribute on `app.core` isn't auto-populated in
# time (these are otherwise only imported lazily inside FastAPI's lifespan).
#
# Wrapped in try/except so tests that don't touch these modules can still
# run in local dev envs that skip optional deps (psutil, docker, etc.).
for _name in (
    "metrics_collector", "sub_scheduler", "circle_scheduler",
    "device_scanner", "naive_supervisor", "nftables",
):
    try:
        __import__(f"app.core.{_name}")
    except Exception:  # ModuleNotFoundError or ImportError from transitive dep
        pass
del _name


@pytest.fixture(name="engine")
def engine_fixture(tmp_path):
    db_path = str(tmp_path / "test.db")
    sync_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(sync_engine)

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    yield sync_engine, async_engine


@pytest.fixture(name="session")
def session_fixture(engine):
    sync_engine, _ = engine
    with Session(sync_engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(engine):
    _, async_engine = engine

    from app.main import app
    from app.database import get_session
    import app.database as db_mod

    old_async = db_mod._async_engine
    old_sync = db_mod._sync_engine
    db_mod._async_engine = async_engine

    async def get_session_override():
        async with AsyncSession(async_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override

    from unittest.mock import patch, AsyncMock

    with (
        patch("app.main.create_db_and_tables"),
        patch("app.main.init_default_settings", new_callable=AsyncMock),
        patch("app.core.xray.xray_manager.get_version", new_callable=AsyncMock, return_value=None),
        patch("app.core.healthcheck.health_checker.start"),
        patch("app.core.sub_scheduler.subscription_scheduler.start"),
        patch("app.core.circle_scheduler.circle_scheduler.start"),
        patch("app.core.device_scanner.device_scanner.start"),
        patch("app.core.metrics_collector.metrics_collector.start"),
        patch("app.core.naive_supervisor.naive_supervisor.start"),
        patch("app.core.naive_supervisor.naive_supervisor.stop"),
        patch("app.core.healthcheck.health_checker.stop"),
        patch("app.core.sub_scheduler.subscription_scheduler.stop"),
        patch("app.core.circle_scheduler.circle_scheduler.stop"),
        patch("app.core.device_scanner.device_scanner.stop"),
        patch("app.core.metrics_collector.metrics_collector.stop"),
        patch("app.core.xray.xray_manager.stop", new_callable=AsyncMock),
        patch("app.core.nftables.nftables_manager.flush", new_callable=AsyncMock),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    app.dependency_overrides.clear()
    db_mod._async_engine = old_async
    db_mod._sync_engine = old_sync


@pytest.fixture(name="admin_user")
def admin_user_fixture(session):
    user = User(username="admin", password_hash=hash_password("password"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="auth_headers")
def auth_headers_fixture(admin_user):
    token = create_access_token("admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(name="sample_node")
def sample_node_fixture(session):
    node = Node(
        name="Test VLESS", protocol="vless", address="1.2.3.4", port=443,
        uuid="test-uuid-1234", transport="ws", tls="tls", sni="example.com",
        enabled=True, order=0,
    )
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@pytest.fixture(name="sample_rule")
def sample_rule_fixture(session):
    rule = RoutingRule(
        name="Test rule", rule_type="domain", match_value="google.com",
        action="proxy", enabled=True, order=100,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@pytest.fixture(name="default_settings")
def default_settings_fixture(session):
    defaults = {
        "mode": "rules", "active_node_id": "", "bypass_private": "true",
        "dns_port": "5353", "tproxy_port_tcp": "7893", "tproxy_port_udp": "7894",
        "socks_port": "1080", "http_port": "8080", "inbound_mode": "tproxy",
        "log_level": "warning", "dns_upstream": "8.8.8.8", "dns_mode": "plain",
        "fakedns_enabled": "false", "dns_sniffing": "true", "block_quic": "true",
        "kill_switch": "false", "failover_enabled": "false", "failover_node_ids": "[]",
        "geoip_url": "", "geosite_url": "", "dns_query_log_enabled": "false",
        "dns_upstream_secondary": "", "dns_fallback": "8.8.8.8",
        "bypass_cn_dns": "false", "bypass_ru_dns": "false",
        "device_routing_mode": "all",
        "auto_restart_xray": "true",
    }
    for k, v in defaults.items():
        session.add(Settings(key=k, value=v))
    session.commit()


# ── Device fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(name="sample_device")
def sample_device_fixture(session):
    dev = Device(
        mac="aa:bb:cc:dd:ee:01", ip="192.168.1.100",
        name="Test Phone", vendor="TestVendor",
        is_online=True, routing_policy="default",
    )
    session.add(dev)
    session.commit()
    session.refresh(dev)
    return dev


@pytest.fixture(name="multiple_devices")
def multiple_devices_fixture(session):
    devices = []
    for i in range(3):
        dev = Device(
            mac=f"aa:bb:cc:dd:ee:{i:02x}",
            ip=f"192.168.1.{100 + i}",
            name=f"Device {i}",
            is_online=(i < 2),
            routing_policy=["default", "include", "exclude"][i],
        )
        session.add(dev)
        session.commit()
        session.refresh(dev)
        devices.append(dev)
    return devices


# ── DNS fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(name="sample_dns_rule")
def sample_dns_rule_fixture(session):
    rule = DNSRule(
        name="CN DNS", enabled=True, domain_match="geosite:cn",
        dns_server="114.114.114.114", dns_type="plain", order=100,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@pytest.fixture(name="sample_dns_queries")
def sample_dns_queries_fixture(session):
    entries = []
    domains = ["google.com", "google.com", "youtube.com", "github.com", "github.com"]
    for i, domain in enumerate(domains):
        entry = DNSQueryLog(
            domain=domain,
            resolved_ips=json.dumps([f"1.2.3.{i}"]),
            server_used="8.8.8.8",
            query_type="A",
            cache_hit=(i % 2 == 0),
        )
        session.add(entry)
        entries.append(entry)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    return entries


# ── Subscription fixtures ────────────────────────────────────────────────────

@pytest.fixture(name="sample_subscription")
def sample_subscription_fixture(session):
    sub = Subscription(
        name="Test Sub", url="https://example.com/sub",
        enabled=True, ua="clash",
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


# ── NodeCircle fixtures ──────────────────────────────────────────────────────

@pytest.fixture(name="sample_circle")
def sample_circle_fixture(session, sample_node):
    circle = NodeCircle(
        name="Test Circle", enabled=True,
        node_ids=json.dumps([sample_node.id]),
        mode="sequential", interval_min=5, interval_max=15,
    )
    session.add(circle)
    session.commit()
    session.refresh(circle)
    return circle
