"""Hysteria2 on an x-ui panel: the TLS certificate, and turning an inbound
client into a PiTun node / share link.

Hysteria2 is QUIC, so it has no Reality: the server needs a certificate.
  * x-ui-pro: the Let's Encrypt certificate that already covers the panel's
    domain is reused, and clients verify it normally.
  * bare: no domain, so a self-signed certificate is made on the VPS and the
    node pins its SHA-256 (xray removed `allowInsecure`).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.core.cert_pin import try_normalize_pin
from app.core.ssh import exec_remote_script
from app.core.xui_api import parse_inbound_field

logger = logging.getLogger(__name__)

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_CERT_DIR = "/etc/x-ui/pitun-hy2"

_PREPARE = r"""set -e
CRT=""; KEY=""
if [ -n "$DOMAIN" ] && [ -d /etc/letsencrypt/live ]; then
  for d in /etc/letsencrypt/live/*/; do
    [ -f "${d}fullchain.pem" ] || continue
    if openssl x509 -in "${d}fullchain.pem" -noout -ext subjectAltName 2>/dev/null \
       | grep -qiE "DNS:[[:space:]]*(\*\.${DOMAIN#*.}|${DOMAIN})([[:space:]]|,|$)"; then
      CRT="${d}fullchain.pem"; KEY="${d}privkey.pem"; break
    fi
  done
fi
if [ -z "$CRT" ]; then
  D=__CERT_DIR__; mkdir -p "$D"; chmod 700 "$D"
  CRT="$D/hy2-$PORT.crt"; KEY="$D/hy2-$PORT.key"
  if [ ! -s "$CRT" ] || [ ! -s "$KEY" ]; then
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
      -keyout "$KEY" -out "$CRT" -days 3650 -subj "/CN=$SNI" \
      -addext "subjectAltName=DNS:$SNI" >/dev/null 2>&1
    chmod 600 "$KEY"
  fi
fi
echo "CERT=$CRT"; echo "KEY=$KEY"
SUBJ=$(openssl x509 -in "$CRT" -noout -subject 2>/dev/null | sed 's/^subject= *//')
ISSU=$(openssl x509 -in "$CRT" -noout -issuer 2>/dev/null | sed 's/^issuer= *//')
if [ -n "$SUBJ" ] && [ "$SUBJ" = "$ISSU" ]; then
  echo "SELF=1"
  echo "PIN=$(openssl x509 -in "$CRT" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')"
else
  echo "SELF=0"
fi
""".replace("__CERT_DIR__", _CERT_DIR)

_PIN_ONLY = r"""set -e
CRT="$CERT"
[ -f "$CRT" ] || { echo "MISSING=1"; exit 0; }
SUBJ=$(openssl x509 -in "$CRT" -noout -subject 2>/dev/null | sed 's/^subject= *//')
ISSU=$(openssl x509 -in "$CRT" -noout -issuer 2>/dev/null | sed 's/^issuer= *//')
if [ -n "$SUBJ" ] && [ "$SUBJ" = "$ISSU" ]; then
  echo "SELF=1"
  echo "PIN=$(openssl x509 -in "$CRT" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')"
else
  echo "SELF=0"
