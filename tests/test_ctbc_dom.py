import base64
import json
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests.ctbc_support import (
    CTBC_TEST_AREA_URL,
    FakeElement,
    FakeSubmitTab,
    FakeTab,
    config,
    make_ctbc_area_snapshot,
)

from platforms.ctbc import (
    CTBC_AREA_ROW_SELECTOR,
    CTBC_DATE_PURCHASE_CONTROL_SELECTOR,
    CTBC_DATE_ROW_SELECTOR,
    _ctbc_attest_fast_destination,
    _ctbc_captcha_and_submit,
    _ctbc_tab_identity,
    _ctbc_tab_state_key,
    _get_ctbc_captcha_image_bytes,
    _handle_ctbc_submit_dialog,
    _recover_ctbc_seat_conflict,
    _state,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_general_admission_main,
    nodriver_ctbc_seat_main,
    nodriver_ctbc_ticket_type_auto_select,
)


class CtbcDomFlowTest(unittest.IsolatedAsyncioTestCase):
    def _track_submit_state(self, tab):
        submit_key = _ctbc_tab_state_key(tab)
        for state_name in (
            "submitted",
            "seat_conflict_pending",
            "submit_blocked",
            "auth_failure",
            "captcha_reset",
        ):
            self.addCleanup(_state[state_name].pop, submit_key, None)
        return submit_key

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
        cfg["ctbc"]["ticket_type_keyword"] = "身心"
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

    async def test_captcha_capture_prefers_native_canvas_bytes(self):
        expected = b"native-png"
        data_url = "data:image/png;base64," + base64.b64encode(expected).decode("ascii")
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=data_url))
        with patch(
            "platforms.ctbc.nodriver_get_captcha_image_from_dom_snapshot",
            new=AsyncMock(return_value=b"fallback"),
        ) as fallback:
            self.assertEqual(expected, await _get_ctbc_captcha_image_bytes(tab, config()))
            fallback.assert_not_awaited()

    async def test_captcha_capture_falls_back_when_canvas_is_unavailable(self):
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=""))
        with patch(
            "platforms.ctbc.nodriver_get_captcha_image_from_dom_snapshot",
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
            expected = _state["fast_target"][_ctbc_tab_identity(tab)]
            self.assertEqual("PRICE_FRONT", expected["price_area_id"])
        finally:
            _state["fast_target"].pop(_ctbc_tab_identity(tab), None)

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

    async def test_native_area_path_accepts_available_status_skew(self):
        snapshot = make_ctbc_area_snapshot(include_back=False)
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR,
            [],
            url=CTBC_TEST_AREA_URL,
            evaluate_results=[json.dumps(snapshot), True],
        )

        self.assertTrue(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertEqual(2, tab.evaluate_count)

    async def test_native_area_commit_is_quantity_aware_and_fails_closed(self):
        snapshot = make_ctbc_area_snapshot(
            front_title_status="40",
            front_action_status="40",
            include_back=False,
        )
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR,
            [],
            url=CTBC_TEST_AREA_URL,
            evaluate_results=[json.dumps(snapshot), False],
        )
        self.assertFalse(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        commit_script = tab.evaluate_scripts[-1]
        self.assertIn("const ticketNumber = 2;", commit_script)
        self.assertIn("action.stableArgs.some", commit_script)
        self.assertIn("hasTickets(action.status)", commit_script)
        self.assertIn("hasTickets(mapTitle.status)", commit_script)
        self.assertIn("hasTickets(status)", commit_script)
        self.assertIn("root.hasAttribute(commitMarker)", commit_script)
        self.assertIn("root.setAttribute(commitMarker, '1')", commit_script)
        self.assertIn("area.click();", commit_script)
        self.assertNotIn("setTimeout", commit_script)
        self.assertNotIn("expectedHref", commit_script)
        self.assertNotIn("expectedTitle", commit_script)
        self.assertNotIn("window.__ticketsHunterCtbcAreaCommit", commit_script)
        self.assertNotIn("status !== expectedStatus", commit_script)

    async def test_failed_fast_destination_remains_fail_closed_until_area_page(self):
        seat_url = "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&PERFORMANCE_PRICE_AREA_ID=P16D12YO"
        tab = FakeTab("", [], url=seat_url, evaluate_results=[True, True])
        identity = _ctbc_tab_identity(tab)
        _state["fast_target"][identity] = {
            "route": "0205", "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO", "returning": True,
            "area_url": "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "created_at": time.monotonic(),
        }
        try:
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertIn(identity, _state["fast_target"])
        finally:
            _state["fast_target"].pop(identity, None)

    async def test_captcha_does_not_submit_when_force_submit_is_off(self):
        tab = FakeSubmitTab()
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": False}
        self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(tab.button.clicked)

    async def test_captcha_submits_when_force_submit_is_on(self):
        tab = FakeSubmitTab(dialog="", dialogs=["", "", "成功加入購物車"])
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = _ctbc_tab_state_key(tab)
        try:
            self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
            self.assertTrue(tab.button.clicked)
        finally:
            _state["submitted"].pop(submit_key, None)
            _state["submit_blocked"].pop(submit_key, None)

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
            "platforms.ctbc._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.ctbc._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page, patch(
            "platforms.ctbc.nodriver_ctbc_ticket_type_auto_select",
            AsyncMock(return_value=True),
        ) as select_type, patch(
            "platforms.ctbc._ctbc_captcha_and_submit",
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

        with patch("platforms.ctbc.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIn(submit_key, _state["submitted"])

        with patch("platforms.ctbc.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.button.click_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_late_conflict_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.ctbc.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.ctbc.time.monotonic", return_value=111.0):
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

        with patch("platforms.ctbc.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.ctbc.time.monotonic", return_value=111.0):
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

        with patch("platforms.ctbc.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.ctbc.time.monotonic", return_value=111.0):
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
            "platforms.ctbc._ctbc_login_fingerprint",
            AsyncMock(return_value=""),
        ), patch(
            "platforms.ctbc._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ):
            self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, cfg))
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
            self.assertEqual(0, tab.button.click_count)

            _state["last_document"][identity] = "document-2"
            self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertNotIn(submit_key, _state["auth_failure"])
        self.assertEqual(0, tab.button.click_count)

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
            "platforms.ctbc._reload_with_interval",
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
            "platforms.ctbc._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.ctbc._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page, patch(
            "platforms.ctbc.nodriver_ctbc_ticket_type_auto_select",
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
            "platforms.ctbc._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.ctbc._reload_with_interval",
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
            "platforms.ctbc._reload_with_interval",
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
            "platforms.ctbc._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertNotIn(submit_key, _state["seat_conflict_pending"])
        reload_page.assert_not_awaited()

    async def test_general_conflict_uses_same_reload_recovery(self):
        tab = FakeSubmitTab(
            dialog="",
            url="https://tix.ctbcsports.com/BROTHERS/UTK0203_?PERFORMANCE_ID=P1",
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
            "platforms.ctbc._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.ctbc._reload_with_interval",
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
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1"
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
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P2"
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
