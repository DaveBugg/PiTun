"""Operator error log.

The full exception (type, message, traceback, context) goes to
`<data dir>/errors.log`; callers get back a short generic message that is safe
to store in the DB and show in the UI — raw exception text can carry URLs with
tokens, file paths or upstream response bodies.
"""
from __future__ import annotations

import logging
import logging.handlers
import socket
import ssl
from collections import deque
from pathlib import Path
from typing import Deque, List
from urllib.parse import urlsplit

import httpx

from app.config import settings

_log = logging.getLogger("pitun.errors")
_log.setLevel(logging.INFO)
_log.propagate = False
_handler_ready = False


def log_path() -> Path:
    url = settings.database_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        return Path(url[len("sqlite:///"):]).parent / "errors.log"
    return Path("data") / "errors.log"


def _ensure_handler() -> None:
    global _handler_ready
    if _handler_ready:
        return
    _handler_ready = True
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _log.addHandler(handler)
    except OSError:
        pass  # no writable data dir: the main log still gets the entry below


def redact_url(url: str) -> str:
    """`https://user:pw@host/path?token=x` -> `https://host/…`"""
    try:
        parts = urlsplit(url)
        if not parts.hostname:
            return "<url>"
        return f"{parts.scheme}://{parts.hostname}/…"
    except ValueError:
        return "<url>"


def public_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    if isinstance(exc, ssl.SSLError):
        return "TLS error"
    if isinstance(exc, (socket.gaierror, httpx.ConnectError)):
        return "could not connect"
    if isinstance(exc, httpx.HTTPError):
        return "network error"
    return "unexpected error (see the error log)"


def record(scope: str, exc: BaseException, **context: object) -> str:
    """Write the full detail to the error log and return the safe message."""
    _ensure_handler()
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    _log.error("[%s] %s: %s %s", scope, type(exc).__name__, exc, ctx, exc_info=exc)
    return public_message(exc)


def tail(lines: int = 200) -> List[str]:
    path = log_path()
    if not path.exists():
        return []
    buf: Deque[str] = deque(maxlen=max(1, lines))
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buf.append(line.rstrip("\n"))
    return list(buf)
