"""Host network mutations: change gateway / DNS, backup, rollback.

Sister to ``network_config.py`` (which reads). This one writes.

Scope decision (recorded for the next maintainer)
-------------------------------------------------
v1.3.3 ships ONLY gateway + DNS changes. IP/CIDR and interface
selection are intentionally out of scope:

  * Changing the gateway does not interrupt LAN-side TCP sessions —
    SSH from a LAN client to PiTun stays alive even with a broken
    gateway, because TCP between LAN devices doesn't traverse it.
    Worst case after a bad apply: this box loses outbound internet,
    operator SSHs in and either calls /api/network/rollback or runs
    ``ip route replace default via <good-ip>`` by hand.
  * Changing the IP, on the other hand, IS disruptive — every active
    TCP session dies, the operator has to discover the new IP. That
    needs a different safety mechanism (auto-rollback timer with
    heartbeats from the UI), which is a bigger feature. Postponed
    until/unless PiTun grows into a full router replacement.

Apply strategy per manager
--------------------------
ifupdown:
  * Find the iface block for the default-route interface in
    /etc/network/interfaces and /etc/network/interfaces.d/*.
  * If currently ``inet dhcp``, convert to ``inet static`` with the
    SAME ip/cidr (read from live state) plus the requested gateway
    and DNS. Required because DHCP lease renewal would otherwise
    clobber our hand-set gateway on the next renew.
  * If already ``inet static``, edit the gateway / dns-nameservers
    lines in place.
  * Apply at runtime with ``ip route replace default via <gw>`` and
    overwrite /etc/resolv.conf. No ifdown/ifup — those drop the link
    and could kill SSH.

NetworkManager:
  * ``nmcli con mod <name> ipv4.gateway <gw> ipv4.dns "<...>" \
     ipv4.ignore-auto-dns yes ipv4.ignore-auto-routes yes``
  * ``nmcli con up <name>``
  * NM keeps the connection alive across the apply, so SSH survives.

Backups
-------
Each apply snapshots the affected files BEFORE mutating them into
``/var/lib/pitun/network-backups/<utc-iso>.json``. The blob holds:

  {
    "id": "<utc-iso>",
    "created_at": "<utc-iso>",
    "manager": "ifupdown" | "networkmanager",
    "interface": "enp1s0",
    "live_state": { ip, cidr, gateway, dns },
    "files": [ { "path": "/etc/network/interfaces", "content": "..." }, ... ]
  }

The 'live_state' field is what runtime restore reads from — file
restore alone wouldn't bring back the old gateway if DHCP isn't
running (e.g. we'd switched DHCP→static).

Last 10 backups are kept; older ones pruned on every new apply.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Optional

from app.core import network_config as nc

logger = logging.getLogger(__name__)

# Backup root lives under /var/lib/pitun on the HOST (via nsenter) since
# the container's /var/lib isn't persisted across image upgrades. For
# read-back convenience we also use nsenter — no bind-mount needed.
HOST_BACKUP_DIR = "/var/lib/pitun/network-backups"

MAX_BACKUPS = 10


# ── Errors ────────────────────────────────────────────────────────────────

class NetworkApplyError(Exception):
    """Raised on any failure during apply / rollback. Carries a
    human-readable message that the API layer turns into a 400."""


# ── Validation ────────────────────────────────────────────────────────────

def _validate_ipv4(ip: str, field: str) -> str:
    """Strict IPv4 parse — raises with a useful field name on bad input.

    Why so picky: an empty string slipping through here would later be
    interpolated into shell + config files, producing impossible-to-
    debug "default via " entries. Better to reject loudly."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as e:
        raise NetworkApplyError(f"{field}: not a valid IP address: {ip!r}") from e
    if not isinstance(addr, ipaddress.IPv4Address):
        raise NetworkApplyError(f"{field}: IPv6 is not supported yet ({ip!r})")
    return str(addr)


def _validate_dns_list(dns: List[str]) -> List[str]:
    """Each DNS server must be an IPv4 address. Empty list is OK
    (means 'don't touch DNS')."""
    out = []
    for i, d in enumerate(dns):
        out.append(_validate_ipv4(d, f"dns[{i}]"))
    return out


# ── Backup ────────────────────────────────────────────────────────────────

@dataclass
class Backup:
    id: str
    created_at: str
    manager: str
    interface: str
    live_state: dict
    files: List[dict]   # [ {path, content}, ... ]

    def to_dict(self) -> dict:
        return asdict(self)


