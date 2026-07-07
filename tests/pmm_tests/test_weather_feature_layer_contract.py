from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from src.strategies.weather_edge_v1.tools import low_price_yes_tail_telemetry as tail_telemetry
from src.strategies.weather_edge_v1.tools import regime_routed_no_stable as stable_regime
from src.strategies.weather_edge_v1.tools import regime_routed_temperature_context
from weather_data_feed import weather_context
from weather_feature_layer import bias, execution, market, regimes, state, store
from weather_feature_layer.builders import build_weather_state_frame, build_weather_state_frame_with_audits
from weather_feature_layer.contracts import (
    DEFAULT_FEATURE_VERSION_MANIFEST,
    FEATURE_FRAME_REQUIRED_METADATA,
    FEATURE_FRAME_SCHEMA_VERSION,
    PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    PIT_PROVENANCE_LIVE_CAPTURE,
)
from weather_feature_layer.runtime_refs import attach_runtime_feature_frame_ref


def test_state_reexports_weather_context_without_private_multiplier() -> None:
    record = {
        "city": "LA",
        "wind_speed_kt": 12,
        "wind_dir_deg": 240,
        "sky_cover_code": 0,
        "temp_trend_1h_f": 1.5,
        "temp_trend_3h_f": 2.0,
        "relative_humidity_pct": 55,
        "dewpoint_depression_f": 18,
        "forecast_peak_delta_hours_local": -1,
    }

    assert state.temperature_context_features(record) == weather_context.temperature_context_features(record)
    assert state.cloud_warming_interaction(0, 1.5, 2.0) == weather_context.cloud_warming_interaction(0, 1.5, 2.0)
    assert state.heating_done_features(record) == weather_context.heating_done_features(record)
    assert not hasattr(state, "temperature_context_multiplier")
    assert not hasattr(weather_context, "temperature_context_multiplier")
    assert regime_routed_temperature_context.temperature_context_multiplier(
        {"route_leg": "runway_current_no", **state.temperature_context_features(record)}
    ) > 0


def test_heating_done_feature_is_price_free_and_directional() -> None:
    done = state.temperature_context_features(
        {
            "city": "Chengdu",
            "unit": "C",
            "forecast_peak_delta_hours_local": 1.25,
            "forecast_gap_to_running_native": 0.2,
            "decline_native": 0.4,
            "minutes_since_running_max": 90,
            "temp_trend_1h_f": 0.0,
            "temp_trend_3h_f": -0.2,
            "relative_humidity_pct": 82,
            "sky_cover_code": 3,
            "yes_best_ask": 0.99,
            "no_best_ask": 0.96,
        }
    )
    runway = state.temperature_context_features(
        {
            "city": "Chengdu",
            "unit": "C",
            "forecast_peak_delta_hours_local": -1.5,
            "forecast_gap_to_running_native": 2.0,
            "decline_native": 0.0,
            "minutes_since_running_max": 10,
            "temp_trend_1h_f": 1.4,
            "temp_trend_3h_f": 3.0,
            "relative_humidity_pct": 55,
            "sky_cover_code": 1,
            "yes_best_ask": 0.99,
            "no_best_ask": 0.96,
        }
    )

    assert done["heating_done_score_v1"] > runway["heating_done_score_v1"]
    assert done["heating_done_bucket_v1"] in {"heating_done_confirmed", "heating_done_probable"}
    assert runway["heating_done_bucket_v1"] == "runway_still_open"
    assert "yes_best_ask" not in done


def test_feature_frame_metadata_contract_includes_pit_provenance() -> None:
    assert "pit_provenance" in FEATURE_FRAME_REQUIRED_METADATA
    assert PIT_PROVENANCE_LIVE_CAPTURE == "live_capture"
    assert PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION == "archive_reconstruction"


