from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "scripts/ops/weather_cross_no_v2_metar.py"
spec = importlib.util.spec_from_file_location("cross_no_v2", MODULE)
assert spec and spec.loader
cross_no_v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cross_no_v2)


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def event(source: str, event_id: str, temp_c: float, *, family: str = "family-1", role: str = "new_content", valid: bool = True) -> dict:
    return {"information_event_id": event_id, "source": source, "event_role": role, "city": "Miami", "target_date": "2026-08-31",
            "station": "KMIA", "temp_c": temp_c, "clock_valid": valid, "pit_eligible": valid,
            "source_topic": cross_no_v2.source_topic(source, "KMIA"),
            "transport_received_at_utc": "2026-08-31T11:59:30Z", "transport_received_monotonic_ns": int(event_id[-1]) * 100,
            "event_family_id": family, "semantic_version_id": event_id, "source_event_ts_utc": "2026-08-31T11:59:00Z"}


def markets() -> dict:
    # 80-81F is the prior official bracket; 82-83F is the next native range.
    return {"records": [
        {"city": "Miami", "event_date": "2026-08-31", "station": "KMIA", "bracket": "80-81", "outcome": "no", "condition_id": "c80", "token_id": "n80", "unit": "F", "book": {"full_depth_valid": True, "summary": {"best_ask": 0.5, "ask_size": 5}}},
        {"city": "Miami", "event_date": "2026-08-31", "station": "KMIA", "bracket": "82-83", "outcome": "no", "condition_id": "c82", "token_id": "n82", "unit": "F", "book": {"full_depth_valid": True, "summary": {"best_ask": 0.5, "ask_size": 5}}},
    ]}


def run(tmp_path: Path, rows: list[dict], **kwargs):
    source = tmp_path / "source"; source.mkdir(); (source / "information_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    books = tmp_path / "books.json"; books.write_text(json.dumps(markets()))
    return cross_no_v2.run_probe(source_events_root=source, market_books_latest=books, output_dir=tmp_path / "out", now=NOW, official_fee_rate=0.05, **kwargs)


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def celsius_for_fahrenheit(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def filled_order(order_id: str = "o1") -> dict:
    return {
        "order_id": order_id,
        "order_type": "GTC",
        "post_only": False,
        "place": {
            "success": True,
            "status": "matched",
            "order_id": order_id,
            "takingAmount": "5",
            "makingAmount": "2.5",
        },
    }


def test_universe_loader_preserves_required_market_units(tmp_path: Path):
    config = tmp_path / "universe.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "cross_no_v2_metar_universe_v1",
                "targets": [
                    {
                        "city": "Miami",
                        "station": "KMIA",
                        "timezone": "America/New_York",
                        "market_unit": "F",
                        "live_eligible": True,
                    },
                    {
                        "city": "London",
                        "station": "EGLL",
                        "timezone": "Europe/London",
                        "market_unit": "C",
                        "live_eligible": False,
                    },
                ],
            }
        )
    )

    allowlist, timezones, market_units, live_eligible = (
        cross_no_v2.load_universe_config(config)
    )

    assert allowlist == {"Miami": "KMIA", "London": "EGLL"}
    assert timezones["London"] == "Europe/London"
    assert market_units == {"Miami": "F", "London": "C"}
    assert live_eligible == frozenset({"Miami"})


