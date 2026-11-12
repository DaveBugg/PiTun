"""Encrypted / wrapped subscription links (happ://crypt*, v2raytun://crypt, incy://crypt1)."""
import asyncio
import base64
import json
import os
from unittest import mock
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

from app.core import sub_link_keys as builtin
from app.core.sub_links import (
    SubLinkError, _rsa_key, _swap_halves, _swap_pairs, crypt5_marker, decrypt_body,
    link_kind, resolve_link, validate_key,
)

SUB = "https://example.com/api/sub/abc123"
URI = "vless://aaa@203.0.113.7:443?type=tcp&security=reality#n1"


# ── encoders (the clients' side, only for tests) ──────────────────────────────

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _rsa_blocks_encrypt(key_value: str, text: str) -> bytes:
    pub = _rsa_key(key_value).public_key()
    step = pub.key_size // 8 - 11
    data = text.encode()
    return b"".join(pub.encrypt(data[i:i + step], padding.PKCS1v15()) for i in range(0, len(data), step))


def happ_rsa_link(version: str, text: str, key_value: str = None) -> str:
    return f"happ://{version}/" + _b64(_rsa_blocks_encrypt(key_value or builtin.HAPP_RSA_KEYS[version], text))


def crypt5_link(text: str, marker: str = "asajzqxt", salted: bool = True, key_value: str = None) -> str:
    pub = _rsa_key(key_value or builtin.HAPP_CRYPT5_KEYS[marker]).public_key()
    chacha_key = os.urandom(32)
    salt = b"Ab3dEf9h" if salted else None
    value = bytes(chacha_key[i] ^ salt[i % 8] for i in range(32)) if salt else chacha_key
    rsa_b64 = _b64(pub.encrypt(_swap_pairs(_b64(value).encode()), padding.PKCS1v15()))
    nonce = b"abcdefghijk1"
    ct = ChaCha20Poly1305(chacha_key).encrypt(nonce, _swap_pairs(_b64(text.encode()).encode()), None)
    seg = _b64(ct)
    body = nonce + ((b"xY" + salt) if salt else b"") + str(len(seg)).encode() + b":" + seg.encode() + rsa_b64.encode()
    return "happ://crypt5/" + _swap_halves(marker[:4].encode() + body + marker[4:].encode()).decode()


def incy_link(url: str, name: str = None, host: str = "crypt1", key: bytes = None) -> str:
    key = key or bytes.fromhex(builtin.INCY_KEYS["crypt1"])
    payload = {"url": url, "v": 1}
    if name:
        payload["n"] = name
    iv = os.urandom(12)
    wire = iv + AESGCM(key).encrypt(iv, json.dumps(payload, sort_keys=True).encode(), None)
    return f"incy://{host}/" + base64.urlsafe_b64encode(wire).decode().rstrip("=")


def _new_rsa_pem() -> str:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


# ── decoding ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("version", ["crypt", "crypt2", "crypt3", "crypt4"])
def test_happ_rsa_versions_decode(version):
    r = resolve_link(happ_rsa_link(version, SUB))
    assert r.url == SUB and r.kind == f"happ {version}"


@pytest.mark.parametrize("salted", [True, False])
def test_happ_crypt5_both_layouts_decode(salted):
    link = crypt5_link(SUB, salted=salted)
    assert crypt5_marker(link.split("/", 3)[3]) == "asajzqxt"
    assert resolve_link(link).url == SUB


def test_every_builtin_crypt5_key_loads_and_works():
    for marker in builtin.HAPP_CRYPT5_KEYS:
        assert resolve_link(crypt5_link(SUB, marker=marker)).url == SUB, marker


def test_unknown_crypt5_marker_names_the_marker_and_a_custom_key_fixes_it():
    pem = _new_rsa_pem()
    link = crypt5_link(SUB, marker="zzzzqqqq", key_value=pem)
    with pytest.raises(SubLinkError, match="zzzzqqqq"):
        resolve_link(link)
    keys = [{"kind": "happ_crypt5", "name": "zzzzqqqq", "value": pem}]
    assert resolve_link(link, keys).url == SUB


