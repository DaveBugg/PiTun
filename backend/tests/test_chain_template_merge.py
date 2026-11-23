"""Cross-box safety of the relay xray template push.

The relay's xrayTemplateConfig is panel-wide: two PiTun installs can
share one VPS panel. The push must replace only the entries owned by
the pushing install and carry everything else over untouched.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.core.xui_chain import (
    _template_has_foreign_entries,
    merge_relay_template,
)


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
