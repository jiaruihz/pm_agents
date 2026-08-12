from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import tokyo_market_prior_adapter as module


def test_five_share_cost_uses_depth_and_weather_fee() -> None:
    book = {
        "summary": {
            "asks": [
                {"price": 0.40, "size": 2.0},
                {"price": 0.42, "size": 4.0},
            ]
        }
    }
    cash, effective = module.five_share_cost(book)
    expected = 2 * (0.40 + module.weather_fee_per_share(0.40)) + 3 * (
        0.42 + module.weather_fee_per_share(0.42)
    )
    assert cash == expected
    assert effective == expected / 5


def test_candidate_rows_uses_weather_head_and_rejects_delayed_replay(tmp_path) -> None:
    path = tmp_path / "bundles.jsonl"

    def bundle(event_id: str, decision: str) -> dict:
        return {
            "information_event": {
                "information_event_id": event_id,
                "first_seen_at_utc": "2026-08-02T01:07:00+00:00",
                "source_event_ts_utc": "2026-08-02T01:00:00+00:00",
                "pit_lineage_class": "collector_exact",
            },
            "model_output": {
                "model_id": module.MODEL_ID,
                "decision_ts_utc": decision,
                "metadata": {
                    "weather_probability_stay": 0.7,
                    "book_association": {
                        "probability_status": "two_sided_midpoint",
                        "best_bid": 0.28,
                        "best_ask": 0.32,
                        "book_snapshot_id": event_id,
                    },
                },
            },
            "signal_candidate": {
                "side": "NO",
                "bracket": "31",
                "condition_id": "condition",
                "market_p": 0.30,
            },
            "state_checkpoint": {"city": "Tokyo", "target_date": "2026-08-02"},
        }

    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                bundle("exact", "2026-08-02T01:07:05Z"),
                bundle("delayed", "2026-08-02T02:07:00Z"),
            )
        )
        + "\n"
    )
    rows, counts = module.candidate_rows(
        path,
        start_date="2026-08-01",
        end_date="2026-08-11",
        maximum_event_to_book_seconds=30,
    )
    assert len(rows) == 1
    assert rows[0]["model_no_probability"] == pytest.approx(0.3)
    assert rows[0]["market_no_probability"] == 0.3
    assert counts["causal_event_book_rows"] == 2
    assert counts["event_book_lag_rows"] == 1


def test_load_settlements_supports_source_grain_without_condition_id(tmp_path) -> None:
    path = tmp_path / "weather.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT,
            target_date TEXT,
            bracket TEXT,
            condition_id TEXT,
            final_price REAL,
            settlement_status TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO settlement_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Tokyo", "2026-08-10", "30", "condition-30", 1.0, "settled"),
            ("Tokyo", "2026-08-10", "31", None, 0.0, "settled"),
        ],
    )
    connection.commit()
    connection.close()

    by_condition, by_source_key = module.load_settlements(path)

    assert by_condition == {"condition-30": 0}
    assert by_source_key[("Tokyo", "2026-08-10", "30")] == 0
    assert by_source_key[("Tokyo", "2026-08-10", "31")] == 1


def test_archive_development_combines_clocks_and_prefers_exact_duplicate(
    tmp_path, monkeypatch
) -> None:
    market_join = tmp_path / "market_join.csv"
    fields = [
        "target_date",
        "availability_clock_class",
        "current_bracket",
        "quotes_json",
        "market_distribution_json",
        "actual_delta",
        "availability_ts_utc",
        "snapshot_ts_utc",
        "decision_ts_utc",
        "state_id",
    ]
    base = {
        "target_date": "2026-07-23",
        "current_bracket": "31",
        "quotes_json": json.dumps({"31": {"bid": 0.7, "ask": 0.72}}),
        "market_distribution_json": json.dumps([0.71, 0.29]),
        "actual_delta": "1",
        "snapshot_ts_utc": "2026-07-23T01:00:20Z",
        "decision_ts_utc": "2026-07-23T01:00:00Z",
        "state_id": "Tokyo:2026-07-23:31",
    }
    with market_join.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                **base,
                "availability_clock_class": "archive_reconstructed_plus_15m",
                "availability_ts_utc": "2026-07-23T01:15:00Z",
            }
        )
        writer.writerow(
            {
                **base,
                "availability_clock_class": "collector_exact_hash_verified",
                "availability_ts_utc": "2026-07-23T01:00:10Z",
            }
        )
    spec = tmp_path / "weather.spec.json"
    spec.write_text(json.dumps({"features": ["jma_temp_c"]}))
    key = ("2026-07-23", "2026-07-23T01:00:00+00:00", 31)
    monkeypatch.setattr(
        module,
        "load_frozen_v5_no_probabilities",
        lambda **_kwargs: ({key: 0.8}, {key: {"jma_temp_c": "31.3"}}),
    )

    rows = module.load_archive_development(
        market_join,
        feature_rows=tmp_path / "unused.csv",
        weather_artifact=tmp_path / "unused.joblib",
        weather_spec=spec,
        clock_classes=(
            "archive_reconstructed_plus_15m",
            "collector_exact_hash_verified",
        ),
    )

    assert len(rows) == 1
    assert rows[0]["availability_clock_class"] == "collector_exact_hash_verified"
    assert rows[0]["event_source"] == "jma_amedas"
    assert rows[0]["weather_feature__jma_temp_c"] == 31.3


