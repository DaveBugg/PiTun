"""Certificate pinning: the replacement for xray's removed `allowInsecure`."""
import asyncio
import hashlib
import ssl
import subprocess
import shutil

import pytest

from app.core.cert_pin import fetch_leaf_pin, normalize_pin, pin_of_der, try_normalize_pin
from app.core.config_gen import generate_config
from app.core.uri_formatter import node_to_uri
from app.core.uri_parser import parse_uri
from app.models import Node

H1 = "ab" * 32
H2 = "cd" * 32


def test_normalize_accepts_the_spellings_seen_in_the_wild():
    colons = ":".join(H1[i:i + 2].upper() for i in range(0, 64, 2))
    assert normalize_pin(colons) == H1
    assert normalize_pin(H1.upper()) == H1
    assert normalize_pin(f"{H1}, {H2}") == f"{H1},{H2}"
    assert normalize_pin(f"{H1};{H1}") == H1          # duplicates collapse
    assert normalize_pin("") == "" and normalize_pin(None) == ""


@pytest.mark.parametrize("bad", ["abc", "zz" * 32, "ab" * 16, H1 + "00"])
def test_normalize_rejects_what_is_not_a_sha256(bad):
    with pytest.raises(ValueError):
        normalize_pin(bad)
    assert try_normalize_pin(bad) == ""


def _node(proto, **kw):
    base = dict(id=1, name="n", protocol=proto, address="203.0.113.5", port=443,
                tls="tls", sni="example.com", enabled=True, is_online=True, order=0)
    base.update(kw)
    return Node(**base)


_SETTINGS = {"mode": "global", "dns_mode": "plain", "dns_upstream": "8.8.8.8",
             "dns_sniffing": "true", "inbound_mode": "tproxy", "block_quic": "true"}


def _tls_of(node):
    cfg = generate_config(node, [node], [], "global", _SETTINGS)
    ob = next(o for o in cfg["outbounds"] if o["tag"] == "node-1")
    return ob["streamSettings"]["tlsSettings"]


@pytest.mark.parametrize("proto,extra", [("vless", dict(uuid="u")), ("trojan", dict(password="p")), ("hy2", dict(password="p"))])
def test_pin_reaches_the_xray_config(proto, extra):
    tls = _tls_of(_node(proto, pinned_cert_sha256=H1, **extra))
    assert tls["pinnedPeerCertSha256"] == H1
    assert "allowInsecure" not in tls


@pytest.mark.parametrize("proto,extra", [("vless", dict(uuid="u")), ("trojan", dict(password="p")), ("hy2", dict(password="p"))])
def test_no_pin_means_no_key_and_never_allow_insecure(proto, extra):
    tls = _tls_of(_node(proto, allow_insecure=True, **extra))
    assert "pinnedPeerCertSha256" not in tls and "allowInsecure" not in tls


def test_vless_link_round_trip_keeps_the_pin():
    n = _node("vless", uuid="11111111-1111-1111-1111-111111111111", pinned_cert_sha256=H1)
    back = parse_uri(node_to_uri(n))
    assert back["pinned_cert_sha256"] == H1


def test_hy2_link_uses_pinSHA256_and_reads_both_spellings():
    n = _node("hy2", password="pw", pinned_cert_sha256=H1)
    uri = node_to_uri(n)
    assert "pinSHA256=" + H1 in uri
    assert parse_uri(uri)["pinned_cert_sha256"] == H1
    colon = ":".join(H1[i:i + 2].upper() for i in range(0, 64, 2))
    assert parse_uri(f"hysteria2://pw@203.0.113.5:443?sni=example.com&pinSHA256={colon}#x")["pinned_cert_sha256"] == H1


def test_a_malformed_pin_in_a_link_is_dropped_not_fatal():
    parsed = parse_uri("vless://11111111-1111-1111-1111-111111111111@203.0.113.5:443?security=tls&pcs=nothex#x")
    assert parsed is not None and not parsed.get("pinned_cert_sha256")


def test_api_validates_and_normalises_the_pin(client, admin_user, auth_headers):
    body = {"name": "pinned", "protocol": "vless", "address": "203.0.113.5", "port": 443,
            "uuid": "11111111-1111-1111-1111-111111111111", "tls": "tls", "pinned_cert_sha256": H1.upper()}
    r = client.post("/api/nodes", json=body, headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    assert r.json()["pinned_cert_sha256"] == H1
    bad = client.post("/api/nodes", json={**body, "name": "bad", "pinned_cert_sha256": "nothex"}, headers=auth_headers)
    assert bad.status_code == 422


# ── reading the pin from a live TLS server ───────────────────────────────────

@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl needed to make a test certificate")
def test_fetch_leaf_pin_matches_the_certificate_hash(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
         "-days", "2", "-subj", "/CN=pin.example.test"],
        check=True, capture_output=True,
    )
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    expected = hashlib.sha256(der).hexdigest()

    async def run():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(crt), str(key))

        async def handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await fetch_leaf_pin("127.0.0.1", port, "pin.example.test")

    got = asyncio.run(run())
    assert got["pin"] == expected == pin_of_der(der)
    assert got["self_signed"] is True


def test_cert_pin_endpoint_reports_a_short_error(client, admin_user, auth_headers):
    r = client.post("/api/nodes/cert-pin", json={"address": "127.0.0.1", "port": 9}, headers=auth_headers)
    assert r.status_code == 502
    assert "Traceback" not in r.text and "errno" not in r.text.lower()
