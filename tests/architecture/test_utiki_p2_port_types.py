"""The real strict type checker must reject a raw adapter result at the port."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "ci" / "mypy-utiki.ini"

_FAKE_SOURCE = """
from platforms.utiki.application.ports import QuantityBrowserPort
from platforms.utiki.domain.effects import EffectReceipt, RunningEffect
from platforms.utiki.domain.identity import OperationStamp
from platforms.utiki.domain.plans import QuantityPlan
from platforms.utiki.domain.snapshots import QuantitySnapshot

class Fake:
    async def observe_page(self, expected_context: OperationStamp) -> QuantitySnapshot:
        raise NotImplementedError

    async def commit_quantity(self, plan: QuantityPlan, claim: RunningEffect) -> {result_type}:
        {result_body}

async def use(port: QuantityBrowserPort) -> None:
    raise NotImplementedError

async def check() -> None:
    await use(Fake())
"""


def _typecheck(result_type: str, result_body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "mypy", "--strict", "--no-incremental",
            "--config-file", str(CONFIG), "-c",
            _FAKE_SOURCE.format(result_type=result_type, result_body=result_body),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_quantity_port_accepts_typed_receipt_and_rejects_raw_dict() -> None:
    legal = _typecheck("EffectReceipt", "raise NotImplementedError")
    assert legal.returncode == 0, legal.stdout + legal.stderr

    wrong = _typecheck("dict[str, str]", "return {}")
    assert wrong.returncode != 0
    assert 'Argument 1 to "use" has incompatible type "Fake"' in wrong.stdout
    assert "commit_quantity" in wrong.stdout
    assert "QuantityBrowserPort" in wrong.stdout
