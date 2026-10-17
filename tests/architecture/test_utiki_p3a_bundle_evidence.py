"""P3a package evidence keeps the frozen checks attached to the target bundle."""

import copy
import hashlib

import pytest

from tools import verify_utiki_ci_results as checker


REQUIRED_CHECKS = (
    "bound source manifest is current source",
    "bound bundle manifest matches final bundle",
    "nodriver_tixcraft exists",
    "settings exists",
    "_internal present",
    "uTiki scripts match source in final bundle",
    "bot binary starts",
    "standalone frozen bundle outside source tree",
    "bot frozen uTiki resource self-test",
    "settings frozen resource self-test",
    "missing frozen uTiki script rejected",
    "mixed frozen uTiki script rejected",
    "corrupt frozen uTiki production script rejected",
)


def _legal(tmp_path, target="macos-arm64"):
    source = tmp_path / "source-manifest.json"
    bundle = tmp_path / "bundle-manifest.json"
    source.write_bytes(b'{"scripts":{"date_snapshot":"fixed"}}\n')
    bundle.write_bytes(source.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    checks = []
    names = list(REQUIRED_CHECKS)
    if target == "windows-x64":
        names.extend((
            "Windows cp950 frozen bot resource self-test",
            "Windows cp950 frozen settings resource self-test",
        ))
    for name in names:
        detail = ""
        if name in {
            "bot frozen uTiki resource self-test",
            "settings frozen resource self-test",
            "Windows cp950 frozen bot resource self-test",
            "Windows cp950 frozen settings resource self-test",
        }:
            detail = "exit 0"
        if name in {
            "missing frozen uTiki script rejected",
            "mixed frozen uTiki script rejected",
            "corrupt frozen uTiki production script rejected",
        }:
            detail = "exit 1"
        checks.append({"name": name, "status": "PASS", "detail": detail})
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "exit_code": 0,
        "platform": "win32" if target == "windows-x64" else "darwin",
        "architecture": "AMD64" if target == "windows-x64" else "arm64",
        "source_manifest_sha256": digest,
        "bundle_manifest_sha256": digest,
        "checks": checks,
    }
    return payload, {"target": target}, {
        "source-manifest": source, "bundle-manifest": bundle,
    }


def test_p3a_bundle_report_accepts_complete_bound_native_checks(tmp_path):
    payload, metadata, inputs = _legal(tmp_path)
    checker._validate_bundle_payload(payload, metadata, inputs)
    assert checker._expected_evidence_producer("bundle-report", "package-smoke") == (
        "build_scripts/build_local.py"
    )
    assert checker._expected_input_contract("bundle-report", "package-smoke", "P3a") == {
        "source-manifest": ("repository", "src/platforms/utiki/scripts/manifest.json"),
        "bundle-manifest": ("artifact", "evidence/bundle-manifest.json"),
    }


def test_p3a_windows_bundle_report_requires_cp950_frozen_checks(tmp_path):
    payload, metadata, inputs = _legal(tmp_path, target="windows-x64")
    checker._validate_bundle_payload(payload, metadata, inputs)
    payload["checks"] = [
        item for item in payload["checks"]
        if item["name"] != "Windows cp950 frozen settings resource self-test"
    ]
    with pytest.raises(checker.GateError) as error:
        checker._validate_bundle_payload(payload, metadata, inputs)
    assert error.value.code == "INVALID_EVIDENCE_CONTENT"


@pytest.mark.parametrize(
    "change, expected_code",
    [
        ("missing-check", "INVALID_EVIDENCE_CONTENT"),
        ("wrong-rejection", "INVALID_EVIDENCE_CONTENT"),
        ("wrong-platform", "INVALID_EVIDENCE_CONTENT"),
        ("stale-bundle", "EVIDENCE_DIGEST_MISMATCH"),
    ],
)
def test_p3a_bundle_report_rejects_specific_wrong_input(tmp_path, change, expected_code):
    payload, metadata, inputs = _legal(tmp_path)
    payload = copy.deepcopy(payload)
    if change == "missing-check":
        payload["checks"] = [
            item for item in payload["checks"]
            if item["name"] != "settings frozen resource self-test"
        ]
    elif change == "wrong-rejection":
        next(item for item in payload["checks"] if item["name"] ==
             "missing frozen uTiki script rejected")["detail"] = "exit 0"
    elif change == "wrong-platform":
        payload["architecture"] = "x86_64"
    else:
        inputs["bundle-manifest"].write_bytes(b"different final bytes\n")
    with pytest.raises(checker.GateError) as error:
        checker._validate_bundle_payload(payload, metadata, inputs)
    assert error.value.code == expected_code
