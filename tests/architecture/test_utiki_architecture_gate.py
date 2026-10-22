import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "tools" / "check_utiki_architecture.py"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
    _write(root / "src" / "platforms" / "utiki" / "__init__.py", "")
    _write(root / "src" / "util.py", "VALUE = 1\n")
    _write(root / "src" / "refresh_coordinator.py", "VALUE = 1\n")


def _run(root, *, strict=True):
    command = [
        sys.executable,
        str(CHECKER),
        "--root",
        str(root),
        "--candidate-sha",
        "1" * 40,
    ]
    if strict:
        command.append("--strict")
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_small_current_tree_passes_without_planned_target_paths(tmp_path):
    _base_tree(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["target_files"] == []


def test_retired_side_effect_modules_cannot_reenter_the_tree(tmp_path):
    _base_tree(tmp_path)
    _write(tmp_path / "src" / "platforms" / "utiki" / "workflow.py", "")

    result = _run(tmp_path)

    assert result.returncode != 0
    assert "RETIRED_SIDE_EFFECT_MODULE" in json.loads(result.stdout)["failure_reasons"]


def test_application_cannot_call_raw_evaluate(tmp_path):
    _base_tree(tmp_path)
    _write(
        tmp_path
        / "src"
        / "platforms"
        / "utiki"
        / "application"
        / "engine.py",
        "async def run(tab: object) -> None:\n    await tab.evaluate('x')\n",
    )

    result = _run(tmp_path)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "APPLICATION_BROWSER_PRIMITIVE" in report["failure_reasons"]


def test_a02_application_evaluate_and_reverse_import_reach_architecture_gate(tmp_path):
    _base_tree(tmp_path)
    application = (
        tmp_path / "src" / "platforms" / "utiki" / "application" / "engine.py"
    )
    _write(application.parent / "adapters.py", "VALUE = 2\n")
    _write(
        application,
        "from ..profiles import VALUE\nfrom .adapters import VALUE as LOCAL\nRESULT = VALUE + LOCAL\n",
    )
    legal = _run(tmp_path)
    assert legal.returncode == 0, legal.stderr
    assert json.loads(legal.stdout)["status"] == "PASS"

    injections = (
        ("async def run(tab):\n    await tab.evaluate('x')\n", "APPLICATION_BROWSER_PRIMITIVE"),
        ("from ..adapters.browser import BrowserPort\n", "APPLICATION_REVERSE_IMPORT"),
        ("from ...utiki.adapters import BrowserPort\n", "APPLICATION_REVERSE_IMPORT"),
        ("from platforms.utiki.adapters.browser import BrowserPort\n", "APPLICATION_REVERSE_IMPORT"),
        ("from .. import bootstrap\n", "APPLICATION_REVERSE_IMPORT"),
        ("from platforms.utiki import bootstrap\n", "APPLICATION_REVERSE_IMPORT"),
    )
    for source, reason in injections:
        _write(application, source)
        rejected = _run(tmp_path)
        assert rejected.returncode != 0
        report = json.loads(rejected.stdout)
        assert reason in report["failure_reasons"]
        assert any(
            finding["path"] == "src/platforms/utiki/application/engine.py"
            and finding["reason"] == reason
            for finding in report["findings"]
        )


def test_domain_rejects_io_and_reverse_imports(tmp_path):
    _base_tree(tmp_path)
    _write(
        tmp_path / "src" / "platforms" / "utiki" / "domain" / "state.py",
        "import os\nimport settings\nVALUE = os.getenv('X')\n",
    )

    result = _run(tmp_path)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "DOMAIN_FORBIDDEN_IMPORT" in report["failure_reasons"]
    assert "DOMAIN_FORBIDDEN_CALL" in report["failure_reasons"]


def test_conditional_bridge_only_uses_approved_core(tmp_path):
    _base_tree(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "import settings\n",
    )

    result = _run(tmp_path)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "CORE_BRIDGE_FORBIDDEN_IMPORT" in report["failure_reasons"]


def test_conditional_bridge_cannot_import_whole_approved_module(tmp_path):
    _base_tree(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "import util\n",
    )

    result = _run(tmp_path)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "CORE_BRIDGE_FORBIDDEN_IMPORT" in report["failure_reasons"]


def test_conditional_bridge_accepts_exact_core_symbol(tmp_path):
    _base_tree(tmp_path)
    _write(
        tmp_path / "src" / "utiki_core_services.py",
        "from util import create_debug_logger\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_strict_mode_is_required(tmp_path):
    _base_tree(tmp_path)

    result = _run(tmp_path, strict=False)

    assert result.returncode != 0
    assert "STRICT_REQUIRED" in result.stderr
