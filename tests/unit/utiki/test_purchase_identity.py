"""A purchase identity is stable across documents but not across sessions."""

from dataclasses import replace

import pytest

from platforms.utiki.domain.identity import AuthEpoch, PurchaseKey, TabKey, TenantKey
from platforms.utiki.profiles import PageKind


def _key(page_mode: PageKind = PageKind.RESERVED_SEAT) -> PurchaseKey:
    return PurchaseKey(
        tab=TabKey(1, "target"),
        tenant=TenantKey("ctbc_brothers"),
        auth_epoch=AuthEpoch(2),
        performance_id="performance-1",
        group_id="group-1",
        price_area_id="area-1",
        page_mode=page_mode,
        product_id="product-1",
    )


def test_purchase_key_has_transaction_fields_but_no_document_or_url() -> None:
    key = _key()

    assert key == _key()
    assert not hasattr(key, "document")
    assert not hasattr(key, "url")
    assert replace(key, auth_epoch=AuthEpoch(3)) != key
    assert replace(key, tenant=TenantKey("wei_chuan_dragons")) != key
    assert replace(key, page_mode=PageKind.GENERAL_ADMISSION) != key


def test_general_admission_may_have_empty_group_but_seat_modes_may_not() -> None:
    general = replace(_key(PageKind.GENERAL_ADMISSION), group_id="", product_id="")
    assert general.group_id == ""
    assert general.product_id == ""

    for mode in (PageKind.RESERVED_SEAT, PageKind.SERVER_ASSIGNED_QUANTITY):
        with pytest.raises(ValueError, match="group"):
            replace(_key(mode), group_id="")
        with pytest.raises(ValueError, match="product_id"):
            replace(_key(mode), product_id="")


@pytest.mark.parametrize("field", ["performance_id", "price_area_id"])
def test_missing_required_purchase_identity_is_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_key(), **{field: ""})


@pytest.mark.parametrize("bad", ["https://example.invalid/path", "x?token=secret", "x#fragment"])
def test_url_shaped_identity_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        replace(_key(), performance_id=bad)


def test_unknown_page_and_invalid_session_evidence_are_rejected() -> None:
    with pytest.raises(ValueError, match="page mode"):
        replace(_key(), page_mode=PageKind.UNKNOWN)
    with pytest.raises(ValueError, match="auth epoch"):
        AuthEpoch(True)
    with pytest.raises(ValueError, match="tenant"):
        TenantKey("")
