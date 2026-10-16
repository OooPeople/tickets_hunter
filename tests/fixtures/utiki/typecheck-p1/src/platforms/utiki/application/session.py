from __future__ import annotations

from collections.abc import Iterable


def collect_operations(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(values)
