import subprocess
import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.ctbc_support import (
    CTBC_TEST_PERFORMANCE_ID,
    CTBC_TEST_SERVER_QUANTITY_URL,
    FakeSubmitTab,
    config,
    make_ctbc_area_snapshot,
    make_ctbc_quantity_snapshot,
)

import platforms.ctbc as legacy_ctbc
import platforms.utiki as utiki
from platforms.utiki import workflow
from platforms.utiki.auth import (
    cookie_domain_is_in_scope,
    get_tenant_auth_profile,
)
from platforms.utiki.profiles import (
    PageKind,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
)
from platforms.utiki.selection import (
    DateActionPlan,
    SeatActionPlan,
    SubmitPlan,
    availability_has_tickets as _ctbc_availability_has_tickets,
    build_submit_plan,
    choose_candidate_index as _choose_candidate_index,
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    parse_area_send_action as _parse_ctbc_map_send,
    parse_area_send_a_action as _parse_ctbc_map_send_a,
    parse_date_snapshot,
    parse_dialog_snapshot,
    parse_keyword_groups as _parse_keyword_groups,
    parse_map_title as _parse_ctbc_map_title,
    parse_quantity_snapshot,
    parse_seat_snapshot,
    quantity_audit_is_valid as _ctbc_general_quantity_audit_is_valid,
    seat_plan_matches_snapshot,
    seat_selection_is_valid,
    select_date_snapshot,
    select_area_snapshot as _select_ctbc_area_snapshot,
    select_quantity_snapshot,
    select_seat_indexes as _select_ctbc_seat_indexes,
    select_seat_snapshot,
    submit_plan_is_valid,
)
from nodriver_common import _order_captcha_candidates, create_ocr_for_platform
from platforms.utiki.workflow import (
    _build_ctbc_destination_url,
    _ctbc_captcha_image_token,
    _ctbc_fast_destination_is_valid,
    _ctbc_purchase_state_key,
    _ctbc_submit_reached_checkout,
    _get_ctbc_page_type,
    _get_utiki_behavior,
    _normalize_ctbc_captcha_answer,
    is_utk_sports_url,
    parse_ctbc_cookie_string,
)
from settings import get_default_config, migrate_config


def _date_control(onclick="", **overrides):
    control = {
        "actionable": True,
        "tag_name": "BUTTON",
        "id": "buy_btn",
        "href": "",
        "resolved_href": "",
        "role": "",
        "type": "button",
        "target": "",
        "download": "",
        "ping": "",
        "formaction": "",
        "has_form": False,
        "onclick": onclick,
    }
    control.update(overrides)
    return control


def _date_snapshot(source_url, rows):
    return {
        "ok": True,
        "page_url": source_url,
        "document_id": "document-1",
        "rows": rows,
    }


def _select_date_action(source_url, control, row_text="9/10 一般場"):
    snapshot = _date_snapshot(
        source_url,
        [{"text": row_text, "controls": [control]}],
    )
    return select_date_snapshot(snapshot, config(date_keyword='"9/10"'))


def _seat_snapshot(*, selected=(), submitted=None, counters=None):
    type_name = "內野全票-NT$400"
    seats = []
    for index in range(3):
        is_selected = index in selected
        seats.append({
            "fingerprint": ["seat", index, f"A區-1排-{index + 1}號"],
            "title": f"A區-1排-{index + 1}號",
            "row_index": 1,
            "cell_index": index,
            "center_x": 10 + index * 20,
            "center_y": 10,
            "width": 20,
            "height": 20,
            "available": not is_selected,
            "selected": is_selected,
        })
    return {
        "ok": True,
        "page_url": (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        ),
        "document_id": "document-1",
        "performance_id": "P1",
        "group_id": "G1",
        "price_area_id": "A1",
        "product_id": "PRODUCT1",
        "product_hidden_present": True,
        "quantity_limit_present": True,
        "quantity_limit_raw": "4",
        "ticket_types": [{
            "fingerprint": ["ticket", "FULL", "setType('FULL')"],
            "text": "全票",
            "disabled": False,
            "type_id": "FULL",
            "type_name": type_name,
            "counter_present": True,
            "counter_raw": str(len(selected)),
        }],
        "seats": seats,
        "submitted_seats": submitted if submitted is not None else [
            {"A": "S", "Y": "FULL", "Z": type_name} for _ in selected
        ],
        "counters": counters if counters is not None else [
            {"id": "FULL", "counter_raw": str(len(selected))},
            {"id": "HALF", "counter_raw": "0"},
        ],
        "visual_selected_count": len(selected),
    }


