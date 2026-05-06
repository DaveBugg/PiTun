"""Background scheduler for NodeCircle automatic rotation."""
import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import NodeCircle, Node, Settings as DBSettings

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 15


class CircleScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._next_rotate: dict[int, datetime] = {}
        # Per-circle lock to serialise rotations of the same circle. The
        # scheduler tick and the API "rotate now" endpoint can both
        # invoke `rotate_circle()` concurrently — without this lock, two
        # parallel probe loops could each select a different candidate
        # and both write `active_node_id`, leaving xray with the
        # outbound from the second writer but the circle.current_index
        # of the first. Per-circle (not global) so rotations of
        # different circles don't queue behind each other.
        self._rotate_locks: dict[int, asyncio.Lock] = {}

    def _get_rotate_lock(self, circle_id: int) -> asyncio.Lock:
        lock = self._rotate_locks.get(circle_id)
        if lock is None:
            lock = asyncio.Lock()
            self._rotate_locks[circle_id] = lock
        return lock

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Circle scheduler started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Circle scheduler error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _tick(self) -> None:
        async with AsyncSession(get_async_engine()) as session:
            circles = (await session.exec(
                select(NodeCircle).where(NodeCircle.enabled == True)  # noqa: E712
            )).all()
            circle_data = []
            for c in circles:
                circle_data.append({
                    "id": c.id, "name": c.name,
                    "node_ids": json.loads(c.node_ids) if isinstance(c.node_ids, str) else c.node_ids,
                    "mode": c.mode,
                    "interval_min": c.interval_min,
                    "interval_max": c.interval_max,
                    "current_index": c.current_index,
                    "last_rotated": c.last_rotated,
                })

        live_ids = {cd["id"] for cd in circle_data}
        for stale_id in list(self._next_rotate.keys()):
            if stale_id not in live_ids:
                del self._next_rotate[stale_id]
        # Same cleanup for rotation locks — parity with `_next_rotate`.
        # Locks for deleted/disabled circles aren't held (we drop them
        # while they're free), so this is just memory hygiene.
        for stale_id in list(self._rotate_locks.keys()):
            if stale_id not in live_ids:
                self._rotate_locks.pop(stale_id, None)

        now = datetime.now(tz=timezone.utc)
        for cd in circle_data:
            cid = cd["id"]
            if not cd["node_ids"] or len(cd["node_ids"]) < 2:
                continue

            if cid not in self._next_rotate:
                interval = self._calc_interval(cd)
                if cd["last_rotated"]:
                    last = cd["last_rotated"]
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    self._next_rotate[cid] = last + timedelta(minutes=interval)
                else:
                    self._next_rotate[cid] = now

            if now >= self._next_rotate[cid]:
                await self.rotate_circle(cid)
                interval = self._calc_interval(cd)
                self._next_rotate[cid] = now + timedelta(minutes=interval)
                logger.debug("NodeCircle %d: next rotation in %.1f minutes", cid, interval)

    def _calc_interval(self, cd: dict) -> float:
        if cd["interval_min"] == cd["interval_max"]:
            return cd["interval_min"]
        return random.uniform(cd["interval_min"], cd["interval_max"])

    # Probe retry to absorb transient SYN drops / brief packet loss.
    # 0.6s delay between attempts is enough that congestion windows
    # have a chance to recover, but short enough that worst-case
    # (all candidates dead + retries) still fits the 20s deadline for
    # circles with up to ~3 candidates.
    _PROBE_MAX_ATTEMPTS = 2
    _PROBE_RETRY_DELAY_S = 0.6

    # Hard timeout for one rotation attempt. Bounds:
    #   - the API "rotate now" request — UI can't hang forever
    #   - the scheduler tick — `_loop` keeps moving even if a single
    #     rotation goes pathologically slow.
    # On timeout we abort cleanly; the next scheduler tick will retry
    # normally (no permanent broken state).
    _ROTATE_DEADLINE_S = 20.0

    async def rotate_circle(self, circle_id: int) -> bool:
        """Public entry point — wraps the actual rotation in a per-circle
        lock and a hard timeout.

        Returns True iff a rotation actually happened (active_node_id was
        changed). False on every flavour of abort: timeout, all-candidates-
        offline, races with manual edits, no enabled candidates, etc.

        Failover (in HealthChecker) uses the return value to decide
        whether the circle handled the failure — if False, failover
        falls through to its `failover_node_ids` list as a backup.

        The scheduler `_tick` advances `_next_rotate[cid]` regardless of
        outcome, so an aborted rotation just means "wait for the next
        normal tick" — no permanent failure mode.
        """
        lock = self._get_rotate_lock(circle_id)
        async with lock:
            try:
                async with asyncio.timeout(self._ROTATE_DEADLINE_S):
                    return await self._do_rotate(circle_id)
            except asyncio.TimeoutError:
                logger.warning(
                    "NodeCircle %d: rotation exceeded %.0fs deadline — aborted, "
                    "will retry on next tick",
                    circle_id, self._ROTATE_DEADLINE_S,
                )
                return False

    async def _do_rotate(self, circle_id: int) -> bool:
        """Pre-ping + rotate to first alive candidate.

        Returns True iff `active_node_id` was actually changed. False on
        any abort path (no candidates, all dead, race condition, etc.).
        Caller (failover layer) uses this to decide whether to fall
        through to alternative recovery strategies.

        Pre-rotation flow:
          1. Snapshot circle into a dict + resolve probe target for each
             enabled candidate Node (chained nodes resolve to the chain
             parent's address — same as HealthChecker). All Node-derived
             values are captured into `candidates: list[dict]` so we
             never carry detached SQLModel instances out of the session.
          2. For each candidate (in `random`-shuffled or `sequential`
             order), run a fresh TCP/UDP probe via `_probe_node` —
             we don't trust the cached `node.is_online` flag because
             it can be 30+ seconds stale.
          3. Switch to the FIRST candidate that probes alive. Fire
             `circle.candidate_failed` events for each rejected one so
             the future TG notifier can warn the user.
          4. If ALL candidates fail, emit `circle.all_offline`, leave
             the current node in place — better to keep a possibly-
             degraded working node than rotate to a dead one and break
             routing. Failover layer takes over from here.

        Why pre-ping at rotation time: the cached `is_online` field is
        updated by HealthChecker on its own ~30s tick. A dead-but-not-
        yet-marked node would otherwise grab the active slot for the
        next full minute. Pre-ping costs <2s per candidate and runs
        only at scheduled rotation moments, so the overhead is tiny.
        """
        from app.core.events import record_event
        from app.core.healthcheck import health_checker

        async def _probe_with_retry(target: dict) -> dict:
            """Probe `target`, retry once on failure with a brief delay.

            `target` is a dict with addr/port/udp/protocol/internal_port.
            Catches transient SYN drops / brief congestion that cause
            single-shot probes to false-negative on otherwise-alive
            nodes. Total worst-case time per call:
                _PROBE_MAX_ATTEMPTS × probe_timeout + (max_attempts-1) × retry_delay
            For default settings: 2 × 3s + 0.6s ≈ 6.6s upper bound.
            """
            last: dict = {"is_online": False, "error": "no probe attempted"}
            for attempt in range(self._PROBE_MAX_ATTEMPTS):
                try:
                    res = await health_checker._probe_node(
                        target["addr"], target["port"], target["udp"],
                        protocol=target.get("protocol"),
                        internal_port=target.get("internal_port"),
                    )
                except Exception as exc:  # noqa: BLE001 — probes shouldn't throw
                    res = {"is_online": False, "error": f"probe exception: {exc}"}
                if res.get("is_online"):
                    return res
                last = res
                if attempt < self._PROBE_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(self._PROBE_RETRY_DELAY_S)
            return last

        # ── Phase 1: snapshot circle + resolve candidates with probe targets ──
        #
        # All Node-derived values (id, name, address, port, protocol,
        # internal_port, udp flag) get copied into plain dicts here, so
        # the rest of the function never accesses a detached SQLModel
        # instance. `_resolve_probe_target` follows chain_node_id back
        # to the chain head — important so probing matches what
        # HealthChecker measures.
        async with AsyncSession(get_async_engine()) as session:
            circle = await session.get(NodeCircle, circle_id)
            if not circle:
                return False

            node_ids = json.loads(circle.node_ids) if isinstance(circle.node_ids, str) else circle.node_ids
            if len(node_ids) < 2:
                return False

            current_idx = circle.current_index if circle.current_index < len(node_ids) else 0
            prev_node_id = node_ids[current_idx]

            # Build candidate order: random=shuffle, sequential=walk forward.
            if circle.mode == "random":
                order = [i for i in range(len(node_ids)) if i != current_idx]
                random.shuffle(order)
            else:  # sequential — walk forward from current+1, wrap, skip current
                order = [(current_idx + i) % len(node_ids) for i in range(1, len(node_ids))]

            candidates: list[dict] = []
            for idx in order:
                cand = await session.get(Node, node_ids[idx])
                if not cand or not cand.enabled:
                    continue
                addr, port, udp = await health_checker._resolve_probe_target(session, cand)
                candidates.append({
                    "idx": idx,
                    "id": cand.id,
                    "name": cand.name,
                    "addr": addr,
                    "port": port,
                    "udp": udp,
                    "protocol": cand.protocol,
                    "internal_port": cand.internal_port,
                })

            circle_name = circle.name
            circle_mode = circle.mode

        if not candidates:
            logger.warning("NodeCircle %d: no enabled candidates to rotate to", circle_id)
            return False

        # ── Phase 2: probe each candidate, take the first that's alive ───────
        #
        # Probes run OUTSIDE the DB session — they're slow (~1-2s each
        # on timeout) and there's no reason to hold a connection.
        # `record_event` calls are wrapped so a failing event store
        # (DB lock, transient write error) can't take down the whole
        # rotation — events are advisory, the rotation itself is the
        # primary action.
        async def _safe_event(**kwargs):
            try:
                await record_event(**kwargs)
            except Exception as exc:  # noqa: BLE001 — events are advisory only
                logger.debug("record_event failed (non-fatal): %s", exc)

        selected: Optional[dict] = None
        failed: list[tuple[str, str]] = []  # (node_name, error_string)

        for cand in candidates:
            # Probe with single retry — `_probe_with_retry` catches
            # transient SYN drops / brief packet loss that would
            # otherwise mark a healthy node as dead and cost us a
            # missed rotation tick.
            result = await _probe_with_retry(cand)

            if result.get("is_online"):
                selected = cand
                break

            err_msg = result.get("error") or "unreachable"
            failed.append((cand["name"], err_msg))
            logger.info(
                "NodeCircle '%s': candidate '%s' (id=%d) offline: %s — trying next",
                circle_name, cand["name"], cand["id"], err_msg,
            )
            await _safe_event(
                category="circle.candidate_failed",
                severity="warning",
                title=f"Circle '{circle_name}': candidate '{cand['name']}' offline",
                details=f"Probe error: {err_msg}. Trying next candidate.",
                entity_id=cand["id"],
                dedup_window_sec=120,
            )

        if not selected:
            logger.error(
                "NodeCircle '%s' (id=%d): all %d candidates offline, no rotation",
                circle_name, circle_id, len(candidates),
            )
            await _safe_event(
                category="circle.all_offline",
                severity="error",
                title=f"Circle '{circle_name}': all candidates offline",
                details=(
                    f"{len(candidates)} candidates probed, none alive. "
                    + "Failures: "
                    + ", ".join(f"{n}({err})" for n, err in failed[:5])
                    + ("…" if len(failed) > 5 else "")
                ),
                entity_id=circle_id,
                dedup_window_sec=300,
            )
            return False

        next_node_id = selected["id"]
        next_node_name = selected["name"]

        # ── Phase 3: persist — re-validate that the chosen node is still
        #   in the circle (user may have edited node_ids during the probe
        #   loop). Re-derive the index from the FRESH node_ids list, so
        #   if nodes were reordered we still set current_index correctly.
        async with AsyncSession(get_async_engine()) as session:
            circle = await session.get(NodeCircle, circle_id)
            if not circle or not circle.enabled:
                logger.info(
                    "NodeCircle %d: circle was disabled/removed during probe — skipping write",
                    circle_id,
                )
                return False

            fresh_ids = json.loads(circle.node_ids) if isinstance(circle.node_ids, str) else circle.node_ids
            if next_node_id not in fresh_ids:
                # User removed the chosen node from the circle while we
                # were probing. Abort cleanly — next scheduler tick will
                # try again with the new circle composition.
                logger.warning(
                    "NodeCircle '%s' (id=%d): chosen node %d (%s) was removed from circle "
                    "during probe — aborting, will retry on next tick",
                    circle_name, circle_id, next_node_id, next_node_name,
                )
                return False
            actual_idx = fresh_ids.index(next_node_id)

            circle.current_index = actual_idx
            circle.last_rotated = datetime.now(tz=timezone.utc)
            session.add(circle)

            setting = (await session.exec(
                select(DBSettings).where(DBSettings.key == "active_node_id")
            )).first()
            if setting:
                setting.value = str(next_node_id)
                session.add(setting)
            else:
                session.add(DBSettings(key="active_node_id", value=str(next_node_id)))

            await session.commit()

        skipped_note = f", skipped {len(failed)} offline" if failed else ""
        logger.info(
            "NodeCircle '%s': rotated to node %d (%s) [index %d/%d%s]",
            circle_name, next_node_id, next_node_name,
            actual_idx + 1, len(fresh_ids), skipped_note,
        )
        await _safe_event(
            category="circle.rotated",
            severity="info",
            title=f"Circle '{circle_name}' → '{next_node_name}'",
            details=f"Position {actual_idx + 1}/{len(fresh_ids)}, mode={circle_mode}{skipped_note}",
            entity_id=circle_id,
        )

        await self._seamless_rotate(prev_node_id, next_node_id)
        return True

    async def _seamless_rotate(
        self, prev_node_id: int, next_node_id: int
    ) -> None:
        try:
            from app.core.xray import xray_manager
            if not xray_manager.is_running:
                return

            from app.core import xray_api
            from app.core.config_gen import _build_outbound, _stream_settings

            if not await xray_api.is_api_available():
                logger.warning("xray API not available, falling back to full restart")
                await self._full_reload()
                return

            async with AsyncSession(get_async_engine()) as session:
                next_node = await session.get(Node, next_node_id)
                if not next_node:
                    logger.error("Node %d not found for seamless rotation", next_node_id)
                    await self._full_reload()
                    return
                try:
                    new_outbound = _build_outbound(next_node)
                except Exception as exc:
                    logger.error("Failed to build outbound for node %d: %s", next_node_id, exc)
                    return

            new_tag = f"node-{next_node_id}"
            old_tag = f"node-{prev_node_id}"

            added = await xray_api.add_outbound(new_outbound)
            if not added:
                # `add_outbound` already retries on "existing tag found" via
                # its idempotency path. Any other failure means the live xray
                # doesn't know about the new outbound — writing config file
                # alone would leave live state desynced. Fall back to a full
                # reload so live xray and config file agree.
                logger.warning(
                    "Seamless rotation: add_outbound(%s) failed — falling back to full reload",
                    new_tag,
                )
                await self._full_reload()
                return

            await self._update_config_file()

            logger.info(
                "Seamless rotation: %s → %s (old connections finish naturally)",
                old_tag, new_tag,
            )

        except Exception as exc:
            logger.error("Seamless rotation failed, falling back to full restart: %s", exc)
            await self._full_reload()

    async def _update_config_file(self) -> None:
        try:
            from app.core.config_gen import generate_config, write_config
            from app.models import RoutingRule, DNSRule, BalancerGroup

            async with AsyncSession(get_async_engine()) as session:
                settings_map = {r.key: r.value for r in (await session.exec(select(DBSettings))).all()}
                active_id = settings_map.get("active_node_id", "")
                active_node = None
                if active_id:
                    try:
                        active_node = await session.get(Node, int(active_id))
                    except ValueError:
                        pass
                all_nodes = list((await session.exec(select(Node).where(Node.enabled == True))).all())
                rules = list((await session.exec(select(RoutingRule).where(RoutingRule.enabled == True))).all())
                dns_rules = list((await session.exec(select(DNSRule).where(DNSRule.enabled == True))).all())
                balancer_groups = list((await session.exec(select(BalancerGroup))).all())
                mode = settings_map.get("mode", "rules")
                config = generate_config(active_node, all_nodes, rules, mode, settings_map, dns_rules, balancer_groups)
                await write_config(config)
        except Exception as exc:
            logger.warning("Config file update failed: %s", exc)

    async def _full_reload(self) -> None:
        try:
            from app.core.xray import xray_manager
            await self._update_config_file()
            if xray_manager.is_running:
                await xray_manager.reload()
        except Exception as exc:
            logger.error("Full reload failed: %s", exc)


circle_scheduler = CircleScheduler()
