import math
import sqlite3

from scripts.analysis.reheat_risk.core_carry_llm_transition_card import (
    binary_metrics,
    canonical_settlement_labels,
    expanded_semantic_audit_selection,
    model_feature_contributions,
    policy_bucket,
    policy_contrast_selection,
    semantic_alignment_audit,
)
from src.strategies.weather_edge_v1.tools.intraday_transition_card import (
    build_transition_packet,
    transition_card_prompt,
)


def test_transition_packet_excludes_market_model_and_label() -> None:
    packet = build_transition_packet(
        {
            "city": "Wellington",
            "target_date": "2026-08-07",
            "decision_snapshot_ts_utc": "2026-08-07T01:35:49Z",
            "decision_hour_local": 13.55,
            "current_bracket": "11",
            "market_mid": 0.84,
            "model_probability_hold": 0.91,
            "label": 0,
            "raw_metar": "METAR NZWN 070100Z 01016KT 9999 SCT028 11/06 Q1030",
            "temp_trend_1h_f": 0.0,
            "temp_trend_3h_f": 1.8,
            "dewpoint_depression_f": 9.0,
            "wind_speed_kt": 16.0,
            "wind_dir_deg": 10.0,
            "forecast_peak_delta_hours_local": -6.45,
            "pressure_trend_3h_hpa": 0.0,
            "metar_sequence": [
                {
                    "last_obs_utc": "2026-08-07T00:30:00Z",
                    "fetched_at_utc": "2026-08-07T00:35:00Z",
                    "raw_metar": "METAR NZWN 070030Z 01016KT 9999 SCT028 11/06 Q1030",
                    "current_temp_c": 11.0,
                }
            ],
        }
    )

    text = str(packet)
    assert "market_mid" not in text
    assert "model_probability_hold" not in text
    assert "label" not in text
    assert packet["input_hash"]
    assert "pressure_trend_or_upstream_station_network" not in packet["known_missing_evidence"]
    assert len(packet["observed_state"]["metar_sequence"]) == 1


def test_prompt_forbids_probability_and_trade_output() -> None:
    prompt = transition_card_prompt(
        build_transition_packet(
            {
                "city": "Wellington",
                "target_date": "2026-08-07",
                "decision_snapshot_ts_utc": "2026-08-07T01:35:49Z",
            }
        )
    )
    assert "Do not output a win probability" in prompt
    assert "Separate boundary-layer mixing" in prompt


def test_canonical_settlement_labels_reads_only_requested_consistent_rows(tmp_path) -> None:
    db_path = tmp_path / "weather.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE settlement_outcomes "
            "(city TEXT, target_date TEXT, bracket TEXT, final_price REAL, settlement_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO settlement_outcomes VALUES (?, ?, ?, ?, ?)",
            [
                ("Wellington", "2026-08-06", "10", 1.0, "settled"),
                ("Munich", "2026-08-06", "24", 0.0, "settled"),
                ("Busan", "2026-08-06", "36", 0.5, "open"),
            ],
        )
    keys = {
        ("Wellington", "2026-08-06", "10"),
        ("Busan", "2026-08-06", "36"),
    }
    assert canonical_settlement_labels(db_path, keys) == {
        ("Wellington", "2026-08-06", "10"): 1
    }


def test_binary_metrics_use_probability_not_directional_accuracy() -> None:
    metrics = binary_metrics([(0.8, 1), (0.2, 0)])
    assert metrics is not None
    assert metrics["n"] == 2
    assert abs(float(metrics["brier"]) - 0.04) < 1e-12


