#!/usr/bin/env python3
"""Fail-closed typed-scope coverage check and strict mypy launcher."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
TARGET_PYTHON = "3.11"
SAFE_MODULE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
REQUIRED_ROOTS = (
    "src/platforms/utiki/domain",
    "src/platforms/utiki/application",
    "src/platforms/utiki/adapters",
)
REQUIRED_SINGLE_FILES = (
    "src/platforms/utiki/auth.py",
    "src/platforms/utiki/profiles.py",
    "src/platforms/utiki/selection.py",
    "src/platforms/utiki/bootstrap.py",
    "src/utiki_config.py",
    "src/keyword_rules.py",
    "src/utiki_core_services.py",
    "tools/migrations/convert_utiki_config.py",
)
UNSAFE_TRUE_OPTIONS = {"ignore_errors", "allow_untyped_globals"}
UNSAFE_FALSE_OPTIONS = {
    "check_untyped_defs",
    "disallow_any_generics",
    "disallow_incomplete_defs",
    "disallow_untyped_defs",
    "disallow_untyped_decorators",
    "no_implicit_optional",
    "strict_equality",
    "warn_redundant_casts",
    "warn_return_any",
    "warn_unreachable",
    "warn_unused_ignores",
}
APPROVED_MISSING_IMPORT_ROOTS = {"ddddocr", "requests", "tornado", "zendriver"}
APPROVED_SKIPPED_MODULES = {
    "global_settings",
    "platforms.utiki",
    "platforms.utiki.dom",
    "platforms.utiki.refresh",
    "platforms.utiki.workflow",
    "refresh_coordinator",
    "util",
}
COVERAGE_CONTEXT_KEYS = {
    "schema_version",
    "coverage_kind",
    "purpose",
    "production_scope_status",
    "legacy_seed_scope_status",
}
CONTROLLED_COVERAGE_CONTEXT = {
    "coverage_kind": "controlled-runner-contract",
    "production_scope_status": "NOT_CLAIMED",
    "legacy_seed_scope_status": "KNOWN_FAILING_DEBT",
}


class GateError(Exception):
    def __init__(self, reason: str, *, details: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or []


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GateError("COVERAGE_CONTEXT_INVALID")
        value[key] = item
    return value


def _coverage_context(root: Path, path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("COVERAGE_CONTEXT_INVALID") from exc
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise GateError("COVERAGE_CONTEXT_INVALID") from exc
    if path.is_symlink() or not resolved.is_file():
        raise GateError("COVERAGE_CONTEXT_INVALID")
    if (
        not isinstance(payload, dict)
        or set(payload) != COVERAGE_CONTEXT_KEYS
        or payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("purpose"), str)
        or not payload["purpose"]
        or any(payload.get(key) != value for key, value in CONTROLLED_COVERAGE_CONTEXT.items())
    ):
        raise GateError("COVERAGE_CONTEXT_INVALID")
    return {
        **CONTROLLED_COVERAGE_CONTEXT,
        "coverage_context_path": resolved.relative_to(root).as_posix(),
        "coverage_context_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise GateError("MANIFEST_PATH_UNSAFE")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or ".." in path.parts
        or path.suffix != ".py"
    ):
        raise GateError("MANIFEST_PATH_UNSAFE")
    return value


def _digest(path: Path, reason: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GateError(reason) from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError as exc:
        raise GateError("REPORT_WRITE_FAILED") from exc


def _safe_regular(root: Path, relative: str, missing_reason: str) -> None:
    current = root
    parts = PurePosixPath(relative).parts
    for position, part in enumerate(parts):
        current = current / part
        try:
            information = current.lstat()
        except FileNotFoundError as exc:
            raise GateError(missing_reason, details=[relative]) from exc
        except OSError as exc:
            raise GateError("MANIFEST_PATH_UNSAFE", details=[relative]) from exc
        if stat.S_ISLNK(information.st_mode):
            raise GateError("MANIFEST_PATH_SYMLINK", details=[relative])
        if position < len(parts) - 1 and not stat.S_ISDIR(information.st_mode):
            raise GateError("MANIFEST_PATH_UNSAFE", details=[relative])
    if not stat.S_ISREG(information.st_mode):
        raise GateError("MANIFEST_PATH_NOT_REGULAR", details=[relative])


def _manifest_paths(root: Path, manifest: Path) -> list[str]:
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise GateError("TYPED_MANIFEST_UNREADABLE") from exc
    paths: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if any(character in value for character in "*?[]{}"):
            raise GateError("MANIFEST_GLOB_FORBIDDEN")
        paths.append(_safe_relative(value))
    if len(paths) != len(set(paths)):
        raise GateError("MANIFEST_DUPLICATE_PATH")
    if not paths:
        raise GateError("TYPED_SCOPE_EMPTY")
    for path in paths:
        _safe_regular(root, path, "MANIFEST_PATH_MISSING")
    folded: dict[str, str] = {}
    for path in paths:
        collision = folded.setdefault(path.casefold(), path)
        if collision != path:
            raise GateError("MANIFEST_CASE_COLLISION", details=[collision, path])
    return paths


def _required_paths(root: Path) -> list[str]:
    required: set[str] = set()
    for relative_root in REQUIRED_ROOTS:
        directory = root / PurePosixPath(relative_root)
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise GateError("TYPED_ROOT_UNSAFE", details=[relative_root])
        for path in directory.rglob("*.py"):
            relative = _safe_relative(path.relative_to(root).as_posix())
            _safe_regular(root, relative, "REQUIRED_TYPED_PATH_MISSING")
            required.add(relative)
    for relative in REQUIRED_SINGLE_FILES:
        path = root / PurePosixPath(relative)
        if path.exists() or path.is_symlink():
            _safe_regular(root, relative, "REQUIRED_TYPED_PATH_MISSING")
            required.add(relative)
    return sorted(required)


def _validate_scope(root: Path, manifest: Path) -> tuple[list[str], list[str]]:
    paths = _manifest_paths(root, manifest)
    required = _required_paths(root)
    missing = sorted(set(required) - set(paths))
    if missing:
        raise GateError("TYPED_SCOPE_MISSING", details=missing)
    return paths, required


def _boolean(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    try:
        return parser.getboolean(section, option)
    except ValueError as exc:
        raise GateError("MYPY_CONFIG_INVALID") from exc


def _validate_config(path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise GateError("MYPY_CONFIG_INVALID") from exc
    if not parser.has_section("mypy"):
        raise GateError("MYPY_CONFIG_INVALID")
    if not parser.has_option("mypy", "strict") or not _boolean(parser, "mypy", "strict"):
        raise GateError("MYPY_STRICT_REQUIRED")
    if parser.get("mypy", "python_version", fallback="") != TARGET_PYTHON:
        raise GateError("MYPY_PYTHON_VERSION_MISMATCH")
    for section in parser.sections():
        for option in UNSAFE_TRUE_OPTIONS:
            if parser.has_option(section, option) and _boolean(parser, section, option):
                raise GateError("UNSAFE_MYPY_CONFIG")
        for option in UNSAFE_FALSE_OPTIONS:
            if parser.has_option(section, option) and not _boolean(parser, section, option):
                raise GateError("UNSAFE_MYPY_CONFIG")
        follow_imports = parser.get(section, "follow_imports", fallback="").strip().lower()
        if follow_imports == "skip":
            module = section.removeprefix("mypy-") if section.startswith("mypy-") else ""
            if module not in APPROVED_SKIPPED_MODULES:
                raise GateError("UNSAFE_MYPY_CONFIG")
        if parser.get(section, "disable_error_code", fallback="").strip():
            raise GateError("UNSAFE_MYPY_CONFIG")
        if parser.has_option(section, "ignore_missing_imports") and _boolean(
            parser, section, "ignore_missing_imports"
        ):
            if not section.startswith("mypy-"):
                raise GateError("UNSAFE_MYPY_CONFIG")
            pattern = section.removeprefix("mypy-")
            root = pattern.split(".", 1)[0]
            if (
                root not in APPROVED_MISSING_IMPORT_ROOTS
                or pattern in {"*", "platforms.*", "src.*"}
                or not SAFE_MODULE_PATTERN.fullmatch(pattern)
            ):
                raise GateError("UNSAFE_MYPY_CONFIG")
    return _digest(path, "MYPY_CONFIG_INVALID")


def _mypy_command(requested: Path | None) -> list[str]:
    if requested is not None:
        try:
            information = requested.lstat()
        except OSError as exc:
            raise GateError("MYPY_NOT_AVAILABLE") from exc
        if (
            stat.S_ISLNK(information.st_mode)
            or not stat.S_ISREG(information.st_mode)
            or not os.access(requested, os.X_OK)
        ):
            raise GateError("MYPY_NOT_AVAILABLE")
        return [str(requested)]
    executable = shutil.which("mypy")
    if executable:
        return [executable]
    if importlib.util.find_spec("mypy") is not None:
        return [sys.executable, "-m", "mypy"]
    raise GateError("MYPY_NOT_AVAILABLE")


def _base_report(
    manifest: Path,
    config: Path,
    files: list[str],
    required: list[str],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "config_sha256": _digest(config, "MYPY_CONFIG_INVALID"),
        "failure_reasons": [],
        "files": files,
        "manifest_sha256": _digest(manifest, "TYPED_MANIFEST_UNREADABLE"),
        "mypy_exit_code": None,
        "required_files": required,
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        **coverage,
    }


def _emit(report: dict[str, Any], report_path: Path | None) -> None:
    if report_path is not None:
        _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run strict mypy over approved uTiki scope.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--coverage-context", type=Path)
    parser.add_argument("--probe-subject", type=Path)
    parser.add_argument("--mypy", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    files: list[str] = []
    required: list[str] = []
    config_sha256: str | None = None
    manifest_sha256: str | None = None
    coverage: dict[str, Any] = {}
    try:
        root = arguments.root.resolve(strict=True)
        if arguments.probe_subject is not None:
            subject = arguments.probe_subject.resolve(strict=True)
            try:
                subject.relative_to(root)
            except ValueError as exc:
                raise GateError("PROBE_SUBJECT_INVALID") from exc
            if arguments.probe_subject.is_symlink() or not subject.is_file():
                raise GateError("PROBE_SUBJECT_INVALID")
        if arguments.coverage_context is not None:
            coverage = _coverage_context(root, arguments.coverage_context)
        files, required = _validate_scope(root, arguments.manifest)
        manifest_sha256 = _digest(
            arguments.manifest, "TYPED_MANIFEST_UNREADABLE"
        )
        config_sha256 = _validate_config(arguments.config)
        command = _mypy_command(arguments.mypy)
        effective = [
            *command,
            "--strict",
            "--no-incremental",
            "--config-file",
            str(arguments.config.resolve()),
            *files,
        ]
        try:
            completed = subprocess.run(
                effective,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise GateError("MYPY_NOT_AVAILABLE") from exc
        report = _base_report(
            arguments.manifest, arguments.config, files, required, coverage
        )
        report["config_sha256"] = config_sha256
        report["effective_flags"] = ["--strict", "--no-incremental"]
        report["mypy_exit_code"] = completed.returncode
        report["stdout_sha256"] = hashlib.sha256(
            completed.stdout.encode("utf-8")
        ).hexdigest()
        report["stderr_sha256"] = hashlib.sha256(
            completed.stderr.encode("utf-8")
        ).hexdigest()
        if completed.returncode == 0:
            report["status"] = "PASS"
            exit_code = 0
        else:
            report["status"] = "FAIL"
            report["failure_reasons"] = ["MYPY_FAILED"]
            exit_code = 1
            if completed.stdout:
                sys.stderr.write(completed.stdout)
            if completed.stderr:
                sys.stderr.write(completed.stderr)
        _emit(report, arguments.report)
        return exit_code
    except GateError as exc:
        status = "BLOCKED" if exc.reason == "MYPY_NOT_AVAILABLE" else "FAIL"
        report: dict[str, Any] = {
            "config_sha256": config_sha256,
            "effective_flags": ["--strict", "--no-incremental"],
            "failure_reasons": [exc.reason],
            "files": files,
            "manifest_sha256": manifest_sha256,
            "mypy_exit_code": None,
            "required_files": required,
            "schema_version": SCHEMA_VERSION,
            "status": status,
            **coverage,
        }
        if exc.details:
            report["affected_paths"] = sorted(exc.details)
        try:
            _emit(report, arguments.report)
        except GateError:
            pass
        sys.stderr.write(f"run_utiki_typecheck: {exc.reason}\n")
        return 2 if status == "BLOCKED" else 1
    except Exception:
        report = {
            "config_sha256": config_sha256,
            "effective_flags": ["--strict", "--no-incremental"],
            "failure_reasons": ["INTERNAL_ERROR"],
            "files": files,
            "manifest_sha256": manifest_sha256,
            "mypy_exit_code": None,
            "required_files": required,
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            **coverage,
        }
        try:
            _emit(report, arguments.report)
        except GateError:
            pass
        sys.stderr.write("run_utiki_typecheck: INTERNAL_ERROR\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
