"""Outbound-SOCKS5 mode for WireGuard servers.

Covers the deploy env mapping (install-time), the retrofit endpoint
(`/servers/{id}/deployments/wireguard/outbound-socks`), and the
structural contract of the setup script's new sub-commands.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.deploy import build_plan, build_wireguard_env
from app.core.jobs import job_manager
from app.core.ssh import DeployResult
from app.models import Server, ServerDeployment

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"


# ── env mapping ──────────────────────────────────────────────────────────────


def test_install_plan_maps_outbound_socks_env():
    plan = build_plan("wireguard", {
        "client_name": "phone-1",
        "outbound_socks": {
            "host": "127.0.0.1", "port": 1081,
            "username": "u", "password": "p", "udp": True,
        },
    })
    assert plan.env["PITUN_WG_SUBCOMMAND"] == "install"
    assert plan.env["SOCKS_HOST"] == "127.0.0.1"
    assert plan.env["SOCKS_PORT"] == "1081"
    assert plan.env["SOCKS_USER"] == "u"
    assert plan.env["SOCKS_PASS"] == "p"
    assert plan.env["SOCKS_UDP"] == "yes"


def test_install_plan_without_outbound_socks_has_no_env():
    plan = build_plan("wireguard", {})
    assert "SOCKS_HOST" not in plan.env
    assert "SOCKS_UDP" not in plan.env


def test_outbound_socks_bad_port_raises():
    with pytest.raises(ValueError):
        build_plan("wireguard", {
            "outbound_socks": {"host": "127.0.0.1", "port": 70000},
        })


def test_socks_subcommands_dont_require_client_name():
    env = build_wireguard_env(
        client_name="", sub_command="setup-outbound-socks",
        socks_host="10.0.0.2",
    )
    assert env["PITUN_WG_SUBCOMMAND"] == "setup-outbound-socks"
    assert env["SOCKS_HOST"] == "10.0.0.2"
    assert "CLIENT_NAME" not in env

    env = build_wireguard_env(client_name="", sub_command="remove-outbound-socks")
    assert env["PITUN_WG_SUBCOMMAND"] == "remove-outbound-socks"


def test_client_subcommands_still_require_client_name():
    with pytest.raises(ValueError):
        build_wireguard_env(client_name="", sub_command="add-client")


# ── retrofit endpoint ────────────────────────────────────────────────────────


def _seed_wg(session):
    srv = Server(name="vps", host="192.0.2.5", port=22, user="root", auth_type="key")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    dep = ServerDeployment(
        server_id=srv.id, protocol="wireguard", status="deployed",
        config_json='{"server_port": 51820}',
    )
    session.add(dep)
    session.commit()
    return srv


def _ok_result():
    class R:
        ok = True
        stderr = ""
        error = ""
        exit_code = 0
        stdout = "OUTBOUND_SOCKS=enabled"
    return R()


def test_enable_persists_state(client, session, admin_user, auth_headers):
    srv = _seed_wg(session)
    with patch(
        "app.api.server_clients.exec_remote_script",
        new=AsyncMock(return_value=_ok_result()),
    ) as ex:
        resp = client.post(
            f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
            json={"enabled": True, "socks": {
                "host": "127.0.0.1", "port": 1080, "udp": False,
            }},
            headers=auth_headers,
        )
    assert resp.status_code == 200, resp.text
    env = ex.await_args.kwargs["env"]
    assert env["PITUN_WG_SUBCOMMAND"] == "setup-outbound-socks"
    assert env["SOCKS_HOST"] == "127.0.0.1"
    assert env["SOCKS_UDP"] == "no"

    state = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    ).json()
    assert state["enabled"] is True
    assert state["socks"]["host"] == "127.0.0.1"
    # Password round-trips so a later redeploy reproduces the mode.
    assert state["socks"]["port"] == 1080


def test_disable_clears_state(client, session, admin_user, auth_headers):
    srv = _seed_wg(session)
    with patch(
        "app.api.server_clients.exec_remote_script",
        new=AsyncMock(return_value=_ok_result()),
    ):
        client.post(
            f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
            json={"enabled": True, "socks": {"host": "127.0.0.1"}},
            headers=auth_headers,
        )
        resp = client.post(
            f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
            json={"enabled": False},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    state = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    ).json()
    assert state["enabled"] is False


def test_enable_without_host_is_400(client, session, admin_user, auth_headers):
    srv = _seed_wg(session)
    resp = client.post(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        json={"enabled": True, "socks": {"host": ""}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_outbound_socks_requires_wg_deployment(client, session, admin_user, auth_headers):
    srv = Server(name="bare", host="5.6.7.8", port=22, user="root", auth_type="key")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    resp = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ── script contract ──────────────────────────────────────────────────────────


def test_setup_script_exposes_socks_subcommands():
    text = (SCRIPTS / "setup-wireguard-server.sh").read_text(encoding="utf-8")
    for needle in (
        "setup-outbound-socks)",
        "remove-outbound-socks)",
        "SINGBOX_VERSION=",
        "SINGBOX_SHA256_AMD64=",
        "SINGBOX_SHA256_ARM64=",
        "PITUN-SOCKS-ROUTE-BEGIN",
        "sha256sum -c",
        '"action": "hijack-dns"',
        "OUTBOUND_SOCKS=enabled",
        "OUTBOUND_SOCKS=disabled",
    ):
        assert needle in text, f"setup-wireguard-server.sh lost {needle!r}"


def test_uninstall_cleans_socks_artifacts():
    text = (SCRIPTS / "uninstall-wireguard-server.sh").read_text(encoding="utf-8")
    for needle in (
        "pitun-sing-box",
        "pitun-outbound.json",
        "table 101",
    ):
        assert needle in text, f"uninstall script lost {needle!r}"


# ── install-time state persistence ───────────────────────────────────────────


@pytest.fixture
def reset_job_manager():
    """Reset the module-level `job_manager` singleton's RAM state so
    leftover slot locks from prior tests don't bleed across."""
    old_slots = dict(job_manager._slot_busy)
    old_tasks = dict(job_manager._tasks)
    old_buffers = dict(job_manager._buffers)
    old_subs = dict(job_manager._subscribers)

    job_manager._slot_busy.clear()
    job_manager._tasks.clear()
    job_manager._buffers.clear()
    job_manager._subscribers.clear()

    yield job_manager

    job_manager._slot_busy.clear()
    job_manager._slot_busy.update(old_slots)
    job_manager._tasks.clear()
    job_manager._tasks.update(old_tasks)
    job_manager._buffers.clear()
    job_manager._buffers.update(old_buffers)
    job_manager._subscribers.clear()
    job_manager._subscribers.update(old_subs)


