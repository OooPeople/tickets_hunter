"""Native Windows/macOS source startup with a local page and dedicated profile."""

import asyncio
import builtins
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys

import pytest

import nodriver_tixcraft as app
import settings


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(sys.platform not in {"win32", "darwin"}, reason="target OS only"),
]


def test_native_source_starts_real_browser_and_stops_its_own_run(tmp_path: Path) -> None:
    profile = tmp_path / "browser-profile"
    profile.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    homepage = (
        Path(__file__).resolve().parents[2] / "fixtures" / "ctbc" / "UTK0201_flow.html"
    ).as_uri()
    config = settings.get_default_config()
    config["homepage"] = homepage
    config["advanced"]["headless"] = True
    config["ocr_captcha"]["enable"] = False
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    args = SimpleNamespace(
        input=str(config_path), instance=None, homepage=None, ticket_number=None,
        headless=None, browser=None, window_size=None, proxy_server=None,
        date_auto_select_mode=None, date_keyword=None, area_auto_select_mode=None,
        area_keyword=None, mcp_debug=None, mcp_connect=None,
    )
    original_get_extension_config = app.get_extension_config

    def dedicated_browser_config(config_dict: dict, cli_args: object) -> object:
        result = original_get_extension_config(config_dict, cli_args)
        result.user_data_dir = profile
        result.browser_args.extend([
            "--disable-background-networking", "--disable-default-apps",
            "--disable-sync", "--no-default-browser-check", "--no-first-run",
        ])
        return result

    original_print = builtins.print
    try:
        with (
            patch.object(app, "get_extension_config", side_effect=dedicated_browser_config),
            patch.object(app, "check_and_handle_quit", new=AsyncMock(return_value=True)),
            patch.object(app.util, "get_instance_state_path", side_effect=lambda name: str(state / name)),
        ):
            asyncio.run(app.main(args))
    finally:
        builtins.print = original_print
    assert profile.is_dir()
    assert not (state / "heartbeat.txt").exists()
