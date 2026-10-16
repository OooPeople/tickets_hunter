import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT_ROOT / "tools" / "run_utiki_typecheck.py"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(tmp_path):
    root = tmp_path / "repo"
    files = [
        "src/platforms/utiki/auth.py",
        "src/platforms/utiki/profiles.py",
        "src/platforms/utiki/selection.py",
    ]
    for path in files:
        _write(root / path, "VALUE: int = 1\n")
    manifest = root / "ci" / "utiki-typed-scope.txt"
    _write(
        manifest,
        "\n".join(files)
        + "\n# INCLUDE_WHEN_PRESENT: src/platforms/utiki/domain/**/*.py\n",
    )
    config = root / "ci" / "mypy-utiki.ini"
    _write(
        config,
        """[mypy]
python_version = 3.11
strict = True
warn_unused_configs = True
mypy_path = src
""",
    )
    fake = tmp_path / "fake-mypy"
    _write(
        fake,
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_MYPY_LOG"]).write_text(json.dumps(sys.argv[1:]))
raise SystemExit(int(os.environ.get("FAKE_MYPY_EXIT", "0")))
""",
    )
    fake.chmod(0o755)
    return root, manifest, config, fake, files


def _run(root, manifest, config, mypy, tmp_path, *, fake_exit="0"):
    report = tmp_path / "type-report.json"
    log = tmp_path / "mypy-args.json"
    environment = os.environ.copy()
    environment["FAKE_MYPY_LOG"] = str(log)
    environment["FAKE_MYPY_EXIT"] = fake_exit
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
            "--config",
            str(config),
            "--mypy",
            str(mypy),
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    return result, report, log


def test_runner_invokes_strict_mypy_with_every_manifest_file(tmp_path):
    root, manifest, config, fake, files = _fixture(tmp_path)

    result, report_path, log = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    arguments = json.loads(log.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["files"] == files
    assert "--strict" in arguments
    assert "--no-incremental" in arguments
    for path in files:
        assert path in arguments


def test_new_domain_file_omitted_from_manifest_fails_before_mypy(tmp_path):
    root, manifest, config, fake, _ = _fixture(tmp_path)
    _write(
        root / "src" / "platforms" / "utiki" / "domain" / "state.py",
        "VALUE: int = 1\n",
    )

    result, report_path, log = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "TYPED_SCOPE_MISSING" in report["failure_reasons"]
    assert not log.exists()


def test_existing_p1_seed_cannot_be_removed_from_manifest(tmp_path):
    root, manifest, config, fake, files = _fixture(tmp_path)
    manifest.write_text("\n".join(files[1:]) + "\n", encoding="utf-8")

    result, report_path, log = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "TYPED_SCOPE_MISSING" in report["failure_reasons"]
    assert files[0] in report["affected_paths"]
    assert not log.exists()


def test_nonexistent_manifest_entry_fails_closed(tmp_path):
    root, manifest, config, fake, files = _fixture(tmp_path)
    manifest.write_text(
        "\n".join([*files, "src/platforms/utiki/domain/missing.py"]) + "\n",
        encoding="utf-8",
    )

    result, report_path, _ = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "MANIFEST_PATH_MISSING" in report["failure_reasons"]


def test_broad_mypy_escape_hatch_is_rejected(tmp_path):
    root, manifest, config, fake, _ = _fixture(tmp_path)
    config.write_text(
        "[mypy]\npython_version = 3.11\nstrict = True\nignore_errors = True\n",
        encoding="utf-8",
    )

    result, report_path, _ = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "UNSAFE_MYPY_CONFIG" in report["failure_reasons"]


def test_global_follow_imports_skip_is_rejected(tmp_path):
    root, manifest, config, fake, _ = _fixture(tmp_path)
    config.write_text(
        "[mypy]\npython_version = 3.11\nstrict = True\nfollow_imports = skip\n",
        encoding="utf-8",
    )

    result, report_path, _ = _run(
        root, manifest, config, fake, tmp_path
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "UNSAFE_MYPY_CONFIG" in report["failure_reasons"]


def test_mypy_failure_is_not_converted_to_pass(tmp_path):
    root, manifest, config, fake, _ = _fixture(tmp_path)

    result, report_path, log = _run(
        root, manifest, config, fake, tmp_path, fake_exit="1"
    )

    assert result.returncode != 0
    assert log.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["mypy_exit_code"] == 1
    assert "MYPY_FAILED" in report["failure_reasons"]


def test_missing_mypy_is_blocked_not_passed(tmp_path):
    root, manifest, config, _, _ = _fixture(tmp_path)
    missing = tmp_path / "does-not-exist" / "mypy"

    result, report_path, log = _run(
        root, manifest, config, missing, tmp_path
    )

    assert result.returncode != 0
    assert not log.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "BLOCKED"
    assert "MYPY_NOT_AVAILABLE" in report["failure_reasons"]
    assert len(report["config_sha256"]) == 64
    assert len(report["manifest_sha256"]) == 64
    assert report["effective_flags"] == ["--strict", "--no-incremental"]


@pytest.mark.parametrize(
    "context_text",
    [
        '{"schema_version":1,"schema_version":1}\n',
        json.dumps(
            {
                "schema_version": 1,
                "coverage_kind": "production-scope",
                "purpose": "misleading fixture",
                "production_scope_status": "PASS",
                "legacy_seed_scope_status": "PASS",
            }
        ),
    ],
)
def test_controlled_coverage_context_is_strict_and_fail_closed(
    tmp_path, context_text
):
    root, manifest, config, fake, _ = _fixture(tmp_path)
    context = root / "context.json"
    context.write_text(context_text, encoding="utf-8")
    report = tmp_path / "context-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
            "--config",
            str(config),
            "--coverage-context",
            str(context),
            "--mypy",
            str(fake),
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert "COVERAGE_CONTEXT_INVALID" in payload["failure_reasons"]
