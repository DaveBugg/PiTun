"""Tests for the chain orchestrator (since v1.3.0-beta.7).

Heavy on the build helpers + lighter on the live orchestrator (the
latter needs both an httpx mock AND a real DB session — covered
in Phase 8's end-to-end smoke). Here we lock in the wire-shape
contract of:

  * `_build_exit_inbound_payload` — xhttp + Reality, SNI-masked
  * `_build_relay_inbound_payload` — TCP + Reality + xtls-rprx-vision
  * `build_xray_template_config` — outbounds + routing rules layout

Plus URI generation, tag conventions, and port allocation.

Channel names + SNIs in this file are intentionally neutral
(`alpha`/`beta`, `example.com`/`example.org`) — PiTun ships no
hardcoded masquerade-target presets. Each operator picks their
own per their threat model. See memory/feedback_chain_naming_opsec.
"""
from __future__ import annotations

import json

import pytest

from app.core.xui_chain import (
    ChannelDraft,
    _build_exit_inbound_payload,
    _build_relay_inbound_payload,
    _exit_tag,
    _pick_port,
    _relay_tag,
    build_xray_template_config,
    orchestrate_delete_client,
)
from app.models import ChainChannel, ProxyChain


# ── Tag conventions ────────────────────────────────────────────────────────


class TestTagConventions:
    def test_exit_tag_format(self):
        assert _exit_tag(7, "alpha") == "chain-7-alpha-exit"

    def test_relay_tag_format(self):
        assert _relay_tag(7, "alpha") == "chain-7-alpha-relay"

    def test_tags_unique_per_chain_channel(self):
        # Two channels of the same name on different chains don't
        # collide (chain_id is in the tag).
        assert _relay_tag(1, "alpha") != _relay_tag(2, "alpha")


# ── Exit-inbound payload ───────────────────────────────────────────────────


class TestBuildExitInboundPayload:
    def test_wire_shape(self):
        draft = ChannelDraft(
            name="alpha",
            client_sni="example.com",  # only used on relay; here ignored
            exit_port=10443,
            exit_xhttp_path="/api/v1/alpha",
            exit_remark="Alpha-Exit",
        )
        payload = _build_exit_inbound_payload(
            chain_id=42, channel=draft,
            bootstrap_uuid="UUID-X",
            exit_sni="cover.example.net",
            private_key="PRIV", public_key="PUB", short_id="SID",
            bootstrap_email="chain-42-alpha-boot",
        )
        assert payload["protocol"] == "vless"
        assert payload["port"] == 10443
        assert payload["tag"] == "chain-42-alpha-exit"
        assert payload["remark"] == "Alpha-Exit"
        # Settings: one bootstrap client with no flow (xhttp doesn't
        # use xtls-rprx-vision).
        client = payload["settings"]["clients"][0]
        assert client["id"] == "UUID-X"
        assert client["flow"] == ""
        assert client["email"] == "chain-42-alpha-boot"
        # Stream: xhttp + reality with the user-provided path.
        ss = payload["streamSettings"]
        assert ss["network"] == "xhttp"
        assert ss["security"] == "reality"
        assert ss["realitySettings"]["dest"] == "cover.example.net:443"
        assert ss["realitySettings"]["serverNames"] == ["cover.example.net"]
        assert ss["realitySettings"]["privateKey"] == "PRIV"
        assert ss["realitySettings"]["shortIds"] == ["SID"]
        assert ss["realitySettings"]["settings"]["publicKey"] == "PUB"
        assert ss["xhttpSettings"]["path"] == "/api/v1/alpha"

    def test_default_xhttp_path_when_empty(self):
        draft = ChannelDraft(
            name="beta", client_sni="example.org",
            exit_port=10444, exit_xhttp_path="",
        )
        payload = _build_exit_inbound_payload(
            chain_id=1, channel=draft,
            bootstrap_uuid="u", exit_sni="x",
            private_key="p", public_key="P", short_id="s",
            bootstrap_email="e",
        )
        assert payload["streamSettings"]["xhttpSettings"]["path"] == "/api/v1/beta"


