"""The documented source command works from an arbitrary current directory."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
