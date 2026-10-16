#!/usr/bin/env python3
"""Verify shared-file changes against the approved upstream touchpoint inventory."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"[0-9a-f]{40}")
FORK_ONLY_TARGETS = {
    "build_scripts/test_frozen_utiki_upgrade.py",
    "build_scripts/utiki_profile_upgrader.spec",
    "build_scripts/verify_utiki_bundle.py",
    "src/keyword_rules.py",
    "src/utiki_config.py",
    "src/utiki_core_services.py",
}
FORK_ONLY_PREFIXES = ("src/platforms/utiki/",)
EXACT_SHARED_FILES = {"requirement.txt", "requirements-dev.txt"}
SHARED_PREFIXES = (".github/workflows/", "build_scripts/", "src/www/")


class GateError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _git(repo: Path, arguments: Iterable[str]) -> bytes:
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C", "GIT_PAGER": "cat"})
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise GateError("GIT_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise GateError("GIT_COMMAND_FAILED")
    return result.stdout


def _safe_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise GateError("UNSAFE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or ".." in path.parts:
        raise GateError("UNSAFE_PATH")
    return value


def _nul_paths(output: bytes) -> list[str]:
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    paths: list[str] = []
    for field in fields:
        try:
            paths.append(_safe_path(field.decode("utf-8", errors="strict")))
        except UnicodeDecodeError as exc:
            raise GateError("UNSAFE_PATH") from exc
    return paths


def _inventory(path: Path) -> tuple[str, str, str, str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError("UPSTREAM_INVENTORY_UNREADABLE") from exc
    patterns = {
        "baseline": r"Actual HEAD[^\n]*`([0-9a-f]{40})`",
        "url": r"Upstream URL[^\n]*?`([^`\n]+)`",
        "upstream": r"Local upstream ref[^\n]*`(?:upstream/main@)?([0-9a-f]{40})`",
        "merge_base": r"Merge base[^\n]*`([0-9a-f]{40})`",
    }
    values: dict[str, str] = {}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text)
        if len(matches) != 1 or (key != "url" and not SHA_RE.fullmatch(matches[0])):
            raise GateError("UPSTREAM_INVENTORY_INCOMPLETE")
        values[key] = matches[0]
    for marker in ("Shared／upstream-owned touchpoints", "Fork-only family paths"):
        if marker not in text:
            raise GateError("UPSTREAM_INVENTORY_INCOMPLETE")
    if not values["url"].startswith(("https://", "ssh://", "git@")):
        raise GateError("UPSTREAM_INVENTORY_INCOMPLETE")
    return (
        values["baseline"],
        values["url"],
        values["upstream"],
        values["merge_base"],
        text,
    )


def _resolved_sha(repo: Path, revision: str, reason: str) -> str:
    output = _git(repo, ["rev-parse", "--verify", f"{revision}^{{commit}}"])
    try:
        value = output.strip().decode("ascii", errors="strict").lower()
    except UnicodeDecodeError as exc:
        raise GateError(reason) from exc
    if not SHA_RE.fullmatch(value):
        raise GateError(reason)
    return value


def _changed_paths(repo: Path, baseline: str) -> list[str]:
    outputs = [
        _git(repo, ["diff", "--no-renames", "--name-only", "-z", f"{baseline}..HEAD"]),
        _git(repo, ["diff", "--no-renames", "--cached", "--name-only", "-z", "HEAD"]),
        _git(repo, ["diff", "--no-renames", "--name-only", "-z"]),
        _git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]),
    ]
    changed: set[str] = set()
    for output in outputs:
        changed.update(_nul_paths(output))
    return sorted(changed)


def _is_shared_touchpoint(path: str) -> bool:
    if path in FORK_ONLY_TARGETS or path.startswith(FORK_ONLY_PREFIXES):
        return False
    if path in EXACT_SHARED_FILES or path.startswith(SHARED_PREFIXES):
        return True
    pure = PurePosixPath(path)
    if pure.parent == PurePosixPath("src") and pure.suffix == ".py":
        return True
    if pure.parent == PurePosixPath("src/platforms") and pure.suffix == ".py":
        return True
    return False


def _declared(path: str, text: str) -> bool:
    if f"`{path}`" in text or path in text:
        return True
    grouped_directories = (
        "src/www/",
        "build_scripts/",
    )
    if path.startswith(grouped_directories):
        parent = str(PurePosixPath(path).parent) + "/"
        return parent in text and f"`{PurePosixPath(path).name}`" in text
    if path == "requirements-dev.txt":
        return "`requirement.txt`" in text and "`requirements-dev.txt`" in text
    return False


def _report(
    status: str,
    baseline: str | None,
    upstream: str | None,
    merge_base: str | None,
    upstream_url: str | None,
    changed: list[str],
    undeclared: list[str],
    reasons: list[str],
) -> dict[str, object]:
    return {
        "baseline_sha": baseline,
        "changed_touchpoints": changed,
        "failure_reasons": sorted(set(reasons)),
        "merge_base_sha": merge_base,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "undeclared_touchpoints": undeclared,
        "upstream_sha": upstream,
        "upstream_url": upstream_url,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check approved upstream touchpoints.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        root = arguments.root.resolve(strict=True)
        baseline, pinned_url, pinned_upstream, pinned_merge_base, text = _inventory(
            arguments.inventory
        )
        actual_url = _git(root, ["remote", "get-url", "upstream"]).strip().decode(
            "utf-8", errors="strict"
        )
        if actual_url != pinned_url:
            raise GateError("UPSTREAM_URL_MISMATCH")
        if _resolved_sha(root, baseline, "BASELINE_SHA_INVALID") != baseline:
            raise GateError("BASELINE_SHA_INVALID")
        actual_upstream = _resolved_sha(
            root, "refs/remotes/upstream/main", "UPSTREAM_REF_MISSING"
        )
        if actual_upstream != pinned_upstream:
            raise GateError("UPSTREAM_REF_MISMATCH")
        actual_merge_base = _git(
            root, ["merge-base", baseline, pinned_upstream]
        ).strip().decode("ascii", errors="strict")
        if actual_merge_base != pinned_merge_base:
            raise GateError("MERGE_BASE_MISMATCH")
        head = _resolved_sha(root, "HEAD", "HEAD_INVALID")
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, head],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ancestor.returncode != 0:
            raise GateError("BASELINE_NOT_ANCESTOR")
        touchpoints = [
            path for path in _changed_paths(root, baseline) if _is_shared_touchpoint(path)
        ]
        undeclared = [path for path in touchpoints if not _declared(path, text)]
        reasons = ["UNDECLARED_UPSTREAM_TOUCHPOINT"] if undeclared else []
        report = _report(
            "FAIL" if reasons else "PASS",
            baseline,
            pinned_upstream,
            pinned_merge_base,
            pinned_url,
            touchpoints,
            undeclared,
            reasons,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1 if reasons else 0
    except GateError as exc:
        report = _report("FAIL", None, None, None, None, [], [], [exc.reason])
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        sys.stderr.write(f"check_upstream_boundary: {exc.reason}\n")
        return 2
    except Exception:
        report = _report("FAIL", None, None, None, None, [], [], ["INTERNAL_ERROR"])
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        sys.stderr.write("check_upstream_boundary: INTERNAL_ERROR\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
