"""Decoy-site templates API (since v1.3.0-beta.6).

  GET    /api/templates           list built-in + uploaded custom
  POST   /api/templates/upload    upload a custom .zip
  DELETE /api/templates/{id}      remove a custom template

Built-ins live in `app.core.templates` (5 entries hard-coded in
the registry); custom uploads live on disk under
`/app/data/templates/custom/<id>/`. Both kinds surface through
this endpoint so the frontend picker can render them in one
gallery without caring about the storage difference.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core import custom_templates
from app.core.templates import TEMPLATES

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateRead(BaseModel):
    id: str
    label: str
    description: str
    kind: str  # "single_html" | "git_repo" | "custom"
    # True when the template ships server-side PHP and the deploy
    # runner needs to force `INSTALL_PHP=yes`. Surfaces in the UI as
    # a "needs PHP" badge so users understand why selecting this
    # template auto-enables the PHP toggle.
    requires_php: bool = False
    # Only populated for custom uploads; the UI uses these to render
    # a "Delete" button and a tooltip with the original filename.
    custom_byte_size: Optional[int] = None
    custom_filename: Optional[str] = None
    custom_created_at: Optional[str] = None


@router.get("", response_model=List[TemplateRead])
async def list_templates():
    """Return the available decoy-site templates (built-in + custom).

    The actual env-var resolution happens server-side in
    `core.templates.resolve_to_env`; this endpoint exposes only
    user-facing metadata so accidentally leaking source URLs out of
    a misconfigured frontend can't disclose where the decoy comes
    from (negligible secrecy benefit, but good hygiene)."""
    out: list[TemplateRead] = [
        TemplateRead(
            id=t.id,
            label=t.label,
            description=t.description,
            kind=t.kind,
            requires_php=t.requires_php,
        )
        for t in TEMPLATES
    ]
    # Custom uploads always render after built-ins so the curated
    # gallery stays prominent. Sort within custom by newest-first
    # (the helper already handles that).
    for cm in custom_templates.list_custom():
        out.append(TemplateRead(
            id=cm.id,
            label=cm.label,
            description=cm.description,
            kind="custom",
            requires_php=cm.requires_php,
            custom_byte_size=cm.byte_size,
            custom_filename=cm.original_filename or None,
            custom_created_at=cm.created_at or None,
        ))
    return out


@router.post("/upload", response_model=TemplateRead, status_code=201)
async def upload_template(
    label: str = Form(..., description="Display label, ≤80 chars"),
    description: str = Form("", description="Optional description, ≤500 chars"),
    archive: UploadFile = File(..., description=".zip with index.html + static assets"),
):
    """Accept a user-supplied .zip. Validates against:
      * 10 MB compressed limit, 50 MB extracted (zip-bomb guard)
      * 2000-entry cap, max 16-deep nesting
      * extension whitelist (html/css/js/img/font/media + .php —
        no shell scripts, no binaries; .php is allowed because the
        VPS runs a hardened php-fpm jail when the archive contains
        any .php file, so dynamic decoys like fake-2FA pages can
        survive view-source inspection)
      * zip-slip safety (no '..' or absolute paths)
      * must contain at least one index.html or index.php (root
        or one level deep)

    On success, persists the archive to `/app/data/templates/custom/
    <id>/archive.zip` so it survives container restarts and gets
    SFTP'd to the VPS at deploy time. The id is `<slug>-<6 hex>`
    derived from the label.
    """
    raw = await archive.read()
    try:
        meta = custom_templates.create_custom(
            label=label,
            description=description,
            archive_bytes=raw,
            original_filename=archive.filename or "",
        )
    except custom_templates.TemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # FileExistsError on slug collision (rare)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save template: {exc}",
        )
    return TemplateRead(
        id=meta.id,
        label=meta.label,
        description=meta.description,
        kind="custom",
        requires_php=meta.requires_php,
        custom_byte_size=meta.byte_size,
        custom_filename=meta.original_filename or None,
        custom_created_at=meta.created_at or None,
    )


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str):
    """Remove a custom template. Built-ins are immutable — attempting
    to delete one (`pacman`, `corporate`, etc.) returns 400."""
    # Refuse to even look at built-in ids — they're not stored on
    # disk and pretending to delete them would be misleading.
    builtin_ids = {t.id for t in TEMPLATES}
    if template_id in builtin_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Built-in template {template_id!r} cannot be deleted",
        )
    if not custom_templates.delete_custom(template_id):
        raise HTTPException(
            status_code=404,
            detail=f"Custom template {template_id!r} not found",
        )
