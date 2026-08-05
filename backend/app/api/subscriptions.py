"""Subscription management: CRUD + fetch/refresh."""
import asyncio
import logging
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ua_templates import build_subscription_headers
from app.database import get_session, get_async_engine
from app.models import Node, NodeCircle, RoutingRule, Settings as DBSettings, Subscription
from app.schemas import SubscriptionCreate, SubscriptionRead, SubscriptionUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

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
    background_tasks: BackgroundTasks,
    delete_nodes: bool = True,
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    deleted_ids: set = set()
    if delete_nodes:
        nodes = (await session.exec(select(Node).where(Node.subscription_id == sub_id))).all()
        for n in nodes:
            deleted_ids.add(n.id)
            await session.delete(n)
    await session.delete(sub)
    await session.commit()

    if deleted_ids:
        # Nodes elsewhere may relay THROUGH one of these. Left dangling,
        # the chained node silently stops working with nothing in the UI
        # to say why — the exact failure reported in the wild.
        from app.api.nodes import clear_chain_refs, _heal_active_node_after_delete
        unchained = await clear_chain_refs(session, deleted_ids)
        if unchained:
            await session.commit()
            logger.warning(
                "Subscription %d delete: unchained node(s) %s — their relay was removed",
                sub_id, unchained,
            )
            from app.core.events import record_event
            await record_event(
                category="node.unchained",
                severity="warning",
                title="Chained nodes lost their relay",
                details=(
                    f"Deleting subscription {sub_id} removed the relay node(s) that "
                    f"node(s) {unchained} were chained through; the chain link was "
                    f"cleared and they now connect directly."
                ),
            )
        # The subscription may have carried the active node with it.
        await _heal_active_node_after_delete(session, deleted_ids, background_tasks)


@router.post("/{sub_id}/refresh", status_code=202)
async def refresh_subscription(
    sub_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    # Per-subscription mutex. Two refreshes racing (double-click,
    # scheduler tick overlapping a manual refresh, two tabs) could each
    # observe a half-deleted node set and import a partial one, or
    # corrupt `active_node_id`.
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


async def _config_referenced_node_ids(session: AsyncSession) -> set:
    """Node ids the CURRENT xray config actually depends on.

    Anything else can change freely without touching the dataplane.
    Covers: the active node, every circle/balancer member, `node:<id>`
    rule targets, and any node reachable as a chain hop from those.
    """
    import json as _json

    from app.models import BalancerGroup

    ids: set = set()

    active_row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "active_node_id")
    )).first()
    if active_row and active_row.value:
        try:
            ids.add(int(active_row.value))
        except (TypeError, ValueError):
            pass

    for group in (await session.exec(select(NodeCircle))).all():
        try:
            members = (
                _json.loads(group.node_ids)
                if isinstance(group.node_ids, str) else (group.node_ids or [])
            )
        except Exception:  # noqa: BLE001
            continue
        ids.update(i for i in members if isinstance(i, int))

    for bal in (await session.exec(select(BalancerGroup))).all():
        try:
            members = (
                _json.loads(bal.node_ids)
                if isinstance(bal.node_ids, str) else (bal.node_ids or [])
            )
        except Exception:  # noqa: BLE001
            continue
        ids.update(i for i in members if isinstance(i, int))

    for rule in (await session.exec(select(RoutingRule))).all():
        if rule.enabled and (rule.action or "").startswith("node:"):
            try:
                ids.add(int(rule.action.split(":", 1)[1]))
            except (TypeError, ValueError):
                pass

    # Follow chain_node_id hops — a relay's parameters matter just as
    # much as the exit's.
    seen: set = set()
    frontier = set(ids)
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        node = await session.get(Node, current)
        if node and node.chain_node_id:
            ids.add(node.chain_node_id)
            frontier.add(node.chain_node_id)

    return ids


