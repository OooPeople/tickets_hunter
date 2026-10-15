import base64
import json
import time
import types
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from tests.ctbc_support import (
    CTBC_TEST_AREA_URL,
    CtbcStateIsolationMixin,
    FakeElement,
    FakeSubmitTab,
    FakeTab,
    config,
    make_ctbc_area_snapshot,
)

from platforms.utiki.dom import SubmitDispatchResult
from platforms.utiki.selection import QuantityPlan

from platforms.utiki.workflow import (
    CTBC_AREA_ROW_SELECTOR,
    CTBC_DATE_PURCHASE_CONTROL_SELECTOR,
    CTBC_DATE_ROW_SELECTOR,
    _ctbc_attest_fast_destination,
    _ctbc_captcha_and_submit,
    _ctbc_fast_marker_matches_pending,
    _fill_ctbc_inline_login,
    _ctbc_pending_from_fast_marker,
    _ctbc_purchase_state_key,
    _ctbc_reconcile_area_navigation,
    _ctbc_selection_config_fingerprint,
    _ctbc_tab_identity,
    _ctbc_tab_state_key,
    _get_ctbc_captcha_image_bytes,
    _handle_ctbc_submit_dialog,
    _recover_ctbc_seat_conflict,
    _state,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_general_admission_main,
    nodriver_ctbc_inject_cookies,
    nodriver_ctbc_seat_main,
    nodriver_ctbc_ticket_type_auto_select,
)


