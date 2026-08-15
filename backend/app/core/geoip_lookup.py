"""Optional GeoIP enrichment — prefixes imported node names with a country
flag emoji (e.g. `🇳🇱 vless-nl`).

**Opt-in and licence-clean.** PiTun ships and downloads nothing: enrichment
is a no-op unless the operator drops a MaxMind `GeoLite2-Country.mmdb` at
`settings.geoip_country_db_path` (the same host dir already bind-mounted for
geoip.dat / geosite.dat). No file — or no `maxminddb` wheel — and every
function below silently returns the name unchanged.

Design notes:
- The reader is opened once and cached. Adding the .mmdb later needs a
  backend restart (acceptable — it's a deploy-time step). `reset()` clears
  the cache for tests.
- Idempotent: re-importing a subscription strips the flag we added last time
  before re-adding, so flags never stack.
- A hostname address is resolved best-effort (`gethostbyname`); failures are
  swallowed so a flaky DNS never breaks an import.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Regional-indicator range — two of these in a row render as a flag.
_RI_BASE = 0x1F1E6  # 'A'
_RI_LAST = 0x1F1FF  # 'Z'

_reader: Any = None
_reader_loaded = False


def reset() -> None:
    """Drop the cached reader — call after the .mmdb path/file changes
    (used by tests; production picks up a new file on restart)."""
    global _reader, _reader_loaded
    if _reader is not None:
        try:
            _reader.close()
        except Exception:  # noqa: BLE001
            pass
    _reader = None
    _reader_loaded = False


def _get_reader():
    """Open (once) the GeoLite2 reader, or None when disabled."""
    global _reader, _reader_loaded
    if _reader_loaded:
        return _reader
    _reader_loaded = True
    path = getattr(settings, "geoip_country_db_path", "") or ""
    if not path or not os.path.exists(path):
        return None
    try:
        import maxminddb  # lazy — absence just disables the feature
        _reader = maxminddb.open_database(path)
        logger.info("GeoIP enrichment enabled (%s)", path)
    except Exception as exc:  # noqa: BLE001 — missing wheel / corrupt db
        logger.info("GeoIP enrichment disabled: %s", exc)
        _reader = None
    return _reader


def _is_ri(ch: str) -> bool:
    return _RI_BASE <= ord(ch) <= _RI_LAST


def flag_emoji(code: str) -> str:
    """ISO-3166-1 alpha-2 → flag emoji. '' for anything malformed."""
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(_RI_BASE + (ord(c) - ord("A"))) for c in code)


def strip_leading_flag(name: str) -> str:
    """Remove a leading flag (+ its trailing space) we added earlier."""
    if len(name) >= 2 and _is_ri(name[0]) and _is_ri(name[1]):
        rest = name[2:]
        return rest[1:] if rest.startswith(" ") else rest
    return name


def _to_ip(address: str, *, resolve: bool = True) -> Optional[str]:
    """Return an IP literal for `address`, resolving a hostname best-effort.

    `resolve=False` answers only for addresses that are already literals.
    The lookup below is blocking, and one caller runs inside a database
    flush where a stalled DNS server would hold up the write.
    """
    try:
        ipaddress.ip_address(address)
        return address
    except ValueError:
        pass
    if not resolve:
        return None
    try:
        return socket.gethostbyname(address)
    except OSError:
        return None


def country_code(ip: str) -> Optional[str]:
    """ISO country code for an IP literal, or None (miss / disabled)."""
    reader = _get_reader()
    if reader is None:
        return None
    try:
        rec = reader.get(ip)
    except Exception:  # noqa: BLE001 — bad address / reader error
        return None
    if not rec:
        return None
    return (rec.get("country") or {}).get("iso_code")


def resolve_country(
    address: str, *, resolve: bool = True, country: Optional[str] = None,
) -> Optional[str]:
    """The ISO code to flag `address` with, or None when it can't be told.

    A `country` handed in wins outright and needs no database: it is an
    observation (where the node's traffic actually came out), while the
    lookup below is an inference from the address it dials.
    """
    if country and len(country) == 2 and country.isalpha():
        return country.upper()
    if _get_reader() is None or not address:
        return None
    ip = _to_ip(address, resolve=resolve)
    return country_code(ip) if ip else None


def enrich_name(
    name: str,
    address: str,
    *,
    resolve: bool = True,
    country: Optional[str] = None,
    keep_on_miss: bool = False,
) -> str:
    """`name` prefixed with the flag for its country.

    `resolve=False` skips the DNS lookup for hostname addresses — see
    `_to_ip`. Such a node gets no flag from that path rather than a stalled
    write. `country` supplies the answer directly (see `resolve_country`).

    `keep_on_miss` returns the name **untouched** when no country could be
    determined, instead of stripping a flag that is already there. The write
    hook needs that: it runs on every update with `resolve=False`, so without
    it a hostname node's flag was removed by the very commit that set it —
    including the one `/apply-country-flags` makes, which is why that button
    reported renaming nodes that then showed no flag.
    """
    code = resolve_country(address, resolve=resolve, country=country)
    if code is None and keep_on_miss:
        return name
    stripped = strip_leading_flag(name)
    flag = flag_emoji(code or "")
    return f"{flag} {stripped}" if flag else stripped


def enrich_parsed_nodes(nodes: List[dict]) -> None:
    """Mutate each parsed node dict's `name` in place. Fast no-op when the
    GeoIP DB is absent, so it's safe to call on every import."""
    if _get_reader() is None:
        return
    for n in nodes:
        addr = n.get("address")
        name = n.get("name")
        if addr and name:
            n["name"] = enrich_name(name, addr)


def install_node_listener() -> None:
    """Flag every Node as it is written, whatever created it.

    Enrichment used to be called at the two import paths, and nodes are
    created at nine — so a node from a server deploy or an x-ui panel never
    got a flag, and the feature looked broken to anyone whose nodes came
    from there. Hooking the model instead of the call sites means the next
    path added gets it for free, which is the failure this had already.

    `node.country` — the exit country a speed test observed through the
    tunnel — is preferred over the address when it is set, and needs no
    GeoLite2 database at all. Otherwise the address is looked up, but DNS is
    deliberately skipped: this runs inside the flush, and a stalled resolver
    would hold up the write. Nothing determinable means the name is left
    exactly as it is (`keep_on_miss`) — this hook fires on every update, and
    stripping there wiped flags other paths had just resolved and applied.
    """
    from sqlalchemy import event
    from app.models import Node

    def _apply(_mapper, _conn, target) -> None:
        try:
            if getattr(target, "name", None):
                target.name = enrich_name(
                    target.name,
                    getattr(target, "address", "") or "",
                    resolve=False,
                    country=getattr(target, "country", None),
                    keep_on_miss=True,
                )
        except Exception:  # noqa: BLE001 — a cosmetic prefix must never
            pass          # block writing the node itself

    for when in ("before_insert", "before_update"):
        event.listen(Node, when, _apply)
