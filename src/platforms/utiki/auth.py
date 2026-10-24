"""Pure tenant authentication policy for the uTiki2 sports family."""

import json
from dataclasses import dataclass
from types import MappingProxyType

from .profiles import CTBC_DOMAIN, get_utiki_tenant_profile


@dataclass(frozen=True)
class TenantAuthProfile:
    tenant_key: str
    cookie_config_key: str = ""
    cookie_scope_domain: str = ""
    cookie_set_host: str = ""
    cookie_name_prefix: str = ""
    cookie_name_separator: str = ""
    cookie_reload_reason: str = ""
    account_key: str = ""
    password_key: str = ""
    inline_login_origin: str = ""

    def __post_init__(self):
        cookie_fields = (
            self.cookie_config_key,
            self.cookie_scope_domain,
            self.cookie_set_host,
            self.cookie_name_prefix,
            self.cookie_reload_reason,
        )
        if (any(cookie_fields) or self.cookie_name_separator) and not all(cookie_fields):
            raise ValueError("Cookie login policy must specify all required fields")
        inline_fields = (
            self.account_key, self.password_key, self.inline_login_origin
        )
        if any(inline_fields) and not all(inline_fields):
            raise ValueError("Inline login policy must specify all required fields")

    @property
    def supports_cookie_login(self):
        """判斷目前租戶是否支援 Cookie 登入。"""

        return bool(self.cookie_config_key)

    @property
    def supports_inline_login(self):
        """判斷目前租戶是否支援頁面內帳號密碼登入。"""

        return bool(
            self.account_key and self.password_key and self.inline_login_origin
        )


_AUTH_PROFILES = (
    TenantAuthProfile(
        tenant_key="ctbc_brothers",
        cookie_reload_reason="apply CTBC cookie session",
        cookie_config_key="ctbc_cookie",
        cookie_scope_domain="ctbcsports.com",
        cookie_set_host=CTBC_DOMAIN,
        cookie_name_prefix="CTBC_",
        cookie_name_separator="|",
        account_key="ctbc_account",
        password_key="ctbc_password",
        inline_login_origin=f"https://{CTBC_DOMAIN}",
    ),
    TenantAuthProfile(
        tenant_key="wei_chuan_dragons",
        cookie_reload_reason="apply WDragons cookie session",
        cookie_config_key="wdragons_cookie",
        cookie_scope_domain="wdragons.com",
        cookie_set_host="tix.wdragons.com",
        cookie_name_prefix="WDRAGONS|",
    ),
)
TENANT_AUTH_PROFILES = MappingProxyType({
    profile.tenant_key: profile for profile in _AUTH_PROFILES
})


def get_tenant_auth_profile(url):
    """依售票網址取得對應租戶的登入設定。"""

    tenant = get_utiki_tenant_profile(url)
    return TENANT_AUTH_PROFILES.get(tenant.key) if tenant else None


def cookie_domain_is_in_scope(auth_profile, cookie_domain):
    """判斷 Cookie 網域是否屬於租戶允許的範圍。"""

    if not auth_profile or not auth_profile.cookie_scope_domain:
        return False
    domain = str(cookie_domain or "").lstrip(".").lower()
    scope = auth_profile.cookie_scope_domain
    return domain == scope or domain.endswith(f".{scope}")


def _cookie_pairs_from_json(raw):
    """從 JSON 匯出內容讀取 Cookie 名稱與值。"""

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        if isinstance(parsed.get("cookies"), list):
            parsed = parsed["cookies"]
        else:
            parsed = [
                {"name": name, "value": value}
                for name, value in parsed.items()
            ]
    if not isinstance(parsed, list):
        return []
    return [
        (str(item.get("name", "")).strip(), str(item.get("value", "")))
        for item in parsed
        if isinstance(item, dict)
    ]


def parse_utiki_cookie_string(raw_value, auth_profile):
    """依租戶的名稱規則解析 Cookie 標頭或 JSON 匯出內容。"""
    if not auth_profile or not auth_profile.cookie_name_prefix:
        return []
    raw = str(raw_value or "").strip()
    if not raw:
        return []

    cookie_pairs = []
    if raw.startswith("{") or raw.startswith("["):
        cookie_pairs = _cookie_pairs_from_json(raw)

    if not cookie_pairs:
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1].strip()
        for part in raw.replace("\r", ";").replace("\n", ";").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator:
                cookie_pairs.append((name.strip(), value.strip()))

    result = []
    seen = set()
    for name, value in cookie_pairs:
        if (
            not name.startswith(auth_profile.cookie_name_prefix)
            or (
                auth_profile.cookie_name_separator
                and auth_profile.cookie_name_separator not in name
            )
            or not value
            or name in seen
        ):
            continue
        result.append((name, value))
        seen.add(name)
    return result