def test_native_integer_lattice_maps_us_celsius_without_decimal_gap(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    rows = [event("metar_ws_metar", "official1", 21.0), event("metar_ws_hfmetar", "hf2", 22.3)]
    (source / "information_events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    books = tmp_path / "books.json"
    books.write_text(
        json.dumps(
            {
                "records": [
                    {**markets()["records"][0], "bracket": "70-71"},
                    {**markets()["records"][1], "bracket": "72-73"},
                ]
            }
        )
    )

    cross_no_v2.run_probe(
        source_events_root=source,
        market_books_latest=books,
        output_dir=tmp_path / "out",
        now=NOW,
        official_fee_rate=0.05,
        market_units={"Miami": "F"},
    )

    candidate = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert candidate["status"] == "candidate"
    assert candidate["prior_official_running_max"] == 70
    assert candidate["source_running_max"] == 72
    assert candidate["market_unit"] == "F"


def test_celsius_market_unit_is_not_silently_treated_as_fahrenheit(tmp_path: Path):
    assert cross_no_v2._market_value(21.4, "C") == 21
    assert cross_no_v2._market_value(21.4, "F") == 71
    assert cross_no_v2._market_value(-1.5, "C") == -2
    assert cross_no_v2._market_value(21.4, "") is None


def test_shadow_cross_is_proxy_and_never_hard_settlement_invalidation(tmp_path: Path):
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), event("metar_ws_datis", "datis2", 27.8)])
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert result["orders"] == 0
    candidate = rows[-1]
    assert candidate["status"] == "candidate"
    assert candidate["source_cross_is_proxy_only"] is True
    assert candidate["settlement_hard_invalidation"] is False
    assert candidate["planned_shares"] == 5.0


def test_single_current_margin_above_point_five_is_shadow_only(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    moderate = event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.6))
    result = run(
        tmp_path,
        [official, moderate],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["shadow_single_cross"] is True
    assert row["strong_single_cross"] is False
    assert row["consecutive_cross_count"] == 1
    assert row["live_cross_confirmed"] is False
    assert row["attribution_role"] == "shadow_single_cross"
    assert row["shadow_zero_notional"] is True
    assert row["execution_authority"] is False
    assert row["planned_shares"] == 0.0
    assert row["hypothetical_shares"] == 5.0
    assert row["planned_principal_usd"] == 0.0
    assert row["hypothetical_principal_usd"] > 0.0
    assert "live_cross_confirmation_not_met" in row["blockers"]
    demand = read(tmp_path / "out/capture_demands.jsonl")[-1]
    assert demand["reason"] == "cross_no_v2_metar_shadow_markout"
    assert demand["metadata"]["capture_role"] == (
        "single_current_gt_0p5_zero_notional_shadow"
    )
    assert demand["metadata"]["shadow_zero_notional"] is True


def test_single_current_margin_strictly_above_point_seven_executes(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    strong = event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.71))
    result = run(
        tmp_path,
        [official, strong],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 1 and len(calls) == 1
    assert row["strong_single_cross"] is True
    assert row["live_cross_confirmed"] is True


def test_single_current_margin_equal_to_point_seven_remains_shadow(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    boundary = event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.7))
    result = run(
        tmp_path,
        [official, boundary],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["strong_single_cross"] is False
    assert row["shadow_single_cross"] is True


def test_two_distinct_consecutive_point_five_crosses_remain_comparison_only(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    first = {
        **event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.6)),
        "source_event_ts_utc": "2026-08-31T11:58:00Z",
    }
    second = {
        **event("metar_ws_hfmetar", "hf3", celsius_for_fahrenheit(81.65)),
        "source_event_ts_utc": "2026-08-31T11:59:00Z",
    }
    result = run(
        tmp_path,
        [official, first, second],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert result["orders"] == 0 and calls == []
    assert rows[-2]["consecutive_cross_count"] == 1
    assert rows[-1]["consecutive_cross_count"] == 2
    assert rows[-1]["consecutive_cross_confirmed"] is True
    assert rows[-1]["strong_single_cross"] is False
    assert rows[-1]["live_cross_confirmed"] is False
    assert rows[-1]["shadow_zero_notional"] is True


def test_same_observation_timestamp_cannot_supply_second_confirmation(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    first = event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.6))
    revision = event(
        "metar_ws_hfmetar",
        "hf3",
        celsius_for_fahrenheit(81.65),
        role="revision",
    )
    result = run(
        tmp_path,
        [official, first, revision],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["confirmation_observation_advanced"] is False
    assert row["consecutive_cross_count"] == 1


def test_backfilled_older_observation_cannot_trigger_strong_single(tmp_path: Path):
    calls = []
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    newer_moderate = {
        **event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.6)),
        "source_event_ts_utc": "2026-08-31T11:59:00Z",
    }
    older_strong = {
        **event("metar_ws_hfmetar", "hf3", celsius_for_fahrenheit(81.8)),
        "source_event_ts_utc": "2026-08-31T11:58:00Z",
    }
    result = run(
        tmp_path,
        [official, newer_moderate, older_strong],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["confirmation_observation_causally_current"] is False
    assert row["strong_single_cross"] is False
    assert row["shadow_single_cross"] is True


def test_comparison_confirmation_streak_survives_poll_cycle_restart(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    official = event("metar_ws_metar", "official1", celsius_for_fahrenheit(81.0))
    first = {
        **event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.6)),
        "source_event_ts_utc": "2026-08-31T11:58:00Z",
    }
    cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[official, first],
    )
    calls = []
    second = {
        **event("metar_ws_hfmetar", "hf3", celsius_for_fahrenheit(81.65)),
        "source_event_ts_utc": "2026-08-31T11:59:00Z",
    }
    result = cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[second],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(out / "opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["consecutive_cross_count"] == 2
    assert row["consecutive_cross_confirmed"] is True
    assert row["live_cross_confirmed"] is False
    state = json.loads((out / "state.json").read_text())
    assert state["schema_version"] == "cross_no_v2_metar_state_v2"


def test_current_observation_not_running_max_controls_execution(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    out.mkdir()
    (out / "state.json").write_text(
        json.dumps(
            {
                "schema_version": "cross_no_v2_metar_state_v1",
                "official_max": {"Miami|2026-08-31": 81},
                "source_max": {"Miami|2026-08-31|metar_ws_hfmetar": 83},
                "processed_event_key_migration_complete": True,
                "processed_event_journal_recovery_complete": True,
            }
        )
    )
    current = event("metar_ws_hfmetar", "hf2", celsius_for_fahrenheit(81.0))
    calls = []
    result = cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[current],
        live=True,
        confirm_live=True,
        place_fn=lambda order: calls.append(order) or filled_order(),
    )
    row = read(out / "opportunities.jsonl")[-1]
    assert result["orders"] == 0 and calls == []
    assert row["source_running_max"] == 83
    assert row["source_running_max_descriptive_only"] is True
    assert row["source_current_native_value"] == 81
    assert "current_observation_did_not_cross_new_bracket" in row["blockers"]


def test_live_requires_double_flag_and_injected_place_fn(tmp_path: Path):
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)], live=True)
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert result["orders"] == 0
    assert "confirm_live_missing" in rows[-1]["blockers"]


