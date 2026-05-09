"""Decoy-site templates API (since v1.3.0-beta.6).

Read-only listing of the curated template gallery — the frontend
calls this once on modal open to populate the picker. Templates
themselves are static metadata defined in `app.core.templates`;
this module is purely an HTTP shell around it. Phase 2 will add a
POST /upload endpoint here for user-uploaded .zip archives.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.templates import TEMPLATES

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateRead(BaseModel):
    id: str
    label: str
    description: str
    kind: str  # "single_html" | "git_repo" (future: "custom")


@router.get("", response_model=List[TemplateRead])
async def list_templates():
    """Return the available decoy-site templates. Used by the Naive
    install forms (DeployModal + ManualScriptModal) to render a
    picker. The actual env-var resolution happens server-side in
    `core.templates.resolve_to_env`; this endpoint exposes only
    user-facing metadata so accidentally leaking source URLs out of
    a misconfigured frontend can't disclose where the decoy comes
    from (negligible secrecy benefit, but good hygiene)."""
    return [
        TemplateRead(
            id=t.id, label=t.label, description=t.description, kind=t.kind,
        )
        for t in TEMPLATES
    ]
