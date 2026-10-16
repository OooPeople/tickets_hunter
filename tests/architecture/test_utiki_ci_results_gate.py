import copy
import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tools.utiki_negative_contract import probe_directory_name


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "tools" / "verify_utiki_ci_results.py"
EVIDENCE_LAUNCHER = PROJECT_ROOT / "tools" / "run_utiki_evidence.py"
CATALOG = PROJECT_ROOT / "ci" / "utiki-required-cases.json"
NEGATIVE_REGISTRY = PROJECT_ROOT / "ci" / "utiki-p1-negative-probes.json"
APPROVED_CATALOG_SHA256 = "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1"
PHASE = "P1"
RUN_ID = "utiki-p1-attempt-1"
CANDIDATE_SHA = "1" * 40
_NEGATIVE_CACHE: Path | None = None


def _expand_probe_value(root: Path, value: str) -> str:
    if value == "{PROBE_ROOT}":
        return str(root.resolve())
    prefix = "{PROBE_ROOT}/"
    if value.startswith(prefix):
        return str((root / value[len(prefix) :]).resolve())
    return value


def _canonical_negative_cache() -> Path:
    global _NEGATIVE_CACHE
    if _NEGATIVE_CACHE is not None:
        return _NEGATIVE_CACHE
    root = Path(tempfile.mkdtemp(prefix="utiki-negative-test-fixtures-"))
    environment = os.environ.copy()
    environment["UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "build_utiki_negative_inputs.py"),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(NEGATIVE_REGISTRY),
            "--output-root",
            str(root),
            "--candidate-sha",
            CANDIDATE_SHA,
            "--environment",
            "all",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    _NEGATIVE_CACHE = root
    return root


def _canonical_negative_fixture(
    destination: Path,
    registry_entry: dict[str, object],
    child_stdout: Path,
    child_stderr: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    if not (destination / "probe.json").is_file():
        if destination.exists():
            raise AssertionError(f"incomplete canonical fixture destination: {destination}")
        if os.environ.get("UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL") == "1":
            _internal_negative_request(destination, registry_entry)
        else:
            source = _canonical_negative_cache() / probe_directory_name(
                registry_entry
            )
            shutil.copytree(source, destination)
    request_path = destination / "probe.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    child = request["child"]
    arguments = [
        _expand_probe_value(destination, value) for value in child["arguments"]
    ]
    if child["kind"] == "repository-python":
        producer_path = PROJECT_ROOT / child["producer"]
        command = [sys.executable, str(producer_path.resolve()), *arguments]
        producer_digest = _sha256(producer_path)
    else:
        command = [sys.executable, "-m", "pytest", *arguments]
        producer_digest = hashlib.sha256(b"python-module:pytest").hexdigest()
    cwd = (
        PROJECT_ROOT
        if child["working_directory"] is None
        else Path(_expand_probe_value(destination, child["working_directory"]))
    )
    inputs = [
        {
            "kind": item["kind"],
            "path": str((destination / item["path"]).resolve()),
            "sha256": item["sha256"],
        }
        for item in request["inputs"]
    ]
    raw = {
        "schema_version": 1,
        "report_kind": "utiki-negative-probe-transcript",
        "key": {
            field: request[field]
            for field in ("case_id", "obligation_id", "variant_id", "job_id", "target")
        },
        "candidate_sha": request["candidate_sha"],
        "environment": request["environment"],
        "mutation": request["mutation"],
        "child": {
            "kind": child["kind"],
            "producer": child["producer"],
            "producer_sha256": producer_digest,
            "command": command,
            "working_directory": str(cwd.resolve()),
        },
        "inputs": inputs,
        "outputs": [
            {"kind": "stdout", "path": str(child_stdout.resolve()), "sha256": _sha256(child_stdout)},
            {"kind": "stderr", "path": str(child_stderr.resolve()), "sha256": _sha256(child_stderr)},
        ],
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
    run_dir = root / (run_dir_name or f"{job_id}-{target}")
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    mappings = _current_mappings(manifest, job_id, target)
    evidence_refs = sorted(
        {
            (evidence["kind"], evidence["path"])
            for _, mapping in mappings
            for evidence in mapping["evidence"]
        }
    )
    artifacts = []
    for support_index, (kind, artifact_relative) in enumerate(evidence_refs):
        path = run_dir / artifact_relative
        path.parent.mkdir(parents=True, exist_ok=True)
        support_stem = (
            f"a{support_index:03d}" if compact_support else path.stem
        )
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
        extra_command_args = []
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
            stdout_path.write_text(rejection_token + "\n", encoding="utf-8")
            expectation = "NONZERO"
            observed_exit = 1
            child_stdout = path.parent / f"{support_stem}.child.stdout"
            child_stderr = path.parent / f"{support_stem}.child.stderr"
            child_stdout.write_text(rejection_token + "\n", encoding="utf-8")
            child_stderr.write_text("", encoding="utf-8")
            child_kind = registry_entry["child_kind"]
            child_producer_value = registry_entry["child_producer"]
            if child_kind == "repository-python":
                child_producer = PROJECT_ROOT / child_producer_value
                child_digest = _sha256(child_producer)
                child_command = [
                    sys.executable,
                    str(child_producer),
                    "--controlled-input",
                    str(input_path.resolve()),
                ]
            else:
                child_digest = hashlib.sha256(b"python-module:pytest").hexdigest()
                child_command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(input_path.resolve()),
                ]
            probe_request = {
                "schema_version": 1,
                **negative_result,
                "candidate_sha": CANDIDATE_SHA,
                "environment": registry_entry["environment"],
                "mutation": registry_entry["mutation"],
                "rejection_token": rejection_token,
                "child": {
                    "kind": child_kind,
                    "producer": child_producer_value,
                    "arguments": ["--controlled-input", str(input_path.resolve())],
                    "working_directory": None,
                },
                "inputs": [],
            }
            input_path.write_text("controlled mutation\n", encoding="utf-8")
            probe_request["inputs"] = [
                {
                    "kind": registry_entry["input_kinds"][0],
                    "path": input_path.name,
                    "sha256": _sha256(input_path),
                }
            ]
            raw_negative = {
                "schema_version": 1,
                "report_kind": "utiki-negative-probe-transcript",
                "key": {
                    **negative_result,
                },
                "candidate_sha": CANDIDATE_SHA,
                "environment": registry_entry["environment"],
                "mutation": registry_entry["mutation"],
                "child": {
                    "kind": child_kind,
                    "producer": child_producer_value,
                    "producer_sha256": child_digest,
                    "command": child_command,
                    "working_directory": str(PROJECT_ROOT.resolve()),
                },
                "inputs": [
                    {
                        "kind": registry_entry["input_kinds"][0],
                        "path": str(input_path.resolve()),
                        "sha256": _sha256(input_path),
                    }
                ],
                "outputs": [
                    {
                        "kind": "stdout",
                        "path": str(child_stdout.resolve()),
                        "sha256": _sha256(child_stdout),
                    },
                    {
                        "kind": "stderr",
                        "path": str(child_stderr.resolve()),
                        "sha256": _sha256(child_stderr),
                    },
                ],
                "observed_exit_code": 1,
                "rejection_token": rejection_token,
                "isolation_proof": None,
                "status": "EXPECTED_REJECTION",
            }
            canonical_fixture = True
            probe_request_path, probe_request, raw_negative = (
                _canonical_negative_fixture(
                    path.parent / f"{support_stem}.probe",
                    registry_entry,
                    child_stdout,
                    child_stderr,
                )
            )
            if job_id == "browser-contract-isolated":
                isolation_proof = path.parent / f"{support_stem}.isolation.json"
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
                    "path": str(isolation_proof.resolve()),
                    "sha256": _sha256(isolation_proof),
                }
            producer_report = path.parent / f"{support_stem}.producer.json"
            if not canonical_fixture:
                probe_request_path = path.parent / f"{support_stem}.probe.json"
                probe_request["inputs"][0]["path"] = input_path.name
            _write_json(probe_request_path, probe_request)
            _write_json(producer_report, raw_negative)
            outputs.append(
                {
                    "kind": "producer-report",
                    "scope": "evidence",
                    "path": producer_report.name,
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
                coverage_context = (
                    path.parent / f"{support_stem}.coverage-context.json"
                )
                _write_json(
                    coverage_context,
                    {
                        "schema_version": 1,
                        "coverage_kind": "controlled-runner-contract",
                        "purpose": "Synthetic checker contract fixture.",
                        "production_scope_status": "NOT_CLAIMED",
                        "legacy_seed_scope_status": "KNOWN_FAILING_DEBT",
                    },
                )
                producer_payloads[kind]["coverage_context_sha256"] = _sha256(
                    coverage_context
                )
                extra_inputs.append(
                    {
                        "kind": "coverage-context",
                        "scope": "evidence",
                        "path": coverage_context.name,
                        "sha256": _sha256(coverage_context),
                    }
                )
                extra_command_args.append(str(coverage_context.resolve()))
            if job_id == "performance-contract" and kind == "browser-isolation-report":
                sibling_files = {
                    "inner_report_sha256": path.parent / "inner-performance-report.json",
                    "benchmark_report_sha256": path.parent / "raw-benchmark-report.json",
                    "junit_sha256": path.parent / "performance.xml",
                    "reporter_result_sha256": path.parent / "performance-reporter-result.json",
                }
                for index, sibling in enumerate(sibling_files.values(), start=1):
                    sibling.write_text(f"controlled performance output {index}\n", encoding="utf-8")
                build_evidence = path.parent / "performance-image-build.json"
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
                        "scope": "evidence",
                        "path": build_evidence.name,
                        "sha256": _sha256(build_evidence),
                    }
                )
                extra_command_args.append(str(build_evidence.resolve()))
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
                    "performance-contract": path.parent / "verified-performance-contract.json",
                    "performance-isolation-report": path.parent / "verified-performance-isolation.json",
                    "raw-benchmark-report": path.parent / "verified-raw-benchmark.json",
                    "managed-junit": path.parent / "verified-performance.xml",
                    "reporter-result": path.parent / "verified-reporter-result.json",
                }
                for index, (input_kind, input_file) in enumerate(
                    performance_inputs.items(), start=1
                ):
                    input_file.write_text(
                        f"controlled {input_kind} {index}\n", encoding="utf-8"
                    )
                    extra_inputs.append(
                        {
                            "kind": input_kind,
                            "scope": "evidence",
                            "path": input_file.name,
                            "sha256": _sha256(input_file),
                        }
                    )
                    extra_command_args.append(str(input_file.resolve()))
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
                            performance_inputs["performance-contract"]
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
            producer_report = path.parent / f"{support_stem}.producer.json"
            _write_json(producer_report, producer_payloads[kind])
            stdout_path.write_text(
                producer_report.read_text(encoding="utf-8"), encoding="utf-8"
            )
            outputs.append(
                {
                    "kind": "producer-report",
                    "scope": "evidence",
                    "path": producer_report.name,
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
                    "scope": "evidence",
                    "path": stdout_path.name,
                    "sha256": _sha256(stdout_path),
                },
                {
                    "kind": "stderr",
                    "scope": "evidence",
                    "path": stderr_path.name,
                    "sha256": _sha256(stderr_path),
                },
            ]
        )
        if kind == "checker-exit-nonzero":
            producer_command = [
                sys.executable,
                str(producer),
                "--probe-input",
                str(probe_request_path.resolve()),
                "--probe-registry",
                str(NEGATIVE_REGISTRY.resolve()),
                "--producer-report",
                str(producer_report.resolve()),
            ]
        elif kind == "architecture-report":
            producer_command = [
                sys.executable,
                str(producer),
                "--root",
                str(PROJECT_ROOT.resolve()),
                "--core-inventory",
                str(input_path.resolve()),
                "--candidate-sha",
                CANDIDATE_SHA,
                "--strict",
                str(producer_report.resolve()),
            ]
        else:
            producer_command = [
                sys.executable,
                str(producer),
                "--controlled",
                str(input_path.resolve()),
                str(producer_report.resolve()),
                *extra_command_args,
            ]
        value = {
            "schema_version": 1,
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
                "command": producer_command,
            },
            "launcher": {
                "path": launcher.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256(launcher),
            },
            "inputs": [
                {
                    "kind": "injected-probe" if kind == "checker-exit-nonzero" else "configuration",
                    "scope": "evidence",
                    "path": (
                        probe_request_path.relative_to(path.parent).as_posix()
                        if kind == "checker-exit-nonzero"
                        else input_path.name
                    ),
                    "sha256": _sha256(
                        probe_request_path
                        if kind == "checker-exit-nonzero"
                        else input_path
                    ),
                },
                *extra_inputs,
                *(
                    [
                        {
                            "kind": "probe-registry",
                            "scope": "repository",
                            "path": NEGATIVE_REGISTRY.relative_to(PROJECT_ROOT).as_posix(),
                            "sha256": _sha256(NEGATIVE_REGISTRY),
                        }
                    ]
                    if kind == "checker-exit-nonzero"
                    else []
                ),
            ],
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
    producer_report = artifact.parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    raw["observed_exit_code"] = envelope["observed_exit_code"] + 1
    _write_json(producer_report, raw)
    producer_output["sha256"] = _sha256(producer_report)
    _write_json(artifact, envelope)
    _refresh_artifact_digest(case["run"], "checker-exit-nonzero")

    result = _run_checker(*_single_arguments(case["manifest_path"], case["run"]))

    assert result.returncode != 0
    assert "INVALID_NEGATIVE_PROBE" in result.stdout


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
    producer_payload = evidence_dir / "architecture-report.producer.json"
    payload = json.loads(producer_payload.read_text(encoding="utf-8"))
    payload["checked_files"] = []
    _write_json(producer_payload, payload)
    artifact = evidence_dir / "architecture-report.json"
    report = json.loads(artifact.read_text(encoding="utf-8"))
    for output in report["outputs"]:
        if output["kind"] == "producer-report":
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
    assert "UNTRUSTED_EVIDENCE_PRODUCER" in result.stdout


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
                compact_support=compact,
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