def test_regime_labels_cover_f_and_c_city_without_unit_drift() -> None:
    rows = pd.DataFrame(
        [
            {
                "city": "LA",
                "unit": "F",
                "decision_hour_local": 10,
                "forecast_gap_to_running_native": 3.0,
                "relative_humidity_pct": 82,
                "sky_cover_code": 3,
                "dewpoint_depression_f": 8,
                "wind_speed_kt": 11,
                "minutes_since_running_max": 30,
                "decline_native": 0.2,
                "temp_trend_1h_f": 1.2,
                "temp_trend_3h_f": 2.4,
            },
            {
                "city": "London",
                "unit": "C",
                "decision_hour_local": 14,
                "forecast_gap_to_running_native": 1.5,
                "relative_humidity_pct": 70,
                "sky_cover_code": 1,
                "dewpoint_depression_f": 26,
                "wind_speed_kt": 8,
                "minutes_since_running_max": 90,
                "decline_native": 0.2,
                "temp_trend_1h_f": 0.2,
                "temp_trend_3h_f": -0.1,
            },
        ]
    )

    shared = regimes.add_regime_labels(rows)
    stable = stable_regime.add_regime_labels(rows)
    pd.testing.assert_frame_equal(shared, stable)

    assert shared.loc[0, "day_regime"] == "day_open_runway"
    assert shared.loc[0, "moisture_cloud_regime"] == "humid_overcast_suppression"
    assert shared.loc[1, "day_regime"] == "day_open_runway"
    assert shared.loc[1, "running_max_state"] == "near_high_plateau"


def test_market_geometry_preserves_stable_and_tail_bracket_semantics() -> None:
    parsed = market.parse_bracket("36+")
    assert parsed is not None
    assert parsed.low == 36
    assert parsed.high is None
    assert market.bracket_contains(parsed, 42)
    assert stable_regime.parse_bracket("36+") == parsed

    assert market.parse_bracket_bounds("36+") == (36.0, 36.0)
    assert tail_telemetry.parse_bracket_bounds("36+") == (36.0, 36.0)
    assert market.bracket_distance_features({"bracket": "36+", "forecast_max_native": 37.2}) == (
        tail_telemetry.bracket_distance_features({"bracket": "36+", "forecast_max_native": 37.2})
    )


def test_execution_book_state_matches_legacy_heada_thresholds() -> None:
    assert execution.classify_book_state(None, 25.0) == "missing"
    assert execution.classify_book_state(0.03, 25.0) == "feasible"
    assert execution.classify_book_state(0.031, 25.0) == "thin_wide"
    assert execution.classify_book_state(0.03, 24.999) == "thin_wide"


def test_execution_features_are_side_normalized_and_city_profiled() -> None:
    rows = pd.DataFrame(
        [
            {
                "city": "LA",
                "side": "BUY_YES",
                "yes_spread": 0.02,
                "yes_depth_ask_5c": 50,
                "decision_entry_price": 0.10,
            },
            {
                "city": "LA",
                "side": "BUY_NO",
                "no_spread": 0.05,
                "no_depth_ask_5c": 10,
                "decision_entry_price": 0.40,
            },
            {
                "city": "London",
                "side": "BUY_YES",
                "yes_spread": None,
                "yes_depth_ask_5c": None,
                "decision_entry_price": 0.20,
            },
        ]
    )

    enriched = execution.add_side_execution_features(rows)
    la_yes = enriched.iloc[0]
    la_no = enriched.iloc[1]
    london = enriched.iloc[2]
    assert la_yes["book_state_v1"] == "feasible"
    assert la_yes["side_depth_ask_5c"] == 50
    assert math.isclose(la_yes["side_fillable_notional_ask_5c"], 5.0)
    assert la_no["book_state_v1"] == "thin_wide"
    assert la_no["side_spread"] == 0.05
    assert london["book_state_v1"] == "missing"

    profile = execution.summarize_city_execution_profile(enriched).set_index("city")
    assert profile.loc["LA", "execution_profile_rows"] == 2
    assert math.isclose(profile.loc["LA", "book_state_feasible_rate"], 0.5)
    assert math.isclose(profile.loc["LA", "book_state_thin_wide_rate"], 0.5)
    assert math.isclose(profile.loc["London", "book_state_missing_rate"], 1.0)


