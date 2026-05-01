# Base image pinned by digest (vs `python:3.11-slim` mutable tag) so a
# Docker Hub repush can't silently change what we ship. Dependabot will
# bump the digest in PR form when a new patch lands. Override at build
# time via --build-arg PYTHON_IMAGE=... if you need a different base.
ARG PYTHON_IMAGE=python:3.11-slim@sha256:6d85378d88a19cd4d76079817532d62232be95757cb45945a99fec8e8084b9c2
ARG XRAY_VERSION=26.3.27

# ── 1. xray fetcher stage ─────────────────────────────────────────────────────
# Downloads the official XTLS/Xray-core release matching the build platform
# (TARGETARCH set by buildx) and extracts just the `xray` binary. Keeping
# this in a separate stage means the production image doesn't carry curl +
# unzip and the downloaded zip just to ship one binary.
#
# Used to be bind-mounted from /usr/local/bin/xray on the host (install.sh
# put it there). As of v1.2.0 the binary is baked into the backend image
# so a fresh `docker pull` is enough — no host-side prerequisite.
FROM ${PYTHON_IMAGE} AS xray-fetcher
ARG XRAY_VERSION
ARG TARGETARCH
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl unzip; \
    case "${TARGETARCH:-amd64}" in \
        amd64) XRAY_ARCH=64 ;; \
        arm64) XRAY_ARCH=arm64-v8a ;; \
        arm)   XRAY_ARCH=arm32-v7a ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    echo "Downloading: ${URL}"; \
    curl -fsSL --retry 3 -o /tmp/xray.zip "${URL}"; \
    mkdir -p /tmp/xray-extract; \
    unzip -q /tmp/xray.zip -d /tmp/xray-extract; \
    install -m 0755 /tmp/xray-extract/xray /usr/local/bin/xray; \
    /usr/local/bin/xray version | head -1

# ── 2. base ──────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nftables \
    iproute2 \
    net-tools \
    arp-scan \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bundled xray-core binary. The geoip.dat / geosite.dat databases stay on
# the host (bind-mounted RW into /usr/local/share/xray) so they remain
# user-updatable from the UI without an image rebuild.
COPY --from=xray-fetcher /usr/local/bin/xray /usr/local/bin/xray

# Production stage
FROM base AS production

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /tmp/pitun /app/data

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# Dev stage — includes tests and dev dependencies
FROM base AS dev

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/
COPY tests/ ./tests/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /tmp/pitun /app/data

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
