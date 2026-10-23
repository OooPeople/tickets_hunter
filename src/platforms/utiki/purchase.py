# encoding=utf-8
"""uTiki reserved-seat and quantity-page purchase preparation."""

import util

from . import submission as submission_engine
from .dom import (
    CommitResult,
    commit_quantity,
    commit_seat,
    read_quantity_snapshot,
    read_seat_snapshot,
)
from .selection import (
    QuantitySnapshot,
    SeatSnapshot,
    build_submit_plan,
    select_quantity_snapshot,
    select_seat_snapshot,
)
from .submission import (
    CTBC_QUANTITY_STALE_RELOAD_REASON,
    CTBC_SEAT_STALE_RELOAD_REASON,
)


async def seat_main(
    tab,
    config_dict,
    ocr,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
    behavior_for_key,
    reload_with_interval,
    poll_seat_reload,
    cancel_refresh,
):
    """執行中信相容售票的劃位頁主要流程。"""

    submit_key = purchase_key_for_tab(tab)
    if submit_key is None:
        return False
    purchase = runtime_for_tab(tab)["purchase"]
    dialog_outcome = await submission_engine._handle_ctbc_submit_dialog(
        tab,
        config_dict,
        submit_key=submit_key,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    if await submission_engine._recover_ctbc_captcha_dialog(
        tab,
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    if await submission_engine._recover_ctbc_seat_conflict(
        tab,
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
        reload_with_interval=reload_with_interval,
    ):
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False
    if dialog_outcome is not None:
        return False
    if await poll_seat_reload(tab, config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    try:
        snapshot = await read_seat_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC SEAT] Seat snapshot failed: {exc}")
        return False
    if not isinstance(snapshot, SeatSnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason if isinstance(snapshot, SeatSnapshot) else "invalid-result"
        )
        debug.log(f"[CTBC SEAT] Reserved-seat page was not ready: {reason}")
        return False

    ticket_number = int(config_dict.get("ticket_number", 1))
    keyword = behavior_for_key(config_dict, "ticket_type_keyword", "全票")
    plan, reason = select_seat_snapshot(
        snapshot,
        keyword,
        ticket_number,
        config_dict.get("advanced", {}).get("disable_adjacent_seat", False),
        behavior_for_key(config_dict, "seat_sorting", "row_seat"),
    )
    if plan is None:
        if reason == "seats-unavailable":
            await reload_with_interval(tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON)
        elif reason == "ticket-type-unavailable":
            debug.log(f"[CTBC SEAT] No ticket type matched: {keyword}")
        else:
            debug.log(f"[CTBC SEAT] Reserved-seat snapshot rejected: {reason}")
        return False

    await cancel_refresh(tab, "reserved-seat plan ready")
    try:
        commit_result = await commit_seat(tab, snapshot, plan)
    except Exception as exc:
        debug.log(f"[CTBC SEAT] Seat commit outcome is unknown: {exc}")
        await reload_with_interval(tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON)
        return False
    if not await _reduce_utiki_seat_commit(
        tab,
        config_dict,
        plan,
        commit_result,
        debug,
        reload_with_interval=reload_with_interval,
    ):
        return False
    submit_plan = build_submit_plan(plan)
    if submit_plan is None:
        debug.log("[CTBC SEAT] Reserved-seat submit plan was rejected")
        return False
    return await submission_engine._ctbc_captcha_and_submit(
        tab,
        config_dict,
        ocr,
        submit_plan=submit_plan,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
        reload_with_interval=reload_with_interval,
    )


async def _reduce_utiki_seat_commit(
    tab, config_dict, plan, commit_result, debug, *, reload_with_interval
):
    """歸納劃位提交結果，避免過早進入送出流程。"""
    if commit_result in {CommitResult.STALE, CommitResult.REJECTED}:
        outcome = commit_result.value
        debug.log(f"[CTBC SEAT] Seat commit {outcome}; waiting to reload")
        await reload_with_interval(tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON)
        return False
    if commit_result is not CommitResult.COMMITTED:
        debug.log("[CTBC SEAT] Seat commit outcome was unknown; submit blocked")
        return False
    if plan.action == "clear":
        debug.log("[CTBC SEAT] Cleared an invalid reserved-seat selection")
        return False
    debug.log(
        f"[CTBC SEAT] Reserved-seat selection ready: "
        f"{plan.ticket_number} x {plan.ticket_type_name}"
    )
    return True


async def _reduce_utiki_quantity_commit(
    tab, config_dict, plan, commit_result, debug, label, *, reload_with_interval
):
    """歸納票數提交結果，避免過早進入送出流程。"""
    if commit_result is CommitResult.STALE:
        debug.log(
            f"[CTBC {label}] Quantity state changed during commit; " "waiting to reload"
        )
        await reload_with_interval(tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON)
        return False
    if commit_result is not CommitResult.COMMITTED:
        outcome = (
            commit_result.value
            if isinstance(commit_result, CommitResult)
            else "unknown"
        )
        debug.log(f"[CTBC {label}] Quantity commit {outcome}; submit blocked")
        return False
    debug.log(f"[CTBC {label}] Selected {plan.ticket_number} x " f"{plan.target_name}")
    return True


async def quantity_main(
    tab,
    config_dict,
    ocr,
    page_type,
    expected_product_id="",
    *,
    runtime_for_tab,
    purchase_key_for_tab,
    behavior_for_key,
    reload_with_interval,
    poll_quantity_reload,
    cancel_refresh,
):
    """在路由驗證後執行共用票數頁流程。"""
    debug = util.create_debug_logger(config_dict)
    label = (
        "SERVER ASSIGNMENT" if page_type == "server_assigned_quantity" else "GENERAL"
    )
    submit_key = purchase_key_for_tab(tab)
    if submit_key is None:
        return False
    purchase = runtime_for_tab(tab)["purchase"]
    dialog_outcome = await submission_engine._handle_ctbc_submit_dialog(
        tab,
        config_dict,
        submit_key=submit_key,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    if await submission_engine._recover_ctbc_captcha_dialog(
        tab,
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    if await submission_engine._recover_ctbc_seat_conflict(
        tab,
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
        reload_with_interval=reload_with_interval,
    ):
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False
    if dialog_outcome is not None:
        return False
    if await poll_quantity_reload(tab, config_dict):
        return False

    try:
        snapshot = await read_quantity_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC {label}] Quantity snapshot failed: {exc}")
        return False
    if not isinstance(snapshot, QuantitySnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason
            if isinstance(snapshot, QuantitySnapshot)
            else "invalid-result"
        )
        debug.log(f"[CTBC {label}] Quantity page was not ready: {reason}")
        return False
    if (
        page_type == "server_assigned_quantity"
        and snapshot.product_id != expected_product_id
    ):
        debug.log(
            "[CTBC SERVER ASSIGNMENT] Product identity changed after "
            "destination attestation"
        )
        return False

    ticket_number = int(config_dict.get("ticket_number", 1))
    keyword = behavior_for_key(config_dict, "ticket_type_keyword", "全票")
    plan = select_quantity_snapshot(
        snapshot, keyword, ticket_number, page_type=page_type
    )
    if plan is None:
        debug.log(f"[CTBC {label}] No available ticket type matched: {keyword}")
        return False

    await cancel_refresh(tab, "ticket type found")
    try:
        commit_result = await commit_quantity(tab, snapshot, plan)
    except Exception as exc:
        debug.log(f"[CTBC {label}] Quantity commit outcome is unknown: {exc}")
        return False
    if not await _reduce_utiki_quantity_commit(
        tab,
        config_dict,
        plan,
        commit_result,
        debug,
        label,
        reload_with_interval=reload_with_interval,
    ):
        return False
    submit_plan = build_submit_plan(plan)
    if submit_plan is None:
        debug.log(f"[CTBC {label}] Quantity submit plan was rejected")
        return False
    return await submission_engine._ctbc_captcha_and_submit(
        tab,
        config_dict,
        ocr,
        submit_plan=submit_plan,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
        reload_with_interval=reload_with_interval,
    )
