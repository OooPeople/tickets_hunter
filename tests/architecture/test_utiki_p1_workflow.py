from __future__ import annotations

import importlib.util
import re
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "utiki-p1-gate.yml"
REQUIRED_JOBS = {
    "unit-contract",
    "modified-python-syntax",
    "architecture-types",
    "browser-contract-isolated",
    "performance-contract",
}


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(text: str, job_id: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        text,
    )
    assert match is not None, job_id
    return match.group("body")


def _module(relative: str):
    path = PROJECT_ROOT / relative
    name = "workflow_parser_" + relative.replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_p1_workflow_has_exact_required_jobs_and_minimal_permissions() -> None:
    text = _text()
    jobs = set(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):$", text.split("jobs:\n", 1)[1]))

    assert jobs == REQUIRED_JOBS | {"aggregate"}
    assert re.search(r"(?m)^permissions:\n  contents: read$", text)
    assert "continue-on-error:" not in text
    assert "APPROVED_CATALOG_SHA256: d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1" in text
    assert "PINNED_BASE_IMAGE: ${{ vars.UTIKI_P1_BASE_IMAGE }}" in text


def test_p1_workflow_does_not_use_step_only_runner_context_in_job_env() -> None:
    text = _text()

    # GitHub resolves job-level ``env`` before a runner exists.  A runner
    # context here makes the workflow fail before any job or diagnostic log is
    # created, so keep temporary paths bound to run-scoped GitHub values.
    assert "${{ runner." not in text
    assert "/tmp/utiki-p1-${{ github.run_id }}-${{ github.run_attempt }}" in text


def test_source_jobs_are_fail_closed_and_unit_preserves_full_suites() -> None:
    text = _text()
    for job_id in ("unit-contract", "modified-python-syntax", "architecture-types"):
        body = _job(text, job_id)
        assert "runs-on: ubuntu-24.04" in body
        assert "source-linux-x64" in body
        assert "run_utiki_negative_job.py" in body
        assert "verify_utiki_ci_results.py" in body
        assert "actions/upload-artifact@v7" in body
        assert "if-no-files-found: error" in body
        assert "build_utiki_negative_inputs.py" in body
        assert "--environment host" in body
        assert "PROBE_INPUT_ROOT:" in body
        assert "/evidence/negative" in body.split("PROBE_INPUT_ROOT:", 1)[1].splitlines()[0]

    unit = _job(text, "unit-contract")
    assert "python -m pytest --strict-config --strict-markers" in unit
    assert "python -m unittest discover -v" in unit
    assert unit.index("Run complete strict pytest suite") < unit.index(
        "Run exact catalog-managed unit nodes"
    )
    assert unit.index("Preserve unittest discovery compatibility") < unit.index(
        "Run exact catalog-managed unit nodes"
    )
    architecture = _job(text, "architecture-types")
    assert '--candidate-sha "$CANDIDATE_SHA" --strict' in architecture


def test_isolated_jobs_build_before_network_off_and_use_content_image_id() -> None:
    text = _text()
    for job_id, launcher in (
        ("browser-contract-isolated", "run_utiki_browser_isolation.py"),
        ("performance-contract", "run_utiki_performance_isolation.py"),
    ):
        body = _job(text, job_id)
        assert "runs-on: ubuntu-24.04" in body
        assert "isolated-linux-x64" in body
        assert "build_utiki_isolation_image.py" in body
        assert "Verify digest-pinned base image prerequisite" in body
        assert "UTIKI_P1_BASE_IMAGE to a reviewed repository@sha256 digest" in body
        assert "[^@[:space:]]+@sha256:[0-9a-f]{64}" in body
        assert launcher in body
        assert body.index("Verify digest-pinned base image prerequisite") < body.index(
            "build_utiki_isolation_image.py"
        )
        assert body.index("build_utiki_isolation_image.py") < body.index(launcher)
        assert "ISOLATION_IMAGE_ID=$(jq -r .image_id" in body
        assert '--image "$ISOLATION_IMAGE_ID"' in body
        assert "assemble_utiki_isolated_job.py" in body
        assert "--required-catalog ci/utiki-required-cases.json" in body
        assert "--require-browser" in body
        assert "actions/upload-artifact@v7" in body


