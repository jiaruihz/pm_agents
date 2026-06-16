#!/usr/bin/env python3
"""Execution and model-optimization note for theta_current_yes_tiny_live_v1.

This report is intentionally local/research-only.  It does not query N100,
change live config, cancel orders, or submit orders.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-execution-model-opt-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-execution-model-opt-v1.md"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
DB = ROOT / "runtime/weather.db"


# Captured in the parent live-control thread on 2026-06-16 after the orders were
# accepted by CLOB.  These are submitted order rows, not confirmed fills.
LIVE_ORDERS = [
    {
        "city": "Shanghai",
        "target_date": "2026-06-16",
        "created_at_utc": "2026-06-16T05:17:02+00:00",
        "bracket": "25",
        "side": "BUY_YES",
        "posted_price": 0.83,
        "posted_notional": 5.0,
        "posted_shares": 6.024096,
        "model_p_yes": 0.950571,
        "model_edge_at_posted": 0.120571,
        "place_status": "live",
        "order_id": "0x389b62ad6c31c18768a9cd02ac5faab168bd54d85e2299e14155705226078f4c",
    },
    {
        "city": "Taipei",
        "target_date": "2026-06-16",
        "created_at_utc": "2026-06-16T05:48:51+00:00",
        "bracket": "34",
        "side": "BUY_YES",
        "posted_price": 0.794,
        "posted_notional": 5.0,
        "posted_shares": 6.297229,
        "model_p_yes": 0.858077,
        "model_edge_at_posted": 0.064077,
        "place_status": "live",
        "order_id": "0xa102f2dd6149388de6f1b0b053753bfcc0673dc5b32da2ba12807135f0eb1f5e",
    },
    {
        "city": "Chongqing",
        "target_date": "2026-06-16",
        "created_at_utc": "2026-06-16T07:09:27+00:00",
        "bracket": "29",
        "side": "BUY_YES",
        "posted_price": 0.82,
        "posted_notional": 5.0,
        "posted_shares": 6.097561,
        "model_p_yes": 0.8792,
        "model_edge_at_posted": 0.0592,
        "place_status": "live",
        "order_id": "0x0e42829d39f47360a8c265a6a74d6ef4e5f25c0fcd55cd75dc4b581f73afa8a7",
    },
]


# Public CLOB /book snapshot captured after the live orders were observed resting.
CURRENT_BOOK_CHECKS = {
    "Shanghai": {
        "best_bid": 0.969,
        "best_ask": 0.99,
        "best_ask_size": 992.86,
        "top_asks": [[0.99, 992.86], [0.992, 100.49], [0.995, 469.86], [0.997, 60.0], [0.998, 193.61]],
    },
    "Taipei": {
        "best_bid": 0.998,
        "best_ask": None,
        "best_ask_size": 0.0,
        "top_asks": [],
    },
    "Chongqing": {
        "best_bid": 0.992,
        "best_ask": 0.998,
        "best_ask_size": 192.28,
        "top_asks": [[0.998, 192.28], [0.999, 724.2]],
    },
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_self_check() -> dict[str, Any]:
    out: dict[str, Any] = {}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    try:
        out["fact_built_at_utc"] = conn.execute("SELECT MAX(fact_built_at_utc) v FROM fact_trades").fetchone()["v"]
        out["trade_class"] = [dict(r) for r in conn.execute("SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class")]
        out["settlement_status"] = [
            dict(r) for r in conn.execute("SELECT settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status")
        ]
        out["signal_coverage"] = dict(
            conn.execute("SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled FROM fact_signal_candidates").fetchone()
        )
        out["clob_orders_fills"] = [
            dict(r)
            for r in conn.execute(
                "SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status"
            )
        ]
    finally:
        conn.close()
    return out


def fixed_notional_shares(notional: float, price: float | None) -> float | None:
    if price is None or price <= 0:
        return None
    return notional / price


def enrich_orders() -> list[dict[str, Any]]:
    rows = []
    for order in LIVE_ORDERS:
        book = CURRENT_BOOK_CHECKS[order["city"]]
        best_ask = book["best_ask"]
        row = {**order, **{f"book_{k}": v for k, v in book.items() if k != "top_asks"}}
        if best_ask is None:
            row.update(
                {
                    "true_taker_possible_now": False,
                    "edge_at_current_ask": None,
                    "same_shares_cost_at_current_ask": None,
                    "extra_cost_same_shares_vs_original": None,
                    "fixed_5usd_shares_at_current_ask": None,
                    "share_loss_fixed_notional": None,
                    "edge_change_vs_posted": None,
                }
            )
        else:
            same_shares_cost = order["posted_shares"] * best_ask
            fixed_shares = fixed_notional_shares(order["posted_notional"], best_ask)
            edge_at_current = order["model_p_yes"] - best_ask
            row.update(
                {
                    "true_taker_possible_now": True,
                    "edge_at_current_ask": edge_at_current,
                    "same_shares_cost_at_current_ask": same_shares_cost,
                    "extra_cost_same_shares_vs_original": same_shares_cost - order["posted_notional"],
                    "fixed_5usd_shares_at_current_ask": fixed_shares,
                    "share_loss_fixed_notional": order["posted_shares"] - fixed_shares,
                    "edge_change_vs_posted": edge_at_current - order["model_edge_at_posted"],
                }
            )
        rows.append(row)
    return rows


def load_context() -> dict[str, Any]:
    model = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    stations = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))["stations"]
    return {
        "model": {
            "artifact_type": model.get("artifact_type"),
            "strategy_id": model.get("strategy_id"),
            "train_rows": model.get("train_rows"),
            "numeric_feature_count": len(model.get("numeric_features", [])),
            "categorical_features": model.get("categorical_features", []),
            "city_category_count": len((model.get("categories") or [[]])[0]),
        },
        "city_pool": {
            "count": len(stations),
            "cities": sorted(x["city"] for x in stations),
            "units": {unit: sum(1 for x in stations if x["unit"] == unit) for unit in sorted({x["unit"] for x in stations})},
        },
    }


def pct(x: float | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{100 * x:+.1f}%"


def dollars(x: float | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"${x:.2f}"


def write_report(payload: dict[str, Any]) -> None:
    rows = payload["orders"]
    table = []
    for r in rows:
        table.append(
            "| {city} | {posted:.3f} | {status} | {ask} | {edge0} | {edge1} | {extra} | {loss} |".format(
                city=r["city"],
                posted=r["posted_price"],
                status=r["place_status"],
                ask="NA" if r["book_best_ask"] is None else f"{r['book_best_ask']:.3f}",
                edge0=pct(r["model_edge_at_posted"]),
                edge1=pct(r["edge_at_current_ask"]),
                extra=dollars(r["extra_cost_same_shares_vs_original"]),
                loss="NA" if r["share_loss_fixed_notional"] is None else f"{r['share_loss_fixed_notional']:.3f}",
            )
        )

    md = f"""# Theta Current YES Execution / Model Optimization v1

