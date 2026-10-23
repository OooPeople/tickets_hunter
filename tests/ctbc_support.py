import sys
import types
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


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

from settings import get_default_config  # noqa: E402


def config(date_keyword='"9/10"', area_keyword='"A"'):
    config_dict = get_default_config()
    config_dict["homepage"] = "https://tix.ctbcsports.com/BROTHERS/"
    config_dict["ticket_number"] = 2
    config_dict["keyword_exclude"] = '"身障","輪椅"'
    config_dict["date_auto_fallback"] = False
    config_dict["area_auto_fallback"] = False
    config_dict["date_auto_select"].update({
        "enable": True,
        "date_keyword": date_keyword,
        "mode": "from top to bottom",
    })
    config_dict["area_auto_select"].update({
        "enable": True,
        "area_keyword": area_keyword,
        "mode": "from top to bottom",
    })
    utiki_behavior = {
        "ticket_type_keyword": "全票",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }
    config_dict["utiki"].update(utiki_behavior)
    config_dict["ctbc"].update(utiki_behavior)
    config_dict["ocr_captcha"].update({
        "enable": False,
        "force_submit": False,
    })
    config_dict["advanced"].update({
        "verbose": False,
        "auto_reload_page_interval": 0,
        "disable_adjacent_seat": False,
    })
    return config_dict


class CtbcStateIsolationMixin:
    ctbc_state = None

    def setUp(self):
        super().setUp()
        self._clear_ctbc_test_state()
        self.addCleanup(self._clear_ctbc_test_state)

    def _clear_ctbc_test_state(self):
        state = self.ctbc_state or {}
        state.get("tabs", {}).clear()
        state.get("cancel_tasks", set()).clear()


def ctbc_tab_runtime(tab):
    """Return the production-owned canonical runtime for one semantic fake."""
    from platforms.utiki.workflow import _ctbc_tab_runtime

    return _ctbc_tab_runtime(tab)


def ctbc_navigation_access():
    """Bind navigation tests to the current workflow-owned runtime lookups."""
    from platforms.utiki.workflow import _navigation_access

    return _navigation_access()


def ctbc_navigation_config_fingerprint(config_dict):
    from platforms.utiki.navigation import _ctbc_selection_config_fingerprint

    return _ctbc_selection_config_fingerprint(
        config_dict, access=ctbc_navigation_access()
    )


async def ctbc_submission_captcha_and_submit(*args, **kwargs):
    """Exercise the extracted submission owner with the live workflow registry."""
    from platforms.utiki import submission, workflow

    return await submission._ctbc_captcha_and_submit(
        *args,
        runtime_for_tab=workflow._ctbc_tab_runtime,
        purchase_key_for_tab=workflow._ctbc_purchase_state_key,
        reload_with_interval=workflow._reload_with_interval,
        **kwargs,
    )


async def ctbc_submission_handle_dialog(*args, **kwargs):
    from platforms.utiki import submission, workflow

    return await submission._handle_ctbc_submit_dialog(
        *args,
        runtime_for_tab=workflow._ctbc_tab_runtime,
        purchase_key_for_tab=workflow._ctbc_purchase_state_key,
        **kwargs,
    )


async def ctbc_submission_reset_captcha(*args, **kwargs):
    from platforms.utiki import submission, workflow

    return await submission._reset_ctbc_captcha(
        *args,
        runtime_for_tab=workflow._ctbc_tab_runtime,
        purchase_key_for_tab=workflow._ctbc_purchase_state_key,
        **kwargs,
    )


async def ctbc_submission_recover_captcha(*args, **kwargs):
    from platforms.utiki import submission, workflow

    return await submission._recover_ctbc_captcha_dialog(
        *args,
        runtime_for_tab=workflow._ctbc_tab_runtime,
        purchase_key_for_tab=workflow._ctbc_purchase_state_key,
        **kwargs,
    )


