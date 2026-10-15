import types
import unittest
from unittest.mock import patch

from tests.ctbc_support import (
    CTBC_TEST_PERFORMANCE_ID,
    config,
    make_ctbc_area_snapshot,
)

from nodriver_common import _order_captcha_candidates, create_ocr_for_platform
from platforms.ctbc import (
    _build_ctbc_destination_url,
    _choose_candidate_index,
    _classify_ctbc_date_action,
    _classify_ctbc_submit_dialog,
    _ctbc_availability_has_tickets,
    _ctbc_fast_destination_is_valid,
    _ctbc_general_quantity_audit_is_valid,
    _ctbc_seat_audit_is_valid,
    _get_ctbc_page_type,
    _normalize_ctbc_captcha_answer,
    _parse_ctbc_date_target,
    _parse_ctbc_map_send,
    _parse_ctbc_map_send_a,
    _parse_ctbc_map_title,
    _parse_ctbc_vip_date_action,
    _parse_keyword_groups,
    _select_ctbc_area_snapshot,
    _select_ctbc_seat_indexes,
    is_utk_sports_url,
    parse_ctbc_cookie_string,
)
from settings import get_default_config, migrate_config


class CtbcPureSelectionTest(unittest.TestCase):
    def test_seat_sorting_setting_defaults_and_migrates_to_number_order(self):
        default = get_default_config()
        self.assertEqual("row_seat", default["ctbc"]["seat_sorting"])

        old_config = {"ctbc": {"ticket_type_keyword": "全票"}}
        migrated = migrate_config(old_config)
        self.assertEqual("row_seat", migrated["ctbc"]["seat_sorting"])

        migrated["ctbc"]["seat_sorting"] = "unsupported"
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

    def test_page_routing(self):
        base = "https://tix.ctbcsports.com/BROTHERS/"
        self.assertEqual("date", _get_ctbc_page_type(base + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual("area", _get_ctbc_page_type(base + "UTK0204_?PERFORMANCE_ID=P1"))
        self.assertEqual("seat", _get_ctbc_page_type(base + "UTK0205_?PERFORMANCE_ID=P1"))
        self.assertEqual("general_admission", _get_ctbc_page_type(base + "UTK0203_?PERFORMANCE_ID=P1"))
        self.assertEqual("checkout", _get_ctbc_page_type(base + "UTK0206_"))
        self.assertIsNone(_get_ctbc_page_type("https://ticket.ibon.com.tw/UTK0201_?PRODUCT_ID=P1"))

        dragons = "https://tix.wdragons.com/"
        self.assertTrue(is_utk_sports_url(dragons + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual("date", _get_ctbc_page_type(dragons + "UTK0201_?PRODUCT_ID=P1"))
        self.assertEqual("area", _get_ctbc_page_type(dragons + "UTK0204_?PERFORMANCE_ID=P1"))
        self.assertEqual("seat", _get_ctbc_page_type(dragons + "UTK0205_?PERFORMANCE_ID=P1"))

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

    def test_date_target_parser_only_accepts_the_same_tenant_and_product(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        target = _parse_ctbc_date_target(onclick, source)
        self.assertEqual("P16D0B8Z", target["performance_id"])
        self.assertEqual("P16ANF0L", target["product_id"])
        self.assertIsNone(_parse_ctbc_date_target(
            "location.href='https://evil.example/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
            source,
        ))
        self.assertIsNone(_parse_ctbc_date_target(
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=OTHER'",
            source,
        ))
        self.assertIsNone(_parse_ctbc_date_target(
            "location.href='https://tix.ctbcsports.com:444/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
            source,
        ))
        self.assertIsNone(_parse_ctbc_date_target(
            "location.href='https://user@tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'",
            source,
        ))

        dragons_source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        dragons_target = _parse_ctbc_date_target(
            "location.href='UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'",
            dragons_source,
        )
        self.assertEqual("tix.wdragons.com", dragons_target["host"])
        self.assertEqual("WDRAGONS", dragons_target["map_namespace"])
        self.assertFalse(dragons_target["fast_area_prefetch"])
        self.assertIsNone(_parse_ctbc_date_target(
            "location.href='https://tix.ctbcsports.com/UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'",
            dragons_source,
        ))

    def test_date_action_classifier_supports_vip_and_safe_structural_fallback(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0P&STARTDATE=2026/09/18"
        )
        vip = _parse_ctbc_vip_date_action("VipSellCheck('P1DN7WJI');", source)
        self.assertEqual("vip_native", vip["kind"])
        self.assertEqual("P1DN7WJI", vip["token"])
        self.assertEqual(
            "structural_native",
            _classify_ctbc_date_action("DragonMemberCheck('P1DRAGON')", source)["kind"],
        )
        self.assertEqual(
            "structural_native",
            _classify_ctbc_date_action("", source)["kind"],
        )
        self.assertIsNone(_classify_ctbc_date_action(
            "location.href='https://evil.example/'", source
        ))
        self.assertIsNone(_classify_ctbc_date_action(
            "DragonMemberCheck('P1'); alert('x')", source
        ))

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
        self.assertEqual("a35", target["rel"])
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
                    self.assertEqual("front", target["rel"])
                    if require_direct_destination:
                        self.assertEqual("PRICE_FRONT", target["destination"]["price_area_id"])

    def test_area_selection_rejects_unavailable_status_from_each_source(self):
        unavailable_by_source = (
            ("row", "front_row_status", "售完"),
            ("title", "front_title_status", "0"),
            ("action", "front_action_status", "1"),
        )
        for source, argument, unavailable in unavailable_by_source:
            with self.subTest(source=source):
                statuses = {
                    "front_row_status": "2",
                    "front_title_status": "2",
                    "front_action_status": "2",
                    argument: unavailable,
                }
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
        self.assertIsNone(native_target["destination"])
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

        general_expected = dict(expected, route="0202", group_id="", price_area_id="P16D12ZY")
        general_url = "https://tix.ctbcsports.com/BROTHERS/UTK0203_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=P16D12ZY"
        general_ids = dict(ids, group_id="", price_area_id="P16D12ZY")
        self.assertTrue(_ctbc_fast_destination_is_valid(
            general_expected, "general_admission", general_url, general_ids
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
        target = {"id": "FULL", "name": "內野全票-NT$400"}
        good_seat = {"A": "S", "Y": "FULL", "Z": target["name"]}
        valid = {
            "submitted_seats": [good_seat, dict(good_seat)],
            "counters": [{"id": "FULL", "count": 2}, {"id": "HALF", "count": 0}],
            "visual_count": 2,
        }
        self.assertTrue(_ctbc_seat_audit_is_valid(valid, 2, target))

        extra = dict(valid, submitted_seats=valid["submitted_seats"] + [dict(good_seat)], visual_count=3)
        self.assertFalse(_ctbc_seat_audit_is_valid(extra, 2, target))

        mixed = dict(valid, submitted_seats=[good_seat, {"A": "S", "Y": "HALF", "Z": "內野半票"}])
        self.assertFalse(_ctbc_seat_audit_is_valid(mixed, 2, target))

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
