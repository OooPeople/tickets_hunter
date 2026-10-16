#!/usr/bin/env python3
"""Build and verify the repository-wide modified-Python syntax manifest.

The verifier intentionally does not import project modules.  Its trust root is
Git plus an optional, digest-pinned build-source inventory in run metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA_VERSION = 1
TARGET_PYTHON = (3, 11)
SOURCE_SUFFIXES = {".py", ".spec"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
PLACEHOLDER_MARKERS = ("${", "$(", "<", ">", "PLACEHOLDER", "CHANGEME", "TODO")

# These are dependency/cache or generated-output roots, not project source
# roots. Tracked changes are derived separately and an explicitly inventoried
# build source is still added below, so these exclusions cannot hide either.
UNTRACKED_EXCLUDES = (
    ":(exclude).venv/**",
    ":(exclude)venv/**",
    ":(exclude).tox/**",
    ":(exclude).nox/**",
    ":(exclude)build/**",
    ":(exclude)dist/**",
    ":(exclude)artifacts/**",
    ":(glob,exclude)**/__pycache__/**",
    ":(glob,exclude)**/.mypy_cache/**",
    ":(glob,exclude)**/.pytest_cache/**",
    ":(glob,exclude)**/.ruff_cache/**",
)
EXCLUDED_OUTPUT_ROOTS = (".venv/", "venv/", ".tox/", ".nox/", "build/", "dist/", "artifacts/")


class GateError(Exception):
    """A failure that is safe to identify by its stable reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DuplicateJsonKeyError(ValueError):
    pass


@dataclass
class ChangeRecord:
    path: str
    statuses: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    old_paths: set[str] = field(default_factory=set)
    explicit_delete: bool = False
    build_source: dict[str, str] | None = None


@dataclass(frozen=True)
class DerivedScope:
    manifest: dict[str, Any]
    dirty: bool


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _load_json(path: Path, *, error_code: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_duplicate_rejecting_object)
    except DuplicateJsonKeyError as exc:
        raise GateError("DUPLICATE_JSON_KEY") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(error_code) from exc


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError as exc:
        raise GateError("OUTPUT_WRITE_FAILED") from exc


def _run_git(repo: Path, arguments: Iterable[str]) -> bytes:
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


def _repo_root() -> Path:
    output = _run_git(Path.cwd(), ["rev-parse", "--show-toplevel"])
    try:
        value = output.rstrip(b"\n").decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GateError("UNSAFE_REPOSITORY_PATH") from exc
    if not value:
        raise GateError("NOT_A_GIT_REPOSITORY")
    return Path(value).resolve()


def _decode_git_field(value: bytes) -> str:
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GateError("UNSAFE_PATH") from exc
    return _safe_repo_path(decoded)