def _backup_id() -> str:
    """ISO timestamp without colons (filesystem-safe). Also doubles as
    a sort key — lexical sort = chronological sort."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _mkdir_host(path: str) -> None:
    r = nc.host_run(["mkdir", "-p", path], timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"Could not create {path}: {r.stderr.strip()}")


def _list_backup_filenames() -> List[str]:
    r = nc.host_run(["sh", "-c", f"ls {HOST_BACKUP_DIR}/*.json 2>/dev/null || true"], timeout=5)
    if r.returncode != 0:
        return []
    return [p.strip() for p in r.stdout.split() if p.strip()]


def _prune_backups() -> None:
    """Keep the most recent ``MAX_BACKUPS`` only. Sort by filename
    (== ISO timestamp prefix). Pruning failures are non-fatal —
    operator can clean up manually."""
    files = sorted(_list_backup_filenames())
    extras = files[:-MAX_BACKUPS]
    for f in extras:
        nc.host_run(["rm", "-f", f], timeout=3)
        logger.info("Pruned old network backup: %s", os.path.basename(f))


def _capture_files(manager: str) -> List[dict]:
    """Snapshot every file we might mutate, regardless of whether we
    actually end up touching it. Storage cost is trivial (~few KB) and
    rollback is simpler with a complete picture."""
    paths: List[str] = []
    if manager == "ifupdown":
        paths.append("/etc/network/interfaces")
        ls = nc.host_run(
            ["sh", "-c", "ls /etc/network/interfaces.d/ 2>/dev/null"],
            timeout=3,
        )
        if ls.returncode == 0:
            for name in ls.stdout.split():
                if name:
                    paths.append(f"/etc/network/interfaces.d/{name}")
        paths.append("/etc/resolv.conf")
    elif manager == "networkmanager":
        # NM keyfile format under system-connections — capture all so
        # rollback works even when the user has multiple connection
        # profiles, only one of which is touched.
        ls = nc.host_run(
            ["sh", "-c", "ls /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null"],
            timeout=3,
        )
        if ls.returncode == 0:
            for path in ls.stdout.split():
                if path.strip():
                    paths.append(path.strip())
        paths.append("/etc/resolv.conf")
    else:
        # Sub-task 6 territory — we don't apply for these managers yet.
        raise NetworkApplyError(
            f"Manager {manager!r} is not supported for apply in this PiTun version. "
            "Edit network config files manually."
        )

    files = []
    for p in paths:
        content = nc.host_read_file(p)
        if content is not None:
            files.append({"path": p, "content": content})
    return files


def create_backup(state: nc.NetworkState) -> Backup:
    """Snapshot current network config + live state.

    Called immediately before apply. Returns the persisted Backup so
    the caller can reference it (and the API can return its id)."""
    _mkdir_host(HOST_BACKUP_DIR)

    files = _capture_files(state.manager)
    backup = Backup(
        id=_backup_id(),
        created_at=datetime.now(timezone.utc).isoformat(),
        manager=state.manager,
        interface=state.interface,
        live_state={
            "ip": state.ip,
            "cidr": state.cidr,
            "gateway": state.gateway,
            "dns": list(state.dns),
            "mode": state.mode,
        },
        files=files,
    )

    target = f"{HOST_BACKUP_DIR}/{backup.id}.json"
    payload = json.dumps(backup.to_dict(), indent=2)
    # `tee` instead of sh redirect — atomicity is best-effort here
    # since we don't need crash-safety for a backup file.
    r = nc.host_run(["tee", target], input_data=payload, timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"Could not write backup {target}: {r.stderr.strip()}")
    logger.info("Network backup created: id=%s, %d files captured", backup.id, len(files))

    _prune_backups()
    return backup


def list_backups() -> List[dict]:
    """Return all stored backups, newest first. The full ``files``
    blob is omitted — only metadata, so the API can render a one-line
    summary per backup without dumping kilobytes."""
    out: List[dict] = []
    for path in sorted(_list_backup_filenames(), reverse=True):
        content = nc.host_read_file(path)
        if content is None:
            continue
        try:
            data = json.loads(content)
        except ValueError:
            continue
        out.append({
            "id": data.get("id"),
            "created_at": data.get("created_at"),
            "manager": data.get("manager"),
            "interface": data.get("interface"),
            "live_state": data.get("live_state"),
        })
    return out


def _load_backup(backup_id: str) -> Backup:
    """Load a backup by id. Caller-friendly errors — backup ids are
    user-facing so we don't want a stack trace if they typo one."""
    if not re.fullmatch(r"[0-9TZ]+", backup_id):
        raise NetworkApplyError(f"Invalid backup id: {backup_id!r}")
    path = f"{HOST_BACKUP_DIR}/{backup_id}.json"
    content = nc.host_read_file(path)
    if content is None:
        raise NetworkApplyError(f"Backup not found: {backup_id}")
    try:
        data = json.loads(content)
    except ValueError as e:
        raise NetworkApplyError(f"Backup {backup_id} is corrupt: {e}")
    return Backup(
        id=data["id"],
        created_at=data["created_at"],
        manager=data["manager"],
        interface=data["interface"],
        live_state=data["live_state"],
        files=data["files"],
    )


