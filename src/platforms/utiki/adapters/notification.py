"""Checkout notification launcher for the isolated uTiki Run.

The existing helpers return after starting background work.  Their return value
cannot attest delivery; the engine records only a launch attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module


class CheckoutNotificationAdapter:
    def __init__(self, settings: Mapping[str, object]) -> None:
        self._settings = dict(settings)

    def start_checkout_notification(self) -> None:
        common = import_module("nodriver_common")
        advanced = self._settings.get("advanced")
        if isinstance(advanced, dict):
            sound = advanced.get("play_sound")
            if isinstance(sound, dict) and sound.get("order"):
                common.play_sound_while_ordering(self._settings)
        common.send_discord_notification(self._settings, "order", "CTBC Sports")
        common.send_telegram_notification(self._settings, "order", "CTBC Sports")