def test_market_geometry_matches_tmax_p0_p3_fixture_columns() -> None:
    script_dir = Path("scripts/analysis/reheat_risk").resolve()
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import research_tmax_distribution_p0_anchor_scorecard_v1 as tmax_p0  # noqa: PLC0415
    import research_tmax_distribution_p1_fusion_scorecard_v1 as tmax_p1  # noqa: PLC0415
    import research_tmax_distribution_p3_feature_ablation_v1 as tmax_p3  # noqa: PLC0415

    rows = pd.DataFrame(
        [
            {
                "current_bracket": "82-83",
                "d1_no_bracket": "84",
                "d2_no_bracket": "85",
                "decision_hour_local": 13.5,
                "forecast_peak_delta_hours_local": -1.25,
                "forecast_max_native": 84.4,
                "running_native": 82.04,
                "current_native": 81.5,
                "current_yes_ask": 0.42,
                "current_yes_bid": 0.39,
                "current_yes_ask_size": 120,
                "current_yes_bid_size": 90,
                "current_bracket_no_ask": 0.61,
                "current_bracket_no_bid": 0.58,
                "d1_no_ask": 0.75,
                "d1_no_bid": 0.72,
                "d2_no_ask": 0.91,
                "d2_no_bid": 0.88,
            },
            {
                "current_bracket": "86+",
                "d1_no_bracket": "87+",
                "d2_no_bracket": "below 84",
                "decision_hour_local": None,
                "forecast_peak_delta_hours_local": None,
                "forecast_max_native": 86.8,
                "running_native": 85.7,
                "current_native": 85.1,
                "current_yes_ask": 0.51,
                "current_yes_bid": 0.47,
                "current_bracket_no_ask": 0.50,
                "current_bracket_no_bid": 0.46,
                "d1_no_ask": 0.63,
                "d1_no_bid": 0.60,
                "d2_no_ask": 0.22,
                "d2_no_bid": 0.18,
            },
        ]
    )

    for label in ["82-83", "84", "86+", "below 84"]:
        assert market.settlement_interval(label) == tmax_p0._interval(label)

    legacy_rows = []
    for item in rows.to_dict("records"):
        current_iv = tmax_p0._interval(item.get("current_bracket"))
        d1_iv = tmax_p0._interval(item.get("d1_no_bracket"))
        d2_iv = tmax_p0._interval(item.get("d2_no_bracket"))
        legacy = dict(item)
        hour = tmax_p0._as_float(item.get("decision_hour_local"))
        legacy["hour_bucket"] = tmax_p0._hour_bucket(item.get("decision_hour_local"))
        if hour is not None:
            legacy["decision_hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            legacy["decision_hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)
        peak_delta = tmax_p0._as_float(item.get("forecast_peak_delta_hours_local"))
        legacy["forecast_peak_delta_abs"] = abs(peak_delta) if peak_delta is not None else None
        current_upper = tmax_p1._safe_upper(current_iv)
        d1_upper = tmax_p1._safe_upper(d1_iv)
        d2_upper = tmax_p1._safe_upper(d2_iv)
        current_mid = tmax_p1._safe_mid(current_iv)
        d1_mid = tmax_p1._safe_mid(d1_iv)
        d2_mid = tmax_p1._safe_mid(d2_iv)
        legacy["forecast_minus_running_native"] = tmax_p1._delta(item.get("forecast_max_native"), item.get("running_native"))
        legacy["forecast_minus_current_native"] = tmax_p1._delta(item.get("forecast_max_native"), item.get("current_native"))
        legacy["running_minus_current_native"] = tmax_p1._delta(item.get("running_native"), item.get("current_native"))
        legacy["forecast_to_current_upper_native"] = tmax_p1._delta(item.get("forecast_max_native"), current_upper)
        legacy["forecast_to_d1_upper_native"] = tmax_p1._delta(item.get("forecast_max_native"), d1_upper)
        legacy["forecast_to_d2_upper_native"] = tmax_p1._delta(item.get("forecast_max_native"), d2_upper)
        legacy["forecast_to_current_mid_native"] = tmax_p1._delta(item.get("forecast_max_native"), current_mid)
        legacy["forecast_to_d1_mid_native"] = tmax_p1._delta(item.get("forecast_max_native"), d1_mid)
        legacy["forecast_to_d2_mid_native"] = tmax_p1._delta(item.get("forecast_max_native"), d2_mid)
        legacy["running_to_current_upper_native"] = tmax_p1._delta(item.get("running_native"), current_upper)
        legacy["current_to_current_upper_native"] = tmax_p1._delta(item.get("current_native"), current_upper)
        if current_mid is not None:
            legacy["running_position_in_current_native"] = tmax_p1._delta(item.get("running_native"), current_mid)
            legacy["current_position_in_current_native"] = tmax_p1._delta(item.get("current_native"), current_mid)
        legacy_rows.append(legacy)
    legacy_df = tmax_p3._add_boundary_features(pd.DataFrame(legacy_rows))
    shared = market.add_market_geometry_features(rows)

    columns = [
        "hour_bucket",
        "decision_hour_sin",
        "decision_hour_cos",
        "forecast_peak_delta_abs",
        "forecast_minus_running_native",
        "forecast_minus_current_native",
        "running_minus_current_native",
        "forecast_to_current_upper_native",
        "forecast_to_d1_upper_native",
        "forecast_to_d2_upper_native",
        "forecast_to_current_mid_native",
        "forecast_to_d1_mid_native",
        "forecast_to_d2_mid_native",
        "running_to_current_upper_native",
        "current_to_current_upper_native",
        "running_position_in_current_native",
        "current_position_in_current_native",
        "current_bracket_width_native",
        "current_frac_in_current_bracket",
        "running_frac_in_current_bracket",
        "forecast_frac_in_current_bracket",
        "current_native_frac",
        "running_native_frac",
        "forecast_native_frac",
        "current_dist_to_upper_share",
        "running_dist_to_upper_share",
        "forecast_dist_to_upper_share",
    ]
    pd.testing.assert_frame_equal(shared[columns], legacy_df[columns])

    assert math.isclose(shared.loc[0, "current_no_best_ask"], 0.61)
    assert math.isclose(shared.loc[0, "current_no_best_bid"], 0.58)
    assert math.isclose(shared.loc[0, "current_no_mid"], 0.595)
    assert math.isclose(shared.loc[0, "current_no_spread"], 0.03)
    assert math.isclose(shared.loc[0, "d1_no_mid"], 0.735)
    assert math.isclose(shared.loc[0, "d2_no_spread"], 0.03)