def delete_backup(backup_id: str) -> None:
    """Remove a single backup by id.

    Strict id validation (same regex as _load_backup) means ``rm`` can
    never get a path-traversal argument like ``../../etc/passwd``.
    Idempotent — already-gone files return success rather than 404 so
    a double-click in the UI doesn't surface as an error.
    """
    if not re.fullmatch(r"[0-9TZ]+", backup_id):
        raise NetworkApplyError(f"Invalid backup id: {backup_id!r}")
    path = f"{HOST_BACKUP_DIR}/{backup_id}.json"
    r = nc.host_run(["rm", "-f", path], timeout=3)
    if r.returncode != 0:
        raise NetworkApplyError(f"Could not delete {path}: {r.stderr.strip()}")
    # Belt-and-braces log sanitisation for CWE-117:
    # 1. The regex above already restricts `backup_id` to `[0-9TZ]+` —
    #    no newlines or control chars possible at runtime.
    # 2. Explicit `.replace('\n','')`/`.replace('\r','')` is the
    #    pattern CodeQL's `py/log-injection` query recognises as a
    #    sanitiser (the regex-narrows-charset argument doesn't get
    #    picked up by its taint tracker).
    # 3. `%r` (repr) is logged anyway as a second layer.
    safe_id = backup_id.replace("\n", "").replace("\r", "")
    logger.info("Network backup deleted: id=%r", safe_id)


def delete_all_backups() -> int:
    """Wipe every backup. Returns the count deleted (best-effort —
    counts what we matched before the rm, since the rm itself is
    atomic per-file).

    Operator-facing "Clear all" button uses this. Confirmation is
    the UI's job, not ours.
    """
    files = _list_backup_filenames()
    if not files:
        return 0
    # Pass all paths in one rm call — bash quoting handled by host_run
    # via shell. We've already validated the dir prefix is constant,
    # so building "rm -f path1 path2 ..." is safe.
    r = nc.host_run(["sh", "-c", f"rm -f {HOST_BACKUP_DIR}/*.json"], timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"Could not clear backups: {r.stderr.strip()}")
    logger.info("Network backups cleared: %d removed", len(files))
    return len(files)


# ── ifupdown apply ────────────────────────────────────────────────────────

def _find_iface_block(content: str, ifname: str) -> Optional[tuple]:
    """Locate the `iface <ifname> inet ...` block in interfaces(5) text.

    Returns (start_line_idx, end_line_idx, method) where end_line_idx
    is exclusive. The block ends at the next top-level directive
    (`iface`, `auto`, `allow-hotplug`, `source`, `mapping`) or EOF.

    Returns None if no such block exists."""
    lines = content.splitlines()
    head_re = re.compile(rf"^\s*iface\s+{re.escape(ifname)}\s+inet\s+(\w+)")
    block_terminators = ("iface", "auto", "allow-", "source", "mapping")

    for i, line in enumerate(lines):
        m = head_re.match(line)
        if not m:
            continue
        method = m.group(1)
        # Find end
        end = len(lines)
        for j in range(i + 1, len(lines)):
            s = lines[j].lstrip()
            if not s:
                continue
            first = s.split(maxsplit=1)[0] if s else ""
            if any(first.startswith(t) for t in block_terminators):
                end = j
                break
        return (i, end, method)
    return None