def test_v2raytun_crypt_decodes():
    ct = _rsa_blocks_encrypt(builtin.V2RAYTUN_KEYS["crypt4"], SUB)
    link = "v2raytun://crypt/" + base64.urlsafe_b64encode(ct).decode().rstrip("=")
    assert resolve_link(link).url == SUB


def test_incy_crypt1_decodes_with_its_name():
    r = resolve_link(incy_link(SUB, name="My plan"))
    assert (r.url, r.name, r.kind) == (SUB, "My plan", "incy crypt1")


def test_a_future_incy_host_works_once_its_key_is_added():
    key = os.urandom(32)
    link = incy_link(SUB, host="crypt2", key=key)
    with pytest.raises(SubLinkError, match="crypt2"):
        resolve_link(link)
    assert resolve_link(link, [{"kind": "incy", "name": "crypt2", "value": key.hex()}]).url == SUB


def test_wrappers_unwrap_including_nested_encryption():
    assert resolve_link("happ://add/" + quote(SUB, safe="")).url == SUB
    assert resolve_link("incy://import/" + _b64(SUB.encode())).url == SUB
    assert resolve_link("happ://add/" + happ_rsa_link("crypt3", SUB)).url == SUB


def test_a_link_holding_one_server_is_refused():
    with pytest.raises(SubLinkError, match="single vless"):
        resolve_link(happ_rsa_link("crypt4", URI))


def test_garbage_is_a_clean_error():
    for bad in ("happ://crypt5/abc", "happ://crypt4/%%%", "incy://crypt1/AAAA",
                "happ://something/else", "v2raytun://crypt/QUJD"):
        with pytest.raises(SubLinkError):
            resolve_link(bad)


def test_error_text_never_contains_the_url():
    link = crypt5_link(SUB)
    tampered = link[:-12] + ("A" * 8) + link[-4:]
    try:
        resolve_link(tampered)
    except SubLinkError as exc:
        assert "example.com" not in str(exc)


def test_link_kind():
    assert link_kind("happ://crypt5/x") == "happ crypt5"
    assert link_kind("incy://crypt1/x") == "incy crypt1"
    assert link_kind(SUB) == ""


# ── body layer ────────────────────────────────────────────────────────────────

def test_encrypted_body_is_decrypted_with_the_named_key():
    key = bytes.fromhex(builtin.BODY_AES_KEYS["key07"])
    sealed = AESGCM(key).encrypt(b"k" * 12, URI.encode(), None)
    body, tag = _b64(sealed[:-16]), _b64(sealed[-16:])
    assert decrypt_body(body, SUB + "?key=key07", {"encrypt-tag": tag}) == URI
    # no header / no key param: untouched
    assert decrypt_body(URI, SUB + "?key=key07", {}) == URI
    assert decrypt_body(URI, SUB, {"encrypt-tag": tag}) == URI
    with pytest.raises(SubLinkError, match="key99"):
        decrypt_body(body, SUB + "?key=key99", {"encrypt-tag": tag})


def test_key_validation():
    validate_key("happ_crypt5", "abcdwxyz", _new_rsa_pem())
    validate_key("incy", "crypt2", "00" * 32)
    validate_key("body_aes", "key91", "0123456789abcdef")
    for kind, name, value in [
        ("happ_crypt5", "short", _new_rsa_pem()),
        ("happ_rsa", "crypt9", _new_rsa_pem()),
        ("v2raytun", "x", "not a key"),
        ("incy", "crypt2", "00" * 8),
        ("nope", "x", "y"),
    ]:
        with pytest.raises(ValueError):
            validate_key(kind, name, value)


# ── API ───────────────────────────────────────────────────────────────────────

