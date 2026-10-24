"""Local settings controls require a same-origin token before any action."""

import json
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

import settings
from settings import (
    ControlTokenHandler,
    PauseHandler,
    ResetJsonHandler,
    ResumeHandler,
    RunHandler,
    ShutdownHandler,
    StopHandler,
)


class ControlActionSecurityTest(AsyncHTTPTestCase):
    def setUp(self):
        self.patches = [
            patch("settings.GLOBAL_SERVER_SHUTDOWN", False, create=True),
            patch("settings.maxbot_idle"),
            patch("settings.maxbot_resume"),
            patch("settings.maxbot_stop"),
            patch("settings.launch_maxbot"),
            patch("settings.reset_json", return_value=("unused.json", {"reset": True})),
            patch("settings.save_profile_config"),
        ]
        self.mock_actions = [item.start() for item in self.patches]
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for item in reversed(self.patches):
            item.stop()

    def get_app(self):
        return Application([
            (r"/control-token", ControlTokenHandler),
            (r"/shutdown", ShutdownHandler),
            (r"/pause", PauseHandler),
            (r"/resume", ResumeHandler),
            (r"/stop", StopHandler),
            (r"/run", RunHandler),
            (r"/reset", ResetJsonHandler),
        ], xsrf_cookie_name="_th_control_xsrf",
           xsrf_cookie_kwargs={"httponly": True, "samesite": "Strict"})

    def _assert_no_action(self):
        self.assertFalse(settings.GLOBAL_SERVER_SHUTDOWN)
        for action in self.mock_actions[1:]:
            action.assert_not_called()

    def _token_headers(self):
        response = self.fetch("/control-token")
        self.assertEqual(200, response.code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        token = json.loads(response.body)["token"]
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("_th_control_xsrf=", cookie)
        self.assertIn("httponly", response.headers["Set-Cookie"].lower())
        self.assertIn("samesite=strict", response.headers["Set-Cookie"].lower())
        return {"Cookie": cookie, "X-XSRFToken": token}

    def _post(self, path, headers=None):
        return self.fetch(path, method="POST", body="", headers=headers or {})

    def test_get_and_head_never_execute_controls(self):
        paths = (
            "/shutdown", "/pause?profile=example", "/resume?profile=example",
            "/stop?profile=example", "/run?profile=example&instance=example-2",
            "/reset",
        )
        for path in paths:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(405, self.fetch(path, method=method).code)
                    self._assert_no_action()

    def test_missing_or_wrong_token_never_executes_controls(self):
        paths = (
            "/shutdown", "/pause", "/resume", "/stop", "/run", "/reset",
        )
        headers = self._token_headers()
        for path in paths:
            with self.subTest(path=path, token="missing"):
                self.assertEqual(403, self._post(path).code)
                self._assert_no_action()
            with self.subTest(path=path, token="wrong"):
                wrong_headers = dict(headers, **{"X-XSRFToken": "wrong"})
                self.assertEqual(403, self._post(path, wrong_headers).code)
                self._assert_no_action()

    def test_foreign_origin_and_host_are_rejected_before_action(self):
        self.assertEqual(403, self.fetch(
            "/control-token", headers={"Host": "external.invalid:16888"}
        ).code)
        self.assertEqual(403, self.fetch(
            "/control-token", headers={"Origin": "https://external.invalid"}
        ).code)
        headers = self._token_headers()
        for path in ("/shutdown", "/pause", "/resume", "/stop", "/run", "/reset"):
            with self.subTest(path=path, source="origin"):
                self.assertEqual(403, self._post(
                    path, dict(headers, Origin="https://external.invalid")
                ).code)
                self._assert_no_action()
            with self.subTest(path=path, source="host"):
                self.assertEqual(403, self._post(
                    path, dict(headers, Host="external.invalid:16888")
                ).code)
                self._assert_no_action()

    def test_valid_controls_keep_actions_parameters_and_response(self):
        headers = self._token_headers()
        headers["Origin"] = f"http://127.0.0.1:{self.get_http_port()}"

        response = self._post("/pause?profile=example", headers)
        self.assertEqual({"pause": True}, json.loads(response.body))
        self.mock_actions[1].assert_called_once_with("example")

        response = self._post("/resume?profile=example", headers)
        self.assertEqual({"resume": True}, json.loads(response.body))
        self.mock_actions[2].assert_called_once_with("example")

        response = self._post("/stop?profile=example", headers)
        self.assertEqual({"stop": True}, json.loads(response.body))
        self.mock_actions[3].assert_called_once_with("example")

        response = self._post("/run?profile=example&instance=example-2", headers)
        self.assertEqual({"run": True, "profile": "example-2"}, json.loads(response.body))
        self.mock_actions[4].assert_called_once_with("example", "example-2")

        response = self._post("/reset", headers)
        self.assertEqual({"reset": True}, json.loads(response.body))
        self.mock_actions[5].assert_called_once_with()
        self.mock_actions[6].assert_called_once_with({"reset": True}, "unused.json")

        response = self._post("/shutdown", headers)
        self.assertEqual({"showdown": True}, json.loads(response.body))
        self.assertTrue(settings.GLOBAL_SERVER_SHUTDOWN)

    def test_existing_invalid_profile_checks_remain(self):
        headers = self._token_headers()
        for path in (
            "/pause?profile=bad.name", "/resume?profile=bad.name",
            "/stop?profile=bad.name", "/run?profile=bad.name",
            "/run?instance=bad.name", "/reset?profile=example",
        ):
            with self.subTest(path=path):
                self.assertEqual(400, self._post(path, headers).code)
                self._assert_no_action()
