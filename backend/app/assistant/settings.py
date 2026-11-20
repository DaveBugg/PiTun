"""Assistant settings, stored as `assistant_*` rows in the settings table.

API keys are kept per provider (switching provider does not lose a key),
encrypted with a key derived from the app secret. They are never returned
by the API.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlmodel import select

from app.models import Settings as DBSettings

PROVIDERS = ("openrouter", "openai", "anthropic", "local")
EGRESS = ("active", "direct")
SMALL_MODES = ("auto", "on", "off")

DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "local": "",  # no sensible default: the server is somewhere in the LAN
}

_PREFIX = "assistant_"


@dataclass
class AssistantSettings:
    enabled: bool = False
    provider: str = "openrouter"
    model: str = ""
    base_urls: Dict[str, str] = field(default_factory=dict)
    egress: str = "active"
    monthly_token_limit: int = 0  # 0 = unlimited
    # Small-model mode: macros only, short prompt, short history.
    # auto = on for local models.
    small_mode: str = "auto"
    # Tools web_search / web_fetch (through the same egress as the providers).
    web_search: bool = True
    # Mic button: browser | provider | server | off (see voice.py).
    voice_mode: str = "browser"
    stt_url: str = ""
    stt_model: str = ""
    voice_lang: str = ""  # "" = auto / the UI language
    # /api/mcp for external agents (tokens in the mcptoken table).
    mcp_enabled: bool = False
    # Screenshots are removed from stored chats after this many days.
    screenshot_days: int = 7
    keys: Dict[str, str] = field(default_factory=dict)  # provider -> plain key
    usage: Dict[str, dict] = field(default_factory=dict)  # "YYYY-MM" -> counters

    @property
    def base_url(self) -> str:
        return (self.base_urls.get(self.provider) or DEFAULT_BASE_URLS[self.provider]).rstrip("/")

    @property
    def api_key(self) -> str:
        return self.keys.get(self.provider, "")

    @property
    def small(self) -> bool:
        return self.small_mode == "on" or (self.small_mode == "auto" and self.provider == "local")

    @property
    def web_proxy(self) -> Optional[str]:
        if self.egress != "active":
            return None
        from app.config import settings as app_settings
        return f"socks5://127.0.0.1:{app_settings.socks_port}"

    def month_usage(self) -> dict:
        return self.usage.get(_month(), {"input": 0, "output": 0, "cost": 0.0, "requests": 0})

    def over_limit(self) -> bool:
        if self.monthly_token_limit <= 0:
            return False
        u = self.month_usage()
        return u["input"] + u["output"] >= self.monthly_token_limit


def _month() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


def _fernet():
    from cryptography.fernet import Fernet
    from app.config import settings
    key = hashlib.sha256(("pitun-assistant:" + settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    """Empty string when the app secret changed since the key was saved."""
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


async def _rows(session) -> Dict[str, DBSettings]:
    rows = (await session.exec(select(DBSettings).where(DBSettings.key.startswith(_PREFIX)))).all()
    return {r.key[len(_PREFIX):]: r for r in rows}


def _json(raw: Optional[str], default):
    try:
        v = json.loads(raw) if raw else default
        return v if isinstance(v, type(default)) else default
    except ValueError:
        return default


async def load(session) -> AssistantSettings:
    rows = await _rows(session)
    get = lambda k, d="": rows[k].value if k in rows else d  # noqa: E731
    s = AssistantSettings(
        enabled=get("enabled", "false").lower() == "true",
        provider=get("provider", "openrouter"),
        model=get("model", ""),
        base_urls=_json(get("base_urls"), {}),
        egress=get("egress", "active"),
        small_mode=get("small_mode", "auto"),
        web_search=get("web_search", "true").lower() == "true",
        voice_mode=get("voice_mode", "browser"),
        stt_url=get("stt_url", ""),
        stt_model=get("stt_model", ""),
        voice_lang=get("voice_lang", ""),
        mcp_enabled=get("mcp_enabled", "false").lower() == "true",
        usage=_json(get("usage"), {}),
    )
    try:
        s.monthly_token_limit = max(0, int(get("monthly_token_limit", "0") or 0))
    except ValueError:
        s.monthly_token_limit = 0
    try:
        s.screenshot_days = min(365, max(1, int(get("screenshot_days", "7") or 7)))
    except ValueError:
        s.screenshot_days = 7
    if s.provider not in PROVIDERS:
        s.provider = "openrouter"
    if s.egress not in EGRESS:
        s.egress = "active"
    if s.small_mode not in SMALL_MODES:
        s.small_mode = "auto"
    s.keys = {p: decrypt(v) for p, v in _json(get("keys"), {}).items() if isinstance(v, str)}
    return s


async def _put(session, key: str, value: str) -> None:
    rows = await _rows(session)
    row = rows.get(key) or DBSettings(key=_PREFIX + key, value="")
    row.value = value
    session.add(row)


async def save(session, s: AssistantSettings) -> None:
    await _put(session, "enabled", "true" if s.enabled else "false")
    await _put(session, "provider", s.provider)
    await _put(session, "model", s.model)
    await _put(session, "base_urls", json.dumps(s.base_urls))
    await _put(session, "egress", s.egress)
    await _put(session, "small_mode", s.small_mode)
    await _put(session, "web_search", "true" if s.web_search else "false")
    await _put(session, "voice_mode", s.voice_mode)
    await _put(session, "stt_url", s.stt_url)
    await _put(session, "stt_model", s.stt_model)
    await _put(session, "voice_lang", s.voice_lang)
    await _put(session, "mcp_enabled", "true" if s.mcp_enabled else "false")
    await _put(session, "screenshot_days", str(s.screenshot_days))
    await _put(session, "monthly_token_limit", str(s.monthly_token_limit))
    await _put(session, "keys", json.dumps({p: encrypt(k) for p, k in s.keys.items() if k}))
    await session.commit()


async def add_usage(session, input_tokens: int, output_tokens: int, cost: float = 0.0) -> None:
    rows = await _rows(session)
    usage = _json(rows["usage"].value if "usage" in rows else None, {})
    m = usage.setdefault(_month(), {"input": 0, "output": 0, "cost": 0.0, "requests": 0})
    m["input"] += int(input_tokens or 0)
    m["output"] += int(output_tokens or 0)
    m["cost"] = round(float(m.get("cost", 0.0)) + float(cost or 0.0), 6)
    m["requests"] += 1
    # keep the last 12 months
    for k in sorted(usage)[:-12]:
        usage.pop(k, None)
    await _put(session, "usage", json.dumps(usage))
    await session.commit()