async def ctbc_submission_recover_conflict(*args, **kwargs):
    from platforms.utiki import submission, workflow

    return await submission._recover_ctbc_seat_conflict(
        *args,
        runtime_for_tab=workflow._ctbc_tab_runtime,
        purchase_key_for_tab=workflow._ctbc_purchase_state_key,
        reload_with_interval=workflow._reload_with_interval,
        **kwargs,
    )


def ctbc_identity_runtime(identity):
    """Return the production-owned canonical runtime for an observed identity."""
    from platforms.utiki.workflow import _ctbc_runtime_for_identity

    return _ctbc_runtime_for_identity(identity)


class FakeUtikiDomPort:
    """Small semantic fake for workflow-to-DOM contract tests."""

    def __init__(self, area_snapshot, commit_result):
        self.area_snapshot = area_snapshot
        self.commit_result = commit_result
        self.events = []
        self.commit_count = 0

    async def read_area_snapshot(self, _tab):
        self.events.append("inspect_area")
        return self.area_snapshot

    async def commit_area(self, _tab, snapshot, plan, ticket_number,
                          navigation_nonce, fast_marker_key):
        self.events.append("commit_area")
        self.commit_count += 1
        assert snapshot is self.area_snapshot
        assert plan.rel == "front"
        assert ticket_number == 2
        assert re.fullmatch(r"[a-f0-9]{32}", navigation_nonce)
        assert fast_marker_key == "ticketsHunter.ctbc.fast-navigation.v1"
        return self.commit_result

    async def commit_server_assigned_area(
        self, _tab, snapshot, plan, ticket_number, navigation_nonce,
        fast_marker_key,
    ):
        self.events.append("commit_server_assigned_area")
        self.commit_count += 1
        assert snapshot is self.area_snapshot
        assert plan.rel == "front"
        assert ticket_number == 2
        assert re.fullmatch(r"[a-f0-9]{32}", navigation_nonce)
        assert fast_marker_key == "ticketsHunter.ctbc.fast-navigation.v1"
        return self.commit_result


CTBC_TEST_PERFORMANCE_ID = "P16D0B8Z"
CTBC_TEST_DATE_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
    "PRODUCT_ID=P16ANF0L"
)
CTBC_TEST_AREA_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
    "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
)
CTBC_TEST_QUANTITY_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
    "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
    "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)
CTBC_TEST_SEAT_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
    "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
    "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)
CTBC_TEST_SERVER_QUANTITY_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0201_001?"
    "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
    "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT&ATYPE=N"
)


def make_ctbc_date_snapshot(*, page_url=CTBC_TEST_DATE_URL):
    return {
        "ok": True,
        "page_url": page_url,
        "document_id": "document-1",
        "rows": [
            {
                "text": "2026/09/10（四）18:35 測試場次 購買",
                "controls": [{
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
                    "onclick": (
                        "location.href='UTK0204_?"
                        "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
                    ),
                }],
            },
            {
                "text": "2026/09/11（五）18:35 測試場次 已售完",
                "controls": [{
                    "actionable": False,
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
                    "onclick": "",
                }],
            },
        ],
    }


class FakeUtikiDateDomPort:
    """Semantic date reader/committer used by workflow contract tests."""

    def __init__(self, snapshot, commit_results):
        self.snapshot = snapshot
        self.commit_results = list(
            commit_results
            if isinstance(commit_results, (list, tuple))
            else [commit_results]
        )
        self.events = []
        self.commit_count = 0
        self.committed_count = 0
        self.nonces = []

    async def read_date_snapshot(self, _tab):
        self.events.append("inspect_date")
        return self.snapshot

    async def commit_date_action(
        self,
        _tab,
        snapshot,
        plan,
        navigation_nonce,
        fast_destination_url="",
        fast_marker_key="",
        fast_marker_ttl_ms=0,
    ):
        from platforms.utiki.dom import CommitResult

        self.events.append("commit_date")
        self.commit_count += 1
        self.nonces.append(navigation_nonce)
        assert snapshot is self.snapshot
        assert plan.text.startswith("2026/09/10")
        assert plan.action_kind == "normal"
        assert re.fullmatch(r"[a-f0-9]{32}", navigation_nonce)
        assert fast_destination_url == ""
        assert fast_marker_key == ""
        assert fast_marker_ttl_ms == 0
        result = self.commit_results.pop(0)
        if result is CommitResult.COMMITTED:
            self.committed_count += 1
        return result