fi
"""

_CERT_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,200}$")


def _parse(stdout: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k in ("CERT", "KEY", "SELF", "PIN", "MISSING"):
                out[k] = v.strip()
    return out


def _creds(srv: Any) -> Dict[str, Any]:
    return dict(
        host=srv.host, port=srv.port or 22, username=srv.user or "root",
        password=srv.password, private_key=srv.private_key,
        passphrase=getattr(srv, "passphrase", None),
    )


async def prepare_certificate(
    srv: Any, *, domain: Optional[str], sni: str, port: int,
) -> Dict[str, Any]:
    """Make sure the VPS has a certificate for a hysteria2 inbound on `port`.

    Returns {"cert_file", "key_file", "pin", "self_signed"}; `pin` is set only
    for a self-signed certificate. Raises RuntimeError with a short reason.
    """
    if not _HOST_RE.match(sni or ""):
        raise RuntimeError("the SNI must be a hostname")
    if domain and not _HOST_RE.match(domain):
        raise RuntimeError("the panel domain is not a valid hostname")
    res = await exec_remote_script(
        **_creds(srv), script_content=_PREPARE, timeout=60.0,
        env={"DOMAIN": domain or "", "SNI": sni, "PORT": str(int(port))},
    )
    if not res.ok:
        logger.warning("hysteria cert prepare failed on %s: %s", srv.host, res.error)
        raise RuntimeError("could not prepare a certificate on the server over SSH")
    got = _parse(res.stdout)
    cert, key = got.get("CERT", ""), got.get("KEY", "")
    if not (_CERT_PATH_RE.match(cert) and _CERT_PATH_RE.match(key)):
        raise RuntimeError("the server did not report a usable certificate")
    self_signed = got.get("SELF") == "1"
    pin = try_normalize_pin(got.get("PIN")) if self_signed else ""
    return {"cert_file": cert, "key_file": key, "pin": pin or None, "self_signed": self_signed}


async def remote_cert_pin(srv: Any, cert_file: str) -> Optional[str]:
    """SHA-256 of `cert_file` on the VPS when it is self-signed, else None
    (a CA-signed certificate is verified normally, no pin needed)."""
    if not cert_file or not _CERT_PATH_RE.match(cert_file):
        return None
    try:
        res = await exec_remote_script(
            **_creds(srv), script_content=_PIN_ONLY, timeout=30.0, env={"CERT": cert_file},
        )
    except Exception as exc:  # noqa: BLE001 - the pin is best effort
        logger.warning("hysteria pin lookup failed on %s: %s", srv.host, exc)
        return None
    if not res.ok:
        return None
    got = _parse(res.stdout)
    if got.get("SELF") == "1":
        return try_normalize_pin(got.get("PIN")) or None
    return None


def client_auth(client: Dict[str, Any]) -> str:
    return str(client.get("auth") or "")


def node_fields(
    inbound: Dict[str, Any], client: Dict[str, Any], host: str, pin: Optional[str] = None,
    hop: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Node keyword arguments for one client of a hysteria inbound.

    `host` is what the client dials (the VPS address). SNI comes from the
    inbound's TLS settings. The password of the PiTun node is the client's
    `auth` token.
    """
    stream = parse_inbound_field(inbound.get("streamSettings"))
    tls = stream.get("tlsSettings") or {}
    alpn_val = tls.get("alpn") or ["h3"]
    alpn = ",".join(alpn_val) if isinstance(alpn_val, list) else str(alpn_val)
    sni = tls.get("serverName") or host
    obfs_pw = salamander_password(stream)
    return dict(
        protocol="hy2",
        address=host,
        port=int(inbound.get("port") or 0),
        password=client_auth(client) or None,
        uuid=None,
        transport="tcp",
        tls="tls",
        sni=sni,
        alpn=alpn,
        fingerprint=(tls.get("settings") or {}).get("fingerprint") or "chrome",
        pinned_cert_sha256=pin or None,
        hy2_obfs="salamander" if obfs_pw else None,
        hy2_obfs_password=obfs_pw,
        hy2_hop_ports=(hop or {}).get("ports") or None,
        hy2_hop_interval=(hop or {}).get("interval") or None,
    )


# ── Port hopping ─────────────────────────────────────────────────────────────
#
# The client moves across a UDP port range; the server redirects that range to
# the inbound's real port with an nftables NAT rule. Three things keep the rule
# from doing harm:
#   * `ct state new` — only the first packet of a flow is redirected, so replies
#     to the VPS's own outbound traffic (DNS and the like, which come back to
#     ephemeral ports) are never touched;
#   * the range may not contain a UDP port something already listens on;
#   * the default range stays below Linux's ephemeral range (32768+).
# The rule lives in its own table, loaded at boot by a oneshot unit, and is
# removed with the inbound.

