"""Host network configuration: detect / read / apply / backup / rollback.

This is the backend side of the v1.3.3 Network UI feature. Lets the
operator change the PiTun host's IP / gateway / DNS from the web UI
without SSHing into the box and editing config files by hand. Cuts
out a class of footguns we saw on the 1.4 mini-PC where the DHCP-
pushed default route pointed at another PiTun in the same LAN,
creating a double-hop.

Why nsenter instead of bind-mounts
----------------------------------
Backend runs in a Docker container with ``network_mode: host`` +
``privileged: true`` + ``pid: host``. The kernel network stack is
shared (so ``ip a`` inside the container shows host interfaces), but
the mount namespace is private — ``/etc/network/interfaces`` from the
host is NOT visible by default.

We could add bind-mounts in docker-compose.yml for each manager's
config dir (``/etc/network``, ``/etc/NetworkManager``, ``/etc/systemd/
network``, ``/etc/dhcpcd.conf``), but that's four mounts that may or
may not exist on every host — Docker would happily create a fresh
empty dir on the host for any missing source, polluting it.

nsenter is the cleaner path: enter PID 1's namespaces and read/write
host files in their natural location. The container itself stays
declarative (compose untouched) and code reads files that genuinely
exist where they should.

Manager detection priority
--------------------------
1. NetworkManager  — modern desktop / RPi OS Bookworm+
2. systemd-networkd — Debian server modern
3. ifupdown         — Debian server classic
4. dhcpcd           — legacy RPi OS

Multiple managers can be installed but only one actively controlling
the link. We pick the first one ``systemctl is-active = active``
returns.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────

ManagerType = str   # "networkmanager" | "networkd" | "ifupdown" | "dhcpcd" | "unknown"
IfaceMode = str     # "dhcp" | "static" | "manual" | "unknown"


@dataclass
class NetworkState:
    interface: str
    manager: ManagerType
    mode: IfaceMode
    ip: Optional[str]
    cidr: Optional[int]
    gateway: Optional[str]
    dns: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── nsenter plumbing ──────────────────────────────────────────────────────

_NSENTER_BASE = [
    "nsenter",
    "--target", "1",
    "--mount", "--uts", "--ipc", "--net", "--pid",
    "--",
]

# Allowlist of program names `host_run` will execute. Defense-in-depth
# against the (current) all-internal callers ever growing a path where
# user input flows into `argv[0]`. List-form subprocess.run() already
# means there's no shell interpretation of metacharacters, so this is
# strictly belt-and-suspenders — but it's also the pattern CodeQL
# recognises as a sanitiser for the `py/command-line-injection` query
# (we picked up two Critical findings on the bare subprocess.run calls
# below until this guard landed).
#
# Add new programs here when callers need them. Never relax this to
# accept user input.
_ALLOWED_PROGRAMS = frozenset({
    "arping", "cat", "chmod", "chown", "command", "dhcpcd", "ifdown",
    "ifup", "ip", "iw", "mkdir", "mv", "netplan", "networkctl", "nmcli",
    "nsenter", "ping", "resolvectl", "rm", "sh", "stat", "systemctl",
    "tee", "test",
})


def _validate_argv(argv: List[str]) -> None:
    """Raise ValueError unless `argv[0]` (the program) is in the
    allowlist. Element shape — string, non-empty — is asserted too so
    a stray `None`/int can't sneak through into subprocess.

    Why argv[0] only: with list-form subprocess.run, the remaining
    elements are passed as-is to execve() — no shell, no metacharacter
    interpretation. The program name is the only thing that *can*
    redirect execution. Callers still need their own input validation
    for the semantic shape of arguments (e.g. interface names match a
    regex) — this is just the last-line gate.
    """
    if not argv:
        raise ValueError("host_run: argv is empty")
    program = argv[0]
    if not isinstance(program, str) or not program:
        raise ValueError(f"host_run: invalid program name: {program!r}")
    if program not in _ALLOWED_PROGRAMS:
        raise ValueError(
            f"host_run: program {program!r} not in allowlist — "
            f"add to _ALLOWED_PROGRAMS in network_config.py if intentional."
        )
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            raise ValueError(f"host_run: argv[{i}] not a string: {arg!r}")


def host_run(
    argv: List[str],
    *,
    input_data: Optional[str] = None,
    timeout: float = 10.0,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command in PID 1's namespaces.

    Falls back to plain subprocess if nsenter isn't available
    (development / unit tests on a dev host). The fallback is best-
    effort and will likely fail on host-side mutation, but lets the
    read-only paths work for local iteration.
    """
    _validate_argv(argv)
    # Re-derive the program name from the literal allowlist instead of
    # passing `argv[0]` directly into subprocess. At runtime this is a
    # no-op — `_validate_argv` already guarantees `argv[0] in
    # _ALLOWED_PROGRAMS`. The point is that CodeQL's `py/command-line-
    # injection` taint tracker doesn't follow into `_validate_argv` and
    # treats `argv` as user-tainted; rebuilding the head of the list
    # from a frozenset literal severs the taint flow at a barrier
    # CodeQL recognises (membership in a set of literal strings).
    program = next(p for p in _ALLOWED_PROGRAMS if p == argv[0])
    safe_argv = [program, *argv[1:]]

    # When `pid: host` isn't set (e.g. dev), nsenter `--target 1` will
    # error with "permission denied" or "No such process". Detect the
    # container-vs-host case here so callers don't have to.
    use_nsenter = os.path.exists("/proc/1/ns/mnt") and _can_use_nsenter()
    cmd = (_NSENTER_BASE + safe_argv) if use_nsenter else safe_argv
    try:
        return subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            shell=False,
        )
    except FileNotFoundError:
        # nsenter missing — try the raw command in container's own
        # namespace as a last resort. Useful in tests.
        return subprocess.run(
            safe_argv,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            shell=False,
        )


