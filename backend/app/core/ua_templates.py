"""User-Agent templates — the fingerprint a subscription fetch presents.

Before v1.4.7 the presets lived as two module-level dicts inside
`app/api/subscriptions.py` (`_UA_MAP` + `_HAPP_PROFILES`), so adding a
preset for a new panel meant editing Python and redeploying. This module
moves the catalogue into the `useragenttemplate` table (seeded by Alembic
018 with exactly the presets that used to be hardcoded) and keeps the
hardcoded map only as a fallback for the window where the table is empty
— a fresh test DB built with `SQLModel.metadata.create_all` instead of
migrations, or an install whose migration hasn't run yet.

What a template owns
--------------------
* `key`         — stable slug stored in `Subscription.ua`. Renaming a key
                  orphans every subscription pointing at it, so the API
                  guards that (see `api/user_agents.py`).
* `user_agent`  — the literal `User-Agent` header value.
* `headers`     — JSON object of extra request headers merged on top of
                  the base set. This is the "modify the request for this
                  client" half of the feature: panels that gate on
                  `X-Api-Key`, `Authorization`, a specific `Referer`, …
                  no longer need a code change.

Happ stays partly in code
-------------------------
`HAPP_PROFILES` still lives here rather than in the `headers` column
because Happ's `X-Hwid` is *derived at request time* from
`/etc/machine-id` (and re-rolled per request when the subscription sets
`rotate_hwid`). A value frozen in the DB would break both behaviours, so
the seeded happ-* templates ship with an empty `headers` object and the
X-* bundle is injected dynamically — a template's own headers are then
applied last and can still override any of it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import UserAgentTemplate

logger = logging.getLogger(__name__)


# ── Happ client emulation ─────────────────────────────────────────────────────
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
# Each Happ flavour is its own template key (`happ`, `happ-android`, …)
# so the subscription-form dropdown lists them as discrete options. The
# legacy `happ` key is the iOS profile, which is why existing
# subscriptions kept working when the per-OS split landed.
HAPP_VERSION = "2.7.0"

# happ-* template key -> (X-Device-Os, X-Ver-Os, X-Device-Model)
HAPP_PROFILES: Dict[str, Tuple[str, str, str]] = {
    "happ":         ("iOS",     "17.4",          "iPhone15,2"),
    "happ-android": ("Android", "14",            "Pixel 8"),
    "happ-windows": ("Windows", "11_10.0.26200", "DESKTOP-PiTun_x86_64"),
    "happ-macos":   ("macOS",   "14.4",          "Mac15,7"),
}


def happ_ua_for(ua_key: str) -> str:
    """Build the User-Agent string for a Happ template key."""
    os_canonical, os_ver, model = HAPP_PROFILES.get(ua_key, HAPP_PROFILES["happ"])
    return f"Happ/{HAPP_VERSION}/{os_canonical.lower()}/{os_ver}/{model}"


def get_happ_headers(ua_key: str = "happ", *, rotate_hwid: bool = False) -> Dict[str, str]:
    """Build the X-* header bundle that real Happ sends alongside its UA.

    HWID is normally derived from `/etc/machine-id` (or a constant
    fallback on non-Linux dev machines) and stable across refreshes —
    most panels device-bind on first-seen HWID and rotating it would
    silently break the subscription. We mix the profile into the seed
    so different OS choices yield different HWIDs (real iOS vs Android
    Happ instances would never share one).

    When `rotate_hwid=True` (operator opt-in per subscription),
    generate a fresh random UUID instead. Useful when a panel starts
    HWID-throttling and returns degraded payloads to the stable
    fingerprint — we've seen panels where the same HWID over time
    starts getting placeholder 'proxy' dummies instead of real nodes.
    """
    if rotate_hwid:
        hwid = str(uuid.uuid4())
    else:
        try:
            with open("/etc/machine-id") as f:
                seed = f.read().strip()
        except FileNotFoundError:
            seed = "pitun-default-seed"
        hwid = str(uuid.UUID(hashlib.md5(f"pitun-happ-{seed}-{ua_key}".encode()).hexdigest()))
    os_canonical, os_ver, model = HAPP_PROFILES.get(ua_key, HAPP_PROFILES["happ"])
    return {
        "X-App-Version": HAPP_VERSION,
        "X-Device-Locale": "RU",
        "X-Device-Os": os_canonical,
        "X-Device-Model": model,
        "X-Hwid": hwid,
        "X-Ver-Os": os_ver,
    }


# ── Fallback catalogue ────────────────────────────────────────────────────────
#
# Used only when the `useragenttemplate` table has no row for the
# subscription's `ua` key. Keeps a pre-migration install (and the test
# suite, which builds its schema with `create_all` and therefore skips
# the seeding migration) fetching subscriptions exactly as before.
BUILTIN_UA_MAP: Dict[str, str] = {
    "v2ray": "v2rayN/6.60",
    "clash": "clash.meta/1.18.0",
    "sing-box": "sing-box/1.8.0",
    "streisand": "Streisand/3.0",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # All Happ presets resolved at import time.
    **{k: happ_ua_for(k) for k in HAPP_PROFILES},
}

# Header set every subscription fetch starts from, before the template's
# own `headers` object is layered on top.
BASE_FETCH_HEADERS: Dict[str, str] = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,en,*",
    "Accept-Encoding": "gzip, deflate",
}


# ── Seed data ─────────────────────────────────────────────────────────────────
#
# Single source of truth for the built-in templates, consumed by BOTH
# the Alembic 018 seeding step and `ensure_default_ua_templates` (the
# first-run bootstrap for installs whose DB predates migrations).
#
# `headers` is empty for every seeded row on purpose: the generic
# presets need nothing extra, and the happ-* ones get their X-* bundle
# injected dynamically (see the module docstring).
DEFAULT_UA_TEMPLATES: List[dict] = [
    {
        "key": "v2ray",
        "name": "v2rayN",
        "user_agent": BUILTIN_UA_MAP["v2ray"],
        "headers": {},
        "description": "Most panels serve a base64 URI list to this UA. Safe default.",
        "order": 10,
    },
    {
        "key": "clash",
        "name": "Clash.Meta",
        "user_agent": BUILTIN_UA_MAP["clash"],
        "headers": {},
        "description": "Panels serve Clash YAML. PiTun parses the proxies list out of it.",
        "order": 20,
    },
    {
        "key": "sing-box",
        "name": "sing-box",
        "user_agent": BUILTIN_UA_MAP["sing-box"],
        "headers": {},
        "description": "Panels serve a sing-box JSON config.",
        "order": 30,
    },
    {
        "key": "happ",
        "name": "Happ (iOS)",
        "user_agent": BUILTIN_UA_MAP["happ"],
        "headers": {},
        "description": "X-Device-* / X-Hwid headers are added automatically to match this profile.",
        "order": 40,
    },
    {
        "key": "happ-android",
        "name": "Happ (Android)",
        "user_agent": BUILTIN_UA_MAP["happ-android"],
        "headers": {},
        "description": "X-Device-* / X-Hwid headers are added automatically to match this profile.",
        "order": 50,
    },
    {
        "key": "happ-windows",
        "name": "Happ (Windows)",
        "user_agent": BUILTIN_UA_MAP["happ-windows"],
        "headers": {},
        "description": "X-Device-* / X-Hwid headers are added automatically to match this profile.",
        "order": 60,
    },
    {
        "key": "happ-macos",
        "name": "Happ (macOS)",
        "user_agent": BUILTIN_UA_MAP["happ-macos"],
        "headers": {},
        "description": "X-Device-* / X-Hwid headers are added automatically to match this profile.",
        "order": 70,
    },
    {
        "key": "streisand",
        "name": "Streisand",
        "user_agent": BUILTIN_UA_MAP["streisand"],
        "headers": {},
        "description": "Gets past some CDN client filters that reject generic UAs.",
        "order": 80,
    },
    {
        "key": "chrome",
        "name": "Chrome (desktop)",
        "user_agent": BUILTIN_UA_MAP["chrome"],
        "headers": {},
        "description": "Full browser UA. For panels behind a strict CDN bot check.",
        "order": 90,
    },
]


# ── Validation helpers ────────────────────────────────────────────────────────

# RFC 7230 token — the only characters legal in a header field name.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Template keys are slugs: they end up in `Subscription.ua`, in export
# bundles and in URLs, so keep them boring.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Header names the operator must not set from a template, because doing
# so either breaks the transport (httpx/httpcore own these) or shadows a
# field that has its own dedicated input.
FORBIDDEN_HEADER_NAMES = frozenset({
    "user-agent",        # use the `user_agent` field
    "host",              # httpx derives this from the URL
    "content-length",
    "transfer-encoding",
    "connection",
    "upgrade",
    "expect",
})


def validate_key(value: str) -> str:
    """Normalise + validate a template key. Raises ValueError."""
    v = (value or "").strip().lower()
    if not v:
        raise ValueError("key must not be empty")
    if len(v) > 64:
        raise ValueError("key must be at most 64 characters")
    if not _KEY_RE.match(v):
        raise ValueError(
            "key must start with a letter or digit and contain only "
            "lowercase letters, digits, '.', '-' or '_'"
        )
    return v


def validate_header_value(value: str, *, field: str) -> str:
    """Reject values httpx cannot send, or that would smuggle a header.

    Two distinct failure modes, both verified against httpx 0.28:

    * **Non-ASCII** — `httpx.Headers` encodes str values as ASCII and
      raises `UnicodeEncodeError`. Caught here it is a clean 422 on
      save; caught at fetch time it would surface as an opaque
      `last_error` on the subscription hours later.
    * **CR / LF / NUL** — httpx does *not* reject these, so a value like
      ``"a\\r\\nX-Admin: 1"`` would be smuggled through as an extra
      header (CWE-93 response/request splitting). We refuse it.
    """
    v = value if isinstance(value, str) else str(value)
    for ch, label in (("\r", "CR"), ("\n", "LF"), ("\0", "NUL")):
        if ch in v:
            raise ValueError(f"{field} must not contain a {label} character")
    try:
        v.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(
            f"{field} must be ASCII-only — HTTP header values cannot carry "
            "non-ASCII characters"
        ) from None
    return v


def validate_name(value: str) -> str:
    """Validate a template's display label. Raises ValueError."""
    v = (value or "").strip()
    if not v:
        raise ValueError("name must not be empty")
    if len(v) > 128:
        raise ValueError("name must be at most 128 characters")
    return v


