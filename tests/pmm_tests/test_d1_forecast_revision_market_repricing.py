from __future__ import annotations

import gzip
import json
import csv

from weather_model_evaluation import d1_revision_repricing as subject


def test_markout_horizons_include_thin_book_short_windows() -> None:
    assert subject.MARKOUT_MINUTES == (5, 10, 30, 60, 90)


def test_lineage_impact_separates_backward_and_repeated_forward_rows() -> None:
    rows = [
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-05T00:00:00Z",
            "forecast_run_at_utc": "2026-08-04T18:00:00Z",
            "available_at_utc": "2026-08-05T01:00:00Z",
        },
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-04T18:00:00Z",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T01:01:00Z",
        },
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-04T18:00:00Z",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T01:31:00Z",
        },
    ]
    result = subject.lineage_impact(rows)
    assert result["backward_previous_run_rows"] == 1
    assert result["forward_previous_run_rows"] == 2
    assert result["unique_forward_transition_keys"] == 1
    assert result["repeated_forward_transition_rows"] == 1


def test_probability_markout_requires_identical_complete_ladders() -> None:
    before = {
        "market_distribution_complete": True,
        "probabilities": {"30 or below": 0.2, "31+": 0.8},
    }
    after = {
        "market_distribution_complete": True,
        "probabilities": {"30 or below": 0.4, "31+": 0.6},
    }
    result = subject._probability_markout(before, after)
    assert result["status"] == "scoreable"
    assert abs(result["total_variation"] - 0.2) < 1e-12
    assert abs(result["mean_rung_shift"] + 0.2) < 1e-12


def test_d1_checkpoint_policy_uses_city_local_target_midnight() -> None:
    policy, hours = subject.d1_checkpoint_policy(
        "2026-08-06", "2026-08-05T12:00:00Z", "Asia/Tokyo"
    )
    assert policy == "D-1_18_24"
    assert hours == -3.0


def test_directional_repricing_flips_downward_revision_sign() -> None:
    events = [
        {
            "target_date": "2026-08-06",
            "event_class": "forward_provider_run_first_seen",
            "checkpoint_policy": "D-1_18_24",
            "model_revision_f": 1.0,
            "consensus_median_revision_f": 1.0,
            "assigned_model_revision_f": 1.0,
            "immediate_market_status": "scoreable",
            "immediate_mean_rung_shift": 0.20,
            **{
                f"markout_{minutes}m_status": "scoreable"
                for minutes in subject.MARKOUT_MINUTES
            },
            **{
                f"markout_{minutes}m_mean_rung_shift": 0.10
                for minutes in subject.MARKOUT_MINUTES
            },
        },
        {
            "target_date": "2026-08-07",
            "event_class": "forward_provider_run_first_seen",
            "checkpoint_policy": "D-1_18_24",
            "model_revision_f": -2.0,
            "consensus_median_revision_f": -2.0,
            "assigned_model_revision_f": -2.0,
            "immediate_market_status": "scoreable",
            "immediate_mean_rung_shift": -0.30,
            **{
                f"markout_{minutes}m_status": "scoreable"
                for minutes in subject.MARKOUT_MINUTES
            },
            **{
                f"markout_{minutes}m_mean_rung_shift": -0.20
                for minutes in subject.MARKOUT_MINUTES
            },
        },
    ]

    rows = subject.directional_repricing_summary(events)
    immediate = next(
        row
        for row in rows
        if row["scope"] == "forward_provider_run_first_seen"
        and row["revision_field"] == "consensus_median_revision_f"
        and row["horizon"] == "immediate"
    )
    assert immediate["events"] == 2
    assert immediate["direction_agreement_rate"] == 1.0
    assert abs(immediate["mean_directional_rung_shift"] - 0.25) < 1e-12


