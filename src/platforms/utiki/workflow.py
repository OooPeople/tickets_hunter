#encoding=utf-8
"""uTiki2 sports ticketing workflow.

The CTBC site uses the same UTK naming convention as a few older ticketing
sites. This module owns the single per-tab runtime and routes purchase work.
"""

import asyncio
import uuid
from typing import TypedDict

from nodriver_common import (
    check_and_handle_pause,
)
from . import refresh as refresh_engine
from . import date_area as date_area_engine
from . import navigation as navigation_engine
from . import purchase as purchase_engine
from . import submission as submission_engine
from .navigation import _get_ctbc_page_type
from .inputs import nodriver_ctbc_inject_cookies
from .profiles import is_utiki_sports_url
from .runtime import TabRuntime, new_tab_runtime as _new_ctbc_tab_runtime


__all__ = [
    "is_utk_sports_url",
    "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_cleanup_tab",
    "nodriver_utk_sports_pause_refresh",
    "nodriver_utk_sports_consume_reload_event",
    "nodriver_utk_sports_health_check",
    "nodriver_ctbc_inject_cookies",
    "nodriver_ctbc_main",
    "nodriver_utk_sports_reload",
    "nodriver_utk_sports_reload_wait",
]


CTBC_AREA_ROW_SELECTOR = ".seats table.f1 > tbody > tr.saleTr"
CTBC_QUANTITY_STALE_RELOAD_REASON = (
    submission_engine.CTBC_QUANTITY_STALE_RELOAD_REASON
)
CTBC_SEAT_STALE_RELOAD_REASON = submission_engine.CTBC_SEAT_STALE_RELOAD_REASON


class _WorkflowState(TypedDict):
    tabs: dict[str, TabRuntime]
    cancel_tasks: set[asyncio.Task]


_state: _WorkflowState = {
    "tabs": {},
    "cancel_tasks": set(),
}
_UTIKI_TAB_TOKEN_ATTR = "_tickets_hunter_utiki_tab_token"


def _get_utiki_behavior(config_dict, key, default):
    """讀取標準租戶行為設定，並提供一期舊設定相容。"""
    canonical = config_dict.get("utiki")
    if isinstance(canonical, dict) and key in canonical:
        return canonical[key]
    legacy = config_dict.get("ctbc")
    if isinstance(legacy, dict) and key in legacy:
        return legacy[key]
    return default


def _ctbc_tab_identity(tab):
    """取得不保留分頁物件參照的穩定純量識別。"""
    identity = str(getattr(tab, _UTIKI_TAB_TOKEN_ATTR, "") or "")
    if identity:
        return identity
    target_id = str(
        getattr(getattr(tab, "target", None), "target_id", "") or ""
    ).strip()
    if target_id:
        identity = f"utiki-target-{target_id}"
    else:
        identity = f"utiki-tab-{uuid.uuid4().hex}"
    try:
        setattr(tab, _UTIKI_TAB_TOKEN_ATTR, identity)
    except (AttributeError, TypeError) as exc:
        raise TypeError("uTiki tab does not support a stable identity token") from exc
    return identity


def _ctbc_runtime_for_identity(identity: str) -> TabRuntime:
    """依分頁識別取得或建立 uTiki 執行期。"""

    runtime = _state["tabs"].get(identity)
    if runtime is None:
        runtime = _new_ctbc_tab_runtime()
        _state["tabs"][identity] = runtime
    return runtime


def _ctbc_tab_runtime(tab) -> TabRuntime:
    """取得目前瀏覽器分頁的 uTiki 執行期。"""

    return _ctbc_runtime_for_identity(_ctbc_tab_identity(tab))


def _existing_utiki_tab_context(tab) -> tuple[str | None, TabRuntime | None]:
    """取得既有分頁執行期，且不配置或固定新識別。"""
    identity = str(getattr(tab, _UTIKI_TAB_TOKEN_ATTR, "") or "")
    if not identity:
        target_id = str(
            getattr(getattr(tab, "target", None), "target_id", "") or ""
        ).strip()
        if not target_id:
            return None, None
        identity = f"utiki-target-{target_id}"
    return identity, _state["tabs"].get(identity)


def _ctbc_tab_state_key(tab):
    """建立目前分頁在相容狀態表中的索引鍵。"""

    identity = _ctbc_tab_identity(tab)
    return (identity, str(tab.target.url))


is_utk_sports_url = is_utiki_sports_url


def _ctbc_purchase_state_key(tab):
    """Use workflow's current tab identity at the original lookup point."""
    return submission_engine._ctbc_purchase_state_key(
        tab, identity_for_tab=_ctbc_tab_identity
    )


def _utk_refresh_context(tab):
    """取得目前分頁的刷新執行期與背景工作集合。"""

    identity = _ctbc_tab_identity(tab)
    return (
        _ctbc_runtime_for_identity(identity),
        refresh_engine.build_refresh_identity(identity),
    )


