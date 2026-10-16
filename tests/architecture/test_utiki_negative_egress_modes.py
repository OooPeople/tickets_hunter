from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import utiki_egress_canary as canary


def _input(tmp_path: Path, mode: str) -> Path:
    path = tmp_path / f"{mode}.json"
    path.write_text(
        json.dumps({"schema_version": 1, "mode": mode}), encoding="utf-8"
    )
    return path


def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(canary.sys, "platform", "linux")
    monkeypatch.setenv(canary.ACTIVE_ENV, "1")
    monkeypatch.setenv(canary.PEER_IPV4_ENV, "192.0.2.1")
    monkeypatch.setenv(canary.PEER_IPV6_ENV, "2001:db8::1")
    monkeypatch.setenv(canary.PEER_PORT_ENV, "18080")


@pytest.mark.parametrize(
    "mode",
    ["interception-disabled", "subresource", "redirect", "websocket", "worker"],
)
def test_browser_negative_modes_use_real_browser_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _environment(monkeypatch)
    calls: list[tuple[str, int, str]] = []

    async def fake_browser(host: str, port: int, selected: str) -> str:
        calls.append((host, port, selected))
        return "blocked-error"

    monkeypatch.setattr(canary, "_browser_negative_probe", fake_browser)

    assert canary._negative_probe(_input(tmp_path, mode)) == 1
    assert calls == [("192.0.2.1", 18080, mode)]


@pytest.mark.parametrize(
    ("mode", "function_name"),
    [
        ("direct-ipv4", "_http_probe"),
        ("direct-ipv6", "_ipv6_probe"),
        ("dns", "_dns_probe"),
        ("udp-quic", "_udp_probe"),
    ],
)
def test_os_negative_modes_use_their_reviewed_socket_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    function_name: str,
) -> None:
    _environment(monkeypatch)
    calls: list[tuple[object, ...]] = []

    def blocked(*arguments: object) -> None:
        calls.append(arguments)
        raise OSError("blocked by isolated namespace")

    monkeypatch.setattr(canary, function_name, blocked)

    assert canary._negative_probe(_input(tmp_path, mode)) == 1
    assert len(calls) == 1


def test_child_process_mode_executes_a_separate_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=1,
            stdout="EGRESS_BLOCKED mode=child-process\n",
            stderr="",
        )

    monkeypatch.setattr(canary.subprocess, "run", fake_run)

    assert canary._negative_probe(_input(tmp_path, "child-process")) == 1
    assert commands and "--negative-probe" in commands[0]


def test_missing_attestation_mode_does_not_masquerade_as_network_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(canary.ACTIVE_ENV, raising=False)
    assert canary._negative_probe(_input(tmp_path, "missing-isolation-proof")) == 1