Status: research_only
Generated: {payload['generated_at_utc']}
Target metric: `theta_current_yes_execution_loss` = today's submitted current-YES orders where snapshot-ask limit became a resting CLOB order, and the cost of converting that intent into true taker execution.

## Data Snapshot

- Evidence layer: raw live order response copied from the live-control thread + public CLOB book check + local model artifacts. This is not settled PnL and not wallet cashflow.
- Row grain: one row = one submitted live order (`exchange_response.place.status=live`), not one fill.
- Local DB self-check fact_built_at_utc: `{payload['db_self_check']['fact_built_at_utc']}`.
- fact_trades by class: `{payload['db_self_check']['trade_class']}`.
- fact_trades settlement: `{payload['db_self_check']['settlement_status']}`.
- fact_signal_candidates coverage: `{payload['db_self_check']['signal_coverage']}`.
- CLOB orders/fills join: `{payload['db_self_check']['clob_orders_fills']}`.

## Trading Recommendation

Do not convert the current live branch into unconditional true-taker.

The correct patch direction is: refresh CLOB book immediately before placement, recompute edge at the fresh best ask, and only cross if `p_yes_win - fresh_ask >= 0.05` after a small slippage buffer. Otherwise keep the order passive or skip. For today's examples, chasing the current ask would have destroyed the modeled edge on Shanghai and Chongqing; Taipei had no ask available in the checked book.

