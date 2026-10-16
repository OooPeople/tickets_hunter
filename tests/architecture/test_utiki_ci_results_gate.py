import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "tools" / "verify_utiki_ci_results.py"
EVIDENCE_LAUNCHER = PROJECT_ROOT / "tools" / "run_utiki_evidence.py"
CATALOG = PROJECT_ROOT / "ci" / "utiki-required-cases.json"
NEGATIVE_REGISTRY = PROJECT_ROOT / "ci" / "utiki-p1-negative-probes.json"
APPROVED_CATALOG_SHA256 = "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1"
PHASE = "P1"
RUN_ID = "utiki-p1-attempt-1"
CANDIDATE_SHA = "1" * 40


def _expand_probe_value(root: Path, value: str) -> str:
    if value == "{PROBE_ROOT}":
        return str(root.resolve())
    prefix = "{PROBE_ROOT}/"
    if value.startswith(prefix):
        return str((root / value[len(prefix) :]).resolve())
    return value


def _canonical_negative_fixture(
    destination: Path,
    registry_entry: dict[str, object],
    child_stdout: Path,
    child_stderr: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    if not (destination / "probe.json").is_file():
        if destination.exists():
            raise AssertionError(f"incomplete canonical fixture destination: {destination}")
        _internal_negative_request(destination, registry_entry)
    request_path = destination / "probe.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    child = request["child"]
    semantic_output = None
    if child["producer"] == "tools/run_utiki_typecheck.py":
        affected = {
            "new-domain-file": "src/platforms/utiki/domain/state.py",
            "new-application-file": "src/platforms/utiki/application/engine.py",
            "new-port-file": "src/platforms/utiki/application/ports.py",
            "new-adapter-file": "src/platforms/utiki/adapters/browser.py",
            "new-bootstrap-file": "src/platforms/utiki/bootstrap.py",
            "new-current-config-file": "src/utiki_config.py",
            "new-keyword-file": "src/keyword_rules.py",
            "new-core-bridge-file": "src/utiki_core_services.py",
            "new-upgrade-tool-file": "tools/migrations/convert_utiki_config.py",
        }.get(request["variant_id"])
        typecheck_report = destination / "typecheck-report.json"
        _write_json(
            typecheck_report,
            {
                "schema_version": 1,
                "status": "FAIL",
                "failure_reasons": [
                    "TYPED_SCOPE_MISSING" if affected else "UNSAFE_MYPY_CONFIG"
                ],
                "affected_paths": [affected] if affected else [],
                "mypy_exit_code": None,
            },
        )
        semantic_output = {
            "path": typecheck_report.relative_to(destination).as_posix(),
            "sha256": _sha256(typecheck_report),
        }
    elif child["producer"] == "tools/verify_modified_python.py" and "--report" in child["arguments"]:
        syntax_report = destination / "syntax-report.json"
        subject = request["inputs"][0]["path"].removeprefix("repo/")
        reason = request["rejection_token"]
        _write_json(
            syntax_report,
            {
                "schema_version": 1,
                "overall_status": "FAIL",
                "exit_code": 1,
                "failure_reasons": [reason],
                "manifest_sha256": _sha256(destination / "modified-python.json"),
                "files": (
                    [{"path": subject, "reason": reason, "status": "FAIL"}]
                    if reason == "SYNTAX_ERROR"
                    else []
                ),
            },
        )
        semantic_output = {
            "path": syntax_report.relative_to(destination).as_posix(),
            "sha256": _sha256(syntax_report),
        }
    if child["kind"] == "repository-python":
        producer_path = PROJECT_ROOT / child["producer"]
        producer_digest = _sha256(producer_path)
    else:
        producer_digest = hashlib.sha256(
            b"python-isolated-module:pytest"
        ).hexdigest()
    raw = {
        "schema_version": 2,
        "report_kind": "utiki-negative-probe-transcript",
        "key": {
            field: request[field]
            for field in ("case_id", "obligation_id", "variant_id", "job_id", "target")
        },
        "candidate_sha": request["candidate_sha"],
        "environment": request["environment"],
        "mutation": request["mutation"],
        "request_sha256": _sha256(request_path),
        "registry_sha256": _sha256(NEGATIVE_REGISTRY),
        "child": {
            "kind": child["kind"],
            "producer": child["producer"],
            "producer_sha256": producer_digest,
        },
        "stdout_sha256": _sha256(child_stdout),
        "stderr_sha256": _sha256(child_stderr),
        "semantic_output": semantic_output,
        "observed_exit_code": 1,
        "rejection_token": request["rejection_token"],
        "isolation_proof": None,
        "status": "EXPECTED_REJECTION",
    }
    return request_path, request, raw


def _internal_negative_request(
    root: Path, entry: dict[str, object]
) -> None:
    """Build contract-valid support evidence without recursively invoking the builder."""

    from tools import build_utiki_negative_inputs as builder

    root.mkdir(parents=True, exist_ok=True)
    if entry["child_producer"] == "pytest":
        builder._pytest_probe(root, entry, CANDIDATE_SHA)
        return
    if entry["child_producer"] == "tools/run_utiki_typecheck.py":
        builder._typecheck_probe(root, entry, CANDIDATE_SHA)
        return
    if entry["child_producer"] == "tools/verify_modified_python.py":
        builder._syntax_probe(PROJECT_ROOT, root, entry, CANDIDATE_SHA)
        return
    if entry["environment"] == "isolated":
        builder._isolated_probe(PROJECT_ROOT, root, entry, CANDIDATE_SHA)
        return
    if entry["child_producer"] != "tools/verify_utiki_ci_results.py":
        raise AssertionError(f"unsupported internal negative fixture: {entry}")
    variant = str(entry["variant_id"])
    common_arguments: list[str | Path] = [
        "--required-catalog",
        PROJECT_ROOT / "ci/utiki-required-cases.json",
        "--approved-catalog-sha256",
        APPROVED_CATALOG_SHA256,
        "--execution-manifest",
        root / "execution-manifest.json",
    ]
    if variant == "missing-job-or-target":
        primary = root / "case" / "run-index.json"
        _write_json(primary, {"runs": [{}]})
        arguments = [
            *common_arguments,
            "--run-index",
            primary,
            "--phase",
            "P1",
            "--run-id",
            RUN_ID,
            "--candidate-sha",
            CANDIDATE_SHA,
        ]
    else:
        arguments = [
            *common_arguments,
            "--junit",
            root / "junit.xml",
            "--run-metadata",
            root / "run-metadata.json",
            "--phase",
            "P1",
            "--run-id",
            RUN_ID,
            "--job-id",
            "architecture-types",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            CANDIDATE_SHA,
        ]
        own_execution = {
            "obligation_id": entry["obligation_id"],
            "variant_id": entry["variant_id"],
            "job_id": "architecture-types",
            "target": "source-linux-x64",
            "status": "MAPPED",
        }
        manifest = {"cases": [{"id": entry["case_id"], "executions": [own_execution]}]}
        _write_json(root / "execution-manifest.json", manifest)
        _write_json(root / "run-metadata.json", {"collected_count": 2, "selected_count": 2})
        suite = ElementTree.Element("testsuite")
        for index in range(2):
            testcase = ElementTree.SubElement(
                suite, "testcase", classname="contract", name=f"test_{index}"
            )
            _properties(testcase, {"outcome": "passed"})
        tree = ElementTree.ElementTree(suite)
        tree.write(root / "junit.xml", encoding="utf-8", xml_declaration=True)
        if variant in {
            "test-and-mapping-removed",
            "removed-test-and-mapping",
            "missing-obligation-variant",
        }:
            manifest["cases"][0]["executions"] = []
            primary = root / "execution-manifest.json"
            _write_json(primary, manifest)
        elif variant == "duplicate-or-unknown-id":
            manifest["cases"][0]["executions"].append(copy.deepcopy(own_execution))
            primary = root / "execution-manifest.json"
            _write_json(primary, manifest)
        elif variant == "forged-not-applicable":
            own_execution["status"] = "NOT_APPLICABLE"
            primary = root / "execution-manifest.json"
            _write_json(primary, manifest)
        elif variant == "catalog-unapproved-change":
            primary = root / "changed-catalog.json"
            _write_json(primary, {"cases": [{"scenario": "controlled mutated"}]})
            arguments[1] = primary
        elif variant in {"zero-collected", "stale-run-or-sha", "failure-exit-overwritten"}:
            primary = root / "run-metadata.json"
            metadata = json.loads(primary.read_text(encoding="utf-8"))
            if variant == "zero-collected":
                metadata.update(collected_count=0, selected_count=0)
            elif variant == "stale-run-or-sha":
                metadata["candidate_sha"] = "2" * 40
            else:
                metadata["exit_codes"] = {"test": 7}
            _write_json(primary, metadata)
        else:
            primary = root / "junit.xml"
            if variant in {"empty-or-missing-result"}:
                for node in list(suite.findall("testcase")):
                    suite.remove(node)
            elif variant == "duplicate-result-or-wrong-layer":
                suite.append(copy.deepcopy(suite.find("testcase")))
            elif variant in {
                "all-skipped",
                "required-outcome-skip",
                "required-outcome-xfail",
                "required-outcome-strict-xpass",
                "required-outcome-non-strict-xpass",
                "required-outcome-setup-error",
                "required-outcome-teardown-error",
            }:
                selected = (
                    suite.findall("testcase")
                    if variant == "all-skipped"
                    else suite.findall("testcase")[:1]
                )
                property_name, property_value, child_name = {
                    "all-skipped": ("outcome", "skipped", "skipped"),
                    "required-outcome-skip": ("outcome", "skipped", "skipped"),
                    "required-outcome-xfail": ("wasxfail", "true", "skipped"),
                    "required-outcome-strict-xpass": ("wasxfail", "true", "failure"),
                    "required-outcome-non-strict-xpass": ("wasxfail", "true", None),
                    "required-outcome-setup-error": ("setup_outcome", "error", "error"),
                    "required-outcome-teardown-error": ("teardown_outcome", "error", "error"),
                }[variant]
                for testcase in selected:
                    properties = testcase.find("properties")
                    assert properties is not None
                    existing = next(
                        (
                            item
                            for item in properties.findall("property")
                            if item.get("name") == property_name
                        ),
                        None,
                    )
                    if existing is None:
                        ElementTree.SubElement(
                            properties,
                            "property",
                            name=property_name,
                            value=property_value,
                        )
                    else:
                        existing.set("value", property_value)
                    if child_name:
                        ElementTree.SubElement(testcase, child_name)
            tree.write(primary, encoding="utf-8", xml_declaration=True)
            if variant == "digest-mismatch":
                primary.write_bytes(primary.read_bytes() + b"\nmutated\n")
    builder._request(
        root,
        entry,
        CANDIDATE_SHA,
        arguments=arguments,
        input_path=primary,
    )


def test_negative_fixture_builds_only_the_requested_case(tmp_path, monkeypatch):
    registry = json.loads(NEGATIVE_REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item for item in registry["probes"] if item["variant_id"] == "unknown-marker"
    )
    child_stdout = tmp_path / "child.stdout"
    child_stderr = tmp_path / "child.stderr"
    child_stdout.write_text("controlled rejection\n", encoding="utf-8")
    child_stderr.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("fixture launched the full probe builder"),
    )

    request_path, request, _ = _canonical_negative_fixture(
        tmp_path / "probe", entry, child_stdout, child_stderr
    )

    assert request_path.is_file()
    assert request["obligation_id"] == entry["obligation_id"]


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _architecture_payload():
    paths = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src" / "platforms" / "utiki").rglob("*.py")
    }
    for relative in {
        "src/platforms/utiki/bootstrap.py",
        "src/utiki_config.py",
        "src/keyword_rules.py",
        "src/utiki_core_services.py",
        "src/nodriver_common.py",
        "src/settings.py",
    }:
        if (PROJECT_ROOT / relative).exists():
            paths.add(relative)
    checked = sorted(paths)
    target_roots = (
        "src/platforms/utiki/domain/",
        "src/platforms/utiki/application/",
        "src/platforms/utiki/adapters/",
    )
    target_singles = {
        "src/platforms/utiki/bootstrap.py",
        "src/utiki_config.py",
        "src/keyword_rules.py",
        "src/utiki_core_services.py",
    }
    source_manifest = [
        {"path": relative, "sha256": _sha256(PROJECT_ROOT / relative)}
        for relative in checked
    ]
    return {
        "schema_version": 1,
        "status": "PASS",
        "baseline_sha": "f307f7caba054fb40aab807a4629c891e69fb200",
        "candidate_sha": CANDIDATE_SHA,
        "repository_root": str(PROJECT_ROOT.resolve()),
        "checked_files": checked,
        "checked_files_sha256": _canonical_digest(checked),
        "target_files": [
            path
            for path in checked
            if path in target_singles or path.startswith(target_roots)
        ],
        "source_manifest": source_manifest,
        "source_manifest_sha256": _canonical_digest(source_manifest),
        "findings": [],
        "failure_reasons": [],
    }


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _run_checker(*arguments):
    return subprocess.run(
        [sys.executable, str(CHECKER), *map(str, arguments)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _profiles(catalog):
    return {
        phase: {
            item["job_id"]: {
                "targets": tuple(item["targets"]),
                "layers": tuple(item["layers"]),
            }
            for item in profile["required_jobs"]
        }
        for phase, profile in catalog["gate_profiles"].items()
    }


def _all_targets(obligation, profiles):
    return sorted(
        {
            target
            for phase in obligation["required_gates"]
            for target in profiles[phase][obligation["job_id"]]["targets"]
        }
    )


def _expected_status(obligation, target, profiles):
    if PHASE not in obligation["required_gates"]:
        return "NOT_DUE"
    job = profiles[PHASE].get(obligation["job_id"])
    if job is None or target not in job["targets"]:
        return "NOT_DUE"
    for condition in obligation["applies_when"]["all"]:
        if condition["field"] == "target" and target not in condition["one_of"]:
            return "NOT_APPLICABLE"
        if condition["field"] != "target":
            raise AssertionError("a due P1 condition needs a trusted test derivation")
    return "MAPPED"


def _safe_node(case_id, obligation_id, variant_id, target):
    stem = re.sub(r"[^A-Za-z0-9_]", "_", f"{case_id}_{obligation_id}_{variant_id}_{target}")
    return f"tests/synthetic/test_required.py::test_{stem}"


def _make_manifest(root, *, compact=False):
    catalog = _catalog()
    profiles = _profiles(catalog)
    cases = []
    compact_evidence_paths = {}
    for case in catalog["cases"]:
        executions = []
        for obligation in case["obligations"]:
            for variant in obligation["variants"]:
                for target in _all_targets(obligation, profiles):
                    status = _expected_status(obligation, target, profiles)
                    node_id = None
                    evidence = []
                    if status == "MAPPED":
                        if "junit" in obligation["required_evidence"]:
                            node_id = _safe_node(case["id"], obligation["obligation_id"], variant, target)
                        evidence = []
                        for kind in obligation["required_evidence"]:
                            if kind == "junit":
                                continue
                            if kind == "checker-exit-nonzero":
                                suffix = re.sub(
                                    r"[^A-Za-z0-9_.-]",
                                    "_",
                                    f"{case['id']}--{obligation['obligation_id']}--{variant}",
                                )
                                evidence_path = f"evidence/{kind}--{suffix}.json"
                            else:
                                evidence_path = f"evidence/{kind}.json"
                            if compact:
                                evidence_key = (kind, evidence_path)
                                if evidence_key not in compact_evidence_paths:
                                    compact_evidence_paths[evidence_key] = (
                                        f"e/a{len(compact_evidence_paths):03d}.json"
                                    )
                                evidence_path = compact_evidence_paths[evidence_key]
                            evidence.append({"kind": kind, "path": evidence_path})
                    executions.append(
                        {
                            "obligation_id": obligation["obligation_id"],
                            "variant_id": variant,
                            "job_id": obligation["job_id"],
                            "target": target,
                            "layer": obligation["layer"],
                            "status": status,
                            "node_id": node_id,
                            "evidence": evidence,
                        }
                    )
        cases.append({"id": case["id"], "executions": executions})
    manifest = {
        "schema_version": 1,
        "catalog_id": "utiki-required-cases",
        "catalog_revision": "1.5.0",
        "catalog_sha256": APPROVED_CATALOG_SHA256,
        "phase": PHASE,
        "cases": cases,
    }
    path = root / "execution-manifest.json"
    _write_json(path, manifest)
    return path, manifest, catalog, profiles


def _properties(parent, values):
    properties = ElementTree.SubElement(parent, "properties")
    for name, value in values.items():
        ElementTree.SubElement(properties, "property", name=name, value=str(value))


def _current_mappings(manifest, job_id, target):
    return [
        (case["id"], execution)
        for case in manifest["cases"]
        for execution in case["executions"]
        if execution["status"] == "MAPPED"
        and execution["job_id"] == job_id
        and execution["target"] == target
    ]


def _source_performance_metric(sample_count):
    samples = [1] * sample_count
    return {
        "unit": "ns",
        "sample_count": sample_count,
        "samples_ns": samples,
        "failure_count": 0,
        "failures": [],
        "batch_sizes": [],
        "median_ns": 1,
        "p95_ns": 1,
        "p99_ns": 1,
        "min_ns": 1,
        "max_ns": 1,
    }


def _browser_performance_metric(unit, samples):
    return {
        "unit": unit,
        "sample_count": len(samples),
        "samples": samples,
        "failure_count": 0,
        "failures": [],
        "median": samples[0],
        "p95": samples[0],
        "p99": samples[0],
        "min": samples[0],
        "max": samples[0],
    }


def _make_run(
    root,
    manifest_path,
    manifest,
    profiles,
    job_id,
    target,
    *,
    run_dir_name=None,
    compact_support=False,
):
    del compact_support
    run_dir = root / (run_dir_name or f"{job_id}-{target}")
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    mappings = _current_mappings(manifest, job_id, target)
    evidence_refs = sorted(
        {
            (evidence["kind"], evidence["path"])
            for _, mapping in mappings
            for evidence in mapping["evidence"]
        },
        key=lambda item: (
            {"browser-isolation-report": 0, "benchmark-report": 1}.get(
                item[0], 2
            ),
            item,
        ),
    )
    artifacts = []
    for kind, artifact_relative in evidence_refs:
        path = run_dir / artifact_relative
        path.parent.mkdir(parents=True, exist_ok=True)
        support_stem = path.stem
        producer_by_kind = {
            "architecture-report": "tools/check_utiki_architecture.py",
            "typecheck-report": "tools/run_utiki_typecheck.py",
            "python-syntax-report": "tools/verify_modified_python.py",
            "browser-isolation-report": "tools/run_utiki_browser_isolation.py",
            "production-origin-report": "tools/run_utiki_browser_isolation.py",
            "benchmark-report": "tools/benchmarks/compare_utiki.py",
            "checker-exit-nonzero": "tools/run_utiki_negative_probe.py",
        }
        if job_id == "performance-contract":
            producer_by_kind.update(
                {
                    "benchmark-report": "tools/benchmarks/verify_utiki_p1_performance.py",
                    "browser-isolation-report": "tools/run_utiki_performance_isolation.py",
                }
            )
        producer = PROJECT_ROOT / producer_by_kind[kind]
        launcher = PROJECT_ROOT / "tools" / "run_utiki_evidence.py"
        input_path = path.parent / f"{support_stem}.input"
        stdout_path = path.parent / f"{support_stem}.stdout"
        stderr_path = path.parent / f"{support_stem}.stderr"
        input_path.write_text("controlled input\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        outputs = []
        extra_inputs = []
        extra_outputs = []
        if kind == "checker-exit-nonzero":
            negative_result = next(
                {
                    "case_id": case_id,
                    "obligation_id": mapping["obligation_id"],
                    "variant_id": mapping["variant_id"],
                    "job_id": job_id,
                    "target": target,
                }
                for case_id, mapping in mappings
                if any(
                    evidence["kind"] == kind
                    and evidence["path"] == artifact_relative
                    for evidence in mapping["evidence"]
                )
            )
            negative_registry = json.loads(NEGATIVE_REGISTRY.read_text(encoding="utf-8"))
            registry_entry = next(
                entry
                for entry in negative_registry["probes"]
                if all(entry[name] == negative_result[name] for name in negative_result)
            )
            rejection_token = registry_entry["rejection_token"]
            pytest_rejections = {
                "unknown-marker": (
                    2,
                    "'utiki_unknown_marker' not found in `markers` configuration option\n",
                ),
                "empty-addopts": (
                    2,
                    "'utiki_unknown_marker' not found in `markers` configuration option\n",
                ),
                "invalid-pytest-config": (
                    4,
                    "ERROR: Unknown config option: utiki_unknown_config\n",
                ),
            }
            producer_name = registry_entry["child_producer"]
            observed_exit, captured_output = pytest_rejections.get(
                registry_entry["variant_id"], (1, rejection_token + "\n")
            )
            child_error = ""
            if producer_name == "tools/verify_utiki_ci_results.py":
                synthetic_code = (
                    "RUN_INDEX_SET_MISMATCH"
                    if registry_entry["variant_id"] == "missing-job-or-target"
                    else rejection_token
                )
                variant = registry_entry["variant_id"]
                synthetic_subject = (
                    registry_entry["obligation_id"]
                    if variant in {
                        "test-and-mapping-removed",
                        "removed-test-and-mapping",
                        "duplicate-or-unknown-id",
                        "missing-obligation-variant",
                        "forged-not-applicable",
                    }
                    else {
                        "zero-collected": "JUnit lacks",
                        "empty-or-missing-result": "JUnit lacks",
                        "catalog-unapproved-change": "approval record",
                        "stale-run-or-sha": "candidate SHA",
                        "digest-mismatch": "JUnit digest",
                        "duplicate-result-or-wrong-layer": "duplicate JUnit execution result",
                        "missing-job-or-target": "run index job/target",
                        "failure-exit-overwritten": "original test exit code",
                    }.get(variant, "testcase[0]")
                )
                captured_output = json.dumps(
                    {
                        "schema_version": 1,
                        "status": "FAIL",
                        "error_code": synthetic_code,
                        "message": f"{synthetic_subject}: {job_id} synthetic {variant} rejection",
                    }
                ) + "\n"
            elif producer_name == "tools/verify_modified_python.py":
                if registry_entry["variant_id"] in {"ignored-build-source", "wrong-base-or-sha"}:
                    observed_exit = 2
                child_error = f"verify_modified_python: {rejection_token}\n"
                captured_output = ""
            stdout_path.write_text(captured_output, encoding="utf-8")
            stderr_path.write_text(child_error, encoding="utf-8")
            expectation = "NONZERO"
            child_stdout = path.parent / f"{support_stem}.child.stdout"
            child_stderr = path.parent / f"{support_stem}.child.stderr"
            child_stdout.write_text(captured_output, encoding="utf-8")
            child_stderr.write_text(child_error, encoding="utf-8")
            probe_request_path, probe_request, raw_negative = (
                _canonical_negative_fixture(
                    path.parent / f"{support_stem}.probe",
                    registry_entry,
                    child_stdout,
                    child_stderr,
                )
            )
            raw_negative["observed_exit_code"] = observed_exit
            if job_id == "browser-contract-isolated":
                isolation_proof = probe_request_path.parent / "isolation-proof.json"
                _write_json(
                    isolation_proof,
                    {
                        "schema_version": 1,
                        "status": "PASS",
                        "network_mode": "none",
                        "candidate_sha": CANDIDATE_SHA,
                    },
                )
                raw_negative["isolation_proof"] = {
                    "path": isolation_proof.relative_to(probe_request_path.parent).as_posix(),
                    "sha256": _sha256(isolation_proof),
                }
            producer_report = path.parent / f"{support_stem}.producer.json"
            _write_json(probe_request_path, probe_request)
            raw_negative["request_sha256"] = _sha256(probe_request_path)
            _write_json(producer_report, raw_negative)
            outputs.append(
                {
                    "kind": "producer-report",
                    "scope": "artifact",
                    "path": producer_report.relative_to(run_dir).as_posix(),
                    "sha256": _sha256(producer_report),
                }
            )
        else:
            owned_results = [
                {
                    "case_id": case_id,
                    "obligation_id": mapping["obligation_id"],
                    "variant_id": mapping["variant_id"],
                    "job_id": job_id,
                    "target": target,
                    "status": "PASS",
                }
                for case_id, mapping in mappings
                if any(
                    evidence["kind"] == kind
                    and evidence["path"] == artifact_relative
                    for evidence in mapping["evidence"]
                )
            ]
            producer_payloads = {
                "architecture-report": _architecture_payload(),
                "typecheck-report": {
                    "schema_version": 1,
                    "status": "PASS",
                    "files": ["src/platforms/utiki/auth.py"],
                    "required_files": ["src/platforms/utiki/auth.py"],
                    "effective_flags": ["--strict", "--no-incremental"],
                    "mypy_exit_code": 0,
                    "failure_reasons": [],
                    "config_sha256": "1" * 64,
                    "manifest_sha256": "2" * 64,
                    "stdout_sha256": "3" * 64,
                    "stderr_sha256": "4" * 64,
                    "coverage_kind": "controlled-runner-contract",
                    "production_scope_status": "NOT_CLAIMED",
                    "legacy_seed_scope_status": "KNOWN_FAILING_DEBT",
                    "coverage_context_path": "context.json",
                    "coverage_context_sha256": "0" * 64,
                },
                "python-syntax-report": {
                    "schema_version": 1,
                    "overall_status": "PASS",
                    "candidate_sha": CANDIDATE_SHA,
                    "exit_code": 0,
                    "compile_count": 1,
                    "files": [{"path": "tools/example.py", "status": "PASS"}],
                    "failure_reasons": [],
                },
                "browser-isolation-report": {
                    "schema_version": 1,
                    "report_kind": "utiki-browser-isolation-launcher",
                    "status": "PASS",
                    "run_id": RUN_ID,
                    "candidate_sha": CANDIDATE_SHA,
                    "job_id": job_id,
                    "target": target,
                    "runtime_evidence": {"network_mode": "none", "process_tree": True},
                    "peer_audit": {"outbound_count": 0},
                    "inner_report_sha256": "a" * 64,
                    "junit_sha256": "b" * 64,
                },
                "production-origin-report": {
                    "schema_version": 1,
                    "status": "PASS",
                    "run_id": RUN_ID,
                    "candidate_sha": CANDIDATE_SHA,
                    "origin": "https://tix.example.invalid",
                    "path": "/fixture",
                    "interception_installed": True,
                    "classifier_patched": False,
                    "live_authorization_used": False,
                    "fulfilled_requests": 1,
                },
                "benchmark-report": {
                    "schema_version": 1,
                    "comparison": "utiki-source",
                    "comparable": True,
                    "metrics": {
                        "decision": {
                            "sample_count": {"baseline": 10, "candidate": 10},
                            "failures": {"baseline": 0, "candidate": 0},
                        }
                    },
                },
            }
            if kind == "typecheck-report":
                fixture_root = (
                    PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "typecheck-p1"
                )
                typed_manifest = fixture_root / "manifest.txt"
                mypy_config = fixture_root / "mypy.ini"
                coverage_context = fixture_root / "context.json"
                for input_kind, input_file in (
                    ("typed-manifest", typed_manifest),
                    ("mypy-config", mypy_config),
                    ("coverage-context", coverage_context),
                ):
                    extra_inputs.append(
                        {
                            "kind": input_kind,
                            "scope": "repository",
                            "path": input_file.relative_to(PROJECT_ROOT).as_posix(),
                            "sha256": _sha256(input_file),
                        }
                    )
                typecheck_stdout = path.parent / "typecheck-mypy.stdout"
                typecheck_stderr = path.parent / "typecheck-mypy.stderr"
                typecheck_stdout.write_text("Success: no issues found\n", encoding="utf-8")
                typecheck_stderr.write_text("", encoding="utf-8")
                producer_payloads[kind].update(
                    {
                        "manifest_sha256": _sha256(typed_manifest),
                        "config_sha256": _sha256(mypy_config),
                        "coverage_context_sha256": _sha256(coverage_context),
                        "stdout_sha256": _sha256(typecheck_stdout),
                        "stderr_sha256": _sha256(typecheck_stderr),
                    }
                )
                extra_outputs.extend(
                    [
                        {
                            "kind": "typecheck-stdout",
                            "scope": "artifact",
                            "path": typecheck_stdout.relative_to(run_dir).as_posix(),
                            "sha256": _sha256(typecheck_stdout),
                        },
                        {
                            "kind": "typecheck-stderr",
                            "scope": "artifact",
                            "path": typecheck_stderr.relative_to(run_dir).as_posix(),
                            "sha256": _sha256(typecheck_stderr),
                        },
                    ]
                )
            if job_id == "performance-contract" and kind == "browser-isolation-report":
                sibling_files = {
                    "inner_report_sha256": path.parent / "inner-performance-report.json",
                    "benchmark_report_sha256": path.parent / "raw-benchmark-report.json",
                    "junit_sha256": path.parent / "performance.xml",
                    "reporter_result_sha256": path.parent / "performance-reporter-result.json",
                }
                for index, sibling in enumerate(sibling_files.values(), start=1):
                    sibling.write_text(f"controlled performance output {index}\n", encoding="utf-8")
                build_evidence = run_dir / "evidence" / "image-build.json"
                build_base_image = "python:3.11@sha256:" + "4" * 64
                build_image_id = "sha256:" + "5" * 64
                build_inputs = [
                    {
                        "path": relative,
                        "sha256": _sha256(PROJECT_ROOT / relative),
                    }
                    for relative in (
                        "ci/utiki-isolation/Dockerfile",
                        "requirement.txt",
                        "requirements-dev.txt",
                    )
                ]
                _write_json(
                    build_evidence,
                    {
                        "schema_version": 1,
                        "evidence_kind": "utiki-isolation-image-build",
                        "base_image": build_base_image,
                        "image_id": build_image_id,
                        "inputs": build_inputs,
                        "build_completed_before_isolation": True,
                        "isolation_phase_pulled_or_built": False,
                    },
                )
                extra_inputs.append(
                    {
                        "kind": "image-build",
                        "scope": "artifact",
                        "path": build_evidence.relative_to(run_dir).as_posix(),
                        "sha256": _sha256(build_evidence),
                    }
                )
                producer_payloads[kind] = {
                    "schema_version": 1,
                    "report_kind": "utiki-browser-isolation-launcher",
                    "status": "PASS",
                    "run_id": RUN_ID,
                    "attempt": 1,
                    "phase": PHASE,
                    "job_id": job_id,
                    "target": target,
                    "candidate_sha": CANDIDATE_SHA,
                    "catalog_sha256": APPROVED_CATALOG_SHA256,
                    "execution_manifest_sha256": _sha256(manifest_path),
                    "context": {
                        "run_id": RUN_ID,
                        "attempt": 1,
                        "phase": PHASE,
                        "job_id": job_id,
                        "target": target,
                        "candidate_sha": CANDIDATE_SHA,
                        "catalog_sha256": APPROVED_CATALOG_SHA256,
                    },
                    "execution_results": owned_results,
                    "contract": {
                        "contract_id": "utiki-p1-performance-hard-egress-v1",
                        "path": "ci/utiki-performance-isolation.json",
                        "sha256": _sha256(PROJECT_ROOT / "ci/utiki-performance-isolation.json"),
                    },
                    "execution_manifest": {
                        "path": str(manifest_path.resolve()),
                        "sha256": _sha256(manifest_path),
                    },
                    "image": {"id": build_image_id},
                    "image_build_evidence": {
                        "base_image": build_base_image,
                        "image_id": build_image_id,
                        "inputs": build_inputs,
                        "path": str(build_evidence.resolve()),
                        "sha256": _sha256(build_evidence),
                    },
                    "runtime_evidence": {
                        "isolated_container": {
                            "network_mode": "none",
                            "privileged": False,
                            "readonly_rootfs": True,
                            "cap_drop": ["ALL"],
                        }
                    },
                    "peer_audit": {
                        "outbound_count": 0,
                        "forbidden_paths_observed": [],
                        "forbidden_events_observed": [],
                        "positive_control_counts": {"tcp_ipv4": 1, "tcp_ipv6": 1, "udp_dns": 1},
                    },
                    **{
                        digest_name: _sha256(sibling)
                        for digest_name, sibling in sibling_files.items()
                    },
                    "started_at_unix": 1,
                    "finished_at_unix": 2,
                    "reasons": [],
                }
            if job_id == "performance-contract" and kind == "benchmark-report":
                performance_inputs = {
                    "performance-isolation-report": run_dir / "evidence" / "performance-isolation-raw.json",
                    "raw-benchmark-report": run_dir / "evidence" / "raw-benchmark-report.json",
                    "managed-junit": run_dir / "evidence" / "performance.xml",
                    "reporter-result": run_dir / "evidence" / "performance-reporter-result.json",
                }
                for index, (input_kind, input_file) in enumerate(
                    performance_inputs.items(), start=1
                ):
                    if not input_file.exists():
                        input_file.write_text(
                            f"controlled {input_kind} {index}\n", encoding="utf-8"
                        )
                    extra_inputs.append(
                        {
                            "kind": input_kind,
                            "scope": "artifact",
                            "path": input_file.relative_to(run_dir).as_posix(),
                            "sha256": _sha256(input_file),
                        }
                    )
                performance_contract = PROJECT_ROOT / "ci" / "utiki-performance-isolation.json"
                extra_inputs.append(
                    {
                        "kind": "performance-contract",
                        "scope": "repository",
                        "path": performance_contract.relative_to(PROJECT_ROOT).as_posix(),
                        "sha256": _sha256(performance_contract),
                    }
                )
                source_metrics = {"event_loop.scheduling_lag": _source_performance_metric(300)}
                for source_case in ("date", "area", "seat", "quantity"):
                    source_metrics[f"{source_case}.cold_import"] = _source_performance_metric(3)
                    source_metrics[f"{source_case}.cold_first_operation"] = _source_performance_metric(3)
                    source_metrics[f"{source_case}.warm_parse_select_plan"] = _source_performance_metric(300)
                semantic_digest = "6" * 64
                producer_payloads[kind] = {
                    "schema_version": 1,
                    "report_kind": "utiki-p1-performance-verification",
                    "status": "PASS",
                    "context": {
                        "run_id": RUN_ID,
                        "attempt": 1,
                        "phase": PHASE,
                        "job_id": job_id,
                        "target": target,
                        "candidate_sha": CANDIDATE_SHA,
                        "catalog_sha256": APPROVED_CATALOG_SHA256,
                    },
                    "execution_results": owned_results,
                    "method": {},
                    "hard_egress_isolation": {
                        "verified": True,
                        "mode": "linux-container-network-none",
                        "peer_outbound_count": 0,
                        "isolation_report_sha256": _sha256(
                            performance_inputs["performance-isolation-report"]
                        ),
                        "image_id": "sha256:" + "5" * 64,
                        "image_build_evidence_sha256": "7" * 64,
                    },
                    "digests": {
                        "contract_sha256": _sha256(
                            performance_contract
                        ),
                        "execution_manifest_sha256": _sha256(manifest_path),
                        "fixture_sha256": "8" * 64,
                        "production_script_sha256": "9" * 64,
                        "source_benchmark_script_sha256": "a" * 64,
                        "inner_harness_sha256": "b" * 64,
                        "semantic_output_sha256": semantic_digest,
                        "production_subject_manifest_sha256": "c" * 64,
                        "raw_benchmark_report_sha256": _sha256(
                            performance_inputs["raw-benchmark-report"]
                        ),
                        "junit_sha256": _sha256(performance_inputs["managed-junit"]),
                        "reporter_result_sha256": _sha256(
                            performance_inputs["reporter-result"]
                        ),
                    },
                    "source": {
                        "run": {
                            "git": {"candidate_sha": CANDIDATE_SHA, "dirty": False},
                            "os": {"system": "Linux", "machine": "x86_64"},
                            "hard_egress_isolation": True,
                        },
                        "parameters": {
                            "mode": "all",
                            "seed": 1701,
                            "warmup_per_batch": 20,
                            "warm_samples_total": 300,
                            "batches": 3,
                            "cold_samples_per_case": 3,
                            "event_loop_samples_total": 300,
                        },
                        "case_results": {
                            "area": "b19b4557991c69cc677e4860efdc87dcb7bba4c989df70ab0db24deedf91041d",
                            "date": "dc7d892478f42f861c82097ea07a7db7ffe31b48fce9387c309826a2968eecff",
                            "quantity": "8f2cdf62b1266ab3025343419058332e6b1c2c72bb3e3a1c01c45b18f45be8b3",
                            "seat": "99f3513d1481a78f1c47b890cade7f212257b92be19ea9432b568de65d14f342",
                        },
                        "metrics": source_metrics,
                    },
                    "browser": {
                        "origin": "https://tix.ctbcsports.com",
                        "path": "/BROTHERS/UTK0201_",
                        "classifier": "DATE",
                        "interception_stage": "Request",
                        "interception_installed": True,
                        "classifier_patched": False,
                        "allowlist_entries": 1,
                        "navigation_fulfilled_requests": 1,
                        "rejected_unknown_requests": 0,
                        "live_authorization_used": False,
                        "semantic_output_sha256": semantic_digest,
                        "command_labels": {"DOM.getDocument": 30},
                        "metrics": {
                            "observation_latency": _browser_performance_metric("ns", [1] * 30),
                            "cdp_commands_per_observation": _browser_performance_metric("count", [1] * 30),
                            "ticketing_requests_per_observation": _browser_performance_metric("count", [0] * 30),
                        },
                    },
                    "threshold_policy": "P1 baseline establishment",
                }
            if kind == "architecture-report":
                producer_report = stdout_path
            elif kind == "typecheck-report":
                producer_report = path.parent / "typecheck-raw.json"
            elif kind == "python-syntax-report":
                producer_report = path.parent / "python-syntax-raw.json"
            elif job_id == "performance-contract" and kind == "browser-isolation-report":
                producer_report = path.parent / "performance-isolation-raw.json"
            elif (
                job_id == "browser-contract-isolated"
                and kind == "browser-isolation-report"
            ):
                producer_report = path.parent / "browser-isolation-raw.json"
            elif kind == "production-origin-report":
                producer_report = path.parent / "production-origin-verified.json"
            elif job_id == "performance-contract" and kind == "benchmark-report":
                producer_report = path.parent / "performance-verification-raw.json"
            else:
                producer_report = path.parent / f"{support_stem}.producer.json"
            _write_json(producer_report, producer_payloads[kind])
            if stdout_path != producer_report:
                stdout_path.write_text(
                    producer_report.read_text(encoding="utf-8"), encoding="utf-8"
                )
            outputs.append(
                {
                    "kind": "producer-report",
                    "scope": "artifact",
                    "path": producer_report.relative_to(run_dir).as_posix(),
                    "sha256": _sha256(producer_report),
                }
            )
            expectation = "ZERO"
            observed_exit = 0
            rejection_token = None
        outputs.extend(
            [
                {
                    "kind": "stdout",
                    "scope": "artifact",
                    "path": stdout_path.relative_to(run_dir).as_posix(),
                    "sha256": _sha256(stdout_path),
                },
                {
                    "kind": "stderr",
                    "scope": "artifact",
                    "path": stderr_path.relative_to(run_dir).as_posix(),
                    "sha256": _sha256(stderr_path),
                },
            ]
        )
        outputs.extend(extra_outputs)
        if kind == "checker-exit-nonzero":
            evidence_inputs = [
                {
                    "kind": "injected-probe",
                    "scope": "artifact",
                    "path": probe_request_path.relative_to(run_dir).as_posix(),
                    "sha256": _sha256(probe_request_path),
                },
                {
                    "kind": "probe-registry",
                    "scope": "repository",
                    "path": NEGATIVE_REGISTRY.relative_to(PROJECT_ROOT).as_posix(),
                    "sha256": _sha256(NEGATIVE_REGISTRY),
                },
            ]
        elif kind == "architecture-report":
            inventory = (
                PROJECT_ROOT
                / "docs"
                / "02-development"
                / "utiki-refactor"
                / "core-integration-inventory.md"
            )
            evidence_inputs = [
                {
                    "kind": "core-inventory",
                    "scope": "repository",
                    "path": inventory.relative_to(PROJECT_ROOT).as_posix(),
                    "sha256": _sha256(inventory),
                }
            ]
        elif kind == "typecheck-report":
            evidence_inputs = extra_inputs
        elif kind == "python-syntax-report":
            run_context = run_dir / "evidence" / "run-context.json"
            modified_manifest = run_dir / "evidence" / "modified-python-manifest.json"
            _write_json(run_context, {"schema_version": 1})
            _write_json(modified_manifest, {"schema_version": 1})
            evidence_inputs = [
                {
                    "kind": "run-context",
                    "scope": "artifact",
                    "path": "evidence/run-context.json",
                    "sha256": _sha256(run_context),
                },
                {
                    "kind": "modified-python-manifest",
                    "scope": "artifact",
                    "path": "evidence/modified-python-manifest.json",
                    "sha256": _sha256(modified_manifest),
                },
            ]
        elif job_id == "browser-contract-isolated" and kind == "browser-isolation-report":
            image_build = run_dir / "evidence" / "image-build.json"
            _write_json(image_build, {"schema_version": 1})
            evidence_inputs = [
                {
                    "kind": "isolation-contract",
                    "scope": "repository",
                    "path": "ci/utiki-browser-isolation.json",
                    "sha256": _sha256(PROJECT_ROOT / "ci/utiki-browser-isolation.json"),
                },
                {
                    "kind": "execution-manifest",
                    "scope": "repository",
                    "path": "tests/fixtures/utiki/manifest.json",
                    "sha256": _sha256(PROJECT_ROOT / "tests/fixtures/utiki/manifest.json"),
                },
                {
                    "kind": "image-build",
                    "scope": "artifact",
                    "path": "evidence/image-build.json",
                    "sha256": _sha256(image_build),
                },
            ]
        elif kind == "production-origin-report":
            isolation_raw = run_dir / "evidence" / "browser-isolation-raw.json"
            origin_raw = run_dir / "evidence" / "production-origin-raw.json"
            if not isolation_raw.exists():
                _write_json(isolation_raw, {"schema_version": 1})
            _write_json(origin_raw, {"schema_version": 1})
            evidence_inputs = [
                {
                    "kind": "isolation-report",
                    "scope": "artifact",
                    "path": "evidence/browser-isolation-raw.json",
                    "sha256": _sha256(isolation_raw),
                },
                {
                    "kind": "origin-report",
                    "scope": "artifact",
                    "path": "evidence/production-origin-raw.json",
                    "sha256": _sha256(origin_raw),
                },
            ]
        elif job_id == "performance-contract" and kind == "browser-isolation-report":
            evidence_inputs = [
                {
                    "kind": "performance-contract",
                    "scope": "repository",
                    "path": "ci/utiki-performance-isolation.json",
                    "sha256": _sha256(PROJECT_ROOT / "ci/utiki-performance-isolation.json"),
                },
                {
                    "kind": "execution-manifest",
                    "scope": "repository",
                    "path": "tests/fixtures/utiki/manifest.json",
                    "sha256": _sha256(PROJECT_ROOT / "tests/fixtures/utiki/manifest.json"),
                },
                *[item for item in extra_inputs if item["kind"] == "image-build"],
            ]
        elif job_id == "performance-contract" and kind == "benchmark-report":
            evidence_inputs = extra_inputs
        else:
            raise AssertionError((job_id, kind))
        value = {
            "schema_version": 2,
            "kind": kind,
            "context": {
                "run_id": RUN_ID,
                "attempt": 1,
                "job_id": job_id,
                "phase": PHASE,
                "target": target,
                "candidate_sha": CANDIDATE_SHA,
                "catalog_sha256": APPROVED_CATALOG_SHA256,
                "execution_manifest_sha256": _sha256(manifest_path),
            },
            "producer": {
                "path": producer.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256(producer),
            },
            "launcher": {
                "path": launcher.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256(launcher),
            },
            "inputs": evidence_inputs,
            "outputs": outputs,
            "expectation": expectation,
            "observed_exit_code": observed_exit,
            "status": "PASS",
            "results": (
                owned_results
                if kind != "checker-exit-nonzero"
                else [
                    {
                        "case_id": case_id,
                        "obligation_id": mapping["obligation_id"],
                        "variant_id": mapping["variant_id"],
                        "job_id": job_id,
                        "target": target,
                        "status": "PASS",
                    }
                    for case_id, mapping in mappings
                    if any(
                        evidence["kind"] == kind
                        and evidence["path"] == artifact_relative
                        for evidence in mapping["evidence"]
                    )
                ]
            ),
            "rejection_token": rejection_token,
        }
        _write_json(path, value)
        artifacts.append(
            {"kind": kind, "path": artifact_relative, "sha256": _sha256(path)}
        )

    layers = list(profiles[PHASE][job_id]["layers"])
    resource_required = bool(
        set(layers) & {"browser-contract", "browser-isolation", "performance"}
    )
    resources = []
    if resource_required:
        fixture = run_dir / "fixture.html"
        script = run_dir / "script.js"
        fixture.write_text("<main>fixed fixture</main>\n", encoding="utf-8")
        script.write_text("(() => true)();\n", encoding="utf-8")
        fixture_digest = _sha256(fixture)
        script_digest = _sha256(script)
        resources = [
            {"kind": "fixture", "path": "fixture.html", "sha256": fixture_digest},
            {"kind": "script", "path": "script.js", "sha256": script_digest},
        ]
    else:
        fixture_digest = "NOT_APPLICABLE"
        script_digest = "NOT_APPLICABLE"
    manifest_digest = _sha256(manifest_path)
    suite_context = {
        "run_id": RUN_ID,
        "attempt": "1",
        "job_id": job_id,
        "phase": PHASE,
        "target": target,
        "candidate_sha": CANDIDATE_SHA,
        "catalog_sha256": APPROVED_CATALOG_SHA256,
        "execution_manifest_sha256": manifest_digest,
        "fixture_digest": fixture_digest,
        "script_digest": script_digest,
    }
    suite = ElementTree.Element("testsuite", name=f"{job_id}-{target}")
    _properties(suite, suite_context)
    for case_id, mapping in mappings:
        if mapping["node_id"] is None:
            continue
        testcase = ElementTree.SubElement(
            suite,
            "testcase",
            name=mapping["node_id"].split("::")[-1],
            classname="synthetic.required",
        )
        _properties(
            testcase,
            {
                "case_id": case_id,
                "obligation_id": mapping["obligation_id"],
                "variant_id": mapping["variant_id"],
                "node_id": mapping["node_id"],
                **suite_context,
                "layer": mapping["layer"],
                "outcome": "passed",
                "wasxfail": "false",
                "setup_outcome": "passed",
                "call_outcome": "passed",
                "teardown_outcome": "passed",
                "markers": "[]",
            },
        )
    junit = run_dir / "junit.xml"
    ElementTree.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)

    browser_job = "browser-isolation" in layers
    pytest_style = any(mapping["node_id"] is not None for _, mapping in mappings)
    isolation_artifact = next(
        (item for item in artifacts if item["kind"] == "browser-isolation-report"),
        None,
    )
    testcase_count = len(suite.findall("testcase"))
    metadata = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "attempt": 1,
        "job_id": job_id,
        "phase": PHASE,
        "target": target,
        "candidate_sha": CANDIDATE_SHA,
        "working_tree_digest": "2" * 64,
        "working_tree_clean": True,
        "os": "linux",
        "architecture": "x86_64",
        "layers": layers,
        "catalog_sha256": APPROVED_CATALOG_SHA256,
        "execution_manifest_sha256": manifest_digest,
        "junit_sha256": _sha256(junit),
        "fixture_digest": fixture_digest,
        "script_digest": script_digest,
        "resources": resources,
        "artifacts": artifacts,
        "effective_command": (
            [
                sys.executable,
                "-m",
                "pytest",
                "--strict-config",
                "--strict-markers",
                "-m",
                "not live",
            ]
            if pytest_style
            else [sys.executable, "tools/verify_modified_python.py", "--manifest", "modified-python.json"]
        ),
        "effective_flags": {
            "strict_config": True if pytest_style else None,
            "strict_markers": True if pytest_style else None,
            "marker_expression": "not live" if pytest_style else None,
        },
        "exit_codes": {"test": 0, "collector": 0, "reporter": 0},
        "collected_count": testcase_count,
        "selected_count": testcase_count,
        "started_at": "2026-10-16T10:00:00Z",
        "finished_at": "2026-10-16T10:00:01Z",
        "browser_enabled": browser_job,
        "isolation": {
            "required": browser_job,
            "verified": browser_job,
            "report_sha256": isolation_artifact["sha256"] if isolation_artifact else None,
        },
        "tool_versions": {"python": "3.11", "pytest": "9.0.3", "checker": "1"},
    }
    metadata_path = run_dir / "run-metadata.json"
    _write_json(metadata_path, metadata)
    return {"dir": run_dir, "junit": junit, "metadata": metadata_path}


def _refresh_junit_digest(run):
    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    metadata["junit_sha256"] = _sha256(run["junit"])
    _write_json(run["metadata"], metadata)


def _artifact_path(run, kind, index=0):
    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    entries = [entry for entry in metadata["artifacts"] if entry["kind"] == kind]
    if index >= len(entries):
        raise AssertionError(f"missing artifact: {kind}")
    return run["dir"] / entries[index]["path"]


def _refresh_artifact_digest(run, kind, index=0):
    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    entries = [entry for entry in metadata["artifacts"] if entry["kind"] == kind]
    if index >= len(entries):
        raise AssertionError(f"missing artifact: {kind}")
    artifact = run["dir"] / entries[index]["path"]
    entries[index]["sha256"] = _sha256(artifact)
    _write_json(run["metadata"], metadata)


def _single_arguments(manifest_path, run, job_id="unit-contract", target="source-linux-x64"):
    return [
        "--required-catalog",
        CATALOG,
        "--approved-catalog-sha256",
        APPROVED_CATALOG_SHA256,
        "--execution-manifest",
        manifest_path,
        "--junit",
        run["junit"],
        "--run-metadata",
        run["metadata"],
        "--phase",
        PHASE,
        "--run-id",
        RUN_ID,
        "--job-id",
        job_id,
        "--target",
        target,
        "--candidate-sha",
        CANDIDATE_SHA,
    ]


@pytest.fixture
def gate_inputs(tmp_path):
    manifest_path, manifest, catalog, profiles = _make_manifest(tmp_path)
    run = _make_run(tmp_path, manifest_path, manifest, profiles, "unit-contract", "source-linux-x64")
    return {
        "root": tmp_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "catalog": catalog,
        "profiles": profiles,
        "run": run,
    }


def test_valid_four_input_unit_run_passes(gate_inputs):
    case = gate_inputs

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["missing"] == 0
    assert report["failed"] == 0
    assert report["runs"][0]["required_junit"] == 3


def test_documented_single_run_form_can_bind_phase_from_manifest(gate_inputs):
    case = gate_inputs
    arguments = _single_arguments(case["manifest_path"], case["run"])
    phase_index = arguments.index("--phase")
    del arguments[phase_index : phase_index + 2]

    result = _run_checker(*arguments)

    assert result.returncode == 0, result.stderr or result.stdout


def test_non_pytest_syntax_job_accepts_true_empty_junit(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "modified-python-syntax",
        "source-linux-x64",
    )

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "modified-python-syntax",
            "source-linux-x64",
        )
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["runs"][0]["required"] == 12
    assert report["runs"][0]["required_junit"] == 0
    assert report["runs"][0]["junit_testcases"] == 0