def test_live_exactly_five_shares_and_cross_source_race_dedupes(tmp_path: Path):
    calls = []
    def place(order):
        calls.append(order)
        return {"order_id": "o1", "order_type": "GTC", "post_only": False, "place": {"success": True, "status": "matched", "order_id": "o1", "takingAmount": "5", "makingAmount": "2.5"}}
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), event("metar_ws_datis", "datis2", 27.8), event("metar_ws_hfmetar", "hf3", 27.9)], live=True, confirm_live=True, place_fn=place)
    orders = read(tmp_path / "out/orders.jsonl")
    assert result["orders"] == 1 and len(calls) == 1
    assert orders[0]["size"] == 5.0 and orders[0]["actual_fill_shares"] == 5.0
    assert orders[0]["public_trade_is_own_fill"] is False
    opportunities = read(tmp_path / "out/opportunities.jsonl")
    assert orders[0]["attribution_source_arm"] == "metar_ws_datis"
    assert orders[0]["attribution_source_topic"] == "metar.atis.kmia"
    assert orders[0]["attribution_role"] == "execution_race_winner"
    assert opportunities[-1]["attribution_role"] == "later_race_blocked"
    assert opportunities[-1]["economic_cross_id"] == orders[0]["economic_cross_id"]
    assert "cross_source_race_already_executed" in opportunities[-1]["blockers"]
    summary = json.loads((tmp_path / "out/source_attribution_latest.json").read_text())
    assert summary["source_arms"]["metar_ws_datis"]["orders"] == 1
    assert summary["source_arms"]["metar_ws_datis"]["fills"] == 1
    assert summary["source_arms"]["metar_ws_hfmetar"]["later_race_blocked_events"] == 1


