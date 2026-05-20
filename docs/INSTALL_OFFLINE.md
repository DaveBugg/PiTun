# Offline / local-file install

`install.sh` defaults to pulling everything from GitHub (release tag → six
artefacts: source tarball, backend image, naive image, frontend dist, geoip,
geosite). On boxes where GitHub is unreachable — RPi behind a strict
captive portal, a corporate LAN that blocks `codeload.github.com`, a fresh
VPS in a region where the GitHub CDN keeps timing out — drop the same six
files next to `install.sh` and run it normally; the script auto-detects the
local copies and skips the network fetch for each one it finds.

## TL;DR

1. On a box that **can** reach GitHub, grab the artefacts for the release
   you want to install (replace `v1.3.0-beta.8` with the target tag and
   `arm64` with `amd64` if you're targeting a x86 host):

   ```bash
   tag=v1.3.0-beta.8
   arch=arm64
   mkdir pitun-offline && cd pitun-offline
   curl -fLO "https://codeload.github.com/DaveBugg/PiTun/tar.gz/refs/tags/${tag}" -o pitun-src.tar.gz
   curl -fLO "https://github.com/DaveBugg/PiTun/releases/download/${tag}/pitun-backend-${tag}-${arch}.tar.gz"
   mv pitun-backend-${tag}-${arch}.tar.gz pitun-backend.tar.gz
   curl -fLO "https://github.com/DaveBugg/PiTun/releases/download/${tag}/pitun-naive-${tag}-${arch}.tar.gz"
   mv pitun-naive-${tag}-${arch}.tar.gz pitun-naive.tar.gz
   curl -fLO "https://github.com/DaveBugg/PiTun/releases/download/${tag}/pitun-frontend-${tag}.tar.gz"
   mv pitun-frontend-${tag}.tar.gz pitun-frontend.tar.gz
   curl -fLO "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
   curl -fLO "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"
   curl -fLO "https://raw.githubusercontent.com/DaveBugg/PiTun/${tag}/install.sh"
   chmod +x install.sh
   ```

2. Transfer the whole directory to the air-gapped box (USB / scp / etc.).

3. On the target:

   ```bash
   cd pitun-offline
   sudo bash install.sh --non-interactive
   ```

That's it. The script logs `Auto-detected pre-downloaded artefacts in <dir>`
and `using local: …` for every file it picks up off disk; anything missing
falls back to a normal HTTPS download.

## File list (auto-detected)

| Filename | Source | Notes |
| --- | --- | --- |
| `pitun-src.tar.gz` | `codeload.github.com/DaveBugg/PiTun/tar.gz/refs/tags/<tag>` | The whole repo at the chosen tag. Required: install.sh reads `docker-compose.yml` + setup scripts out of it. |
| `pitun-backend.tar.gz` | release asset `pitun-backend-<tag>-<arch>.tar.gz` | `docker load`-able image. Architecture must match the host (`arm64` for RPi, `amd64` for VPS). |
| `pitun-naive.tar.gz` | release asset `pitun-naive-<tag>-<arch>.tar.gz` | Same `docker load` format, naive sidecar image. |
| `pitun-frontend.tar.gz` | release asset `pitun-frontend-<tag>.tar.gz` | Plain tar of the Vite `dist/` output. No arch suffix — pure static files. |
| `geoip.dat` | `Loyalsoldier/v2ray-rules-dat` latest release | Bind-mounted into the backend container. |
| `geosite.dat` | `Loyalsoldier/v2ray-rules-dat` latest release | Same. |

All six are independent — you can drop only `geoip.dat`/`geosite.dat`
if those are the only ones the network refuses to fetch, and the
script will download the rest as usual.

## How auto-detection works

When `install.sh` starts, it resolves its own location (`${BASH_SOURCE[0]}`)
and scans that directory for any of the six filenames above. The first
hit promotes the directory to `OFFLINE_DIR`; the script then symlinks
each present file into its temp staging area and emits an info log so
you can see what was picked up.

The auto-detect path is **skipped** when:

- The script was piped from `curl` (no `BASH_SOURCE[0]`).
- An explicit `--offline DIR` flag is passed (the operator's choice
  wins).
- None of the six expected filenames are present.

## Explicit `--offline DIR`

Useful when the artefacts live in a different directory than the
script itself:

```bash
sudo bash install.sh --offline /mnt/usb/pitun-offline --non-interactive
```

Same hybrid semantics as auto-detect: each file in `DIR` is used as-is,
each missing file falls back to a download.

## `--build` (build from source)

If the release page is missing the arch-specific images you need
(e.g. installing on a brand-new architecture before a release has been
cut), pass `--build` to force `docker compose build` against the source
tarball:

```bash
sudo bash install.sh --offline . --build --non-interactive
```

You still need `pitun-src.tar.gz`, `geoip.dat`, `geosite.dat` locally
(or accessible online) but you do **not** need any of the
`pitun-backend.tar.gz` / `pitun-naive.tar.gz` / `pitun-frontend.tar.gz`
image artefacts — they'll be built from `Dockerfile`s inside the source
tarball. Build on the RPi takes ~25 min on the slow path; ~5 min on a
fast VPS.

## Re-runs

`install.sh` clears its staging directory on each run (it's a temp
mount in `/tmp/pitun-install-*`), so the auto-detect logic re-scans
the offline directory every time. Safe to leave a populated offline
dir as your default install path — pulling in newer images is just
a matter of replacing the four image tarballs and re-running.

## Uninstall (air-gapped or otherwise)

The bundled `uninstall.sh` runs entirely off the local filesystem —
no network needed. After an offline install:

```bash
# Preview what would be removed:
sudo bash /opt/pitun/scripts/uninstall.sh --dry-run

# Standard removal (asks before host-level changes):
sudo bash /opt/pitun/scripts/uninstall.sh

# Full re-image prep, no prompts:
sudo bash /opt/pitun/scripts/uninstall.sh --purge
```

The script handles the offline install path identically to a
registry-pulled one: it finds containers / images / volumes by
name pattern (`pitun-backend`, `pitun-frontend`, `pitun-naive`,
`docker-socket-proxy`, plus any `pitun-naive-<node-id>` sidecars)
regardless of whether they were `docker load`ed from a tarball or
pulled from a registry.

Useful flags for air-gapped boxes:

| Flag | Why it matters offline |
|---|---|
| `--keep-data` | Survives `data/` (DB + configs) so a re-install from the same offline bundle picks up where the previous one left off. |
| `--keep-network` | If the host's static IP / DNS was set via PiTun's UI, this prevents the uninstaller from touching the network manager — safer when there's no console fallback. |
| `--keep-xray` | If the same xray binary is used by another tool on the host. |
| `--prefix PATH` | Use when the offline install lived outside `/opt/pitun`. |

See the [main scripts/README.md](../scripts/README.md#uninstall)
for the full flag list and removal-phase breakdown.