def test_strict_json_rejects_duplicate_keys(gate_inputs):
    case = gate_inputs
    duplicate = case["root"] / "duplicate-manifest.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")

    result = _run_checker(*_single_arguments(duplicate, case["run"]))

    assert result.returncode != 0
    assert "DUPLICATE_JSON_KEY" in result.stdout


def test_unapproved_catalog_change_is_not_self_approving(gate_inputs):
    case = gate_inputs
    changed = copy.deepcopy(case["catalog"])
    changed["cases"][0]["scenario"] += " mutated"
    catalog_path = case["root"] / "changed-catalog.json"
    _write_json(catalog_path, changed)
    changed_digest = _sha256(catalog_path)
    arguments = _single_arguments(case["manifest_path"], case["run"])
    arguments[1] = catalog_path
    arguments[3] = changed_digest

    result = _run_checker(*arguments)

    assert result.returncode != 0
    assert "REVIEW_REQUIRED" in result.stdout


def test_joint_test_and_mapping_deletion_is_rejected(gate_inputs):
    case = gate_inputs
    manifest = copy.deepcopy(case["manifest"])
    removed_node = None
    for item in manifest["cases"]:
        for index, execution in enumerate(item["executions"]):
            if execution["status"] == "MAPPED" and execution["job_id"] == "unit-contract":
                removed_node = execution["node_id"]
                del item["executions"][index]
                break
        if removed_node:
            break
    _write_json(case["manifest_path"], manifest)
    tree = ElementTree.parse(case["run"]["junit"])
    suite = tree.getroot()
    for testcase in suite.findall("testcase"):
        properties = {p.get("name"): p.get("value") for p in testcase.find("properties")}
        if properties["node_id"] == removed_node:
            suite.remove(testcase)
            break
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    _refresh_junit_digest(case["run"])

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "EXECUTION_SET_MISMATCH" in result.stdout