def _rewrite_ifupdown_block(
    *,
    content: str,
    ifname: str,
    new_ip: str,
    new_cidr: int,
    new_gateway: str,
    new_dns: List[str],
) -> str:
    """Rewrite the iface block (or append a new one) to set static
    config with the requested gateway/DNS, preserving the current IP.

    The address is written as ``A.B.C.D/N`` (the modern ifupdown
    syntax that doesn't require a separate `netmask` line). dns-
    nameservers needs the `resolvconf` package to actually populate
    /etc/resolv.conf — we ALSO write /etc/resolv.conf directly in
    the apply step so it works on systems without resolvconf.
    """
    block_text = (
        f"# Managed by PiTun (Network UI) — last modified {datetime.now(timezone.utc).isoformat()}\n"
        f"iface {ifname} inet static\n"
        f"    address {new_ip}/{new_cidr}\n"
        f"    gateway {new_gateway}\n"
    )
    if new_dns:
        block_text += f"    dns-nameservers {' '.join(new_dns)}\n"

    found = _find_iface_block(content, ifname)
    if found:
        start, end, _method = found
        lines = content.splitlines()
        # Preserve the `auto`/`allow-hotplug` line that usually sits
        # one line above — don't touch it. Insert new block where the
        # old one was.
        new_lines = lines[:start] + block_text.rstrip().split("\n") + lines[end:]
        return "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")

    # No existing block — append at end with an `auto` directive too,
    # otherwise ifupdown won't bring the interface up at boot.
    suffix = "" if content.endswith("\n") else "\n"
    return content + suffix + f"\nauto {ifname}\n" + block_text


def _apply_ifupdown(
    *, state: nc.NetworkState, new_gateway: Optional[str], new_dns: Optional[List[str]],
) -> None:
    """Apply via ifupdown: edit /etc/network/interfaces + runtime
    apply via `ip route` / /etc/resolv.conf."""
    if not state.ip or state.cidr is None:
        raise NetworkApplyError(
            "Cannot apply: current IP/CIDR not detected on the interface. "
            "Run with a working network configuration first.",
        )

    final_gateway = new_gateway if new_gateway is not None else state.gateway
    final_dns = list(new_dns) if new_dns is not None else list(state.dns)

    if not final_gateway:
        raise NetworkApplyError("Gateway must be set in either the request or current state.")

    # Rewrite /etc/network/interfaces (or interfaces.d/ entry if found there)
    content = nc.host_read_file("/etc/network/interfaces") or ""
    write_path = "/etc/network/interfaces"
    block = _find_iface_block(content, state.interface)
    if not block:
        # The iface block might live in interfaces.d/ instead. Search
        # those files and rewrite there if found.
        ls = nc.host_run(["sh", "-c", "ls /etc/network/interfaces.d/ 2>/dev/null"], timeout=3)
        if ls.returncode == 0:
            for name in ls.stdout.split():
                if not name:
                    continue
                p = f"/etc/network/interfaces.d/{name}"
                c = nc.host_read_file(p)
                if c is not None and _find_iface_block(c, state.interface):
                    content = c
                    write_path = p
                    break

    new_content = _rewrite_ifupdown_block(
        content=content,
        ifname=state.interface,
        new_ip=state.ip,
        new_cidr=state.cidr,
        new_gateway=final_gateway,
        new_dns=final_dns,
    )
    r = nc.host_run(["tee", write_path], input_data=new_content, timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"Could not write {write_path}: {r.stderr.strip()}")
    logger.info("ifupdown: rewrote %s for iface=%r", write_path, state.interface)

    # Runtime apply: replace default route + DNS without bringing the
    # interface down (would kill SSH).
    r = nc.host_run(["ip", "route", "replace", "default", "via", final_gateway, "dev", state.interface], timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"ip route replace failed: {r.stderr.strip()}")
    logger.info("ifupdown: default route -> %r dev %r", final_gateway, state.interface)

    if final_dns:
        resolv = "# Managed by PiTun (Network UI)\n"
        for d in final_dns:
            resolv += f"nameserver {d}\n"
        r = nc.host_run(["tee", "/etc/resolv.conf"], input_data=resolv, timeout=5)
        if r.returncode != 0:
            raise NetworkApplyError(f"Could not write /etc/resolv.conf: {r.stderr.strip()}")
        # Also write /etc/resolv.conf.head — dhcpcd's resolvconf hook
        # prepends this file on every regen. Without it, the next time
        # dhcpcd refreshes (lease renewal, daemon restart, reboot on
        # systems where dhcpcd-base ships even when ifupdown owns the
        # interface), the resolv.conf we just wrote gets clobbered to
        # an empty header and the host loses DNS. The failure chain is:
        # static IP applied → resolv.conf populated → reboot → dhcpcd
        # regenerates it empty → every endpoint touching getaddrinfo
        # wedges.
        nc.host_run(["tee", "/etc/resolv.conf.head"], input_data=resolv, timeout=5)
        logger.info("ifupdown: resolv.conf + .head -> %r", final_dns)


