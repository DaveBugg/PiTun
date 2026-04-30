"""Background health checks and automatic failover."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_async_engine
from app.models import Node, Settings as DBSettings

logger = logging.getLogger(__name__)


class HealthChecker:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._full_task: Optional[asyncio.Task] = None
        self._fail_counts: Dict[int, int] = {}
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            # Two loops: fast (active node, default 30s, drives failover)
            # and slow (every enabled node, default 5min, keeps the
            # node-list `is_online` flags fresh in the UI). Without the
            # slow loop, non-active nodes stay at whatever value they
            # had on last manual "Test" — naive sidecar dying silently
            # would still show as "online" in Dashboard / Nodes lists.
            self._task = asyncio.create_task(self._loop(), name="health-active")
            self._full_task = asyncio.create_task(self._full_loop(), name="health-full")
            logger.info("Health checker started (active + full)")

    def stop(self) -> None:
        self._running = False
        for t in (self._task, self._full_task):
            if t:
                t.cancel()
        self._task = None
        self._full_task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_active_node()
            except Exception as exc:
                logger.exception("Health check loop error: %s", exc)
            await asyncio.sleep(settings.health_interval)

    async def _full_loop(self) -> None:
        """Background full sweep — refreshes is_online for every enabled
        node at a slower cadence than the active-node loop. Configurable
        via the `health_full_check_interval` Settings key (seconds).
        Default 300s. Runs the same logic as the manual "Test All" button.

        Setting the value to `0` disables the auto-sweep entirely — the
        loop stays alive but only re-reads the setting once a minute, so
        flipping it back on doesn't require a backend restart. The user
        can still trigger a full check manually via "Test All" on the
        Nodes page.
        """
        # Initial delay so the full sweep doesn't race startup with the
        # subscription scheduler / metrics collector / etc.
        await asyncio.sleep(60)
        while self._running:
            interval = 300
            try:
                interval = await self._read_full_interval()
                if interval > 0:
                    await self.check_all_nodes()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Health full-check loop error: %s", exc)
            # When disabled (0 or negative), poll the setting every 60 s
            # so re-enabling takes effect without a backend restart.
            await asyncio.sleep(interval if interval > 0 else 60)

    async def _read_full_interval(self) -> int:
        """Read `health_full_check_interval` (seconds) from DB Settings.
        Falls back to 300 (5 min) on any error."""
        try:
            async with AsyncSession(get_async_engine()) as session:
                v = await self._get_setting(session, "health_full_check_interval")
            return int(v) if v else 300
        except (TypeError, ValueError):
            return 300

    async def _check_active_node(self) -> None:
        async with AsyncSession(get_async_engine()) as session:
            active_id = await self._get_setting(session, "active_node_id")
            if not active_id:
                return
            try:
                node_id = int(active_id)
            except ValueError:
                return

            node = await session.get(Node, node_id)
            if node is None or not node.enabled:
                return
            probe_addr, probe_port, probe_udp = await self._resolve_probe_target(session, node)
            node_name = node.name
            probe_protocol = node.protocol
            probe_internal_port = node.internal_port

        result = await self._probe_node(
            probe_addr, probe_port, probe_udp,
            protocol=probe_protocol, internal_port=probe_internal_port,
        )

        async with AsyncSession(get_async_engine()) as session:
            db_node = await session.get(Node, node_id)
            if db_node:
                db_node.latency_ms = result["latency_ms"]
                db_node.last_check = datetime.now(tz=timezone.utc)
                db_node.is_online = result["is_online"]
                session.add(db_node)
                await session.commit()

            if result["is_online"]:
                self._fail_counts[node_id] = 0
            else:
                self._fail_counts[node_id] = self._fail_counts.get(node_id, 0) + 1
                logger.warning(
                    "Node %d (%s) health check failed (%d/%d)",
                    node_id, node_name,
                    self._fail_counts[node_id], settings.health_fail_threshold,
                )
                if self._fail_counts[node_id] >= settings.health_fail_threshold:
                    await self._failover(node_id)

    async def _failover(self, failed_node_id: int) -> None:
        async with AsyncSession(get_async_engine()) as session:
            failover_enabled = await self._get_setting(session, "failover_enabled") or "false"
            if failover_enabled.lower() != "true":
                return

            failover_ids_raw = await self._get_setting(session, "failover_node_ids") or "[]"
            try:
                failover_ids: List[int] = json.loads(failover_ids_raw)
            except (ValueError, json.JSONDecodeError):
                failover_ids = []

            candidates = []
            for fid in failover_ids:
                if fid == failed_node_id:
                    continue
                candidate = await session.get(Node, fid)
                if candidate and candidate.enabled:
                    addr, port, udp = await self._resolve_probe_target(session, candidate)
                    candidates.append({
                        "id": fid,
                        "address": addr,
                        "port": port,
                        "udp": udp,
                        "name": candidate.name,
                        "protocol": candidate.protocol,
                        "internal_port": candidate.internal_port,
                    })

        for cand in candidates:
            result = await self._probe_node(
                cand["address"], cand["port"], udp=cand["udp"],
                protocol=cand["protocol"], internal_port=cand["internal_port"],
            )
            if result["is_online"]:
                async with AsyncSession(get_async_engine()) as session:
                    # Re-check the candidate is still present and enabled
                    # RIGHT BEFORE the write — a TCP probe takes 1-3 seconds,
                    # during which an API call could have deleted or disabled
                    # the node. Without this guard, we'd happily point
                    # active_node_id at a row the user just decommissioned.
                    fresh = await session.get(Node, cand["id"])
                    if not fresh or not fresh.enabled:
                        logger.info(
                            "Failover: candidate node %d was removed/disabled mid-probe, skipping",
                            cand["id"],
                        )
                        continue
                    await self._set_setting(session, "active_node_id", str(cand["id"]))
                    await session.commit()
                    logger.info(
                        "Failover: switched from node %d to node %d (%s)",
                        failed_node_id, cand["id"], cand["name"],
                    )
                    from app.core.events import record_event
                    await record_event(
                        category="failover.switched",
                        severity="warning",
                        title=f"Failover: switched to '{cand['name']}'",
                        details=f"Previous node #{failed_node_id} failed health checks; promoted node #{cand['id']}",
                        entity_id=cand["id"],
                    )
                    await self._reload_xray(session)
                self._fail_counts[failed_node_id] = 0
                return

        logger.error("Failover: no available fallback nodes found")
        from app.core.events import record_event
        await record_event(
            category="failover.no_fallback",
            severity="error",
            title="Failover: no fallback available",
            details=f"Active node #{failed_node_id} failed; no enabled fallback could be reached",
            entity_id=failed_node_id,
            dedup_window_sec=300,
        )

    async def _reload_xray(self, session: AsyncSession) -> None:
        try:
            from app.core.xray import xray_manager
            from app.core.config_gen import generate_config, write_config
            from app.models import RoutingRule, DNSRule, BalancerGroup

            active_id_str = await self._get_setting(session, "active_node_id") or ""
            active_node = None
            if active_id_str:
                try:
                    active_node = await session.get(Node, int(active_id_str))
                except ValueError:
                    pass

            all_nodes = list((await session.exec(select(Node).where(Node.enabled == True))).all())
            rules = list((await session.exec(select(RoutingRule).where(RoutingRule.enabled == True))).all())
            dns_rules = list((await session.exec(select(DNSRule).where(DNSRule.enabled == True))).all())
            balancer_groups = list((await session.exec(select(BalancerGroup))).all())
            settings_map = {
                row.key: row.value
                for row in (await session.exec(select(DBSettings))).all()
            }
            mode = settings_map.get("mode", "rules")

            config = generate_config(active_node, all_nodes, rules, mode, settings_map, dns_rules, balancer_groups)
            await write_config(config)
            await xray_manager.reload()
        except Exception as exc:
            logger.error("Failed to reload xray after failover: %s", exc)

    # ── Public helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _resolve_probe_target(session: AsyncSession, node: Node):
        """
        Decide what we should actually probe to *measure latency* for `node`.

        - Chained node: probe the chain parent's real endpoint. The child's
          own endpoint (e.g. a WG server blocked by the ISP) is unreachable
          directly by design — the chain exists precisely to tunnel to it.
        - WireGuard (no chain): WG is UDP-only and silently drops random
          packets, so TCP SYN is meaningless. We mark it with udp=True
          and use a lightweight UDP probe (sends 0 bytes, expects no
          reply — any ICMP unreachable / network error fails it).
        - Naive node: returns the UPSTREAM Caddy server endpoint so we
          report a real-world latency in the UI. The sidecar liveness
          check is handled separately in `_probe_node` — probing only
          127.0.0.1:<sidecar_port> would always round to 0 ms (loopback
          is sub-millisecond).
        """
        # Follow the chain to the first non-chained ancestor so we probe the
        # actual internet-facing endpoint.
        cur = node
        seen = {cur.id}
        while cur.chain_node_id and cur.chain_node_id not in seen:
            parent = await session.get(Node, cur.chain_node_id)
            if not parent or not parent.enabled:
                break
            cur = parent
            seen.add(cur.id)

        udp = (cur.protocol == "wireguard")
        return cur.address, cur.port, udp

    async def _probe_node(
        self, addr: str, port: int, udp: bool,
        *, protocol: Optional[str] = None, internal_port: Optional[int] = None,
    ) -> Dict:
        """
        Protocol-aware wrapper around `check_node_by_addr`.

        For naive nodes we run a TWO-STAGE probe:
          1. TCP connect to the local sidecar (127.0.0.1:<internal_port>) —
             if the sidecar isn't listening, the node is unusable regardless
             of whether the upstream Caddy server is reachable. Mark offline.
          2. Probe the upstream endpoint for real latency. Loopback round-
             trips are sub-millisecond and rounding to 0 ms looks broken in
             the UI; the upstream value is what users actually care about.

        For all other protocols this is a single direct probe.
        """
        if protocol == "naive" and internal_port:
            sidecar_ok = await self._tcp_alive("127.0.0.1", int(internal_port), 1.0)
            if not sidecar_ok:
                return {
                    "is_online": False,
                    "latency_ms": None,
                    "error": f"naive sidecar not listening on :{internal_port}",
                }
        return await self.check_node_by_addr(addr, port, udp=udp)

    @staticmethod
    async def _tcp_alive(host: str, port: int, timeout: float) -> bool:
        """Quick TCP-connect liveness check (no SO_MARK — loopback only)."""
        try:
            fut = asyncio.open_connection(host, port)
            _, w = await asyncio.wait_for(fut, timeout=timeout)
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def check_node_by_addr(self, address: str, port: int, udp: bool = False) -> Dict:
        """
        Probe `address:port` with SO_MARK=0xff to bypass pitun tproxy.

        - TCP (default): SYN connect measures real latency.
        - UDP (wireguard): best-effort reachability — we send a tiny packet
          and treat the result as "online" unless the OS reports a hard
          error (ICMP unreachable, no route, etc.). Latency is not measured
          for UDP because WG never replies to invalid handshakes.
        """
        try:
            ip = await self._resolve_direct(address)
        except Exception as exc:
            return {"is_online": False, "latency_ms": None, "error": f"dns: {exc}"}

        loop = asyncio.get_event_loop()
        if udp:
            return await loop.run_in_executor(
                None, self._udp_probe_sync, ip, port, settings.health_timeout
            )
        return await loop.run_in_executor(
            None, self._tcp_ping_sync, ip, port, settings.health_timeout
        )

    @staticmethod
    def _udp_probe_sync(ip: str, port: int, timeout: float) -> Dict:
        """
        Best-effort UDP reachability probe for WireGuard-like endpoints.

        WG silently drops garbage packets, so we can't measure RTT. We just
        try to send one byte and then do a blocking recv with a short timeout.
        If the kernel reports a hard error (ICMP port unreachable, no route
        to host, network unreachable), mark as offline. Otherwise assume
        online — real failure will show up later at the xray layer.
        """
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 0xFF)
            except (OSError, AttributeError):
                pass
            sock.settimeout(min(float(timeout), 1.5))
            try:
                sock.connect((ip, port))
                sock.send(b"\x00")
                # Try a tiny recv so that a pending ICMP error surfaces as an
                # exception. Timeout = assume OK (no error surfaced).
                try:
                    sock.recv(1)
                except socket.timeout:
                    pass
            except (ConnectionRefusedError, OSError) as exc:
                return {"is_online": False, "latency_ms": None, "error": f"udp: {exc}"}
            return {"is_online": True, "latency_ms": None, "error": None}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _tcp_ping_sync(ip: str, port: int, timeout: float) -> Dict:
        """Blocking TCP connect with SO_MARK=0xff to bypass nftables tproxy."""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 0xFF)
            except (OSError, AttributeError):
                pass  # no CAP_NET_ADMIN or non-Linux — still try plain connect
            sock.settimeout(float(timeout))
            start = time.monotonic()
            try:
                sock.connect((ip, port))
            except (OSError, socket.timeout) as exc:
                return {"is_online": False, "latency_ms": None, "error": str(exc) or "connect failed"}
            latency = round((time.monotonic() - start) * 1000)
            return {"is_online": True, "latency_ms": latency, "error": None}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    async def _resolve_direct(address: str) -> str:
        """Resolve hostname via 8.8.8.8 directly, bypassing system/tproxy DNS."""
        import socket
        import struct
        # If already an IP, return as-is
        try:
            socket.inet_aton(address)
            return address
        except OSError:
            pass

        def _sync_resolve() -> str:
            """Blocking DNS via raw UDP socket with SO_MARK to bypass tproxy."""
            import os
            txn_id = os.urandom(2)
            name_parts = b""
            for part in address.encode().split(b"."):
                name_parts += bytes([len(part)]) + part
            name_parts += b"\x00"
            query = txn_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + name_parts + b"\x00\x01\x00\x01"

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 0xFF)
            except (OSError, AttributeError):
                pass
            sock.sendto(query, ("8.8.8.8", 53))
            data = sock.recv(512)
            sock.close()

            # Parse answer — skip header (12) + query section, find first A record
            pos = 12
            while pos < len(data) and data[pos] != 0:
                pos += data[pos] + 1
            pos += 5  # null byte + qtype(2) + qclass(2)
            an_count = struct.unpack("!H", data[6:8])[0]
            for _ in range(an_count):
                if data[pos] & 0xC0 == 0xC0:
                    pos += 2
                else:
                    while pos < len(data) and data[pos] != 0:
                        pos += data[pos] + 1
                    pos += 1
                rtype = struct.unpack("!H", data[pos:pos + 2])[0]
                rdlen = struct.unpack("!H", data[pos + 8:pos + 10])[0]
                pos += 10
                if rtype == 1 and rdlen == 4:
                    return socket.inet_ntoa(data[pos:pos + 4])
                pos += rdlen
            raise OSError(f"Could not resolve {address}")

        return await asyncio.get_event_loop().run_in_executor(None, _sync_resolve)

    async def check_node(self, node: Node) -> Dict:
        async with AsyncSession(get_async_engine()) as session:
            addr, port, udp = await self._resolve_probe_target(session, node)
        return await self._probe_node(
            addr, port, udp,
            protocol=node.protocol, internal_port=node.internal_port,
        )

    async def check_all_nodes(self) -> List[Dict]:
        async with AsyncSession(get_async_engine()) as session:
            nodes = list((await session.exec(select(Node).where(Node.enabled == True))).all())
            node_data = []
            for n in nodes:
                addr, port, udp = await self._resolve_probe_target(session, n)
                # Snapshot the protocol/internal_port we need post-session for
                # the naive sidecar liveness check inside `_probe_node`.
                node_data.append({
                    "id": n.id, "name": n.name,
                    "address": addr, "port": port, "udp": udp,
                    "protocol": n.protocol, "internal_port": n.internal_port,
                })

        tasks = [
            self._probe_node(
                nd["address"], nd["port"], udp=nd["udp"],
                protocol=nd["protocol"], internal_port=nd["internal_port"],
            )
            for nd in node_data
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        async with AsyncSession(get_async_engine()) as session:
            for nd, result in zip(node_data, results):
                if isinstance(result, Exception):
                    result = {"is_online": False, "latency_ms": None, "error": str(result)}

                db_node = await session.get(Node, nd["id"])
                if db_node:
                    db_node.is_online = result["is_online"]
                    db_node.latency_ms = result["latency_ms"]
                    db_node.last_check = datetime.now(tz=timezone.utc)
                    session.add(db_node)

                output.append({
                    "node_id": nd["id"],
                    "node_name": nd["name"],
                    **result,
                })
            await session.commit()

        return output

    # ── Settings helpers ─────────────────────────────────────────────────────

    @staticmethod
    async def _get_setting(session: AsyncSession, key: str) -> Optional[str]:
        row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
        return row.value if row else None

    @staticmethod
    async def _set_setting(session: AsyncSession, key: str, value: str) -> None:
        row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
        if row:
            row.value = value
            session.add(row)
        else:
            session.add(DBSettings(key=key, value=value))


health_checker = HealthChecker()
