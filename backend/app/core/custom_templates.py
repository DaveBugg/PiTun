"""Custom decoy-site templates uploaded by the user (since v1.3.0-beta.6).

Phase 2 of the template gallery (Phase 1 = built-in registry, see
`app.core.templates`). Lets the admin drop their own static-site
.zip into PiTun. We validate it on the way in (zip-slip, extension
whitelist, sane size budget, must contain an index.html), then keep
the archive on disk until a deploy sends it onward to the VPS.

Storage layout
--------------
    /app/data/templates/custom/
        <id>/
            archive.zip   - the validated upload, byte-identical to
                             what the user submitted. Used verbatim
                             when SCP'ing to the VPS at deploy time.
            meta.json     - {label, description, original_filename,
                             sha256, created_at, byte_size}.

The data dir is the same volume backing `pitun.db`, so custom
templates persist across container restarts and can be backed up
together with the rest of PiTun's state.

Deploy delivery
---------------
The install script learns a new env var, `TEMPLATE_LOCAL_ARCHIVE`,
that points at a path on the VPS where the script will find the
SFTP'd zip. At deploy time, `exec_remote_script_streaming` accepts
an optional `extra_files` mapping that gets uploaded alongside
`/tmp/pitun-deploy/setup-*.sh`. The script extracts the archive
into `/var/www/html/` before falling through to the `DECOY_REPO`
git path or the `TEMPLATE_HTML_URL` curl path.

Threat model
------------
PiTun is single-user (admin gets a JWT). The threat is the admin
uploading a malicious / oversized / zip-slip archive that ends up
landing on their own VPS — single-tenant, but we still validate
to protect the local VPS filesystem from `../../etc/passwd` writes
during extraction.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Validation knobs ─────────────────────────────────────────────────────────

# Hard upper bound on the *uploaded* archive (compressed). Phase 2
# scope is small static-site decoys — corporate landings, doc sites,
# tiny games. Anything beyond a few megabytes is a sign someone's
# trying to ship something other than a cover page.
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024  # 10 MB

# Sum of decompressed sizes — independent zip-bomb guard. A 10 MB
# zip with a 100x compression ratio is still suspect; cap the
# total uncompressed payload at 50 MB.
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024  # 50 MB

# Files-per-archive cap — prevents 10000-empty-files DoS.
MAX_ENTRIES = 2000

# Static-asset whitelist. Rejected extensions can never end up
# served by Caddy as a decoy file (unless misconfigured) but we
# still refuse them to keep the surface honest. `.html` mandatory
# at least once; everything else is supporting media.
ALLOWED_EXTENSIONS = frozenset({
    ".html", ".htm",
    ".css",
    ".js", ".mjs", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".json", ".txt", ".xml", ".md",
    ".mp3", ".mp4", ".webm", ".ogg",  # decoys with media (e.g. pacman)
    ".pdf",
    # PHP allowed since v1.3.0-beta.6 — when an upload contains .php,
    # the meta gets `requires_php=True` and the deploy runner forces
    # `INSTALL_PHP=yes` so the VPS gets the hardened php-fpm install.
    # The Caddy `disable_functions` jail in setup-naive-server.sh
    # neutralises typical RCE / SSRF / data-exfil paths even for
    # vulnerable user-uploaded code; .phtml / .php3 / .php7 etc. are
    # NOT whitelisted because Caddy's `php_fastcgi` only matches *.php
    # by default — listing the variants here would be misleading.
    ".php",
})

# Subset that triggers the `requires_php` flag on the upload. Anything
# beyond .php (e.g. legacy .phtml) would need both an extension here
# and a Caddyfile pattern bump in setup-naive-server.sh.
PHP_EXTENSIONS = frozenset({".php"})

# Slug allowed chars for the user-supplied label → id derivation.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── Errors ────────────────────────────────────────────────────────────────────


class TemplateValidationError(ValueError):
    """Raised when an uploaded zip doesn't pass safety checks."""


# ── Storage location ─────────────────────────────────────────────────────────


def _data_dir() -> Path:
    """Resolve the data directory. Matches `app.config.settings.database_url`
    default (`./data/pitun.db`) and the docker-compose bind mount layout.
    """
    # Container path; on host this is a bind-mount via docker-compose.
    base = Path("/app/data")
    if base.exists():
        return base
    # Dev fallback when running uvicorn directly outside container.
    return Path.cwd() / "data"


def _custom_dir() -> Path:
    p = _data_dir() / "templates" / "custom"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Public types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CustomTemplateMeta:
    id: str
    label: str
    description: str
    original_filename: str
    sha256: str
    byte_size: int
    created_at: str
    # Set at upload time when the archive contains any `.php` file.
    # The deploy runner uses this to force `INSTALL_PHP=yes` so the
    # VPS gets php-fpm provisioned — without this, a php-needing
    # template would deploy onto a static-only Caddy and silently
    # serve the .php source as plain text. Default False keeps
    # backward-compat for meta.json files written before this field
    # existed.
    requires_php: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "original_filename": self.original_filename,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "created_at": self.created_at,
            "requires_php": self.requires_php,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CustomTemplateMeta":
        return cls(
            id=d["id"],
            label=d["label"],
            description=d.get("description", ""),
            original_filename=d.get("original_filename", ""),
            sha256=d.get("sha256", ""),
            byte_size=int(d.get("byte_size", 0)),
            created_at=d.get("created_at", ""),
            requires_php=bool(d.get("requires_php", False)),
        )


# ── Validation ────────────────────────────────────────────────────────────────


