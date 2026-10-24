import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from settings import (  # noqa: E402
    ControlTokenHandler,
    LoadJsonHandler,
    ProfilesHandler,
    ResetJsonHandler,
    SaveJsonHandler,
    get_default_config,
    migrate_config,
    save_profile_config,
)
from nodriver_tixcraft import reload_config  # noqa: E402
from nodriver_common import get_config_dict  # noqa: E402


class ProfileConfigPersistenceTest(unittest.TestCase):
    def test_utiki_seat_selection_ui_is_wired_to_canonical_setting(self):
        html = (SRC / "www" / "settings.html").read_text(encoding="utf-8")
        javascript = (SRC / "www" / "settings.js").read_text(encoding="utf-8")

        self.assertEqual(1, html.count('id="utiki_seat_selection_mode"'))
        self.assertIn('<option value="manual" selected>', html)
        self.assertIn('<option value="computer">', html)
        self.assertIn(
            "utiki_behavior_setting(settings, 'seat_selection_mode', 'manual')",
            javascript,
        )
        self.assertIn(
            "settings.utiki.seat_selection_mode = utiki_seat_selection_mode.value",
            javascript,
        )
        self.assertNotIn("settings.ctbc.seat_selection_mode", javascript)
        self.assertNotIn("['utiki', 'ctbc']", javascript)
        self.assertIn(
            "utiki_seat_selection_mode.value !== savedValue",
            javascript,
        )

    def test_atomic_failure_preserves_the_previous_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "settings.json"
            target.write_text('{"previous": true}\n', encoding="utf-8")

            with patch("settings.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_profile_config({"replacement": True}, target)

            self.assertEqual(
                {"previous": True},
                json.loads(target.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(Path(temp_dir).glob(".profile_settings_*.tmp")))

    def test_current_utiki_values_normalize_without_legacy_mirror(self):
        config = {
            "utiki": {
                "ticket_type_keyword": "",
                "seat_sorting": "unsupported",
                "seat_selection_mode": "unsupported",
            },
            "ctbc": {
                "ticket_type_keyword": "舊票種",
                "seat_sorting": "display",
                "seat_selection_mode": "computer",
            },
            "accounts": {
                "ctbc_cookie": "cookie-sentinel",
                "ctbc_account": "account-sentinel",
                "ctbc_password": "password-sentinel",
            },
        }

        migrated = migrate_config(config)
        self.assertEqual("", migrated["utiki"]["ticket_type_keyword"])
        self.assertEqual("row_seat", migrated["utiki"]["seat_sorting"])
        self.assertEqual("manual", migrated["utiki"]["seat_selection_mode"])
        self.assertEqual("舊票種", migrated["ctbc"]["ticket_type_keyword"])
        self.assertEqual(
            {"ticket_type_keyword", "seat_sorting", "seat_selection_mode"},
            set(migrated["utiki"]),
        )
        self.assertEqual("cookie-sentinel", migrated["accounts"]["ctbc_cookie"])
        self.assertEqual("", migrated["accounts"]["wdragons_cookie"])

        snapshot = json.loads(json.dumps(migrated))
        self.assertEqual(snapshot, migrate_config(migrated))

    def test_missing_current_keys_use_defaults_without_legacy_values(self):
        migrated = migrate_config({
            "utiki": {"ticket_type_keyword": "家族票種"},
            "ctbc": {
                "ticket_type_keyword": "舊票種",
                "seat_sorting": "display",
                "seat_selection_mode": "computer",
            },
        })

        self.assertEqual("家族票種", migrated["utiki"]["ticket_type_keyword"])
        self.assertEqual("row_seat", migrated["utiki"]["seat_sorting"])
        self.assertEqual("manual", migrated["utiki"]["seat_selection_mode"])
        self.assertEqual("display", migrated["ctbc"]["seat_sorting"])


class ProfileConfigReloadTest(unittest.IsolatedAsyncioTestCase):
    def test_source_input_loads_current_utiki_without_legacy_behavior(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "current-settings.json"
            configured = get_default_config()
            configured["utiki"]["ticket_type_keyword"] = "目前票種"
            configured["ctbc"] = {"ticket_type_keyword": "過時票種"}
            target.write_text(json.dumps(configured), encoding="utf-8")
            args = SimpleNamespace(input=str(target), **{
                key: None for key in (
                    "headless", "homepage", "ticket_number", "browser",
                    "proxy_server", "window_size", "date_auto_select_mode",
                    "date_keyword", "area_auto_select_mode", "area_keyword",
                )
            })

            loaded = get_config_dict(args)

        self.assertEqual("目前票種", loaded["utiki"]["ticket_type_keyword"])
        self.assertEqual("過時票種", loaded["ctbc"]["ticket_type_keyword"])

    async def test_hot_reload_uses_current_input_and_keeps_account_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "settings.json"
            replacement = get_default_config()
            replacement["utiki"] = {
                "ticket_type_keyword": "熱更新票種",
                "seat_sorting": "display",
                "seat_selection_mode": "computer",
            }
            replacement["ctbc"] = {
                "ticket_type_keyword": "過時票種",
                "seat_sorting": "row_seat",
                "seat_selection_mode": "manual",
            }
            replacement["accounts"]["ctbc_account"] = "new-account"
            replacement["accounts"]["wdragons_cookie"] = "new-dragons-cookie"
            target.write_text(json.dumps(replacement), encoding="utf-8")

            original = get_default_config()
            original["accounts"]["ctbc_account"] = "original-account"
            original["accounts"]["wdragons_cookie"] = "original-dragons-cookie"
            with patch("nodriver_tixcraft.asyncio.sleep", new=AsyncMock()):
                runtime, mtime = await reload_config(original, 0, target)

        self.assertGreater(mtime, 0)
        self.assertEqual("熱更新票種", runtime["utiki"]["ticket_type_keyword"])
        self.assertEqual("computer", runtime["utiki"]["seat_selection_mode"])
        self.assertNotIn("ctbc", runtime)
        self.assertEqual("original-account", runtime["accounts"]["ctbc_account"])
        self.assertEqual("original-dragons-cookie", runtime["accounts"]["wdragons_cookie"])


class ProfileConfigHandlerTest(AsyncHTTPTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_patch = patch(
            "settings.util.get_app_root", return_value=self.temp_dir.name
        )
        self.root_patch.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def get_app(self):
        return Application([
            (r"/control-token", ControlTokenHandler),
            (r"/load", LoadJsonHandler),
            (r"/save", SaveJsonHandler),
            (r"/reset", ResetJsonHandler),
            (r"/profiles", ProfilesHandler),
        ], xsrf_cookie_name="_th_control_xsrf",
           xsrf_cookie_kwargs={"httponly": True, "samesite": "Strict"})

    def test_save_failure_returns_error_and_preserves_profile(self):
        target = Path(self.temp_dir.name) / "settings.json"
        previous = {"previous": True}
        target.write_text(json.dumps(previous), encoding="utf-8")

        with patch("settings.os.replace", side_effect=OSError("replace failed")):
            response = self.fetch(
                "/save",
                method="POST",
                body=json.dumps(get_default_config()),
            )

        self.assertEqual(500, response.code)
        self.assertEqual(previous, json.loads(target.read_text(encoding="utf-8")))

    def test_reset_and_profile_create_report_write_failures(self):
        token_response = self.fetch("/control-token")
        token = json.loads(token_response.body)["token"]
        cookie = token_response.headers["Set-Cookie"].split(";", 1)[0]
        with patch(
            "settings.save_profile_config", side_effect=OSError("write failed")
        ):
            reset_response = self.fetch(
                "/reset", method="POST", body="",
                headers={"Cookie": cookie, "X-XSRFToken": token},
            )
            create_response = self.fetch(
                "/profiles",
                method="POST",
                body=json.dumps({
                    "name": "new-profile",
                    "config": get_default_config(),
                }),
            )

        self.assertEqual(500, reset_response.code)
        self.assertEqual(500, create_response.code)
        self.assertFalse((Path(self.temp_dir.name) / "profiles" / "new-profile.json").exists())

    def test_save_and_load_current_utiki_behavior(self):
        old_config = get_default_config()
        old_config["utiki"] = {
            "ticket_type_keyword": "學生票",
            "seat_sorting": "display",
            "seat_selection_mode": "computer",
        }
        old_config["ctbc"] = {
            "ticket_type_keyword": "舊票種",
            "seat_sorting": "row_seat",
            "seat_selection_mode": "manual",
        }

        save_response = self.fetch(
            "/save",
            method="POST",
            body=json.dumps(old_config),
        )
        self.assertEqual(200, save_response.code)
        saved = json.loads(save_response.body)
        self.assertEqual("學生票", saved["utiki"]["ticket_type_keyword"])
        self.assertEqual("computer", saved["utiki"]["seat_selection_mode"])
        self.assertEqual("舊票種", saved["ctbc"]["ticket_type_keyword"])
        persisted = json.loads(
            (Path(self.temp_dir.name) / "settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["utiki"], persisted["utiki"])
        self.assertEqual("舊票種", persisted["ctbc"]["ticket_type_keyword"])

        new_only = get_default_config()
        new_only["utiki"] = {
            "ticket_type_keyword": "家族限定票",
            "seat_sorting": "row_asc_aisle",
            "seat_selection_mode": "computer",
        }
        (Path(self.temp_dir.name) / "settings.json").write_text(
            json.dumps(new_only), encoding="utf-8"
        )

        load_response = self.fetch("/load")
        self.assertEqual(200, load_response.code)
        loaded = json.loads(load_response.body)
        self.assertEqual(new_only["utiki"], loaded["utiki"])
        self.assertNotIn("ctbc", loaded)

    def test_dragons_cookie_uses_its_own_current_account_field(self):
        target = Path(self.temp_dir.name) / "settings.json"
        existing = get_default_config()
        existing["accounts"].pop("wdragons_cookie")
        existing["accounts"]["ctbc_cookie"] = "ctbc-sentinel"
        target.write_text(json.dumps(existing), encoding="utf-8")

        load_response = self.fetch("/load")
        self.assertEqual(200, load_response.code)
        loaded = json.loads(load_response.body)
        self.assertEqual("", loaded["accounts"]["wdragons_cookie"])
        self.assertEqual("ctbc-sentinel", loaded["accounts"]["ctbc_cookie"])

        loaded["accounts"]["wdragons_cookie"] = "dragons-sentinel"
        save_response = self.fetch("/save", method="POST", body=json.dumps(loaded))
        self.assertEqual(200, save_response.code)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual("dragons-sentinel", saved["accounts"]["wdragons_cookie"])
        self.assertEqual("ctbc-sentinel", saved["accounts"]["ctbc_cookie"])

    def test_legacy_only_values_do_not_become_current_behavior(self):
        legacy_only = get_default_config()
        legacy_only.pop("utiki")
        legacy_only["ctbc"] = {
            "ticket_type_keyword": "舊票種",
            "seat_sorting": "display",
            "seat_selection_mode": "computer",
        }
        response = self.fetch("/save", method="POST", body=json.dumps(legacy_only))
        self.assertEqual(200, response.code)
        saved = json.loads(response.body)
        self.assertEqual(get_default_config()["utiki"], saved["utiki"])
        self.assertEqual("舊票種", saved["ctbc"]["ticket_type_keyword"])


if __name__ == "__main__":
    unittest.main()