async def _reload_if_config_nodes_touched(
    session: AsyncSession, *, touched_ids: set, force: bool = False,
) -> None:
    """Re-apply the dataplane when a refresh changed a node the config uses.

    A panel rotating a Reality key or an SNI updates the row in place —
    the fingerprint still matches, so nothing else notices — while the
    running xray keeps dialling with the old crypto. Health checks don't
    catch it either: they TCP-connect to the address from the FRESH row.
    Result: the whole LAN loses internet while the UI stays green.
    """
    if not touched_ids and not force:
        return
    try:
        from app.core.xray import xray_manager
        if not xray_manager.is_running:
            return
        if not force:
            relevant = await _config_referenced_node_ids(session)
            if not (touched_ids & relevant):
                return
        from app.api.system import (
            _apply_nftables, _load_settings_map, _regenerate_and_write,
        )
        await _regenerate_and_write(session)
        await xray_manager.reload()
        settings_map = await _load_settings_map(session)
        await _apply_nftables(session, settings_map)
        logger.info("Subscription refresh changed live nodes — dataplane reloaded")
    except Exception as exc:  # noqa: BLE001
        # Never fail a refresh over this: the DB is already consistent.
        logger.warning("Reload after subscription refresh failed: %s", exc)


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

        headers = await build_subscription_headers(session, sub)

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

        # First: dedup `parsed` by fingerprint. Panels (especially Happ
        # JSON bundles) often expose the SAME (addr, port, uuid) server
        # under multiple SNI / fingerprint / sid combos for domain-
        # fronting resilience — each is one outbound entry. Our Node
        # model treats (protocol, addr, port, uuid, password) as the
        # unit (see `_node_fingerprint`), so collapse variants to one
        # row, last-wins. Without this, the upsert touches only one
        # row per fingerprint and the other duplicates from the OLD
        # delete-and-insert era stay forever as orphans (their fp is
        # in `seen_fps` → not removed; never the matched `existing` →
        # not updated). Seen in the wild on a Happ-macos subscription
        # that returned 1256 outbounds for 303 unique servers; without
        # this dedup the row count never collapsed back to 303.
        deduped: dict[str, dict] = {}
        for n in parsed:
            fp = _node_fingerprint(n)
            deduped[fp] = n  # last-wins
        parsed_dedup_skipped = len(parsed) - len(deduped)
        parsed = list(deduped.values())
        # Optional country-flag prefix (no-op unless a GeoLite2 DB exists).
        from app.core.geoip_lookup import enrich_parsed_nodes
        enrich_parsed_nodes(parsed)
        if parsed_dedup_skipped > 0:
            logger.info(
                "Subscription %d: collapsed %d duplicate parsed entries "
                "(same fingerprint, different SNI/fp variants)",
                sub_id, parsed_dedup_skipped,
            )

        old_nodes = (await session.exec(
            select(Node).where(Node.subscription_id == sub_id)
        )).all()
        # `old_by_fp` keys by fingerprint; multiple old rows with the
        # same fingerprint (legacy duplicates from pre-1.3.6 inserts)
        # collapse here — we keep the survivor with the smallest id
        # (so external references — active_node_id, NodeCircle,
        # RoutingRule — that point at the lowest id of a fingerprint
        # group keep working) and remap all references on the other
        # rows to the survivor before deleting them.
        old_by_fp: dict = {}
        old_by_id: dict = {}
        # Process in id-ascending order so the FIRST seen for each fp
        # is the smallest id → "survivor" is stable.
        for n in sorted(old_nodes, key=lambda r: r.id):
            fp = _node_row_fingerprint(n)
            if fp not in old_by_fp:
                old_by_fp[fp] = n
            old_by_id[n.id] = n
        # Build {legacy_dup_id → survivor_id} map for transparent remap.
        legacy_dup_remap: dict[int, int] = {}
        for n in old_nodes:
            fp = _node_row_fingerprint(n)
            survivor = old_by_fp[fp]
            if survivor.id != n.id:
                legacy_dup_remap[n.id] = survivor.id

        if legacy_dup_remap:
            # Rewrite NodeCircle.node_ids so legacy-dup ids are
            # transparently swapped for their fingerprint survivor.
            # Also dedup within the list (a circle that referenced
            # both halves of a dup pair shouldn't end up with the
            # same survivor id twice).
            import json as _json
            all_circles_pre = (await session.exec(select(NodeCircle))).all()
            for circle in all_circles_pre:
                try:
                    ids = (
                        _json.loads(circle.node_ids)
                        if isinstance(circle.node_ids, str)
                        else (circle.node_ids or [])
                    )
                except Exception:
                    continue
                remapped: list[int] = []
                seen: set[int] = set()
                for i in ids:
                    new_i = legacy_dup_remap.get(i, i)
                    if new_i not in seen:
                        remapped.append(new_i)
                        seen.add(new_i)
                if remapped != ids:
                    if circle.current_index >= len(remapped):
                        circle.current_index = 0
                    circle.node_ids = _json.dumps(remapped)
                    session.add(circle)

            # Rewrite RoutingRule.action when it references a dup id
            # (form `node:<id>`). The validator in routing.py rejects
            # rules pointing at non-existent nodes, but the field is
            # mutated by SQLModel directly — nothing prevents the id
            # from going stale after we delete its row here. Remap to
            # the survivor so the rule keeps working.
            rules_remapped = 0
            all_rules = (await session.exec(select(RoutingRule))).all()
            for rule in all_rules:
                action = rule.action or ""
                if not action.startswith("node:"):
                    continue
                try:
                    rule_node_id = int(action.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                if rule_node_id in legacy_dup_remap:
                    rule.action = f"node:{legacy_dup_remap[rule_node_id]}"
                    session.add(rule)
                    rules_remapped += 1

            # Delete the legacy dup rows now.
            for dup_id in legacy_dup_remap:
                await session.delete(old_by_id[dup_id])
            logger.info(
                "Subscription %d: removed %d legacy duplicate Node rows "
                "(same fingerprint as another row; refs remapped to survivor: "
                "%d RoutingRule(s) updated)",
                sub_id, len(legacy_dup_remap), rules_remapped,
            )

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
        # If the active node was one of the legacy duplicates we just
        # deleted, transparently remap to the surviving sibling with
        # the same fingerprint and persist immediately. This keeps the
        # user's "active" pin on the same logical server through the
        # dedup pass, with no UI gap.
        if (
            active_id_before is not None
            and active_id_before in legacy_dup_remap
        ):
            survivor_id = legacy_dup_remap[active_id_before]
            logger.warning(
                "Subscription %d: active_node_id %d was a legacy duplicate "
                "of %d — remapping to survivor",
                sub_id, active_id_before, survivor_id,
            )
            if active_row is not None:
                active_row.value = str(survivor_id)
                session.add(active_row)
            active_id_before = survivor_id  # downstream heal sees survivor

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
        # Node ids whose wire parameters actually CHANGED value. The
        # fingerprint only covers protocol/address/port/uuid, so a panel
        # rotating a Reality key or an SNI updates the row in place and
        # leaves the running xray dialling with stale crypto. We reload
        # the dataplane below when a changed node is one the config uses.
        changed_ids: set[int] = set()
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
                            # `or None` folds the ""/None/0 spread the
                            # parsers produce for absent values — only a
                            # real value change counts as a change.
                            if (getattr(existing, k, None) or None) != (node_dict[k] or None):
                                changed_ids.add(existing.id)
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

        # ── Heal NodeCircles that reference removed Node ids ─────────
        #
        # Same fingerprint-upsert rationale as for `active_node_id`:
        # if a node still exists by fingerprint, its DB id survives
        # the refresh and any NodeCircle referencing it keeps working
        # untouched. If a node legitimately vanished from the panel
        # (operator removed it), its id is left as an orphan inside
        # every Circle's `node_ids` JSON list — prune it here so the
        # UI doesn't keep showing dead ids.
        #
        # We DO NOT auto-disable circles that shrink below 2 members:
        # circle_scheduler is already defensive (skips rotation when
        # `len(node_ids) < 2`), and if every member dies the routing
        # layer falls back to kill-switch / direct anyway. Leaving
        # the circle enabled lets it spring back to life automatically
        # if the operator re-adds nodes to the panel.
        circles_pruned: list[int] = []
        if removed_ids:
            import json as _json
            all_circles = (await session.exec(select(NodeCircle))).all()
            for circle in all_circles:
                try:
                    ids = (
                        _json.loads(circle.node_ids)
                        if isinstance(circle.node_ids, str)
                        else (circle.node_ids or [])
                    )
                except Exception:
                    continue
                surviving_ids = [i for i in ids if i not in removed_ids]
                if surviving_ids == ids:
                    continue  # no change for this circle
                # Reset current_index if it fell out of bounds — the
                # scheduler also does this lazily but doing it here
                # keeps the persisted state honest and matches the
                # circle's "next rotation starts from current_index"
                # mental model.
                if surviving_ids and circle.current_index >= len(surviving_ids):
                    circle.current_index = 0
                circle.node_ids = _json.dumps(surviving_ids)
                session.add(circle)
                circles_pruned.append(circle.id)
            if circles_pruned:
                logger.warning(
                    "Subscription %d refresh: pruned dangling refs from "
                    "%d NodeCircle(s) (%s)",
                    sub_id, len(circles_pruned), circles_pruned,
                )

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

        # Same dangling-relay problem as the delete path: a node that
        # vanished from the panel may have been someone's relay.
        from app.api.nodes import clear_chain_refs
        unchained = await clear_chain_refs(session, removed_ids)
        if unchained:
            await session.commit()
            logger.warning(
                "Subscription %d refresh: unchained node(s) %s — their relay vanished "
                "from the panel", sub_id, unchained,
            )
            from app.core.events import record_event
            await record_event(
                category="node.unchained",
                severity="warning",
                title="Chained nodes lost their relay",
                details=(
                    f"Subscription {sub_id} no longer serves the relay node(s) that "
                    f"node(s) {unchained} were chained through; the chain link was "
                    f"cleared and they now connect directly."
                ),
            )

        await _reload_if_config_nodes_touched(
            session,
            touched_ids=changed_ids | removed_ids,
            force=healed_active is not None,
        )
