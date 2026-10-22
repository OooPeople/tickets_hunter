import sys
import types
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
        "seat_selection_mode": "manual",
    }
    config_dict["utiki"].update(utiki_behavior)
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


def selector_settings(date_keyword='"9/10"', area_keyword='"A"'):
    """Current typed inputs for the pure date and area selectors."""
    from utiki_config import FlowSettings

    return FlowSettings(
        True, True, 2,
        date_keyword=date_keyword, area_keyword=area_keyword,
        keyword_exclude='"身障","輪椅"',
    )

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
            "fingerprint": ["seat", index, f"A區-1排-{index + 1}號", f"S{index + 1}"],
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
            {"A": "S", "Y": "FULL", "Z": type_name, "key": f"S{index + 1}"}
            for index in selected
        ],
        "counters": [
            {"id": "FULL", "counter_raw": str(len(selected))},
            {"id": "OTHER", "counter_raw": "0"},
        ],
        "visual_selected_count": len(selected),
    }

def make_effective_policy(behavior, flow=None):
    """Test assembly from one typed behavior and explicit flow values."""
    from platforms.utiki.application.flow_settings import EffectivePolicy, FlowSettings
    from utiki_config import UtikiBehavior

    if not isinstance(behavior, UtikiBehavior):
        raise ValueError("test policy requires typed behavior")
    selection = flow or FlowSettings(False, False, 1)
    return EffectivePolicy(behavior, selection, 1, 1, None)
