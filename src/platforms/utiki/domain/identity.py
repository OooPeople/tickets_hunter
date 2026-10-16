"""Identities assigned outside the pure uTiki domain."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..profiles import PageKind


_IDENTITY_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def _nonempty_token(value: str, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _purchase_token(value: str, name: str, *, optional: bool = False) -> None:
    if type(value) is not str:
        raise ValueError(f"{name} must be an exact identity token")
    if (not optional or value) and not _IDENTITY_TOKEN.fullmatch(value):
        raise ValueError(f"{name} must be an exact identity token")


@dataclass(frozen=True, slots=True)
class TenantKey:
    """A key supplied by the validated tenant profile, not a hostname guess."""

    token: str

    def __post_init__(self) -> None:
        _purchase_token(self.token, "tenant")


@dataclass(frozen=True, slots=True)
class AuthEpoch:
    """A verified session-boundary generation; credential edits alone do not advance it."""

    generation: int

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("auth epoch must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class TabKey:
    browser_generation: int
    target_id: str

    def __post_init__(self) -> None:
        if type(self.browser_generation) is not int or self.browser_generation < 0:
            raise ValueError("browser_generation must be a nonnegative integer")
        _nonempty_token(self.target_id, "target_id")


@dataclass(frozen=True, slots=True)
class PurchaseKey:
    """Stable transaction identity, independent of URL and document changes."""

    tab: TabKey
    tenant: TenantKey
    auth_epoch: AuthEpoch
    performance_id: str
    group_id: str
    price_area_id: str
    page_mode: PageKind
    product_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not isinstance(self.tenant, TenantKey):
            raise ValueError("purchase identity requires tab and tenant")
        if not isinstance(self.auth_epoch, AuthEpoch):
            raise ValueError("purchase identity requires a verified auth epoch")
        if not isinstance(self.page_mode, PageKind) or self.page_mode not in (
            PageKind.RESERVED_SEAT,
            PageKind.GENERAL_ADMISSION,
            PageKind.SERVER_ASSIGNED_QUANTITY,
        ):
            raise ValueError("purchase identity requires a purchase page mode")
        _purchase_token(self.performance_id, "performance_id")
        _purchase_token(self.price_area_id, "price_area_id")
        _purchase_token(
            self.group_id,
            "group_id",
            optional=self.page_mode is PageKind.GENERAL_ADMISSION,
        )
        _purchase_token(
            self.product_id,
            "product_id",
            optional=self.page_mode is PageKind.GENERAL_ADMISSION,
        )


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
