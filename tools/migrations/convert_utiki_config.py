"""Offline-only conversion of legacy uTiki profile data."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

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