def test_three_topics_are_explicit_source_arms_and_capture_metadata_is_attributed(tmp_path: Path):
    assert cross_no_v2.source_topic("metar_ws_metar", "KJFK") == "metar.obs.kjfk"
    assert cross_no_v2.source_topic("metar_ws_hfmetar", "KJFK") == "metar.obs10.kjfk"
    assert cross_no_v2.source_topic("metar_ws_datis", "KJFK") == "metar.atis.kjfk"
    run(
        tmp_path,
        [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)],
    )
    demand = read(tmp_path / "out/capture_demands.jsonl")[0]
    assert demand["metadata"]["source_arm"] == "metar_ws_hfmetar"
    assert demand["metadata"]["source_topic"] == "metar.obs10.kmia"
    assert demand["metadata"]["economic_cross_id"].startswith("cross_no_v2:")


def test_topic_mismatch_is_preserved_but_fail_closed(tmp_path: Path):
    mismatched = {
        **event("metar_ws_hfmetar", "hf2", 27.8),
        "source_topic": "metar.atis.kmia",
    }
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), mismatched])
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert result["orders"] == 0
    assert row["source_topic"] == "metar.atis.kmia"
    assert row["source_topic_valid"] is False
    assert "source_topic_mismatch_or_missing" in row["blockers"]


def test_equal_producer_event_ids_from_different_sources_are_both_denominator_rows(tmp_path: Path):
    run(
        tmp_path,
        [
            event("metar_ws_metar", "shared1", 26.7),
            event("metar_ws_hfmetar", "shared1", 27.8),
        ],
    )
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert len(rows) == 2
    state = json.loads((tmp_path / "out/state.json").read_text())
    assert state["processed_event_keys"] == [
        "metar_ws_hfmetar|shared1",
        "metar_ws_metar|shared1",
    ]


def test_legacy_bare_id_migration_skips_only_the_source_already_in_old_journal(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"; out.mkdir()
    old = event("metar_ws_metar", "shared1", 26.7)
    (out / "opportunities.jsonl").write_text(json.dumps(old) + "\n")
    (out / "state.json").write_text(json.dumps({
        "processed_event_ids": ["shared1"],
        "official_max": {"Miami|2026-08-31": 80.06},
    }))
    cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[
            old,
            event("metar_ws_hfmetar", "shared1", 27.8),
        ],
    )
    rows = read(out / "opportunities.jsonl")
    assert len(rows) == 2
    assert rows[-1]["source"] == "metar_ws_hfmetar"
    state = json.loads((out / "state.json").read_text())
    assert set(state["processed_event_keys"]) == {
        "metar_ws_metar|shared1",
        "metar_ws_hfmetar|shared1",
    }


def test_empty_partial_migration_self_heals_from_append_only_opportunities(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"; out.mkdir()
    old = event("metar_ws_metar", "old1", 26.7)
    (out / "opportunities.jsonl").write_text(json.dumps(old) + "\n")
    (out / "state.json").write_text(json.dumps({
        "processed_event_keys": [],
        "processed_event_key_migration_complete": True,
    }))
    health = cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[],
    )
    state = json.loads((out / "state.json").read_text())
    assert state["processed_event_keys"] == ["metar_ws_metar|old1"]
    assert state["processed_event_journal_recovery_complete"] is True
    assert health["events_seen_total"] == 1


def test_explicit_missing_raw_topic_is_not_synthesized(tmp_path: Path):
    missing = {**event("metar_ws_hfmetar", "hf2", 27.8), "source_topic": None}
    run(tmp_path, [event("metar_ws_metar", "official1", 26.7), missing])
    row = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert row["source_topic"] is None
    assert row["source_topic_valid"] is False
    assert "source_topic_mismatch_or_missing" in row["blockers"]


