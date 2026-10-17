#!/usr/bin/env python3
"""Exercise one already-built standalone upgrader with synthetic profiles.

This is a local packaging smoke check, not the full P6a release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _run(executable: Path, directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PATH"] = str(directory.parent / "empty executable path")
    return subprocess.run(
        [str(executable), *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def smoke(executable: Path) -> None:
    executable = executable.resolve(strict=True)
    _require(executable.is_file(), "frozen executable is not a regular file")
    _require((executable.parent / "_internal").is_dir(), "frozen onedir is incomplete")

    with tempfile.TemporaryDirectory(prefix="utiki frozen smoke ") as temporary_name:
        root = Path(temporary_name)
        bundle = root / "standalone bundle"
        shutil.copytree(executable.parent, bundle, symlinks=True)
        frozen = bundle / executable.name
        external_source = root / "src"
        external_source.mkdir()
        (external_source / "utiki_config.py").write_text(
            "raise RuntimeError('EXTERNAL_MODULE_SHADOWED')\n", encoding="utf-8"
        )
        work = root / "outside source 樣本"
        work.mkdir()
        empty_path = root / "empty executable path"
        empty_path.mkdir()
        _require(not any(empty_path.iterdir()), "restricted executable path is not empty")

        help_result = _run(frozen, work, "--help")
        _require("EXTERNAL_MODULE_SHADOWED" not in help_result.stderr,
                 "frozen entry imported a module outside its bundle")
        _require(help_result.returncode == 0 and "--dry-run" in help_result.stdout,
                 "frozen --help failed")
        _require(not any(work.iterdir()), "frozen --help wrote into the working directory")

        behavior = {
            "ticket_type_keyword": "",
            "seat_sorting": "display",
            "seat_selection_mode": "computer",
        }
        source = work / "舊 profile.json"
        output = work / "新 profile.json"
        profile = {
            "utiki": behavior,
            "ctbc": behavior.copy(),
            "accounts": {"password": "fixture-only-sentinel"},
            "other_platform": {"enabled": False},
        }
        source.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        before = source.read_bytes()
        source_digest = hashlib.sha256(before).hexdigest()

        preview = _run(frozen, work, "--input", str(source), "--dry-run")
        _require(preview.returncode == 0, "frozen dry-run failed")
        _require(re.fullmatch(
            rf"READY source_sha256={source_digest} output_sha256=[0-9a-f]{{64}}\n",
            preview.stdout,
        ) is not None, "frozen dry-run reported unexpected output")
        _require(source.read_bytes() == before and not output.exists(),
                 "frozen dry-run changed the profile")

        written = _run(frozen, work, "--input", str(source), "--output", str(output))
        _require(written.returncode == 0, "frozen output conversion failed")
        _require(source.read_bytes() == before, "frozen conversion changed the source")
        converted = json.loads(output.read_text(encoding="utf-8"))
        _require("ctbc" not in converted and converted.get("utiki") == behavior,
                 "frozen conversion did not produce canonical behavior")
        _require(converted.get("accounts") == profile["accounts"] and
                 converted.get("other_platform") == profile["other_platform"],
                 "frozen conversion changed unrelated fields")
        written_bytes = output.read_bytes()
        output_digest = hashlib.sha256(written_bytes).hexdigest()
        _require(preview.stdout ==
                 f"READY source_sha256={source_digest} output_sha256={output_digest}\n",
                 "frozen preview digest mismatch")
        _require(written.stdout ==
                 f"WRITTEN source_sha256={source_digest} output_sha256={output_digest}\n",
                 "frozen written digest mismatch")

        repeat = _run(frozen, work, "--input", str(source), "--output", str(output))
        _require(repeat.returncode == 2 and repeat.stderr.strip() == "ERROR TARGET_EXISTS",
                 "frozen upgrader accepted an existing output")
        _require(source.read_bytes() == before,
                 "frozen existing-output rejection changed the source")
        _require(output.read_bytes() == written_bytes,
                 "frozen upgrader changed existing output")

        conflict = work / "conflict.json"
        conflict.write_text(json.dumps({
            "utiki": {"ticket_type_keyword": ""},
            "ctbc": {"ticket_type_keyword": "全票"},
        }, ensure_ascii=False), encoding="utf-8")
        conflict_before = conflict.read_bytes()
        rejected = _run(frozen, work, "--input", str(conflict), "--dry-run")
        _require(rejected.returncode == 2 and
                 rejected.stderr.strip() == "ERROR CONFLICT_POLICY_REQUIRED",
                 "frozen upgrader did not reject an unresolved conflict")
        _require(conflict.read_bytes() == conflict_before,
                 "frozen conflict rejection changed the source")

        profile_cases = (
            (
                "legacy-only",
                {"ctbc": {"ticket_type_keyword": "全票"}},
                {"ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                 "seat_selection_mode": "manual"},
            ),
            (
                "complementary-explicit-empty",
                {"utiki": {"ticket_type_keyword": ""},
                 "ctbc": {"seat_sorting": "display", "seat_selection_mode": "computer"}},
                behavior,
            ),
            (
                "canonical-only",
                {"utiki": behavior},
                behavior,
            ),
        )
        for name, sections, expected_behavior in profile_cases:
            case_source = work / f"{name}.json"
            case_output = work / f"{name}-converted.json"
            case_profile = {**sections, "accounts": profile["accounts"]}
            case_source.write_text(
                json.dumps(case_profile, ensure_ascii=False), encoding="utf-8"
            )
            case_before = case_source.read_bytes()
            case_result = _run(
                frozen, work, "--input", str(case_source), "--output", str(case_output)
            )
            _require(case_result.returncode == 0, f"frozen {name} conversion failed")
            _require(case_source.read_bytes() == case_before,
                     f"frozen {name} conversion changed the source")
            case_converted = json.loads(case_output.read_text(encoding="utf-8"))
            _require(case_converted == {
                "utiki": expected_behavior,
                "accounts": profile["accounts"],
            },
                     f"frozen {name} conversion changed the expected values")
            _require("fixture-only-sentinel" not in case_result.stdout + case_result.stderr,
                     f"frozen {name} conversion exposed a fixture value")

        batch_output = work / "batch converted"
        batch_sources = (work / "legacy-only.json", work / "canonical-only.json")
        batch_before = tuple(path.read_bytes() for path in batch_sources)
        batch_result = _run(
            frozen, work, "--input", str(batch_sources[0]),
            "--input", str(batch_sources[1]), "--output-dir", str(batch_output),
        )
        _require(batch_result.returncode == 0 and
                 batch_result.stdout == "BATCH PASS entries=2\n" and
                 batch_result.stderr == "", "frozen batch conversion failed")
        batch_manifest = json.loads((batch_output / "manifest.json").read_text(encoding="utf-8"))
        _require(batch_manifest["status"] == "PASS" and
                 [(entry["output_name"], entry["status"]) for entry in batch_manifest["entries"]] == [
                     ("legacy-only.json", "PASS"), ("canonical-only.json", "PASS")
                 ], "frozen batch manifest is incomplete")
        expected_batch = (
            {"utiki": {"ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                       "seat_selection_mode": "manual"}, "accounts": profile["accounts"]},
            {"utiki": behavior, "accounts": profile["accounts"]},
        )
        for index, source in enumerate(batch_sources):
            output_bytes = (batch_output / source.name).read_bytes()
            _require(json.loads(output_bytes.decode("utf-8")) == expected_batch[index],
                     "frozen batch output changed the expected profile")
            _require(batch_manifest["entries"][index]["source_sha256"] ==
                     hashlib.sha256(batch_before[index]).hexdigest() and
                     batch_manifest["entries"][index]["output_sha256"] ==
                     hashlib.sha256(output_bytes).hexdigest(),
                     "frozen batch manifest digest does not match actual bytes")
        _require(tuple(path.read_bytes() for path in batch_sources) == batch_before,
                 "frozen batch conversion changed a source")
        _require("fixture-only-sentinel" not in batch_result.stdout + batch_result.stderr and
                 "fixture-only-sentinel" not in (batch_output / "manifest.json").read_text(encoding="utf-8"),
                 "frozen batch exposed a fixture value")

        bad_batch_output = work / "invalid batch"
        bad_batch = _run(
            frozen, work, "--input", str(batch_sources[0]),
            "--input", str(conflict), "--output-dir", str(bad_batch_output),
        )
        _require(bad_batch.returncode == 2 and
                 bad_batch.stderr.strip() == "ERROR CONFLICT_POLICY_REQUIRED" and
                 bad_batch.stdout == "" and not bad_batch_output.exists(),
                 "frozen batch wrote output before all inputs were valid")
        _require(tuple(path.read_bytes() for path in batch_sources) == batch_before and
                 conflict.read_bytes() == conflict_before,
                 "frozen invalid batch changed a source")

        invalid_profiles = (
            ("corrupt-json", '{"accounts":{"password":"fixture-only-sentinel"},"utiki":'),
            ("duplicate-key", '{"accounts":{"password":"fixture-only-sentinel"},'
                              '"utiki":{},"utiki":{}}'),
        )
        for name, raw_profile in invalid_profiles:
            case_source = work / f"{name}.json"
            case_source.write_text(raw_profile, encoding="utf-8")
            case_before = case_source.read_bytes()
            case_result = _run(frozen, work, "--input", str(case_source), "--dry-run")
            error_code = "INVALID_JSON" if name == "corrupt-json" else "DUPLICATE_JSON_KEY"
            _require(case_result.returncode == 2 and
                     case_result.stderr.strip() == f"ERROR {error_code}",
                     f"frozen {name} was not rejected for the expected reason")
            _require(case_result.stdout == "" and
                     "fixture-only-sentinel" not in case_result.stderr,
                     f"frozen {name} rejection exposed a fixture value")
            _require(case_source.read_bytes() == case_before,
                     f"frozen {name} rejection changed the source")

        for result in (preview, written, repeat, rejected):
            _require("fixture-only-sentinel" not in result.stdout + result.stderr,
                     "frozen upgrader exposed a fixture value")
        _require(not any(empty_path.iterdir()),
                 "frozen upgrader wrote into the restricted executable path")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        smoke(arguments.executable)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"FAIL frozen upgrader smoke: {type(exc).__name__}: {exc}")
        return 1
    print("PASS frozen upgrader smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
