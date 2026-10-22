"""Public tenant and page classification API for the uTiki2 family."""

from .profiles import (
    PageKind,
    TenantProfile,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
)


__all__ = [
    "PageKind",
    "TenantProfile",
    "classify_utiki_page",
    "get_utiki_tenant_profile",
    "is_utiki_sports_url",
]
