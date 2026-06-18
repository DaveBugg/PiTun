# Changelog

All notable user-facing changes to PiTun. Full per-release detail lives in the
[GitHub Releases](https://github.com/DaveBugg/PiTun/releases); this file is the
committed summary.

## v1.4.4 — 2026-06-16

Multi-hop node chaining that actually wires every hop, a Route Explainer that
accepts pasted URLs, and a frontend toolchain refresh (Vite 5→8 / Rolldown) that
drops the vulnerable esbuild dependency. Plus security bumps for react-router and
build-time transitives.

### Added

- **Recursive multi-hop node chaining.** `config_gen` now follows `chain_node_id`
  transitively, wiring proxySettings (→ `sockopt.dialerProxy`) at every hop with
  cycle detection and a depth cap. A 3-node chain (exit → mid → entry) generates
  fully; previously only the first link was wired and deeper relays silently
  dialed direct, collapsing the chain.

### Changed

- **WireGuard can only be a chain's exit hop.** xray can't tunnel traffic THROUGH
  a WireGuard outbound — as a relay it forwards 0 bytes (config is accepted, xray
  starts, traffic dies; verified live: WG-over-VLESS works, VLESS-over-WG and
  WG-over-WG = 0 B). Enforced three ways: the nodes API rejects pointing a chain
  at a WireGuard node (400), config_gen skips a WG-relay link with a warning, and
  the Node form omits WireGuard from the "chain via" dropdown.
- **Frontend build moved to Vite 8 / Rolldown** (`@vitejs/plugin-react` 4→6),
  which removes the bundled esbuild entirely.

### Fixed

- **Route Explainer accepts a full URL.** Pasting `https://host/path` resolved the
  whole string as a domain → confusing NXDOMAIN. It now extracts the bare host
  (strips scheme / userinfo / path / port, leaves bare IPv6 intact).

### Security / dependencies

- Dropping esbuild (via the Vite 8 bump) closes 2 dev-scope esbuild advisories
  (Deno integrity GHSA-gv7w-rqvm-qjhr; Windows dev-server file read
  GHSA-g7r4-m6w7-qqqr) and the Vite ≤6.4.1 path-traversal (GHSA-4w7w-66w2-5vf9).
- `react-router` / `react-router-dom` 7.15.0 → 7.18.0 — CSRF via PUT/PATCH/DELETE
  document requests (GHSA-84g9-w2xq-vcv6).
- Build-time transitives patched via `npm audit fix`: `@babel/core` (arbitrary
  file read), `form-data` (CRLF injection), `js-yaml` (DoS). `npm audit` → 0.

### Notes

- No breaking changes, no schema migration (alembic head stays at `017`).
- The frontend now builds with Rolldown — output is functionally identical;
  verified in a browser (authenticated, 0 console errors).
- Backend suite 762 passing; 25 frontend tests passing; `npm audit` clean.

**Full Changelog:** https://github.com/DaveBugg/PiTun/compare/v1.4.3...v1.4.4

## v1.4.3 — 2026-06-15

Set-aware routing rule **export/import** (pick scopes, resolve conflicts, import
into Global / an existing / a new set), a **DNS-over-HTTPS resolver fix**, and a
clearer **set-deletion** flow. Plus the post-1.4.2 dependency security bumps.

### Added

- **Set-aware export/import (Routing).** Export picks scopes — Global and/or each
  routing set — and downloads them merged into one file or as separate per-scope
  files, in a native PiTun envelope (`format: "pitun-routing"`) that preserves
  every rule field including `mac`/`geosite` and `node:`/`balancer:` actions.
  Import reads that envelope (or a legacy V2Ray JSON array — auto-detected),
  previews what would be added vs. skipped (identical / in-file duplicates /
  unusable), and surfaces **action conflicts** (same match, different action) for
  per-rule resolution before committing into Global, an existing set, or a new
  one. Rules referencing a node/balancer or geo tag absent on this box are
  dropped (and counted) so the result stays valid for xray.
- **`cascade=delete` on set deletion.** `DELETE /api/routing-sets/{id}` can now
  drop a set's rules instead of moving them to Global.

### Changed

- **Deleting a routing set now defaults to deleting its rules.** The delete
  dialog leads with "Delete set + rules"; "Move to Global" is the secondary
  option. Assigned devices always fall back to Global (a physical device row is
  never deleted). Previously a delete silently moved every rule to Global.
- The legacy client-side V2Ray export and the standalone V2Ray import dialog are
  removed from the UI; the `import-v2ray` endpoint stays for API compatibility.

### Fixed

- **DoH resolver uses RFC 8484 wire format.** `_resolve_doh` issued the
  Google/Cloudflare JSON query (`?name=&type=A`, `application/dns-json`), which
  AdGuard's `/dns-query` rejects with HTTP 400 — breaking the Route Explainer's
  resolution and reachability for any AdGuard-over-DoH rule. It now POSTs
  `application/dns-message` and parses the wire response (DNS query builder
  shared with the UDP path).

### Security / dependencies

- `aiohttp` 3.13.5 → 3.14.0 and `asyncssh` 2.22.0 → 2.23.0 — closes 3 Dependabot
  advisories (none reachable in PiTun's usage, but bumped for hygiene).
- `uvicorn` 0.46.0 → 0.48.0, `pydantic-settings` 2.14.0 → 2.14.1,
  `pytest-asyncio` 1.3.0 → 1.4.0 (dev).
- CI: `actions/checkout` 6.0.2 → 6.0.3, `docker/setup-buildx-action` 4.0.0 → 4.1.0.
- 3 CodeQL `routing_sets.py` alerts dismissed as false positives (int-typed log
  args; an intentional bind-and-close port probe).

### Notes

- No breaking changes, no schema migration (alembic head stays at `017`).
- Backend suite 745 passing. Frontend type-checks and builds clean.

**Full Changelog:** https://github.com/DaveBugg/PiTun/compare/v1.4.2...v1.4.3

## v1.4.2 — 2026-06-12

Adds the **Route Explainer** — a two-layer diagnostic that shows exactly where
traffic to any domain or IP goes, which DNS server resolves it, and optionally
whether it actually connects. Also brings first-class **host-DNS controls** with
a shared in-UI explainer, closes an **IPv6 DNS-leak** path, and ships two
crash/correctness fixes.

### Added

- **Route Explainer (Diagnostics).** Enter a domain/IP, port, and protocol to
  see the full path a packet takes: the matched DNS rule + resolver, the matched
  routing rule + outbound, and optional reachability. Two layers mirror how the
  xray config is built — a pure-Python matcher replays the exact rule ordering
  (`config_gen`) for literal matchers, and for `geosite:` / `geoip:` categories
  an opt-in live xray probe reads the chosen outbound from the access log for
  ground truth. Per-device (routing set) context supported via a device MAC.
- **Host resolver controls (DNS page).** A "Host resolver (this box only)" block
  sets additive fallback DNS for the box's own lookups (subscriptions, geo
  files, panels, health checks), applied through `systemd-resolved`
  `FallbackDNS=` / NetworkManager / `resolv.conf` — idempotent and boot-applied.
- **Shared "What is this?" explainer (`HostDnsHelp`)** on both Settings → Host
  network and DNS → Host resolver, clarifying primary gateway+DNS vs. fallback
  resolver, and that neither changes what LAN clients resolve.
- **`queryStrategy: UseIPv4`** for xray DNS, configurable on the DNS page.

### Changed

- DNS settings and rules now **auto-reload xray** on every change (settings,
  create, update, delete, reorder), matching the routing endpoints.
- **Honest DoT labels** — xray has no native DNS-over-TLS, so the UI no longer
  implies `tls://` is encrypted (it's DNS-over-TCP/53, plaintext; DoH is the
  encrypted option).
- `disable_ipv6` relabeled "host only" with a clarifying tooltip.
- A router-provided IPv6 RA nameserver is no longer flagged red in the
  host-network form (it's managed by RA, not the IPv4-only apply path).

### Fixed

- **IPv6 DNS-leak path.** `AAAA` answers could hand a client an IPv6 destination
  that routed around the IPv4-only TPROXY via the client's router IPv6 default
  route — a silent bypass of all routing rules. `UseIPv4` keeps destinations on
  the intercepted IPv4 path.
- **Device scanner crash.** A MAC appearing twice in one ARP sweep queued a
  second `Device` row with the same MAC, so the scan rolled back on a UNIQUE
  constraint every ~60 s and the device never persisted. Freshly-created rows
  are now registered in-batch so a repeat MAC updates instead of re-inserting.
- **Route Explainer probe merge.** When the live xray probe overrode the offline
  best-guess, the action and matched-rule stayed from the `geosite` candidate —
  so the UI could show `action: direct / outbound: node-2`. The action is now
  re-derived from the real outbound and the stale candidate is dropped.

### Notes

- No breaking changes, no schema migration. New settings (`dns_query_strategy`,
  `host_fallback_dns`) populate on first boot.
- The `UseIPv4` default means IPv6 destinations are no longer resolved by the
  box's DNS engine; switch the strategy to `UseIP` on the DNS page if you rely
  on IPv6.
- Backend suite: 727 passing (+6). Frontend type-checks and builds clean.

**Full Changelog:** https://github.com/DaveBugg/PiTun/compare/v1.4.1...v1.4.2
