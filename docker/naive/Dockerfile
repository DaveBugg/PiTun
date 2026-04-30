# PiTun NaiveProxy sidecar image
#
# One container per NaiveProxy node. Listens on loopback only
# (127.0.0.1:<port>), xray connects to it via SOCKS5.
#
# Binary comes from klzgrad/naiveproxy GitHub releases.
# Override NAIVE_VERSION as needed: docker build --build-arg NAIVE_VERSION=v138.0.7204.92-1 ...

# Base image: debian:bookworm-slim (glibc). naiveproxy is a Chromium fork
# and is dynamically linked against glibc — running its binary under
# Alpine (musl) fails with the classic "exec .../naive: no such file or
# directory" even though the binary is present, because the glibc dynamic
# linker (/lib/ld-linux-aarch64.so.1 or /lib64/ld-linux-x86-64.so.2) is
# missing. Alpine + gcompat can work in some cases but is unreliable for
# Chromium-based binaries; bookworm-slim (~30 MB compressed) is the
# predictable choice.
ARG BASE_IMAGE=debian:bookworm-slim
FROM ${BASE_IMAGE} AS fetcher

ARG NAIVE_VERSION=v147.0.7727.49-1
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    case "${TARGETARCH}" in \
      amd64) NAIVE_ARCH=x64 ;; \
      arm64) NAIVE_ARCH=arm64 ;; \
      arm)   NAIVE_ARCH=arm ;; \
      *) echo "Unsupported arch: ${TARGETARCH}" >&2 ; exit 1 ;; \
    esac; \
    URL="https://github.com/klzgrad/naiveproxy/releases/download/${NAIVE_VERSION}/naiveproxy-${NAIVE_VERSION}-linux-${NAIVE_ARCH}.tar.xz"; \
    echo "Downloading: ${URL}"; \
    curl -fsSL -o /tmp/naive.tar.xz "${URL}"; \
    mkdir -p /tmp/naive-extract; \
    tar -xJf /tmp/naive.tar.xz -C /tmp/naive-extract; \
    BIN=$(find /tmp/naive-extract -name naive -type f -executable | head -1); \
    [ -n "$BIN" ] || { echo "naive binary not found in tarball" >&2; exit 1; }; \
    install -m 0755 "$BIN" /usr/local/bin/naive; \
    /usr/local/bin/naive --version || true

FROM ${BASE_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system naive \
 && useradd --system --gid naive --no-create-home --shell /usr/sbin/nologin naive

COPY --from=fetcher /usr/local/bin/naive /usr/local/bin/naive

USER naive
WORKDIR /etc/naive

# Config path mounted from host: /etc/pitun/naive/<node_id>.json -> /etc/naive/config.json
ENTRYPOINT ["/usr/local/bin/naive"]
CMD ["/etc/naive/config.json"]