def test_api_accepts_an_encrypted_link_and_decode_shows_it(client, admin_user, auth_headers):
    link = incy_link(SUB, name="Plan")
    r = client.post("/api/subscriptions/decode-link", json={"link": link}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "incy crypt1", "url": SUB, "host": "example.com", "name": "Plan"}
    with mock.patch("app.api.subscriptions._fetch_subscription", new=AsyncMock()):
        r = client.post("/api/subscriptions", json={"name": "P", "url": link}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["url"] == link


def test_api_refuses_links_to_private_addresses_or_without_a_key(client, admin_user, auth_headers):
    r = client.post("/api/subscriptions", headers=auth_headers,
                    json={"name": "P", "url": happ_rsa_link("crypt4", "http://10.0.0.1/sub")})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]
    r = client.post("/api/subscriptions", headers=auth_headers,
                    json={"name": "P", "url": crypt5_link(SUB, marker="zzzzqqqq", key_value=_new_rsa_pem())})
    assert r.status_code == 400 and "zzzzqqqq" in r.json()["detail"]


def test_link_keys_crud_never_returns_values(client, admin_user, auth_headers):
    pem = _new_rsa_pem()
    r = client.post("/api/subscriptions/link-keys", headers=auth_headers,
                    json={"kind": "happ_crypt5", "name": "zzzzqqqq", "value": pem})
    assert r.status_code == 201, r.text
    kid = r.json()["id"]
    assert client.post("/api/subscriptions/link-keys", headers=auth_headers,
                       json={"kind": "happ_crypt5", "name": "zzzzqqqq", "value": pem}).status_code == 409
    assert client.post("/api/subscriptions/link-keys", headers=auth_headers,
                       json={"kind": "happ_crypt5", "name": "zz", "value": pem}).status_code == 400
    listing = client.get("/api/subscriptions/link-keys", headers=auth_headers).json()
    assert [k["name"] for k in listing["custom"]] == ["zzzzqqqq"]
    assert "PRIVATE" not in json.dumps(listing)
    assert listing["builtin"]["happ_crypt5"] == len(builtin.HAPP_CRYPT5_KEYS)
    # the added key now decodes links with that marker
    link = crypt5_link(SUB, marker="zzzzqqqq", key_value=pem)
    r = client.post("/api/subscriptions/decode-link", json={"link": link}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["url"] == SUB
    assert client.delete(f"/api/subscriptions/link-keys/{kid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/api/subscriptions/link-keys/{kid}", headers=auth_headers).status_code == 404


def test_fetch_goes_to_the_decoded_url(client, admin_user, session):
    from app.api.subscriptions import _fetch_subscription_unlocked
    from app.models import Node, Subscription
    from sqlmodel import select
    sub = Subscription(name="T", url=crypt5_link(SUB), enabled=True)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client, \
         mock.patch("socket.getaddrinfo", return_value=[]):
        inst = mock_client.return_value.__aenter__.return_value
        inst.get = AsyncMock(return_value=mock.Mock(
            status_code=200, text=URI, headers={}, raise_for_status=lambda: None,
        ))
        asyncio.run(_fetch_subscription_unlocked(sub.id))
    assert inst.get.call_args.args[0] == SUB
    session.expire_all()
    assert session.exec(select(Node).where(Node.subscription_id == sub.id)).all()


def test_fetch_with_an_undecodable_link_stores_a_readable_error(client, admin_user, session):
    from app.api.subscriptions import _fetch_subscription_unlocked
    from app.models import Subscription
    sub = Subscription(name="T", url=crypt5_link(SUB, marker="zzzzqqqq", key_value=_new_rsa_pem()),
                       enabled=True)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
        asyncio.run(_fetch_subscription_unlocked(sub.id))
        assert not mock_client.return_value.__aenter__.return_value.get.called
    session.expire_all()
    err = session.get(Subscription, sub.id).last_error
    assert err.startswith("Link:") and "zzzzqqqq" in err
