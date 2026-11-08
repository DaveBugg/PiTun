"""A circle can follow several subscriptions at once."""
import asyncio
import json
from unittest import mock
from unittest.mock import AsyncMock

from sqlmodel import select

from app.api.subscriptions import _fetch_subscription_unlocked
from app.models import Node, NodeCircle, Subscription

PANELS = {
    "A": "vless://a@192.0.2.1:443?type=tcp#a-one\nvless://b@192.0.2.2:443?type=tcp#a-two\n",
    "B": "vless://c@192.0.2.3:443?type=tcp#b-one\n",
}


def _refresh(sub_id, body):
    with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
        inst = mock_client.return_value.__aenter__.return_value
        inst.get = AsyncMock(return_value=mock.Mock(
            status_code=200, text=body, raise_for_status=lambda: None))
        asyncio.run(_fetch_subscription_unlocked(sub_id))


def _two_subs(session):
    subs = []
    for name in ("A", "B"):
        s = Subscription(name=name, url=f"http://example/{name}", enabled=True)
        session.add(s)
        session.commit()
        session.refresh(s)
        subs.append(s.id)
    return subs


def _members(session, circle_id):
    session.expire_all()
    ids = json.loads(session.get(NodeCircle, circle_id).node_ids)
    return sorted(n.split(" ", 1)[-1] for n in session.exec(select(Node.name).where(Node.id.in_(ids))).all())


def test_circle_follows_both_subscriptions(client, admin_user, auth_headers, session):
    a, b = _two_subs(session)
    r = client.post("/api/nodecircle", headers=auth_headers, json={
        "name": "c", "node_ids": [], "mode": "sequential", "interval_min": 5,
        "interval_max": 10, "subscription_ids": [a, b],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["subscription_ids"] == [a, b] and body["subscription_id"] == a
    cid = body["id"]

    _refresh(a, PANELS["A"])
    _refresh(b, PANELS["B"])
    assert _members(session, cid) == ["a-one", "a-two", "b-one"]


def test_legacy_single_id_is_reported_as_a_list(client, admin_user, auth_headers, session):
    a, _ = _two_subs(session)
    c = NodeCircle(name="old", node_ids="[]", mode="sequential", interval_min=5,
                   interval_max=10, subscription_id=a)
    session.add(c)
    session.commit()
    session.refresh(c)
    r = client.get(f"/api/nodecircle/{c.id}", headers=auth_headers)
    assert r.json()["subscription_ids"] == [a]


def test_patch_can_shrink_to_one_and_unlink(client, admin_user, auth_headers, session):
    a, b = _two_subs(session)
    cid = client.post("/api/nodecircle", headers=auth_headers, json={
        "name": "c", "node_ids": [], "mode": "sequential", "interval_min": 5,
        "interval_max": 10, "subscription_ids": [a, b]}).json()["id"]
    r = client.patch(f"/api/nodecircle/{cid}", json={"subscription_ids": [b]}, headers=auth_headers)
    assert r.json()["subscription_ids"] == [b] and r.json()["subscription_id"] == b
    r = client.patch(f"/api/nodecircle/{cid}", json={"subscription_ids": []}, headers=auth_headers)
    assert r.json()["subscription_ids"] == [] and r.json()["subscription_id"] is None