def test_not_due_mapping_cannot_be_deleted(gate_inputs):
    case = gate_inputs
    manifest = copy.deepcopy(case["manifest"])
    for item in manifest["cases"]:
        for index, execution in enumerate(item["executions"]):
            if execution["status"] == "NOT_DUE":
                del item["executions"][index]
                break
        else:
            continue
        break
    _write_json(case["manifest_path"], manifest)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "EXECUTION_SET_MISMATCH" in result.stdout


def test_applicable_mapping_cannot_be_forged_not_applicable(gate_inputs):
    case = gate_inputs
    manifest = copy.deepcopy(case["manifest"])
    for item in manifest["cases"]:
        for execution in item["executions"]:
            if execution["status"] == "MAPPED" and execution["job_id"] == "unit-contract":
                execution["status"] = "NOT_APPLICABLE"
                execution["node_id"] = None
                execution["evidence"] = []
                break
        else:
            continue
        break
    _write_json(case["manifest_path"], manifest)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "FORGED_APPLICABILITY" in result.stdout


def test_wrong_execution_layer_is_rejected(gate_inputs):
    case = gate_inputs
    manifest = copy.deepcopy(case["manifest"])
    for item in manifest["cases"]:
        for execution in item["executions"]:
            if execution["status"] == "MAPPED":
                execution["layer"] = "wrong-layer"
                break
        else:
            continue
        break
    _write_json(case["manifest_path"], manifest)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "WRONG_LAYER" in result.stdout