def test_policy_contrast_includes_hit_near_miss_and_never_hit_control() -> None:
    common = {
        "probability_status": "scored_by_current_yes_core_artifact",
        "current_bracket": "12",
        "decision_hour_local": 15.0,
        "market_mid": 0.9,
    }
    rows = [
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-06",
            "decision_snapshot_ts_utc": "2026-08-06T02:00:00Z",
            "decision_status": "not_eligible",
            "eligible": False,
            "reasons": ["non_positive_taker_ev"],
        },
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-06",
            "decision_snapshot_ts_utc": "2026-08-06T03:00:00Z",
            "decision_status": "positive_taker_ev",
            "eligible": True,
            "reasons": [],
        },
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-07",
            "decision_snapshot_ts_utc": "2026-08-07T03:00:00Z",
            "decision_status": "not_eligible",
            "eligible": False,
            "reasons": ["non_positive_taker_ev"],
        },
    ]
    roles = policy_contrast_selection(rows)
    assert sorted(roles.values()) == [
        "never_selected_matched_control",
        "policy_selected",
        "same_city_day_near_miss",
    ]
    assert policy_bucket(rows[1]) == "policy_selected"


def test_model_feature_contributions_reconstruct_recorded_probability() -> None:
    artifact = {
        "artifact_version": "test",
        "artifact_hash": "hash",
        "numeric_features": ["market_logit", "wind_speed_kt"],
        "numeric_means": [0.0, 10.0],
        "numeric_scales": [1.0, 2.0],
        "numeric_medians": [0.0, 10.0],
        "coef": [1.0, 0.5],
        "intercept": 0.0,
    }
    expected = 1.0 / (1.0 + math.exp(-1.5))
    result = model_feature_contributions(
        {
            "features": {"market_logit": 1.0, "wind_speed_kt": 12.0},
            "model_probability_hold": expected,
            "artifact_hash": "row-hash",
        },
        artifact,
    )
    assert float(result["reconstruction_abs_error"]) < 1e-12
    assert [
        round(float(item["logit_contribution"]), 6)
        for item in result["feature_contributions"]
    ] == [1.0, 0.5]


def test_expanded_semantic_audit_adds_prelabel_city_day_diversity() -> None:
    common = {
        "probability_status": "scored_by_current_yes_core_artifact",
        "current_bracket": "12",
        "decision_hour_local": 15.0,
        "market_mid": 0.9,
        "model_probability_hold": 0.92,
        "decision_status": "not_eligible",
        "eligible": False,
        "reasons": ["non_positive_taker_ev"],
    }
    rows = [
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-06",
            "decision_snapshot_ts_utc": "2026-08-06T02:00:00Z",
        },
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-06",
            "decision_snapshot_ts_utc": "2026-08-06T03:00:00Z",
            "decision_status": "positive_taker_ev",
            "eligible": True,
            "reasons": [],
        },
        {
            **common,
            "city": "Wellington",
            "target_date": "2026-08-07",
            "decision_snapshot_ts_utc": "2026-08-07T03:00:00Z",
        },
        {
            **common,
            "city": "CapeTown",
            "target_date": "2026-08-07",
            "decision_snapshot_ts_utc": "2026-08-07T12:00:00Z",
            "market_mid": 0.6,
            "model_probability_hold": 0.4,
            "temp_trend_1h_f": -1.8,
        },
    ]
    roles = expanded_semantic_audit_selection(rows, target_size=4)
    assert len(roles) == 4
    assert "semantic_diversity_control" in roles.values()

    for row in rows:
        row["settlement_label"] = int(row["city"] == "CapeTown")
    assert expanded_semantic_audit_selection(rows, target_size=4) == roles


def test_semantic_alignment_audit_marks_mixing_wind_tension() -> None:
    result = semantic_alignment_audit(
        {"model_probability_hold": 0.97, "market_mid": 0.92},
        {
            "next_state": "current_high_holds",
            "reheat_risk": "low",
            "second_heat_lobe": "none",
            "wind_role": "mixing_only",
            "moisture_transition": "stable",
            "source_conflict": False,
        },
        {
            "feature_contributions": [
                {"feature": "wind_speed_kt", "logit_contribution": 0.8}
            ]
        },
        ["wind_direction_and_change"],
    )
    assert result is not None
    assert result["alignment"] == "aligned"
    assert "mixing_only_but_wind_speed_boosts_hold" in result["feature_semantic_flags"]