class CtbcPureSelectionTest(unittest.TestCase):
    def test_captcha_image_token_is_stable_and_nonempty_only_for_bytes(self):
        first = _ctbc_captcha_image_token(b"synthetic-captcha")

        self.assertEqual(64, len(first))
        self.assertEqual(first, _ctbc_captcha_image_token(b"synthetic-captcha"))
        self.assertNotEqual(first, _ctbc_captcha_image_token(b"changed"))
        self.assertEqual("", _ctbc_captcha_image_token(b""))
        self.assertEqual("", _ctbc_captcha_image_token("raw-image"))

    def test_legacy_facade_shares_the_canonical_workflow(self):
        for name in legacy_ctbc.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(legacy_ctbc, name), getattr(workflow, name))
        self.assertIs(utiki.nodriver_utiki_main, workflow.nodriver_ctbc_main)
        self.assertFalse(hasattr(legacy_ctbc, "_state"))

    def test_legacy_and_canonical_import_orders_do_not_cycle(self):
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        orders = (
            "import platforms.ctbc; import platforms.utiki",
            "import platforms.utiki; import platforms.ctbc",
        )
        for imports in orders:
            script = (
                f"import sys; sys.path.insert(0, {source_root!r}); "
                f"{imports}; import nodriver_tixcraft"
            )
            with self.subTest(imports=imports):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_seat_sorting_setting_defaults_and_migrates_to_number_order(self):
        default = get_default_config()
        self.assertEqual("row_seat", default["utiki"]["seat_sorting"])
        self.assertEqual("row_seat", default["ctbc"]["seat_sorting"])

        old_config = {"ctbc": {"ticket_type_keyword": "全票"}}
        migrated = migrate_config(old_config)
        self.assertEqual("row_seat", migrated["ctbc"]["seat_sorting"])

        migrated["utiki"]["seat_sorting"] = "unsupported"
        self.assertEqual("row_seat", migrate_config(migrated)["ctbc"]["seat_sorting"])

        for seat_sorting in (
            "display", "row_seat", "row_asc_aisle",
            "row_asc_seat_desc", "row_desc_seat_asc"
        ):
            configured = {"ctbc": {"seat_sorting": seat_sorting}}
            self.assertEqual(
                seat_sorting,
                migrate_config(configured)["ctbc"]["seat_sorting"],
            )

    def test_utiki_behavior_accessor_prefers_canonical_by_key(self):
        configured = {
            "utiki": {"ticket_type_keyword": ""},
            "ctbc": {"ticket_type_keyword": "舊票種", "seat_sorting": "display"},
        }
        self.assertEqual(
            "", _get_utiki_behavior(configured, "ticket_type_keyword", "全票")
        )
        self.assertEqual(
            "display", _get_utiki_behavior(configured, "seat_sorting", "row_seat")
        )

    def test_config_builder_returns_fresh_complete_configs(self):
        first = config()
        second = config()

        self.assertIn("accounts", first)
        self.assertIn("ocr_captcha", first)
        self.assertFalse(first["ocr_captcha"]["enable"])
        first["advanced"]["auto_reload_page_interval"] = 99
        self.assertEqual(0, second["advanced"]["auto_reload_page_interval"])

    def test_captcha_answer_is_ascii_only_and_uppercase(self):
        self.assertEqual("2CHH", _normalize_ctbc_captcha_answer(" 2C-HH\n"))
        self.assertEqual("WTYF", _normalize_ctbc_captcha_answer("wtyf"))

    def test_submit_dialog_classifier_separates_retry_and_terminal_outcomes(self):
        cases = {
            "": None,
            "成功加入購物車": "success",
            "驗證碼錯誤": "captcha",
            "帳號或密碼錯誤": "auth",
            "有部分座位已被訂走, 請再重新選取座位 !": "seat_conflict",
            "該位置已被選走": "seat_conflict",
            "位置已被預訂": "seat_conflict",
            "已超出可購買額度": "quota",
            "系統忙碌，請稍後再試": "rejected",
        }
        for dialog_text, expected in cases.items():
            with self.subTest(dialog_text=dialog_text):
                self.assertEqual(expected, _classify_ctbc_submit_dialog(dialog_text))

    def test_dialog_snapshot_parser_preserves_visible_ambiguity(self):
        snapshot = parse_dialog_snapshot({
            "page_url": "https://tix.ctbcsports.com/BROTHERS/UTK0205_",
            "document_id": "document-1",
            "visible_count": 2,
            "text": "",
        })
        self.assertEqual(2, snapshot.visible_count)
        self.assertEqual("", snapshot.text)
        self.assertIsNone(parse_dialog_snapshot({
            "page_url": "https://tix.ctbcsports.com/BROTHERS/UTK0205_",
            "document_id": "document-1",
            "visible_count": 0,
            "text": "unexpected",
        }))
        self.assertIsNone(parse_dialog_snapshot({
            "page_url": "",
            "document_id": "",
            "visible_count": 0,
            "text": "",
        }))

    def test_ctbc_uses_official_ocr_instead_of_universal_model(self):
        fake_ocr = types.SimpleNamespace(set_ranges=lambda value: setattr(fake_ocr, "ranges", value))
        fake_ddddocr = types.SimpleNamespace(DdddOcr=lambda **_kwargs: fake_ocr)
        cfg = {
            "homepage": "https://tix.ctbcsports.com/BROTHERS/UTK0205_",
            "ocr_captcha": {
                "use_universal": True,
                "path": "assets/model/universal",
                "beta": True,
            },
            "advanced": {"verbose": False},
        }
        with patch("nodriver_common.ddddocr", fake_ddddocr):
            self.assertIs(fake_ocr, create_ocr_for_platform(cfg))
        self.assertEqual(5, fake_ocr.ranges)

        cfg["homepage"] = "https://tix.wdragons.com/UTK0205_"
        with patch("nodriver_common.ddddocr", fake_ddddocr):
            self.assertIs(fake_ocr, create_ocr_for_platform(cfg))

    def test_cookie_parser_accepts_header_and_filters_non_ctbc_cookies(self):
        raw = (
            "Cookie: .AspNetCore.Antiforgery.token=stale; "
            "CTBC_BROTHERS|BROTHERS|_1=session==; "
            "CTBC_BROTHERS|BROTHERS|_5=1; _ga=tracking"
        )
        self.assertEqual([
            ("CTBC_BROTHERS|BROTHERS|_1", "session=="),
            ("CTBC_BROTHERS|BROTHERS|_5", "1"),
        ], parse_ctbc_cookie_string(raw))

    def test_cookie_parser_accepts_json_export(self):
        raw = '[{"name":"CTBC_BROTHERS|BROTHERS|_1","value":"abc"},{"name":"_ga","value":"x"}]'
        self.assertEqual(
            [("CTBC_BROTHERS|BROTHERS|_1", "abc")],
            parse_ctbc_cookie_string(raw),
        )

    def test_tenant_auth_capabilities_keep_ctbc_credentials_scoped(self):
        ctbc = get_tenant_auth_profile(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1"
        )
        dragons = get_tenant_auth_profile(
            "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1"
        )

        self.assertTrue(ctbc.supports_cookie_login)
        self.assertTrue(ctbc.supports_inline_login)
        self.assertEqual("ctbc_account", ctbc.account_key)
        self.assertEqual("ctbc_password", ctbc.password_key)
        self.assertTrue(cookie_domain_is_in_scope(ctbc, ".ctbcsports.com"))
        self.assertTrue(cookie_domain_is_in_scope(ctbc, "login.ctbcsports.com"))
        self.assertFalse(cookie_domain_is_in_scope(ctbc, "evilctbcsports.com"))
        self.assertFalse(cookie_domain_is_in_scope(ctbc, "ctbcsports.com.evil.test"))

        self.assertFalse(dragons.supports_cookie_login)
        self.assertFalse(dragons.supports_inline_login)
        self.assertEqual("", dragons.account_key)
        self.assertEqual("", dragons.password_key)

    def test_page_routing(self):
        base = "https://tix.ctbcsports.com/BROTHERS/"
        self.assertEqual("date", _get_ctbc_page_type(base + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual(
            "date",
            _get_ctbc_page_type(
                base + "UTK0201_?PRODUCT_ID=P1&STARTDATE=2026/09/18"
            ),
        )
        self.assertEqual("area", _get_ctbc_page_type(base + "UTK0204_?PERFORMANCE_ID=P1"))
        self.assertEqual("seat", _get_ctbc_page_type(base + "UTK0205_?PERFORMANCE_ID=P1"))
        self.assertEqual("general_admission", _get_ctbc_page_type(base + "UTK0202_?PERFORMANCE_ID=P1"))
        self.assertEqual("general_admission", _get_ctbc_page_type(base + "UTK0203_?PERFORMANCE_ID=P1"))
        self.assertEqual("checkout", _get_ctbc_page_type(base + "UTK0206_"))
        self.assertIsNone(_get_ctbc_page_type("https://ticket.ibon.com.tw/UTK0201_?PRODUCT_ID=P1"))

        dragons = "https://tix.wdragons.com/"
        self.assertTrue(is_utk_sports_url(dragons + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual("date", _get_ctbc_page_type(dragons + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual("area", _get_ctbc_page_type(dragons + "UTK0204_?PERFORMANCE_ID=P1"))
        self.assertEqual("seat", _get_ctbc_page_type(dragons + "UTK0205_?PERFORMANCE_ID=P1"))
        self.assertEqual(
            "general_admission",
            _get_ctbc_page_type(dragons + "UTK0203_?PERFORMANCE_ID=P1"),
        )
        self.assertEqual("checkout", _get_ctbc_page_type(dragons + "UTK0206_"))

    def test_family_profiles_and_page_kind_contract(self):
        ctbc_root = "https://tix.ctbcsports.com/BROTHERS/"
        dragons_root = "https://tix.wdragons.com/"
        cases = (
            (ctbc_root + "UTK0201_?PRODUCT_ID=P1", PageKind.DATE),
            (ctbc_root + "UTK0204_?PERFORMANCE_ID=P1", PageKind.AREA),
            (ctbc_root + "UTK0205_?PERFORMANCE_ID=P1", PageKind.RESERVED_SEAT),
            (ctbc_root + "UTK0202_?PERFORMANCE_ID=P1", PageKind.GENERAL_ADMISSION),
            (
                ctbc_root
                + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=G1&"
                "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N",
                PageKind.SERVER_ASSIGNED_QUANTITY,
            ),
            (ctbc_root + "UTK0206_", PageKind.CHECKOUT),
            (dragons_root + "UTK0201_?PRODUCT_ID=P1", PageKind.DATE),
            (dragons_root + "UTK0204_?PERFORMANCE_ID=P1", PageKind.AREA),
            (dragons_root + "UTK0205_?PERFORMANCE_ID=P1", PageKind.RESERVED_SEAT),
            (dragons_root + "UTK0203_?PERFORMANCE_ID=P1", PageKind.GENERAL_ADMISSION),
            (
                dragons_root
                + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=G1&"
                "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N",
                PageKind.SERVER_ASSIGNED_QUANTITY,
            ),
            (dragons_root + "UTK0206_", PageKind.CHECKOUT),
        )
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(expected, classify_utiki_page(url))
                self.assertEqual(expected.value, _get_ctbc_page_type(url))

        ctbc = get_utiki_tenant_profile(ctbc_root)
        dragons = get_utiki_tenant_profile(dragons_root)
        self.assertEqual("ctbc_brothers", ctbc.key)
        self.assertEqual("/BROTHERS", ctbc.tenant_path)
        self.assertEqual("CTBC_SPORTS", ctbc.map_namespace)
        self.assertTrue(ctbc.fast_area_prefetch)
        self.assertFalse(ctbc.supports_server_assignment)
        self.assertEqual("wei_chuan_dragons", dragons.key)
        self.assertEqual("", dragons.tenant_path)
        self.assertEqual("WDRAGONS", dragons.map_namespace)
        self.assertFalse(dragons.fast_area_prefetch)
        self.assertFalse(dragons.supports_server_assignment)

        invalid_server_routes = (
            ctbc_root
            + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=G1&"
            "PERFORMANCE_PRICE_AREA_ID=A1",
            ctbc_root
            + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=G1&"
            "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=Y",
            ctbc_root
            + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=G1&"
            "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N&ATYPE=N",
            ctbc_root
            + "UTK0201_001/extra?PERFORMANCE_ID=P1&GROUP_ID=G1&"
            "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N",
            ctbc_root
            + "UTK0201_001?PERFORMANCE_ID=P1&GROUP_ID=&"
            "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N",
        )
        for url in invalid_server_routes:
            with self.subTest(invalid_server_route=url):
                self.assertEqual(PageKind.UNKNOWN, classify_utiki_page(url))
                self.assertIsNone(_get_ctbc_page_type(url))

    def test_family_membership_is_independent_from_actionable_routes(self):
        homepage = "https://tix.ctbcsports.com/BROTHERS/"
        unknown_page = homepage + "account/profile"
        self.assertTrue(is_utiki_sports_url(homepage))
        self.assertTrue(is_utiki_sports_url(unknown_page))
        self.assertEqual(PageKind.UNKNOWN, classify_utiki_page(homepage))
        self.assertEqual(PageKind.UNKNOWN, classify_utiki_page(unknown_page))
        self.assertIsNone(_get_ctbc_page_type(unknown_page))

    def test_utk_origin_rejects_insecure_or_ambiguous_authorities(self):
        valid_urls = (
            "https://tix.ctbcsports.com/BROTHERS/",
            "https://tix.ctbcsports.com:443/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1",
        )
        rejected_urls = (
            "http://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://@tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://user@tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://user:pass@tix.wdragons.com/UTK0201_?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com:444/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com:notaport/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://tix.wdragons.com:8443/UTK0201_?PRODUCT_ID=P1",
        )
        for url in valid_urls:
            with self.subTest(url=url):
                self.assertTrue(is_utk_sports_url(url))
        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertFalse(is_utk_sports_url(url))
                self.assertIsNone(_get_ctbc_page_type(url))

    def test_page_routing_rejects_noncanonical_paths_and_query_keys(self):
        rejected_urls = (
            "https://tix.ctbcsports.com/UTK0201_?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/nested/UTK0201_?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_extra?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_/child?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/%42ROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1#fragment",
            "https://tix.wdragons.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
            "https://tix.wdragons.com/nested/UTK0204_?PERFORMANCE_ID=P1",
            "https://tix.wdragons.com/UTK0205_suffix?PERFORMANCE_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?NEXT=PRODUCT_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?NEXT=PERFORMANCE_ID=P1",
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1&PRODUCT_ID=P2",
            "https://tix.wdragons.com/UTK0203_?PERFORMANCE_ID=P1&PERFORMANCE_ID=P2",
        )
        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertIsNone(_get_ctbc_page_type(url))

    def test_keyword_priority_and_strict_no_match(self):
        candidates = [
            {"text": "2026/09/10 一般席"},
            {"text": "2026/09/10 家庭席"},
        ]
        self.assertEqual(1, _choose_candidate_index(candidates, '"家庭席","一般席"', "from top to bottom"))
        self.assertIsNone(_choose_candidate_index(candidates, '"8/15"', "from top to bottom", False))
        self.assertEqual(0, _choose_candidate_index(candidates, '"8/15"', "from top to bottom", True))

    def test_raw_and_semicolon_keywords_are_accepted(self):
        self.assertEqual(["A"], _parse_keyword_groups("A"))
        self.assertEqual(["A", "B"], _parse_keyword_groups("A;B"))

    def test_sold_out_and_known_shortage_are_rejected(self):
        self.assertFalse(_ctbc_availability_has_tickets("售完", 1))
        self.assertFalse(_ctbc_availability_has_tickets("1", 2))
        self.assertTrue(_ctbc_availability_has_tickets("2", 2))
        self.assertFalse(_ctbc_availability_has_tickets("剩餘 1", 2))
        self.assertTrue(_ctbc_availability_has_tickets("熱賣中", 2))

    def test_date_snapshot_is_immutable_and_rejects_ambiguous_controls(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=A1&PRODUCT_ID=P1'"
        raw = _date_snapshot(source, [{
            "text": "9/10 一般場",
            "controls": [_date_control(onclick)],
        }])
        snapshot = parse_date_snapshot(raw)
        raw["rows"][0]["text"] = "mutated"
        raw["rows"][0]["controls"][0]["onclick"] = "alert('x')"
        plan = select_date_snapshot(snapshot, config(date_keyword='"9/10"'))
        self.assertEqual("9/10 一般場", plan.text)
        self.assertEqual(onclick, plan.onclick)

        ambiguous = _date_snapshot(source, [{
            "text": "9/10 一般場",
            "controls": [_date_control(onclick), _date_control(onclick)],
        }])
        self.assertIsNone(select_date_snapshot(
            ambiguous, config(date_keyword='"9/10"')
        ))

    def test_date_selection_preserves_keyword_exclude_mode_and_fallback(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        snapshot = _date_snapshot(source, [
            {
                "text": "9/10 一般場",
                "controls": [_date_control(
                    "location.href='UTK0204_?PERFORMANCE_ID=A1&PRODUCT_ID=P1'"
                )],
            },
            {
                "text": "9/11 家庭場",
                "controls": [_date_control(
                    "location.href='UTK0204_?PERFORMANCE_ID=A2&PRODUCT_ID=P1'"
                )],
            },
        ])
        cfg = config(date_keyword='"家庭場","一般場"')
        self.assertEqual(1, select_date_snapshot(snapshot, cfg).row_index)

        cfg["keyword_exclude"] = '"家庭場"'
        self.assertEqual(0, select_date_snapshot(snapshot, cfg).row_index)
        cfg["date_auto_select"]["date_keyword"] = '"不存在"'
        self.assertIsNone(select_date_snapshot(snapshot, cfg))
        cfg["date_auto_fallback"] = True
        self.assertEqual(0, select_date_snapshot(snapshot, cfg).row_index)
        cfg["date_auto_select"]["date_keyword"] = ""
        cfg["date_auto_select"]["mode"] = "from bottom to top"
        cfg["keyword_exclude"] = ""
        self.assertEqual(1, select_date_snapshot(snapshot, cfg).row_index)

    def test_date_target_parser_only_accepts_the_same_tenant_and_product(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        target = _select_date_action(source, _date_control(onclick))
        self.assertIsInstance(target, DateActionPlan)
        self.assertEqual("normal", target.action_kind)
        self.assertEqual("P16D0B8Z", target.performance_id)
        self.assertEqual("P16ANF0L", target.product_id)
        self.assertIsNone(_select_date_action(source, _date_control(
            "location.href='https://evil.example/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
        )))
        self.assertIsNone(_select_date_action(source, _date_control(
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=OTHER'",
        )))
        self.assertIsNone(_select_date_action(source, _date_control(
            "location.href='https://tix.ctbcsports.com:444/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
        )))
        self.assertIsNone(_select_date_action(source, _date_control(
            "location.href='https://user@tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
        )))

        dragons_source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        dragons_target = _select_date_action(dragons_source, _date_control(
            "location.href='UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'",
        ))
        self.assertEqual("tix.wdragons.com", dragons_target.host)
        self.assertEqual("WDRAGONS", dragons_target.map_namespace)
        self.assertFalse(dragons_target.fast_area_prefetch)
        self.assertIsNone(_select_date_action(dragons_source, _date_control(
            "location.href='https://tix.ctbcsports.com/UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'",
        )))

    def test_date_action_classifier_supports_vip_and_safe_structural_fallback(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0P&STARTDATE=2026/09/18"
        )
        vip = _select_date_action(
            source, _date_control("VipSellCheck('P1DN7WJI');")
        )
        self.assertEqual("vip_native", vip.action_kind)
        self.assertEqual("P1DN7WJI", vip.action_token)
        self.assertEqual(
            "structural_native",
            _select_date_action(
                source, _date_control("DragonMemberCheck('P1DRAGON')")
            ).action_kind,
        )
        self.assertEqual(
            "structural_native",
            _select_date_action(source, _date_control()).action_kind,
        )
        self.assertIsNone(_select_date_action(
            source, _date_control("location.href='https://evil.example/'")
        ))
        self.assertIsNone(_select_date_action(
            source, _date_control("DragonMemberCheck('P1'); alert('x')")
        ))
        self.assertIsNone(_select_date_action(
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P1",
            _date_control(),
        ))

        anchor_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0P"
        )
        anchor = _select_date_action(source, _date_control(
            tag_name="A", id="", type="", href=anchor_url,
            resolved_href=anchor_url,
        ))
        self.assertEqual("anchor_native", anchor.action_kind)
        self.assertEqual(anchor_url, anchor.target_url)
        self.assertIsNone(_select_date_action(source, _date_control(
            tag_name="A", id="", type="", href=anchor_url,
            resolved_href=anchor_url, target="_blank",
        )))

    def test_map_send_parser_is_anchored_and_builds_the_official_url(self):
        href = "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')"
        target = _parse_ctbc_map_send(href, "P16D0B8Z")
        self.assertEqual("P16D12YO", target["price_area_id"])
        self.assertEqual(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&PERFORMANCE_PRICE_AREA_ID=P16D12YO",
            _build_ctbc_destination_url(
                "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
                target,
            ),
        )
        self.assertIsNone(_build_ctbc_destination_url(
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_suffix?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            target,
        ))
        self.assertIsNone(_build_ctbc_destination_url(
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=OTHER&PRODUCT_ID=P16ANF0L",
            target,
        ))
        self.assertIsNone(_parse_ctbc_map_send(href + ";alert(1)", "P16D0B8Z"))
        self.assertIsNone(_parse_ctbc_map_send(href, "OTHER"))
        general = _parse_ctbc_map_send(
            "javascript:Send('0202','P16D0B8Z','P16D12ZY','','熱賣中');", "P16D0B8Z"
        )
        self.assertEqual("", general["group_id"])
        send_a = "javascript:SendA('0205','P16D0B8Z','35','P16D12YO','AG1','AI1','熱賣中');"
        send_a_target = _parse_ctbc_map_send_a(send_a, "P16D0B8Z")
        self.assertEqual("SendA", send_a_target["action_name"])
        self.assertEqual("0205", send_a_target["route"])
        self.assertEqual("P16D0B8Z", send_a_target["performance_id"])
        self.assertEqual("35", send_a_target["group_id"])
        self.assertEqual("P16D12YO", send_a_target["price_area_id"])
        self.assertEqual(
            ["0205", "P16D0B8Z", "35", "P16D12YO", "AG1", "AI1"],
            send_a_target["stable_args"],
        )
        self.assertEqual("熱賣中", send_a_target["status"])
        self.assertIsNone(_parse_ctbc_map_send_a(send_a, "OTHER"))
        self.assertIsNone(_parse_ctbc_map_send_a(send_a + "alert(1)", "P16D0B8Z"))
        self.assertIsNone(_parse_ctbc_map_send_a(
            "javascript:SendA('0205','P16D0B8Z','','P16D12YO','AG1','AI1','熱賣中')",
            "P16D0B8Z",
        ))
        dragons_target = _parse_ctbc_map_send(
            "javascript:Send('0205','P16DRAGON','PRICE1','GROUP1','熱賣中')", "P16DRAGON"
        )
        self.assertEqual(
            "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P16DRAGON&GROUP_ID=GROUP1&PERFORMANCE_PRICE_AREA_ID=PRICE1",
            _build_ctbc_destination_url(
                "https://tix.wdragons.com/UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U",
                dragons_target,
            ),
        )

    def test_map_title_parses_area_price_and_availability(self):
        self.assertEqual(
            {"area": "內野南A區下層", "price": "400", "status": "熱賣中"},
            _parse_ctbc_map_title("票區:內野南A區下層 票價：400 尚餘：熱賣中"),
        )
        self.assertIsNone(_parse_ctbc_map_title("內野南A區下層 400 熱賣中"))

    def test_direct_area_rejects_unavailable_action_and_wrong_identity(self):
        snapshot = {
            "rows": [
                {"area": "內野南A區下層", "price": "400", "status": "熱賣中", "rel": "a35"},
                {"area": "內野北A區下層", "price": "400", "status": "售完", "rel": "a36"},
            ],
            "areas": [
                {"id": "a35", "href": "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')", "title": "票區:內野南A區下層 票價：400 尚餘：熱賣中"},
                {"id": "a36", "href": "javascript:Send('0205','P16D0B8Z','P16D12YP','36','0')", "title": "票區:內野北A區下層 票價：400 尚餘：0"},
            ],
        }
        target = _select_ctbc_area_snapshot(
            snapshot, config(), "P16D0B8Z", require_direct_destination=True
        )
        self.assertEqual("a35", target.rel)
        snapshot["areas"][0]["href"] = "javascript:Send('0205','P16D0B8Z','P16D12YO','35','0')"
        self.assertIsNone(_select_ctbc_area_snapshot(
            snapshot, config(), "P16D0B8Z", require_direct_destination=True
        ))
        snapshot["areas"][0]["href"] = "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')"
        snapshot["areas"][0]["title"] = "票區:內野南B區下層 票價：400 尚餘：熱賣中"
        self.assertIsNone(_select_ctbc_area_snapshot(
            snapshot, config(), "P16D0B8Z", require_direct_destination=True
        ))

    def test_available_status_skew_keeps_exact_and_broad_keyword_priority(self):
        snapshot = make_ctbc_area_snapshot()
        for require_direct_destination in (False, True):
            for keyword in ('"109區前排"', '"109"'):
                with self.subTest(
                    require_direct_destination=require_direct_destination,
                    keyword=keyword,
                ):
                    target = _select_ctbc_area_snapshot(
                        snapshot,
                        config(area_keyword=keyword),
                        CTBC_TEST_PERFORMANCE_ID,
                        require_direct_destination=require_direct_destination,
                    )
                    self.assertIsNotNone(target)
                    self.assertEqual("front", target.rel)
                    if require_direct_destination:
                        self.assertTrue(target.direct_destination)
                        self.assertEqual("PRICE_FRONT", target.price_area_id)

    def test_area_selection_rejects_unavailable_status_from_each_source(self):
        baseline = {
            "front_row_status": "2",
            "front_title_status": "2",
            "front_action_status": "2",
        }
        self.assertIsNotNone(_select_ctbc_area_snapshot(
            make_ctbc_area_snapshot(include_back=False, **baseline),
            config(area_keyword='"109區前排"'),
            CTBC_TEST_PERFORMANCE_ID,
            require_direct_destination=True,
        ))
        unavailable_by_source = (
            ("row", "front_row_status", "售完"),
            ("title", "front_title_status", "0"),
            ("action", "front_action_status", "1"),
            ("row_unknown", "front_row_status", "暫停銷售"),
            ("title_unknown", "front_title_status", "剩餘NaN"),
            ("action_unknown", "front_action_status", "暫停銷售"),
        )
        for source, argument, unavailable in unavailable_by_source:
            with self.subTest(source=source):
                statuses = {**baseline, argument: unavailable}
                self.assertIsNone(_select_ctbc_area_snapshot(
                    make_ctbc_area_snapshot(include_back=False, **statuses),
                    config(area_keyword='"109區前排"'),
                    CTBC_TEST_PERFORMANCE_ID,
                    require_direct_destination=True,
                ))

    def test_direct_and_native_area_safety_boundaries(self):
        cfg = config(area_keyword='"109區前排"')
        wrong_price = make_ctbc_area_snapshot(
            front_title_status="40",
            front_action_status="40",
            front_title_price="1300",
            include_back=False,
        )
        self.assertIsNone(_select_ctbc_area_snapshot(
            wrong_price, cfg, CTBC_TEST_PERFORMANCE_ID, require_direct_destination=True
        ))

        wrong_performance = make_ctbc_area_snapshot(
            front_title_status="40",
            front_action_status="40",
            include_back=False,
        )
        wrong_performance["areas"][0]["href"] = (
            "javascript:Send('0205','OTHER','PRICE_FRONT','GROUP_FRONT','40')"
        )
        self.assertIsNone(_select_ctbc_area_snapshot(
            wrong_performance,
            cfg,
            CTBC_TEST_PERFORMANCE_ID,
            require_direct_destination=True,
        ))

        send_a = make_ctbc_area_snapshot(
            front_row_status="40",
            front_title_status="40",
            include_back=False,
        )
        send_a["areas"][0]["href"] = (
            "javascript:SendA('0205','P16D0B8Z','GROUP_FRONT','PRICE_FRONT',"
            "'AG1','AI1','40');"
        )
        native_target = _select_ctbc_area_snapshot(
            send_a, cfg, CTBC_TEST_PERFORMANCE_ID, require_direct_destination=False
        )
        self.assertIsNotNone(native_target)
        self.assertFalse(native_target.direct_destination)
        self.assertIsNone(_select_ctbc_area_snapshot(
            send_a, cfg, CTBC_TEST_PERFORMANCE_ID, require_direct_destination=True
        ))
        send_a["areas"][0]["href"] = send_a["areas"][0]["href"].replace(
            "'40');", "'1');"
        )
        self.assertIsNone(_select_ctbc_area_snapshot(
            send_a, cfg, CTBC_TEST_PERFORMANCE_ID, require_direct_destination=False
        ))

        ambiguous = make_ctbc_area_snapshot(
            front_row_status="40",
            front_title_status="40",
            front_action_status="40",
            include_back=False,
        )
        ambiguous["rows"][0]["rel"] = "front alternate"
        ambiguous["areas"].append({
            "id": "alternate",
            "href": (
                "javascript:Send('0205','P16D0B8Z','PRICE_OTHER',"
                "'GROUP_OTHER','40')"
            ),
            "title": "票區:109區前排 票價：1600 尚餘：40",
        })
        self.assertIsNone(_select_ctbc_area_snapshot(
            ambiguous, cfg, CTBC_TEST_PERFORMANCE_ID, require_direct_destination=True
        ))

    def test_fast_destination_attestation_checks_url_and_all_hidden_ids(self):
        expected = {
            "route": "0205", "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO",
            "area_url": "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
        }
        url = "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&PERFORMANCE_PRICE_AREA_ID=P16D12YO"
        ids = {
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO",
        }
        self.assertTrue(_ctbc_fast_destination_is_valid(expected, "seat", url, ids))
        self.assertFalse(_ctbc_fast_destination_is_valid(expected, "seat", url, dict(ids, group_id="99")))
        self.assertFalse(_ctbc_fast_destination_is_valid(expected, "general_admission", url, ids))
        self.assertFalse(_ctbc_fast_destination_is_valid(
            dict(expected, route="9999"),
            None,
            url.replace("UTK0205_", "UTK9999_"),
            ids,
        ))

        general_expected = dict(expected, route="0202", group_id="", price_area_id="P16D12ZY")
        general_url = "https://tix.ctbcsports.com/BROTHERS/UTK0203_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=P16D12ZY"
        general_ids = dict(ids, group_id="", price_area_id="P16D12ZY")
        self.assertTrue(_ctbc_fast_destination_is_valid(
            general_expected, "general_admission", general_url, general_ids
        ))
        general_direct_url = general_url.replace("UTK0203_", "UTK0202_")
        self.assertTrue(_ctbc_fast_destination_is_valid(
            general_expected, "general_admission", general_direct_url, general_ids
        ))

        server_expected = dict(
            expected,
            route="0201_001",
            selection_mode="computer",
            group_id="GROUP_FRONT",
            price_area_id="PRICE_FRONT",
        )
        server_ids = dict(
            ids, group_id="GROUP_FRONT", price_area_id="PRICE_FRONT"
        )
        self.assertTrue(_ctbc_fast_destination_is_valid(
            server_expected,
            "server_assigned_quantity",
            CTBC_TEST_SERVER_QUANTITY_URL,
            server_ids,
        ))
        self.assertFalse(_ctbc_fast_destination_is_valid(
            dict(server_expected, selection_mode="manual"),
            "server_assigned_quantity",
            CTBC_TEST_SERVER_QUANTITY_URL,
            server_ids,
        ))
        self.assertFalse(_ctbc_fast_destination_is_valid(
            server_expected,
            "server_assigned_quantity",
            CTBC_TEST_SERVER_QUANTITY_URL.replace("ATYPE=N", "ATYPE=Y"),
            server_ids,
        ))

        dragons_expected = dict(
            expected,
            performance_id="P16DRAGON",
            product_id="P16MXT0U",
            group_id="GROUP1",
            price_area_id="PRICE1",
            area_url="https://tix.wdragons.com/UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U",
        )
        dragons_url = "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P16DRAGON&GROUP_ID=GROUP1&PERFORMANCE_PRICE_AREA_ID=PRICE1"
        dragons_ids = {
            "performance_id": "P16DRAGON", "product_id": "P16MXT0U",
            "group_id": "GROUP1", "price_area_id": "PRICE1",
        }
        self.assertTrue(_ctbc_fast_destination_is_valid(
            dragons_expected, "seat", dragons_url, dragons_ids
        ))
        self.assertFalse(_ctbc_fast_destination_is_valid(
            dragons_expected, "seat", url, dragons_ids
        ))

    def test_server_quantity_purchase_key_includes_assignment_mode(self):
        key = _ctbc_purchase_state_key(FakeSubmitTab(
            url=CTBC_TEST_SERVER_QUANTITY_URL
        ))
        self.assertIsNotNone(key)
        self.assertEqual("server_assigned_quantity", key[3])
        self.assertEqual("N", key[-1])

    def test_server_quantity_checkout_requires_same_tenant_origin(self):
        checkout = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        self.assertTrue(_ctbc_submit_reached_checkout(
            CTBC_TEST_SERVER_QUANTITY_URL, checkout
        ))
        self.assertFalse(_ctbc_submit_reached_checkout(
            CTBC_TEST_SERVER_QUANTITY_URL,
            "https://tix.wdragons.com/UTK0206_",
        ))

    def test_fast_destination_rejects_noncanonical_origin_route_and_query(self):
        expected = {
            "route": "0205", "performance_id": "P16D0B8Z",
            "product_id": "P16ANF0L", "group_id": "35",
            "price_area_id": "P16D12YO",
            "area_url": (
                "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
                "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
            ),
        }
        ids = {
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO",
        }
        query = (
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&"
            "PERFORMANCE_PRICE_AREA_ID=P16D12YO"
        )
        rejected_urls = (
            f"http://tix.ctbcsports.com/BROTHERS/UTK0205_?{query}",
            f"https://user@tix.ctbcsports.com/BROTHERS/UTK0205_?{query}",
            f"https://tix.ctbcsports.com:444/BROTHERS/UTK0205_?{query}",
            f"https://tix.ctbcsports.com/BROTHERS/nested/UTK0205_?{query}",
            f"https://tix.ctbcsports.com/BROTHERS/UTK0205_suffix?{query}",
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "NEXT=PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&"
            "PERFORMANCE_PRICE_AREA_ID=P16D12YO",
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&PERFORMANCE_ID=OTHER&GROUP_ID=35&"
            "PERFORMANCE_PRICE_AREA_ID=P16D12YO",
        )
        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    _ctbc_fast_destination_is_valid(expected, "seat", url, ids)
                )

        invalid_area_urls = (
            "https://tix.ctbcsports.com/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "https://user@tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_suffix?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=OTHER&PRODUCT_ID=P16ANF0L",
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L&PRODUCT_ID=OTHER",
        )
        valid_url = f"https://tix.ctbcsports.com/BROTHERS/UTK0205_?{query}"
        for area_url in invalid_area_urls:
            with self.subTest(area_url=area_url):
                self.assertFalse(_ctbc_fast_destination_is_valid(
                    dict(expected, area_url=area_url), "seat", valid_url, ids
                ))

    def test_adjacent_seat_selection_uses_cell_indexes(self):
        seats = [
            {"index": 0, "title": "A區-1排-3號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-1號", "row_index": 1, "cell_index": 3, "available": True, "selected": False},
            {"index": 3, "title": "A區-2排-2號", "row_index": 2, "cell_index": 0, "available": True, "selected": False},
            {"index": 4, "title": "A區-2排-1號", "row_index": 2, "cell_index": 1, "available": True, "selected": False},
        ]
        self.assertEqual(
            [0, 1],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="display"),
        )

    def test_rotated_map_groups_vertical_cells_by_actual_row_number(self):
        seats = [
            {"index": 0, "title": "B1區-35排-21號", "row_index": 0, "cell_index": 1, "center_x": 100, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 1, "title": "B1區-35排-20號", "row_index": 1, "cell_index": 1, "center_x": 100, "center_y": 30, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 2, "title": "B1區-34排-20號", "row_index": 1, "cell_index": 2, "center_x": 120, "center_y": 30, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 3, "title": "B1區-24排-4號", "row_index": 2, "cell_index": 12, "center_x": 320, "center_y": 350, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 4, "title": "B1區-24排-3號", "row_index": 3, "cell_index": 12, "center_x": 320, "center_y": 370, "width": 20, "height": 20, "available": True, "selected": False},
        ]
        self.assertEqual(
            [3, 4],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_seat"),
        )
        self.assertEqual(
            [0, 1],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="display"),
        )

    def test_numbered_seats_do_not_bridge_a_visual_aisle(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 0, "cell_index": 0, "center_x": 10, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 0, "cell_index": 2, "center_x": 70, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
        ]
        self.assertEqual(
            [],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_seat"),
        )

    def test_non_adjacent_requires_explicit_opt_in(self):
        seats = [
            {"index": 0, "title": "A區-1排-3號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-1排-1號", "row_index": 1, "cell_index": 2, "available": True, "selected": False},
        ]
        self.assertEqual([], _select_ctbc_seat_indexes(seats, 2, False, "display"))
        self.assertEqual([0, 1], _select_ctbc_seat_indexes(seats, 2, True, "display"))

    def test_seat_number_sorting_overrides_dom_row_and_seat_order(self):
        seats = [
            {"index": 0, "title": "A區-3排-2號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-3排-1號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-10號", "row_index": 2, "cell_index": 0, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-9號", "row_index": 2, "cell_index": 1, "available": True, "selected": False},
            {"index": 4, "title": "A區-1排-2號", "row_index": 3, "cell_index": 0, "available": True, "selected": False},
            {"index": 5, "title": "A區-1排-1號", "row_index": 3, "cell_index": 1, "available": True, "selected": False},
        ]
        self.assertEqual(
            [0, 1],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="display"),
        )
        self.assertEqual(
            [4, 5],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_seat"),
        )

    def test_seat_number_sorting_applies_to_non_adjacent_fallback(self):
        seats = [
            {"index": 0, "title": "A區-3排-10號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-3排-1號", "row_index": 3, "cell_index": 0, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-10號", "row_index": 5, "cell_index": 0, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-1號", "row_index": 7, "cell_index": 0, "available": True, "selected": False},
        ]
        self.assertEqual(
            [3, 2],
            _select_ctbc_seat_indexes(
                seats,
                2,
                allow_non_adjacent=True,
                seat_sorting="row_seat",
            ),
        )
        self.assertEqual(
            [2, 3],
            _select_ctbc_seat_indexes(
                seats,
                2,
                allow_non_adjacent=True,
                seat_sorting="row_asc_seat_desc",
            ),
        )
        self.assertEqual(
            [1, 0],
            _select_ctbc_seat_indexes(
                seats,
                2,
                allow_non_adjacent=True,
                seat_sorting="row_desc_seat_asc",
            ),
        )

    def test_mixed_row_and_seat_number_priorities(self):
        seats = [
            {"index": 0, "title": "A區-3排-10號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-3排-9號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-3排-2號", "row_index": 2, "cell_index": 0, "available": True, "selected": False},
            {"index": 3, "title": "A區-3排-1號", "row_index": 2, "cell_index": 1, "available": True, "selected": False},
            {"index": 4, "title": "A區-1排-10號", "row_index": 3, "cell_index": 0, "available": True, "selected": False},
            {"index": 5, "title": "A區-1排-9號", "row_index": 3, "cell_index": 1, "available": True, "selected": False},
            {"index": 6, "title": "A區-1排-2號", "row_index": 4, "cell_index": 0, "available": True, "selected": False},
            {"index": 7, "title": "A區-1排-1號", "row_index": 4, "cell_index": 1, "available": True, "selected": False},
        ]
        self.assertEqual(
            [6, 7],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_seat"),
        )
        self.assertEqual(
            [4, 5],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_asc_seat_desc"),
        )
        self.assertEqual(
            [2, 3],
            _select_ctbc_seat_indexes(seats, 2, seat_sorting="row_desc_seat_asc"),
        )

    def test_aisle_sorting_skips_front_row_interior_block(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": False, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-4號", "row_index": 1, "cell_index": 3, "available": False, "selected": False},
            {"index": 4, "title": "A區-2排-1號", "row_index": 2, "cell_index": 0, "available": True, "selected": False},
            {"index": 5, "title": "A區-2排-2號", "row_index": 2, "cell_index": 1, "available": True, "selected": False},
            {"index": 6, "title": "A區-2排-3號", "row_index": 2, "cell_index": 2, "available": False, "selected": False},
            {"index": 7, "title": "A區-2排-4號", "row_index": 2, "cell_index": 3, "available": False, "selected": False},
        ]
        self.assertEqual(
            [4, 5],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_accepts_right_edge_block(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": False, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": False, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-4號", "row_index": 1, "cell_index": 3, "available": True, "selected": False},
        ]
        self.assertEqual(
            [2, 3],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_for_one_seat_skips_a_front_row_interior(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": False, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": False, "selected": False},
            {"index": 3, "title": "A區-2排-1號", "row_index": 2, "cell_index": 0, "available": True, "selected": False},
            {"index": 4, "title": "A區-2排-2號", "row_index": 2, "cell_index": 1, "available": False, "selected": False},
        ]
        self.assertEqual(
            [3],
            _select_ctbc_seat_indexes(
                seats, 1, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_counts_a_selected_edge_in_the_full_row(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": False, "selected": True},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": False, "selected": False},
        ]
        self.assertEqual(
            [],
            _select_ctbc_seat_indexes(
                seats, 1, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_uses_dom_order_when_both_edges_are_available(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-4號", "row_index": 1, "cell_index": 3, "available": True, "selected": False},
        ]
        self.assertEqual(
            [0, 1],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_supports_a_rotated_row(self):
        seats = [
            {"index": 0, "title": "B1區-1排-1號", "row_index": 0, "cell_index": 1, "center_x": 100, "center_y": 10, "width": 20, "height": 20, "available": False, "selected": False},
            {"index": 1, "title": "B1區-1排-2號", "row_index": 1, "cell_index": 1, "center_x": 100, "center_y": 30, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 2, "title": "B1區-1排-3號", "row_index": 2, "cell_index": 1, "center_x": 100, "center_y": 50, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 3, "title": "B1區-1排-4號", "row_index": 3, "cell_index": 1, "center_x": 100, "center_y": 70, "width": 20, "height": 20, "available": False, "selected": False},
            {"index": 4, "title": "B1區-2排-1號", "row_index": 0, "cell_index": 2, "center_x": 120, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 5, "title": "B1區-2排-2號", "row_index": 1, "cell_index": 2, "center_x": 120, "center_y": 30, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 6, "title": "B1區-2排-3號", "row_index": 2, "cell_index": 2, "center_x": 120, "center_y": 50, "width": 20, "height": 20, "available": False, "selected": False},
        ]
        self.assertEqual(
            [4, 5],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_does_not_treat_an_internal_gap_as_a_row_end(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "center_x": 10, "center_y": 10, "width": 20, "height": 20, "available": False, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "center_x": 30, "center_y": 10, "width": 20, "height": 20, "available": False, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 3, "center_x": 90, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 3, "title": "A區-1排-4號", "row_index": 1, "cell_index": 4, "center_x": 110, "center_y": 10, "width": 20, "height": 20, "available": True, "selected": False},
            {"index": 4, "title": "A區-1排-5號", "row_index": 1, "cell_index": 5, "center_x": 130, "center_y": 10, "width": 20, "height": 20, "available": False, "selected": False},
            {"index": 5, "title": "A區-1排-6號", "row_index": 1, "cell_index": 6, "center_x": 150, "center_y": 10, "width": 20, "height": 20, "available": False, "selected": False},
        ]
        self.assertEqual(
            [],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_aisle_sorting_stays_strict_when_non_adjacent_is_allowed(self):
        seats = [
            {"index": 0, "title": "A區-1排-1號", "row_index": 1, "cell_index": 0, "available": True, "selected": False},
            {"index": 1, "title": "A區-1排-2號", "row_index": 1, "cell_index": 1, "available": False, "selected": False},
            {"index": 2, "title": "A區-1排-3號", "row_index": 1, "cell_index": 2, "available": False, "selected": False},
            {"index": 3, "title": "A區-1排-4號", "row_index": 1, "cell_index": 3, "available": True, "selected": False},
        ]
        self.assertEqual(
            [],
            _select_ctbc_seat_indexes(
                seats,
                2,
                allow_non_adjacent=True,
                seat_sorting="row_asc_aisle",
            ),
        )

    def test_aisle_sorting_fails_closed_on_an_unparseable_seat_title(self):
        seats = [
            {"index": 0, "title": "座位資料異常", "row_index": 1, "cell_index": 0, "available": False, "selected": False},
            {"index": 1, "title": "A區-1排-1號", "row_index": 1, "cell_index": 1, "available": True, "selected": False},
            {"index": 2, "title": "A區-1排-2號", "row_index": 1, "cell_index": 2, "available": True, "selected": False},
        ]
        self.assertEqual(
            [],
            _select_ctbc_seat_indexes(
                seats, 2, seat_sorting="row_asc_aisle"
            ),
        )

    def test_seat_planner_freezes_identity_and_selects_one_block(self):
        raw = _seat_snapshot()
        snapshot = parse_seat_snapshot(raw)
        raw["seats"][0]["fingerprint"][0] = "mutated"

        plan, reason = select_seat_snapshot(snapshot, "全票", 2)

        self.assertEqual("", reason)
        self.assertIsInstance(plan, SeatActionPlan)
        self.assertEqual("select", plan.action)
        self.assertEqual(("seat", 0, "A區-1排-1號"), plan.target_seat_fingerprints[0])
        self.assertEqual("G1", plan.group_id)
        self.assertEqual("A1", plan.price_area_id)
        self.assertEqual("PRODUCT1", plan.product_id)
        self.assertTrue(plan.require_physical_adjacency)
        self.assertTrue(seat_plan_matches_snapshot(snapshot, plan))
        non_adjacent_plan, reason = select_seat_snapshot(
            snapshot, "全票", 2, allow_non_adjacent=True
        )
        self.assertEqual("", reason)
        self.assertFalse(non_adjacent_plan.require_physical_adjacency)
        self.assertTrue(seat_plan_matches_snapshot(
            snapshot, non_adjacent_plan
        ))
        self.assertFalse(seat_plan_matches_snapshot(
            snapshot, replace(plan, group_id="OTHER")
        ))
        self.assertFalse(seat_plan_matches_snapshot(
            snapshot, replace(plan, product_id="OTHER")
        ))

    def test_seat_planner_distinguishes_type_and_seat_unavailability(self):
        snapshot = parse_seat_snapshot(_seat_snapshot())
        self.assertEqual(
            (None, "ticket-type-unavailable"),
            select_seat_snapshot(snapshot, "敬老票", 2),
        )
        unavailable = _seat_snapshot()
        for seat in unavailable["seats"]:
            seat["available"] = False
        self.assertEqual(
            (None, "seats-unavailable"),
            select_seat_snapshot(unavailable, "全票", 2),
        )

    def test_seat_planner_keyword_selects_the_second_ticket_type(self):
        raw = _seat_snapshot()
        raw["ticket_types"].append({
            "fingerprint": [
                "ticket", "DISABILITY", "setType('DISABILITY')",
            ],
            "text": "身心障礙票及陪同票",
            "disabled": False,
            "type_id": "DISABILITY",
            "type_name": "身心障礙票-NT$200",
            "counter_present": True,
            "counter_raw": "0",
        })
        raw["counters"].append({
            "id": "DISABILITY", "counter_raw": "0",
        })

        plan, reason = select_seat_snapshot(raw, "身心", 2)

        self.assertEqual("", reason)
        self.assertEqual("DISABILITY", plan.ticket_type_id)
        self.assertEqual("身心障礙票-NT$200", plan.ticket_type_name)

    def test_seat_planner_clears_invalid_selection_then_accepts_valid_one(self):
        invalid = _seat_snapshot(
            selected=(0,),
            submitted=[{"A": "S", "Y": "HALF", "Z": "內野半票"}],
        )
        clear_plan, reason = select_seat_snapshot(invalid, "全票", 2)
        self.assertEqual("", reason)
        self.assertEqual("clear", clear_plan.action)
        self.assertTrue(seat_plan_matches_snapshot(invalid, clear_plan))

        ready = _seat_snapshot(selected=(0, 1))
        ready_plan, reason = select_seat_snapshot(ready, "全票", 2)
        self.assertEqual("", reason)
        self.assertEqual("ready", ready_plan.action)
        self.assertTrue(seat_plan_matches_snapshot(ready, ready_plan))

    def test_submit_plan_freezes_only_ready_reserved_seat_intent(self):
        ready = parse_seat_snapshot(_seat_snapshot(selected=(0, 1)))
        ready_plan, reason = select_seat_snapshot(ready, "全票", 2)
        self.assertEqual("", reason)

        submit_plan = build_submit_plan(ready_plan)

        self.assertIsInstance(submit_plan, SubmitPlan)
        self.assertIs(PageKind.RESERVED_SEAT, submit_plan.page_kind)
        self.assertEqual("seat", submit_plan.payload_fingerprint[0])
        self.assertTrue(submit_plan_is_valid(submit_plan))
        self.assertFalse(submit_plan_is_valid(replace(
            submit_plan,
            payload_fingerprint=("seat", "forged"),
        )))

        partial = parse_seat_snapshot(_seat_snapshot(selected=(0,)))
        clear_plan, reason = select_seat_snapshot(partial, "全票", 2)
        self.assertEqual("", reason)
        self.assertEqual("clear", clear_plan.action)
        self.assertIsNone(build_submit_plan(clear_plan))

    def test_submit_plan_preserves_general_quantity_page_kind(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        quantity_plan = select_quantity_snapshot(snapshot, "全票", 2)

        submit_plan = build_submit_plan(quantity_plan)

        self.assertIsInstance(submit_plan, SubmitPlan)
        self.assertIs(PageKind.GENERAL_ADMISSION, submit_plan.page_kind)
        self.assertEqual("quantity", submit_plan.payload_fingerprint[0])
        self.assertTrue(submit_plan_is_valid(submit_plan))

    def test_strict_seat_planner_clears_valid_non_adjacent_selection(self):
        snapshot = parse_seat_snapshot(_seat_snapshot(selected=(0, 2)))

        clear_plan, reason = select_seat_snapshot(snapshot, "全票", 2)

        self.assertEqual("", reason)
        self.assertEqual("clear", clear_plan.action)
        self.assertTrue(clear_plan.require_physical_adjacency)
        self.assertTrue(seat_plan_matches_snapshot(snapshot, clear_plan))
        self.assertFalse(seat_plan_matches_snapshot(
            snapshot,
            replace(clear_plan, require_physical_adjacency=False),
        ))

        ready_plan, reason = select_seat_snapshot(
            snapshot, "全票", 2, allow_non_adjacent=True
        )
        self.assertEqual("", reason)
        self.assertEqual("ready", ready_plan.action)
        self.assertFalse(ready_plan.require_physical_adjacency)
        self.assertTrue(seat_plan_matches_snapshot(snapshot, ready_plan))

    def test_seat_planner_requires_reserved_identity_and_quantity_limit(self):
        missing_limit = _seat_snapshot()
        missing_limit["quantity_limit_present"] = False
        self.assertEqual(
            (None, "invalid-snapshot"),
            select_seat_snapshot(missing_limit, "全票", 2),
        )

        wrong_group = _seat_snapshot()
        wrong_group["group_id"] = "OTHER"
        self.assertEqual(
            (None, "invalid-snapshot"),
            select_seat_snapshot(wrong_group, "全票", 2),
        )

        tracked = _seat_snapshot()
        tracked["page_url"] += "&campaign=opening"
        plan, reason = select_seat_snapshot(tracked, "全票", 2)
        self.assertEqual("", reason)
        self.assertEqual("select", plan.action)

    def test_visible_captcha_is_preferred_over_hidden_dom_match(self):
        candidates = [
            {"image_id": "chk_pic", "visible": False, "order": 0},
            {"image_id": "other_pic", "visible": True, "order": 1},
        ]
        ordered = _order_captcha_candidates(candidates, "chk_pic")
        self.assertEqual("other_pic", ordered[0]["image_id"])

        candidates[0]["visible"] = True
        ordered = _order_captcha_candidates(candidates, "chk_pic")
        self.assertEqual("chk_pic", ordered[0]["image_id"])

    def test_seat_audit_rejects_extra_or_mixed_ticket_types(self):
        target_name = "內野全票-NT$400"
        valid = _seat_snapshot(selected=(0, 1))
        self.assertTrue(seat_selection_is_valid(
            valid, 2, "FULL", target_name
        ))

        extra = _seat_snapshot(selected=(0, 1, 2))
        self.assertFalse(seat_selection_is_valid(
            extra, 2, "FULL", target_name
        ))

        mixed = _seat_snapshot(selected=(0, 1), submitted=[
            {"A": "S", "Y": "FULL", "Z": target_name},
            {"A": "S", "Y": "HALF", "Z": "內野半票"},
        ])
        self.assertFalse(seat_selection_is_valid(
            mixed, 2, "FULL", target_name
        ))

    def test_general_quantity_audit_requires_the_target_and_zeros_others(self):
        self.assertTrue(_ctbc_general_quantity_audit_is_valid(
            [{"key": "FULL", "value": 2}, {"key": "HALF", "value": 0}], "FULL", 2
        ))
        self.assertFalse(_ctbc_general_quantity_audit_is_valid(
            [{"key": "FULL", "value": 2}, {"key": "HALF", "value": 1}], "FULL", 2
        ))
        self.assertFalse(_ctbc_general_quantity_audit_is_valid(
            [{"key": "FULL", "value": 0}, {"key": "HALF", "value": 2}], "FULL", 2
        ))

    def test_quantity_selection_is_frozen_and_fails_closed(self):
        raw = {
            "ok": True,
            "page_url": (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID=&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            ),
            "document_id": "document-1",
            "performance_id": "P1",
            "group_id": "",
            "price_area_id": "A1",
            "performance_hidden_present": True,
            "product_hidden_present": False,
            "group_hidden_present": True,
            "price_area_hidden_present": True,
            "quantity_limit_present": True,
            "quantity_limit_raw": "4",
            "rows": [
                {
                    "key": "FULL", "name": "全票", "text": "全票",
                    "disabled": False, "remaining_present": True,
                    "remaining_raw": "5", "value": "0",
                },
                {
                    "key": "OTHER", "name": "其他票", "text": "其他票",
                    "disabled": False, "remaining_present": True,
                    "remaining_raw": "5", "value": "0",
                },
            ],
        }
        snapshot = parse_quantity_snapshot(raw)
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        self.assertEqual("FULL", plan.target_key)
        self.assertEqual(2, plan.ticket_number)
        self.assertTrue(plan.performance_hidden_present)
        self.assertFalse(plan.product_hidden_present)
        self.assertTrue(plan.quantity_limit_present)
        self.assertTrue(plan.target_remaining_present)
        raw["rows"][0]["key"] = "MUTATED"
        self.assertEqual("FULL", snapshot.rows[0].key)

        malformed = dict(raw, rows=[{
            "key": "FULL", "name": "全票", "text": "全票",
            "disabled": False, "remaining_present": True,
            "remaining_raw": "unknown", "value": "0",
        }])
        self.assertIsNone(select_quantity_snapshot(malformed, "全票", 2))
        duplicate = dict(raw, rows=[raw["rows"][1], raw["rows"][1]])
        self.assertIsNone(select_quantity_snapshot(duplicate, "其他票", 2))
        self.assertIsNone(select_quantity_snapshot(
            dict(raw, quantity_limit_raw="1"), "其他票", 2
        ))

    def test_general_quantity_plan_requires_a_supported_tenant_route(self):
        valid_urls = (
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0202_?"
                "GROUP_ID=&tracking=fixture&PERFORMANCE_PRICE_AREA_ID=A1&"
                "PERFORMANCE_ID=P16D0B8Z"
            ),
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            ),
            (
                "https://tix.wdragons.com/UTK0203_?"
                "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            ),
        )
        for page_url in valid_urls:
            with self.subTest(page_url=page_url):
                raw = make_ctbc_quantity_snapshot(page_url=page_url)
                self.assertIsNotNone(
                    select_quantity_snapshot(raw, "全票", 2)
                )

        invalid_urls = (
            "https://example.com/BROTHERS/UTK0203_?PERFORMANCE_ID=P16D0B8Z",
            "https://tix.ctbcsports.com/UTK0203_?PERFORMANCE_ID=P16D0B8Z",
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z",
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P16D0B8Z#fragment"
            ),
        )
        for page_url in invalid_urls:
            with self.subTest(page_url=page_url):
                raw = make_ctbc_quantity_snapshot(page_url=page_url)
                self.assertIsNone(
                    select_quantity_snapshot(raw, "全票", 2)
                )

    def test_general_quantity_plan_freezes_optional_identity_presence(self):
        raw = make_ctbc_quantity_snapshot()
        raw.update({
            "performance_hidden_present": True,
            "product_hidden_present": True,
            "product_id": "PRODUCT_FIXTURE",
            "group_hidden_present": False,
            "price_area_hidden_present": True,
            "quantity_limit_present": True,
        })
        plan = select_quantity_snapshot(raw, "全票", 2)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.performance_hidden_present)
        self.assertTrue(plan.product_hidden_present)
        self.assertFalse(plan.group_hidden_present)
        self.assertTrue(plan.price_area_hidden_present)
        self.assertTrue(plan.quantity_limit_present)
        self.assertTrue(plan.target_remaining_present)

        raw["product_id"] = ""
        self.assertIsNone(select_quantity_snapshot(raw, "全票", 2))
