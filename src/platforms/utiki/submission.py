# encoding=utf-8
"""uTiki submit records, outcome attribution, and recovery."""

from .dom import CommitResult, dismiss_dialog, read_dialog_snapshot
from .profiles import PageKind
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import util
from nodriver_common import (
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
)
from .dom import (
    SubmitDispatchResult,
    clear_conflict_selection,
    dispatch_submit,
)
from .inputs import (
    _clear_ctbc_captcha_input,
    _ctbc_captcha,
    _ctbc_login_fingerprint,
    _fill_ctbc_inline_login,
    _click_ctbc_captcha_refresh,
    _query_ctbc_captcha_input,
    _query_ctbc_captcha_refresh,
    _read_ctbc_captcha_image_token,
)
from .profiles import (
    classify_utiki_page,
    get_utiki_tenant_profile,
    same_origin,
    unique_query,
)
from .selection import (
    SubmitPlan,
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    submit_plan_is_valid,
)

CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS = 10.0
CTBC_QUANTITY_STALE_RELOAD_REASON = "quantity state changed during commit"
CTBC_SEAT_STALE_RELOAD_REASON = "requested seats are unavailable"


def _page_type(url):
    page_kind = classify_utiki_page(url)
    return None if page_kind is PageKind.UNKNOWN else page_kind.value


def _profile_dict(url):
    profile = get_utiki_tenant_profile(url)
    return profile.as_legacy_dict() if profile else None


async def _get_ctbc_visible_dialog_text(tab):
    """為舊呼叫端讀取目前可見對話框文字。"""
    snapshot = await read_dialog_snapshot(tab)
    if snapshot and snapshot.visible_count == 1:
        return snapshot.text
    return ""


async def _ctbc_dialog_is_visible(tab):
    """以保守規則判斷對話框是否仍可見。"""
    snapshot = await read_dialog_snapshot(tab)
    return snapshot is None or snapshot.visible_count != 0


async def _wait_ctbc_dialog_closed(tab, snapshot):
    """等待指定對話框關閉或文件狀態改變。"""

    for _ in range(10):
        await tab.sleep(0.1)
        current = await read_dialog_snapshot(tab)
        if current is None:
            return False
        if (
            current.page_url != snapshot.page_url
            or current.document_id != snapshot.document_id
        ):
            return False
        if current.visible_count == 0:
            return True
        if current.visible_count != 1 or current.text != snapshot.text:
            return False
    return False


async def _dismiss_ctbc_dialog(tab, expected_text):
    """透過快照與提交邊界關閉指定對話框。"""
    snapshot = await read_dialog_snapshot(tab)
    if not (
        snapshot and snapshot.visible_count == 1 and snapshot.text == expected_text
    ):
        return False
    if await dismiss_dialog(tab, snapshot) is not CommitResult.COMMITTED:
        return False
    return await _wait_ctbc_dialog_closed(tab, snapshot)


def _ctbc_purchase_outcome(
    kind,
    *,
    text="",
    reason="",
    source_url="",
    source_document_id="",
    page_type="",
    nonce="",
    dom_document_id="",
    product_id="",
):
    """建立不含敏感資料的標準購票結果紀錄。"""
    normalized_text = " ".join(str(text or "").split())
    record = {
        "kind": str(kind or "unknown"),
        "text": normalized_text,
        "reason": str(reason or ""),
        "source_url": str(source_url or ""),
        "source_document_id": str(source_document_id or ""),
        "nonce": str(nonce or ""),
    }
    if dom_document_id:
        record["dom_document_id"] = str(dom_document_id)
    if product_id:
        record["product_id"] = str(product_id)
    if page_type:
        record["page_type"] = str(page_type)
    return record


def _ctbc_source_document_id(context):
    """取得流程與 DOM 共用的標準文件時間來源識別。"""
    if "source_document_id" in context:
        return str(context.get("source_document_id") or "")
    return str(context.get("document_id") or "")


def _ctbc_dialog_outcome_record(
    outcome,
    text,
    source_url,
    source_document_id,
    page_type,
    nonce,
    dom_document_id="",
    product_id="",
):
    """將已分類對話框純化為標準阻擋結果紀錄。"""
    kind = "unknown" if outcome == "rejected" else outcome
    reason = "dialog" if kind == "unknown" else ""
    record = _ctbc_purchase_outcome(
        kind,
        text=text,
        reason=reason,
        source_url=source_url,
        source_document_id=source_document_id,
        page_type=page_type if kind == "seat_conflict" else "",
        nonce=nonce,
        dom_document_id=dom_document_id if kind == "seat_conflict" else "",
        product_id=product_id if kind == "seat_conflict" else "",
    )
    if kind == "seat_conflict":
        record.update(
            {
                "dialog_dismissed": False,
                "clear_phase": "",
                "clear_result": "",
                "reload_dispatched": False,
                "recovery_document_id": "",
            }
        )
    elif kind == "auth":
        record["fingerprint"] = ""
    elif kind == "captcha":
        record.update(
            {
                "reset_phase": "",
                "reset_source_document_id": "",
                "reset_before_token": "",
            }
        )
    return record


