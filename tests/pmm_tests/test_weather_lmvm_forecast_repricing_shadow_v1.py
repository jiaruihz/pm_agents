from __future__ import annotations

from argparse import Namespace
import gzip
import json
from pathlib import Path
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor

from scripts.ops import weather_lmvm_forecast_repricing_shadow_v1 as shadow
from weather_model_evaluation import forecast_repricing_quote_ev as quote_ev


def test_default_forward_input_is_current_strategy_snapshot_root() -> None:
    assert len(shadow.DEFAULT_SNAPSHOTS) == 1
    assert "strategy_snapshots/paper_snapshots" in str(shadow.DEFAULT_SNAPSHOTS[0])
    assert "full_ladder_output" not in str(shadow.DEFAULT_SNAPSHOTS[0])


def write_snapshot(
    root: Path,
    stamp: str,
    ts_utc: str,
    forecast_hash: str,
    model_probs: tuple[float, float, float],
    market_mids: tuple[float, float, float],
) -> Path:
    records = []
    for index, (model_probability, market_mid) in enumerate(zip(model_probs, market_mids)):
        records.append(
            {
                "city": "London",
                "target_date": "2026-08-05",
                "event_slug": "highest-temperature-in-london-on-august-5-2026",
                "timezone_name": "Europe/London",
                "bracket": str(20 + index),
                "question": f"London {20 + index}",
                "condition_id": f"condition-{index}",
                "market_id": "market-london",
                "yes_token_id": f"token-{index}",
                "forecast_source": "open_meteo_live_ecmwf",
                "forecast_model": "ecmwf",
                "model_version": "ecmwf-fixture-v1",
                "forecast_values_hash": forecast_hash,
                "model_init_utc_estimated": "2026-08-04T00:00:00Z",
                "model_prob": model_probability,
                "ladder_available_at_utc": ts_utc,
                "event_time_pit_scorable": True,
                "yes_best_bid": market_mid - 0.01,
                "yes_best_ask": market_mid + 0.01,
                "yes_bid_size": 10.0 + index,
                "yes_ask_size": 20.0 + index,
                "tick_size": 0.001,
            }
        )
    path = root / f"snapshot_{stamp}.json"
    path.write_text(json.dumps({"ts_utc": ts_utc, "records": records}))
    return path


def args(snapshot_dir: Path, output_dir: Path) -> Namespace:
    return Namespace(
        snapshot_dir=[snapshot_dir],
        output_dir=output_dir,
        state=output_dir / "state.json",
        bootstrap_lookback_files=64,
        follow_minutes=180.0,
        max_snapshot_age_seconds=10**9,
    )


