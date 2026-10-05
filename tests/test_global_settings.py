import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from global_settings import (  # noqa: E402
    get_default_global_settings,
    load_global_settings,
    save_global_settings,
    validate_global_settings,
)
from settings import GlobalSettingsHandler  # noqa: E402


class GlobalSettingsTest(unittest.TestCase):
    def test_defaults_are_safe_and_isolated(self):
        first = get_default_global_settings()
        second = get_default_global_settings()

        self.assertIsNot(first, second)
        self.assertEqual({
            "enabled": True,
            "min_interval_seconds": 1.5,
            "initial_cooldown_seconds": 60,
            "max_cooldown_seconds": 240,
        }, first["utk_sports"])
        first["utk_sports"]["enabled"] = False
        self.assertTrue(second["utk_sports"]["enabled"])

    def test_load_missing_or_invalid_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "global_settings.json"
            self.assertEqual(get_default_global_settings(), load_global_settings(path))

            path.write_text("{not-json", encoding="utf-8")
            self.assertEqual(get_default_global_settings(), load_global_settings(path))

            path.write_text(json.dumps({"utk_sports": {"enabled": True}}), encoding="utf-8")
            self.assertEqual(get_default_global_settings(), load_global_settings(path))

    def test_save_round_trip_is_normalized_and_drops_unknown_keys(self):
        value = {
            "utk_sports": {
                "enabled": False,
                "min_interval_seconds": 2,
                "initial_cooldown_seconds": 90,
                "max_cooldown_seconds": 300,
                "profile_only_value": "must not persist",
            },
            "accounts": {"password": "must not persist"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "global_settings.json"
            saved = save_global_settings(value, path)
            self.assertEqual(saved, load_global_settings(path))
            self.assertEqual(2.0, saved["utk_sports"]["min_interval_seconds"])
            self.assertNotIn("profile_only_value", saved["utk_sports"])
            self.assertNotIn("accounts", saved)

    def test_validation_rejects_unsafe_values(self):
        default = get_default_global_settings()
        invalid_values = []

        value = get_default_global_settings()
        value["utk_sports"]["enabled"] = 1
        invalid_values.append(value)

        value = get_default_global_settings()
        value["utk_sports"]["min_interval_seconds"] = 0.99
        invalid_values.append(value)

        value = get_default_global_settings()
        value["utk_sports"]["initial_cooldown_seconds"] = 0
        invalid_values.append(value)

        value = get_default_global_settings()
        value["utk_sports"]["initial_cooldown_seconds"] = 120
        value["utk_sports"]["max_cooldown_seconds"] = 60
        invalid_values.append(value)

        value = get_default_global_settings()
        value["utk_sports"]["max_cooldown_seconds"] = 240.5
        invalid_values.append(value)

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_global_settings(value)

        self.assertEqual(default, validate_global_settings(default))


class GlobalSettingsHandlerTest(AsyncHTTPTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_patch = patch(
            "global_settings.util.get_app_root", return_value=self.temp_dir.name
        )
        self.root_patch.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def get_app(self):
        return Application([(r"/global-settings", GlobalSettingsHandler)])

    def test_get_defaults_and_post_valid_settings(self):
        response = self.fetch("/global-settings")
        self.assertEqual(200, response.code)
        self.assertEqual(
            get_default_global_settings(), json.loads(response.body.decode("utf-8"))
        )

        value = get_default_global_settings()
        value["utk_sports"]["min_interval_seconds"] = 2.25
        response = self.fetch(
            "/global-settings",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(value),
        )
        self.assertEqual(200, response.code)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["success"])
        self.assertEqual(value, payload["settings"])

        response = self.fetch("/global-settings")
        self.assertEqual(value, json.loads(response.body.decode("utf-8")))

    def test_post_rejects_invalid_range_without_overwriting(self):
        original = get_default_global_settings()
        save_global_settings(original)
        invalid = get_default_global_settings()
        invalid["utk_sports"]["min_interval_seconds"] = 0.5

        response = self.fetch(
            "/global-settings",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(invalid),
        )
        self.assertEqual(400, response.code)
        self.assertFalse(json.loads(response.body.decode("utf-8"))["success"])
        self.assertEqual(original, load_global_settings())


if __name__ == "__main__":
    unittest.main()
