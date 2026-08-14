# Changelog

All notable user-facing changes to PiTun. Full per-release detail lives in the
[GitHub Releases](https://github.com/DaveBugg/PiTun/releases); this file is the
committed summary.

## v1.6.2 — 2026-08-14

**The Update button in the panel now works.** It never could.

### Fixed

- **The panel's Update button waited for an agent nobody installed.** The
  button writes a request file, and a host-side systemd path unit is what
  carries it out — but `install.sh` never installed that unit, on any box it
  ever produced. The button reported "waiting for the update agent" and sat at
  0% forever.

  The hint shown underneath made it worse: it named `--install-timer`, which is
  the separate daily scheduled check and does not service the button at all, so
  an operator who followed the message installed a timer and watched the same
  0%.

  The installer now sets the agent up. This does **not** enable unattended
  updates — the agent acts only on a request you made from the panel, and the
  scheduled timer stays opt-in. The hint and the knowledge base name the right
  flag and say plainly that the two are different things.

  Existing boxes need the agent once, by hand:
  `bash /opt/pitun/scripts/pitun-update.sh --install-agent`

## v1.6.1 — 2026-08-14

**A panel PiTun didn't install can now be connected to it.** Everything here
came from using 1.6.0 on real boxes.

### Added

- **Connect an existing x-ui panel.** The X-ui page shows registered panels,
  and a panel only became one by being deployed through PiTun — its install
  script ends by registering it. Import a server that already runs x-ui and
  the page stayed empty, with nothing on it suggesting another route, so
  reinstalling over a working panel looked like the only option. There is now
  a button, taking either the `xui://` line from the install or, if that is
  long gone, the login you use for the panel yourself.

  **The API token is no longer asked for.** It is what every call needs and
  what nobody has lying around — the install script obtains it by logging in
  and reading, or creating, a token named `pitun`. PiTun now does the same, so
  you supply only the port, the base path and your panel login. An existing
  token is reused rather than a new one minted per attempt, which keeps any
  `xui://` handed out earlier valid.

- **The panel travels with its server.** Exporting a server with secrets and
  restoring it elsewhere left the panel behind — it lives in its own table —
  so the server arrived with x-ui plainly installed and the X-ui page empty.
  The bundle (envelope v3) now nests it. Its shape travels either way, so a
  secret-stripped file still says a panel was here; the token and password
  follow the same opt-in as the SSH credentials. Older bundles import exactly
  as before.

### Fixed

- **Upgrading from a beta to its own release was refused as a downgrade.**
  `sort -V` is not semver aware: it reads `1.6.0-beta.3` as `1.6.0` plus extra
  characters and sorts it *after* the finished release, so the guard against
  an accidental rollback fired on the single most common upgrade there is.
  Hit while updating the test box to 1.6.0.
- A client that has no API token yet — the one that logs in precisely to fetch
  one — sent a bare `Bearer `, which the HTTP layer refuses to encode: the
  request failed before reaching the panel, reporting a header problem rather
  than a missing token. The underlying failure was unreadable too, printing
  "transport error: " and stopping; it now names what went wrong and the URL
  it could not reach, which is the error you get when the port or base path is
  wrong.

## v1.6.0 — 2026-08-14

**PiTun can be the router.** On a box with two or more physical ports it takes
the ISP uplink, hands out addresses, does NAT, and can serve the WiFi itself.
Gateway mode is untouched and remains the default: nothing changes for an
existing install unless the mode is switched on deliberately.

Three betas of hardware testing went into this. Everything below was run on a
real box, not reasoned about — the reboot alone found three faults that no
amount of reading would have.

### Router mode

**Where:** Settings → Router. Offered only when the box actually has two or
more physical NICs, and never switched on automatically.

- **DHCP for the LAN**, with pool, lease time and per-device reserved
  addresses assigned from the Devices page. PiTun advertises itself as the
  resolver, so routing rules and the DNS query log cover devices that never
  opted in to anything.
- **A LAN of several ports.** Sockets and the radio are bridged into one
  segment — same subnet, one DHCP scope, clients on either side see each
  other.
- **WAN in the shapes an ISP link comes in**: DHCP (what providers call IPoE),
  static, **PPPoE**, VLAN tagging, MAC cloning. With PPPoE or a VLAN the
  traffic leaves on a different interface than the port the cable is in, and
  NAT, the firewall and the counters follow it.
- **WiFi access point**, gated on a capability probe: plenty of adapters can
  only join networks, not create them, and discovering that when hostapd
  refuses to start means the working setup is already dismantled.
- **Commit-confirm watchdog.** Router mode has no fallback — PiTun *is* the
  router — so an apply that breaks the network would leave nobody able to undo
  it. The box reverts to gateway unless a human confirms, and an unconfirmed
  apply never survives a reboot.
- **The uplink accepts nothing new from the internet.** One blanket rule
  rather than a list of ports to close. The panel, SSH and xray's inbounds all
  bind `0.0.0.0` — they stay reachable over the LAN and invisible from
  outside. Two exceptions keep the link working: DHCP replies, which arrive as
  NEW rather than RELATED, and the ICMP that PMTU discovery needs.
- **Optional access from the uplink**, off by default, for a PiTun that sits
  behind another router — that "WAN" is your own network. Refused outright if
  the uplink address turns out to be public.
- **Uplink diagnosis** built on nftables counters, because the rules a WAN
  depends on fail silently.

### Also in this release

- **Connection-lifetime policy for Xray**, for the box's own instance and every
  registered panel. Xray's `connIdle` of 300 s kills an idle *pooled*
  connection, so the next request on that socket hangs — the "works, then it
  doesn't" that SDK and agent clients hit — and the half-close timers cut long
  streaming answers. Nothing set any of it before. Panels are patched, never
  overwritten, and **Apply to all panels** pushes a change to the fleet.
- **The installer produced a box with no panel at all.** `nginx.conf` has
  carried an unconditional `listen 443 ssl` since v1.5.2 while `install.sh`
  generated no certificate, so nginx aborted — taking port 80 with it, since
  it is the only service publishing either. Every one-liner install since
  v1.5.2 landed that way.
- **A network card present but unusable is now named as such** — "no adapters
  found" is the wrong thing to say about hardware sitting on the PCI bus.
- Searchable country picker for the WiFi regulatory domain, with flags and
  localised names.

### Upgrading

Standard update path, verified from v1.6.0-beta.2 through to this release: the
database is backed up first, migrations run, and a box in router mode brings
itself back without help. Ships migrations **023–025**, all additive. Nothing
about router mode activates until you choose it.

**Secrets:** the WiFi passphrase and the PPPoE password are write-only — set
but never returned, and redacted from configuration backups unless secrets are
explicitly included.

**Full diff:** https://github.com/DaveBugg/PiTun/compare/v1.5.3...v1.6.0

## v1.6.0-beta.3 — 2026-08-14

**Beta — what a reboot found.** beta.2 had run on hardware but had never been
switched off and on again. It did not survive: the box came back claiming to
be a router while behaving as a gateway. That, and publishing the panel on the
uplink turning out to work for SSH and not for the panel, are what this fixes.

### Fixed

- **A reboot left the box claiming router and behaving as gateway.** Three
  causes, all of them about boot differing from an operator pressing Save.
  The reconcile ran before the hardware had finished arriving — a wifi adapter
  appears only once its firmware has loaded, and the backend is up in seconds —
  so it asked, got "port is not present", and gave up. It gave up *without
  cleaning up*, leaving both sidecars resurrected by Docker's own restart
  policy: dnsmasq serving DHCP for a bridge that no longer existed, hostapd
  crash-looping on a missing radio. And it was a single attempt, so a LAN cable
  plugged in a minute later changed nothing. Boot now waits for the configured
  ports, tears the dataplane down if it cannot restore it, and the watchdog
  retries once a minute while the settings say router and the dataplane is
  absent.
- **Publishing the panel on the uplink only opened half the path.** With both
  toggles on, SSH answered from the WAN and the panel did not: SSH is a host
  service and reaches INPUT, while the panel is a container with published
  ports, so the request is DNAT'd and *forwarded* — a chain whose policy is
  drop. Worse than a missing feature, because the operator turns that toggle on
  precisely so they can confirm after the switch, and could not, so the
  watchdog reverted a working router.
- **The LAN address ended up on both the bridge and the member.** The port
  needs a persistent address — without one there is nothing for the next apply
  to read after a reboot — and NetworkManager puts that address straight back
  the moment the bridge takes it. Members are now taken out of its hands while
  enslaved and handed back on teardown.
- **TCP MSS is clamped to the route MTU**, without which a PPPoE uplink hangs
  on large transfers while small requests work.
- The ICMP counter is read before conntrack, where it can actually see the
  PMTU messages it exists to detect, and zero is no longer reported as a
  warning on an uplink that has an address.
- Diagnosis reports when traffic is leaving by a port that isn't the uplink —
  NAT and a firewall on a link nothing uses, with every other check healthy.

### Added

- **A network card present but unusable is now named as such.** "No wireless
  adapters found" is the wrong thing to say about a card sitting on the PCI
  bus. The inventory reports controllers that produced no interface and
  separates the two readings — nothing bound the device, or a driver is bound
  and produced no interface anyway, which in practice means firmware it could
  not load. Found the hard way: a purged firmware package cost an evening.

### Notes

- No new migrations; head remains 025.
- Verified on hardware: reboot with a LAN cable restores router mode
  unattended; a client on the LAN resolves DNS, pings out and pulls 1 MB
  through NAT; the update path preserves the database and brings the router
  back by itself.
- 5 commits. Tests: 188 router-mode, 96 frontend.

## v1.6.0-beta.2 — 2026-08-14

**Beta — router mode, first hardware run.** beta.1 had never been switched on
outside tests. This is what a two-port mini PC found: a clean install that left
the panel unreachable, an access point that broadcast nothing while reporting
success, and a router you could not switch back off. Everything below was
observed on real hardware, not reasoned about.

Upgrading from beta.1 is worthwhile even if you never enable router mode — the
installer and X-ui fixes apply to every box.

### Fixed — install

- **The one-liner install left the panel unreachable on both 80 and 443.**
  `nginx.conf` has carried an unconditional `listen 443 ssl` since v1.5.2, but
  `install.sh` never generated a certificate — the word does not appear in it.
  nginx aborts outright when the files are missing, and since it is the only
  service publishing either port, the panel was gone from *both* while the
  container crash-looped. Every one-liner install since v1.5.2 landed this way;
  it went unnoticed because our own boxes were provisioned through
  `03-deploy.sh`, which does generate the cert.
- **The sidecar build shipped inert.** `build-sidecars.sh` referenced `$DOCKER`
  while declaring `DOCKER_CMD`; under `set -u` that aborts on the first loop
  iteration, so the dnsmasq and hostapd images were never built by any caller —
  the exact failure the script had just been extracted to fix.
- **avahi came back within seconds** of being disabled and kept UDP/5353, which
  xray needs for DNS. Only the service was masked, not the socket unit that
  re-activates it, and masking never kills a running process.
- The installer no longer claims success without looking: `compose up -d`
  returns 0 for a container that starts and immediately dies, so it printed
  "PiTun is up" over a dead stack.

### Fixed — router mode

- **"Auto" WiFi channel put nothing on the air.** `channel=0` runs hostapd's
  ACS, which needs noise-floor figures the driver may not report — mt7921
  doesn't — and hostapd then loops over every channel forever *without
  exiting*. The container looked healthy while no SSID existed. Auto now
  resolves to a fixed channel, and the start path asks the radio whether it
  actually entered AP mode rather than trusting that the process survived.
- **Bridging the LAN stranded the operator in router mode.** Enslaved ports
  were dropped from the inventory, so the moment the bridge came up the
  assigned ports vanished, the panel reported one port on a three-port box, and
  validation rejected the stored configuration — including the request to turn
  router mode *off*. Leaving router mode is no longer validated at all: gateway
  is the resting state and the escape hatch.
- **A save that changed nothing rebuilt the network.** Any patch touching a
  router key tore the bridge down and back up and restarted hostapd and
  dnsmasq — indistinguishable, from a client, from the router rebooting — and
  re-armed the confirm window on an already-confirmed router, which then
  reverted itself three minutes later.
- **No TCP MSS clamping.** A PPPoE uplink carries 1492 bytes; LAN clients
  announce MSS for their own 1500. Without clamping the router depends on ICMP
  "fragmentation needed" reaching the sender, which the public internet drops
  often enough that it is not a plan. DNS resolves, ping works, small pages
  load, large transfers hang forever.
- **The ICMP counter could not see what it counted.** It sat after
  `ct state established,related accept`, and the packets it exists to observe
  are exactly what conntrack marks RELATED — so it read zero on a healthy link
  and could never have detected the path-MTU black hole it was added for.
- The LAN address is found on whichever member holds it, the radio may be any
  LAN member rather than only the primary, and the confirm countdown is driven
  by the box's own `seconds_left` — a box whose clock is hours off used to
  promise three hours where three minutes were left.

### Added

- **A LAN can be several ports.** `br-lan` bridges them into one segment —
  same subnet, one DHCP pool, wired and wireless clients seeing each other.
  The radio is joined by hostapd itself rather than from both ends, which
  races.
- **The uplink can publish the panel and SSH** (both off by default). Useful
  when PiTun sits behind another router, where that "WAN" is your own network.
  Refused outright if the uplink address is public. Turn it on *before*
  switching, not after.
- **A dedicated Router page** between Diagnostics and Settings, naming the two
  deployments — behind an existing router, or first in line facing the ISP —
  so it is clear which fields apply.
- **Country picker with names and flags** for the WiFi regulatory domain,
  searchable, no new dependency.
- **A banner when the panel stops answering at this address**, pointing at the
  LAN one — the switch to router mode is made from a page served by the port
  that is about to stop serving it.
- **`scripts/pppoe-test-rig.sh`** — a local PPPoE concentrator on a veth pair.
  PPPoE frames don't route, so it cannot be tested remotely and was shipping
  unexercised. The rig proved the path works and found the MSS and default-route
  faults above.
- Diagnosis now reports when traffic is leaving by a port that isn't the
  uplink: NAT and firewall on a link nothing uses, while every other check
  reads healthy.

### Notes

- No new migrations; head remains 025.
- The default WiFi network name is now `PiTun`. Broadcasting stays **off**
  until switched on.
- 13 commits. Tests: 1299 backend, 96 frontend.

## v1.6.0-beta.1 — 2026-08-13

**Beta — router mode.** PiTun can now *be* the router instead of sitting beside
one: it takes the ISP uplink, hands out addresses on the LAN, does NAT, and
optionally serves the WiFi itself. Gateway mode is untouched and remains the
default; nothing changes for existing installs unless the mode is switched on
deliberately. Ships DB migrations 023-025.

**This has never been enabled on real hardware.** Treat the first switch-on as
an experiment: do it with physical access to the box, not over SSH from the
LAN it is about to reconfigure.

### Added

- **Router mode.** Offered only on hardware with two or more physical NICs, and
  never switched on automatically. One port faces the ISP, the other the home
  network; the choice is explicit in **Settings → Network**.
- **DHCP server** for the LAN (dnsmasq sidecar), with pool, lease time and
  per-device reserved addresses assigned from the Devices page. PiTun
  advertises itself as the resolver, so routing rules and the DNS query log
  apply to devices that never opted in to anything.
- **WAN acquisition**: DHCP, static, **PPPoE**, VLAN tagging and MAC cloning —
  the shapes an ISP link actually comes in. With PPPoE or a VLAN the traffic
  leaves on a different interface than the port the cable is in, and NAT, the
  firewall and the counters follow it.
- **WiFi access point** (hostapd), gated on a capability probe: many adapters
  can only join networks, not create them, and finding that out when hostapd
  refuses to start means the working setup is already dismantled. An
  undetermined answer says so rather than blaming the hardware.
- **Commit-confirm watchdog.** Router mode has no fallback — PiTun *is* the
  router — so an apply that breaks the network would otherwise leave nobody
  able to undo it. The box reverts to gateway unless a human confirms the
  network still works, and an unconfirmed apply never survives a reboot.
- **Uplink diagnosis** built on nftables counters, because the two rules the
  WAN depends on fail silently: no DHCP replies at all, versus no ICMP coming
  back — the path-MTU black hole where large transfers hang while DNS and ping
  look fine.
- **Connection-lifetime policy** (X-ui page), for the box's own xray and every
  panel at once. Xray's defaults are tuned for short browser sessions: an idle
  *pooled* connection dies after five minutes and the next request on that
  socket hangs, and the half-close timers cut a streaming answer after 2–5
  seconds — the "works, then it doesn't" that SDK and agent clients hit.
  Nothing in PiTun set any of it before. Panels are patched rather than
  overwritten, so outbounds, routing and stats flags survive; applied
  automatically on deploy and on registration, and **Apply to all panels**
  pushes a change to the fleet. Inbound TCP keep-alive comes with it — with an
  hour-long idle timeout, a client that vanished without a FIN would otherwise
  hold its slot the whole hour.

### Changed

- **The uplink accepts nothing new from the internet.** One blanket rule rather
  than a list of ports to close, because a list has to be maintained and a
  forgotten port is an exposed service. The panel, SSH and xray's DNS / SOCKS /
  HTTP inbounds — all of which bind 0.0.0.0 — are reachable over the LAN and
  invisible from outside. Two exceptions keep the link working: DHCP replies
  arrive as NEW rather than RELATED, and PMTU discovery needs its ICMP back.
- **Install paths now build the router-mode sidecars.** dnsmasq and hostapd are
  not compose services (the backend starts them on demand), so `compose up`
  never built them and boxes installed through the one-liner had no images at
  all. Extracted into `scripts/build-sidecars.sh`, called from every path.

### Fixed

- The offline deploy shipped neither `nginx.conf` nor `frontend/nginx-spa.conf`,
  both mounted as files — Docker created the missing sources as empty
  directories and the stack could not come up. It also generated no TLS
  certificate, as did `setup-vm.sh`; nginx aborts on `listen 443 ssl` without
  one, and since it is the only service publishing 80 and 443, that took the
  panel down on both ports rather than just HTTPS.
- Four assignments aborted their install script under `set -euo pipefail`, each
  taking the fallback written directly below it with them — including one in
  `01-first-boot.sh` that died after restarting sshd but before setting the
  static IP, forwarding and hostname.
- A failed port probe in the panel reported "this box has 0 physical ports",
  which is a claim about the hardware that was never established. It now says
  the probe failed, and names a 404 as an out-of-date backend.

### Notes

- Migrations 023-025: `nodecircle.subscription_id`, `device.dhcp_reserved_ip`,
  and a unique index on the latter. All additive; gateway-mode behaviour is
  unchanged.
- The WiFi passphrase and the PPPoE password are write-only — set but never
  returned, and redacted from backups unless secrets are explicitly included.
- 30 commits, reviewed adversarially in four passes before release.

## v1.5.3 — 2026-08-11

Two ways to stop hand-maintaining state: a NodeCircle can now track a
subscription automatically, and the whole configuration can be exported to (and
restored from) a single file. Ships DB migration 023.

### Added

- **NodeCircle auto-sync from a subscription.** Link a circle to a subscription
  and every refresh keeps its membership current: nodes the panel still serves
  are kept or **added** — including one that came back under a new address, which
  arrives as a new id and previously had to be re-added by hand — while nodes the
  panel dropped leave. Members you picked yourself, or that came from another
  subscription, are always preserved. Unlinked circles behave exactly as before
  (dangling ids pruned, "check members" badge), so nothing changes unless you opt
  in. Membership changes are reported in Recent Events as `circle.synced`.
- **Whole-box configuration backup.** **Settings → Backup & Restore** exports
  settings, subscriptions, nodes, routing sets and rules, DNS rules, balancer
  groups, node circles, devices and UA templates as one JSON file, and restores
  it onto a fresh box.
  - **Secrets are opt-in** (`include_secrets`, off by default), so the file you
    share for debugging carries no node credentials or subscription URLs — and
    restoring such a file never blanks the credentials already on the box.
  - **Restore previews before it writes**: per-section add / update / delete
    counts plus warnings, then an explicit confirm. `merge` adds and updates;
    `replace` additionally deletes rows the backup doesn't contain.
  - The dataplane is regenerated and xray reloaded afterwards, so restored
    routing and DNS take effect immediately.
  - Endpoints: `GET /api/system/backup`, `POST /api/system/backup/preview`,
    `POST /api/system/backup/restore`.

## v1.5.2 — 2026-08-06

Adds HTTPS for the panel (per-install cert + downloadable local CA), disables
host IPv6 by default on clean installs, and surfaces NodeCircles that a
subscription refresh shrank.

### Added

- **HTTPS for the panel.** The reverse proxy now serves the UI over TLS on
  **443** alongside plain HTTP on 80, so a cert issue can't lock you out. A
  per-install certificate is signed by a local **PiTun CA**, generated before
  nginx starts (`scripts/gen-cert.sh`), with the box's LAN IP + `pitun` /
  `pitun.local` in the SAN. **Settings → HTTPS** offers the root CA for
  download — trust it once to drop the browser warning. WebSocket / log
  streams upgrade to `wss://` automatically. Existing boxes pick it up on the
  next deploy.
- **NodeCircle "shrank by refresh" highlight.** When a subscription refresh
  removes a node that belonged to a circle, PiTun records a `circle.pruned`
  event (Recent Events) naming the affected circle(s), and the NodeCircles
  page flags any circle with a missing member or fewer than 2 nodes with a
  **check members** badge. Covers the case where a provider moves a node to a
  new address — it comes back as a new id and isn't auto-re-added.

### Changed

- **Host IPv6 is disabled by default on a CLEAN install** (`disable_ipv6=true`
  seeded for fresh DBs only). PiTun's TPROXY is IPv4-only, so this avoids a
  class of IPv6-path surprises; existing installs keep whatever they had
  (`INSERT OR IGNORE` — an upgrade never flips it). Client-side IPv6 leaks
  were already closed by `dns_query_strategy=UseIPv4`.
- **Dark-theme polish.** Blue type/mode pills (rules / vmess / src_ip) use a
  desaturated grey-blue in dark mode instead of the saturated blue-900 wash.

### Housekeeping

- Sanitized example identifiers in a few source comments and test fixtures
  (no behaviour change).

## v1.5.1 — 2026-08-06

Fixes the active node reporting no speed on a general sweep, adds a REALITY
dest / SNI scanner to x-ui inbound creation, and a small dark-theme refresh.

### Added

- **REALITY dest / SNI scanner at inbound creation.** Creating an x-ui inbound
  from a REALITY preset gains a **Scan (via active node)** button that probes
  the SNI field's target — a domain OR a bare IP — through the active node and
  reports TLS 1.3 / HTTP-2 suitability plus the certificate the endpoint
  presents, so a bare-IP scan surfaces the domain behind it (usable as the
  serverName). Mirrors 3x-ui's reality-sni scan, routed like every other
  server op. No hardcoded candidate lists — it scans exactly what you enter.

### Fixed

- **The active node reported "no speed" on a general / auto speed sweep.**
  Every speed test spun up a throwaway xray, which for the *active* node opens
  a SECOND tunnel to the same server — fatal for WireGuard, which holds one
  session per peer key: the temp test and the live tunnel fought, the
  reachability gate flapped to "unreachable", and the reading failed (briefly
  disrupting the live tunnel too). The active node is now measured through the
  live tunnel — config_gen adds a loopback `speed-probe` inbound pinned to the
  active outbound and the test reuses the session already up. No second
  session, no disruption, an honest number. Non-active nodes are unchanged.

### Changed

- **Failed speed checks are visible.** A node the sweep couldn't measure now
  shows an amber `no speed · <age>` badge instead of a blank row — the check
  is stamped so the failure persists across reloads.
- **Dark-theme polish.** The main content pane now matches the sidebar colour,
  and the brand accent returns to the original sky-blue ramp in dark mode only
  (light keeps the TailAdmin indigo).
- Removed the redundant "Check SNI" button from the add-node form — the scan
  belongs at inbound creation, not when registering an already-existing node.

## v1.5.0 — 2026-08-05

Promotes v1.5.0-beta.1 to stable and lands a UI-framework refresh, a full
Russian translation sweep, and hardening against the "gateway points at itself"
install footgun. Everything from the beta — quality-aware NodeCircle rotation,
background auto speed-checks, the unified reachability-gated speed test, login
lockout and opt-in GeoIP flags (DB migrations 019–022) — ships here.

### Added

- **Routing self-loop protection (Settings → Network).** State read, apply and
  the gateway probe now detect and refuse a default route that points at the
  box's own IP — the "a new device set its PiTun gateway to itself" footgun.
  The Network page flags it in red and blocks re-applying a self-referential
  gateway; the install/deploy scripts add an `IP == GATEWAY` guard so the loop
  can't be baked in at first boot.
- **Knowledge Base + README refresh.** New KB sections (Speed Tests & Node
  Health, Host Network, Direct Connection, Updates, TLS Fragment) plus updated
  Node Circles / Subscriptions / Security sections, all bilingual EN/RU; the
  README feature list and version pins were brought up to date.

### Changed

- **UI framework: Tailwind v3 → v4** (CSS-first `@theme`) with the TailAdmin
  palette as the base, keeping PiTun's variable structure. Light-theme contrast
  fixed page by page.
- **Russian localisation sweep.** Pages that lacked `useT` (GeoData, Balancers,
  Diagnostics, Logs, Login, routing / rule editors, …) are now translated, with
  technical terms kept in English.
- **Sidebar** reordered into logical groups with thin separators, a scrollable
  nav that never exceeds the viewport, and distinct icons (Balancers no longer
  shares the Nodes glyph).
- **Install / deploy DNS hardening.** `02-install-stack.sh`, `03-deploy.sh` and
  `setup-vm.sh` now check ports 53 / 5353, remove the native `systemd-resolved`,
  and make `/etc/resolv.conf` a static PiTun-owned file so the box's own name
  resolution (hostname) stays reliable; `03-deploy.sh` self-heals already-
  installed boxes on the next deploy.

### Fixed

- **"Speed All" no longer 504s** — it reuses the background auto-check sweep
  (one-off, forced scope "all"), so a large node set can't time out at the
  reverse proxy; the sweep order now checks the newest nodes first.
- A latent `warn: command not found` (`set -e` crash path) in
  `02-install-stack.sh`.

## v1.5.0-beta.1 — 2026-08-04

**Beta.** Smarter node-circle rotation driven by real speed data, an automatic
background speed-check, a unified speed test that gates on reachability, plus
login lockout and optional GeoIP flags. Ships DB migrations 019–022.

### Added

- **NodeCircle quality-aware rotation.** Circles gain a **`best`** mode and two
  candidate filters — **`max_latency_ms`** (drop high-RTT nodes) and
  **`min_speed_mbps`** (drop nodes whose last speed reading is below a floor;
  never-tested nodes get the benefit of the doubt). A **smart-skip** keeps a
  scheduled rotation from moving off a healthy, low-latency active node — manual
  "rotate now" still always rotates.
- **Automatic speed checks.** A background sweep (Nodes → **Auto-checks**)
  speed-tests a chosen scope — **all / a subscription / a group / specific
  nodes** — on an interval, so `best` / `min_speed` and the UI stay fresh
  without manual testing. Sequential (a speed test saturates the uplink), with a
  per-node staleness guard and per-node error isolation. `POST /api/autocheck`
  + `run`.
- **Per-node speed history in the UI.** The last reading (average **and** peak)
  and its age show on the node card; a reading older than 6h is flagged so a
  stale number never reads as current. Persisted, so it survives a restart.
- **Login lockout.** After 5 consecutive failed logins an account is locked for
  15 minutes (HTTP 429 + `Retry-After`); a successful login resets the counter.
  PiTun is LAN-only with no captcha, so this is the primary brute-force guard.
- **Optional GeoIP flags.** Imported node names can be prefixed with a country
  flag (`🇳🇱 vless-nl`). Fully opt-in and licence-clean — nothing is shipped or
  downloaded; drop a MaxMind `GeoLite2-Country.mmdb` next to the geoip/geosite
  data and it lights up, absent it's a silent no-op.

### Changed

- **One speed-measurement path** for the manual button, "speed all", the live
  stream and the auto-check. It now **gates on reachability first** — two
  popular 204 endpoints (Google, Cloudflare) with a retry — and only then
  measures, so a dead node fails in ~1s instead of grinding every download
  fallback. The number is the **average after a warm-up plus the peak** steady
  window (previously a single curl figure), and both avg and peak are saved.

## v1.4.12 — 2026-08-04

Hotfix.

### Fixed

- **Self-update could brick itself after the first update.** The update
  agent's systemd unit executed `pitun-update.sh` directly, but the repo
  ships its scripts non-executable (`100644`) and a source re-extraction on
  update drops any `+x` bit — so the *next* update failed to spawn the agent
  with `status=203/EXEC` (`Permission denied`) and the UI Update button went
  dead. The unit now invokes the script via `bash` (no `+x` needed, matching
  every other script here), and `pitun-update.sh` is additionally tracked
  executable so extraction preserves the bit. Already-affected boxes need a
  one-time `chmod +x /opt/pitun/scripts/pitun-update.sh` (or the bash-unit
  edit) before they can update again.

## v1.4.11 — 2026-08-04

Hotfix.

### Fixed

- **In-UI update reported "failed" right after it succeeded.** The update
  agent (`pitun-update.sh`) runs under `set -u`, and its temp-dir cleanup
  `trap 'rm -rf "$tmp"' RETURN` could re-fire on a later function return —
  after `$tmp` had gone out of scope — aborting the script with
  `tmp: unbound variable`. This happened *after* the update had already
  applied and written an `ok` status, so the box ended up on the new version
  while the systemd unit went to `failed`. The trap now guards with
  `${tmp:-}`, so a clean update ends clean.

## v1.4.10 — 2026-08-04

Bundled **xray-core moves to 26.7.28**, and every server / panel operation can
now be run **through the active node or straight past it** with a per-page
**Direct** switch. New node diagnostics land on the dashboard — a live
streaming speed test, a one-tap reachability check, single-node URI export —
plus an SNI scanner in the node form and a fix for the WireGuard speed test on
IPv4-only hosts.

### Added

- **TLS ClientHello fragmentation (anti-DPI).** A **Settings → TLS Fragment**
  toggle splits the outgoing TLS ClientHello across several packets so a DPI
  box cannot match the SNI in a single read. Entirely client-side — the server
  is unaware and reassembles the stream normally. Off by default; when on, only
  proxy entry hops are routed through a `fragment` freedom outbound (chain relay
  hops, `freedom` / `blackhole` / `dns` and reserved tags are never touched),
  with tunable packet mode / length / interval. Needs a bundled xray 26.x.
- **Direct-connection switch, everywhere.** By default every SSH / panel
  operation (server test, deploy, uninstall, WireGuard clients, x-ui sync /
  healthcheck / inbounds / clients, chain create / healthcheck / clients /
  export) now dials **through the active node** — the same tunnel the LAN
  uses — instead of straight off the host. A themed **Direct** toggle in each
  page header (Servers, x-ui, Chains) and in the Deploy modal flips a single
  operation back to a direct dial (SO_MARK bypass) for reaching a box while the
  active node is down. Backend honours `?direct=` on every `/servers` and
  `/xui` route.
- **Live speed test.** The node speed test now streams Mbps as it runs, reports
  the **average after a 2 s warmup** and the **peak**, and runs over a longer
  time-box against a multi-CDN target list for a more honest number.
- **Reachability check.** One tap confirms a node actually carries traffic to
  the internet (204 through the live tunnel), separate from raw link speed.
- **Single-node URI export.** Copy a node's `vless://` (etc.) share link to the
  clipboard straight from its card.
- **SNI / REALITY-dest scanner in the node form.** Probe a candidate host for
  TLS 1.3 + HTTP/2 before saving it as the REALITY masquerade target, routed
  through the active node.

### Changed

- **xray-core bumped to 26.7.28** (baked into the backend image). The runtime
  SHA-256 pins in `install.sh` are kept in lock-step with the Dockerfile so the
  in-UI updater's post-load integrity check passes on the new binary.
- **Clearer node-card actions** — distinct icons for activate (highlighted when
  active), speed, reachability and URI export.

### Fixed

- **WireGuard speed test failing with "failed to find available ipv6 table".**
  Commercial WG configs ship both an IPv4 and an IPv6 interface address; xray's
  WireGuard netstack could not bring up the IPv6 side on an IPv4-only host and
  aborted the whole outbound. The IPv6 interface address is now dropped when
  generating the config, so WG nodes test (and route) cleanly. Only WireGuard
  was affected — vless / vmess / trojan / ss carry no interface address.

## v1.4.9 — 2026-08-02

Hotfix.

### Fixed

- **A single node with the `raw` transport 500'd the whole node list.**
  Xray (v25.x) renamed the plain `tcp` transport to `raw`, and panels emit
  `type=raw` in share links. A subscription imported such a node verbatim,
  and because the `/api/nodes` response validates every row, that one
  unknown value made the entire list endpoint fail — the dashboard then
  showed "No active node selected" even though the active node was set and
  routing fine. `raw` is now recognised as the alias for `tcp` it is:
  folded on import (URI / Clash / JSON), accepted on read, and generated as
  `tcp` in the xray config so every bundled xray version accepts it. The
  paginated `/api/nodes/page` was unaffected, which is why only the
  Dashboard's node picker broke.

## v1.4.8 — 2026-07-31

PiTun now **updates itself from the web UI**, fetching releases through the
active node so a throttled direct route is not a problem. The pinned 3x-ui
panel version moves from `v3.1.0` to `v3.6.0`, with both generations managed
side by side. The rest of the release is a sweep of logic and
frontend-to-backend interaction bugs found in a full audit — most visibly, a
deleted active node no longer sends the whole LAN out unproxied, a speed test
no longer loses its result when you paginate or leave the page, and a node
whose relay was deleted now says so instead of failing silently.

### Added

- **Updates from the UI.** **Settings -> Updates** checks GitHub, shows what is
  new and applies it with progress. The backend deliberately cannot apply an
  update itself — doing so restarts the very container serving the request —
  so it writes a request file on the shared volume and a systemd path unit on
  the host (`pitun-update.sh --agent`) does the work. Progress travels back the
  same way, which is why the panel keeps reporting correctly straight through
  the backend restart. Endpoints: `GET /api/system/update/check`,
  `GET /api/system/update/status`, `POST /api/system/update/start`.
  - "Could not reach GitHub" never renders as "you are up to date" — on a box
    that TPROXYs its own traffic a dead tunnel takes GitHub with it, so the
    reply names the route that answered (`active node` / `direct` /
    `unreachable`).
  - Installing a build older than 1.4.8 **removes this panel**, so a downgrade
    is called out before it happens, with the shell command to come back.
  - Re-installing the current version is offered as the repair path.
  - After a verified-healthy update, superseded Docker images are dropped and
    only the **3 most recent** DB snapshots are kept. Neither runs on failure:
    that is exactly when the old artefacts are worth having.
- **`scripts/pitun-update.sh` — unattended updates.** Asks GitHub for the
  latest release, compares it with the version the backend reports, and hands
  over to `install.sh` when there is something newer. The interesting part is
  the network path: this box TPROXYs its own traffic, so the updater first
  probes xray's local SOCKS inbound and fetches **through the active node**
  (useful where GitHub is throttled), falling back to the direct route when
  the tunnel is down — an update must never be blocked by the very tunnel it
  might be fixing. `--check` reports without touching anything (exit 10 when
  an update is available), `--install-timer` adds a daily systemd timer that
  reports by default and only applies with `--apply`.

### Changed

- **3x-ui pin bumped to `v3.6.0`** in `setup-xui-server.sh`, for both install
  modes (bare and x-ui-pro). The upstream installer scripts are still fetched
  at immutable commit SHAs, and now additionally verified by **sha256 content
  hash** before anything executes them (`fetch_pinned`) — a rewritten tag or a
  tampered download aborts the install instead of running.
- **Non-interactive install went env-driven.** v3.6.0's `install.sh` accepts
  `XUI_NONINTERACTIVE=1` / `XUI_SSL_MODE` / `XUI_DB_TYPE` instead of prompt
  feeding; both install branches export them, replacing the old
  `printf '4\nn\n'` pipe.

### Fixed

- **v3.6.0 moved its UI-internal controllers** (`/panel/setting/*`,
  `/panel/xray/*`) under `/panel/api/...`; the old paths answer with the new
  web UI's SPA shell or 404. `XuiClient` now probes the new mount first and
  falls back to the old one (cached per client), so API-token bootstrap and
  chain template pushes work against both v3.1.x and v3.6.x panels.
- **Add-inbound against a v3.6.0 panel** rejected the legacy empty-string
  defaults (`"tgId": ""`) in the preset's embedded client — the panel now
  parses `settings.clients[]` strictly on `inbounds/add|update`, not just on
  the per-client endpoints. Numeric/bool fields of embedded clients are now
  coerced before every inbound write.
- **Creating a proxy chain broke every ordinary inbound on the relay panel.**
  The generated `xrayTemplateConfig` declared an outbound tagged `api` and
  placed it first. Xray's Commander already owns that tag, and `outbounds[0]`
  is where traffic matching no routing rule goes — so plain inbounds (which
  have no rule) had their traffic handed to the API handler and got zero bytes
  through, while the chain itself kept working. The template now declares only
  `direct` (first, as the default egress) and `blocked`, matching the stock
  3x-ui layout. Re-saving an existing chain re-pushes a corrected template.

#### Dataplane — routing that silently did not apply

- **Deleting the active node left `active_node_id` dangling**, and the next
  config regeneration — any rule, DNS or settings edit — quietly produced a
  config with no proxy outbound, so everything meant for the tunnel went out
  direct. The health checker stayed silent because there was no node left to
  check. Deletion now re-points to a surviving node (or stops the proxy and
  says so) and re-applies the dataplane. Same for a subscription delete that
  takes the active node with it.
- **A node whose relay was deleted broke silently.** `chain_node_id` has no FK
  constraint, so the pointer survived deletion: xray skipped the outbound,
  probes followed the dead pointer, speed tests returned nothing, and the list
  still showed a healthy "chained" badge. The subscription paths (delete with
  nodes, and refresh dropping nodes that vanished from the panel) now clear
  those links and record an event naming the affected nodes, and every node
  read reports `chain_orphan` so rows broken by an older version surface too —
  the UI marks them **chain broken** and keeps the link visible so it can be
  repaired.
- **MAC rules never reached nftables.** `mac` rules are invisible to xray by
  design — nftables owns L2 — but a rule change only reloaded xray, so a new
  MAC bypass did nothing and, worse, a deleted one kept bypassing until the
  next restart. Rule changes now re-apply both layers.
- **`POST /system/mode` only wrote a setting.** Switching to Bypass on the
  Dashboard left nftables TPROXYing and xray on the old config while the UI
  reported the new mode. The switch now applies both layers.
- **Subscription refresh never reloaded xray.** A panel rotating a Reality key
  or an SNI updates the row in place — the fingerprint still matches — while
  the running xray kept dialling with stale crypto. Health checks agreed,
  because they connect to the address from the fresh row. Refresh now reloads
  when it changed a node the config actually uses.
- **`/system/start` and `/system/restart` applied nftables before xray** and
  never rolled back, so a failed start left the LAN redirected into a TPROXY
  port with nothing listening. Order reversed to match the routing-set path.
- **A circle's balancer stayed on its cold-start `random` after any reload** —
  every new connection went to a random member, including ones a failover had
  just rejected, while the UI showed one specific node. The gRPC pin now
  retries until xray's API is up, and warns instead of failing silently.
- **Failover could overwrite a manual node switch** made while it was still
  probing candidates. It now re-checks that the failed node is still active.
- **Config writes are atomic.** Overlapping writers (scheduled rotation vs. a
  manual reload vs. failover) truncated the same file in place, so a reader —
  or `xray run -test` — could see half a document.

#### Speed test

- **The result survived neither navigation nor pagination.** It lived in page
  state and was written from per-mutation callbacks, so leaving the page threw
  it away, a second test stranded the first row on "testing..." forever, and
  the spinner tracked the wrong node. Results and in-flight state now live in
  the query cache.
- **A pinned active node never showed its result at all** — the row that
  renders it existed only inside the list.
- **Speed All** dies at the reverse proxy's 120s ceiling on a large node set;
  it now says so instead of silently stopping.
- **A leaked xray process on startup timeout.** The timeout branch returned a
  3-tuple where the caller unpacked four, so cleanup ran on nothing: the
  process outlived the request, holding a temp config with the node's
  credentials. Ports are now reserved by binding instead of guessed, so a
  collision can no longer route one node's measurement through another's
  tunnel.

#### x-ui and chains

- **A failed chain poisoned the whole relay panel.** Its channels carry empty
  Reality material, and the combined template included them, so the next push
  produced an outbound with an empty `publicKey` — xray refused to start and
  every inbound on that panel went down. Only live chains are included now.
- **Deleting a trojan / shadowsocks / socks client always failed with 502.**
  Those clients have no `id` field, and the lookup matched on `id` alone. It
  now matches the natural key (`id` -> `password` -> `user` -> email).
- **Sync deleted a live trojan client's exported Node**, because the cache row
  stored an empty key that could never match the panel. New rows store the
  natural key, and legacy rows are adopted on the next sync instead of being
  treated as vanished.
- **Deleting a channel could delete another chain's Node** — the cleanup
  matched by name suffix and relay host, which two chains can share. It now
  uses the recorded export links.
- **Deleting a whole chain left its exported Nodes behind**, pointing at UUIDs
  the relay no longer knows.
- **`degraded` was documented and rendered but never set** — drift found by a
  chain healthcheck vanished when the dialog closed. It is persisted now.

#### Deploy jobs and logs

- **A failed install could show a green "Install succeeded".** A script exiting
  non-zero still finalizes the job as `succeeded` (the runner returns the
  failure as a result), and the modals rendered the raw status. Both now use
  the same projection as the tasks page.
- **A dropped WebSocket froze the deploy modal forever**, because treating the
  drop as "finished" also switched off the polling fallback.
- **Two open Logs tabs split the xray stream between them** — one shared queue
  handed each line to exactly one reader, so a backgrounded tab quietly ate
  half the lines. Each viewer now gets its own queue.
- **Errors from system mutations were swallowed** — start/stop/mode/active
  node/settings had no error handling anywhere, so a 400 explaining a rejected
  `inbound_mode`, or a 503 asking you to retry, produced nothing on screen.

## v1.4.7 — 2026-07-29

The User-Agent presets a subscription fetches with are no longer baked into the
code. They now live in an editable table you manage from the Subscriptions page,
and a template can carry extra request headers for panels that check more than
the UA string.

### Added

- **User-Agent templates.** The nine presets that used to be two hardcoded Python
  dicts (`v2ray`, `clash`, `sing-box`, the four Happ profiles, `streisand`,
  `chrome`) are now ordinary rows in a new `useragenttemplate` table, seeded by
  migration `018`. Manage them from **Subscriptions → UA templates**: a table with
  add / edit / delete, and an editor for the name, key, UA string and description.
  Bumping Happ's app version or Chrome's build number when a panel starts rejecting
  a stale fingerprint no longer needs a redeploy.
- **Custom request headers per template.** A template can declare extra headers
  sent alongside its User-Agent — for panels that also gate on an API key, a
  `Referer`, or a device fingerprint. Leaving a value **empty removes** that header
  from the request instead of sending it blank, which is how you drop
  `Accept-Encoding` for panels that mishandle gzip.
- **Export / import.** Download the whole catalogue as JSON from the Subscriptions
  header and restore it on another install. Import is additive by default; matching
  keys are skipped unless you choose to overwrite them in place (which keeps their
  row id, so subscriptions stay attached).
- **Guard rails.** Renaming a template's key re-points every subscription using it
  in the same transaction. Deleting one that is still in use returns a `409` naming
  the affected subscriptions, with an explicit "delete anyway" path.

### Fixed

- **Header injection and non-ASCII in operator-supplied headers.** Values are now
  validated on save. A `CR`/`LF` inside a header value is forwarded verbatim by
  httpx — a smuggled extra header — and a non-ASCII value raises at send time and
  would have surfaced hours later as an opaque `last_error` on the subscription.
  Both are rejected with a clear message instead, in the UI and at the API.
- **Esc no longer closes a whole modal stack.** `useEscapeKey` listens on
  `document`, so two open dialogs both closed on one keypress, discarding the
  half-filled form underneath. `ModalShell` gained `closeOnEscape` for nested
  dialogs.

### Notes

- Migration `018` seeds the presets with the exact User-Agent strings the hardcoded
  map used, keyed by the same slugs already stored in `subscription.ua` — existing
  subscriptions send a byte-identical header set after the upgrade. A regression
  test pins this by replaying the v1.4.6 logic and diffing the result.
- `subscription.ua` stays a plain string, not a foreign key: an unknown key falls
  back to the built-in User-Agent map rather than breaking a refresh, so a deleted
  template degrades instead of failing.

## v1.4.6 — 2026-07-03

Two fixes from field-testing multi-hop chains: removing a node's chain (or its
Server link) now actually saves, and a failed speed test now tells you *why*
instead of a blank "couldn't start".

