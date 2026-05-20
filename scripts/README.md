# PiTun — Scripts reference

🇬🇧 English · [🇷🇺 На русском](README.ru.md)

> Reference for everything under `scripts/`. For project overview see
> the [main README](../README.md). For the recommended install path —
> the **one-shot `install.sh` in the repo root** — see the
> [Quick install](../README.md#quick-install) section.
> Air-gapped install bundle workflow is in
> [`docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md).

## TL;DR

Everyday flow on a fresh Raspberry Pi 4/5:

```bash
curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
     | sudo bash -s -- --version v1.3.3
```

That's it — the one-shot installer (in the repo root, not in `scripts/`)
handles everything. The scripts in this folder are for **specialised
flows**: manual multi-step installs, remote VPS provisioning, offline
bundle building, maintenance, uninstall.

---

## Script inventory

### Host install (the "everything" path lives one level up)

| Script | Purpose |
|---|---|
| [`../install.sh`](../install.sh) | **Recommended.** One-shot host installer. Handles deps, Docker, xray, geo data, image pulls / build, compose up, host network config (since 1.3.3). Supports `--version`, `--build`, `--offline DIR`, `--fix-blockers`. |
| `setup.sh` | Lightweight all-in-one (older path). Use when you already cloned the repo and want a minimal install without the network/offline features of `install.sh`. |
| `setup-vm.sh` | Full integration setup on a clean Debian 12 / Ubuntu 22.04 VM. Differs from RPi flow: uses `docker.io`, disables avahi (frees UDP/5353), clones from git. |

### Multi-step manual flow (advanced)

Use these when you need to inspect each phase separately. `install.sh`
automates the same steps; reach for these only if something's wrong or
you want maximum control.

| Script | Stage |
|---|---|
| `01-first-boot.sh` | First-boot config on a fresh RPi: SSH keys, static IP, hostname, IP forwarding, disable desktop. |
| `02-install-stack.sh` | Docker, Compose v2, xray-core, nftables, system deps. |
| `03-deploy.sh` | Generate `.env`, build/load images, `docker compose up -d`. |
| `04-migrate.sh` | Alembic migrations: `--status` / apply pending / `--fresh` (reset DB). |

### Remote VPS provisioning (called by the backend over SSH)

These run on the **VPS** that hosts a proxy server, not on the PiTun
gateway itself. The backend's "Deploy" button on the Servers page
fires them over SSH. You can run them by hand for testing or recovery.

| Pair | Protocol |
|---|---|
| `setup-naive-server.sh` / `uninstall-naive-server.sh` | NaiveProxy (Caddy + forwardproxy, Let's Encrypt cert, systemd unit). |
| `setup-wireguard-server.sh` / `uninstall-wireguard-server.sh` | WireGuard (multi-client, sub-command dispatcher: `install` / `add-client` / `remove-client` / `list-clients`). |
| `setup-xui-server.sh` / `uninstall-xui-server.sh` | 3x-ui or x-ui-pro (mode auto-selected by whether `DOMAIN=` is set). |
| `cleanup-go.sh` | Helper sweep of the Go toolchain + build cache that the two heavyweight installers (xui-pro and naive) pull in. Called automatically; safe to source from any post-install context. |

### Offline / air-gapped install

| Script | Purpose |
|---|---|
| `build-offline-bundle.sh` | Build pitun-backend / -frontend / -naive images **and** re-export 3rd-party bases (nginx, docker-socket-proxy) as `.tar.gz` under `docker/offline/`. ARM64 and AMD64 supported via `ARCH=`. |
| `deploy-offline.sh` | rsync source + scp tarballs + `docker load` + retag + migrate + `compose up` on a target host. |
| `make-offline-bundle.sh` | Assembles the **single-directory drop** that `install.sh` auto-detects (`pitun-src.tar.gz`, `pitun-backend.tar.gz`, etc.). See [`docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md). |

### Maintenance

| Script | Purpose |
|---|---|
| `cleanup.sh` | Daily cron — prunes dangling Docker images / build cache + restarts xray (it slowly leaks ~500 MB if left running). Installed by `install.sh`. |
| `update_geo.sh` | Refresh `geoip.dat` + `geosite.dat` from the configured upstream profile. Standalone or via cron. |
| `change-network.sh` | Change host IP / gateway when moving PiTun to a different LAN. Updates network manager + PiTun DB + restarts services. Pre-1.3.3 path; the Settings page now exposes the same thing. |
| `nftables.sh` | Manual `apply` / `flush` / `status` / `bypass-mac <mac>`. PiTun's backend manages nftables automatically — use this only for troubleshooting. |
| `reset-password.sh` | Reset `admin` password (no arg → `password`). |
| `e2e-test.sh` | E2E API smoke test for VM environments. |

### Uninstall

| Script | Purpose |
|---|---|
| [`uninstall.sh`](uninstall.sh) | **Recommended.** Remove the full PiTun stack: containers + images + volumes + install dirs, with `--dry-run`, `--purge`, `--keep-data`, `--keep-network`, etc. Safe by default — asks before touching host-level state. See [section below](#uninstall). |
| `uninstall-naive-server.sh` | VPS-side: undo what `setup-naive-server.sh` did. |
| `uninstall-wireguard-server.sh` | VPS-side: undo what `setup-wireguard-server.sh install` did. |
| `uninstall-xui-server.sh` | VPS-side: undo both x-ui modes (full xui-pro stack + bare 3x-ui). |

---

## What gets created where

Standard host install via `install.sh` puts:

```
/opt/pitun/                ← Main install dir
├── backend/app/           ← Python sources (bind-mounted into pitun-backend)
├── backend/alembic/       ← DB migrations
├── frontend/dist/         ← Built React SPA (served by pitun-frontend nginx)
├── data/                  ← SQLite DB + persistent state
│   └── pitun.db
├── docker-compose.yml     ← Stack definition (4 services)
└── .env                   ← SECRET_KEY + LAN settings

/etc/pitun/                ← Naive sidecar configs
/var/lib/pitun/            ← Reserved for future persistent state
/tmp/pitun/                ← xray runtime (config.json, logs)
/usr/local/bin/xray        ← Standalone xray binary (host)
/usr/local/share/xray/     ← geoip.dat / geosite.dat / GeoLite2-Country.mmdb
/etc/cron.d/pitun-cleanup  ← Daily maintenance trigger
/swapfile                  ← 2 GB swap (created on hosts without swap)
```

The `uninstall.sh` script knows all of these — see below for cleanup.

---

## Docker services

| Container | Network mode | Function |
|---|---|---|
| `pitun-backend` | host | FastAPI + xray + nftables manager |
| `pitun-frontend` | bridge | nginx serving the React SPA bundle |
| `pitun-nginx` | bridge → host:80 | Reverse proxy (UI + WebSocket fan-in) |
| `docker-socket-proxy` | bridge | Locked-down Docker API for naive sidecar lifecycle |

`pitun-backend` uses `network_mode: host` because it needs raw nftables
+ TPROXY access. The other services live on a Docker bridge and reach
the backend via `extra_hosts: ["backend:host-gateway"]`.

When the backend deploys naive nodes from the UI, additional containers
appear with names like `pitun-naive-<node-id>`.

---

## Common maintenance recipes

### Apply migrations after a code update

```bash
cd /opt/pitun
git pull
docker compose up -d --build
bash scripts/04-migrate.sh
bash scripts/04-migrate.sh --status
```

### Reset admin password

```bash
docker exec pitun-backend bash /app/scripts/reset-password.sh myNewPass
```

### Refresh GeoData by hand

```bash
sudo bash /opt/pitun/scripts/update_geo.sh
# Reload xray to pick up the new tag tables:
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/system/restart-xray
```

### Inspect logs

```bash
docker compose logs -f                  # all containers
docker logs pitun-backend --tail 50     # just the API
docker logs pitun-nginx --tail 50       # access / error logs
```

---

## Uninstall

To completely remove PiTun from the host:

```bash
# Interactive — asks before host-level operations:
sudo bash /opt/pitun/scripts/uninstall.sh

# Headless re-image prep — wipes everything including host tweaks:
sudo bash /opt/pitun/scripts/uninstall.sh --purge

# Preview without changing anything:
sudo bash /opt/pitun/scripts/uninstall.sh --dry-run

# Preserve DB + configs for a future reinstall:
sudo bash /opt/pitun/scripts/uninstall.sh --yes --keep-data
```

The script handles every installer permutation (registry-pulled,
locally-built, offline-bundled, dev stack, naive sidecars,
hot-deploy backup dirs) and is idempotent — re-running on a
cleaned host downgrades missing artefacts to "skip" rather than
erroring.

Flags overview:

| Flag | Effect |
|---|---|
| `--dry-run` | Preview only. |
| `-y` / `--yes` | No prompts on standard removals. |
| `--purge` | Everything, including host network config (5 s warning before that step). |
| `--keep-data` | Preserve DB + configs. |
| `--keep-network` | Never touch host network manager files. |
| `--keep-xray` | Leave `/usr/local/bin/xray` + geo data. |
| `--keep-swap` | Leave `/swapfile`. |
| `--prefix PATH` | Override install-dir detection. |

Run `sudo bash scripts/uninstall.sh --help` for the full list.

**Phase 7 (host network config) is the only HIGH-RISK step** — it
can break SSH if the operator changed PiTun's IP via the Settings
UI. The script warns about this and refuses to silently proceed
under `--yes` alone (`--purge` adds a 5 s Ctrl-C window). Always
open a second SSH session before confirming if you're not on a
local console.

---

## Building images yourself

`install.sh --build` builds images on the target host (slow on RPi).
The recommended path is to build on a beefier box and ship tarballs:

```bash
# Build amd64 + arm64 bundles on a build host with `docker buildx`:
ARCH=arm64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh
ARCH=amd64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh

# Ship to a target RPi:
ARCH=arm64 bash scripts/deploy-offline.sh user@pitun.local ~/.ssh/id_ed25519
```

Output lands under `docker/offline/`:

```
pitun-backend-arm64-<version>.tar.gz
pitun-frontend-arm64-<version>.tar.gz
pitun-naive-arm64-<version>.tar.gz
nginx-arm64-<version>.tar.gz
docker-socket-proxy-arm64-<version>.tar.gz
```

Environment knobs (`build-offline-bundle.sh`):

| Variable | Default | Notes |
|---|---|---|
| `ARCH` | `arm64` | `arm64` for Raspberry Pi 4/5; `amd64` for mini-PCs. |
| `VERSION` | from `backend/app/config.py` (`APP_VERSION`) | Override only when explicitly preparing an out-of-sync release. |
| `BUILDER` | `pitun-builder` | `docker buildx` builder name. |
| `MIRROR` | `mirror.gcr.io` | Hub mirror for `library/*` bases. |

---

## See also

- [Main README](../README.md) — project overview, features, screenshots
- [`../docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md) — air-gapped install workflow
- [Russian version of this page](README.ru.md)