def _safe_repo_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GateError("UNSAFE_PATH")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GateError("UNSAFE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise GateError("UNSAFE_PATH")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise GateError("UNSAFE_PATH")
    return value


def _split_nul(output: bytes) -> list[bytes]:
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if any(field == b"" for field in fields):
        raise GateError("MALFORMED_GIT_OUTPUT")
    return fields


def _parse_name_status(output: bytes) -> list[tuple[str, str, str | None]]:
    fields = _split_nul(output)
    changes: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(fields):
        try:
            status_value = fields[index].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise GateError("MALFORMED_GIT_STATUS") from exc
        index += 1
        if not status_value or status_value[0] not in "ACDMRTUXB":
            raise GateError("MALFORMED_GIT_STATUS")
        status = status_value[0]
        if status in {"U", "X", "B"}:
            raise GateError("UNMERGED_OR_UNKNOWN_STATUS")
        if status in {"R", "C"}:
            if index + 1 >= len(fields):
                raise GateError("MALFORMED_GIT_OUTPUT")
            old_path = _decode_git_field(fields[index])
            new_path = _decode_git_field(fields[index + 1])
            index += 2
            changes.append((status_value, new_path, old_path))
        else:
            if index >= len(fields):
                raise GateError("MALFORMED_GIT_OUTPUT")
            path = _decode_git_field(fields[index])
            index += 1
            changes.append((status_value, path, None))
    return changes


def _is_source_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SOURCE_SUFFIXES


def _resolve_exact_sha(repo: Path, value: str, *, reason: str) -> str:
    if not isinstance(value, str) or not GIT_SHA_RE.fullmatch(value):
        raise GateError(reason)
    output = _run_git(repo, ["rev-parse", "--verify", f"{value}^{{commit}}"])
    try:
        resolved = output.strip().decode("ascii", errors="strict").lower()
    except UnicodeDecodeError as exc:
        raise GateError(reason) from exc
    if not GIT_SHA_RE.fullmatch(resolved) or resolved != value.lower():
        raise GateError(reason)
    return resolved


def _validated_id(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise GateError("INVALID_RUN_METADATA")
    upper = value.upper()
    if any(marker in upper for marker in PLACEHOLDER_MARKERS):
        raise GateError("PLACEHOLDER_RUN_METADATA")
    return value


def _validate_metadata(
    repo: Path, metadata_path: Path, base_ref: str, candidate_ref: str
) -> tuple[dict[str, Any], str, str]:
    metadata = _load_json(metadata_path, error_code="INVALID_RUN_METADATA")
    if not isinstance(metadata, dict) or metadata.get("schema_version") != SCHEMA_VERSION:
        raise GateError("INVALID_RUN_METADATA")
    for key in ("run_id", "job_id", "phase"):
        _validated_id(metadata, key)
    base_sha = _resolve_exact_sha(repo, base_ref, reason="INVALID_BASE_REF")
    candidate_sha = _resolve_exact_sha(
        repo, candidate_ref, reason="INVALID_CANDIDATE_REF"
    )
    if metadata.get("base_sha") != base_sha:
        raise GateError("RUN_METADATA_BASE_MISMATCH")
    if metadata.get("candidate_sha") != candidate_sha:
        raise GateError("RUN_METADATA_CANDIDATE_MISMATCH")
    head = _run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    if head.strip().decode("ascii", errors="strict").lower() != candidate_sha:
        raise GateError("CANDIDATE_NOT_CHECKED_OUT")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, candidate_sha],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestor.returncode != 0:
        raise GateError("BASE_NOT_ANCESTOR")
    return metadata, base_sha, candidate_sha


def _validate_python() -> str:
    if sys.version_info[:2] != TARGET_PYTHON:
        raise GateError("PYTHON_3_11_REQUIRED")
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _untracked_pathspec() -> list[str]:
    return ["--", ".", *UNTRACKED_EXCLUDES]


def _untracked_paths(repo: Path, *, ignored: bool = False) -> list[str]:
    arguments = ["ls-files", "--others", "--exclude-standard", "-z"]
    if ignored:
        arguments.insert(2, "--ignored")
    output = _run_git(repo, [*arguments, *_untracked_pathspec()])
    paths: list[str] = []
    for field in _split_nul(output):
        # Git can report an ignored nested repository as a directory ending
        # in '/', even when the pathspec excludes generated output below it.
        # Only those already-excluded output roots may be skipped here.
        if field.endswith(b"/"):
            directory = _decode_git_field(field[:-1])
            if directory.startswith(EXCLUDED_OUTPUT_ROOTS):
                continue
        paths.append(_decode_git_field(field))
    return paths


def _is_dirty(repo: Path) -> bool:
    # "normal" detects an untracked directory without descending through a
    # potentially huge virtual environment.  Exact untracked files are derived
    # independently by _untracked_paths with narrow dependency exclusions.
    output = _run_git(
        repo, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"]
    )
    return bool(output)


def _assert_no_unmerged(repo: Path) -> None:
    if _run_git(repo, ["ls-files", "-u", "-z"]):
        raise GateError("UNMERGED_INDEX")


def _add_change(
    records: dict[str, ChangeRecord],
    *,
    path: str,
    status: str,
    source: str,
    old_path: str | None = None,
    explicit_delete: bool = False,
) -> None:
    record = records.setdefault(path, ChangeRecord(path=path))
    record.statuses.add(status)
    record.sources.add(source)
    if old_path is not None:
        record.old_paths.add(old_path)
    record.explicit_delete = record.explicit_delete or explicit_delete


def _record_diff_changes(
    records: dict[str, ChangeRecord],
    changes: Iterable[tuple[str, str, str | None]],
    source: str,
) -> None:
    for full_status, path, old_path in changes:
        status = full_status[0]
        if status == "R":
            assert old_path is not None
            if _is_source_path(path):
                _add_change(
                    records,
                    path=path,
                    status=full_status,
                    source=source,
                    old_path=old_path,
                )
                # A later Git layer can rename a path introduced by an earlier
                # layer (for example, candidate rename followed by staged
                # rename).  Retire that intermediate path instead of treating
                # its absence as an unexplained missing compile input.
                if _is_source_path(old_path) and old_path in records:
                    _add_change(
                        records,
                        path=old_path,
                        status=full_status,
                        source=source,
                        explicit_delete=True,
                    )
            if _is_source_path(old_path) and not _is_source_path(path):
                _add_change(
                    records,
                    path=old_path,
                    status=full_status,
                    source=source,
                    explicit_delete=True,
                )
        elif status == "C":
            assert old_path is not None
            if _is_source_path(path):
                _add_change(
                    records,
                    path=path,
                    status=full_status,
                    source=source,
                    old_path=old_path,
                )
        elif _is_source_path(path):
            _add_change(
                records,
                path=path,
                status=full_status,
                source=source,
                explicit_delete=status == "D",
            )


def _inventory_entries(metadata: dict[str, Any]) -> list[dict[str, str]]:
    raw = metadata.get("build_sources", [])
    if not isinstance(raw, list):
        raise GateError("INVALID_BUILD_SOURCE_INVENTORY")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "source"}:
            raise GateError("INVALID_BUILD_SOURCE_INVENTORY")
        path = _safe_repo_path(item.get("path"))
        digest = item.get("sha256")
        source = item.get("source")
        source_is_safe = False
        if isinstance(source, str) and SAFE_ID_RE.fullmatch(source):
            source_is_safe = True
        elif isinstance(source, str):
            try:
                _safe_repo_path(source)
                source_is_safe = True
            except GateError:
                source_is_safe = False
        if (
            not _is_source_path(path)
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or not source_is_safe
            or path in seen
        ):
            raise GateError("INVALID_BUILD_SOURCE_INVENTORY")
        seen.add(path)
        entries.append({"path": path, "sha256": digest, "source": source})
    return sorted(entries, key=lambda item: item["path"])


