"""Narrow browser capabilities; a port never owns session state or grants effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..domain.effects import EffectReceipt, RunningEffect
from ..domain.identity import AuthEpoch, OperationStamp, TabKey
from ..domain.inputs import CaptchaAttempt, CaptchaImageKey
from ..domain.plans import AreaActionPlan, DateActionPlan, QuantityPlan, SeatActionPlan, SubmitPlan
from ..domain.purchase import ManualSubmitEvidence, SubmitAttempt
from ..domain.snapshots import AreaSnapshot, DateSnapshot, DialogSnapshot, QuantitySnapshot, SeatSnapshot


class CoreGate(str, Enum):
    PASSIVE = "passive"
    HEALTH_ONLY = "health-only"
    PURCHASE_ALLOWED = "purchase-allowed"
    PAUSED = "paused"
    CLOSING = "closing"


@dataclass(frozen=True, slots=True)
class CoreControl:
    revision: int
    gate: CoreGate

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision <= 0:
            raise ValueError("core control requires a positive revision")
        if not isinstance(self.gate, CoreGate):
            raise ValueError("core control requires a known gate")


class CoreControlPort(Protocol):
    """Read the trusted in-memory gate; a closed tab remains closing here."""

    def current_control(self, tab: TabKey) -> CoreControl:
        ...


@dataclass(frozen=True, slots=True)
class PurchaseAuthority:
    auth_epoch: AuthEpoch
    credential_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.auth_epoch, AuthEpoch):
            raise ValueError("purchase authority requires verified auth epoch")
        if type(self.credential_revision) is not int or self.credential_revision < 0:
            raise ValueError("credential revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class VerifiedLoginAcceptance:
    """Host attestation of server acceptance, separate from a session cookie."""

    tab: TabKey
    tenant_key: str
    page_url: str
    document_id: str
    rejected_attempt_id: str
    auth_epoch: AuthEpoch

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tab, TabKey)
            or not all(type(value) is str and value for value in (
                self.tenant_key, self.page_url, self.document_id,
                self.rejected_attempt_id,
            ))
            or not isinstance(self.auth_epoch, AuthEpoch)
        ):
            raise ValueError("accepted login requires attributed host evidence")


class PurchaseAuthorityPort(CoreControlPort, Protocol):
    def current_purchase_authority(self, tab: TabKey) -> PurchaseAuthority | None: ...


@dataclass(frozen=True, slots=True, repr=False)
class InlineLoginCredentials:
    account: str
    password: str
    revision: int

    def __post_init__(self) -> None:
        if (
            type(self.account) is not str or not self.account.strip()
            or type(self.password) is not str or not self.password
            or type(self.revision) is not int or self.revision < 0
        ):
            raise ValueError("inline login requires nonempty credentials and revision")


class InlineLoginCredentialPort(PurchaseAuthorityPort, Protocol):
    def current_inline_login_credentials(
        self, tab: TabKey, tenant_key: str
    ) -> InlineLoginCredentials | None: ...


@dataclass(frozen=True, slots=True)
class QuantityObservation:
    """A browser observation attributed to its actual source context."""

    source: OperationStamp
    snapshot: QuantitySnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.source, OperationStamp) or not isinstance(self.snapshot, QuantitySnapshot):
            raise ValueError("quantity observation requires typed source and snapshot")


class QuantityBrowserPort(Protocol):
    """One observation and one already-granted quantity mutation capability."""

    async def observe_page(self, expected_context: OperationStamp) -> QuantityObservation:
        """Return typed data with the actual tab, generation, and document source."""
        ...

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        """Perform the guarded mutation and report the original claim's result."""
        ...


@dataclass(frozen=True, slots=True)
class PageObservation:
    """An observed page attributed to the browser tab that produced it."""

    tab: TabKey
    snapshot: DateSnapshot | AreaSnapshot | QuantitySnapshot | SeatSnapshot
    manual_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not isinstance(
            self.snapshot, (DateSnapshot, AreaSnapshot, QuantitySnapshot, SeatSnapshot)
        ):
            raise ValueError("page observation requires a typed tab and snapshot")
        if type(self.manual_revision) is not int or self.manual_revision < 0:
            raise ValueError("manual selection revision must be nonnegative")


