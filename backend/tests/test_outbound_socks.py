"""Outbound-SOCKS5 mode for WireGuard servers.

Covers the deploy env mapping (install-time), the retrofit endpoint
(`/servers/{id}/deployments/wireguard/outbound-socks`), and the
structural contract of the setup script's new sub-commands.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.deploy import build_plan, build_wireguard_env
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
    srv = Server(name="vps", host="1.2.3.4", port=22, user="root", auth_type="key")
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
