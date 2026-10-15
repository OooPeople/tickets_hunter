"""Compatibility facade for the renamed uTiki2 sports engine."""

from platforms.utiki.workflow import (
    is_utk_sports_url,
    nodriver_ctbc_inject_cookies,
    nodriver_ctbc_main,
    nodriver_utk_sports_cancel_refresh,
    nodriver_utk_sports_consume_reload_event,
    nodriver_utk_sports_health_check,
    nodriver_utk_sports_pause_refresh,
    nodriver_utk_sports_reload,
    nodriver_utk_sports_reload_wait,
)


__all__ = [
    "is_utk_sports_url",
    "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_consume_reload_event",
    "nodriver_utk_sports_health_check",
    "nodriver_utk_sports_pause_refresh",
    "nodriver_ctbc_inject_cookies",
    "nodriver_ctbc_main",
    "nodriver_utk_sports_reload",
    "nodriver_utk_sports_reload_wait",
]
