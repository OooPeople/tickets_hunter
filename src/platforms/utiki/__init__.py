"""Public API for the shared uTiki2 sports ticketing engine."""

from .profiles import (
    PageKind,
    TenantProfile,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
)
from .workflow import (
    nodriver_ctbc_inject_cookies,
    nodriver_ctbc_main,
    nodriver_utk_sports_cancel_refresh,
    nodriver_utk_sports_cleanup_tab,
    nodriver_utk_sports_consume_reload_event,
    nodriver_utk_sports_health_check,
    nodriver_utk_sports_pause_refresh,
    nodriver_utk_sports_reload,
    nodriver_utk_sports_reload_wait,
)


nodriver_utiki_main = nodriver_ctbc_main
is_utk_sports_url = is_utiki_sports_url

__all__ = [
    "PageKind",
    "TenantProfile",
    "classify_utiki_page",
    "get_utiki_tenant_profile",
    "is_utiki_sports_url",
    "is_utk_sports_url",
    "nodriver_ctbc_inject_cookies",
    "nodriver_utiki_main",
    "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_cleanup_tab",
    "nodriver_utk_sports_consume_reload_event",
    "nodriver_utk_sports_health_check",
    "nodriver_utk_sports_pause_refresh",
    "nodriver_utk_sports_reload",
    "nodriver_utk_sports_reload_wait",
]
