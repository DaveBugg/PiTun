"""x-ui client cache bookkeeping and chain status persistence.

`/sync` reconciles PiTun's `xuiclient` cache against what the panel
reports, keyed by the client's NATURAL id (`id` → `password` → `user`).
Storing "" for trojan clients meant the cache row never matched its live
panel counterpart, so the next sync declared the client "vanished" and
cascade-deleted the Node it had been exported to.
"""
from unittest import mock
from unittest.mock import AsyncMock

from app.models import (
    ChainChannel, ProxyChain, Server, XuiClient as XuiClientModel, XuiServer,
)


def _seed_panel(session):
    srv = Server(name="p", host="1.2.3.4", port=22, user="root", auth_type="key")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = XuiServer(
        server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
        panel_port=12345, panel_basepath="/t", mode="bare",
    )
    session.add(xs)
    session.commit()
    session.refresh(xs)
    return xs


class TestAddClientCachesNaturalId:
    def _add(self, client, auth_headers, xs_id, protocol, monkey_client):
        with mock.patch("app.api.xui.XuiClient") as MC:
            MC.return_value.__aenter__.return_value = monkey_client
            return client.post(
                f"/api/xui/servers/{xs_id}/inbounds/7/clients",
                json={"label": "pi-test", "extras": {}},
                headers=auth_headers,
            )

    @staticmethod
    def _panel(protocol):
        inst = mock.MagicMock()
        inst.get_inbound = AsyncMock(return_value={
            "id": 7, "protocol": protocol, "port": 443, "remark": "r",
        })
        inst.get_new_uuid = AsyncMock(return_value="uuid-1")
        inst.add_client = AsyncMock(return_value=None)
        inst.restart_xray = AsyncMock(return_value=None)
        return inst

    def test_vless_caches_uuid(self, client, session, admin_user, auth_headers):
        xs = _seed_panel(session)
        resp = self._add(client, auth_headers, xs.id, "vless", self._panel("vless"))
        assert resp.status_code in (200, 201), resp.text

        session.expire_all()
        row = session.query(XuiClientModel).one()
        assert row.client_uuid == "uuid-1"

    def test_trojan_caches_password_so_sync_can_match_it(
        self, client, session, admin_user, auth_headers,
    ):
        xs = _seed_panel(session)
        panel = self._panel("trojan")
        resp = self._add(client, auth_headers, xs.id, "trojan", panel)
        assert resp.status_code in (200, 201), resp.text

        # Whatever password the panel payload carried must be the cache key —
        # `/sync` derives the same value from `settings.clients[]`.
        sent = panel.add_client.await_args.args[1]
        assert sent.get("password")
        session.expire_all()
        row = session.query(XuiClientModel).one()
        assert row.client_uuid == sent["password"], (
            "empty client_uuid made sync treat a live trojan client as gone "
            "and cascade-delete its exported Node"
        )


class TestChainHealthcheckPersistsStatus:
    """`degraded` was documented on the model and rendered by the UI, but
    no code path ever set it: drift found by a healthcheck vanished as
    soon as the dialog was closed."""

    def _seed_chain(self, session, status="deployed"):
        xs = _seed_panel(session)
        chain = ProxyChain(
            name="c", relay_xui_server_id=xs.id, exit_xui_server_id=xs.id,
            status=status,
        )
        session.add(chain)
        session.commit()
        session.refresh(chain)
        ch = ChainChannel(
            chain_id=chain.id, name="alpha", client_sni="a.example",
            relay_port=443, exit_port=8443,
            relay_inbound_remote_id=1, exit_inbound_remote_id=2,
            relay_pbk="rp", relay_sid="rs", relay_pvk="rv",
            exit_uuid="u", exit_pbk="p", exit_sid="s",
        )
        session.add(ch)
        session.commit()
        return chain

    def _run_healthcheck(self, client, auth_headers, chain_id, *, inbounds):
        inst = mock.MagicMock()
        inst.list_inbounds = AsyncMock(return_value=inbounds)
        inst.get_xray_setting = AsyncMock(return_value={})
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        with (
            mock.patch("app.api.xui.XuiClient", return_value=inst),
            mock.patch("app.api.xui._ssh_creds", return_value=None),
        ):
            return client.post(
                f"/api/xui/chains/{chain_id}/healthcheck", headers=auth_headers,
            )

    def test_missing_inbound_flips_deployed_to_degraded(
        self, client, session, admin_user, auth_headers,
    ):
        chain = self._seed_chain(session)
        # Panel reports no inbounds at all — the chain's are gone.
        resp = self._run_healthcheck(client, auth_headers, chain.id, inbounds=[])
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

        session.expire_all()
        assert session.get(ProxyChain, chain.id).status == "degraded"

    def test_failed_chain_status_is_not_overwritten(
        self, client, session, admin_user, auth_headers,
    ):
        chain = self._seed_chain(session, status="failed")
        self._run_healthcheck(client, auth_headers, chain.id, inbounds=[])
        session.expire_all()
        # A `failed` chain stays failed — healthcheck only moves the
        # deployed ↔ degraded pair.
        assert session.get(ProxyChain, chain.id).status == "failed"