def test_left_censored_baseline_then_delta_innovation_candidate(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    output = tmp_path / "output"
    write_snapshot(
        snapshots,
        "20260804_1000",
        "2026-08-04T09:00:00Z",
        "forecast-a",
        (0.2, 0.5, 0.3),
        (0.2, 0.5, 0.3),
    )
    state = shadow.load_state(output / "state.json")
    baseline = shadow.run_cycle(args(snapshots, output), state)
    assert baseline["phase"] == "left_censored_baseline"
    assert not (output / "decision_bundles.jsonl").exists()

    write_snapshot(
        snapshots,
        "20260804_1020",
        "2026-08-04T09:20:00Z",
        "forecast-b",
        (0.5, 0.3, 0.2),
        (0.25, 0.45, 0.3),
    )
    result = shadow.run_cycle(args(snapshots, output), state)
    assert result["cycle"]["forecast_updates"] == 1
    assert result["cycle"]["candidate_rows"] == 3

    bundles = [json.loads(line) for line in (output / "decision_bundles.jsonl").read_text().splitlines()]
    selected = [row["signal_candidate"] for row in bundles if row["signal_candidate"]["selected"]]
    assert len(selected) == 1
    assert selected[0]["condition_id"] == "condition-0"
    assert selected[0]["metadata"]["forecast_innovation_score"] > 0
    assert selected[0]["metadata"]["candidate_grain"] == "forecast_update_exact_bracket_v1"

    intent = json.loads((output / "trade_intents.jsonl").read_text().strip())
    assert intent["mode"] == "zero_notional"
    assert intent["requested_size"] == 0.0
    assert intent["metadata"]["no_order"] is True


def test_followup_quote_is_markout_not_maker_fill_and_dedupes(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    output = tmp_path / "output"
    first = write_snapshot(
        snapshots,
        "20260804_1000",
        "2026-08-04T09:00:00Z",
        "forecast-a",
        (0.2, 0.5, 0.3),
        (0.2, 0.5, 0.3),
    )
    state = shadow.load_state(output / "state.json")
    shadow.run_cycle(args(snapshots, output), state)
    write_snapshot(
        snapshots,
        "20260804_1020",
        "2026-08-04T09:20:00Z",
        "forecast-b",
        (0.5, 0.3, 0.2),
        (0.25, 0.45, 0.3),
    )
    shadow.run_cycle(args(snapshots, output), state)
    write_snapshot(
        snapshots,
        "20260804_1120",
        "2026-08-04T10:20:00Z",
        "forecast-b",
        (0.5, 0.3, 0.2),
        (0.35, 0.40, 0.25),
    )
    result = shadow.run_cycle(args(snapshots, output), state)
    assert result["cycle"]["markouts_written"] == 1
    row = json.loads((output / "quote_markouts.jsonl").read_text().strip())
    assert row["elapsed_minutes"] == 60.0
    assert row["maker_fill_status"] == "not_inferred_from_quote_cross"
    assert row["trade_print_coverage"] == "absent"
    assert row["no_order_placed"] is True

    before = (output / "quote_markouts.jsonl").read_text()
    repeated = shadow.run_cycle(args(snapshots, output), state)
    assert repeated["cycle"]["new_snapshot_files"] == 0
    assert (output / "quote_markouts.jsonl").read_text() == before
    assert str(first) in state["seen_files"]


def test_maker_quote_records_visible_queue_without_claiming_fill() -> None:
    quote = shadow.maker_quote(
        {
            "yes_bid": 0.20,
            "yes_ask": 0.201,
            "yes_bid_size": 14.0,
            "tick_size": 0.001,
        }
    )
    assert quote["maker_limit_price"] == 0.20
    assert quote["visible_queue_ahead_shares"] == 14.0
    assert quote["maker_fill_status"] == "not_observable_without_order_or_trade_prints"


def test_maker_quote_joins_best_bid_even_when_spread_is_wide() -> None:
    quote = shadow.maker_quote(
        {
            "yes_bid": 0.02,
            "yes_ask": 0.08,
            "yes_bid_size": 25.0,
            "tick_size": 0.001,
        }
    )
    assert quote["maker_limit_price"] == 0.02
    assert quote["visible_queue_ahead_shares"] == 25.0


def test_completion_policy_builds_zero_notional_maker_trigger() -> None:
    def state(key: str, epoch: float, model: tuple[float, float, float]) -> dict:
        return {
            "snapshot_id": f"snapshot-{key}",
            "snapshot_ts_utc": "2026-08-10T10:00:00Z",
            "decision_ts_utc": "2026-08-10T10:00:00Z",
            "decision_epoch": epoch,
            "clock_lineage_status": "collector_exact_joined_full_ladder_v1",
            "source_path": f"/fixture/{key}.json",
            "city": "London",
            "target_date": "2026-08-11",
            "event_slug": "london-aug-11",
            "market_timezone": "Europe/London",
            "forecast_source": "fixture",
            "forecast_model": "fixture",
            "model_version": "fixture-v1",
            "forecast_state_key": key,
            "lead_days": 1,
            "rung_count": 3,
            "rungs": [
                {
                    "condition_id": f"c{index}",
                    "bracket": str(20 + index),
                    "question": str(20 + index),
                    "yes_token_id": f"token-{index}",
                    "model_prob": model[index],
                    "market_prob": (0.2, 0.4, 0.4)[index],
                    "yes_bid": (0.10, 0.28, 0.28)[index],
                    "yes_ask": (0.30, 0.30, 0.30)[index],
                    "yes_bid_size": 10.0,
                    "yes_ask_size": 10.0,
                    "tick_size": 0.001,
                }
                for index in range(3)
            ],
        }

    bundle = {
        "schema_version": "forecast_repricing_position_policy_v2",
        "model_id": "forecast_repricing_full_ladder_position_v1",
        "primary_policy": "full_ladder_completion_v1",
        "maker_quote_ttl_min": 60,
        "completion_policy": {
            "buffer_per_set": 0.01,
            "requested_shares": 5.0,
            "hedge_slippage_per_leg": 0.001,
        },
    }
    built = shadow.build_update(
        state("before", 1_000.0, (0.2, 0.5, 0.3)),
        state("after", 1_600.0, (0.4, 0.4, 0.2)),
        bundle,
    )
    assert built is not None
    update, bundles, intent, open_candidate = built
    assert update["decision"] == "POST_MAKER"
    assert len(bundles) == 3
    assert intent is not None
    assert intent["requested_size"] == 0.0
    assert intent["metadata"]["completion_hedge_after_actual_fill"] is True
    assert open_candidate is not None
    assert open_candidate["maker_limit_price"] == 0.10


def test_quote_ev_policy_runs_in_zero_notional_runner() -> None:
    def model(width: int, value: float) -> DummyRegressor:
        return DummyRegressor(strategy="constant", constant=value).fit(
            pd.DataFrame([[0.0] * width]), [value]
        )

    touch_features = pd.DataFrame(
        [[0.0] * len(quote_ev.QUOTE_FEATURES), [1.0] * len(quote_ev.QUOTE_FEATURES)]
    )
    bundle = {
        "schema_version": quote_ev.SCHEMA_VERSION,
        "model_id": quote_ev.MODEL_ID,
        "quote_actions": list(quote_ev.QUOTE_ACTIONS),
        "quote_features": list(quote_ev.QUOTE_FEATURES),
        "signal_ttl_min": 30,
        "direct_model": model(len(quote_ev.QUOTE_FEATURES), 0.01),
        "market_direct_model": model(len(quote_ev.MARKET_QUOTE_FEATURES), 0.0),
        "market_static_direct_model": model(
            len(quote_ev.MARKET_STATIC_QUOTE_FEATURES), 0.0
        ),
        "touch_model": DummyClassifier(strategy="constant", constant=1).fit(
            touch_features, [0, 1]
        ),
        "value_model": model(len(quote_ev.QUOTE_FEATURES), 0.02),
    }

    def state(key: str, epoch: float, model_probs: tuple[float, float, float]) -> dict:
        rungs = []
        for index in range(3):
            rungs.append(
                {
                    "condition_id": f"c{index}",
                    "bracket": str(20 + index),
                    "question": str(20 + index),
                    "yes_token_id": f"token-{index}",
                    "model_prob": model_probs[index],
                    "market_prob": (0.2, 0.4, 0.4)[index],
                    "yes_bid": (0.10, 0.28, 0.28)[index],
                    "yes_ask": (0.15, 0.32, 0.32)[index],
                    "yes_bid_size": 10.0,
                    "yes_ask_size": 10.0,
                    "tick_size": 0.01,
                }
            )
        return {
            "snapshot_id": f"snapshot-{key}",
            "snapshot_ts_utc": "2026-08-10T10:00:00Z",
            "decision_ts_utc": "2026-08-10T10:00:00Z",
            "decision_epoch": epoch,
            "clock_lineage_status": "collector_exact_joined_full_ladder_v1",
            "source_path": f"/fixture/{key}.json",
            "city": "London",
            "target_date": "2026-08-11",
            "event_slug": "london-aug-11",
            "market_timezone": "Europe/London",
            "forecast_source": "fixture",
            "forecast_model": "fixture",
            "model_version": "fixture-v1",
            "forecast_state_key": key,
            "lead_days": 1,
            "rung_count": 3,
            "rungs": rungs,
        }

    built = shadow.build_update(
        state("before", 1_000.0, (0.2, 0.5, 0.3)),
        state("after", 1_600.0, (0.4, 0.4, 0.2)),
        bundle,
    )
    assert built is not None
    update, bundles, intent, open_candidate = built
    assert update["decision"] == "POST_MAKER"
    assert update["policy_id"] == shadow.QUOTE_EV_POLICY_ID
    assert len(bundles) == 3
    assert intent is not None and intent["requested_size"] == 0.0
    assert intent["execution_profile"] == "quote_ev_post_only_v1"
    assert open_candidate is not None
    assert open_candidate["quote_ev_policy_enabled"] is True
    assert open_candidate["maker_limit_price"] < open_candidate["entry_ask"]
    selected_bundle = next(
        row for row in bundles if row["signal_candidate"]["selected"]
    )
    assert (
        selected_bundle["signal_candidate"]["metadata"]["maker_limit_price"]
        == open_candidate["maker_limit_price"]
    )


def test_current_snapshot_adapter_joins_canonical_full_ladder(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    day = "2026-08-10"
    books_dir = runtime / "market_books" / "batches" / day
    ladder_dir = runtime / "market_ladder_snapshots" / day
    snapshots = runtime / "strategy_snapshots" / "paper_snapshots"
    books_dir.mkdir(parents=True)
    ladder_dir.mkdir(parents=True)
    snapshots.mkdir(parents=True)
    monkeypatch.setattr(shadow, "DEFAULT_RUNTIME", runtime)
    stamp = "20260810_020000"
    book_path = books_dir / f"market_books_{stamp}.jsonl.gz"
    clock = {
        "status": "ok",
        "event_time_pit_scorable": True,
        "request_started_at_utc": "2026-08-09T18:00:00Z",
        "response_received_at_utc": "2026-08-09T18:00:01Z",
        "parsed_at_utc": "2026-08-09T18:00:02Z",
    }
    books = []
    for index in range(2):
        books.extend(
            [
                {
                    **clock,
                    "book_capture_id": f"yes-{index}",
                    "summary": {
                        "best_bid": 0.2 + index * 0.4,
                        "best_ask": 0.22 + index * 0.4,
                        "bid_size": 10.0,
                        "ask_size": 11.0,
                    },
                },
                {
                    **clock,
                    "book_capture_id": f"no-{index}",
                    "summary": {
                        "best_bid": 0.77 - index * 0.4,
                        "best_ask": 0.79 - index * 0.4,
                        "bid_size": 12.0,
                        "ask_size": 13.0,
                    },
                },
            ]
        )
    with gzip.open(book_path, "wt", encoding="utf-8") as handle:
        for row in books:
            handle.write(json.dumps(row) + "\n")
    ladder = {
        "available_at_utc": "2026-08-09T18:00:03Z",
        "batch_capture_id": "market-batch",
        "records": [
            {
                "city": "London",
                "target_date": "2026-08-10",
                "event_slug": "london-aug-10",
                "rung_count": 2,
                "rungs": [
                    {
                        "condition_id": f"condition-{index}",
                        "bracket": str(20 + index),
                        "market_id": f"market-{index}",
                        "yes_token_id": f"token-{index}",
                        "yes_book_capture_id": f"yes-{index}",
                        "no_book_capture_id": f"no-{index}",
                    }
                    for index in range(2)
                ],
            }
        ],
    }
    (ladder_dir / f"market_ladder_snapshot_{stamp}.json").write_text(
        json.dumps(ladder), encoding="utf-8"
    )
    strategy = {
        "ts_utc": "2026-08-09T18:00:00Z",
        "available_at_utc": "2026-08-09T18:00:04Z",
        "snapshot_capture_id": "strategy-capture",
        "producer_build_id": "build-1",
        "canonical_orderbook_source": {"archive_path": str(book_path)},
        "records": [
            {
                "condition_id": f"condition-{index}",
                "city": "London",
                "target_date": "2026-08-10",
                "city_local_date_at_snapshot": "2026-08-09",
                "event_slug": "london-aug-10",
                "bracket": str(20 + index),
                "question": f"London {20 + index}",
                "model_prob": (0.35, 0.65)[index],
                "forecast_values_hash": "forecast-a",
                "forecast_source": "open_meteo_live_ecmwf",
                "forecast_model": "ecmwf",
                "forecast_max_f": 69.0,
                "timezone_name": "Europe/London",
            }
            for index in range(2)
        ],
    }
    snapshot_path = snapshots / "snapshot_20260810_0200.json"
    snapshot_path.write_text(json.dumps(strategy), encoding="utf-8")
    rows, coverage = shadow.parse_clock_exact_snapshot_file(snapshot_path)
    assert coverage["current_joined_ladders"] == 1
    assert coverage["current_joined_rungs"] == 2
    assert rows[0]["clock_lineage_status"] == "collector_exact_joined_full_ladder_v1"
    assert rows[0]["lead_days"] == 1
    assert rows[0]["rungs"][0]["yes_bid"] == pytest.approx(0.21)
    assert rows[0]["rungs"][0]["yes_ask"] == pytest.approx(0.22)