class SelectionBrowserPort(QuantityBrowserPort, Protocol):
    async def observe_date(self, tab: TabKey) -> PageObservation: ...

    async def observe_area(self, tab: TabKey) -> PageObservation: ...

    async def observe_quantity(self, tab: TabKey) -> PageObservation: ...
    async def observe_seat(self, tab: TabKey) -> PageObservation: ...

    async def commit_date(
        self, snapshot: DateSnapshot, plan: DateActionPlan, claim: RunningEffect
    ) -> EffectReceipt: ...

    async def commit_area(
        self, snapshot: AreaSnapshot, plan: AreaActionPlan,
        ticket_number: int, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def commit_server_assigned_area(
        self, snapshot: AreaSnapshot, plan: AreaActionPlan,
        ticket_number: int, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def commit_quantity_snapshot(
        self, snapshot: QuantitySnapshot, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt: ...

    async def commit_seat_snapshot(
        self, snapshot: SeatSnapshot, plan: SeatActionPlan, claim: RunningEffect
    ) -> EffectReceipt: ...


@dataclass(frozen=True, slots=True)
class PurchaseInputObservation:
    tab: TabKey
    page_url: str
    document_id: str
    captcha_present: bool
    captcha_unique: bool
    captcha_enabled: bool
    manual_value_ready: bool
    login_required: bool
    login_ready: bool
    button_ready: bool
    dialog_visible: bool
    login_fields_usable: bool = False
    login_account_present: bool = False
    login_password_present: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not self.page_url or not self.document_id:
            raise ValueError("input observation requires tab and page identity")
        for name in (
            "captcha_present", "captcha_unique", "captcha_enabled",
            "manual_value_ready", "login_required", "login_ready",
            "button_ready", "dialog_visible",
            "login_fields_usable", "login_account_present", "login_password_present",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class InlineLoginIntent:
    page_url: str
    document_id: str
    expected_origin: str
    credential_revision: int
    write_account: bool
    write_password: bool

    def __post_init__(self) -> None:
        if not self.page_url or not self.document_id or not self.expected_origin:
            raise ValueError("inline login intent requires page identity and origin")
        if type(self.credential_revision) is not int or self.credential_revision < 0:
            raise ValueError("inline login intent requires credential revision")
        if type(self.write_account) is not bool or type(self.write_password) is not bool:
            raise ValueError("inline login write masks must be boolean")
        if not (self.write_account or self.write_password):
            raise ValueError("inline login intent requires a missing field")


@dataclass(frozen=True, slots=True)
class InlineLoginAudit:
    tab: TabKey
    page_url: str
    document_id: str
    matched: bool
    manual_ready: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not self.page_url or not self.document_id:
            raise ValueError("inline login audit requires page identity")
        if type(self.matched) is not bool:
            raise ValueError("inline login audit match must be boolean")
        if type(self.manual_ready) is not bool or (self.matched and self.manual_ready):
            raise ValueError("inline login audit source is invalid")


@dataclass(frozen=True, slots=True)
class RejectedLoginObservation:
    tab: TabKey
    page_url: str
    document_id: str
    token: str
    changed_ready: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tab, TabKey) or not self.page_url
            or not self.document_id or not self.token
            or type(self.changed_ready) is not bool
        ):
            raise ValueError("rejected login observation requires attributed evidence")


@dataclass(frozen=True, slots=True)
class SubmitOutcomeObservation:
    tab: TabKey
    page_url: str
    document_id: str
    dialog: DialogSnapshot
    manual_submit: ManualSubmitEvidence | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tab, TabKey) or not self.page_url or not self.document_id
            or not isinstance(self.dialog, DialogSnapshot)
            or self.dialog.page_url != self.page_url
            or self.dialog.document_id != self.document_id
        ):
            raise ValueError("submit outcome requires one attributed page and dialog")
        if self.manual_submit is not None and not isinstance(
            self.manual_submit, ManualSubmitEvidence
        ):
            raise ValueError("submit outcome requires typed manual evidence")


@dataclass(frozen=True, slots=True)
class CaptchaResetObservation:
    tab: TabKey
    page_url: str
    document_id: str
    image: CaptchaImageKey | None
    image_data_url: str = field(repr=False)
    field_usable: bool
    field_empty: bool
    refresh_usable: bool
    dialog_visible: bool
    manual_value_ready: bool = False
    manual_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not self.page_url or not self.document_id:
            raise ValueError("CAPTCHA reset observation requires page identity")
        if self.image is not None and not isinstance(self.image, CaptchaImageKey):
            raise ValueError("CAPTCHA reset image key is invalid")
        if type(self.image_data_url) is not str or bool(self.image_data_url) != bool(self.image):
            raise ValueError("CAPTCHA reset image data differs from its key")
        for name in (
            "field_usable", "field_empty", "refresh_usable", "dialog_visible",
            "manual_value_ready",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if type(self.manual_revision) is not int or self.manual_revision < 0:
            raise ValueError("CAPTCHA manual revision is invalid")


class CaptchaProviderPort(Protocol):
    """One Run-owned OCR instance, supplied by the composition root."""

    def classification(self, image: bytes) -> str: ...


class PurchaseBrowserPort(SelectionBrowserPort, Protocol):
    async def observe_purchase_input(self, tab: TabKey) -> PurchaseInputObservation: ...

    async def arm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str,
        prior: ManualSubmitEvidence | None = None,
    ) -> str: ...

    async def disarm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str, arm_token: str,
    ) -> bool: ...

    async def commit_inline_login(
        self, intent: InlineLoginIntent, credentials: InlineLoginCredentials,
        claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def audit_inline_login(
        self, tab: TabKey, intent: InlineLoginIntent,
        credentials: InlineLoginCredentials,
    ) -> InlineLoginAudit: ...

    async def arm_rejected_login(
        self, tab: TabKey, page_url: str, document_id: str, token: str,
        claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def observe_rejected_login(
        self, tab: TabKey, page_url: str, document_id: str, token: str,
    ) -> RejectedLoginObservation: ...

    async def clear_rejected_selection(
        self, attempt: SubmitAttempt, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def dispatch_submit(
        self, plan: SubmitPlan, claim: RunningEffect,
        login_intent: InlineLoginIntent | None = None,
        credentials: InlineLoginCredentials | None = None,
        captcha_observed: CaptchaResetObservation | None = None,
        captcha_answer: str | None = None,
        manual_login_token: str | None = None,
    ) -> EffectReceipt: ...

    async def observe_submit_outcome(self, tab: TabKey) -> SubmitOutcomeObservation: ...

    async def dismiss_submit_dialog(
        self, dialog: DialogSnapshot, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def observe_captcha_reset(self, tab: TabKey) -> CaptchaResetObservation: ...

    async def commit_captcha_reset(
        self, observed: CaptchaResetObservation, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def commit_captcha_input(
        self, observed: CaptchaResetObservation, attempt: CaptchaAttempt,
        answer: str, claim: RunningEffect,
    ) -> EffectReceipt: ...

    async def audit_captcha_input(
        self, observed: CaptchaResetObservation, answer: str,
    ) -> bool: ...


class CheckoutNotificationPort(Protocol):
    def start_checkout_notification(self) -> None: ...