def make_ctbc_area_snapshot(
    *,
    front_row_status="40",
    front_title_status="45",
    front_action_status="45",
    front_title_price="1600",
    include_back=True,
):
    rows = [{
        "area": "109區前排",
        "price": "1600",
        "status": front_row_status,
        "rel": "front",
    }]
    areas = [{
        "id": "front",
        "href": (
            "javascript:Send('0205','P16D0B8Z','PRICE_FRONT',"
            f"'GROUP_FRONT','{front_action_status}')"
        ),
        "title": (
            f"票區:109區前排 票價：{front_title_price} "
            f"尚餘：{front_title_status}"
        ),
    }]
    if include_back:
        rows.append({
            "area": "109區後排",
            "price": "1300",
            "status": "熱賣中",
            "rel": "back",
        })
        areas.append({
            "id": "back",
            "href": (
                "javascript:Send('0205','P16D0B8Z','PRICE_BACK',"
                "'GROUP_BACK','熱賣中')"
            ),
            "title": "票區:109區後排 票價：1300 尚餘：熱賣中",
        })
    return {
        "ok": True,
        "page_url": CTBC_TEST_AREA_URL,
        "document_id": "document-1",
        "rows": rows,
        "areas": areas,
    }


def make_ctbc_quantity_snapshot(
    *, page_url=CTBC_TEST_QUANTITY_URL, group_id="", product_id=""
):
    return {
        "ok": True,
        "page_url": page_url,
        "document_id": "document-1",
        "performance_id": "P16D0B8Z",
        "product_id": product_id,
        "group_id": group_id,
        "price_area_id": "PRICE_FRONT",
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


def make_ctbc_seat_snapshot(*, available=True, selected=()):
    type_name = "全票"
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
            "available": bool(available and not is_selected),
            "selected": is_selected,
        })
    return {
        "ok": True,
        "page_url": CTBC_TEST_SEAT_URL,
        "document_id": "document-1",
        "performance_id": "P16D0B8Z",
        "group_id": "GROUP_FRONT",
        "price_area_id": "PRICE_FRONT",
        "product_id": "PRODUCT_FIXTURE",
        "product_hidden_present": True,
        "quantity_limit_present": True,
        "quantity_limit_raw": "2",
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
        "submitted_seats": [
            {"A": "S", "Y": "FULL", "Z": type_name}
            for _ in selected
        ],
        "counters": [
            {"id": "FULL", "counter_raw": str(len(selected))},
            {"id": "OTHER", "counter_raw": "0"},
        ],
        "visual_selected_count": len(selected),
    }