DEFAULT_HOP_PORTS = "20000-32000"
DEFAULT_HOP_INTERVAL = "10-30"
_HOP_DIR = _CERT_DIR
_HOP_RANGE_RE = re.compile(r"^(\d{1,5})-(\d{1,5})$")
_HOP_INTERVAL_RE = re.compile(r"^(\d{1,4})(?:-(\d{1,4}))?$")


def validate_hop(ports: str, interval: str, listen_port: int) -> tuple[str, str]:
    """Normalised (ports, interval) or ValueError with a short reason."""
    ports = (ports or "").strip().replace(" ", "") or DEFAULT_HOP_PORTS
    interval = (interval or "").strip().replace(" ", "") or DEFAULT_HOP_INTERVAL
    m = _HOP_RANGE_RE.match(ports)
    if not m:
        raise ValueError("the hop range must look like 20000-32000")
    lo, hi = int(m.group(1)), int(m.group(2))
    if not (1024 <= lo < hi <= 65535):
        raise ValueError("the hop range must lie within 1024-65535, low end below high end")
    if hi - lo + 1 > 20000:
        raise ValueError("the hop range may hold at most 20000 ports")
    if lo <= 22 <= hi:
        raise ValueError("the hop range must not include the SSH port")
    mi = _HOP_INTERVAL_RE.match(interval)
    if not mi:
        raise ValueError("the hop interval must be seconds, like 10-30")
    a = int(mi.group(1))
    b = int(mi.group(2) or a)
    if a < 5 or b < a or b > 3600:
        raise ValueError("the hop interval must be 5-3600 seconds, low end first")
    if not (1 <= int(listen_port) <= 65535):
        raise ValueError("bad listen port")
    return ports, interval


_HOP_APPLY = r"""set -e
D=__DIR__; mkdir -p "$D"; chmod 700 "$D"
LO=${HOPRANGE%-*}; HI=${HOPRANGE#*-}
if ! command -v nft >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nftables >/dev/null 2>&1 || true
  fi
fi
command -v nft >/dev/null 2>&1 || { echo "ERR=nftables is not installed and could not be installed"; exit 3; }
CLASH=""
for p in $(ss -lunH 2>/dev/null | awk '{n=split($4,a,":"); print a[n]}' | sort -un); do
  if [ "$p" -ge "$LO" ] && [ "$p" -le "$HI" ] && [ "$p" != "$PORT" ]; then CLASH="$CLASH $p"; fi
done
if [ -n "$CLASH" ]; then echo "ERR=UDP ports in the range are already in use:$CLASH"; exit 4; fi
T="pitun_hy2_hop_$PORT"
write_rules() {
cat > "$D/hop-$PORT.nft" <<EOF
table $1 $T
delete table $1 $T
table $1 $T {
  chain pre {
    type nat hook prerouting priority dstnat; policy accept;
    ct state new udp dport $LO-$HI counter redirect to :$PORT
  }
}
EOF
}
write_rules inet
if ! nft -c -f "$D/hop-$PORT.nft" >/dev/null 2>&1; then
  write_rules ip
  nft -c -f "$D/hop-$PORT.nft" >/dev/null 2>&1 || { echo "ERR=this kernel/nftables cannot do the NAT redirect"; exit 5; }
fi
printf 'PORTS=%s\nINTERVAL=%s\n' "$HOPRANGE" "$HOPINTERVAL" > "$D/hop-$PORT.env"
NFT=$(command -v nft)
cat > /etc/systemd/system/pitun-hy2-hop-$PORT.service <<EOF
[Unit]
Description=PiTun Hysteria2 port hopping for UDP $PORT
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$NFT -f $D/hop-$PORT.nft
ExecStop=-$NFT delete table inet $T
ExecStop=-$NFT delete table ip $T

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable pitun-hy2-hop-$PORT.service >/dev/null 2>&1 || true
systemctl restart pitun-hy2-hop-$PORT.service
nft list tables 2>/dev/null | grep -q "$T" || { echo "ERR=the redirect rule did not load"; exit 6; }
echo "HOP=ok"
""".replace("__DIR__", _HOP_DIR)