async def nodriver_utk_sports_cancel_refresh(
    tab,
    reason="",
    *,
    wait=False,
    abandon_probe=False,
):
    """透過標準 uTiki 刷新引擎取消分頁刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.cancel_refresh(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        reason,
        wait=wait,
        abandon_probe=abandon_probe,
    )


async def nodriver_utk_sports_cleanup_tab(tab, reason=""):
    """解除並清理單一分頁所持有的既有執行期。"""
    identity, runtime = _existing_utiki_tab_context(tab)
    if runtime is None or _state["tabs"].get(identity) is not runtime:
        return False
    _state["tabs"].pop(identity, None)
    try:
        await refresh_engine.cancel_refresh(
            tab,
            runtime,
            refresh_engine.build_refresh_identity(identity),
            _state["cancel_tasks"],
            reason,
            wait=True,
            abandon_probe=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    return True


async def nodriver_utk_sports_pause_refresh(tab, reason="paused"):
    """暫停分頁的佇列或預約刷新，且不重設意圖。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.pause_refresh(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        reason,
    )


def nodriver_utk_sports_consume_reload_event(tab):
    """通知外層路由同網址刷新需要重新偵測 Cloudflare。"""
    return refresh_engine.consume_reload_event(_ctbc_tab_runtime(tab))


async def nodriver_utk_sports_reload(
    tab,
    config_dict,
    reason,
    *,
    apply_local_interval=True,
    allow_unknown_document=False,
    allow_blocked_recovery=False,
    dispatch_deadline=None,
):
    """透過標準 uTiki 共用刷新引擎推進一次刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.reload(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        config_dict,
        reason,
        apply_local_interval=apply_local_interval,
        allow_unknown_document=allow_unknown_document,
        allow_blocked_recovery=allow_blocked_recovery,
        dispatch_deadline=dispatch_deadline,
    )


async def nodriver_utk_sports_reload_wait(
    tab,
    config_dict,
    reason,
    *,
    apply_local_interval=False,
    allow_unknown_document=True,
    allow_blocked_recovery=True,
):
    """透過有界等待器完成一次 uTiki 共用刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.reload_wait(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        config_dict,
        reason,
        apply_local_interval=apply_local_interval,
        allow_unknown_document=allow_unknown_document,
        allow_blocked_recovery=allow_blocked_recovery,
    )