# ── NetworkManager apply ──────────────────────────────────────────────────

def _nm_connection_for_iface(ifname: str) -> Optional[str]:
    r = nc.host_run(
        ["nmcli", "-t", "-f", "NAME,DEVICE,STATE", "connection", "show", "--active"],
        timeout=5,
    )
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == ifname and parts[2] == "activated":
            return parts[0]
    return None


def _apply_networkmanager(
    *, state: nc.NetworkState, new_gateway: Optional[str], new_dns: Optional[List[str]],
) -> None:
    conn = _nm_connection_for_iface(state.interface)
    if not conn:
        raise NetworkApplyError(
            f"No active NetworkManager connection found for {state.interface}.",
        )

    final_gateway = new_gateway if new_gateway is not None else state.gateway
    final_dns = list(new_dns) if new_dns is not None else list(state.dns)
    if not final_gateway:
        raise NetworkApplyError("Gateway must be set in either the request or current state.")

    # We need the IP/CIDR on the connection if we're keeping DHCP off.
    # Easiest: switch to manual with the current address. nmcli requires
    # ipv4.addresses when ipv4.method=manual.
    if not state.ip or state.cidr is None:
        raise NetworkApplyError("Cannot apply: current IP/CIDR not detected.")

    cmd = [
        "nmcli", "connection", "modify", conn,
        "ipv4.method", "manual",
        "ipv4.addresses", f"{state.ip}/{state.cidr}",
        "ipv4.gateway", final_gateway,
        "ipv4.ignore-auto-dns", "yes",
        "ipv4.ignore-auto-routes", "yes",
    ]
    if final_dns:
        cmd += ["ipv4.dns", " ".join(final_dns)]
    else:
        cmd += ["ipv4.dns", ""]

    r = nc.host_run(cmd, timeout=15)
    if r.returncode != 0:
        raise NetworkApplyError(f"nmcli modify failed: {r.stderr.strip() or r.stdout.strip()}")

    # `nmcli con up` re-applies the connection live. It does cycle the
    # connection but NM keeps the L2 link up — typically SSH survives
    # for the same IP.
    r = nc.host_run(["nmcli", "connection", "up", conn], timeout=15)
    if r.returncode != 0:
        raise NetworkApplyError(f"nmcli up failed: {r.stderr.strip() or r.stdout.strip()}")
    logger.info("NetworkManager: applied gateway=%r dns=%r on %r", final_gateway, final_dns, conn)


# ── Public apply / rollback ──────────────────────────────────────────────

@dataclass
class ApplyRequest:
    gateway: Optional[str] = None
    dns: Optional[List[str]] = None


def apply(req: ApplyRequest) -> Backup:
    """Validate, snapshot, then mutate. Returns the Backup the caller
    can quote back in the UI as 'rollback target'."""
    # Validate inputs FIRST — empty apply (no gateway, no dns) is a
    # no-op and surfaces as a 400 instead of silently doing nothing.
    if req.gateway is None and req.dns is None:
        raise NetworkApplyError("Empty apply — provide gateway and/or dns.")

    gw = _validate_ipv4(req.gateway, "gateway") if req.gateway is not None else None
    dns = _validate_dns_list(req.dns) if req.dns is not None else None

    state = nc.read_state()

    # Never let the panel (re)introduce a routing self-loop: a default
    # route via the box's own IP hands every off-LAN packet back to us.
    # This is the exact footgun the Network page exists to fix, so
    # refusing it here — with a clear message — is the whole point.
    if gw is not None and state.ip and gw == state.ip:
        raise NetworkApplyError(
            f"Gateway {gw} is this host's own IP — that's a routing "
            "self-loop, not a gateway. Use your ISP router's address "
            "(usually 192.168.x.1)."
        )

    if state.manager not in ("ifupdown", "networkmanager"):
        raise NetworkApplyError(
            f"Apply not supported on manager {state.manager!r} yet. "
            "Edit network config manually."
        )

    # Backup BEFORE mutation. If create_backup fails, we abort —
    # better to leave things untouched than to mutate with no rollback.
    backup = create_backup(state)

    try:
        if state.manager == "ifupdown":
            _apply_ifupdown(state=state, new_gateway=gw, new_dns=dns)
        else:
            _apply_networkmanager(state=state, new_gateway=gw, new_dns=dns)
    except Exception as e:
        logger.error("Apply failed mid-mutation: %s — backup %s available", e, backup.id)
        raise

    return backup


