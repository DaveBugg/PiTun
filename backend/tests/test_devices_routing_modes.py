"""Tests for the device-routing-mode logic — `all` / `include_only`
/ `exclude_list`.

The user-facing contract:

* ``all`` (default): every device on the LAN is proxied. Bypass only
  works via explicit MAC routing rules.
* ``include_only``: ONLY devices with ``routing_policy=include`` get
  proxied. Everyone else's traffic is left untouched (passes through
  PiTun's nftables hooks straight to the real gateway).
* ``exclude_list``: devices with ``routing_policy=exclude`` BYPASS the
  proxy. Everyone else is proxied normally.

The ``get_device_macs_for_mode`` helper produces the MAC-list payload
the nftables manager consumes. These tests pin the contract on the
helper directly — the live nftables-rule rendering already has its
own tests.

Why a dedicated file: ``test_system.py`` only checked that the SETTING
can be read/written, not that the logic of populating MAC lists per
mode actually works. Manual UI testing on this is annoying because
it requires real LAN devices with real MACs to validate end-to-end.

Tests are sync at the pytest level — they seed via the sync `session`
fixture, then drive the async helper through a tiny `asyncio.run`
wrapper that opens an AsyncSession on the SAME sqlite file. This is
intentionally simpler than introducing a project-wide `async_session`
fixture for a one-helper test file.
"""
import asyncio

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.device_scanner import get_device_macs_for_mode
from app.models import Device, Settings as DBSettings


def _run_helper(engine) -> dict:
    """Open an AsyncSession on the test engine and call the helper.

    `engine` is the (sync, async) tuple from conftest.engine_fixture —
    we use the second element. Re-creating the engine here would split
    the connection pool from the sync session that just seeded the DB,
    so we pass it through.

    Uses a dedicated event loop rather than `asyncio.run` to avoid
    interfering with pytest-asyncio's auto-fixture machinery, which
    otherwise hangs the test runner when it sees a top-level asyncio
    call from within a sync test function.
    """
    _, async_engine = engine

    async def _call():
        async with AsyncSession(async_engine) as s:
            return await get_device_macs_for_mode(s)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_call())
    finally:
        loop.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seed_devices(session, fixtures):
    """Insert a batch of (mac, policy) Device rows into the test DB.

    ``policy`` is either ``"include"``, ``"exclude"``, or anything
    else (matches the column's default — typically ``"all"``).
    """
    for idx, (mac, policy) in enumerate(fixtures):
        session.add(Device(
            mac=mac,
            ip=f"192.168.88.{100 + idx}",
            hostname=f"device-{idx}",
            vendor="testlab",
            routing_policy=policy,
            is_online=True,
        ))
    session.commit()


def _set_mode(session, mode: str):
    """Set the ``device_routing_mode`` Settings row to `mode`."""
    from sqlmodel import select
    row = session.exec(
        select(DBSettings).where(DBSettings.key == "device_routing_mode"),
    ).first()
    if row:
        row.value = mode
        session.add(row)
    else:
        session.add(DBSettings(key="device_routing_mode", value=mode))
    session.commit()


# ── Mode: all ─────────────────────────────────────────────────────────────────


class TestModeAll:
    """When ``device_routing_mode=all``, the helper returns empty MAC
    lists. nftables falls back to proxying every LAN device with no
    per-device exclusion. This is the default and the "I don't care
    about per-device routing" state."""

    def test_no_devices_returns_empty_lists(self, session, engine):
        _set_mode(session, "all")
        result = _run_helper(engine)
        assert result == {
            "mode": "all", "include_macs": [], "exclude_macs": [],
        }

    def test_devices_with_policies_ignored_in_all_mode(self, session, engine):
        """Even when individual devices have include/exclude set,
        `all` mode short-circuits before touching the device table —
        the per-device policy is irrelevant in this mode."""
        _seed_devices(session, [
            ("aa:bb:cc:dd:ee:01", "include"),
            ("aa:bb:cc:dd:ee:02", "exclude"),
            ("aa:bb:cc:dd:ee:03", "all"),
        ])
        _set_mode(session, "all")
        result = _run_helper(engine)
        assert result["mode"] == "all"
        assert result["include_macs"] == []
        assert result["exclude_macs"] == []


