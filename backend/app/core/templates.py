"""Decoy-site templates registry (since v1.3.0-beta.6).

Forwards-proxy setups (NaiveProxy + Caddy) need a plausible-looking
public-facing site to serve at `/` for non-authenticated visitors —
otherwise the endpoint stands out as "obviously a proxy", which
defeats the whole point of the masquerade. Previously
`scripts/setup-naive-server.sh` shipped a single hardcoded default
(daleharvey/pacman). This module exposes a small curated gallery so
the user can pick a more contextually-plausible cover for their VPS.

Two kinds of source supported, transparently:
  * `single_html` — a single self-contained HTML file (with inline
    CSS/JS) hosted in the PiTun repo at
    `docker/naive/templates/<id>.html`. Resolved to a raw GitHub URL
    that the install script `curl`s into `/var/www/html/index.html`.
    Ideal for the lightweight cover-page templates we ship by
    default (corporate / blog / docs / maintenance).
  * `git_repo` — a public git repository the script clones in full
    into `/var/www/html/`. Used for richer multi-file decoys like
    daleharvey/pacman where a single file isn't expressive enough.

A future `custom` type (Phase 2) will accept user-uploaded .zip
archives stored on the PiTun host and SCP'd to the VPS at deploy
time. Out of scope for this commit.

Adding a new template
---------------------
For single-file: drop an `index.html` into
`docker/naive/templates/<new_id>.html`, then add a row here. The raw
URL resolves at runtime against `master` so newly-added templates
become available as soon as the user re-runs `install.sh` (the
script body lives in the same repo, so the version skew window is
tiny).

For git: add a row pointing at a public repo. Pin a commit SHA in
the `pinned_commit` field if reproducibility matters more than
"always get the latest decoy site". For our default `pacman` we
deliberately leave it floating since the upstream repo hasn't seen
a meaningful change in years.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional


# Branch / tag of the PiTun repo whose `docker/naive/templates/`
# tree backs the `single_html` URLs. Keeping a single constant lets
# us flip the whole gallery to a release tag during stable cuts
# without touching every row.
_REPO_BRANCH = "master"
_REPO_RAW_BASE = (
    "https://raw.githubusercontent.com/DaveBugg/PiTun/"
    f"{_REPO_BRANCH}/docker/naive/templates"
)

TemplateKind = Literal["single_html", "git_repo"]


@dataclass(frozen=True)
class DecoyTemplate:
    """One row in the gallery. The frontend renders a card per row;
    the backend resolves the row to either `TEMPLATE_HTML_URL` or
    `DECOY_REPO` env vars when generating an install plan."""
    id: str
    label: str
    description: str
    kind: TemplateKind
    # For `single_html`: filename inside the repo's templates/ dir.
    # For `git_repo`: the clone URL.
    source: str
    # Optional commit SHA (git) to pin reproducible decoys. Unused
    # for single_html (the raw URL doesn't accept a SHA at the
    # filename level — to pin those we'd swap _REPO_BRANCH).
    pinned_commit: Optional[str] = None


# Gallery — order is the order the UI renders cards in. `pacman`
# stays first because it was the prior hard-coded default; users
# upgrading from v1.3.0-beta.5 expect the same look unless they
# explicitly switch.
TEMPLATES: List[DecoyTemplate] = [
    DecoyTemplate(
        id="pacman",
        label="Pac-Man",
        description=(
            "Playable Pac-Man game (~2 MB, html+css+js+mp3). "
            "Recognisable, diverse asset mix that doesn't look "
            "like a proxy. Default through v1.3.0-beta.5."
        ),
        kind="git_repo",
        source="https://github.com/daleharvey/pacman",
    ),
    DecoyTemplate(
        id="corporate",
        label="Corporate landing",
        description=(
            "Generic 'Aether Systems' tech-company beta-signup "
            "page. Suits a business-y domain name. Single file, "
            "no external assets — loads instantly."
        ),
        kind="single_html",
        source="corporate.html",
    ),
    DecoyTemplate(
        id="blog",
        label="Personal blog",
        description=(
            "Minimal serif-typeset 'Margaret's Notebook' fake "
            "blog with four post excerpts. Suits hobby / "
            "personal-domain VPS. Single file."
        ),
        kind="single_html",
        source="blog.html",
    ),
    DecoyTemplate(
        id="docs",
        label="Library docs",
        description=(
            "Two-column documentation site for a fictional "
            "'libsleep' Python package. Sidebar navigation, "
            "code blocks, compatibility table. Suits a tech / "
            "open-source-flavoured domain."
        ),
        kind="single_html",
        source="docs.html",
    ),
    DecoyTemplate(
        id="maintenance",
        label="Maintenance page",
        description=(
            "'We'll be right back' scheduled-maintenance page "
            "with a soft animated spinner. Visitors are likely "
            "to leave and come back later — minimum scrutiny "
            "of the proxy itself."
        ),
        kind="single_html",
        source="maintenance.html",
    ),
]


def get_template(template_id: str) -> Optional[DecoyTemplate]:
    """Look up by id. Returns None for unknown ids — callers
    typically fall back to the script's built-in default rather
    than raising."""
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    return None


def resolve_to_env(template_id: Optional[str]) -> dict[str, str]:
    """Map a template id to the env vars the install script
    understands. Returns an empty dict for unknown / unset ids so
    callers can `env.update(...)` unconditionally.

    Conversions:
      * `git_repo` → `{"DECOY_REPO": <git url>}` (existing var,
         backward-compatible — pre-template-gallery deploys still
         work the same way).
      * `single_html` → `{"TEMPLATE_HTML_URL": <raw url>}` (new var
         this commit teaches the script to handle).

    The two are mutually exclusive — script logic prefers
    `TEMPLATE_HTML_URL` when both are set since single-file mode
    is deterministic and ~10 KB, whereas the git path needs apt
    install + clone (~2 MB + minutes on slow VPS).
    """
    if not template_id:
        return {}
    t = get_template(template_id)
    if t is None:
        return {}
    if t.kind == "git_repo":
        return {"DECOY_REPO": t.source}
    if t.kind == "single_html":
        return {"TEMPLATE_HTML_URL": f"{_REPO_RAW_BASE}/{t.source}"}
    return {}