def _ctbc_recent_submit_context(purchase, fallback_key=None):
    """在不猜測歷史結果的前提下判定對話框所屬送出操作。"""
    active = purchase.get("active_submit")
    if isinstance(active, dict) and active.get("purchase_key") is not None:
        return active.get("purchase_key"), active
    outcomes = purchase.get("outcomes", {})
    fallback = outcomes.get(fallback_key)
    pointer = purchase.get("last_submit")
    pointer_key = None
    pointer_outcome = None
    if isinstance(pointer, dict):
        pointer_key = pointer.get("purchase_key")
        pointer_outcome = outcomes.get(pointer_key)
        if not isinstance(pointer_outcome, dict) or pointer_outcome.get(
            "nonce"
        ) != pointer.get("nonce"):
            pointer_key, pointer_outcome = None, None
    if isinstance(fallback, dict):
        if pointer_outcome is not None and pointer_key != fallback_key:
            return None, None
        return fallback_key, fallback
    if pointer_outcome is not None:
        return pointer_key, pointer_outcome
    if fallback_key is not None:
        return fallback_key, None
    return None, None


def _ctbc_store_purchase_outcome(purchase, purchase_key, record, active=None):
    """套用購票結果並安全清理相符的進行中送出狀態。"""
    if purchase_key is None or not isinstance(record, dict):
        return False
    current = purchase.get("active_submit")
    if active is not None and current is not active:
        return False
    if active is not None:
        purchase["active_submit"] = None
    purchase["outcomes"][purchase_key] = record
    if active is not None:
        purchase["last_submit"] = {
            "purchase_key": purchase_key,
            "nonce": record.get("nonce", ""),
        }
    pointer = purchase.get("last_submit")
    if isinstance(pointer, dict) and pointer.get("purchase_key") == purchase_key:
        pointer["nonce"] = record.get("nonce", "")
    return True


def _ctbc_conflict_page_kind(page_type):
    """將頁面類型轉成衝突清理所需的選擇種類。"""

    return {
        "seat": PageKind.RESERVED_SEAT,
        "general_admission": PageKind.GENERAL_ADMISSION,
        "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
    }.get(page_type)


def _ctbc_submit_dispatched_at(value):
    """將送出時間轉成可比較的時間戳。"""

    if isinstance(value, dict):
        value = value.get("dispatched_at")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ctbc_unknown_submit_outcome(active, reason):
    """將已送出意圖轉成需保留的未知結果。"""
    return _ctbc_purchase_outcome(
        "unknown",
        reason=reason,
        source_url=active.get("source_url", ""),
        source_document_id=_ctbc_source_document_id(active),
        nonce=active.get("nonce", ""),
        dom_document_id=active.get("document_id", ""),
        product_id=active.get("product_id", ""),
    )


def _ctbc_finish_active_unknown(purchase, active, reason):
    """以未知結果結束目前進行中的送出意圖。"""

    if purchase.get("active_submit") is not active:
        return False
    purchase_key = active.get("purchase_key")
    record = _ctbc_unknown_submit_outcome(active, reason)
    if not _ctbc_store_purchase_outcome(purchase, purchase_key, record, active=active):
        return False
    return True


def _ctbc_purchase_state_key(tab, *, identity_for_tab):
    """以購票身分而非外觀網址建立送出狀態索引鍵。"""
    identity = identity_for_tab(tab)
    current_url = str(tab.target.url)
    try:
        parsed = urlsplit(current_url)
    except ValueError:
        return None

    purchase_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
    )
    page_type = _page_type(purchase_url)
    query = unique_query(purchase_url)
    profile = _profile_dict(purchase_url)
    if (
        page_type not in ("seat", "general_admission", "server_assigned_quantity")
        or query is None
        or not profile
    ):
        return None

    performance_id = query.get("PERFORMANCE_ID")
    group_id = query.get("GROUP_ID")
    price_area_id = query.get("PERFORMANCE_PRICE_AREA_ID")
    token_pattern = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token_pattern.fullmatch(str(performance_id or ""))
        or not token_pattern.fullmatch(str(price_area_id or ""))
        or group_id is None
        or (group_id and not token_pattern.fullmatch(str(group_id)))
        or (page_type in {"seat", "server_assigned_quantity"} and not group_id)
    ):
        return None

    return (
        identity,
        profile["host"],
        profile["tenant_path"],
        page_type,
        performance_id,
        group_id,
        price_area_id,
        query.get("ATYPE", ""),
    )


