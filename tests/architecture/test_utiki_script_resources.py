"""P3a production script resources retain their original browser contract."""

import asyncio
import hashlib
import shutil
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


def test_date_snapshot_script_matches_p2_browser_bytes():
    script = script_resources.load_script("date_snapshot")
    assert hashlib.sha256(script.encode("utf-8")).hexdigest() == DATE_SNAPSHOT_P2_SHA256
    assert "__ticketsHunterUtikiDateSnapshot" in script
    assert script_resources.verify_script_resources() == hashlib.sha256(
        (script_resources.RESOURCE_DIR / "manifest.json").read_bytes()
    ).hexdigest()


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
