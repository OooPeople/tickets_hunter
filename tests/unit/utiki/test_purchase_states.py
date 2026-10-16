"""A submit cannot enter dispatch, awaiting, or held state without its owner."""

from dataclasses import replace

import pytest

from platforms.utiki.domain.effects import (
    AwaitingEvidenceEffect,
    ClaimId,
    ClaimedEffect,
    EffectKind,
    EffectProposal,
    HeldEffect,
    HeldReason,
    RunningEffect,
)
from platforms.utiki.domain.identity import (
    AuthEpoch,
    DocumentKey,
    OperationId,
    OperationStamp,
    PurchaseKey,
    TabKey,
    TenantKey,
)
from platforms.utiki.domain.inputs import (
    InputContext,
    InputPolicy,
    InputReadyEvidence,
    InputSource,
    PurchaseCycleId,
)
from platforms.utiki.domain.plans import (
    QuantityPlan,
    SeatActionPlan,
    SubmitPlan,
    build_submit_plan,
)
from platforms.utiki.domain.purchase import (
    AwaitingSubmitOutcome,
    ClaimedSubmit,
    HeldSubmitOutcome,
    PreparedSubmit,
    SubmitAttempt,
    SubmitAttemptId,
)
from platforms.utiki.profiles import PageKind


def _attempt() -> SubmitAttempt:
    tab = TabKey(1, "target")
    document = DocumentKey("doc")
    cycle = PurchaseCycleId("cycle")
    key = PurchaseKey(
        tab, TenantKey("ctbc_brothers"), AuthEpoch(2),
        "performance", "", "area", PageKind.GENERAL_ADMISSION,
    )
    stamp = OperationStamp(tab, 3, OperationId("submit-op"), document, 4, 5)
    quantity = QuantityPlan(
        page_url=(
            "https://tix.ctbcsports.com/BROTHERS/UTK0202_?"
            "PERFORMANCE_ID=performance&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=area"
        ),
        document_id="doc",
        performance_id="performance",
        group_id="",
        price_area_id="area",
        quantity_limit=5,
        target_key="FULL",
        target_name="Full",
        ticket_number=2,
    )
    plan = build_submit_plan(quantity)
    assert isinstance(plan, SubmitPlan)
    context = InputContext(
        tab, document, cycle, 1, 2, 3, InputPolicy.MANUAL_ONLY, True,
    )
    ready = InputReadyEvidence(context, InputSource.MANUAL, None, None, True)
    return SubmitAttempt(SubmitAttemptId("attempt-1"), cycle, key, stamp, plan, ready)


def _claim(attempt: SubmitAttempt, kind: EffectKind = EffectKind.SUBMIT) -> ClaimedEffect:
    return ClaimedEffect(ClaimId("claim-1"), EffectProposal(attempt.stamp, kind))