# ── Relay-inbound payload ──────────────────────────────────────────────────


class TestBuildRelayInboundPayload:
    def test_uses_client_sni_and_vision(self):
        draft = ChannelDraft(
            name="alpha", client_sni="example.com",
            relay_port=443, relay_remark="Channel-Alpha",
        )
        payload = _build_relay_inbound_payload(
            chain_id=7, channel=draft,
            bootstrap_uuid="U-RELAY",
            private_key="PR", public_key="PB", short_id="SD",
            bootstrap_email="chain-7-alpha-boot",
        )
        assert payload["port"] == 443
        assert payload["tag"] == "chain-7-alpha-relay"
        assert payload["remark"] == "Channel-Alpha"
        client = payload["settings"]["clients"][0]
        # Relay-side bootstrap client carries the vision flow.
        assert client["flow"] == "xtls-rprx-vision"
        ss = payload["streamSettings"]
        assert ss["network"] == "tcp"
        assert ss["security"] == "reality"
        assert ss["realitySettings"]["dest"] == "example.com:443"
        assert ss["realitySettings"]["serverNames"] == ["example.com"]


# ── xrayTemplateConfig ─────────────────────────────────────────────────────


class TestBuildXrayTemplate:
    def test_two_channels_produce_two_chain_outbounds_and_rules(self):
        chain = ProxyChain(
            id=3, name="test",
            exit_xui_server_id=1, relay_xui_server_id=2,
            exit_sni="cover.example.net",
        )
        ch1 = ChainChannel(
            id=10, chain_id=3, name="alpha", order=0,
            exit_port=10443, relay_port=443,
            exit_xhttp_path="/api/v1/alpha",
            client_sni="example.com",
            exit_uuid="UUID-A", exit_pbk="PBK-A",
            exit_pvk="PVK-A", exit_sid="SID-A",
            relay_pbk="r-pbk-a", relay_pvk="r-pvk-a", relay_sid="r-sid-a",
        )
        ch2 = ChainChannel(
            id=11, chain_id=3, name="beta", order=1,
            exit_port=10444, relay_port=8443,
            exit_xhttp_path="/api/v1/beta",
            client_sni="example.org",
            exit_uuid="UUID-B", exit_pbk="PBK-B",
            exit_pvk="PVK-B", exit_sid="SID-B",
            relay_pbk="r-pbk-b", relay_pvk="r-pvk-b", relay_sid="r-sid-b",
        )
        tpl = build_xray_template_config(
            chain=chain, channels=[ch1, ch2],
            exit_host="1.2.3.4",
        )

        # Chain outbounds — one per channel + api + direct + blocked.
        # Tag is `chain-<chain_id>-<channel_name>` so two chains
        # sharing the same channel name on one relay don't collide.
        tags = [o["tag"] for o in tpl["outbounds"]]
        assert "chain-3-alpha" in tags
        assert "chain-3-beta" in tags
        assert "api" in tags
        assert "direct" in tags
        assert "blocked" in tags

        # Alpha outbound: vless+xhttp+reality dialing the exit host
        # on the exit-port with our UUID + Reality material.
        alpha = next(o for o in tpl["outbounds"] if o["tag"] == "chain-3-alpha")
        assert alpha["protocol"] == "vless"
        vnext = alpha["settings"]["vnext"][0]
        assert vnext["address"] == "1.2.3.4"
        assert vnext["port"] == 10443
        assert vnext["users"][0]["id"] == "UUID-A"
        ss = alpha["streamSettings"]
        assert ss["network"] == "xhttp"
        assert ss["security"] == "reality"
        assert ss["realitySettings"]["serverName"] == "cover.example.net"
        assert ss["realitySettings"]["publicKey"] == "PBK-A"
        assert ss["realitySettings"]["shortId"] == "SID-A"
        assert ss["xhttpSettings"]["path"] == "/api/v1/alpha"

        # Routing — one rule per channel: relay inboundTag → chain
        # outboundTag. Plus the api rule + bittorrent blackhole.
        rules = tpl["routing"]["rules"]
        assert {"type": "field", "inboundTag": ["api"], "outboundTag": "api"} in rules
        # Each chain rule by tag-shape (we don't enforce ordering).
        alpha_rule = next(
            r for r in rules
            if r.get("outboundTag") == "chain-3-alpha"
        )
        assert alpha_rule["inboundTag"] == ["chain-3-alpha-relay"]
        beta_rule = next(
            r for r in rules
            if r.get("outboundTag") == "chain-3-beta"
        )
        assert beta_rule["inboundTag"] == ["chain-3-beta-relay"]
        assert any(
            r.get("outboundTag") == "blocked" and r.get("protocol") == ["bittorrent"]
            for r in rules
        )

    def test_empty_channels_yields_minimal_template(self):
        # The delete-chain path pushes an empty template to clear
        # stale routing rules from the relay's xray instance.
        chain = ProxyChain(
            id=99, name="x",
            exit_xui_server_id=1, relay_xui_server_id=2,
            exit_sni="cover.example.net",
        )
        tpl = build_xray_template_config(
            chain=chain, channels=[], exit_host="127.0.0.1",
        )
        chain_tags = [o["tag"] for o in tpl["outbounds"] if o["tag"].startswith("chain-")]
        assert chain_tags == []
        # api + direct + blocked still present so the relay xray
        # boots cleanly.
        baseline = {o["tag"] for o in tpl["outbounds"]}
        assert {"api", "direct", "blocked"} <= baseline


