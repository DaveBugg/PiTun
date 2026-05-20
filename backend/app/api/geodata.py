"""GeoIP/GeoSite management endpoints."""
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas import (
    GeoDataStatus,
    GeoDataUpdateRequest,
    GeoUpdateProgress,
    GeoUpdateFileProgress,
)

router = APIRouter(prefix="/geodata", tags=["geodata"])


@router.get("/categories")
async def get_geo_categories():
    """Return the lower-cased sorted lists of `geosite:*` and `geoip:*`
    tags available in the currently loaded `.dat` files. Used by the
    Routing rule editor for autocomplete.

    Empty arrays mean "tag cache not loaded yet" — caller should treat
    autocomplete as unavailable and fall back to free-form text input.
    Cache is populated at backend startup and after a successful Geo
    update; see `app.core.geo.refresh_tag_cache`.
    """
    from app.core.geo import AVAILABLE_GEOSITE_TAGS, AVAILABLE_GEOIP_TAGS
    return {
        "geosite": sorted(AVAILABLE_GEOSITE_TAGS),
        "geoip": sorted(AVAILABLE_GEOIP_TAGS),
    }


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

    Live progress is exposed at `GET /update/progress` for the UI to
    poll. The state singleton resets on each call to this endpoint;
    don't fire multiple concurrent updates expecting independent
    progress views.

    Concurrent calls are refused (409 Conflict). Without this guard
    a double-click on the UI's "Update" button used to spawn two
    overlapping download tasks racing on the `.tmp` write path —
    first one renamed `.tmp` → `geoip.dat`, second one then tried
    to rename a now-missing `.tmp` and surfaced as `FileNotFoundError`.
    The per-call tmp filename in `_download_file` is the second line
    of defence; this 409 is the first.
    """
    from app.core import geo_progress

    if geo_progress.get_state().active:
        existing = geo_progress.get_state()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "geodata update already in progress",
                "job_id": existing.job_id,
                "hint": "Poll GET /api/geodata/update/progress; wait for it to finish before retrying.",
            },
        )

    update_type = body.type or "all"

    do_geoip = update_type in ("geoip", "all")
    do_geosite = update_type in ("geosite", "all")
    do_mmdb = update_type in ("mmdb", "all")

    files = []
    if do_geoip:
        files.append("geoip")
    if do_geosite:
        files.append("geosite")
    if do_mmdb:
        files.append("mmdb")

    # Seed the progress state synchronously so the very first poll
    # right after `update.mutate()` returns sees `active=true` —
    # otherwise the frontend can race the background task and show
    # "no job in flight" for ~50ms before the first chunk lands.
    job_id = geo_progress.start_job(files)

    background_tasks.add_task(
        _do_update,
        body.geoip_url if do_geoip else None,
        body.geosite_url if do_geosite else None,
        body.mmdb_url if do_mmdb else None,
        do_geoip,
        do_geosite,
        do_mmdb,
    )
    return {"status": "update queued", "type": update_type, "job_id": job_id}


@router.get("/update/progress", response_model=GeoUpdateProgress)
async def get_update_progress():
    """Return the latest geo-update job's progress snapshot.

    Polled by the frontend ~2 Hz while a job is active (see
    `useGeoUpdateProgress` hook). Returns `active=false` and zero-
    state files when no job has run since the backend started; that
    distinction lets the UI suppress the progress UI on first paint.
    """
    from app.core import geo_progress

    state = geo_progress.get_state()
    return GeoUpdateProgress(
        job_id=state.job_id,
        active=state.active,
        started_at=state.started_at,
        finished_at=state.finished_at,
        tag_cache_refreshed=state.tag_cache_refreshed,
        files={
            name: GeoUpdateFileProgress(
                stage=fp.stage,
                bytes_downloaded=fp.bytes_downloaded,
                bytes_total=fp.bytes_total,
                started_at=fp.started_at,
                finished_at=fp.finished_at,
                error=fp.error,
                source_url=fp.source_url,
            )
            for name, fp in state.files.items()
        },
    )


async def _do_update(
    geoip_url=None,
    geosite_url=None,
    mmdb_url=None,
    do_geoip=True,
    do_geosite=True,
    do_mmdb=True,
):
    from app.core.geo import update_geoip, update_geosite, update_mmdb
    from app.core import geo_progress
    import logging
    import asyncio

    logger = logging.getLogger(__name__)

    # Per-file wrapper so an exception in one download is contained
    # AND surfaces in the progress state with a user-readable message.
    # Without this, asyncio.gather(return_exceptions=True) hides
    # individual failures from the UI — the user would see "all done"
    # even if geosite.dat 404'd.
    async def _run(name: str, coro) -> None:
        try:
            await coro
            geo_progress.set_stage(name, "applying")
            geo_progress.mark_done(name)
        except Exception as exc:
            # Compact, user-readable. Full traceback goes to logs.
            short = type(exc).__name__
            detail = str(exc)[:200] if str(exc) else "no detail"
            geo_progress.set_error(name, f"{short}: {detail}")
            logger.exception("GeoData update error for %s", name)
            raise

    tasks = []
    if do_geoip:
        tasks.append(_run("geoip", update_geoip(geoip_url or None)))
    if do_geosite:
        tasks.append(_run("geosite", update_geosite(geosite_url or None)))
    if do_mmdb:
        tasks.append(_run("mmdb", update_mmdb(mmdb_url or None)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    any_succeeded = any(not isinstance(r, Exception) for r in results)

    # Invalidate the geosite/geoip tag cache so the new file's tag set
    # immediately takes effect for rule pre-flight validation and the
    # /api/geo/categories autocomplete endpoint. Off-loop: the parser
    # is sync I/O on a 5-10 MB file, ~50 ms on a Pi 4.
    if any_succeeded:
        try:
            from app.core.geo import refresh_tag_cache
            await asyncio.to_thread(refresh_tag_cache)
            geo_progress.mark_tag_cache_refreshed()
        except Exception as exc:
            logger.warning("Geo tag cache refresh after update failed: %s", exc)

    # Mark the whole job done — frontend polling stops one cycle
    # later because `active` flips to False.
    geo_progress.finish()