def _ctbc_submit_reached_checkout(source_url, destination_url):
    """僅接受導向來源租戶結帳路由的送出結果。"""
    try:
        source = urlsplit(source_url)
        destination = urlsplit(destination_url)
    except ValueError:
        return False

    source_without_fragment = urlunsplit(
        (source.scheme, source.netloc, source.path, source.query, "")
    )
    return (
        _page_type(source_without_fragment)
        in ("seat", "general_admission", "server_assigned_quantity")
        and _page_type(destination_url) == "checkout"
        and same_origin(source, destination)
    )


def _ctbc_captcha_reset_is_current(
    tab,
    submit_key,
    outcome,
    source_document_id,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """判斷驗證碼重設工作是否仍屬於目前送出意圖。"""

    runtime = runtime_for_tab(tab)
    return bool(
        source_document_id
        and runtime["purchase"]["outcomes"].get(submit_key) is outcome
        and purchase_key_for_tab(tab) == submit_key
        and runtime["document"]["last_document"] == source_document_id
    )


def _ctbc_captcha_phase_is_current(
    tab,
    submit_key,
    outcome,
    source_document_id,
    phase,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """判斷驗證碼工作是否仍處於指定階段。"""

    return bool(
        _ctbc_captcha_reset_is_current(
            tab,
            submit_key,
            outcome,
            source_document_id,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
        and outcome.get("reset_phase") == phase
    )


def _release_ctbc_captcha_preflight(tab, submit_key, outcome, *, runtime_for_tab):
    """釋放驗證碼重設前置檢查所持有的狀態。"""

    if (
        _ctbc_outcome_is_current(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
        and outcome.get("reset_phase") == "preflighting"
    ):
        outcome["reset_phase"] = ""
    return False


async def _observe_ctbc_captcha_refresh(
    tab, config_dict, submit_key, outcome, *, runtime_for_tab, purchase_key_for_tab
):
    """觀察驗證碼刷新是否已產生新的圖片。"""

    current_document_id = runtime_for_tab(tab)["document"]["last_document"]
    source_document_id = str(outcome.get("reset_source_document_id") or "")
    if not _ctbc_outcome_is_current(
        tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
    ):
        return False
    if purchase_key_for_tab(tab) != submit_key:
        return False
    if not source_document_id:
        return False
    if current_document_id and current_document_id != source_document_id:
        outcome["reset_phase"] = "done"
        return True
    if current_document_id != source_document_id:
        return False
    before_token = str(outcome.get("reset_before_token") or "")
    if not before_token:
        return False
    current_token = await _read_ctbc_captcha_image_token(tab, config_dict)
    if not _ctbc_captcha_reset_is_current(
        tab,
        submit_key,
        outcome,
        source_document_id,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    if current_token and current_token != before_token:
        outcome["reset_phase"] = "done"
        return True
    return False


async def _dispatch_ctbc_captcha_refresh(
    tab,
    config_dict,
    submit_key,
    outcome,
    source_document_id,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """送出驗證碼圖片刷新並登記觀察狀態。"""

    before_token = await _read_ctbc_captcha_image_token(tab, config_dict)
    if not _ctbc_captcha_phase_is_current(
        tab,
        submit_key,
        outcome,
        source_document_id,
        "preflighting",
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
    if not before_token:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
    outcome["reset_before_token"] = before_token
    try:
        refresh = await _query_ctbc_captcha_refresh(tab)
    except Exception:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
    if not _ctbc_captcha_phase_is_current(
        tab,
        submit_key,
        outcome,
        source_document_id,
        "preflighting",
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
    if not refresh:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        )
    outcome["reset_phase"] = "dispatching"
    try:
        await _click_ctbc_captcha_refresh(refresh)
    except Exception:
        if not _ctbc_captcha_phase_is_current(
            tab,
            submit_key,
            outcome,
            source_document_id,
            "dispatching",
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return False
        outcome["reset_phase"] = "unknown"
        return await _observe_ctbc_captcha_refresh(
            tab,
            config_dict,
            submit_key,
            outcome,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
    if not _ctbc_captcha_phase_is_current(
        tab,
        submit_key,
        outcome,
        source_document_id,
        "dispatching",
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    outcome["reset_phase"] = "awaiting_change"
    return await _observe_ctbc_captcha_refresh(
        tab,
        config_dict,
        submit_key,
        outcome,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )


async def _reset_ctbc_captcha(
    tab,
    *,
    refresh_image,
    submit_key=None,
    config_dict=None,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """重設驗證碼輸入，並視需要刷新圖片。"""

    config_dict = config_dict or {}
    runtime = runtime_for_tab(tab)
    purchase = runtime["purchase"]
    outcome = purchase["outcomes"].get(submit_key)
    if (
        refresh_image
        and submit_key is not None
        and (not isinstance(outcome, dict) or outcome.get("kind") != "captcha")
    ):
        return False
    if refresh_image and submit_key is not None:
        if not _ctbc_outcome_is_current(
            tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
        ):
            return False
        if purchase_key_for_tab(tab) != submit_key:
            return False
        phase = outcome.get("reset_phase")
        prior_source = str(outcome.get("reset_source_document_id") or "")
        current_document_id = str(runtime["document"]["last_document"] or "")
        if prior_source and current_document_id and current_document_id != prior_source:
            outcome["reset_phase"] = "done"
            return True
        if phase == "done":
            return True
        if phase == "preflighting":
            return False
        if phase:
            return await _observe_ctbc_captcha_refresh(
                tab,
                config_dict,
                submit_key,
                outcome,
                runtime_for_tab=runtime_for_tab,
                purchase_key_for_tab=purchase_key_for_tab,
            )
        source_document_id = current_document_id
        if not source_document_id:
            return False
        outcome["reset_source_document_id"] = source_document_id
        outcome["reset_before_token"] = ""
        outcome["reset_phase"] = "preflighting"

    try:
        captcha_input = await _query_ctbc_captcha_input(tab)
        if refresh_image and not _ctbc_captcha_phase_is_current(
            tab,
            submit_key,
            outcome,
            source_document_id,
            "preflighting",
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
            )
        if refresh_image and not captcha_input:
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
            )
        if captcha_input:
            await _clear_ctbc_captcha_input(captcha_input)
            if refresh_image and not _ctbc_captcha_phase_is_current(
                tab,
                submit_key,
                outcome,
                source_document_id,
                "preflighting",
                runtime_for_tab=runtime_for_tab,
                purchase_key_for_tab=purchase_key_for_tab,
            ):
                return _release_ctbc_captcha_preflight(
                    tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
                )
    except Exception:
        if refresh_image:
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome, runtime_for_tab=runtime_for_tab
            )
        return False
    if refresh_image:
        return await _dispatch_ctbc_captcha_refresh(
            tab,
            config_dict,
            submit_key,
            outcome,
            source_document_id,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
    return True


def _ctbc_outcome_is_current(tab, submit_key, record, *, runtime_for_tab):
    """判斷購票結果是否仍屬於目前文件與送出意圖。"""

    return runtime_for_tab(tab)["purchase"]["outcomes"].get(submit_key) is record


def _ctbc_conflict_is_current(
    tab, submit_key, record, *, runtime_for_tab, purchase_key_for_tab
):
    """判斷座位或票數衝突是否仍可安全清理。"""

    return bool(
        _ctbc_outcome_is_current(
            tab, submit_key, record, runtime_for_tab=runtime_for_tab
        )
        and purchase_key_for_tab(tab) == submit_key
    )


async def _clear_ctbc_conflict_selection(
    tab, config_dict, submit_key, record, *, runtime_for_tab, purchase_key_for_tab
):
    """清除目前送出衝突所對應的座位或票數選擇。"""

    if not _ctbc_conflict_is_current(
        tab,
        submit_key,
        record,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    clear_phase = record.get("clear_phase")
    if clear_phase == "done":
        return True
    if clear_phase:
        return False
    record["clear_phase"] = "dispatching"
    page_kind = _ctbc_conflict_page_kind(record.get("page_type"))
    try:
        result = await clear_conflict_selection(
            tab,
            page_kind,
            str(record.get("source_url") or ""),
            str(record.get("dom_document_id") or ""),
            str(record.get("product_id") or ""),
        )
    except Exception as exc:
        result = None
        util.create_debug_logger(config_dict).log(
            f"[CTBC] Conflict clear outcome is unknown: {exc}"
        )
    if not _ctbc_outcome_is_current(
        tab, submit_key, record, runtime_for_tab=runtime_for_tab
    ):
        return False
    record["clear_phase"] = "done"
    record["clear_result"] = (
        result.value if isinstance(result, CommitResult) else "unknown"
    )
    return _ctbc_conflict_is_current(
        tab,
        submit_key,
        record,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )


async def _apply_ctbc_dialog_effects(
    tab,
    config_dict,
    submit_key,
    outcome,
    record,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """依對話框分類套用關閉、清理或重試效果。"""

    debug = util.create_debug_logger(config_dict)
    purchase = runtime_for_tab(tab)["purchase"]
    if purchase["outcomes"].get(submit_key) is not record:
        return
    if outcome == "auth":
        debug.log(
            "[CTBC LOGIN] Authentication rejected; waiting for credentials to change"
        )
    elif outcome == "captcha":
        reset_complete = await _reset_ctbc_captcha(
            tab,
            refresh_image=True,
            submit_key=submit_key,
            config_dict=config_dict,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
        if reset_complete and purchase["outcomes"].get(submit_key) is record:
            purchase["outcomes"].pop(submit_key, None)
        debug.log(f"[CTBC CAPTCHA] Server rejected captcha: {record['text']}")
    elif outcome == "seat_conflict":
        if not _ctbc_conflict_is_current(
            tab,
            submit_key,
            record,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return
        record["dialog_dismissed"] = True
        debug.log(f"[CTBC SEAT] Selected seats were rejected: {record['text']}")
        if not await _clear_ctbc_conflict_selection(
            tab,
            config_dict,
            submit_key,
            record,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return
        if record.get("clear_result") != CommitResult.COMMITTED.value:
            debug.log(
                "[CTBC] Local selection clear was not committed; reload remains pending"
            )
        if not _ctbc_conflict_is_current(
            tab,
            submit_key,
            record,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return
        await _reset_ctbc_captcha(
            tab,
            refresh_image=False,
            submit_key=submit_key,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
        if not _ctbc_conflict_is_current(
            tab,
            submit_key,
            record,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return
    elif outcome == "success":
        debug.log("[CTBC] Cart accepted; same-page resubmit is blocked")
    else:
        debug.log(f"[CTBC] Submit blocked after {outcome}: {record['text']}")


async def _handle_ctbc_submit_dialog(
    tab,
    config_dict,
    submit_key=None,
    source_url=None,
    dialog_snapshot=None,
    *,
    runtime_for_tab,
    purchase_key_for_tab,
):
    """分類並處理加入購物車後出現的售票對話框。"""

    debug = util.create_debug_logger(config_dict)
    runtime = runtime_for_tab(tab)
    purchase = runtime["purchase"]
    if dialog_snapshot is None:
        dialog_snapshot = await read_dialog_snapshot(tab)
    if dialog_snapshot is None:
        debug.log("[CTBC] Submit dialog observation is invalid; action blocked")
        return "rejected"
    if dialog_snapshot.visible_count == 0:
        return None

    current_key = submit_key if submit_key is not None else purchase_key_for_tab(tab)
    submit_key, context = _ctbc_recent_submit_context(purchase, current_key)
    if submit_key is None:
        debug.log("[CTBC] Submit dialog appeared outside a valid purchase context")
        return "rejected"
    active = context if context is purchase.get("active_submit") else None
    context_source = context.get("source_url", "") if isinstance(context, dict) else ""
    context_document = ""
    context_dom_document = ""
    context_product_id = ""
    if isinstance(context, dict):
        context_document = _ctbc_source_document_id(context)
        context_dom_document = str(
            context.get("dom_document_id") or context.get("document_id") or ""
        )
        context_product_id = str(context.get("product_id") or "")
    source_url = str(source_url or context_source or tab.target.url)
    page_type = _page_type(source_url)
    dialog_text = dialog_snapshot.text
    outcome = (
        _classify_ctbc_submit_dialog(dialog_text)
        if dialog_snapshot.visible_count == 1 and dialog_text
        else "rejected"
    )
    record = _ctbc_dialog_outcome_record(
        outcome,
        dialog_text,
        source_url,
        (
            context_document
            if isinstance(context, dict)
            else runtime["document"]["last_document"]
        ),
        page_type,
        context.get("nonce", "") if isinstance(context, dict) else "",
        context_dom_document,
        context_product_id,
    )
    if not _ctbc_store_purchase_outcome(purchase, submit_key, record, active=active):
        return "rejected"
    if outcome == "rejected":
        debug.log("[CTBC] Unknown or ambiguous dialog preserved; action blocked")
        return outcome

    if outcome == "auth":
        try:
            fingerprint = await _ctbc_login_fingerprint(
                tab, expected_dialog=dialog_snapshot
            )
        except Exception as exc:
            debug.log(
                "[CTBC LOGIN] Credential fingerprint observation failed; "
                f"document-scoped blocker retained: {exc}"
            )
        else:
            if purchase["outcomes"].get(submit_key) is record:
                record["fingerprint"] = fingerprint
    dismiss_result = await dismiss_dialog(tab, dialog_snapshot)
    if (
        dismiss_result is not CommitResult.COMMITTED
        or not await _wait_ctbc_dialog_closed(tab, dialog_snapshot)
    ):
        debug.log(
            f"[CTBC] Visible dialog changed before it could be dismissed: {dialog_text}"
        )
        return "rejected" if outcome == "success" else outcome
    await _apply_ctbc_dialog_effects(
        tab,
        config_dict,
        submit_key,
        outcome,
        record,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    return outcome


async def _recover_ctbc_captcha_dialog(
    tab, config_dict, *, runtime_for_tab, purchase_key_for_tab
):
    """從驗證碼錯誤對話框恢復至可再次送出的狀態。"""

    submit_key = purchase_key_for_tab(tab)
    purchase = runtime_for_tab(tab)["purchase"]
    outcome = purchase["outcomes"].get(submit_key)
    if not isinstance(outcome, dict) or outcome.get("kind") != "captcha":
        return False
    if await _ctbc_dialog_is_visible(tab):
        return True
    reset_complete = await _reset_ctbc_captcha(
        tab,
        refresh_image=True,
        submit_key=submit_key,
        config_dict=config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    if reset_complete and purchase["outcomes"].get(submit_key) is outcome:
        purchase["outcomes"].pop(submit_key, None)
    debug = util.create_debug_logger(config_dict)
    if reset_complete:
        debug.log("[CTBC CAPTCHA] Dialog disappeared; captcha was reset before retry")
    else:
        debug.log(
            "[CTBC CAPTCHA] Captcha refresh outcome is unknown; retry remains blocked"
        )
    return True


async def _recover_ctbc_seat_conflict(
    tab, config_dict, *, runtime_for_tab, purchase_key_for_tab, reload_with_interval
):
    """從座位或票數衝突恢復至可重新選擇的狀態。"""

    submit_key = purchase_key_for_tab(tab)
    runtime = runtime_for_tab(tab)
    outcomes = runtime["purchase"]["outcomes"]
    pending = outcomes.get(submit_key)
    if not isinstance(pending, dict) or pending.get("kind") != "seat_conflict":
        return False

    current_document_id = runtime["document"]["last_document"]
    if not pending.get("dialog_dismissed", False):
        if await _ctbc_dialog_is_visible(tab):
            return True
        if not _ctbc_conflict_is_current(
            tab,
            submit_key,
            pending,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        ):
            return False
        pending["dialog_dismissed"] = True

    if not await _clear_ctbc_conflict_selection(
        tab,
        config_dict,
        submit_key,
        pending,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False

    if pending["reload_dispatched"]:
        recovery_document = str(pending.get("recovery_document_id") or "")
        if (
            recovery_document
            and current_document_id
            and current_document_id != recovery_document
        ):
            outcomes.pop(submit_key, None)
            return False
        return True

    if not _ctbc_conflict_is_current(
        tab,
        submit_key,
        pending,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    reload_reason = "selected seats were taken"
    reload_completed = await reload_with_interval(tab, config_dict, reload_reason)
    if not _ctbc_conflict_is_current(
        tab,
        submit_key,
        pending,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    reload_request = runtime["refresh"]["reload_request"]
    request_source_document = str(
        reload_request.get("source_document_token")
        if isinstance(reload_request, dict)
        else ""
    )
    request_dispatched = bool(
        reload_request
        and reload_request.get("reason") == reload_reason
        and reload_request.get("phase") in {"awaiting_document", "completed"}
        and request_source_document
        and request_source_document == current_document_id
    )
    if reload_completed or request_dispatched:
        pending["recovery_document_id"] = (
            request_source_document
            if request_dispatched
            else str(current_document_id or "")
        )
        pending["reload_dispatched"] = True
    return True


def _ctbc_auth_failure_is_active(
    tab, submit_key, login_fingerprint, *, runtime_for_tab
):
    """判斷登入失敗是否仍屬於目前文件與送出操作。"""

    runtime = runtime_for_tab(tab)
    outcomes = runtime["purchase"]["outcomes"]
    failure = outcomes.get(submit_key)
    if not isinstance(failure, dict) or failure.get("kind") != "auth":
        return False

    failed_fingerprint = str(failure.get("fingerprint") or "")
    if failed_fingerprint:
        current_fingerprint = str(login_fingerprint or "")
        if not current_fingerprint or failed_fingerprint == current_fingerprint:
            return True
        outcomes.pop(submit_key, None)
        return False

    current_document_id = runtime["document"]["last_document"]
    source_document_id = str(failure.get("source_document_id") or "")
    if not source_document_id:
        return True
    if current_document_id and current_document_id != source_document_id:
        outcomes.pop(submit_key, None)
        return False
    return True


async def _observe_ctbc_submit_outcome(
    tab, config_dict, source_url, submit_key, *, runtime_for_tab, purchase_key_for_tab
):
    """歸納加入購物車送出後第一個可觀察結果。"""
    await tab.sleep(1.0)
    result = await _reconcile_utiki_purchase(
        tab,
        str(tab.target.url),
        _page_type(str(tab.target.url)),
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    return result == "success"


async def _reconcile_utiki_purchase(
    tab, current_url, page_type, config_dict, *, runtime_for_tab, purchase_key_for_tab
):
    """在執行頁面效果前協調延遲出現的送出結果。"""
    debug = util.create_debug_logger(config_dict)
    runtime = runtime_for_tab(tab)
    purchase = runtime["purchase"]
    snapshot = await read_dialog_snapshot(tab)
    if snapshot is None:
        debug.log("[CTBC] Submit dialog observation is invalid; action blocked")
        return "blocked"
    if snapshot.visible_count:
        current_key = purchase_key_for_tab(tab)
        submit_key, context = _ctbc_recent_submit_context(purchase, current_key)
        source_url = context.get("source_url", "") if isinstance(context, dict) else ""
        outcome = await _handle_ctbc_submit_dialog(
            tab,
            config_dict,
            submit_key=submit_key,
            source_url=source_url,
            dialog_snapshot=snapshot,
            runtime_for_tab=runtime_for_tab,
            purchase_key_for_tab=purchase_key_for_tab,
        )
        return "success" if outcome == "success" else "blocked"

    active = purchase.get("active_submit")
    context_key = None
    context = None
    if isinstance(active, dict):
        context_key, context = active.get("purchase_key"), active
    else:
        context_key, context = _ctbc_recent_submit_context(
            purchase, purchase_key_for_tab(tab)
        )
    if not isinstance(context, dict) or context_key is None:
        return None
    source_url = str(context.get("source_url") or "")
    if _ctbc_submit_reached_checkout(source_url, current_url):
        if active is None and not (
            context.get("kind") == "unknown"
            and context.get("reason") in {"submit_outcome", "dispatch_unknown"}
        ):
            return "success" if context.get("kind") == "success" else "blocked"
        record = _ctbc_purchase_outcome(
            "success",
            source_url=source_url,
            source_document_id=_ctbc_source_document_id(context),
            nonce=context.get("nonce", ""),
        )
        _ctbc_store_purchase_outcome(purchase, context_key, record, active=active)
        return "success"
    destination_key = purchase_key_for_tab(tab)
    if active is None:
        if destination_key != context_key:
            return None
        return (
            None
            if context.get("kind") in {"auth", "captcha", "seat_conflict"}
            else "blocked"
        )
    dispatched_at = _ctbc_submit_dispatched_at(active)
    if (
        dispatched_at
        and time.monotonic() - dispatched_at < CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS
    ):
        return "pending"
    _ctbc_finish_active_unknown(purchase, active, "submit_outcome")
    debug.log("[CTBC] Submit outcome is unknown; automatic resubmit is blocked")
    return "blocked"


async def _dispatch_ctbc_submit(
    tab, config_dict, submit_key, submit_plan, *, runtime_for_tab, reload_with_interval
):
    """送出一次加入購物車操作，並等待後續確認結果。"""
    debug = util.create_debug_logger(config_dict)
    runtime = runtime_for_tab(tab)
    purchase = runtime["purchase"]
    if purchase.get("active_submit") is not None:
        return False
    dispatched_at = time.monotonic()
    dispatch_state = {
        "purchase_key": submit_key,
        "nonce": uuid.uuid4().hex,
        "dispatched_at": dispatched_at,
        "phase": "dispatching",
        "source_url": str(tab.target.url),
        "source_document_id": str(runtime["document"]["last_document"] or ""),
        "document_id": submit_plan.document_id,
        "page_kind": submit_plan.page_kind.value,
        "product_id": str(getattr(submit_plan.selection_plan, "product_id", "") or ""),
        "payload_fingerprint": submit_plan.payload_fingerprint,
    }
    purchase["active_submit"] = dispatch_state
    try:
        result = await dispatch_submit(tab, submit_plan)
        if result is not SubmitDispatchResult.DISPATCHED:
            if purchase.get("active_submit") is not dispatch_state:
                return False
            debug.log(f"[CTBC] Submit dispatch {result.value}; click blocked")
            if (
                result is SubmitDispatchResult.STALE
                and str(tab.target.url) == submit_plan.page_url
            ):
                reason = (
                    CTBC_SEAT_STALE_RELOAD_REASON
                    if submit_plan.page_kind is PageKind.RESERVED_SEAT
                    else CTBC_QUANTITY_STALE_RELOAD_REASON
                )
                try:
                    await reload_with_interval(tab, config_dict, reason)
                finally:
                    if purchase.get("active_submit") is dispatch_state:
                        purchase["active_submit"] = None
            else:
                if purchase.get("active_submit") is dispatch_state:
                    purchase["active_submit"] = None
            return False
    except Exception as exc:
        _ctbc_finish_active_unknown(purchase, dispatch_state, "dispatch_unknown")
        debug.log(f"[CTBC] Submit dispatch outcome is unknown: {exc}")
        return False
    if purchase.get("active_submit") is not dispatch_state:
        return False
    purchase["last_submit"] = None
    dispatch_state["phase"] = "awaiting_result"
    return True


async def _ctbc_captcha_and_submit(
    tab,
    config_dict,
    ocr,
    *,
    submit_plan=None,
    runtime_for_tab,
    purchase_key_for_tab,
    reload_with_interval,
):
    """完成驗證碼辨識後送出已驗證的購票計畫。"""

    debug = util.create_debug_logger(config_dict)
    purchase = runtime_for_tab(tab)["purchase"]
    current_url = str(tab.target.url)
    submit_key = purchase_key_for_tab(tab)
    page_type = _page_type(current_url)
    if submit_key is None or page_type not in (
        "seat",
        "general_admission",
        "server_assigned_quantity",
    ):
        debug.log("[CTBC] Purchase identity is incomplete; automatic submit is blocked")
        return False
    if (
        not isinstance(submit_plan, SubmitPlan)
        or not submit_plan_is_valid(submit_plan)
        or submit_plan.page_kind.value != page_type
    ):
        debug.log("[CTBC] Submit plan does not match the purchase page")
        return False

    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab,
        config_dict,
        submit_key=submit_key,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    if dialog_outcome is not None:
        return dialog_outcome == "success"
    if await _recover_ctbc_captcha_dialog(
        tab,
        config_dict,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    ):
        return False
    active = purchase.get("active_submit")
    if isinstance(active, dict):
        dispatched_at = _ctbc_submit_dispatched_at(active)
        if (
            not dispatched_at
            or time.monotonic() - dispatched_at >= CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS
        ):
            _ctbc_finish_active_unknown(purchase, active, "submit_outcome")
            debug.log("[CTBC] Submit outcome is unknown; automatic resubmit is blocked")
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False

    try:
        login_ready = await _fill_ctbc_inline_login(tab, config_dict)
        if not login_ready:
            debug.log("[CTBC LOGIN] Waiting for login or CTBC account settings")
            return False
        login_fingerprint = await _ctbc_login_fingerprint(tab)
    except Exception as exc:
        debug.log(
            "[CTBC LOGIN] Credential observation failed; "
            f"previous authentication blocker retained: {exc}"
        )
        return False
    if _ctbc_auth_failure_is_active(
        tab, submit_key, login_fingerprint, runtime_for_tab=runtime_for_tab
    ):
        debug.log(
            "[CTBC LOGIN] Previous authentication failure; waiting for credentials to change"
        )
        return False

    captcha_value = await tab.evaluate(
        """
        (() => {
            const input = document.querySelector('#CHK');
            return input ? input.value.trim() : '';
        })()
    """
    )
    captcha_ready = isinstance(captcha_value, str) and len(captcha_value) == 4
    if not captcha_ready and config_dict.get("ocr_captcha", {}).get("enable", False):
        captcha_ready = await _ctbc_captcha(tab, config_dict, ocr)

    # With OCR disabled, keep the selections and wait for manual input.  The
    # next loop iteration detects the four-character value and continues.
    if not captcha_ready:
        return False

    # Match the existing OCR setting semantics: OCR may fill the field while
    # the final submit remains manual unless "force submit" is enabled.
    ocr_config = config_dict.get("ocr_captcha", {})
    if not ocr_config.get("enable", False) or not ocr_config.get("force_submit", False):
        return True

    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab,
        config_dict,
        submit_key=submit_key,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )
    if dialog_outcome is not None:
        return dialog_outcome == "success"
    if purchase.get("active_submit") is not None:
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict):
        return False

    if not await _dispatch_ctbc_submit(
        tab,
        config_dict,
        submit_key,
        submit_plan,
        runtime_for_tab=runtime_for_tab,
        reload_with_interval=reload_with_interval,
    ):
        return False
    debug.log("[CTBC] Add-to-cart submitted")
    return await _observe_ctbc_submit_outcome(
        tab,
        config_dict,
        current_url,
        submit_key,
        runtime_for_tab=runtime_for_tab,
        purchase_key_for_tab=purchase_key_for_tab,
    )


async def _handle_ctbc_checkout(
    tab, config_dict, *, runtime_for_tab, tab_state_key_for_tab
):
    """處理中信相容售票的結帳頁狀態。"""

    purchase = runtime_for_tab(tab)["purchase"]
    purchase_key, context = _ctbc_recent_submit_context(purchase)
    if purchase_key is not None and isinstance(context, dict) and context.get("nonce"):
        checkout_key = ("submit", purchase_key, str(context["nonce"]))
    else:
        checkout_key = ("location", tab_state_key_for_tab(tab))
    checkout_notified = purchase["checkout_notified"]
    if checkout_key in checkout_notified:
        return
    checkout_notified.add(checkout_key)
    if config_dict.get("advanced", {}).get("play_sound", {}).get("order", False):
        play_sound_while_ordering(config_dict)
    send_discord_notification(config_dict, "order", "CTBC Sports")
    send_telegram_notification(config_dict, "order", "CTBC Sports")
