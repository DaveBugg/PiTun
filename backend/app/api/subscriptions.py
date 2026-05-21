"""Subscription management: CRUD + fetch/refresh."""
import asyncio
import logging
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session, get_async_engine
from app.models import Node, Settings as DBSettings, Subscription
from app.schemas import SubscriptionCreate, SubscriptionRead, SubscriptionUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# Happ client emulation — exposed as separate UA presets in the picker.
#
# Happ ships on iOS / Android / macOS / Windows. Stricter panels
# (xtoolapp / marzban with per-OS rules) cross-validate the UA against
# the `X-Device-Os` / `X-Ver-Os` / `X-Device-Model` headers — so all
# four must describe the same device, otherwise the panel falls back to
# a dummy "App not supported" placeholder.
#
# UA format that panels reliably accept: `Happ/<app_ver>/<os>/<os_ver>/<model>`.
# OS segment is lowercased to mirror what real Happ sends; the
# corresponding `X-Device-Os` header keeps the canonical case
# (`iOS`, `Android`, `Windows`, `macOS`) — some panels look at both,
# and a mismatch flips the fingerprint check.
#
# Each Happ flavour is its own UA key (`happ`, `happ-android`, …) so
# the subscription-form dropdown lists them as discrete options. The
# legacy `happ` key is an alias for the iOS profile to keep existing
# subscriptions working without a migration.
_HAPP_VERSION = "2.7.0"

# happ-* ua key -> (X-Device-Os, X-Ver-Os, X-Device-Model)
_HAPP_PROFILES: dict[str, tuple[str, str, str]] = {
    "happ":         ("iOS",     "17.4",          "iPhone15,2"),
    "happ-android": ("Android", "14",            "Pixel 8"),
    "happ-windows": ("Windows", "11_10.0.26200", "DESKTOP-PiTun_x86_64"),
    "happ-macos":   ("macOS",   "14.4",          "Mac15,7"),
}


def _happ_ua_for(ua_key: str) -> str:
    """Build the User-Agent string for a Happ UA preset key."""
    os_canonical, os_ver, model = _HAPP_PROFILES.get(ua_key, _HAPP_PROFILES["happ"])
    return f"Happ/{_HAPP_VERSION}/{os_canonical.lower()}/{os_ver}/{model}"


_UA_MAP = {
    "v2ray": "v2rayN/6.60",
    "clash": "clash.meta/1.18.0",
    "sing-box": "sing-box/1.8.0",
    "streisand": "Streisand/3.0",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # All Happ presets resolved at module load.
    **{k: _happ_ua_for(k) for k in _HAPP_PROFILES},
}


def _get_happ_headers(ua_key: str = "happ") -> dict:
    """Build the X-* header bundle that real Happ sends alongside its UA.

    HWID is derived from `/etc/machine-id` (or a constant fallback on
    non-Linux dev machines) — keeping it stable matters because some
    panels rate-limit or device-bind on first observed HWID, and
    rotating it would silently break the subscription. We mix the
    profile into the seed so different OS choices yield different HWIDs
    (real iOS vs Android Happ instances would never share one).
    """
    import uuid, hashlib
    try:
        with open("/etc/machine-id") as f:
            seed = f.read().strip()
    except FileNotFoundError:
        seed = "pitun-default-seed"
    hwid = str(uuid.UUID(hashlib.md5(f"pitun-happ-{seed}-{ua_key}".encode()).hexdigest()))
    os_canonical, os_ver, model = _HAPP_PROFILES.get(ua_key, _HAPP_PROFILES["happ"])
    return {
        "X-App-Version": _HAPP_VERSION,
        "X-Device-Locale": "RU",
        "X-Device-Os": os_canonical,
        "X-Device-Model": model,
        "X-Hwid": hwid,
        "X-Ver-Os": os_ver,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[SubscriptionRead])
async def list_subscriptions(session: AsyncSession = Depends(get_session)):
    return list((await session.exec(select(Subscription))).all())


