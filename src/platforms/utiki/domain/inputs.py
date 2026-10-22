"""Secret-free input and CAPTCHA evidence owned by a purchase cycle."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .effects import ClaimId, EffectStatus
from .identity import DocumentKey, TabKey


def _nonempty(value: str, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonempty")


def _deadline(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("deadline must be positive and finite")


@dataclass(frozen=True, slots=True)
class PurchaseCycleId:
    token: str

    def __post_init__(self) -> None:
        _nonempty(self.token, "purchase cycle ID")


@dataclass(frozen=True, slots=True)
class InputAttemptId:
    token: str

    def __post_init__(self) -> None:
        _nonempty(self.token, "input attempt ID")


@dataclass(frozen=True, slots=True)
class ResetId:
    token: str

    def __post_init__(self) -> None:
        _nonempty(self.token, "reset ID")


class InputPolicy(str, Enum):
    MANUAL_ONLY = "manual-only"
    RECOGNITION_ALLOWED = "recognition-allowed"


class ResetReason(str, Enum):
    KNOWN_REJECTION = "known-rejection"
    INVALID_RECOGNITION = "invalid-recognition"


class InputBlockReason(str, Enum):
    MISSING_FIELD = "missing-field"
    IMAGE_TIMEOUT = "image-timeout"
    PROVIDER_UNAVAILABLE = "provider-unavailable"
    PROVIDER_RESTART_REQUIRED = "provider-restart-required"
    UNKNOWN_EFFECT = "unknown-effect"
    MANUAL_REVIEW = "manual-review"


@dataclass(frozen=True, slots=True)
class InputContext:
    tab: TabKey
    document: DocumentKey
    cycle: PurchaseCycleId
    selection_revision: int
    credential_revision: int
    input_revision: int
    input_policy: InputPolicy
    auto_submit: bool

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or not isinstance(self.document, DocumentKey):
            raise ValueError("input context requires tab and document")
        if not isinstance(self.cycle, PurchaseCycleId):
            raise ValueError("input context requires a purchase cycle")
        for name in ("selection_revision", "credential_revision", "input_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not isinstance(self.input_policy, InputPolicy):
            raise ValueError("input context requires a known input policy")
        if type(self.auto_submit) is not bool:
            raise ValueError("auto-submit policy must be a boolean")


@dataclass(frozen=True, slots=True, repr=False)
class CaptchaImageKey:
    """Identity from observed source, element, and load evidence, never a read UUID."""

    source: str
    element_fingerprint: str
    load_revision: str
    challenge_revision: str | None

    def __post_init__(self) -> None:
        _nonempty(self.source, "image source")
        _nonempty(self.element_fingerprint, "image element fingerprint")
        _nonempty(self.load_revision, "image load revision")
        if self.challenge_revision is not None:
            _nonempty(self.challenge_revision, "challenge revision")


@dataclass(frozen=True, slots=True)
class CaptchaObservation:
    context: InputContext
    field_present: bool
    field_unique: bool
    field_enabled: bool
    image: CaptchaImageKey | None
    manual_value_ready: bool

    def __post_init__(self) -> None:
        if not isinstance(self.context, InputContext):
            raise ValueError("CAPTCHA observation requires an input context")
        for name in ("field_present", "field_unique", "field_enabled", "manual_value_ready"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.image is not None and not isinstance(self.image, CaptchaImageKey):
            raise ValueError("image must be observed evidence")
        if not self.field_present and (self.field_unique or self.field_enabled):
            raise ValueError("absent field cannot be unique or enabled")
        if not self.field_unique and self.field_enabled:
            raise ValueError("non-unique field cannot be enabled")
        if self.manual_value_ready and not (
            self.field_present and self.field_unique and self.field_enabled
        ):
            raise ValueError("manual readiness requires a unique usable field")


@dataclass(frozen=True, slots=True)
class CaptchaAttempt:
    attempt_id: InputAttemptId
    context: InputContext
    image: CaptchaImageKey
    provider_revision: str
    deadline_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, InputAttemptId):
            raise ValueError("CAPTCHA attempt requires an attempt ID")
        if not isinstance(self.context, InputContext) or not isinstance(self.image, CaptchaImageKey):
            raise ValueError("CAPTCHA attempt requires context and image evidence")
        _nonempty(self.provider_revision, "provider revision")
        _deadline(self.deadline_monotonic)
        if self.context.input_policy is not InputPolicy.RECOGNITION_ALLOWED:
            raise ValueError("CAPTCHA recognition requires recognition policy")


@dataclass(frozen=True, slots=True)
class CaptchaResetAttempt:
    reset_id: ResetId
    context: InputContext
    before_image: CaptchaImageKey
    reason: ResetReason
    deadline_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.reset_id, ResetId):
            raise ValueError("CAPTCHA reset requires a reset ID")
        if not isinstance(self.context, InputContext) or not isinstance(self.before_image, CaptchaImageKey):
            raise ValueError("CAPTCHA reset requires context and prior image evidence")
        if not isinstance(self.reason, ResetReason):
            raise ValueError("CAPTCHA reset requires a reason code")
        _deadline(self.deadline_monotonic)


class InputSource(str, Enum):
    NOT_REQUIRED = "not-required"
    MANUAL = "manual"
    RECOGNITION = "recognition"


@dataclass(frozen=True, slots=True)
class InputReadyEvidence:
    context: InputContext
    source: InputSource
    image: CaptchaImageKey | None
    attempt_id: InputAttemptId | None
    field_audit_passed: bool
    manual_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.context, InputContext) or not isinstance(self.source, InputSource):
            raise ValueError("input readiness requires context and source")
        if type(self.field_audit_passed) is not bool or not self.field_audit_passed:
            raise ValueError("input readiness requires a passing field audit")
        if type(self.manual_revision) is not int or self.manual_revision < 0:
            raise ValueError("input readiness requires a valid manual revision")
        if self.image is not None and not isinstance(self.image, CaptchaImageKey):
            raise ValueError("input readiness image must be observed evidence")
        if self.source is InputSource.RECOGNITION:
            if self.image is None or not isinstance(self.attempt_id, InputAttemptId):
                raise ValueError("recognition readiness requires image and attempt")
            if self.context.input_policy is not InputPolicy.RECOGNITION_ALLOWED:
                raise ValueError("recognition readiness requires recognition policy")
        elif self.attempt_id is not None:
            raise ValueError("non-recognition readiness cannot reuse an attempt")
        if self.source is InputSource.NOT_REQUIRED and self.image is not None:
            raise ValueError("not-required input cannot carry an image")


@dataclass(frozen=True, slots=True)
class InputWriteReceipt:
    attempt_id: InputAttemptId
    context: InputContext
    status: EffectStatus
    native_setter_applied: bool
    readback_matches: bool
    framework_ready: bool

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, InputAttemptId) or not isinstance(self.context, InputContext):
            raise ValueError("input write receipt requires attempt and context")
        if not isinstance(self.status, EffectStatus):
            raise ValueError("input write receipt requires a known status")
        for name in ("native_setter_applied", "readback_matches", "framework_ready"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.status is EffectStatus.APPLIED and not (
            self.native_setter_applied and self.readback_matches and self.framework_ready
        ):
            raise ValueError("applied input write requires setter, readback, and framework readiness")


def _valid_state_context(context: InputContext) -> None:
    if not isinstance(context, InputContext):
        raise ValueError("input state requires an input context")


@dataclass(frozen=True, slots=True)
class UnobservedInput:
    context: InputContext

    def __post_init__(self) -> None:
        _valid_state_context(self.context)


@dataclass(frozen=True, slots=True)
class WaitingImageInput:
    context: InputContext

    def __post_init__(self) -> None:
        _valid_state_context(self.context)


@dataclass(frozen=True, slots=True)
class WaitingManualInput:
    context: InputContext

    def __post_init__(self) -> None:
        _valid_state_context(self.context)


@dataclass(frozen=True, slots=True)
class RecognizingInput:
    attempt: CaptchaAttempt

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, CaptchaAttempt):
            raise ValueError("recognition state requires an attempt")


@dataclass(frozen=True, slots=True)
class WritingInput:
    attempt: CaptchaAttempt
    claim_id: ClaimId

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, CaptchaAttempt):
            raise ValueError("writing state requires an attempt")
        if not isinstance(self.claim_id, ClaimId):
            raise ValueError("writing state requires a claim")


@dataclass(frozen=True, slots=True)
class ReadyInput:
    evidence: InputReadyEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, InputReadyEvidence):
            raise ValueError("ready state requires input evidence")


@dataclass(frozen=True, slots=True)
class ConsumedInput:
    evidence: InputReadyEvidence
    submit_claim: ClaimId

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, InputReadyEvidence):
            raise ValueError("consumed state requires input evidence")
        if not isinstance(self.submit_claim, ClaimId):
            raise ValueError("consumed state requires a submit claim")


@dataclass(frozen=True, slots=True)
class ResetPreparedInput:
    reset: CaptchaResetAttempt

    def __post_init__(self) -> None:
        if not isinstance(self.reset, CaptchaResetAttempt):
            raise ValueError("prepared reset requires a reset attempt")


@dataclass(frozen=True, slots=True)
class ResetAwaitingInput:
    reset: CaptchaResetAttempt
    claim_id: ClaimId

    def __post_init__(self) -> None:
        if not isinstance(self.reset, CaptchaResetAttempt):
            raise ValueError("awaiting reset requires a reset attempt")
        if not isinstance(self.claim_id, ClaimId):
            raise ValueError("awaiting reset requires a claim")


@dataclass(frozen=True, slots=True)
class BlockedInput:
    context: InputContext
    reason: InputBlockReason

    def __post_init__(self) -> None:
        _valid_state_context(self.context)
        if not isinstance(self.reason, InputBlockReason):
            raise ValueError("blocked input requires a reason code")


InputState: TypeAlias = (
    UnobservedInput
    | WaitingImageInput
    | WaitingManualInput
    | RecognizingInput
    | WritingInput
    | ReadyInput
    | ConsumedInput
    | ResetPreparedInput
    | ResetAwaitingInput
    | BlockedInput
)