def make_ctbc_submit_plan(page_url=CTBC_TEST_SEAT_URL):
    """Build a production-valid final-submit plan for state-machine tests."""
    from platforms.utiki.profiles import PageKind, classify_utiki_page
    from platforms.utiki.selection import (
        build_submit_plan,
        parse_quantity_snapshot,
        parse_seat_snapshot,
        select_quantity_snapshot,
        select_seat_snapshot,
    )

    query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
    page_kind = classify_utiki_page(page_url)
    performance_id = query.get("PERFORMANCE_ID", ["P1"])[0] or "P1"
    group_id = query.get("GROUP_ID", ["G1"])[0]
    price_area_id = (
        query.get("PERFORMANCE_PRICE_AREA_ID", ["A1"])[0] or "A1"
    )
    if page_kind is PageKind.RESERVED_SEAT:
        raw = make_ctbc_seat_snapshot(selected=(0, 1))
        raw.update({
            "page_url": page_url,
            "performance_id": performance_id,
            "group_id": group_id or "G1",
            "price_area_id": price_area_id,
        })
        selection_plan, reason = select_seat_snapshot(
            parse_seat_snapshot(raw), "全票", 2
        )
    elif page_kind in (
        PageKind.GENERAL_ADMISSION,
        PageKind.SERVER_ASSIGNED_QUANTITY,
    ):
        is_server = page_kind is PageKind.SERVER_ASSIGNED_QUANTITY
        raw = make_ctbc_quantity_snapshot(
            page_url=page_url,
            group_id=(group_id or "GROUP_FRONT") if is_server else group_id,
            product_id="PRODUCT_FIXTURE" if is_server else "",
        )
        raw.update({
            "performance_id": performance_id,
            "price_area_id": price_area_id,
        })
        selection_plan = select_quantity_snapshot(
            parse_quantity_snapshot(raw),
            "全票",
            2,
            page_type=(
                "server_assigned_quantity" if is_server
                else "general_admission"
            ),
        )
        reason = ""
    else:
        return None
    if selection_plan is None:
        return None
    submit_plan = build_submit_plan(selection_plan)
    assert submit_plan is not None
    return submit_plan


class FakeUtikiQuantityDomPort:
    def __init__(self, snapshot, commit_result):
        self.snapshot = snapshot
        self.commit_result = commit_result
        self.events = []
        self.commit_count = 0

    async def read_quantity_snapshot(self, _tab):
        self.events.append("inspect_quantity")
        return self.snapshot

    async def commit_quantity(self, _tab, snapshot, plan):
        self.events.append("commit_quantity")
        self.commit_count += 1
        assert snapshot is self.snapshot
        assert plan.target_key == "FULL"
        assert plan.ticket_number == 2
        return self.commit_result


class FakeUtikiSeatDomPort:
    """Semantic reserved-seat reader/committer for workflow contracts."""

    def __init__(self, snapshot, commit_results, *, expected_action="select"):
        self.snapshot = snapshot
        self.commit_results = list(
            commit_results
            if isinstance(commit_results, (list, tuple))
            else [commit_results]
        )
        self.expected_action = expected_action
        self.events = []
        self.commit_count = 0

    async def read_seat_snapshot(self, _tab):
        self.events.append("inspect_seat")
        return self.snapshot

    async def commit_seat(self, _tab, snapshot, plan):
        self.events.append("commit_seat")
        self.commit_count += 1
        assert snapshot is self.snapshot
        assert plan.ticket_type_id == "FULL"
        assert plan.ticket_number == 2
        assert plan.action == self.expected_action
        if len(self.commit_results) > 1:
            return self.commit_results.pop(0)
        return self.commit_results[0]


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
        self.clicked = False
        self.click_count = 0
        self.apply_count = 0
        self.value = self.attrs.get("value", "")

    async def get_html(self):
        return self.html

    async def query_selector(self, selector):
        return self.children.get(selector)

    async def query_selector_all(self, selector):
        return self.child_lists.get(selector, [])

    async def apply(self, script):
        self.apply_count += 1
        if 'el.value = ""' in script:
            self.value = ""
            return None
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


def _script_json_constant(script, name):
    marker = f"const {name} = "
    start = script.find(marker)
    if start < 0:
        return None
    value, _end = json.JSONDecoder().raw_decode(
        script[start + len(marker):].lstrip()
    )
    return value


