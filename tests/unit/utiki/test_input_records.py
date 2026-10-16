import math
from dataclasses import fields

import pytest

from platforms.utiki.domain.effects import ClaimId, EffectStatus
from platforms.utiki.domain.identity import DocumentKey, TabKey


def _context(*, recognition: bool = False):
    from platforms.utiki.domain.inputs import InputContext, InputPolicy, PurchaseCycleId

    policy = InputPolicy.RECOGNITION_ALLOWED if recognition else InputPolicy.MANUAL_ONLY
    return InputContext(TabKey(1, "target"), DocumentKey("doc"), PurchaseCycleId("cycle"), 2, 3, 4, policy, False)


def _image():
    from platforms.utiki.domain.inputs import CaptchaImageKey

    return CaptchaImageKey("source", "element", "load-1", None)


def test_image_identity_depends_on_observed_load_evidence_not_random_reads() -> None:
    from platforms.utiki.domain.inputs import CaptchaImageKey

    original = _image()
    same_observation = _image()
    next_load = CaptchaImageKey("source", "element", "load-2", None)

    assert original == same_observation
    assert original != next_load
    assert "source" not in repr(original)
    with pytest.raises(ValueError):
        CaptchaImageKey("source", "element", "", None)


def test_recognition_and_write_states_require_attempt_and_claim() -> None:
    from platforms.utiki.domain.inputs import (
        CaptchaAttempt,
        InputAttemptId,
        RecognizingInput,
        WritingInput,
    )

    attempt = CaptchaAttempt(InputAttemptId("input-1"), _context(recognition=True), _image(), "model-1", 50.0)
    assert RecognizingInput(attempt).attempt == attempt
    with pytest.raises(ValueError, match="claim"):
        WritingInput(attempt, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="attempt"):
        RecognizingInput(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CaptchaAttempt(InputAttemptId("input-1"), _context(recognition=True), _image(), "model-1", math.nan)


def test_ready_evidence_and_write_receipt_do_not_hold_answer_or_image_bytes() -> None:
    from platforms.utiki.domain.inputs import (
        InputAttemptId,
        InputReadyEvidence,
        InputSource,
        InputWriteReceipt,
        ReadyInput,
    )

    attempt_id = InputAttemptId("input-1")
    ready = InputReadyEvidence(_context(recognition=True), InputSource.RECOGNITION, _image(), attempt_id, True)
    receipt = InputWriteReceipt(attempt_id, _context(recognition=True), EffectStatus.APPLIED, True, True, True)

    assert ReadyInput(ready).evidence is ready
    assert receipt.status is EffectStatus.APPLIED
    assert all("answer" not in field.name and "bytes" not in field.name for field in fields(ready))
    assert all("answer" not in field.name and "bytes" not in field.name for field in fields(receipt))
    with pytest.raises(ValueError, match="write"):
        InputWriteReceipt(attempt_id, _context(), EffectStatus.APPLIED, True, False, True)
    with pytest.raises(ValueError, match="attempt"):
        InputReadyEvidence(_context(recognition=True), InputSource.RECOGNITION, _image(), None, True)


def test_reset_awaiting_and_consumed_states_require_exact_ownership() -> None:
    from platforms.utiki.domain.inputs import (
        CaptchaResetAttempt,
        ConsumedInput,
        InputReadyEvidence,
        InputSource,
        ResetAwaitingInput,
        ResetId,
        ResetReason,
    )

    reset = CaptchaResetAttempt(ResetId("reset-1"), _context(), _image(), ResetReason.KNOWN_REJECTION, 70.0)
    ready = InputReadyEvidence(_context(), InputSource.MANUAL, _image(), None, True)

    assert ResetAwaitingInput(reset, ClaimId("reset-claim")).reset == reset
    assert ConsumedInput(ready, ClaimId("submit-claim")).evidence == ready
    with pytest.raises(ValueError, match="claim"):
        ResetAwaitingInput(reset, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim"):
        ConsumedInput(ready, None)  # type: ignore[arg-type]


def test_context_only_states_reject_missing_context() -> None:
    from platforms.utiki.domain.inputs import UnobservedInput, WaitingImageInput, WaitingManualInput

    for constructor in (UnobservedInput, WaitingImageInput, WaitingManualInput):
        with pytest.raises(ValueError, match="context"):
            constructor(None)  # type: ignore[arg-type]


def test_observation_and_context_reject_inconsistent_policy_or_field_flags() -> None:
    from platforms.utiki.domain.inputs import (
        CaptchaObservation,
        InputContext,
        PurchaseCycleId,
    )

    with pytest.raises(ValueError, match="input policy"):
        InputContext(TabKey(1, "target"), DocumentKey("doc"), PurchaseCycleId("cycle"), 2, 3, 4, "manual-only", False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="absent field"):
        CaptchaObservation(_context(), False, True, True, None, False)
    with pytest.raises(ValueError, match="recognition policy"):
        from platforms.utiki.domain.inputs import InputAttemptId, InputReadyEvidence, InputSource

        InputReadyEvidence(_context(), InputSource.RECOGNITION, _image(), InputAttemptId("input"), True)
