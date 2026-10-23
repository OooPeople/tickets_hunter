# encoding=utf-8
"""Observe and classify the current uTiki browser document."""

import json
import math
import re
import time

import util
from .profiles import PageKind, classify_utiki_page
from .runtime import TabRuntime


def _classify_utk_document_state(snapshot):
    """依文件快照判別正常、載入中或限流狀態。"""

    if not isinstance(snapshot, dict) or not snapshot.get("ready"):
        return "unknown"
    body_text = str(snapshot.get("body_text") or "")
    has_code = bool(
        re.search(r"(^|\s|[:：])(?:HTTP\s*)?429(?:\s|[-:：]|$)", body_text, re.I)
    )
    has_message = bool(re.search(r"請求頻率過高|Too Many Requests", body_text, re.I))
    if has_code and has_message and not snapshot.get("has_normal_root"):
        return "blocked"
    if snapshot.get("has_normal_root"):
        return "normal"
    if not snapshot.get("requires_normal_root", True):
        return "unverified"
    return "unknown"


def _decode_json_result(raw_value):
    """將瀏覽器回傳值解碼為 JSON 資料。"""

    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _canonical_document_token(value):
    """取得非零的標準文件時間來源識別，無效時採保守失敗。"""
    token = str(value or "").strip()
    try:
        numeric = float(token)
    except (TypeError, ValueError):
        return ""
    return token if math.isfinite(numeric) and numeric > 0 else ""


async def _inspect_utk_document(tab, runtime: TabRuntime, url=None, *, force=False):
    """判別目前文件，避免把載入中狀態誤認為 HTTP 429。"""
    current_url = str(url or getattr(getattr(tab, "target", None), "url", ""))
    document = runtime["document"]
    cached = document["snapshot"]
    if (
        not force
        and cached
        and cached.get("url") == current_url
        and time.monotonic() - cached.get("checked_at", 0.0) < 0.2
    ):
        return dict(cached["snapshot"])
    normal_selector = {
        PageKind.DATE: "#PerformanceListTable",
        PageKind.AREA: ".seats, #mapdata",
        PageKind.RESERVED_SEAT: "#TBL, .ticket",
        PageKind.GENERAL_ADMISSION: "#table_tickettype, .ticket",
        PageKind.SERVER_ASSIGNED_QUANTITY: "#table_tickettype",
    }.get(classify_utiki_page(current_url))
    selector_expression = (
        f"!!document.querySelector({json.dumps(normal_selector)})"
        if normal_selector
        else "false"
    )
    try:
        raw_value = await tab.evaluate(
            f"""
            (() => {{
                const ready = document.readyState === 'interactive' ||
                    document.readyState === 'complete';
                const bodyText = document.body
                    ? (document.body.innerText || document.body.textContent || '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 12000)
                    : '';
                const normalRoot = {selector_expression};
                const timeOrigin = Number(performance.timeOrigin ||
                    (performance.timing && performance.timing.navigationStart) || 0);
                const documentToken = Number.isFinite(timeOrigin) && timeOrigin > 0
                    ? String(timeOrigin) : '';
                return JSON.stringify({{
                    ready,
                    body_text: bodyText,
                    document_id: documentToken,
                    document_started_at: timeOrigin > 0 ? timeOrigin / 1000 : 0,
                    url: location.href,
                    ready_state: document.readyState,
                    has_normal_root: normalRoot,
                    requires_normal_root: {str(bool(normal_selector)).lower()}
                }});
            }})()
        """
        )
        snapshot = _decode_json_result(raw_value)
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        return {
            "classification": "unknown",
            "document_id": "",
            "document_started_at": 0.0,
            "url": current_url,
            "ready_state": "",
            "has_normal_root": False,
            "requires_normal_root": bool(normal_selector),
        }
    snapshot["document_id"] = _canonical_document_token(snapshot.get("document_id"))
    try:
        snapshot["document_started_at"] = max(
            float(snapshot.get("document_started_at") or 0.0), 0.0
        )
    except (TypeError, ValueError):
        snapshot["document_started_at"] = 0.0
    snapshot["url"] = str(snapshot.get("url") or current_url)
    snapshot["classification"] = (
        _classify_utk_document_state(snapshot) if snapshot["document_id"] else "unknown"
    )
    document["snapshot"] = {
        "url": snapshot["url"],
        "checked_at": time.monotonic(),
        "snapshot": dict(snapshot),
    }
    return snapshot
