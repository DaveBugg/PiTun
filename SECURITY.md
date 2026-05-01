# Security Policy

## Supported versions

PiTun is a small project with one active line of releases — the latest
`v1.x` minor on the `master` branch. Security fixes go into the next
patch release; older minors aren't backported.

| Version | Supported          |
| ------- | ------------------ |
| latest `v1.x` | :white_check_mark: |
| anything else | :x:               |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, use **GitHub's private vulnerability reporting**:

> https://github.com/DaveBugg/PiTun/security/advisories/new

This sends the report to the maintainer privately, lets us coordinate
a fix and CVE assignment if applicable, and avoids exposing users
before a patch is available.

If GitHub's private reporting is unavailable in your locale, email
the maintainer at the address listed on the [GitHub profile](https://github.com/DaveBugg).

### What to include

- A clear description of the issue (what happens, where, on which
  version) and the impact (data exposure, RCE, DoS, etc.).
- Reproduction steps — the smallest viable PoC, ideally as a unit
  test or a `curl` one-liner.
- Suggested fix if you have one.

### Response time

PiTun is a hobby project maintained in spare time, so we deliberately
avoid hard SLAs. As a rough guide:

- **Acknowledgement:** as soon as the maintainer sees the report —
  usually within a few days.
- **Triage:** when bandwidth allows — we'll either confirm severity,
  ask for more info, or explain why we don't think it's exploitable.
- **Fix + release:** depends on severity, complexity, and how busy
  life is. Critical issues are prioritised; lower-severity ones bundle
  into the next regular release.

If a report sits without any reply for more than a couple of weeks,
feel free to ping us — it usually means the email landed in the wrong
folder, not that we're ignoring you.

### Coordinated disclosure

If you intend to publish an advisory or talk publicly about the
issue, please:

- Wait until a fixed release is available.
- Coordinate the disclosure date with us through the GitHub advisory.

We're a tiny team — please be patient and we'll do the same.

## Threat model — what's in scope

**In scope:**
- The PiTun backend HTTP API (FastAPI on `:8000`)
- The web UI (frontend SPA + nginx reverse proxy on `:80`)
- The `install.sh` one-touch installer (downloads-then-deploys flow)
- nftables / TPROXY rule generation logic
- xray-core config generation (we own the JSON we hand to xray)
- Docker image build chain (backend + naive sidecar)
- Default credentials and authentication flow
- Anything that could let an attacker reach the LAN, exfiltrate
  config / DB / `.env`, or bypass the proxy

**Out of scope (report upstream):**
- xray-core itself → https://github.com/XTLS/Xray-core
- naiveproxy → https://github.com/klzgrad/naiveproxy
- The Loyalsoldier GeoIP / GeoSite datasets → upstream repo
- Underlying Linux kernel, Docker, nginx, Python / Node runtime —
  report to the respective project, then ping us if PiTun's defaults
  amplify the issue.

## Hardening recommendations for users

- Change the default `admin` password on first login (via
  *Settings → Account*). The default is documented and public.
- Bind the web UI to LAN only — never expose port 80 to the internet
  unless behind your own auth proxy.
- Set a strong `SECRET_KEY` in `.env` (the installer auto-generates
  one; if you ran without it, regenerate via `openssl rand -hex 32`).
- Keep your host kernel + Docker patched.
- Watch the [Releases page](https://github.com/DaveBugg/PiTun/releases)
  for security-relevant updates and re-run `install.sh` to apply.
