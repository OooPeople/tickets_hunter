from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILDER = PROJECT_ROOT / "tools" / "build_utiki_isolation_image.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("utiki_image_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _repository(root: Path) -> Path:
    repository = root / "repo"
    (repository / ".git").mkdir(parents=True)
    for relative in (
        "ci/utiki-isolation/Dockerfile",
        "requirement.txt",
        "requirements-dev.txt",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"input={relative}\n", encoding="utf-8")
    return repository


def test_builder_pins_platform_image_id_and_input_digests(tmp_path, monkeypatch, capsys):
    builder = _load_builder()
    repository = _repository(tmp_path)
    private = repository / "src" / "settings.json"
    private.parent.mkdir(parents=True)
    private.write_text("private-setting\n", encoding="utf-8")
    (repository / "artifacts").mkdir()
    (repository / "artifacts" / "private.txt").write_text(
        "private-artifact\n", encoding="utf-8"
    )
    image_id = "sha256:" + "b" * 64
    commands: list[list[str]] = []
    build_context_entries: set[str] | None = None

    monkeypatch.setattr(builder, "_validate_host", lambda: None)

    def fake_run(command, *, cwd):
        nonlocal build_context_entries
        commands.append(list(command))
        output = ""
        if command[1] == "build":
            build_context_entries = {item.name for item in cwd.iterdir()}
        if command[1:3] == ["image", "inspect"]:
            output = json.dumps(
                {"Id": image_id, "Os": "linux", "Architecture": "amd64"}
            )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(builder, "_run", fake_run)
    evidence = tmp_path / "out" / "image-build.json"
    base = "python@sha256:" + "a" * 64

    exit_code = builder.main(
        [
            "--repository-root",
            str(repository),
            "--base-image",
            base,
            "--tag",
            "utiki-p1:test",
            "--evidence",
            str(evidence),
        ]
    )

    assert exit_code == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["base_image"] == base
    assert payload["image_id"] == image_id
    assert payload["build_completed_before_isolation"] is True
    assert payload["isolation_phase_pulled_or_built"] is False
    assert {item["path"] for item in payload["inputs"]} == set(builder.REQUIRED_INPUTS)
    build = next(command for command in commands if command[1] == "build")
    assert "--pull=false" in build
    assert build[build.index("--platform") + 1] == "linux/amd64"
    assert f"BASE_IMAGE={base}" in build
    assert build_context_entries == {
        "Dockerfile",
        "requirement.txt",
        "requirements-dev.txt",
    }
    assert str(repository) not in " ".join(build)
    assert json.loads(capsys.readouterr().out)["image_id"] == image_id


@pytest.mark.parametrize(
    "base_image",
    [
        "python:3.11.9-slim-bookworm",
        "sha256:" + "a" * 64,
        "python@sha256:not-a-digest",
    ],
)
def test_builder_rejects_unpinned_base_before_runtime(tmp_path, monkeypatch, base_image):
    builder = _load_builder()
    repository = _repository(tmp_path)
    monkeypatch.setattr(builder, "_validate_host", lambda: None)
    monkeypatch.setattr(
        builder,
        "_run",
        lambda *args, **kwargs: pytest.fail("runtime must not execute"),
    )

    exit_code = builder.main(
        [
            "--repository-root",
            str(repository),
            "--base-image",
            base_image,
            "--tag",
            "utiki-p1:test",
            "--evidence",
            str(tmp_path / "image-build.json"),
        ]
    )

    assert exit_code != 0


def test_builder_rejects_wrong_image_platform(tmp_path, monkeypatch):
    builder = _load_builder()
    repository = _repository(tmp_path)
    monkeypatch.setattr(builder, "_validate_host", lambda: None)

    def fake_run(command, *, cwd):
        output = ""
        if command[1:3] == ["image", "inspect"]:
            output = json.dumps(
                {
                    "Id": "sha256:" + "b" * 64,
                    "Os": "linux",
                    "Architecture": "arm64",
                }
            )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(builder, "_run", fake_run)

    exit_code = builder.main(
        [
            "--repository-root",
            str(repository),
            "--base-image",
            "python@sha256:" + "a" * 64,
            "--tag",
            "utiki-p1:test",
            "--evidence",
            str(tmp_path / "image-build.json"),
        ]
    )

    assert exit_code != 0
    assert not (tmp_path / "image-build.json").exists()


def test_builder_refuses_stale_evidence(tmp_path, monkeypatch):
    builder = _load_builder()
    repository = _repository(tmp_path)
    evidence = tmp_path / "image-build.json"
    evidence.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(builder, "_validate_host", lambda: None)
    monkeypatch.setattr(
        builder,
        "_run",
        lambda *args, **kwargs: pytest.fail("runtime must not execute"),
    )

    exit_code = builder.main(
        [
            "--repository-root",
            str(repository),
            "--base-image",
            "python@sha256:" + "a" * 64,
            "--tag",
            "utiki-p1:test",
            "--evidence",
            str(evidence),
        ]
    )

    assert exit_code != 0
