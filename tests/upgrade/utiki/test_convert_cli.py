"""The documented source command works from an arbitrary current directory."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.migrations import convert_utiki_config as converter
from utiki_config import load_current_policy, parse_profile_json


SCRIPT = Path(__file__).resolve().parents[3] / "tools/migrations/convert_utiki_config.py"


def _run(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("option", [(), ("--dry-run",)])
def test_cli_preview_is_read_only_and_omits_profile_values(
    tmp_path: Path, option: tuple[str, ...]
) -> None:
    source = tmp_path / "old.json"
    source.write_text(
        json.dumps({"ctbc": {"ticket_type_keyword": "fixture-secret-keyword"},
                    "accounts": {"password": "fixture-private-value"}}),
        encoding="utf-8",
    )
    original = source.read_bytes()

    result = _run(tmp_path, "--input", str(source), *option)

    assert result.returncode == 0
    assert result.stdout.startswith("READY source_sha256=")
    assert "output_sha256=" in result.stdout
    assert "fixture-secret-keyword" not in result.stdout + result.stderr
    assert "fixture-private-value" not in result.stdout + result.stderr
    assert source.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["old.json"]


def test_cli_output_is_loadable_and_preserves_unrelated_values(tmp_path: Path) -> None:
    source = tmp_path / "old.json"
    output = tmp_path / "new.json"
    source.write_text(
        json.dumps({"ctbc": {"ticket_type_keyword": "全票"},
                    "accounts": {"password": "fixture-private-value"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    original = source.read_bytes()

    result = _run(tmp_path, "--input", str(source), "--output", str(output))

    assert result.returncode == 0
    assert result.stdout.startswith("WRITTEN source_sha256=")
    converted = parse_profile_json(output.read_text(encoding="utf-8"))
    assert load_current_policy(converted).ticket_type_keyword == "全票"
    assert converted["accounts"] == {"password": "fixture-private-value"}
    assert "ctbc" not in converted
    assert source.read_bytes() == original
    assert "fixture-private-value" not in result.stdout + result.stderr


def test_cli_mixed_equal_writes_current_profile_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "mixed-equal.json"
    output = tmp_path / "converted.json"
    current = {
        "ticket_type_keyword": "",
        "seat_sorting": "display",
        "seat_selection_mode": "computer",
    }
    original_profile = {
        "utiki": current,
        "ctbc": current.copy(),
        "accounts": {"password": "fixture-private-value"},
        "other_platform": {"enabled": False},
    }
    source.write_text(json.dumps(original_profile, ensure_ascii=False), encoding="utf-8")
    original_bytes = source.read_bytes()

    result = _run(tmp_path, "--input", str(source), "--output", str(output))

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("WRITTEN source_sha256=")
    assert "fixture-private-value" not in result.stdout
    assert source.read_bytes() == original_bytes
    converted = parse_profile_json(output.read_text(encoding="utf-8"))
    assert "ctbc" not in converted
    assert converted["utiki"] == current
    assert converted["accounts"] == original_profile["accounts"]
    assert converted["other_platform"] == original_profile["other_platform"]
    assert load_current_policy(converted).ticket_type_keyword == ""


def test_cli_mixed_conflict_requires_explicit_choice(tmp_path: Path) -> None:
    source = tmp_path / "conflict.json"
    output = tmp_path / "new.json"
    source.write_text(
        json.dumps({"ctbc": {"ticket_type_keyword": "全票"},
                    "utiki": {"ticket_type_keyword": ""}}, ensure_ascii=False),
        encoding="utf-8",
    )

    rejected = _run(tmp_path, "--input", str(source), "--output", str(output))
    accepted = _run(
        tmp_path, "--input", str(source), "--output", str(output),
        "--conflict-policy", "canonical",
    )

    assert rejected.returncode == 2
    assert rejected.stderr.strip() == "ERROR CONFLICT_POLICY_REQUIRED"
    assert accepted.returncode == 0
    assert load_current_policy(parse_profile_json(output.read_text(encoding="utf-8"))).ticket_type_keyword == ""


def test_cli_refuses_existing_output_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "old.json"
    output = tmp_path / "new.json"
    source.write_text('{"ctbc": {}}', encoding="utf-8")
    output.write_text("existing", encoding="utf-8")

    result = _run(tmp_path, "--input", str(source), "--output", str(output))

    assert result.returncode == 2
    assert result.stderr.strip() == "ERROR TARGET_EXISTS"
    assert output.read_text(encoding="utf-8") == "existing"


def test_cli_batch_validates_all_sources_then_writes_separate_outputs(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output_dir = tmp_path / "converted"
    first.write_text(
        json.dumps({"utiki": {"ticket_type_keyword": "", "seat_sorting": "display",
                              "seat_selection_mode": "computer"},
                    "ctbc": {"ticket_type_keyword": "", "seat_sorting": "display",
                             "seat_selection_mode": "computer"},
                    "accounts": {"password": "fixture-private-value"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    second.write_text('{"ctbc":{"ticket_type_keyword":"全票"}}', encoding="utf-8")
    originals = (first.read_bytes(), second.read_bytes())

    result = _run(
        tmp_path, "--input", str(first), "--input", str(second),
        "--output-dir", str(output_dir),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == "BATCH PASS entries=2\n"
    assert (first.read_bytes(), second.read_bytes()) == originals
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert [(entry["output_name"], entry["status"]) for entry in manifest["entries"]] == [
        ("first.json", "PASS"), ("second.json", "PASS")
    ]
    expected = (
        {"utiki": {"ticket_type_keyword": "", "seat_sorting": "display",
                   "seat_selection_mode": "computer"},
         "accounts": {"password": "fixture-private-value"}},
        {"utiki": {"ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                   "seat_selection_mode": "manual"}},
    )
    for index, source in enumerate((first, second)):
        output = output_dir / source.name
        converted = parse_profile_json(output.read_text(encoding="utf-8"))
        assert converted == expected[index]
        load_current_policy(converted)
        assert manifest["entries"][index]["source_sha256"] == hashlib.sha256(originals[index]).hexdigest()
        assert manifest["entries"][index]["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert "fixture-private-value" not in result.stdout + result.stderr
    assert "fixture-private-value" not in (output_dir / "manifest.json").read_text(encoding="utf-8")


def test_cli_batch_invalid_second_source_leaves_no_output(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output_dir = tmp_path / "converted"
    first.write_text('{"ctbc":{}}', encoding="utf-8")
    second.write_text('{"ctbc":{"unknown":"fixture-private-value"}}', encoding="utf-8")
    originals = (first.read_bytes(), second.read_bytes())

    result = _run(
        tmp_path, "--input", str(first), "--input", str(second),
        "--output-dir", str(output_dir),
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "ERROR UNKNOWN_BEHAVIOR_KEY"
    assert result.stdout == ""
    assert (first.read_bytes(), second.read_bytes()) == originals
    assert not output_dir.exists()


def test_cli_multiple_sources_require_output_directory(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"ctbc":{}}', encoding="utf-8")
    second.write_text('{"ctbc":{}}', encoding="utf-8")

    result = _run(tmp_path, "--input", str(first), "--input", str(second))

    assert result.returncode == 2
    assert result.stderr.strip() == "ERROR BATCH_OUTPUT_DIR_REQUIRED"
    assert result.stdout == ""
    assert sorted(path.name for path in tmp_path.iterdir()) == ["first.json", "second.json"]


def test_cli_partial_write_returns_nonzero_and_preserves_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output_dir = tmp_path / "converted"
    first.write_text('{"ctbc":{"ticket_type_keyword":"全票"}}', encoding="utf-8")
    second.write_text('{"ctbc":{"ticket_type_keyword":"半票"}}', encoding="utf-8")
    originals = (first.read_bytes(), second.read_bytes())
    original_write = converter.write_new_profile

    def fail_second(prepared: converter.PreparedProfile, target: Path) -> str:
        if target.name == "second.json":
            raise OSError("fixture disk full")
        return original_write(prepared, target)

    monkeypatch.setattr(converter, "write_new_profile", fail_second)
    exit_code = converter.main([
        "--input", str(first), "--input", str(second), "--output-dir", str(output_dir),
    ])

    assert exit_code == 4
    captured = capsys.readouterr()
    assert captured.out == "BATCH PARTIAL_FAILURE entries=2\n"
    assert captured.err == ""
    assert (first.read_bytes(), second.read_bytes()) == originals
    assert (output_dir / "first.json").exists()
    assert not (output_dir / "second.json").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PARTIAL_FAILURE"
    assert [(entry["status"], entry["error_code"]) for entry in manifest["entries"]] == [
        ("PASS", None), ("FAILED", "WRITE_FAILED")
    ]