@pytest.mark.parametrize(
    ("mutation", "property_name", "property_value", "xml_child"),
    [
        ("skip", "outcome", "skipped", "skipped"),
        ("xfail", "wasxfail", "true", "skipped"),
        ("strict-xpass", "wasxfail", "true", "failure"),
        ("nonstrict-xpass", "wasxfail", "true", None),
        ("setup-error", "setup_outcome", "error", "error"),
        ("teardown-error", "teardown_outcome", "error", "error"),
    ],
)
def test_required_non_pass_outcomes_are_rejected(
    gate_inputs, mutation, property_name, property_value, xml_child
):
    case = gate_inputs
    tree = ElementTree.parse(case["run"]["junit"])
    testcase = tree.getroot().find("testcase")
    properties = testcase.find("properties")
    for prop in properties.findall("property"):
        if prop.get("name") == property_name:
            prop.set("value", property_value)
    if xml_child:
        ElementTree.SubElement(testcase, xml_child, message=mutation)
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    _refresh_junit_digest(case["run"])

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "REQUIRED_OUTCOME_NOT_PASS" in result.stdout


def test_old_junit_without_required_properties_is_rejected(gate_inputs):
    case = gate_inputs
    tree = ElementTree.parse(case["run"]["junit"])
    testcase = tree.getroot().find("testcase")
    properties = testcase.find("properties")
    for prop in properties.findall("property"):
        if prop.get("name") == "candidate_sha":
            properties.remove(prop)
            break
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    _refresh_junit_digest(case["run"])

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "OLD_OR_PARTIAL_JUNIT" in result.stdout


