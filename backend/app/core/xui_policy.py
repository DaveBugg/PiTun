"""Push the recommended xray timeouts onto a 3x-ui panel.

Split out of the API layer because three callers need it and they must
behave identically: the manual action, a fresh deploy, and registering a
panel that was installed elsewhere. A panel that PiTun manages should not
sit on xray's defaults — `connIdle 300` kills idle pooled connections and
the half-close timers cut streaming responses (see core/xray_policy.py).

We PATCH the panel's current template instead of pushing one of our own.
`get_xray_setting` returns the effective config even when the panel has
never stored a template, so a bare install gets its own default back with
only `policy.levels[*]` rewritten — outbounds, routing and stats flags are
left exactly as they were.
"""
import logging
from typing import NamedTuple, Optional

from app.core.xray_policy import merge_timeouts
from app.core.xui_api import XuiAPIError, XuiClient

logger = logging.getLogger(__name__)


class PolicyResult(NamedTuple):
    changed: bool
    restarted: bool
    detail: str


async def apply_policy_to_panel(
    *,
    base_url: str,
    api_token: str,
    panel_user: Optional[str] = None,
    panel_pass: Optional[str] = None,
    direct: bool = False,
    restart: bool = True,
    settings_map: Optional[dict] = None,
) -> PolicyResult:
    """Set the timeouts in the panel's template. Idempotent.

    Raises `XuiAPIError` when the panel is unreachable or rejects the
    write, so an interactive caller can surface it; the deploy path
    catches it instead — a timeout tweak must never fail an install that
    otherwise succeeded.
    """
    async with XuiClient(
        base_url=base_url, api_token=api_token, verify_tls=False,
        panel_user=panel_user, panel_pass=panel_pass, direct=direct,
    ) as client:
        setting = await client.get_xray_setting()
        template = setting.get("xraySetting")
        if not isinstance(template, dict):
            raise XuiAPIError(
                "panel returned no usable xray template "
                f"(got {type(template).__name__})",
                kind="format",
            )

        policy, changed = merge_timeouts(template.get("policy"), settings_map)
        if not changed:
            return PolicyResult(
                False, False, "Policy already matches the recommended timeouts.",
            )

        template["policy"] = policy
        await client.push_xray_setting(
            template, outbound_test_url=setting.get("outboundTestUrl"),
        )

        restarted = False
        if restart:
            # The template is persisted immediately, but the panel keeps
            # serving the previously generated config until xray bounces.
            try:
                await client.restart_xray()
                restarted = True
            except XuiAPIError as exc:
                logger.warning("apply policy: restart_xray failed: %s", exc)

    return PolicyResult(
        True, restarted,
        "Policy applied and Xray restarted." if restarted
        else "Policy applied; restart Xray on the panel for it to take effect.",
    )
