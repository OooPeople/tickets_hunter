"""Public API for the shared uTiki2 sports ticketing engine."""

from importlib import import_module

from .profiles import (
    PageKind,
    TenantProfile,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
)


is_utk_sports_url = is_utiki_sports_url

_WORKFLOW_EXPORTS = {
    "nodriver_ctbc_inject_cookies": "nodriver_ctbc_inject_cookies",
    "nodriver_ctbc_main": "nodriver_ctbc_main",
    "nodriver_utiki_main": "nodriver_ctbc_main",
    "nodriver_utk_sports_cancel_refresh": "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_cleanup_tab": "nodriver_utk_sports_cleanup_tab",
    "nodriver_utk_sports_consume_reload_event": "nodriver_utk_sports_consume_reload_event",
    "nodriver_utk_sports_health_check": "nodriver_utk_sports_health_check",
    "nodriver_utk_sports_pause_refresh": "nodriver_utk_sports_pause_refresh",
    "nodriver_utk_sports_reload": "nodriver_utk_sports_reload",
    "nodriver_utk_sports_reload_wait": "nodriver_utk_sports_reload_wait",
}


def __getattr__(name: str) -> object:
    target = _WORKFLOW_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    workflow = import_module(".workflow", __name__)
    value = getattr(workflow, target)
    globals()[name] = value
    return value

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
