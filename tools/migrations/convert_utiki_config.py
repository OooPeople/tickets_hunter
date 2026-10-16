"""Offline-only conversion of legacy uTiki profile data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import unicodedata
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal, cast

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utiki_config import (
    DEFAULT_BEHAVIOR,
    ConfigError,
    UpgradeKind,
    inspect_upgrade_input,
    load_current_policy,
    parse_profile_json,
)


ConflictPolicy = Literal["canonical", "legacy"]
_ON_WINDOWS = os.name == "nt"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedProfile:
    source: Path
    source_identity: tuple[int, int, int, int, int]
    source_digest: str
    output_bytes: bytes


@dataclass(frozen=True, slots=True)
class ProfileRequest:
    source: Path
    output_name: str
    conflict_policy: ConflictPolicy | None = None


@dataclass(frozen=True, slots=True)
class BatchEntry:
    source_name: str
    output_name: str
    source_sha256: str
    status: Literal["PASS", "FAILED", "NOT_RUN"]
    output_sha256: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BatchResult:
    status: Literal["PASS", "PARTIAL_FAILURE"]
    entries: tuple[BatchEntry, ...]


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_source(path: Path) -> tuple[bytes, tuple[int, int, int, int, int]]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ConfigError("INVALID_SOURCE_FILE")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(before):
            raise ConfigError("SOURCE_CHANGED")
        content = stream.read()
        after = os.fstat(stream.fileno())
    if _identity(after) != _identity(before) or _identity(path.lstat()) != _identity(before):
        raise ConfigError("SOURCE_CHANGED")
    return content, _identity(before)


def prepare_profile(
    source: Path, *, conflict_policy: ConflictPolicy | None = None
) -> PreparedProfile:
    """Read and validate one source; do not write or expose profile values."""
    content, identity = _read_source(source)
    try:
        profile = parse_profile_json(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError("INVALID_UTF8") from exc
    converted = convert_profile_mapping(profile, conflict_policy=conflict_policy)
    output = (json.dumps(converted, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return PreparedProfile(source, identity, hashlib.sha256(content).hexdigest(), output)


def _require_unchanged(prepared: PreparedProfile) -> None:
    try:
        content, identity = _read_source(prepared.source)
    except (FileNotFoundError, ConfigError) as exc:
        raise ConfigError("SOURCE_CHANGED") from exc
    if identity != prepared.source_identity or hashlib.sha256(content).hexdigest() != prepared.source_digest:
        raise ConfigError("SOURCE_CHANGED")


def _publish(temporary: Path, target: Path) -> None:
    if _ON_WINDOWS:
        # Windows rename rejects an existing destination, unlike POSIX rename.
        os.rename(temporary, target)
    else:
        # On POSIX, hard linking is atomic and never replaces an existing path.
        os.link(temporary, target)


def _sync_directory(directory: Path) -> None:
    if _ON_WINDOWS:
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_profile(prepared: PreparedProfile, target: Path) -> str:
    """Write a separately named profile; never replace the source or an existing target."""
    if target == prepared.source:
        raise ConfigError("IN_PLACE_REQUIRES_BACKUP")
    if os.path.lexists(target):
        raise ConfigError("TARGET_EXISTS")
    _require_unchanged(prepared)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(prepared.output_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _require_unchanged(prepared)
        try:
            _publish(temporary, target)
        except FileExistsError as exc:
            raise ConfigError("TARGET_EXISTS") from exc
        try:
            _require_unchanged(prepared)
        except ConfigError as exc:
            # There is no portable atomic compare-and-unlink of the published
            # path. Leave it for manual review rather than risk removing a
            # different writer's replacement.
            raise ConfigError("SOURCE_CHANGED_OUTPUT_UNCERTAIN") from exc
        temporary.unlink(missing_ok=True)
        _sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(prepared.output_bytes).hexdigest()


def _check_output_names(requests: tuple[ProfileRequest, ...]) -> None:
    if not requests:
        raise ConfigError("EMPTY_BATCH")
    names: set[str] = set()
    sources: set[Path] = set()
    for request in requests:
        name = request.output_name
        normalized_name = unicodedata.normalize("NFC", name).casefold()
        if (
            not name
            or name in {".", ".."}
            or normalized_name == "manifest.json"
            or name.startswith(".")
            or name.rstrip(" .") != name
            or PureWindowsPath(name).name != name
            or PureWindowsPath(name).is_reserved()
            or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name)
        ):
            raise ConfigError("INVALID_OUTPUT_NAME")
        if normalized_name in names or request.source in sources:
            raise ConfigError("DUPLICATE_BATCH_ENTRY")
        names.add(normalized_name)
        sources.add(request.source)


def _write_manifest(directory: Path, result: BatchResult) -> None:
    payload = (
        json.dumps(
            {"schema_version": 1, "status": result.status, "entries": [asdict(entry) for entry in result.entries]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest.", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _publish(temporary, directory / "manifest.json")
        except FileExistsError as exc:
            raise ConfigError("MANIFEST_EXISTS") from exc
        temporary.unlink(missing_ok=True)
        _sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)


def write_profile_batch(requests: tuple[ProfileRequest, ...], output_dir: Path) -> BatchResult:
    """Validate every input before creating a new output directory."""
    _check_output_names(requests)
    if os.path.lexists(output_dir):
        raise ConfigError("OUTPUT_DIRECTORY_EXISTS")
    prepared = tuple(
        prepare_profile(request.source, conflict_policy=request.conflict_policy)
        for request in requests
    )
    output_dir.mkdir(mode=0o700)
    _sync_directory(output_dir.parent)
    entries: list[BatchEntry] = []
    failed = False
    for request, item in zip(requests, prepared):
        if failed:
            entries.append(BatchEntry(request.source.name, request.output_name, item.source_digest, "NOT_RUN"))
            continue
        try:
            digest = write_new_profile(item, output_dir / request.output_name)
        except (ConfigError, OSError) as exc:
            reason = exc.code if isinstance(exc, ConfigError) else "WRITE_FAILED"
            entries.append(BatchEntry(request.source.name, request.output_name, item.source_digest, "FAILED", error_code=reason))
            failed = True
        else:
            entries.append(BatchEntry(request.source.name, request.output_name, item.source_digest, "PASS", digest))
    result = BatchResult("PARTIAL_FAILURE" if failed else "PASS", tuple(entries))
    _write_manifest(output_dir, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline uTiki profile conversion")
    parser.add_argument("--input", required=True, type=Path)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--dry-run", action="store_true")
    output_group.add_argument("--output", type=Path)
    parser.add_argument("--conflict-policy", choices=("canonical", "legacy"))
    arguments = parser.parse_args(argv)
    try:
        prepared = prepare_profile(
            arguments.input, conflict_policy=arguments.conflict_policy
        )
        if arguments.output is None:
            digest = hashlib.sha256(prepared.output_bytes).hexdigest()
            print(f"READY source_sha256={prepared.source_digest} output_sha256={digest}")
        else:
            digest = write_new_profile(prepared, arguments.output)
            print(f"WRITTEN source_sha256={prepared.source_digest} output_sha256={digest}")
    except ConfigError as exc:
        print(f"ERROR {exc.code}", file=sys.stderr)
        return 2
    except OSError:
        print("ERROR IO_FAILURE", file=sys.stderr)
        return 3
    return 0


def convert_profile_mapping(
    profile: dict[str, object], *, conflict_policy: ConflictPolicy | None = None
) -> dict[str, object]:
    """Return canonical behavior while keeping every unrelated parsed value."""
    if conflict_policy not in (None, "canonical", "legacy"):
        raise ConfigError("INVALID_CONFLICT_POLICY")
    inspection = inspect_upgrade_input(profile)
    if inspection.kind is UpgradeKind.MIXED_CONFLICT:
        if conflict_policy is None:
            raise ConfigError("CONFLICT_POLICY_REQUIRED")
        canonical = cast(dict[str, str], profile["utiki"])
        legacy = cast(dict[str, str], profile["ctbc"])
        values = asdict(DEFAULT_BEHAVIOR)
        if conflict_policy == "canonical":
            values.update(legacy)
            values.update(canonical)
        else:
            values.update(canonical)
            values.update(legacy)
        policy = load_current_policy({"utiki": values})
    else:
        projected = inspection.projected
        if projected is None:
            raise ConfigError("INVALID_UPGRADE_INPUT")
        policy = projected
    converted = deepcopy(profile)
    converted.pop("ctbc", None)
    converted["utiki"] = asdict(policy)
    return converted


if __name__ == "__main__":
    raise SystemExit(main())