### Fixed

- **Removing a chain / Server link now persists.** Clearing a node's *"Chain via"*
  relay (or its optional Server link) never stuck — you could add a chain but never
  remove it. The form sent the cleared field as `undefined`, which `JSON.stringify`
  drops, so it never reached the `PATCH` body and the backend's
  `model_dump(exclude_unset=True)` kept the old value. The form now sends an explicit
  `null`, which the backend nulls out correctly. Affects `chain_node_id` and `server_id`.
- **Speed test surfaces the real xray error.** A node test that couldn't start its
  throwaway xray always showed a generic *"Failed to start temp xray"*, hiding the
  cause. `_start_temp_xray` now propagates xray's own error into the result, so the UI
  shows the actionable reason — e.g. `xray: invalid "shortId"` or `xray: empty publicKey`.

## v1.4.5 — 2026-06-18

Node-circle rotation that no longer drops connections, a fix so switching the
active node (WireGuard chains especially) actually takes effect, and node-import
quality-of-life (drag & drop, `.conf` files, name-from-filename).

### Fixed

- **Switching the active node now applies immediately.** `POST /api/system/active-node`
  only wrote the DB row — it never regenerated the xray config or reloaded xray,
  so activating a node (a WireGuard chain especially) left traffic exiting the
  *previous* node while the UI showed the new one. It now regenerates + hot-reloads.
- **Balancer override silently failed.** `xray api bo` was called with the balancer
  tag positionally instead of via `-b <tag>` ("balancer tag not specified"), so
  every runtime balancer override was a no-op.

### Added

- **Seamless NodeCircle rotation.** An enabled circle now routes proxy traffic at a
  per-circle xray balancer over all preloaded members; rotation hot-swaps the
  selected node via the gRPC `balancerOverride` API — no xray restart, so live
  connections finish on their current node and only new ones move. Manual
  active-node switches into a circle pin the balancer the same way.
- **Node import UX.** The upload box now accepts **drag & drop**, the file picker
  allows WireGuard `.conf` (and `.ini`), and a **"name from filename"** toggle names
  a single-config import after the dropped file.

### Notes

- WireGuard can still only be a chain's **exit** hop — it can't carry transit as a
  relay (verified again live: a mid-chain WG forwards 0 bytes). The circle balancer
  preloads each member together with its stream relay, so WG-circle rotation works.

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
