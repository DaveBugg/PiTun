# Router mode — design & plan

Branch `router-mode`, versions `1.6.0-beta.x`, merged to `master` when complete.

## Goal

Today PiTun is a **transparent proxy beside the router**: devices point their
gateway at the box, the box TPROXYs and hands "direct" traffic back to the
router, which does NAT and DHCP. That stays the default and the only option on
single-NIC hardware.

**Router mode** makes PiTun *the* router on hardware with 2+ ports: it owns the
WAN uplink, hands out addresses on the LAN, and NATs — with the proxy engine we
already have layered on top.

## Decisions taken

| Question | Decision |
|---|---|
| Hardware | 2+ NICs → router mode offered. 1 NIC → current mode only. |
| Choice | Explicit, in the UI. Never auto-switched. |
| Panel exposure | LAN-only from day one — never reachable from WAN. |
| Versioning | `1.6.0-beta.x` on this branch; merge to `master` when done. |
| Install/update scripts | Full review once the feature work lands (phase 4). |

## What we already have

- `table inet pitun` with `prerouting` / `output` / `forward` chains — NAT is a
  new chain in a table we already own and reload safely.
- **DNS for the LAN already works**: xray's `dns-in-53` listens on `0.0.0.0:53`
  with per-domain rules, DoH/DoT, FakeDNS and a query log.
- A **supervised-sidecar precedent**: `naive_manager` runs a container with
  `network_mode: host`, restart policy and backoff. A DHCP daemon is the same
  shape.
- Host network control (gateway/DNS apply + **backup/rollback**), which is
  exactly the safety net router mode needs.
- Devices page with MAC inventory → natural source for **static DHCP leases**.

## What's missing (the actual work)

1. **DHCP server** — hand out address/gateway/DNS on LAN.
2. **NAT** — masquerade LAN → WAN.
3. **WAN acquisition** — DHCP client (OS already), static, **PPPoE** (the
   "authorising WAN" case), VLAN tag, MAC clone.
4. **Firewall posture** — currently `policy accept` on forward; a router needs
   conntrack state rules, WAN→LAN drop, invalid drop.
5. **Multi-interface model** — config assumes one `interface` + `lan_cidr` +
   `gateway_ip`.
6. **Safety** — in router mode a failure takes the whole house offline with no
   fallback path. Needs a watchdog and a way back.

## Reuse strategy — the same daemons OpenWrt wraps

OpenWrt is a distro that packages upstream daemons plus its own glue. The glue
(`netifd`, `uci`, `ubus`, `procd`, `firewall4`) is tied to OpenWrt's config
system and is not liftable; the daemons are ordinary software we can run.

| Need | Take | Note |
|---|---|---|
| DHCP server | **dnsmasq** with `port=0` | `port=0` disables its DNS — **required**, xray already owns `:53` |
| NAT / firewall | **nftables** | already ours; a masquerade chain + state rules |
| PPPoE | **ppp** + `pppoe` plugin | generated config, like everything else we generate |
| VLAN on WAN | `ip link ... type vlan` | no daemon needed |
| RA / DHCPv6 | **odhcpd** | optional; we disable IPv6 by default on clean installs |

Alternative to dnsmasq: **Kea** (JSON config + REST control agent, stylistically
closer to us) — rejected for now as heavier for the same job.

**No DHCP server written in Python.** Boot-critical, security-sensitive, and a
solved problem.

`firewall4`'s generated ruleset is worth reading as a reference for a sane
router nftables layout — reading, not vendoring.

### Analogues for OpenWrt's glue (researched 2026-08-11)

| OpenWrt | Analogue | Verdict |
|---|---|---|
| `netifd` | `systemd-networkd`, or NetworkManager | **partly already ours** — see below |
| `uci` | our `Settings` table + API | not needed |
| `ubus` | D-Bus / nothing | not needed — single FastAPI process |
| `procd` | systemd + docker restart policy | already have it (naive sidecar) |
| `firewall4` | `firewalld`, or **`python3-nftables`** (libnftables JSON) | see below |

**We already own the netifd layer.** `network_config.detect_manager()` plus the
apply paths abstract over **ifupdown and NetworkManager** (netplan/networkctl are
already in the command allowlist). Two consequences:

- `systemd-networkd` **cannot do PPPoE** — it needs a separate `pppd`/`rp-pppoe`
  unit. NetworkManager **can**, and we already drive it via `nmcli`. So the
  nastiest part of phase 2 (WAN auth) may reuse a path we already have rather
  than introducing a new daemon. Decide per-manager in phase 2.
- `networkctl reload` applies `.network` changes without a reboot, which matters
  for the "apply, verify, roll back" loop.

**`python3-nftables`** (Debian `python3-nftables`) is a ctypes binding to
libnftables exposing the same JSON schema as `nft -j`, with `json_validate()`
and batched ruleset updates. We currently emit a **string script** into `nft -f -`
(`nftables._nft`), which is fine and already atomic for a full-table replace. The
JSON API earns its keep only once the ruleset grows (NAT + conntrack state +
WAN/LAN zones) — **evaluate in phase 3**, not before; it is a refactor of working
code. `firewalld` is rejected: a heavy abstraction over exactly what we already
do directly, and known to push very large JSON blobs through the same binding.

**router7** (pure-Go home router, gokrazy appliance) is a useful *structural*
reference — it cleanly separates `dhcp4` (WAN client) from `dhcp4d` (LAN server)
and `radvd`, and keeps all state as human-readable JSON. Not adoptable: it's a
whole appliance image in Go, has no PPPoE (built for a DHCP-provisioned Swiss
fibre ISP), and the author explicitly does not support outside users.

## Phases

### Phase 0 — foundations (no behaviour change)
- NIC enumeration + roles (we have no interface enumeration today).
- `operating_mode` setting: `gateway` (current, default) | `router`.
- UI offers router mode only when ≥2 usable NICs are present.
- Everything below stays inert while the mode is `gateway`.

### Phase 1 — LAN side
- dnsmasq sidecar (`port=0`), config generated from settings.
- DHCP settings: pool, lease time, options; **static leases from the Devices
  page** (MACs are already there).
- NAT + forward/state rules in `table inet pitun`.
- Panel bound to LAN only; WAN-side drop for the panel ports.

### Phase 2 — WAN side
- WAN modes: DHCP client / static / PPPoE / VLAN tag / MAC clone.
- WAN status + link diagnostics in the UI.

### Phase 3 — safety & hardening
- Router-mode firewall defaults (drop WAN→LAN, conntrack, drop invalid).
- **Watchdog / safe mode**: detect "no WAN after switch" and roll back, reusing
  the existing network backup/rollback machinery.
- Diagnostics extended for router mode.

### Phase 4 — install & update script review *(requested)*
- Re-review `01-first-boot.sh`, `02-install-stack.sh`, `03-deploy.sh`,
  `setup-vm.sh`, `install.sh`, `pitun-update.sh` end-to-end against the new
  mode: interface assumptions, DNS/hostname handling, idempotency, and the
  self-heal paths added in 1.5.x.

## Risks to keep in front

- **Single point of failure.** In gateway mode a dead PiTun means "point devices
  back at the router". In router mode there is no such fallback — the watchdog
  in phase 3 is not optional.
- **ISP variety.** PPPoE / IPoE / VLAN / MAC-binding differ per provider; WAN
  auth is the least testable part of this work.
- **Lock-out.** Getting the firewall wrong can lock the operator out of the
  panel. Every router-mode apply must go through the same backup + timed
  rollback path we use for host network changes.
