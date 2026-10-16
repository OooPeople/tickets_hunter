"""Identities assigned outside the pure uTiki domain."""

from __future__ import annotations

from dataclasses import dataclass


def _nonempty_token(value: str, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class TabKey:
    browser_generation: int
    target_id: str

    def __post_init__(self) -> None:
        if type(self.browser_generation) is not int or self.browser_generation < 0:
            raise ValueError("browser_generation must be a nonnegative integer")
        _nonempty_token(self.target_id, "target_id")


@dataclass(frozen=True, slots=True)
class DocumentKey:
    token: str

    def __post_init__(self) -> None:
        _nonempty_token(self.token, "document token")
        if self.token.strip().casefold() in {"0", "nan", "inf", "infinity"}:
            raise ValueError("document token is unknown")


@dataclass(frozen=True, slots=True)
class OperationId:
    token: str

    def __post_init__(self) -> None:
        _nonempty_token(self.token, "operation ID")


@dataclass(frozen=True, slots=True)
class OperationStamp:
    tab: TabKey
    session_generation: int
    operation: OperationId
    document: DocumentKey
    policy_revision: int
    control_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey):
            raise ValueError("operation stamp requires a tab")
        if not isinstance(self.operation, OperationId):
            raise ValueError("operation stamp requires an operation ID")
        if not isinstance(self.document, DocumentKey):
            raise ValueError("operation stamp requires a document")
        for name in ("session_generation", "policy_revision", "control_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