## Today's Orders

| city | posted limit | CLOB status | checked best ask | edge at posted | edge at checked ask | extra cost for same shares | shares lost if still $5 |
|---|---:|---|---:|---:|---:|---:|---:|
{chr(10).join(table)}

Interpretation:

- These orders did not fail; they were accepted as live/resting orders.
- The runner used the snapshot ask as the limit price. If the real CLOB ask moved up before submission, the order becomes a bid below the ask instead of an immediate fill.
- True taker is only good if the fresh executable ask is still close to the snapshot ask. When the ask has jumped to 0.99+, buying just to get filled turns a positive modeled edge into negative edge.

## Why Passive Became Unfilled

1. The signal snapshot and order placement are not atomic.
2. The current runner writes a plan using `yes_current_ask` from the weather snapshot.
3. The executor is allowed to take, but it does not raise the limit to the fresh CLOB ask for this branch.
4. If market makers reprice the current winner toward 0.99 before the order lands, the old 0.79-0.83 limit rests.

## Model Optimization Directions

1. Split the problem into two models:
   - `p_yes_win`: probability the current bracket settles YES.
   - `p_fill_or_edge_survives`: probability the edge is still executable by the time the order reaches CLOB.

2. Add execution features:
   - snapshot age in seconds,
   - latest live book best ask/bid,
   - ask jump from snapshot ask to fresh ask,
   - top-of-book ask depth,
   - market near-certain flag (`fresh_ask >= 0.95` or no ask),
   - local-minute bucket inside 13-15.

3. Recalibrate high probabilities:
   - today's p values were high enough for the signal model, but execution at 0.99 would be negative edge.
   - calibration should be checked by city/hour/price bucket, especially `yes_ask >= 0.80`.

4. City pool refinement:
   - Keep source-aligned cities, but add a separate execution-quality gate by city.
   - Asian afternoon markets may reprice fast near the close; this should be measured before raising size.

5. Candidate decision change:
   - Replace `snapshot_best_ask_taker` with `fresh_book_guarded_taker`.
   - Recommended logic: if `fresh_ask <= snapshot_ask + 0.02` and `p_yes_win - fresh_ask >= 0.05`, cross; else do not chase.

## Current Model Context

- Model artifact: `{payload['context']['model']['artifact_type']}`.
- Train rows: {payload['context']['model']['train_rows']}.
- Numeric features: {payload['context']['model']['numeric_feature_count']}.
- City categories: {payload['context']['model']['city_category_count']}.
- City pool count: {payload['context']['city_pool']['count']}; units: {payload['context']['city_pool']['units']}.

## Verdict

The weather/no-reheat model may still be right, but today's bottleneck is execution, not temperature logic. A taker upgrade must be conditional on a fresh executable ask. Unconditional taker would likely overpay exactly when the market has already repriced the bracket to near-certain.
"""
    OUT_MD.write_text(md, encoding="utf-8")


def main() -> int:
    payload = {
        "generated_at_utc": now_utc(),
        "db_self_check": db_self_check(),
        "context": load_context(),
        "orders": enrich_orders(),
        "recommendation": {
            "action": "do_not_use_unconditional_taker",
            "patch_direction": "fresh_book_guarded_taker",
            "guard": "cross only when fresh_ask <= snapshot_ask + 0.02 and p_yes_win - fresh_ask >= 0.05",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "orders": len(payload["orders"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