async def nodriver_utk_sports_health_check(tab, url, config_dict):
    """僅執行限流復原，不選擇任何票券控制項。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.health_check(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        url,
        config_dict,
    )


async def _reload_with_interval(tab, config_dict, reason):
    """依設定的刷新間隔排程一次頁面刷新。"""

    return await nodriver_utk_sports_reload(
        tab,
        config_dict,
        reason,
        apply_local_interval=True,
        allow_unknown_document=False,
    )


async def _poll_ctbc_quantity_reload(tab, config_dict):
    """延續既有票數頁刷新，且不重設等待間隔。"""
    request = _ctbc_tab_runtime(tab)["refresh"]["reload_request"]
    if not request or request.get("reason") != CTBC_QUANTITY_STALE_RELOAD_REASON:
        return False
    await _reload_with_interval(
        tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON
    )
    return True


async def _poll_ctbc_seat_reload(tab, config_dict):
    """延續既有劃位頁刷新，且不重設等待間隔。"""
    request = _ctbc_tab_runtime(tab)["refresh"]["reload_request"]
    if not request or request.get("reason") != CTBC_SEAT_STALE_RELOAD_REASON:
        return False
    await _reload_with_interval(tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON)
    return True


async def nodriver_ctbc_date_auto_select(tab, config_dict):
    return await date_area_engine.nodriver_ctbc_date_auto_select(
        tab, config_dict,
        identity_for_tab=_ctbc_tab_identity,
        runtime_for_identity=_ctbc_runtime_for_identity,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
    )


async def nodriver_ctbc_area_auto_select(tab, config_dict):
    return await date_area_engine.nodriver_ctbc_area_auto_select(
        tab, config_dict,
        identity_for_tab=_ctbc_tab_identity,
        runtime_for_identity=_ctbc_runtime_for_identity,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
    )


async def _ctbc_attest_fast_destination(tab, page_type, config_dict):
    identity = _ctbc_tab_identity(tab)
    return await navigation_engine._ctbc_attest_fast_destination(
        tab, page_type, config_dict,
        identity=identity, runtime=_ctbc_runtime_for_identity(identity),
        runtime_for_identity=_ctbc_runtime_for_identity,
        behavior_for_key=_get_utiki_behavior,
    )


async def _ctbc_reconcile_area_navigation(tab, current_url, config_dict=None):
    identity = _ctbc_tab_identity(tab)
    return await navigation_engine._ctbc_reconcile_area_navigation(
        tab, current_url, config_dict,
        identity=identity, runtime=_ctbc_runtime_for_identity(identity),
        runtime_for_identity=_ctbc_runtime_for_identity,
        reload_with_interval=_reload_with_interval,
    )


async def nodriver_ctbc_seat_main(tab, config_dict, ocr):
    """保留原座位頁導覽核對，再交由購票模組準備與提交。"""
    if not await _ctbc_attest_fast_destination(tab, "seat", config_dict):
        return False
    if _get_utiki_behavior(config_dict, "seat_selection_mode", "manual") != "manual":
        return False
    return await purchase_engine.seat_main(
        tab, config_dict, ocr,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        poll_seat_reload=_poll_ctbc_seat_reload,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
    )


async def _nodriver_utiki_quantity_main(
    tab, config_dict, ocr, page_type, expected_product_id=""
):
    """把已核對頁面的票數購票流程交給購票模組。"""
    return await purchase_engine.quantity_main(
        tab, config_dict, ocr, page_type,
        expected_product_id=expected_product_id,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        poll_quantity_reload=_poll_ctbc_quantity_reload,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
    )


async def nodriver_ctbc_general_admission_main(tab, config_dict, ocr):
    """在不劃位的 UTK0202／UTK0203 頁面選擇票數。"""
    if not await _ctbc_attest_fast_destination(
        tab, "general_admission", config_dict
    ):
        return False
    if _get_utiki_behavior(
        config_dict, "seat_selection_mode", "manual"
    ) != "manual":
        return False
    return await _nodriver_utiki_quantity_main(
        tab, config_dict, ocr, "general_admission"
    )


async def nodriver_utiki_server_assigned_quantity_main(tab, config_dict, ocr):
    """沿原電腦配位來源核對，接回票數頁準備。"""
    identity = _ctbc_tab_identity(tab)
    authorization = await navigation_engine._ctbc_server_assigned_authorization(
        tab, config_dict, identity=identity,
        runtime=_ctbc_runtime_for_identity(identity),
        runtime_for_identity=_ctbc_runtime_for_identity,
        purchase_key_for_tab=_ctbc_purchase_state_key,
        behavior_for_key=_get_utiki_behavior,
    )
    if authorization is None:
        return False
    return await _nodriver_utiki_quantity_main(
        tab, config_dict, ocr, "server_assigned_quantity",
        expected_product_id=authorization["product_id"],
    )


async def _reconcile_utiki_purchase(tab, current_url, page_type, config_dict):
    """在外層路由前核對既有提交結果。"""
    return await submission_engine._reconcile_utiki_purchase(
        tab, current_url, page_type, config_dict,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
    )


async def _handle_ctbc_checkout(tab, config_dict):
    """由既有 checkout 路由交給提交模組處理通知。"""
    return await submission_engine._handle_ctbc_checkout(
        tab, config_dict,
        runtime_for_tab=_ctbc_tab_runtime,
        tab_state_key_for_tab=_ctbc_tab_state_key,
    )


async def nodriver_ctbc_main(tab, url, config_dict, ocr):
    """依頁面類型分派中信相容棒球售票自動化流程。"""

    page_type = _get_ctbc_page_type(url)
    if page_type != "server_assigned_quantity":
        navigation_engine._ctbc_clear_server_assignment_for_other_page(
            _ctbc_tab_runtime(tab)["transition"]
        )
    if await check_and_handle_pause(config_dict):
        await nodriver_utk_sports_pause_refresh(tab, "paused")
        return tab

    guard_state = await nodriver_utk_sports_health_check(tab, url, config_dict)
    if guard_state == "blocked":
        return tab
    if guard_state == "unknown":
        return tab

    purchase_result = await _reconcile_utiki_purchase(
        tab, url, page_type, config_dict
    )
    if purchase_result in {"blocked", "pending"}:
        return tab
    if purchase_result == "success" and page_type != "checkout":
        return tab

    if page_type == "date" and config_dict.get("date_auto_select", {}).get("enable", False):
        await nodriver_ctbc_date_auto_select(tab, config_dict)
    elif page_type == "area":
        if config_dict.get("area_auto_select", {}).get("enable", False):
            await nodriver_ctbc_area_auto_select(tab, config_dict)
        else:
            await _ctbc_reconcile_area_navigation(tab, url, config_dict)
    elif page_type == "seat":
        await nodriver_ctbc_seat_main(tab, config_dict, ocr)
    elif page_type == "general_admission":
        await nodriver_ctbc_general_admission_main(tab, config_dict, ocr)
    elif page_type == "server_assigned_quantity":
        await nodriver_utiki_server_assigned_quantity_main(
            tab, config_dict, ocr
        )
    elif page_type == "checkout":
        await nodriver_utk_sports_cancel_refresh(
            tab, "checkout reached", wait=True, abandon_probe=True
        )
        await _handle_ctbc_checkout(tab, config_dict)
    else:
        await nodriver_utk_sports_cancel_refresh(
            tab,
            "automatic refresh not applicable",
            wait=True,
            abandon_probe=True,
        )
    return tab
