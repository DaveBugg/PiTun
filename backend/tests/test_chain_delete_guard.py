"""Deleting a chain's inbound by stored id must not remove someone else's
inbound: panel reinstalls re-issue low ids."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.core.xui_api import XuiAPIError
from app.core.xui_chain import _del_inbound_if_ours


def _client(current=None, error=None):
    c = MagicMock()
    c.get_inbound = AsyncMock(return_value=current, side_effect=error)
    c.del_inbound = AsyncMock(return_value=None)
    return c


def _run(c, remark="VPN-NAME", port=8443):
    asyncio.run(_del_inbound_if_ours(c, 7, remark, port, "test"))


def test_deletes_when_remark_and_port_match():
    c = _client({"remark": "VPN-NAME", "port": 8443})
    _run(c)
    c.del_inbound.assert_awaited_once_with(7)


def test_deletes_when_only_the_remark_survived_a_port_edit():
    c = _client({"remark": "VPN-NAME", "port": 9999})
    _run(c)
    c.del_inbound.assert_awaited_once_with(7)


def test_deletes_when_only_the_port_matches_after_a_rename():
    c = _client({"remark": "renamed by hand", "port": 8443})
    _run(c)
    c.del_inbound.assert_awaited_once_with(7)


def test_leaves_a_foreign_inbound_that_reused_the_id():
    c = _client({"remark": "someone-elses", "port": 2053})
    _run(c)
    c.del_inbound.assert_not_awaited()


def test_missing_or_unreadable_inbound_is_not_deleted():
    c = _client(error=XuiAPIError("not found"))
    _run(c)
    c.del_inbound.assert_not_awaited()
