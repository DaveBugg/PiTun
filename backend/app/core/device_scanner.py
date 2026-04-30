"""Background device scanner — discovers LAN devices via ARP."""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_async_engine
from app.models import Device, Settings as DBSettings

logger = logging.getLogger(__name__)


async def _get_db_setting(key: str) -> Optional[str]:
    """Read a single setting from the DB (returns None if missing)."""
    try:
        async with AsyncSession(get_async_engine()) as session:
            row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
            return row.value if row else None
    except Exception:
        return None

_MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")

_OUI_CACHE: Dict[str, str] = {}


def _oui_lookup(mac: str) -> Optional[str]:
    prefix = mac[:8].upper()
    if prefix in _OUI_CACHE:
        return _OUI_CACHE[prefix]
    return None


async def _arp_scan() -> List[Dict[str, str]]:
    """Run arp-scan or fall back to ip neigh / /proc/net/arp."""
    devices: List[Dict[str, str]] = []

    try:
        iface = await _get_db_setting("interface") or settings.interface
        proc = await asyncio.create_subprocess_exec(
            "arp-scan", "--localnet", "--interface", iface, "-q",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        for line in stdout.decode(errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                ip_match = _IP_RE.search(parts[0])
                mac_match = _MAC_RE.search(parts[1])
                if ip_match and mac_match:
                    entry = {"ip": ip_match.group(1), "mac": mac_match.group(1).lower()}
                    if len(parts) >= 3:
                        entry["vendor"] = parts[2].strip()
                    devices.append(entry)
        if devices:
            return devices
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as exc:
        logger.debug("arp-scan unavailable: %s", exc)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "neigh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        neigh_re = re.compile(
            r"(\d+\.\d+\.\d+\.\d+)\s+\S+\s+\S+\s+lladdr\s+([0-9a-f:]{17})", re.I
        )
        for line in stdout.decode().splitlines():
            m = neigh_re.search(line)
            if m:
                devices.append({"ip": m.group(1), "mac": m.group(2).lower()})
        if devices:
            return devices
    except Exception:
        pass

    try:
        def _read_arp():
            with open("/proc/net/arp") as f:
                return f.readlines()[1:]

        lines = await asyncio.to_thread(_read_arp)
        for line in lines:
            parts = line.split()
            if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
                devices.append({"ip": parts[0], "mac": parts[3].lower()})
    except Exception:
        pass

    return devices


async def scan_and_update_devices() -> Dict[str, int]:
    """Scan LAN, update Device table. Returns {discovered, updated, total}."""
    raw = await _arp_scan()
    now = datetime.now(tz=timezone.utc)
    discovered = 0
    updated = 0

    own_ip = await _get_db_setting("gateway_ip") or settings.gateway_ip

    async with AsyncSession(get_async_engine()) as session:
        existing: Dict[str, Device] = {}
        for d in (await session.exec(select(Device))).all():
            existing[d.mac] = d

        seen_macs = set()
        for entry in raw:
            mac = entry["mac"]
            ip = entry["ip"]

            if ip == own_ip:
                continue

            seen_macs.add(mac)
            vendor = entry.get("vendor") or _oui_lookup(mac)

            if mac in existing:
                dev = existing[mac]
                dev.ip = ip
                dev.last_seen = now
                dev.is_online = True
                if vendor and not dev.vendor:
                    dev.vendor = vendor
                session.add(dev)
                updated += 1
            else:
                dev = Device(
                    mac=mac,
                    ip=ip,
                    vendor=vendor,
                    first_seen=now,
                    last_seen=now,
                    is_online=True,
                    routing_policy="default",
                )
                session.add(dev)
                discovered += 1

        for mac, dev in existing.items():
            if mac not in seen_macs:
                dev.is_online = False
                session.add(dev)

        await session.commit()
        total = (await session.exec(select(Device))).all()
        total_count = len(total)

    return {"discovered": discovered, "updated": updated, "total": total_count}


async def get_device_macs_for_mode(session: AsyncSession) -> Dict[str, List[str]]:
    """Return MAC lists based on device_routing_mode setting."""
    mode_row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "device_routing_mode")
    )).first()
    mode = mode_row.value if mode_row else "all"

    result: Dict[str, list] = {"mode": mode, "include_macs": [], "exclude_macs": []}

    if mode == "all":
        return result

    devices = (await session.exec(select(Device))).all()

    if mode == "include_only":
        result["include_macs"] = [d.mac for d in devices if d.routing_policy == "include"]
    elif mode == "exclude_list":
        result["exclude_macs"] = [d.mac for d in devices if d.routing_policy == "exclude"]

    return result


class DeviceScanner:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Device scanner started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(10)
        while self._running:
            try:
                result = await scan_and_update_devices()
                if result["discovered"] > 0:
                    logger.info(
                        "Device scan: %d new, %d updated, %d total",
                        result["discovered"], result["updated"], result["total"],
                    )
            except Exception as exc:
                logger.exception("Device scan error: %s", exc)

            interval = await self._get_interval()
            await asyncio.sleep(interval)

    @staticmethod
    async def _get_interval() -> int:
        try:
            async with AsyncSession(get_async_engine()) as session:
                row = (await session.exec(
                    select(DBSettings).where(DBSettings.key == "device_scan_interval")
                )).first()
                return int(row.value) if row else 60
        except Exception:
            return 60


device_scanner = DeviceScanner()
