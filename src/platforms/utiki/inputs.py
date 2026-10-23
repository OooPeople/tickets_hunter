# encoding=utf-8
"""Existing uTiki login and CAPTCHA input/browser operations."""

import base64
import hashlib
import json
import re

from zendriver import cdp

import util
from nodriver_common import nodriver_get_captcha_image_from_dom_snapshot
from .auth import (
    cookie_domain_is_in_scope,
    get_tenant_auth_profile,
    parse_ctbc_cookie_string,
)
from .selection import DialogSnapshot


async def _query_ctbc_captcha_input(tab):
    return await tab.query_selector("#CHK")


async def _clear_ctbc_captcha_input(captcha_input):
    return await captcha_input.apply('function(el) { el.value = ""; }')


async def _query_ctbc_captcha_refresh(tab):
    return await tab.query_selector("#chang_pic")


async def _click_ctbc_captcha_refresh(refresh):
    return await refresh.click()


async def nodriver_ctbc_inject_cookies(tab, config_dict):
    """注入複製的中信售票 Cookie，並保留帳號密碼備援。"""
    debug = util.create_debug_logger(config_dict)
    auth_profile = get_tenant_auth_profile(str(tab.target.url))
    if not auth_profile or not auth_profile.supports_cookie_login:
        debug.log("[CTBC COOKIE] Cookie login is unavailable for this tenant")
        return False
    raw_value = config_dict.get("accounts", {}).get(auth_profile.cookie_config_key, "")
    cookies = parse_ctbc_cookie_string(raw_value)
    if not cookies:
        if str(raw_value or "").strip():
            debug.log("[CTBC COOKIE] No valid CTBC session cookies were found")
        return False

    try:
        existing_cookies = await tab.browser.cookies.get_all()
        for cookie in existing_cookies:
            if cookie.name.startswith(
                auth_profile.cookie_name_prefix
            ) and cookie_domain_is_in_scope(auth_profile, cookie.domain):
                try:
                    await tab.send(
                        cdp.network.delete_cookies(
                            name=cookie.name,
                            domain=cookie.domain,
                            path=cookie.path or "/",
                        )
                    )
                except Exception:
                    pass
    except Exception as exc:
        debug.log(f"[CTBC COOKIE] Could not clear the current CTBC cookie set: {exc}")

    injected = 0
    for name, value in cookies:
        try:
            result = await tab.send(
                cdp.network.set_cookie(
                    name=name,
                    value=value,
                    domain=auth_profile.cookie_set_host,
                    path="/",
                    secure=True,
                    http_only=True,
                )
            )
            if result is not False:
                injected += 1
        except Exception as exc:
            debug.log(f"[CTBC COOKIE] Failed to inject {name}: {exc}")

    if injected != len(cookies):
        debug.log(f"[CTBC COOKIE] Injected {injected}/{len(cookies)} cookies")
        return False
    debug.log(f"[CTBC COOKIE] Injected {injected} session cookies")
    return True


async def _fill_ctbc_inline_login(tab, config_dict):
    """在支援的租戶頁面填入並送出登入資料。"""

    state_raw = await tab.evaluate(
        """
        (() => {
            const input = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!input) return {required: false};
            const style = getComputedStyle(input);
            const required = style.display !== 'none' && style.visibility !== 'hidden' && input.offsetParent !== null;
            return {
                required: required,
                account: input.value || '',
                password: password ? password.value : ''
            };
        })()
    """
    )
    state = util.parse_nodriver_result(state_raw)
    if not isinstance(state, dict) or not state.get("required", False):
        return True
    if state.get("account") and state.get("password"):
        return True

    auth_profile = get_tenant_auth_profile(str(tab.target.url))
    if not auth_profile or not auth_profile.supports_inline_login:
        return False
    accounts = config_dict.get("accounts", {})
    account = str(
        state.get("account") or accounts.get(auth_profile.account_key, "")
    ).strip()
    password = str(
        state.get("password") or accounts.get(auth_profile.password_key, "")
    ).strip()
    if not account or not password:
        return False

    result = await tab.evaluate(
        f"""
        (() => {{
            const expectedOrigin = {json.dumps(auth_profile.inline_login_origin)};
            const currentUrl = new URL(location.href);
            if (
                currentUrl.origin !== expectedOrigin ||
                currentUrl.username ||
                currentUrl.password
            ) return false;
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!account || !password) return false;
            account.value = {json.dumps(account)};
            password.value = {json.dumps(password)};
            account.dispatchEvent(new Event('input', {{bubbles: true}}));
            password.dispatchEvent(new Event('input', {{bubbles: true}}));
            return true;
        }})()
    """
    )
    return bool(result)