def test_aggregate_requires_and_downloads_every_formal_job() -> None:
    body = _job(_text(), "aggregate")

    assert "if: always()" in body
    assert "Require every producer job to succeed" in body
    for job_id in sorted(REQUIRED_JOBS):
        assert f"- {job_id}" in body
        assert f"name: utiki-p1-{job_id}" in body
        assert f"needs.{job_id}.result" in body
    assert body.count("actions/download-artifact@v8") == 5
    assert "build_utiki_run_index.py" in body
    assert "verify_utiki_ci_results.py" in body
    assert body.index("build_utiki_run_index.py") < body.index(
        "verify_utiki_ci_results.py"
    )


def test_browser_workflow_arguments_are_accepted_by_real_parser(tmp_path) -> None:
    runtime = tmp_path / "runtime-denied"
    runtime.mkdir()
    report = tmp_path / "browser-isolation-raw.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_utiki_browser_isolation.py"),
            "--contract",
            str(PROJECT_ROOT / "ci" / "utiki-browser-isolation.json"),
            "--image",
            "sha256:" + "a" * 64,
            "--image-build-evidence",
            str(tmp_path / "image-build.json"),
            "--execution-manifest",
            str(PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "manifest.json"),
            "--run-dir",
            str(tmp_path),
            "--report",
            str(report),
            "--run-id",
            "p1-parser-attempt-1",
            "--attempt",
            "1",
            "--phase",
            "P1",
            "--catalog-sha256",
            "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1",
            "--candidate-sha",
            "b" * 40,
            "--runtime",
            str(runtime),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "unrecognized arguments" not in completed.stderr
    assert report.is_file()


def test_every_checked_in_workflow_tool_exposes_a_real_parser() -> None:
    tools = set(re.findall(r"tools/[A-Za-z0-9_./-]+\.py", _text()))
    assert len(tools) == 17
    for relative in sorted(tools):
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / relative), "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (relative, completed.stderr)


def test_workflow_family_runner_arguments_parse_with_checked_in_contracts(tmp_path) -> None:
    sha = "a" * 40
    digest = "b" * 64
    common = [
        "--repository-root", str(PROJECT_ROOT),
        "--execution-manifest", str(PROJECT_ROOT / "tests/fixtures/utiki/manifest.json"),
        "--run-dir", str(tmp_path / "run"),
        "--run-id", "p1-parser-attempt-1",
        "--attempt", "1",
        "--job-id", "unit-contract",
        "--phase", "P1",
        "--target", "source-linux-x64",
        "--candidate-sha", sha,
        "--catalog-sha256", digest,
    ]
    builder = _module("tools/build_utiki_negative_inputs.py")._parser()
    built = builder.parse_args(
        [
            "--repository-root", str(PROJECT_ROOT),
            "--probe-registry", "ci/utiki-p1-negative-probes.json",
            "--output-root", str(tmp_path / "probes"),
            "--candidate-sha", sha,
            "--environment", "host",
        ]
    )
    assert built.environment == "host"

    negative = _module("tools/run_utiki_negative_job.py")._parser()
    parsed_negative = negative.parse_args(
        [
            "--repository-root", str(PROJECT_ROOT),
            "--required-catalog", "ci/utiki-required-cases.json",
            "--approved-catalog-sha256", digest,
            "--execution-manifest", "tests/fixtures/utiki/manifest.json",
            "--probe-registry", "ci/utiki-p1-negative-probes.json",
            "--probe-input-root", str(tmp_path / "probes"),
            "--run-dir", str(tmp_path / "run"),
            "--run-id", "p1-parser-attempt-1",
            "--attempt", "1",
            "--job-id", "unit-contract",
            "--phase", "P1",
            "--target", "source-linux-x64",
            "--candidate-sha", sha,
            "--catalog-sha256", digest,
        ]
    )
    assert parsed_negative.job_id == "unit-contract"

    positive = _module("tools/run_utiki_evidence.py")._parser()
    parsed_positive = positive.parse_args(
        [
            "--repository-root", str(PROJECT_ROOT),
            "--execution-manifest", "tests/fixtures/utiki/manifest.json",
            "--report", str(tmp_path / "run/evidence/architecture-report.json"),
            "--kind", "architecture-report",
            "--run-id", "p1-parser-attempt-1",
            "--attempt", "1",
            "--job-id", "architecture-types",
            "--phase", "P1",
            "--target", "source-linux-x64",
            "--candidate-sha", sha,
            "--catalog-sha256", digest,
            "--producer", "tools/check_utiki_architecture.py",
            "--producer-report", "stdout",
            "--", "--strict",
        ]
    )
    assert parsed_positive.producer_arguments[-1] == "--strict"

    non_pytest = _module("tools/assemble_utiki_non_pytest_job.py")._parser()
    assert non_pytest.parse_args(common).job_id == "unit-contract"
    isolated = _module("tools/assemble_utiki_isolated_job.py")._parser()
    isolated_arguments = [
        *common,
        "--required-catalog", "ci/utiki-required-cases.json",
        "--junit", str(tmp_path / "browser.xml"),
        "--reporter-result", str(tmp_path / "reporter.json"),
        "--inner-report", str(tmp_path / "inner.json"),
        "--resource", "fixture", str(tmp_path / "fixture.html"),
        "--resource", "script", str(tmp_path / "script.py"),
        "--artifact", "browser-isolation-report", str(tmp_path / "outer.json"),
    ]
    assert isolated.parse_args(isolated_arguments).job_id == "unit-contract"