def test_relocated_evidence_rejects_same_name_with_wrong_artifact_suffix(gate_inputs):
    case = gate_inputs
    artifact = _artifact_path(case["run"], "checker-exit-nonzero")
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    declared = next(item for item in envelope["inputs"] if item["scope"] == "evidence")
    declared_path = (artifact.parent / declared["path"]).resolve()
    command = envelope["producer"]["command"]
    command[command.index(str(declared_path))] = str(
        declared_path.parent / "wrong-directory" / declared_path.name
    )
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
    assert "UNBOUND_EVIDENCE_INPUT" in result.stdout


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
    declared = next(item for item in envelope["inputs"] if item["scope"] == "evidence")
    declared_path = artifact.parent / declared["path"]
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
    producer_report = artifact.parent / producer_output["path"]
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
    envelope["producer"]["command"] = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "run_utiki_typecheck.py"),
        "--root",
        str(fixture_root),
        "--manifest",
        str(typed_manifest),
        "--config",
        str(mypy_config),
        "--coverage-context",
        str(coverage_context),
        "--report",
        str(producer_report.resolve()),
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


def test_performance_image_build_cannot_substitute_same_digest_sibling(gate_inputs):
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
    producer_report = artifact.parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = artifact.parent / bound_input["path"]
    substitute = bound_path.with_name("substituted-image-build.json")
    shutil.copyfile(bound_path, substitute)
    raw["image_build_evidence"]["path"] = str(substitute.resolve())
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
    assert "performance image build path differs" in result.stdout


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
    producer_report = artifact.parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = artifact.parent / bound_input["path"]
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
    producer_report = artifact.parent / producer_output["path"]
    raw = json.loads(producer_report.read_text(encoding="utf-8"))
    bound_input = next(item for item in envelope["inputs"] if item["kind"] == "image-build")
    bound_path = artifact.parent / bound_input["path"]
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
