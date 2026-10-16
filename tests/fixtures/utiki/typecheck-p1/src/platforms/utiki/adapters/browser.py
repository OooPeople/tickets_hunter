from __future__ import annotations


def accepted_receipt(operation_id: str) -> dict[str, str]:
    return {"operation_id": operation_id, "status": "accepted"}
