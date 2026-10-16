#!/usr/bin/env python3
"""Materialize an exact Git candidate without exposing the source checkout."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
from typing import Any, Iterator


class WorkspaceError(RuntimeError):
    """The candidate cannot be materialized as a trustworthy workspace."""


def _git(repository: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceError("git candidate context cannot be verified") from exc
    if completed.returncode != 0:
        raise WorkspaceError("git candidate context cannot be verified")
    return completed.stdout


def require_clean_candidate(repository: Path, candidate_sha: str) -> None:
    """Require the checked-out candidate and every non-ignored file to be clean."""

    head = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    status = _git(
        repository,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if head != candidate_sha:
        raise WorkspaceError("candidate SHA does not equal repository HEAD")
    if status:
        raise WorkspaceError("formal isolation requires a clean working tree")


def _safe_member_path(member: tarfile.TarInfo) -> PurePosixPath:
    value = PurePosixPath(member.name)
    if not member.name or value.is_absolute() or ".." in value.parts:
        raise WorkspaceError("candidate archive contains an unsafe path")
    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise WorkspaceError("candidate archive contains a non-regular entry")
    if not member.isdir() and not member.isfile():
        raise WorkspaceError("candidate archive contains an unsupported entry")
    return value


def _file_manifest(workspace: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink():
            raise WorkspaceError("candidate workspace contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise WorkspaceError("candidate workspace contains a non-regular file")
        data = path.read_bytes()
        result.append(
            {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "executable": bool(path.stat().st_mode & 0o111),
            }
        )
    result.sort(key=lambda item: item["path"])
    return result


def candidate_attestation(
    workspace: Path, workspace_evidence: dict[str, object]
) -> dict[str, Any]:
    """Build the strict file manifest consumed inside a no-``.git`` container."""

    files = _file_manifest(workspace)
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tree_sha256 = hashlib.sha256(canonical).hexdigest()
    if (
        workspace_evidence.get("tracked_file_count") != len(files)
        or workspace_evidence.get("tree_sha256") != tree_sha256
    ):
        raise WorkspaceError("candidate workspace tree changed after materialization")
    return {
        "schema_version": 1,
        "attestation_kind": "utiki-sanitized-candidate-workspace",
        "candidate_sha": workspace_evidence["candidate_sha"],
        "archive_sha256": workspace_evidence["archive_sha256"],
        "tree_sha256": tree_sha256,
        "tracked_file_count": len(files),
        "git_directory_present": False,
        "files": files,
    }


@contextmanager
def materialize_candidate(
    repository: Path, candidate_sha: str
) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield a sanitized workspace built solely from ``git archive`` bytes."""

    repository = repository.resolve(strict=True)
    require_clean_candidate(repository, candidate_sha)
    archive = _git(repository, "archive", "--format=tar", candidate_sha)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    file_count = 0
    with tempfile.TemporaryDirectory(prefix="utiki-candidate-") as raw_workspace:
        workspace = Path(raw_workspace)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
                for member in source.getmembers():
                    relative = _safe_member_path(member)
                    target = workspace.joinpath(*relative.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    handle = source.extractfile(member)
                    if handle is None:
                        raise WorkspaceError("candidate archive file cannot be read")
                    target.write_bytes(handle.read())
                    target.chmod(member.mode & 0o777)
                    file_count += 1
        except (OSError, tarfile.TarError) as exc:
            raise WorkspaceError("candidate archive cannot be extracted safely") from exc

        forbidden = (
            workspace / ".git",
            workspace / "src" / "settings.json",
        )
        if any(path.exists() or path.is_symlink() for path in forbidden):
            raise WorkspaceError("sanitized candidate contains a forbidden private path")
        files = _file_manifest(workspace)
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        yield workspace, {
            "source": "git-archive",
            "candidate_sha": candidate_sha,
            "archive_sha256": archive_sha256,
            "tracked_file_count": file_count,
            "tree_sha256": hashlib.sha256(canonical).hexdigest(),
            "source_checkout_mounted": False,
            "ignored_or_untracked_files_included": False,
        }
