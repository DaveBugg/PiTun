"""Encrypted and wrapped subscription links.

Clients like Happ, v2RayTun and INCY hand out subscription links that hide the
real https URL: `happ://crypt…/`, `v2raytun://crypt/`, `incy://crypt1/`, plus
plain wrappers (`happ://add/`, `incy://import/`, …). A subscription keeps the
link exactly as pasted and it is resolved to the https URL on every fetch, so
keys added later (or rotated by the client) apply without re-adding it.

Keys: the built-in ones in `sub_link_keys`, plus operator-added ones stored as
JSON in the `sub_link_keys` setting (tried first).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from app.core import sub_link_keys as builtin

logger = logging.getLogger(__name__)

SETTING_KEY = "sub_link_keys"

KEY_KINDS = ("happ_crypt5", "happ_rsa", "v2raytun", "incy", "body_aes")
HAPP_RSA_NAMES = ("crypt", "crypt2", "crypt3", "crypt4")

_HAPP_PREFIXES = (  # longest first so crypt5 wins over crypt
    ("happ://crypt5/", "crypt5"),
    ("happ://crypt4/", "crypt4"),
    ("happ://crypt3/", "crypt3"),
    ("happ://crypt2/", "crypt2"),
    ("happ://crypt/", "crypt"),
)
_WRAPPERS = ("happ://add/", "v2raytun://import/", "v2raytun://add/",
             "incy://add/", "incy://import/")
_V2_PREFIX = "v2raytun://crypt/"
_INCY_PREFIX = "incy://"
_BODY_IV = b"k" * 12


class SubLinkError(ValueError):
    """The link is recognised but cannot be turned into a URL. The message is
    safe to show: it never contains the decrypted URL."""


@dataclass
class ResolvedLink:
    url: str
    kind: str = ""            # "" for a plain URL, else e.g. "happ crypt5"
    name: Optional[str] = None  # INCY links carry a subscription name


# ── keys ──────────────────────────────────────────────────────────────────────

def parse_custom_keys(raw: Optional[str]) -> List[dict]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for k in data:
        if isinstance(k, dict) and k.get("kind") in KEY_KINDS and k.get("name") and k.get("value"):
            out.append({"kind": k["kind"], "name": str(k["name"]), "value": str(k["value"])})
    return out


def _custom(keys: Iterable[dict], kind: str) -> List[Tuple[str, str]]:
    return [(k["name"], k["value"]) for k in keys if k.get("kind") == kind]


def _der_from_text(value: str) -> bytes:
    v = value.strip()
    if "-----BEGIN" in v:
        v = "".join(line for line in v.splitlines() if line and not line.startswith("-----"))
    return _b64(v)


@lru_cache(maxsize=128)
def _rsa_key(value: str):
    from cryptography.hazmat.primitives.serialization import load_der_private_key
    return load_der_private_key(_der_from_text(value), password=None)


def _secret_bytes(value: str, size: int) -> bytes:
    """A symmetric key given as hex, base64 or raw text of exactly `size` bytes."""
    v = value.strip()
    if len(v) == size * 2:
        try:
            return bytes.fromhex(v)
        except ValueError:
            pass
    raw = value.encode("utf-8")
    if len(raw) == size:
        return raw
    try:
        b = _b64(v)
        if len(b) == size:
            return b
    except ValueError:
        pass
    raise ValueError(f"expected a {size}-byte key (hex, base64 or {size} characters)")


def validate_key(kind: str, name: str, value: str) -> None:
    """Raise ValueError when a key entry would never work."""
    if kind not in KEY_KINDS:
        raise ValueError(f"unknown key kind '{kind}'")
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if kind == "happ_crypt5" and (len(name) != 8 or not name.isascii()):
        raise ValueError("a crypt5 key name is its 8-character link marker")
    if kind == "happ_rsa" and name not in HAPP_RSA_NAMES:
        raise ValueError(f"name must be one of {', '.join(HAPP_RSA_NAMES)}")
    if kind in ("happ_crypt5", "happ_rsa", "v2raytun"):
        try:
            key = _rsa_key(value)
        except Exception:  # noqa: BLE001
            raise ValueError("not an RSA private key (PEM or base64 DER)") from None
        if not hasattr(key, "private_numbers") or not hasattr(key, "key_size"):
            raise ValueError("not an RSA private key")
    elif kind == "incy":
        _secret_bytes(value, 32)
    elif kind == "body_aes":
        _secret_bytes(value, 16)


# ── small helpers ─────────────────────────────────────────────────────────────

def _b64(s) -> bytes:
    if isinstance(s, str):
        s = s.encode()
    s = bytes(c for c in s if c not in b" \r\n\t").replace(b"-", b"+").replace(b"_", b"/")
    try:
        return base64.b64decode(s + b"=" * (-len(s) % 4), validate=True)
    except Exception:  # noqa: BLE001
        raise ValueError("bad base64") from None


def _swap_pairs(b: bytes) -> bytes:
    a = bytearray(b)
    for i in range(0, len(a) - 1, 2):
        a[i], a[i + 1] = a[i + 1], a[i]
    return bytes(a)


def _swap_halves(b: bytes) -> bytes:
    a = bytearray(b)
    for i in range(0, len(a) - len(a) % 4, 4):
        a[i:i + 4] = a[i + 2:i + 4] + a[i:i + 2]
    return bytes(a)


def _looks_like_scheme_link(s: str) -> bool:
    head, sep, _ = s.strip().partition("://")
    return bool(sep) and bool(head) and head[0].isalpha() and all(
        c.isalnum() or c in "+.-" for c in head)


def _rsa_decrypt(key, ct: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import padding
    return key.decrypt(ct, padding.PKCS1v15())


def _rsa_blocks(key, ct: bytes) -> bytes:
    size = key.key_size // 8
    if not ct or len(ct) % size:
        raise ValueError("ciphertext is not a whole number of RSA blocks")
    return b"".join(_rsa_decrypt(key, ct[i:i + size]) for i in range(0, len(ct), size))


# ── decoders ──────────────────────────────────────────────────────────────────

def _happ_rsa(version: str, payload: str, keys: List[dict]) -> str:
    candidates = [v for n, v in _custom(keys, "happ_rsa") if n == version]
    candidates.append(builtin.HAPP_RSA_KEYS[version])
    ct = _b64(payload)
    for value in candidates:
        try:
            text = _rsa_blocks(_rsa_key(value), ct).decode("utf-8").strip()
        except Exception:  # noqa: BLE001
            continue
        if _looks_like_scheme_link(text):
            return text
    raise SubLinkError(f"cannot decrypt this happ://{version}/ link with the known keys")


def crypt5_marker(payload: str) -> str:
    s = _swap_halves(payload.encode())
    return (s[:4] + s[-4:]).decode("latin-1") if len(s) >= 8 else ""


def _crypt5_body(body: bytes, key, salted: bool) -> str:
    nonce = body[:12]
    salt = body[14:22] if salted else None
    rest = body[22 if salted else 12:]
    digits = len(rest) - len(rest.lstrip(b"0123456789"))
    if not digits:
        raise ValueError("segment length missing")
    seg_len = int(rest[:digits])
    packed = rest[digits:]
    if not packed or seg_len > len(packed) - 1:
        raise ValueError("segment truncated")
    segment, rsa_ct = packed[1:1 + seg_len], packed[1 + seg_len:]
    value = _b64(_swap_pairs(_rsa_decrypt(key, _b64(rsa_ct))))
    if len(value) != 32:
        raise ValueError("unexpected key length")
    chacha_key = bytes(value[i] ^ salt[i % 8] for i in range(32)) if salt else value
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    inner = ChaCha20Poly1305(chacha_key).decrypt(nonce, _b64(segment), None)
    return _b64(_swap_pairs(inner)).decode("utf-8").strip()


def _happ_crypt5(payload: str, keys: List[dict]) -> str:
    marker = crypt5_marker(payload)
    shuffled = _swap_halves(payload.encode())
    body = shuffled[4:-4]
    if len(body) < 13:
        raise SubLinkError("this happ://crypt5/ link is too short")
    candidates = [v for n, v in _custom(keys, "happ_crypt5") if n == marker]
    if marker in builtin.HAPP_CRYPT5_KEYS:
        candidates.append(builtin.HAPP_CRYPT5_KEYS[marker])
    if not candidates:
        raise SubLinkError(
            f"no key for this happ://crypt5/ link (marker '{marker}'); "
            "add it under Subscriptions -> Link keys")
    salted_first = not chr(body[12]).isdigit()
    for value in candidates:
        try:
            key = _rsa_key(value)
        except Exception:  # noqa: BLE001
            continue
        for salted in ((True, False) if salted_first else (False, True)):
            try:
                return _crypt5_body(body, key, salted)
            except Exception:  # noqa: BLE001
                continue
    raise SubLinkError(f"the key for marker '{marker}' does not decrypt this happ://crypt5/ link")


def _v2raytun(payload: str, keys: List[dict]) -> str:
    ct = _b64(payload)
    for _name, value in _custom(keys, "v2raytun") + list(builtin.V2RAYTUN_KEYS.items()):
        try:
            text = _rsa_blocks(_rsa_key(value), ct).decode("utf-8").strip()
        except Exception:  # noqa: BLE001
            continue
        if _looks_like_scheme_link(text):
            return text
    raise SubLinkError("cannot decrypt this v2raytun://crypt/ link with the known keys")


def _incy(host: str, payload: str, keys: List[dict]) -> Tuple[str, Optional[str]]:
    candidates = [v for n, v in _custom(keys, "incy") if n == host]
    if host in builtin.INCY_KEYS:
        candidates.append(builtin.INCY_KEYS[host])
    if not candidates:
        raise SubLinkError(
            f"no key for incy://{host}/ links; add it under Subscriptions -> Link keys")
    wire = _b64(payload.rstrip("/"))
    if len(wire) < 12 + 16 + 1:
        raise SubLinkError(f"this incy://{host}/ link is too short")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    for value in candidates:
        try:
            plain = AESGCM(_secret_bytes(value, 32)).decrypt(wire[:12], wire[12:], None)
            data = json.loads(plain.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        url = data.get("url") if isinstance(data, dict) else None
        if isinstance(url, str) and url:
            name = data.get("n")
            return url.strip(), (name.strip() if isinstance(name, str) and name.strip() else None)
    raise SubLinkError(f"cannot decrypt this incy://{host}/ link with the known keys")


def _unwrap(link: str) -> Optional[str]:
    low = link.lower()
    for prefix in _WRAPPERS:
        if low.startswith(prefix):
            rest = link[len(prefix):].strip()
            if "%" in rest:
                rest = unquote(rest).strip()
            if _looks_like_scheme_link(rest):
                return rest
            try:
                text = _b64(rest).decode("utf-8").strip()
            except (ValueError, UnicodeDecodeError):
                return None
            return text if _looks_like_scheme_link(text) else None
    return None


# ── public API ────────────────────────────────────────────────────────────────

def is_special_link(link: str) -> bool:
    """A link this module resolves (anything but plain http/https)."""
    low = (link or "").strip().lower()
    return (low.startswith(("happ://", "v2raytun://", _INCY_PREFIX))
            and not low.startswith(("http://", "https://")))


def link_kind(link: str) -> str:
    low = (link or "").strip().lower()
    for prefix, version in _HAPP_PREFIXES:
        if low.startswith(prefix):
            return f"happ {version}"
    if low.startswith(_V2_PREFIX):
        return "v2raytun crypt"
    if low.startswith(_INCY_PREFIX):
        host = low[len(_INCY_PREFIX):].split("/", 1)[0]
        return f"incy {host}" if host not in ("add", "import") else "incy link"
    if low.startswith(("happ://", "v2raytun://")):
        return low.split("://", 1)[0] + " link"
    return ""


def resolve_link(link: str, keys: Optional[List[dict]] = None) -> ResolvedLink:
    """Turn a pasted link into the http(s) subscription URL."""
    keys = keys or []
    cur = (link or "").strip()
    kind = link_kind(cur)
    name: Optional[str] = None
    for _ in range(6):
        low = cur.lower()
        if low.startswith(("http://", "https://")):
            return ResolvedLink(url=cur, kind=kind, name=name)
        if cur != link.strip() and not is_special_link(cur):
            break  # decoded to something else (a single server link)
        nxt: Optional[str] = None
        for prefix, version in _HAPP_PREFIXES:
            if low.startswith(prefix):
                payload = cur[len(prefix):]
                try:
                    nxt = (_happ_crypt5(payload, keys) if version == "crypt5"
                           else _happ_rsa(version, payload, keys))
                except ValueError as exc:
                    raise SubLinkError(str(exc) if isinstance(exc, SubLinkError)
                                       else f"malformed happ://{version}/ link") from None
                break
        else:
            if low.startswith(_V2_PREFIX):
                try:
                    nxt = _v2raytun(cur[len(_V2_PREFIX):], keys)
                except SubLinkError:
                    raise
                except ValueError:
                    raise SubLinkError("malformed v2raytun://crypt/ link") from None
            elif low.startswith(_INCY_PREFIX) and low[len(_INCY_PREFIX):].split("/", 1)[0] not in ("add", "import"):
                host, _, payload = cur[len(_INCY_PREFIX):].partition("/")
                try:
                    nxt, name = _incy(host.lower(), payload, keys)
                except SubLinkError:
                    raise
                except ValueError:
                    raise SubLinkError(f"malformed incy://{host}/ link") from None
            else:
                nxt = _unwrap(cur)
        if not nxt:
            raise SubLinkError("unsupported link: expected http(s)://, happ://, v2raytun:// or incy://")
        if nxt == cur:
            break
        cur = nxt
    if cur.lower().startswith(("http://", "https://")):
        return ResolvedLink(url=cur, kind=kind, name=name)
    scheme = cur.split("://", 1)[0] if "://" in cur else "?"
    raise SubLinkError(
        f"the link holds a single {scheme}:// server, not a subscription; add it as a node instead")


async def load_custom_keys(session) -> List[dict]:
    from sqlmodel import select
    from app.models import Settings as DBSettings
    row = (await session.exec(select(DBSettings).where(DBSettings.key == SETTING_KEY))).first()
    return parse_custom_keys(row.value if row else None)


def decrypt_body(text: str, url: str, headers, keys: Optional[List[dict]] = None) -> str:
    """Undo the AES-128-GCM body layer some panels add: the URL carries
    `?key=keyNN` and the response an `Encrypt-Tag` header. Anything else is
    returned unchanged."""
    tag_b64 = ""
    try:
        tag_b64 = (headers or {}).get("encrypt-tag", "") or ""
    except Exception:  # noqa: BLE001
        return text
    if not isinstance(tag_b64, str) or not tag_b64.strip():
        return text
    try:
        key_name = (parse_qs(urlsplit(url).query).get("key") or [""])[0]
    except ValueError:
        return text
    if not key_name:
        return text
    values = [v for n, v in _custom(keys or [], "body_aes") if n == key_name]
    if key_name in builtin.BODY_AES_KEYS:
        values.append(builtin.BODY_AES_KEYS[key_name])
    if not values:
        raise SubLinkError(f"the panel encrypted the subscription with an unknown key '{key_name}'")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        ct, tag = _b64(text.strip()), _b64(tag_b64.strip())
    except ValueError:
        raise SubLinkError("the encrypted subscription body is not base64") from None
    for value in values:
        try:
            return AESGCM(_secret_bytes(value, 16)).decrypt(_BODY_IV, ct + tag, None).decode("utf-8")
        except Exception:  # noqa: BLE001
            continue
    raise SubLinkError(f"the subscription body does not decrypt with key '{key_name}'")


def fingerprint(value: str) -> str:
    """Short id of a key value, for the UI (the value itself is never returned)."""
    return hashlib.sha256(value.strip().encode()).hexdigest()[:12]
