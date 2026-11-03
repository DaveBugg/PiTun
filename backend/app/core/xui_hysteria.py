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
    )
