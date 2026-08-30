import importlib.util
import json
import sqlite3
from pathlib import Path


MODULE = Path(__file__).parents[2] / "scripts/analysis/live_performance/weather_cross_no_v2_source_attribution.py"
SPEC = importlib.util.spec_from_file_location("attribution", MODULE)
attribution = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(attribution)


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def test_winner_only_pnl_checkpoint_and_missing_coverage(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    common = {"city": "Amsterdam", "target_date": "2026-08-30", "book_full_depth_valid": True, "expected_five_share_vwap": 0.4, "worst_ask_for_five_shares": 0.4}
    write_jsonl(out / "opportunities.jsonl", [
        {**common, "source": "metar_ws_hfmetar", "information_event_id": "hf", "status": "candidate", "previous_official_bracket": "30", "new_source_bracket": "31", "execution_race_key": "race", "economic_cross_id": "economic-race"},
        {**common, "source": "metar_ws_datis", "information_event_id": "datis", "status": "blocked", "previous_official_bracket": "30", "new_source_bracket": "31", "execution_race_key": "race", "economic_cross_id": "economic-race", "blockers": ["cross_source_race_already_executed"]},
        {**common, "source": "metar_ws_metar", "information_event_id": "metar", "status": "blocked", "blockers": ["market_not_found"]},
    ])
    write_jsonl(out / "execution_attempts.jsonl", [{**common, "source": "metar_ws_hfmetar", "execution_attempt_id": "a", "execution_race_key": "race", "economic_cross_id": "economic-race"}])
    write_jsonl(out / "orders.jsonl", [{**common, "source": "metar_ws_hfmetar", "order_id": "o1", "execution_attempt_id": "a"}])
    write_jsonl(out / "fills.jsonl", [{**common, "source": "metar_ws_hfmetar", "order_id": "o1", "actual_fill_shares": 5, "actual_fill_cost_usd": 2, "condition_id": "c1", "token_id": "t1", "information_event_id": "hf", "economic_cross_id": "economic-race"}])
    write_jsonl(out / "capture_demands.jsonl", [
        {"demand_id": "d1", "trigger_event_id": "hf", "token_id": "t1", "requested_checkpoints_seconds": [0, 15, 30, 60, 120, 300], "metadata": {"source_arm": "metar_ws_hfmetar", "economic_cross_id": "economic-race"}},
        {"demand_id": "d2", "trigger_event_id": "datis", "token_id": "t1", "requested_checkpoints_seconds": [0, 15, 30, 60, 120, 300], "metadata": {"source_arm": "metar_ws_datis", "economic_cross_id": "economic-race"}},
    ])
    books = tmp_path / "books"; books.mkdir()
    write_jsonl(books / "public_books_20260830.jsonl", [{"requested_strategy_checkpoint": True, "checkpoint_ref": {"demand_id": "d1", "offset_seconds": 15}, "condition_id": "c1", "best_bid": 0.5, "sweeps": [{"shares": 5, "sell_proceeds": 2.5}]}])
    db = tmp_path / "weather.db"; conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fact_trades (order_id TEXT, fill_id TEXT, settlement_status TEXT, fees_usd REAL, pnl_usd_at_fill REAL, fill_qty REAL, cost_usd REAL, val_bid REAL, val_snapshot_ts_utc TEXT, fact_built_at_utc TEXT)")
    conn.execute("INSERT INTO fact_trades VALUES ('o1','f1','settled',.1,2.9,5,2,NULL,NULL,'2026-08-31T00:00:00Z')")
    conn.commit(); conn.close()
    report = attribution.build_report(out, market_books_root=books, canonical_db=db)
    hf, datis, metar = (report["arms"][key] for key in ("metar_ws_hfmetar", "metar_ws_datis", "metar_ws_metar"))
    assert hf["race_winner"] == 1 and datis["race_later_blocked"] == 1
    assert report["policy_max_no_ask"] == 0.97
    assert report["generated_at_utc"].endswith("Z")
    assert hf["canonical"]["realized_pnl_usd"] == 2.9 and datis["canonical"]["realized_pnl_usd"] == 0
    assert hf["checkpoint_coverage"]["15"]["coverage"] == 1.0
    assert datis["checkpoint_coverage"]["15"]["coverage"] == 0.0
    assert metar["blockers"]["market_not_found"] == 1
    assert hf["exploratory_fee_adjusted_liquidation_markout"]["by_checkpoint"]["15"]["markout_usd"] == 0.3375
    assert report["race_attribution_conflicts"] == []


def test_same_event_id_is_counted_per_source_and_race_conflict_is_not_double_attributed(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    common = {
        "information_event_id": "shared",
        "status": "candidate",
        "book_full_depth_valid": True,
        "expected_five_share_vwap": 0.4,
        "worst_ask_for_five_shares": 0.4,
        "economic_cross_id": "same-race",
    }
    write_jsonl(out / "opportunities.jsonl", [
        {**common, "source": "metar_ws_hfmetar"},
        {**common, "source": "metar_ws_datis"},
    ])
    write_jsonl(out / "execution_attempts.jsonl", [
        {**common, "source": "metar_ws_hfmetar", "execution_attempt_id": "a", "reserved_at_monotonic_ns": 10},
        {**common, "source": "metar_ws_datis", "execution_attempt_id": "a", "reserved_at_monotonic_ns": 20},
    ])
    report = attribution.build_report(out)
    assert report["arms"]["metar_ws_hfmetar"]["fixed_denominator_events"] == 1
    assert report["arms"]["metar_ws_datis"]["fixed_denominator_events"] == 1
    assert report["arms"]["metar_ws_hfmetar"]["race_winner"] == 1
    assert report["arms"]["metar_ws_datis"]["race_winner"] == 0
    assert report["race_attribution_conflicts"][0]["winner_source_arm"] == "metar_ws_hfmetar"


def test_no_inputs_are_silent_or_mutated(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    report = attribution.build_report(out)
    assert report["coverage_blockers"]["market_books"] == "not_requested"
    assert report["canonical_db"]["status"] == "not_requested"
    assert set(report["arms"]) == set(attribution.ARMS)


def test_five_share_executable_respects_live_no_ask_cap(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    write_jsonl(out / "opportunities.jsonl", [{
        "source": "metar_ws_hfmetar",
        "information_event_id": "too-expensive",
        "book_full_depth_valid": True,
        "expected_five_share_vwap": 0.98,
        "worst_ask_for_five_shares": 0.98,
    }])
    report = attribution.build_report(out)
    assert report["arms"]["metar_ws_hfmetar"]["five_share_executable"] == 0
