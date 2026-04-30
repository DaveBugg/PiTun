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

    async def rotate_circle(self, circle_id: int) -> None:
        async with AsyncSession(get_async_engine()) as session:
            circle = await session.get(NodeCircle, circle_id)
            if not circle:
                return

            node_ids = json.loads(circle.node_ids) if isinstance(circle.node_ids, str) else circle.node_ids
            if len(node_ids) < 2:
                return

            prev_node_id = node_ids[circle.current_index] if circle.current_index < len(node_ids) else node_ids[0]

            if circle.mode == "random":
                choices = [i for i in range(len(node_ids)) if i != circle.current_index]
                next_idx = random.choice(choices) if choices else 0
            else:
                next_idx = (circle.current_index + 1) % len(node_ids)

            next_node_id = node_ids[next_idx]
            next_node = await session.get(Node, next_node_id)
            if not next_node or not next_node.enabled:
                for attempt in range(len(node_ids)):
                    next_idx = (next_idx + 1) % len(node_ids)
                    if next_idx == circle.current_index:
                        continue
                    candidate = await session.get(Node, node_ids[next_idx])
                    if candidate and candidate.enabled:
                        next_node_id = node_ids[next_idx]
                        next_node = candidate
                        break
                else:
                    logger.warning("NodeCircle %d: no enabled nodes to rotate to", circle_id)
                    return

            circle_name = circle.name
            circle_mode = circle.mode  # captured before commit — ORM expires
                                        # attributes on commit and reloading
                                        # them would trip MissingGreenlet here.
            next_node_name = next_node.name

            circle.current_index = next_idx
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

            logger.info(
                "NodeCircle '%s': rotated to node %d (%s) [index %d/%d]",
                circle_name, next_node_id, next_node_name, next_idx + 1, len(node_ids),
            )
            from app.core.events import record_event
            await record_event(
                category="circle.rotated",
                severity="info",
                title=f"Circle '{circle_name}' → '{next_node_name}'",
                details=f"Position {next_idx + 1}/{len(node_ids)}, mode={circle_mode}",
                entity_id=circle_id,
            )

        await self._seamless_rotate(prev_node_id, next_node_id)

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