def test_prestart_manifest_allows_only_narrow_self_health_schema_transition():
    finding = {
        "severity": "critical",
        "kind": "runtime_health_contract_mismatch",
        "detail": {
            "runtimes": [{
                "instance_id": "cross_no_v2_metar_v1",
                "status": "mismatch",
                "mismatches": [
                    {"field": "code_identity"},
                    {"field": "source_attribution_schema_version"},
                ],
            }]
        },
    }
    cross_no_v2.validate_isolated_prestart_manifest({"findings": [finding]})
    bad = json.loads(json.dumps(finding))
    bad["detail"]["runtimes"][0]["mismatches"].append({"field": "live_enabled"})
    try:
        cross_no_v2.validate_isolated_prestart_manifest({"findings": [bad]})
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("unrelated live health mismatch must fail closed")


def test_fail_closed_on_invalid_clock_and_missing_depth(tmp_path: Path):
    bad = event("metar_ws_datis", "bad2", 27.8, valid=False)
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), bad])
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert result["orders"] == 0
    assert "clock_or_pit_not_eligible" in rows[-1]["blockers"]


def test_atlanta_terminal_false_fixture_is_retained_not_rewritten(tmp_path: Path):
    rows = [event("metar_ws_metar", "official1", 26.7), {**event("metar_ws_datis", "datis2", 27.8), "terminal_false_cross": True, "city": "Atlanta", "station": "KATL"}]
    result = run(tmp_path, rows)
    written = read(tmp_path / "out/opportunities.jsonl")
    assert result["opportunities_emitted"] == 2
    assert written[-1]["terminal_false_cross"] is True
    assert written[-1]["settlement_hard_invalidation"] is False


def test_clock_invalid_is_fail_closed_unless_explicit_same_boot_live_override(tmp_path: Path):
    invalid = {**event("metar_ws_datis", "datis2", 27.8, valid=False), "evidence_incremental_after_start_watermark": True,
               "transport_received_monotonic_ns": 9_000, "transport_received_at_utc": "2026-08-31T11:59:50Z", "clock_offset_ms": 55.8}
    result = run(tmp_path, [event("metar_ws_metar", "official1", 26.7), invalid], live=True, confirm_live=True,
                 place_fn=lambda _: {"order_id": "x", "order_type": "GTC", "post_only": False, "place": {"success": True, "status": "matched", "order_id": "x", "takingAmount": "5", "makingAmount": "2.5"}},
                 allow_clock_invalid_same_boot_monotonic_probe=True, clock_uncertainty_ms=190.0,
                 boot_monotonic_start_ns=1_000, monotonic_now_ns=10_000)
    assert result["orders"] == 1
    candidate = read(tmp_path / "out/opportunities.jsonl")[-1]
    assert candidate["formal_latency_rank_eligible"] is False
    assert candidate["execution_clock_mode"] == "same_boot_monotonic_exploratory_v1"


def test_running_max_state_survives_poll_cycles(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    first = cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[event("metar_ws_metar", "official1", 26.7)],
    )
    second = cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[event("metar_ws_datis", "datis2", 27.8)],
    )
    assert first["orders_attempted_this_cycle"] == 0
    assert second["blocked"] == 0
    assert read(out / "opportunities.jsonl")[-1]["status"] == "candidate"


def test_real_market_raw_asks_are_swept_across_levels():
    market = {
        "full_depth_valid": True,
        "raw": {
            "asks": [
                {"price": "0.70", "size": "2"},
                {"price": "0.71", "size": "4"},
            ]
        }
    }
    assert cross_no_v2._book_summary(market) == (0.70, 2.0, True)
    sweep = cross_no_v2._five_share_sweep(market)
    assert sweep["covered"] is True
    assert sweep["worst_ask"] == 0.71
    assert abs(sweep["vwap"] - 0.706) < 1e-12