def test_market_transition_summary_scores_shared_book_move_once() -> None:
    common = {
        "target_date": "2026-08-07",
        "city": "Tokyo",
        "event_class": "forward_provider_run_first_seen",
        "pre_book_snapshot_id": "pre",
        "post_book_snapshot_id": "post",
        "immediate_market_status": "scoreable",
        "immediate_mean_rung_shift": 0.2,
    }
    events = [
        {
            **common,
            "model_revision_f": 1.0,
            "consensus_mean_revision_f": 0.2,
        },
        {
            **common,
            "model_revision_f": -0.5,
            "consensus_mean_revision_f": -0.1,
        },
    ]
    rows = subject.market_transition_repricing_summary(events)
    immediate = next(
        row
        for row in rows
        if row["scope"] == "forward_collector_exact"
        and row["horizon"] == "immediate"
    )
    assert immediate["event_rows"] == 2
    assert immediate["independent_market_transitions"] == 1
    assert immediate["single_forecast_event_transitions"] == 0
    assert immediate["conflicting_provider_direction_transitions"] == 1
    assert immediate["direction_agreement_rate"] == 1.0


def test_provider_run_events_use_asof_model_arrivals_not_complete_batches() -> None:
    models = [
        ("gfs_global", 80.0),
        ("ecmwf_ifs025", 81.0),
        ("icon_seamless", 82.0),
        ("gem_global", 83.0),
        ("jma_gsm", 84.0),
    ]
    rows = []
    for index, (model, value) in enumerate(models):
        rows.append(
            {
                "schema_version": "weather_forecast_run_row_v2",
                "model_key": model,
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T00:00:00Z",
                "forecast_max_f": value,
                "first_seen_at_utc": f"2026-08-06T00:0{index}:00Z",
                "assigned_model": model == "gfs_global",
            }
        )
    rows.extend(
        [
            {
                "schema_version": "weather_forecast_run_row_v2",
                "model_key": "icon_seamless",
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T06:00:00Z",
                "forecast_max_f": 85.0,
                "first_seen_at_utc": "2026-08-06T01:00:00Z",
                "assigned_model": False,
            },
            {
                "schema_version": "weather_forecast_run_row_v3",
                "model_key": "gfs_global",
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T06:00:00Z",
                "forecast_max_f": 82.0,
                "run_first_seen_at_utc": "2026-08-06T01:10:00Z",
                "run_first_seen_status": "collector_response_complete",
                "assigned_model": True,
            },
        ]
    )
    events, summary = subject.build_provider_run_events(rows)
    assert summary["provider_run_transition_events"] == 2
    assert events[0]["event_class"] == "legacy_provider_run_earliest_observed"
    assert events[0]["model_key"] == "icon_seamless"
    assert events[0]["model_value_before_f"] == 82.0
    assert events[0]["model_value_after_f"] == 85.0
    assert events[0]["consensus_median_before_f"] == 82.0
    assert events[0]["consensus_median_after_f"] == 83.0
    assert events[0]["consensus_iqr_before_f"] == 2.0
    assert events[0]["consensus_iqr_after_f"] == 3.0
    assert events[0]["consensus_median_revision_f"] == 1.0
    assert events[1]["event_class"] == "forward_provider_run_first_seen"
    assert events[1]["assigned_model_revision_f"] == 2.0


def test_markout_does_not_compare_late_post_checkpoint_to_itself() -> None:
    event = {
        "city": "Tokyo",
        "target_date": "2026-08-07",
        "event_available_at_utc": "2026-08-06T00:00:00Z",
    }
    common = {
        "city": "Tokyo",
        "target_date": "2026-08-07",
        "market_distribution_complete": True,
        "probabilities": {"30": 0.4, "31+": 0.6},
    }
    checkpoints = [
        {
            **common,
            "checkpoint_ts_utc": "2026-08-05T23:59:00Z",
            "available_at_utc": "2026-08-05T23:59:30Z",
            "feature_book_snapshot_id": "pre",
        },
        {
            **common,
            "checkpoint_ts_utc": "2026-08-06T00:12:00Z",
            "available_at_utc": "2026-08-06T00:12:30Z",
            "feature_book_snapshot_id": "post",
        },
        {
            **common,
            "probabilities": {"30": 0.3, "31+": 0.7},
            "checkpoint_ts_utc": "2026-08-06T00:31:00Z",
            "available_at_utc": "2026-08-06T00:31:30Z",
            "feature_book_snapshot_id": "later",
        },
    ]
    result = subject.attach_market_evidence([event], checkpoints)[0]
    assert result["markout_5m_status"] == "post_checkpoint_after_markout_horizon"
    assert result["markout_10m_status"] == "post_checkpoint_after_markout_horizon"
    assert result["markout_30m_status"] == "scoreable"
    assert abs(result["markout_30m_mean_rung_shift"] - 0.1) < 1e-12


