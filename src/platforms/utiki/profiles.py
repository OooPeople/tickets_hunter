"""Tenant identity and route classification for the uTiki2 sports family."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from urllib.parse import SplitResult, parse_qsl, urlsplit
from utiki_config import is_supported_utiki_sports_url


class PageKind(str, Enum):
    UNKNOWN = "unknown"
    DATE = "date"
    AREA = "area"
    RESERVED_SEAT = "seat"
    GENERAL_ADMISSION = "general_admission"
    SERVER_ASSIGNED_QUANTITY = "server_assigned_quantity"
    CHECKOUT = "checkout"


@dataclass(frozen=True)
class TenantProfile:
    key: str
    name: str
    host: str
    tenant_path: str
    map_namespace: str
    fast_area_prefetch: bool
    supports_server_assignment: bool = False


CTBC_DOMAIN = "tix.ctbcsports.com"

_TENANTS = (
    TenantProfile(
        key="ctbc_brothers",
        name="CTBC Sports",
        host=CTBC_DOMAIN,
        tenant_path="/BROTHERS",
        map_namespace="CTBC_SPORTS",
        fast_area_prefetch=True,
        supports_server_assignment=True,
    ),
    TenantProfile(
        key="wei_chuan_dragons",
        name="Wei Chuan Dragons",
        host="tix.wdragons.com",
        tenant_path="",
        map_namespace="WDRAGONS",
        fast_area_prefetch=False,
    ),
)

TENANT_PROFILES = MappingProxyType({profile.host: profile for profile in _TENANTS})


def get_utiki_tenant_profile(url: str | None) -> TenantProfile | None:
    """依精確的安全來源取得對應 uTiki 租戶。"""
    try:
        parsed = urlsplit(str(url or ""))
        host = (parsed.hostname or "").lower()
        if not is_supported_utiki_sports_url(url):
            return None
    except ValueError:
        return None
    return TENANT_PROFILES.get(host)


def is_utiki_sports_url(url: str | None) -> bool:
    """判斷網址是否屬於支援的 uTiki 棒球售票站。"""

    return get_utiki_tenant_profile(url) is not None


def unique_query(url: str) -> dict[str, str] | None:
    """僅在每個查詢鍵都唯一時回傳查詢參數字典。"""
    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return None
    if len({key for key, _value in pairs}) != len(pairs):
        return None
    return dict(pairs)


def classify_utiki_page(url: str) -> PageKind:
    """依網址判別 uTiki 售票頁面類型。"""

    profile = get_utiki_tenant_profile(url)
    if profile is None:
        return PageKind.UNKNOWN
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return PageKind.UNKNOWN
    if parsed.fragment:
        return PageKind.UNKNOWN

    query = unique_query(url)
    if query is None:
        return PageKind.UNKNOWN

    server_assignment_path = f"{profile.tenant_path}/UTK0201_001"
    if parsed.path == server_assignment_path:
        required_keys = {
            "PERFORMANCE_ID",
            "GROUP_ID",
            "PERFORMANCE_PRICE_AREA_ID",
            "ATYPE",
        }
        token_keys = required_keys - {"ATYPE"}
        if (
            set(query) == required_keys
            and query.get("ATYPE") == "N"
            and all(
                re.fullmatch(r"[A-Za-z0-9_-]+", str(query.get(key) or ""))
                for key in token_keys
            )
        ):
            return PageKind.SERVER_ASSIGNED_QUANTITY
        return PageKind.UNKNOWN

    route_specs: dict[str, tuple[PageKind, str | None]] = {
        f"{profile.tenant_path}/UTK0201_": (PageKind.DATE, "PRODUCT_ID"),
        f"{profile.tenant_path}/UTK0202_": (
            PageKind.GENERAL_ADMISSION,
            "PERFORMANCE_ID",
        ),
        f"{profile.tenant_path}/UTK0203_": (
            PageKind.GENERAL_ADMISSION,
            "PERFORMANCE_ID",
        ),
        f"{profile.tenant_path}/UTK0204_": (PageKind.AREA, "PERFORMANCE_ID"),
        f"{profile.tenant_path}/UTK0205_": (
            PageKind.RESERVED_SEAT,
            "PERFORMANCE_ID",
        ),
        f"{profile.tenant_path}/UTK0206_": (PageKind.CHECKOUT, None),
    }
    route = route_specs.get(parsed.path)
    if route is None:
        return PageKind.UNKNOWN
    page_kind, required_key = route
    if required_key and not re.fullmatch(
        r"[A-Za-z0-9_-]+", str(query.get(required_key) or "")
    ):
        return PageKind.UNKNOWN
    return page_kind


def same_origin(first: SplitResult, second: SplitResult) -> bool:
    """拒絕內嵌憑證後比較兩個網址是否同源。"""
    if first.username or first.password or second.username or second.password:
        return False
    try:
        first_port = first.port or (443 if first.scheme == "https" else 80)
        second_port = second.port or (443 if second.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        first.scheme.lower(),
        (first.hostname or "").lower(),
        first_port,
    ) == (
        second.scheme.lower(),
        (second.hostname or "").lower(),
        second_port,
    )