def test_stale_candidate_sha_is_rejected_by_trusted_expectation(gate_inputs):
    case = gate_inputs
    arguments = _single_arguments(case["manifest_path"], case["run"])
    arguments[-1] = "3" * 40

    result = _run_checker(*arguments)

    assert result.returncode != 0
    assert "RUN_CONTEXT_MISMATCH" in result.stdout


def test_stale_junit_run_id_is_rejected(gate_inputs):
    case = gate_inputs
    tree = ElementTree.parse(case["run"]["junit"])
    testcase = tree.getroot().find("testcase")
    for prop in testcase.find("properties").findall("property"):
        if prop.get("name") == "run_id":
            prop.set("value", "old-run-attempt-1")
            break
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    _refresh_junit_digest(case["run"])

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "JUNIT_CONTEXT_MISMATCH" in result.stdout


def test_junit_digest_mismatch_is_rejected_before_results_are_used(gate_inputs):
    case = gate_inputs
    case["run"]["junit"].write_text("<testsuite/>\n", encoding="utf-8")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "JUNIT_DIGEST_MISMATCH" in result.stdout


def test_effective_strict_flags_cannot_be_cleared(gate_inputs):
    case = gate_inputs
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    metadata["effective_command"].remove("--strict-markers")
    _write_json(case["run"]["metadata"], metadata)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "STRICT_FLAGS_MISSING" in result.stdout


