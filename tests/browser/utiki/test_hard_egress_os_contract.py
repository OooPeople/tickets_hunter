"""Observe the container boundary before any production-origin navigation."""

import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


pytestmark = pytest.mark.browser


def _require_network_none() -> None:
    assert os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") == "1"
    assert sys.platform == "linux"
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not [
        line for line in Path("/proc/net/route").read_text().splitlines()[1:]
        if line.split()[1] == "00000000"
    ]
    assert not [
        line for line in Path("/proc/net/ipv6_route").read_text().splitlines()
        if line.split()[0] == "0" * 32 and line.split()[1] == "00"
        and line.split()[-1] != "lo"
    ]


def test_browser_starts_only_after_os_network_isolation() -> None:
    _require_network_none()
    with pytest.raises(OSError):
        socket.create_connection(("198.51.100.1", 80), timeout=0.2)


def test_child_process_inherits_the_network_boundary() -> None:
    _require_network_none()
    result = subprocess.run(
        [sys.executable, "-c", "import socket; socket.create_connection(('198.51.100.1', 80), timeout=0.2)"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "OSError" in result.stderr