_NSENTER_OK: Optional[bool] = None


def _can_use_nsenter() -> bool:
    """Memoised check: can we actually enter PID 1's mount ns?

    Some constrained environments (CI runners, podman without
    --privileged) ship nsenter but block the actual namespace switch
    with EPERM. Probing once at startup keeps the hot path fast.
    """
    global _NSENTER_OK
    if _NSENTER_OK is not None:
        return _NSENTER_OK
    try:
        r = subprocess.run(
            _NSENTER_BASE + ["true"],
            capture_output=True,
            timeout=2,
        )
        _NSENTER_OK = (r.returncode == 0)
    except (FileNotFoundError, subprocess.SubprocessError):
        _NSENTER_OK = False
    return _NSENTER_OK


def host_read_file(path: str) -> Optional[str]:
    """Read a host-side file via nsenter.

    Returns None if the file doesn't exist or can't be read — caller
    decides whether absence is a hard error or a "manager not in use"
    signal.
    """
    r = host_run(["cat", path], timeout=5)
    if r.returncode != 0:
        return None
    return r.stdout


def host_path_exists(path: str) -> bool:
    r = host_run(["test", "-e", path], timeout=3)
    return r.returncode == 0


def host_systemctl_is_active(service: str) -> bool:
    r = host_run(["systemctl", "is-active", service], timeout=5)
    # systemctl prints state on stdout AND exits 0 iff active. Both
    # checks together survive distros where stdout might be empty.
    return r.returncode == 0 and r.stdout.strip() == "active"


# ── Manager detection ─────────────────────────────────────────────────────

# Probe order matches the docstring's priority list. First match wins
# so e.g. a system with both NetworkManager installed and ifupdown
# masked → reports NM correctly.
_MANAGER_PROBES = [
    ("NetworkManager",    "networkmanager"),
    ("systemd-networkd",  "networkd"),
    ("networking",        "ifupdown"),
    ("dhcpcd",            "dhcpcd"),
]


def detect_manager() -> ManagerType:
    for service, name in _MANAGER_PROBES:
        if host_systemctl_is_active(service):
            return name
    return "unknown"


# ── Live interface state (works without nsenter because network_mode: host) ──

