"""P2 domain imports are usable without browser dependencies or side effects."""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = r"""
import importlib
import os
import sys

sys.modules["zendriver"] = None
sys.dont_write_bytecode = True

def deny_side_effects(event, args):
    if event == "open":
        flags = args[2]
        if flags is not None and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise RuntimeError("domain import attempted a file write")
    if event in {
        "os.mkdir",
        "os.rmdir",
        "os.remove",
        "os.rename",
        "os.chmod",
        "os.chown",
        "os.utime",
        "os.link",
        "os.symlink",
        "os.truncate",
    }:
        raise RuntimeError("domain import attempted a filesystem mutation")
    if event.startswith("socket.connect"):
        raise RuntimeError("domain import attempted a network connection")

sys.addaudithook(deny_side_effects)
for module in (
    "platforms.utiki.domain.identity",
    "platforms.utiki.domain.snapshots",
    "platforms.utiki.domain.plans",
    "platforms.utiki.domain.effects",
    "platforms.utiki.domain.inputs",
    "platforms.utiki.domain.purchase",
):
    importlib.import_module(module)
assert "zendriver" not in sys.modules or sys.modules["zendriver"] is None
print("IMPORT_OK")
"""


def test_a01_domain_imports_without_zendriver_or_io(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["HOME"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-S", "-c", SOURCE],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "IMPORT_OK"
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