class CtbcDomFlowTest(CtbcStateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    ctbc_state = _state

    def _quantity_plan(self, page_url):
        query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
        return QuantityPlan(
            page_url=page_url,
            document_id="fixture-document",
            performance_id=query["PERFORMANCE_ID"][0],
            group_id=query["GROUP_ID"][0],
            price_area_id=query["PERFORMANCE_PRICE_AREA_ID"][0],
            quantity_limit=4,
            target_key="FULL",
            target_name="全票",
            ticket_number=2,
        )

    def _track_submit_state(self, tab):
        submit_key = _ctbc_purchase_state_key(tab)
        for state_name in (
            "submitted",
            "seat_conflict_pending",
            "submit_blocked",
            "auth_failure",
            "captcha_reset",
        ):
            self.addCleanup(_state[state_name].pop, submit_key, None)
        return submit_key

    async def test_inline_login_never_reuses_ctbc_credentials_for_dragons(self):
        class LoginTab:
            def __init__(self, url, navigate_before_commit=False):
                self.target = types.SimpleNamespace(url=url)
                self.scripts = []
                self.navigate_before_commit = navigate_before_commit
                self.credentials_written = False

            async def evaluate(self, script):
                self.scripts.append(script)
                if len(self.scripts) == 1:
                    return {"required": True, "account": "", "password": ""}
                if self.navigate_before_commit:
                    self.target.url = (
                        "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1"
                    )
                has_atomic_origin_gate = (
                    'const expectedOrigin = "https://tix.ctbcsports.com";' in script
                    and "const currentUrl = new URL(location.href);" in script
                    and "currentUrl.origin !== expectedOrigin" in script
                    and "currentUrl.username" in script
                    and "currentUrl.password" in script
                )
                if (
                    has_atomic_origin_gate
                    and not self.target.url.startswith(
                        "https://tix.ctbcsports.com/BROTHERS/"
                    )
                ):
                    return False
                self.credentials_written = True
                return True

        cfg = config()
        cfg["accounts"]["ctbc_account"] = "ctbc-test-account"
        cfg["accounts"]["ctbc_password"] = "ctbc-test-password"
        dragons = LoginTab(
            "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1"
        )
        ctbc = LoginTab(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1"
        )

        self.assertFalse(await _fill_ctbc_inline_login(dragons, cfg))
        self.assertEqual(1, len(dragons.scripts))
        self.assertNotIn("ctbc-test-account", dragons.scripts[0])
        self.assertNotIn("ctbc-test-password", dragons.scripts[0])

        self.assertTrue(await _fill_ctbc_inline_login(ctbc, cfg))
        self.assertEqual(2, len(ctbc.scripts))
        self.assertIn("ctbc-test-account", ctbc.scripts[1])
        self.assertIn("ctbc-test-password", ctbc.scripts[1])
        self.assertTrue(ctbc.credentials_written)

        raced = LoginTab(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1",
            navigate_before_commit=True,
        )
        self.assertFalse(await _fill_ctbc_inline_login(raced, cfg))
        self.assertFalse(raced.credentials_written)

    async def test_cookie_injection_rechecks_the_exact_tenant(self):
        get_all = AsyncMock(return_value=[])
        send = AsyncMock(return_value=True)
        tab = types.SimpleNamespace(
            target=types.SimpleNamespace(
                url="https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
            ),
            browser=types.SimpleNamespace(
                cookies=types.SimpleNamespace(get_all=get_all)
            ),
            send=send,
        )
        cfg = config()
        cfg["accounts"]["ctbc_cookie"] = "CTBC_IMPORTED|BROTHERS|_1=session"

        self.assertFalse(await nodriver_ctbc_inject_cookies(tab, cfg))
        get_all.assert_not_awaited()
        send.assert_not_awaited()

    async def test_cookie_cleanup_requires_ctbc_dns_label_boundary(self):
        cookies = [
            types.SimpleNamespace(
                name="CTBC_SESSION", domain=".ctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_SUBDOMAIN", domain="login.ctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_EVIL", domain="evilctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_SUFFIX", domain="ctbcsports.com.evil.test", path="/"
            ),
        ]
        tab = types.SimpleNamespace(
            target=types.SimpleNamespace(
                url="https://tix.ctbcsports.com/BROTHERS/"
            ),
            browser=types.SimpleNamespace(
                cookies=types.SimpleNamespace(
                    get_all=AsyncMock(return_value=cookies)
                )
            ),
            send=AsyncMock(return_value=True),
        )
        cfg = config()
        cfg["accounts"]["ctbc_cookie"] = "CTBC_IMPORTED|BROTHERS|_1=session"

        with patch(
            "platforms.utiki.workflow.cdp.network.delete_cookies",
            side_effect=lambda **kwargs: ("delete", kwargs),
        ), patch(
            "platforms.utiki.workflow.cdp.network.set_cookie",
            side_effect=lambda **kwargs: ("set", kwargs),
        ):
            self.assertTrue(await nodriver_ctbc_inject_cookies(tab, cfg))

        commands = [call.args[0] for call in tab.send.await_args_list]
        deleted_domains = [
            command[1]["domain"] for command in commands if command[0] == "delete"
        ]
        self.assertEqual(
            [".ctbcsports.com", "login.ctbcsports.com"], deleted_domains
        )
        set_commands = [command for command in commands if command[0] == "set"]
        self.assertEqual(1, len(set_commands))
        self.assertEqual("tix.ctbcsports.com", set_commands[0][1]["domain"])

    async def test_explicit_ticket_type_keyword_overrides_global_exclusions(self):
        full_counter = FakeElement(attrs={"id": "FULL", "val": "內野全票-NT$800"})
        disability_counter = FakeElement(
            attrs={"id": "DISABILITY", "val": "身心障礙票(及陪同票)-NT$400"}
        )
        full_button = FakeElement(
            "<button>內野全票-NT$800<div class='checkNum'>0</div></button>",
            children={".checkNum": full_counter},
        )
        disability_button = FakeElement(
            "<button>身心障礙票(及陪同票)-NT$400<div class='checkNum'>0</div></button>",
            children={".checkNum": disability_counter},
        )
        selector = '.ticket button[onclick*="setType"]'
        tab = FakeTab(
            selector,
            [full_button, disability_button],
            url="https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1",
        )
        cfg = config()
        cfg["utiki"]["ticket_type_keyword"] = "身心"
        cfg["keyword_exclude"] = '"輪椅","身障","身心","障礙"'

        state_key = _ctbc_tab_state_key(tab)
        try:
            self.assertTrue(await nodriver_ctbc_ticket_type_auto_select(tab, cfg))
            self.assertFalse(full_button.clicked)
            self.assertTrue(disability_button.clicked)
            self.assertEqual(
                {"id": "DISABILITY", "name": "身心障礙票(及陪同票)-NT$400"},
                _state["ticket_type"][state_key],
            )
        finally:
            _state["ticket_type"].pop(state_key, None)

    async def test_manual_reserved_flow_revalidates_and_submits_once(self):
        tab = FakeSubmitTab()
        events = []
        audits = iter((False, True))

        async def record(name, result=True):
            events.append(name)
            return result

        def record_effect(name):
            async def effect(*_args, **_kwargs):
                return await record(name)
            return effect

        async def audit(*_args, **_kwargs):
            result = next(audits)
            events.append("audit")
            return result

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(side_effect=record_effect("attest")),
        ), patch(
            "platforms.utiki.workflow.nodriver_ctbc_ticket_type_auto_select",
            AsyncMock(side_effect=record_effect("ticket_type")),
        ), patch(
            "platforms.utiki.workflow._validate_ctbc_seat_selection",
            AsyncMock(side_effect=audit),
        ), patch(
            "platforms.utiki.workflow.nodriver_ctbc_seat_auto_select",
            AsyncMock(side_effect=record_effect("seat")),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
            AsyncMock(side_effect=record_effect("cancel")),
        ), patch(
            "platforms.utiki.workflow._ctbc_captcha_and_submit",
            AsyncMock(side_effect=record_effect("submit")),
        ) as submit:
            self.assertTrue(await nodriver_ctbc_seat_main(tab, config(), ocr=None))

        self.assertEqual(
            ["attest", "ticket_type", "audit", "seat", "cancel", "audit", "submit"],
            events,
        )
        submit.assert_awaited_once()

    async def test_captcha_capture_prefers_native_canvas_bytes(self):
        expected = b"native-png"
        data_url = "data:image/png;base64," + base64.b64encode(expected).decode("ascii")
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=data_url))
        with patch(
            "platforms.utiki.workflow.nodriver_get_captcha_image_from_dom_snapshot",
            new=AsyncMock(return_value=b"fallback"),
        ) as fallback:
            self.assertEqual(expected, await _get_ctbc_captcha_image_bytes(tab, config()))
            fallback.assert_not_awaited()

    async def test_captcha_capture_falls_back_when_canvas_is_unavailable(self):
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=""))
        with patch(
            "platforms.utiki.workflow.nodriver_get_captcha_image_from_dom_snapshot",
            new=AsyncMock(return_value=b"fallback"),
        ) as fallback:
            self.assertEqual(b"fallback", await _get_ctbc_captcha_image_bytes(tab, config()))
            fallback.assert_awaited_once_with(tab, config(), preferred_image_id="chk_pic")

    async def test_date_uses_visible_main_table_and_skips_disabled_sold_out(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        available_button = FakeElement(
            "<button id='buy_btn'>購買</button>", attrs={"onclick": onclick}
        )
        sold_button = FakeElement(
            "<button id='buy_btn' disabled>已售完</button>",
            disabled=True,
            attrs={"onclick": onclick},
        )
        available_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": available_button},
        )
        sold_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>家庭席</td><td><button disabled>已售完</button></td></tr>",
            children={"button#buy_btn": sold_button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [available_row, sold_row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertEqual(CTBC_DATE_ROW_SELECTOR, tab.last_selector)
        self.assertTrue(available_button.clicked)
        self.assertFalse(sold_button.clicked)

    async def test_date_fast_path_accepts_available_area_status_skew(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>立即選位》</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>立即選位》</button></td></tr>",
            children={"button#buy_btn": button},
        )
        snapshot = make_ctbc_area_snapshot(include_back=False)
        snapshot.update({
            "elapsed_ms": 120,
            "map_url": "https://imgs2.utiki.com.tw/Data/CTBC_SPORTS/Images/Temp/P16D0B8Z/1_P16D0B8Z_live.map?v=12345678",
        })
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            evaluate_results=[json.dumps(snapshot), True],
        )
        try:
            self.assertTrue(await nodriver_ctbc_date_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))
            self.assertFalse(button.clicked)
            self.assertEqual(2, tab.evaluate_count)
            self.assertNotIn("includes('購買')", tab.evaluate_scripts[-1])
            expected = _state["pending_navigation"][_ctbc_tab_identity(tab)]
            self.assertEqual("PRICE_FRONT", expected["price_area_id"])
        finally:
            _state["pending_navigation"].pop(_ctbc_tab_identity(tab), None)

    async def test_date_fast_path_failure_falls_back_to_the_native_button(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            evaluate_results=[json.dumps({"ok": False, "reason": "special-mode"})],
        )
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertTrue(button.clicked)
        self.assertEqual(1, tab.evaluate_count)

    async def test_computer_mode_skips_date_fast_path(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L"
        )
        onclick = (
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&"
            "PRODUCT_ID=P16ANF0L'"
        )
        button = FakeElement(
            "<button id='buy_btn'>購買</button>", attrs={"onclick": onclick}
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td>"
            "<td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config()
        cfg["utiki"]["seat_selection_mode"] = "computer"
        cfg["ctbc"] = dict(cfg["utiki"])

        with (
            patch(
                "platforms.utiki.workflow._get_utk_sports_profile",
                return_value={
                    "name": "CTBC Sports",
                    "host": "tix.ctbcsports.com",
                    "tenant_path": "/BROTHERS",
                    "map_namespace": "CTBC_SPORTS",
                    "fast_area_prefetch": True,
                    "supports_server_assignment": True,
                },
            ),
            patch(
                "platforms.utiki.workflow._ctbc_try_fast_area_navigation",
                AsyncMock(),
            ) as fast_path,
        ):
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        fast_path.assert_not_awaited()
        self.assertTrue(button.clicked)

    async def test_computer_mode_without_capability_does_not_select_date(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L"
        )
        onclick = (
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&"
            "PRODUCT_ID=P16ANF0L'"
        )
        button = FakeElement(
            "<button id='buy_btn'>購買</button>", attrs={"onclick": onclick}
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td>"
            "<td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config()
        cfg["utiki"]["seat_selection_mode"] = "computer"
        cfg["ctbc"] = dict(cfg["utiki"])

        with (
            patch(
                "platforms.utiki.workflow._ctbc_try_fast_area_navigation",
                AsyncMock(),
            ) as fast_path,
            patch(
                "platforms.utiki.workflow._ctbc_commit_native_date_action",
                AsyncMock(),
            ) as native_commit,
        ):
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))

        fast_path.assert_not_awaited()
        native_commit.assert_not_awaited()
        self.assertFalse(button.clicked)

    def test_seat_selection_mode_changes_navigation_fingerprint(self):
        manual = config()
        computer = config()
        computer["utiki"]["seat_selection_mode"] = "computer"
        computer["ctbc"] = dict(computer["utiki"])

        self.assertNotEqual(
            _ctbc_selection_config_fingerprint(manual),
            _ctbc_selection_config_fingerprint(computer),
        )

    async def test_fast_navigation_installs_pending_before_dispatch_exception(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        observed = []
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="throw_after_dispatch",
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
            },
            on_fast_commit=lambda: observed.append(
                _state["pending_navigation"][_ctbc_tab_identity(tab)]["kind"]
            ),
        )

        cfg = config(area_keyword='"109區前排"')
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertEqual(["FAST"], observed)
        self.assertFalse(button.clicked)
        self.assertEqual(destination, tab.target.url)
        self.assertIn(_ctbc_tab_identity(tab), _state["pending_navigation"])
        commit_script = next(
            script for script in tab.evaluate_scripts
            if "__ticketsHunterCtbcFastCommit" in script
        )
        self.assertLess(
            commit_script.index("sessionStorage.setItem"),
            commit_script.index("location.assign(destinationUrl)"),
        )
        self.assertIn("stored.nonce !== navigationNonce", commit_script)
        self.assertNotIn("setTimeout", commit_script)

        self.assertTrue(await _ctbc_attest_fast_destination(tab, "seat", cfg))
        self.assertNotIn(_ctbc_tab_identity(tab), _state["pending_navigation"])
        self.assertIsNone(tab.session_marker)

    async def test_rejected_fast_commit_compare_pops_pending_before_native_fallback(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="rejected",
            marker_delete_allowed=False,
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertTrue(button.clicked)
        self.assertNotIn(_ctbc_tab_identity(tab), _state["pending_navigation"])
        self.assertIsNone(tab.session_marker)

    async def test_rejected_old_nonce_does_not_clear_newer_pending(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        replacement = {"kind": "FAST", "nonce": "9" * 32}
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="rejected",
            on_fast_commit=lambda: _state["pending_navigation"].__setitem__(
                _ctbc_tab_identity(tab), replacement
            ),
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertIs(replacement, _state["pending_navigation"][_ctbc_tab_identity(tab)])
        self.assertFalse(button.clicked)

    async def test_date_commit_guard_recovers_only_after_reload_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="pending",
        )
        identity = _ctbc_tab_identity(tab)
        _state["last_document"][identity] = "document-1"
        cfg = config(area_keyword='"109區前排"')

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertIn(identity, _state["pending_navigation"])
        self.assertFalse(button.clicked)
        _state["pending_navigation"][identity]["created_at"] -= 30

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(side_effect=[False, True]),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertNotIn(
                "reload_requested_at", _state["pending_navigation"][identity]
            )
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            pending = _state["pending_navigation"][identity]
            self.assertEqual(2, reload_page.await_count)
            self.assertIn("reload_requested_at", pending)

            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertEqual(2, reload_page.await_count)

            _state["last_document"][identity] = "document-2"
            cfg["area_auto_select"]["enable"] = False
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        self.assertTrue(button.clicked)
        self.assertNotIn(identity, _state["pending_navigation"])

    async def test_storage_unavailable_clean_date_uses_native_fallback(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        button = FakeElement("<button id='buy_btn'>購買</button>")
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, storage_available=False,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertTrue(button.clicked)
        self.assertNotIn(_ctbc_tab_identity(tab), _state["pending_navigation"])

    async def test_storage_unavailable_native_attests_but_fast_returns(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        expected = {
            "phase": "DISPATCH_UNKNOWN", "nonce": "4" * 32,
            "source_url": source, "area_url": CTBC_TEST_AREA_URL,
            "destination_url": destination, "route": "0205",
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
            "created_at": time.monotonic(),
        }
        dom_ids = {
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
        }
        native = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            storage_available=False, dom_ids=dom_ids,
        )
        native_pending = {**expected, "kind": "NATIVE"}
        native_identity = _ctbc_tab_identity(native)
        _state["pending_navigation"][native_identity] = native_pending
        self.assertTrue(await _ctbc_attest_fast_destination(
            native, "seat", config()
        ))
        self.assertNotIn(native_identity, _state["pending_navigation"])

        fast = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            storage_available=False, dom_ids=dom_ids,
        )
        fast_pending = {**expected, "kind": "FAST", "nonce": "5" * 32}
        fast_identity = _ctbc_tab_identity(fast)
        _state["pending_navigation"][fast_identity] = fast_pending
        self.assertFalse(await _ctbc_attest_fast_destination(
            fast, "seat", config()
        ))
        self.assertEqual(CTBC_TEST_AREA_URL, fast.target.url)
        self.assertEqual("RETURNING", fast_pending["phase"])
        return_script = next(
            script for script in fast.evaluate_scripts
            if "__ticketsHunterCtbcReturn" in script
        )
        self.assertIn("location.href !== sourceUrl", return_script)

    async def test_async_old_navigation_observation_cannot_replace_new_nonce(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        replacement = {
            "kind": "NATIVE", "phase": "DISPATCH_UNKNOWN",
            "nonce": "6" * 32, "area_url": CTBC_TEST_AREA_URL,
        }
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1, "nonce": "7" * 32, "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )
        identity = _ctbc_tab_identity(tab)
        tab.on_marker_read = lambda: _state["pending_navigation"].__setitem__(
            identity, replacement
        )

        self.assertFalse(await _ctbc_attest_fast_destination(
            tab, "seat", config()
        ))
        self.assertIs(replacement, _state["pending_navigation"][identity])
        self.assertEqual(destination, tab.target.url)

        other_destination = destination.replace("GROUP_FRONT", "GROUP_OTHER")
        native = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            dom_ids={
                "performance_id": "WRONG", "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
            },
        )
        native_identity = _ctbc_tab_identity(native)
        old_pending = {
            "kind": "NATIVE", "phase": "DISPATCH_UNKNOWN",
            "nonce": "a" * 32, "area_url": CTBC_TEST_AREA_URL,
            "destination_url": destination, "route": "0205",
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
        }
        new_pending = {**old_pending, "nonce": "b" * 32}
        _state["pending_navigation"][native_identity] = old_pending

        def replace_during_destination_read():
            _state["pending_navigation"][native_identity] = new_pending
            native.target.url = other_destination

        native.on_destination_state = replace_during_destination_read
        self.assertFalse(await _ctbc_attest_fast_destination(
            native, "seat", config()
        ))
        self.assertIs(new_pending, _state["pending_navigation"][native_identity])
        self.assertEqual(other_destination, native.target.url)

    async def test_browser_marker_blocks_duplicate_fast_dispatch_after_state_loss(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        marker = {
            "version": 1,
            "nonce": "f" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        }
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            marker=marker,
        )

        cfg = config(area_keyword='"109區前排"')
        cfg["area_auto_select"]["enable"] = False
        identity = _ctbc_tab_identity(tab)
        _state["last_document"][identity] = "document-1"

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertEqual(source, tab.target.url)
        self.assertEqual(marker, tab.session_marker)
        self.assertFalse(button.clicked)
        self.assertIn(identity, _state["pending_navigation"])
        self.assertFalse(any("fetch(areaUrl" in script for script in tab.evaluate_scripts))

        tab.session_marker["expires_at"] = time.time() * 1000 - 1
        _state["pending_navigation"][identity]["created_at"] -= 30
        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            reload_page.assert_awaited_once()

            _state["last_document"][identity] = "document-2"
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        self.assertTrue(button.clicked)
        self.assertIsNone(tab.session_marker)
        self.assertNotIn(identity, _state["pending_navigation"])

    async def test_marker_only_fast_destination_returns_then_allows_native_retry(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        area_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "a" * 32,
                "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )
        identity = _ctbc_tab_identity(tab)
        _state["last_document"][identity] = "destination-document"

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        pending = _state["pending_navigation"][identity]
        self.assertEqual(("FAST", "RETURNING"), (pending["kind"], pending["phase"]))
        self.assertEqual(area_url, tab.target.url)
        self.assertIsNotNone(tab.session_marker)

        _state["last_document"][identity] = "area-document"
        self.assertTrue(await _ctbc_reconcile_area_navigation(tab, area_url))
        self.assertNotIn(identity, _state["pending_navigation"])
        self.assertIsNone(tab.session_marker)

    async def test_active_fast_marker_blocks_native_area_snapshot(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR,
            [],
            url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            native_clears_marker=False,
            native_destination=destination,
            marker={
                "version": 1,
                "nonce": "b" * 32,
                "source": "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L",
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
            },
        )

        self.assertFalse(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertFalse(any(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))
        self.assertFalse(any(
            "new MutationObserver(collect)" in script
            for script in tab.evaluate_scripts
        ))
        self.assertIsNotNone(tab.session_marker)
        pending = _state["pending_navigation"][_ctbc_tab_identity(tab)]
        self.assertEqual("FAST", pending["kind"])

    def test_fast_marker_requires_exact_pending_identity(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        marker_pending = _ctbc_pending_from_fast_marker({
            "version": 1,
            "nonce": "1" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        })
        pending = dict(marker_pending)
        self.assertTrue(_ctbc_fast_marker_matches_pending(marker_pending, pending))

        changes = {
            "source_url": source + "&TRACKING=1",
            "destination_url": destination.replace("GROUP_FRONT", "GROUP_OTHER"),
            "route": "0202",
            "performance_id": "OTHER_PERFORMANCE",
            "product_id": "OTHER_PRODUCT",
            "group_id": "OTHER_GROUP",
            "price_area_id": "OTHER_PRICE",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                changed = dict(pending)
                changed[key] = value
                self.assertFalse(
                    _ctbc_fast_marker_matches_pending(marker_pending, changed)
                )

    async def test_malformed_marker_clears_once_and_preserves_racing_replacement(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        button = FakeElement("<button id='buy_btn'>購買</button>")
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        cfg = config()
        cfg["area_auto_select"]["enable"] = False
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, marker_raw="{broken-json",
        )

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertIsNone(tab._marker_raw())
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertTrue(button.clicked)

        replacement = {
            "version": 1,
            "nonce": "2" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        }
        racing = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, marker_raw="{old-broken-json",
        )

        def replace_marker():
            racing.session_marker_raw = None
            racing.session_marker = replacement

        racing.on_raw_marker_delete = replace_marker
        self.assertFalse(await nodriver_ctbc_date_auto_select(racing, cfg))
        self.assertEqual(replacement, racing.session_marker)

        far_future = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            marker={**replacement, "expires_at": time.time() * 1000 + 60000},
        )
        self.assertFalse(await nodriver_ctbc_date_auto_select(far_future, cfg))
        self.assertIsNone(far_future.session_marker)

    async def test_malformed_destination_marker_fails_one_tick_then_recovers(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker_raw="not-json",
        )

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        self.assertIsNone(tab._marker_raw())
        self.assertTrue(await _ctbc_attest_fast_destination(tab, "seat", config()))

    async def test_native_exception_reloads_before_same_area_retry(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        snapshot = json.dumps(make_ctbc_area_snapshot(include_back=False))
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR, [], url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[snapshot],
            native_commit_outcome="throw_after_dispatch",
        )
        identity = _ctbc_tab_identity(tab)
        _state["last_document"][identity] = "document-1"
        cfg = config(area_keyword='"109區前排"')

        self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
        pending = _state["pending_navigation"][identity]
        self.assertEqual("NATIVE", pending["kind"])
        self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
        self.assertEqual(1, sum(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))

        pending["created_at"] -= 30
        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
            self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
            reload_page.assert_awaited_once()

            _state["last_document"][identity] = "document-2"
            tab.native_commit_outcome = True
            tab.native_destination = destination
            tab.evaluate_results.append(snapshot)
            self.assertTrue(await nodriver_ctbc_area_auto_select(tab, cfg))

        self.assertEqual(2, sum(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))

    async def test_native_exception_after_navigation_is_consumed_at_destination(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR, [], url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            native_commit_outcome="throw_after_dispatch",
            native_clears_marker=False,
            native_destination=destination,
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
            },
            on_native_commit=lambda: setattr(tab, "session_marker", {
                "version": 1,
                "nonce": "8" * 32,
                "source": (
                    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
                    "PRODUCT_ID=P16ANF0L"
                ),
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            }),
        )

        self.assertFalse(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        identity = _ctbc_tab_identity(tab)
        self.assertIn(identity, _state["pending_navigation"])
        self.assertIsNotNone(tab.session_marker)
        self.assertTrue(await _ctbc_attest_fast_destination(
            tab, "seat", config(area_keyword='"109區前排"')
        ))
        self.assertNotIn(identity, _state["pending_navigation"])
        self.assertIsNone(tab.session_marker)

    async def test_expired_exact_marker_fails_closed_and_manual_arrival_passes(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "c" * 32,
                "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 - 1,
            },
        )
        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))

        manual = FakeTab("", [], url=destination, semantic_navigation=True)
        self.assertTrue(await _ctbc_attest_fast_destination(manual, "seat", config()))

    async def test_marker_destination_mismatch_fails_closed(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        marker_destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        current_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=OTHER&"
            "PERFORMANCE_PRICE_AREA_ID=OTHER_PRICE"
        )
        tab = FakeTab(
            "", [], url=current_url, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "e" * 32,
                "source": source,
                "destination": marker_destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        self.assertEqual(CTBC_TEST_AREA_URL, tab.target.url)

    async def test_vip_presale_uses_native_click_without_fast_prefetch(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0P&STARTDATE=2026/09/18"
        )
        button = FakeElement(
            "<button id='buy_btn'>2026季票<br>優先訂購》</button>",
            attrs={"onclick": "VipSellCheck('P1DN7WJI');"},
        )
        row = FakeElement(
            "<tr><td>2026/09/18(五)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>2026季票 優先訂購》</button></td></tr>",
            children={"button#buy_btn": button},
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/18"')
        ))
        self.assertEqual(1, button.click_count)
        self.assertEqual(0, tab.evaluate_count)

    async def test_unknown_single_handler_uses_scoped_structural_native_fallback(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement(
            "<button>會員立即選位</button>",
            attrs={"onclick": "DragonMemberCheck('P1DRAGON')"},
        )
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>會員立即選位</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertEqual(1, button.click_count)
        self.assertEqual(0, tab.evaluate_count)

    async def test_empty_button_keeps_structural_native_fallback(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement("<button></button>")
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button></button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertEqual(1, button.click_count)

    async def test_anchor_requires_a_same_tenant_same_product_purchase_target(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L&STARTDATE=2026/09/10"
        )
        safe_href = (
            "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
        )
        safe_anchor = FakeElement(
            "<a>立即選位</a>", tag_name="A", attrs={"href": safe_href}
        )
        safe_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>立即選位</a></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [safe_anchor]},
        )
        safe_tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [safe_row], url=source)
        self.assertTrue(await nodriver_ctbc_date_auto_select(safe_tab, config()))
        self.assertTrue(safe_anchor.clicked)

        unsafe_hrefs = (
            "https://example.com/UTK0204_?PERFORMANCE_ID=P1&PRODUCT_ID=P16ANF0L",
            "https://tix.ctbcsports.com:444/BROTHERS/UTK0204_?PERFORMANCE_ID=P1&PRODUCT_ID=P16ANF0L",
            "/BROTHERS/member/account",
            "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=OTHER",
            "/OTHER/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
        )
        for href in unsafe_hrefs:
            with self.subTest(href=href):
                anchor = FakeElement(
                    "<a>其他連結</a>", tag_name="A", attrs={"href": href}
                )
                row = FakeElement(
                    "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
                    "<td><a>其他連結</a></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(tab, config()))
                self.assertFalse(anchor.clicked)

        base_redirected_anchor = FakeElement(
            "<a>看似站內的連結</a>",
            tag_name="A",
            attrs={
                "href": safe_href,
                "resolved_href": (
                    "https://example.com/UTK0204_?"
                    "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
                ),
            },
        )
        base_redirected_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>看似站內的連結</a></td></tr>",
            child_lists={
                CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [base_redirected_anchor]
            },
        )
        base_redirected_tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [base_redirected_row], url=source
        )
        self.assertFalse(
            await nodriver_ctbc_date_auto_select(base_redirected_tab, config())
        )
        self.assertFalse(base_redirected_anchor.clicked)

        for label, attrs in (
            ("onclick", {"href": safe_href, "onclick": "return evil()"}),
            ("target", {"href": safe_href, "target": "_blank"}),
            ("download", {"href": safe_href, "download": "ticket.html"}),
            ("ping", {"href": safe_href, "ping": "https://example.com/ping"}),
        ):
            with self.subTest(anchor_side_effect=label):
                anchor = FakeElement(
                    "<a>立即選位</a>", tag_name="A", attrs=attrs
                )
                row = FakeElement(
                    "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
                    "<td><a>立即選位</a></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
                )
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source), config()
                ))
                self.assertFalse(anchor.clicked)

    async def test_anchor_href_change_after_scan_is_rejected(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L&STARTDATE=2026/09/10"
        )
        anchor = FakeElement(
            "<a>立即選位</a>",
            tag_name="A",
            attrs={
                "href": "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
            },
            commit_attrs={"href": "https://example.com/changed"},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>立即選位</a></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertFalse(anchor.clicked)

    async def test_structural_fallback_rejects_ambiguous_or_unsafe_controls(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        cases = (
            ("hidden", FakeElement(attrs={"onclick": "FutureBuy('P1')"}, visible=False)),
            ("aria-disabled", FakeElement(
                attrs={"onclick": "FutureBuy('P1')", "aria-disabled": "true"}
            )),
            ("assignment", FakeElement(
                attrs={"onclick": "location.href='https://evil.example/'"}
            )),
            ("multiple-statements", FakeElement(
                attrs={"onclick": "FutureBuy('P1');alert('x')"}
            )),
            ("submit", FakeElement(
                attrs={"onclick": "FutureBuy('P1')", "type": "submit"}
            )),
            ("implicit-form-submit", FakeElement(
                attrs={"onclick": "", "has_form": True}
            )),
            ("formaction", FakeElement(
                attrs={
                    "onclick": "FutureBuy('P1')",
                    "type": "button",
                    "formaction": "https://evil.example/",
                }
            )),
        )
        for label, button in cases:
            with self.subTest(label=label):
                row = FakeElement(
                    "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
                    "<td><button>訂購</button></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    tab, config(date_keyword='"9/08"')
                ))
                self.assertFalse(button.clicked)

        first = FakeElement(attrs={"onclick": "FutureBuy('P1')"})
        second = FakeElement(attrs={"onclick": "OtherBuy('P1')"})
        ambiguous_row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>訂購一</button><button>訂購二</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [first, second]},
        )
        ambiguous_tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [ambiguous_row], url=source)
        self.assertFalse(await nodriver_ctbc_date_auto_select(
            ambiguous_tab, config(date_keyword='"9/08"')
        ))
        self.assertFalse(first.clicked)
        self.assertFalse(second.clicked)

    async def test_native_commit_rejects_a_control_that_changed_after_scan(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement(
            "<button>會員立即選位</button>",
            attrs={"onclick": "FutureBuy('P1')"},
            native_commit_allowed=False,
        )
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>會員立即選位</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertFalse(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertFalse(button.clicked)

    async def test_native_commit_revalidates_the_complete_control_fingerprint(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        mutations = (
            ("tag_name", {}, "A"),
            ("href", {"href": "/changed"}, None),
            ("role", {"role": "link"}, None),
            ("type", {"type": "submit"}, None),
            ("target", {"target": "_blank"}, None),
            ("formaction", {"formaction": "/changed"}, None),
            ("onclick", {"onclick": "OtherBuy('P2')"}, None),
        )
        for label, commit_attrs, commit_tag_name in mutations:
            with self.subTest(field=label):
                button = FakeElement(
                    "<button>會員立即選位</button>",
                    attrs={
                        "onclick": "FutureBuy('P1')",
                        "role": "button",
                        "type": "button",
                    },
                    commit_attrs=commit_attrs,
                    commit_tag_name=commit_tag_name,
                )
                row = FakeElement(
                    "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
                    "<td><button>會員立即選位</button></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    tab, config(date_keyword='"9/08"')
                ))
                self.assertFalse(button.clicked)

    async def test_wdragons_logged_in_date_uses_native_notice_preserving_flow(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config(date_keyword='"9/08"', area_keyword='"L2"')

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertTrue(button.clicked)
        self.assertEqual(0, tab.evaluate_count)

    async def test_failed_fast_destination_remains_fail_closed_until_area_page(self):
        seat_url = "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&PERFORMANCE_PRICE_AREA_ID=P16D12YO"
        tab = FakeTab("", [], url=seat_url, evaluate_results=[True, True])
        identity = _ctbc_tab_identity(tab)
        _state["pending_navigation"][identity] = {
            "kind": "FAST", "phase": "RETURNING", "nonce": "d" * 32,
            "route": "0205", "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO",
            "area_url": "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "created_at": time.monotonic(),
        }
        try:
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertIn(identity, _state["pending_navigation"])
        finally:
            _state["pending_navigation"].pop(identity, None)

    async def test_captcha_does_not_submit_when_force_submit_is_off(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": False}
        submit_key = _ctbc_purchase_state_key(tab)
        self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(tab.button.clicked)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertNotIn(submit_key, _state["submit_blocked"])

    async def test_captcha_submits_when_force_submit_is_on(self):
        tab = FakeSubmitTab(dialog="", dialogs=["", "", "成功加入購物車"])
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = _ctbc_purchase_state_key(tab)
        try:
            with patch(
                "platforms.utiki.workflow.dispatch_quantity_submit",
                AsyncMock(),
            ) as quantity_dispatch:
                self.assertTrue(await _ctbc_captcha_and_submit(
                    tab, cfg, ocr=None
                ))
            quantity_dispatch.assert_not_awaited()
            self.assertTrue(tab.button.clicked)
            self.assertEqual(1, tab.button.click_count)
        finally:
            _state["submitted"].pop(submit_key, None)
            _state["submit_blocked"].pop(submit_key, None)

    async def test_delayed_success_does_not_submit_twice(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = _ctbc_purchase_state_key(tab)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIn(submit_key, _state["submitted"])

        tab.dialog = "成功加入購物車"
        tab.dialog_visible = True
        self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("success", _state["submit_blocked"][submit_key])

    async def test_delayed_seat_conflict_closes_and_clears_submit_guard(self):
        conflict_text = "該位置已被選走"
        tab = FakeSubmitTab(dialog="", dialogs=["", "", "", conflict_text])
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIn(submit_key, _state["submitted"])

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page, patch(
            "platforms.utiki.workflow.nodriver_ctbc_ticket_type_auto_select",
            AsyncMock(return_value=True),
        ) as select_type, patch(
            "platforms.utiki.workflow._ctbc_captcha_and_submit",
            AsyncMock(return_value=True),
        ) as submit:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertIn(submit_key, _state["seat_conflict_pending"])
        self.assertEqual("", tab.captcha_input.value)
        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")
        select_type.assert_not_awaited()
        submit.assert_not_awaited()

    async def test_submit_outcome_timeout_never_clicks_twice(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIn(submit_key, _state["submitted"])

        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_cosmetic_purchase_url_changes_never_submit_twice(self):
        cases = []
        for mode, route, group_id in (
            ("reserved", "UTK0205_", "G1"),
            ("general", "UTK0203_", ""),
        ):
            prefix = f"https://tix.ctbcsports.com/BROTHERS/{route}?"
            source_url = (
                f"{prefix}PERFORMANCE_ID=P1&GROUP_ID={group_id}&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            )
            cases.extend((
                (
                    f"{mode}-query-order",
                    source_url,
                    f"{prefix}GROUP_ID={group_id}&PERFORMANCE_PRICE_AREA_ID=A1&"
                    "PERFORMANCE_ID=P1",
                ),
                (f"{mode}-tracking", source_url, f"{source_url}&tracking=fixture"),
                (f"{mode}-fragment", source_url, f"{source_url}#fixture"),
            ))
        cases.append((
            "general-route-alias",
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0202_?"
                "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
            ),
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_PRICE_AREA_ID=A1&GROUP_ID=&PERFORMANCE_ID=P1"
            ),
        ))
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, source_url, cosmetic_url in cases:
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", url=source_url)
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_change_url(target=cosmetic_url):
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = target

                tab.button.click = click_and_change_url
                quantity_plan = (
                    self._quantity_plan(source_url)
                    if label.startswith("general") else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.time.monotonic",
                    return_value=100.0,
                ), patch(
                    "platforms.utiki.workflow.dispatch_quantity_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertFalse(
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            quantity_plan=quantity_plan,
                        )
                    )
                if label.endswith("fragment"):
                    self.assertNotIn(submit_key, _state["submitted"])
                    self.assertEqual(
                        "unknown", _state["submit_blocked"][submit_key]
                    )
                else:
                    self.assertIn(submit_key, _state["submitted"])
                with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
                    self.assertFalse(
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            quantity_plan=quantity_plan,
                        )
                    )

                self.assertEqual(submit_key, _ctbc_purchase_state_key(tab))
                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_submit_navigation_requires_same_tenant_checkout(self):
        reserved_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        general_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
            "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        destinations = (
            (
                "reserved-checkout",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_",
                True,
            ),
            (
                "general-checkout",
                general_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_",
                True,
            ),
            (
                "cross-tenant-checkout",
                reserved_url,
                "https://tix.wdragons.com/UTK0206_",
                False,
            ),
            (
                "same-origin-unknown",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/complete",
                False,
            ),
            (
                "checkout-prefix-only",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_extra",
                False,
            ),
        )

        for label, source_url, destination, expected in destinations:
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", url=source_url)
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_navigate(target=destination):
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = target

                tab.button.click = click_and_navigate
                quantity_plan = (
                    self._quantity_plan(source_url)
                    if label == "general-checkout" else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.time.monotonic",
                    return_value=100.0,
                ), patch(
                    "platforms.utiki.workflow.dispatch_quantity_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertEqual(
                        expected,
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            quantity_plan=quantity_plan,
                        ),
                    )
                self.assertEqual(1, tab.button.click_count)
                if expected:
                    self.assertNotIn(submit_key, _state["submitted"])
                    self.assertEqual(
                        "success", _state["submit_blocked"][submit_key]
                    )
                    tab.target.url = source_url
                    self.assertFalse(
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            quantity_plan=quantity_plan,
                        )
                    )
                    self.assertEqual(1, tab.button.click_count)
                    continue

                self.assertNotIn(submit_key, _state["submitted"])
                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_checkout_only_completes_its_own_tab_submit(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        waiting_tab = FakeSubmitTab(dialog="")
        checkout_tab = FakeSubmitTab(dialog="")
        waiting_key = _ctbc_purchase_state_key(waiting_tab)
        checkout_key = _ctbc_purchase_state_key(checkout_tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(waiting_tab, cfg, ocr=None)
            )

            async def click_and_checkout():
                checkout_tab.button.clicked = True
                checkout_tab.button.click_count += 1
                checkout_tab.target.url = (
                    "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
                )

            checkout_tab.button.click = click_and_checkout
            self.assertTrue(
                await _ctbc_captcha_and_submit(checkout_tab, cfg, ocr=None)
            )

        self.assertIn(waiting_key, _state["submitted"])
        self.assertNotIn(checkout_key, _state["submitted"])
        self.assertEqual("success", _state["submit_blocked"][checkout_key])

    async def test_submit_dialog_after_unverified_navigation_uses_source_purchase(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        tab = FakeSubmitTab(
            dialog="",
            dialogs=["", "", "已超出可購買額度"],
        )
        submit_key = _ctbc_purchase_state_key(tab)

        async def click_and_leave_purchase():
            tab.button.clicked = True
            tab.button.click_count += 1
            tab.target.url = "https://tix.ctbcsports.com/BROTHERS/complete"

        tab.button.click = click_and_leave_purchase
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("quota", _state["submit_blocked"][submit_key])
        self.assertEqual(1, tab.button.click_count)

    async def test_negative_dialog_takes_priority_over_checkout_navigation(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, dialog, state_name, expected in (
            ("quota", "已超出可購買額度", "submit_blocked", "quota"),
            ("seat-conflict", "該位置已被選走", "seat_conflict_pending", None),
        ):
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", dialogs=["", "", dialog])
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_checkout():
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = (
                        "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
                    )

                tab.button.click = click_and_checkout
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )

                self.assertNotIn(submit_key, _state["submitted"])
                self.assertIn(submit_key, _state[state_name])
                if expected is not None:
                    self.assertEqual(expected, _state[state_name][submit_key])
                if state_name == "seat_conflict_pending":
                    self.assertEqual(
                        "seat", _state[state_name][submit_key]["page_type"]
                    )
                self.assertNotEqual(
                    "success", _state["submit_blocked"].get(submit_key)
                )
                self.assertEqual(1, tab.dialog_button.click_count)

    async def test_incomplete_purchase_identity_never_submits(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        urls = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1",
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID="
            ),
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&PERFORMANCE_ID=P2&GROUP_ID=G1&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            ),
        )

        for url in urls:
            with self.subTest(url=url):
                tab = FakeSubmitTab(dialog="", url=url)
                self.assertIsNone(_ctbc_purchase_state_key(tab))
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )
                self.assertEqual(0, tab.button.click_count)
                self.assertNotIn(None, _state["submitted"])

    async def test_submit_blockers_survive_cosmetic_url_variants(self):
        source_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cosmetic_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "tracking=fixture&PERFORMANCE_PRICE_AREA_ID=A1&GROUP_ID=G1&"
            "PERFORMANCE_ID=P1#fixture"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        quota_tab = FakeSubmitTab(dialog="已超出可購買額度", url=source_url)
        quota_key = _ctbc_purchase_state_key(quota_tab)
        self.assertFalse(await _ctbc_captcha_and_submit(quota_tab, cfg, ocr=None))
        quota_tab.dialog = ""
        quota_tab.dialog_visible = False
        quota_tab.target.url = cosmetic_url
        self.assertEqual(quota_key, _ctbc_purchase_state_key(quota_tab))
        self.assertFalse(await _ctbc_captcha_and_submit(quota_tab, cfg, ocr=None))
        self.assertEqual(0, quota_tab.button.click_count)
        self.assertEqual("quota", _state["submit_blocked"][quota_key])

        unknown_tab = FakeSubmitTab(dialog="", url=source_url)
        unknown_key = _ctbc_purchase_state_key(unknown_tab)
        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None)
            )
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None)
            )
        unknown_tab.target.url = cosmetic_url
        self.assertEqual(unknown_key, _ctbc_purchase_state_key(unknown_tab))
        self.assertFalse(await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None))
        self.assertEqual(1, unknown_tab.button.click_count)
        self.assertEqual("unknown", _state["submit_blocked"][unknown_key])

    async def test_submit_state_isolated_by_purchase_identity(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, second_url in (
            (
                "performance",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P2&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "group",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "price-area",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A2",
            ),
            (
                "mode",
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "tenant",
                "https://tix.wdragons.com/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
        ):
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="已超出可購買額度", url=first_url)
                first_key = _ctbc_purchase_state_key(tab)
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )
                tab.dialog = ""
                tab.dialog_visible = False
                tab.target.url = second_url
                second_key = _ctbc_purchase_state_key(tab)
                self.assertNotEqual(first_key, second_key)
                quantity_plan = (
                    self._quantity_plan(second_url)
                    if label == "mode" else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.dispatch_quantity_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertFalse(await _ctbc_captcha_and_submit(
                        tab, cfg, ocr=None, quantity_plan=quantity_plan
                    ))

                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("quota", _state["submit_blocked"][first_key])
                self.assertIn(second_key, _state["submitted"])

    async def test_late_conflict_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "該位置已被選走"
        tab.dialog_visible = True
        self.assertEqual("seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertNotIn(submit_key, _state["submit_blocked"])
        self.assertIn(submit_key, _state["seat_conflict_pending"])

    async def test_late_captcha_error_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "驗證碼錯誤"
        tab.dialog_visible = True
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertNotIn(submit_key, _state["submit_blocked"])
        self.assertEqual("", tab.captcha_input.value)
        self.assertEqual(1, tab.captcha_refresh.click_count)

    async def test_late_auth_error_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "帳號或密碼錯誤"
        tab.dialog_visible = True
        self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertNotIn(submit_key, _state["submit_blocked"])
        self.assertIn(submit_key, _state["auth_failure"])

    async def test_auth_error_without_credentials_blocks_until_new_document(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": False}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"

        with patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(return_value=""),
        ), patch(
            "platforms.utiki.workflow._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ):
            self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, cfg))
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
            self.assertEqual(0, tab.button.click_count)

            _state["last_document"][identity] = "document-2"
            self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertNotIn(submit_key, _state["auth_failure"])
        self.assertEqual(0, tab.button.click_count)

    async def test_auth_fingerprint_failure_keeps_document_blocker(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"
        _state["submitted"][submit_key] = 1.0

        with patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(side_effect=RuntimeError("CDP read failed")),
        ) as fingerprint:
            self.assertEqual(
                "auth", await _handle_ctbc_submit_dialog(tab, cfg)
            )

        fingerprint.assert_awaited_once()
        expected_dialog = fingerprint.await_args.kwargs["expected_dialog"]
        self.assertEqual(str(tab.target.url), expected_dialog.page_url)
        self.assertEqual(tab.document_id, expected_dialog.document_id)
        self.assertEqual("帳號或密碼錯誤", expected_dialog.text)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual({
            "fingerprint": "",
            "source_document_id": "document-1",
        }, _state["auth_failure"][submit_key])

        with patch(
            "platforms.utiki.workflow._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(return_value="current-credentials"),
        ), patch(
            "platforms.utiki.workflow._dispatch_ctbc_submit",
            AsyncMock(),
        ) as dispatch:
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None
            ))
        dispatch.assert_not_awaited()

    async def test_click_exception_after_dispatch_blocks_second_submit(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        async def click_then_raise():
            tab.button.clicked = True
            tab.button.click_count += 1
            raise RuntimeError("CDP result unavailable")

        tab.button.click = click_then_raise
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_conflict_waits_for_new_document_before_resubmitting(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"
        _state["seat_conflict_pending"][submit_key] = {
            "source_document_id": "document-1",
            "dialog_dismissed": True,
            "reload_dispatched": False,
        }

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(side_effect=[False, True]),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertEqual(2, reload_page.await_count)

            _state["last_document"][identity] = "document-2"
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertNotIn(submit_key, _state["seat_conflict_pending"])

    async def test_conflict_dismiss_failure_blocks_reload(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dismiss_allowed = False
        cfg = config()
        submit_key = self._track_submit_state(tab)

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page, patch(
            "platforms.utiki.workflow.nodriver_ctbc_ticket_type_auto_select",
            AsyncMock(return_value=True),
        ) as select_type:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertFalse(
            _state["seat_conflict_pending"][submit_key]["dialog_dismissed"]
        )
        reload_page.assert_not_awaited()
        select_type.assert_not_awaited()

    async def test_conflict_click_without_close_blocks_reload(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dialog_closes_on_click = False
        cfg = config()
        submit_key = self._track_submit_state(tab)

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertFalse(
            _state["seat_conflict_pending"][submit_key]["dialog_dismissed"]
        )
        reload_page.assert_not_awaited()

    async def test_conflict_dismiss_race_recovers_after_dialog_disappears(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dismiss_allowed = False
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"

        self.assertEqual("seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg))
        tab.dialog = ""
        tab.dialog_visible = False

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertTrue(
            _state["seat_conflict_pending"][submit_key]["dialog_dismissed"]
        )
        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")

    async def test_conflict_pending_clears_when_document_changes(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-2"
        _state["seat_conflict_pending"][submit_key] = {
            "source_document_id": "document-1",
            "page_type": "seat",
            "dialog_dismissed": False,
            "reload_dispatched": False,
        }

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertNotIn(submit_key, _state["seat_conflict_pending"])
        reload_page.assert_not_awaited()

    async def test_general_conflict_uses_same_reload_recovery(self):
        tab = FakeSubmitTab(
            dialog="",
            url=(
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
            ),
        )
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"
        _state["seat_conflict_pending"][submit_key] = {
            "source_document_id": "document-1",
            "page_type": "general_admission",
            "dialog_dismissed": True,
            "reload_dispatched": False,
        }

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(
                await nodriver_ctbc_general_admission_main(tab, cfg, ocr=None)
            )

        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")

    async def test_visible_conflict_blocks_click_immediately_before_submit(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertIn(submit_key, _state["seat_conflict_pending"])
        self.assertEqual(1, tab.dialog_button.click_count)

    async def test_quota_dialog_blocks_only_current_purchase(self):
        original_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        tab = FakeSubmitTab(dialog="已超出可購買額度", url=original_url)
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        original_key = self._track_submit_state(tab)
        _state["submitted"][original_key] = time.monotonic()
        self.assertEqual("quota", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertNotIn(original_key, _state["submitted"])
        self.assertEqual("quota", _state["submit_blocked"][original_key])

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)

        tab.target.url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        new_key = self._track_submit_state(tab)
        self.assertNotIn(new_key, _state["submit_blocked"])

    async def test_quota_state_survives_dialog_dismiss_race(self):
        tab = FakeSubmitTab(dialog="已超出可購買額度")
        tab.dismiss_allowed = False
        submit_key = self._track_submit_state(tab)
        _state["submitted"][submit_key] = time.monotonic()

        self.assertEqual("quota", await _handle_ctbc_submit_dialog(tab, config()))

        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("quota", _state["submit_blocked"][submit_key])
        self.assertEqual(0, tab.dialog_button.click_count)

    async def test_captcha_dismiss_failure_defers_reset_without_resubmit(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        tab.dismiss_allowed = False
        submit_key = self._track_submit_state(tab)
        _state["submitted"][submit_key] = 1.0
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, config()))
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, config()))
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual(0, tab.dialog_button.click_count)
        self.assertEqual("captcha_pending", _state["submit_blocked"][submit_key])
        self.assertEqual("ABCD", tab.captcha_input.value)
        self.assertEqual(0, tab.captcha_refresh.click_count)

        tab.dialog = ""
        tab.dialog_visible = False
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertEqual("", tab.captcha_input.value)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertNotIn(submit_key, _state["submit_blocked"])

    async def test_captcha_refresh_exception_never_clicks_refresh_twice(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        self.addCleanup(_state["last_document"].pop, identity, None)
        _state["last_document"][identity] = "document-1"

        async def refresh_then_raise():
            tab.captcha_refresh.clicked = True
            tab.captcha_refresh.click_count += 1
            raise RuntimeError("CDP result unavailable")

        tab.captcha_refresh.click = refresh_then_raise
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertEqual(1, tab.captcha_input.apply_count)
        self.assertEqual("unknown", _state["captcha_reset"][submit_key]["phase"])
        self.assertEqual("captcha_pending", _state["submit_blocked"][submit_key])

        async def refresh_success():
            tab.captcha_refresh.clicked = True
            tab.captcha_refresh.click_count += 1

        tab.captcha_refresh.click = refresh_success
        _state["last_document"][identity] = "document-2"
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(2, tab.captcha_refresh.click_count)
        self.assertEqual(2, tab.captcha_input.apply_count)
        self.assertNotIn(submit_key, _state["captcha_reset"])
        self.assertNotIn(submit_key, _state["submit_blocked"])

    async def test_auth_error_blocks_retry_until_credentials_change(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)
        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertIn(submit_key, _state["auth_failure"])
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)
