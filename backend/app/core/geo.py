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


async def _download_file(url: str, dest: str) -> None:
    """Stream-download a file with progress logging."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(dest_path) + ".tmp"

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        if pct % 20 == 0:
                            logger.debug("Download %s: %d%%", dest_path.name, pct)

    os.replace(tmp_path, dest)
    logger.info("Downloaded %s (%d bytes) to %s", url, downloaded, dest)


async def update_geoip(url: Optional[str] = None) -> None:
    target_url = url or settings.geoip_url
    await _download_file(target_url, settings.xray_geoip_path)


async def update_geosite(url: Optional[str] = None) -> None:
    target_url = url or settings.geosite_url
    await _download_file(target_url, settings.xray_geosite_path)


async def update_mmdb(url: Optional[str] = None) -> None:
    target_url = url or settings.geoip_mmdb_url
    await _download_file(target_url, settings.geoip_mmdb_path)


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
