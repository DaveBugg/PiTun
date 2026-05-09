"""GeoData download and management."""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _download_file(
    url: str,
    dest: str,
    *,
    progress_name: Optional[str] = None,
) -> None:
    """Stream-download a file with progress reporting.

    When `progress_name` is set (e.g. 'geoip' / 'geosite' / 'mmdb'),
    each chunk pushes (downloaded, total) into the geo_progress
    singleton so the frontend's poll endpoint can render a live bar.
    Stage is set to 'downloading' on first chunk and flipped to
    'verifying' just before the atomic rename. Errors propagate to
    the caller — `_do_update` in api/geodata.py wraps each download
    in its own try/except and marks the corresponding file failed.
    """
    from app.core import geo_progress

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(dest_path) + ".tmp"

    if progress_name:
        geo_progress.set_stage(progress_name, "downloading", source_url=url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("content-length")
            total: Optional[int] = int(cl) if cl else None
            downloaded = 0
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_name:
                        # Push every chunk — the state is in-process
                        # and the poll endpoint just reads the latest
                        # snapshot, so there's no I/O cost per push.
                        geo_progress.update_bytes(progress_name, downloaded, total)
                    if total:
                        pct = downloaded * 100 // total
                        if pct % 20 == 0:
                            logger.debug("Download %s: %d%%", dest_path.name, pct)

    if progress_name:
        geo_progress.set_stage(progress_name, "verifying")

    os.replace(tmp_path, dest)
    logger.info("Downloaded %s (%d bytes) to %s", url, downloaded, dest)


async def update_geoip(url: Optional[str] = None) -> None:
    target_url = url or settings.geoip_url
    await _download_file(target_url, settings.xray_geoip_path, progress_name="geoip")


async def update_geosite(url: Optional[str] = None) -> None:
    target_url = url or settings.geosite_url
    await _download_file(target_url, settings.xray_geosite_path, progress_name="geosite")


async def update_mmdb(url: Optional[str] = None) -> None:
    target_url = url or settings.geoip_mmdb_url
    await _download_file(target_url, settings.geoip_mmdb_path, progress_name="mmdb")


def get_geoip_info() -> dict:
    path = Path(settings.xray_geoip_path)
    if path.exists():
        stat = path.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        }
    return {"exists": False, "size": None, "mtime": None}


def get_geosite_info() -> dict:
    path = Path(settings.xray_geosite_path)
    if path.exists():
        stat = path.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        }
    return {"exists": False, "size": None, "mtime": None}


def get_mmdb_info() -> dict:
    path = Path(settings.geoip_mmdb_path)
    if path.exists():
        stat = path.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        }
    return {"exists": False, "size": None, "mtime": None}


def get_all_geo_info() -> dict:
    return {
        "geoip": get_geoip_info(),
        "geosite": get_geosite_info(),
        "mmdb": get_mmdb_info(),
    }


# ── geosite.dat / geoip.dat tag parser + cache ───────────────────────────────
#
# Both files are protobuf-encoded (v2fly schemas: GeoSiteList /
# GeoIPList, each with a `repeated Entry` where Entry has
# `string country_code = 1` as the first field). For PiTun we only
# need the country_code tag list (`category-ads-all`, `cn`, `ru`, …)
# to validate routing rules referencing them — we never enumerate
# domains or CIDRs from the .dat at runtime.
#
# We avoid pulling a generic protobuf runtime by writing a tiny
# wire-format reader specialised for the "list of messages, each
# starting with a length-delimited string field 1" shape. ~30 LOC.
#
# Cache is module-level state populated at startup (see main.py) and
# invalidated after `POST /api/geo/update` succeeds. Read paths use
# the snapshot directly — no locks; the dict is replaced atomically
# (the GIL gives us the publish/visibility we need for `set` -> `set`).


