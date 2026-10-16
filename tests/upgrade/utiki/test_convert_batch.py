"""Batch upgrade validates all inputs before writing and reports partial output."""

import json
from pathlib import Path

import pytest

from tools.migrations import convert_utiki_config as converter
from utiki_config import ConfigError


def _profile(path: Path, keyword: str) -> bytes:
    content = json.dumps(
        {
            "ctbc": {"ticket_type_keyword": keyword},
            "accounts": {"password": "fixture-private-value"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    path.write_bytes(content)
    return content


def _requests(first: Path, second: Path) -> tuple[converter.ProfileRequest, ...]:
    return (
        converter.ProfileRequest(first, "first.json"),
        converter.ProfileRequest(second, "second.json"),
    )


def test_valid_batch_writes_both_profiles_and_manifest(tmp_path: Path) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    original_first = _profile(first, "全票")
    original_second = _profile(second, "半票")
    output_dir = tmp_path / "converted"

    result = converter.write_profile_batch(_requests(first, second), output_dir)

    assert result.status == "PASS"
    assert [entry.status for entry in result.entries] == ["PASS", "PASS"]
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["entries"] == [
        {"source_name": entry.source_name, "output_name": entry.output_name,
         "source_sha256": entry.source_sha256, "status": entry.status,
         "output_sha256": entry.output_sha256, "error_code": None}
        for entry in result.entries
    ]
    assert "fixture-private-value" not in (output_dir / "manifest.json").read_text(encoding="utf-8")
    assert json.loads((output_dir / "first.json").read_text(encoding="utf-8"))["utiki"]["ticket_type_keyword"] == "全票"
    assert json.loads((output_dir / "second.json").read_text(encoding="utf-8"))["utiki"]["ticket_type_keyword"] == "半票"
    assert first.read_bytes() == original_first
    assert second.read_bytes() == original_second


def test_invalid_second_input_prevents_any_output(tmp_path: Path) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    original_first = _profile(first, "全票")
    second.write_text('{"ctbc": {"unknown": "fixture-private-value"}}', encoding="utf-8")
    output_dir = tmp_path / "converted"

    with pytest.raises(ConfigError) as error:
        converter.write_profile_batch(_requests(first, second), output_dir)

    assert error.value.code == "UNKNOWN_BEHAVIOR_KEY"
    assert first.read_bytes() == original_first
    assert not output_dir.exists()


def test_second_write_failure_reports_partial_batch_without_touching_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    third = tmp_path / "source-third.json"
    original_first = _profile(first, "全票")
    original_second = _profile(second, "半票")
    original_third = _profile(third, "學生票")
    output_dir = tmp_path / "converted"
    original_write = converter.write_new_profile

    def fail_second(prepared: converter.PreparedProfile, target: Path) -> str:
        if target.name == "second.json":
            raise OSError("fixture-disk-full")
        return original_write(prepared, target)

    monkeypatch.setattr(converter, "write_new_profile", fail_second)
    requests = _requests(first, second) + (converter.ProfileRequest(third, "third.json"),)
    result = converter.write_profile_batch(requests, output_dir)

    assert result.status == "PARTIAL_FAILURE"
    assert [entry.status for entry in result.entries] == ["PASS", "FAILED", "NOT_RUN"]
    assert result.entries[1].error_code == "WRITE_FAILED"
    assert result.entries[1].output_sha256 is None
    assert (output_dir / "first.json").exists()
    assert not (output_dir / "second.json").exists()
    assert not (output_dir / "third.json").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PARTIAL_FAILURE"
    assert manifest["entries"][1]["status"] == "FAILED"
    assert manifest["entries"][1]["error_code"] == "WRITE_FAILED"
    assert manifest["entries"][2]["status"] == "NOT_RUN"
    assert first.read_bytes() == original_first
    assert second.read_bytes() == original_second
    assert third.read_bytes() == original_third


def test_batch_rejects_unsafe_or_duplicate_outputs_before_write(tmp_path: Path) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    _profile(first, "全票")
    _profile(second, "半票")
    output_dir = tmp_path / "converted"
    for name in (
        "../escape.json", "manifest.json", "Manifest.json", "MANIFEST.JSON",
        "CON", "first.json", "bad?.json", "bad<name>.json", "bad|name.json",
        "bad\x01name.json",
    ):
        requests = (
            converter.ProfileRequest(first, "first.json"),
            converter.ProfileRequest(second, name),
        )
        with pytest.raises(ConfigError) as error:
            converter.write_profile_batch(requests, output_dir)
        assert error.value.code in {"INVALID_OUTPUT_NAME", "DUPLICATE_BATCH_ENTRY"}
        assert not output_dir.exists()


def test_unicode_equivalent_output_names_are_rejected_before_write(tmp_path: Path) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    _profile(first, "全票")
    _profile(second, "半票")
    output_dir = tmp_path / "converted"
    requests = (
        converter.ProfileRequest(first, "é.json"),
        converter.ProfileRequest(second, "e\u0301.json"),
    )

    with pytest.raises(ConfigError) as error:
        converter.write_profile_batch(requests, output_dir)

    assert error.value.code == "DUPLICATE_BATCH_ENTRY"
    assert not output_dir.exists()


def test_batch_syncs_parent_directory_before_reporting_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "source-first.json"
    second = tmp_path / "source-second.json"
    _profile(first, "全票")
    _profile(second, "半票")
    output_dir = tmp_path / "converted"
    synced: list[Path] = []
    original_sync = converter._sync_directory

    def record_sync(directory: Path) -> None:
        synced.append(directory)
        original_sync(directory)

    monkeypatch.setattr(converter, "_sync_directory", record_sync)
    result = converter.write_profile_batch(_requests(first, second), output_dir)

    assert result.status == "PASS"
    assert output_dir.parent in synced
    assert output_dir in synced
