"""P3a production script resources retain their original browser contract."""

import asyncio
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from build_scripts import build_local
from platforms.utiki.adapters import script_resources
from platforms.utiki.dom import read_date_snapshot


DATE_SNAPSHOT_P2_SHA256 = (
    "a77821050976e0030b2fb0151063ad6c081c17acf2d6451762685dae331989df"
)
DOM_SCRIPT_P2_SHA256 = {
    "conflict_clear": "86125ff4636f7672ec587f9a497dfdabc348625df339af191abb732208a4b312",
    "date_commit": "49912506cb2cc20ec1a3f2f8d117259f174d4e3486827394ebf682b9cfd39eec",
    "server_assigned_area_commit": "04f9d8359e68ae7270c8391da44ae593f4c41e0f9b6328996c3d895802fcafe9",
    "seat_snapshot": "f20d31f23697d434e15a812da3dc09f9a96e3919bf388448c71837320c4f17b1",
    "seat_commit": "b7df9252e0ca5f3091523429d5e9f4537ed1a44ff065137263ed3a5c2085193e",
    "quantity_snapshot": "6d301545cdad5a2185381eb16791d7d8159ea7a13cc5d922116a89183864dfda",
    "quantity_commit": "6e2945a4a530109895279441d89f2da7331ce35046f289fcc03ee9e73f075dfc",
    "submit": "20dd01f24d91a77f7df6f8a151a1ee220324d699aff4145c984ba2776344386d",
}
P2_TRAILING_WHITESPACE = {
    "server_assigned_area_commit": "\n\n    ",
    "seat_snapshot": "\n    ",
    "quantity_snapshot": "\n    ",
}
WORKFLOW_TEMPLATE_P2_SHA256 = {
    "fast_marker_read": ("11a02527d896d85dbee62fb6a2f6749f7f0662d9a9ef788dd52c8ec3d752f650", 1, ""),
    "fast_marker_delete": ("3f04463b9ca17d94b0f466f4938c81bb096848f2d46562985735cdfbcf3b8524", 3, ""),
    "fast_marker_malformed_delete": ("78eedd34ada53940d663f92cb062e7742713bc6b7c26b6d0c88dd8daabb936f7", 4, ""),
    "document_health": ("9505d92f0879c7e52165f4c7f238334aa9c1dc6093bfe4344fdd7f64a7870283", 2, "\n        "),
}


def test_date_snapshot_script_matches_p2_browser_bytes():
    script = script_resources.load_script("date_snapshot")
    assert hashlib.sha256(script.encode("utf-8")).hexdigest() == DATE_SNAPSHOT_P2_SHA256
    assert "__ticketsHunterUtikiDateSnapshot" in script
    assert script_resources.verify_script_resources() == hashlib.sha256(
        (script_resources.RESOURCE_DIR / "manifest.json").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("name, original_sha256", DOM_SCRIPT_P2_SHA256.items())
def test_remaining_dom_script_matches_p2_browser_bytes(name, original_sha256):
    script = script_resources.assemble_script(name)
    # Three original literals ended with indentation after their closing IIFE.
    # That non-executable trailing whitespace is omitted from the LF resource.
    original = script + P2_TRAILING_WHITESPACE.get(name, "")
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == original_sha256


@pytest.mark.parametrize("name, placeholder", [
    ("conflict_clear", "__TICKETS_HUNTER_CONFLICT_CLEAR_JSON__"),
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


@pytest.mark.parametrize("name, expected", WORKFLOW_TEMPLATE_P2_SHA256.items())
def test_workflow_template_preserves_original_script_chunks(name, expected):
    original_sha256, binding_count, trailing = expected
    script = script_resources.load_script(name)
    assert hashlib.sha256((script + trailing).encode()).hexdigest() == original_sha256
    replacements = {
        f"__UTIKI_{name.upper()}_ARG_{index}__": f'"value-{index}"'
        for index in range(binding_count)
    }
    assert all(script.count(key) == 1 for key in replacements)
    assembled = script_resources.assemble_script(name, replacements)
    for key, value in replacements.items():
        assert key not in assembled
        assert assembled.count(value) >= 1


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
    monkeypatch.setattr(sys, "argv", ["build_local.py", "--assemble-only", "--keep"])
    build_local.results.clear()

    assert build_local.main() == 1
    assert any(name == "_internal content conflict" and not ok
               for name, ok, _ in build_local.results)


def test_release_entries_use_checked_assembly():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "build-release.yml").read_text()
    batch = (root / "build_scripts" / "build_and_test.bat").read_text()
    assert workflow.count("python build_scripts/build_local.py --assemble-only --keep") == 2
    assert "--skip-build" not in workflow
    assert "xcopy /E /I /Y dist\\settings\\_internal" not in workflow
    assert "cp -R dist/settings/_internal" not in workflow
    assert "python build_scripts\\build_local.py --assemble-only --keep" in batch
    assert "xcopy /E /I /Y dist\\settings\\_internal" not in batch


def test_byte_pinned_scripts_have_checkout_independent_line_endings():
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