def test_market_side_quote_helpers_match_range_rv_and_tp_stop_semantics() -> None:
    script_dir = Path("scripts/analysis/market_structure_edge").resolve()
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import research_range_rv_scanner as range_rv  # noqa: PLC0415
    import research_range_rv_variant_lab_v03 as variants  # noqa: PLC0415

    row = {"yes_spread": 0.03, "no_spread": 0.04}
    for side in ["BUY_YES", "BUY_NO"]:
        assert market.side_cost_from_yes(side, 0.42) == range_rv.side_cost_from_yes(side, 0.42)
        assert market.side_cost_from_yes(side, 0.42) == variants.leg_cost(side, 0.42)
        assert market.side_spread(row, side) == range_rv.side_spread(row, side)

    yes = market.side_quote_from_yes_book(
        side="BUY_YES",
        yes_best_bid=0.42,
        yes_best_ask=0.45,
        yes_best_bid_size=80,
        yes_best_ask_size=120,
    )
    assert yes == {
        "side": "BUY_YES",
        "side_best_bid": 0.42,
        "side_best_ask": 0.45,
        "side_best_bid_size": 80.0,
        "side_best_ask_size": 120.0,
        "side_spread": 0.030000000000000027,
        "side_mid": 0.435,
        "decision_entry_price": 0.45,
    }

    no = market.side_quote_from_yes_book(
        side="BUY_NO",
        yes_best_bid=0.42,
        yes_best_ask=0.45,
        yes_best_bid_size=80,
        yes_best_ask_size=120,
    )
    assert no == {
        "side": "BUY_NO",
        "side_best_bid": 0.55,
        "side_best_ask": 0.5800000000000001,
        "side_best_bid_size": 120.0,
        "side_best_ask_size": 80.0,
        "side_spread": 0.030000000000000027,
        "side_mid": 0.5650000000000001,
        "decision_entry_price": 0.5800000000000001,
    }