class FakeTab:
    def __init__(
        self,
        selector,
        elements,
        map_area=None,
        url="",
        evaluate_results=None,
        *,
        semantic_navigation=False,
        fast_commit_outcome="dispatched",
        native_commit_outcome=True,
        native_clears_marker=True,
        native_destination="",
        marker=None,
        marker_raw=None,
        marker_delete_allowed=True,
        storage_available=True,
        dom_ids=None,
        on_fast_commit=None,
        on_marker_read=None,
        on_destination_state=None,
        on_native_commit=None,
        on_raw_marker_delete=None,
    ):
        self.selector = selector
        self.elements = elements
        self.map_area = map_area
        self.target = types.SimpleNamespace(url=url)
        self.evaluate_results = list(evaluate_results or [])
        self.evaluate_scripts = []
        self.evaluate_count = 0
        self.last_selector = None
        self.reloaded = False
        self.semantic_navigation = semantic_navigation
        self.fast_commit_outcome = fast_commit_outcome
        self.native_commit_outcome = native_commit_outcome
        self.native_clears_marker = native_clears_marker
        self.native_destination = native_destination
        self.session_marker = marker
        self.session_marker_raw = marker_raw
        self.marker_delete_allowed = marker_delete_allowed
        self.storage_available = storage_available
        self.dom_ids = dom_ids or {}
        self.on_fast_commit = on_fast_commit
        self.on_marker_read = on_marker_read
        self.on_destination_state = on_destination_state
        self.on_native_commit = on_native_commit
        self.on_raw_marker_delete = on_raw_marker_delete
        self.date_document_id = "document-1"
        self.date_commit_nonce = None

    def _marker_raw(self):
        if self.session_marker_raw is not None:
            return self.session_marker_raw
        if self.session_marker is not None:
            return json.dumps(self.session_marker, separators=(",", ":"))
        return None

    def _clear_marker(self):
        self.session_marker = None
        self.session_marker_raw = None

    async def query_selector_all(self, selector):
        self.last_selector = selector
        return self.elements if selector == self.selector else []

    async def query_selector(self, selector):
        if selector.startswith("map#mapdata area#"):
            return self.map_area
        return None

    async def evaluate(self, script, **_kwargs):
        self.evaluate_scripts.append(script)
        if "__ticketsHunterUtikiDateSnapshot" in script:
            return json.dumps(self._date_snapshot())
        if "__ticketsHunterUtikiDateCommit" in script:
            return await self._commit_date(script)
        if self.semantic_navigation or "CtbcFastMarker" in script or (
            "CtbcMalformedMarker" in script
        ):
            result = await self._evaluate_navigation(script)
            if result is not NotImplemented:
                return result
        self.evaluate_count += 1
        return self.evaluate_results.pop(0) if self.evaluate_results else None

    @staticmethod
    def _date_row_text(row):
        return re.sub(
            r"\s+", " ", re.sub(r"<[^>]*>", " ", row.html or "")
        ).strip()

    @staticmethod
    def _date_controls(row):
        controls = []
        for items in row.child_lists.values():
            for control in items:
                if control is not None and control not in controls:
                    controls.append(control)
        if not controls:
            for control in row.children.values():
                if control is not None and control not in controls:
                    controls.append(control)
        return controls

    @staticmethod
    def _date_control_fingerprint(control):
        return {
            "tag_name": str(control.tag_name or "").upper(),
            "id": control.attrs.get("id", ""),
            "href": control.attrs.get("href", ""),
            "resolved_href": control.attrs.get(
                "resolved_href", control.attrs.get("href", "")
            ),
            "role": control.attrs.get("role", ""),
            "type": control.attrs.get("type", ""),
            "target": control.attrs.get("target", ""),
            "download": control.attrs.get("download", ""),
            "ping": control.attrs.get("ping", ""),
            "formaction": control.attrs.get("formaction", ""),
            "has_form": bool(control.attrs.get("has_form", False)),
            "onclick": control.attrs.get("onclick", ""),
        }

    @staticmethod
    def _date_control_actionable(control):
        return bool(
            control.visible and control.connected and control.in_purchase_cell
            and not control.disabled
            and control.attrs.get("aria-disabled") != "true"
        )

    def _date_snapshot(self):
        self.last_selector = self.selector
        rows = []
        for row in self.elements if self.selector else []:
            rows.append({
                "text": self._date_row_text(row),
                "controls": [
                    {
                        "actionable": self._date_control_actionable(control),
                        **self._date_control_fingerprint(control),
                    }
                    for control in self._date_controls(row)
                ],
            })
        return {
            "ok": bool(rows),
            "reason": "" if rows else "not-ready",
            "page_url": self.target.url,
            "document_id": self.date_document_id,
            "rows": rows,
        }

    async def _commit_date(self, script):
        intent = _script_json_constant(script, "intent") or {}
        if (
            intent.get("pageUrl") != self.target.url
            or intent.get("documentId") != self.date_document_id
        ):
            return "stale"
        nonce = intent.get("navigationNonce")
        if self.date_commit_nonce:
            return "committed" if self.date_commit_nonce == nonce else "stale"
        try:
            row = self.elements[intent["rowIndex"]]
            controls = self._date_controls(row)
            control = controls[intent["controlIndex"]]
        except (IndexError, KeyError, TypeError):
            return "rejected"
        if control.commit_attrs:
            control.attrs.update(control.commit_attrs)
        if control.commit_tag_name is not None:
            control.tag_name = control.commit_tag_name
        actionable = [
            item for item in controls if self._date_control_actionable(item)
        ]
        if (
            self._date_row_text(row) != intent.get("rowText")
            or len(actionable) != 1 or actionable[0] is not control
            or self._date_control_fingerprint(control) != intent.get("fingerprint")
            or not control.native_commit_allowed
        ):
            return "stale"

        fast_destination = intent.get("fastDestinationUrl") or ""
        if fast_destination:
            if self.on_fast_commit:
                self.on_fast_commit()
            if not self.storage_available or self.fast_commit_outcome == "rejected":
                return "rejected"
            if self.fast_commit_outcome == "pending" or self.session_marker:
                return "stale"
            self.session_marker = {
                "version": 1,
                "nonce": nonce,
                "source": intent.get("pageUrl"),
                "destination": fast_destination,
                "expires_at": time.time() * 1000 + int(
                    intent.get("fastMarkerTtlMs") or 0
                ),
            }
            self.session_marker_raw = None

        self.date_commit_nonce = nonce
        if fast_destination:
            self.target.url = fast_destination
            if self.fast_commit_outcome == "throw_after_dispatch":
                raise RuntimeError("execution context destroyed")
        else:
            control.clicked = True
            control.click_count += 1
            if self.native_commit_outcome == "throw_after_dispatch":
                raise RuntimeError("execution context destroyed")
        return "committed"

    async def _evaluate_navigation(self, script):
        if "__ticketsHunterCtbcFastMarkerRead" in script:
            if self.on_marker_read:
                self.on_marker_read()
            if not self.storage_available:
                return json.dumps({
                    "available": False,
                    "present": False,
                    "marker": None,
                    "page_url": self.target.url,
                })
            raw = self._marker_raw()
            marker = None
            if raw is not None:
                try:
                    marker = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    marker = None
            return json.dumps({
                "available": True,
                "present": raw is not None,
                "marker": marker,
                "raw": raw,
                "page_url": self.target.url,
            })
        if "__ticketsHunterCtbcFastMarkerDelete" in script:
            if not self.storage_available or not self.marker_delete_allowed:
                return False
            nonce = _script_json_constant(script, "expectedNonce")
            raw = self._marker_raw()
            if raw is None:
                return True
            try:
                marker = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            if isinstance(marker, dict) and marker.get("nonce") == nonce:
                self._clear_marker()
                return True
            return False
        if "__ticketsHunterCtbcMalformedMarkerDelete" in script:
            expected_raw = _script_json_constant(script, "expectedRaw")
            if self.on_raw_marker_delete:
                self.on_raw_marker_delete()
            if self._marker_raw() != expected_raw:
                return False
            self._clear_marker()
            return True
        if "__ticketsHunterCtbcDestinationState" in script:
            result = {
                "ready": True, "page_url": self.target.url, **self.dom_ids,
            }
            if self.on_destination_state:
                self.on_destination_state()
            return json.dumps(result)
        if "__ticketsHunterCtbcReturn" in script:
            source_url = _script_json_constant(script, "sourceUrl")
            if self.target.url != source_url:
                return False
            self.target.url = _script_json_constant(script, "areaUrl")
            return True
        if "__ticketsHunterCtbcNativeAreaCommit" in script:
            if self.on_native_commit:
                self.on_native_commit()
            if self.native_clears_marker:
                self._clear_marker()
            if self.native_destination:
                self.target.url = self.native_destination
            if self.native_commit_outcome == "throw_after_dispatch":
                raise RuntimeError("execution context destroyed")
            return self.native_commit_outcome
        return NotImplemented

    async def sleep(self, _seconds):
        return None

    async def reload(self):
        self.reloaded = True
