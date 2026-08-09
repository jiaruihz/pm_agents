from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / (
    "scripts/analysis/reheat_risk/"
    "core_carry_price_conditioned_challenger.py"
)
SPEC = importlib.util.spec_from_file_location(
    "core_carry_price_conditioned_challenger", SCRIPT
)
assert SPEC and SPEC.loader
challenger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(challenger)

FULL_SCRIPT = ROOT / (
    "scripts/analysis/reheat_risk/"
    "core_carry_price_conditioned_full_support.py"
)
FULL_SPEC = importlib.util.spec_from_file_location(
    "core_carry_price_conditioned_full_support", FULL_SCRIPT
)
assert FULL_SPEC and FULL_SPEC.loader
full_support = importlib.util.module_from_spec(FULL_SPEC)
FULL_SPEC.loader.exec_module(full_support)


def test_training_weights_equalize_dates_city_days_and_checkpoints() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["2026-07-01"] * 3 + ["2026-07-02"],
            "city": ["A", "A", "B", "C"],
        }
    )
    weighted = frame.assign(weight=challenger.training_weights(frame))

    assert np.allclose(
        weighted.groupby("target_date")["weight"].sum().to_numpy(),
        [0.5, 0.5],
    )
    first = weighted[weighted["target_date"].eq("2026-07-01")]
    assert np.allclose(
        first.groupby("city")["weight"].sum().to_numpy(),
        [0.25, 0.25],
    )


def test_price_path_features_do_not_depend_on_label() -> None:
    base = pd.DataFrame(
        {
            "market_logit": [1.4],
            "forecast_reheat_after_now_f": [1.5],
            "forecast_remaining_gap_to_running_native": [0.8],
            "daylight_remaining_minutes": [240.0],
            "temp_trend_1h_f": [0.5],
            "temp_trend_3h_f": [1.2],
            "reheating_transition_num": [1.0],
            "strict_high_age_log": [2.0],
            "same_running_max_obs_count_log": [1.1],
            "minutes_since_last_strict_new_high": [30.0],
        }
    )
    zero = challenger.add_price_path_features(base.assign(label=0))
    one = challenger.add_price_path_features(base.assign(label=1))

    columns = [*challenger.PATH_FEATURES, *challenger.INTERACTION_FEATURES]
    assert np.allclose(zero[columns], one[columns], equal_nan=True)


def test_price_interaction_model_changes_correction_by_market_level() -> None:
    rows = []
    for date_index in range(6):
        target_date = f"2026-07-{date_index + 1:02d}"
        for market_mid, label, heat in (
            (0.55, 0, 2.0),
            (0.65, 1, 0.2),
            (0.85, 1, 2.0),
            (0.95, 1, 0.2),
        ):
            market_logit = float(np.log(market_mid / (1 - market_mid)))
            rows.append(
                {
                    "target_date": target_date,
                    "city": f"C{market_mid}",
                    "label": label,
                    "core_logit": market_logit,
                    "market_logit": market_logit,
                    "remaining_heat_load": heat,
                    "forecast_cross_pressure": heat,
                    "warming_persistence": heat,
                    "plateau_evidence": 1.0 / heat,
                    "fresh_high_runway": heat,
                    **{
                        f"market_x_{name}": market_logit * value
                        for name, value in {
                            "remaining_heat_load": heat,
                            "forecast_cross_pressure": heat,
                            "warming_persistence": heat,
                            "plateau_evidence": 1.0 / heat,
                            "fresh_high_runway": heat,
                        }.items()
                    },
                }
            )
    frame = pd.DataFrame(rows)
    bundle = challenger.fit_model(frame, "price_path_interactions")
    probability = challenger.predict_model(bundle, frame)

    assert np.isfinite(probability).all()
    assert ((probability > 0) & (probability < 1)).all()
    assert len(np.unique(np.round(probability, 6))) > 2


def test_select_first_positive_respects_optional_floor() -> None:
    frame = pd.DataFrame(
        [
            {
                "city": "A",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T01:30:00Z"),
                "current_bracket": "20",
                "ten_share_executable": True,
                "ten_share_cost_per_share": 0.72,
                "p_market_hold": 0.70,
                "p_model": 0.78,
            },
            {
                "city": "A",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T02:30:00Z"),
                "current_bracket": "20",
                "ten_share_executable": True,
                "ten_share_cost_per_share": 0.84,
                "p_market_hold": 0.82,
                "p_model": 0.88,
            },
        ]
    )

    no_floor = challenger.select_first_positive(frame, "p_model", floor=None)
    floor = challenger.select_first_positive(frame, "p_model", floor=0.80)

    assert float(no_floor.iloc[0]["p_market_hold"]) == 0.70
    assert float(floor.iloc[0]["p_market_hold"]) == 0.82


def test_full_support_interactions_are_price_conditioned() -> None:
    frame = pd.DataFrame(
        {
            "market_logit": [-1.0, 1.0],
            "decision_hour_local": [14.0, 14.0],
            "dewpoint_depression_f": [10.0, 10.0],
            "wind_speed_kt": [12.0, 12.0],
        }
    )
    for source, target in zip(
        full_support.WEATHER_FEATURES,
        full_support.INTERACTION_FEATURES,
        strict=True,
    ):
        frame[target] = frame["market_logit"] * frame[source]

    assert frame["market_x_wind_speed_kt"].tolist() == [-12.0, 12.0]


def test_candidate_family_has_no_price_bucket_gate() -> None:
    assert full_support.CANDIDATES == (
        "core_reweighted_linear",
        "market_spline_core",
        "market_spline_weather_interactions",
    )
    assert all("floor" not in name and "bucket" not in name for name in full_support.CANDIDATES)