class TestSyncHealsLegacyTrojanRows:
    """Rows written before the natural-id fix hold an EMPTY `client_uuid`
    for trojan clients. They can never match a panel entry keyed by
    password, so sync called a live client "vanished" and cascade-deleted
    the Node it had been exported to — on a real box that Node was the
    ACTIVE one. Such rows must be adopted, not dropped."""

    def _seed(self, session, *, client_uuid, label="pi-legacy"):
        from app.models import Node, XuiClient as XuiClientModel

        xs = _seed_panel(session)
        node = Node(
            name="exported", protocol="trojan", address="1.2.3.4", port=443,
            password="s3cret", transport="tcp", tls="tls", enabled=True,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        row = XuiClientModel(
            xui_server_id=xs.id, inbound_remote_id=3,
            inbound_protocol="trojan", label=label,
            client_uuid=client_uuid, exported_node_id=node.id,
            config_json="{}",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return xs, row, node

    def _sync(self, client, auth_headers, xs_id, panel_clients):
        inst = mock.MagicMock()
        inst.list_inbounds = AsyncMock(return_value=[{
            "id": 3, "protocol": "trojan", "port": 443, "remark": "r",
            "settings": {"clients": panel_clients},
        }])
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        with mock.patch("app.api.xui.XuiClient", return_value=inst):
            return client.post(
                f"/api/xui/servers/{xs_id}/sync", headers=auth_headers,
            )

    def test_legacy_empty_uuid_row_is_adopted_not_deleted(
        self, client, session, admin_user, auth_headers,
    ):
        from app.models import Node, XuiClient as XuiClientModel

        xs, row, node = self._seed(session, client_uuid="")
        resp = self._sync(client, auth_headers, xs.id, [
            {"password": "s3cret", "email": "pi-legacy"},
        ])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["orphan_nodes_removed"] == 0
        assert body["removed"] == 0

        session.expire_all()
        assert session.get(Node, node.id) is not None, (
            "sync deleted a Node whose client is alive on the panel"
        )
        healed = session.get(XuiClientModel, row.id)
        assert healed is not None
        # Adopted the panel's natural id, so the next sync matches directly.
        assert healed.client_uuid == "s3cret"
        assert healed.exported_node_id == node.id

    def test_row_with_a_truly_missing_client_is_still_dropped(
        self, client, session, admin_user, auth_headers,
    ):
        from app.models import Node, XuiClient as XuiClientModel

        xs, row, node = self._seed(session, client_uuid="")
        row_id, node_id = row.id, node.id
        # Panel reports a DIFFERENT client — ours really is gone.
        resp = self._sync(client, auth_headers, xs.id, [
            {"password": "other", "email": "pi-someone-else"},
        ])
        assert resp.status_code == 200, resp.text
        assert resp.json()["orphan_nodes_removed"] == 1

        # Drop the identity map — it still holds the deleted instances.
        session.expunge_all()
        assert session.get(XuiClientModel, row_id) is None
        assert session.get(Node, node_id) is None

    def test_modern_row_still_matches_on_the_fast_path(
        self, client, session, admin_user, auth_headers,
    ):
        from app.models import Node

        xs, _row, node = self._seed(session, client_uuid="s3cret")
        resp = self._sync(client, auth_headers, xs.id, [
            {"password": "s3cret", "email": "pi-legacy"},
        ])
        assert resp.status_code == 200
        assert resp.json()["removed"] == 0
        session.expire_all()
        assert session.get(Node, node.id) is not None
