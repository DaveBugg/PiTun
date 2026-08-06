# Changelog

All notable user-facing changes to PiTun. Full per-release detail lives in the
[GitHub Releases](https://github.com/DaveBugg/PiTun/releases); this file is the
committed summary.

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
