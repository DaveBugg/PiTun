"""Query xray traffic statistics via the local stats API."""
import asyncio
import json
import logging
from typing import Dict

from app.config import settings

logger = logging.getLogger(__name__)


async def get_outbound_stats() -> Dict[str, Dict[str, int]]:
    """
    Query xray stats API using the xray binary's api subcommand.
    Returns: {tag: {"uplink": bytes, "downlink": bytes}}
    Stat name format: "outbound>>>node-1>>>traffic>>>uplink"
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.xray_binary, "api", "statsquery",
            f"--server=127.0.0.1:{settings.xray_api_port}",
            "--pattern", "outbound",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("xray stats query timed out")
        return {}
    except (FileNotFoundError, OSError) as exc:
        logger.warning("xray stats query failed: %s", exc)
        return {}

    if proc.returncode != 0:
        logger.debug("xray stats query stderr: %s", stderr.decode(errors="replace"))
        return {}

    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return {}

    result: Dict[str, Dict[str, int]] = {}
    for item in data.get("stat", []):
        name: str = item.get("name", "")
        value = int(item.get("value") or 0)
        # "outbound>>>node-1>>>traffic>>>uplink"
        parts = name.split(">>>")
        if len(parts) == 4 and parts[0] == "outbound" and parts[2] == "traffic":
            tag = parts[1]
            direction = parts[3]
            if tag not in result:
                result[tag] = {"uplink": 0, "downlink": 0}
            if direction in ("uplink", "downlink"):
                result[tag][direction] = value

    return result
