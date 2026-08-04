import sys
import types
import unittest
import base64
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def install_zendriver_stub():
    try:
        __import__("zendriver")
        __import__("zendriver.core.config", fromlist=["Config"])
        return
    except ModuleNotFoundError as exc:
        if exc.name != "zendriver":
            raise

    zendriver = types.ModuleType("zendriver")
    zendriver.cdp = types.SimpleNamespace()
    core = types.ModuleType("zendriver.core")
    config = types.ModuleType("zendriver.core.config")
    config.Config = type("Config", (), {})
    core.config = config
    sys.modules.setdefault("zendriver", zendriver)
    sys.modules.setdefault("zendriver.core", core)
    sys.modules.setdefault("zendriver.core.config", config)


install_zendriver_stub()

from platforms.ctbc import (  # noqa: E402
    CTBC_AREA_ROW_SELECTOR,
    CTBC_DATE_ROW_SELECTOR,
    _ctbc_captcha_and_submit,
    _ctbc_check_captcha_error,
    _ctbc_attest_fast_destination,
    _ctbc_general_quantity_audit_is_valid,
    _get_ctbc_captcha_image_bytes,
    _build_ctbc_destination_url,
    _ctbc_fast_destination_is_valid,
    _ctbc_native_map_href_is_allowed,
    _ctbc_seat_audit_is_valid,
    _ctbc_tab_identity,
    _ctbc_tab_state_key,
    _choose_candidate_index,
    _ctbc_availability_has_tickets,
    _get_ctbc_page_type,
    _parse_keyword_groups,
    _normalize_ctbc_captcha_answer,
    _parse_ctbc_date_target,
    _parse_ctbc_map_send,
    _parse_ctbc_map_title,
    _select_ctbc_area_snapshot,
    _select_ctbc_seat_indexes,
    _state,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_ticket_type_auto_select,
    is_utk_sports_url,
    parse_ctbc_cookie_string,
)
from nodriver_common import _order_captcha_candidates, create_ocr_for_platform  # noqa: E402
from settings import get_default_config, migrate_config  # noqa: E402


def config(date_keyword='"9/10"', area_keyword='"A"'):
    return {
        "ticket_number": 2,
        "keyword_exclude": '"身障","輪椅"',
        "date_auto_fallback": False,
        "area_auto_fallback": False,
        "date_auto_select": {
            "enable": True,
            "date_keyword": date_keyword,
            "mode": "from top to bottom",
        },
        "area_auto_select": {
            "enable": True,
            "area_keyword": area_keyword,
            "mode": "from top to bottom",
        },
        "ctbc": {
            "ticket_type_keyword": "全票",
            "seat_sorting": "row_seat",
        },
        "advanced": {
            "verbose": False,
            "auto_reload_page_interval": 0,
            "disable_adjacent_seat": False,
        },
    }


class FakeElement:
    def __init__(self, html="", *, children=None, child_lists=None, disabled=False, attrs=None):
        self.html = html
        self.children = children or {}
        self.child_lists = child_lists or {}
        self.disabled = disabled
        self.attrs = attrs or {}
        self.clicked = False
        self.click_count = 0

    async def get_html(self):
        return self.html

    async def query_selector(self, selector):
        return self.children.get(selector)

    async def query_selector_all(self, selector):
        return self.child_lists.get(selector, [])

    async def apply(self, script):
        if "el.disabled" in script:
            return self.disabled
        if "getAttribute" in script and "rel" in script:
            return self.attrs.get("rel", "")
        if "getAttribute" in script and "onclick" in script:
            return self.attrs.get("onclick", "")
        if "el.id" in script:
            return self.attrs.get("id", "")
        if "getAttribute" in script and '"val"' in script:
            return self.attrs.get("val", "")
        if "el.click" in script:
            self.clicked = True
            self.click_count += 1
            return True
        return None

    async def click(self):
        self.clicked = True
        self.click_count += 1


class FakeTab:
    def __init__(self, selector, elements, map_area=None, url="", evaluate_results=None):
        self.selector = selector
        self.elements = elements
        self.map_area = map_area
        self.target = types.SimpleNamespace(url=url)
        self.evaluate_results = list(evaluate_results or [])
        self.evaluate_count = 0
        self.last_selector = None
        self.reloaded = False

    async def query_selector_all(self, selector):
        self.last_selector = selector
        return self.elements if selector == self.selector else []

    async def query_selector(self, selector):
        if selector.startswith("map#mapdata area#"):
            return self.map_area
        return None

    async def evaluate(self, _script, **_kwargs):
        self.evaluate_count += 1
        return self.evaluate_results.pop(0) if self.evaluate_results else None

    async def sleep(self, _seconds):
        return None

    async def reload(self):
        self.reloaded = True