def test_canonical_market_books_join_materializes_exact_checkpoint(tmp_path) -> None:
    books_root = tmp_path / "market_books"
    ladder_root = tmp_path / "market_ladder_snapshots"
    day = "2026-08-08"
    (books_root / "batches" / day).mkdir(parents=True)
    (ladder_root / day).mkdir(parents=True)
    stamp = "20260808_120000"
    book_path = books_root / "batches" / day / f"market_books_{stamp}.jsonl.gz"
    ladder_path = ladder_root / day / f"market_ladder_snapshot_{stamp}.json"

    rungs = []
    books = []
    for index, bracket in enumerate(("29", "30", "31+")):
        yes_id = f"yes-{index}"
        no_id = f"no-{index}"
        rungs.append(
            {
                "bracket": bracket,
                "condition_id": f"condition-{index}",
                "yes_token_id": f"yes-token-{index}",
                "no_token_id": f"no-token-{index}",
                "yes_book_capture_id": yes_id,
                "no_book_capture_id": no_id,
            }
        )
        yes_bid = 0.1 + 0.1 * index
        yes_ask = 0.12 + 0.1 * index
        for capture_id, bid, ask in (
            (yes_id, yes_bid, yes_ask),
            (no_id, 1.0 - yes_ask, 1.0 - yes_bid),
        ):
            books.append(
                {
                    "book_capture_id": capture_id,
                    "status": "ok",
                    "event_time_pit_scorable": True,
                    "request_started_at_utc": "2026-08-08T04:00:00Z",
                    "response_received_at_utc": "2026-08-08T04:00:01Z",
                    "parsed_at_utc": "2026-08-08T04:00:02Z",
                    "summary": {"best_bid": bid, "best_ask": ask},
                }
            )
    with gzip.open(book_path, "wt", encoding="utf-8") as handle:
        for row in books:
            handle.write(json.dumps(row) + "\n")
    ladder_path.write_text(
        json.dumps(
            {
                "available_at_utc": "2026-08-08T04:00:03Z",
                "batch_capture_id": "batch-1",
                "records": [
                    {
                        "city": "Tokyo",
                        "target_date": "2026-08-09",
                        "event_id": "event-1",
                        "rungs": rungs,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    checkpoints = subject.load_canonical_market_checkpoints(
        books_root=books_root,
        ladder_root=ladder_root,
        events=[{"city": "Tokyo", "target_date": "2026-08-09"}],
    )
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint["event_time_pit_scorable"] is True
    assert checkpoint["source_contract"] == "canonical_market_books_v1"
    assert checkpoint["native_lattice_ordering"] == ["29", "30", "31+"]
    assert checkpoint["rung_manifest"][0]["bottom"] is True
    assert checkpoint["rung_manifest"][-1]["top"] is True
    assert checkpoint["market_distribution_complete"] is True
    assert sum(checkpoint["probabilities"].values()) == 1.0
    assert checkpoint["rung_manifest"][0]["yes_best_bid"] == 0.1
    assert checkpoint["rung_manifest"][0]["yes_best_ask"] == 0.12


def test_revision_execution_scores_independent_transition_with_real_quotes() -> None:
    event = {
        "event_class": "forward_provider_run_first_seen",
        "checkpoint_policy": "D-1_18_24",
        "event_available_at_utc": "2026-08-11T10:00:00Z",
        "city": "Tokyo",
        "target_date": "2026-08-12",
        "post_book_snapshot_id": "post",
        "markout_30m_status": "scoreable",
        "markout_30m_snapshot_id": "later",
        "markout_60m_status": "missing_checkpoint",
        "markout_90m_status": "missing_checkpoint",
        "consensus_mean_before_f": 79.0,
        "consensus_mean_after_f": 80.0,
    }
    common = {
        "source_contract": "canonical_market_books_v1",
        "event_time_pit_scorable": True,
        "market_distribution_complete": True,
    }
    before = [
        {
            "label": "25",
            "low": None,
            "high": 25.0,
            "condition_id": "cold",
            "yes_best_bid": 0.20,
            "yes_best_ask": 0.21,
            "yes_best_bid_size": 10.0,
            "yes_best_ask_size": 10.0,
        },
        {
            "label": "26+",
            "low": 26.0,
            "high": None,
            "condition_id": "warm",
            "yes_best_bid": 0.30,
            "yes_best_ask": 0.31,
            "yes_best_bid_size": 10.0,
            "yes_best_ask_size": 10.0,
        },
    ]
    later = [dict(before[0]), {**before[1], "yes_best_bid": 0.35}]
    rows = subject.revision_execution_candidates(
        [event],
        [
            {**common, "feature_book_snapshot_id": "post", "rung_manifest": before},
            {**common, "feature_book_snapshot_id": "later", "rung_manifest": later},
        ],
    )
    sigma_two = next(row for row in rows if row["sigma_f"] == 2.0)
    assert sigma_two["condition_id"] == "warm"
    assert sigma_two["entry_ask"] == 0.31
    assert sigma_two["exit_bid"] == 0.35
    expected = (
        0.35
        - 0.05 * 0.35 * 0.65
        - 0.01
        - 0.31
        - 0.05 * 0.31 * 0.69
        - 0.01
    )
    assert abs(sigma_two["taker_pnl_per_share"] - expected) < 1e-12
    assert sigma_two["maker_fill_evidence"] == "blocked_no_own_order_queue_overlap"


def _write_d1_panel_artifacts(tmp_path, events, checkpoints) -> None:
    event_fields = sorted({key for row in events for key in row})
    checkpoint_fields = sorted({key for row in checkpoints for key in row})
    for name, fields, rows in (
        ("revision_events.csv", event_fields, events),
        ("market_checkpoints.csv", checkpoint_fields, checkpoints),
    ):
        with (tmp_path / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def test_legacy_panel_scores_shared_transition_once_and_builds_yes_no(tmp_path) -> None:
    manifest_pre = [
        {
            "condition_id": "c1", "label": "80", "yes_best_bid": 0.40,
            "yes_best_ask": 0.42, "yes_best_bid_size": 7, "yes_best_ask_size": 8,
        }
    ]
    manifest_post = [{**manifest_pre[0], "yes_best_bid": 0.45, "yes_best_ask": 0.47}]
    manifest_exit = [{**manifest_pre[0], "yes_best_bid": 0.55, "yes_best_ask": 0.57}]
    base_event = {
        "event_class": "legacy_provider_run_earliest_observed",
        "checkpoint_policy": "D-1_18_24", "markout_60m_status": "scoreable",
        "city": "Tokyo", "target_date": "2026-08-12", "pre_book_snapshot_id": "pre",
        "post_book_snapshot_id": "post", "markout_60m_snapshot_id": "exit",
        "consensus_median_before_f": "79", "consensus_iqr_before_f": "1",
        "event_available_at_utc": "2026-08-11T01:00:00Z", "event_local_hour": "10",
    }
    events = [
        {**base_event, "consensus_median_after_f": "80", "consensus_iqr_after_f": "2", "consensus_median_revision_f": "1"},
        {**base_event, "event_available_at_utc": "2026-08-11T01:03:00Z", "consensus_median_after_f": "81", "consensus_iqr_after_f": "3", "consensus_median_revision_f": "1"},
    ]
    checkpoints = [
        {
            "feature_book_snapshot_id": snapshot, "source_contract": "canonical_market_books_v1",
            "event_time_pit_scorable": "true", "market_distribution_complete": "true",
            "rung_manifest": repr(manifest),
        }
        for snapshot, manifest in (("pre", manifest_pre), ("post", manifest_post), ("exit", manifest_exit))
    ]
    _write_d1_panel_artifacts(tmp_path, events, checkpoints)

    rows, summary = subject.build_legacy_development_executable_rung_panel(tmp_path)
    assert len(rows) == 2
    yes = next(row for row in rows if row["side"] == "YES")
    no = next(row for row in rows if row["side"] == "NO")
    assert yes["provider_event_count"] == 2
    assert yes["rolling_consensus_net_revision_f"] == 2.0
    assert yes["entry_price"] == 0.47
    assert yes["exit_price"] == 0.55
    assert no["entry_price"] == 0.55
    assert abs(no["exit_price"] - 0.43) < 1e-12
    expected_yes = 0.55 - 0.05 * 0.55 * 0.45 - 0.47 - 0.05 * 0.47 * 0.53
    assert abs(yes["fee_only_pnl_per_share"] - expected_yes) < 1e-12
    assert abs(yes["one_cent_per_side_stress_pnl_per_share"] - (expected_yes - 0.02)) < 1e-12
    assert summary["signal_evidence_funnel"]["independent_transitions"] == 1


def test_legacy_panel_reports_market_contract_and_ladder_blockers(tmp_path) -> None:
    event = {
        "event_class": "legacy_provider_run_earliest_observed", "checkpoint_policy": "D-1_18_24",
        "markout_60m_status": "scoreable", "city": "Tokyo", "target_date": "2026-08-12",
        "pre_book_snapshot_id": "pre", "post_book_snapshot_id": "post", "markout_60m_snapshot_id": "exit",
        "consensus_median_before_f": "79", "consensus_median_after_f": "80",
        "consensus_median_revision_f": "1", "event_available_at_utc": "2026-08-11T01:00:00Z",
    }
    valid = [{"condition_id": "c1", "label": "80", "yes_best_bid": 0.4, "yes_best_ask": 0.5, "yes_best_bid_size": 1, "yes_best_ask_size": 1}]
    bad = [{**valid[0], "label": "81"}]
    checkpoints = [
        {"feature_book_snapshot_id": "pre", "source_contract": "canonical_market_books_v1", "event_time_pit_scorable": "true", "market_distribution_complete": "true", "rung_manifest": repr(valid)},
        {"feature_book_snapshot_id": "post", "source_contract": "legacy_paper_snapshot", "event_time_pit_scorable": "true", "market_distribution_complete": "true", "rung_manifest": repr(valid)},
        {"feature_book_snapshot_id": "exit", "source_contract": "canonical_market_books_v1", "event_time_pit_scorable": "true", "market_distribution_complete": "true", "rung_manifest": repr(bad)},
    ]
    _write_d1_panel_artifacts(tmp_path, [event], checkpoints)
    rows, summary = subject.build_legacy_development_executable_rung_panel(tmp_path)
    assert rows == []
    assert summary["blocker_counts"] == {"noncanonical_market_checkpoint": 1}

    checkpoints[1]["source_contract"] = "canonical_market_books_v1"
    checkpoints[1]["event_time_pit_scorable"] = "false"
    _write_d1_panel_artifacts(tmp_path, [event], checkpoints)
    _, summary = subject.build_legacy_development_executable_rung_panel(tmp_path)
    assert summary["blocker_counts"] == {"inexact_market_clock": 1}

    checkpoints[1]["event_time_pit_scorable"] = "true"
    _write_d1_panel_artifacts(tmp_path, [event], checkpoints)
    _, summary = subject.build_legacy_development_executable_rung_panel(tmp_path)
    assert summary["blocker_counts"] == {"misaligned_rung_manifest": 1}


def test_default_forward_execution_does_not_admit_legacy_event() -> None:
    legacy = {
        "event_class": "legacy_provider_run_earliest_observed",
        "checkpoint_policy": "D-1_18_24", "city": "Tokyo", "target_date": "2026-08-12",
        "post_book_snapshot_id": "post", "markout_60m_status": "scoreable",
        "markout_60m_snapshot_id": "exit",
    }
    assert subject.revision_execution_candidates([legacy], []) == []