# Public state — read by api/routing.py rule validation, api/geodata.py
# autocomplete endpoint. Empty set means "we don't have a parsed view of
# this file yet" — code reading these treats that as "skip validation"
# (fail-open) rather than rejecting every rule. Failing-closed on a
# read error in geo.py would brick the whole rule-CRUD surface.
AVAILABLE_GEOSITE_TAGS: set[str] = set()
AVAILABLE_GEOIP_TAGS: set[str] = set()


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf base-128 varint from `buf` starting at `pos`.
    Returns `(value, new_pos)`. Caller already validated `pos < len(buf)`.
    """
    result = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long (>10 bytes)")
    raise ValueError("varint truncated at end of buffer")


def _parse_top_level_country_codes(data: bytes) -> set[str]:
    """Extract every entry's `country_code` (field 1, string) from a
    `GeoSiteList` / `GeoIPList` protobuf payload.

    Both v2fly types share the wire-format shape we care about:
        message <List> {
            repeated <Entry> entry = 1;   // field 1, wire type 2 (length-delimited)
        }
        message <Entry> {
            string country_code = 1;      // field 1, wire type 2 (length-delimited string)
            ...                            // ignored
        }

    We scan field 1 of the outer list, recurse one level into each
    entry's bytes, and pull field 1 from the entry. Any unknown wire
    types or non-field-1 entries are skipped over with proper varint /
    length consumption — robust against schema additions upstream.
    """
    tags: set[str] = set()
    pos = 0
    n = len(data)
    while pos < n:
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:  # varint — skip
            _, pos = _read_varint(data, pos)
            continue
        if wire_type == 1:  # 64-bit fixed
            pos += 8
            continue
        if wire_type == 5:  # 32-bit fixed
            pos += 4
            continue
        if wire_type != 2:  # only length-delimited from here
            # Group-start/end (3/4) deprecated in proto3 — stop the world.
            raise ValueError(f"unsupported wire type {wire_type} at pos {pos}")
        length, pos = _read_varint(data, pos)
        entry = data[pos:pos + length]
        pos += length
        if field_num != 1:
            continue
        # Inside each entry, look for field 1 (country_code, string).
        ipos = 0
        while ipos < len(entry):
            itag, ipos = _read_varint(entry, ipos)
            ifield = itag >> 3
            iwire = itag & 0x7
            if iwire == 0:
                _, ipos = _read_varint(entry, ipos)
                continue
            if iwire == 1:
                ipos += 8
                continue
            if iwire == 5:
                ipos += 4
                continue
            if iwire != 2:
                break
            ilen, ipos = _read_varint(entry, ipos)
            if ifield == 1:
                code = entry[ipos:ipos + ilen].decode("utf-8", errors="replace")
                if code:
                    tags.add(code.lower())
                ipos += ilen
                # country_code found — entries usually have it first;
                # we could break here, but a well-formed file allows
                # multiple field-1 occurrences (last-one-wins in proto3).
                # Keep scanning to be safe; cheap.
            else:
                ipos += ilen
    return tags


def parse_geosite_tags(path: str | None = None) -> set[str]:
    """Parse `geosite.dat` and return the lower-cased set of available
    `geosite:<tag>` codes. Empty set on read error.
    """
    target = path or settings.xray_geosite_path
    try:
        with open(target, "rb") as f:
            data = f.read()
        return _parse_top_level_country_codes(data)
    except FileNotFoundError:
        logger.warning("geosite.dat not found at %s — tag cache empty", target)
        return set()
    except Exception as exc:
        logger.warning("Could not parse geosite.dat at %s: %s", target, exc)
        return set()


def parse_geoip_tags(path: str | None = None) -> set[str]:
    """Same as `parse_geosite_tags` but for `geoip.dat`."""
    target = path or settings.xray_geoip_path
    try:
        with open(target, "rb") as f:
            data = f.read()
        return _parse_top_level_country_codes(data)
    except FileNotFoundError:
        logger.warning("geoip.dat not found at %s — tag cache empty", target)
        return set()
    except Exception as exc:
        logger.warning("Could not parse geoip.dat at %s: %s", target, exc)
        return set()


def refresh_tag_cache() -> tuple[int, int]:
    """Re-parse both .dat files and update the module-level caches.
    Called once on backend startup and after every successful Geo
    update via the UI.

    Returns `(geosite_count, geoip_count)` for caller logging.
    """
    global AVAILABLE_GEOSITE_TAGS, AVAILABLE_GEOIP_TAGS
    geosite = parse_geosite_tags()
    geoip = parse_geoip_tags()
    # Atomic publish: rebind the module attribute so concurrent readers
    # that captured the old set keep working with consistent contents.
    AVAILABLE_GEOSITE_TAGS = geosite
    AVAILABLE_GEOIP_TAGS = geoip
    logger.info(
        "Geo tag cache refreshed: %d geosite tags, %d geoip tags",
        len(geosite), len(geoip),
    )
    return len(geosite), len(geoip)
