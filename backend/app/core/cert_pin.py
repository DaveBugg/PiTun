"""Certificate pinning for nodes.

xray removed `allowInsecure` (it is a hard error when true) and replaced it
with `pinnedPeerCertSha256`: the server is accepted when its certificate hashes
to one of the pinned values, which is how a self-signed server is trusted
without turning verification off. The value is a string of 64 hex characters,
several joined with commas.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
from typing import Optional

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def normalize_pin(raw: Optional[str]) -> str:
    """Lower-case, separator-free, comma-joined pins; "" when nothing is set.

    Accepts the spellings seen in share links and tools: `AB:CD:...`,
    `ab cd ...`, several values separated by `,` or `;`. Raises ValueError on
    anything that is not a SHA-256 (64 hex characters).
    """
    if raw is None:
        return ""
    out = []
    for part in re.split(r"[,;]", str(raw)):
        h = re.sub(r"[\s:]", "", part).lower()
        if not h:
            continue
        if not _HEX64.match(h):
            raise ValueError("a certificate pin must be a SHA-256: 64 hex characters")
        if h not in out:
            out.append(h)
    return ",".join(out)


def try_normalize_pin(raw: Optional[str]) -> str:
    """Like `normalize_pin` for untrusted share links: a malformed pin is
    dropped rather than failing the whole import."""
    try:
        return normalize_pin(raw)
    except ValueError:
        return ""


def pin_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


async def fetch_leaf_pin(host: str, port: int, sni: Optional[str] = None, timeout: float = 8.0) -> dict:
    """Open a TLS connection WITHOUT verifying and report the leaf certificate's
    SHA-256, so the operator can pin a server they already trust (trust on first
    use). TCP/TLS only: QUIC (hysteria) cannot be read this way.

    Returns {"pin", "subject", "issuer", "not_after", "self_signed"}.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx, server_hostname=sni or host),
        timeout=timeout,
    )
    try:
        sslobj = writer.get_extra_info("ssl_object")
        der = sslobj.getpeercert(binary_form=True)
        if not der:
            raise ValueError("the server presented no certificate")
        info = _describe(der)
        return {"pin": pin_of_der(der), **info}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - closing a probe socket
            pass


def _describe(der: bytes) -> dict:
    """Subject/issuer/expiry for the confirmation line in the UI. Uses
    `cryptography` (already a dependency via python-jose) when present."""
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        return {
            "subject": subject,
            "issuer": issuer,
            "not_after": cert.not_valid_after_utc.isoformat() if hasattr(cert, "not_valid_after_utc")
            else cert.not_valid_after.isoformat(),
            "self_signed": subject == issuer,
        }
    except Exception:  # noqa: BLE001 - description is cosmetic
        return {"subject": "", "issuer": "", "not_after": "", "self_signed": False}
