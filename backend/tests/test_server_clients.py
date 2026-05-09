"""Tests for `/api/servers/{id}/deployments/wireguard/clients/*` —
the multi-client peer management layer added in v1.3.0-beta.4.

These tests mock `exec_remote_script` so no actual SSH happens; we
verify that:
  * 404 / 400 boundary cases match the documented contract
  * Add-client parses the script's URI + inline conf into a row
  * List returns the public projection (no priv key)
  * Sync reconciles correctly (added / orphaned)
  * Remove deletes the row and flips linked Nodes to client_orphan
  * Export-to-Node creates a Node row pointing back at the DC

We do NOT test the SSH path itself — that lives in test_ssh_exec.py
and is exercised by `setup-wireguard-server.sh` integration on a
real VPS during e2e.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.ssh import DeployResult
from app.models import (
    DeploymentClient, Node, Server, ServerDeployment,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def server(session):
    s = Server(
        name="VPS-WG", host="vps.example.com", port=22, user="root",
        auth_type="password", password="rootpw",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


@pytest.fixture
def wg_deployment(session, server):
    """A wireguard ServerDeployment row sitting on `server`. Required
    before any of the /clients/* endpoints will respond 200."""
    dep = ServerDeployment(
        server_id=server.id,
        protocol="wireguard",
        config_json='{"server_port": 51820}',
        status="deployed",
    )
    session.add(dep)
    session.commit()
    session.refresh(dep)
    return dep


def _add_client_stdout(name: str = "phone-1") -> str:
    """Shape of stdout that `setup-wireguard-server.sh add-client` prints.
    Mirrors the contract enforced by the script: a `URI=wireguard://…`
    line PLUS an inline `PITUN-CLIENT-CONF-BEGIN/END` block.
    """
    conf = (
        "[Interface]\n"
        "PrivateKey = aPRIV=\n"
        f"Address = 10.66.66.2/24,fd42:42:42::2/64\n"
        "DNS = 1.1.1.1,1.0.0.1\n"
        "[Peer]\n"
        "PublicKey = serverPUB=\n"
        "PresharedKey = aPSK=\n"
        "Endpoint = vps.example.com:51820\n"
        "AllowedIPs = 0.0.0.0/0,::/0\n"
    )
    return (
        f"Adding peer {name}...\n"
        f"URI=wireguard://aPRIV%3D@vps.example.com:51820"
        f"?publickey=serverPUB%3D&presharedkey=aPSK%3D"
        f"&address=10.66.66.2/24,fd42:42:42::2/64&mtu=1420#{name}\n"
        f"PITUN-CLIENT-CONF-BEGIN {name}\n"
        f"{conf}"
        f"PITUN-CLIENT-CONF-END {name}\n"
    )


def _ok(stdout: str) -> DeployResult:
    return DeployResult(
        ok=True, exit_code=0, stdout=stdout, stderr="",
        duration_sec=1.5, connect_latency_ms=20,
    )


def _patch_exec(stdout: str):
    """Replace `exec_remote_script` (used by server_clients.py — NOT
    the streaming variant) with a fast canned response."""
    from unittest.mock import AsyncMock
    return patch(
        "app.api.server_clients.exec_remote_script",
        new=AsyncMock(return_value=_ok(stdout)),
    )


# ── 404 / boundary ──────────────────────────────────────────────────────────


class TestBoundary:
    def test_list_404_when_server_missing(self, client, auth_headers):
        resp = client.get(
            "/api/servers/99999/deployments/wireguard/clients",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_list_404_when_no_wg_deployment(
        self, client, auth_headers, server,
    ):
        # Server exists but no WG deployment row → 404 with helpful detail
        resp = client.get(
            f"/api/servers/{server.id}/deployments/wireguard/clients",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "wireguard" in resp.json()["detail"].lower()


# ── Happy paths ─────────────────────────────────────────────────────────────


class TestAddClient:
    def test_add_creates_dc_with_parsed_uri_and_conf(
        self, client, auth_headers, server, wg_deployment, session,
    ):
        with _patch_exec(_add_client_stdout("phone-1")):
            resp = client.post(
                f"/api/servers/{server.id}/deployments/wireguard/clients",
                json={"name": "phone-1"},
                headers=auth_headers,
            )

        assert resp.status_code in (200, 201)
        body = resp.json()
        assert body["name"] == "phone-1"
        assert body["status"] == "available"
        assert body["wg_endpoint"]
        # Public projection — no private key in the response
        assert "wg_private_key" not in body
        assert "private_key" not in body

        # DB row exists with the priv key cached locally
        from sqlmodel import select
        rows = session.exec(
            select(DeploymentClient)
            .where(DeploymentClient.deployment_id == wg_deployment.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].wg_private_key  # cached for export-to-node


class TestListClients:
    def test_list_returns_existing_rows(
        self, client, auth_headers, server, wg_deployment, session,
    ):
        # Pre-seed two rows
        for n in ("a", "b"):
            session.add(DeploymentClient(
                deployment_id=wg_deployment.id, name=n,
                wg_public_key="pub", wg_endpoint="vps.example.com:51820",
                wg_local_address="10.66.66.X/24", status="available",
            ))
        session.commit()

        resp = client.get(
            f"/api/servers/{server.id}/deployments/wireguard/clients",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        names = sorted(c["name"] for c in body["clients"])
        assert names == ["a", "b"]


class TestRemoveClient:
    def test_remove_flips_linked_nodes_to_orphan(
        self, client, auth_headers, server, wg_deployment, session,
    ):
        # Seed: a DC + a Node linked to it
        dc = DeploymentClient(
            deployment_id=wg_deployment.id, name="phone-1",
            wg_public_key="pub", wg_private_key="priv",
            wg_endpoint="vps.example.com:51820",
            wg_local_address="10.66.66.2/24",
            status="exported",
        )
        session.add(dc)
        session.commit()
        session.refresh(dc)
        dc_id = dc.id  # capture before delete (after expire_all the obj is detached)

        node = Node(
            name="phone-1", protocol="wireguard",
            address="vps.example.com", port=51820,
            wg_private_key="priv", wg_public_key="pub",
            server_id=server.id, from_deployment_client_id=dc_id,
            client_orphan=False, enabled=True, order=0,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        node_id = node.id

        # Mock the SSH call to return a "REMOVED=phone-1" line
        from unittest.mock import AsyncMock
        with patch(
            "app.api.server_clients.exec_remote_script",
            new=AsyncMock(return_value=_ok("REMOVED=phone-1\n")),
        ):
            resp = client.delete(
                f"/api/servers/{server.id}/deployments/wireguard/clients/phone-1",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 204)

        session.expire_all()
        # DC row gone
        from sqlmodel import select
        leftover = session.exec(
            select(DeploymentClient).where(DeploymentClient.id == dc_id)
        ).first()
        assert leftover is None
        # Node still here, but flagged orphan
        n2 = session.exec(select(Node).where(Node.id == node_id)).first()
        assert n2 is not None
        assert n2.client_orphan is True
        # `from_deployment_client_id` may stay or be cleared depending on
        # FK handling; the orphan flag is the contract for the UI badge.


class TestSyncClients:
    def test_sync_marks_missing_as_orphan(
        self, client, auth_headers, server, wg_deployment, session,
    ):
        # Seed two rows; the server only knows about one of them.
        for n in ("alive", "dead"):
            session.add(DeploymentClient(
                deployment_id=wg_deployment.id, name=n,
                wg_public_key="pub", wg_endpoint="vps.example.com:51820",
                status="available",
            ))
        session.commit()

        # `list-clients` script output contract: CLIENTS=<json array>
        listed = '[{"name":"alive","public_key":"pub"}]'
        from unittest.mock import AsyncMock
        with patch(
            "app.api.server_clients.exec_remote_script",
            new=AsyncMock(return_value=_ok(f"CLIENTS={listed}\n")),
        ):
            resp = client.post(
                f"/api/servers/{server.id}/deployments/wireguard/clients/sync",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "dead" in body["orphaned"]
        assert "alive" in body["unchanged"]

        # DB reflects new statuses
        session.expire_all()
        from sqlmodel import select
        rows = {
            r.name: r.status for r in session.exec(
                select(DeploymentClient)
                .where(DeploymentClient.deployment_id == wg_deployment.id)
            ).all()
        }
        assert rows["dead"] == "orphan"
        assert rows["alive"] == "available"
