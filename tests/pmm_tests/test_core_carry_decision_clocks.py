from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as producer
from scripts.ops import weather_current_yes_core_carry_tiny_live_v2 as runner


BASE = datetime(2026, 9, 7, 6, 40, 24, tzinfo=timezone.utc)


class Clock(datetime):
    current = BASE

    @classmethod
    def now(cls, tz=None):
        return cls.current


def test_rest_clock_tracks_response_not_request_or_inherited_paper(monkeypatch):
    Clock.current = BASE
    monkeypatch.setattr(producer, "datetime", Clock)

    def handle(request):
        Clock.current = BASE + timedelta(seconds=5, microseconds=123456)
        return httpx.Response(200, json={
            "bids": [{"price": "0.745", "size": "20"}],
            "asks": [{"price": "0.86", "size": "20"}], "tick_size": "0.001",
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        book = producer.fetch_full_book(client, "token")
    row = {"current_yes_book_response_received_at_utc": "2020-01-01T00:00:00Z",
           **producer.direct_book_clock_fields(book), "current_yes_book_status": "ok"}
    assert book["request_started_at_utc"] == BASE.isoformat(timespec="microseconds")
    assert row["current_yes_book_response_received_at_utc"] == Clock.current.isoformat(timespec="microseconds")
    result = runner.near_core_book_freshness(row, now=Clock.current + timedelta(seconds=1), max_age_sec=10)
    assert result["near_core_book_age_sec"] == 1
    assert result["near_core_book_fresh"] is True
    assert result["near_core_book_clock_source"] == "direct_response_received_at_utc"


@pytest.mark.parametrize("offset,status", [(-1, "direct_book_clock_in_future"), (11, "stale_direct_book_quote"), (10, "fresh_direct_book_quote")])
def test_real_future_and_stale_books_remain_rejected(offset, status):
    row = {"current_yes_book_status": "ok", "current_yes_book_clock_lineage_status": runner.DIRECT_BOOK_CLOCK_STATUS,
           "current_yes_book_response_received_at_utc": BASE.isoformat()}
    result = runner.near_core_book_freshness(row, now=BASE + timedelta(seconds=offset), max_age_sec=10)
    assert result["near_core_book_freshness_status"] == status


def test_replaced_legacy_quote_does_not_inherit_response_clock():
    row = {"current_yes_book_response_received_at_utc": "2020-01-01T00:00:00Z",
           "current_yes_book_clock_lineage_status": runner.DIRECT_BOOK_CLOCK_STATUS,
           **producer.direct_book_clock_fields({"fetched_at_utc": BASE.isoformat()})}
    assert row["current_yes_book_response_received_at_utc"] is None
    assert row["current_yes_book_clock_lineage_status"] == "legacy_direct_fetch_clock_utc"


def test_rescore_completion_clock_and_direct_clock_lineage(monkeypatch, tmp_path):
    Clock.current = BASE
    monkeypatch.setattr(runner, "datetime", Clock)
    checkpoint = {"city": "Manila", "target_date": "2026-09-07", "checkpoint_key": "Manila|2026-09-07|14",
                  "model_probability_hold": .85, "current_yes_token_id": "token", "current_bracket": "27",
                  "decision_snapshot_ts_utc": BASE.isoformat(),
                  "current_yes_book_response_received_at_utc": "2020-01-01T00:00:00Z"}
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [checkpoint])
    monkeypatch.setattr(runner, "market_httpx_client", lambda *a, **kw: nullcontext(None))
    monkeypatch.setattr(producer, "observation_freshness_valid", lambda row: (True, "ok"))

    def fetch(*args):
        Clock.current = BASE + timedelta(seconds=5)
        return {"status": "ok", "fetched_at_utc": BASE.isoformat(),
                "request_started_at_utc": BASE.isoformat(), "response_received_at_utc": Clock.current.isoformat(),
                "clock_lineage_status": runner.DIRECT_BOOK_CLOCK_STATUS, "bids": [], "asks": []}

    monkeypatch.setattr(producer, "fetch_full_book", fetch)
    monkeypatch.setattr(runner, "evaluate_entry", lambda *a: {"reasons": ["non_positive_taker_ev"]})
    args = runner.parser().parse_args(["run"])
    rows = runner.continuous_market_rescores(args, tmp_path, now=BASE)
    assert runner.parse_utc(rows[0]["created_at_utc"]) == Clock.current
    assert runner.parse_utc(rows[0]["cycle_started_at_utc"]) == BASE
    assert rows[0]["current_yes_book_clock_lineage_status"] == runner.DIRECT_BOOK_CLOCK_STATUS
    assert runner.parse_utc(rows[0]["current_yes_book_response_received_at_utc"]) == Clock.current


def test_run_once_resamples_after_each_io_stage(monkeypatch, tmp_path):
    Clock.current = BASE
    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(runner, "assert_runtime_contract", lambda: None)
    monkeypatch.setattr(producer, "run_once", lambda args: {})
    monkeypatch.setattr(runner, "write_new_decision_packets", lambda *a: {"written_packets": []})
    monkeypatch.setattr(runner, "write_capture_demands", lambda *a, **kw: {})
    monkeypatch.setattr(runner, "near_core_control_failures", lambda args: [])
    for name in ["recover_journal_terminal_makers", "recover_near_core_journal_terminal_makers"]:
        monkeypatch.setattr(runner, name, lambda *a: 0)

    def rescore(*a, **kw):
        Clock.current += timedelta(seconds=6)
        return []

    monkeypatch.setattr(runner, "continuous_market_rescores", rescore)
    seen = []

    def stage(*a, **kw):
        assert kw["now"] == Clock.current
        seen.append(kw["now"])
        Clock.current += timedelta(seconds=4)
        return [], []

    monkeypatch.setattr(runner, "new_entry_plans", stage)
    monkeypatch.setattr(runner, "near_core_lifecycle_plans", lambda *a, **kw: (*stage(*a, **kw), set()))
    monkeypatch.setattr(runner, "near_core_entry_plans", stage)
    monkeypatch.setattr(runner, "collect_post_update_maker_revalidations", lambda *a, **kw: stage(*a, **kw)[0])
    monkeypatch.setattr(runner, "maker_lifecycle_plans", stage)
    monkeypatch.setattr(runner, "append_near_core_ledger", lambda *a: None)

    class ReachedExecution(Exception):
        pass

    def stop(*args):
        raise ReachedExecution

    monkeypatch.setattr(runner, "execute_plans", stop)
    args = runner.parser().parse_args(["run", "--output-dir", str(tmp_path)])
    with pytest.raises(ReachedExecution):
        runner.run_once(args)
    assert seen == [BASE + timedelta(seconds=s) for s in [6, 10, 14, 18, 22]]


def test_book_batch_has_bounded_concurrency_and_stable_candidate_order(monkeypatch, tmp_path):
    import threading

    gate = threading.Barrier(4)
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0, "requests": 0}
    checkpoints = {(f"City{i}", "2026-09-07"): {
        "city": f"City{i}", "target_date": "2026-09-07",
        "checkpoint_key": f"City{i}|2026-09-07|14", "current_yes_token_id": f"token{i}",
        "current_bracket": "27", "model_probability_hold": .85,
        "decision_snapshot_ts_utc": BASE.isoformat(),
    } for i in range(8)}
    monkeypatch.setattr(runner, "latest_scores_by_city_day", lambda path: checkpoints)
    monkeypatch.setattr(runner, "latest_weather_epochs", lambda path: {})
    monkeypatch.setattr(producer, "observation_freshness_valid", lambda row: (True, "ok"))
    monkeypatch.setattr(runner, "market_httpx_client", lambda *a, **kw: nullcontext(None))
    monkeypatch.setattr(runner, "evaluate_entry", lambda *a: {"eligible": False})

    def fetch(client, token):
        with lock:
            counts["active"] += 1
            counts["requests"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        gate.wait(timeout=3)
        with lock:
            counts["active"] -= 1
        return {"status": "ok", "fetched_at_utc": BASE.isoformat(),
                "bid": int(token[5:]) / 10, "asks": [], "bids": []}

    monkeypatch.setattr(producer, "fetch_full_book", fetch)
    args = runner.parser().parse_args(["run", "--continuous-market-max-books-per-cycle", "8"])
    rows = runner.continuous_market_rescores(args, tmp_path, now=BASE)
    assert counts == {"active": 0, "peak": 4, "requests": 8}
    assert [row["city"] for row in rows] == [f"City{i}" for i in range(8)]
    assert [row["current_yes_bid"] for row in rows] == [i / 10 for i in range(8)]
    for row in rows:
        assert row["market_rescore_id"] == "market-rescore-" + runner.stable_hash({
            "city": row["city"], "target_date": row["target_date"],
            "checkpoint_key": row["checkpoint_key"], "book": row["execution_book_snapshot_id"],
        })


@pytest.mark.parametrize("bid,ask,tick,cap,expected", [(.745,.86,.001,.841,.802),(.80,.84,.01,.9,.82),(.8,.84,.01,.805,.80),(.83,.84,.01,.9,.83),(.85,.84,.01,.9,0)])
def test_near_midpoint_is_tick_rounded_post_only_and_capped(bid,ask,tick,cap,expected):
    assert runner.near_core_maker_probe.midpoint_entry_price(best_bid=bid,best_ask=ask,tick_size=tick,price_cap=cap)==expected


@pytest.mark.parametrize("status", ["submitted", "unknown", "rejected", "cancelled"])
def test_immediate_handoff_waits_for_exact_cancel_and_rescores(monkeypatch,tmp_path,status):
    plan={"execution_action":"near_core_maker_cancel_core_supersession","source_order_id":"old","city":"Manila","target_date":"2026-09-07"}
    monkeypatch.setattr(runner,"execution_journal_order_states",lambda p:{"old":{"status":status}})
    monkeypatch.setattr(runner,"recover_near_core_journal_terminal_makers",lambda p:0)
    calls=[]
    def rescore(*a,**kw):
        assert kw["city_days"]=={("Manila","2026-09-07")}
        calls.append("fresh_score");return [{"fresh":True}]
    monkeypatch.setattr(runner,"continuous_market_rescores",rescore)
    def entry(*a,**kw):
        assert kw["market_rescores"]==[{"fresh":True}]
        calls.append("dedup_and_budget");return [{"city":"Manila","target_date":"2026-09-07"}],[]
    monkeypatch.setattr(runner,"new_entry_plans",entry)
    monkeypatch.setattr(runner,"execute_plans",lambda *a:(calls.append("submit") or {"exit_code":0}))
    result=runner.immediate_core_handoff(runner.parser().parse_args(["run"]),tmp_path,cancel_plans=[plan])
    assert calls==(["fresh_score","dedup_and_budget","submit"] if status=="cancelled" else [])
    assert result["entry_plans"]==(1 if status=="cancelled" else 0)


def test_immediate_handoff_all_roots_must_be_terminal(monkeypatch,tmp_path):
    monkeypatch.setattr(runner,"execution_journal_order_states",lambda p:{"a":{"status":"cancelled"}})
    plans=[{"execution_action":"near_core_maker_cancel_core_supersession","source_order_id":oid,"city":"Manila","target_date":"2026-09-07"} for oid in ["a","b"]]
    assert runner.immediate_core_handoff(None,tmp_path,cancel_plans=plans)["target_city_days"]==[]
