"""Regression: device scanner must not crash on a duplicate MAC in one scan.

Live-caught bug: when the ARP scan returns the SAME MAC twice in a single
sweep (a device answering on two IPs, a duplicate ARP cache entry, a
dual-homed NIC), `scan_and_update_devices` created a fresh `Device` row
for the first occurrence but never registered it in its in-memory
`existing` map. The second occurrence therefore still looked "new" and a
SECOND row with the same MAC was queued, so `session.commit()` blew up
with:

    sqlite3.IntegrityError: UNIQUE constraint failed: device.mac

The whole scan was rolled back and lost. Worse: because the device never
persisted, every subsequent 60-second scan saw it as "new" again and hit
the same wall — the error spammed the log forever and that device never
showed up in the UI.

Fix: register each freshly-created Device in `existing` so a repeat MAC
in the same batch updates the in-memory object instead of re-inserting.

These tests drive `scan_and_update_devices` against the live async engine
(the `client` fixture patches `db_mod._async_engine` globally), mocking
`_arp_scan` to control exactly what the "scan" returns.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlmodel import select

from app.models import Device


async def _scan_with(entries):
    """Run scan_and_update_devices with a mocked ARP result."""
    import app.core.device_scanner as ds

    async def _fake_arp():
        return list(entries)

    with patch.object(ds, "_arp_scan", _fake_arp):
        return await ds.scan_and_update_devices()


class TestScannerDuplicateMac:
    @pytest.mark.asyncio
    async def test_same_mac_twice_does_not_raise(self, client, session):
        """A MAC appearing twice in one scan inserts exactly one row."""
        mac = "c8:ff:bf:0e:0d:ee"
        result = await _scan_with([
            {"ip": "192.168.50.225", "mac": mac},
            {"ip": "192.168.50.226", "mac": mac},  # same device, 2nd IP
        ])

        # Did not raise; one discovered, one row persisted.
        rows = session.exec(select(Device).where(Device.mac == mac)).all()
        assert len(rows) == 1
        assert result["discovered"] == 1
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_distinct_macs_all_inserted(self, client, session):
        """Sanity: distinct MACs in one scan each get their own row."""
        result = await _scan_with([
            {"ip": "192.168.50.10", "mac": "aa:bb:cc:00:00:01"},
            {"ip": "192.168.50.11", "mac": "aa:bb:cc:00:00:02"},
            {"ip": "192.168.50.12", "mac": "aa:bb:cc:00:00:03"},
        ])
        assert result["discovered"] == 3
        assert result["total"] == 3

    @pytest.mark.asyncio
    async def test_repeat_scan_updates_not_duplicates(self, client, session):
        """A MAC seen on a later scan updates the existing row (no dup)."""
        mac = "aa:bb:cc:00:00:09"
        await _scan_with([{"ip": "192.168.50.40", "mac": mac}])
        # Same device, new IP, on the next sweep.
        result = await _scan_with([{"ip": "192.168.50.41", "mac": mac}])

        rows = session.exec(select(Device).where(Device.mac == mac)).all()
        assert len(rows) == 1
        assert rows[0].ip == "192.168.50.41"
        assert result["discovered"] == 0
        assert result["updated"] == 1

    @pytest.mark.asyncio
    async def test_existing_mac_repeated_in_same_scan(self, client, session):
        """A MAC already in the DB, then seen twice in one fresh scan,
        still resolves to a single updated row (no second INSERT)."""
        mac = "aa:bb:cc:00:00:0a"
        await _scan_with([{"ip": "192.168.50.50", "mac": mac}])
        result = await _scan_with([
            {"ip": "192.168.50.51", "mac": mac},
            {"ip": "192.168.50.52", "mac": mac},
        ])

        rows = session.exec(select(Device).where(Device.mac == mac)).all()
        assert len(rows) == 1
        assert result["discovered"] == 0
        # Both occurrences took the update path.
        assert result["updated"] == 2
