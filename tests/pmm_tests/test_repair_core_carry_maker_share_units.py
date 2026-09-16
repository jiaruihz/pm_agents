from __future__ import annotations

import json

from scripts.ops.repair_core_carry_maker_share_units import (
    missing_maker_fee_adjustments,
)


def test_corrected_maker_fill_requires_exact_zero_fee_lineage(tmp_path) -> None:
    cache = tmp_path / "fills.jsonl"
    fees = tmp_path / "fees.jsonl"
    cache.write_text(
        json.dumps(
            {
                "fill_id": "corrected-fill",
                "order_id": "order-1",
                "source": "authenticated_order_state_share_unit_correction_v1",
                "fee_metadata": {
                    "maker_only": True,
                    "correction": {"matched_shares": 5.0},
                },
            }
        )
        + "\n"
    )

    repairs = missing_maker_fee_adjustments(cache, fees)
    assert len(repairs) == 1
    assert repairs[0]["fill_id"] == "corrected-fill"
    assert repairs[0]["fee_source"] == "maker_zero"
    assert repairs[0]["fee_evidence_class"] == "exact"
    assert repairs[0]["fee_delta_usd"] == 0.0

    fees.write_text(json.dumps(repairs[0]) + "\n")
    assert missing_maker_fee_adjustments(cache, fees) == []


def test_non_maker_correction_never_gets_maker_zero_fee(tmp_path) -> None:
    cache = tmp_path / "fills.jsonl"
    fees = tmp_path / "fees.jsonl"
    cache.write_text(
        json.dumps(
            {
                "fill_id": "corrected-fill",
                "source": "authenticated_order_state_share_unit_correction_v1",
                "fee_metadata": {"maker_only": False},
            }
        )
        + "\n"
    )

    assert missing_maker_fee_adjustments(cache, fees) == []