def rollback(backup_id: Optional[str] = None) -> Backup:
    """Restore from a backup. If id omitted, use the most recent.

    Restores BOTH the config files (so the change survives reboot)
    AND the runtime state via ``ip route replace`` + /etc/resolv.conf
    rewrite (so the rollback takes effect immediately)."""
    backups = sorted(_list_backup_filenames(), reverse=True)
    if not backups:
        raise NetworkApplyError("No backups available to roll back to.")

    if backup_id is None:
        # Take the newest
        backup_id = os.path.basename(backups[0]).rsplit(".", 1)[0]

    backup = _load_backup(backup_id)

    # Restore files
    for entry in backup.files:
        path = entry["path"]
        content = entry["content"]
        r = nc.host_run(["tee", path], input_data=content, timeout=5)
        if r.returncode != 0:
            raise NetworkApplyError(f"Could not restore {path}: {r.stderr.strip()}")
        logger.info("Restored file: %s (from backup %s)", path, backup.id)

    # Restore runtime: re-apply old gateway + DNS
    old_gw = backup.live_state.get("gateway")
    old_dns = backup.live_state.get("dns") or []
    iface = backup.interface

    if old_gw and iface:
        r = nc.host_run(["ip", "route", "replace", "default", "via", old_gw, "dev", iface], timeout=5)
        if r.returncode != 0:
            logger.warning(
                "rollback: ip route replace failed (%s) — files restored "
                "but runtime may need a reboot to take full effect",
                r.stderr.strip(),
            )
        else:
            logger.info("rollback: runtime default route -> %r dev %r", old_gw, iface)

    if old_dns:
        resolv = "".join(f"nameserver {d}\n" for d in old_dns)
        nc.host_run(["tee", "/etc/resolv.conf"], input_data=resolv, timeout=5)
        # Also restore the dhcpcd `.head` pin we wrote on apply (or
        # delete it when the pre-change state had no DNS pin). Mirror
        # of the apply step in `_apply_ifupdown`.
        nc.host_run(["tee", "/etc/resolv.conf.head"], input_data=resolv, timeout=5)
    else:
        # No DNS in the pre-change state → also strip any .head we
        # wrote on the apply, so the system goes back to "let dhcpcd
        # do whatever it wants" behaviour.
        nc.host_run(["rm", "-f", "/etc/resolv.conf.head"], timeout=3)

    return backup


# ── Pre-flight probe ──────────────────────────────────────────────────────

