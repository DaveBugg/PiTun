"""Server credentials pasted into the chat.

The user pastes a short block, one value per line:

    203.0.113.5
    the-password
    root          (optional, default root)
    22            (optional, default 22)

or `key: value` lines (ip / password / user / port / name). The box turns it
into a plan "add the server + test SSH" by itself: the password never reaches
the model, and the chat history keeps only a masked line.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional

_HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
_USER = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.I)
_KEYS = {
    "host": ("ip", "host", "address", "server", "addr", "адрес", "хост", "сервер", "айпи"),
    "password": ("password", "pass", "passwd", "pwd", "пароль", "пасс"),
    "user": ("user", "username", "login", "логин", "пользователь", "юзер", "юзернейм"),
    "port": ("port", "ssh port", "порт"),
    "name": ("name", "имя", "название"),
}
_KV = re.compile(r"^\s*([a-zа-яё ]{2,20}?)\s*[:=]\s*(.+?)\s*$", re.I)
_HEADER = re.compile(r"(server|сервер|vps|впс|добав|add)", re.I)


_NOT_PASSWORDS = {"direct", "proxy", "block", "node", "напрямую", "прокси", "блок", "заблокировать"}


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def _host(v: str) -> Optional[str]:
    v = v.strip().strip("[]").lower()
    try:
        ipaddress.ip_address(v)
        return v
    except ValueError:
        return v if _HOSTNAME.match(v) else None


def _port(v: str) -> Optional[int]:
    v = v.strip()
    if v.isdigit() and 1 <= int(v) <= 65535:
        return int(v)
    return None


def _split_host(line: str) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """'user@host:port' / 'host:port' / 'host' -> (user, host, port)."""
    user = None
    s = line.strip()
    if s.lower().startswith("ssh "):
        s = s[4:].strip()
    if "@" in s:
        user, s = s.split("@", 1)
        if not _USER.match(user):
            return None, None, None
    port = None
    if s.count(":") == 1:
        s, p = s.split(":")
        port = _port(p)
        if port is None:
            return None, None, None
    return user, _host(s), port


def _finish(host, password, user, port, name) -> Optional[dict]:
    if not host or not password:
        return None
    return {"host": host, "password": password, "user": user or "root", "port": port or 22,
            "name": (name or host)[:100]}


def parse(text: str) -> Optional[dict]:
    """Credentials when the whole message is such a block, else None."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) > 2 and _HEADER.search(lines[0]) and _host(lines[0]) is None and ":" not in lines[0].rstrip(":"):
        lines = lines[1:]  # "add a server:" style first line
    if not 2 <= len(lines) <= 5:
        return None

    kv = [_KV.match(ln) for ln in lines]
    if all(kv):
        vals: dict = {}
        for m in kv:
            key = m.group(1).strip().lower()
            field = next((f for f, names in _KEYS.items() if key in names), None)
            if field is None:
                return None
            vals[field] = m.group(2)
        user, host, port = _split_host(vals.get("host", ""))
        if "port" in vals:
            port = _port(vals["port"])
            if port is None:
                return None
        if vals.get("user") and not _USER.match(vals["user"]):
            return None
        return _finish(host, vals.get("password"), vals.get("user") or user, port, vals.get("name"))

    if len(lines) > 4:
        return None
    user, host, port = _split_host(lines[0])
    if host is None:
        return None
    # A bare hostname + one word is more likely "example.com / direct" than
    # credentials: ask for an IP, user@host, host:port or a third line.
    is_ip = _is_ip(host)
    if not is_ip and len(lines) == 2 and user is None and port is None:
        return None
    password = lines[1]
    if password.lower() in _NOT_PASSWORDS:
        return None
    if " " in password or len(password) > 200:
        return None
    for extra in lines[2:]:
        if _port(extra) is not None and port is None:
            port = _port(extra)
        elif _USER.match(extra) and user is None:
            user = extra
        else:
            return None
    return _finish(host, password, user, port, None)


def masked(creds: dict, host_shown: str) -> str:
    return (f"[Server credentials pasted: host {host_shown}, user {creds['user']}, port {creds['port']}, "
            "password hidden]")


async def plan_steps(session, creds: dict) -> tuple[str, str, list]:
    """-> (title, summary, steps): add (or update) the server, then test SSH."""
    from sqlmodel import select
    from app.models import Server
    same = (await session.exec(select(Server).where(Server.host == creds["host"]))).first()
    if same is not None:
        first = {"method": "PATCH", "path": f"/servers/{same.id}",
                 "body": {"port": creds["port"], "user": creds["user"], "auth_type": "password",
                          "password": creds["password"]},
                 "note": f"update the SSH access of the existing server '{same.name}'"}
        test = f"/servers/{same.id}/test"
        title = f"Update server {same.name}"
    else:
        first = {"method": "POST", "path": "/servers",
                 "body": {"name": creds["name"], "host": creds["host"], "port": creds["port"],
                          "user": creds["user"], "auth_type": "password", "password": creds["password"]},
                 "note": "save the server"}
        test = "/servers/{{step1.id}}/test"
        title = f"Add server {creds['name']}"
    steps = [first, {"method": "POST", "path": test, "note": "test the SSH connection", "no_undo": True}]
    summary = (f"SSH {creds['user']}@{creds['host']}:{creds['port']} with the pasted password, then a "
               "connection test. The password is stored like on the Servers page and never sent to the model.")
    return title[:120], summary, steps
