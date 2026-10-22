"""Current uTiki Settings form against real local handlers, with no egress."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import pytest
import zendriver as uc
from zendriver.core.connection import ProtocolException
from tornado.httpserver import HTTPServer
from tornado.web import Application, StaticFileHandler


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from settings import (  # noqa: E402
    GlobalSettingsHandler, LoadJsonHandler, ProfilesHandler, SaveJsonHandler,
    get_default_config,
)
from utiki_config import load_current_profile_policy  # noqa: E402


pytestmark = pytest.mark.browser


def test_batch5_settings_ui_reenters_legacy_profile_and_loads_current_policy() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("settings UI browser lacks independent network isolation")

    async def run_ui() -> None:
        browser = None
        server = None
        sandbox = tempfile.TemporaryDirectory(prefix="utiki-batch5-settings-")
        profile_path = Path(sandbox.name) / "settings.json"
        legacy = get_default_config()
        legacy["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        legacy["ctbc"] = {
            "ticket_type_keyword": "舊票種",
            "seat_sorting": "display",
            "seat_selection_mode": "computer",
        }
        profile_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        try:
            with patch("settings.util.get_app_root", return_value=sandbox.name):
                app = Application([
                    (r"/load", LoadJsonHandler),
                    (r"/save", SaveJsonHandler),
                    (r"/profiles", ProfilesHandler),
                    (r"/global-settings", GlobalSettingsHandler),
                    (r"/(.*)", StaticFileHandler, {
                        "path": str(SRC / "www"), "default_filename": "settings.html",
                    }),
                ])
                server = HTTPServer(app)
                server.listen(0, address="127.0.0.1")
                port = next(iter(server._sockets.values())).getsockname()[1]
                browser = await uc.start(
                    user_data_dir=str(Path(sandbox.name) / "browser"),
                    headless=True, sandbox=False,
                    browser_args=[
                        "--disable-background-networking", "--disable-default-apps",
                        "--disable-sync", "--no-default-browser-check", "--no-first-run",
                    ],
                )
                tab = browser.main_tab
                await tab.get(f"http://127.0.0.1:{port}/settings.html")
                for _ in range(120):
                    ready = await tab.evaluate(
                        "typeof settings !== 'undefined' && !!settings && "
                        "!document.querySelector('#save_btn').disabled"
                    )
                    if ready:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Settings form did not load the saved profile")
                assert await tab.evaluate(
                    "document.querySelector('#run_btn').disabled"
                ) is True
                assert await tab.evaluate(
                    "document.querySelector('#ctbc_ticket_type_keyword').value"
                ) == legacy["utiki"]["ticket_type_keyword"]

                await tab.evaluate(
                    "document.querySelector('#ctbc_ticket_type_keyword').value="
                    "'合成新票種'; document.querySelector('#save_btn').click()"
                )
                for _ in range(120):
                    saved = json.loads(profile_path.read_text(encoding="utf-8"))
                    if (
                        "ctbc" not in saved and saved["utiki"]["ticket_type_keyword"]
                        == "合成新票種"
                    ):
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Settings form did not save current behavior")
                load_current_profile_policy(saved)
                assert await tab.evaluate(
                    "document.querySelector('#run_btn').disabled"
                ) is False
                prior_document = await tab.evaluate("String(performance.timeOrigin)")
                await tab.reload()
                deadline = asyncio.get_running_loop().time() + 6.0
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        loaded = await tab.evaluate("""(() => ({
                            document: String(performance.timeOrigin),
                            ready: document.readyState !== 'loading' &&
                                typeof settings !== 'undefined' && !!settings &&
                                !!document.querySelector('#ctbc_ticket_type_keyword') &&
                                !document.querySelector('#save_btn').disabled,
                            value: document.querySelector('#ctbc_ticket_type_keyword')?.value
                        }))()""")
                    except ProtocolException as exc:
                        if "Inspected target navigated or closed" not in str(exc):
                            raise
                        await asyncio.sleep(0.05)
                        continue
                    if (loaded["document"] != prior_document and loaded["ready"]
                        and loaded["value"] == "合成新票種"):
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Saved uTiki behavior did not load in a new document")
        finally:
            if browser is not None:
                await browser.stop()
            if server is not None:
                server.stop()
            sandbox.cleanup()

    asyncio.run(run_ui())