class FakeSubmitTab:
    def __init__(
        self,
        captcha="ABCD",
        dialog="",
        *,
        dialogs=None,
        url=(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        ),
    ):
        self.target = types.SimpleNamespace(url=url)
        self.captcha = captcha
        self.dialog = dialog
        self.dialogs = list(dialogs or [])
        self.button = FakeElement("<button id='addcart'>加入購物車</button>")
        self.dialog_button = FakeElement("<button type='button'>OK</button>")
        self.captcha_input = FakeElement(attrs={"value": captcha})
        self.captcha_refresh = FakeElement("<button id='chang_pic'></button>")
        self.dialog_visible = bool(dialog)
        self.dismiss_allowed = True
        self.dialog_closes_on_click = True
        self.document_id = "fake-dialog-document"
        self.reload_count = 0

    async def evaluate(self, script):
        if "__ticketsHunterUtikiSubmit" in script:
            await self.button.click()
            return "dispatched"
        if "#LOGIN_ID" in script:
            if "JSON.stringify" in script:
                expected = _script_json_constant(
                    script, "expectedDialog"
                )
                if expected and (
                    expected.get("pageUrl") != self.target.url
                    or expected.get("documentId") != self.document_id
                    or not self.dialog_visible
                    or expected.get("text") != self.dialog
                ):
                    return '{"ok":false}'
                return '{"ok":true,"account":"user","password":"secret"}'
            return {"required": False}
        if "#CHK" in script:
            return self.captcha_input.value
        if "__ticketsHunterUtikiDialogSnapshot" in script:
            if self.dialogs:
                self.dialog = self.dialogs.pop(0)
                self.dialog_visible = bool(self.dialog)
            return json.dumps({
                "page_url": self.target.url,
                "document_id": self.document_id,
                "visible_count": int(self.dialog_visible),
                "text": self.dialog if self.dialog_visible else "",
            })
        if "__ticketsHunterUtikiDialogDismiss" in script:
            snapshot = _script_json_constant(script, "snapshot") or {}
            if (
                snapshot.get("pageUrl") != self.target.url
                or snapshot.get("documentId") != self.document_id
                or not self.dialog_visible
                or snapshot.get("text") != self.dialog
            ):
                return "stale"
            if not self.dismiss_allowed:
                return "rejected"
            await self.dialog_button.click()
            if self.dialog_closes_on_click:
                self.dialog = ""
                self.dialog_visible = False
            return "committed"
        return None

    async def query_selector(self, selector):
        if selector == "button#addcart":
            return self.button
        if selector == "#CHK":
            return self.captcha_input
        if selector == "#chang_pic":
            return self.captcha_refresh
        return None

    async def sleep(self, _seconds):
        return None

    async def reload(self):
        self.reload_count += 1
