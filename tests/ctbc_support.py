import sys
import types
import json
import re
import time
from pathlib import Path


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
        for bucket in (self.ctbc_state or {}).values():
            if isinstance(bucket, (dict, list, set)):
                bucket.clear()


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


CTBC_TEST_PERFORMANCE_ID = "P16D0B8Z"
CTBC_TEST_AREA_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
    "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
)
CTBC_TEST_QUANTITY_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
    "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
    "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)


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


def make_ctbc_quantity_snapshot():
    return {
        "ok": True,
        "page_url": CTBC_TEST_QUANTITY_URL,
        "document_id": "document-1",
        "performance_id": "P16D0B8Z",
        "group_id": "",
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


def _script_json_constant(script, name):
    match = re.search(rf"const {re.escape(name)} = (.+?);", script)
    return json.loads(match.group(1)) if match else None


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
        if self.semantic_navigation or "CtbcFastMarker" in script or (
            "CtbcMalformedMarker" in script
        ):
            result = await self._evaluate_navigation(script)
            if result is not NotImplemented:
                return result
        self.evaluate_count += 1
        return self.evaluate_results.pop(0) if self.evaluate_results else None

    async def _evaluate_navigation(self, script):
        if "__ticketsHunterCtbcFastCommit" in script:
            if self.on_fast_commit:
                self.on_fast_commit()
            if not self.storage_available or self.fast_commit_outcome == "rejected":
                return "rejected"
            if self.fast_commit_outcome == "pending" or (
                isinstance(self.session_marker, dict)
                and self.session_marker.get("expires_at", 0) > time.time() * 1000
            ):
                return "pending"
            self.session_marker = {
                "version": 1,
                "nonce": _script_json_constant(script, "navigationNonce"),
                "source": _script_json_constant(script, "sourceUrl"),
                "destination": _script_json_constant(script, "destinationUrl"),
                "expires_at": time.time() * 1000 + 15000,
            }
            self.session_marker_raw = None
            self.target.url = self.session_marker["destination"]
            if self.fast_commit_outcome == "throw_after_dispatch":
                raise RuntimeError("execution context destroyed")
            return "dispatched"
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
        if "#LOGIN_ID" in script:
            if "JSON.stringify" in script:
                return '{"account":"user","password":"secret"}'
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
