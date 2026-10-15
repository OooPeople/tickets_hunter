import sys
import types
import json
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
    config_dict["ctbc"].update({
        "ticket_type_keyword": "全票",
        "seat_sorting": "row_seat",
    })
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


CTBC_TEST_PERFORMANCE_ID = "P16D0B8Z"
CTBC_TEST_AREA_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
    "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
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
        "rows": rows,
        "areas": areas,
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
    def __init__(
        self,
        captcha="ABCD",
        dialog="",
        *,
        dialogs=None,
        url="https://tix.ctbcsports.com/BROTHERS/UTK0205_?P=1",
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
        self.reload_count = 0

    async def evaluate(self, script):
        if "#LOGIN_ID" in script:
            if "JSON.stringify" in script:
                return '{"account":"user","password":"secret"}'
            return {"required": False}
        if "#CHK" in script:
            return self.captcha_input.value
        if "#dialog-message" in script:
            if "const expectedText" in script:
                expected_literal = f"const expectedText = {json.dumps(self.dialog)};"
                if (
                    not self.dismiss_allowed
                    or not self.dialog_visible
                    or expected_literal not in script
                    or "message.closest('.ui-dialog')" not in script
                    or "message.textContent.trim() !== expectedText" not in script
                    or "wrapper.getClientRects().length > 0" not in script
                    or "wrapper.querySelector('.ui-dialog-buttonset button')" not in script
                ):
                    return False
                await self.dialog_button.click()
                if self.dialog_closes_on_click:
                    self.dialog = ""
                    self.dialog_visible = False
                return True
            if self.dialogs:
                self.dialog = self.dialogs.pop(0)
                self.dialog_visible = bool(self.dialog)
            return self.dialog if self.dialog_visible else ""
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
