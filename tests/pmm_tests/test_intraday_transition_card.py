import sqlite3

from scripts.analysis.reheat_risk.research_core_carry_llm_transition_card_v1 import (
    binary_metrics,
    canonical_settlement_labels,
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
        }
    )

    text = str(packet)
    assert "market_mid" not in text
    assert "model_probability_hold" not in text
    assert "label" not in text
    assert packet["input_hash"]
    assert "pressure_trend_or_upstream_station_network" not in packet["known_missing_evidence"]


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