class FakeSubmitTab:
    def __init__(self, captcha="ABCD", dialog="成功加入購物車"):
        self.target = types.SimpleNamespace(url="https://tix.ctbcsports.com/BROTHERS/UTK0205_?P=1")
        self.captcha = captcha
        self.dialog = dialog
        self.button = FakeElement("<button id='addcart'>加入購物車</button>")

    async def evaluate(self, script):
        if "#LOGIN_ID" in script:
            if "JSON.stringify" in script:
                return '{"account":"user","password":"secret"}'
            return {"required": False}
        if "#CHK" in script:
            return self.captcha
        if "#dialog-message" in script:
            return self.dialog
        return None

    async def query_selector(self, selector):
        return self.button if selector == "button#addcart" else None

    async def sleep(self, _seconds):
        return None


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
            "display", "row_seat", "row_asc_seat_desc", "row_desc_seat_asc"
        ):
            configured = {"ctbc": {"seat_sorting": seat_sorting}}
            self.assertEqual(
                seat_sorting,
                migrate_config(configured)["ctbc"]["seat_sorting"],
            )

    def test_captcha_answer_is_ascii_only_and_uppercase(self):
        self.assertEqual("2CHH", _normalize_ctbc_captcha_answer(" 2C-HH\n"))
        self.assertEqual("WTYF", _normalize_ctbc_captcha_answer("wtyf"))

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
        self.assertTrue(_ctbc_native_map_href_is_allowed(href, "P16D0B8Z"))
        self.assertFalse(_ctbc_native_map_href_is_allowed(href + ";alert(1)", "P16D0B8Z"))
        send_a = "javascript:SendA('0205','P16D0B8Z','35','P16D12YO','AG1','AI1','熱賣中');"
        self.assertTrue(_ctbc_native_map_href_is_allowed(send_a, "P16D0B8Z"))
        self.assertFalse(_ctbc_native_map_href_is_allowed(send_a + "alert(1)", "P16D0B8Z"))
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

    def test_map_title_is_a_second_area_price_and_status_fingerprint(self):
        self.assertEqual(
            {"area": "內野南A區下層", "price": "400", "status": "熱賣中"},
            _parse_ctbc_map_title("票區:內野南A區下層 票價：400 尚餘：熱賣中"),
        )
        self.assertIsNone(_parse_ctbc_map_title("內野南A區下層 400 熱賣中"))

    def test_prefetched_area_requires_consistent_row_and_map_availability(self):
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
        target = _select_ctbc_area_snapshot(snapshot, config(), "P16D0B8Z", strict_map=True)
        self.assertEqual("a35", target["rel"])
        snapshot["areas"][0]["href"] = "javascript:Send('0205','P16D0B8Z','P16D12YO','35','0')"
        self.assertIsNone(_select_ctbc_area_snapshot(snapshot, config(), "P16D0B8Z", strict_map=True))
        snapshot["areas"][0]["href"] = "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')"
        snapshot["areas"][0]["title"] = "票區:內野南B區下層 票價：400 尚餘：熱賣中"
        self.assertIsNone(_select_ctbc_area_snapshot(snapshot, config(), "P16D0B8Z", strict_map=True))

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


class CtbcDomFlowTest(unittest.IsolatedAsyncioTestCase):
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
        available_button = FakeElement("<button id='buy_btn'>購買</button>")
        sold_button = FakeElement("<button id='buy_btn' disabled>已售完</button>", disabled=True)
        available_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": available_button},
        )
        sold_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>家庭席</td><td><button disabled>已售完</button></td></tr>",
            children={"button#buy_btn": sold_button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [available_row, sold_row])

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertEqual(CTBC_DATE_ROW_SELECTOR, tab.last_selector)
        self.assertTrue(available_button.clicked)
        self.assertFalse(sold_button.clicked)

    async def test_date_fast_path_prefetches_one_area_and_skips_area_page_click(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>購買</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        snapshot = json.dumps({
            "ok": True,
            "elapsed_ms": 120,
            "map_url": "https://imgs2.utiki.com.tw/Data/CTBC_SPORTS/Images/Temp/P16D0B8Z/1_P16D0B8Z_live.map?v=12345678",
            "rows": [{"area": "內野南A區下層", "price": "400", "status": "熱賣中", "rel": "a35"}],
            "areas": [{"id": "a35", "href": "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')", "title": "票區:內野南A區下層 票價：400 尚餘：熱賣中"}],
        })
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source, evaluate_results=[snapshot, True])
        try:
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
            self.assertFalse(button.clicked)
            self.assertEqual(2, tab.evaluate_count)
            expected = _state["fast_target"][_ctbc_tab_identity(tab)]
            self.assertEqual("P16D12YO", expected["price_area_id"])
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

    async def test_area_uses_sale_rows_and_skips_sold_out_match(self):
        url = "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
        snapshot = json.dumps({
            "ok": True,
            "page_url": url,
            "rows": [
                {"area": "內野南A區下層", "price": "400", "status": "售完", "rel": "a1"},
                {"area": "內野南A區上層", "price": "400", "status": "熱賣中", "rel": "a35"},
            ],
            "areas": [
                {"id": "a1", "href": "javascript:Send('0205','P16D0B8Z','P16D12YX','1','0')", "title": "票區:內野南A區下層 票價：400 尚餘：0"},
                {"id": "a35", "href": "javascript:Send('0205','P16D0B8Z','P16D12YO','35','熱賣中')", "title": "票區:內野南A區上層 票價：400 尚餘：熱賣中"},
            ],
        })
        tab = FakeTab(CTBC_AREA_ROW_SELECTOR, [], url=url, evaluate_results=[snapshot, True])

        self.assertTrue(await nodriver_ctbc_area_auto_select(tab, config()))
        self.assertEqual(2, tab.evaluate_count)

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
        tab = FakeSubmitTab()
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertTrue(tab.button.clicked)

    async def test_captcha_error_clears_per_tab_submit_guard(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        submit_key = _ctbc_tab_state_key(tab)
        _state["submitted"][submit_key] = 1.0

        async def query_selector(_selector):
            return None

        tab.query_selector = query_selector
        self.assertTrue(await _ctbc_check_captcha_error(tab, config()))
        self.assertNotIn(submit_key, _state["submitted"])

    async def test_auth_error_blocks_retry_until_credentials_change(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)


if __name__ == "__main__":
    unittest.main()
