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

from settings import SendkeyHandler, _sendkey_target_path  # noqa: E402


class SendkeyHandlerTest(AsyncHTTPTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_root = Path(self.temp_dir.name) / "app"
        self.app_root.mkdir()
        self.root_patch = patch("settings.util.get_app_root", return_value=str(self.app_root))
        self.root_patch.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def get_app(self):
        return Application([(r"/sendkey", SendkeyHandler)])

    def post_payload(self, payload):
        return self.fetch(
            "/sendkey",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
        )

    def test_valid_token_writes_json_and_can_update_the_same_file(self):
        first = {"token": "synthetic-01", "answer": "first"}
        response = self.post_payload(first)
        target = self.app_root / "synthetic-01.tmp"

        self.assertEqual(200, response.code)
        self.assertEqual({"return": True}, json.loads(response.body))
        self.assertEqual(first, json.loads(target.read_text(encoding="utf-8")))

        second = {"token": "synthetic-01", "answer": "second"}
        response = self.post_payload(second)
        self.assertEqual(200, response.code)
        self.assertEqual({"return": True}, json.loads(response.body))
        self.assertEqual(second, json.loads(target.read_text(encoding="utf-8")))

    def test_valid_json_keeps_the_existing_content_type_behavior(self):
        payload = {"token": "synthetic.sample", "answer": "ready"}
        response = self.fetch(
            "/sendkey",
            method="POST",
            headers={"Content-Type": "text/plain"},
            body=json.dumps(payload),
        )

        self.assertEqual(200, response.code)
        self.assertEqual({"return": True}, json.loads(response.body))
        target = self.app_root / "synthetic.sample.tmp"
        self.assertEqual(payload, json.loads(target.read_text(encoding="utf-8")))

    def test_invalid_json_and_non_object_body_are_rejected(self):
        for body in (b"{invalid", b"[]", b'"token"'):
            with self.subTest(body=body):
                response = self.fetch("/sendkey", method="POST", body=body)
                self.assertEqual(400, response.code)
                self.assertEqual(False, json.loads(response.body)["return"])
                self.assertEqual([], list(self.app_root.iterdir()))

    def test_missing_or_invalid_token_is_rejected(self):
        for payload in (
            {},
            {"token": None},
            {"token": 7},
            {"token": []},
            {"token": ""},
            {"token": "bad\x00name"},
        ):
            with self.subTest(payload=payload):
                response = self.post_payload(payload)
                self.assertEqual(400, response.code)
                self.assertEqual(False, json.loads(response.body)["return"])
                self.assertEqual([], list(self.app_root.iterdir()))

    def test_path_escape_is_rejected_before_writing(self):
        outside = Path(self.temp_dir.name) / "outside.tmp"
        outside.write_text("sentinel", encoding="utf-8")
        for token in (
            "../outside",
            "..\\outside",
            str(Path(self.temp_dir.name) / "outside"),
        ):
            with self.subTest(token=token):
                response = self.post_payload({"token": token})
                self.assertEqual(400, response.code)
                self.assertEqual(False, json.loads(response.body)["return"])
                self.assertEqual("sentinel", outside.read_text(encoding="utf-8"))
                self.assertEqual([], list(self.app_root.iterdir()))

    def test_windows_unsafe_names_are_rejected_without_opening_them(self):
        for token in ("C:drive", "\\\\server\\share\\outside", "CON", "bad|name"):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    _sendkey_target_path(token)

    def test_existing_symlink_cannot_redirect_the_write(self):
        outside = Path(self.temp_dir.name) / "outside.tmp"
        outside.write_text("sentinel", encoding="utf-8")
        link = self.app_root / "synthetic-01.tmp"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        response = self.post_payload({"token": "synthetic-01"})
        self.assertEqual(400, response.code)
        self.assertEqual(False, json.loads(response.body)["return"])
        self.assertEqual("sentinel", outside.read_text(encoding="utf-8"))

    def test_write_failure_does_not_report_success(self):
        with patch("settings.open", side_effect=OSError("synthetic failure"), create=True):
            response = self.post_payload({"token": "synthetic-01"})

        self.assertEqual(500, response.code)
        self.assertEqual(False, json.loads(response.body)["return"])
        self.assertNotIn(str(self.app_root), response.body.decode("utf-8"))
        self.assertEqual([], list(self.app_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