_HOP_REMOVE = r"""D=__DIR__; T="pitun_hy2_hop_$PORT"
systemctl disable --now pitun-hy2-hop-$PORT.service >/dev/null 2>&1 || true
nft delete table inet $T >/dev/null 2>&1 || true
nft delete table ip $T >/dev/null 2>&1 || true
rm -f /etc/systemd/system/pitun-hy2-hop-$PORT.service "$D/hop-$PORT.nft" "$D/hop-$PORT.env"
systemctl daemon-reload >/dev/null 2>&1 || true
echo "HOP=removed"
""".replace("__DIR__", _HOP_DIR)

_HOP_READ = r"""F=__DIR__/hop-$PORT.env
if [ -f "$F" ]; then cat "$F"; fi
""".replace("__DIR__", _HOP_DIR)


def _hop_err(stdout: str) -> str:
    for line in (stdout or "").splitlines():
        if line.startswith("ERR="):
            return line[4:].strip()[:200]
    return ""


async def apply_hop(srv: Any, listen_port: int, ports: str, interval: str) -> Dict[str, str]:
    """Install the redirect for `ports` -> `listen_port` on the VPS. Returns the
    normalised {"ports", "interval"}; RuntimeError with a short reason on failure."""
    try:
        ports, interval = validate_hop(ports, interval, listen_port)
    except ValueError as exc:
        raise RuntimeError(str(exc))
    res = await exec_remote_script(
        **_creds(srv), script_content=_HOP_APPLY, timeout=120.0,
        env={"PORT": str(int(listen_port)), "HOPRANGE": ports, "HOPINTERVAL": interval},
    )
    if not res.ok or "HOP=ok" not in (res.stdout or ""):
        why = _hop_err(res.stdout) or res.error or "the server did not confirm the rule"
        logger.warning("hysteria hop apply failed on %s: %s", srv.host, why)
        raise RuntimeError(why)
    return {"ports": ports, "interval": interval}


async def remove_hop(srv: Any, listen_port: int) -> None:
    """Take the redirect of an inbound away. Best effort, never raises."""
    try:
        await exec_remote_script(
            **_creds(srv), script_content=_HOP_REMOVE, timeout=60.0,
            env={"PORT": str(int(listen_port))},
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail a delete
        logger.warning("hysteria hop remove failed on %s: %s", getattr(srv, "host", "?"), exc)


async def remote_hop(srv: Any, listen_port: int) -> Optional[Dict[str, str]]:
    """The hop settings installed for this inbound, or None."""
    try:
        res = await exec_remote_script(
            **_creds(srv), script_content=_HOP_READ, timeout=30.0,
            env={"PORT": str(int(listen_port))},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hysteria hop lookup failed on %s: %s", getattr(srv, "host", "?"), exc)
        return None
    if not res.ok:
        return None
    got: Dict[str, str] = {}
    for line in (res.stdout or "").splitlines():
        k, _, v = line.partition("=")
        if k in ("PORTS", "INTERVAL"):
            got[k] = v.strip()
    try:
        ports, interval = validate_hop(got.get("PORTS", ""), got.get("INTERVAL", ""), listen_port)
    except ValueError:
        return None
    if "PORTS" not in got:
        return None
    return {"ports": ports, "interval": interval}


def salamander_password(stream: Dict[str, Any]) -> Optional[str]:
    """The salamander obfuscation password of an inbound's stream settings."""
    fm = stream.get("finalmask") or {}
    for item in (fm.get("udp") or []):
        if isinstance(item, dict) and item.get("type") == "salamander":
            pw = (item.get("settings") or {}).get("password")
            if pw:
                return str(pw)
    return None
