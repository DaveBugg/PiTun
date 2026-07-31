"""Guards against half-applied state when two writers race.

Three independent paths could each clobber the others: overlapping config
writers truncating one file in place, a failover overwriting a manual node
switch made while it was still probing, and a balancer pin fired before
xray's gRPC API had come up after a reload.
"""
import asyncio
import json
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from app.core import config_gen


class TestWriteConfigAtomicity:
    def test_concurrent_writes_never_leave_torn_json(self, tmp_path):
        """Two overlapping writers used to truncate the same file in
        place, so a reader (or `xray run -test`) could see half a doc."""
        target = tmp_path / "config.json"
        big_a = {"outbounds": [{"tag": f"a-{i}"} for i in range(2000)]}
        big_b = {"outbounds": [{"tag": f"b-{i}"} for i in range(2000)]}

        async def run():
            await asyncio.gather(*[
                config_gen.write_config(cfg, validate=False)
                for cfg in (big_a, big_b, big_a, big_b)
            ])

        with mock.patch.object(
            config_gen.settings, "xray_config_path", str(target),
        ):
            asyncio.run(run())

        # Parses at all → not torn; and it's exactly one of the inputs.
        parsed = json.loads(target.read_text())
        assert parsed in (big_a, big_b)
        # No temp file left behind.
        assert list(tmp_path.glob("*.tmp")) == []

    def test_write_then_validate_is_serialized(self, tmp_path):
        """The lock must span validation too — `xray run -test` re-reads
        the path, so a competing writer could swap the file underneath
        it and get its error persisted against the wrong config."""
        target = tmp_path / "config.json"
        order: list[str] = []

        async def fake_validate(*args, **kwargs):
            order.append("validate-start")
            await asyncio.sleep(0.05)
            order.append("validate-end")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        async def run():
            await asyncio.gather(
                config_gen.write_config({"a": 1}),
                config_gen.write_config({"b": 2}),
            )

        with (
            mock.patch.object(config_gen.settings, "xray_config_path", str(target)),
            mock.patch.object(
                config_gen.settings, "xray_binary", str(tmp_path / "xray"),
            ),
            mock.patch.object(Path, "exists", lambda self: True),
            mock.patch("asyncio.create_subprocess_exec", side_effect=fake_validate),
            mock.patch.object(
                config_gen, "_persist_validation_error", new_callable=AsyncMock,
            ),
        ):
            asyncio.run(run())

        # Never interleaved: each validate finishes before the next starts.
        assert order == [
            "validate-start", "validate-end", "validate-start", "validate-end",
        ]


class TestFailoverRespectsManualSwitch:
    """Probing candidates takes seconds. An operator who notices the
    outage first and switches by hand must not be silently overridden."""

    def _checker(self):
        from app.core.healthcheck import HealthChecker

        hc = HealthChecker()
        hc._fail_counts = {}
        return hc

    def test_aborts_when_active_node_changed_mid_probe(self, client, session):
        from app.models import Node, Settings as DBSettings

        for name in ("failed", "candidate", "manual-pick"):
            session.add(Node(
                name=name, protocol="vless", address="1.2.3.4", port=443,
                uuid="u", transport="tcp", tls="none", enabled=True,
            ))
        session.commit()
        failed_id, cand_id, manual_id = [
            n.id for n in session.query(Node).order_by(Node.id).all()
        ]
        # The operator already switched to `manual-pick` while we probed.
        session.add(DBSettings(key="active_node_id", value=str(manual_id)))
        session.add(DBSettings(key="failover_enabled", value="true"))
        session.add(DBSettings(key="failover_node_ids", value=json.dumps([cand_id])))
        session.commit()
        bind = session.get_bind()
        session.close()

        hc = self._checker()
        with (
            mock.patch.object(
                hc, "_probe_node", new_callable=AsyncMock,
                return_value={"is_online": True, "latency_ms": 10},
            ),
            mock.patch.object(hc, "_reload_xray", new_callable=AsyncMock) as reload_mock,
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(hc._failover(failed_id))
            finally:
                loop.close()

        from sqlmodel import Session as SyncSession, select as sync_select

        with SyncSession(bind) as s:
            row = s.exec(
                sync_select(DBSettings).where(DBSettings.key == "active_node_id")
            ).first()
            assert row.value == str(manual_id), (
                "failover overwrote the operator's manual choice"
            )
        reload_mock.assert_not_awaited()


class TestPinCircleBalancerRetry:
    """`reload()` returns as soon as the process spawns; xray needs a
    moment more before its gRPC API answers. A single probe almost always
    missed, leaving the circle balancer on its cold-start `random`."""

    def _seed_circle(self, session):
        from app.models import Node, NodeCircle

        for name in ("a", "b"):
            session.add(Node(
                name=name, protocol="vless", address="1.2.3.4", port=443,
                uuid="u", transport="tcp", tls="none", enabled=True,
            ))
        session.commit()
        ids = [n.id for n in session.query(Node).order_by(Node.id).all()]
        session.add(NodeCircle(
            name="c", node_ids=json.dumps(ids), enabled=True,
            mode="interval", interval_min=10, interval_max=20,
        ))
        session.commit()
        return ids

    def _run_pin(self, node_id):
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.api.system import _pin_circle_balancer
        from app.database import get_async_engine

        async def run():
            async with AsyncSession(get_async_engine()) as s:
                await _pin_circle_balancer(s, node_id)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    def test_retries_until_api_comes_up(self, client, session):
        ids = self._seed_circle(session)
        session.close()

        calls = {"n": 0}

        async def api_available():
            calls["n"] += 1
            return calls["n"] >= 3  # up on the third probe

        with (
            mock.patch("app.core.xray_api.is_api_available", side_effect=api_available),
            mock.patch(
                "app.core.xray_api.override_balancer", new_callable=AsyncMock,
            ) as override,
            mock.patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            self._run_pin(ids[0])

        override.assert_awaited_once()
        args = override.await_args.args
        assert args[1] == [f"node-{ids[0]}"]

    def test_gives_up_with_a_warning_not_silence(self, client, session, caplog):
        ids = self._seed_circle(session)
        session.close()

        with (
            mock.patch(
                "app.core.xray_api.is_api_available", new_callable=AsyncMock,
                return_value=False,
            ),
            mock.patch(
                "app.core.xray_api.override_balancer", new_callable=AsyncMock,
            ) as override,
            mock.patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            with caplog.at_level("WARNING"):
                self._run_pin(ids[0])

        override.assert_not_awaited()
        assert any("not pinned" in r.message for r in caplog.records), (
            "a silent debug line hid this failure for a whole release"
        )
