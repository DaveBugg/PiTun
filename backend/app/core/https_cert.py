"""Manual re-issue of the panel's HTTPS leaf certificate.

`install.sh` runs `scripts/gen-cert.sh` on the host before nginx starts
and re-issues the leaf automatically when the LAN IP changes. This
manager is the on-demand path for the Settings → HTTPS "re-issue"
button: run the same script with `--force` on the host (via nsenter),
then reload the gateway nginx so it picks up the new files.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

from app.core.network_config import host_run

logger = logging.getLogger(__name__)

_SAFE_PATH = re.compile(r"[A-Za-z0-9_\-./]+")


@dataclass
class CertReissueResult:
    ok: bool
    output: str
    nginx_reloaded: bool


class HttpsCertManager:
    def __init__(
        self,
        scripts_mount: str = "/app/scripts",
        nginx_container: str = "pitun-nginx",
    ) -> None:
        self.scripts_mount = scripts_mount
        self.nginx_container = nginx_container

    def _host_scripts_dir(self) -> str:
        """Host path backing our /app/scripts bind mount.

        The install dir is operator-chosen (--dir), so we read the mount
        source from /proc/self/mounts instead of hardcoding /opt/pitun.
        """
        with open("/proc/self/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == self.scripts_mount:
                    src = parts[0]
                    if not _SAFE_PATH.fullmatch(src):
                        raise RuntimeError(f"unexpected mount source: {src!r}")
                    return src
        raise RuntimeError(f"{self.scripts_mount} is not a bind mount")

    def _run_gen_cert(self) -> str:
        script = f"{self._host_scripts_dir()}/gen-cert.sh"
        proc = host_run(
            ["sh", "-c", f"bash {script} --force"],
            timeout=30.0,
        )
        output = "\n".join(
            line for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
            if line.strip()
        ).strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"gen-cert.sh exited {proc.returncode}: {output[-400:]}"
            )
        return output

    def _reload_nginx(self) -> bool:
        try:
            import docker  # lazy — matches naive_manager's pattern

            host = os.environ.get("DOCKER_HOST")
            client = (
                docker.DockerClient(base_url=host, timeout=15)
                if host else docker.from_env(timeout=15)
            )
            try:
                container = client.containers.get(self.nginx_container)
                return container.exec_run(["nginx", "-s", "reload"]).exit_code == 0
            finally:
                client.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("pitun-nginx reload after cert re-issue failed: %s", e)
            return False

    async def reissue(self) -> CertReissueResult:
        output = await asyncio.to_thread(self._run_gen_cert)
        reloaded = await asyncio.to_thread(self._reload_nginx)
        return CertReissueResult(ok=True, output=output, nginx_reloaded=reloaded)


https_cert_manager = HttpsCertManager()