def read_default_route() -> Tuple[Optional[str], Optional[str]]:
    """Return (interface, gateway_ip) of the default IPv4 route.

    Both fields can be None if no default route is configured —
    common during initial install before DHCP completes.
    """
    try:
        r = subprocess.run(
            ["ip", "-j", "-4", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None, None
    if r.returncode != 0 or not r.stdout.strip():
        return None, None
    try:
        routes = json.loads(r.stdout)
    except ValueError:
        return None, None
    if not routes:
        return None, None
    return routes[0].get("dev"), routes[0].get("gateway")


def read_interface_address(ifname: str) -> Tuple[Optional[str], Optional[int]]:
    """Return (ipv4, prefix_length) for the first IPv4 address on ifname.

    Skips link-local (169.254.0.0/16) because those are auto-assigned
    when DHCP fails — they're never the operator's intended config.
    """
    try:
        r = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "dev", ifname],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None, None
    if r.returncode != 0:
        return None, None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None, None
    if not data:
        return None, None
    for ai in data[0].get("addr_info", []):
        if ai.get("family") != "inet":
            continue
        ip = ai.get("local")
        if ip and ip.startswith("169.254."):
            continue
        return ip, ai.get("prefixlen")
    return None, None


def read_dns_servers() -> List[str]:
    """Parse /etc/resolv.conf for active nameservers.

    Works inside the container thanks to the existing bind-mount
    (/etc/resolv.conf:/host/resolv.conf). We read both paths and
    prefer the host-side one when present — covers the case where
    the container has a stale copy (NetworkManager replaces the file
    on the host but the bind-mount tracks the inode).
    """
    for candidate in ("/host/resolv.conf", "/etc/resolv.conf"):
        try:
            with open(candidate) as f:
                servers: List[str] = []
                for line in f:
                    line = line.strip()
                    if not line.startswith("nameserver"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] not in servers:
                        servers.append(parts[1])
                if servers:
                    return servers
        except OSError:
            continue
    return []


# ── Mode detection (per manager) ──────────────────────────────────────────

def detect_mode(manager: ManagerType, ifname: str) -> IfaceMode:
    if not ifname:
        return "unknown"
    if manager == "ifupdown":
        return _mode_ifupdown(ifname)
    if manager == "networkmanager":
        return _mode_nm(ifname)
    if manager == "networkd":
        return _mode_networkd(ifname)
    if manager == "dhcpcd":
        return _mode_dhcpcd(ifname)
    return "unknown"


def _mode_ifupdown(ifname: str) -> IfaceMode:
    """Parse /etc/network/interfaces{,d/*} for the `iface <ifname> inet …`
    line. ifupdown's grammar is line-based so a regex search is fine
    even without proper parsing of `auto` / `allow-hotplug` directives.
    """
    candidates = ["/etc/network/interfaces"]
    # `interfaces.d/` files are sourced from the main file.
    ls = host_run(["sh", "-c", "ls /etc/network/interfaces.d/ 2>/dev/null"], timeout=3)
    if ls.returncode == 0:
        for name in ls.stdout.split():
            if name:
                candidates.append(f"/etc/network/interfaces.d/{name}")

    needle = f"iface {ifname} inet"
    for path in candidates:
        content = host_read_file(path)
        if content is None:
            continue
        for line in content.splitlines():
            s = line.strip()
            if s.startswith(needle):
                rest = s[len(needle):].strip().split()
                if not rest:
                    continue
                method = rest[0]
                if method in ("dhcp", "static", "manual"):
                    return method
    return "unknown"


def _mode_nm(ifname: str) -> IfaceMode:
    """NetworkManager: nmcli reports ipv4.method per connection.

    Multiple connections can target one device; we look at the active
    one (state == activated)."""
    r = host_run([
        "nmcli", "-t", "-f", "NAME,DEVICE,STATE",
        "connection", "show", "--active",
    ], timeout=5)
    if r.returncode != 0:
        return "unknown"
    conn_name = None
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == ifname and parts[2] == "activated":
            conn_name = parts[0]
            break
    if not conn_name:
        return "unknown"
    r2 = host_run(["nmcli", "-t", "-f", "ipv4.method", "connection", "show", conn_name], timeout=5)
    if r2.returncode != 0:
        return "unknown"
    # Output is "ipv4.method:auto" or "ipv4.method:manual"
    line = r2.stdout.strip()
    if ":" not in line:
        return "unknown"
    method = line.split(":", 1)[1].strip()
    if method == "auto":
        return "dhcp"
    if method == "manual":
        return "static"
    return "unknown"


def _mode_networkd(ifname: str) -> IfaceMode:
    """systemd-networkd: parse /etc/systemd/network/*.network for the
    matching .network file. networkctl status would be authoritative
    but parses harder than a config file.
    """
    ls = host_run(["sh", "-c", "ls /etc/systemd/network/*.network 2>/dev/null"], timeout=3)
    if ls.returncode != 0:
        return "unknown"
    for path in ls.stdout.split():
        content = host_read_file(path)
        if content is None:
            continue
        # A .network file matches by [Match] Name= directive
        in_match = False
        in_network = False
        matches_iface = False
        has_dhcp = False
        has_static_addr = False
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("[Match]"):
                in_match, in_network = True, False
                continue
            if s.startswith("[Network]"):
                in_match, in_network = False, True
                continue
            if s.startswith("["):
                in_match = in_network = False
                continue
            if in_match and s.startswith("Name="):
                if s.split("=", 1)[1].strip() == ifname:
                    matches_iface = True
            elif in_network and s.startswith("DHCP="):
                val = s.split("=", 1)[1].strip().lower()
                if val in ("yes", "ipv4", "true"):
                    has_dhcp = True
            elif in_network and s.startswith("Address="):
                has_static_addr = True
        if matches_iface:
            if has_dhcp:
                return "dhcp"
            if has_static_addr:
                return "static"
    return "unknown"


def _mode_dhcpcd(ifname: str) -> IfaceMode:
    """dhcpcd: presence of `interface <ifname>` block with `static
    ip_address=` lines means static. Otherwise default is DHCP for
    every interface dhcpcd manages."""
    content = host_read_file("/etc/dhcpcd.conf")
    if content is None:
        return "unknown"
    in_iface_block = False
    saw_static = False
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("interface "):
            in_iface_block = (s.split(maxsplit=1)[1] == ifname)
            continue
        if in_iface_block and s.startswith("static "):
            saw_static = True
    return "static" if saw_static else "dhcp"


# ── Upstream-PiTun warning ────────────────────────────────────────────────

def _upstream_is_pitun(gateway: Optional[str]) -> bool:
    """Quick probe — does the upstream gateway respond like PiTun's /health?

    PiTun's `/health` endpoint is unauthenticated by design (used by
    docker healthcheck + watchdogs) and returns a small JSON blob that
    always contains "xray_running" — a field no random ISP router would
    emit. We use that as the cheapest fingerprint for "another PiTun
    is the upstream gateway", which would create a routing double-hop.

    Times out fast so this check never stalls the state read. False on
    any error — caller treats unreachable / parse-fail / nginx-404 as
    "not a PiTun".
    """
    if not gateway:
        return False
    try:
        import urllib.request
        # `/health` is the FastAPI's own endpoint (proxied through nginx
        # at /, see frontend nginx.conf). Hitting port 80 hits nginx which
        # forwards /health to the backend.
        req = urllib.request.Request(
            f"http://{gateway}/health",
            headers={"User-Agent": "PiTun-self-check/1.0"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status != 200:
                return False
            body = resp.read(1024).decode("utf-8", errors="ignore")
            # Match two PiTun-specific fields together to avoid false
            # positives on a random "status: ok" health endpoint of an
            # unrelated service.
            return '"xray_running"' in body and '"xray_expected"' in body
    except Exception:
        return False


# ── Top-level read_state ──────────────────────────────────────────────────

def read_state() -> NetworkState:
    """Snapshot of current network configuration on the host.

    Pure read — no side effects. Safe to call repeatedly; ~50ms total
    on a Debian host (3 systemctl probes + 2 `ip` calls + a few file
    reads).
    """
    manager = detect_manager()
    ifname, gateway = read_default_route()

    if not ifname:
        # No default route. Pick the first non-lo interface with an
        # IPv4 address so the UI still has something useful to show.
        try:
            r = subprocess.run(
                ["ip", "-j", "-4", "addr"],
                capture_output=True, text=True, timeout=5,
            )
            for iface in json.loads(r.stdout):
                if iface.get("ifname") in ("lo", None):
                    continue
                for ai in iface.get("addr_info", []):
                    if ai.get("family") == "inet":
                        ifname = iface["ifname"]
                        break
                if ifname:
                    break
        except Exception:  # noqa: BLE001
            pass

    ip, cidr = read_interface_address(ifname) if ifname else (None, None)
    dns = read_dns_servers()
    mode = detect_mode(manager, ifname or "")

    warnings: List[str] = []
    # Self-loop takes top billing: the default route points at THIS box's
    # own IP, so every off-LAN packet is handed straight back to us and
    # dies. This is the "new device set its PiTun gateway to itself"
    # footgun on a fresh install left on DHCP in gateway mode — the box
    # gets its own address as option 3. Checked BEFORE _upstream_is_pitun
    # because a self-loop also matches PiTun's own /health fingerprint and
    # would otherwise be mislabelled as a *different* PiTun upstream.
    self_loop = bool(gateway and ip and gateway == ip)
    if self_loop:
        warnings.append(
            f"Default gateway {gateway} is THIS PiTun's own IP — a routing "
            "self-loop. Outbound traffic has nowhere to go. Set the default "
            "route to your actual ISP router (usually 192.168.x.1) under "
            "'Change gateway / DNS' below. If the box was installed on DHCP "
            "in gateway mode, also give it a static IP so it stops receiving "
            "its own address as the gateway."
        )
    if manager == "unknown":
        warnings.append(
            "Could not detect an active network manager on the host. "
            "Network edits from the UI will be disabled — edit config "
            "files manually if you need to change IP/gateway/DNS."
        )
    if mode == "unknown" and manager != "unknown":
        warnings.append(
            f"Interface {ifname} configuration mode could not be parsed "
            f"from {manager}'s config — apply is disabled until resolved."
        )
    if not self_loop and _upstream_is_pitun(gateway):
        warnings.append(
            f"Upstream gateway {gateway} appears to be ANOTHER PiTun "
            "(matches PiTun's /health fingerprint). This creates a "
            "double-hop: your traffic goes you → this PiTun → other "
            "PiTun → ISP router. Set a static default route to your "
            "actual ISP router (usually 192.168.x.1) instead."
        )

    return NetworkState(
        interface=ifname or "",
        manager=manager,
        mode=mode,
        ip=ip,
        cidr=cidr,
        gateway=gateway,
        dns=dns,
        warnings=warnings,
    )


# ── Interface inventory (router mode, phase 0) ───────────────────────────────
#
# Router mode needs to know how many REAL NICs the box has: the UI only offers
# it on hardware with two or more, and the operator has to pick which one faces
# the ISP. Everything here is read-only — nothing in phase 0 changes behaviour.

# Interfaces that exist because of virtualisation, containers or our own tunnel.
# They must never be offered as a WAN/LAN candidate: picking `docker0` as the
# uplink would be nonsense, and `xray0` is created by PiTun itself.
_VIRTUAL_PREFIXES = (
    "lo", "docker", "br-", "veth", "virbr", "tun", "tap", "xray",
    "wg", "ppp", "bond", "dummy", "sit", "gre", "vxlan", "tailscale", "zt",
)


def _is_physical(name: str, link: dict) -> bool:
    """True when `name` looks like a real NIC we could assign a role to.

    Prefix matching alone is not enough — a USB-Ethernet dongle can be
    `enx<mac>` and a VLAN sub-interface is `eth0.835` — so we also drop
    anything the kernel reports as having a `link_type` we can't route on,
    and anything that is a sub-interface of another link.
    """
    if name.startswith(_VIRTUAL_PREFIXES):
        return False
    if link.get("link_type") != "ether":
        return False
    # VLAN sub-interfaces (eth0.835) are not standalone role candidates.
    if "." in name:
        return False
    # Being enslaved is NOT disqualifying. Router mode bridges the LAN ports
    # into `br-lan` itself, so the moment it comes up, the very ports the
    # operator assigned would vanish from this inventory — the panel would
    # report one port on a three-port box, and role validation would reject
    # the stored configuration, including the request to switch back to
    # gateway. Callers that care can look at `master`.
    return True


def list_interfaces() -> List[dict]:
    """Inventory of physical NICs with enough state to choose roles.

    Returns one dict per interface: name, mac, up (admin state), carrier
    (cable actually plugged in), ipv4/cidr, and `is_default_route` marking
    the one currently carrying traffic to the internet.

    Physical-only: virtual/container/tunnel interfaces are filtered out, so
    `len(...) >= 2` is a meaningful "this box can be a router" test.
    """
    try:
        r = subprocess.run(
            ["ip", "-j", "link", "show"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    try:
        links = json.loads(r.stdout or "[]")
    except ValueError:
        return []

    default_iface, _ = read_default_route()
    out: List[dict] = []
    for link in links:
        name = link.get("ifname") or ""
        if not name or not _is_physical(name, link):
            continue
        ip, cidr = read_interface_address(name)
        # Wireless NICs count as ports, but serving WiFi needs AP mode — a
        # chipset property. Probe it here so the UI never offers "use this as
        # the LAN's WiFi" on a client-only adapter.
        wifi = wifi_capabilities(name)
        out.append({
            "wireless": wifi["wireless"],
            "ap_capable": wifi["ap_capable"],
            "wifi_detail": wifi["detail"],
            "wifi_modes": wifi["modes"],
            "name": name,
            "mac": link.get("address") or "",
            # `operstate` is the kernel's view; "UP" needs a carrier too, and
            # a NIC with no cable reports LOWER_UP missing from its flags.
            "up": link.get("operstate") == "UP",
            "carrier": "LOWER_UP" in (link.get("flags") or []),
            "ipv4": ip,
            "cidr": cidr,
            "is_default_route": name == default_iface,
            # Set while router mode has the port in the LAN bridge; the
            # address then lives on the bridge, not here.
            "master": link.get("master") or "",
        })
    out.sort(key=lambda i: i["name"])
    return out


# ── Wireless capability probe (router mode, phase 1b gate) ───────────────────
#
# Serving WiFi means putting the radio into AP mode, and that is a property of
# the chipset + driver, not of "it's a wireless NIC". Plenty of parts —
# most cheap USB dongles, some built-in Realtek/Broadcom — are client-only.
# Trying hostapd on those fails at runtime, after we've already torn down the
# working setup. So: probe first, and only offer AP mode on a positive answer.
#
# Deliberately tri-state. `None` means "we could not determine" (usually `iw`
# isn't installed) and must NOT be shown as "not supported" — the operator can
# fix a missing tool, and telling them their hardware is incapable when it may
# be fine is worse than admitting we don't know.


def _phy_for(ifname: str) -> Optional[str]:
    """Kernel phy name (`phy0`) backing a wireless interface, else None."""
    try:
        with open(f"/sys/class/net/{ifname}/phy80211/name") as f:
            return f.read().strip() or None
    except OSError:
        return None


def is_wireless(ifname: str) -> bool:
    """True for a WiFi interface. Filesystem check — no tooling required."""
    return os.path.exists(f"/sys/class/net/{ifname}/wireless") or _phy_for(ifname) is not None


def wifi_capabilities(ifname: str) -> dict:
    """Can `ifname` act as an access point?

    Returns {wireless, ap_capable, modes, bands, detail}. `ap_capable` is
    True / False / None, where None means undetermined (see module note).
    """
    if not is_wireless(ifname):
        return {"wireless": False, "ap_capable": False, "modes": [], "bands": [],
                "detail": "not a wireless interface"}

    phy = _phy_for(ifname)
    if not phy:
        return {"wireless": True, "ap_capable": None, "modes": [], "bands": [],
                "detail": "no phy80211 for this interface — cannot query the driver"}

    r = host_run(["iw", "phy", phy, "info"], timeout=8)
    if r.returncode != 0:
        # Missing tool is the common case and it's fixable, so say so plainly.
        err_l = (r.stderr or "").lower()
        # The message differs by path: bare exec says "not found", nsenter
        # reports "failed to execute iw: No such file or directory".
        missing = (
            "not found" in err_l
            or "no such file" in err_l
            or r.returncode == 127
            or not (r.stdout or r.stderr)
        )
        return {
            "wireless": True, "ap_capable": None, "modes": [], "bands": [],
            "detail": ("`iw` is not installed — install it to check whether this "
                       "adapter supports AP mode" if missing
                       else f"iw failed: {(r.stderr or '').strip()[:120]}"),
        }

    # `iw phy info` prints an indented list under "Supported interface modes:",
    # each entry as "\t\t * AP". Collect until the indentation block ends.
    modes: List[str] = []
    in_modes = False
    bands: List[str] = []
    for line in (r.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Supported interface modes"):
            in_modes = True
            continue
        if in_modes:
            if stripped.startswith("*"):
                modes.append(stripped.lstrip("* ").strip())
                continue
            in_modes = False
        if stripped.startswith("Band "):
            bands.append(stripped.rstrip(":"))

    ap = "AP" in modes
    return {
        "wireless": True,
        "ap_capable": ap,
        "modes": modes,
        "bands": bands,
        "detail": ("adapter supports AP mode" if ap
                   else "adapter is client-only — it cannot serve WiFi"),
    }
