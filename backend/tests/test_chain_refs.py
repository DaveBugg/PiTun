"""Dangling `chain_node_id` — prevention and visibility.

`chain_node_id` carries no FK (a self-FK is painful on SQLite), so a
pointer at a deleted node survives happily. The chained node then stops
working with no visible cause: config_gen skips the relay, probes follow
the dead pointer, speed tests come back empty. Manual deletion always
cleared these; the subscription paths did not, which is how it bit in the
wild — and nothing in the node list said why.
"""
from unittest import mock

from sqlmodel import select

from app.models import Node, Subscription


def _node(session, name, *, sub_id=None, chain_to=None):
    n = Node(
        name=name, protocol="vless", address="1.2.3.4", port=443,
        uuid="u", transport="tcp", tls="none", enabled=True,
        subscription_id=sub_id, chain_node_id=chain_to,
    )
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _sub(session):
    s = Subscription(name="s", url="https://example.com/sub")
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _not_running():
    return mock.patch(
        "app.core.xray.XrayManager.is_running",
        new_callable=mock.PropertyMock, return_value=False,
    )


class TestOrphanVisibility:
    def test_list_flags_a_node_whose_relay_is_gone(
        self, client, session, admin_user, auth_headers,
    ):
        relay = _node(session, "relay")
        chained = _node(session, "chained", chain_to=relay.id)
        # Break the link the way a pre-fix delete would have.
        session.delete(relay)
        session.commit()

        rows = client.get("/api/nodes", headers=auth_headers).json()
        row = next(r for r in rows if r["id"] == chained.id)
        assert row["chain_orphan"] is True
        assert row["chain_node_id"] == relay.id  # link kept, so it can be fixed

    def test_healthy_chain_is_not_flagged(
        self, client, session, admin_user, auth_headers,
    ):
        relay = _node(session, "relay")
        chained = _node(session, "chained", chain_to=relay.id)

        rows = client.get("/api/nodes", headers=auth_headers).json()
        by_id = {r["id"]: r for r in rows}
        assert by_id[chained.id]["chain_orphan"] is False
        assert by_id[relay.id]["chain_orphan"] is False

    def test_paginated_list_flags_it_too(
        self, client, session, admin_user, auth_headers,
    ):
        relay = _node(session, "relay")
        chained = _node(session, "chained", chain_to=relay.id)
        session.delete(relay)
        session.commit()

        page = client.get("/api/nodes/page?limit=50", headers=auth_headers).json()
        row = next(r for r in page["items"] if r["id"] == chained.id)
        assert row["chain_orphan"] is True

    def test_single_node_read_flags_it_too(
        self, client, session, admin_user, auth_headers,
    ):
        relay = _node(session, "relay")
        chained = _node(session, "chained", chain_to=relay.id)
        session.delete(relay)
        session.commit()

        row = client.get(f"/api/nodes/{chained.id}", headers=auth_headers).json()
        assert row["chain_orphan"] is True


class TestSubscriptionDeleteClearsChainRefs:
    def test_deleting_a_subscription_unchains_outside_nodes(
        self, client, session, admin_user, auth_headers,
    ):
        sub = _sub(session)
        relay = _node(session, "relay", sub_id=sub.id)
        standalone = _node(session, "standalone", chain_to=relay.id)

        with _not_running():
            resp = client.delete(
                f"/api/subscriptions/{sub.id}?delete_nodes=true", headers=auth_headers,
            )
        assert resp.status_code == 204

        session.expire_all()
        assert session.get(Node, standalone.id).chain_node_id is None

    def test_keeping_nodes_leaves_the_chain_intact(
        self, client, session, admin_user, auth_headers,
    ):
        sub = _sub(session)
        relay = _node(session, "relay", sub_id=sub.id)
        standalone = _node(session, "standalone", chain_to=relay.id)

        with _not_running():
            client.delete(
                f"/api/subscriptions/{sub.id}?delete_nodes=false", headers=auth_headers,
            )
        session.expire_all()
        assert session.get(Node, standalone.id).chain_node_id == relay.id

    def test_an_event_names_the_unchained_nodes(
        self, client, session, admin_user, auth_headers,
    ):
        from app.models import Event

        sub = _sub(session)
        relay = _node(session, "relay", sub_id=sub.id)
        standalone = _node(session, "standalone", chain_to=relay.id)

        with _not_running():
            client.delete(
                f"/api/subscriptions/{sub.id}?delete_nodes=true", headers=auth_headers,
            )

        session.expire_all()
        events = session.exec(
            select(Event).where(Event.category == "node.unchained")
        ).all()
        assert events, "silently unchaining a node is the bug, not the fix"
        assert str(standalone.id) in (events[0].details or "")


class TestRefreshClearsChainRefs:
    """A node that vanished from the panel may have been someone's relay."""

    def _refresh(self, sub_id, uri):
        import asyncio

        from app.api.subscriptions import _fetch_subscription_unlocked

        class _Resp:
            status_code = 200
            text = uri

            def raise_for_status(self):
                return None

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _Resp()

        with mock.patch("httpx.AsyncClient", _Client):
            asyncio.run(_fetch_subscription_unlocked(sub_id))

    def test_vanished_relay_unchains_its_dependant(self, client, session):
        sub = _sub(session)
        # Seeded to match what the parser produces for the URI below.
        relay = Node(
            name="relay", protocol="vless", address="9.9.9.9", port=443,
            uuid="relay-uuid", transport="tcp", tls="none",
            subscription_id=sub.id, enabled=True,
        )
        session.add(relay)
        session.commit()
        session.refresh(relay)
        chained = _node(session, "chained", chain_to=relay.id)
        sub_id, chained_id = sub.id, chained.id
        session.close()

        # The refresh returns a DIFFERENT node — the relay is gone.
        with _not_running():
            self._refresh(
                sub_id,
                "vless://other-uuid@5.5.5.5:443?type=tcp&security=none#other",
            )

        from sqlmodel import Session as SyncSession
        from app.database import get_sync_engine

        session.expire_all()
        with SyncSession(session.get_bind()) as s:
            assert s.get(Node, chained_id).chain_node_id is None
