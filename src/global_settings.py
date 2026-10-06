#!/usr/bin/env python3
# encoding=utf-8
"""Process-shared settings that must not be copied into instance profiles."""

import copy
import hashlib
import json
import math
import os
import tempfile
import time

import util


GLOBAL_SETTINGS_FILENAME = "global_settings.json"

_DEFAULT_GLOBAL_SETTINGS = {
    "utk_sports": {
        "enabled": True,
        "min_interval_seconds": 1.5,
        "initial_cooldown_seconds": 60,
        "max_cooldown_seconds": 240,
    }
}
_CACHE_TTL_SECONDS = 0.5
_cached_path = ""
_cached_until = 0.0
_cached_value = None


def get_default_global_settings():
    """Return an isolated copy so callers cannot mutate module defaults."""
    return copy.deepcopy(_DEFAULT_GLOBAL_SETTINGS)


def get_global_settings_path():
    return os.path.join(util.get_app_root(), GLOBAL_SETTINGS_FILENAME)


def _number(value, field_name, *, minimum, integer=False, exclusive_minimum=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    number = float(value)
    below_minimum = number <= minimum if exclusive_minimum else number < minimum
    if not math.isfinite(number) or below_minimum:
        comparison = "greater than" if exclusive_minimum else "at least"
        raise ValueError(f"{field_name} must be {comparison} {minimum:g}")
    if integer:
        if not number.is_integer():
            raise ValueError(f"{field_name} must be an integer")
        return int(number)
    return number


def validate_global_settings(value):
    """Validate and normalize the public global-settings schema.

    Unknown keys are intentionally discarded. This keeps the shared file small
    and prevents profile data or credentials from being persisted here.
    """
    if not isinstance(value, dict):
        raise ValueError("global settings must be an object")
    utk_sports = value.get("utk_sports")
    if not isinstance(utk_sports, dict):
        raise ValueError("utk_sports must be an object")

    enabled = utk_sports.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("utk_sports.enabled must be a boolean")
    min_interval = _number(
        utk_sports.get("min_interval_seconds"),
        "utk_sports.min_interval_seconds",
        minimum=0.0,
        exclusive_minimum=True,
    )
    initial_cooldown = _number(
        utk_sports.get("initial_cooldown_seconds"),
        "utk_sports.initial_cooldown_seconds",
        minimum=1.0,
        integer=True,
    )
    max_cooldown = _number(
        utk_sports.get("max_cooldown_seconds"),
        "utk_sports.max_cooldown_seconds",
        minimum=1.0,
        integer=True,
    )
    if max_cooldown < initial_cooldown:
        raise ValueError(
            "utk_sports.max_cooldown_seconds must be greater than or equal to "
            "utk_sports.initial_cooldown_seconds"
        )

    return {
        "utk_sports": {
            "enabled": enabled,
            "min_interval_seconds": min_interval,
            "initial_cooldown_seconds": initial_cooldown,
            "max_cooldown_seconds": max_cooldown,
        }
    }


def global_settings_revision(value):
    """Return a stable content revision for optimistic concurrency control."""
    normalized = validate_global_settings(value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_global_settings(path=None):
    """Read shared settings with a short cache and safe fallback."""
    global _cached_path, _cached_until, _cached_value
    settings_path = path or get_global_settings_path()
    use_cache = path is None
    now = time.monotonic()
    if (
        use_cache
        and _cached_value is not None
        and _cached_path == settings_path
        and now < _cached_until
    ):
        return copy.deepcopy(_cached_value)
    try:
        with open(settings_path, encoding="utf-8") as settings_file:
            value = validate_global_settings(json.load(settings_file))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        value = get_default_global_settings()
    if use_cache:
        _cached_path = settings_path
        _cached_until = now + _CACHE_TTL_SECONDS
        _cached_value = copy.deepcopy(value)
    return value


def save_global_settings(value, path=None):
    """Validate and atomically replace the process-shared settings file."""
    global _cached_path, _cached_until, _cached_value
    normalized = validate_global_settings(value)
    settings_path = path or get_global_settings_path()
    settings_dir = os.path.dirname(settings_path)
    os.makedirs(settings_dir, exist_ok=True)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=settings_dir,
            prefix=".global_settings_",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(normalized, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, settings_path)
        temp_path = None
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    if path is None:
        _cached_path = settings_path
        _cached_until = time.monotonic() + _CACHE_TTL_SECONDS
        _cached_value = copy.deepcopy(normalized)
    return normalized