def _slugify(label: str) -> str:
    """Lowercase + strip-to-alnum-hyphen. Adds a short random
    suffix so two uploads with the same label don't collide."""
    base = _SLUG_RE.sub("-", label.strip().lower()).strip("-")
    base = base[:32] or "custom"
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-{suffix}"


def _validate_zip(data: bytes) -> bool:
    """Raise `TemplateValidationError` on any safety violation.
    Doesn't extract — just walks the zip's central directory.

    Returns True if the archive contains at least one `.php` file (so
    the caller can persist `requires_php=True` on the meta), False
    otherwise.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise TemplateValidationError(
            f"Archive is {len(data)} bytes, exceeds {MAX_ARCHIVE_BYTES} limit"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TemplateValidationError(f"Not a valid zip archive: {exc}")

    names = zf.namelist()
    if len(names) > MAX_ENTRIES:
        raise TemplateValidationError(
            f"Archive has {len(names)} entries, exceeds {MAX_ENTRIES} limit"
        )

    has_entry_point = False  # index.html OR index.php at root or 1-deep
    has_php = False
    extracted_total = 0
    for info in zf.infolist():
        name = info.filename
        # Skip directory entries — we recreate dirs at extract time.
        if name.endswith("/"):
            continue
        # Zip-slip / absolute path / parent traversal.
        if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
            raise TemplateValidationError(
                f"Unsafe path in archive: {name!r} (absolute or contains '..')"
            )
        # Enforce extension whitelist.
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise TemplateValidationError(
                f"Disallowed file type: {name!r} ({ext!r}). "
                f"Static assets only — see ALLOWED_EXTENSIONS."
            )
        if ext in PHP_EXTENSIONS:
            has_php = True
        # Track total uncompressed size (zip-bomb guard).
        extracted_total += info.file_size
        if extracted_total > MAX_EXTRACTED_BYTES:
            raise TemplateValidationError(
                f"Decompressed size > {MAX_EXTRACTED_BYTES} bytes — "
                "rejected as potential zip bomb."
            )
        # Per-entry sanity: nothing absurdly nested.
        if name.count("/") > 16:
            raise TemplateValidationError(
                f"Path nested too deep: {name!r}"
            )
        # Must contain at least one index.{html,php} (root or first level).
        # `index.php` only counts when the archive uses PHP — otherwise
        # the file would be served as plain text by Caddy without
        # php_fastcgi (= obvious decoy, defeats the point).
        base = Path(name).name.lower()
        depth = name.count("/")
        if depth <= 1 and base in ("index.html", "index.htm"):
            has_entry_point = True
        if depth <= 1 and base == "index.php":
            has_entry_point = True

    if not has_entry_point:
        raise TemplateValidationError(
            "Archive must contain an index.html or index.php at its root "
            "or one level deep (this is what Caddy will serve at /)."
        )
    return has_php


# ── CRUD ──────────────────────────────────────────────────────────────────────


def list_custom() -> List[CustomTemplateMeta]:
    """Scan the custom dir and return all valid entries. Sorted
    newest-first so the picker UI lists recent uploads at the top."""
    out: list[CustomTemplateMeta] = []
    for entry in _custom_dir().iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            logger.warning("Custom template dir without meta.json: %s", entry)
            continue
        try:
            meta = CustomTemplateMeta.from_dict(
                json.loads(meta_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            logger.warning("Bad meta.json in %s: %s", entry, exc)
            continue
        out.append(meta)
    out.sort(key=lambda m: m.created_at, reverse=True)
    return out


def get_custom(template_id: str) -> Optional[CustomTemplateMeta]:
    meta_path = _custom_dir() / template_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return CustomTemplateMeta.from_dict(
            json.loads(meta_path.read_text(encoding="utf-8"))
        )
    except Exception:
        return None


def get_archive_bytes(template_id: str) -> Optional[bytes]:
    """Return the raw archive bytes for SFTP'ing to a VPS. Used by
    the deploy runner when the user picked a custom template."""
    archive_path = _custom_dir() / template_id / "archive.zip"
    if not archive_path.exists():
        return None
    return archive_path.read_bytes()


def create_custom(
    *,
    label: str,
    description: str,
    archive_bytes: bytes,
    original_filename: str,
) -> CustomTemplateMeta:
    """Validate the uploaded zip + persist it. Returns the new
    meta on success; raises `TemplateValidationError` on rejection.
    """
    label = (label or "").strip()
    if not label:
        raise TemplateValidationError("Template label is required")
    if len(label) > 80:
        raise TemplateValidationError("Label too long (max 80 chars)")

    description = (description or "").strip()
    if len(description) > 500:
        raise TemplateValidationError("Description too long (max 500 chars)")

    has_php = _validate_zip(archive_bytes)

    template_id = _slugify(label)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    target_dir = _custom_dir() / template_id
    target_dir.mkdir(parents=True, exist_ok=False)
    try:
        (target_dir / "archive.zip").write_bytes(archive_bytes)
        meta = CustomTemplateMeta(
            id=template_id,
            label=label,
            description=description,
            original_filename=original_filename or "",
            sha256=digest,
            byte_size=len(archive_bytes),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            requires_php=has_php,
        )
        (target_dir / "meta.json").write_text(
            json.dumps(meta.to_dict(), indent=2), encoding="utf-8",
        )
    except Exception:
        # Roll back partial dir on any write error so list_custom doesn't
        # show half-created entries.
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    return meta


def delete_custom(template_id: str) -> bool:
    """Remove a custom template directory. Returns True if anything
    was deleted, False if the id wasn't there. Idempotent."""
    target_dir = _custom_dir() / template_id
    if not target_dir.exists():
        return False
    shutil.rmtree(target_dir)
    return True
