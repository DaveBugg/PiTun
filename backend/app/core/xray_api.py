"""xray gRPC API client — add/remove outbounds and routing rules at runtime."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_API_ADDR = f"127.0.0.1:{settings.xray_api_port}"


async def _run_api(*args: str) -> tuple[int, str, str]:
    """Run xray api command and return (rc, stdout, stderr)."""
    cmd = [settings.xray_binary, "api"] + list(args[:1]) + [f"--server={_API_ADDR}"] + list(args[1:])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        logger.warning("xray api command timed out: %s", " ".join(args))
        return 1, "", "timeout"
    except (FileNotFoundError, OSError) as exc:
        logger.warning("xray api command failed: %s", exc)
        return 1, "", str(exc)


async def add_outbound(outbound: Dict[str, Any]) -> bool:
    """Add an outbound to running xray via API (writes temp file).

    Idempotent: if xray reports "existing tag found", we remove the old
    outbound and retry once. This matters for NodeCircle rotations — the
    tag may already exist from the previous incarnation.
    """
    import tempfile, os
    tag = outbound.get("tag")
    payload = json.dumps({"outbounds": [outbound]})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(payload)
        tmp_path = f.name
    try:
        rc, out, err = await _run_api("ado", tmp_path)
        if rc != 0 and tag and "existing tag found" in err:
            logger.debug("xray api: tag %s already exists — removing and retrying", tag)
            await remove_outbound(tag)
            rc, out, err = await _run_api("ado", tmp_path)
        if rc != 0:
            logger.warning("xray api ado failed: %s", err.strip())
            return False
        logger.debug("xray api: added outbound %s", tag)
        return True
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def remove_outbound(tag: str) -> bool:
    """Remove an outbound from running xray by tag."""
    rc, out, err = await _run_api("rmo", tag)
    if rc != 0:
        logger.debug("xray api rmo failed for %s: %s", tag, err.strip())
        return False
    logger.debug("xray api: removed outbound %s", tag)
    return True


async def add_routing_rules(rules: List[Dict[str, Any]]) -> bool:
    """Add routing rules to running xray."""
    payload = json.dumps({"rule": rules})
    rc, out, err = await _run_api("adrules", f"--json={payload}")
    if rc != 0:
        logger.warning("xray api adrules failed: %s", err.strip())
        return False
    return True


async def remove_routing_rules(rule_tags: List[str]) -> bool:
    """Remove routing rules by their tags."""
    for tag in rule_tags:
        rc, out, err = await _run_api("rmrules", tag)
        if rc != 0:
            logger.debug("xray api rmrules failed for %s: %s", tag, err.strip())
    return True


async def override_balancer(balancer_tag: str, outbound_tags: List[str]) -> bool:
    """Override which outbounds a balancer selects (bo = balancer override)."""
    # bo command: xray api bo <balancerTag> <outbound1> <outbound2> ...
    rc, out, err = await _run_api("bo", balancer_tag, *outbound_tags)
    if rc != 0:
        logger.debug("xray api bo failed: %s", err.strip())
        return False
    return True


async def is_api_available() -> bool:
    """Check if xray stats API is reachable."""
    rc, out, err = await _run_api("statssys")
    return rc == 0
