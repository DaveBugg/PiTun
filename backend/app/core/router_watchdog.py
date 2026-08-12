"""Commit-confirm watchdog for router mode.

Gateway mode has a fallback: if PiTun misbehaves you point devices back at the
router. Router mode has none — PiTun *is* the router, so a bad apply takes the
whole house offline, and quite possibly the operator's own access to the panel
along with it. Network gear solves this with commit-confirm (Cisco's "reload
in", MikroTik's safe mode, VyOS's commit-confirm) and so do we:

    apply → arm a deadline → operator confirms → disarm
                          ↘ no confirmation → revert to gateway

Two properties matter more than the mechanism:

**An unconfirmed apply must not survive a reboot.** If the box is wedged badly
enough that nobody could confirm, the reconciliation on boot would happily
re-apply the same broken configuration — forever. So a pending confirmation
found at startup means "this configuration was never proven to work": revert
first, ask questions later.

**The revert must not depend on what broke.** It runs through
`router_mode.teardown()`, which is best-effort in every step for exactly this
reason. A watchdog that needs a healthy Docker socket to rescue a box whose
Docker socket died is not a watchdog.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import Settings as DBSettings

logger = logging.getLogger(__name__)

# Key holding the ISO deadline of an unconfirmed apply. Absent/empty = nothing
# pending. Stored in Settings rather than memory precisely so it outlives the
# process that armed it.
PENDING_KEY = "router_mode_confirm_deadline"

DEFAULT_WINDOW_SECONDS = 180
_CHECK_INTERVAL = 10


async def _get(session: AsyncSession, key: str) -> str:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    return row.value if row else ""


async def _set(session: AsyncSession, key: str, value: str) -> None:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    if row:
        row.value = value
    else:
        row = DBSettings(key=key, value=value)
    session.add(row)
    await session.commit()


async def arm(session: AsyncSession, seconds: int = DEFAULT_WINDOW_SECONDS) -> datetime:
    """Start the confirmation window. Returns the deadline."""
    deadline = datetime.now(timezone.utc) + timedelta(seconds=max(30, seconds))
    await _set(session, PENDING_KEY, deadline.isoformat())
    logger.warning(
        "Router mode armed: revert at %s unless confirmed", deadline.isoformat()
    )
    return deadline


async def confirm(session: AsyncSession) -> bool:
    """Operator says the network still works. Returns False if nothing pending."""
    pending = await _get(session, PENDING_KEY)
    if not pending:
        return False
    await _set(session, PENDING_KEY, "")
    logger.info("Router mode confirmed — watchdog disarmed")
    return True


async def pending(session: AsyncSession) -> Optional[datetime]:
    raw = await _get(session, PENDING_KEY)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        # Unparseable means we can't know when it expires. Treat it as expired
        # rather than pending forever: erring toward the safe mode is the whole
        # point of this component.
        logger.warning("Unparseable confirm deadline %r — treating as expired", raw)
        return datetime.now(timezone.utc) - timedelta(seconds=1)


async def revert(session: AsyncSession, reason: str) -> dict:
    """Put the box back in gateway mode and clear the pending state."""
    from app.core import router_mode

    logger.error("Router mode reverting to gateway: %s", reason)
    result = await router_mode.teardown()
    await _set(session, "operating_mode", "gateway")
    await _set(session, PENDING_KEY, "")

    try:
        from app.core.events import record_event
        await record_event(
            category="router.reverted",
            severity="warning",
            title="Router mode reverted automatically",
            details=(
                f"{reason} The box is back in gateway mode: devices point their "
                f"gateway at PiTun and your previous router handles DHCP and NAT "
                f"again. Nothing about the saved router settings was lost — fix "
                f"the cause and enable it again."
            ),
        )
    except Exception as exc:  # noqa: BLE001 — never let reporting block recovery
        logger.warning("Could not record the revert event: %s", exc)
    return result


async def check_once() -> Optional[dict]:
    """One watchdog tick. Reverts if the window closed unconfirmed."""
    async with AsyncSession(get_async_engine()) as session:
        deadline = await pending(session)
        if deadline is None:
            return None
        if datetime.now(timezone.utc) < deadline:
            return None
        return await revert(
            session,
            "Router mode was applied but nobody confirmed the network still "
            "worked before the confirmation window closed.",
        )


async def recover_on_boot() -> Optional[dict]:
    """Called at startup, before router mode is reconciled.

    A pending confirmation here means the previous apply was never proven to
    work — most likely because it took the box down hard enough that nobody
    could confirm. Re-applying it would repeat that on every boot, so the
    configuration is reverted instead.
    """
    async with AsyncSession(get_async_engine()) as session:
        if await pending(session) is None:
            return None
        return await revert(
            session,
            "The box restarted while a router-mode change was still waiting to "
            "be confirmed, so that configuration was never shown to work.",
        )


class RouterWatchdog:
    """Background loop driving `check_once`."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await check_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — a bad tick must not end the loop
                logger.warning("Router watchdog tick failed: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)


router_watchdog = RouterWatchdog()
