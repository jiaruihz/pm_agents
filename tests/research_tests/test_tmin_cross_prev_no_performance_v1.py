from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.analysis.tmin.evaluate_tmin_cross_prev_no_shadow_v1 import evaluate


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _candidate(*, suffix: str, date: str, ts: str, ask: float) -> dict:
    return {
        "candidate_id": f"candidate-{suffix}",
        "checkpoint_id": f"checkpoint-{suffix}",
        "city": "Seoul",
        "target_date": date,
        "decision_ts_utc": ts,
        "condition_id": f"condition-{suffix}",
        "token_id": f"token-{suffix}",
        "bracket": "24",
        "input_refs": [{"event_key": f"Seoul|{date}|amos_runway|23"}],
        "metadata": {"raw_no_best_ask": ask, "cold_cross_margin_native": 0.2},
    }


def _quote(
    *, suffix: str, date: str, ts: str, ask: float, size: float,
    bid: float | None = None, bid_size: float | None = None,
) -> dict:
    return {
        "event_key": f"Seoul|{date}|amos_runway|23",
        "ts_utc": ts,
        "quotes": {
            "t_minus_1": {
                "no": {
                    "token_id": f"token-{suffix}",
                    "fresh_best_ask": ask,
                    "fresh_ask_size": size,
                    "fresh_best_bid": bid,
                    "fresh_bid_size": bid_size,
                    "fresh_status": "ok",
                }
            }
        },
    }


def _event(*, condition: str, closed: bool, no_wins: bool) -> list[dict]:
    return [
        {
            "closed": closed,
            "markets": [
                {
                    "conditionId": condition,
                    "closed": closed,
                    "outcomePrices": ["0", "1"] if no_wins else ["1", "0"],
                }
            ],
        }
    ]


def test_evaluator_requires_closed_settlement_and_freezes_cap90(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    gamma = tmp_path / "gamma"
    gamma.mkdir()
    rows = [
        _candidate(suffix="a", date="2026-07-15", ts="2026-07-14T16:00:00Z", ask=0.80),
        _candidate(suffix="b", date="2026-07-15", ts="2026-07-14T17:00:00Z", ask=0.70),
        _candidate(suffix="c", date="2026-07-16", ts="2026-07-15T16:00:00Z", ask=0.99),
    ]
    _write_jsonl(candidates, rows)
    _write_jsonl(
        quotes,
        [
            _quote(suffix="a", date="2026-07-15", ts="2026-07-14T16:00:00Z", ask=0.80, size=5),
            _quote(suffix="a", date="2026-07-15", ts="2026-07-14T16:10:00Z", ask=0.80, size=5, bid=0.70, bid_size=5),
            _quote(suffix="b", date="2026-07-15", ts="2026-07-14T17:00:00Z", ask=0.70, size=10),
            _quote(suffix="c", date="2026-07-16", ts="2026-07-15T16:00:00Z", ask=0.99, size=10),
        ],
    )
    for suffix, date, closed in (("a", "2026-07-15", True), ("b", "2026-07-15", True), ("c", "2026-07-16", False)):
        day = pd.Timestamp(date)
        path = gamma / f"lowest-temperature-in-seoul-on-{day.strftime('%B').lower()}-{day.day}-{day.year}.json"
        payload = _event(condition=f"condition-{suffix}", closed=closed, no_wins=True)
        if path.exists():
            existing = json.loads(path.read_text())
            existing[0]["markets"].extend(payload[0]["markets"])
            existing[0]["closed"] = existing[0]["closed"] and closed
            payload = existing
        path.write_text(json.dumps(payload), encoding="utf-8")

    frame, summary = evaluate(candidates_path=candidates, quotes_path=quotes, gamma_root=gamma)

    assert summary["evidence_funnel"]["closed_binary_settlement"] == 2
    assert summary["evidence_funnel"]["settled_and_5_share_executable"] == 2
    assert summary["frozen_policy"]["historical_replay"]["rows"] == 1
    assert summary["taker_repricing_exit"]["10m"]["all_entries"]["rows"] == 1
    assert summary["taker_repricing_exit"]["10m"]["all_entries"]["pnl_usd"] < 0
    selected = frame[frame["frozen_policy_selected"]]
    assert selected["candidate_id"].tolist() == ["candidate-a"]
    open_row = frame[frame["candidate_id"].eq("candidate-c")].iloc[0]
    assert open_row["settlement_status"] == "open_event_not_settlement"
    assert open_row["settlement_no_label"] != open_row["settlement_no_label"]
