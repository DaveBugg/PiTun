"""Unit tests for `scripts/pitun-update.sh`.

The script is sourceable (`main` runs only when executed directly), so we
can call its functions in isolation with stubbed `curl`/`docker` on PATH.
Skipped where bash is unavailable.
"""
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pitun-update.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or not SCRIPT.exists(), reason="bash or script unavailable",
)


def run_bash(body: str, *, path_dir: Path | None = None, env: dict | None = None):
    """Source the script and run `body`, with an optional stub dir first on PATH."""
    full_env = dict(os.environ)
    if path_dir is not None:
        full_env["PATH"] = f"{path_dir}{os.pathsep}{full_env['PATH']}"
    full_env.update(env or {})
    script = f'source "{SCRIPT.as_posix()}"\n{body}\n'
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, env=full_env, timeout=60,
    )


def stub(dirpath: Path, name: str, body: str) -> None:
    p = dirpath / name
    p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), newline="\n")
    p.chmod(0o755)


class TestSyntax:
    def test_script_parses(self):
        assert subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0

    def test_sourcing_does_not_execute_main(self):
        # A test that sources the script must not kick off an update.
        res = run_bash('echo SOURCED_OK')
        assert "SOURCED_OK" in res.stdout
        assert "Network path" not in res.stdout


class TestVersionComparison:
    @pytest.mark.parametrize(("newer", "older"), [
        ("v1.4.8", "1.4.7"),
        ("1.5.0", "1.4.9"),
        ("2.0.0", "1.9.9"),
        # String comparison gets this one wrong — 1.4.10 sorts before 1.4.9.
        ("v1.4.10", "v1.4.9"),
        # A stable release supersedes its own pre-release.
        ("1.5.0", "1.5.0-beta.1"),
    ])
    def test_detects_newer(self, newer, older):
        res = run_bash(f'version_gt "{newer}" "{older}" && echo YES || echo NO')
        assert "YES" in res.stdout, f"{newer} should be newer than {older}"

    @pytest.mark.parametrize(("a", "b"), [
        ("1.4.7", "1.4.7"),
        ("v1.4.7", "1.4.7"),      # the `v` prefix is cosmetic
        ("1.4.7", "1.4.8"),
        ("1.4.9", "1.4.10"),
        ("1.5.0-beta.1", "1.5.0"),  # a pre-release is not an upgrade
    ])
    def test_rejects_same_or_older(self, a, b):
        res = run_bash(f'version_gt "{a}" "{b}" && echo YES || echo NO')
        assert "NO" in res.stdout, f"{a} must not count as newer than {b}"


class TestNetworkPath:
    """The box TPROXYs its own traffic, so the updater must prove it has a
    usable path before touching anything — and must not brick itself when
    the tunnel is down."""

    def _stubs(self, tmp_path, *, socks_ok: bool, direct_ok: bool,
               kill_switch="false", auth="false"):
        d = tmp_path / "bin"
        d.mkdir(exist_ok=True)
        stub(d, "curl", f"""
            for a in "$@"; do
              if [[ "$a" == socks5h://* ]]; then
                exit {0 if socks_ok else 7}
              fi
            done
            exit {0 if direct_ok else 7}
        """)
        stub(d, "docker", f"""
            # `setting()` pipes a python snippet through docker exec.
            case "$*" in
              *kill_switch*)             echo "{kill_switch}" ;;
              *lan_proxy_auth_enabled*)  echo "{auth}" ;;
              *socks_port*)              echo "1080" ;;
              *)                         echo "" ;;
            esac
        """)
        return d

    def test_prefers_the_active_node(self, tmp_path):
        d = self._stubs(tmp_path, socks_ok=True, direct_ok=True)
        res = run_bash(
            'resolve_network_path && echo "PATH=$NET_PATH" '
            '&& echo "ARGS=${CURL_NET_ARGS[*]}"', path_dir=d,
        )
        assert "PATH=active node" in res.stdout
        assert "socks5h://127.0.0.1:1080" in res.stdout

    def test_falls_back_to_direct_when_tunnel_is_dead(self, tmp_path):
        # The update must still happen when the node is down — otherwise
        # a broken tunnel would also block the fix for it.
        d = self._stubs(tmp_path, socks_ok=False, direct_ok=True)
        res = run_bash(
            'resolve_network_path && echo "PATH=$NET_PATH" '
            '&& echo "ARGS=${CURL_NET_ARGS[*]}"', path_dir=d,
        )
        assert "PATH=direct" in res.stdout
        assert "ARGS=" in res.stdout and "socks5h" not in res.stdout

    def test_no_proxy_flag_skips_the_node_entirely(self, tmp_path):
        d = self._stubs(tmp_path, socks_ok=True, direct_ok=True)
        res = run_bash(
            'USE_PROXY=0; resolve_network_path && echo "PATH=$NET_PATH"',
            path_dir=d,
        )
        assert "PATH=direct" in res.stdout

    def test_lan_proxy_auth_skips_socks(self, tmp_path):
        # With auth enabled we have no credentials; the default TPROXY
        # route already egresses through the same node.
        d = self._stubs(tmp_path, socks_ok=True, direct_ok=True, auth="true")
        res = run_bash('resolve_network_path && echo "PATH=$NET_PATH"', path_dir=d)
        assert "PATH=direct" in res.stdout

    def test_reports_kill_switch_when_everything_fails(self, tmp_path):
        d = self._stubs(
            tmp_path, socks_ok=False, direct_ok=False, kill_switch="true",
        )
        res = run_bash(
            'resolve_network_path || echo "RC=fail"',
            path_dir=d, env={"PITUN_UPDATE_LOG": ""},
        )
        assert "RC=fail" in res.stdout
        assert "Kill switch is ARMED" in res.stdout


