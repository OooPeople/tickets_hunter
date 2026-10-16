import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_domain_import_is_pure_without_zendriver(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[3] / "src"
    program = (
        "import sys; "
        "from platforms.utiki.domain.identity import TabKey, DocumentKey; "
        "from platforms.utiki.domain.snapshots import parse_nonnegative_count; "
        "from platforms.utiki.domain.plans import DateActionPlan; "
        "from platforms.utiki.application.ports import QuantityBrowserPort; "
        "from platforms.utiki.domain.effects import ClaimId; "
        "from platforms.utiki.domain.inputs import InputAttemptId; "
        "assert 'zendriver' not in sys.modules; "
        "assert 'nodriver_common' not in sys.modules; "
        "assert 'platforms.utiki.workflow' not in sys.modules; "
        "assert TabKey(0, 'tab').target_id == 'tab'; "
        "assert DocumentKey('doc').token == 'doc'; "
        "assert ClaimId('claim').token == 'claim'; "
        "assert InputAttemptId('input').token == 'input'"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


def test_field_observation_distinguishes_missing_empty_and_invalid() -> None:
    from platforms.utiki.domain.snapshots import (
        InvalidField,
        MissingField,
        PresentField,
        parse_nonnegative_count,
    )

    assert parse_nonnegative_count(None) == MissingField()
    assert parse_nonnegative_count("") == InvalidField("empty")
    assert parse_nonnegative_count("not-a-count") == InvalidField("not-integer")
    assert parse_nonnegative_count(0) == PresentField(0)
    assert PresentField("").value == ""


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_or_boolean_count_is_not_accepted(value: object) -> None:
    from platforms.utiki.domain.snapshots import InvalidField, parse_nonnegative_count

    assert isinstance(parse_nonnegative_count(value), InvalidField)


def test_oversized_count_remains_an_invalid_observation() -> None:
    from platforms.utiki.domain.snapshots import InvalidField, parse_nonnegative_count

    assert parse_nonnegative_count("9" * 5000) == InvalidField("out-of-range")
    assert parse_nonnegative_count(9_007_199_254_740_992) == InvalidField("out-of-range")


def test_identity_records_reject_missing_or_invalid_components() -> None:
    from platforms.utiki.domain.identity import DocumentKey, OperationId, OperationStamp, TabKey

    with pytest.raises(ValueError):
        DocumentKey("0")
    with pytest.raises(ValueError):
        TabKey(0, "")
    with pytest.raises(ValueError):
        OperationId("")
    with pytest.raises(ValueError):
        OperationStamp(TabKey(0, "tab"), -1, OperationId("op"), DocumentKey("doc"), 0, 0)


def test_existing_workflow_exports_still_resolve() -> None:
    import platforms.utiki as utiki
    from platforms.utiki import workflow

    assert utiki.nodriver_ctbc_main is workflow.nodriver_ctbc_main
    assert utiki.nodriver_utiki_main is workflow.nodriver_ctbc_main


def test_selection_uses_the_same_domain_snapshot_and_plan_classes() -> None:
    from platforms.utiki import selection
    from platforms.utiki.domain import plans, snapshots

    for name in ("DateSnapshot", "AreaSnapshot", "QuantitySnapshot", "SeatSnapshot", "DialogSnapshot"):
        assert getattr(selection, name) is getattr(snapshots, name)
    for name in ("DateActionPlan", "AreaActionPlan", "QuantityPlan", "SeatActionPlan", "SubmitPlan"):
        assert getattr(selection, name) is getattr(plans, name)
