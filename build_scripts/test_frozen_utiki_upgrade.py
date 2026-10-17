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

        for result in (preview, written, repeat, rejected):
            _require("fixture-only-sentinel" not in result.stdout + result.stderr,
                     "frozen upgrader exposed a fixture value")


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