def validate_user_agent(value: str) -> str:
    """Validate the `User-Agent` header value. Raises ValueError."""
    v = (value or "").strip()
    if not v:
        raise ValueError("user_agent must not be empty")
    if len(v) > 512:
        raise ValueError("user_agent must be at most 512 characters")
    return validate_header_value(v, field="user_agent")


def validate_description(value: Optional[str]) -> Optional[str]:
    """Normalise a free-text description. Empty collapses to None."""
    if value is None:
        return None
    v = value.strip()
    if len(v) > 512:
        raise ValueError("description must be at most 512 characters")
    return v or None


def validate_headers(raw: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Validate a template's extra-headers mapping.

    An empty *value* is legal and meaningful: it removes the header from
    the request instead of sending it blank (see `apply_header_overrides`).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("headers must be an object of name -> value")
    if len(raw) > 32:
        raise ValueError("headers must contain at most 32 entries")

    out: Dict[str, str] = {}
    seen: Dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            raise ValueError("header names must be strings")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("header names must not be empty")
        if not _HEADER_NAME_RE.match(clean_name):
            raise ValueError(
                f"invalid header name {clean_name!r} — only RFC 7230 token "
                "characters are allowed (no spaces or colons)"
            )
        lowered = clean_name.lower()
        if lowered in FORBIDDEN_HEADER_NAMES:
            hint = (
                " — set it in the User-Agent field instead"
                if lowered == "user-agent" else ""
            )
            raise ValueError(f"header {clean_name!r} cannot be overridden{hint}")
        if lowered in seen:
            raise ValueError(
                f"duplicate header {clean_name!r} (already set as {seen[lowered]!r})"
            )
        seen[lowered] = clean_name
        if value is None:
            value = ""
        out[clean_name] = validate_header_value(
            value, field=f"header {clean_name!r} value"
        )
    return out


# ── (de)serialisation ─────────────────────────────────────────────────────────

def parse_headers(raw: Optional[str]) -> Dict[str, str]:
    """Decode the `headers` column into a dict, defensively.

    A malformed blob (hand-edited DB, half-written import) degrades to
    "no extra headers" rather than breaking every refresh of every
    subscription that uses the template.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("UA template has unparseable headers JSON — ignoring")
        return {}
    if not isinstance(data, dict):
        logger.warning("UA template headers JSON is not an object — ignoring")
        return {}
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def sanitize_headers(raw) -> Dict[str, str]:
    """Lenient counterpart to `validate_headers`, for data on the way OUT.

    `validate_headers` raises, which is right for a save. But a row can
    already hold something it would reject — a hand-edited DB, or a rule
    we tightened after the row was written. Listing templates must not
    500 because of one bad entry, so here we drop the offenders and keep
    the rest.
    """
    parsed = parse_headers(raw) if isinstance(raw, (str, type(None))) else raw
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, str] = {}
    for name, value in parsed.items():
        try:
            out.update(validate_headers({name: value}))
        except ValueError as exc:
            logger.warning("Dropping invalid UA template header %r: %s", name, exc)
    return out


def dump_headers(headers: Optional[Dict[str, str]]) -> str:
    """Encode a headers dict for the `headers` column."""
    return json.dumps(headers or {}, ensure_ascii=True, sort_keys=True)


def apply_header_overrides(
    base: Dict[str, str], overrides: Dict[str, str]
) -> Dict[str, str]:
    """Merge template headers into `base`, case-insensitively, in place.

    HTTP header names are case-insensitive but a plain dict is not, so a
    template setting `accept-encoding` next to our own `Accept-Encoding`
    would send the header twice with conflicting values. Match on the
    lowercased name and replace the existing entry, keeping the
    template's spelling.

    An empty override value *deletes* the header rather than sending it
    blank — that is the only way to drop one of the base headers, and
    dropping `Accept-Encoding` is a real need (a handful of panels
    mis-handle gzip and return a truncated body).
    """
    lowered = {k.lower(): k for k in base}
    for name, value in overrides.items():
        existing = lowered.get(name.lower())
        if existing is not None:
            base.pop(existing, None)
            lowered.pop(name.lower(), None)
        if value == "":
            continue
        base[name] = value
        lowered[name.lower()] = name
    return base


# ── Lookup + resolution ───────────────────────────────────────────────────────

async def get_template_by_key(
    session: AsyncSession, key: str
) -> Optional[UserAgentTemplate]:
    """Look up a template, treating "can't" the same as "not found".

    The `useragenttemplate` table may genuinely not exist yet: the backend
    bind-mounts `./backend/app` and `./backend/alembic` separately, so a
    hot deploy that drops in new code and reloads the app *before*
    `entrypoint.sh` re-runs `alembic upgrade head` leaves this querying a
    missing table. Letting the `OperationalError` escape would fail the
    refresh and stamp a cryptic `last_error` on the subscription; falling
    through to `BUILTIN_UA_MAP` instead keeps it fetching with exactly the
    UA it used before the deploy. The next container restart migrates and
    the template takes over silently.
    """
    if not key:
        return None
    try:
        return (
            await session.exec(
                select(UserAgentTemplate).where(UserAgentTemplate.key == key)
            )
        ).first()
    except SQLAlchemyError as exc:
        logger.warning(
            "UA template lookup failed (%s) — falling back to the built-in "
            "User-Agent map. If this persists, the 018 migration has not run.",
            type(exc).__name__,
        )
        return None


async def build_subscription_headers(session: AsyncSession, sub) -> Dict[str, str]:
    """Assemble the full outbound header set for one subscription fetch.

    Precedence, highest first:

    1. `Subscription.custom_ua` — a per-subscription paste-the-exact-UA
       escape hatch. Beats the template so an operator can fix one
       misbehaving subscription without cloning a template.
    2. The template's `user_agent`.
    3. `BUILTIN_UA_MAP[sub.ua]`, then `BUILTIN_UA_MAP["v2ray"]` — covers
       an unknown/deleted key and the pre-seed window.

    Then, in order: base headers → dynamic Happ X-* bundle (when the key
    is a happ profile, or the resolved UA looks like Happ) → the
    template's own `headers`. The template goes last deliberately: it is
    the operator's explicit instruction and must be able to override
    anything we picked for them.
    """
    key = (sub.ua or "").strip()
    tpl = await get_template_by_key(session, key)

    custom = (sub.custom_ua or "").strip()
    tpl_ua = (tpl.user_agent or "").strip() if tpl else ""
    ua = custom or tpl_ua or BUILTIN_UA_MAP.get(key) or BUILTIN_UA_MAP["v2ray"]

    headers: Dict[str, str] = {"User-Agent": ua, **BASE_FETCH_HEADERS}

    # Happ-based panels gate on UA + a bundle of X-* headers. Attach
    # them whenever the subscription's template key is a happ profile,
    # or the resolved UA starts with "Happ/" (a pasted custom UA
    # targeting a Happ panel).
    rotate = bool(getattr(sub, "rotate_hwid", False))
    if key in HAPP_PROFILES:
        headers.update(get_happ_headers(key, rotate_hwid=rotate))
    elif ua.lower().startswith("happ/"):
        headers.update(get_happ_headers("happ", rotate_hwid=rotate))

    if tpl:
        apply_header_overrides(headers, parse_headers(tpl.headers))

    return headers


# ── Bootstrap ─────────────────────────────────────────────────────────────────

async def ensure_default_ua_templates(session: AsyncSession) -> int:
    """Seed the built-in templates, but only into an empty table.

    Deliberately *not* an upsert. The whole point of moving the presets
    into the DB is that they are editable and deletable — re-seeding on
    every boot would resurrect a template the operator deleted and
    revert one they edited. Existing installs get the same rows from
    Alembic 018; this covers a DB created by `create_all` (no migration
    history) and is a no-op every time after the first.

    Returns the number of rows inserted.
    """
    existing = (await session.exec(select(UserAgentTemplate).limit(1))).first()
    if existing is not None:
        return 0

    for spec in DEFAULT_UA_TEMPLATES:
        session.add(
            UserAgentTemplate(
                key=spec["key"],
                name=spec["name"],
                user_agent=spec["user_agent"],
                headers=dump_headers(spec.get("headers")),
                description=spec.get("description"),
                builtin=True,
                order=spec.get("order", 100),
            )
        )
    await session.commit()
    logger.info("Seeded %d built-in User-Agent templates", len(DEFAULT_UA_TEMPLATES))
    return len(DEFAULT_UA_TEMPLATES)


def default_template_rows() -> Iterable[dict]:
    """Seed rows shaped for a raw SQL insert (used by Alembic 018)."""
    for spec in DEFAULT_UA_TEMPLATES:
        yield {
            "key": spec["key"],
            "name": spec["name"],
            "user_agent": spec["user_agent"],
            "headers": dump_headers(spec.get("headers")),
            "description": spec.get("description"),
            "builtin": True,
            "order": spec.get("order", 100),
        }