def probe_gateway(ip: str) -> dict:
    """Quick reachability check on a candidate gateway. Returns a dict
    with `reachable` (bool) and a `detail` (string explaining how the
    answer was determined). Frontend uses this BEFORE apply to warn
    the operator if the candidate looks dead.

    Uses ARP-level reachability (`ip neigh` after `arping`) rather
    than ICMP — some ISP routers block ping but answer ARP. ARP is
    also LAN-only so it can't be fooled by a public-IP-that-pings."""
    _validate_ipv4(ip, "ip")

    # First: is the IP even in the same subnet as our interface?
    state = nc.read_state()
    if not state.ip or state.cidr is None:
        return {
            "reachable": False,
            "detail": "Cannot validate — current host IP/CIDR not detected.",
        }

    # The box's own address is never a valid gateway — probing it would
    # "succeed" (we always answer ourselves) and mask the self-loop the
    # operator is trying to escape. Reject it explicitly instead.
    if ip == state.ip:
        return {
            "reachable": False,
            "detail": (
                f"{ip} is THIS PiTun's own address, not a gateway. A gateway "
                "must be a different device — your ISP router (usually "
                "192.168.x.1)."
            ),
        }
    try:
        candidate_net = ipaddress.ip_network(f"{state.ip}/{state.cidr}", strict=False)
        if ipaddress.ip_address(ip) not in candidate_net:
            return {
                "reachable": False,
                "detail": (
                    f"{ip} is not in this host's subnet "
                    f"{candidate_net}. A gateway must be on the same LAN."
                ),
            }
    except ValueError:
        pass

    # ping(8) lives on the host but isn't in our slimmed-down backend
    # image — go through nsenter so we use the host's binary. Same
    # reasoning for arping below. This also exercises the proper
    # interface (the container's own routing is just host-shared so
    # source-IP picking would still work, but staying on the host
    # binary keeps any future PiTun container changes (e.g. extra
    # routes inside the netns) from breaking probe semantics.
    r = nc.host_run(["ping", "-c", "1", "-W", "2", ip], timeout=5)
    if r.returncode == 0:
        return {"reachable": True, "detail": f"{ip} responds to ICMP ping."}

    # ICMP failed — fall back to ARP. arping comes from iputils-arping
    # and isn't on every Debian server install. If absent, return
    # inconclusive rather than pretending the gateway is dead.
    has_arping = nc.host_run(["sh", "-c", "command -v arping"], timeout=3).returncode == 0
    if has_arping:
        r2 = nc.host_run(
            ["arping", "-c", "1", "-w", "2", "-I", state.interface, ip],
            timeout=5,
        )
        if r2.returncode == 0:
            return {"reachable": True, "detail": f"{ip} answers ARP (ping blocked but reachable)."}

    return {
        "reachable": False,
        "detail": f"{ip} does not respond to ICMP" + (" or ARP" if has_arping else "")
                  + ". Either the host is offline or you're behind a firewall.",
    }


# ── Host fallback DNS (additive, non-destructive) ───────────────────────────
#
# Distinct from `apply()` above, which rewrites the whole interface to a
# static config. This adds FALLBACK DNS servers to the host's OWN resolver
# WITHOUT touching the DHCP-provided ones or switching off DHCP.
#
# Why this exists: the box itself (backend container — subscriptions, geo
# downloads, x-ui panel reachability, healthchecks) resolves names through
# the host /etc/resolv.conf, which normally points only at the LAN router.
# If the router's DNS flakes, ALL of that breaks even though LAN clients
# keep resolving fine through xray's own DNS engine. Adding public
# fallbacks (1.1.1.1 / 8.8.8.8) after the router means a router DNS outage
# no longer blinds the box. Managed from the DNS page so it's one place.

_RESOLVED_DROPIN_DIR = "/etc/systemd/resolved.conf.d"
_RESOLVED_DROPIN = "/etc/systemd/resolved.conf.d/pitun-fallback.conf"
_RESOLV_MARK = "# pitun-fallback-dns"


def _resolved_active() -> bool:
    return nc.host_systemctl_is_active("systemd-resolved")


def _fallback_via_resolved(servers: List[str]) -> dict:
    """systemd-resolved: FallbackDNS= is exactly built for this — used
    only when the primary (DHCP/link) DNS returns nothing. Cleanest,
    fully persistent, non-destructive."""
    body = "[Resolve]\nFallbackDNS=" + " ".join(servers) + "\n"
    # Idempotent: skip the restart if the drop-in already matches.
    existing = nc.host_read_file(_RESOLVED_DROPIN)
    if existing is not None and existing.strip() == body.strip():
        return {"applied": False, "manager": "systemd-resolved",
                "detail": "fallback DNS already configured"}
    nc.host_run(["mkdir", "-p", _RESOLVED_DROPIN_DIR], timeout=5)
    r = nc.host_run(["tee", _RESOLVED_DROPIN], input_data=body, timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"failed to write {_RESOLVED_DROPIN}: {r.stderr.strip()}")
    nc.host_run(["systemctl", "restart", "systemd-resolved"], timeout=10)
    return {"applied": True, "manager": "systemd-resolved",
            "detail": f"FallbackDNS set to {' '.join(servers)}"}


