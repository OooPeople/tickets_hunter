from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools.utiki_isolated_workspace import (
    WorkspaceError,
    _file_manifest,
    materialize_candidate,
    require_clean_candidate,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "workspace-test@example.invalid")
    _git(repository, "config", "user.name", "Workspace Test")
    (repository / ".gitignore").write_text(
        "src/settings.json\nprivate-profile/\nartifacts/\n", encoding="utf-8"
    )
    tracked = repository / "tools" / "probe.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "candidate")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_materialized_candidate_excludes_private_ignored_and_git_data(tmp_path):
    repository, candidate = _repository(tmp_path)
    private_paths = {
        repository / "src" / "settings.json": "private settings",
        repository / "private-profile" / "Default" / "Cookies": "private profile",
        repository / "artifacts" / "run" / "token.txt": "private artifact",
    }
    for path, content in private_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    with materialize_candidate(repository, candidate) as (workspace, evidence):
        assert (workspace / "tools" / "probe.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert not (workspace / ".git").exists()
        assert not (workspace / "src" / "settings.json").exists()
        assert not (workspace / "private-profile").exists()
        assert not (workspace / "artifacts").exists()
        assert evidence["source"] == "git-archive"
        assert evidence["candidate_sha"] == candidate
        assert evidence["source_checkout_mounted"] is False
        assert evidence["ignored_or_untracked_files_included"] is False


def test_clean_candidate_rejects_dirty_or_untracked_files(tmp_path):
    repository, candidate = _repository(tmp_path)
    (repository / "untracked.txt").write_text("not formal input\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="clean working tree"):
        require_clean_candidate(repository, candidate)


def test_clean_candidate_rejects_a_different_sha(tmp_path):
    repository, _ = _repository(tmp_path)

    with pytest.raises(WorkspaceError, match="does not equal"):
        require_clean_candidate(repository, "0" * 40)


def test_file_manifest_uses_canonical_posix_path_order(tmp_path):
    nested = tmp_path / "docs" / "guide.md"
    sibling = tmp_path / "docs.md"
    nested.parent.mkdir()
    nested.write_text("nested\n", encoding="utf-8")
    sibling.write_text("sibling\n", encoding="utf-8")

    manifest = _file_manifest(tmp_path)

    assert [item["path"] for item in manifest] == ["docs.md", "docs/guide.md"]
