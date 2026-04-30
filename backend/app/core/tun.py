"""TUN interface lifecycle management."""
import asyncio
import logging

log = logging.getLogger("pitun.tun")


async def setup_tun(address: str = "10.0.0.1/30", mtu: int = 9000) -> None:
    """Create and configure tun0 interface (called when autoRoute=False)."""
    cmds = [
        ["ip", "tuntap", "add", "dev", "tun0", "mode", "tun"],
        ["ip", "addr", "add", address, "dev", "tun0"],
        ["ip", "link", "set", "tun0", "up"],
        ["ip", "link", "set", "tun0", "mtu", str(mtu)],
    ]
    for cmd in cmds:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 and b"exists" not in (err or b""):
            log.warning("tun cmd %s: %s", cmd, err)


async def teardown_tun() -> None:
    """Remove tun0 interface."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "link", "del", "tun0",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()


async def tun_exists() -> bool:
    """Check if tun0 interface exists."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "link", "show", "tun0",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0
