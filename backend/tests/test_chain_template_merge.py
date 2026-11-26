"""Cross-box safety of the relay xray template push.

The relay's xrayTemplateConfig is panel-wide: two PiTun installs can
share one VPS panel. The push must replace only the entries owned by
the pushing install and carry everything else over untouched.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.xui_chain import (
    _template_has_foreign_entries,
    merge_relay_template,
    orchestrate_delete,
)
from app.database import get_async_engine
from app.models import ChainChannel, ProxyChain, Server, XuiServer


def _ours(chain_id: int = 7) -> dict:
    return {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": f"chain-{chain_id}-alpha-exit", "protocol": "vless"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {
                    "type": "field",
                    "inboundTag": [f"chain-{chain_id}-alpha-relay"],
                    "outboundTag": f"chain-{chain_id}-alpha-exit",
                },
                {"type": "field", "outboundTag": "blocked",
                 "protocol": ["bittorrent"]},
            ],
        },
    }


def _current_with_foreign() -> dict:
    return {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            # stale copy of our own chain from the previous push
            {"tag": "chain-7-alpha-exit", "protocol": "vless", "stale": True},
            # another PiTun install's chain
            {"tag": "chain-9-beta-exit", "protocol": "vless"},
            # hand-made entry on the panel
            {"tag": "custom-warp", "protocol": "wireguard"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "inboundTag": ["chain-7-alpha-relay"],
                 "outboundTag": "chain-7-alpha-exit"},
                {"type": "field", "inboundTag": ["chain-9-beta-relay"],
                 "outboundTag": "chain-9-beta-exit"},
                {"type": "field", "inboundTag": ["in-8443"],
                 "outboundTag": "custom-warp"},
                {"type": "field", "outboundTag": "blocked",
                 "protocol": ["bittorrent"]},
            ],
        },
    }


def test_merge_preserves_foreign_and_replaces_own():
    merged = merge_relay_template(
        current=_current_with_foreign(), ours=_ours(), own_chain_ids={7},
    )
    tags = [o["tag"] for o in merged["outbounds"]]
    # Default egress stays first; our chain rebuilt exactly once.
    assert tags[0] == "direct"
    assert tags.count("chain-7-alpha-exit") == 1
    assert not any(o.get("stale") for o in merged["outbounds"])
    # Foreign entries survive, appended after ours (order past
    # outbounds[0] carries no routing meaning).
    assert "chain-9-beta-exit" in tags
    assert "custom-warp" in tags
    assert "blocked" in tags

    rules = merged["routing"]["rules"]
    assert rules[-1]["outboundTag"] == "blocked", (
        "the bittorrent block stays the last word"
    )
    assert sum(1 for r in rules if "api" in (r.get("inboundTag") or [])) == 1
    assert any("chain-9-beta-relay" in (r.get("inboundTag") or []) for r in rules)
    assert any("in-8443" in (r.get("inboundTag") or []) for r in rules)


def test_merge_with_empty_current_returns_ours():
    ours = _ours()
    assert merge_relay_template(current={}, ours=ours, own_chain_ids={7}) is ours
    assert merge_relay_template(current=None, ours=ours, own_chain_ids={7}) is ours
    assert merge_relay_template(current="garbage", ours=ours, own_chain_ids={7}) is ours


def test_merge_strips_deleted_chain_entries():
    # Last channel deleted: our fresh template has no chain outbounds,
    # but the panel still carries our stale ones — they must go while
    # the foreign install's entries stay.
    ours = {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
        "routing": {"rules": [
            {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
            {"type": "field", "outboundTag": "blocked",
             "protocol": ["bittorrent"]},
        ]},
    }
    merged = merge_relay_template(
        current=_current_with_foreign(), ours=ours, own_chain_ids={7},
    )
    tags = [o["tag"] for o in merged["outbounds"]]
    assert "chain-7-alpha-exit" not in tags
    assert "chain-9-beta-exit" in tags


def _client_with(setting=None, error=None):
    c = MagicMock()
    c.get_xray_setting = AsyncMock(return_value=setting, side_effect=error)
    return c


def test_foreign_check_detects_foreign_chain_entries():
    c = _client_with(setting=_current_with_foreign())
    assert asyncio.run(_template_has_foreign_entries(c, own_chain_ids={7}))


def test_foreign_check_clean_template():
    c = _client_with(setting=_ours())
    assert not asyncio.run(_template_has_foreign_entries(c, own_chain_ids={7}))


def test_foreign_check_assumes_foreign_on_read_failure():
    c = _client_with(error=RuntimeError("panel down"))
    assert asyncio.run(_template_has_foreign_entries(c, own_chain_ids={7}))


class TestOrchestrateDeleteSurvivorDedup:
    """Regression: deleting a chain while another of our chains survives on
    the same relay must not duplicate the survivor's template entries.

    The delete path rebuilds a combined relay template that excludes the
    deleted chain. That fresh template contains the surviving chain's
    outbounds/rules, while the panel's current template still carries the
    previous push of those same entries. If `_push_xray_template` is called
    with only the deleted chain's id in `own_chain_ids`, merge_relay_template
    treats the survivor's current entries as foreign, carries them over, and
    then appends the fresh ones — producing duplicate outbounds and rules.

    This test exercises the actual delete path (not only the merge helper)
    to ensure the set of own chain ids passed to the merge includes both the
    deleted chain and the survivors.
    """

    def _seed_panel(self, session):
        srv = Server(name="relay", host="192.0.2.5", port=22,
                     user="root", auth_type="key")
        session.add(srv)
        session.commit()
        session.refresh(srv)
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u",
            panel_pass="p", panel_port=12345, panel_basepath="/t", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)
        return xs

    def _seed_chain(self, session, xs, *, name, relay_port, exit_port,
                    relay_rid, exit_rid, uuid, pbk):
        chain = ProxyChain(
            name=name, relay_xui_server_id=xs.id,
            exit_xui_server_id=xs.id, status="deployed",
        )
        session.add(chain)
        session.commit()
        session.refresh(chain)
        ch = ChainChannel(
            chain_id=chain.id, name="alpha", client_sni=f"{name}.example",
            relay_port=relay_port, exit_port=exit_port,
            relay_inbound_remote_id=relay_rid, exit_inbound_remote_id=exit_rid,
            relay_pbk="rpbk", relay_sid="rsid", relay_pvk="rprv",
            exit_uuid=uuid, exit_pbk=pbk, exit_sid="esid",
        )
        session.add(ch)
        session.commit()
        return chain, ch

    @staticmethod
    def _current_template(chain_a_id, chain_b_id):
        """A panel template that already holds stale entries for both our
        chains plus a genuinely foreign hand-made entry."""
        return {
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": f"chain-{chain_a_id}-alpha",
                 "protocol": "vless", "stale": True},
                {"tag": f"chain-{chain_b_id}-alpha",
                 "protocol": "vless", "stale": True},
                {"tag": "foreign-custom", "protocol": "wireguard"},
                {"tag": "blocked", "protocol": "blackhole"},
            ],
            "routing": {
                "rules": [
                    {"type": "field", "inboundTag": ["api"],
                     "outboundTag": "api"},
                    {"type": "field",
                     "inboundTag": [f"chain-{chain_a_id}-alpha-relay"],
                     "outboundTag": f"chain-{chain_a_id}-alpha"},
                    {"type": "field",
                     "inboundTag": [f"chain-{chain_b_id}-alpha-relay"],
                     "outboundTag": f"chain-{chain_b_id}-alpha"},
                    {"type": "field", "inboundTag": ["in-9999"],
                     "outboundTag": "foreign-custom"},
                    {"type": "field", "outboundTag": "blocked",
                     "protocol": ["bittorrent"]},
                ],
            },
        }

    @pytest.mark.usefixtures("client")
    def test_delete_chain_with_survivor_replaces_not_duplicates(self, session):
        xs = self._seed_panel(session)
        chain_a, _ = self._seed_chain(
            session, xs, name="alpha-chain", relay_port=443, exit_port=8443,
            relay_rid=11, exit_rid=12, uuid="u-a", pbk="p-a",
        )
        chain_b, _ = self._seed_chain(
            session, xs, name="beta-chain", relay_port=444, exit_port=8444,
            relay_rid=21, exit_rid=22, uuid="u-b", pbk="p-b",
        )
        chain_a_id, chain_b_id = chain_a.id, chain_b.id
        session.close()

        with (
            mock.patch("app.core.xui_chain.XuiClient") as MC,
            mock.patch("app.core.xui_chain._ufw_chain_ports",
                       new_callable=AsyncMock),
            mock.patch("app.core.xui_chain._clear_xray_template_via_ssh",
                       new_callable=AsyncMock),
        ):
            inst = MC.return_value.__aenter__.return_value
            inst.del_inbound = AsyncMock(return_value=None)
            inst.restart_xray = AsyncMock(return_value=None)
            inst.get_inbound = AsyncMock(side_effect=lambda rid: {
                11: {"remark": "VPN-ALPHA-CHAIN", "port": 443},
                12: {"remark": "ALPHA-CHAIN-Exit", "port": 8443},
                21: {"remark": "VPN-BETA-CHAIN", "port": 444},
                22: {"remark": "BETA-CHAIN-Exit", "port": 8444},
            }.get(rid, {"remark": "?", "port": 0}))
            inst.get_xray_setting = AsyncMock(return_value=self._current_template(
                chain_a_id, chain_b_id,
            ))
            inst.push_xray_setting = AsyncMock(return_value=None)

            async def run():
                async with AsyncSession(get_async_engine()) as s:
                    chain = await s.get(ProxyChain, chain_a_id)
                    channels = list((await s.exec(
                        select(ChainChannel).where(
                            ChainChannel.chain_id == chain.id),
                    )).all())
                    await orchestrate_delete(
                        chain=chain, channels=channels, session=s,
                    )

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()

        # The relay panel was reached and the template was pushed (not
        # cleared via SSH), because chain B still lives on this relay.
        inst.push_xray_setting.assert_awaited_once()
        pushed = inst.push_xray_setting.await_args.args[0]

        outbound_tags = [o["tag"] for o in pushed["outbounds"]]
        rules = pushed["routing"]["rules"]

        # Deleted chain stripped entirely.
        assert all(f"chain-{chain_a_id}-" not in t for t in outbound_tags)
        assert not any(
            f"chain-{chain_a_id}-" in (r.get("outboundTag") or "")
            or f"chain-{chain_a_id}-" in (r.get("inboundTag") or [])
            for r in rules
        )

        # Surviving chain occurs exactly once: its stale copy was treated
        # as ours and replaced, not preserved as foreign and duplicated.
        assert outbound_tags.count(f"chain-{chain_b_id}-alpha") == 1
        assert not any(
            o.get("stale") for o in pushed["outbounds"]
        ), "stale surviving-chain copy should have been replaced"
        b_relay_rules = [
            r for r in rules
            if f"chain-{chain_b_id}-alpha-relay" in (r.get("inboundTag") or [])
        ]
        assert len(b_relay_rules) == 1
        assert b_relay_rules[0]["outboundTag"] == f"chain-{chain_b_id}-alpha"

        # Genuinely foreign entry and the baseline scaffolding survive.
        assert "foreign-custom" in outbound_tags
        assert any(
            "in-9999" in (r.get("inboundTag") or [])
            and r.get("outboundTag") == "foreign-custom"
            for r in rules
        )
        assert outbound_tags[0] == "direct"
        assert "blocked" in outbound_tags
        assert rules[-1]["outboundTag"] == "blocked"
        assert sum(1 for r in rules if "api" in (r.get("inboundTag") or [])) == 1
