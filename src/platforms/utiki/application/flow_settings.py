"""Validated selection inputs for the first offline uTiki flow slice."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.snapshots import MAX_SAFE_COUNT


@dataclass(frozen=True, slots=True)
class FlowSettings:
    date_enabled: bool
    area_enabled: bool
    ticket_number: int
    date_keyword: str = ""
    date_mode: str = "from top to bottom"
    date_fallback: bool = False
    area_keyword: str = ""
    area_mode: str = "from top to bottom"
    area_fallback: bool = False
    keyword_exclude: str = ""
    auto_submit: bool = False
    allow_non_adjacent: bool = False

    def __post_init__(self) -> None:
        for name in (
            "date_enabled", "area_enabled", "date_fallback", "area_fallback",
            "auto_submit", "allow_non_adjacent",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if type(self.ticket_number) is not int or not 0 < self.ticket_number <= MAX_SAFE_COUNT:
            raise ValueError("ticket number must be a positive bounded integer")
        for name in ("date_keyword", "date_mode", "area_keyword", "area_mode", "keyword_exclude"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")

    def selector_values(self) -> dict[str, object]:
        """Project only the keys consumed by the existing pure selectors."""
        return {
            "date_auto_select": {"date_keyword": self.date_keyword, "mode": self.date_mode},
            "area_auto_select": {"area_keyword": self.area_keyword, "mode": self.area_mode},
            "date_auto_fallback": self.date_fallback,
            "area_auto_fallback": self.area_fallback,
            "keyword_exclude": self.keyword_exclude,
            "ticket_number": self.ticket_number,
        }
