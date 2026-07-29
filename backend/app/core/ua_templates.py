"""Header assembly for a subscription fetch, driven by UA templates.

A template is a row in `useragenttemplate`: a `key` (stored in
`Subscription.ua`), a `user_agent` string, and a JSON object of extra
request headers merged over the base set. CRUD lives in
`api/user_agents.py`.

`BUILTIN_UA_MAP` is the fallback for a key with no row — a deleted
template, or a DB built by `create_all` rather than by migration.

Happ's X-* bundle stays in code because `X-Hwid` is derived per request;
a value frozen in the DB would break HWID rotation.
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
# Strict panels cross-validate the UA against `X-Device-Os` / `X-Ver-Os` /
# `X-Device-Model`, so all four must describe the same device or the panel
# serves an "App not supported" placeholder. UA shape they accept:
# `Happ/<app_ver>/<os>/<os_ver>/<model>` — OS segment lowercased, while the
# `X-Device-Os` header keeps its canonical case. Some panels read both.
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
    """Build the X-* bundle Happ sends alongside its UA.

    HWID is derived from `/etc/machine-id` and stable across refreshes:
    panels device-bind on the first one they see, so rotating it silently
    breaks the subscription. The profile is mixed into the seed so
    different OS choices yield different HWIDs.

    `rotate_hwid=True` generates a fresh UUID instead — for a panel that
    has started throttling the stable one.
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


# Fallback for a `ua` key with no matching row.
BUILTIN_UA_MAP: Dict[str, str] = {
    "v2ray": "v2rayN/6.60",
    "clash": "clash.meta/1.18.0",
    "sing-box": "sing-box/1.8.0",
    "streisand": "Streisand/3.0",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    **{k: happ_ua_for(k) for k in HAPP_PROFILES},
}

# Starting point for every fetch; a template's headers layer over this.
BASE_FETCH_HEADERS: Dict[str, str] = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,en,*",
    "Accept-Encoding": "gzip, deflate",
}


# Built-in templates. `headers` is empty throughout: the generic presets
# need nothing extra and the happ-* ones get their X-* bundle at request
# time. The seeding migration keeps its own copy — see the drift test.
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

    Non-ASCII raises `UnicodeEncodeError` inside `httpx.Headers` at send
    time. CR/LF/NUL are forwarded unchanged and smuggle an extra header
    (CWE-93). Both have to be caught here, on save.
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

    An empty value is legal: it removes the header rather than sending it
    blank (see `apply_header_overrides`).
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
    """Decode the `headers` column. A malformed blob degrades to `{}`
    rather than breaking every refresh that uses the template."""
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
    """Lenient counterpart to `validate_headers`, for data on the way out.

    A stored row can hold something the validator would now reject, and
    listing templates must not 500 over one bad entry. Drop offenders,
    keep the rest.
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

    Header names are case-insensitive but a dict is not, so matching on
    the lowercased name is what stops `accept-encoding` and
    `Accept-Encoding` both going out with conflicting values.

    An empty override value deletes the header — the only way to drop a
    base header, which panels that mis-handle gzip need for
    `Accept-Encoding`.
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

    The table may not exist yet: `app/` and `alembic/` are separate bind
    mounts, so new code can run before `entrypoint.sh` re-runs the
    migration. Falling through to `BUILTIN_UA_MAP` keeps the subscription
    fetching instead of stamping a cryptic `last_error` on it.
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
            "User-Agent map. If this persists, migrations have not run.",
            type(exc).__name__,
        )
        return None


async def build_subscription_headers(session: AsyncSession, sub) -> Dict[str, str]:
    """Assemble the outbound header set for one subscription fetch.

    UA precedence: `custom_ua` (per-subscription escape hatch) → the
    template's `user_agent` → `BUILTIN_UA_MAP[key]` → v2ray.

    Then base headers → Happ X-* bundle → the template's own headers.
    The template goes last so it can override anything chosen for it.
    """
    key = (sub.ua or "").strip()
    tpl = await get_template_by_key(session, key)

    custom = (sub.custom_ua or "").strip()
    tpl_ua = (tpl.user_agent or "").strip() if tpl else ""
    ua = custom or tpl_ua or BUILTIN_UA_MAP.get(key) or BUILTIN_UA_MAP["v2ray"]

    headers: Dict[str, str] = {"User-Agent": ua, **BASE_FETCH_HEADERS}

    # Attach the X-* bundle for a happ profile key, or for a pasted
    # custom UA that targets a Happ panel.
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
    """Seed the built-in templates into an EMPTY table only; returns the
    number of rows inserted.

    Not an upsert: re-seeding on boot would resurrect a template the
    operator deleted and revert one they edited. Covers a DB built by
    `create_all`; migrated installs already have the rows.
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
    """Seed rows flattened the way the ORM stores them."""
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