def test_every_job_specific_workflow_command_accepts_its_declared_options(
    tmp_path,
) -> None:
    sha = "a" * 40
    digest = "b" * 64
    run_id = "p1-parser-attempt-1"
    manifest = PROJECT_ROOT / "tests/fixtures/utiki/manifest.json"
    context = tmp_path / "run-context.json"
    context.write_text("{}\n", encoding="utf-8")

    syntax = _module("tools/verify_modified_python.py")._parser().parse_args(
        [
            "--base-ref", sha,
            "--candidate-ref", sha,
            "--run-metadata", str(context),
            "--manifest", str(tmp_path / "modified-python.json"),
            "--report", str(tmp_path / "syntax.json"),
            "--cache-dir", str(tmp_path / "pyc"),
        ]
    )
    assert syntax.report == tmp_path / "syntax.json"
    frozen_syntax = _module("tools/verify_modified_python.py")._parser().parse_args(
        [
            "--base-ref", sha,
            "--candidate-ref", sha,
            "--run-metadata", str(context),
            "--write-manifest", str(tmp_path / "modified-python.json"),
        ]
    )
    assert frozen_syntax.write_manifest == tmp_path / "modified-python.json"
    architecture = _module("tools/check_utiki_architecture.py")._parser().parse_args(
        [
            "--root", str(PROJECT_ROOT),
            "--core-inventory",
            str(PROJECT_ROOT / "docs/02-development/utiki-refactor/core-integration-inventory.md"),
            "--candidate-sha", sha,
            "--strict",
        ]
    )
    assert architecture.strict is True
    assert architecture.candidate_sha == sha
    typed = _module("tools/run_utiki_typecheck.py")._parser().parse_args(
        [
            "--root", str(PROJECT_ROOT / "tests/fixtures/utiki/typecheck-p1"),
            "--manifest", "manifest.txt",
            "--config", "mypy.ini",
            "--coverage-context", "context.json",
            "--report", str(tmp_path / "typecheck.json"),
        ]
    )
    assert typed.coverage_context == Path("context.json")
    pytest_runner = _module("tools/run_utiki_pytest_job.py")._parser().parse_args(
        [
            "--repository-root", str(PROJECT_ROOT),
            "--execution-manifest", str(manifest),
            "--run-dir", str(tmp_path / "managed"),
            "--run-id", run_id,
            "--attempt", "1",
            "--job-id", "unit-contract",
            "--phase", "P1",
            "--target", "source-linux-x64",
            "--candidate-sha", sha,
            "--catalog-sha256", digest,
            "--artifact", "checker-exit-nonzero", str(tmp_path / "negative.json"),
        ]
    )
    assert pytest_runner.job_id == "unit-contract"
    image = _module("tools/build_utiki_isolation_image.py")._parser().parse_args(
        [
            "--repository-root", str(PROJECT_ROOT),
            "--base-image", "python@sha256:" + "c" * 64,
            "--tag", "utiki-p1:test",
            "--evidence", str(tmp_path / "image-build.json"),
        ]
    )
    assert image.tag == "utiki-p1:test"
    index = _module("tools/build_utiki_run_index.py")._parser().parse_args(
        [
            "--required-catalog", "ci/utiki-required-cases.json",
            "--approved-catalog-sha256", digest,
            "--execution-manifest", str(manifest),
            "--phase", "P1",
            "--run-id", run_id,
            "--attempt", "1",
            "--candidate-sha", sha,
            "--output", str(tmp_path / "gate-runs.json"),
            "--run", "unit-contract", "source-linux-x64", str(tmp_path / "unit"),
        ]
    )
    assert index.run[0][0] == "unit-contract"
    checker = _module("tools/verify_utiki_ci_results.py").build_parser().parse_args(
        [
            "--required-catalog", "ci/utiki-required-cases.json",
            "--approved-catalog-sha256", digest,
            "--execution-manifest", str(manifest),
            "--run-index", str(tmp_path / "gate-runs.json"),
            "--phase", "P1",
            "--run-id", run_id,
            "--candidate-sha", sha,
            "--report", str(tmp_path / "gate-report.json"),
        ]
    )
    assert checker.run_index == tmp_path / "gate-runs.json"