def _safe_lstat(repo: Path, path: str, *, require_file: bool) -> os.stat_result | None:
    safe = _safe_repo_path(path)
    current = repo
    parts = PurePosixPath(safe).parts
    for position, part in enumerate(parts):
        current = current / part
        try:
            information = os.lstat(current)
        except FileNotFoundError:
            if position == len(parts) - 1 and not require_file:
                return None
            if not require_file:
                return None
            raise GateError("MISSING_REQUIRED_FILE")
        except OSError as exc:
            raise GateError("UNSAFE_PATH") from exc
        if stat.S_ISLNK(information.st_mode):
            raise GateError("UNSAFE_SYMLINK")
        if position < len(parts) - 1 and not stat.S_ISDIR(information.st_mode):
            raise GateError("UNSAFE_PATH")
    if not stat.S_ISREG(information.st_mode):
        if require_file:
            raise GateError("UNSAFE_FILE_TYPE")
        return information
    return information


def _file_digest(repo: Path, path: str) -> tuple[str, tuple[int, int, int, int]]:
    before = _safe_lstat(repo, path, require_file=True)
    assert before is not None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(repo / PurePosixPath(path), flags)
    except OSError as exc:
        raise GateError("UNSAFE_FILE_OPEN") from exc
    digest = hashlib.sha256()
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise GateError("UNSAFE_FILE_TYPE")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
    except OSError as exc:
        raise GateError("SOURCE_READ_FAILED") from exc
    finally:
        os.close(descriptor)
    signature_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    signature_opened_before = (
        opened_before.st_dev,
        opened_before.st_ino,
        opened_before.st_size,
        opened_before.st_mtime_ns,
    )
    signature_opened_after = (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_size,
        opened_after.st_mtime_ns,
    )
    if not (signature_before == signature_opened_before == signature_opened_after):
        raise GateError("STALE_INPUT")
    return digest.hexdigest(), signature_opened_after


def _role(path: str) -> str:
    if path.endswith(".spec") or path.startswith("build_scripts/"):
        return "build"
    if path.startswith("src/"):
        return "production"
    if path.startswith("tests/"):
        return "test"
    return "tool"


