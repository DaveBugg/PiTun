"""index.html must be revalidated, or an update leaves the browser on the old UI."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_html_is_never_served_from_a_heuristic_cache():
    conf = (ROOT / "frontend" / "nginx-spa.conf").read_text(encoding="utf-8")
    m = re.search(r"location\s*=\s*/index\.html\s*\{([^}]*)\}", conf)
    assert m, "no exact-match location for index.html"
    assert 'Cache-Control "no-cache"' in m.group(1)


def test_hashed_assets_stay_long_cached():
    conf = (ROOT / "frontend" / "nginx-spa.conf").read_text(encoding="utf-8")
    assert "immutable" in conf


def test_the_installer_restarts_the_frontend_after_bringing_the_stack_up():
    src = (ROOT / "install.sh").read_text(encoding="utf-8")
    up = src.index("docker compose up -d\n    fi")
    assert "docker restart pitun-frontend" in src[up:up + 700]