# ── Port allocation ────────────────────────────────────────────────────────


class TestPickPort:
    def test_avoids_local_and_remote(self):
        local: set[int] = {10001}
        # 10002-10004 already taken on the panel.
        remote = {10002, 10003, 10004}
        # Snapshot the local/remote sets BEFORE the call — _pick_port
        # mutates `local` by adding the chosen port to it.
        forbidden = local | remote
        port = _pick_port(local, remote, 10001, 10100)
        assert port not in forbidden
        assert 10001 <= port <= 10100

    def test_local_set_mutated_in_place(self):
        local: set[int] = set()
        port = _pick_port(local, set(), 10000, 10100)
        assert port in local


class TestOrchestrateDeleteClient:
    """Cascade behaviour of `orchestrate_delete_client`.

    Pattern mirrors `test_subscriptions.py`: seed DB via the conftest
    sync `session` fixture, then run the async orchestrator via
    `asyncio.run` inside the test. The orchestrator opens its OWN
    `AsyncSession` against `get_async_engine()` — for this to point
    at the same temp DB as our seed, we need the `client` fixture
    too (it monkey-patches `db_mod._async_engine` so prod code sees
    our test engine).
    """

    def test_orphan_node_cleanup_cascades_on_chain_client_delete(
        self, client, session,
    ):
        """Regression: deleting a ChainClient via orchestrate_delete_client
        must also drop any Nodes exported from its channels. Without
        this cascade, the user sees orphan Nodes in the Nodes tab whose
        UUID no longer authenticates against the panel (because the
        panel-side client is gone). Mirror of the cascade in
        xui.py::delete_client for regular x-ui clients."""
        import asyncio
        from unittest import mock
        from unittest.mock import AsyncMock
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import (
            Node, Server, XuiServer, ChainClient, ChainClientChannel,
        )

        # Seed via the conftest sync session — points at the same
        # temp SQLite file the patched async_engine sees.
        srv = Server(
            name="relay-srv", host="1.2.3.4", port=22,
            user="root", auth_type="key",
        )
        session.add(srv)
        session.commit()
        session.refresh(srv)
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u",
            panel_pass="p", panel_port=12345,
            panel_basepath="/test", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)
        chain = ProxyChain(
            name="test-chain",
            relay_xui_server_id=xs.id,
            exit_xui_server_id=xs.id,
            sni_cover="example.com",
            status="deployed",
        )
        session.add(chain)
        session.commit()
        session.refresh(chain)
        ch = ChainChannel(
            chain_id=chain.id, name="alpha",
            client_sni="example.com",
            relay_port=443, exit_port=8443,
            relay_inbound_remote_id=1,
            exit_inbound_remote_id=2,
            relay_pbk="pbk", relay_sid="sid",
            relay_prv="prv",
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        node = Node(
            name="CHAIN-alice-alpha", protocol="vless",
            address="1.2.3.4", port=443,
            uuid="00000000-0000-0000-0000-000000000001",
            transport="tcp", enabled=True,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        cc = ChainClient(chain_id=chain.id, label="alice")
        session.add(cc)
        session.commit()
        session.refresh(cc)
        pair = ChainClientChannel(
            chain_client_id=cc.id, channel_id=ch.id,
            client_uuid="00000000-0000-0000-0000-000000000001",
            exported_node_id=node.id,
        )
        session.add(pair)
        session.commit()
        session.refresh(pair)

        cc_id = cc.id
        node_id = node.id
        chain_id = chain.id
        # Close the sync session BEFORE running async — otherwise the
        # SQLite writer lock from this session blocks the async commit.
        session.close()

        # Stub XuiClient so the orchestrator doesn't try to dial
        # the panel — del_client + restart_xray are no-ops here.
        with mock.patch("app.core.xui_chain.XuiClient") as MC:
            instance = MC.return_value.__aenter__.return_value
            instance.del_client = AsyncMock(return_value=None)
            instance.restart_xray = AsyncMock(return_value=None)

            async def run():
                async with AsyncSession(get_async_engine()) as async_s:
                    chain_row = await async_s.get(ProxyChain, chain_id)
                    cc_row = await async_s.get(ChainClient, cc_id)
                    pairs = list((await async_s.exec(
                        select(ChainClientChannel)
                        .where(ChainClientChannel.chain_client_id == cc_id),
                    )).all())
                    channels = list((await async_s.exec(
                        select(ChainChannel)
                        .where(ChainChannel.chain_id == chain_id),
                    )).all())
                    channels_by_id = {c.id: c for c in channels}
                    await orchestrate_delete_client(
                        chain=chain_row, chain_client=cc_row,
                        pairs=pairs,
                        channels_by_id=channels_by_id,
                        session=async_s,
                    )

            # Use the existing event loop indirectly via run_until_complete
            # — `asyncio.run()` creates a fresh loop, which conflicts with
            # the one the `client` fixture's TestClient holds onto.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()

        # Verify via fresh sync read.
        from sqlmodel import Session as SyncSession
        sync_engine_obj = session.get_bind()
        with SyncSession(sync_engine_obj) as s:
            assert s.get(ChainClient, cc_id) is None, \
                "ChainClient should be deleted"
            pairs_after = list(s.exec(
                select(ChainClientChannel)
                .where(ChainClientChannel.chain_client_id == cc_id),
            ).all())
            assert pairs_after == [], \
                "ChainClientChannel should cascade-delete with parent"
            assert s.get(Node, node_id) is None, (
                "exported Node should cascade-delete with chain client "
                "(this was the regression — orphan Node was left behind)"
            )


class TestPickPortExhaustion:
    def test_raises_when_exhausted(self):
        # 5-port window, all taken.
        with pytest.raises(RuntimeError):
            _pick_port(
                used_local={10000, 10001, 10002, 10003, 10004},
                used_remote=set(),
                low=10000, high=10004,
                max_tries=10,
            )
