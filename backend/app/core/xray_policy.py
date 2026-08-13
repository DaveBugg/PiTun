"""Connection-lifetime policy for every xray we generate a config for.

Xray's defaults are tuned for short browser sessions and quietly break
long-lived clients:

    handshake     4 s     tight on a lossy uplink
    connIdle    300 s     an idle POOLED connection is killed after 5 min,
                          so the next request on it hangs — the classic
                          "it works, then it doesn't" with SDK/agent
                          clients that keep sockets warm between calls
    uplinkOnly    2 s     after the peer half-closes, the other direction
    downlinkOnly  5 s     gets seconds to finish — a model streaming a
                          long answer is cut off mid-response

None of our configs used to set these, so every deployment ran on the
defaults. Xray's own docs recommend 0 for the half-close timers in
HTTP-shaped traffic; `connIdle` at an hour matches what the panel
ecosystem settles on.

`bufferSize` is deliberately NOT set: raising it multiplies per-connection
memory, which is the wrong trade on a Pi or a 1 GB VPS.

3x-ui merges its own `statsUserOnline` into whatever we push (see
`ensureStatsPolicy` in the panel source) and leaves other keys alone, so
these values survive into the panel's generated runtime config.
"""
from typing import Any, Dict

# The timeout half of level 0. Stats flags are added by whoever builds the
# config — the panel forces its own set anyway.
RECOMMENDED_TIMEOUTS: Dict[str, Any] = {
    "handshake": 10,
    "connIdle": 3600,
    "uplinkOnly": 0,
    "downlinkOnly": 0,
}


# Settings keys ↔ policy fields. Operators tune these from the UI; the
# defaults above are what a fresh install gets.
SETTING_KEYS: Dict[str, str] = {
    "handshake": "xray_handshake",
    "connIdle": "xray_conn_idle",
    "uplinkOnly": "xray_uplink_only",
    "downlinkOnly": "xray_downlink_only",
}

# Inbound-only. Xray keeps OUTBOUND sockets alive at Chrome's 45 s/45 s by
# default, so overriding those would only make dead-path detection slower;
# inbound keep-alive is off unless asked for, which is the gap worth
# closing once connIdle is an hour.
KEEPALIVE_SETTING_KEYS: Dict[str, str] = {
    "tcpKeepAliveIdle": "xray_tcp_keepalive_idle",
    "tcpKeepAliveInterval": "xray_tcp_keepalive_interval",
}

# Guard rails for the UI and the API. Wrong values here are hard to debug
# from the symptom (`connIdle: 30` looks like a flaky network), so the
# bounds are deliberately tight enough to keep an operator out of trouble.
BOUNDS: Dict[str, tuple] = {
    "xray_handshake": (1, 600),
    "xray_conn_idle": (30, 86400),
    "xray_uplink_only": (0, 3600),
    "xray_downlink_only": (0, 3600),
    "xray_tcp_keepalive_idle": (0, 86400),
    "xray_tcp_keepalive_interval": (0, 3600),
}


def _int_setting(settings_map: Any, key: str, default: int) -> int:
    try:
        raw = (settings_map or {}).get(key)
        if raw is None or raw == "":
            return default
        value = int(raw)
    except (TypeError, ValueError, AttributeError):
        return default
    low, high = BOUNDS.get(key, (None, None))
    if low is not None and not (low <= value <= high):
        return default
    return value


def timeouts_from_settings(settings_map: Any = None) -> Dict[str, Any]:
    """Policy timeouts, operator-tuned where set, recommended otherwise."""
    return {
        field: _int_setting(settings_map, key, RECOMMENDED_TIMEOUTS[field])
        for field, key in SETTING_KEYS.items()
    }


def inbound_keepalive(settings_map: Any = None) -> Dict[str, int]:
    """`sockopt` keep-alive fields for INBOUND streamSettings.

    Empty when both values are 0 — xray treats a non-zero in either field
    as "enable", so emitting zeros would be a no-op with extra noise.
    """
    values = {
        field: _int_setting(settings_map, key, 0)
        for field, key in KEEPALIVE_SETTING_KEYS.items()
    }
    if not any(values.values()):
        return {}
    return values


def level_zero(settings_map: Any = None, **extra: Any) -> Dict[str, Any]:
    """Level 0 with the effective timeouts plus any caller-specific keys."""
    return {**timeouts_from_settings(settings_map), **extra}


def merge_timeouts(
    policy: Any, settings_map: Any = None,
) -> tuple[Dict[str, Any], bool]:
    """Return `(policy, changed)` with the timeouts applied to every level.

    Used when adopting a panel's EXISTING template: everything the operator
    or the panel already put there is preserved — other levels, stats
    flags, the `system` block — and only the four timeout keys are set.
    A missing level 0 is created, since generated clients carry no explicit
    level and therefore land on it.
    """
    result: Dict[str, Any] = dict(policy) if isinstance(policy, dict) else {}
    levels = result.get("levels")
    levels = dict(levels) if isinstance(levels, dict) else {}
    if "0" not in levels:
        levels["0"] = {}

    wanted = timeouts_from_settings(settings_map)
    changed = False
    for name, level in list(levels.items()):
        merged = dict(level) if isinstance(level, dict) else {}
        for key, value in wanted.items():
            if merged.get(key) != value:
                merged[key] = value
                changed = True
        levels[name] = merged

    if changed or result.get("levels") != levels:
        result["levels"] = levels
        changed = True
    return result, changed