def test_non_browser_job_must_not_manufacture_fixture_or_script(gate_inputs):
    case = gate_inputs
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    fixture = case["run"]["dir"] / "fake-fixture.html"
    fixture.write_text("fake\n", encoding="utf-8")
    metadata["fixture_digest"] = _sha256(fixture)
    metadata["resources"] = [
        {"kind": "fixture", "path": fixture.name, "sha256": _sha256(fixture)}
    ]
    _write_json(case["run"]["metadata"], metadata)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_NOT_APPLICABLE" in result.stdout


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("observed_exit_code", 0, "INVALID_NEGATIVE_PROBE"),
        ("rejection_token", "TOKEN_NOT_IN_CAPTURE", "INVALID_NEGATIVE_PROBE"),
    ],
)
def test_negative_probe_cannot_self_assert_without_process_evidence(
    gate_inputs, field, value, expected
):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    report = json.loads(artifact.read_text(encoding="utf-8"))
    report[field] = value
    _write_json(artifact, report)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert expected in result.stdout


def test_negative_envelope_cannot_overwrite_actual_child_exit(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        output for output in envelope["outputs"] if output["kind"] == "producer-report"
    )
    producer_report = case["run"]["metadata"].parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["observed_exit_code"] = envelope["observed_exit_code"] + 1
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_NEGATIVE_PROBE" in result.stdout


