import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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
    def test_ocr_policy_uses_homepage_host_and_explicit_tenant_capability(self):
        from nodriver_tixcraft import _ocr_model_for_homepage

        self.assertEqual(
            "official", _ocr_model_for_homepage("https://tix.ctbcsports.com/BROTHERS/")
        )
        self.assertEqual(
            "official", _ocr_model_for_homepage("https://tix.wdragons.com/UTK0201_")
        )
        self.assertEqual(
            "official",
            _ocr_model_for_homepage(
                "https://tix.wdragons.com/UTK0201_?next=https://tixcraft.com/"
            ),
        )
        self.assertEqual(
            "tixcraft_tm",
            _ocr_model_for_homepage("https://tixcraft.com/?ref=tix.wdragons.com"),
        )
        self.assertEqual(
            "universal",
            _ocr_model_for_homepage("https://tix.wdragons.com.evil.test/"),
        )
        self.assertEqual(
            "universal", _ocr_model_for_homepage("http://tix.wdragons.com/")
        )
        self.assertEqual(
            "universal", _ocr_model_for_homepage("https://ticketmaster.sg.evil.test/")
        )
        self.assertEqual(
            "tixcraft_tm", _ocr_model_for_homepage("https://www.ticketmaster.sg/")
        )
        self.assertEqual(
            "tixcraft_tm", _ocr_model_for_homepage("https://ticketmaster.com/")
        )

    def test_utiki_cookie_login_requires_matching_exact_secure_tenant(self):
        from nodriver_tixcraft import _utiki_cookie_login_profile

        ctbc = "https://tix.ctbcsports.com/BROTHERS/"
        dragons = "https://tix.wdragons.com/UTK0201_"
        self.assertEqual(
            "ctbc_brothers", _utiki_cookie_login_profile(ctbc, ctbc).tenant_key
        )
        self.assertEqual(
            "wei_chuan_dragons",
            _utiki_cookie_login_profile(dragons, dragons).tenant_key,
        )
        rejected_urls = (
            "http://tix.ctbcsports.com/BROTHERS/",
            "https://@tix.ctbcsports.com/BROTHERS/",
            "https://tix.ctbcsports.com:444/BROTHERS/",
            "https://tix.ctbcsports.com.evil.test/BROTHERS/",
            "http://tix.wdragons.com/UTK0201_",
            "https://tix.wdragons.com:444/UTK0201_",
            "https://tix.wdragons.com.evil.test/UTK0201_",
        )
        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertIsNone(_utiki_cookie_login_profile(url, url))
        self.assertIsNone(_utiki_cookie_login_profile(ctbc, dragons))
        self.assertIsNone(_utiki_cookie_login_profile(dragons, ctbc))


class UtikiCookieStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_utiki_homepage_query_does_not_run_other_platform_startup(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        config = get_default_config()
        homepage = (
            "https://tix.wdragons.com/UTK0201_?next=https://tixcraft.com/"
            "&other=ibon.com&redirect=kktix.com"
        )
        config["homepage"] = homepage
        config["accounts"]["tixcraft_sid"] = "synthetic-sid"
        tab = SimpleNamespace(target=SimpleNamespace(url=homepage))
        driver = SimpleNamespace(get=AsyncMock(return_value=tab))
        with patch(
            "nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock
        ), patch(
            "nodriver_tixcraft.nodriver_utiki_inject_cookies",
            new=AsyncMock(return_value=False),
        ), patch(
            "nodriver_tixcraft.register_ibon_alert_handler",
            new_callable=AsyncMock,
        ) as ibon_alert, patch(
            "nodriver_tixcraft.util.create_debug_logger",
            return_value=SimpleNamespace(log=Mock()),
        ):
            self.assertIs(tab, await nodriver_goto_homepage(driver, config))

        driver.get.assert_awaited_once_with(homepage)
        ibon_alert.assert_not_awaited()

    async def test_homepage_flow_injects_and_reloads_only_for_its_tenant(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        cases = (
            ("https://tix.ctbcsports.com/BROTHERS/", "apply CTBC cookie session"),
            ("https://tix.wdragons.com/", "apply WDragons cookie session"),
        )
        for homepage, reason in cases:
            with self.subTest(homepage=homepage):
                config = get_default_config()
                config["homepage"] = homepage
                tab = SimpleNamespace(
                    target=SimpleNamespace(url=homepage),
                    evaluate=AsyncMock(return_value="1"),
                )
                driver = SimpleNamespace(get=AsyncMock(return_value=tab))
                inject = AsyncMock(return_value=True)
                reload_wait = AsyncMock(return_value=True)
                debug = SimpleNamespace(log=Mock())
                with patch("nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock), patch(
                    "nodriver_tixcraft.nodriver_utiki_inject_cookies", inject
                ), patch(
                    "nodriver_tixcraft.nodriver_utk_sports_reload_wait", reload_wait
                ), patch("nodriver_tixcraft.util.create_debug_logger", return_value=debug):
                    self.assertIs(tab, await nodriver_goto_homepage(driver, config))
                inject.assert_awaited_once_with(tab, config)
                reload_wait.assert_awaited_once_with(
                    tab,
                    config,
                    reason,
                    apply_local_interval=False,
                    allow_unknown_document=True,
                )
                tab.evaluate.assert_awaited_once()

    async def test_homepage_flow_does_not_inject_after_cross_tenant_redirect(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        config = get_default_config()
        config["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
        tab = SimpleNamespace(target=SimpleNamespace(url="https://tix.wdragons.com/"))
        driver = SimpleNamespace(get=AsyncMock(return_value=tab))
        inject = AsyncMock(return_value=True)
        with patch("nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock), patch(
            "nodriver_tixcraft.nodriver_utiki_inject_cookies", inject
        ), patch("nodriver_tixcraft.util.create_debug_logger", return_value=SimpleNamespace(log=Mock())):
            self.assertIs(tab, await nodriver_goto_homepage(driver, config))
        inject.assert_not_awaited()

    async def test_dragons_cookie_absent_does_not_reload_or_retry(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        config = get_default_config()
        config["homepage"] = "https://tix.wdragons.com/"
        tab = SimpleNamespace(target=SimpleNamespace(url=config["homepage"]))
        driver = SimpleNamespace(get=AsyncMock(return_value=tab))
        inject = AsyncMock(return_value=False)
        reload_wait = AsyncMock(return_value=True)
        with patch("nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock), patch(
            "nodriver_tixcraft.nodriver_utiki_inject_cookies", inject
        ), patch(
            "nodriver_tixcraft.nodriver_utk_sports_reload_wait", reload_wait
        ), patch("nodriver_tixcraft.util.create_debug_logger", return_value=SimpleNamespace(log=Mock())):
            self.assertIs(tab, await nodriver_goto_homepage(driver, config))
        inject.assert_awaited_once_with(tab, config)
        reload_wait.assert_not_awaited()

    async def test_cookie_reload_error_does_not_log_sensitive_exception_text(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        config = get_default_config()
        config["homepage"] = "https://tix.wdragons.com/"
        tab = SimpleNamespace(target=SimpleNamespace(url=config["homepage"]))
        driver = SimpleNamespace(get=AsyncMock(return_value=tab))
        logger = SimpleNamespace(log=Mock())
        with patch("nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock), patch(
            "nodriver_tixcraft.nodriver_utiki_inject_cookies",
            new=AsyncMock(return_value=True),
        ), patch(
            "nodriver_tixcraft.nodriver_utk_sports_reload_wait",
            new=AsyncMock(side_effect=RuntimeError("synthetic-secret-value")),
        ), patch("nodriver_tixcraft.util.create_debug_logger", return_value=logger):
            self.assertIs(tab, await nodriver_goto_homepage(driver, config))
        self.assertNotIn("synthetic-secret-value", str(logger.log.call_args_list))

    async def test_dragons_unverified_login_does_not_retry(self):
        from nodriver_tixcraft import nodriver_goto_homepage
        from settings import get_default_config

        config = get_default_config()
        config["homepage"] = "https://tix.wdragons.com/"
        tab = SimpleNamespace(
            target=SimpleNamespace(url=config["homepage"]),
            evaluate=AsyncMock(return_value="0"),
        )
        driver = SimpleNamespace(get=AsyncMock(return_value=tab))
        inject = AsyncMock(return_value=True)
        reload_wait = AsyncMock(return_value=True)
        logger = SimpleNamespace(log=Mock())
        with patch("nodriver_tixcraft.asyncio.sleep", new_callable=AsyncMock), patch(
            "nodriver_tixcraft.nodriver_utiki_inject_cookies", inject
        ), patch(
            "nodriver_tixcraft.nodriver_utk_sports_reload_wait", reload_wait
        ), patch("nodriver_tixcraft.util.create_debug_logger", return_value=logger):
            self.assertIs(tab, await nodriver_goto_homepage(driver, config))
        inject.assert_awaited_once_with(tab, config)
        reload_wait.assert_awaited_once()
        tab.evaluate.assert_awaited_once()
        self.assertIn("session may be expired", str(logger.log.call_args_list))

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
