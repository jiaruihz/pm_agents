"""Fixed post-run report over the unified prediction-table contract."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Iterable, Mapping

import pandas as pd

from .contracts import stable_sha256, validate_prediction_row
from .probability import binary_score


def _score(rows: list[dict[str, Any]], probability_field: str) -> dict[str, Any]:
    if not rows:
        return {"status": "not_available", "rows": 0}
    frame = pd.DataFrame(rows)
    result = binary_score(
        frame,
        frame[probability_field].astype(float).to_numpy(),
        label_column="label",
    )
    result = {
        key: (None if isinstance(value, float) and not math.isfinite(value) else value)
        for key, value in result.items()
    }
    return {"status": "available", "rows": len(frame), **result}


def build_evaluation_report(
    prediction_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one schema-stable report; unavailable evidence remains explicit."""

    rows = [validate_prediction_row(row) for row in prediction_rows]
    scorable = [
        row
        for row in rows
        if row["scorable_status"] == "scorable"
        and row["p_model"] is not None
        and row["label"] is not None
    ]
    same_denominator = [
        row for row in scorable if row.get("market_p") is not None
    ]
    model_same = _score(same_denominator, "p_model")
    market_same = _score(same_denominator, "market_p")
    if model_same["status"] == "available":
        market_delta = {
            "brier_model_minus_market": model_same["brier"] - market_same["brier"],
            "logloss_model_minus_market": model_same["logloss"] - market_same["logloss"],
        }
    else:
        market_delta = {"status": "not_available"}

    plan_ids = {row.get("plan_id") for row in rows if row.get("plan_id")}
    order_ids = {row.get("order_id") for row in rows if row.get("order_id")}
    fill_rows = [row for row in rows if row.get("fill_id")]
    settled_pnl = [
        float(row["pnl_usd_at_fill"])
        for row in fill_rows
        if row.get("settlement_status") == "settled"
        and row.get("pnl_usd_at_fill") is not None
    ]
    liquidity = Counter(
        str(row.get("liquidity_role"))
        for row in fill_rows
        if row.get("liquidity_role") in {"maker", "taker"}
    )
    raw_candidates = sum(int(row.get("raw_candidate_count", 0)) for row in rows)
    canonical_candidates = sum(
        int(row.get("canonical_candidate_count", 0)) for row in rows
    )
    raw_fills = sum(int(row.get("raw_fill_count", 0)) for row in rows)
    canonical_fills = sum(int(row.get("canonical_fill_count", 0)) for row in rows)
    reconciliation_fields = {
        "raw_candidate_count",
        "canonical_candidate_count",
        "raw_fill_count",
        "canonical_fill_count",
    }
    reconciliation_present = any(
        reconciliation_fields.intersection(row) for row in rows
    )
    report = {
        "schema_version": "weather_city_evaluation_report_v1",
        "coverage": {
            "prediction_rows": len(rows),
            "target_dates": len({(row["city"], row["target_date"]) for row in rows}),
            "cities": sorted({row["city"] for row in rows}),
            "scorable_rows": len(scorable),
            "coverage_status": dict(
                sorted(Counter(row["coverage_status"] for row in rows).items())
            ),
            "pit_provenance": dict(
                sorted(Counter(row["pit_provenance"] for row in rows).items())
            ),
        },
        "prediction_quality": _score(scorable, "p_model"),
        "same_denominator_market_baseline": {
            "rows": len(same_denominator),
            "model": model_same,
            "market": market_same,
            "delta": market_delta,
        },
        "signal_funnel": {
            "prediction_rows": len(rows),
            "candidate_rows": sum(bool(row.get("candidate_id")) for row in rows),
            "intent_rows": sum(bool(row.get("intent_id")) for row in rows),
        },
        "evidence_funnel": {
            "pit_market_rows": sum(row.get("market_p") is not None for row in rows),
            "settlement_rows": sum(row.get("label") is not None for row in rows),
            "executable_rows": sum(row.get("executable_cost") is not None for row in rows),
            "fill_rows": len(fill_rows),
        },
        "execution": {
            "status": (
                "available" if plan_ids or order_ids or fill_rows else "not_available"
            ),
            "plans": len(plan_ids),
            "orders": len(order_ids),
            "fills": len(fill_rows),
            "maker_fills": liquidity["maker"],
            "taker_fills": liquidity["taker"],
            "fee_adjusted_realized_pnl_usd": (
                sum(settled_pnl) if settled_pnl else None
            ),
        },
        "raw_canonical_reconciliation": {
            "status": (
                "available"
                if reconciliation_present
                else "not_available"
            ),
            "raw_candidates": raw_candidates,
            "canonical_candidates": canonical_candidates,
            "candidate_delta": raw_candidates - canonical_candidates,
            "raw_fills": raw_fills,
            "canonical_fills": canonical_fills,
            "fill_delta": raw_fills - canonical_fills,
        },
    }
    report["report_hash"] = stable_sha256(report)
    return report