@router.post("", response_model=SubscriptionRead, status_code=201)
async def create_subscription(
    data: SubscriptionCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    sub = Subscription(**data.model_dump())
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    background_tasks.add_task(_fetch_subscription, sub.id)
    return sub


@router.get("/{sub_id}", response_model=SubscriptionRead)
async def get_subscription(sub_id: int, session: AsyncSession = Depends(get_session)):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    return sub


@router.patch("/{sub_id}", response_model=SubscriptionRead)
async def update_subscription(
    sub_id: int, data: SubscriptionUpdate, session: AsyncSession = Depends(get_session)
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(sub, k, v)
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return sub


@router.delete("/{sub_id}", status_code=204)
async def delete_subscription(
    sub_id: int,
    delete_nodes: bool = True,
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    if delete_nodes:
        nodes = (await session.exec(select(Node).where(Node.subscription_id == sub_id))).all()
        for n in nodes:
            await session.delete(n)
    await session.delete(sub)
    await session.commit()


@router.post("/{sub_id}/refresh", status_code=202)
async def refresh_subscription(
    sub_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    # Per-subscription mutex — concurrent calls return 409 instead of
    # spawning duplicate fetch tasks. Without this, two clicks within
    # a few hundred ms (UI double-click, scheduler tick overlapping a
    # manual refresh, two browser tabs etc.) used to fire two
    # background `_fetch_subscription` runs against the same row.
    # Each one would `delete all old nodes → insert new`, so the
    # second one racing the first could observe a half-deleted state
    # and import a partial node set, or both could land near-
    # simultaneously and corrupt `active_node_id` via duplicate
    # delete-then-create. Observed in the wild on 192.168.1.4 —
    # logs show 4 refreshes within 60s with one returning 57 nodes
    # instead of the canonical 1256.
    if _is_refresh_active(sub_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "subscription refresh already in progress",
                "subscription_id": sub_id,
                "hint": "Wait for the previous refresh to finish before retrying.",
            },
        )
    background_tasks.add_task(_fetch_subscription, sub_id)
    return {"status": "refresh queued"}


# ── Concurrent-refresh guard ──────────────────────────────────────────────────
#
# Module-level set of subscription ids that have an active refresh
# in flight. `_fetch_subscription` adds on entry, removes in finally.
# Cheap, in-process — fine for the single-uvicorn-worker deployment.
# If we ever scale to multiple workers, this needs to move to a DB
# advisory lock or a Redis SET.
_REFRESH_IN_FLIGHT: set[int] = set()


def _is_refresh_active(sub_id: int) -> bool:
    return sub_id in _REFRESH_IN_FLIGHT


# ── Fetch logic ───────────────────────────────────────────────────────────────

def _node_fingerprint(node_dict: dict) -> str:
    """Deterministic identity for a subscription Node.

    Two refreshes of the same subscription should produce the SAME
    fingerprint for the SAME server entry, so we can match new entries
    back to existing DB rows and reuse the row id. Picking the right
    fields: protocol + address + port is the core; uuid OR password
    disambiguates same-host-multiple-accounts panels; transport + tls
    catches the case where one server hosts multiple inbounds at the
    same port (rare but real on some 3x-ui-pro setups using xhttp +
    vless reality on the same :443).

    SNI is deliberately NOT in the fingerprint — operators sometimes
    rotate SNI per refresh (panels with random cover-domain pools)
    and we don't want that to look like a "new node".
    """
    keys = (
        node_dict.get("protocol", ""),
        node_dict.get("address", ""),
        node_dict.get("port", 0),
        node_dict.get("uuid", "") or node_dict.get("password", "") or "",
        node_dict.get("transport", "") or "tcp",
        node_dict.get("tls", "") or "none",
    )
    return "|".join(str(k) for k in keys)


def _node_row_fingerprint(node) -> str:
    """Same fingerprint shape, but on a Node ORM row instead of the
    parsed dict. Kept symmetric — change one, change the other."""
    return "|".join(str(k) for k in (
        node.protocol or "",
        node.address or "",
        node.port or 0,
        node.uuid or node.password or "",
        node.transport or "tcp",
        node.tls or "none",
    ))


async def _fetch_subscription(sub_id: int) -> None:
    """Download subscription URL and import nodes. Runs in background."""
    from app.core.uri_parser import parse_uri_list
    from datetime import datetime, timezone

    # Refresh mutex — see comment on `_REFRESH_IN_FLIGHT`. The endpoint
    # already checks this before dispatching, but the scheduler path
    # (sub_scheduler.py → `_fetch_subscription`) doesn't — so we guard
    # the function itself too. If a manual refresh + scheduler tick
    # race, the second one bails out cleanly.
    if sub_id in _REFRESH_IN_FLIGHT:
        logger.info(
            "Subscription %d refresh skipped — another refresh in flight",
            sub_id,
        )
        return
    _REFRESH_IN_FLIGHT.add(sub_id)
    try:
        await _fetch_subscription_unlocked(sub_id)
    finally:
        _REFRESH_IN_FLIGHT.discard(sub_id)


async def _fetch_subscription_unlocked(sub_id: int) -> None:
    """The actual fetch — separate from the wrapper so the mutex
    cleanup `finally:` block stays the only exit point."""
    from app.core.uri_parser import parse_uri_list
    from datetime import datetime, timezone

    async with AsyncSession(get_async_engine()) as session:
        sub = await session.get(Subscription, sub_id)
        if not sub:
            return

        # Pick UA: explicit per-subscription override > preset map > v2ray fallback.
        # Override is for panels that gate on a fingerprint we don't ship
        # a preset for — paste the UA the panel docs specify.
        custom = (sub.custom_ua or "").strip()
        ua = custom or _UA_MAP.get(sub.ua, _UA_MAP["v2ray"])
        headers = {
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,en,*",
            "Accept-Encoding": "gzip, deflate",
        }
        # Happ-based panels gate on UA + a bundle of X-* headers. Attach
        # them whenever:
        #   - the subscription's preset is a `happ-*` profile, OR
        #   - the custom UA starts with "Happ/" (likely a Happ-targeted panel
        #     even if the user pasted a unique UA string).
        # The profile key drives which OS the X-* describe so UA + headers
        # stay consistent.
        ua_lc = ua.lower()
        if sub.ua in _HAPP_PROFILES:
            headers.update(_get_happ_headers(sub.ua))
        elif ua_lc.startswith("happ/"):
            headers.update(_get_happ_headers("happ"))

        content: str = ""
        err_msg: str = ""

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30,
                verify=False,  # many self-hosted panels use self-signed certs
            ) as client:
                resp = await client.get(sub.url, headers=headers)
                resp.raise_for_status()
                content = resp.text
        except httpx.HTTPStatusError as exc:
            err_msg = f"HTTP {exc.response.status_code}"
            logger.error("Subscription %d fetch failed: %s for url '%s'", sub_id, err_msg, sub.url)
        except Exception as exc:
            err_msg = str(exc)
            logger.error("Subscription %d fetch failed: %s", sub_id, exc)

        if err_msg:
            # Capture name before commit — ORM expires attributes on commit
            # and reloading them in async context trips MissingGreenlet.
            sub_name = sub.name
            # Persist the error so UI can show it
            sub.last_error = err_msg
            sub.last_updated = datetime.now(tz=timezone.utc)
            session.add(sub)
            await session.commit()
            from app.core.events import record_event
            await record_event(
                category="subscription.failed",
                severity="error",
                title=f"Subscription failed: '{sub_name}'",
                details=err_msg,
                entity_id=sub_id,
                # Auto-update can retry every minute on a broken sub. 30 min
                # dedup keeps the feed informative without spamming.
                dedup_window_sec=1800,
            )
            return

        if sub.filter_regex:
            try:
                pattern = re.compile(sub.filter_regex, re.I)
            except re.error:
                pattern = None
        else:
            pattern = None

        parsed = parse_uri_list(content)

        # Filter out dummy/placeholder nodes returned by some panels
        # (e.g. "App not supported", "Limit of devices reached", 0.0.0.0).
        # Panels do this when they detect an unsupported client UA, the
        # subscription is expired, or — like xtoolapp / marzban with
        # Happ-iOS gating — when our request doesn't match the exact
        # client signature they require (TG auth, hwid, etc.).
        _DUMMY_MARKERS = ["0.0.0.0", "127.0.0.1", ""]
        _DUMMY_NAMES = ["app not supported", "limit of devices", "not supported",
                        "expired", "disabled", "blocked"]
        # All-zero / placeholder UUID (`00000000-0000-…`) is the canonical
        # "this isn't a real node" marker across panels — catch it even
        # when the panel hides the dummy behind a plausible-looking name
        # or address.
        _ZERO_UUID = "00000000-0000-0000-0000-000000000000"
        real_nodes = []
        dummy_names = []
        for n in parsed:
            addr = n.get("address", "")
            name = n.get("name", "").lower()
            uid = n.get("uuid", "")
            port = n.get("port") or 0
            is_dummy = (
                addr in _DUMMY_MARKERS
                or any(m in name for m in _DUMMY_NAMES)
                or uid == _ZERO_UUID
                or port in (0, 1)
            )
            if is_dummy:
                dummy_names.append(n.get("name") or "unnamed-dummy")
                continue
            real_nodes.append(n)
        parsed = real_nodes

        if dummy_names and not parsed:
            # Panel returned only dummy nodes — report as error
            sub.last_error = f"Panel: {dummy_names[0]}"
            sub.last_updated = datetime.now(tz=timezone.utc)
            session.add(sub)
            await session.commit()
            logger.warning("Subscription %d: panel returned dummy nodes: %s", sub_id, dummy_names)
            return

        if pattern:
            parsed = [n for n in parsed if pattern.search(n.get("name", ""))]

        if not parsed:
            sub.last_error = "0 nodes parsed from response"
            sub.last_updated = datetime.now(tz=timezone.utc)
            session.add(sub)
            await session.commit()
            logger.warning("Subscription %d: 0 nodes parsed, keeping existing nodes", sub_id)
            return

        # ── Stable-fingerprint upsert (since v1.3.6) ─────────────────
        #
        # Until 1.3.5 this was a brute "delete every old Node row for
        # this subscription, then insert the parsed list as fresh
        # rows". That had a UX-fatal side effect: every refresh
        # invalidated `Settings.active_node_id` because the row it
        # pointed at was gone and the "same" server came back with a
        # new id. UI showed "No active node selected" after every
        # auto-refresh; routing fell back to direct.
        #
        # New flow:
        #   1. Snapshot active_node_id (we may need to remap).
        #   2. Build fingerprint → old Node row map.
        #   3. For each parsed entry: if fingerprint matches an old
        #      row → UPDATE in place (preserves id, drag-order,
        #      last_check, latency_ms). Else → INSERT new row.
        #   4. Old rows that didn't match any parsed entry → DELETE.
        #   5. If active_node_id pointed at one of the deleted rows,
        #      try to remap to a same-fingerprint replacement; if no
        #      remap is possible, pick the first remaining enabled +
        #      online node from this subscription as a fallback so the
        #      user doesn't lose proxy after a refresh.
        old_nodes = (await session.exec(
            select(Node).where(Node.subscription_id == sub_id)
        )).all()
        old_by_fp: dict = {}
        old_by_id: dict = {}
        for n in old_nodes:
            old_by_fp[_node_row_fingerprint(n)] = n
            old_by_id[n.id] = n

        # Snapshot active node id (may live in this subscription or in
        # another one — we only care if it's in THIS subscription's
        # old set).
        active_row = (await session.exec(
            select(DBSettings).where(DBSettings.key == "active_node_id")
        )).first()
        active_id_before: int | None = None
        if active_row and active_row.value:
            try:
                active_id_before = int(active_row.value)
            except (TypeError, ValueError):
                active_id_before = None
        active_was_in_sub = (
            active_id_before is not None and active_id_before in old_by_id
        )

        # Field copy list — keep in sync with Node ORM. We deliberately
        # don't blow away `order` / `last_check` / `latency_ms` /
        # `is_online` on update so reorder + healthcheck history
        # survive the refresh.
        _MUTABLE_FIELDS = (
            "name", "protocol", "address", "port", "uuid", "password",
            "transport", "tls", "sni", "fingerprint", "alpn",
            "allow_insecure", "flow",
            "ws_path", "ws_host", "grpc_service", "grpc_mode",
            "grpc_authority", "http_path", "http_host",
            "kcp_seed", "kcp_header",
            "reality_pbk", "reality_sid", "reality_spx",
            "wg_private_key", "wg_public_key", "wg_preshared_key",
            "wg_endpoint", "wg_mtu", "wg_reserved", "wg_local_address",
            "hy2_obfs", "hy2_obfs_password",
            "group", "note",
        )

        imported = 0
        seen_fps: set[str] = set()
        for node_dict in parsed:
            node_dict["subscription_id"] = sub_id
            fp = _node_fingerprint(node_dict)
            seen_fps.add(fp)
            existing = old_by_fp.get(fp)
            try:
                if existing is not None:
                    # UPDATE in place — preserves id, order, health.
                    for k in _MUTABLE_FIELDS:
                        if k in node_dict:
                            setattr(existing, k, node_dict[k])
                    session.add(existing)
                else:
                    # INSERT new.
                    node = Node(**{
                        k: v for k, v in node_dict.items() if hasattr(Node, k)
                    })
                    session.add(node)
                imported += 1
            except Exception:
                pass

        # Delete old rows that didn't match any parsed entry (vanished
        # from the panel). Active node remap below picks up the slack
        # if the active one is in this set.
        removed_ids: set[int] = set()
        for fp_old, n in old_by_fp.items():
            if fp_old not in seen_fps:
                removed_ids.add(n.id)
                await session.delete(n)

        # Heal active_node_id if it pointed at a now-removed row.
        # Prefer: a node that survived the refresh (same id still
        # valid). Fallback: first enabled + online node from this
        # subscription. Last resort: leave as-is (admin can manually
        # repick — at least we don't fail silently).
        healed_active: int | None = None
        if active_was_in_sub and active_id_before in removed_ids:
            # Try to find a replacement from the SAME subscription.
            # Re-query because the in-memory `old_by_fp` map only knows
            # about pre-update rows; we want post-update survivors.
            survivors = (await session.exec(
                select(Node)
                .where(Node.subscription_id == sub_id)
                .where(Node.enabled == True)  # noqa: E712
                .order_by(Node.is_online.desc(), Node.id)  # type: ignore[union-attr]
            )).all()
            if survivors:
                healed_active = survivors[0].id
                active_row.value = str(healed_active)
                session.add(active_row)
                logger.warning(
                    "Subscription %d refresh: active_node_id %d disappeared "
                    "from panel — auto-picked %d (%r) from same subscription",
                    sub_id, active_id_before, healed_active, survivors[0].name,
                )

        sub.last_updated = datetime.now(tz=timezone.utc)
        sub.node_count = imported
        sub.last_error = None  # clear error on success
        session.add(sub)
        await session.commit()
        logger.info(
            "Subscription %d: imported %d nodes (matched=%d new=%d removed=%d%s)",
            sub_id, imported,
            sum(1 for fp in seen_fps if fp in old_by_fp),
            sum(1 for fp in seen_fps if fp not in old_by_fp),
            len(removed_ids),
            f", active_node healed: {active_id_before}→{healed_active}"
            if healed_active is not None else "",
        )
