# Changelog

All notable user-facing changes to PiTun. Full per-release detail lives in the
[GitHub Releases](https://github.com/DaveBugg/PiTun/releases); this file is the
committed summary.

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
