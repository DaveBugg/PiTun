"""WAN acquisition for router mode: how the box gets its uplink.

Four shapes, because ISPs differ and the differences are not cosmetic:

* **DHCP** — the common case; the OS already does this.
* **Static** — address, gateway and resolvers supplied by hand.
* **PPPoE** — the "authorising WAN": a session is established with a username
  and password before any IP exists.
* **VLAN tag** — many ISPs deliver service on a tagged VLAN; untagged frames
  get no response at all, which looks exactly like a dead line.
* **MAC clone** — some ISPs bind service to the MAC of whatever was first
  plugged in, so a new box gets nothing until it presents the old address.

The one that shapes the rest of the system: **with PPPoE or a VLAN tag, the
interface carrying internet traffic is not the physical port.** PPPoE creates
`ppp0`; a VLAN creates `eth0.835`. NAT, the firewall and the counters must be
attached to that interface, not to the port the cable is in — masquerading on
`eth0` while traffic leaves on `ppp0` produces a router that silently forwards
nothing. `effective_interface()` is what the rest of router mode must use.

Implementation goes through NetworkManager. That's not arbitrary:
systemd-networkd cannot establish a PPPoE session at all (it needs a separate
pppd unit), while NetworkManager handles PPPoE, VLANs and MAC cloning through
one interface we already drive elsewhere. On a box running something else we
say so plainly rather than half-working.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.network_config import detect_manager, host_run

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_CONN_NAME = "pitun-wan"


class WanConfigError(ValueError):
    """The requested uplink settings could not produce a working WAN."""


@dataclass
class WanConfig:
    interface: str                     # the physical port the cable is in
    mode: str = "dhcp"                 # dhcp | static | pppoe
    vlan_id: int = 0                   # 0 = untagged
    mac_clone: str = ""                # "" = use the adapter's own address
    # static
    address: str = ""                  # CIDR, e.g. "203.0.113.7/24"
    gateway: str = ""
    dns: List[str] = field(default_factory=list)
    # pppoe
    username: str = ""
    password: str = ""
    service: str = ""                  # optional PPPoE service name


def effective_interface(cfg: WanConfig) -> str:
    """The interface internet traffic actually leaves on.

    NAT, the firewall rules and the WAN counters all key off this. Attaching
    them to the physical port while PPPoE or a VLAN moves traffic elsewhere
    yields a router that forwards nothing, with no error to explain it.
    """
    if cfg.mode == "pppoe":
        # NetworkManager names PPPoE links ppp0, ppp1… in creation order. With
        # a single uplink this is ppp0; the orchestrator re-reads the live
        # state after apply rather than trusting this blindly.
        return "ppp0"
    if cfg.vlan_id:
        return f"{cfg.interface}.{cfg.vlan_id}"
    return cfg.interface


def validate(cfg: WanConfig) -> None:
    if not cfg.interface:
        raise WanConfigError("No WAN port selected")
    if cfg.mode not in ("dhcp", "static", "pppoe"):
        raise WanConfigError("WAN mode must be 'dhcp', 'static' or 'pppoe'")

    if cfg.vlan_id and not 1 <= cfg.vlan_id <= 4094:
        raise WanConfigError(
            f"VLAN id {cfg.vlan_id} is out of range — valid ids are 1-4094"
        )

    if cfg.mac_clone and not _MAC_RE.match(cfg.mac_clone):
        raise WanConfigError(
            f"'{cfg.mac_clone}' is not a MAC address (expected aa:bb:cc:dd:ee:ff)"
        )

    if cfg.mode == "static":
        if not cfg.address:
            raise WanConfigError("Static WAN needs an address")
        # A bare address is not an error to `ip_interface` — it silently
        # becomes a /32. Left alone, the operator who forgot the mask gets
        # told their gateway is outside a /32 subnet, which explains nothing.
        if "/" not in cfg.address:
            raise WanConfigError(
                f"'{cfg.address}' has no prefix length. Include the mask, "
                f"e.g. {cfg.address}/24 — without it the address covers only "
                f"itself and the gateway is unreachable."
            )
        try:
            iface = ipaddress.ip_interface(cfg.address)
        except ValueError as exc:
            raise WanConfigError(
                f"'{cfg.address}' is not a valid address with a prefix "
                f"(e.g. 203.0.113.7/24): {exc}"
            )
        if not cfg.gateway:
            raise WanConfigError("Static WAN needs a gateway")
        try:
            gw = ipaddress.ip_address(cfg.gateway)
        except ValueError as exc:
            raise WanConfigError(f"'{cfg.gateway}' is not a valid gateway address: {exc}")
        # A gateway outside the WAN subnet can't be reached to begin with, and
        # the failure appears as "no internet" long after this screen.
        if gw not in iface.network:
            raise WanConfigError(
                f"Gateway {gw} is outside the WAN subnet {iface.network} — it "
                f"can't be reached from this address."
            )
        for server in cfg.dns:
            try:
                ipaddress.ip_address(server)
            except ValueError:
                raise WanConfigError(f"'{server}' is not a valid DNS server address")

    if cfg.mode == "pppoe":
        if not cfg.username:
            raise WanConfigError(
                "PPPoE needs the username your ISP issued — the session is "
                "authenticated before any address exists."
            )
        if not cfg.password:
            raise WanConfigError("PPPoE needs a password")


def _nmcli(*args: str, timeout: float = 30.0):
    return host_run(["nmcli", *args], timeout=timeout)


def _require_networkmanager() -> None:
    manager = detect_manager()
    if manager != "networkmanager":
        raise WanConfigError(
            f"WAN configuration needs NetworkManager, but this box runs "
            f"'{manager}'. PPPoE in particular cannot be set up through "
            f"systemd-networkd at all. Configure the uplink with the host's own "
            f"tooling, or install NetworkManager."
        )


def apply(cfg: WanConfig) -> dict:
    """Create (or replace) the uplink connection. Returns a summary.

    Deleting the old connection before adding the new one is deliberate:
    leaving two profiles that both claim the port produces a link that comes
    up as whichever NetworkManager picked, which is worse to debug than a
    clean failure.
    """
    validate(cfg)
    _require_networkmanager()

    # Remove any previous PiTun-managed uplink so this is a replace, not an
    # accumulation. Ignore failures: absence is the expected first-run state.
    _nmcli("connection", "delete", _CONN_NAME)
    vlan_conn = f"{_CONN_NAME}-vlan"
    _nmcli("connection", "delete", vlan_conn)

    steps: List[str] = []
    parent = cfg.interface

    # VLAN first: everything above it rides on the tagged link, so the
    # addressing mode has to be configured on the VLAN, not the raw port.
    if cfg.vlan_id:
        r = _nmcli(
            "connection", "add", "type", "vlan", "con-name", vlan_conn,
            "ifname", f"{cfg.interface}.{cfg.vlan_id}",
            "dev", cfg.interface, "id", str(cfg.vlan_id),
        )
        if r.returncode != 0:
            raise WanConfigError(f"Could not create VLAN {cfg.vlan_id}: {r.stderr.strip()}")
        parent = f"{cfg.interface}.{cfg.vlan_id}"
        steps.append(f"VLAN {cfg.vlan_id} on {cfg.interface}")

    if cfg.mode == "pppoe":
        args = [
            "connection", "add", "type", "pppoe", "con-name", _CONN_NAME,
            "ifname", parent, "username", cfg.username, "password", cfg.password,
        ]
        if cfg.service:
            args += ["service", cfg.service]
        r = _nmcli(*args)
        if r.returncode != 0:
            raise WanConfigError(f"Could not create the PPPoE session: {r.stderr.strip()}")
        steps.append(f"PPPoE as {cfg.username}")
    else:
        args = [
            "connection", "add", "type", "ethernet", "con-name", _CONN_NAME,
            "ifname", parent,
        ]
        if cfg.mode == "static":
            args += [
                "ipv4.method", "manual",
                "ipv4.addresses", cfg.address,
                "ipv4.gateway", cfg.gateway,
            ]
            if cfg.dns:
                args += ["ipv4.dns", ",".join(cfg.dns)]
        else:
            args += ["ipv4.method", "auto"]
        r = _nmcli(*args)
        if r.returncode != 0:
            raise WanConfigError(f"Could not create the uplink connection: {r.stderr.strip()}")
        steps.append(f"{cfg.mode} on {parent}")

    if cfg.mac_clone:
        # Set on the connection rather than the device so it survives a
        # reconnect — an ISP that binds to a MAC will refuse service again
        # the moment the original address reappears.
        r = _nmcli("connection", "modify", _CONN_NAME,
                   "802-3-ethernet.cloned-mac-address", cfg.mac_clone)
        if r.returncode != 0:
            logger.warning("Could not set cloned MAC: %s", r.stderr.strip())
        else:
            steps.append(f"MAC cloned as {cfg.mac_clone}")

    r = _nmcli("connection", "up", _CONN_NAME)
    if r.returncode != 0:
        raise WanConfigError(
            f"The uplink connection was created but would not come up: "
            f"{r.stderr.strip() or r.stdout.strip()}"
        )
    steps.append("link up")

    logger.info("WAN applied: %s", "; ".join(steps))
    return {"mode": cfg.mode, "interface": effective_interface(cfg), "steps": steps}


def teardown() -> dict:
    """Remove PiTun's uplink connections, returning the port to the host."""
    _nmcli("connection", "delete", _CONN_NAME)
    _nmcli("connection", "delete", f"{_CONN_NAME}-vlan")
    return {"removed": True}


def redact(cfg: WanConfig) -> dict:
    """A loggable view of the config with the PPPoE password removed."""
    return {
        "interface": cfg.interface, "mode": cfg.mode, "vlan_id": cfg.vlan_id,
        "mac_clone": cfg.mac_clone, "address": cfg.address,
        "gateway": cfg.gateway, "dns": list(cfg.dns),
        "username": cfg.username, "password": "********" if cfg.password else "",
    }
