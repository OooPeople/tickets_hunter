import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "tools" / "check_upstream_boundary.py"


def _run(command, *, cwd):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _git(repo, *arguments):
    result = _run(["git", *arguments], cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "Gate Test")
    _write(repo / "src" / "nodriver_tixcraft.py", "VALUE = 1\n")
    _write(repo / "src" / "platforms" / "utiki" / "profiles.py", "VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    upstream_url = "https://example.invalid/upstream.git"
    _git(repo, "remote", "add", "upstream", upstream_url)
    _git(repo, "update-ref", "refs/remotes/upstream/main", baseline)
    inventory = repo / "upstream-touchpoints.md"
    _write(
        inventory,
        f"""# uTiki upstream touchpoints

| Actual HEAD | `{baseline}` |
| Upstream URL | `{upstream_url}` |
| Local upstream ref | `upstream/main@{baseline}` |
| Merge base／上次整合點 | `{baseline}` |

## Shared／upstream-owned touchpoints

| Path | Handling |
|---|---|
| `src/nodriver_tixcraft.py` | minimal composition-root patch |

## Fork-only family paths

`src/platforms/utiki/` and `src/utiki_core_services.py` are fork-only.
""",
    )
    return repo, inventory, baseline


def _check(repo, inventory):
    return _run(
        [
            sys.executable,
            str(CHECKER),
            "--root",
            str(repo),
            "--inventory",
            str(inventory),
        ],
        cwd=repo,
    )


def test_declared_shared_touchpoint_passes(tmp_path):
    repo, inventory, _ = _repo(tmp_path)
    _write(repo / "src" / "nodriver_tixcraft.py", "VALUE = 2\n")

    result = _check(repo, inventory)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["changed_touchpoints"] == ["src/nodriver_tixcraft.py"]


def test_new_undeclared_shared_module_fails_closed(tmp_path):
    repo, inventory, _ = _repo(tmp_path)
    _write(repo / "src" / "new_core.py", "VALUE = 2\n")

    result = _check(repo, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "UNDECLARED_UPSTREAM_TOUCHPOINT" in report["failure_reasons"]
    assert "src/new_core.py" in report["undeclared_touchpoints"]


def test_planned_fork_only_target_does_not_require_existing_touchpoint(tmp_path):
    repo, inventory, _ = _repo(tmp_path)
    _write(
        repo / "src" / "platforms" / "utiki" / "bootstrap.py",
        "def create_engine() -> object:\n    return object()\n",
    )

    result = _check(repo, inventory)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["changed_touchpoints"] == []


def test_planned_fork_only_upgrader_spec_is_not_a_shared_touchpoint(tmp_path):
    repo, inventory, _ = _repo(tmp_path)
    _write(
        repo / "build_scripts" / "utiki_profile_upgrader.spec",
        "VALUE = 1\n",
    )

    result = _check(repo, inventory)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["changed_touchpoints"] == []


def test_pinned_upstream_ref_mismatch_fails(tmp_path):
    repo, inventory, _ = _repo(tmp_path)
    _write(repo / "README.md", "new upstream\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "different upstream")
    other = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/upstream/main", other)

    result = _check(repo, inventory)

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "UPSTREAM_REF_MISMATCH" in report["failure_reasons"]