def test_submit_states_require_original_attempt_and_matching_claim() -> None:
    attempt = _attempt()
    claim = _claim(attempt)

    assert PreparedSubmit(attempt).attempt is attempt
    assert ClaimedSubmit(attempt, claim).claim is claim
    assert ClaimedSubmit(attempt, RunningEffect(claim.claim_id, claim.proposal)).attempt is attempt
    assert AwaitingSubmitOutcome(
        attempt, AwaitingEvidenceEffect(claim.claim_id, claim.proposal), 50.0,
    ).attempt is attempt
    assert HeldSubmitOutcome(
        attempt, HeldEffect(claim.claim_id, claim.proposal, HeldReason.UNCERTAIN_DISPATCH),
    ).attempt is attempt

    with pytest.raises(ValueError, match="attempt"):
        PreparedSubmit(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim"):
        ClaimedSubmit(attempt, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim"):
        AwaitingSubmitOutcome(attempt, None, 50.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim"):
        HeldSubmitOutcome(attempt, None)  # type: ignore[arg-type]


def test_submit_claim_cannot_use_another_operation_or_effect_kind() -> None:
    attempt = _attempt()
    wrong_stamp = replace(attempt.stamp, operation=OperationId("other"))
    for claim in (
        ClaimedEffect(ClaimId("other"), EffectProposal(wrong_stamp, EffectKind.SUBMIT)),
        _claim(attempt, EffectKind.SELECT),
    ):
        with pytest.raises(ValueError, match="claim"):
            ClaimedSubmit(attempt, claim)


def test_submit_attempt_rejects_mismatched_plan_and_input_context() -> None:
    attempt = _attempt()
    with pytest.raises(ValueError, match="document"):
        replace(attempt, stamp=replace(attempt.stamp, document=DocumentKey("other")))
    with pytest.raises(ValueError, match="submit plan is invalid"):
        replace(attempt, plan=replace(attempt.plan, document_id="other"))
    with pytest.raises(ValueError, match="cycle"):
        replace(attempt, cycle=PurchaseCycleId("other"))
    with pytest.raises(ValueError, match="auto-submit"):
        replace(
            attempt,
            input_evidence=replace(
                attempt.input_evidence,
                context=replace(attempt.input_evidence.context, auto_submit=False),
            ),
        )
    wrong_tenant_url = attempt.plan.page_url.replace(
        "tix.ctbcsports.com/BROTHERS", "tix.wdragons.com"
    )
    with pytest.raises(ValueError, match="tenant"):
        replace(
            attempt,
            plan=replace(
                attempt.plan,
                page_url=wrong_tenant_url,
                selection_plan=replace(attempt.plan.selection_plan, page_url=wrong_tenant_url),
            ),
        )
    wrong_route_url = attempt.plan.page_url.replace("UTK0202_", "UTK0205_")
    with pytest.raises(ValueError, match="submit plan is invalid"):
        replace(
            attempt,
            plan=replace(
                attempt.plan,
                page_url=wrong_route_url,
                selection_plan=replace(attempt.plan.selection_plan, page_url=wrong_route_url),
            ),
        )
    wrong_performance_url = attempt.plan.page_url.replace(
        "PERFORMANCE_ID=performance", "PERFORMANCE_ID=other"
    )
    with pytest.raises(ValueError, match="route identity"):
        replace(
            attempt,
            plan=replace(
                attempt.plan,
                page_url=wrong_performance_url,
                selection_plan=replace(
                    attempt.plan.selection_plan, page_url=wrong_performance_url,
                ),
            ),
        )
    with pytest.raises(ValueError, match="selection"):
        replace(
            attempt,
            plan=replace(
                attempt.plan,
                selection_plan=replace(attempt.plan.selection_plan, price_area_id="other"),
            ),
        )


def test_submit_attempt_rejects_incomplete_route_identity() -> None:
    attempt = _attempt()
    for altered_url in (
        attempt.plan.page_url.replace("&GROUP_ID=", ""),
        attempt.plan.page_url.replace("&PERFORMANCE_PRICE_AREA_ID=area", ""),
        attempt.plan.page_url.replace("PERFORMANCE_PRICE_AREA_ID=area", "PERFORMANCE_PRICE_AREA_ID=other"),
    ):
        altered_selection = replace(attempt.plan.selection_plan, page_url=altered_url)
        altered_plan = build_submit_plan(altered_selection)
        assert isinstance(altered_plan, SubmitPlan)
        with pytest.raises(ValueError, match="route identity"):
            replace(attempt, plan=altered_plan)


def test_submit_attempt_rejects_forged_payload_or_clear_action() -> None:
    attempt = _attempt()
    with pytest.raises(ValueError, match="submit plan is invalid"):
        replace(attempt, plan=replace(attempt.plan, payload_fingerprint=("quantity", 2)))
    clear = SeatActionPlan(
        page_url=attempt.plan.page_url.replace("UTK0202_", "UTK0205_"),
        document_id="doc",
        performance_id="performance",
        group_id="group",
        price_area_id="area",
        product_id="product",
        action="clear",
        ticket_number=1,
        ticket_type_fingerprint=("type",),
        ticket_type_id="FULL",
        ticket_type_name="Full",
    )
    assert build_submit_plan(clear) is None
    forged = SubmitPlan(
        clear.page_url, clear.document_id, PageKind.RESERVED_SEAT,
        ("seat", 1), clear,
    )
    with pytest.raises(ValueError, match="submit plan is invalid"):
        replace(attempt, plan=forged)