# ── Mode: include_only ───────────────────────────────────────────────────────


class TestModeIncludeOnly:
    """When ``device_routing_mode=include_only``, only devices with
    ``routing_policy=include`` end up in ``include_macs``. nftables
    uses this list to render an ``include_mac`` set and a
    ``ether saddr != @include_mac return`` rule — meaning traffic
    from any other LAN MAC bypasses the proxy entirely."""

    def test_returns_only_include_marked(self, session, engine):
        _seed_devices(session, [
            ("aa:bb:cc:dd:ee:11", "include"),
            ("aa:bb:cc:dd:ee:12", "exclude"),  # should be IGNORED here
            ("aa:bb:cc:dd:ee:13", "all"),       # should be IGNORED too
            ("aa:bb:cc:dd:ee:14", "include"),
        ])
        _set_mode(session, "include_only")
        result = _run_helper(engine)
        assert result["mode"] == "include_only"
        assert sorted(result["include_macs"]) == [
            "aa:bb:cc:dd:ee:11", "aa:bb:cc:dd:ee:14",
        ]
        # exclude_macs irrelevant in this mode but must still be a list
        # (the API consumer reads it unconditionally).
        assert result["exclude_macs"] == []

    def test_empty_include_list_returns_no_macs(self, session, engine):
        """No devices marked `include` → empty include_macs → nftables
        renders no include set → ``ether saddr != @include_mac return``
        won't be emitted, falling back to "proxy everyone". Pin this
        explicitly so an empty-list reading isn't conflated with `all`
        mode at the helper level."""
        _seed_devices(session, [
            ("aa:bb:cc:dd:ee:21", "exclude"),
            ("aa:bb:cc:dd:ee:22", "all"),
        ])
        _set_mode(session, "include_only")
        result = _run_helper(engine)
        assert result["mode"] == "include_only"
        assert result["include_macs"] == []


# ── Mode: exclude_list ───────────────────────────────────────────────────────


class TestModeExcludeList:
    """When ``device_routing_mode=exclude_list``, only devices with
    ``routing_policy=exclude`` end up in ``exclude_macs``. The system
    merges this into the nftables ``bypass_mac`` set so those devices
    skip TPROXY (their traffic goes direct via the host's normal
    routing table)."""

    def test_returns_only_exclude_marked(self, session, engine):
        _seed_devices(session, [
            ("aa:bb:cc:dd:ee:31", "exclude"),
            ("aa:bb:cc:dd:ee:32", "include"),   # IGNORED in exclude mode
            ("aa:bb:cc:dd:ee:33", "all"),        # IGNORED
            ("aa:bb:cc:dd:ee:34", "exclude"),
        ])
        _set_mode(session, "exclude_list")
        result = _run_helper(engine)
        assert result["mode"] == "exclude_list"
        assert sorted(result["exclude_macs"]) == [
            "aa:bb:cc:dd:ee:31", "aa:bb:cc:dd:ee:34",
        ]
        assert result["include_macs"] == []

    def test_empty_exclude_list(self, session, engine):
        _seed_devices(session, [
            ("aa:bb:cc:dd:ee:41", "include"),
            ("aa:bb:cc:dd:ee:42", "all"),
        ])
        _set_mode(session, "exclude_list")
        result = _run_helper(engine)
        assert result["mode"] == "exclude_list"
        assert result["exclude_macs"] == []


# ── Mode resolution fallback ─────────────────────────────────────────────────


class TestModeFallback:
    """If the ``device_routing_mode`` setting row is missing entirely
    (fresh install before the seed migration runs), default to ``all``.
    Pinning this prevents a future refactor from silently switching
    the default to a more restrictive mode and tightening LAN access
    behind the operator's back."""

    def test_missing_setting_defaults_to_all(self, engine):
        # Don't call _set_mode — the row stays absent.
        result = _run_helper(engine)
        assert result["mode"] == "all"
        assert result["include_macs"] == []
        assert result["exclude_macs"] == []