def _pre_cross_row(
    *,
    event_id: str,
    decision: str,
    jma_temp_c: float,
    won_no: int | None,
) -> dict:
    return {
        "target_date": "2026-08-12",
        "event_id": event_id,
        "event_decision_ts_utc": decision,
        "quote_ts_utc": decision,
        "bracket": 31,
        "market_no_probability": 0.80,
        "no_best_bid": 0.79,
        "no_best_ask": 0.81,
        "cash_cost_5": 4.10,
        "effective_cost_5": 0.82,
        "displayed_ask_depth_5": 1,
        "won_no": won_no,
        "evaluation_role": "strict_pit_forward",
        "weather_feature__jma_temp_c": jma_temp_c,
    }


def test_pre_cross_candidate_is_first_state_entry_not_first_positive_edge() -> None:
    frame = __import__("pandas").DataFrame(
        [
            _pre_cross_row(
                event_id="first",
                decision="2026-08-12T01:00:00Z",
                jma_temp_c=31.3,
                won_no=1,
            ),
            _pre_cross_row(
                event_id="later",
                decision="2026-08-12T01:10:00Z",
                jma_temp_c=31.4,
                won_no=1,
            ),
        ]
    )

    candidates, funnel = module.build_tokyo_pre_cross_candidates(frame)

    assert candidates["event_id"].tolist() == ["first"]
    assert candidates["pre_cross_margin_c"].tolist() == [0.3]
    assert funnel["pre_cross_mechanism_rows"] == 2
    assert funnel["first_date_bracket_candidates"] == 1


def test_frozen_pre_cross_forward_is_zero_notional_and_label_free() -> None:
    frame = [
        _pre_cross_row(
            event_id="unsettled",
            decision="2026-08-12T01:00:00Z",
            jma_temp_c=31.3,
            won_no=None,
        )
    ]
    spec = {
        "model_id": module.PRE_CROSS_MODEL_ID,
        "effective_from_target_date": "2026-08-12",
        "posterior": {"exponent": 2.0},
    }

    candidates, summary = module.score_tokyo_pre_cross_forward(
        frame, frozen_spec=spec
    )

    assert len(candidates) == 1
    assert candidates.iloc[0]["p_pre_cross_posterior"] > 0.9
    assert bool(candidates.iloc[0]["zero_notional_signal"])
    assert candidates.iloc[0]["signal_notional"] == 0.0
    assert summary["settlement_used_for_signal"] is False
    assert summary["zero_notional_signals"] == 1


def test_market_sharpening_never_flips_market_minor_side() -> None:
    probability = __import__("pandas").Series([0.2, 0.8])
    sharpened = module._sharpen_probability(probability, 2.0)

    assert sharpened[0] == pytest.approx(0.2)
    assert sharpened[1] == pytest.approx(0.9411764706)


def test_v2_forward_reserves_half_spread_without_price_band() -> None:
    wide = _pre_cross_row(
        event_id="wide",
        decision="2026-08-12T01:00:00Z",
        jma_temp_c=31.3,
        won_no=None,
    )
    wide.update(
        {
            "market_no_probability": 0.80,
            "no_best_bid": 0.70,
            "no_best_ask": 0.90,
            "cash_cost_5": 4.51,
            "effective_cost_5": 0.902,
        }
    )
    spec = {
        "model_id": module.PRE_CROSS_MODEL_ID,
        "effective_from_target_date": "2026-08-12",
        "posterior": {
            "exponent": 2.0,
            "weather_innovation_alpha": 0.0,
        },
        "execution_uncertainty_reserve": {
            "minimum_reserve": 0.001,
            "spread_multiplier": 0.5,
        },
    }

    candidates, summary = module.score_tokyo_pre_cross_forward(
        [wide], frozen_spec=spec
    )

    row = candidates.iloc[0]
    assert row["entry_edge"] > 0.0
    assert row["execution_uncertainty_reserve"] == pytest.approx(0.10)
    assert row["net_entry_edge"] < 0.0
    assert not bool(row["zero_notional_signal"])
    assert row["candidate_status"] == "edge_below_execution_uncertainty_reserve"
    assert summary["zero_notional_signals"] == 0


def test_v2_rejects_unfrozen_weather_innovation_in_forward() -> None:
    spec = {
        "model_id": module.PRE_CROSS_MODEL_ID,
        "effective_from_target_date": "2026-08-12",
        "posterior": {
            "exponent": 2.0,
            "weather_innovation_alpha": 0.25,
        },
    }

    with pytest.raises(ValueError, match="selected alpha must remain zero"):
        module.score_tokyo_pre_cross_forward(
            [
                _pre_cross_row(
                    event_id="unsettled",
                    decision="2026-08-12T01:00:00Z",
                    jma_temp_c=31.3,
                    won_no=None,
                )
            ],
            frozen_spec=spec,
        )
