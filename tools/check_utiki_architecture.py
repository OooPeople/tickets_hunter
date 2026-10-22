#!/usr/bin/env python3
"""Check the current uTiki dependency boundary without importing production."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"[0-9a-f]{40}")
TARGET_ROOTS = (
    "src/platforms/utiki/domain/",
    "src/platforms/utiki/application/",
    "src/platforms/utiki/adapters/",
)
TARGET_SINGLE_FILES = {
    "src/platforms/utiki/bootstrap.py",
    "src/utiki_config.py",
    "src/keyword_rules.py",
    "src/utiki_core_services.py",
}
LEGACY_LEAF_FILES = {
    "src/platforms/utiki/auth.py",
    "src/platforms/utiki/profiles.py",
    "src/platforms/utiki/selection.py",
}
CURRENT_BOUNDARY_FILES = {
    "src/nodriver_common.py",
    "src/settings.py",
}
RETIRED_SIDE_EFFECT_FILES = {
    "src/platforms/utiki/workflow.py",
    "src/platforms/utiki/refresh.py",
}
TARGET_FORBIDDEN_IMPORT_FRAGMENTS = (
    "nodriver_tixcraft",
    "platforms.ctbc",
    "platforms.utiki.workflow",
    "platforms.utiki.refresh",
    "utiki_core_services",
)
DOMAIN_FORBIDDEN_IMPORT_ROOTS = {
    "asyncio",
    "http",
    "logging",
    "nodriver_common",
    "os",
    "pathlib",
    "random",
    "requests",
    "settings",
    "socket",
    "subprocess",
    "sys",
    "time",
    "util",
    "uuid",
    "zendriver",
}
DOMAIN_FORBIDDEN_CALLS = {
    "create_task",
    "getenv",
    "get_event_loop",
    "get_running_loop",
    "monotonic",
    "open",
    "perf_counter",
    "sleep",
    "print",
    "time",
    "uuid4",
}
APPLICATION_FORBIDDEN_IMPORT_ROOTS = {
    "glob",
    "nodriver_common",
    "os",
    "pathlib",
    "zendriver",
}
APPLICATION_BROWSER_ATTRIBUTES = {
    "click",
    "evaluate",
    "get_content",
    "query_selector",
    "query_selector_all",
    "reload",
    "send",
}
CORE_BRIDGE_APPROVED_PROJECT_ROOTS = {
    "global_settings",
    "nodriver_common",
    "refresh_coordinator",
    "util",
}
CORE_BRIDGE_APPROVED_SYMBOLS = {
    "global_settings": {
        "get_global_settings_path",
        "load_global_settings",
    },
    "nodriver_common": {
        "CONST_NATIVE_INPUT_SETTER_JS",
        "check_and_handle_pause",
        "check_and_handle_quit",
        "nodriver_get_captcha_image_from_dom_snapshot",
        "play_sound_while_ordering",
        "send_discord_notification",
        "send_telegram_notification",
    },
    "refresh_coordinator": {
        "SharedRefreshCoordinator",
        "UTK_SPORTS_SCOPE",
        "get_shared_refresh_coordinator",
    },
    "util": {
        "create_debug_logger",
        "get_app_root",
        "get_instance_id",
        "get_instance_state_path",
    },
}


class GateError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Finding:
    reason: str
    path: str
    line: int

    def as_json(self) -> dict[str, object]:
        return {"line": self.line, "path": self.path, "reason": self.reason}


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise GateError("UNSAFE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or ".." in path.parts:
        raise GateError("UNSAFE_PATH")
    return value


def _read_text(path: Path, reason: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(reason) from exc


def _python_files(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    utiki = root / "src" / "platforms" / "utiki"
    if utiki.is_dir():
        for path in utiki.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            files.append((_safe_relative(relative), path))
    for relative in TARGET_SINGLE_FILES:
        path = root / PurePosixPath(relative)
        if path.exists() or path.is_symlink():
            files.append((relative, path))
    for relative in CURRENT_BOUNDARY_FILES:
        path = root / PurePosixPath(relative)
        if path.exists() or path.is_symlink():
            files.append((relative, path))
    return sorted(set(files), key=lambda item: item[0])


def _safe_regular_file(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise GateError("UNSAFE_PATH") from exc
    current = root
    for position, part in enumerate(relative.parts):
        current = current / part
        try:
            information = current.lstat()
        except OSError as exc:
            raise GateError("SOURCE_UNREADABLE") from exc
        if stat.S_ISLNK(information.st_mode):
            raise GateError("SOURCE_SYMLINK")
        if position < len(relative.parts) - 1 and not stat.S_ISDIR(
            information.st_mode
        ):
            raise GateError("UNSAFE_PATH")
    if not stat.S_ISREG(information.st_mode):
        raise GateError("SOURCE_NOT_REGULAR")


def _parse(root: Path, path: Path) -> ast.Module:
    _safe_regular_file(root, path)
    text = _read_text(path, "SOURCE_UNREADABLE")
    try:
        return ast.parse(text, filename=path.name)
    except SyntaxError as exc:
        raise GateError("AST_PARSE_ERROR") from exc


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            if module:
                found.append((prefix + module, node.lineno))
            for alias in node.names:
                found.append((prefix + module + "." + alias.name, node.lineno))
    return found


def _resolved_imports(path: str, tree: ast.AST) -> list[tuple[str, int]]:
    """Resolve imports against the package containing one source file."""
    package = list(PurePosixPath(path).parts[1:-1])
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(package):
                    continue
                parts = package[: len(package) - node.level + 1]
            else:
                parts = []
            if node.module:
                parts.extend(node.module.split("."))
            base = ".".join(parts)
            found.append((base, node.lineno))
            found.extend(
                (f"{base}.{alias.name}", node.lineno)
                for alias in node.names if alias.name != "*"
            )
    return found


def _is_target(path: str) -> bool:
    return path in TARGET_SINGLE_FILES or path.startswith(TARGET_ROOTS)


def _has_fragment(module: str, fragment: str) -> bool:
    normalized = module.lstrip(".")
    return normalized == fragment or normalized.startswith(fragment + ".")


def _target_findings(path: str, tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    imports = _imports(tree)
    for module, line in imports:
        normalized = module.lstrip(".")
        parts = normalized.split(".")
        if (
            any(_has_fragment(module, item) for item in TARGET_FORBIDDEN_IMPORT_FRAGMENTS)
            or "workflow" in parts
            or "nodriver_tixcraft" in parts
            or "utiki_core_services" in parts
        ):
            findings.append(Finding("TARGET_REVERSE_OR_LEGACY_IMPORT", path, line))
        if normalized.startswith("platforms.") and not normalized.startswith(
            "platforms.utiki"
        ):
            findings.append(Finding("CROSS_PLATFORM_IMPORT", path, line))

    pure_target = path.startswith("src/platforms/utiki/domain/") or path in {
        "src/keyword_rules.py",
        "src/utiki_config.py",
    }
    if pure_target:
        for module, line in imports:
            normalized = module.lstrip(".")
            root = normalized.split(".", 1)[0]
            if root in DOMAIN_FORBIDDEN_IMPORT_ROOTS or any(
                token in normalized
                for token in ("application", "adapters", "ports", "workflow")
            ):
                findings.append(Finding("DOMAIN_FORBIDDEN_IMPORT", path, line))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in DOMAIN_FORBIDDEN_CALLS:
                findings.append(Finding("DOMAIN_FORBIDDEN_CALL", path, node.lineno))

    if path.startswith("src/platforms/utiki/application/"):
        for module, line in _resolved_imports(path, tree):
            if module in {"platforms.utiki.adapters", "platforms.utiki.bootstrap"} or (
                module.startswith("platforms.utiki.adapters.")
                or module.startswith("platforms.utiki.bootstrap.")
            ):
                findings.append(Finding("APPLICATION_REVERSE_IMPORT", path, line))
        for module, line in imports:
            normalized = module.lstrip(".")
            if normalized.split(".", 1)[0] in APPLICATION_FORBIDDEN_IMPORT_ROOTS:
                findings.append(Finding("APPLICATION_RAW_IO_IMPORT", path, line))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in APPLICATION_BROWSER_ATTRIBUTES:
                findings.append(
                    Finding("APPLICATION_BROWSER_PRIMITIVE", path, node.lineno)
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                findings.append(Finding("APPLICATION_RAW_IO_CALL", path, node.lineno))
    return findings


def _legacy_findings(path: str, tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    imports = _imports(tree)
    for module, line in imports:
        normalized = module.lstrip(".")
        if normalized.startswith("platforms.") and not normalized.startswith(
            "platforms.utiki"
        ):
            findings.append(Finding("CROSS_PLATFORM_IMPORT", path, line))
        if "nodriver_tixcraft" in normalized.split("."):
            findings.append(Finding("PLATFORM_IMPORTS_MAIN", path, line))
    if path in LEGACY_LEAF_FILES:
        for module, line in imports:
            if any(
                token in module.lstrip(".").split(".")
                for token in ("workflow", "refresh", "dom", "nodriver_tixcraft")
            ):
                findings.append(Finding("LEAF_REVERSE_IMPORT", path, line))
    if path == "src/nodriver_common.py":
        for module, line in imports:
            normalized = module.lstrip(".")
            if normalized.startswith("platforms") or normalized.startswith(
                "nodriver_tixcraft"
            ):
                findings.append(Finding("COMMON_REVERSE_IMPORT", path, line))
    if path == "src/settings.py":
        forbidden_project_roots = {
            "nodriver_common",
            "nodriver_tixcraft",
            "platforms",
            "refresh_coordinator",
        }
        for module, line in imports:
            if module.lstrip(".").split(".", 1)[0] in forbidden_project_roots:
                findings.append(Finding("SETTINGS_FORBIDDEN_IMPORT", path, line))
    return findings


def _project_roots(root: Path) -> set[str]:
    roots = {
        path.stem
        for path in (root / "src").glob("*.py")
        if path.is_file()
    }
    roots.update(
        {
            "global_settings",
            "nodriver_common",
            "nodriver_tixcraft",
            "platforms",
            "refresh_coordinator",
            "settings",
            "util",
        }
    )
    return roots


def _bridge_findings(root: Path, path: str, tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    project_roots = _project_roots(root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_root = alias.name.split(".", 1)[0]
                if import_root in project_roots:
                    findings.append(
                        Finding("CORE_BRIDGE_FORBIDDEN_IMPORT", path, node.lineno)
                    )
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lstrip(".")
            import_root = module.split(".", 1)[0]
            approved_port = module == "platforms.utiki.application.ports"
            if approved_port:
                continue
            if import_root in CORE_BRIDGE_APPROVED_PROJECT_ROOTS:
                approved = CORE_BRIDGE_APPROVED_SYMBOLS[import_root]
                if module != import_root or any(
                    alias.name == "*" or alias.name not in approved
                    for alias in node.names
                ):
                    findings.append(
                        Finding("CORE_BRIDGE_FORBIDDEN_IMPORT", path, node.lineno)
                    )
            elif import_root in project_roots or node.level:
                findings.append(
                    Finding("CORE_BRIDGE_FORBIDDEN_IMPORT", path, node.lineno)
                )
    return findings


def _deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(set(findings), key=lambda item: (item.path, item.line, item.reason))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_manifest(root: Path, files: list[tuple[str, Path]]) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for relative, path in files:
        _safe_regular_file(root, path)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise GateError("SOURCE_UNREADABLE") from exc
        manifest.append({"path": relative, "sha256": digest})
    return manifest


def _report(
    status: str,
    baseline: str | None,
    files: list[str],
    findings: list[Finding],
    *,
    repository_root: str | None,
    candidate_sha: str | None,
    source_manifest: list[dict[str, str]],
) -> dict[str, object]:
    target_files = [path for path in files if _is_target(path)]
    return {
        "baseline_sha": baseline,
        "candidate_sha": candidate_sha,
        "checked_files": files,
        "checked_files_sha256": _canonical_digest(files),
        "failure_reasons": sorted({finding.reason for finding in findings}),
        "findings": [finding.as_json() for finding in findings],
        "repository_root": repository_root,
        "schema_version": SCHEMA_VERSION,
        "source_manifest": source_manifest,
        "source_manifest_sha256": _canonical_digest(source_manifest),
        "status": status,
        "target_files": target_files,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the current uTiki import DAG.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-sha")
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.strict:
        sys.stderr.write("check_utiki_architecture: STRICT_REQUIRED\n")
        return 2
    try:
        root = arguments.root.resolve(strict=True)
        if arguments.candidate_sha is not None and not SHA_RE.fullmatch(
            arguments.candidate_sha
        ):
            raise GateError("CANDIDATE_SHA_INVALID")
        files = _python_files(root)
        findings: list[Finding] = []
        for relative, path in files:
            if relative in RETIRED_SIDE_EFFECT_FILES:
                findings.append(Finding("RETIRED_SIDE_EFFECT_MODULE", relative, 1))
            tree = _parse(root, path)
            if _is_target(relative):
                findings.extend(_target_findings(relative, tree))
            else:
                findings.extend(_legacy_findings(relative, tree))
            if relative == "src/utiki_core_services.py":
                findings.extend(_bridge_findings(root, relative, tree))
        findings = _deduplicate(findings)
        relative_files = [item[0] for item in files]
        source_manifest = _source_manifest(root, files)
        report = _report(
            "FAIL" if findings else "PASS",
            None,
            relative_files,
            findings,
            repository_root=str(root),
            candidate_sha=arguments.candidate_sha,
            source_manifest=source_manifest,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1 if findings else 0
    except GateError as exc:
        report = _report(
            "FAIL",
            None,
            [],
            [Finding(exc.reason, "", 0)],
            repository_root=None,
            candidate_sha=arguments.candidate_sha,
            source_manifest=[],
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        sys.stderr.write(f"check_utiki_architecture: {exc.reason}\n")
        return 2
    except Exception:
        report = _report(
            "FAIL",
            None,
            [],
            [Finding("INTERNAL_ERROR", "", 0)],
            repository_root=None,
            candidate_sha=arguments.candidate_sha,
            source_manifest=[],
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        sys.stderr.write("check_utiki_architecture: INTERNAL_ERROR\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
