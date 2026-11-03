"""The xray checksums the installer verifies on the device must be the ones the
Dockerfile pins when it builds the image. They drifted once (xray bumped in the
Dockerfile, install.sh kept the old values) and an update on a real box died on
"xray sha MISMATCH"."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "backend" / "Dockerfile"
INSTALL = ROOT / "install.sh"

pytestmark = pytest.mark.skipif(not (DOCKERFILE.exists() and INSTALL.exists()), reason="repo files not shipped")


def _pins(text: str, pattern: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(pattern, text)}


def test_installer_pins_match_the_dockerfile_for_every_arch():
    docker = _pins(DOCKERFILE.read_text(encoding="utf-8", errors="ignore"),
                   r"ARG XRAY_SHA256_(AMD64|ARM64|ARM)=([0-9a-f]{64})")
    inst = _pins(INSTALL.read_text(encoding="utf-8", errors="ignore"),
                 r'XRAY_SHA_(AMD64|ARM64|ARM)="\$\{PITUN_XRAY_SHA_\w+:-([0-9a-f]{64})\}"')
    assert set(docker) == {"AMD64", "ARM64", "ARM"} and set(inst) == set(docker)
    assert inst == docker


def test_failure_message_does_not_read_an_out_of_scope_variable():
    # `attempt` is local to the retry function; the message used to read it
    # under `set -u` and crashed with "unbound variable" instead of explaining.
    assert "$((attempt" not in INSTALL.read_text(encoding="utf-8", errors="ignore")
