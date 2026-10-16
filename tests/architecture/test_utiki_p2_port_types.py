"""The real strict type checker must reject a raw adapter result at the port."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "ci" / "mypy-utiki.ini"

_FAKE_SOURCE = """
from platforms.utiki.application.ports import QuantityBrowserPort, QuantityObservation
from platforms.utiki.domain.effects import EffectReceipt, RunningEffect
from platforms.utiki.domain.identity import OperationStamp
from platforms.utiki.domain.plans import QuantityPlan

class Fake:
    async def observe_page(self, expected_context: OperationStamp) -> QuantityObservation:
        raise NotImplementedError

    async def commit_quantity(self, plan: QuantityPlan{claim_parameter}) -> {result_type}:
        {result_body}

async def use(port: QuantityBrowserPort) -> None:
    raise NotImplementedError

async def check() -> None:
    await use(Fake())
"""


def _typecheck(
    result_type: str, result_body: str, *, include_claim: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "mypy", "--strict", "--no-incremental",
            "--config-file", str(CONFIG), "-c",
            _FAKE_SOURCE.format(
                claim_parameter=", claim: RunningEffect" if include_claim else "",
                result_type=result_type,
                result_body=result_body,
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_legal_port() -> None:
    legal = _typecheck("EffectReceipt", "raise NotImplementedError")
    assert legal.returncode == 0, legal.stdout + legal.stderr


def _assert_port_rejection(
    result: subprocess.CompletedProcess[str], *, got_signature: str
) -> None:
    assert result.returncode != 0
    assert 'Argument 1 to "use" has incompatible type "Fake"' in result.stdout
    assert "QuantityBrowserPort" in result.stdout
    assert "[arg-type]" in result.stdout
    assert (
        "commit_quantity(self, plan: QuantityPlan, claim: RunningEffect) "
        "-> Coroutine[Any, Any, EffectReceipt]"
    ) in result.stdout
    assert got_signature in result.stdout


def test_quantity_port_accepts_typed_receipt() -> None:
    _assert_legal_port()


def test_quantity_port_rejects_wrong_receipt_type() -> None:
    _assert_legal_port()
    wrong = _typecheck("QuantityPlan", "return plan")
    _assert_port_rejection(
        wrong,
        got_signature="commit_quantity(self, plan: QuantityPlan, claim: RunningEffect) "
        "-> Coroutine[Any, Any, QuantityPlan]",
    )


def test_quantity_port_rejects_missing_claim() -> None:
    _assert_legal_port()
    wrong = _typecheck("EffectReceipt", "raise NotImplementedError", include_claim=False)
    _assert_port_rejection(
        wrong,
        got_signature="commit_quantity(self, plan: QuantityPlan) "
        "-> Coroutine[Any, Any, EffectReceipt]",
    )


def test_quantity_port_rejects_raw_dict_adapter() -> None:
    _assert_legal_port()
    wrong = _typecheck("dict[str, str]", "return {}")
    _assert_port_rejection(
        wrong,
        got_signature="commit_quantity(self, plan: QuantityPlan, claim: RunningEffect) "
        "-> Coroutine[Any, Any, dict[str, str]]",
    )


def test_real_quantity_application_rejects_raw_plan() -> None:
    source = """
from platforms.utiki.application.quantity import commit_granted_quantity
from platforms.utiki.application.ports import QuantityBrowserPort
from platforms.utiki.domain.effects import RunningEffect
from platforms.utiki.domain.plans import QuantityPlan

async def legal(port: QuantityBrowserPort, plan: QuantityPlan, claim: RunningEffect) -> None:
    await commit_granted_quantity(port, plan, claim)

async def wrong(port: QuantityBrowserPort, claim: RunningEffect) -> None:
    await commit_granted_quantity(port, {"raw": "plan"}, claim)
"""
    legal_source = source.replace(
        '    await commit_granted_quantity(port, {"raw": "plan"}, claim)\n',
        "    raise NotImplementedError\n",
    )
    command = [
        sys.executable, "-m", "mypy", "--strict", "--no-incremental",
        "--config-file", str(CONFIG), "-c",
    ]
    legal = subprocess.run(
        [*command, legal_source], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert legal.returncode == 0, legal.stdout + legal.stderr
    wrong = subprocess.run(
        [*command, source], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert wrong.returncode != 0
    assert 'Argument 2 to "commit_granted_quantity" has incompatible type' in wrong.stdout
    assert "dict[str, str]" in wrong.stdout
    assert "QuantityPlan" in wrong.stdout
    assert "[arg-type]" in wrong.stdout
