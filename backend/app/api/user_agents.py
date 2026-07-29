"""User-Agent template CRUD + JSON export/import.

A template is the fingerprint a subscription fetch presents: the
`User-Agent` string plus any extra headers the panel gates on. Header
assembly and the validation ruleset live in `app/core/ua_templates.py`.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import APP_VERSION
from app.core.ua_templates import dump_headers, sanitize_headers
from app.database import get_session
from app.models import Subscription, UserAgentTemplate
from app.schemas import (
    UserAgentTemplateCreate,
    UserAgentTemplateImportResponse,
    UserAgentTemplateRead,
    UserAgentTemplateUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-agents", tags=["user-agents"])

EXPORT_KIND = "pitun-ua-templates-export"
EXPORT_VERSION = 1

# Bounds the dropdown and an import bundle's blast radius.
MAX_TEMPLATES = 200


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _usage_counts(session: AsyncSession) -> Dict[str, int]:
    """How many subscriptions reference each template key.

    Counted in Python: subscriptions number in the tens, and a GROUP BY
    would need a raw-SQL escape hatch under SQLModel's async session.
    """
    keys = (await session.exec(select(Subscription.ua))).all()
    counts: Dict[str, int] = {}
    for key in keys:
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _to_read(row: UserAgentTemplate, usage: int = 0) -> UserAgentTemplateRead:
    return UserAgentTemplateRead(
        id=row.id,
        key=row.key,
        name=row.name,
        user_agent=row.user_agent,
        headers=sanitize_headers(row.headers),
        description=row.description,
        builtin=bool(row.builtin),
        order=row.order,
        usage_count=usage,
    )


async def _get_or_404(session: AsyncSession, tpl_id: int) -> UserAgentTemplate:
    row = await session.get(UserAgentTemplate, tpl_id)
    if row is None:
        raise HTTPException(404, "User-Agent template not found")
    return row


def _row_label(entry: Any) -> str:
    """Safe identifier for an import row, for logs and error text.

    The raw `key` is the only field present on a row that failed
    validation. Stripped of control characters (CWE-117) and truncated,
    since a bundle is untrusted. Never surfaces `user_agent` or
    `headers` — those can carry panel credentials.
    """
    if not isinstance(entry, dict):
        return "?"
    raw = entry.get("key")
    if not isinstance(raw, str) or not raw.strip():
        return "?"
    cleaned = "".join(ch for ch in raw.strip() if ch.isprintable())
    return (cleaned[:64] or "?")


async def _key_taken(
    session: AsyncSession, key: str, *, exclude_id: int | None = None
) -> bool:
    stmt = select(UserAgentTemplate).where(UserAgentTemplate.key == key)
    if exclude_id is not None:
        stmt = stmt.where(UserAgentTemplate.id != exclude_id)
    return (await session.exec(stmt)).first() is not None


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[UserAgentTemplateRead])
async def list_ua_templates(session: AsyncSession = Depends(get_session)):
    """List templates in dropdown order, each with its usage count."""
    rows = (await session.exec(
        select(UserAgentTemplate).order_by(UserAgentTemplate.order, UserAgentTemplate.id)
    )).all()
    usage = await _usage_counts(session)
    return [_to_read(r, usage.get(r.key, 0)) for r in rows]


@router.post("", response_model=UserAgentTemplateRead, status_code=201)
async def create_ua_template(
    body: UserAgentTemplateCreate, session: AsyncSession = Depends(get_session)
):
    """Create a template. Key must be unique."""
    if await _key_taken(session, body.key):
        raise HTTPException(
            400, f"A User-Agent template with key {body.key!r} already exists"
        )
    total = len((await session.exec(select(UserAgentTemplate.id))).all())
    if total >= MAX_TEMPLATES:
        raise HTTPException(
            409,
            f"Template limit reached ({MAX_TEMPLATES}). Delete an unused one first.",
        )

    row = UserAgentTemplate(
        key=body.key,
        name=body.name,
        user_agent=body.user_agent,
        headers=dump_headers(body.headers),
        description=body.description,
        builtin=False,
        order=body.order,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("Created UA template %r (id=%d)", row.key, row.id)
    return _to_read(row, 0)


# Must precede `/{tpl_id}` or FastAPI reads "export-json" as an int param.
@router.get("/export-json")
async def export_ua_templates(session: AsyncSession = Depends(get_session)):
    """Return every template as a downloadable JSON bundle."""
    rows = (await session.exec(
        select(UserAgentTemplate).order_by(UserAgentTemplate.order, UserAgentTemplate.id)
    )).all()
    payload = {
        "kind": EXPORT_KIND,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pitun_version": APP_VERSION,
        "count": len(rows),
        "templates": [
            {
                "key": r.key,
                "name": r.name,
                "user_agent": r.user_agent,
                # Sanitised so the bundle survives import re-validation.
                "headers": sanitize_headers(r.headers),
                "description": r.description,
                "order": r.order,
            }
            for r in rows
        ],
    }
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": (
                'attachment; filename="pitun-ua-templates-'
                f'{datetime.now().strftime("%Y%m%d-%H%M%S")}.json"'
            ),
        },
    )


@router.post("/import-json", response_model=UserAgentTemplateImportResponse)
async def import_ua_templates(
    payload: Dict[str, Any],
    replace: bool = Query(
        False,
        description=(
            "Delete every existing template before importing (full restore). "
            "Subscriptions keep their `ua` key, so any key absent from the "
            "bundle falls back to the built-in map until you re-add it."
        ),
    ),
    overwrite: bool = Query(
        False,
        description=(
            "Update existing templates whose key matches instead of skipping "
            "them. Ignored when `replace=true` (nothing is left to match)."
        ),
    ),
    session: AsyncSession = Depends(get_session),
):
    """Restore templates from an exported bundle.

    Additive by default (existing key skipped); `overwrite` updates a
    match in place, keeping its id so subscriptions stay attached;
    `replace` wipes first. Rows validate individually — one bad entry
    lands in `errors` and the rest still import.
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid bundle: expected a JSON object at the top level")
    if payload.get("kind") != EXPORT_KIND:
        raise HTTPException(
            400, "Not a PiTun User-Agent template export bundle (kind mismatch)"
        )
    if payload.get("version") != EXPORT_VERSION:
        raise HTTPException(400, f"Unsupported export version: {payload.get('version')}")
    entries = payload.get("templates")
    if not isinstance(entries, list):
        raise HTTPException(400, "Bundle missing 'templates' array")
    if len(entries) > MAX_TEMPLATES:
        raise HTTPException(
            400,
            f"Bundle holds {len(entries)} templates — more than the "
            f"{MAX_TEMPLATES} limit.",
        )

    imported = updated = skipped = 0
    errors: List[str] = []

    if replace:
        for row in (await session.exec(select(UserAgentTemplate))).all():
            await session.delete(row)
        await session.flush()

    # A key twice in one bundle should be a per-row error, not a UNIQUE
    # violation that rolls back the whole request.
    seen_keys: set[str] = set()

    for entry in entries:
        # Label from the RAW key: deriving it from the validated object
        # would leave every rejected row as "?".
        row_label = _row_label(entry)
        try:
            if not isinstance(entry, dict):
                raise ValueError("entry is not an object")
            # Forward-compat: a newer export with extra columns still imports.
            allowed = set(UserAgentTemplateCreate.model_fields.keys())
            clean = {k: v for k, v in entry.items() if k in allowed}
            validated = UserAgentTemplateCreate(**clean)
            row_label = validated.key

            if validated.key in seen_keys:
                raise ValueError("duplicate key within the bundle")
            seen_keys.add(validated.key)

            existing = None
            if not replace:
                existing = (await session.exec(
                    select(UserAgentTemplate).where(
                        UserAgentTemplate.key == validated.key
                    )
                )).first()

            if existing is not None and not overwrite:
                skipped += 1
                continue

            if existing is not None:
                existing.name = validated.name
                existing.user_agent = validated.user_agent
                existing.headers = dump_headers(validated.headers)
                existing.description = validated.description
                existing.order = validated.order
                session.add(existing)
                updated += 1
                continue

            session.add(UserAgentTemplate(
                key=validated.key,
                name=validated.name,
                user_agent=validated.user_agent,
                headers=dump_headers(validated.headers),
                description=validated.description,
                builtin=False,
                order=validated.order,
            ))
            await session.flush()
            imported += 1
        except Exception as exc:  # noqa: BLE001 — surface per-row errors
            # Type only, never the value: a ValidationError echoes its
            # input and a row's headers can hold a panel API key
            # (CWE-209/532/117).
            logger.warning(
                "UA template import row failed: key=%r err_type=%s",
                row_label, type(exc).__name__,
            )
            errors.append(f"{row_label}: import failed ({type(exc).__name__})")

    await session.commit()
    logger.info(
        "UA template import: %d created, %d updated, %d skipped, %d failed",
        imported, updated, skipped, len(errors),
    )
    return UserAgentTemplateImportResponse(
        imported=imported, updated=updated, skipped=skipped, errors=errors
    )


