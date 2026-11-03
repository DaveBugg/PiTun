"""A subscription fetch validates the certificate unless the subscription
opts out with `allow_insecure_tls`."""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from app.api.subscriptions import _fetch_subscription_unlocked
from app.models import Subscription

URI = "vless://aaa@203.0.113.7:443?type=tcp&security=reality#n1"


def _fetch_and_capture_verify(session, **sub_kwargs):
    sub = Subscription(name="T", url="https://panel.example.test/sub", enabled=True, **sub_kwargs)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
        inst = mock_client.return_value.__aenter__.return_value
        inst.get = AsyncMock(return_value=mock.Mock(
            status_code=200, text=URI, raise_for_status=lambda: None,
        ))
        asyncio.run(_fetch_subscription_unlocked(sub.id))
    return mock_client.call_args.kwargs["verify"]


def test_certificate_is_verified_by_default(client, admin_user, session):
    assert _fetch_and_capture_verify(session) is True


def test_opt_out_disables_verification(client, admin_user, session):
    assert _fetch_and_capture_verify(session, allow_insecure_tls=True) is False


def test_new_subscription_via_api_defaults_to_verified(client, admin_user, auth_headers):
    r = client.post(
        "/api/subscriptions",
        json={"name": "X", "url": "https://example.com/sub"},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["allow_insecure_tls"] is False


def test_checkbox_round_trips_through_update(client, admin_user, auth_headers):
    r = client.post(
        "/api/subscriptions",
        json={"name": "X", "url": "https://example.com/sub"},
        headers=auth_headers,
    )
    sub_id = r.json()["id"]
    r = client.patch(f"/api/subscriptions/{sub_id}", json={"allow_insecure_tls": True}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["allow_insecure_tls"] is True
