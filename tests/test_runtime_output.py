import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class RecordingStream:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class RuntimeOutputTest(unittest.TestCase):
    def test_ctbc_cookie_injection_requires_exact_secure_origin(self):
        from nodriver_tixcraft import _should_inject_ctbc_cookies

        self.assertTrue(_should_inject_ctbc_cookies(
            "https://tix.ctbcsports.com/BROTHERS/"
        ))
        rejected_urls = (
            "https://tix.wdragons.com/?next=tix.ctbcsports.com",
            "https://tix.wdragons.com/tix.ctbcsports.com",
            "http://tix.ctbcsports.com/BROTHERS/",
            "https://@tix.ctbcsports.com/BROTHERS/",
            "https://tix.ctbcsports.com:444/BROTHERS/",
        )
        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertFalse(_should_inject_ctbc_cookies(url))

    def test_windows_configuration_reuses_existing_streams(self):
        from nodriver_tixcraft import _configure_windows_utf8_output

        stdout = RecordingStream()
        stderr = RecordingStream()
        with patch.object(sys, "platform", "win32"), patch.object(
            sys, "stdout", stdout
        ), patch.object(sys, "stderr", stderr):
            _configure_windows_utf8_output()
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)

        expected = {
            "encoding": "utf-8",
            "errors": "replace",
            "line_buffering": True,
        }
        self.assertEqual([expected], stdout.calls)
        self.assertEqual([expected], stderr.calls)

    def test_windows_configuration_keeps_unsupported_streams(self):
        from nodriver_tixcraft import _configure_windows_utf8_output

        unsupported = object()
        rejected = RecordingStream(ValueError("not configurable"))
        with patch.object(sys, "platform", "win32"), patch.object(
            sys, "stdout", unsupported
        ), patch.object(sys, "stderr", rejected):
            _configure_windows_utf8_output()
            self.assertIs(sys.stdout, unsupported)
            self.assertIs(sys.stderr, rejected)

    @unittest.skipUnless(sys.platform == "win32", "Windows import contract")
    def test_import_does_not_replace_standard_streams(self):
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        code = (
            "import sys; "
            "stdout = sys.stdout; stderr = sys.stderr; "
            "import nodriver_tixcraft; "
            "assert sys.stdout is stdout; assert sys.stderr is stderr; "
            "print('stream-ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("stream-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
