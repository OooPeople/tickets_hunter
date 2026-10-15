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

from settings import (  # noqa: E402
    ProfilesHandler,
    ResetJsonHandler,
    SaveJsonHandler,
    get_default_config,
    save_profile_config,
)


class ProfileConfigPersistenceTest(unittest.TestCase):
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
            (r"/save", SaveJsonHandler),
            (r"/reset", ResetJsonHandler),
            (r"/profiles", ProfilesHandler),
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


if __name__ == "__main__":
    unittest.main()
