"""Native P3a bundle diagnostics, not formal managed JUnit evidence.

This file is named outside pytest's default test pattern. The native bundle
workflow selects it explicitly, with paths to that same job's raw report and
final assembled bundle. A source-only test run must not collect these nodes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

import pytest


@pytest.fixture(scope="module")
def native_bundle() -> dict[str, object]:
    target = os.environ["UTIKI_P3A_TARGET"]
    expected = {
        "windows-x64": ("win32", {"AMD64", "x86_64"}),
        "macos-arm64": ("darwin", {"arm64"}),
    }
    assert target in expected
    expected_platform, expected_architectures = expected[target]
    assert sys.platform == expected_platform
    assert platform.machine() in expected_architectures

    report_path = Path(os.environ["UTIKI_P3A_BUNDLE_REPORT"])
    source_manifest = Path(os.environ["UTIKI_P3A_SOURCE_MANIFEST"])
    bundle_root = Path(os.environ["UTIKI_P3A_BUNDLE_ROOT"])
    bundle_manifest = (
        bundle_root / "_internal/platforms/utiki/scripts/manifest.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["status"] == "PASS" and report["exit_code"] == 0
    assert report["platform"] == expected_platform
    assert report["architecture"] in expected_architectures
    source_bytes = source_manifest.read_bytes()
    assert source_bytes == bundle_manifest.read_bytes()
    manifest_digest = hashlib.sha256(source_bytes).hexdigest()
    assert report["source_manifest_sha256"] == manifest_digest
    assert report["bundle_manifest_sha256"] == manifest_digest

    checks = report["checks"]
    assert isinstance(checks, list)
    assert len(checks) == len({entry["name"] for entry in checks})
    assert all(entry["status"] == "PASS" for entry in checks)
    return {"target": target, "checks": {entry["name"]: entry for entry in checks}}


def _detail(bundle: dict[str, object], name: str) -> str:
    checks = bundle["checks"]
    assert isinstance(checks, dict)
    entry = checks[name]
    assert entry["status"] == "PASS"
    detail = entry["detail"]
    assert isinstance(detail, str)
    return detail


def _nonzero(bundle: dict[str, object], name: str) -> None:
    assert re.fullmatch(r"exit [1-9][0-9]*", _detail(bundle, name))


def test_a04_corrupt_production_resource_rejected(native_bundle):
    _nonzero(native_bundle, "corrupt frozen uTiki production script rejected")


def test_h09_externalized_resource_in_final_bundle(native_bundle):
    _detail(native_bundle, "uTiki scripts match source in final bundle")
    assert _detail(native_bundle, "bot frozen uTiki resource self-test") == "exit 0"
    assert _detail(native_bundle, "settings frozen resource self-test") == "exit 0"


def test_h09_missing_resource_rejected(native_bundle):
    _nonzero(native_bundle, "missing frozen uTiki script rejected")


def test_h09_digest_mismatch_rejected(native_bundle):
    _nonzero(native_bundle, "mixed frozen uTiki script rejected")


def test_p01_frozen_resource_works_outside_source_tree(native_bundle):
    _detail(native_bundle, "standalone frozen bundle outside source tree")
    assert _detail(native_bundle, "bot frozen uTiki resource self-test") == "exit 0"


def test_diagnostic_bot_resource_entry(native_bundle):
    _detail(native_bundle, "nodriver_tixcraft exists")
    assert _detail(native_bundle, "bot frozen uTiki resource self-test") == "exit 0"


def test_diagnostic_settings_resource_entry(native_bundle):
    _detail(native_bundle, "settings exists")
    assert _detail(native_bundle, "settings frozen resource self-test") == "exit 0"


if sys.platform == "win32":
    def test_p02_windows_cp950_bundle(native_bundle):
        assert native_bundle["target"] == "windows-x64"
        assert _detail(
            native_bundle, "Windows cp950 frozen bot resource self-test"
        ) == "exit 0"
        assert _detail(
            native_bundle, "Windows cp950 frozen settings resource self-test"
        ) == "exit 0"
