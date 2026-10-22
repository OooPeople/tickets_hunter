from contextlib import redirect_stderr, redirect_stdout
import io
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
    LoadJsonHandler,
    ProfilesHandler,
    ResetJsonHandler,
    RunHandler,
    SaveJsonHandler,
    get_default_config,
    migrate_config,
    save_profile_config,
)
from nodriver_tixcraft import reload_config  # noqa: E402
from nodriver_common import get_config_dict  # noqa: E402
from platforms.utiki.application.flow_settings import (  # noqa: E402
    apply_effective_policy_reload, load_effective_policy,
)
from utiki_config import load_current_profile_policy  # noqa: E402


def test_formal_utiki_policy_preserves_four_ocr_modes_with_headless_sid() -> None:
    with tempfile.TemporaryDirectory(prefix="utiki-current-settings-") as folder:
        source = Path(folder) / "profile.json"
        args = SimpleNamespace(
            input=str(source), headless=None, homepage=None, ticket_number=2,
            browser=None, proxy_server=None, window_size=None,
            date_auto_select_mode=None, date_keyword=None,
            area_auto_select_mode=None, area_keyword=None,
        )
        profile = get_default_config()
        profile["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        profile["advanced"]["headless"] = True
        profile["accounts"]["tixcraft_sid"] = "synthetic-sid"
        for enable in (False, True):
            for force_submit in (False, True):
                profile["ocr_captcha"].update({
                    "enable": enable, "force_submit": force_submit,
                })
                source.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
                effective = get_config_dict(args, apply_tixcraft_headless_ocr=False)
                assert effective is not None
                assert get_config_dict(args) == effective  # Formal startup uses this default call.
                _behavior, flow = load_effective_policy(effective)
                assert (flow.ocr_enabled, flow.force_submit) == (enable, force_submit)
                assert flow.ticket_number == 2  # CLI override remains effective.
        profile["homepage"] = "https://tixcraft.com/"
        profile["ocr_captcha"].update({"enable": False, "force_submit": False})
        source.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        legacy = get_config_dict(args)
        assert legacy is not None
        assert legacy["ocr_captcha"]["enable"] is True
        assert legacy["ocr_captcha"]["force_submit"] is True
        for homepage in (
            "http://tix.ctbcsports.com/BROTHERS/",
            "//tix.ctbcsports.com/BROTHERS/",
            "https://user@tix.ctbcsports.com/BROTHERS/",
            "https://tix.ctbcsports.com:444/BROTHERS/",
            "https://tix.ctbcsports.com:invalid/BROTHERS/",
        ):
            profile["homepage"] = homepage
            source.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
            legacy = get_config_dict(args)
            assert legacy is not None
            assert legacy["ocr_captcha"]["enable"] is True
            assert legacy["ocr_captcha"]["force_submit"] is True


class ProfileConfigPersistenceTest(unittest.TestCase):
    def test_utiki_seat_selection_ui_is_wired_to_canonical_setting(self):
        html = (SRC / "www" / "settings.html").read_text(encoding="utf-8")
        javascript = (SRC / "www" / "settings.js").read_text(encoding="utf-8")

        self.assertEqual(1, html.count('id="ctbc_seat_selection_mode"'))
        self.assertIn('<option value="manual" selected>', html)
        self.assertIn('<option value="computer">', html)
        self.assertIn(
            "utiki_behavior_setting(settings, 'seat_selection_mode', 'manual')",
            javascript,
        )
        self.assertIn(
            "settings.utiki.seat_selection_mode = ctbc_seat_selection_mode.value",
            javascript,
        )
        self.assertIn("delete settings.ctbc", javascript)
        self.assertNotIn("settings.ctbc.seat_selection_mode =", javascript)
        self.assertIn(
            "ctbc_seat_selection_mode.value !== savedValue",
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

    def test_save_failure_preserves_the_previous_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "settings.json"
            target.write_text('{"previous": true}\n', encoding="utf-8")

            with patch("settings.json.dump", side_effect=OSError("save failed")):
                with self.assertRaises(OSError):
                    save_profile_config({"replacement": True}, target)

            self.assertEqual(
                {"previous": True},
                json.loads(target.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(Path(temp_dir).glob(".profile_settings_*.tmp")))

    def test_current_round_trip_preserves_other_platform_and_private_data(self):
        config = {
            "utiki": {
                "ticket_type_keyword": "",
                "seat_sorting": "display",
                "seat_selection_mode": "computer",
            },
            "ibon": {"enabled": True},
            "accounts": {
                "ctbc_cookie": "fixture-cookie-secret",
                "ctbc_account": "fixture-account",
                "ctbc_password": "fixture-password",
            },
            "coordinator": {"lease": "keep"},
            "profile": {"directory": "keep"},
        }
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "settings.json"
            with redirect_stdout(output), redirect_stderr(output):
                save_profile_config(config, target)
            persisted = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(config, persisted)
        self.assertNotIn("fixture-cookie-secret", output.getvalue())
        self.assertNotIn("fixture-password", output.getvalue())

    def test_unrelated_migration_does_not_rewrite_utiki_behavior_or_accounts(self):
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
        self.assertEqual("unsupported", migrated["utiki"]["seat_sorting"])
        self.assertEqual("unsupported", migrated["utiki"]["seat_selection_mode"])
        self.assertNotEqual(migrated["utiki"], migrated["ctbc"])
        self.assertEqual(
            {"ticket_type_keyword", "seat_sorting", "seat_selection_mode"},
            set(migrated["utiki"]),
        )
        self.assertEqual("cookie-sentinel", migrated["accounts"]["ctbc_cookie"])

        snapshot = json.loads(json.dumps(migrated))
        self.assertEqual(snapshot, migrate_config(migrated))

    def test_utiki_behavior_migration_does_not_copy_legacy_values(self):
        migrated = migrate_config({
            "utiki": {"ticket_type_keyword": "家族票種"},
            "ctbc": {
                "ticket_type_keyword": "舊票種",
                "seat_sorting": "display",
                "seat_selection_mode": "computer",
            },
        })

        self.assertEqual("家族票種", migrated["utiki"]["ticket_type_keyword"])
        self.assertNotIn("seat_sorting", migrated["utiki"])
        self.assertNotIn("seat_selection_mode", migrated["utiki"])
        self.assertEqual("display", migrated["ctbc"]["seat_sorting"])


class ProfileConfigReloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_hot_reload_migrates_before_updating_runtime_config(self):
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
            target.write_text(json.dumps(replacement), encoding="utf-8")

            with patch("nodriver_tixcraft.asyncio.sleep", new=AsyncMock()):
                runtime, mtime = await reload_config(get_default_config(), 0, target)

        self.assertGreater(mtime, 0)
        self.assertEqual("熱更新票種", runtime["utiki"]["ticket_type_keyword"])
        self.assertEqual("computer", runtime["utiki"]["seat_selection_mode"])
        self.assertNotEqual(runtime["utiki"], runtime["ctbc"])


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
            (r"/load", LoadJsonHandler),
            (r"/save", SaveJsonHandler),
            (r"/reset", ResetJsonHandler),
            (r"/profiles", ProfilesHandler),
            (r"/run", RunHandler),
        ])

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
        with patch(
            "settings.save_profile_config", side_effect=OSError("write failed")
        ):
            reset_response = self.fetch("/reset")
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

    def test_utiki_old_format_is_rejected_and_current_round_trips(self):
        old_config = get_default_config()
        old_config["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        old_config.pop("utiki")
        old_config["ctbc"] = {
            "ticket_type_keyword": "學生票",
            "seat_sorting": "display",
            "seat_selection_mode": "computer",
        }

        save_response = self.fetch(
            "/save",
            method="POST",
            body=json.dumps(old_config),
        )
        self.assertEqual(400, save_response.code)
        self.assertIn("reopen Settings", json.loads(save_response.body)["error"]["message"])
        self.assertFalse((Path(self.temp_dir.name) / "settings.json").exists())

        new_only = get_default_config()
        new_only["homepage"] = old_config["homepage"]
        new_only["utiki"] = {
            "ticket_type_keyword": "家族限定票",
            "seat_sorting": "row_asc_aisle",
            "seat_selection_mode": "computer",
        }
        saved_response = self.fetch(
            "/save", method="POST", body=json.dumps(new_only),
        )
        self.assertEqual(200, saved_response.code)

        load_response = self.fetch("/load")
        self.assertEqual(200, load_response.code)
        self.assertEqual("1", load_response.headers["X-Utiki-Current-Ready"])
        loaded = json.loads(load_response.body)
        self.assertEqual(new_only["utiki"], loaded["utiki"])
        self.assertNotIn("ctbc", loaded)

    def test_loaded_utiki_profile_with_invalid_current_fields_is_not_run_ready(self):
        invalid = get_default_config()
        invalid["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        invalid["utiki"]["seat_selection_mode"] = "unsupported"
        (Path(self.temp_dir.name) / "settings.json").write_text(
            json.dumps(invalid, ensure_ascii=False), encoding="utf-8",
        )
        response = self.fetch("/load")
        self.assertEqual(200, response.code)
        self.assertEqual("0", response.headers["X-Utiki-Current-Ready"])
        with patch("settings.launch_maxbot") as launch:
            run = self.fetch("/run")
        self.assertEqual(400, run.code)
        self.assertFalse(json.loads(run.body)["run"])
        launch.assert_not_called()

    def test_named_utiki_profile_create_edit_and_load_share_effective_policy(self):
        initial = get_default_config()
        initial["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        initial["utiki"]["seat_selection_mode"] = "computer"
        initial["ticket_number"] = 1
        initial["accounts"]["ctbc_account"] = "fixture-account"
        created = self.fetch(
            "/profiles", method="POST",
            body=json.dumps({"name": "fixture-profile", "config": initial}),
        )
        self.assertEqual(200, created.code)
        loaded = json.loads(self.fetch("/load?profile=fixture-profile").body)
        self.assertEqual(load_current_profile_policy(initial),
                         load_current_profile_policy(loaded))

        loaded["ticket_number"] = 2
        loaded["utiki"]["ticket_type_keyword"] = "合成票種"
        saved = self.fetch(
            "/save?profile=fixture-profile", method="POST", body=json.dumps(loaded),
        )
        self.assertEqual(200, saved.code)
        reloaded = json.loads(self.fetch("/load?profile=fixture-profile").body)
        runtime = apply_effective_policy_reload(
            None, json.dumps(reloaded, ensure_ascii=False),
        )
        self.assertTrue(runtime.allows_new_mutation)
        self.assertEqual(load_current_profile_policy(reloaded),
                         (runtime.last_valid, runtime.flow))
        self.assertEqual(2, runtime.flow.ticket_number)
        self.assertEqual("fixture-account", reloaded["accounts"]["ctbc_account"])
        self.assertNotIn("ctbc", reloaded)


if __name__ == "__main__":
    unittest.main()