async def _ctbc_login_fingerprint(tab, *, expected_dialog=None):
    """建立登入對話框與目前文件的識別指紋。"""

    if expected_dialog is not None and not isinstance(expected_dialog, DialogSnapshot):
        return ""
    expected_json = "null"
    if expected_dialog is not None:
        expected_json = json.dumps(
            {
                "pageUrl": expected_dialog.page_url,
                "documentId": expected_dialog.document_id,
                "text": expected_dialog.text,
            },
            ensure_ascii=False,
        )
    raw = await tab.evaluate(
        f"""
        (() => {{
            const expectedDialog = {expected_json};
            if (expectedDialog) {{
                if (location.href !== expectedDialog.pageUrl ||
                    String(performance.timeOrigin) !==
                        expectedDialog.documentId) {{
                    return JSON.stringify({{ok: false}});
                }}
                const visible = element => {{
                    if (!element || !element.isConnected || element.hidden ||
                        element.getAttribute('aria-hidden') === 'true') {{
                        return false;
                    }}
                    const style = getComputedStyle(element);
                    return style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        element.getClientRects().length > 0;
                }};
                const dialogs = Array.from(
                    document.querySelectorAll('#dialog-message'))
                    .map(message => ({{
                        message,
                        wrapper: message.closest('.ui-dialog')
                    }}))
                    .filter(item =>
                        visible(item.wrapper) && visible(item.message));
                if (dialogs.length !== 1 ||
                    (dialogs[0].message.textContent || '').trim() !==
                        expectedDialog.text) {{
                    return JSON.stringify({{ok: false}});
                }}
            }}
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            return JSON.stringify({{
                ok: true,
                account: account ? account.value : '',
                password: password ? password.value : ''
            }});
        }})()
    """
    )
    try:
        credentials = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        credentials = {}
    if (
        credentials.get("ok") is not True
        or not credentials.get("account")
        or not credentials.get("password")
    ):
        return ""
    material = f"{credentials['account']}\0{credentials['password']}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _ctbc_captcha_image_token(image_bytes):
    """在不保留圖片位元組下建立驗證碼圖片識別。"""
    if not isinstance(image_bytes, (bytes, bytearray, memoryview)) or not image_bytes:
        return ""
    return hashlib.sha256(bytes(image_bytes)).hexdigest()


async def _read_ctbc_captcha_image_token(tab, config_dict):
    """讀取目前驗證碼圖片的非敏感識別。"""

    try:
        image_bytes = await _get_ctbc_captcha_image_bytes(tab, config_dict)
    except Exception:
        return ""
    return _ctbc_captcha_image_token(image_bytes)


def _normalize_ctbc_captcha_answer(answer):
    """將 OCR 結果正規化為四個大寫 ASCII 字元。"""
    return re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()


async def _get_ctbc_captcha_image_bytes(tab, config_dict):
    """以原始 90×25 解析度擷取同源驗證碼圖片。"""
    debug = util.create_debug_logger(config_dict)
    try:
        data_url = await tab.evaluate(
            """
            (() => {
                const image = document.querySelector('img#chk_pic');
                if (!image || !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) {
                    return '';
                }
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                const context = canvas.getContext('2d');
                if (!context) return '';
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                return canvas.toDataURL('image/png');
            })()
        """
        )
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            _header, separator, encoded = data_url.partition(",")
            if separator and encoded:
                image_bytes = base64.b64decode(encoded, validate=True)
                if image_bytes:
                    debug.log(f"[CTBC CAPTCHA] Native image: {len(image_bytes)} bytes")
                    return image_bytes
    except Exception as exc:
        debug.log(f"[CTBC CAPTCHA] Native image capture failed: {exc}")

    # Keep the shared DOM screenshot path as a compatibility fallback for
    # browsers that block canvas extraction or change the captcha origin.
    return await nodriver_get_captcha_image_from_dom_snapshot(
        tab, config_dict, preferred_image_id="chk_pic"
    )


async def _ctbc_captcha(tab, config_dict, ocr):
    """讀取並辨識目前頁面的中信相容驗證碼。"""

    debug = util.create_debug_logger(config_dict)
    if not ocr:
        debug.log("[CTBC CAPTCHA] OCR engine is unavailable")
        return False

    for _ in range(10):
        image_ready = await tab.evaluate(
            """
            (() => {
                const image = document.querySelector('img#chk_pic');
                return !!(image && image.src && image.complete && image.naturalWidth > 0);
            })()
        """
        )
        if image_ready:
            break
        await tab.sleep(0.1)

    image_bytes = await _get_ctbc_captcha_image_bytes(tab, config_dict)
    if not image_bytes:
        debug.log("[CTBC CAPTCHA] Captcha image was not captured")
        return False
    try:
        answer = _normalize_ctbc_captcha_answer(ocr.classification(image_bytes))
    except Exception as exc:
        debug.log(f"[CTBC CAPTCHA] OCR failed: {exc}")
        return False
    if len(answer) != 4:
        debug.log(f"[CTBC CAPTCHA] OCR returned an invalid length: {len(answer)}")
        refresh = await tab.query_selector("#chang_pic")
        if refresh:
            await refresh.click()
            await tab.sleep(0.3)
        return False

    captcha_input = await tab.query_selector("#CHK")
    if not captcha_input:
        return False
    await captcha_input.click()
    await captcha_input.apply('function(el) { el.value = ""; }')
    await captcha_input.send_keys(answer)
    debug.log("[CTBC CAPTCHA] Four-character answer filled")
    return True
