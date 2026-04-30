"""GeoIP/GeoSite management endpoints."""
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas import GeoDataStatus, GeoDataUpdateRequest

router = APIRouter(prefix="/geodata", tags=["geodata"])


@router.get("/status", response_model=GeoDataStatus)
async def get_geodata_status():
    from app.core.geo import get_geoip_info, get_geosite_info, get_mmdb_info

    geoip = get_geoip_info()
    geosite = get_geosite_info()
    mmdb = get_mmdb_info()
    return GeoDataStatus(
        geoip_exists=geoip["exists"],
        geoip_size=geoip.get("size"),
        geoip_mtime=geoip.get("mtime"),
        geosite_exists=geosite["exists"],
        geosite_size=geosite.get("size"),
        geosite_mtime=geosite.get("mtime"),
        mmdb_exists=mmdb["exists"],
        mmdb_size=mmdb.get("size"),
        mmdb_mtime=mmdb.get("mtime"),
    )


@router.post("/update", status_code=202)
async def update_geodata(
    background_tasks: BackgroundTasks,
    body: GeoDataUpdateRequest = GeoDataUpdateRequest(),
):
    """Trigger background download of geoip.dat and/or geosite.dat and/or GeoLite2.mmdb.

    Use the `type` field to control which files to update:
    - "geoip" — update geoip.dat only
    - "geosite" — update geosite.dat only
    - "mmdb" — update GeoLite2-Country.mmdb only
    - "all" or None — update all three
    """
    update_type = body.type or "all"

    do_geoip = update_type in ("geoip", "all")
    do_geosite = update_type in ("geosite", "all")
    do_mmdb = update_type in ("mmdb", "all")

    background_tasks.add_task(
        _do_update,
        body.geoip_url if do_geoip else None,
        body.geosite_url if do_geosite else None,
        body.mmdb_url if do_mmdb else None,
        do_geoip,
        do_geosite,
        do_mmdb,
    )
    return {"status": "update queued", "type": update_type}


async def _do_update(
    geoip_url=None,
    geosite_url=None,
    mmdb_url=None,
    do_geoip=True,
    do_geosite=True,
    do_mmdb=True,
):
    from app.core.geo import update_geoip, update_geosite, update_mmdb
    import logging
    import asyncio

    logger = logging.getLogger(__name__)
    tasks = []

    if do_geoip:
        tasks.append(update_geoip(geoip_url or None))
    if do_geosite:
        tasks.append(update_geosite(geosite_url or None))
    if do_mmdb:
        tasks.append(update_mmdb(mmdb_url or None))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.error("GeoData update error: %s", r)
