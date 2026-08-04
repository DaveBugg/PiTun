# Base image pinned by digest (vs `python:3.11-slim` mutable tag) so a
# Docker Hub repush can't silently change what we ship. Dependabot will
# bump the digest in PR form when a new patch lands. Override at build
# time via --build-arg PYTHON_IMAGE=... if you need a different base.
ARG PYTHON_IMAGE=python:3.11-slim@sha256:6d85378d88a19cd4d76079817532d62232be95757cb45945a99fec8e8084b9c2
ARG XRAY_VERSION=26.7.28

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
# SHA-256 digests of the unpacked xray binary per arch, taken from the
# upstream XTLS/Xray-core v${XRAY_VERSION} release. Pinning the digest
# at build-time gives us defence-in-depth against:
#   1. **Upstream republish**: the GitHub release of a tagged version is
#      mutable — if upstream silently re-uploads a binary with the same
#      tag, our supply chain would change underneath us. Pinning makes
#      any such change a hard build failure that we have to consciously
#      bump.
#   2. **Transport corruption** during curl: in-flight bit-flips on the
#      build runner's network or storage that don't trip TLS/Content-
#      Length checks. (Observed on a 1.3 user device during v1.3.0-
#      beta.4 install — see the post-mortem in notes.md.)
#
# When bumping XRAY_VERSION above, regenerate these by:
#   for a in 64 arm64-v8a arm32-v7a; do
#     curl -sL https://github.com/XTLS/Xray-core/releases/download/v${VER}/Xray-linux-$a.zip \
#       | bsdtar -xOf- xray | sha256sum | awk -v a=$a '{print a, $1}'
#   done
ARG XRAY_SHA256_AMD64=64d46afb80adea1bf97a0d467e83f4a9ac1ebd0995891e84bca3f1a1d1affb1d
ARG XRAY_SHA256_ARM64=4b8af237444801bf17b3dc10a1c5c24581fbe3d433eba3d78c6c3a0da1df56fc
ARG XRAY_SHA256_ARM=63a2fb09b928d1bde3a0df9b663d1936f13b53cd0db40c8ce85e4accdc779860
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl unzip; \
    case "${TARGETARCH:-amd64}" in \
        amd64) XRAY_ARCH=64;          EXPECTED_SHA="${XRAY_SHA256_AMD64}" ;; \
        arm64) XRAY_ARCH=arm64-v8a;   EXPECTED_SHA="${XRAY_SHA256_ARM64}" ;; \
        arm)   XRAY_ARCH=arm32-v7a;   EXPECTED_SHA="${XRAY_SHA256_ARM}"   ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    echo "Downloading: ${URL}"; \
    curl -fsSL --retry 3 -o /tmp/xray.zip "${URL}"; \
    mkdir -p /tmp/xray-extract; \
    unzip -q /tmp/xray.zip -d /tmp/xray-extract; \
    # Pinned-sha verification — fails the build if the unpacked binary
    # doesn't match the expected digest for this arch. See the ARG
    # block above for what to do when the version changes.
    echo "${EXPECTED_SHA}  /tmp/xray-extract/xray" | sha256sum -c -; \
    install -m 0755 /tmp/xray-extract/xray /usr/local/bin/xray; \
    /usr/local/bin/xray version | head -1

# ── 2. base ──────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS base

# Don't write *.pyc files at runtime. Combined with the explicit `find …
# -delete` after pip install below, this keeps the final image free of
# bytecode caches. Why bother:
#   * Cross-arch builds via QEMU (older release.yml flow) sometimes
#     wrote *.pyc files that didn't match the runtime architecture's
#     load alignment, surfacing as `bad marshal data` ImportError on
#     RPi 4 — observed in v1.3.0-beta.1 / .2 arm64 image. Even with
#     native runners (since v1.3.0-beta.3) we keep this guard belt-
#     and-braces, since bind-mounted source on the host can carry
#     stale .pyc from any earlier image's Python.
#   * Smaller image (~30-40 MB savings on a slim base).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
# `--no-compile` skips the post-install `compileall` step so pip doesn't
# generate *.pyc during install. The `find -delete` mop-up catches
# anything wheels embedded directly (rare but happens). Together with
# PYTHONDONTWRITEBYTECODE=1 (set above), the resulting image has zero
# *.pyc files, removing an entire class of bytecode-vs-runtime mismatches.
RUN pip install --no-cache-dir --no-compile -r requirements.txt && \
    find /usr/local/lib -name '*.pyc' -delete 2>/dev/null && \
    find /usr/local/lib -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

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