def _case_collision_check(
    repo: Path, records: dict[str, ChangeRecord], untracked: list[str]
) -> None:
    relevant = {
        path
        for path in records
        if _safe_lstat(repo, path, require_file=False) is not None
    }
    tracked = [
        _decode_git_field(field)
        for field in _split_nul(_run_git(repo, ["ls-files", "-z"]))
    ]
    candidates = set(untracked)
    candidates.update(tracked)
    candidates.update(relevant)
    by_folded: dict[str, set[str]] = {}
    for path in candidates:
        if not _is_source_path(path):
            continue
        try:
            exists = _safe_lstat(repo, path, require_file=False) is not None
        except GateError:
            if path in relevant:
                raise
            continue
        if exists:
            by_folded.setdefault(path.casefold(), set()).add(path)
    for paths in by_folded.values():
        if len(paths) > 1 and paths.intersection(relevant):
            raise GateError("CASE_COLLISION")


def _derive_scope(
    repo: Path,
    metadata: dict[str, Any],
    base_sha: str,
    candidate_sha: str,
    *,
    include_worktree: bool,
) -> DerivedScope:
    _assert_no_unmerged(repo)
    dirty = _is_dirty(repo)
    if dirty and not include_worktree:
        raise GateError("DIRTY_WORKTREE_REQUIRES_INCLUDE")

    records: dict[str, ChangeRecord] = {}
    committed = _parse_name_status(
        _run_git(
            repo,
            [
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                "--find-copies-harder",
                f"{base_sha}..{candidate_sha}",
            ],
        )
    )
    _record_diff_changes(records, committed, "base..candidate")

    untracked: list[str] = []
    if include_worktree:
        staged = _parse_name_status(
            _run_git(
                repo,
                [
                    "diff",
                    "--cached",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies",
                    "--find-copies-harder",
                    "HEAD",
                ],
            )
        )
        working = _parse_name_status(
            _run_git(
                repo,
                [
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies",
                    "--find-copies-harder",
                ],
            )
        )
        _record_diff_changes(records, staged, "staged")
        _record_diff_changes(records, working, "working")
        untracked = _untracked_paths(repo)
        for path in untracked:
            if _is_source_path(path):
                _add_change(
                    records, path=path, status="??", source="untracked"
                )

    inventory = _inventory_entries(metadata)
    inventory_by_path = {item["path"]: item for item in inventory}
    ignored_sources = [
        path for path in _untracked_paths(repo, ignored=True) if _is_source_path(path)
    ]
    for path in ignored_sources:
        if path not in inventory_by_path:
            raise GateError("IGNORED_SOURCE_WITHOUT_INVENTORY")
    for item in inventory:
        path = item["path"]
        _add_change(records, path=path, status="BUILD_SOURCE", source="build-source")
        records[path].build_source = item

    _case_collision_check(repo, records, untracked)

    files: list[dict[str, Any]] = []
    for path in sorted(records):
        record = records[path]
        if len(record.old_paths) > 1:
            raise GateError("AMBIGUOUS_RENAME_OR_COPY")
        information = _safe_lstat(repo, path, require_file=False)
        exists = information is not None
        if exists and information is not None and not stat.S_ISREG(information.st_mode):
            raise GateError("UNSAFE_FILE_TYPE")
        if exists:
            digest, _ = _file_digest(repo, path)
            if record.build_source and digest != record.build_source["sha256"]:
                raise GateError("BUILD_SOURCE_DIGEST_MISMATCH")
            action = "compile"
        else:
            if not record.explicit_delete:
                raise GateError("MISSING_REQUIRED_FILE")
            digest = None
            action = "verify-delete"
        files.append(
            {
                "additional": False,
                "build_source": record.build_source,
                "exists": exists,
                "git_statuses": sorted(record.statuses),
                "old_path": next(iter(record.old_paths), None),
                "path": path,
                "required_action": action,
                "role": "build" if record.build_source else _role(path),
                "sha256": digest,
                "sources": sorted(record.sources),
            }
        )

    state_facts = {
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "dirty": dirty,
        "files": files,
        "include_worktree": include_worktree,
    }
    working_tree_digest = hashlib.sha256(
        _canonical_json_bytes(state_facts)
    ).hexdigest()
    manifest = {
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "files": files,
        "include_worktree": include_worktree,
        "job_id": metadata["job_id"],
        "phase": metadata["phase"],
        "python_version": _validate_python(),
        "run_id": metadata["run_id"],
        "schema_version": SCHEMA_VERSION,
        "working_tree_digest": working_tree_digest,
        "working_tree_state": "DIRTY" if dirty else "CLEAN",
    }
    return DerivedScope(manifest=manifest, dirty=dirty)