@router.get("/{tpl_id}", response_model=UserAgentTemplateRead)
async def get_ua_template(tpl_id: int, session: AsyncSession = Depends(get_session)):
    row = await _get_or_404(session, tpl_id)
    usage = await _usage_counts(session)
    return _to_read(row, usage.get(row.key, 0))


@router.patch("/{tpl_id}", response_model=UserAgentTemplateRead)
async def update_ua_template(
    tpl_id: int,
    body: UserAgentTemplateUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Edit a template, built-ins included.

    Renaming the `key` would orphan every subscription pointing at the old
    value — they would fall back to the built-in map and start presenting
    a different fingerprint — so they are re-pointed in the same
    transaction.
    """
    row = await _get_or_404(session, tpl_id)
    patch = body.model_dump(exclude_unset=True)

    old_key = row.key
    new_key = patch.get("key")
    migrated = 0
    if new_key is not None and new_key != old_key:
        if await _key_taken(session, new_key, exclude_id=tpl_id):
            raise HTTPException(
                400, f"A User-Agent template with key {new_key!r} already exists"
            )
        subs = (await session.exec(
            select(Subscription).where(Subscription.ua == old_key)
        )).all()
        for sub in subs:
            sub.ua = new_key
            session.add(sub)
            migrated += 1

    if "headers" in patch:
        patch["headers"] = dump_headers(patch["headers"])

    for field, value in patch.items():
        setattr(row, field, value)
    session.add(row)
    await session.commit()
    await session.refresh(row)

    if migrated:
        logger.info(
            "UA template %r renamed to %r — re-pointed %d subscription(s)",
            old_key, row.key, migrated,
        )
    usage = await _usage_counts(session)
    return _to_read(row, usage.get(row.key, 0))


@router.delete("/{tpl_id}", status_code=204)
async def delete_ua_template(
    tpl_id: int,
    force: bool = Query(
        False,
        description=(
            "Delete even while subscriptions reference this template. They "
            "keep the now-dangling key and fall back to the built-in UA map, "
            "which means their fingerprint changes on the next refresh."
        ),
    ),
    session: AsyncSession = Depends(get_session),
):
    """Delete a template. Refuses while it is in use unless `force=true`."""
    row = await _get_or_404(session, tpl_id)

    in_use = (await session.exec(
        select(Subscription).where(Subscription.ua == row.key)
    )).all()
    if in_use and not force:
        names = ", ".join(sorted(s.name for s in in_use)[:5])
        raise HTTPException(
            409,
            (
                f"Template {row.key!r} is used by {len(in_use)} subscription(s): "
                f"{names}. Point them at another template first, or pass "
                "?force=true — they will fall back to the built-in UA and "
                "their fingerprint will change on the next refresh."
            ),
        )

    await session.delete(row)
    await session.commit()
    logger.info(
        "Deleted UA template %r (id=%d, was used by %d subscription(s))",
        row.key, tpl_id, len(in_use),
    )
