"""Shared pure keyword parsing and matching for ticket platforms."""

from __future__ import annotations

import json
from typing import cast


def format_keyword_string(keyword: str | None) -> str | None:
    """Preserve exact matching while removing only full-width spaces."""
    if keyword is None:
        return None
    return keyword.replace("　", "") if keyword else keyword


def parse_keyword_string_to_array(keyword_string: str | None) -> list[object]:
    """Parse the existing JSON fragment format; malformed input has no groups."""
    if not keyword_string or not keyword_string.strip():
        return []
    try:
        parsed: object = json.loads("[" + keyword_string + "]")
    except Exception:
        return []
    return cast(list[object], parsed) if isinstance(parsed, list) else []


def is_row_match_keyword(keyword_string: str, row_text: str) -> bool:
    """Match OR groups whose space-separated members are AND conditions."""
    formatted = format_keyword_string(row_text)
    if formatted is None:
        raise TypeError("row text must be a string")
    if not keyword_string or not formatted:
        return True
    for group in parse_keyword_string_to_array(keyword_string):
        if not isinstance(group, str):
            if isinstance(group, (list, dict)) and not group:
                return True
            raise TypeError("keyword group must be a string")
        if not group:
            return True
        if " " in group:
            if all(
                (format_keyword_string(part) or "") in formatted
                for part in group.split(" ")
            ):
                return True
        elif (format_keyword_string(group) or "") in formatted:
            return True
    return False


def is_keyword_excluded(keyword_exclude: str, row_text: str) -> bool:
    """An absent exclusion never matches, unlike an empty positive keyword."""
    return bool(keyword_exclude) and is_row_match_keyword(keyword_exclude, row_text)
