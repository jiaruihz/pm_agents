from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/analysis/tmin/research_tmin_v2_2_readout_v1.py"
SPEC = importlib.util.spec_from_file_location("v22_readout", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_truth_contract_fails_closed_below_99_percent(tmp_path: Path) -> None:
    reconciliation = pd.DataFrame(
        {
            "city": ["Seoul", "Tokyo"],
            "target_date": ["2026-01-01", "2026-01-01"],
            "exchange_winner_count": [1, 1],
            "reconciliation_status": [
                "EXACT_MATCH_IEM_MIRROR",
                "UNRESOLVED_MISMATCH",
            ],
        }
    )
    (tmp_path / "Tokyo_2026-01-01.json").write_text("{}\n", encoding="utf-8")
    result, keys = MODULE.truth_contract(reconciliation, tmp_path, "2026-08-30")
    assert result["overall_exact_match_rate"] == 0.5
    assert result["all_mismatches_row_level_audited"]
    assert not result["pass"]
    assert keys == {("Tokyo", "2026-01-01")}


def test_exact_truth_innovation_and_outside_market_fallback() -> None:
    archive_rows = []
    for day in range(1, 5):
        for city in MODULE.SUPPORTED_CITIES:
            archive_rows.append(
                {
                    "city": city,
                    "target_date": f"2026-01-0{day}",
                    "checkpoint_hour": 6,
                    "status": "PIT_NATIVE_VINTAGE_ELIGIBLE",
                    "training_truth_eligible": day != 2,
                    "forecast_error": float(day - 2),
                    "label_no_further_cooling": int(day % 2 == 0),
                    "forecast_remaining_min": 10.0,
                    "model_run": f"run-{day}",
                }
            )
    p0 = pd.DataFrame(
        [
            {
                "checkpoint_id": "route",
                "city": "Seoul",
                "target_date": "2026-01-04",
                "checkpoint_hour": 6,
                "supported_city": True,
                "routing_indicator": 1,
                "next_colder_boundary_native": 10.5,
                "label": 1,
                "p_market": 0.8,
            },
            {
                "checkpoint_id": "outside",
                "city": "Seoul",
                "target_date": "2026-01-04",
                "checkpoint_hour": 12,
                "supported_city": True,
                "routing_indicator": 0,
                "next_colder_boundary_native": 10.5,
                "label": 1,
                "p_market": 0.9,
            },
        ]
    )
    output = MODULE.attach_innovation(
        p0, pd.DataFrame(archive_rows), exclude_ambiguous_truth=True
    )
    all_source = MODULE.attach_innovation(
        p0, pd.DataFrame(archive_rows), exclude_ambiguous_truth=False
    )
    assert np.isfinite(output.loc[output["checkpoint_id"].eq("route"), "z_weather"]).all()
    assert not np.allclose(
        output.loc[output["checkpoint_id"].eq("route"), "z_weather"],
        all_source.loc[all_source["checkpoint_id"].eq("route"), "z_weather"],
    )
    assert output.loc[output["checkpoint_id"].eq("outside"), "z_weather"].eq(0.0).all()
    fallback, folds = MODULE.fit_or_fallback(output, authorized=False)
    assert not folds
    assert fallback["p_v2_2"].to_numpy().tobytes() == fallback[
        "p_market"
    ].to_numpy().tobytes()


def test_forecast_gate_passes_only_with_contract_counts() -> None:
    rows = []
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    for city in MODULE.SUPPORTED_CITIES:
        for date in dates:
            for hour in MODULE.ACTIVE_HOURS:
                rows.append(
                    {
                        "city": city,
                        "target_date": date.date().isoformat(),
                        "checkpoint_hour": hour,
                        "status": "PIT_NATIVE_VINTAGE_ELIGIBLE",
                        "available_at": pd.Timestamp(date, tz="UTC"),
                        "next_colder_event": date.day <= 20,
                    }
                )
    result = MODULE.forecast_gate(pd.DataFrame(rows), "2026-03-31")
    assert result["independent_city_days"] == 120
    assert result["next_colder_event_city_days"] >= 40
    assert result["pass"]


def test_audited_mismatch_policy_allows_fit_but_keeps_truth_diagnostic() -> None:
    authorized, blockers, diagnostics = MODULE.fit_authorization(
        policy="AUDITED_MISMATCH_EXCLUSION",
        coverage={"pass": True, "failures": []},
        truth={
            "pass": False,
            "all_mismatches_row_level_audited": True,
            "no_unexplained_mismatch_in_new_forward": True,
        },
        foundation={"beats_clock_logloss": True},
        gradient={"S": 0.1, "one_sided_lower_95": -0.2},
        sensitivity_sign_change=False,
        gradient_max_abs_date_share=0.36,
    )
    assert authorized
    assert blockers == []
    assert "FULL_SOURCE_TRUTH_RATE_BELOW_99_PERCENT" in diagnostics
    assert "ROUTED_SCORE_GRADIENT_LOWER_BOUND_NOT_POSITIVE" in diagnostics
    assert "SINGLE_DATE_GRADIENT_CONCENTRATION_ABOVE_35_PERCENT" in diagnostics


def test_authorized_fit_preserves_market_bytes_outside_route() -> None:
    rows = []
    for day in range(1, 13):
        date = f"2026-01-{day:02d}"
        for city in MODULE.SUPPORTED_CITIES:
            label = int(day % 2 == 0)
            rows.extend(
                [
                    {
                        "checkpoint_id": f"{city}-{date}-route",
                        "city": city,
                        "target_date": date,
                        "supported_city": True,
                        "routing_indicator": 1,
                        "label": label,
                        "p_market": 0.8,
                        "z_weather": 0.4,
                    },
                    {
                        "checkpoint_id": f"{city}-{date}-outside",
                        "city": city,
                        "target_date": date,
                        "supported_city": True,
                        "routing_indicator": 0,
                        "label": label,
                        "p_market": 0.9,
                        "z_weather": 0.0,
                    },
                ]
            )
    rows.append(
        {
            "checkpoint_id": "London-unsupported-active",
            "city": "London",
            "target_date": "2026-01-12",
            "supported_city": False,
            "routing_indicator": 1,
            "label": 1,
            "p_market": 0.95,
            "z_weather": np.nan,
        }
    )
    fitted, folds = MODULE.fit_or_fallback(pd.DataFrame(rows), authorized=True)
    active = fitted["supported_city"] & fitted["routing_indicator"].eq(1)
    fallback = ~active
    assert any(item.get("status") == "FIT" for item in folds)
    assert all(
        item["training_max_target_date"] < item["test_date"]
        for item in folds
        if item.get("status") == "FIT"
    )
    assert fitted.loc[fallback, "p_v2_2"].to_numpy().tobytes() == fitted.loc[
        fallback, "p_market"
    ].to_numpy().tobytes()
    assert fitted.loc[active, "fit_status"].eq("FIT_PRIOR_DATE_POSTERIOR").any()


def test_trade_readout_one_trade_numeric_and_row_summary_consistency(
    tmp_path: Path,
) -> None:
    rows = []
    predictions = []
    for index in range(168):
        checkpoint_id = f"checkpoint-{index:03d}"
        is_selected = index == 0
        rows.append(
            {
                "checkpoint_id": checkpoint_id,
                "candidate_id": f"candidate-{index:03d}",
                "decision_ts_utc": f"2026-08-23T{index % 24:02d}:00:00Z",
                "city": "Seoul",
                "target_date": "2026-08-23",
                "settled_binary": True,
                "label": 1,
                "p_model": 0.50,
                "challenger_p": 0.50,
                "execution_candidate_status_gate": True,
                "executable_cost": 0.94,
                "cooling_window_state": "morning_cooling",
            }
        )
        predictions.append(
            {
                "checkpoint_id": checkpoint_id,
                "p_v2_2": 0.956 if is_selected else 0.50,
            }
        )
    trade_path = tmp_path / "trade.csv"
    pd.DataFrame(rows).to_csv(trade_path, index=False)

    summary, row_level, selected = MODULE.trade_readout(
        trade_path, pd.DataFrame(predictions)
    )
    v2 = summary["overall"][MODULE.MODEL_ID]
    assert v2["positive_edge_checkpoint_rows"] == 1
    assert v2["settled_trades"] == 1
    assert v2["wins"] == 1
    assert np.isclose(v2["five_share_cost"], 4.7141)
    assert np.isclose(v2["fee_adjusted_pnl"], 0.2859)
    assert int(row_level["v2_2_positive_edge_checkpoint"].sum()) == 1
    assert int(row_level["v2_2_selected_city_date"].sum()) == 1
    assert int(selected["model"].eq(MODULE.MODEL_ID).sum()) == 1
