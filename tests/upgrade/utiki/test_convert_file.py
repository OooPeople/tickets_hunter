"""Source conversion writes new files without changing an original profile."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.migrations import convert_utiki_config as converter
from utiki_config import ConfigError


def _source(tmp_path: Path) -> tuple[Path, dict[str, object], bytes]:
    profile: dict[str, object] = {
        "ctbc": {"ticket_type_keyword": ""},
        "accounts": {"password": "fixture-private-value"},
        "other_platform": {"enabled": True, "count": 2},
    }
    source = tmp_path / "profile.json"
    content = json.dumps(profile, ensure_ascii=False).encode("utf-8")
    source.write_bytes(content)
    return source, profile, content


def _temporary_files(tmp_path: Path, target: Path) -> list[Path]:
    return list(tmp_path.glob(f".{target.name}.*"))


def test_new_file_round_trip_preserves_other_values_and_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, profile, original = _source(tmp_path)
    target = tmp_path / "converted.json"

    prepared = converter.prepare_profile(source)
    output_digest = converter.write_new_profile(prepared, target)

    assert prepared.source_digest == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original
    result = json.loads(target.read_text(encoding="utf-8"))
    assert "ctbc" not in result
    assert result["utiki"] == {
        "ticket_type_keyword": "",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }
    assert result["accounts"] == profile["accounts"]
    assert result["other_platform"] == profile["other_platform"]
    assert output_digest == hashlib.sha256(target.read_bytes()).hexdigest()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert "fixture-private-value" not in repr(prepared)
    assert _temporary_files(tmp_path, target) == []
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


def test_prepared_profile_representation_hides_private_values(tmp_path: Path) -> None:
    source, _, _ = _source(tmp_path)
    prepared = converter.prepare_profile(source)
    assert "fixture-private-value" not in repr(prepared)
    assert "profile.json" not in repr(prepared)


def test_changed_source_before_write_is_rejected_without_output(tmp_path: Path) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)
    source.write_bytes(original + b" ")

    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(prepared, target)

    assert error.value.code == "SOURCE_CHANGED"
    assert source.read_bytes() == original + b" "
    assert not target.exists()


def test_source_changed_while_staging_is_rejected_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)
    original_sync = converter.os.fsync

    def change_during_sync(descriptor: int) -> None:
        original_sync(descriptor)
        source.write_bytes(original + b" ")

    monkeypatch.setattr(converter.os, "fsync", change_during_sync)
    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(prepared, target)

    assert error.value.code == "SOURCE_CHANGED"
    assert source.read_bytes() == original + b" "
    assert not target.exists()
    assert _temporary_files(tmp_path, target) == []


def test_source_changed_during_publish_keeps_unconfirmed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)
    original_publish = converter._publish

    def change_after_publish(temporary: Path, destination: Path) -> None:
        original_publish(temporary, destination)
        source.write_bytes(original + b" ")

    monkeypatch.setattr(converter, "_publish", change_after_publish)
    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(prepared, target)

    assert error.value.code == "SOURCE_CHANGED_OUTPUT_UNCERTAIN"
    assert source.read_bytes() == original + b" "
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["utiki"]["ticket_type_keyword"] == ""
    assert _temporary_files(tmp_path, target) == []


def test_source_change_does_not_remove_replaced_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)
    original_publish = converter._publish

    def replace_after_publish(temporary: Path, destination: Path) -> None:
        original_publish(temporary, destination)
        destination.unlink()
        destination.write_text("other-writer", encoding="utf-8")
        source.write_bytes(original + b" ")

    monkeypatch.setattr(converter, "_publish", replace_after_publish)
    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(prepared, target)

    assert error.value.code == "SOURCE_CHANGED_OUTPUT_UNCERTAIN"
    assert target.read_text(encoding="utf-8") == "other-writer"
    assert _temporary_files(tmp_path, target) == []


def test_save_failure_keeps_source_and_removes_own_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)

    def fail_sync(_descriptor: int) -> None:
        raise OSError("fixture-sync-failure")

    monkeypatch.setattr(converter.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="fixture-sync-failure"):
        converter.write_new_profile(prepared, target)

    assert source.read_bytes() == original
    assert not target.exists()
    assert _temporary_files(tmp_path, target) == []


def test_publish_failure_keeps_source_and_removes_own_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)

    def fail_publish(_temporary: Path, _target: Path) -> None:
        raise OSError("fixture-publish-failure")

    monkeypatch.setattr(converter, "_publish", fail_publish)
    with pytest.raises(OSError, match="fixture-publish-failure"):
        converter.write_new_profile(prepared, target)

    assert source.read_bytes() == original
    assert not target.exists()
    assert _temporary_files(tmp_path, target) == []


def test_existing_target_is_not_replaced(tmp_path: Path) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    target.write_text("keep-existing", encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(converter.prepare_profile(source), target)

    assert error.value.code == "TARGET_EXISTS"
    assert source.read_bytes() == original
    assert target.read_text(encoding="utf-8") == "keep-existing"


def test_target_created_during_publish_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, original = _source(tmp_path)
    target = tmp_path / "converted.json"
    prepared = converter.prepare_profile(source)

    def create_competing_target(_temporary: Path, competing: Path) -> None:
        competing.write_text("keep-racing-target", encoding="utf-8")
        raise FileExistsError("target appeared")

    monkeypatch.setattr(converter, "_publish", create_competing_target)
    with pytest.raises(ConfigError) as error:
        converter.write_new_profile(prepared, target)

    assert error.value.code == "TARGET_EXISTS"
    assert source.read_bytes() == original
    assert target.read_text(encoding="utf-8") == "keep-racing-target"
    assert _temporary_files(tmp_path, target) == []


def test_windows_publish_uses_no_clobber_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = tmp_path / "staged.json"
    target = tmp_path / "converted.json"
    temporary.write_text("staged", encoding="utf-8")
    target.write_text("existing", encoding="utf-8")
    calls: list[tuple[Path, Path]] = []

    def refuse_existing(source: Path, destination: Path) -> None:
        calls.append((source, destination))
        raise FileExistsError("fixture-existing")

    monkeypatch.setattr(converter, "_ON_WINDOWS", True)
    monkeypatch.setattr(converter.os, "rename", refuse_existing)
    with pytest.raises(FileExistsError):
        converter._publish(temporary, target)
    assert calls == [(temporary, target)]
    assert temporary.read_text(encoding="utf-8") == "staged"
    assert target.read_text(encoding="utf-8") == "existing"


def test_invalid_json_and_unknown_legacy_key_do_not_write(tmp_path: Path) -> None:
    source = tmp_path / "profile.json"
    source.write_text('{"ctbc": {}, "ctbc": {}}', encoding="utf-8")
    with pytest.raises(ConfigError) as duplicate:
        converter.prepare_profile(source)
    assert duplicate.value.code == "DUPLICATE_JSON_KEY"

    source.write_text('{"ctbc": {"unexpected": "fixture-private-value"}}', encoding="utf-8")
    with pytest.raises(ConfigError) as unknown:
        converter.prepare_profile(source)
    assert unknown.value.code == "UNKNOWN_BEHAVIOR_KEY"
    assert "fixture-private-value" not in str(unknown.value)
    assert not (tmp_path / "converted.json").exists()