class TestVersionDiscovery:
    def test_running_version_parsed_from_health(self, tmp_path):
        d = tmp_path / "bin"
        d.mkdir()
        stub(d, "curl", """
            echo '{"status":"ok","xray_running":true,"version":"1.4.8"}'
        """)
        res = run_bash('running_version', path_dir=d)
        assert res.stdout.strip() == "1.4.8"

    def test_latest_release_tag_parsed(self, tmp_path):
        d = tmp_path / "bin"
        d.mkdir()
        stub(d, "curl", """
            echo '{"tag_name":"v1.4.7","name":"v1.4.7","assets":[]}'
        """)
        res = run_bash('latest_release', path_dir=d)
        assert res.stdout.strip() == "v1.4.7"

    def test_prerelease_picks_the_newest_listed_tag(self, tmp_path):
        d = tmp_path / "bin"
        d.mkdir()
        stub(d, "curl", """
            echo '[{"tag_name":"v1.5.0-beta.1"},{"tag_name":"v1.4.7"}]'
        """)
        res = run_bash('ALLOW_PRERELEASE=1; latest_release', path_dir=d)
        assert res.stdout.strip() == "v1.5.0-beta.1"


class TestStatusFile:
    """The UI polls a file, not a stream: the update restarts the backend,
    so any connection would die exactly when the user most wants to know
    what happened. The file has to stay valid JSON throughout."""

    def _run_with_status(self, tmp_path, body):
        status = tmp_path / "update-status.json"
        res = run_bash(
            f'STATUS_FILE="{status.as_posix()}"; LOG_FILE=""; {body}',
        )
        return status, res

    def test_writes_parsable_json(self, tmp_path):
        import json

        status, _ = self._run_with_status(
            tmp_path, 'STATE_FROM=1.4.7; STATE_TO=v1.4.8; '
                      'status_write running 42 install "Installing v1.4.8"',
        )
        doc = json.loads(status.read_text())
        assert doc["state"] == "running"
        assert doc["pct"] == 42
        assert doc["step"] == "install"
        assert doc["message"] == "Installing v1.4.8"
        assert doc["from"] == "1.4.7" and doc["to"] == "v1.4.8"

    def test_escapes_quotes_and_newlines(self, tmp_path):
        import json

        status, _ = self._run_with_status(
            tmp_path,
            'status_write failed 0 err "he said \\"boom\\" and left"',
        )
        # Installer output lands in `message` verbatim, so an unescaped
        # quote would leave the UI polling broken JSON forever.
        doc = json.loads(status.read_text())
        assert doc["message"] == 'he said "boom" and left'

    def test_terminal_states_carry_the_ok_flag(self, tmp_path):
        import json

        status, _ = self._run_with_status(
            tmp_path, 'status_write done 100 done "Updated" true',
        )
        assert json.loads(status.read_text())["ok"] is True