def _wg_success_result() -> DeployResult:
    return DeployResult(
        ok=True,
        exit_code=0,
        stdout=(
            "PITUN-CLIENT-CONF-BEGIN client1\n"
            "[Interface]\n"
            "PrivateKey = cHJpdmtleQ==\n"
            "Address = 10.66.66.2/24\n"
            "DNS = 1.1.1.1,1.0.0.1\n"
            "\n"
            "[Peer]\n"
            "PublicKey = cHVia2V5\n"
            "AllowedIPs = 0.0.0.0/0, ::/0\n"
            "Endpoint = 192.0.2.5:51820\n"
            "PITUN-CLIENT-CONF-END client1\n"
            "URI=wireguard://cHJpdmtleQ==@192.0.2.5:51820"
            "?publickey=cHVia2V5&address=10.66.66.2/24#client1\n"
        ),
        stderr="",
        duration_sec=60.0,
        connect_latency_ms=10,
    )


def _patch_streaming(canned: DeployResult):
    async def fake(*args, **kwargs):
        on_line = kwargs.get("on_line")
        if on_line is not None:
            await on_line("stdout", "[fake] starting")
            await on_line("stdout", "[fake] done")
        return canned

    return patch("app.core.ssh.exec_remote_script_streaming", new=fake)


async def _wait_for_job(client, auth_headers, job_id: str, timeout: float = 5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = client.get(f"/api/server-tasks/{job_id}", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            if data["status"] in ("succeeded", "failed"):
                return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


@pytest.mark.asyncio
async def test_deploy_wireguard_with_outbound_socks_persists_state(
    client, session, admin_user, auth_headers, reset_job_manager
):
    srv = Server(
        name="vps", host="192.0.2.5", port=22,
        user="root", auth_type="password", password="pw",
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)

    with _patch_streaming(_wg_success_result()):
        resp = client.post(
            f"/api/servers/{srv.id}/deploy",
            json={
                "protocol": "wireguard",
                "config": {
                    "client_name": "client1",
                    "outbound_socks": {
                        "host": "127.0.0.1",
                        "port": 1081,
                        "username": "u",
                        "password": "p",
                        "udp": True,
                    },
                },
            },
            headers=auth_headers,
        )
    assert resp.status_code == 202, resp.text
    result = await _wait_for_job(client, auth_headers, resp.json()["job_id"])
    assert result["status"] == "succeeded", result

    state = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    ).json()
    assert state["enabled"] is True
    assert state["socks"]["host"] == "127.0.0.1"
    assert state["socks"]["port"] == 1081
    assert state["socks"]["username"] == "u"
    assert state["socks"]["password"] == "p"
    assert state["socks"]["udp"] is True


@pytest.mark.asyncio
async def test_deploy_wireguard_without_outbound_socks_reports_disabled(
    client, session, admin_user, auth_headers, reset_job_manager
):
    srv = Server(
        name="vps", host="192.0.2.5", port=22,
        user="root", auth_type="password", password="pw",
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)

    with _patch_streaming(_wg_success_result()):
        resp = client.post(
            f"/api/servers/{srv.id}/deploy",
            json={"protocol": "wireguard", "config": {"client_name": "client1"}},
            headers=auth_headers,
        )
    assert resp.status_code == 202, resp.text
    result = await _wait_for_job(client, auth_headers, resp.json()["job_id"])
    assert result["status"] == "succeeded", result

    state = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    ).json()
    assert state["enabled"] is False


@pytest.mark.asyncio
async def test_redeploy_wireguard_clears_stale_outbound_socks_state(
    client, session, admin_user, auth_headers, reset_job_manager
):
    srv = Server(
        name="vps", host="192.0.2.5", port=22,
        user="root", auth_type="password", password="pw",
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)

    with _patch_streaming(_wg_success_result()):
        resp = client.post(
            f"/api/servers/{srv.id}/deploy",
            json={
                "protocol": "wireguard",
                "config": {
                    "client_name": "client1",
                    "outbound_socks": {"host": "127.0.0.1", "port": 1080},
                },
            },
            headers=auth_headers,
        )
        job_id = resp.json()["job_id"]
    assert resp.status_code == 202, resp.text
    result = await _wait_for_job(client, auth_headers, job_id)
    assert result["status"] == "succeeded", result

    with _patch_streaming(_wg_success_result()):
        resp = client.post(
            f"/api/servers/{srv.id}/deploy",
            json={"protocol": "wireguard", "config": {"client_name": "client2"}},
            headers=auth_headers,
        )
    assert resp.status_code == 202, resp.text
    result = await _wait_for_job(client, auth_headers, resp.json()["job_id"])
    assert result["status"] == "succeeded", result

    state = client.get(
        f"/api/servers/{srv.id}/deployments/wireguard/outbound-socks",
        headers=auth_headers,
    ).json()
    assert state["enabled"] is False
