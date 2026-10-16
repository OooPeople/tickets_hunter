#!/usr/bin/env python3
"""Prebuild the pinned Linux x64 image used by uTiki isolated CI jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
BASE_IMAGE_RE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
TAG_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_.-]+\Z")
REQUIRED_INPUTS = (
    "ci/utiki-isolation/Dockerfile",
    "requirement.txt",
    "requirements-dev.txt",
)


class BuildError(Exception):
    """The isolation image or its evidence could not be built safely."""


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BuildError(f"cannot digest build input: {path}") from exc


def _run(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"container command could not run: {command[0]}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BuildError(f"container command failed ({completed.returncode}): {detail}")
    return completed


def _load_object(value: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BuildError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BuildError(f"{label} did not return an object")
    return payload


def _validate_host() -> None:
    if platform.system().lower() != "linux":
        raise BuildError("isolation image must be built on Linux")
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        raise BuildError("isolation image must be built on an x86_64 host")


def _regular_inputs(repository: Path) -> list[dict[str, str]]:
    inputs: list[dict[str, str]] = []
    for relative in REQUIRED_INPUTS:
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            raise BuildError(f"build input is absent or a symlink: {relative}")
        inputs.append({"path": relative, "sha256": _sha256(path)})
    return inputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--runtime", default="docker")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _validate_host()
        repository = arguments.repository_root.resolve(strict=True)
        if not (repository / ".git").exists():
            raise BuildError("repository root is not a Git checkout")
        if not BASE_IMAGE_RE.fullmatch(arguments.base_image):
            raise BuildError("base image must be a repository digest")
        if not TAG_RE.fullmatch(arguments.tag):
            raise BuildError("image tag is invalid")
        evidence = arguments.evidence.resolve()
        if arguments.evidence.is_symlink() or evidence.exists():
            raise BuildError("build evidence already exists or is a symlink")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        inputs = _regular_inputs(repository)

        _run([arguments.runtime, "version", "--format", "{{json .Server}}"], cwd=repository)
        _run([arguments.runtime, "pull", arguments.base_image], cwd=repository)
        with tempfile.TemporaryDirectory(prefix="utiki-isolation-build-") as raw_context:
            context = Path(raw_context)
            shutil.copyfile(
                repository / "ci" / "utiki-isolation" / "Dockerfile",
                context / "Dockerfile",
            )
            shutil.copyfile(repository / "requirement.txt", context / "requirement.txt")
            shutil.copyfile(
                repository / "requirements-dev.txt", context / "requirements-dev.txt"
            )
            if {item.name for item in context.iterdir()} != {
                "Dockerfile",
                "requirement.txt",
                "requirements-dev.txt",
            }:
                raise BuildError("minimal image build context contains unexpected files")
            _run(
                [
                    arguments.runtime,
                    "build",
                    "--pull=false",
                    "--platform",
                    "linux/amd64",
                    "--file",
                    "Dockerfile",
                    "--build-arg",
                    f"BASE_IMAGE={arguments.base_image}",
                    "--tag",
                    arguments.tag,
                    ".",
                ],
                cwd=context,
            )
        inspection = _load_object(
            _run(
                [
                    arguments.runtime,
                    "image",
                    "inspect",
                    arguments.tag,
                    "--format",
                    "{{json .}}",
                ],
                cwd=repository,
            ).stdout,
            label="image inspection",
        )
        image_id = inspection.get("Id")
        if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
            raise BuildError("built image has no content-addressed image ID")
        if inspection.get("Os") != "linux" or inspection.get("Architecture") != "amd64":
            raise BuildError("built image is not linux/amd64")

        payload = {
            "schema_version": SCHEMA_VERSION,
            "evidence_kind": "utiki-isolation-image-build",
            "base_image": arguments.base_image,
            "image_id": image_id,
            "build_completed_before_isolation": True,
            "isolation_phase_pulled_or_built": False,
            "inputs": inputs,
        }
        temporary = evidence.with_name(f".{evidence.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(evidence)
        print(json.dumps({"evidence": str(evidence), "image_id": image_id}))
        return 0
    except (BuildError, OSError) as exc:
        sys.stderr.write(f"build_utiki_isolation_image: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
