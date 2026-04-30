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
from app.models import Node, Subscription
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
    background_tasks.add_task(_fetch_subscription, sub_id)
    return {"status": "refresh queued"}


# ── Fetch logic ───────────────────────────────────────────────────────────────

async def _fetch_subscription(sub_id: int) -> None:
    """Download subscription URL and import nodes. Runs in background."""
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

        old_nodes = (await session.exec(select(Node).where(Node.subscription_id == sub_id))).all()
        for n in old_nodes:
            await session.delete(n)

        imported = 0
        for node_dict in parsed:
            node_dict["subscription_id"] = sub_id
            try:
                node = Node(**{k: v for k, v in node_dict.items() if hasattr(Node, k)})
                session.add(node)
                imported += 1
            except Exception:
                pass

        sub.last_updated = datetime.now(tz=timezone.utc)
        sub.node_count = imported
        sub.last_error = None  # clear error on success
        session.add(sub)
        await session.commit()
        logger.info("Subscription %d: imported %d nodes", sub_id, imported)
