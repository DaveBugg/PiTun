"""Run PiTun API calls on the assistant's behalf.

Calls go through the real app in-process (ASGI transport), authenticated as
the user who is chatting, so every route keeps its own validation and side
effects (xray reloads, events). Some routes are off limits: the assistant
must not touch auth, its own settings, updates, backups or the box itself.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_PREFIX = "/api"
MUTATING = ("POST", "PATCH", "PUT", "DELETE")

# Never callable by the assistant (regex on the path after /api).
_DENY = [
    r"^/auth(/|$)",
    r"^/assistant(/|$)",
    r"^/mcp(/|$)",
    r"^/system/update/start$",
    r"^/system/(reboot|shutdown|restart-host|factory-reset)",
    r"^/backup/restore",
    r"^/backup/import",
    r"^/scripts(/|$)",          # arbitrary script execution on servers
    r"^/servers/[^/]+/(exec|shell|ssh)",
]
_DENY_RE = [re.compile(p) for p in _DENY]

_STEP_REF = re.compile(r"\{\{step(\d+)\.([A-Za-z0-9_.\[\]]+)\}\}")


def _app():
    from app.main import app
    return app


@lru_cache(maxsize=1)
def _routes() -> List[Tuple[str, re.Pattern, str]]:
    """(method, compiled path regex, template) for every /api operation."""
    out = []
    spec = _app().openapi()
    for path, ops in (spec.get("paths") or {}).items():
        if not path.startswith(API_PREFIX):
            continue
        rx = "^" + re.sub(r"\{[^/}]+\}", r"[^/]+", re.escape(path).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        for method in ops:
            if method.upper() in ("GET",) + MUTATING:
                out.append((method.upper(), re.compile(rx), path))
    return out


def normalize_path(path: str) -> str:
    p = "/" + (path or "").strip().lstrip("/")
    if not p.startswith(API_PREFIX + "/"):
        p = API_PREFIX + p
    return p.split("?", 1)[0]


def denied(path: str) -> bool:
    tail = normalize_path(path)[len(API_PREFIX):]
    return any(r.search(tail) for r in _DENY_RE)


def match_route(method: str, path: str) -> Optional[str]:
    p = normalize_path(path)
    for m, rx, tpl in _routes():
        if m == method.upper() and rx.match(p):
            return tpl
    return None


def validate_step(step: dict, allow_get: bool = False) -> Optional[str]:
    """Problem with a step, or None when it can run."""
    method = str(step.get("method") or "").upper()
    allowed = MUTATING + (("GET",) if allow_get else ())
    if method not in allowed:
        return f"method must be one of {', '.join(allowed)}"
    path = str(step.get("path") or "")
    if not path:
        return "path is required"
    if denied(path):
        return f"{path} is not available to the assistant"
    if "{{step" not in path and not match_route(method, path):
        return f"no API operation {method} {normalize_path(path)}"
    if step.get("body") is not None and not isinstance(step.get("body"), (dict, list)):
        return "body must be a JSON object or list"
    return None


def _dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in re.split(r"\.|\[|\]", dotted):
        if part == "":
            continue
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def fill_refs(obj: Any, results: List[dict]) -> Any:
    """Replace {{stepN.field}} with values from earlier step responses."""
    if isinstance(obj, dict):
        return {k: fill_refs(v, results) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_refs(v, results) for v in obj]
    if isinstance(obj, str) and "{{step" in obj:
        whole = _STEP_REF.fullmatch(obj)
        if whole:
            i = int(whole.group(1)) - 1
            return _dig(results[i]["body"], whole.group(2)) if 0 <= i < len(results) else obj
        def sub(m: re.Match) -> str:
            i = int(m.group(1)) - 1
            v = _dig(results[i]["body"], m.group(2)) if 0 <= i < len(results) else None
            return str(v) if v is not None else m.group(0)
        return _STEP_REF.sub(sub, obj)
    return obj


class Executor:
    def __init__(self, username: str):
        from app.core.auth import create_access_token
        self.token = create_access_token(username, expires_hours=1)

    async def call(self, method: str, path: str, query: Optional[dict] = None,
                   body: Any = None) -> Dict[str, Any]:
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant.internal",
                                     timeout=httpx.Timeout(300.0)) as client:
            r = await client.request(
                method.upper(), normalize_path(path), params=query or None,
                json=body if body is not None else None,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        try:
            data = r.json()
        except (ValueError, json.JSONDecodeError):
            data = r.text[:4000] if r.text else None
        return {"status": r.status_code, "ok": r.status_code < 400, "body": data}

    async def snapshot_before(self, step: dict) -> Any:
        """Current state of what a PATCH/PUT/DELETE step touches, when readable."""
        method = str(step.get("method") or "").upper()
        path = str(step.get("path") or "")
        if method not in ("PATCH", "PUT", "DELETE") or "{{step" in path or not match_route("GET", path):
            return None
        res = await self.call("GET", path)
        return res["body"] if res["ok"] else None

    async def run_plan(self, steps: List[dict]) -> Tuple[bool, List[dict]]:
        results: List[dict] = []
        for step in steps:
            path = fill_refs(step.get("path"), results)
            body = fill_refs(step.get("body"), results)
            query = fill_refs(step.get("query"), results)
            problem = validate_step({**step, "path": path})
            if problem:
                results.append({"status": 0, "ok": False, "body": {"detail": problem}})
                return False, results
            res = await self.call(step["method"], path, query, body)
            res["path"] = normalize_path(path)[len(API_PREFIX):]
            results.append(res)
            if not res["ok"]:
                return False, results
        return True, results


_SERVER_FIELDS = {"id", "created_at", "updated_at", "last_seen", "first_seen"}


def undo_steps(steps: List[dict], before: List[Any], results: List[dict]) -> Tuple[List[dict], List[str]]:
    """Steps that put back what an executed plan changed, newest first, and
    notes on the steps that cannot be reverted.

    PATCH/PUT -> the same call with the old values of the fields it changed.
    POST that created something (answer has an id) -> DELETE it.
    DELETE -> POST the old object again (it comes back with a new id).
    """
    undo: List[dict] = []
    skipped: List[str] = []
    for i, res in enumerate(results):
        if not res.get("ok"):
            continue
        step = steps[i]
        method = str(step.get("method") or "").upper()
        path = res.get("path") or normalize_path(str(step.get("path") or ""))[len(API_PREFIX):]
        old = before[i] if i < len(before) else None
        body = step.get("body")
        label = f"step {i + 1} ({method} {path})"
        if step.get("no_undo"):
            skipped.append(f"{label}: not reversible from here")
            continue
        if method in ("PATCH", "PUT") and isinstance(old, dict) and isinstance(body, dict):
            revert = {k: old[k] for k in body if k in old}
            if revert:
                undo.append({"method": method, "path": path, "body": revert,
                             "note": f"put back the old values of {label}"})
            else:
                skipped.append(f"{label}: the old values are unknown")
        elif method == "POST" and isinstance(res.get("body"), dict) and res["body"].get("id") is not None:
            target = f"{path.rstrip('/')}/{res['body']['id']}"
            if match_route("DELETE", target) and not denied(target):
                undo.append({"method": "DELETE", "path": target, "note": f"delete what {label} created"})
            else:
                skipped.append(f"{label}: what it created cannot be deleted through the API")
        elif method == "DELETE" and isinstance(old, dict):
            parent = path.rstrip("/").rsplit("/", 1)[0]
            if match_route("POST", parent) and not denied(parent):
                undo.append({"method": "POST", "path": parent,
                             "body": {k: v for k, v in old.items() if k not in _SERVER_FIELDS},
                             "note": f"re-create what {label} deleted (it gets a new id)"})
            else:
                skipped.append(f"{label}: what it deleted cannot be re-created through the API")
        else:
            skipped.append(f"{label}: an action, not reversible")
    undo.reverse()
    return undo, skipped