def _manifest_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GateError("MANIFEST_READ_FAILED") from exc


def _safe_cache_name(path: str) -> str:
    path_digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return f"{path_digest}.pyc"


def _compile_file(
    repo: Path, item: dict[str, Any], cache_dir: Path
) -> tuple[str, str | None]:
    path = item["path"]
    if item["required_action"] == "verify-delete":
        if _safe_lstat(repo, path, require_file=False) is not None:
            return "FAIL", "UNEXPECTED_PRESENT_PATH"
        return "PASS", "RETIRED_PATH"

    try:
        digest_before, signature_before = _file_digest(repo, path)
    except GateError as exc:
        return "FAIL", exc.code
    if digest_before != item["sha256"]:
        return "FAIL", "STALE_INPUT"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        py_compile.compile(
            str(repo / PurePosixPath(path)),
            cfile=str(cache_dir / _safe_cache_name(path)),
            doraise=True,
        )
    except py_compile.PyCompileError:
        return "FAIL", "SYNTAX_ERROR"
    except (OSError, ValueError):
        return "FAIL", "COMPILE_FAILED"
    try:
        digest_after, signature_after = _file_digest(repo, path)
    except GateError as exc:
        return "FAIL", exc.code
    if (
        digest_after != digest_before
        or digest_after != item["sha256"]
        or signature_after != signature_before
    ):
        return "FAIL", "STALE_INPUT"
    return "PASS", "COMPILED"


def _base_report(
    manifest: dict[str, Any], manifest_sha256: str | None
) -> dict[str, Any]:
    return {
        "base_sha": manifest.get("base_sha"),
        "candidate_sha": manifest.get("candidate_sha"),
        "effective_interpreter": (
            f"{sys.implementation.name} "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "files": [],
        "job_id": manifest.get("job_id"),
        "manifest_sha256": manifest_sha256,
        "phase": manifest.get("phase"),
        "python_version": manifest.get("python_version"),
        "run_id": manifest.get("run_id"),
        "schema_version": SCHEMA_VERSION,
        "working_tree_digest": manifest.get("working_tree_digest"),
        "working_tree_state": manifest.get("working_tree_state"),
    }


def _verify(
    repo: Path,
    expected: dict[str, Any],
    manifest_path: Path,
    report_path: Path,
    cache_dir: Path,
) -> int:
    manifest_sha256 = _manifest_digest(manifest_path)
    try:
        supplied = _load_json(manifest_path, error_code="INVALID_MANIFEST")
    except GateError as exc:
        report = _base_report(expected, manifest_sha256)
        report.update(
            {
                "exit_code": 1,
                "failure_reasons": [exc.code],
                "overall_status": "FAIL",
            }
        )
        _write_json(report_path, report)
        return 1
    if not isinstance(supplied, dict) or supplied != expected:
        report = _base_report(expected, manifest_sha256)
        report.update(
            {
                "exit_code": 1,
                "failure_reasons": ["MANIFEST_MISMATCH"],
                "overall_status": "FAIL",
            }
        )
        _write_json(report_path, report)
        return 1

    report = _base_report(expected, manifest_sha256)
    failures: set[str] = set()
    results: list[dict[str, Any]] = []
    compiled_count = 0
    for item in expected["files"]:
        status_value, reason = _compile_file(repo, item, cache_dir)
        if reason == "COMPILED":
            compiled_count += 1
        if status_value != "PASS" and reason is not None:
            failures.add(reason)
        results.append(
            {
                "path": item["path"],
                "reason": reason,
                "required_action": item["required_action"],
                "sha256": item["sha256"],
                "status": status_value,
            }
        )
    if failures:
        overall = "FAIL"
        exit_code = 1
    elif not expected["files"]:
        overall = "NO_PYTHON_CHANGES"
        exit_code = 0
    else:
        overall = "PASS"
        exit_code = 0
    report.update(
        {
            "compile_count": compiled_count,
            "exit_code": exit_code,
            "failure_reasons": sorted(failures),
            "files": results,
            "overall_status": overall,
        }
    )
    _write_json(report_path, report)
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or independently verify the modified-Python manifest."
    )
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--run-metadata", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-manifest", type=Path)
    mode.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--include-worktree", action="store_true")
    parser.add_argument("--probe-subject", type=Path)
    return parser


