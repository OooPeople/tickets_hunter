import asyncio
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
    CTBC_DATE_PURCHASE_CONTROL_SELECTOR,
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
    _classify_ctbc_date_action,
    _classify_utk_document_state,
    _choose_candidate_index,
    _ctbc_availability_has_tickets,
    _get_ctbc_page_type,
    _parse_keyword_groups,
    _normalize_ctbc_captcha_answer,
    _parse_ctbc_date_target,
    _parse_ctbc_vip_date_action,
    _parse_ctbc_map_send,
    _parse_ctbc_map_title,
    _pop_reload_request_if_token,
    _inspect_utk_document,
    _finish_utk_recovery_probe,
    _utk_document_guard,
    _select_ctbc_area_snapshot,
    _select_ctbc_seat_indexes,
    _state,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_main,
    nodriver_ctbc_ticket_type_auto_select,
    nodriver_utk_sports_cancel_refresh,
    nodriver_utk_sports_consume_reload_event,
    nodriver_utk_sports_reload,
    nodriver_utk_sports_reload_wait,
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
    def __init__(
        self,
        html="",
        *,
        children=None,
        child_lists=None,
        disabled=False,
        attrs=None,
        visible=True,
        connected=True,
        in_purchase_cell=True,
        native_commit_allowed=True,
        tag_name="BUTTON",
        commit_attrs=None,
        commit_tag_name=None,
    ):
        self.html = html
        self.children = children or {}
        self.child_lists = child_lists or {}
        self.disabled = disabled
        self.attrs = attrs or {}
        self.visible = visible
        self.connected = connected
        self.in_purchase_cell = in_purchase_cell
        self.native_commit_allowed = native_commit_allowed
        self.tag_name = tag_name
        self.commit_attrs = commit_attrs
        self.commit_tag_name = commit_tag_name
        self._date_snapshot_fingerprint = None
        self.clicked = False
        self.click_count = 0

    async def get_html(self):
        return self.html

    async def query_selector(self, selector):
        return self.children.get(selector)

    async def query_selector_all(self, selector):
        return self.child_lists.get(selector, [])

    async def apply(self, script):
        if "__ticketsHunterDateControlSnapshot" in script:
            aria_disabled = self.attrs.get("aria-disabled") == "true"
            actionable = (
                self.visible and self.connected and self.in_purchase_cell
                and not self.disabled and not aria_disabled
            )
            fingerprint = {
                "tag_name": str(self.tag_name or "").upper(),
                "href": self.attrs.get("href", ""),
                "resolved_href": self.attrs.get(
                    "resolved_href", self.attrs.get("href", "")
                ),
                "role": self.attrs.get("role", ""),
                "type": self.attrs.get("type", ""),
                "target": self.attrs.get("target", ""),
                "download": self.attrs.get("download", ""),
                "ping": self.attrs.get("ping", ""),
                "formaction": self.attrs.get("formaction", ""),
                "has_form": bool(self.attrs.get("has_form", False)),
                "onclick": self.attrs.get("onclick", ""),
            }
            self._date_snapshot_fingerprint = dict(fingerprint)
            return json.dumps({
                "actionable": actionable,
                "connected": self.connected,
                "visible": self.visible,
                "disabled": self.disabled or aria_disabled,
                "in_purchase_cell": self.in_purchase_cell,
                **fingerprint,
                "text": self.html,
            })
        if "__ticketsHunterCommitNativeDateAction" in script:
            if self.commit_attrs:
                self.attrs.update(self.commit_attrs)
            if self.commit_tag_name is not None:
                self.tag_name = self.commit_tag_name
            aria_disabled = self.attrs.get("aria-disabled") == "true"
            current_fingerprint = {
                "tag_name": str(self.tag_name or "").upper(),
                "href": self.attrs.get("href", ""),
                "resolved_href": self.attrs.get(
                    "resolved_href", self.attrs.get("href", "")
                ),
                "role": self.attrs.get("role", ""),
                "type": self.attrs.get("type", ""),
                "target": self.attrs.get("target", ""),
                "download": self.attrs.get("download", ""),
                "ping": self.attrs.get("ping", ""),
                "formaction": self.attrs.get("formaction", ""),
                "has_form": bool(self.attrs.get("has_form", False)),
                "onclick": self.attrs.get("onclick", ""),
            }
            if not (
                self.native_commit_allowed and self.visible and self.connected
                and self.in_purchase_cell and not self.disabled and not aria_disabled
                and current_fingerprint == self._date_snapshot_fingerprint
            ):
                return False
            self.clicked = True
            self.click_count += 1
            return True
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
        self.evaluate_scripts = []
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
        self.evaluate_scripts.append(_script)
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
            "display", "row_seat", "row_asc_aisle",
            "row_asc_seat_desc", "row_desc_seat_asc"
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

    async def test_date_fast_path_prefetches_one_area_and_skips_area_page_click(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement("<button id='buy_btn'>立即選位》</button>", attrs={"onclick": onclick})
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>立即選位》</button></td></tr>",
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
            self.assertNotIn("includes('購買')", tab.evaluate_scripts[-1])
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


class CtbcSharedRefreshTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.global_settings = {
            "utk_sports": {
                "enabled": True,
                "min_interval_seconds": 1.5,
                "initial_cooldown_seconds": 60,
                "max_cooldown_seconds": 240,
            }
        }
        marker_patch = patch("platforms.ctbc._mark_utk_emergency_window")
        remaining_patch = patch(
            "platforms.ctbc._utk_emergency_remaining", return_value=0.0
        )
        marker_patch.start()
        remaining_patch.start()
        self.addCleanup(marker_patch.stop)
        self.addCleanup(remaining_patch.stop)

    @staticmethod
    def coordinator_for(decision, *, report=None):
        reservation = types.SimpleNamespace(
            reserved=bool(decision.granted),
            status="reserved" if decision.granted else decision.status,
            retry_after_seconds=getattr(decision, "retry_after_seconds", 0.0),
            reservation_id="reservation-1" if decision.granted else "",
            is_recovery_probe=getattr(decision, "is_recovery_probe", False),
            cooldown_until=getattr(decision, "cooldown_until", 0.0),
            error=getattr(decision, "error", ""),
        )
        return types.SimpleNamespace(
            cancel_request=AsyncMock(return_value=False),
            reserve_refresh=AsyncMock(return_value=reservation),
            commit_refresh=AsyncMock(return_value=decision),
            release_refresh=AsyncMock(return_value=True),
            report_rate_limited=AsyncMock(return_value=report),
        )

    def test_rate_limit_classifier_requires_code_message_and_no_normal_root(self):
        self.assertEqual(
            "blocked",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "429 - 請求頻率過高 HTTP 429 - Too Many Requests",
                "has_normal_root": False,
            }),
        )
        self.assertEqual(
            "normal",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "票價 429 元",
                "has_normal_root": True,
            }),
        )
        self.assertEqual(
            "unknown",
            _classify_utk_document_state({
                "ready": False,
                "body_text": "429 - 請求頻率過高",
                "has_normal_root": False,
            }),
        )
        self.assertEqual(
            "unknown",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "一般錯誤代碼 429",
                "has_normal_root": False,
            }),
        )

    async def test_document_inspection_classifies_the_dom_snapshot(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [],
            url=source,
            evaluate_results=[json.dumps({
                "ready": True,
                "body_text": "429 - 請求頻率過高 HTTP 429 - Too Many Requests",
                "document_id": source + "|123",
                "url": source,
                "ready_state": "complete",
                "has_normal_root": False,
            })],
        )
        snapshot = await _inspect_utk_document(tab)
        self.assertEqual("blocked", snapshot["classification"])
        self.assertEqual(source + "|123", snapshot["document_id"])

    async def test_429_guard_runs_before_date_dispatch_and_reports_once(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|429",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=0.25,
                cooldown_until=time.time() + 60,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            ),
        )
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator), \
                    patch("platforms.ctbc.nodriver_ctbc_date_auto_select", AsyncMock()) as select:
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            select.assert_not_awaited()
            self.assertEqual(1, coordinator.report_rate_limited.await_count)
        finally:
            _state["rate_limit_observation"].pop(identity, None)
            _state["reload_request"].pop(identity, None)
            _state["recovery_probe"].pop(identity, None)
            _state["reload_event"].discard(identity)

    async def test_cooldown_expiry_grants_one_reload_of_the_blocked_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|429",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="probe-reload",
                is_recovery_probe=True,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            ),
        )
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            self.assertTrue(tab.reloaded)
            self.assertEqual(
                "probe-reload", _state["recovery_probe"][identity]["permit_id"]
            )
            self.assertEqual(1, coordinator.report_rate_limited.await_count)
        finally:
            _state["rate_limit_observation"].pop(identity, None)
            _state["reload_request"].pop(identity, None)
            _state["recovery_probe"].pop(identity, None)
            _state["reload_event"].discard(identity)

    async def test_shared_permit_is_required_before_programmatic_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        decision = types.SimpleNamespace(
            granted=True,
            status="granted",
            retry_after_seconds=0.0,
            permit_id="permit-1",
            is_recovery_probe=False,
            error="",
        )
        coordinator = self.coordinator_for(decision)
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "scheduled refresh",
                    apply_local_interval=False,
                )
            self.assertTrue(reloaded)
            self.assertTrue(tab.reloaded)
            self.assertTrue(nodriver_utk_sports_consume_reload_event(tab))
            self.assertFalse(nodriver_utk_sports_consume_reload_event(tab))
            self.assertEqual(
                1.5,
                coordinator.reserve_refresh.await_args.kwargs["min_interval_seconds"],
            )
            coordinator.commit_refresh.assert_awaited_once()
        finally:
            _state["reload_request"].pop(identity, None)
            _state["recovery_probe"].pop(identity, None)

    async def test_reload_orders_reserve_preflight_commit_then_network(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        events = []

        async def reserve(*_args, **_kwargs):
            events.append("reserve")
            return types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-order",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )

        async def inspect(*_args, **_kwargs):
            events.append("preflight")
            return snapshot

        async def commit(*_args, **_kwargs):
            events.append("commit")
            return types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="permit-order",
                is_recovery_probe=False,
                error="",
            )

        async def reload():
            events.append("reload")
            tab.reloaded = True

        tab.reload = reload
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(side_effect=reserve),
            commit_refresh=AsyncMock(side_effect=commit),
            release_refresh=AsyncMock(return_value=True),
        )
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(side_effect=inspect)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertTrue(await nodriver_utk_sports_reload(
                    tab, config(), "ordered reload", apply_local_interval=False
                ))
            self.assertEqual(["reserve", "preflight", "commit", "reload"], events)
        finally:
            _state["reload_request"].pop(identity, None)
            _state["reload_event"].discard(identity)

    async def test_incident_during_preflight_revokes_reload_at_commit(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-revoked",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )),
            commit_refresh=AsyncMock(return_value=types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=60.0,
                cooldown_until=time.time() + 60,
                error="",
            )),
            release_refresh=AsyncMock(return_value=False),
        )
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(await nodriver_utk_sports_reload(
                    tab, config(), "revoked reload", apply_local_interval=False
                ))
            coordinator.commit_refresh.assert_awaited_once()
            self.assertFalse(tab.reloaded)
        finally:
            _state["reload_request"].pop(identity, None)

    async def test_cancelled_preflight_releases_its_reservation(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-cancelled",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )),
            release_refresh=AsyncMock(return_value=True),
        )
        identity = _ctbc_tab_identity(tab)
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(side_effect=asyncio.CancelledError)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                with self.assertRaises(asyncio.CancelledError):
                    await nodriver_utk_sports_reload(
                        tab, config(), "cancelled preflight", apply_local_interval=False
                    )
            coordinator.release_refresh.assert_awaited_once()
            self.assertFalse(tab.reloaded)
        finally:
            _state["reload_request"].pop(identity, None)

    def test_old_coroutine_cannot_pop_a_new_local_reload_request(self):
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [])
        identity = _ctbc_tab_identity(tab)
        _state["reload_request"][identity] = {"request_token": "new-token"}
        try:
            self.assertIsNone(
                _pop_reload_request_if_token(identity, "old-token")
            )
            self.assertEqual(
                "new-token",
                _state["reload_request"][identity]["request_token"],
            )
        finally:
            _state["reload_request"].pop(identity, None)

    async def test_reload_wait_has_a_bounded_fail_closed_deadline(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        inner_reload = AsyncMock(return_value=True)
        with patch("platforms.ctbc.UTK_RELOAD_WAIT_MAX_SECONDS", 0.0), \
                patch("platforms.ctbc.time.monotonic", return_value=100.0), \
                patch("platforms.ctbc.nodriver_utk_sports_reload", inner_reload), \
                patch("platforms.ctbc.nodriver_utk_sports_cancel_refresh", AsyncMock(return_value=True)) as cancel, \
                patch("platforms.ctbc.check_and_handle_pause", AsyncMock(return_value=False)), \
                patch("platforms.ctbc.check_and_handle_quit", AsyncMock(return_value=False)):
            self.assertFalse(await nodriver_utk_sports_reload_wait(
                tab, config(), "bounded startup reload"
            ))
        cancel.assert_awaited_once()
        inner_reload.assert_not_awaited()

    async def test_old_429_follower_reloads_without_reopening_the_incident(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|old-429",
            "url": source,
        }
        _state["rate_limit_observation"][identity] = snapshot["document_id"]
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="paced-follower",
                is_recovery_probe=False,
                error="",
            )
        )
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "post-recovery follower",
                    apply_local_interval=False,
                    allow_blocked_recovery=True,
                )
            self.assertTrue(reloaded)
            coordinator.report_rate_limited.assert_not_awaited()
        finally:
            _state["rate_limit_observation"].pop(identity, None)
            _state["reload_request"].pop(identity, None)
            _state["recovery_probe"].pop(identity, None)
            _state["reload_event"].discard(identity)

    async def test_probe_429_passes_its_permit_for_backoff_escalation(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        _state["recovery_probe"][identity] = {
            "permit_id": "probe-1",
            "source_document_id": source + "|old",
        }
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|new",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=0.25,
                cooldown_until=time.time() + 120,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=2,
                retry_after_seconds=120.0,
                error="",
            ),
        )
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            self.assertEqual(
                "probe-1",
                coordinator.report_rate_limited.await_args.kwargs["probe_permit_id"],
            )
            self.assertNotIn(identity, _state["recovery_probe"])
        finally:
            _state["rate_limit_observation"].pop(identity, None)
            _state["reload_request"].pop(identity, None)
            _state["recovery_probe"].pop(identity, None)

    async def test_probe_lease_is_renewed_until_document_is_classified(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        _state["recovery_probe"][identity] = {
            "permit_id": "probe-renew",
            "source_document_id": source + "|old",
            "started_at": 100.0,
            "next_renew_at": 101.0,
        }
        snapshot = {
            "classification": "unknown",
            "document_id": source + "|old",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            renew_recovery_probe=AsyncMock(return_value=True),
        )
        try:
            with patch("platforms.ctbc.time.monotonic", return_value=106.0), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(
                    await _finish_utk_recovery_probe(tab, config(), snapshot)
                )
            coordinator.renew_recovery_probe.assert_awaited_once_with(
                "utk-sports", "probe-renew"
            )
            self.assertEqual(
                111.0, _state["recovery_probe"][identity]["next_renew_at"]
            )
        finally:
            _state["recovery_probe"].pop(identity, None)

    async def test_pause_abandons_owned_probe_without_marking_recovery_success(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        _state["recovery_probe"][identity] = {
            "permit_id": "probe-pause",
            "source_document_id": source + "|old",
        }
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True,
                recovery_required=True,
                error="",
            )),
        )
        with patch(
            "platforms.ctbc.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertTrue(
                await nodriver_utk_sports_cancel_refresh(
                    tab,
                    "paused",
                    wait=True,
                    abandon_probe=True,
                )
            )
        self.assertFalse(
            coordinator.complete_recovery_probe.await_args.kwargs["success"]
        )
        self.assertNotIn(identity, _state["recovery_probe"])

    async def test_reenabled_switch_prevents_legacy_uncoordinated_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        disabled = {"utk_sports": dict(self.global_settings["utk_sports"], enabled=False)}
        with patch(
            "platforms.ctbc.load_global_settings",
            side_effect=[disabled, self.global_settings],
        ), patch(
            "platforms.ctbc.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.ctbc.check_and_handle_quit",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(
                await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "switch changed",
                    apply_local_interval=False,
                )
            )
        self.assertFalse(tab.reloaded)

    async def test_persistent_unknown_document_never_opens_shared_cooldown(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        snapshot = {
            "classification": "unknown",
            "document_id": source + "|unknown",
            "document_started_at": 1000.0,
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            report_rate_limited=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            )),
        )
        try:
            with patch("platforms.ctbc.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.ctbc._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.ctbc.get_shared_refresh_coordinator", return_value=coordinator), \
                    patch("platforms.ctbc.time.monotonic", side_effect=[100.0, 100.0, 111.0, 111.0, 111.0]):
                first, _ = await _utk_document_guard(tab, config(), source)
                second, _ = await _utk_document_guard(tab, config(), source)
            self.assertEqual("unknown", first)
            self.assertEqual("unknown", second)
            coordinator.report_rate_limited.assert_not_awaited()
        finally:
            _state["unknown_document"].pop(identity, None)
            _state["rate_limit_observation"].pop(identity, None)
            _state["last_document"].pop(identity, None)

if __name__ == "__main__":
    unittest.main()
