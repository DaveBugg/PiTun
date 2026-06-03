"""nftables per-set rendering tests (v1.4).

The nftables apply path invokes `nft -f -` via subprocess to actually
load rules into the kernel. These tests don't need real nftables — they
monkey-patch `_nft` to capture the generated script string and assert
that the per-set sections render correctly.
"""
import pytest

from app.core import nftables as nft_mod
from app.core.nftables import NftablesManager, RoutingSetSpec


@pytest.fixture
def captured_script(monkeypatch):
    """Capture the nft script that NftablesManager.apply() would run."""
    captured: dict = {"script": None}

    async def _fake_nft(script):
        captured["script"] = script
        return True

    async def _fake_run_exec(*args):
        return (0, "", "")

    monkeypatch.setattr(nft_mod, "_nft", _fake_nft)
    monkeypatch.setattr(nft_mod, "_run_exec", _fake_run_exec)
    return captured


@pytest.mark.asyncio
async def test_no_per_set_sections_when_specs_empty(captured_script):
    """v1.3.x behaviour preserved when no RoutingSets exist."""
    manager = NftablesManager()
    ok = await manager.apply(routing_set_specs=None)
    assert ok
    script = captured_script["script"]
    assert script is not None
    assert "rset_" not in script  # no per-set sets
    assert "tproxy ip to 127.0.0.1:7893" in script  # default TPROXY still there


@pytest.mark.asyncio
async def test_single_set_renders_mac_set_and_tproxy_rule(captured_script):
    spec = RoutingSetSpec(
        set_id=1,
        macs=("aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"),
        tproxy_port=65500,
    )
    manager = NftablesManager()
    ok = await manager.apply(routing_set_specs=[spec])
    assert ok
    script = captured_script["script"]
    # MAC set declaration
    assert "set rset_1_mac" in script
    assert "type ether_addr" in script
    assert "aa:bb:cc:dd:ee:01" in script
    assert "aa:bb:cc:dd:ee:02" in script
    # TPROXY rules (one TCP + one UDP, both → 65500)
    # Trailing `accept` is REQUIRED — without it, the default TPROXY
    # rule below overwrites the per-set redirect (nft trace confirmed
    # on 1.3 smoke test). See `nftables.py` comment for the full story.
    assert "ether saddr @rset_1_mac ip protocol tcp tproxy ip to 127.0.0.1:65500 meta mark set 1 accept" in script
    assert "ether saddr @rset_1_mac ip protocol udp tproxy ip to 127.0.0.1:65500 meta mark set 1 accept" in script


@pytest.mark.asyncio
async def test_per_set_tproxy_rendered_before_default(captured_script):
    """First-match-wins requires per-set rules to appear BEFORE the
    default TPROXY rule in the prerouting chain."""
    spec = RoutingSetSpec(set_id=1, macs=("aa:bb:cc:dd:ee:01",), tproxy_port=65500)
    manager = NftablesManager()
    await manager.apply(routing_set_specs=[spec])
    script = captured_script["script"]

    per_set_idx = script.index("@rset_1_mac")
    # Default TCP TPROXY without MAC filter
    default_idx = script.index("# TCP TPROXY (default")
    assert per_set_idx < default_idx, (
        "per-set TPROXY redirect must appear before the default TPROXY rule"
    )


@pytest.mark.asyncio
async def test_empty_macs_skipped(captured_script):
    """Spec with no validated MACs must not generate an empty
    `elements = { }` block (would be invalid nftables syntax)."""
    spec = RoutingSetSpec(set_id=1, macs=(), tproxy_port=65500)
    manager = NftablesManager()
    ok = await manager.apply(routing_set_specs=[spec])
    assert ok
    script = captured_script["script"]
    assert "rset_1_mac" not in script


@pytest.mark.asyncio
async def test_invalid_macs_filtered(captured_script):
    """MAC validation strips malformed entries, but a spec with at
    least one VALID MAC still renders normally."""
    spec = RoutingSetSpec(
        set_id=1,
        macs=("not-a-mac", "aa:bb:cc:dd:ee:01", "also bad"),
        tproxy_port=65500,
    )
    manager = NftablesManager()
    await manager.apply(routing_set_specs=[spec])
    script = captured_script["script"]
    assert "rset_1_mac" in script
    assert "aa:bb:cc:dd:ee:01" in script
    assert "not-a-mac" not in script
    assert "also bad" not in script


@pytest.mark.asyncio
async def test_multiple_sets_each_get_their_own_section(captured_script):
    specs = [
        RoutingSetSpec(set_id=1, macs=("aa:01:01:01:01:01",), tproxy_port=65500),
        RoutingSetSpec(set_id=2, macs=("bb:02:02:02:02:02",), tproxy_port=65501),
    ]
    manager = NftablesManager()
    await manager.apply(routing_set_specs=specs)
    script = captured_script["script"]
    assert "set rset_1_mac" in script
    assert "set rset_2_mac" in script
    assert "tproxy ip to 127.0.0.1:65500" in script
    assert "tproxy ip to 127.0.0.1:65501" in script


@pytest.mark.asyncio
async def test_dns_redirect_accept_terminates_chain(captured_script):
    """DNS redirect rules MUST end with `accept` so they terminate the
    prerouting chain before per-set redirects can re-route DNS packets.

    Pre-fix bug: DNS redirect was `tproxy ip to ...:5353 meta mark set 1`
    (no accept). Per-set redirect (which DOES `accept`) sat below in the
    same chain. For devices in a routing set, a DNS query matched both
    verbs in sequence — last-writer wins on the tproxy target → packet
    landed on the per-set inbound instead of `dns-in`. xray's DNS rules
    are gated on `inboundTag: [dns-in, dns-in-53]`, so the engine never
    ran and the operator's DNS Rules (e.g. *.youtube.com → DoT) silently
    didn't apply for set members. Live-caught on 1.3.
    """
    manager = NftablesManager()
    spec = RoutingSetSpec(set_id=1, macs=("aa:bb:cc:dd:ee:01",), tproxy_port=65500)
    await manager.apply(routing_set_specs=[spec])
    script = captured_script["script"]
    # Both DNS redirects must end with `accept`
    assert "ip protocol tcp tcp dport 53 tproxy ip to 127.0.0.1:5353 meta mark set 1 accept" in script, \
        "TCP DNS redirect must accept-terminate"
    assert "ip protocol udp udp dport 53 tproxy ip to 127.0.0.1:5353 meta mark set 1 accept" in script, \
        "UDP DNS redirect must accept-terminate"

    # And the DNS redirect must come BEFORE the per-set redirect — so a
    # DNS-port packet from a set member hits DNS first and exits the
    # chain before the per-set rule can grab it.
    dns_idx = script.index("dport 53 tproxy ip to 127.0.0.1:5353")
    set_idx = script.index("@rset_1_mac ip protocol")
    assert dns_idx < set_idx, "DNS redirect must precede per-set redirect"