class TestProgressMapping:
    """install.sh announces phases as `[STEP] …`; the updater turns those
    into a coarse percentage. Coarse on purpose — phases are what the
    installer exposes, byte counts are not."""

    @pytest.mark.parametrize(("line", "expect_pct", "expect_step"), [
        ("[STEP]  Downloading pitun-backend-v1.4.8-arm64.tar.gz", "25", "download"),
        ("[STEP]  Loading pre-built Docker images (no build needed)", "70", "images"),
        ("[STEP]  Starting Docker stack", "88", "restart"),
        ("[STEP]  Backing up SQLite (pre-upgrade snapshot)", "60", "backup"),
    ])
    def test_known_phases_map_to_progress(self, line, expect_pct, expect_step):
        res = run_bash(f'step_to_pct "{line}"')
        assert res.stdout.strip() == f"{expect_pct}|{expect_step}"

    def test_unknown_lines_produce_nothing(self):
        res = run_bash('step_to_pct "some random installer chatter"')
        assert res.stdout.strip() == ""


class TestImageCleanup:
    """Every update loads `pitun-backend:vX.Y.Z` and retags `:latest`,
    leaving the old tag behind. On a 32 GB card a few of those matter."""

    def _docker_stub(self, tmp_path, images, running=""):
        d = tmp_path / "bin"
        d.mkdir(exist_ok=True)
        removed = tmp_path / "removed.txt"
        stub(d, "docker", f"""
            case "$1" in
              ps)     printf '%s' "{running}" ;;
              images) printf '%s' "{images}" ;;
              rmi)    echo "$2" >> "{removed.as_posix()}" ;;
              image)  : ;;
            esac
        """)
        return d, removed

    def test_removes_superseded_release_tags(self, tmp_path):
        images = (
            "pitun-backend:v1.4.6 aaa\npitun-backend:v1.4.7 bbb\n"
            "pitun-backend:latest ccc\n"
        )
        d, removed = self._docker_stub(tmp_path, images)
        run_bash('LOG_FILE=""; prune_old_images', path_dir=d)
        got = removed.read_text().split() if removed.exists() else []
        assert "aaa" in got and "bbb" in got

    def test_never_touches_latest_or_a_running_image(self, tmp_path):
        images = (
            "pitun-backend:v1.4.7 bbb\npitun-backend:latest ccc\n"
            "pitun-naive:v1.4.8 ddd\n"
        )
        # v1.4.7 is what a container is actually running right now.
        d, removed = self._docker_stub(
            tmp_path, images, running="pitun-backend:v1.4.7",
        )
        run_bash('LOG_FILE=""; prune_old_images', path_dir=d)
        got = removed.read_text().split() if removed.exists() else []
        assert "ccc" not in got, ":latest is what compose references"
        assert "bbb" not in got, "image of a running container must survive"
        assert "ddd" in got

    def test_leaves_foreign_images_alone(self, tmp_path):
        images = "nginx:1.25-alpine eee\ntecnativa/docker-socket-proxy:0.3 fff\n"
        d, removed = self._docker_stub(tmp_path, images)
        run_bash('LOG_FILE=""; prune_old_images', path_dir=d)
        assert not removed.exists(), "only PiTun's own images are ours to delete"


class TestAgentRequest:
    """The backend can't restart itself, so the UI drops a request file
    and a host-side systemd path unit picks it up."""

    def _agent(self, tmp_path, request_body):
        req = tmp_path / "update-request.json"
        req.write_text(request_body, newline="\n")
        marker = tmp_path / "ran.txt"
        res = run_bash(
            f'REQUEST_FILE="{req.as_posix()}"; LOG_FILE=""; '
            f'run_update() {{ echo "version=$WANT_VERSION force=$FORCE '
            f'prerelease=$ALLOW_PRERELEASE" > "{marker.as_posix()}"; }}; '
            'agent_once',
        )
        return req, marker, res

    def test_parses_version_force_and_channel(self, tmp_path):
        _req, marker, _ = self._agent(
            tmp_path, '{"version":"v1.5.0","force":true,"prerelease":true}',
        )
        assert marker.read_text().strip() == "version=v1.5.0 force=1 prerelease=1"

    def test_consumes_the_request_so_it_cannot_loop(self, tmp_path):
        # The path unit re-fires while the file exists; a request that
        # kills the agent must not be retried forever.
        req, _marker, _ = self._agent(tmp_path, '{"version":"v1.5.0"}')
        assert not req.exists()

    def test_no_request_is_a_clean_no_op(self, tmp_path):
        res = run_bash(
            f'REQUEST_FILE="{(tmp_path / "absent.json").as_posix()}"; LOG_FILE=""; '
            'run_update() { echo SHOULD_NOT_RUN; }; agent_once',
        )
        assert "SHOULD_NOT_RUN" not in res.stdout
        assert res.returncode == 0


