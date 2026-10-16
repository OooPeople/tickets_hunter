import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "tools" / "check_utiki_architecture.py"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _inventory(root):
    path = root / "core-inventory.md"
    _write(
        path,
        """# Core inventory

| Actual HEAD | `f307f7caba054fb40aab807a4629c891e69fb200` |

`KEEP` `DIRECT` `CONDITIONAL_BRIDGE` `REMOVE`

Conditional bridge: `src/utiki_core_services.py`.
Approved sources: `refresh_coordinator`, `global_settings`,
`nodriver_common`, and `util`.
""",
    )
    return path


def _base_tree(root):
    _write(root / "src" / "platforms" / "utiki" / "profiles.py", "VALUE = 1\n")
    _write(
        root / "src" / "platforms" / "utiki" / "auth.py",
        "from .profiles import VALUE\n",
    )
    _write(
        root / "src" / "platforms" / "utiki" / "selection.py",
        "import util\nfrom .profiles import VALUE\n",
    )
    _write(
        root / "src" / "platforms" / "utiki" / "refresh.py",
        "import util\nfrom refresh_coordinator import VALUE\n",
    )
    _write(
        root / "src" / "platforms" / "utiki" / "__init__.py",
        "from .workflow import legacy_main\n",
    )
    _write(
        root / "src" / "platforms" / "utiki" / "workflow.py",
        "legacy_main = object()\n",
    )
    _write(root / "src" / "util.py", "VALUE = 1\n")
    _write(root / "src" / "refresh_coordinator.py", "VALUE = 1\n")


def _run(root, inventory, *, strict=True):
    command = [
        sys.executable,
        str(CHECKER),
        "--root",
        str(root),
        "--core-inventory",
        str(inventory),
        "--candidate-sha",
        "1" * 40,
    ]
    if strict:
        command.append("--strict")
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_p1_legacy_tree_passes_without_planned_target_paths(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)

    result = _run(tmp_path, inventory)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["target_files"] == []


def test_application_cannot_call_raw_evaluate(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)
    _write(
        tmp_path
        / "src"
        / "platforms"
        / "utiki"
        / "application"
        / "engine.py",
        "async def run(tab: object) -> None:\n    await tab.evaluate('x')\n",
    )

    result = _run(tmp_path, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "APPLICATION_BROWSER_PRIMITIVE" in report["failure_reasons"]


def test_domain_rejects_io_and_reverse_imports(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)
    _write(
        tmp_path / "src" / "platforms" / "utiki" / "domain" / "state.py",
        "import os\nimport settings\nVALUE = os.getenv('X')\n",
    )

    result = _run(tmp_path, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "DOMAIN_FORBIDDEN_IMPORT" in report["failure_reasons"]
    assert "DOMAIN_FORBIDDEN_CALL" in report["failure_reasons"]


def test_conditional_bridge_only_uses_inventory_approved_core(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "import settings\n",
    )

    result = _run(tmp_path, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "CORE_BRIDGE_FORBIDDEN_IMPORT" in report["failure_reasons"]


def test_conditional_bridge_cannot_import_whole_approved_module(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "import util\n",
    )

    result = _run(tmp_path, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "CORE_BRIDGE_FORBIDDEN_IMPORT" in report["failure_reasons"]


def test_conditional_bridge_accepts_exact_inventory_symbol(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "from util import create_debug_logger\n",
    )

    result = _run(tmp_path, inventory)

    assert result.returncode == 0, result.stderr


def test_strict_mode_is_required(tmp_path):
    _base_tree(tmp_path)
    inventory = _inventory(tmp_path)

    result = _run(tmp_path, inventory, strict=False)

    assert result.returncode != 0
    assert "STRICT_REQUIRED" in result.stderr
