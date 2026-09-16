from __future__ import annotations

import json
import numpy as np
import pandas as pd
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation.market_prior_posterior import (
    prepare_expression_grain,
    replay_fmi_entry_metar_correction,
    run_market_prior_posterior_research,
    select_city_rows,
)
from weather_model_evaluation.ladder_microstructure import (
    LADDER_FEATURES,
    add_ladder_microstructure_features,
)
from weather_model_evaluation.cli import main as evaluation_cli_main
from weather_model_evaluation.tokyo_market_prior_adapter import (
    _exact_source_events,
    _load_current_bracket_books,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_tokyo_exact_census_uses_target_date_across_utc_shards(tmp_path) -> None:
    root = tmp_path / "live_cross"
    legacy = {
        "city": "Tokyo",
        "target_date": "2026-08-01",
        "source": "jma_amedas",
        "source_status": "ok",
        "schema_version": "weather_high_frequency_observation_v1",
        "observation_time_utc": "2026-07-31T21:00:00Z",
        "source_first_seen_at_utc": "2026-07-31T21:07:00Z",
        "fetched_at_utc": "2026-07-31T21:07:00Z",
        "local_detect_ts_utc": "2026-07-31T21:07:00Z",
        "payload_hash": "payload",
        "raw_payload_hash": "raw",
    }
    explicit = {
        **legacy,
        "observation_time_utc": "2026-08-01T01:00:00Z",
        "source_first_seen_at_utc": "2026-08-01T01:07:00Z",
        "fetched_at_utc": "2026-08-01T01:07:00Z",
        "local_detect_ts_utc": "2026-08-01T01:07:00Z",
        "pit_lineage_class": "collector_exact",
        "information_event_id": "explicit-event",
    }
    _write_jsonl(
        root / "2026-07-31/high_frequency_observations.jsonl", [legacy]
    )
    _write_jsonl(
        root / "2026-08-01/high_frequency_observations.jsonl", [explicit]
    )
    events, histories, coverage = _exact_source_events(
        root, start_date="2026-08-01", end_date="2026-08-01"
    )
    assert len(histories["2026-08-01"]) == 2
    assert len(events["2026-08-01"]) == 2
    assert coverage["2026-08-01"]["legacy_hash_verified_exact_observations"] == 1
    assert coverage["2026-08-01"]["explicit_collector_exact_observations"] == 1


def test_tokyo_current_bracket_join_uses_reference_anchor(tmp_path) -> None:
    books = tmp_path / "books"
    base = {
        "city": "Tokyo",
        "target_date": "2026-08-01",
        "source": "jma_amedas",
        "outcome": "no",
        "book_status": "ok",
        "source_obs_ts_utc": "2026-08-01T01:00:00Z",
        "book_fetched_at_utc": "2026-08-01T01:07:01Z",
        "reference_market_value": 29,
        "metar_running_max_market_value": 28,
    }
    _write_jsonl(
        books / "2026-08-01.jsonl",
        [{**base, "bracket": "29"}, {**base, "bracket": "28"}],
    )
    indexed, raw_counts = _load_current_bracket_books(books, ["2026-08-01"])
    assert raw_counts["2026-08-01"] == 2
    assert len(indexed) == 1
    assert next(iter(indexed.values()))[0]["bracket"] == "29"


def _fixture() -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(7)
    for day in range(1, 7):
        target_date = f"2026-08-{day:02d}"
        for event in range(12):
            for rung in range(4):
                won_no = int(rung != (day + event) % 4)
                market = np.clip(0.78 if won_no else 0.22, 0.02, 0.98)
                market = float(np.clip(market + rng.normal(0, 0.08), 0.02, 0.98))
                model = float(np.clip(market + rng.normal(0, 0.12), 0.01, 0.99))
                quote = pd.Timestamp(target_date, tz="UTC") + pd.Timedelta(
                    hours=7, minutes=event * 10
                )
                rows.append(
                    {
                        "target_date": target_date,
                        "event_id": f"{target_date}-{event}",
                        "event_source": "metar" if event % 3 == 0 else "fmi",
                        "event_decision_ts_utc": quote - pd.Timedelta(minutes=2),
                        "quote_ts_utc": quote,
                        "event_age_min": 2.0,
                        "bracket": str(18 + rung),
                        "relative_rung": rung - 1,
                        "won_no": won_no,
                        "model_no_probability": model,
                        "market_no_probability": market,
                        "no_best_bid": market - 0.01,
                        "no_best_ask": market + 0.01,
                        "cash_cost_5": 5 * (market + 0.02),
                        "effective_cost_5": market + 0.02,
                    }
                )
    return pd.DataFrame(rows)


def test_prepare_expression_grain_deduplicates_event_bracket() -> None:
    frame = _fixture()
    duplicate = frame.iloc[[0]].copy()
    duplicate["quote_ts_utc"] = pd.to_datetime(duplicate["quote_ts_utc"], utc=True) + pd.Timedelta(minutes=1)
    prepared, denominator = prepare_expression_grain(
        pd.concat([frame, duplicate], ignore_index=True), timezone="Europe/Helsinki"
    )
    assert len(prepared) == len(frame)
    assert denominator["input_expression_rows"] == len(frame) + 1
    assert denominator["event_bracket_rows"] == len(frame)


def test_city_runner_cannot_relabel_a_multi_city_input() -> None:
    frame = pd.DataFrame({"city": ["Helsinki", "Tokyo"], "value": [1, 2]})
    selected = select_city_rows(frame, "helsinki")
    assert selected["value"].tolist() == [1]
    with __import__("pytest").raises(ValueError, match="available cities"):
        select_city_rows(frame, "Amsterdam")


def test_shared_cli_writes_city_scoped_strict_json(tmp_path) -> None:
    frame = _fixture()
    frame["city"] = "Helsinki"
    foreign = frame.iloc[[0]].copy()
    foreign["city"] = "Tokyo"
    input_path = tmp_path / "expressions.csv"
    pd.concat([frame, foreign], ignore_index=True).to_csv(input_path, index=False)
    output_dir = tmp_path / "output"
    assert (
        evaluation_cli_main(
            [
                "market-prior",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--city",
                "Helsinki",
                "--timezone",
                "Europe/Helsinki",
                "--min-train-dates",
                "3",
                "--bootstrap-draws",
                "20",
            ]
        )
        == 0
    )
    raw_summary = (output_dir / "summary.json").read_text(encoding="utf-8")
    assert "NaN" not in raw_summary
    summary = json.loads(raw_summary)
    assert summary["input_scope"]["raw_rows"] == len(frame) + 1
    assert summary["input_scope"]["city_rows"] == len(frame)
    assert summary["input_scope"]["city_filter"] == "Helsinki"
    assert len(summary["input_scope"]["input_sha256"]) == 64
    assert summary["denominator"]["candidate_grain_version"] == (
        "research_first_event_bracket_v1"
    )
    assert summary["producer"]["entrypoint"].endswith(":market-prior")
    assert len(summary["producer"]["build_id"]) == 64


def test_shared_cli_requires_explicit_assertion_for_legacy_cityless_input(
    tmp_path,
) -> None:
    input_path = tmp_path / "helsinki_legacy.csv"
    _fixture().to_csv(input_path, index=False)
    output_dir = tmp_path / "output"
    with __import__("pytest").raises(ValueError, match="input-city-assertion"):
        evaluation_cli_main(
            [
                "market-prior",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--city",
                "Helsinki",
                "--timezone",
                "Europe/Helsinki",
            ]
        )
    assert (
        evaluation_cli_main(
            [
                "market-prior",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--city",
                "Helsinki",
                "--input-city-assertion",
                "Helsinki",
                "--timezone",
                "Europe/Helsinki",
                "--min-train-dates",
                "3",
                "--bootstrap-draws",
                "20",
            ]
        )
        == 0
    )
    summary = json.loads((output_dir / "summary.json").read_text())
    assert (
        summary["input_scope"]["city_scope_origin"]
        == "explicit_legacy_cityless_input_assertion"
    )


def test_prepare_expression_grain_accepts_mixed_iso_precision() -> None:
    frame = _fixture().iloc[:2].copy()
    frame.loc[frame.index[0], "event_decision_ts_utc"] = "2026-08-01T07:01:02+00:00"
    frame.loc[frame.index[1], "event_decision_ts_utc"] = "2026-08-01T07:01:02.123456+00:00"
    frame.loc[frame.index[0], "quote_ts_utc"] = "2026-08-01T07:02:03.829+00:00"
    frame.loc[frame.index[1], "quote_ts_utc"] = "2026-08-01T07:02:03+00:00"
    prepared, _ = prepare_expression_grain(frame, timezone="Europe/Helsinki")
    assert len(prepared) == 2


def test_prepare_expression_grain_exposes_noncausal_and_invalid_book_gaps() -> None:
    frame = _fixture().iloc[:3].copy()
    frame.loc[frame.index[0], "quote_ts_utc"] = (
        pd.to_datetime(frame.loc[frame.index[0], "event_decision_ts_utc"], utc=True)
        - pd.Timedelta(seconds=1)
    )
    frame.loc[frame.index[1], "no_best_bid"] = 0.80
    frame.loc[frame.index[1], "no_best_ask"] = 0.70
    prepared, denominator = prepare_expression_grain(
        frame, timezone="Europe/Helsinki"
    )
    assert len(prepared) == 1
    assert denominator["input_expression_rows"] == 3
    assert denominator["noncausal_quote_rows"] == 1
    assert denominator["two_sided_scored_rows"] == 1


def test_market_prior_research_is_blocked_by_target_date() -> None:
    result = run_market_prior_posterior_research(
        _fixture(), timezone="Europe/Helsinki", min_train_dates=3, bootstrap_draws=100
    )
    assert result.folds["test_date"].tolist() == [
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
    ]
    assert (result.folds["train_end"] < result.folds["test_date"]).all()
    assert set(result.scores["model"]) == {
        "raw_market",
        "raw_a8",
        "compact_logistic_market_offset",
        "strong_shrinkage_logit_blend",
        "compact_logistic_model_only",
        "compact_logistic_market_prior",
        "shallow_hgb_market_prior",
    }
    assert result.folds["strong_shrinkage_weather_weight"].between(0.0, 0.5).all()
    assert set(result.trade_summary["model"]) == set(result.scores["model"])
    assert "valid causal event/book clocks" in result.denominator["eligibility"]


def test_frozen_forward_keeps_the_same_training_dates() -> None:
    result = run_market_prior_posterior_research(
        _fixture(),
        timezone="Europe/Helsinki",
        min_train_dates=3,
        bootstrap_draws=20,
        freeze_after_min_train_dates=True,
    )
    assert result.folds["train_dates"].tolist() == [3, 3, 3]
    assert result.folds["train_end"].nunique() == 1
    assert result.denominator["freeze_after_min_train_dates"] is True


def test_ladder_features_keep_denominator_and_use_only_prior_checkpoint() -> None:
    frame = _fixture()
    frame["current_x"] = frame.groupby(["target_date", "event_id"]).ngroup() % 4 + 18
    prepared, _ = prepare_expression_grain(frame, timezone="Europe/Helsinki")
    featured, coverage = add_ladder_microstructure_features(prepared)
    assert len(featured) == len(prepared)
    assert set(LADDER_FEATURES).issubset(featured.columns)
    assert coverage["events_with_prior_checkpoint"] > 0
    first_event = featured.sort_values("event_decision_ts_utc")["event_id"].iloc[0]
    assert featured.loc[
        featured["event_id"].eq(first_event), "rung_relative_markout"
    ].isna().all()


def test_ladder_ablation_uses_same_oof_rows() -> None:
    frame = _fixture()
    event_number = frame["event_id"].str.rsplit("-", n=1).str[-1].astype(int)
    frame["current_x"] = 18 + (event_number // 3)
    result = run_market_prior_posterior_research(
        frame,
        timezone="Europe/Helsinki",
        min_train_dates=3,
        bootstrap_draws=20,
        include_ladder_features=True,
    )
    assert result.denominator["include_ladder_features"] is True
    assert result.denominator["oof_test_rows"] == 3 * 12 * 4
    assert {
        "compact_logistic_market_offset_ladder",
        "compact_logistic_market_prior_ladder",
        "shallow_hgb_market_prior_ladder",
    }.issubset(set(result.scores["model"]))
    paired = result.bootstrap.loc[
        result.bootstrap["baseline_model"].eq("compact_logistic_market_offset")
    ]
    assert set(paired["model"]) == {"compact_logistic_market_offset_ladder"}
    assert set(paired["metric"]) == {"brier", "logloss"}


def test_fmi_entry_metar_correction_keeps_source_roles_separate() -> None:
    frame = _fixture().iloc[:3].copy()
    frame["target_date"] = "2026-08-01"
    frame["bracket"] = ["20", "21", "20"]
    frame["event_id"] = ["fmi-entry", "metar-would-enter", "metar-exit"]
    frame["event_source"] = ["fmi", "metar", "metar"]
    frame["event_decision_ts_utc"] = pd.to_datetime(
        ["2026-08-01T08:00:00Z", "2026-08-01T08:05:00Z", "2026-08-01T08:10:00Z"]
    )
    frame["quote_ts_utc"] = pd.to_datetime(
        ["2026-08-01T08:00:30Z", "2026-08-01T08:05:30Z", "2026-08-01T08:10:30Z"]
    )
    frame["model_no_probability"] = [0.80, 0.99, 0.20]
    frame["effective_cost_5"] = [0.60, 0.60, 0.60]
    frame["cash_cost_5"] = [3.0, 3.0, 3.0]
    frame["no_best_bid"] = [0.58, 0.58, 0.70]
    frame["won_no"] = [0, 1, 0]
    replay, summary = replay_fmi_entry_metar_correction(frame, bootstrap_draws=100)
    assert replay["event_id"].tolist() == ["fmi-entry"]
    assert replay.iloc[0]["exit_event_id"] == "metar-exit"
    assert summary["signal_funnel"]["first_date_bracket_entries"] == 1
    assert summary["evidence_funnel"]["metar_exit_triggers"] == 1
    assert summary["evidence_funnel"]["exit_quote_within_30s"] == 1
    assert "blocked_metar_t0_quote" in summary["metar_exit_diagnostic"]["qualification"]
    filtered, filtered_summary = replay_fmi_entry_metar_correction(
        frame,
        bootstrap_draws=100,
        research_entry_cost_min_exclusive=0.7,
        research_entry_cost_max_exclusive=0.9,
    )
    assert filtered.empty
    assert filtered_summary["entry_policy"].startswith("FMI-only")


def test_fmi_entry_metar_correction_handles_no_exit_trigger() -> None:
    frame = _fixture().iloc[:2].copy()
    frame["target_date"] = "2026-08-01"
    frame["bracket"] = "20"
    frame["event_id"] = ["fmi-entry", "metar-hold"]
    frame["event_source"] = ["fmi", "metar"]
    frame["event_decision_ts_utc"] = pd.to_datetime(
        ["2026-08-01T08:00:00Z", "2026-08-01T08:10:00Z"]
    )
    frame["quote_ts_utc"] = pd.to_datetime(
        ["2026-08-01T08:00:30Z", "2026-08-01T08:10:30Z"]
    )
    frame["model_no_probability"] = [0.80, 0.90]
    frame["effective_cost_5"] = [0.60, 0.60]
    frame["cash_cost_5"] = [3.0, 3.0]
    frame["no_best_bid"] = [0.58, 0.50]
    replay, summary = replay_fmi_entry_metar_correction(
        frame, bootstrap_draws=100
    )
    assert replay["exit_triggered"].tolist() == [False]
    assert summary["evidence_funnel"]["metar_exit_triggers"] == 0
    assert np.isnan(summary["evidence_funnel"]["exit_quote_lag_p90_s"])
