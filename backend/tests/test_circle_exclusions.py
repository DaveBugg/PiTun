"""A subscription-linked circle remembers what was taken out of it, and never
takes the countries it was told to refuse, across subscription refreshes."""
import asyncio
import json
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.api.subscriptions import _fetch_subscription_unlocked
from app.core import circle_filters as cf
from app.models import Node, NodeCircle, Subscription

RU = "\U0001F1F7\U0001F1FA"   # regional indicators R, U
NL = "\U0001F1F3\U0001F1F1"

PANEL = (
    "vless://a@1.1.1.1:443?type=tcp#ru-one\n"
    "vless://b@2.2.2.2:443?type=tcp#ru-two\n"
    "vless://c@3.3.3.3:443?type=tcp#nl-one\n"
    "vless://d@4.4.4.4:443?type=tcp#nl-two\n"
)


def test_country_comes_from_the_exit_first_then_the_name_flag():
    assert cf.node_country("ru", "whatever") == "RU"
    assert cf.node_country(None, f"{RU} proxy") == "RU"
    assert cf.node_country("", f"{NL} proxy") == "NL"
    assert cf.node_country(None, "proxy") is None
    assert cf.normalize_countries(["ru", " RU", "xx1", "nl", None]) == ["RU", "NL"]


def _refresh(sub_id):
    with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
        inst = mock_client.return_value.__aenter__.return_value
        inst.get = AsyncMock(return_value=mock.Mock(
            status_code=200, text=PANEL, raise_for_status=lambda: None))
        asyncio.run(_fetch_subscription_unlocked(sub_id))


@pytest.fixture
def linked(session):
    sub = Subscription(name="S", url="http://example/sub", enabled=True)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    circle = NodeCircle(name="c", node_ids="[]", mode="sequential", interval_min=5,
                        interval_max=10, enabled=False, subscription_id=sub.id)
    session.add(circle)
    session.commit()
    session.refresh(circle)
    return sub.id, circle.id


def _names(session, circle_id):
    session.expire_all()
    ids = json.loads(session.get(NodeCircle, circle_id).node_ids)
    names = session.exec(select(Node.name).where(Node.id.in_(ids))).all()
    # Imported names carry a flag prefix ("<flag> name"); compare the bare name.
    return sorted(cf._flag_code(n) and n.split(" ", 1)[1] or n for n in names)


def test_unticked_nodes_stay_out_after_the_next_refresh(client, admin_user, auth_headers, session, linked):
    sub_id, circle_id = linked
    _refresh(sub_id)
    session.expire_all()
    assert len(_names(session, circle_id)) == 4          # a linked circle takes everything first

    nodes = {n.name: n.id for n in session.exec(select(Node)).all()}
    keep = [nodes["nl-one"], nodes["nl-two"]]
    r = client.patch(f"/api/nodecircle/{circle_id}", json={"node_ids": keep}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["excluded_node_ids"]) == sorted([nodes["ru-one"], nodes["ru-two"]])

    _refresh(sub_id)
    assert _names(session, circle_id) == ["nl-one", "nl-two"]


def test_ticking_a_node_again_forgets_the_exclusion(client, admin_user, auth_headers, session, linked):
    sub_id, circle_id = linked
    _refresh(sub_id)
    nodes = {n.name: n.id for n in session.exec(select(Node)).all()}
    client.patch(f"/api/nodecircle/{circle_id}", json={"node_ids": [nodes["nl-one"]]}, headers=auth_headers)
    r = client.patch(f"/api/nodecircle/{circle_id}",
                     json={"node_ids": [nodes["nl-one"], nodes["ru-one"]]}, headers=auth_headers)
    assert nodes["ru-one"] not in r.json()["excluded_node_ids"]
    _refresh(sub_id)
    assert "ru-one" in _names(session, circle_id)


def test_an_excluded_country_never_joins_and_leaves_at_once(client, admin_user, auth_headers, session, linked):
    sub_id, circle_id = linked
    _refresh(sub_id)
    # Give the nodes a country the way a speed test would (RU for two of them).
    for n in session.exec(select(Node)).all():
        n.country = "RU" if n.name.startswith("ru") else "NL"
        session.add(n)
    session.commit()

    r = client.patch(f"/api/nodecircle/{circle_id}", json={"excluded_countries": ["ru"]}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["excluded_countries"] == ["RU"]
    assert _names(session, circle_id) == ["nl-one", "nl-two"]      # removed right away

    _refresh(sub_id)
    assert _names(session, circle_id) == ["nl-one", "nl-two"]      # and not put back


def test_a_manual_member_from_an_excluded_country_is_left_alone(client, admin_user, auth_headers, session, linked):
    sub_id, circle_id = linked
    manual = Node(name=f"{RU} mine", protocol="vless", address="9.9.9.9", port=443,
                  uuid="m", transport="tcp", enabled=True, country="RU")
    session.add(manual)
    session.commit()
    session.refresh(manual)
    mid = manual.id
    c = session.get(NodeCircle, circle_id)
    c.node_ids = json.dumps([mid])
    c.excluded_countries = json.dumps(["RU"])
    session.add(c)
    session.commit()
    _refresh(sub_id)
    session.expire_all()
    assert mid in json.loads(session.get(NodeCircle, circle_id).node_ids)


def test_create_accepts_and_returns_the_exclusions(client, admin_user, auth_headers):
    r = client.post("/api/nodecircle", json={"name": "x", "excluded_countries": ["ru", "by"],
                                             "excluded_node_ids": [1, 2]}, headers=auth_headers)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["excluded_countries"] == ["RU", "BY"] and d["excluded_node_ids"] == [1, 2]