@dataclass
class _Resources:
    bias_index: dict[tuple[str, str], list[tuple[str, float]]]


def test_bias_asof_features_match_tail_telemetry_contract() -> None:
    resources = _Resources(
        {
            ("LA", "gfs"): [
                ("2026-05-01", 1.0),
                ("2026-05-02", -2.0),
                ("2026-05-04", 3.0),
                ("2026-05-08", 9.0),
            ]
        }
    )

    shared = bias.asof_bias_features(resources, city="LA", forecast_model="gfs", target_date="2026-05-05")
    legacy = tail_telemetry.asof_bias_features(resources, city="LA", forecast_model="gfs", target_date="2026-05-05")
    assert shared == legacy
    assert shared["bias_n_asof"] == 3
    assert math.isclose(shared["bias_mean_asof"], 0.666667)
    assert math.isclose(shared["hot_tail_pct_asof"], 2 / 3, abs_tol=1e-6)


def test_bias_reference_metadata_and_lookup_contract(tmp_path: Path) -> None:
    path = tmp_path / "city_model_error_summary.csv"
    path.write_text(
        "\n".join(
            [
                "city,model,n,first_date,last_date,bias,mae,p90,p10,pct_actual_ge_forecast_plus_1,pct_forecast_ge_actual_plus_1",
                "LA,gfs,3,2026-05-01,2026-05-03,0.8,1.1,2.5,-0.5,0.45,0.10",
                "London,ecmwf,2,2026-05-02,2026-05-04,-0.6,1.2,0.5,-2.0,0.10,0.40",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    reference = bias.load_city_source_bias_reference(
        path,
        generated_at_utc="2026-07-06T00:00:00Z",
        settlement_source="fixture_settlement",
        source_policy="fixture_policy",
    )
    metadata = bias.bias_reference_metadata_dict(reference.metadata)

    assert metadata["schema_version"] == "bias_reference_v1"
    assert metadata["generated_at_utc"] == "2026-07-06T00:00:00Z"
    assert metadata["build_window_start"] == "2026-05-01"
    assert metadata["build_window_end"] == "2026-05-04"
    assert metadata["settlement_source"] == "fixture_settlement"
    assert metadata["source_policy"] == "fixture_policy"
    assert len(metadata["input_sha256"]) == 64
    assert metadata["input_row_count"] == 2
    assert metadata["city_count"] == 2
    assert metadata["model_count"] == 2
    assert metadata["snapshot_id"] == metadata["input_sha256"][:16]

    assert reference.lookup[("LA", "gfs")]["city_source_bias_regime"] == "hot_underforecast_clean"
    assert reference.lookup[("London", "ecmwf")]["city_source_bias_regime"] == "cold_overforecast_clean"
    assert bias.load_city_source_bias_lookup(path) == reference.lookup


def test_error_bias_index_reference_preserves_asof_settlement_rule(tmp_path: Path) -> None:
    path = tmp_path / "daily_error_rows.csv"
    path.write_text(
        "\n".join(
            [
                "city,model,date,error_f_actual_minus_forecast",
                "LA,gfs,2026-05-01,1.0",
                "LA,gfs,2026-05-02,-2.0",
                "LA,gfs,2026-05-03,5.0",
                "LA,gfs,2026-05-04,9.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    reference = bias.load_error_bias_index_reference(path, generated_at_utc="2026-07-06T00:00:00Z")
    metadata = bias.bias_reference_metadata_dict(reference.metadata)
    features = bias.asof_error_bias_features(
        reference.bias_index,
        city="LA",
        forecast_model="gfs",
        target_date="2026-05-03",
    )

    assert metadata["build_window_start"] == "2026-05-01"
    assert metadata["build_window_end"] == "2026-05-04"
    assert features["bias_n_asof"] == 2
    assert features["bias_mean_asof"] == -0.5
    assert features["hot_tail_pct_asof"] == 0.5


def test_missing_bias_reference_is_explicit_empty(tmp_path: Path) -> None:
    reference = bias.load_city_source_bias_reference(
        tmp_path / "missing.csv",
        generated_at_utc="2026-07-06T00:00:00Z",
        snapshot_id="missing-fixture",
    )

    assert reference.lookup == {}
    assert reference.metadata.input_row_count == 0
    assert reference.metadata.snapshot_id == "missing-fixture"


def test_city_source_bias_classifier_contract() -> None:
    assert (
        bias.classify_city_source_bias(
            {
                "bias": 0.8,
                "p90": 2.5,
                "p10": -0.5,
                "pct_actual_ge_forecast_plus_1": 0.45,
                "pct_forecast_ge_actual_plus_1": 0.10,
                "mae": 1.4,
            }
        )
        == "hot_underforecast_clean"
    )
    assert (
        bias.classify_city_source_bias(
            {
                "bias": -0.6,
                "p90": 0.5,
                "p10": -2.0,
                "pct_actual_ge_forecast_plus_1": 0.10,
                "pct_forecast_ge_actual_plus_1": 0.40,
                "mae": 1.2,
            }
        )
        == "cold_overforecast_clean"
    )


def test_weather_state_frame_builder_carries_metadata_and_unit_contract() -> None:
    snapshot_rows = [
        {
            "city": "LA",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T19:00:00Z",
            "unit": "F",
            "timezone_name": "America/Los_Angeles",
            "forecast_source": "gfs",
            "forecast_max_native": 72.8,
            "forecast_peak_hour_local": 14,
            "forecast_peak_delta_hours_local": -2,
        },
        {
            "city": "London",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T13:00:00Z",
            "unit": "C",
            "timezone_name": "Europe/London",
            "forecast_source": "ecmwf",
            "forecast_max_native": 22.5,
            "forecast_max_f": 72.4,
            "forecast_peak_hour_local": 16,
            "forecast_peak_delta_hours_local": -2,
        },
    ]
    observation_cache = {
        "records": [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "last_obs_utc": "2026-07-06T18:20:00Z",
                "cadence_min": 60,
                "current_temp_c": 20,
                "running_max_c": 21,
                "tmpf_now": 68,
                "dwpf_now": 55,
                "dewpoint_depression_f": 13,
                "relative_humidity_pct": 55,
                "wind_speed_kt": 12,
                "wind_dir_deg": 240,
                "sky_code_now": "FEW",
                "d_tmpf_1h": 1.4,
                "d_tmpf_3h": 2.2,
                "minutes_since_running_max": 35,
                "running_max_obs_utc": "2026-07-06T18:00:00Z",
            },
            {
                "city": "London",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "EGLL",
                "last_obs_utc": "2026-07-06T12:30:00Z",
                "cadence_min": 60,
                "current_temp_c": 20,
                "running_max_c": 21,
                "relative_humidity_pct": 82,
                "wind_speed_kt": 8,
                "wind_dir_deg": 180,
                "sky_cover_code": 3,
                "temp_trend_1h_f": 0.2,
                "temp_trend_3h_f": -0.1,
                "minutes_since_running_max": 90,
            },
        ]
    }

    frame, audits = build_weather_state_frame_with_audits(
        snapshot_rows,
        observation_cache,
        as_of_ts_utc="2026-07-06T19:00:00Z",
        source_profile_id="mac_weather_data_feed_v1",
        input_snapshot_id="fixture-snapshot",
    )

    assert [audit.status for audit in audits] == ["included", "included"]
    assert set(frame["city"]) == {"LA", "London"}
    assert frame.attrs["feature_metadata"]["pit_provenance"] == PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION
    for key in FEATURE_FRAME_REQUIRED_METADATA:
        assert key in frame.columns

    la = frame.set_index("city").loc["LA"]
    assert math.isclose(la["current_native"], 68.0)
    assert math.isclose(la["running_native"], 69.8)
    assert math.isclose(la["forecast_gap_to_running_native"], 3.0)
    assert la["decision_hour_local"] == 12
    assert la["station_gap_state"] == "within_expected_cadence"
    assert la["sky_cover_code"] == 1
    assert la["warming_state"] == "warming"
    assert la["solar_window"] == "solar_peak_window"
    assert la["pit_provenance"] == PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION

    london = frame.set_index("city").loc["London"]
    assert math.isclose(london["current_native"], 20.0)
    assert math.isclose(london["running_native"], 21.0)
    assert math.isclose(london["forecast_gap_to_running_native"], 1.5)
    assert math.isclose(london["forecast_max_f"], 72.4)
    assert london["station_gap_state"] == "within_expected_cadence"
    assert london["moisture_cloud_regime"] == "humid_overcast_suppression"


def test_feature_frame_store_ref_joins_opportunity_row_back_to_frame(tmp_path: Path) -> None:
    metadata = {
        "feature_schema_version": FEATURE_FRAME_SCHEMA_VERSION,
        "feature_grain": "city_date_snapshot",
        "as_of_ts_utc": "2026-07-06T19:00:00Z",
        "source_profile_id": "fixture_profile",
        "feature_version_manifest": dict(DEFAULT_FEATURE_VERSION_MANIFEST),
        "pit_provenance": PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
        "builder_version": "fixture_builder_v1",
        "input_snapshot_id": "fixture-snapshot",
    }
    frame = pd.DataFrame(
        [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "decision_snapshot_ts_utc": "2026-07-06T19:00:00Z",
                "forecast_gap_to_running_native": 3.0,
                **metadata,
            }
        ]
    )
    frame.attrs["feature_metadata"] = metadata

    stored = store.write_feature_frame_store(frame, tmp_path)
    ref = stored.row_refs[0]
    opportunity_row = {"candidate_id": "fixture-candidate", "feature_frame_ref": ref}
    loaded = store.load_feature_row_by_ref(tmp_path, opportunity_row["feature_frame_ref"])

    assert stored.row_count == 1
    assert stored.rows_path.exists()
    assert stored.index_path.exists()
    assert stored.manifest_path.exists()
    for key in [
        "feature_schema_version",
        "feature_grain",
        "feature_version_manifest",
        "as_of_ts_utc",
        "input_snapshot_id",
        "pit_provenance",
        "feature_row_key",
        "feature_row_id",
        "store_frame_id",
    ]:
        assert key in ref
    assert loaded["city"] == "LA"
    assert loaded["target_date"] == "2026-07-06"
    assert loaded["feature_frame_ref"]["feature_row_id"] == ref["feature_row_id"]


def test_runtime_feature_frame_ref_attach_is_non_blocking(tmp_path: Path) -> None:
    row = {
        "strategy_instance": "fixture_shadow_v1",
        "strategy_id": "fixture_shadow_v1",
        "city": "LA",
        "target_date": "2026-07-06",
        "decision_snapshot_ts_utc": "2026-07-06T19:00:00Z",
        "bracket": "82-83",
        "model_p_yes": 0.18,
    }

    attached = attach_runtime_feature_frame_ref(
        row,
        store_root=tmp_path / "store",
        feature_grain="fixture_shadow_decision",
        source_profile_id="fixture_shadow_v1",
        builder_version="fixture_feature_ref_v1",
    )
    loaded = store.load_feature_row_by_ref(tmp_path / "store", attached["feature_frame_ref"])

    assert attached["feature_frame_ref_status"] == "stored"
    assert loaded["city"] == "LA"
    assert loaded["feature_frame_ref"]["feature_row_id"] == attached["feature_frame_ref"]["feature_row_id"]

    blocking_path = tmp_path / "not_a_directory"
    blocking_path.write_text("occupied\n", encoding="utf-8")
    non_blocking = attach_runtime_feature_frame_ref(
        row,
        store_root=blocking_path,
        feature_grain="fixture_shadow_decision",
        source_profile_id="fixture_shadow_v1",
        builder_version="fixture_feature_ref_v1",
    )
    assert non_blocking["feature_frame_ref_status"] == "error"
    assert "feature_frame_ref_error" in non_blocking


def test_weather_state_frame_builder_rejects_invalid_pit_provenance() -> None:
    snapshot_rows = [
        {
            "city": "LA",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T19:00:00Z",
            "unit": "F",
        }
    ]
    observation_cache = {
        "records": [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "current_temp_c": 20,
                "running_max_c": 21,
            }
        ]
    }

    with pytest.raises(ValueError, match="invalid pit_provenance"):
        build_weather_state_frame(
            snapshot_rows,
            observation_cache,
            as_of_ts_utc="2026-07-06T19:00:00Z",
            pit_provenance="detect_time_only",
        )


def test_weather_state_frame_builder_does_not_use_utc_hour_as_local_fallback() -> None:
    frame = build_weather_state_frame(
        [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T19:00:00Z",
                "unit": "F",
                "forecast_max_native": 72,
            }
        ],
        {
            "records": [
                {
                    "city": "LA",
                    "target_date": "2026-07-06",
                    "status": "ok",
                    "source": "aviationweather_metar",
                    "station": "KLAX",
                    "current_temp_c": 20,
                    "running_max_c": 21,
                }
            ]
        },
        as_of_ts_utc="2026-07-06T19:00:00Z",
    )

    row = frame.iloc[0]
    assert pd.isna(row["decision_hour_local"])
    assert row["solar_window"] == "hour_missing"


def test_weather_state_frame_builder_chooses_representative_row_deterministically() -> None:
    frame = build_weather_state_frame(
        [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T18:00:00Z",
                "unit": "F",
                "forecast_max_native": 70,
                "forecast_peak_hour_local": 14,
                "forecast_peak_delta_hours_local": -3,
            },
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T19:00:00Z",
                "unit": "F",
                "forecast_max_native": 72,
                "forecast_peak_hour_local": 14,
                "forecast_peak_delta_hours_local": -2,
            },
        ],
        {
            "records": [
                {
                    "city": "LA",
                    "target_date": "2026-07-06",
                    "status": "ok",
                    "source": "aviationweather_metar",
                    "station": "KLAX",
                    "current_temp_c": 20,
                    "running_max_c": 21,
                }
            ]
        },
        as_of_ts_utc="2026-07-06T19:00:00Z",
    )

    assert len(frame) == 1
    assert frame.iloc[0]["decision_snapshot_ts_utc"] == "2026-07-06T19:00:00Z"
    assert frame.iloc[0]["forecast_max_native"] == 72