def test_main_local_parsers_accept_exact_browser_performance_and_manifest_argv(
    tmp_path,
) -> None:
    sha = "a" * 40
    digest = "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1"
    manifest = PROJECT_ROOT / "tests/fixtures/utiki/manifest.json"
    commands = [
        [
            sys.executable,
            str(PROJECT_ROOT / "tools/generate_utiki_execution_manifest.py"),
            "--root", str(PROJECT_ROOT),
            "--required-catalog", str(PROJECT_ROOT / "ci/utiki-required-cases.json"),
            "--approved-catalog-sha256", digest,
            "--binding-registry", str(PROJECT_ROOT / "ci/utiki-p1-execution-bindings.json"),
            "--output", str(manifest),
            "--check",
        ],
        [
            sys.executable,
            str(PROJECT_ROOT / "tools/run_utiki_performance_isolation.py"),
            "--image", "sha256:" + "c" * 64,
            "--image-build-evidence", str(tmp_path / "image-build.json"),
            "--execution-manifest", str(manifest),
            "--run-dir", str(tmp_path / "performance"),
            "--report", str(tmp_path / "performance/performance-isolation-raw.json"),
            "--run-id", "p1-parser-attempt-1",
            "--attempt", "1",
            "--phase", "P1",
            "--catalog-sha256", digest,
            "--candidate-sha", sha,
            "--runtime", str(tmp_path / "missing-runtime"),
        ],
        [
            sys.executable,
            str(PROJECT_ROOT / "tools/benchmarks/verify_utiki_p1_performance.py"),
            "--contract", str(PROJECT_ROOT / "ci/utiki-performance-isolation.json"),
            "--isolation-report", str(tmp_path / "isolation.json"),
            "--raw-benchmark-report", str(tmp_path / "raw.json"),
            "--junit", str(tmp_path / "performance.xml"),
            "--reporter-result", str(tmp_path / "reporter.json"),
            "--output", str(tmp_path / "verification.json"),
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert "unrecognized arguments" not in completed.stderr
        assert "the following arguments are required" not in completed.stderr
