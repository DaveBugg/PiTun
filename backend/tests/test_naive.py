"""Tests for NaiveProxy integration — URI parser, config_gen, and
naive_manager with the docker SDK mocked out.

These are standalone unit tests and don't touch the DB or the network."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.uri_parser import parse_uri


# ── URI parser ───────────────────────────────────────────────────────────────

class TestNaiveURIParser:
    def test_basic_naive_uri(self):
        uri = "naive+https://alice:secret@proxy.example.com:443/?padding=1#My%20Naive"
        n = parse_uri(uri)
        assert n is not None
        assert n["protocol"] == "naive"
        assert n["address"] == "proxy.example.com"
        assert n["port"] == 443
        assert n["uuid"] == "alice"         # user in uuid slot
        assert n["password"] == "secret"
        assert n["tls"] == "tls"
        assert n["sni"] == "proxy.example.com"
        assert n["naive_padding"] is True
        assert n["name"] == "My Naive"

    def test_default_port_is_443(self):
        uri = "naive+https://u:p@example.com"
        n = parse_uri(uri)
        assert n is not None
        assert n["port"] == 443

    def test_padding_disabled(self):
        uri = "naive+https://u:p@example.com?padding=0#Node"
        n = parse_uri(uri)
        assert n is not None
        assert n["naive_padding"] is False

    def test_padding_default_on_without_param(self):
        uri = "naive+https://u:p@example.com#Node"
        n = parse_uri(uri)
        assert n is not None
        # Spec default in _parse_naive: "1" → on
        assert n["naive_padding"] is True

    def test_missing_auth_still_parses(self):
        uri = "naive+https://example.com:8443#Open"
        n = parse_uri(uri)
        assert n is not None
        assert n["uuid"] == ""
        assert n["password"] == ""
        assert n["port"] == 8443

    def test_unknown_scheme_not_parsed(self):
        assert parse_uri("magic+https://x") is None


# ── config_gen ───────────────────────────────────────────────────────────────

class TestNaiveOutbound:
    def test_outbound_uses_loopback_socks(self):
        from app.core.config_gen import _outbound_naive
        from app.models import Node

        node = Node(
            id=42,
            name="Naive-1",
            protocol="naive",
            address="proxy.example.com",
            port=443,
            uuid="user",
            password="pass",
            internal_port=20801,
            naive_padding=True,
        )
        ob = _outbound_naive(node)
        assert ob["protocol"] == "socks"
        assert ob["tag"] == "node-42"
        server = ob["settings"]["servers"][0]
        assert server["address"] == "127.0.0.1"
        assert server["port"] == 20801
        # SO_MARK for kill-switch consistency
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_outbound_raises_without_port(self):
        from app.core.config_gen import _outbound_naive
        from app.models import Node

        node = Node(
            id=7, name="n", protocol="naive", address="x", port=443,
            internal_port=None,
        )
        with pytest.raises(ValueError, match="internal_port"):
            _outbound_naive(node)

    def test_build_outbound_dispatches_naive(self):
        from app.core.config_gen import _build_outbound
        from app.models import Node

        node = Node(
            id=1, name="n", protocol="naive", address="x", port=443,
            internal_port=20800,
        )
        ob = _build_outbound(node)
        assert ob["protocol"] == "socks"
        assert ob["tag"] == "node-1"


# ── naive_manager ────────────────────────────────────────────────────────────

class TestNaiveManager:
    def test_container_name_and_path(self, tmp_path, monkeypatch):
        from app.core.naive_manager import NaiveManager
        from app.config import settings as app_settings

        monkeypatch.setattr(app_settings, "naive_config_dir", str(tmp_path))
        assert NaiveManager.container_name(5) == "pitun-naive-5"
        assert NaiveManager.config_path(5) == str(tmp_path / "5.json")

    def test_build_config_minimal(self):
        from app.core.naive_manager import NaiveManager
        from app.models import Node

        n = Node(
            id=1, name="n", protocol="naive",
            address="proxy.example.com", port=443,
            uuid="alice", password="secret",
            internal_port=20800, naive_padding=True,
        )
        cfg = NaiveManager._build_config(n)
        assert cfg["listen"] == "socks://127.0.0.1:20800"
        assert cfg["proxy"] == "https://alice:secret@proxy.example.com:443"
        assert cfg["padding"] is True

    def test_build_config_no_auth(self):
        from app.core.naive_manager import NaiveManager
        from app.models import Node

        n = Node(
            id=1, name="n", protocol="naive",
            address="open.example.com", port=8443,
            internal_port=20801, naive_padding=False,
        )
        cfg = NaiveManager._build_config(n)
        assert cfg["proxy"] == "https://open.example.com:8443"
        assert "padding" not in cfg

    def test_build_config_without_port_raises(self):
        from app.core.naive_manager import NaiveManager, NaiveManagerError
        from app.models import Node

        n = Node(id=1, name="n", protocol="naive", address="x", port=443)
        with pytest.raises(NaiveManagerError):
            NaiveManager._build_config(n)

    def test_write_config_sync_atomic(self, tmp_path, monkeypatch):
        from app.core.naive_manager import NaiveManager
        from app.config import settings as app_settings
        from app.models import Node
        import json

        monkeypatch.setattr(app_settings, "naive_config_dir", str(tmp_path))
        n = Node(
            id=9, name="n", protocol="naive",
            address="ex.com", port=443,
            uuid="u", password="p",
            internal_port=20802,
        )
        NaiveManager._write_config_sync(n)
        path = tmp_path / "9.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["listen"] == "socks://127.0.0.1:20802"
        assert "proxy" in data

    def test_delete_config_sync_missing_is_noop(self, tmp_path, monkeypatch):
        from app.core.naive_manager import NaiveManager
        from app.config import settings as app_settings

        monkeypatch.setattr(app_settings, "naive_config_dir", str(tmp_path))
        # Should not raise even if file doesn't exist
        NaiveManager._delete_config_sync(999)


# ── Port allocation ──────────────────────────────────────────────────────────

class TestNaiveAllocatePort:
    """`allocate_port` must pick the lowest free port in the configured range
    and raise when the range is exhausted. Uses the async DB engine fixture."""

    def test_allocates_lowest_free_port(self, engine, monkeypatch):
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.config import settings as app_settings
        from app.core.naive_manager import NaiveManager
        from app.models import Node

        monkeypatch.setattr(app_settings, "naive_port_range_start", 20800)
        monkeypatch.setattr(app_settings, "naive_port_range_end",   20802)

        sync_engine, async_engine = engine
        # Seed the DB with an existing naive node occupying 20800.
        from sqlmodel import Session
        with Session(sync_engine) as s:
            s.add(Node(
                id=1, name="existing", protocol="naive",
                address="x", port=443, internal_port=20800,
            ))
            s.commit()

        mgr = NaiveManager()

        async def _run() -> int:
            async with AsyncSession(async_engine) as s:
                return await mgr.allocate_port(s, node_id=2)

        port = asyncio.run(_run())
        assert port == 20801

    def test_exhaustion_raises(self, engine, monkeypatch):
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession
        from sqlmodel import Session
        from app.config import settings as app_settings
        from app.core.naive_manager import NaiveManager, NaiveManagerError
        from app.models import Node

        monkeypatch.setattr(app_settings, "naive_port_range_start", 20800)
        monkeypatch.setattr(app_settings, "naive_port_range_end",   20801)

        sync_engine, async_engine = engine
        with Session(sync_engine) as s:
            s.add(Node(id=1, name="a", protocol="naive", address="x", port=443,
                       internal_port=20800))
            s.add(Node(id=2, name="b", protocol="naive", address="x", port=443,
                       internal_port=20801))
            s.commit()

        mgr = NaiveManager()

        async def _run() -> int:
            async with AsyncSession(async_engine) as s:
                return await mgr.allocate_port(s, node_id=3)

        with pytest.raises(NaiveManagerError, match="No free naive ports"):
            asyncio.run(_run())


# ── Container lifecycle (docker SDK mocked) ─────────────────────────────────

class TestNaiveLifecycle:
    """Start/stop/sync paths with a mocked docker client. Verifies that the
    manager writes config, starts container with the right args, and cleans
    up on stop."""

    def _patched_manager(self, tmp_path, monkeypatch):
        """Build a NaiveManager with a MagicMock docker client injected,
        and naive_config_dir pointed at tmp_path.
        Returns (manager, client_mock)."""
        from app.core.naive_manager import NaiveManager
        from app.config import settings as app_settings

        monkeypatch.setattr(app_settings, "naive_config_dir", str(tmp_path))
        monkeypatch.setattr(app_settings, "naive_image", "pitun-naive:latest")

        # Fake `docker` module with error classes the manager references.
        fake_docker = MagicMock()
        class _NotFound(Exception): pass
        class _APIError(Exception): pass
        class _ImageNotFound(Exception): pass
        fake_docker.errors = SimpleNamespace(
            NotFound=_NotFound, APIError=_APIError, ImageNotFound=_ImageNotFound,
        )
        # docker.types.LogConfig(...) → harmless stub
        fake_docker.types = SimpleNamespace(
            LogConfig=MagicMock(return_value=None,
                                types=SimpleNamespace(JSON="json-file")),
        )
        monkeypatch.setitem(sys.modules, "docker", fake_docker)

        mgr = NaiveManager()
        client = MagicMock()
        mgr._client = client
        return mgr, client, fake_docker

    def test_start_node_writes_config_and_runs(self, tmp_path, monkeypatch):
        import asyncio
        from app.models import Node

        mgr, client, fake_docker = self._patched_manager(tmp_path, monkeypatch)
        # `containers.get(name)` raises NotFound → nothing to remove first
        client.containers.get.side_effect = fake_docker.errors.NotFound
        client.containers.run.return_value = MagicMock()

        node = Node(
            id=5, name="n", protocol="naive", address="ex.com", port=443,
            uuid="u", password="p", internal_port=20850, naive_padding=True,
        )
        asyncio.run(mgr.start_node(node))

        # Config written atomically to <dir>/<id>.json
        cfg = tmp_path / "5.json"
        assert cfg.exists()

        # `containers.run` called exactly once with our image + host netns
        assert client.containers.run.call_count == 1
        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["image"] == "pitun-naive:latest"
        assert kwargs["name"] == "pitun-naive-5"
        assert kwargs["network_mode"] == "host"
        # Read-only + no-new-privileges hardening present
        assert kwargs["read_only"] is True
        assert "no-new-privileges" in kwargs["security_opt"]

    def test_start_node_recreates_existing_container(self, tmp_path, monkeypatch):
        import asyncio
        from app.models import Node

        mgr, client, _ = self._patched_manager(tmp_path, monkeypatch)
        # First call (remove step): container exists → removed
        old_container = MagicMock()
        client.containers.get.return_value = old_container

        node = Node(id=7, name="n", protocol="naive", address="x", port=443,
                    internal_port=20851)
        asyncio.run(mgr.start_node(node))
        old_container.remove.assert_called_once_with(force=True)
        client.containers.run.assert_called_once()

    def test_start_node_without_port_raises(self, tmp_path, monkeypatch):
        import asyncio
        from app.core.naive_manager import NaiveManagerError
        from app.models import Node

        mgr, *_ = self._patched_manager(tmp_path, monkeypatch)
        node = Node(id=1, name="n", protocol="naive", address="x", port=443)

        with pytest.raises(NaiveManagerError, match="internal_port"):
            asyncio.run(mgr.start_node(node))

    def test_start_node_skips_non_naive(self, tmp_path, monkeypatch):
        import asyncio
        from app.models import Node

        mgr, client, _ = self._patched_manager(tmp_path, monkeypatch)
        node = Node(id=1, name="n", protocol="vless",
                    address="x", port=443, uuid="u", internal_port=20800)
        asyncio.run(mgr.start_node(node))
        client.containers.run.assert_not_called()

    def test_stop_node_removes_container_and_config(self, tmp_path, monkeypatch):
        import asyncio

        mgr, client, _ = self._patched_manager(tmp_path, monkeypatch)
        c = MagicMock()
        client.containers.get.return_value = c

        # Place a stale config file
        cfg = tmp_path / "99.json"
        cfg.write_text("{}")

        asyncio.run(mgr.stop_node(99))
        c.remove.assert_called_once_with(force=True)
        assert not cfg.exists()

    def test_stop_node_missing_is_idempotent(self, tmp_path, monkeypatch):
        import asyncio

        mgr, client, fake_docker = self._patched_manager(tmp_path, monkeypatch)
        client.containers.get.side_effect = fake_docker.errors.NotFound
        # No config file, no container. Must not raise.
        asyncio.run(mgr.stop_node(1234))


# ── _collect_naive_bypass_dsts (auto-bypass fix) ────────────────────────────

class TestNaiveBypassDsts:
    def _session_with(self, engine, nodes):
        """Insert given Nodes and return an async session."""
        from sqlmodel import Session
        sync_engine, async_engine = engine
        with Session(sync_engine) as s:
            for n in nodes:
                s.add(n)
            s.commit()
        return async_engine

    def test_resolves_enabled_naive_only(self, engine, monkeypatch):
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.api.system import _collect_naive_bypass_dsts
        from app.models import Node

        async_engine = self._session_with(engine, [
            Node(id=1, name="enabled-naive", protocol="naive",
                 address="naive-1.example", port=443, enabled=True),
            Node(id=2, name="disabled-naive", protocol="naive",
                 address="naive-2.example", port=443, enabled=False),
            Node(id=3, name="vless-ignored", protocol="vless",
                 address="vless.example", port=443, enabled=True, uuid="u"),
        ])

        # Fake resolver — deterministic IPs per host.
        import socket as _sock
        def fake_getaddrinfo(host, *a, **kw):
            if host == "naive-1.example":
                return [(_sock.AF_INET, None, None, None, ("203.0.113.10", 0))]
            raise _sock.gaierror("should not resolve disabled / non-naive")
        monkeypatch.setattr(_sock, "getaddrinfo", fake_getaddrinfo)

        async def _run():
            async with AsyncSession(async_engine) as s:
                return await _collect_naive_bypass_dsts(s)

        cidrs = asyncio.run(_run())
        assert cidrs == ["203.0.113.10/32"]

    def test_unresolvable_host_is_skipped(self, engine, monkeypatch):
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.api.system import _collect_naive_bypass_dsts
        from app.models import Node

        async_engine = self._session_with(engine, [
            Node(id=1, name="bad-dns", protocol="naive",
                 address="does-not-resolve.invalid", port=443, enabled=True),
        ])

        import socket as _sock
        monkeypatch.setattr(_sock, "getaddrinfo",
                            lambda *a, **kw: (_ for _ in ()).throw(_sock.gaierror()))

        async def _run():
            async with AsyncSession(async_engine) as s:
                return await _collect_naive_bypass_dsts(s)

        # Graceful: returns empty list, no exception
        assert asyncio.run(_run()) == []

    def test_dedup_of_repeated_ips(self, engine, monkeypatch):
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.api.system import _collect_naive_bypass_dsts
        from app.models import Node

        async_engine = self._session_with(engine, [
            Node(id=1, name="n1", protocol="naive",
                 address="a.example", port=443, enabled=True),
            Node(id=2, name="n2", protocol="naive",
                 address="b.example", port=443, enabled=True),
        ])

        import socket as _sock
        monkeypatch.setattr(_sock, "getaddrinfo",
            lambda host, *a, **kw: [(_sock.AF_INET, None, None, None, ("198.51.100.1", 0))])

        async def _run():
            async with AsyncSession(async_engine) as s:
                return await _collect_naive_bypass_dsts(s)

        # Same IP behind both hostnames → single /32 in output
        assert asyncio.run(_run()) == ["198.51.100.1/32"]
