"""Current production scripts are verified and fail closed on missing bytes."""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nodriver_common import CONST_NATIVE_INPUT_SETTER_JS
from build_scripts import build_local
from platforms.utiki.adapters import script_resources
from platforms.utiki.dom import read_date_snapshot


def test_current_script_package_integrity_and_date_reader():
    manifest = json.loads((script_resources.RESOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["scripts"]) == {
        path.stem for path in script_resources.RESOURCE_DIR.glob("*.js")
    }
    script = script_resources.load_script("date_snapshot")
    assert "__ticketsHunterUtikiDateSnapshot" in script
    assert script_resources.verify_script_resources(
        CONST_NATIVE_INPUT_SETTER_JS
    ) == hashlib.sha256(
        (script_resources.RESOURCE_DIR / "manifest.json").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("name, placeholder", [
    ("conflict_clear_recovery", "__TICKETS_HUNTER_CONFLICT_CLEAR_JSON__"),
    ("date_commit", "__TICKETS_HUNTER_DATE_INTENT_JSON__"),
    ("server_assigned_area_commit", "__TICKETS_HUNTER_INTENT_JSON__"),
    ("seat_commit", "__TICKETS_HUNTER_PLAN_JSON__"),
    ("quantity_commit", "__TICKETS_HUNTER_PLAN_JSON__"),
    ("submit", "__TICKETS_HUNTER_PLAN_JSON__"),
])
def test_script_template_binds_one_json_payload_without_reordering(name, placeholder):
    original = script_resources.load_script(name)
    assert original.count(placeholder) == 1
    encoded = '{"safe":true}'
    assert script_resources.assemble_script(name, {placeholder: encoded}) == (
        original.replace(placeholder, encoded, 1)
    )


def test_inline_login_payload_does_not_reparse_placeholder_literals():
    account = 'user__UTIKI_LOGIN_GUARD_ARG_5__"\\\n\u2028@example.test'
    password = 'p__UTIKI_LOGIN_GUARD_ARG_0__\\\n"密碼'
    values = (
        "write", "https://tickets.example.test", "document-1",
        "https://tickets.example.test", account, password, True, True,
    )
    assembled = script_resources.assemble_script("inline_login_guarded", {
        f"__UTIKI_LOGIN_GUARD_ARG_{index}__": json.dumps(value)
        for index, value in enumerate(values)
    }, native_setter_fragment=CONST_NATIVE_INPUT_SETTER_JS)
    assert json.dumps(account) in assembled
    assert json.dumps(password) in assembled



def test_native_input_setter_fragment_is_one_verified_shared_source(tmp_path, monkeypatch):
    expected_digest = "89fd95bde60ac93802c32e061e7239b6e973344beef12626b253070ef335723e"
    assert hashlib.sha256(CONST_NATIVE_INPUT_SETTER_JS.encode()).hexdigest() == expected_digest
    resource_dir = tmp_path / "scripts"
    shutil.copytree(script_resources.RESOURCE_DIR, resource_dir)
    probe = resource_dir / "setter_probe.js"
    placeholder = script_resources.NATIVE_SETTER_PLACEHOLDER
    probe.write_text(
        "(() => { " + placeholder + " return typeof setNativeInputValue === 'function'; })()\n",
        encoding="utf-8",
    )
    manifest_path = resource_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["native_setter_sha256"] == expected_digest
    manifest["scripts"]["setter_probe"] = hashlib.sha256(probe.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    monkeypatch.setattr(script_resources, "RESOURCE_DIR", resource_dir)
    script_resources._manifest.cache_clear()
    script_resources.load_script.cache_clear()
    script_resources.verified_native_setter_fragment.cache_clear()
    try:
        with pytest.raises(script_resources.ScriptResourceError, match="not supplied"):
            script_resources.assemble_script("setter_probe")
        assembled = script_resources.assemble_script(
            "setter_probe", native_setter_fragment=CONST_NATIVE_INPUT_SETTER_JS
        )
        assert placeholder not in assembled
        assert assembled.count(CONST_NATIVE_INPUT_SETTER_JS) == 1
        assert script_resources.verify_script_resources(CONST_NATIVE_INPUT_SETTER_JS)
        manifest["native_setter_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        script_resources._manifest.cache_clear()
        script_resources.load_script.cache_clear()
        script_resources.verified_native_setter_fragment.cache_clear()
        with pytest.raises(script_resources.ScriptResourceError, match="setter digest"):
            script_resources.assemble_script(
                "setter_probe", native_setter_fragment=CONST_NATIVE_INPUT_SETTER_JS
            )
    finally:
        script_resources._manifest.cache_clear()
        script_resources.load_script.cache_clear()
        script_resources.verified_native_setter_fragment.cache_clear()


@pytest.mark.parametrize("change", ["missing", "tampered", "manifest-tampered"])
def test_date_snapshot_resource_fails_closed(tmp_path, monkeypatch, change):
    resource_dir = tmp_path / "scripts"
    shutil.copytree(script_resources.RESOURCE_DIR, resource_dir)
    script_file = resource_dir / "date_snapshot.js"
    if change == "missing":
        script_file.unlink()
    elif change == "tampered":
        script_file.write_bytes(script_file.read_bytes().replace(b"#PerformanceListTable", b"#OtherTable"))
    else:
        manifest = resource_dir / "manifest.json"
        manifest.write_bytes(manifest.read_bytes().replace(b"a6211456", b"00000000"))
    monkeypatch.setattr(script_resources, "RESOURCE_DIR", resource_dir)
    script_resources._manifest.cache_clear()
    script_resources.load_script.cache_clear()
    try:
        with pytest.raises(script_resources.ScriptResourceError):
            script_resources.load_script("date_snapshot")
    finally:
        script_resources._manifest.cache_clear()
        script_resources.load_script.cache_clear()


def test_missing_date_resource_never_calls_browser(tmp_path, monkeypatch):
    class FakeTab:
        def __init__(self):
            self.calls = 0

        async def evaluate(self, _script):
            self.calls += 1
            return None

    resource_dir = tmp_path / "scripts"
    shutil.copytree(script_resources.RESOURCE_DIR, resource_dir)
    (resource_dir / "date_snapshot.js").unlink()
    monkeypatch.setattr(script_resources, "RESOURCE_DIR", resource_dir)
    script_resources._manifest.cache_clear()
    script_resources.load_script.cache_clear()
    tab = FakeTab()
    try:
        with pytest.raises(script_resources.ScriptResourceError):
            asyncio.run(read_date_snapshot(tab))
        assert tab.calls == 0
    finally:
        script_resources._manifest.cache_clear()
        script_resources.load_script.cache_clear()


@pytest.mark.parametrize("second_content, expected", [(b"same", True), (b"mixed", False)])
def test_final_internal_merge_rejects_mixed_resources(
    tmp_path, monkeypatch, second_content, expected
):
    root = tmp_path / "repo"
    dist = root / "dist"
    stage = dist / "tickets_hunter"
    (root / "src" / "assets").mkdir(parents=True)
    (root / "src" / "www").mkdir(parents=True)
    for name, content in (("nodriver_tixcraft", b"same"),
                          ("settings", second_content)):
        bundle = dist / name
        bundle.mkdir(parents=True)
        (bundle / f"{name}{build_local.EXE_SUFFIX}").write_bytes(b"exe")
        resource = bundle / "_internal" / "platforms" / "utiki" / "scripts"
        resource.mkdir(parents=True)
        (resource / "date_snapshot.js").write_bytes(content)
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "DIST", dist)
    monkeypatch.setattr(build_local, "STAGE", stage)
    build_local.results.clear()
    assert build_local.assemble() is expected
    if not expected:
        assert (stage / "_internal" / "platforms" / "utiki" /
                "scripts" / "date_snapshot.js").read_bytes() == b"same"
        assert any(name == "_internal content conflict" and not ok
                   for name, ok, _ in build_local.results)


def test_base_library_metadata_only_difference_is_not_content_conflict(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    for target, timestamp in ((first, (2020, 1, 1, 0, 0, 0)),
                              (second, (2021, 1, 1, 0, 0, 0))):
        with ZipFile(target, "w") as archive:
            archive.writestr(ZipInfo("same.pyc", timestamp), b"same")
    assert first.read_bytes() != second.read_bytes()
    assert build_local.equivalent_base_library(first, second)
    with ZipFile(second, "w") as archive:
        archive.writestr("same.pyc", b"mixed")
    assert not build_local.equivalent_base_library(first, second)


def test_assemble_only_rejects_unknown_collision_before_release(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    dist = root / "dist"
    for name, payload in (("nodriver_tixcraft", b"bot"),
                          ("settings", b"different")):
        bundle = dist / name
        bundle.mkdir(parents=True)
        (bundle / f"{name}{build_local.EXE_SUFFIX}").write_bytes(b"exe")
        unknown = bundle / "_internal" / "unrelated" / "config.dat"
        unknown.parent.mkdir(parents=True)
        unknown.write_bytes(payload)
    (root / "src" / "assets").mkdir(parents=True)
    (root / "src" / "www").mkdir(parents=True)
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "DIST", dist)
    monkeypatch.setattr(build_local, "STAGE", dist / "tickets_hunter")
    monkeypatch.setattr(build_local, "preflight", lambda: True)
    monkeypatch.setattr(build_local, "smoke", lambda: pytest.fail("smoke must not run"))
    report_path = tmp_path / "bundle-report.json"
    monkeypatch.setattr(sys, "argv", [
        "build_local.py", "--assemble-only", "--keep", "--report", str(report_path)
    ])
    build_local.results.clear()

    assert build_local.main() == 1
    assert any(name == "_internal content conflict" and not ok
               for name, ok, _ in build_local.results)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["exit_code"] == 1
    assert any(item["name"] == "_internal content conflict" and item["status"] == "FAIL"
               for item in report["checks"])


def test_package_report_binds_actual_source_and_final_manifest_bytes(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = root / "src/platforms/utiki/scripts/manifest.json"
    bundle = root / "dist/tickets_hunter/_internal/platforms/utiki/scripts/manifest.json"
    source.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    source.write_bytes(b'{"source":true}\n')
    bundle.write_bytes(source.read_bytes())
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "STAGE", root / "dist/tickets_hunter")
    build_local.results.clear()
    build_local.step("bot frozen uTiki resource self-test", True)
    build_local.step("missing frozen uTiki script rejected", True, "exit 2")
    report_path = tmp_path / "raw-bundle-report.json"
    build_local._write_report(report_path, 0)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["status"] == "PASS"
    assert report["exit_code"] == 0
    assert report["source_manifest_sha256"] == expected_digest
    assert report["bundle_manifest_sha256"] == expected_digest
    assert [item["name"] for item in report["checks"]] == [
        "bot frozen uTiki resource self-test",
        "missing frozen uTiki script rejected",
    ]
    bundle.write_bytes(b'{"mixed":true}\n')
    build_local._write_report(report_path, 1)
    mismatched = json.loads(report_path.read_text(encoding="utf-8"))
    assert mismatched["status"] == "FAIL"
    assert mismatched["bundle_manifest_sha256"] != expected_digest


def test_package_report_rejects_stale_captured_bundle_manifest(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = root / "src/platforms/utiki/scripts/manifest.json"
    final = root / "dist/tickets_hunter/_internal/platforms/utiki/scripts/manifest.json"
    source.parent.mkdir(parents=True)
    final.parent.mkdir(parents=True)
    source.write_bytes(b"current manifest\n")
    final.write_bytes(source.read_bytes())
    captured = tmp_path / "captured-manifest.json"
    captured.write_bytes(final.read_bytes())
    report_path = tmp_path / "bundle-report.json"
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "DIST", root / "dist")
    monkeypatch.setattr(build_local, "STAGE", root / "dist/tickets_hunter")
    monkeypatch.setattr(build_local, "IS_MACOS", False)
    monkeypatch.setattr(build_local, "preflight", lambda: True)
    monkeypatch.setattr(build_local, "verify_layout", lambda: build_local.step("layout", True))
    monkeypatch.setattr(build_local, "smoke", lambda: build_local.step("frozen smoke", True))
    monkeypatch.setattr(sys, "argv", [
        "build_local.py", "--skip-build", "--keep", "--report", str(report_path),
        "--source-manifest", str(source), "--bundle-manifest", str(captured),
    ])
    assert build_local.main() == 0
    legal = json.loads(report_path.read_text(encoding="utf-8"))
    assert legal["status"] == "PASS"
    assert any(item["name"] == "bound bundle manifest matches final bundle"
               and item["status"] == "PASS" for item in legal["checks"])

    captured.write_bytes(b"stale manifest\n")
    assert build_local.main() == 1
    stale = json.loads(report_path.read_text(encoding="utf-8"))
    assert stale["status"] == "FAIL"
    assert any(item["name"] == "bound bundle manifest matches final bundle"
               and item["status"] == "FAIL" for item in stale["checks"])


def test_windows_layout_produces_platform_stable_bundle_check_names(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = root / "src/platforms/utiki/scripts"
    final = root / "dist/tickets_hunter"
    bundled_scripts = final / "_internal/platforms/utiki/scripts"
    source.mkdir(parents=True)
    bundled_scripts.mkdir(parents=True)
    for name, content in (("manifest.json", b"{}\n"), ("date_snapshot.js", b"(() => 1)()\n")):
        (source / name).write_bytes(content)
        (bundled_scripts / name).write_bytes(content)
    for name in ("nodriver_tixcraft.exe", "settings.exe"):
        (final / name).write_bytes(b"frozen executable")
    (final / "www").mkdir()
    (final / "www/settings.html").write_bytes(b"settings")
    (final / "_internal/model.onnx").write_bytes(b"model")
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "STAGE", final)
    monkeypatch.setattr(build_local, "IS_WINDOWS", True)
    monkeypatch.setattr(build_local, "EXE_SUFFIX", ".exe")
    build_local.results.clear()
    assert build_local.verify_layout()
    names = [name for name, _, _ in build_local.results]
    assert "nodriver_tixcraft exists" in names
    assert "settings exists" in names
    assert "nodriver_tixcraft.exe exists" not in names
    assert "settings.exe exists" not in names


def test_macos_alias_checks_keep_distinct_resource_names(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    dist = root / "dist"
    aliases = tuple(build_local.MACOS_ALIAS_PAIRS)[:2]
    for package, content in (("nodriver_tixcraft", b"first"), ("settings", b"second")):
        bundle = dist / package
        bundle.mkdir(parents=True)
        (bundle / package).write_bytes(b"frozen executable")
        for relative in aliases:
            resource = bundle / "_internal" / relative
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_bytes(content)
    (root / "src/assets").mkdir(parents=True)
    (root / "src/www").mkdir(parents=True)
    monkeypatch.setattr(build_local, "ROOT", root)
    monkeypatch.setattr(build_local, "BUILD_SCRIPTS", root / "build_scripts")
    monkeypatch.setattr(build_local, "DIST", dist)
    monkeypatch.setattr(build_local, "STAGE", dist / "tickets_hunter")
    monkeypatch.setattr(build_local, "IS_MACOS", True)
    monkeypatch.setattr(build_local, "EXE_SUFFIX", "")
    monkeypatch.setattr(build_local, "expected_macos_alias", lambda *_args: True)
    build_local.results.clear()
    assert build_local.assemble()
    names = [name for name, _, _ in build_local.results]
    assert {f"canonical macOS library alias: {relative}" for relative in aliases} <= set(names)
    assert len(names) == len(set(names))


def test_release_entries_use_checked_assembly():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "build-release.yml").read_text(encoding="utf-8")
    batch = (root / "build_scripts" / "build_and_test.bat").read_text()
    assert workflow.count("python build_scripts/build_local.py --assemble-only --keep") == 2
    assert "--skip-build" not in workflow
    assert "xcopy /E /I /Y dist\\settings\\_internal" not in workflow
    assert "cp -R dist/settings/_internal" not in workflow
    assert "python build_scripts\\build_local.py --assemble-only --keep" in batch
    assert "xcopy /E /I /Y dist\\settings\\_internal" not in batch


def test_manifest_bound_scripts_have_checkout_independent_line_endings():
    root = Path(__file__).resolve().parents[2]
    relative_paths = [
        path.relative_to(root).as_posix()
        for path in sorted(script_resources.RESOURCE_DIR.glob("*.js"))
    ]
    relative_paths.append("src/platforms/utiki/scripts/manifest.json")
    completed = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *relative_paths],
        cwd=root, capture_output=True, text=True, check=True,
    )
    actual = {
        (path, attribute, value)
        for line in completed.stdout.splitlines()
        for path, attribute, value in [line.split(": ", 2)]
    }
    assert actual == {
        (path, attribute, value)
        for path in relative_paths
        for attribute, value in (("text", "set"), ("eol", "lf"))
    }
