"""Hourly chain re-check: flags a chain that drifted, honours the off switch."""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock

from app.core import chain_drift
from app.models import ProxyChain, Server, Settings as DBSettings, XuiServer


def _seed(session, status="deployed"):
    s = session
    if True:
        srv = Server(name="relay-srv", host="203.0.113.5", port=22, user="root", auth_type="key")
        s.add(srv)
        s.commit()
        s.refresh(srv)
        xs = XuiServer(server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
                       panel_port=12345, panel_basepath="/t", mode="bare")
        s.add(xs)
        s.commit()
        s.refresh(xs)
        chain = ProxyChain(name="c1", relay_xui_server_id=xs.id, exit_xui_server_id=xs.id, status=status)
        s.add(chain)
        s.commit()
        s.refresh(chain)
        return chain.id


def _status(session, chain_id):
    session.expire_all()
    return session.get(ProxyChain, chain_id).status


def _fake_check(new_status):
    async def check(chain_id, session):
        chain = await session.get(ProxyChain, chain_id)
        chain.status = new_status
        session.add(chain)
        await session.commit()
    return check


def test_deployed_chain_that_fails_is_degraded_and_an_event_is_raised(client, session):
    cid = _seed(session)
    with mock.patch("app.api.xui.healthcheck_chain", side_effect=_fake_check("degraded")), \
         mock.patch("app.core.events.record_event", new_callable=AsyncMock) as ev:
        checked = asyncio.run(chain_drift.ChainDriftScheduler().run_once())
    assert checked == 1
    assert _status(session, cid) == "degraded"
    assert ev.await_args.kwargs["category"] == "chain.degraded"


def test_healthy_chain_raises_no_event(client, session):
    cid = _seed(session)
    with mock.patch("app.api.xui.healthcheck_chain", side_effect=_fake_check("deployed")), \
         mock.patch("app.core.events.record_event", new_callable=AsyncMock) as ev:
        asyncio.run(chain_drift.ChainDriftScheduler().run_once())
    assert _status(session, cid) == "deployed"
    ev.assert_not_awaited()


def test_already_degraded_chain_does_not_re_alert(client, session):
    _seed(session, status="degraded")
    with mock.patch("app.api.xui.healthcheck_chain", side_effect=_fake_check("degraded")), \
         mock.patch("app.core.events.record_event", new_callable=AsyncMock) as ev:
        asyncio.run(chain_drift.ChainDriftScheduler().run_once())
    ev.assert_not_awaited()


def test_switch_off_skips_everything(client, session):
    _seed(session)
    session.add(DBSettings(key="chain_drift_check", value="false"))
    session.commit()
    with mock.patch("app.api.xui.healthcheck_chain", side_effect=_fake_check("degraded")) as hc:
        assert asyncio.run(chain_drift.ChainDriftScheduler().run_once()) == 0
    hc.assert_not_called()


def test_one_unreachable_chain_does_not_stop_the_pass(client, session):
    _seed(session)

    async def boom(chain_id, session):
        raise RuntimeError("panel unreachable")

    with mock.patch("app.api.xui.healthcheck_chain", side_effect=boom), \
         mock.patch("app.core.error_log.record") as rec:
        assert asyncio.run(chain_drift.ChainDriftScheduler().run_once()) == 0
    rec.assert_called_once()
