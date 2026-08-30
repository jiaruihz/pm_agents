"""Read-only GO/NO_GO audit for weather-data product cutovers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


ACCOUNTING_FIELDS = ("orders", "fills", "notional", "cost", "fee", "pnl", "coverage")


@dataclass(frozen=True)
class ProductCutoverResult:
    product: str
    decision: str
    failures: tuple[str, ...]
    retain_read_only: bool = True


def _zero(value: Any) -> bool:
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def _hex_identity(value: str, length: int) -> bool:
    return len(value) == length and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _aware_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def audit_product(
    product: str,
    evidence: dict[str, Any],
    *,
    expected_registry_build_id: str | None = None,
    expected_producer_release_sha: str | None = None,
) -> ProductCutoverResult:
    failures: list[str] = []
    registry_build_id = str(evidence.get("registry_build_id") or "")
    if not _hex_identity(registry_build_id, 64):
        failures.append("registry_build_id_missing")
    elif expected_registry_build_id and registry_build_id != expected_registry_build_id:
        failures.append("registry_build_id_mismatch")
    producer_release_sha = str(evidence.get("producer_release_sha") or "")
    if not _hex_identity(producer_release_sha, 40):
        failures.append("producer_release_sha_missing")
    elif expected_producer_release_sha and producer_release_sha != expected_producer_release_sha:
        failures.append("producer_release_sha_mismatch")
    if not evidence.get("new_writer_single_owner"):
        failures.append("new_writer_single_owner_not_proven")
    if not evidence.get("consumer_routes_verified"):
        failures.append("consumer_routes_not_verified")
    window_start = evidence.get("evidence_window_start_utc")
    window_end = evidence.get("evidence_window_end_utc")
    if not _aware_timestamp(window_start) or not _aware_timestamp(window_end):
        failures.append("evidence_window_missing")
    elif datetime.fromisoformat(str(window_start).replace("Z", "+00:00")) >= datetime.fromisoformat(
        str(window_end).replace("Z", "+00:00")
    ):
        failures.append("evidence_window_invalid")
    if not evidence.get("same_input_snapshot"):
        failures.append("input_snapshot_mismatch")
    for field in ("row_parity", "identity_parity", "key_parity", "watermark_parity"):
        if not evidence.get(field):
            failures.append(field)
    if int(evidence.get("target_date_blocks", 0)) < 2:
        failures.append("insufficient_target_date_blocks")
    if not evidence.get("quarantine_clear"):
        failures.append("quarantine_not_clear")
    if int(evidence.get("rollovers", 0)) < 1:
        failures.append("rollover_not_proven")
    if list(evidence.get("old_writer_cycles", ()))[-2:] != [0, 0]:
        failures.append("old_writer_not_zero_for_two_cycles")
    deltas = evidence.get("accounting_deltas")
    if not isinstance(deltas, dict):
        failures.append("accounting_deltas_missing")
    else:
        for field in ACCOUNTING_FIELDS:
            if field not in deltas or not _zero(deltas[field]):
                failures.append(f"accounting_delta_{field}")
    rollback = evidence.get("rollback_contract")
    if not isinstance(rollback, dict) or not rollback.get("documented") or not rollback.get("old_data_retain_read_only"):
        failures.append("rollback_contract_missing")
    if not evidence.get("old_path_read_only_tombstone"):
        failures.append("old_path_read_only_tombstone_missing")
    return ProductCutoverResult(product, "GO" if not failures else "NO_GO", tuple(failures))


def audit_cutover(products: dict[str, dict[str, Any]] | Iterable[tuple[str, dict[str, Any]]]) -> list[ProductCutoverResult]:
    items = products.items() if isinstance(products, dict) else products
    return [audit_product(name, evidence) for name, evidence in items]