def test_raw_asks_without_explicit_depth_contract_are_not_promoted_to_full_depth():
    market = {"raw": {"asks": [{"price": "0.70", "size": "10"}]}}
    assert cross_no_v2._book_summary(market) == (0.70, 10.0, False)


def test_submit_failure_reservation_prevents_retry(tmp_path: Path):
    def fail(_order):
        raise RuntimeError("synthetic rejection")

    rows = [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)]
    first = run(tmp_path, rows, live=True, confirm_live=True, place_fn=fail)
    assert first["orders_attempted_this_cycle"] == 1
    assert (tmp_path / "out/execution_attempts.jsonl").exists()
    second = cross_no_v2.run_probe(
        source_events_root=tmp_path / "source",
        market_books_latest=tmp_path / "books.json",
        output_dir=tmp_path / "out",
        now=NOW,
        official_fee_rate=0.05,
        live=True,
        confirm_live=True,
        place_fn=fail,
    )
    assert second["orders_attempted_this_cycle"] == 0


def test_prior_daily_reservations_do_not_cap_a_new_economic_cross(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    rows = [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)]
    (source / "information_events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    out.mkdir()
    reservations = [
        {
            "strategy_id": cross_no_v2.STRATEGY_ID,
            "execution_attempt_id": f"prior-{index}",
            "execution_race_key": f"prior-race-{index}",
            "city": f"Prior{index}",
            "target_date": "2026-08-31",
            "created_at_utc": "2026-08-31T01:00:00Z",
            "reserved_principal_usd": 4.0,
        }
        for index in range(5)
    ]
    (out / "execution_attempts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in reservations) + "\n"
    )
    result = cross_no_v2.run_probe(
        source_events_root=source,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        live=True,
        confirm_live=True,
        place_fn=lambda _: {
            "order_id": "new-order",
            "place": {"success": True, "status": "matched", "order_id": "new-order", "takingAmount": "5", "makingAmount": "2.5"},
        },
    )
    assert result["orders_attempted_this_cycle"] == 1
    assert result["orders_reserved_utc_day"] == 6
    assert result["daily_order_cap_enabled"] is False
    assert result["daily_principal_cap_enabled"] is False


def test_same_condition_token_is_not_bought_twice(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "information_events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)]) + "\n"
    )
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    out.mkdir()
    (out / "execution_attempts.jsonl").write_text(
        json.dumps({
            "strategy_id": cross_no_v2.STRATEGY_ID,
            "execution_attempt_id": "prior-token",
            "execution_race_key": "different-race",
            "token_id": "n80",
            "created_at_utc": "2026-08-31T01:00:00Z",
        }) + "\n"
    )
    result = cross_no_v2.run_probe(
        source_events_root=source,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        live=True,
        confirm_live=True,
        place_fn=lambda _: (_ for _ in ()).throw(AssertionError("same token must not be placed twice")),
    )
    assert result["orders_attempted_this_cycle"] == 0
    assert "condition_token_already_executed" in read(out / "opportunities.jsonl")[-1]["blockers"]


def test_non_live_eligible_city_is_collected_but_cannot_execute(tmp_path: Path):
    result = run(
        tmp_path,
        [event("metar_ws_metar", "official1", 26.7), event("metar_ws_hfmetar", "hf2", 27.8)],
        live=True,
        confirm_live=True,
        live_eligible_cities=set(),
        place_fn=lambda _: (_ for _ in ()).throw(AssertionError("ineligible city must fail closed")),
    )
    assert result["orders_attempted_this_cycle"] == 0
    assert "city_not_live_eligible" in read(tmp_path / "out/opportunities.jsonl")[-1]["blockers"]


def test_expired_experiment_creates_pause_and_blocks_candidate(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "experiment_control.json").write_text(
        json.dumps(
            {
                "schema_version": "cross_no_v2_metar_experiment_control_v1",
                "started_at_utc": "2026-08-29T00:00:00Z",
                "stop_after_sec": 86400.0,
                "ends_at_utc": "2026-08-30T00:00:00Z",
            }
        )
    )
    pause = out / "PAUSE"
    control = cross_no_v2.enforce_experiment_deadline(out, pause_file=pause, stop_after_sec=86400)
    assert control["expired"] is True and pause.exists()
    source = tmp_path / "source"
    source.mkdir()
    (source / "information_events.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [event("metar_ws_metar", "official1", 26.7), event("metar_ws_datis", "datis2", 27.8)]
        )
        + "\n"
    )
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    result = cross_no_v2.run_probe(
        source_events_root=source,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        live=True,
        confirm_live=True,
        place_fn=lambda _: (_ for _ in ()).throw(AssertionError("pause must block placement")),
        pause_file=pause,
    )
    assert result["orders_attempted_this_cycle"] == 0
    assert "pause_file_present" in read(out / "opportunities.jsonl")[-1]["blockers"]