def _execute(arguments: argparse.Namespace) -> int:
    if arguments.manifest is not None and arguments.report is None:
        raise GateError("REPORT_REQUIRED")
    if arguments.write_manifest is not None and arguments.report is not None:
        raise GateError("REPORT_ONLY_VALID_FOR_VERIFY")
    repo = _repo_root()
    if arguments.probe_subject is not None:
        try:
            subject = arguments.probe_subject.resolve(strict=True)
            subject.relative_to(repo)
        except (OSError, ValueError) as exc:
            raise GateError("PROBE_SUBJECT_INVALID") from exc
        if arguments.probe_subject.is_symlink() or not subject.is_file():
            raise GateError("PROBE_SUBJECT_INVALID")
    metadata, base_sha, candidate_sha = _validate_metadata(
        repo,
        arguments.run_metadata,
        arguments.base_ref,
        arguments.candidate_ref,
    )
    derived = _derive_scope(
        repo,
        metadata,
        base_sha,
        candidate_sha,
        include_worktree=arguments.include_worktree,
    )
    if arguments.write_manifest is not None:
        _write_json(arguments.write_manifest, derived.manifest)
        return 0
    assert arguments.manifest is not None
    assert arguments.report is not None
    cache_dir = arguments.cache_dir or (
        arguments.report.parent / "python-bytecode-cache"
    )
    return _verify(
        repo,
        derived.manifest,
        arguments.manifest,
        arguments.report,
        cache_dir,
    )


def _write_terminal_failure_report(
    arguments: argparse.Namespace, reason: str, exit_code: int
) -> None:
    if arguments.report is None:
        return
    metadata: dict[str, Any] = {}
    try:
        loaded_metadata = _load_json(
            arguments.run_metadata, error_code="INVALID_RUN_METADATA"
        )
        if isinstance(loaded_metadata, dict):
            metadata = loaded_metadata
    except GateError:
        pass

    def safe_identifier(key: str) -> str | None:
        value = metadata.get(key)
        if isinstance(value, str) and SAFE_ID_RE.fullmatch(value):
            return value
        return None

    def safe_sha(key: str) -> str | None:
        value = metadata.get(key)
        if isinstance(value, str) and GIT_SHA_RE.fullmatch(value):
            return value.lower()
        return None

    manifest_sha256 = None
    if arguments.manifest is not None:
        try:
            manifest_sha256 = _manifest_digest(arguments.manifest)
        except GateError:
            pass
    report = {
        "base_sha": safe_sha("base_sha"),
        "candidate_sha": safe_sha("candidate_sha"),
        "effective_interpreter": (
            f"{sys.implementation.name} "
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
        "exit_code": exit_code,
        "failure_reasons": [reason],
        "files": [],
        "job_id": safe_identifier("job_id"),
        "manifest_sha256": manifest_sha256,
        "overall_status": "FAIL",
        "phase": safe_identifier("phase"),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
        "run_id": safe_identifier("run_id"),
        "schema_version": SCHEMA_VERSION,
        "working_tree_digest": None,
        "working_tree_state": None,
    }
    try:
        _write_json(arguments.report, report)
    except GateError:
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        exit_code = _execute(arguments)
        if exit_code != 0 and arguments.report is not None:
            try:
                report = _load_json(arguments.report, error_code="INVALID_REPORT")
                reasons = report.get("failure_reasons") if isinstance(report, dict) else None
                if isinstance(reasons, list):
                    for reason in reasons:
                        if isinstance(reason, str) and reason:
                            sys.stderr.write(f"verify_modified_python: {reason}\n")
            except GateError:
                pass
        return exit_code
    except GateError as exc:
        _write_terminal_failure_report(arguments, exc.code, 2)
        sys.stderr.write(f"verify_modified_python: {exc.code}\n")
        return 2
    except Exception:
        # Do not leak exception text, source lines, environment paths, or
        # credentials through a CI traceback.  Tests exercise detailed stable
        # reason codes for all expected failure classes.
        _write_terminal_failure_report(arguments, "INTERNAL_ERROR", 3)
        sys.stderr.write("verify_modified_python: INTERNAL_ERROR\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
