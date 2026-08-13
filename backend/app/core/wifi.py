"""WiFi access point for router mode (hostapd sidecar).

Only relevant when `operating_mode = router` and the LAN is served over a
wireless adapter. Gated on the AP-capability probe in `network_config` — the
radio has to support AP mode, and most of the ways this goes wrong are
hardware or regulatory rather than software.

Things the wired side doesn't have to care about:

* **Country code is mandatory.** Which channels exist, and at what power, is a
  legal question that varies by country. hostapd will refuse to start, or come
  up with no usable channels, if this is wrong or missing.
* **Channel has to match the band.** 2.4 GHz is 1-14 on hw_mode=g; 5 GHz is a
  different set entirely on hw_mode=a. A 5 GHz channel with hw_mode=g is a
  config that parses and then produces silence.
* **The passphrase length is a protocol constraint**, not a policy: WPA-PSK is
  defined for 8-63 characters. Shorter is not "weak", it's invalid, and
  hostapd fails to start rather than warning.

As with DHCP, config generation is separate from the container so the part
that decides what goes on the air is testable without hardware.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CONTAINER_NAME = "pitun-hostapd"
CONF_DIR = "/etc/pitun"
CONF_NAME = "hostapd.conf"

# 2.4 GHz: 1-14 (14 is Japan-only, and only for 802.11b — we allow it and let
# the regulatory domain reject it rather than second-guessing every country).
_CHANNELS_24 = set(range(1, 15))
# 5 GHz channels that exist somewhere in the world. Which are *legal* is the
# country code's job; hostapd + the kernel's regulatory database enforce it.
_CHANNELS_5 = {
    36, 40, 44, 48, 52, 56, 60, 64,
    100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
    149, 153, 157, 161, 165,
}

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
# Interface names and addresses reach `ip` as arguments; keep them tight.
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
_CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")


class WifiConfigError(ValueError):
    """The requested WiFi settings could not produce a working access point."""


@dataclass
class WifiConfig:
    interface: str
    ssid: str
    passphrase: str
    country: str                 # ISO 3166-1 alpha-2, e.g. "DE"
    band: str = "2.4"            # "2.4" | "5"
    channel: int = 0             # 0 = let hostapd pick within the band
    security: str = "wpa2"       # "wpa2" | "wpa2wpa3"
    hidden: bool = False
    bridge: str = ""             # bridge to attach the AP to (one L2 segment)


def _validate(cfg: WifiConfig) -> None:
    if not cfg.interface:
        raise WifiConfigError("No wireless interface selected")

    # SSID is measured in bytes, not characters — an emoji SSID can be well
    # under 32 characters and still be rejected by the driver.
    ssid_bytes = len(cfg.ssid.encode("utf-8"))
    if not 1 <= ssid_bytes <= 32:
        raise WifiConfigError(
            f"Network name must be 1-32 bytes (this one is {ssid_bytes})"
        )

    # WPA-PSK counts BYTES of printable ASCII, not characters. A Cyrillic
    # passphrase passes a len() check and is then rejected by hostapd — and
    # since a failed AP start now rolls the whole router back, a typo in the
    # WiFi password would drop NAT, DHCP and the uplink with it.
    pass_bytes = len(cfg.passphrase.encode("utf-8"))
    if not 8 <= pass_bytes <= 63:
        raise WifiConfigError(
            f"WiFi password must be 8-63 bytes (this one is {pass_bytes}) — "
            f"that's the WPA standard, not a policy we chose; hostapd refuses "
            f"to start outside it."
        )
    if not all(32 <= ord(c) <= 126 for c in cfg.passphrase):
        raise WifiConfigError(
            "WiFi password must use printable ASCII only. WPA-PSK is defined "
            "over ASCII, so anything else is accepted here and then rejected "
            "by the radio."
        )
    # The SSID is written verbatim into a line-oriented config file, so a
    # newline in it would inject a directive rather than name a network.
    if any(ord(c) < 32 or ord(c) == 127 for c in cfg.ssid):
        raise WifiConfigError("Network name cannot contain control characters")

    if not _COUNTRY_RE.match(cfg.country or ""):
        raise WifiConfigError(
            "A two-letter country code is required (e.g. DE, NL, GE). It "
            "decides which channels and power levels are legal, and the radio "
            "won't come up correctly without it."
        )

    if cfg.band not in ("2.4", "5"):
        raise WifiConfigError("Band must be '2.4' or '5'")

    if cfg.channel:
        allowed = _CHANNELS_24 if cfg.band == "2.4" else _CHANNELS_5
        if cfg.channel not in allowed:
            raise WifiConfigError(
                f"Channel {cfg.channel} is not a {cfg.band} GHz channel. "
                f"A mismatched channel produces a config that loads and then "
                f"puts nothing on the air."
            )

    if cfg.security not in ("wpa2", "wpa2wpa3"):
        raise WifiConfigError("Security must be 'wpa2' or 'wpa2wpa3'")


# Fallback channels for "auto". Non-overlapping in 2.4 GHz; the lowest
# universally-legal indoor channel in 5 GHz (36 is allowed everywhere 5 GHz
# is, and needs no DFS wait).
_AUTO_CHANNEL = {"2.4": 6, "5": 36}


def render_hostapd_conf(cfg: WifiConfig) -> str:
    """Produce the hostapd config, or raise WifiConfigError."""
    _validate(cfg)

    # `channel=0` asks hostapd to run ACS, which needs noise-floor figures in
    # the driver's survey data. Plenty of chips never report it — mt7921 is one
    # — and hostapd then loops "insufficient survey data" over every channel
    # forever instead of failing. The process stays alive, so the container
    # looks healthy while nothing is on the air. Pick a channel ourselves: a
    # fixed one that works beats an automatic one that hangs.
    channel = cfg.channel or _AUTO_CHANNEL[cfg.band]

    hw_mode = "g" if cfg.band == "2.4" else "a"
    lines = [
        "# Generated by PiTun — do not edit; changes are overwritten.",
        f"interface={cfg.interface}",
    ]
    if cfg.bridge:
        # Putting the AP in the same bridge as the wired LAN is what makes
        # wired and wireless one network: one subnet, one DHCP scope, and
        # devices on either side can see each other.
        lines.append(f"bridge={cfg.bridge}")

    lines += [
        "driver=nl80211",
        f"ssid={cfg.ssid}",
        f"country_code={cfg.country}",
        # Obey the regulatory database rather than transmitting on whatever
        # the hardware is physically capable of.
        "ieee80211d=1",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        f"ignore_broadcast_ssid={1 if cfg.hidden else 0}",
        "auth_algs=1",
        "wmm_enabled=1",
        "wpa=2",
        f"wpa_passphrase={cfg.passphrase}",
        "rsn_pairwise=CCMP",
    ]

    if cfg.security == "wpa2wpa3":
        # Transitional mode: WPA3-capable clients use SAE, older ones stay on
        # PSK. Management-frame protection is optional here rather than
        # required — required would lock out the WPA2 clients this mode exists
        # to keep working.
        lines += ["wpa_key_mgmt=WPA-PSK SAE", "ieee80211w=1", "sae_require_mfp=1"]
    else:
        lines += ["wpa_key_mgmt=WPA-PSK"]

    if cfg.band == "5":
        # 802.11ac on 5 GHz where the radio supports it. hostapd ignores these
        # if the driver can't do VHT, so they're safe to always emit.
        lines += ["ieee80211n=1", "ieee80211ac=1", "wmm_enabled=1"]
    else:
        lines += ["ieee80211n=1"]

    return "\n".join(lines) + "\n"


def redact(conf: str) -> str:
    """The config with the passphrase removed, for logs and diagnostics."""
    return re.sub(r"^(wpa_passphrase=).*$", r"\1********", conf, flags=re.M)


# ── Bridging wired LAN + AP into one segment ─────────────────────────────────
#
# Wired and wireless clients should be one network: same subnet, one DHCP
# scope, able to see each other. That means a bridge holding the wired LAN
# port, with hostapd adding the AP interface to it via `bridge=`.
#
# This is the most disruptive operation in router mode. Enslaving an interface
# to a bridge moves its IP address, which drops every connection on that port
# — including the SSH session or web UI of whoever is running it, if they are
# on the LAN side. It is never done implicitly: the caller decides, and phase
# 3's watchdog is what makes it recoverable.

BRIDGE_NAME = "br-lan"


def _ip(*args: str, timeout: float = 10.0):
    from app.core.network_config import host_run
    return host_run(["ip", *args], timeout=timeout)


def bridge_exists(name: str = BRIDGE_NAME) -> bool:
    return _ip("link", "show", name).returncode == 0


def create_lan_bridge(wired_lan, address_cidr: str,
                      name: str = BRIDGE_NAME, also_flush=()) -> dict:
    """Put the wired LAN ports into a bridge and move the address there.

    `wired_lan` is one interface name or a sequence of them — a LAN can be
    several sockets plus the radio, and clients on any of them belong to the
    same subnet and the same DHCP scope. The AP is not enslaved here: hostapd
    adds its own interface via `bridge=`, and doing it from both sides races.

    `also_flush` names interfaces that must give up their addresses without
    being enslaved — the radio, when it is the port the operator nominated as
    primary. hostapd enslaves it later, but the address has to leave now:
    otherwise the same gateway address answers on both the radio and the
    bridge, and which one a client reaches is a coin toss.

    `address_cidr` is the address the bridge must end up holding (e.g.
    "192.168.10.1/24") — the gateway clients talk to. Passing it explicitly
    rather than discovering it means the caller has already decided what the
    LAN's identity is, instead of this depending on whatever state an
    interface happens to be in mid-operation.

    Returns a summary of the steps taken. Raises WifiConfigError on failure,
    having attempted to put the address back where it came from.
    """
    members = [wired_lan] if isinstance(wired_lan, str) else list(wired_lan)
    members = [m for m in members if m]

    # These become arguments to `ip`, so constrain them the same way the
    # firewall does rather than trusting the caller.
    for m in members:
        if not _IFACE_RE.match(m):
            raise WifiConfigError(f"Invalid interface name ({m!r})")
    if not _IFACE_RE.match(name or ""):
        raise WifiConfigError(f"Invalid bridge name ({name!r})")
    if not _CIDR_RE.match(address_cidr or ""):
        raise WifiConfigError(f"{address_cidr!r} is not an address in CIDR form")
    if not members:
        raise WifiConfigError("A LAN bridge needs at least one wired port")

    primary = members[0]
    steps: list[str] = []

    if bridge_exists(name):
        # Already bridged — reconcile membership and move on. Ports may have
        # been added to the LAN since the bridge was built.
        for m in members:
            if _ip("link", "set", m, "master", name).returncode == 0:
                _ip("link", "set", m, "up")
                steps.append(f"{m} already/now enslaved to {name}")
        return {"bridge": name, "members": members, "steps": steps, "created": False}

    if _ip("link", "add", "name", name, "type", "bridge").returncode != 0:
        raise WifiConfigError(f"Could not create bridge {name}")
    steps.append(f"created {name}")

    def _undo(reason: str):
        """Leave a working un-bridged LAN rather than neither."""
        for m in members:
            _ip("link", "set", m, "nomaster")
        _ip("link", "delete", name)
        _ip("addr", "add", address_cidr, "dev", primary)
        _ip("link", "set", primary, "up")
        raise WifiConfigError(reason)

    # Order matters: flush addresses only after the bridge exists, so a failure
    # leaves the smallest possible window with no address anywhere. Every
    # member is flushed — a secondary port carrying its own address would
    # otherwise keep answering on a subnet nothing routes any more.
    extra_flush = [f for f in (also_flush or ()) if f and f not in members]
    for m in members + extra_flush:
        _ip("addr", "flush", "dev", m)
    steps.append(f"flushed addresses from {', '.join(members + extra_flush)}")

    for m in members:
        if _ip("link", "set", m, "master", name).returncode != 0:
            _undo(f"Could not enslave {m} to {name}")
        _ip("link", "set", m, "up")
        steps.append(f"enslaved {m}")

    if _ip("addr", "add", address_cidr, "dev", name).returncode != 0:
        _undo(f"Could not move {address_cidr} onto {name}")
    _ip("link", "set", name, "up")
    steps.append(f"{name} holds {address_cidr}")
    logger.info("LAN bridge ready: %s", steps)
    return {"bridge": name, "members": members, "steps": steps, "created": True}


def bridge_members(name: str = BRIDGE_NAME) -> list[str]:
    """Interfaces currently enslaved to the bridge, as the kernel sees it."""
    r = _ip("-o", "link", "show", "master", name)
    if r.returncode != 0:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        # "3: enp2s0: <BROADCAST,...> mtu 1500 master br-lan ..."
        parts = line.split(":", 2)
        if len(parts) >= 2:
            iface = parts[1].strip().split("@")[0]
            if iface:
                out.append(iface)
    return out


def dissolve_lan_bridge(name: str = BRIDGE_NAME) -> dict:
    """Take the bridge apart using only what the kernel currently reports.

    Teardown is the escape hatch and runs from states nobody planned, including
    after the settings that built the bridge have changed. Reading the members
    and the address off the live bridge means it undoes what is actually there
    rather than what a config file believes.
    """
    if not bridge_exists(name):
        return {"bridge": name, "removed": False}

    from app.core.network_config import read_interface_address
    ip, prefix = read_interface_address(name)
    members = bridge_members(name)
    # The address goes back to a wired member: a radio in AP mode is torn down
    # with hostapd and would not keep it.
    from app.core.network_config import is_wireless
    target = next((m for m in members if not is_wireless(m)), members[0] if members else "")

    for m in members:
        _ip("link", "set", m, "nomaster")
    _ip("addr", "flush", "dev", name)
    _ip("link", "delete", name)
    if target and ip and prefix is not None:
        _ip("addr", "add", f"{ip}/{prefix}", "dev", target)
        _ip("link", "set", target, "up")
    logger.info("LAN bridge %s dissolved; %s holds %s/%s", name, target or "-", ip, prefix)
    return {"bridge": name, "removed": True, "members": members, "address_on": target}


def remove_lan_bridge(wired_lan, address_cidr: str,
                      name: str = BRIDGE_NAME) -> dict:
    """Undo `create_lan_bridge`, returning the address to the primary port."""
    members = [wired_lan] if isinstance(wired_lan, str) else list(wired_lan)
    members = [m for m in members if m]
    if not bridge_exists(name):
        return {"bridge": name, "removed": False}
    for m in members:
        _ip("link", "set", m, "nomaster")
    _ip("addr", "flush", "dev", name)
    _ip("link", "delete", name)
    if members:
        # Back onto the port the operator nominated, not spread across all of
        # them: two ports holding the same address on one segment is a fault,
        # not a fallback.
        _ip("addr", "add", address_cidr, "dev", members[0])
        _ip("link", "set", members[0], "up")
    logger.info("LAN bridge %s removed; %s holds %s",
                name, members[0] if members else "-", address_cidr)
    return {"bridge": name, "members": members, "removed": True}


# ── Container lifecycle ──────────────────────────────────────────────────────

import asyncio  # noqa: E402
import os  # noqa: E402

from app.config import settings  # noqa: E402


def write_conf(conf: str) -> str:
    os.makedirs(CONF_DIR, exist_ok=True)
    path = os.path.join(CONF_DIR, CONF_NAME)
    # The passphrase lives here in the clear (hostapd needs it that way), so
    # keep it off other users' eyes at least.
    with open(path, "w") as f:
        f.write(conf)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _docker_client():
    import docker
    return docker.DockerClient(base_url=os.environ.get("DOCKER_HOST", settings.docker_host))


def _start_sync(cfg: WifiConfig) -> dict:
    from docker.errors import ImageNotFound, NotFound

    conf = render_hostapd_conf(cfg)      # validates before anything runs
    write_conf(conf)
    logger.info("hostapd config:\n%s", redact(conf))

    client = _docker_client()
    try:
        client.containers.get(CONTAINER_NAME).remove(force=True)
    except NotFound:
        pass

    try:
        c = client.containers.run(
            image=settings.hostapd_image,
            name=CONTAINER_NAME,
            detach=True,
            network_mode="host",
            restart_policy={"Name": "unless-stopped"},
            # NET_ADMIN to drive the radio and the bridge port.
            cap_add=["NET_ADMIN", "NET_RAW"],
            volumes={CONF_DIR: {"bind": "/etc/hostapd", "mode": "ro"}},
        )
    except ImageNotFound:
        raise WifiConfigError(
            f"Image {settings.hostapd_image} not found — build it first: "
            f"docker build -t {settings.hostapd_image} docker/hostapd/"
        )

    # A detached run returns as soon as the container is *started*, not as soon
    # as it survives. Both daemons exit immediately on a config they can't use,
    # and reporting success then makes the orchestrator's all-or-nothing
    # rollback impossible for the two steps most likely to fail on first use.
    import time as _time
    _time.sleep(1.5)
    try:
        c.reload()
        if c.status != "running":
            tail = c.logs(tail=20).decode(errors="replace").strip()
            c.remove(force=True)
            raise WifiConfigError(
                "hostapd started and exited immediately. Its own output:\n" + tail
            )
    except WifiConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 — the check must not mask the start
        logger.warning("Could not confirm hostapd stayed up: %s", exc)

    # "The process is alive" is not "there is a WiFi network". hostapd can sit
    # in channel selection or fail to bring the radio up while staying
    # running — the container looks healthy and no client can see an SSID.
    # Ask the radio instead: it reports AP mode only once the AP is actually
    # on the air, which is what the caller's rollback needs to know.
    if not _radio_is_ap(cfg.interface, timeout=15.0):
        tail = ""
        try:
            tail = c.logs(tail=25).decode(errors="replace").strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            c.remove(force=True)
        except Exception:  # noqa: BLE001
            pass
        raise WifiConfigError(
            f"hostapd is running but {cfg.interface} never entered AP mode, so "
            "no network is being broadcast. Its own output:\n" + tail
        )

    logger.info("Access point started on %s (%s GHz)", cfg.interface, cfg.band)
    return {"running": True, "interface": cfg.interface}


def _radio_is_ap(interface: str, *, timeout: float = 15.0) -> bool:
    """True once `iw` reports the interface in AP mode, else False on timeout."""
    import time as _time
    from app.core.network_config import host_run

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            proc = host_run(["iw", "dev", interface, "info"], timeout=5)
            if proc.returncode == 0 and "type AP" in (proc.stdout or ""):
                return True
        except Exception:  # noqa: BLE001 — probing must never raise
            pass
        _time.sleep(1.0)
    return False


def _stop_sync() -> dict:
    from docker.errors import NotFound
    try:
        _docker_client().containers.get(CONTAINER_NAME).remove(force=True)
        return {"running": False}
    except NotFound:
        return {"running": False}


def _status_sync() -> dict:
    from docker.errors import NotFound
    try:
        c = _docker_client().containers.get(CONTAINER_NAME)
        c.reload()
        return {"exists": True, "running": c.status == "running", "status": c.status}
    except NotFound:
        return {"exists": False, "running": False, "status": "absent"}
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "running": False, "status": f"error: {exc}"}


async def start(cfg: WifiConfig) -> dict:
    return await asyncio.to_thread(_start_sync, cfg)


async def stop() -> dict:
    return await asyncio.to_thread(_stop_sync)


async def status() -> dict:
    return await asyncio.to_thread(_status_sync)