def test_fresh_book_error_is_fail_closed(tmp_path: Path):
    result = run(
        tmp_path,
        [event("metar_ws_metar", "official1", 26.7), event("metar_ws_datis", "datis2", 27.8)],
        fetch_book_fn=lambda *_args, **_kwargs: {"status": "error", "raw": {}},
    )
    assert result["orders_attempted_this_cycle"] == 0
    blockers = read(tmp_path / "out/opportunities.jsonl")[-1]["blockers"]
    assert "fresh_execution_book_not_ok" in blockers
    assert "full_depth_execution_book_missing" in blockers


def test_lower_official_revision_cannot_reduce_running_max(tmp_path: Path):
    books = tmp_path / "books.json"
    books.write_text(json.dumps(markets()))
    out = tmp_path / "out"
    cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[event("metar_ws_metar", "official1", 26.7)],
    )
    cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[event("metar_ws_metar", "official2", 25.0, role="revision")],
    )
    cross_no_v2.run_probe(
        source_events_root=tmp_path,
        market_books_latest=books,
        output_dir=out,
        now=NOW,
        official_fee_rate=0.05,
        events_override=[event("metar_ws_hfmetar", "hf3", 27.8)],
    )
    candidate = read(out / "opportunities.jsonl")[-1]
    assert candidate["status"] == "candidate"
    assert candidate["prior_official_running_max"] == 80


def test_late_official_updates_state_but_cannot_trigger_decision(tmp_path: Path):
    late_official = {
        **event("metar_ws_metar", "official1", 26.7),
        "source_event_ts_utc": "2026-08-31T08:00:00Z",
    }
    run(
        tmp_path,
        [late_official, event("metar_ws_hfmetar", "hf2", 27.8)],
        max_observation_delay_sec=3600,
    )
    rows = read(tmp_path / "out/opportunities.jsonl")
    assert "source_observation_delay_out_of_bounds" in rows[0]["blockers"]
    assert rows[1]["status"] == "candidate"
    assert rows[1]["prior_official_running_max"] == 80


def test_source_stream_silence_is_visible_in_health(tmp_path: Path):
    result = run(
        tmp_path,
        [],
        source_stream_last_received_at_utc="2026-08-31T11:50:00Z",
        max_stream_silence_sec=120,
        monitor_source_stream=True,
    )
    assert result["status"] == "source_stream_stale"
    assert result["source_stream_stale"] is True


def test_direct_evidence_rejects_ended_collector_run(tmp_path: Path):
    db = tmp_path / "evidence.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE collector_run(run_id TEXT, started_at_ns INTEGER);
        CREATE TABLE collector_run_end(run_id TEXT);
        INSERT INTO collector_run VALUES('run-ended', 123);
        INSERT INTO collector_run_end VALUES('run-ended');
        """
    )
    conn.close()
    try:
        cross_no_v2.direct_evidence_events(
            db,
            cursor_path=tmp_path / "cursor.json",
            allowlist=cross_no_v2.DEFAULT_CITY_STATIONS,
            collector_run_start_wall_ns=123,
        )
    except ValueError as exc:
        assert "collector run has ended" in str(exc)
    else:
        raise AssertionError("ended collector run must fail closed")
