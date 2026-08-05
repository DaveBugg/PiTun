# PiTun

**🌐 Languages:** **English** · [Русский](README.ru.md)

> Self-hosted transparent proxy manager for Raspberry Pi 4/5 (and any
> other Linux box). Drops in next to your router, intercepts LAN
> traffic via nftables TPROXY, and routes it through xray-core based
> on your rules — domain, GeoIP, GeoSite, MAC, port, protocol — with a
> web UI.

[![CI](https://img.shields.io/github/actions/workflow/status/DaveBugg/PiTun/ci.yml?branch=master&label=CI)](#)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey)](#)

📸 **Screenshots:** [jump to gallery](#screenshots).

---

## Table of contents

- [What it is](#what-it-is)
- [Why PiTun?](#why-pitun)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Features](#features)
- [Supported protocols](#supported-protocols)
- [Quick start](#quick-start)
- [Server-side proxy install](#server-side-proxy-install)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Development](#development)
- [Tech stack](#tech-stack)
- [Acknowledgements](#acknowledgements)
- [Contributing](#contributing)
- [License](#license)

---

## What it is

PiTun turns a small Linux box into a **transparent proxy gateway** for
your home network. Devices that use the box as their default gateway
have their outbound traffic intercepted at the kernel level, routed
through one of several supported VPN protocols, and either tunnelled,
sent direct, or dropped — all according to rules you set up in the
web UI.

It was built for and primarily tested on the **Raspberry Pi 4 / 5**
(64-bit Raspberry Pi OS), but the project ships **linux/amd64** images
too, so any Intel/AMD mini-PC, NUC, old laptop or x86_64 server that
can run Docker works just as well. Multi-arch images for both
`linux/arm64` and `linux/amd64` are produced by the
[release workflow](.github/workflows/release.yml).

It's designed for the case where you want a single shared exit policy
for the whole house (TVs, phones, IoT) without installing a client app
on every device, and without depending on cloud-managed routers.

**Three proxy endpoints exposed simultaneously, all sharing the same
routing rule set:**

| Endpoint | Default port | Use case |
|---|---|---|
| TPROXY | `7893` | Transparent gateway — devices set the box as gateway |
| SOCKS5 | `1080` | Explicit proxy for browsers and apps |
| HTTP | `8080` | For apps without SOCKS5 support |

## Why PiTun?

A bunch of self-hosted proxy managers already exist:
**OpenWrt + podkop / passwall / passwall2 / homeproxy**, **xKeen** on
Keenetic, **Hiddify**, **Outline**, and others. They're all great at
"drop on a small router, set up bypass, forget it." PiTun is built
around features that **aren't there** in those lighter-weight,
router-bound options:

- **Deploy your own VPN with one click from the UI.** Add your VPS's
  SSH credentials → click "Deploy NaiveProxy / WireGuard / x-ui" →
  PiTun SSHs in, runs the installer, writes the new Node into your
  DB, and your traffic flows through it 30 seconds later. No
  SSH-and-curl ritual. ("VPS" = a small remote Linux server you rent
  for a few dollars a month.) **All your data — credentials, panel
  tokens, rules — stays on your hardware, never in someone's cloud.**

- **Two-hop chains across panels.** Bridge two x-ui panels (a popular
  web dashboard for managing VPN servers) into one chain with managed
  per-channel clients. Per-device traffic hops VPS-A → VPS-B →
  internet. No competing router-based tool does this from one UI.

- **Tunnel one protocol through another, right from the node form.**
  Want your WireGuard node to dial out through a VLESS tunnel? Open
  the WG node's edit form, pick a VLESS node from the "Chain"
  dropdown — done. PiTun wires xray outbounds so the WG handshake
  (and everything that follows) rides on top of VLESS first, then
  hits your WG server. Handy when your VPS provider blocks raw WG.

- **Per-device-group rules.** Create your own group of devices and
  define routing rules just for that group. Examples:
  - "Kids" group → blocks gambling sites and forces traffic through a
    parental-control node.
  - "Work laptop" group → routes corporate domains direct (no VPN),
    everything else via a different node.
  - "Smart TV" group → blocks ads and pushes streaming traffic
    through a specific high-bandwidth node.

  Devices get assigned to a group via a dropdown on the Devices page —
  no per-device app to install on the client side.

- **Auto-rotate the active node on a schedule.** NodeCircle picks the
  next live VPN server every N minutes, pre-pings each one, skips
  dead ones, swaps without dropping your existing connections.

- **Three proxy endpoints, one rule set** — transparent gateway for
  the whole LAN (TPROXY), SOCKS5 for apps that want an explicit proxy,
  HTTP for legacy clients. Most tools force you to pick one.

vs. **router-based packages** (podkop, passwall, passwall2,
homeproxy, xKeen): they're brilliant at "all my devices, minimal
setup" on a $30 router with 64 MB RAM. PiTun is for the case where
you want **server-side orchestration** (deploy & manage your own
VPS), **multi-tier traffic policy** (different rules per device
group), and **a modern web UI with a mobile-friendly layout — tweak
a rule from your phone, even from the bathroom if you must** — at
the cost of needing an RPi 4/5 (or any small Linux box) with 64 GB+
disk.

## Screenshots

<a href="docs/screenshots/dashboard.jpg">
  <img src="docs/screenshots/dashboard.jpg" alt="Dashboard" width="800">
</a>

<details>
<summary><strong>VPS provisioning & x-ui orchestration</strong> (since v1.3.0) — click to expand · 6 screenshots</summary>

<br>

<table>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/servers.jpg"><img src="docs/screenshots/servers.jpg" alt="Servers"></a>
      <p align="center"><sub><b>Servers</b> — VPS inventory, deployment badges (NaiveProxy / WireGuard / x-ui), one-click auto-install via SSH</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/servers_tasks.jpg"><img src="docs/screenshots/servers_tasks.jpg" alt="Server tasks"></a>
      <p align="center"><sub><b>Server tasks</b> — live install logs streamed over WebSocket, status filters, captured tail for finalised jobs</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/xui.jpg"><img src="docs/screenshots/xui.jpg" alt="X-ui Panels"></a>
      <p align="center"><sub><b>X-ui Panels</b> — manage inbounds + clients on registered 3x-ui / x-ui-pro panels, healthcheck, sync, fakesite rotation</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/chains.jpg"><img src="docs/screenshots/chains.jpg" alt="Proxy Chains"></a>
      <p align="center"><sub><b>Proxy Chains</b> — two-hop VLESS+Reality across two x-ui panels, independent channels with their own SNI / Reality keys</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/deploy_modal.jpg"><img src="docs/screenshots/deploy_modal.jpg" alt="Deploy modal"></a>
      <p align="center"><sub><b>Deploy modal</b> — pick protocol (Naive / x-ui / WG), domain + LE email if needed, watch the install stream live</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/chain_healthcheck.jpg"><img src="docs/screenshots/chain_healthcheck.jpg" alt="Chain healthcheck"></a>
      <p align="center"><sub><b>Chain healthcheck</b> — panel API, xray state, inbound presence, relay routing, plus a live <code>testOutbound</code> probe of the relay→exit hop</sub></p>
    </td>
  </tr>
</table>

</details>

<details>
<summary><strong>Routing engine & nodes</strong> — click to expand · 6 screenshots</summary>

<br>

<table>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/nodes.jpg"><img src="docs/screenshots/nodes.jpg" alt="Nodes"></a>
      <p align="center"><sub><b>Nodes</b> — protocols, transports, latency, unified palette pills (protocol blue / transport green / reality purple / tls orange)</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/routing.jpg"><img src="docs/screenshots/routing.jpg" alt="Routing"></a>
      <p align="center"><sub><b>Routing</b> — drag-priority rules, bulk import, V2RayN/Shadowrocket round-trip, multi-tag match-value editor</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/balancers.jpg"><img src="docs/screenshots/balancers.jpg" alt="Balancers"></a>
      <p align="center"><sub><b>Balancers</b> — group nodes with xray's <code>leastPing</code> / <code>random</code> strategy</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/circles.jpg"><img src="docs/screenshots/circles.jpg" alt="Node Circles"></a>
      <p align="center"><sub><b>Node Circles</b> — seamless rotation via xray gRPC API, TCP pre-ping with retry, two-tier auto-failover</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/subscription.jpg"><img src="docs/screenshots/subscription.jpg" alt="Subscriptions"></a>
      <p align="center"><sub><b>Subscriptions</b> — auto-update, per-OS Happ presets, custom UA override</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/geodata.jpg"><img src="docs/screenshots/geodata.jpg" alt="Geo data profiles"></a>
      <p align="center"><sub><b>Geo data</b> — three switchable upstream profiles (Loyalsoldier / runetfreedom / v2fly) + scheduled refresh</sub></p>
    </td>
  </tr>
</table>

</details>

<details>
<summary><strong>Devices, DNS & diagnostics</strong> — click to expand · 4 screenshots</summary>

<br>

<table>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/dns.jpg"><img src="docs/screenshots/dns.jpg" alt="DNS"></a>
      <p align="center"><sub><b>DNS</b> — per-domain rules, FakeDNS pool, query log + stats</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/devices.jpg"><img src="docs/screenshots/devices.jpg" alt="Devices"></a>
      <p align="center"><sub><b>Devices</b> — LAN discovery, OUI vendors, per-device routing policy</sub></p>
    </td>
  </tr>
  <tr>
    <td colspan="2" width="100%">
      <a href="docs/screenshots/diagnostics.jpg"><img src="docs/screenshots/diagnostics.jpg" alt="Diagnostics"></a>
      <p align="center"><sub><b>Diagnostics</b> — DNS reachability, gateway state, xray health, resource snapshot, exportable bundle for bug reports</sub></p>
    </td>
  </tr>
  <tr>
    <td colspan="2" width="100%">
      <a href="docs/screenshots/settings.jpg"><img src="docs/screenshots/settings.jpg" alt="Settings"></a>
      <p align="center"><sub><b>Settings</b> — TPROXY / TUN / DNS / health check / GeoData scheduler / kill switch</sub></p>
    </td>
  </tr>
</table>

</details>

## Architecture

```
                 ┌──────────────────────────────────────────────┐
  Devices  ────► │  PiTun host (RPi / mini-PC)                  │
  (LAN)          │                                              │
                 │  nftables TPROXY :7893                       │
                 │       │                                      │
                 │       ▼                                      │
                 │  xray-core ─┬─ rules (geoip / geosite /      │
                 │             │   domain / IP / MAC / port)    │
                 │             │                                │
                 │             ├─► proxy   (VPN node / chain)   │
                 │             ├─► direct  (home router)        │
                 │             └─► block                        │
                 │                                              │
                 │  + balancer groups (leastPing / random)      │
                 │  + node circles (auto-rotate active node)    │
                 │  + per-domain DNS (plain / DoH / DoT)        │
                 └──────────────────────────────────────────────┘
```

Web UI talks to a FastAPI backend that owns the xray-core process,
the nftables ruleset, and a SQLite database with all configuration.
Frontend is a single-page React app served by nginx.

## Features

**Core**
- Transparent proxy via TPROXY + nftables, no per-device client
- SOCKS5 / HTTP proxies on the LAN
- Optional TUN mode and combined TPROXY+TUN
- QUIC (UDP/443) blocking — forces TCP fallback for protocols TPROXY
  can intercept
- Tunnel chaining — VLESS-inside-WireGuard, etc.
- **Recursive multi-hop chains** — `config_gen` follows `chain_node_id`
  transitively, wiring every hop (exit → mid → entry) with cycle
  detection and a depth cap (WireGuard is exit-hop only)
- **Proxy Chains** (multi-panel, two-hop VLESS+Reality across two
  x-ui panels with independent channels per chain; managed clients
  + per-channel delete + live healthcheck)
- **TLS ClientHello fragmentation (anti-DPI)** — a Settings toggle
  splits the outgoing ClientHello across several packets so a DPI box
  can't match the SNI in a single read; entirely client-side, tunable
  packet mode / length / interval, off by default (needs bundled xray 26.x)
- Kill switch — drop all forwarded traffic if xray crashes

**Routing**
- Rule types: `mac`, `src_ip`, `dst_ip`, `domain`, `port`, `protocol`,
  `geoip`, `geosite`
- Actions: `proxy`, `direct`, `block`, `node:<id>`, `balancer:<id>`
- Drag-and-drop priority, bulk import, V2RayN/Shadowrocket JSON
  round-trip
- Per-MAC overrides ("this device always direct, that one always
  through node #5")
- **Routing Sets** — per-device-group rule lists ("Kids" set blocks
  gambling, "Work" set routes corp domains direct). Assign devices
  individually or in bulk; per-set rules apply first, then fall through
  to global rules. DHCP-resistant (MAC-based via dedicated per-set
  loopback TPROXY ports + xray `inboundTag` matching). Set-aware
  **export/import** — pick scopes, resolve conflicts, import into
  Global / an existing / a new set
- **Route Explainer** — paste a domain, URL or IP and see which rule
  wins and whether it exits proxied, direct or blocked

**Health & resilience**
- Background liveness probe with two-tier auto-failover: if the failed
  node belongs to an enabled NodeCircle, the failover handler delegates
  recovery to the circle (which skips dead siblings via pre-ping +
  retry); otherwise it walks a configurable fallback list
- **Unified speed test** — gates on reachability first (Google /
  Cloudflare 204 with a retry, so a dead node fails in ~1s instead of
  grinding every fallback), then measures the **average after warm-up
  plus the peak**; streams live, persists both numbers, and flags a
  reading older than 6h as stale
- **Automatic background speed checks** — sweep a chosen scope (all /
  a subscription / a group / specific nodes) on an interval so `best` /
  `min_speed` and the UI stay fresh without manual testing; sequential,
  staleness-guarded, with per-node error isolation
- One-tap **reachability check** — confirms a node actually carries
  traffic to the internet (204 through the live tunnel), separate from
  raw link speed
- Naive sidecar supervisor — auto-restarts crashed Naive containers
  with a sliding-window rate limiter
- Recent Events feed on Dashboard surfaces failovers, sidecar
  restarts, geo updates, circle rotations

**Balancing & rotation**
- Balancer groups (xray's `leastPing` or `random` strategies)
- Node Circles — schedule seamless active-node rotation via xray's gRPC
  API (no dropped connections). A **`best`** mode plus **`max_latency_ms`**
  and **`min_speed_mbps`** candidate filters pick from real speed data
  (never-tested nodes get the benefit of the doubt), and a **smart-skip**
  keeps a scheduled rotation from moving off a healthy, low-latency node
  — manual "rotate now" always rotates

**Subscriptions**
- Periodic refresh from VLESS / VMess / Trojan / SS / Hysteria2 /
  Clash YAML / xray JSON subscription URLs
- **User-Agent templates** — an editable table (add / edit / delete)
  replacing the old hardcoded presets; each row carries a UA string and
  optional custom request headers, with catalogue export / import
- Optional **GeoIP country flags** on imported node names (`🇳🇱 vless-nl`)
  — opt-in and licence-clean (drop a MaxMind `GeoLite2-Country.mmdb`
  next to the geo data; absent it's a silent no-op)
- Optional regex filter, configurable interval

**Devices & DNS**
- LAN discovery via `arp-scan`, OUI vendor lookup
- Per-device routing policy (default / always-include / always-bypass)
- Per-domain DNS rules (plain, DoH, DoT)
- FakeDNS pool for sniffing-friendly geoip resolution
- DNS query log with stats
- **Host network controls** — change the box's own gateway / DNS from
  Settings → Network with an auto-backup + one-click rollback; warns on
  a routing self-loop (gateway pointing at the box itself) or a
  double-hop through another PiTun

**Servers & deployments**
- Inventory of remote VPS hosts (host, SSH credentials, tags) separate
  from runtime nodes — async-SSH probe, deployment records track which
  protocol/port is set up on which box, optional manual provisioning
  scripts (Caddy + naive, xray, SSH hardening) over the same SSH link
- One-click auto-deploy over SSH for **NaiveProxy**, **WireGuard**,
  **x-ui** (3x-ui / x-ui-pro) — live log streaming, status badges,
  cascade-clean on uninstall
- **Per-page Direct switch** — every server / panel / chain SSH
  operation runs *through the active node* by default (the same tunnel
  the LAN uses); a Direct toggle flips one operation back to a straight
  dial for reaching a box while the active node is down
- Dedicated **X-ui Panels** page — full inbound + client management
  (6 wired presets covering Reality / TLS / domain modes), live
  healthcheck (panel API, xray, nginx, UFW, TLS cert, disk, mem),
  cache↔panel sync for hand-added clients, random / custom
  fakesite rotation
- **SNI / REALITY-dest scanner** in the node form — probe a candidate
  host for TLS 1.3 + HTTP/2 before saving it as the masquerade target

**Operations**
- One-click GeoIP / GeoSite refresh — three switchable upstream
  profiles: Loyalsoldier (CN-focused community list), runetfreedom
  (Russian-internet curated list), v2fly (vanilla baseline)
- **In-UI self-update** — Settings → Updates checks GitHub (through the
  active node, so a throttled direct route isn't a blocker), shows
  what's new and applies it with live progress via a host-side agent
- Full-fidelity JSON Export/Import for Nodes and Servers — versioned
  bundle envelope, append/replace modes, optional secret redaction
  (separate from URI/subscription import which is single-node only)
- Plain-text URI export (`.txt`, one `vless://…` per line) — share
  your node list with any v2rayN-compatible client; symmetric
  `Import` button auto-detects URI list vs JSON bundle. Single-node URI
  export straight from a node card too
- **Login lockout** — five consecutive failed logins lock an account
  for 15 minutes (HTTP 429 + `Retry-After`); the primary LAN-only
  brute-force guard
- Built-in diagnostics page (DNS reachability, gateway, xray status,
  resource usage)
- Streaming xray log viewer
- Multi-language UI (English / Russian)

## Supported protocols

| Protocol | Notes |
|---|---|
| **VLESS** | Plain, TLS, REALITY, XTLS Vision, with WebSocket / gRPC / xhttp / HTTP/2 / HTTPUpgrade / mKCP / QUIC transports |
| **VMess** | Same transport menu as VLESS |
| **Trojan** | TLS / WebSocket / gRPC / xhttp |
| **Shadowsocks** | All modern stream / AEAD ciphers |
| **WireGuard** | Native xray outbound; works inside chains |
| **Hysteria2** | UDP, with optional obfuscation password |
| **SOCKS5** | As outbound (e.g. for chaining) |
| **NaiveProxy** | Per-node sidecar container (Caddy + forwardproxy on the server side); xray connects via local SOCKS5 |

## Quick start

### System requirements

| Resource | Minimum | Recommended |
|---|---|---|
| **CPU** | 64-bit ARM (RPi 4) or x86_64, 4 cores | RPi 5 / any modern x86_64 mini-PC |
| **RAM** | 1 GB | 2 GB+ (helps with naive sidecars and large geo data refresh) |
| **Disk** | 4 GB free | 8 GB+ (docker images + DB growth + DNS query log) |
| **Network** | 1 LAN interface, static IP, wired preferred | 1× wired GbE for LAN |
| **OS** | Any modern 64-bit Linux with kernel ≥ 5.4 (TPROXY support) | Raspberry Pi OS 64-bit, Debian 12+, Ubuntu 22.04+ |
| **Architectures** | `linux/arm64` *(RPi 4/5)* · `linux/amd64` *(Intel/AMD mini-PC, NUC, x86_64 server)* | — |

### Prerequisites

- One of the supported architectures above
- Docker + Docker Compose v2
- Root access on the host (nftables + raw socket binding)
- A static LAN IP for the host

### Install — one-liner

The simplest install is a single command that downloads everything,
prepares the host, and brings up the stack. It pulls pre-built images
from the latest GitHub Release, so no Docker build runs locally —
total time is ~5 minutes on a fresh RPi, and the install resumes
cleanly if the connection drops mid-way (every download is retried
and atomically renamed).

```bash
curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh | sudo bash
```

> **Heads up — passing flags to a piped script.** The `--flag` arguments
> below need to reach our installer, not bash. There are three working
> forms; pick the one that's hardest to mistype:
>
> **(A) Foolproof — download then run:**
> ```bash
> curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
>      -o /tmp/pitun-install.sh
> sudo bash /tmp/pitun-install.sh --version v1.4.12
> ```
>
> **(B) Pipe with `bash -s --` separator** (the `-s --` is **required**):
> ```bash
> curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
>      | sudo bash -s -- --version v1.4.12
> ```
>
> **(C) Environment variable** (no `-s --` voodoo needed):
> ```bash
> curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
>      | sudo PITUN_VERSION=v1.4.12 bash
> ```
>
> ❌ **Do NOT do this:** `curl ... | sudo bash --version v1.4.12` — bash
> swallows `--version` as its own flag (prints bash's version + exits)
> before our installer ever runs. Common copy-paste trap.

Useful flags (work via any of the three forms above; examples use form B):

```bash
# Pin a specific version (example tag — see GitHub Releases for the latest)
... | sudo bash -s -- --version v1.4.12

# Force rebuilding from source (no published release available, or
# you're testing local changes). Slower, needs reliable internet
# during the docker build.
... | sudo bash -s -- --build

# Air-gapped / hybrid offline install — point at a directory containing
# pre-downloaded artifacts. ANY file present in the directory is used
# as-is; missing ones fall back to a normal download (hybrid mode).
# Also auto-detected when install.sh is launched from a directory that
# already has any of the six expected filenames sitting next to it —
# no --offline flag needed in that case. Full instructions and the
# exact file list: docs/INSTALL_OFFLINE.md.
... | sudo bash -s -- --offline /tmp/pitun-artifacts

# Custom install path (default: /opt/pitun)
... | sudo bash -s -- --dir /srv/pitun

# Just preview what it would do — no changes made
... | sudo bash -s -- --dry-run

# Force IPv4 is the default. If you have a v6-only network, opt in:
... | sudo bash -s -- --ipv6

# Recover from a stale kill-switch lockup. ONLY when:
#   - a previous PiTun run died with kill_switch=true active, AND
#   - curl from this host now hangs on the very first download.
# On a HEALTHY install with kill_switch + a running backend, omit
# this flag — the install works over xray's normal bypass path.
# The pre-flight detects + warns automatically; re-run with the flag
# only if it suggests so. See "Troubleshooting" below for details.
... | sudo bash -s -- --fix-blockers
```

After the script finishes:
- Web UI is at `http://<this-host-ip>/`, login `admin` / `password`
  (**change it on first login** via *Settings → Account*).
- `/opt/pitun/.env` was generated with a random `SECRET_KEY` and the
  network block autodetected from your default-route interface:
  `INTERFACE`, `LAN_CIDR`, `GATEWAY_IP` (the PiTun host's own LAN IP),
  `VITE_API_BASE_URL`, `VITE_WS_BASE_URL`, `CORS_ORIGINS`. Verify with
  `head -30 /opt/pitun/.env` before going to production; if anything
  looks off, edit and `docker compose -f /opt/pitun/docker-compose.yml restart`.

> See [`install.sh --help`](install.sh) for the full option list.

### Install — git clone path

If you want the source tree alongside the running stack (e.g. for
development, or to apply patches before deploy), the classic path
still works:

```bash
git clone https://github.com/DaveBugg/PiTun pitun
cd pitun

# Host bootstrap: installs Docker (if missing), xray-core, GeoIP/GeoSite,
# system packages, kernel modules, sysctl tweaks, log rotation, daily
# cleanup cron. Skip if you'd rather do it manually — see "Manual install"
# below.
sudo bash scripts/setup.sh

cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, INTERFACE, LAN_CIDR,
# GATEWAY_IP (the PiTun host's own LAN IP — what devices will use as
# their default gateway). A random SECRET_KEY: openssl rand -hex 32
#
# Tip: instead of editing manually, run `sudo bash install.sh
# --skip-host-prep` from the same checkout — it autodetects all the
# network values from your default-route interface and writes them
# into .env (only on first generation).

docker compose up -d --build
```

The web UI listens on the host's LAN IP, port 80. Default login is
`admin` / `password` — **change it on first run** via *Settings → Account*.

### Manual install (without `setup.sh`)

If you'd rather wire the host yourself, here's the equivalent checklist.
Everything below must be done **before** `docker compose up`:

```bash
# 1. System packages
sudo apt update
sudo apt install -y curl wget ca-certificates nftables iproute2 \
    net-tools iptables arp-scan dnsutils unzip jq cron

# 2. Free UDP/5353 (PiTun's DNS port)
sudo systemctl stop avahi-daemon avahi-daemon.socket || true
sudo systemctl disable avahi-daemon avahi-daemon.socket || true
sudo systemctl mask avahi-daemon || true

# 3. Sysctl: IP forwarding + TPROXY loopback
sudo tee /etc/sysctl.d/99-pitun.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.route_localnet = 1
EOF
sudo sysctl --system

# 4. TPROXY kernel modules (load now + pin for next boot)
sudo modprobe nft_tproxy xt_TPROXY
echo -e "nft_tproxy\nxt_TPROXY" | sudo tee /etc/modules-load.d/pitun.conf

# 5. Docker + Compose v2 (skip if already installed)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # then log out + back in

# 6. GeoIP/GeoSite databases (bind-mounted RW into the backend container
#    so the user can refresh them from the UI without an image rebuild).
#    The xray binary itself is bundled inside the backend image as of
#    v1.2.0 — no separate host install needed.
sudo mkdir -p /usr/local/share/xray
sudo curl -fsSL -o /usr/local/share/xray/geoip.dat   https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
sudo curl -fsSL -o /usr/local/share/xray/geosite.dat https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat

# 7. Static IP on the LAN interface (use NetworkManager, dhcpcd, or netplan
#    depending on your distro; not scripted because the right tool varies).

# 8. Now you can deploy
cp .env.example .env && $EDITOR .env
docker compose up -d --build
```

> **Why the geo databases are on the host, not inside the image.**
> `geoip.dat` and `geosite.dat` are refreshable from the UI
> (*GeoData → Update*). Keeping them as a host bind-mount means a
> single `curl` updates them in place — no image rebuild required.
> The xray binary itself, by contrast, is baked into the backend image
> as of v1.2.0 (used to be a host install). One less host-side
> prerequisite, version pinned to the release tag.

### Pre-built images

The CI release workflow publishes loadable Docker tarballs (linux/amd64
and linux/arm64) as GitHub Release assets. Useful for air-gapped /
factory-fresh RPi installs:

```bash
# On a machine with internet
curl -LO https://github.com/DaveBugg/PiTun/releases/download/vX.Y.Z/pitun-backend-vX.Y.Z-arm64.tar.gz
curl -LO https://github.com/DaveBugg/PiTun/releases/download/vX.Y.Z/pitun-frontend-vX.Y.Z.tar.gz

# Transfer to the host, then:
docker load < pitun-backend-vX.Y.Z-arm64.tar.gz
tar -xzf pitun-frontend-vX.Y.Z.tar.gz -C frontend/dist/
docker compose up -d
```

### Setup scripts

For RPi-specific bootstrap (first boot, OS-level dependencies, network
config) `scripts/` ships with helpers — see [scripts/README.md](scripts/README.md).

## Server-side proxy install

Once your PiTun box is up and you've added a VPS under **Servers**, the
**Install** button (rocket icon) lets you provision the upstream proxy
*on the VPS itself* over SSH, with the install log streamed live in the
modal. Three protocols are supported:

| Protocol | Topology | Where the credentials live |
|---|---|---|
| **NaiveProxy** | Single TLS tunnel per server | One `Node` row per deploy |
| **WireGuard** | Multi-client (one server, N peers) | One `DeploymentClient` row per peer; export selected peers to `Node` rows on demand |
| **x-ui** | Full 3x-ui / x-ui-pro panel (many inbounds + many clients per inbound) | Panel rows under `XuiServer`; each inbound's clients live on the panel itself, surfaced + cached by PiTun |

> The 443 slot — NaiveProxy and **x-ui-pro** can't coexist on one VPS
> (both want :443 with Let's Encrypt). Bare-mode x-ui (no domain →
> panel on a random high port) IS compatible with NaiveProxy. PiTun
> enforces this slot rule at deploy time with a clear error message.

### NaiveProxy

Form: domain (with an A-record pointing at the VPS), Let's Encrypt email,
optional Caddy basic-auth user/password. Behind the scenes:

1. PiTun uploads `scripts/setup-naive-server.sh` to the VPS.
2. The script installs Caddy + the `caddyserver/forwardproxy` plugin via
   `xcaddy`, fetches a TLS cert, writes a Caddyfile, enables the
   service.
3. On success the script emits `URI=naive+https://…` on its last line;
   PiTun parses that, creates a `Node` row, and the deploy is done.

A second deploy on the same server overwrites the existing
`ServerDeployment` row; the linked `Node` is left intact (you can
recreate it from the deployment if it was deleted).

### WireGuard

Form: first-client name (defaults to `client1`), UDP port (default
51820), DNS servers, AllowedIPs. The install script
(`scripts/setup-wireguard-server.sh`) is sub-command driven:

| Sub-command | Used by | What it does |
|---|---|---|
| `install` | Initial deploy | apt, sysctl, generate server keypair, write `wg0.conf`, enable `wg-quick@wg0`, add the first peer |
| `add-client` | "Add" in the Clients modal | New keypair + `wg syncconf` reload (no tunnel restart) |
| `remove-client` | "Remove" in the Clients modal | Strip peer + `wg syncconf` reload |
| `list-clients` | "Sync" in the Clients modal | List current peer names + pubkeys |
| `get-conf` | "Download .conf" | Re-print the cached INI for one client |

**Clients are a separate layer.** The Servers page shows a `Users`
icon next to any server with a WG deployment — that opens the
**Clients** modal where you:

- **Add** a peer (creates a new keypair on the VPS + caches the priv
  key locally so we can render a `.conf` for download).
- **Sync** to reconcile against the VPS — peers added manually on the
  server show up; peers deleted on the server are flagged `orphan`
  *but not auto-deleted from PiTun*, because the admin may have linked
  them to Nodes.
- **Download .conf** for QR-coding into a phone.
- **Export to Node** to actually route traffic through this peer
  (creates a `Node` row referencing the `DeploymentClient`; the Nodes
  page renders "from `<server name>`" alongside the row).
- **Remove** to strip the peer from the server AND delete the
  `DeploymentClient` row. Any Nodes that were exported from it stay
  but get an `orphan` badge so the admin sees the upstream is gone.

One VPS can host clients used by multiple PiTun instances — each PiTun
sees the peers it added itself + any it imported via Sync.

### x-ui

Form: optional domain (empty → bare-mode panel on a random high port,
self-signed cert; non-empty → xui-pro stack with nginx + Let's Encrypt
on :443) and optional LE registration email (defaults to
`admin@<apex-domain>`). Behind the scenes
(`scripts/setup-xui-server.sh`):

1. Installs upstream **3x-ui v3.1.0** in `--non-interactive` mode.
2. For xui-pro: also installs nginx + certbot + the
   [`GFW4Fun/randomfakehtml`](https://github.com/GFW4Fun/randomfakehtml)
   archive of fakesite templates, picks one at random, and wires the
   reverse-proxy `externalProxy: 443` block.
3. Generates fresh admin user/password + a Bearer API token + panel
   basepath + sub-port, then emits `URI=xui://…` so PiTun creates the
   `XuiServer` row + sets up the Bearer-authenticated client.
4. Runs `cleanup-go.sh` to remove the Go SDK / build cache the
   installer left behind (~2.5 GB on a 10 GB VPS).
5. Applies the VPS optimisation profile (BBR + sysctl + swap +
   ulimits + logrotate).

Once registered, every inbound + client on the panel becomes
manageable from the dedicated **Панели X-ui** page in the UI:
6 wired-in inbound presets (VLESS+Reality / VLESS+xhttp+Reality /
VLESS+WS+TLS / VLESS+xhttp+TLS / Trojan+gRPC / SOCKS5), per-client
export to `Node` rows (cache-backed, idempotent on re-export),
multi-layer healthcheck, cache↔panel sync that picks up clients
added directly via the panel UI, and (xui-pro only) fakesite
rotation — random pick from the bundled archive or upload a custom
`.zip`. Chain orchestration (see **Proxy Chains** below) glues two
registered panels into a two-hop VLESS+Reality tunnel.

## Configuration

All runtime config goes through the web UI. The only settings that
must be set before first start, via `.env`:

| Variable | Default | What |
|---|---|---|
| `SECRET_KEY` | `changeme-…` | JWT signing key — `openssl rand -hex 32` |
| `INTERFACE` | `eth0` | LAN interface name on the host |
| `LAN_CIDR` | `192.168.1.0/24` | Your LAN subnet (autodetected by `install.sh`) |
| `GATEWAY_IP` | `192.168.1.100` | **The PiTun host's own LAN IP** — devices set this as their default gateway. (Misnomer kept for backward compat; *not* the router's IP.) Autodetected by `install.sh`. |
| `BACKEND_PORT` | `8000` | Backend listen port (behind nginx) |
| `TPROXY_PORT_TCP` | `7893` | TPROXY TCP listener |
| `DNS_PORT` | `5353` | Internal DNS forwarder port |
| `NAIVE_PORT_RANGE_START` | `20800` | Allocator range for naive sidecars |
| `NAIVE_IMAGE` | `pitun-naive:latest` | Image tag built locally or loaded from release |

Full annotated example: [`.env.example`](.env.example).

> **About `GATEWAY_IP`:** the variable name predates the LAN-gateway
> feature and refers to the PiTun host itself, not your home router.
> If the .env value disagrees with the actual interface IP, the backend
> auto-syncs the live IP into the database on the first `GET /settings`,
> so the UI always shows the truth. `LAN_CIDR` has the same runtime
> fallback as of 1.2.3.

## Troubleshooting

### `curl` hangs on every install / upgrade — stale kill-switch

**Symptom.** You run `install.sh` and the very first download
(`api.github.com/.../releases/...`) hangs forever (~75 s before
TCP gives up). You can `ping 8.8.8.8` from the host but `curl
https://api.github.com` doesn't return.

**Cause.** PiTun's `kill_switch=true` mode installs an
`inet pitun` nftables table + `ip rule fwmark 0x1 lookup 100`
policy route that TPROXYs all non-bypass traffic to xray on
`127.0.0.1:7893`. If the backend dies with that protection still
active (crash, OOM, manual `docker compose down`), the rules stay
in the kernel but xray isn't there to receive — every outbound
packet drops silently. Even the installer.

**Auto-detection.** Since v1.3.0-beta.3, `install.sh` checks for
this state at startup and prints a clear warning if it finds stale
rules without a running `pitun-backend` container. If you see:

```
[WARN]  ════════════════════════════════════════════════════════════════════
[WARN]    Detected stale kill-switch state on this host:
[WARN]      * 'inet pitun' nftables table is present but backend is down
[WARN]      * 'ip rule fwmark 0x1 lookup 100' policy route is present
…
```

…re-run with the `--fix-blockers` flag (or `PITUN_FIX_BLOCKERS=1`):

```bash
sudo bash /tmp/pitun-install.sh --version v1.3.0 --fix-blockers
```

It will flush the stale rules before any download, then proceed.
The backend will reinstall them at startup if needed.

> **When NOT to use `--fix-blockers`:** on a healthy install where
> `pitun-backend` is currently `Up`, kill-switch is doing its job
> and traffic flows fine through xray's bypass path. The installer
> auto-detects this case and leaves nftables alone — no flag
> needed. Adding the flag anyway just causes a brief LAN exposure
> while you re-stack.

### `curl` hangs but no kill-switch state

If the install hangs on a fresh host (no PiTun previously installed)
and `--fix-blockers` doesn't help, suspect IPv6. Some Debian 13 RPi
images and a number of VPS providers have advertised but unroutable
IPv6 paths to GitHub. Since v1.3.0-beta.3 the installer defaults to
IPv4 (`-4`) — but if you've passed `--ipv6` or set
`PITUN_FORCE_IPV6=1`, drop those.

### Backend container crash-loops with `bad marshal data` or `Segmentation fault`

If you upgraded to v1.3.0-beta.1 or .2 (via the original beta release
artifacts) on an arm64 device — particularly **RPi 4 (Cortex-A72)** —
the backend container will crash-loop with one of:

```
ValueError: bad marshal data (unknown type code)
```
or, worse, a silent `Segmentation fault (core dumped)` at
`import uvicorn`. Root cause was a CI-side QEMU cross-build
producing arm64 wheels with corrupted bytecode caches.
**Fixed in v1.3.0-beta.3** via Dockerfile (`PYTHONDONTWRITEBYTECODE=1`
+ post-install `*.pyc` strip) and a switch to native arm64 runners
in CI. Just re-run the installer with `--version v1.3.0-beta.3` (or
later); the new image loads cleanly on every Cortex-A72 / A76 we've
tested.

### Banner: `xray validation failed: (empty stderr)` after upgrade

**Symptom.** Right after a fresh `install.sh` run the dashboard
displays a red banner *"Валидация конфигурации Xray не прошла /
xray validation failed: (empty stderr)"*. Backend logs show
`xray process died unexpectedly (rc=-11)` (SIGSEGV, exit 139)
and `Auto-restart aborted: config verification failed:` with no
stderr — `xray -test -config …` segfaults before it can print
anything.

**Cause.** The bundled `xray` binary inside the freshly-loaded
Docker image is bit-corrupted. The release tarball on GitHub is
fine (verified by per-arch sha256 pinning at build time, since
v1.3.0-beta.5), but `docker load < tarball` writes layer files
to local storage, and on a flaky SD card / SSD or with active
ext4 metadata corruption, individual bytes flip silently. The
binary still has a valid ELF header (so `file` reports it as a
plain executable), but executing instructions land on garbage
addresses → segfault.

> **RPi 4 / 5 with a USB3-connected SSD?** The most common cause
> is the **UAS** USB-storage protocol corrupting bytes on heavy
> writes — not actual disk damage. Skip directly to
> [RPi 4 / 5 with USB-SATA / USB-NVMe SSD: disable UAS](#rpi-4--5-with-usb-sata--usb-nvme-ssd-disable-uas)
> for the one-time kernel-cmdline fix.

**Auto-detection.** Since v1.3.0-beta.5, `install.sh` re-runs
`docker load` up to 3 times and verifies the bundled xray's
sha256 against a pinned digest after each attempt. If all three
fail, the installer aborts with a pointer to this section
instead of leaving you with a non-bootable stack.

**Recovery.**

1. **Verify the diagnosis** — confirm the binary is corrupted
   (and isn't just a config error):
   ```bash
   docker run --rm --entrypoint sha256sum pitun-backend:latest \
     /usr/local/bin/xray
   ```
   Compare to the pinned values in `backend/Dockerfile`
   (`XRAY_SHA256_AMD64` / `_ARM64` / `_ARM`). If they don't
   match, you've hit storage corruption.

2. **Check the filesystem** — the most common root cause is
   ext4 hash-tree (htree) corruption; one or more of:
   ```bash
   sudo dumpe2fs -h /dev/<rootdev> | grep -E 'state|First error|Last error'
   sudo dmesg -T | grep -iE 'ext4|mmc|sd|ata'
   sudo smartctl -a /dev/<rootdev>   # for SSD/NVMe
   ```
   `Filesystem state: clean with errors` plus recent
   `EFSCORRUPTED` events is a strong signal.

3. **Repair** — schedule an fsck on the next boot:
   ```bash
   sudo touch /forcefsck
   sudo tune2fs -c 1 /dev/<rootdev>
   sudo reboot
   ```
   On a Pi, the boot will pause for fsck (1–10 min depending on
   disk size and damage). After the reboot, `dumpe2fs` should
   show `Filesystem state: clean` with no error timestamps.

4. **Recover the data** — `install.sh` makes a pre-upgrade
   SQLite snapshot at `/opt/pitun/data-backup-pre-vX.Y.Z-*.db`
   on every run. Compose's `data:/app/data` bind mount also
   keeps the live DB at `/opt/pitun/data/pitun.db`. Either is a
   safe starting point if the running stack got into a bad
   state.

5. **Re-install** — wipe `/var/lib/docker/{overlay2,image,containers}/*`
   while the daemon is stopped (orphaned layers from the
   corrupted attempt won't auto-clean), then run `install.sh
   --version vX.Y.Z` again. The retry loop in step 2 of the
   installer should now pass.

6. **If it keeps recurring** — the storage hardware is dying.
   Back up `/opt/pitun/data` + `/opt/pitun/.env` to another
   machine and swap the SD card / SSD before re-installing.
   RPi 4 has no ECC RAM and SD cards are notoriously prone to
   silent bit-rot; an M.2 SSD via USB3 is much more reliable.

### RPi 4 / 5 with USB-SATA / USB-NVMe SSD: disable UAS

**Who this affects.** Raspberry Pi 4 / 5 owners running root from an
SSD (or plain HDD) connected via a USB3 → SATA / NVMe adapter — i.e.
*not* SD card, *not* PCIe-direct NVMe HAT. Common culprits are
ASMedia bridges (`174c:1051`, `174c:1153`, `174c:1156`, `174c:55aa`)
and JMicron (`152d:0578`, `152d:1561`, `152d:583*`). Check yours:

```bash
lsusb | grep -i -E 'asmedia|jmicron|realtek'
lsusb -t   # look for "Driver=uas" — that's the trouble signal
```

**Why it breaks `install.sh`.** Most cheap USB-SATA bridges on Linux
default to **UAS** (USB Attached SCSI) for speed, but several bridge
+ Pi-firmware combinations silently flip bytes during heavy single-
stream writes — exactly the workload of `docker load < pitun-backend-
*.tar.gz` (90 MB+). The result: bytes inside the bundled `xray`
binary get corrupted on disk, the binary segfaults on first use,
and the dashboard greets you with **"xray validation failed: (empty
stderr)"**. SMART says the SSD is fine; the bridge / driver is the
culprit. Forcing the slower **BOT** (Bulk-Only Transport) protocol
trades ~20 % throughput for rock-solid integrity.

**One-time fix (kernel cmdline quirk).** Replace `<VID:PID>` with
your bridge's IDs from `lsusb`. The example below is for an Argon
ONE M.2 + ASM1156 (`174c:1156`):

```bash
# 1. Find your bridge VID:PID
lsusb -t                          # confirms "Driver=uas"
lsusb | grep -i -E 'asmedia|jmicron|realtek'
#   →  Bus 002 Device 002: ID 174c:1156 ASMedia Technology Inc. ...
VIDPID=174c:1156                  # ← put YOUR VID:PID here

# 2. Backup the current cmdline + insert the quirk
sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak
sudo sed -i "1s|^|usb-storage.quirks=${VIDPID}:u |" /boot/firmware/cmdline.txt
cat /boot/firmware/cmdline.txt    # verify

# 3. Reboot
sudo reboot

# 4. After reboot — verify the driver flipped to usb-storage (not uas)
lsusb -t                          # expect "Driver=usb-storage"
sudo dmesg | grep -i 'uas is ignored'
#   →  usb 2-2: UAS is ignored for this device, using usb-storage instead
```

After that, re-run `install.sh --version vX.Y.Z` — the new
load-time sha verification (since v1.3.0-beta.5) will pass on the
first attempt and any subsequent upgrade will be stable.

**To revert** (if your bridge actually works fine on UAS and you
want the speed back), restore `/boot/firmware/cmdline.txt.bak` over
`cmdline.txt` and reboot.

> The quirk only disables UAS for the matching VID:PID; other USB
> mass-storage devices on the same Pi (USB stick, second drive)
> are unaffected.

## Uninstall

To completely remove PiTun from the host:

```bash
# Interactive — asks before host-level operations:
sudo bash /opt/pitun/scripts/uninstall.sh

# Headless re-image prep — wipes everything including host tweaks:
sudo bash /opt/pitun/scripts/uninstall.sh --purge

# Preview what would be removed, change nothing:
sudo bash /opt/pitun/scripts/uninstall.sh --dry-run

# Preserve DB + configs for a future reinstall:
sudo bash /opt/pitun/scripts/uninstall.sh --yes --keep-data
```

The uninstaller handles every installer permutation we ship —
registry-pulled images, locally-built (`--build`), offline bundles,
the dev compose stack, dynamic naive sidecars, hot-deploy backup
dirs. It's idempotent (re-running on a cleaned host downgrades
missing artefacts to "skip") and safe by default (asks before
nftables / sysctl / DNS / swap / host network changes).

Notable flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Preview only — no changes. |
| `-y` / `--yes` | Skip prompts on standard removals. |
| `--purge` | Everything, including host network config. |
| `--keep-data` | Preserve DB + configs (`data/` survives). |
| `--keep-network` | Never touch network manager files. |
| `--keep-xray` | Leave `/usr/local/bin/xray` + geo data alone. |

See [`scripts/README.md`](scripts/README.md#uninstall) for the
full list and per-flag rationale. **Phase 7 (host network)** is
the only HIGH-RISK step — it can break SSH if PiTun's IP was set
via the Settings UI. Open a second SSH session before confirming
when remote.

## Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
python -m pytest tests/ -q

# Frontend
cd frontend
npm ci
npm run dev          # http://localhost:5173
npm run build        # tsc + vite (catches type errors)
npm run test:ci
npm run lint
```

The full Docker stack lives in `docker-compose.yml`. For local UI work
without RPi-specific bits (TPROXY, nftables) you can skip Docker — auth,
nodes, routing rules and most of the UI work fine on macOS/Windows
against a backend running on `localhost:8000`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for PR conventions and code
style.

## Tech stack

**Backend** — Python 3.11, FastAPI, SQLModel/SQLAlchemy, Alembic,
Pydantic v2, Uvicorn, httpx, aiohttp, aiosqlite, bcrypt, python-jose,
psutil, docker-py, PyYAML.

**Frontend** — React 19, TypeScript, Vite, Tailwind CSS 3, TanStack
Query (React Query) v5, Zustand, React Router 6, Recharts, Lucide
React, axios, clsx, tailwind-merge.

**Infrastructure** — Docker + Compose, nginx (frontend), Tecnativa's
docker-socket-proxy (read-only Docker API access from the backend),
nftables, systemd.

**Testing** — pytest, Vitest, Testing Library.

## Acknowledgements

PiTun is glue code on top of mature, hard-to-replicate upstream
projects. Without them, none of this would exist:

### Proxy / network core

- **[XTLS/Xray-core](https://github.com/XTLS/Xray-core)** — the actual
  proxy engine. PiTun manages an xray-core process, generates its
  config, and talks to its gRPC API.
- **[klzgrad/naiveproxy](https://github.com/klzgrad/naiveproxy)** —
  Chromium-based HTTPS-tunnelling proxy used as a per-node sidecar.
  PiTun's `docker/naive/` builds an image from upstream releases.
- **[Caddy](https://caddyserver.com/)** with **[caddyserver/forwardproxy](https://github.com/caddyserver/forwardproxy)**
  (klzgrad's fork) — recommended NaiveProxy server. `scripts/setup-naive-server.sh`
  builds it via [`xcaddy`](https://github.com/caddyserver/xcaddy).
- **[MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui)** — the upstream
  x-ui panel (v3.1.0). PiTun's "bare" x-ui mode auto-installs it and
  manages its inbounds/clients via the panel API.
- **[GFW4Fun/x-ui-pro](https://github.com/GFW4Fun/x-ui-pro)** — domain
  + nginx + LE fork of 3x-ui used in PiTun's "xui-pro" mode and as
  relay/exit nodes in Proxy Chains.
- **[GFW4Fun/randomfakehtml](https://github.com/GFW4Fun/randomfakehtml)**
  — fakesite templates bundled into xui-pro installs, also driven by
  the in-app "rotate fakesite" feature.
- **[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)**
  — GeoIP / GeoSite rule databases used by xray's `geoip:` / `geosite:`
  matchers. PiTun pulls the latest `geoip.dat` and `geosite.dat` from
  here.
- **[MaxMind GeoLite2](https://www.maxmind.com/en/geolite2/)** —
  GeoIP-MMDB lookups (optional, opt-in).
- **[netfilter / nftables](https://www.netfilter.org/projects/nftables/)**
  — kernel-side TPROXY interception.
- **[arp-scan](https://github.com/royhills/arp-scan)** — LAN device
  discovery.

### Backend

- **[FastAPI](https://github.com/tiangolo/fastapi)** — HTTP framework
- **[SQLModel](https://github.com/tiangolo/sqlmodel)** + **[SQLAlchemy](https://www.sqlalchemy.org/)** — ORM
- **[Pydantic](https://github.com/pydantic/pydantic)** — validation
- **[Alembic](https://github.com/sqlalchemy/alembic)** — migrations
- **[Uvicorn](https://github.com/encode/uvicorn)** — ASGI server
- **[httpx](https://github.com/encode/httpx)** + **[aiohttp](https://github.com/aio-libs/aiohttp)** — HTTP clients
- **[asyncssh](https://github.com/ronf/asyncssh)** — async SSH client used for VPS auto-deploy and remote diagnostics
- **[websockets](https://github.com/python-websockets/websockets)** — install-log streaming
- **[aiosqlite](https://github.com/omnilib/aiosqlite)** — async SQLite
- **[python-jose](https://github.com/mpdavis/python-jose)** + **[bcrypt](https://github.com/pyca/bcrypt/)** — auth
- **[psutil](https://github.com/giampaolo/psutil)** — host metrics
- **[docker-py](https://github.com/docker/docker-py)** — Docker API client (Naive sidecar lifecycle)
- **[PyYAML](https://pyyaml.org/)** — Clash YAML import

### Frontend

- **[React](https://react.dev/)**, **[Vite](https://vitejs.dev/)**,
  **[TypeScript](https://www.typescriptlang.org/)**
- **[Tailwind CSS](https://tailwindcss.com/)** — styling
- **[TanStack Query](https://tanstack.com/query)** — server state
- **[Zustand](https://github.com/pmndrs/zustand)** — UI state
- **[React Router](https://reactrouter.com/)** — routing
- **[Recharts](https://recharts.org/)** — metrics charts
- **[Lucide](https://lucide.dev/)** — icons
- **[axios](https://github.com/axios/axios)** — HTTP client
- **[Vitest](https://vitest.dev/)** + **[Testing Library](https://testing-library.com/)** — tests

### Infrastructure

- **[Docker](https://www.docker.com/)** + **[Compose](https://docs.docker.com/compose/)**
- **[nginx](https://nginx.org/)** — frontend serving + WebSocket proxy
- **[Tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)**
  — locked-down Docker API access for the backend

PiTun's import format compatibility (V2RayN / Shadowrocket / Clash JSON)
is inspired by the formats of those projects — no code is borrowed.

## Contributing

Bug reports and PRs welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
for code style, PR conventions, and what to keep out of the repo.

## License

[BSD 3-Clause](LICENSE) © PiTun contributors

---

> **Disclaimer.** PiTun is a network management tool. You are responsible
> for complying with the laws of your jurisdiction and the terms of
> service of any upstream provider you use it with. The maintainers
> provide no warranty and accept no liability for misuse.