def test_h05_token_only_output_cannot_replace_the_reviewed_pytest_reason(
    gate_inputs,
):
    case = gate_inputs
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    for artifact_entry in metadata["artifacts"]:
        if artifact_entry["kind"] != "checker-exit-nonzero":
            continue
        artifact = case["run"]["dir"] / artifact_entry["path"]
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        if envelope["results"][0]["obligation_id"] == "H05.arch.unknown-marker":
            break
    else:
        raise AssertionError("missing H05 unknown-marker negative evidence")

    outputs = {item["kind"]: item for item in envelope["outputs"]}
    stdout = case["run"]["dir"] / outputs["stdout"]["path"]
    stdout.write_text("utiki_unknown_marker\n", encoding="utf-8")
    outputs["stdout"]["sha256"] = _sha256(stdout)
    producer_report = case["run"]["dir"] / outputs["producer-report"]["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["stdout_sha256"] = _sha256(stdout)
    _write_json(producer_report, raw)
    outputs["producer-report"]["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    artifact_entry["sha256"] = _sha256(artifact)
    _write_json(case["run"]["metadata"], metadata)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_NEGATIVE_PROBE" in result.stdout


def test_negative_registry_must_remain_repository_scoped(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    registry_input = next(
        item for item in envelope["inputs"] if item["kind"] == "probe-registry"
    )
    copied_registry = case["run"]["dir"] / "evidence" / "copied-registry.json"
    shutil.copyfile(NEGATIVE_REGISTRY, copied_registry)
    registry_input.update(
        {
            "scope": "artifact",
            "path": copied_registry.relative_to(case["run"]["dir"]).as_posix(),
            "sha256": _sha256(copied_registry),
        }
    )
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_REPORT" in result.stdout


def test_negative_capture_outputs_must_remain_artifact_scoped(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    stdout_output = next(
        item for item in envelope["outputs"] if item["kind"] == "stdout"
    )
    stdout_output.update(
        {
            "scope": "repository",
            "path": NEGATIVE_REGISTRY.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256(NEGATIVE_REGISTRY),
        }
    )
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_REPORT" in result.stdout


def test_negative_capture_outputs_may_not_alias_each_other(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    outputs = {item["kind"]: item for item in envelope["outputs"]}
    outputs["stderr"].update(
        {
            "path": outputs["stdout"]["path"],
            "sha256": outputs["stdout"]["sha256"],
        }
    )
    producer_report = case["run"]["dir"] / outputs["producer-report"]["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["stderr_sha256"] = outputs["stdout"]["sha256"]
    _write_json(producer_report, raw)
    outputs["producer-report"]["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_REPORT" in result.stdout


def test_negative_request_rejects_unknown_top_level_fields(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    request_input = next(
        item for item in envelope["inputs"] if item["kind"] == "injected-probe"
    )
    request_path = case["run"]["dir"] / request_input["path"]
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["unreviewed"] = True
    _write_json(request_path, request)
    request_input["sha256"] = _sha256(request_path)
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = case["run"]["dir"] / producer_output["path"]
    transcript = json.loads(producer_report.read_text(encoding="utf-8"))
    transcript["request_sha256"] = _sha256(request_path)
    _write_json(producer_report, transcript)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_SCHEMA" in result.stdout


def test_evidence_producer_digest_is_not_self_authoritative(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    report = json.loads(artifact.read_text(encoding="utf-8"))
    report["producer"]["sha256"] = "f" * 64
    _write_json(artifact, report)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "EVIDENCE_DIGEST_MISMATCH" in result.stdout


def test_one_negative_transcript_cannot_cover_two_execution_keys(gate_inputs):
    case = gate_inputs
    manifest = copy.deepcopy(case["manifest"])
    negative = [
        evidence
        for item in manifest["cases"]
        for execution in item["executions"]
        if execution["status"] == "MAPPED"
        and execution["job_id"] == "unit-contract"
        for evidence in execution["evidence"]
        if evidence["kind"] == "checker-exit-nonzero"
    ]
    assert len(negative) >= 2
    negative[1]["path"] = negative[0]["path"]
    _write_json(case["manifest_path"], manifest)
    run = _make_run(
        case["root"],
        case["manifest_path"],
        manifest,
        case["profiles"],
        "unit-contract",
        "source-linux-x64",
    )

    result = _run_checker(*_single_arguments(case["manifest_path"], run))

    assert result.returncode != 0
    assert "NEGATIVE_EVIDENCE_NOT_INDEPENDENT" in result.stdout


def test_h08_negative_report_keeps_exact_reason_and_subject(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    arguments = _single_arguments(
        case["manifest_path"],
        run,
        job_id="architecture-types",
        target="source-linux-x64",
    )
    baseline = _run_checker(*arguments)
    assert baseline.returncode == 0, baseline.stderr or baseline.stdout

    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    request_path = None
    artifact_path = None
    artifact_entry = None
    producer_output = None
    producer_report = None
    for entry in metadata["artifacts"]:
        if entry["kind"] != "checker-exit-nonzero":
            continue
        candidate_artifact = run["dir"] / entry["path"]
        envelope = json.loads(candidate_artifact.read_text(encoding="utf-8"))
        if envelope["results"][0]["obligation_id"] == "H08.arch.new-domain-file":
            request_input = next(
                item for item in envelope["inputs"] if item["kind"] == "injected-probe"
            )
            request_path = run["dir"] / request_input["path"]
            artifact_path = candidate_artifact
            artifact_entry = entry
            producer_output = next(
                item
                for item in envelope["outputs"]
                if item["kind"] == "producer-report"
            )
            producer_report = run["dir"] / producer_output["path"]
            break
    assert all(
        value is not None
        for value in (
            request_path,
            artifact_path,
            artifact_entry,
            producer_output,
            producer_report,
        )
    )
    typecheck_report = request_path.parent / "typecheck-report.json"
    original = json.loads(typecheck_report.read_text(encoding="utf-8"))

    def refresh_semantic_binding() -> None:
        raw = json.loads(producer_report.read_text(encoding="utf-8"))
        raw["semantic_output"]["sha256"] = _sha256(typecheck_report)
        _write_json(producer_report, raw)
        producer_output["sha256"] = _sha256(producer_report)
        _write_json(artifact_path, envelope)
        artifact_entry["sha256"] = _sha256(artifact_path)
        _write_json(run["metadata"], metadata)

    wrong_reason = copy.deepcopy(original)
    wrong_reason["failure_reasons"] = ["UNSAFE_MYPY_CONFIG"]
    _write_json(typecheck_report, wrong_reason)
    result = _run_checker(*arguments)
    assert result.returncode != 0
    assert "EVIDENCE_DIGEST_MISMATCH" in result.stdout

    refresh_semantic_binding()
    result = _run_checker(*arguments)
    assert result.returncode != 0
    assert "INVALID_NEGATIVE_PROBE" in result.stdout

    wrong_subject = copy.deepcopy(original)
    wrong_subject["affected_paths"] = ["src/platforms/utiki/domain/other.py"]
    _write_json(typecheck_report, wrong_subject)
    refresh_semantic_binding()
    result = _run_checker(*arguments)
    assert result.returncode != 0
    assert "INVALID_NEGATIVE_PROBE" in result.stdout


def test_a05_negative_transcript_requires_exact_code_and_subject(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    arguments = _single_arguments(
        case["manifest_path"], run, "architecture-types", "source-linux-x64"
    )
    baseline = _run_checker(*arguments)
    assert baseline.returncode == 0, baseline.stderr or baseline.stdout

    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    for artifact_entry in metadata["artifacts"]:
        if artifact_entry["kind"] != "checker-exit-nonzero":
            continue
        artifact = run["dir"] / artifact_entry["path"]
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        if envelope["results"][0]["obligation_id"] == "A05.arch.zero-collected":
            break
    else:
        raise AssertionError("missing A05 zero-collected negative evidence")
    outputs = {item["kind"]: item for item in envelope["outputs"]}
    stdout = run["dir"] / outputs["stdout"]["path"]
    producer_report = run["dir"] / outputs["producer-report"]["path"]

    def replace_child_result(payload):
        _write_json(stdout, payload)
        outputs["stdout"]["sha256"] = _sha256(stdout)
        raw = json.loads(producer_report.read_text(encoding="utf-8"))
        raw["stdout_sha256"] = _sha256(stdout)
        _write_json(producer_report, raw)
        outputs["producer-report"]["sha256"] = _sha256(producer_report)
        _write_json(artifact, envelope)
        artifact_entry["sha256"] = _sha256(artifact)
        _write_json(run["metadata"], metadata)

    wrong_code = {
        "schema_version": 1,
        "status": "FAIL",
        "error_code": "REQUIRED_OUTCOME_NOT_PASS",
        "message": "EMPTY_OR_MISSING_RESULT JUnit lacks required results",
    }
    replace_child_result(wrong_code)
    result = _run_checker(*arguments)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error_code"] == "INVALID_NEGATIVE_PROBE"

    wrong_subject = {
        "schema_version": 1,
        "status": "FAIL",
        "error_code": "EMPTY_OR_MISSING_RESULT",
        "message": "EMPTY_OR_MISSING_RESULT unrelated object",
    }
    replace_child_result(wrong_subject)
    result = _run_checker(*arguments)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error_code"] == "INVALID_NEGATIVE_PROBE"


def test_positive_report_payload_is_semantically_checked(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    evidence_dir = run["dir"] / "evidence"
    artifact = evidence_dir / "architecture-report.json"
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_payload = run["dir"] / producer_output["path"]
    payload = json.loads(producer_payload.read_text(encoding="utf-8"))
    payload["checked_files"] = []
    _write_json(producer_payload, payload)
    report = envelope
    for output in report["outputs"]:
        if output["path"] == producer_output["path"]:
            output["sha256"] = _sha256(producer_payload)
    _write_json(artifact, report)
    _refresh_artifact_digest(run, "architecture-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"], run, "architecture-types", "source-linux-x64"
        )
    )

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_CONTENT" in result.stdout


def test_actual_architecture_producer_launcher_and_checker_are_end_to_end(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "architecture-report")
    for path in (
        artifact,
        artifact.with_name("architecture-report.stdout"),
        artifact.with_name("architecture-report.stderr"),
        artifact.with_name("architecture-report.producer.json"),
    ):
        path.unlink(missing_ok=True)
    inventory = (
        PROJECT_ROOT
        / "docs"
        / "02-development"
        / "utiki-refactor"
        / "core-integration-inventory.md"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_LAUNCHER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--execution-manifest",
            str(case["manifest_path"]),
            "--artifact-root",
            str(run["metadata"].parent),
            "--report",
            str(artifact),
            "--kind",
            "architecture-report",
            "--run-id",
            RUN_ID,
            "--attempt",
            "1",
            "--job-id",
            "architecture-types",
            "--phase",
            PHASE,
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            CANDIDATE_SHA,
            "--catalog-sha256",
            APPROVED_CATALOG_SHA256,
            "--producer",
            "tools/check_utiki_architecture.py",
            "--input",
            "core-inventory",
            "repository",
            inventory.relative_to(PROJECT_ROOT).as_posix(),
            "--producer-report",
            "stdout",
            "--",
            "--root",
            str(PROJECT_ROOT),
            "--core-inventory",
            str(inventory),
            "--candidate-sha",
            CANDIDATE_SHA,
            "--strict",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    _refresh_artifact_digest(run, "architecture-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"], run, "architecture-types", "source-linux-x64"
        )
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_architecture_report_cannot_substitute_a_benign_temporary_root(
    gate_inputs, tmp_path
):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "architecture-report")
    for path in (
        artifact,
        artifact.with_name("architecture-report.stdout"),
        artifact.with_name("architecture-report.stderr"),
        artifact.with_name("architecture-report.producer.json"),
    ):
        path.unlink(missing_ok=True)
    benign_root = tmp_path / "benign-root"
    benign_source = benign_root / "src" / "settings.py"
    benign_source.parent.mkdir(parents=True)
    benign_source.write_text("VALUE = 1\n", encoding="utf-8")
    inventory = (
        PROJECT_ROOT
        / "docs"
        / "02-development"
        / "utiki-refactor"
        / "core-integration-inventory.md"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_LAUNCHER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--execution-manifest",
            str(case["manifest_path"]),
            "--artifact-root",
            str(run["metadata"].parent),
            "--report",
            str(artifact),
            "--kind",
            "architecture-report",
            "--run-id",
            RUN_ID,
            "--attempt",
            "1",
            "--job-id",
            "architecture-types",
            "--phase",
            PHASE,
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            CANDIDATE_SHA,
            "--catalog-sha256",
            APPROVED_CATALOG_SHA256,
            "--producer",
            "tools/check_utiki_architecture.py",
            "--input",
            "core-inventory",
            "repository",
            inventory.relative_to(PROJECT_ROOT).as_posix(),
            "--producer-report",
            "stdout",
            "--",
            "--root",
            str(benign_root),
            "--core-inventory",
            str(inventory),
            "--candidate-sha",
            CANDIDATE_SHA,
            "--strict",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    raw = json.loads(
        artifact.with_name("architecture-report.stdout").read_text(encoding="utf-8")
    )
    assert raw["status"] == "PASS"
    assert raw["checked_files"] == ["src/settings.py"]
    _refresh_artifact_digest(run, "architecture-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"], run, "architecture-types", "source-linux-x64"
        )
    )

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_CONTENT" in result.stdout


@pytest.mark.parametrize("tampered", ["fixture", "script", "report"])
def test_resource_and_report_digest_mismatch_is_rejected(gate_inputs, tampered):
    case = gate_inputs
    run = case["run"]
    job_id = "unit-contract"
    target = "source-linux-x64"
    if tampered in {"fixture", "script"}:
        job_id = "browser-contract-isolated"
        target = "isolated-linux-x64"
        run = _make_run(
            case["root"],
            case["manifest_path"],
            case["manifest"],
            case["profiles"],
            job_id,
            target,
        )
        path = run["dir"] / ("fixture.html" if tampered == "fixture" else "script.js")
    else:
        path = _artifact_path(run, "checker-exit-nonzero")
    path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    result = _run_checker(
        *_single_arguments(case["manifest_path"], run, job_id, target)
    )

    assert result.returncode != 0
    assert "DIGEST_MISMATCH" in result.stdout


def test_duplicate_junit_result_is_rejected(gate_inputs):
    case = gate_inputs
    tree = ElementTree.parse(case["run"]["junit"])
    suite = tree.getroot()
    suite.append(copy.deepcopy(suite.find("testcase")))
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    metadata["collected_count"] += 1
    metadata["selected_count"] += 1
    metadata["junit_sha256"] = _sha256(case["run"]["junit"])
    _write_json(case["run"]["metadata"], metadata)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "DUPLICATE_JUNIT_RESULT" in result.stdout


def test_original_test_exit_cannot_be_overwritten(gate_inputs):
    case = gate_inputs
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    metadata["exit_codes"]["test"] = 1
    _write_json(case["run"]["metadata"], metadata)

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "UPSTREAM_PROCESS_FAILED" in result.stdout


def _make_run_index(case, *, compact=False):
    entries = []
    runs = []
    run_index = 0
    for job_id, profile in case["profiles"][PHASE].items():
        for target in profile["targets"]:
            run = _make_run(
                case["root"],
                case["manifest_path"],
                case["manifest"],
                case["profiles"],
                job_id,
                target,
                run_dir_name=f"r{run_index:02d}" if compact else None,
            )
            run_index += 1
            runs.append(run)
            entries.append(
                {
                    "job_id": job_id,
                    "target": target,
                    "junit_path": str(run["junit"].relative_to(case["root"])),
                    "junit_sha256": _sha256(run["junit"]),
                    "run_metadata_path": str(run["metadata"].relative_to(case["root"])),
                    "run_metadata_sha256": _sha256(run["metadata"]),
                }
            )
    index = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "attempt": 1,
        "phase": PHASE,
        "candidate_sha": CANDIDATE_SHA,
        "catalog_sha256": APPROVED_CATALOG_SHA256,
        "execution_manifest_sha256": _sha256(case["manifest_path"]),
        "runs": entries,
    }
    path = case["root"] / "run-index.json"
    _write_json(path, index)
    return path, index, runs


def _index_arguments(manifest_path, index_path):
    return [
        "--required-catalog",
        CATALOG,
        "--approved-catalog-sha256",
        APPROVED_CATALOG_SHA256,
        "--execution-manifest",
        manifest_path,
        "--run-index",
        index_path,
        "--phase",
        PHASE,
        "--run-id",
        RUN_ID,
        "--candidate-sha",
        CANDIDATE_SHA,
    ]


def test_aggregate_revalidates_every_required_p1_job_target(gate_inputs):
    case = gate_inputs
    index_path, _, _ = _make_run_index(case)

    result = _run_checker(*_index_arguments(case["manifest_path"], index_path))

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert {(run["job_id"], run["target"]) for run in report["runs"]} == {
        (job_id, target)
        for job_id, profile in case["profiles"][PHASE].items()
        for target in profile["targets"]
    }


def test_aggregate_accepts_digest_bound_artifacts_relocated_after_production(
    gate_inputs,
):
    case = gate_inputs
    index_path, _, _ = _make_run_index(case, compact=True)
    relocated_root = case["root"].with_name(case["root"].name + "-relocated")
    shutil.copytree(case["root"], relocated_root)
    relocated_manifest = relocated_root / case["manifest_path"].relative_to(case["root"])
    relocated_index = relocated_root / index_path.relative_to(case["root"])

    result = _run_checker(*_index_arguments(relocated_manifest, relocated_index))

    assert result.returncode == 0, result.stderr or result.stdout


def test_relocated_evidence_rejects_unsafe_artifact_path(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    declared = next(item for item in envelope["inputs"] if item["scope"] == "artifact")
    declared["path"] = "../outside/probe.json"
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")
    relocated_root = case["root"].with_name(case["root"].name + "-relocated")
    shutil.copytree(case["root"], relocated_root)
    relocated_run = {
        key: relocated_root / value.relative_to(case["root"])
        for key, value in case["run"].items()
    }

    result = _run_checker(*_single_arguments(case["manifest_path"], relocated_run))

    assert result.returncode != 0
    assert "UNSAFE_PATH" in result.stdout


def test_relocated_evidence_still_rejects_declared_input_digest_mismatch(gate_inputs):
    case = gate_inputs
    relocated_root = case["root"].with_name(case["root"].name + "-relocated")
    shutil.copytree(case["root"], relocated_root)
    relocated_run = {
        key: relocated_root / value.relative_to(case["root"])
        for key, value in case["run"].items()
    }
    artifact = _artifact_path(relocated_run, "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    declared = next(item for item in envelope["inputs"] if item["scope"] == "artifact")
    declared_path = relocated_run["metadata"].parent / declared["path"]
    declared_path.write_bytes(declared_path.read_bytes() + b"tampered\n")

    result = _run_checker(*_single_arguments(case["manifest_path"], relocated_run))

    assert result.returncode != 0
    assert "EVIDENCE_DIGEST_MISMATCH" in result.stdout


def test_repository_only_typecheck_inputs_bind_relocated_producer_report(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "typecheck-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["metadata"].parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "typecheck-p1"
    typed_manifest = fixture_root / "manifest.txt"
    mypy_config = fixture_root / "mypy.ini"
    coverage_context = fixture_root / "context.json"
    raw["coverage_context_path"] = str(coverage_context)
    raw["coverage_context_sha256"] = _sha256(coverage_context)
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    envelope["inputs"] = [
        {
            "kind": "typed-manifest",
            "scope": "repository",
            "path": typed_manifest.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256(typed_manifest),
        },
        {
            "kind": "mypy-config",
            "scope": "repository",
            "path": mypy_config.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256(mypy_config),
        },
        {
            "kind": "coverage-context",
            "scope": "repository",
            "path": coverage_context.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256(coverage_context),
        },
    ]
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "typecheck-report")
    relocated_root = case["root"].with_name(case["root"].name + "-relocated")
    shutil.copytree(case["root"], relocated_root)
    relocated_run = {
        key: relocated_root / value.relative_to(case["root"])
        for key, value in run.items()
    }

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            relocated_run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_typecheck_inputs_must_match_the_reviewed_repository_contract(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "typecheck-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    config_input = next(
        item for item in envelope["inputs"] if item["kind"] == "mypy-config"
    )
    copied = run["dir"] / "evidence" / "copied-mypy.ini"
    shutil.copyfile(
        PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "typecheck-p1" / "mypy.ini",
        copied,
    )
    config_input.update(
        {
            "scope": "artifact",
            "path": copied.relative_to(run["dir"]).as_posix(),
            "sha256": _sha256(copied),
        }
    )
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "typecheck-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_REPORT" in result.stdout


def test_typecheck_report_must_bind_the_reviewed_config_digest(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "typecheck-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["dir"] / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["config_sha256"] = "f" * 64
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "typecheck-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "EVIDENCE_DIGEST_MISMATCH" in result.stdout


def test_typecheck_report_must_bind_the_exact_captured_output(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "typecheck-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    captured_output = next(
        item for item in envelope["outputs"] if item["kind"] == "typecheck-stdout"
    )
    captured_path = run["dir"] / captured_output["path"]
    captured_path.write_bytes(captured_path.read_bytes() + b"forged success\n")
    captured_output["sha256"] = _sha256(captured_path)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "typecheck-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "EVIDENCE_DIGEST_MISMATCH" in result.stdout


def test_typecheck_captures_may_not_be_substituted_or_aliased(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "typecheck-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    outputs = {item["kind"]: item for item in envelope["outputs"]}
    outputs["typecheck-stdout"].update(
        {
            "path": outputs["typecheck-stderr"]["path"],
            "sha256": outputs["typecheck-stderr"]["sha256"],
        }
    )
    producer_report = run["dir"] / outputs["producer-report"]["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["stdout_sha256"] = outputs["typecheck-stderr"]["sha256"]
    _write_json(producer_report, raw)
    outputs["producer-report"]["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "typecheck-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "INVALID_EVIDENCE_REPORT" in result.stdout


def test_architecture_repository_root_is_diagnostic_not_a_locator(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "architecture-types",
        "source-linux-x64",
    )
    artifact = _artifact_path(run, "architecture-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["dir"] / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["repository_root"] = "Z:/old-runner/relocated-checkout"
    _write_json(producer_report, raw)
    for output in envelope["outputs"]:
        if output["path"] == producer_output["path"]:
            output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "architecture-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "architecture-types",
            "source-linux-x64",
        )
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_performance_diagnostic_path_is_not_a_filesystem_locator(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "performance-contract",
        "isolated-linux-x64",
    )
    artifact = _artifact_path(run, "browser-isolation-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["metadata"].parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = run["metadata"].parent / bound_input["path"]
    substitute = bound_path.with_name("substituted-image-build.json")
    shutil.copyfile(bound_path, substitute)
    raw["image_build_evidence"]["path"] = str(substitute.resolve())
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "browser-isolation-report")
    benchmark_artifact = _artifact_path(run, "benchmark-report")
    benchmark_envelope = json.loads(
        benchmark_artifact.read_text(encoding="utf-8")
    )
    performance_input = next(
        item
        for item in benchmark_envelope["inputs"]
        if item["kind"] == "performance-isolation-report"
    )
    performance_input["sha256"] = _sha256(producer_report)
    benchmark_output = next(
        item
        for item in benchmark_envelope["outputs"]
        if item["kind"] == "producer-report"
    )
    benchmark_report = run["dir"] / benchmark_output["path"]
    benchmark_raw = json.loads(benchmark_report.read_text(encoding="utf-8"))
    benchmark_raw["hard_egress_isolation"]["isolation_report_sha256"] = _sha256(
        producer_report
    )
    _write_json(benchmark_report, benchmark_raw)
    benchmark_output["sha256"] = _sha256(benchmark_report)
    _write_json(benchmark_artifact, benchmark_envelope)
    _refresh_artifact_digest(run, "benchmark-report")
    metadata = json.loads(run["metadata"].read_text(encoding="utf-8"))
    metadata["isolation"]["report_sha256"] = _sha256(artifact)
    _write_json(run["metadata"], metadata)

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "performance-contract",
            "isolated-linux-x64",
        )
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_performance_image_build_must_match_current_candidate_inputs(gate_inputs):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "performance-contract",
        "isolated-linux-x64",
    )
    artifact = _artifact_path(run, "browser-isolation-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["metadata"].parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = run["metadata"].parent / bound_input["path"]
    build = json.loads(bound_path.read_text(encoding="utf-8"))
    build["inputs"][0]["sha256"] = "f" * 64
    _write_json(bound_path, build)
    bound_input["sha256"] = _sha256(bound_path)
    raw["image_build_evidence"]["inputs"] = build["inputs"]
    raw["image_build_evidence"]["sha256"] = _sha256(bound_path)
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "browser-isolation-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "performance-contract",
            "isolated-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "performance image build input digest mismatch" in result.stdout


@pytest.mark.parametrize(
    "base_image",
    [
        "bad image@sha256:" + "a" * 64,
        "bad@name@sha256:" + "a" * 64,
        "bad\nimage@sha256:" + "a" * 64,
    ],
)
def test_performance_image_build_rejects_invalid_base_image_reference(
    gate_inputs, base_image
):
    case = gate_inputs
    run = _make_run(
        case["root"],
        case["manifest_path"],
        case["manifest"],
        case["profiles"],
        "performance-contract",
        "isolated-linux-x64",
    )
    artifact = _artifact_path(run, "browser-isolation-report")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    producer_output = next(
        item for item in envelope["outputs"] if item["kind"] == "producer-report"
    )
    producer_report = run["metadata"].parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = run["metadata"].parent / bound_input["path"]
    build = json.loads(bound_path.read_text(encoding="utf-8"))
    build["base_image"] = base_image
    _write_json(bound_path, build)
    bound_input["sha256"] = _sha256(bound_path)
    raw["image_build_evidence"]["base_image"] = base_image
    raw["image_build_evidence"]["sha256"] = _sha256(bound_path)
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(run, "browser-isolation-report")

    result = _run_checker(
        *_single_arguments(
            case["manifest_path"],
            run,
            "performance-contract",
            "isolated-linux-x64",
        )
    )

    assert result.returncode != 0
    assert "base image is not repository-digest pinned" in result.stdout


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_aggregate_rejects_missing_or_invalid_job_target(gate_inputs, mutation):
    case = gate_inputs
    index_path, index, _ = _make_run_index(case)
    if mutation == "missing":
        index["runs"].pop()
    elif mutation == "duplicate":
        index["runs"].append(copy.deepcopy(index["runs"][0]))
    else:
        index["runs"][0]["target"] = "unknown-target"
    _write_json(index_path, index)

    result = _run_checker(*_index_arguments(case["manifest_path"], index_path))

    assert result.returncode != 0
    assert "RUN_INDEX" in result.stdout


def test_aggregate_cannot_hide_a_failed_test_process(gate_inputs):
    case = gate_inputs
    index_path, index, runs = _make_run_index(case)
    failed_run = runs[0]
    metadata = json.loads(failed_run["metadata"].read_text(encoding="utf-8"))
    metadata["exit_codes"]["test"] = 7
    _write_json(failed_run["metadata"], metadata)
    for entry in index["runs"]:
        if entry["run_metadata_path"] == str(failed_run["metadata"].relative_to(case["root"])):
            entry["run_metadata_sha256"] = _sha256(failed_run["metadata"])
            break
    _write_json(index_path, index)

    result = _run_checker(*_index_arguments(case["manifest_path"], index_path))

    assert result.returncode != 0
    assert "UPSTREAM_PROCESS_FAILED" in result.stdout
