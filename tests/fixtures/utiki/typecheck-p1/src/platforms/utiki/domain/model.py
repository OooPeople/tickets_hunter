from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunnerContract:
    operation_id: str


def operation_id(contract: RunnerContract) -> str:
    return contract.operation_id