class TestSnapshotRetention:
    """`install.sh` snapshots the DB before every upgrade and never
    removes it, so they accumulate one per update forever. We prune —
    but only after a verified-healthy update, and never below the limit:
    a failed update is precisely when the older ones are worth having."""

    def _snapshots(self, d: Path, names_with_age: list[tuple[str, int]]) -> None:
        """Create snapshots with explicit, distinct mtimes (oldest = biggest age)."""
        for name, age_min in names_with_age:
            f = d / name
            f.write_text("db")
            subprocess.run(
                [BASH, "-c", f'touch -d "-{age_min} minutes" "{f.as_posix()}"'],
                check=True,
            )

    def test_keeps_the_three_newest_and_drops_the_rest(self, tmp_path):
        self._snapshots(tmp_path, [
            ("data-backup-pre-v1.4.8-500.db", 1),
            ("data-backup-pre-v1.4.7-400.db", 2),
            ("data-backup-pre-v1.4.6-300.db", 3),
            ("data-backup-pre-v1.4.5-200.db", 4),
            ("data-backup-pre-v1.4.4-100.db", 5),
        ])
        r = run_bash("prune_old_snapshots", env={"PITUN_DIR": str(tmp_path)})
        assert r.returncode == 0, r.stderr

        left = sorted(p.name for p in tmp_path.glob("data-backup-pre-*.db"))
        assert left == [
            "data-backup-pre-v1.4.6-300.db",
            "data-backup-pre-v1.4.7-400.db",
            "data-backup-pre-v1.4.8-500.db",
        ]

    def test_noop_when_at_or_below_the_limit(self, tmp_path):
        self._snapshots(tmp_path, [
            ("data-backup-pre-v1.4.8-3.db", 1),
            ("data-backup-pre-v1.4.7-2.db", 2),
        ])
        r = run_bash("prune_old_snapshots", env={"PITUN_DIR": str(tmp_path)})
        assert r.returncode == 0
        assert len(list(tmp_path.glob("data-backup-pre-*.db"))) == 2

    def test_limit_is_configurable(self, tmp_path):
        self._snapshots(tmp_path, [
            ("data-backup-pre-a.db", 1),
            ("data-backup-pre-b.db", 2),
            ("data-backup-pre-c.db", 3),
        ])
        r = run_bash(
            "prune_old_snapshots",
            env={"PITUN_DIR": str(tmp_path), "KEEP_SNAPSHOTS": "1"},
        )
        assert r.returncode == 0
        left = [p.name for p in tmp_path.glob("data-backup-pre-*.db")]
        assert left == ["data-backup-pre-a.db"]

    def test_touches_nothing_but_snapshots(self, tmp_path):
        # The live DB and unrelated backups share the directory.
        (tmp_path / "pitun.db").write_text("live")
        (tmp_path / "data-backup-manual.db").write_text("mine")
        (tmp_path / "notes.txt").write_text("x")
        self._snapshots(tmp_path, [
            ("data-backup-pre-1.db", 1), ("data-backup-pre-2.db", 2),
            ("data-backup-pre-3.db", 3), ("data-backup-pre-4.db", 4),
        ])
        r = run_bash("prune_old_snapshots", env={"PITUN_DIR": str(tmp_path)})
        assert r.returncode == 0
        assert (tmp_path / "pitun.db").exists()
        assert (tmp_path / "data-backup-manual.db").exists()
        assert (tmp_path / "notes.txt").exists()

    def test_empty_directory_is_not_an_error(self, tmp_path):
        r = run_bash("prune_old_snapshots", env={"PITUN_DIR": str(tmp_path)})
        assert r.returncode == 0

    def test_pruning_runs_only_after_a_healthy_update(self):
        # Placement matters more than the function: called before the
        # health gate, a failed update would delete the very snapshot
        # needed to recover from it.
        src = SCRIPT.read_text(encoding="utf-8")
        health_gate = src.index("Backend did not report healthy")
        prune_call = src.index("\n    prune_old_snapshots")
        assert prune_call > health_gate, (
            "prune_old_snapshots must run after the health check, not before"
        )