def _fallback_via_nm(servers: List[str]) -> dict:
    """NetworkManager: append servers to ipv4.dns on the active default-
    route connection. With ipv4.method=auto these MERGE with DHCP DNS
    (router stays first, our fallbacks follow). Persistent in the
    connection profile. Idempotent — only adds servers not already
    present, and only reactivates if something changed."""
    iface, _ = nc.read_default_route()
    if not iface:
        raise NetworkApplyError("no default-route interface to attach fallback DNS to")
    conn = _nm_connection_for_iface(iface)
    if not conn:
        raise NetworkApplyError(f"no active NetworkManager connection for {iface}")

    cur = nc.host_run(["nmcli", "-g", "ipv4.dns", "connection", "show", conn], timeout=5)
    current = set()
    if cur.returncode == 0:
        # nmcli -g returns comma-separated, sometimes with trailing escape
        for tok in cur.stdout.replace("\\", "").replace(",", " ").split():
            tok = tok.strip()
            if tok:
                current.add(tok)
    to_add = [s for s in servers if s not in current]

    # Always (re)assert fast-fallback options so a dead first server
    # doesn't stall the whole resolver for 5s — even when DNS list is
    # unchanged we want these present.
    mod = ["nmcli", "connection", "modify", conn,
           "ipv4.dns-options", "timeout:1,attempts:2"]
    if to_add:
        mod += ["+ipv4.dns", ",".join(to_add)]
    r = nc.host_run(mod, timeout=15)
    if r.returncode != 0:
        raise NetworkApplyError(f"nmcli modify failed: {r.stderr.strip() or r.stdout.strip()}")

    if not to_add:
        # Options may have changed; reapply is cheap and keeps SSH up.
        nc.host_run(["nmcli", "connection", "up", conn], timeout=15)
        return {"applied": False, "manager": "networkmanager",
                "detail": "fallback DNS already present (refreshed options)"}

    up = nc.host_run(["nmcli", "connection", "up", conn], timeout=15)
    if up.returncode != 0:
        raise NetworkApplyError(f"nmcli up failed: {up.stderr.strip() or up.stdout.strip()}")
    return {"applied": True, "manager": "networkmanager",
            "detail": f"appended {', '.join(to_add)} to {conn}"}


def _fallback_via_resolvconf(servers: List[str]) -> dict:
    """Fallback path for networkd / dhcpcd / ifupdown / unknown where we
    don't have a clean knob — append nameserver lines straight to
    /etc/resolv.conf with a marker so we stay idempotent. NOTE: may be
    overwritten by the resolver manager on next lease/reboot — that's a
    best-effort; the caller surfaces a warning in that case."""
    content = nc.host_read_file("/etc/resolv.conf") or ""
    existing_ns = set()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) >= 2:
                existing_ns.add(parts[1])
    to_add = [s for s in servers if s not in existing_ns]
    if not to_add:
        return {"applied": False, "manager": "resolvconf",
                "detail": "fallback DNS already in /etc/resolv.conf"}
    # Strip any prior pitun block, then re-append a fresh one.
    lines = [ln for ln in content.splitlines() if _RESOLV_MARK not in ln]
    lines.append(_RESOLV_MARK)
    for s in to_add:
        lines.append(f"nameserver {s}  {_RESOLV_MARK}")
    new = "\n".join(lines) + "\n"
    r = nc.host_run(["tee", "/etc/resolv.conf"], input_data=new, timeout=5)
    if r.returncode != 0:
        raise NetworkApplyError(f"failed to write /etc/resolv.conf: {r.stderr.strip()}")
    return {"applied": True, "manager": "resolvconf",
            "detail": f"appended {', '.join(to_add)} (may not persist across reboot)"}


def apply_host_fallback_dns(servers: List[str]) -> dict:
    """Ensure `servers` are present as FALLBACK DNS for the host's own
    resolver, additively (DHCP/router DNS stays primary).

    Picks the right mechanism: systemd-resolved FallbackDNS if active
    (cleanest), else NetworkManager ipv4.dns append, else direct
    resolv.conf append. Idempotent — safe to call on every boot.

    Returns {"applied": bool, "manager": str, "detail": str}. An empty
    `servers` list is a no-op (returns applied=False) — removal of
    fallbacks is intentionally NOT automated here to avoid surprising
    an operator who set them up manually.
    """
    clean = _validate_dns_list([s.strip() for s in servers if s and s.strip()])
    if not clean:
        return {"applied": False, "manager": "none",
                "detail": "no fallback DNS servers configured"}

    if _resolved_active():
        return _fallback_via_resolved(clean)
    manager = nc.detect_manager()
    if manager == "networkmanager":
        return _fallback_via_nm(clean)
    return _fallback_via_resolvconf(clean)
