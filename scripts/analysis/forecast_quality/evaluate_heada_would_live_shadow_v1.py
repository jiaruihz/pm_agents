#!/usr/bin/env python3
"""Evaluate HeadA would-live shadow entries against canonical settlements.

The primary result uses the fresh best ask captured at the first eligible
shadow decision and the official Weather taker fee curve.  The maker-limit
counterfactual is reported separately because a shadow journal cannot prove
queue position or maker fill probability.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_feature_layer.market import bracket_distance_features


JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/would_live_entries.jsonl"
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_DIR_DEFAULT = ROOT / "docs/analysis/2026-07/generated/heada_would_live_shadow_v1"
RNG_SEED = 20260724


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL_DEFAULT)
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def weather_taker_fee_per_share(price: float) -> float:
    """Official Weather fee curve used by the HeadA runner."""
    return 0.05 * price * (1.0 - price)


def load_settlements(db: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, unit, final_price, settlement_status,
               source_system, source_path, created_at_utc
        FROM settlement_outcomes
        """
    ).fetchall()
    conn.close()
    return {
        (str(row["city"]).lower(), str(row["target_date"]), str(row["bracket"])): dict(row)
        for row in rows
    }


def bootstrap_roi(daily: list[dict[str, float]], *, samples: int, value_key: str) -> tuple[float | None, float | None]:
    if len(daily) < 2:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = np.array([row["taker_cost_usd"] for row in daily], dtype=float)
    values = np.array([row[value_key] for row in daily], dtype=float)
    results = []
    for _ in range(samples):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost > 0:
            results.append(float(values[idx].sum() / cost))
    low, high = np.quantile(results, [0.025, 0.975])
    return (float(low), float(high))


def probability_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    if not rows:
        return {}
    actual = np.array([float(row["win"]) for row in rows], dtype=float)
    model = np.clip(np.array([float(row["model_p_yes"]) for row in rows], dtype=float), 1e-6, 1 - 1e-6)
    market = np.clip(np.array([float(row["best_ask"]) for row in rows], dtype=float), 1e-6, 1 - 1e-6)
    # AUC via average ranks, including tied predicted values.
    order = np.argsort(model)
    ranks = np.empty(len(model), dtype=float)
    ranks[order] = np.arange(1, len(model) + 1, dtype=float)
    for value in np.unique(model):
        tied = model == value
        ranks[tied] = ranks[tied].mean()
    positives = int(actual.sum())
    negatives = len(actual) - positives
    auc = None if positives == 0 or negatives == 0 else float((ranks[actual == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))
    return {
        "actual_win_rate": float(actual.mean()),
        "mean_model_p_yes": float(model.mean()),
        "mean_market_ask": float(market.mean()),
        "model_brier": float(np.mean((model - actual) ** 2)),
        "market_brier": float(np.mean((market - actual) ** 2)),
        "model_logloss": float(-np.mean(actual * np.log(model) + (1 - actual) * np.log(1 - model))),
        "market_logloss": float(-np.mean(actual * np.log(market) + (1 - actual) * np.log(1 - market))),
        "model_auc": auc,
    }


def segment_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "missing")].append(row)
    output = []
    for value, group in groups.items():
        cost = sum(float(row["taker_cost_usd"]) for row in group)
        pnl = sum(float(row["taker_pnl_usd"]) for row in group)
        output.append(
            {
                key: value,
                "rows": len(group),
                "wins": sum(int(row["win"]) for row in group),
                "taker_cost_usd": cost,
                "taker_pnl_usd": pnl,
                "taker_roi": pnl / cost if cost else None,
            }
        )
    return sorted(output, key=lambda row: (-int(row["rows"]), str(row[key])))


def summarize(rows: list[dict[str, Any]], *, samples: int) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    pending = [row for row in rows if not row["settled"]]
    daily_map: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "rows": 0.0,
            "wins": 0.0,
            "taker_cost_usd": 0.0,
            "taker_fee_usd": 0.0,
            "taker_pnl_usd": 0.0,
            "market_baseline_pnl_usd": 0.0,
            "maker_limit_cost_usd": 0.0,
            "maker_limit_pnl_usd": 0.0,
        }
    )
    for row in settled:
        day = daily_map[row["target_date"]]
        day["rows"] += 1
        day["wins"] += row["win"]
        for key in (
            "taker_cost_usd",
            "taker_fee_usd",
            "taker_pnl_usd",
            "market_baseline_pnl_usd",
            "maker_limit_cost_usd",
            "maker_limit_pnl_usd",
        ):
            day[key] += float(row[key])

    daily = []
    for target_date, item in sorted(daily_map.items()):
        item = {"target_date": target_date, **item}
        item["taker_roi"] = item["taker_pnl_usd"] / item["taker_cost_usd"]
        item["market_baseline_roi"] = item["market_baseline_pnl_usd"] / item["taker_cost_usd"]
        item["excess_pnl_usd"] = item["taker_pnl_usd"] - item["market_baseline_pnl_usd"]
        item["excess_roi"] = item["excess_pnl_usd"] / item["taker_cost_usd"]
        daily.append(item)

    def total(key: str) -> float:
        return sum(float(row[key]) for row in settled)

    cost = total("taker_cost_usd")
    pnl = total("taker_pnl_usd")
    baseline_pnl = total("market_baseline_pnl_usd")
    excess_pnl = pnl - baseline_pnl
    top_removed = sorted(settled, key=lambda row: float(row["taker_pnl_usd"],), reverse=True)[1:]
    top_removed_cost = sum(float(row["taker_cost_usd"]) for row in top_removed)
    top_removed_pnl = sum(float(row["taker_pnl_usd"]) for row in top_removed)
    roi_ci = bootstrap_roi(daily, samples=samples, value_key="taker_pnl_usd")
    excess_ci = bootstrap_roi(daily, samples=samples, value_key="excess_pnl_usd")

    return {
        "entries": len(rows),
        "settled_entries": len(settled),
        "unsettled_entries": len(pending),
        "target_dates": len(daily),
        "cities": len({row["city"] for row in settled}),
        "wins": int(sum(int(row["win"]) for row in settled)),
        "win_rate": (sum(int(row["win"]) for row in settled) / len(settled)) if settled else None,
        "avg_best_ask": (sum(float(row["best_ask"]) for row in settled) / len(settled)) if settled else None,
        "taker_cost_usd": cost,
        "taker_fee_usd": total("taker_fee_usd"),
        "taker_pnl_usd": pnl,
        "taker_roi": pnl / cost if cost else None,
        "taker_roi_ci_95": roi_ci,
        "market_baseline_pnl_usd": baseline_pnl,
        "market_baseline_roi": baseline_pnl / cost if cost else None,
        "excess_pnl_usd": excess_pnl,
        "excess_roi": excess_pnl / cost if cost else None,
        "excess_roi_ci_95": excess_ci,
        "maker_limit_counterfactual_cost_usd": total("maker_limit_cost_usd"),
        "maker_limit_counterfactual_pnl_usd": total("maker_limit_pnl_usd"),
        "maker_limit_counterfactual_roi": (
            total("maker_limit_pnl_usd") / total("maker_limit_cost_usd") if total("maker_limit_cost_usd") else None
        ),
        "losing_days": sum(1 for row in daily if row["taker_pnl_usd"] < 0),
        "roi_le_minus_50_days": sum(1 for row in daily if row["taker_roi"] <= -0.5),
        "max_daily_loss_usd": min((row["taker_pnl_usd"] for row in daily), default=None),
        "top_trade_removed_roi": top_removed_pnl / top_removed_cost if top_removed_cost else None,
        "probability_metrics": probability_metrics(settled),
        "by_forecast_source": segment_summary(settled, "forecast_source"),
        "daily": daily,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row.get(key) for key in fields} for row in rows])


def main() -> int:
    args = parse_args()
    source = [row for row in read_jsonl(args.journal) if bool(row.get("would_live_entry"))]
    deduped: dict[str, dict[str, Any]] = {}
    for row in source:
        key = str(row.get("signal_id") or row.get("condition_id"))
        deduped.setdefault(key, row)
    settlements = load_settlements(args.db)
    evaluated: list[dict[str, Any]] = []
    for row in deduped.values():
        target_date = str(row["target_date"])
        city = str(row["city"])
        bracket = str(row["bracket"])
        settlement = settlements.get((city.lower(), target_date, bracket))
        best_ask = float(row["would_live_best_ask"])
        shares = float(row["would_live_shares"])
        maker_limit = float(row["would_live_maker_limit_price"])
        geometry = bracket_distance_features(
            {
                "bracket": bracket,
                "forecast_max_native": row.get("forecast_max_native"),
            }
        )
        dist = geometry["forecast_to_bracket_low_native"]
        fee = weather_taker_fee_per_share(best_ask) * shares
        taker_cost = best_ask * shares + fee
        maker_cost = maker_limit * shares
        settled = bool(settlement and settlement["settlement_status"] == "settled" and settlement["final_price"] in (0.0, 1.0))
        final_yes = float(settlement["final_price"]) if settled else None
        payout = shares * final_yes if settled else None
        evaluated.append(
            {
                "created_at_utc": row.get("created_at_utc"),
                "target_date": target_date,
                "city": city,
                "bracket": bracket,
                "unit": row.get("unit"),
                "condition_id": row.get("condition_id"),
                "signal_id": row.get("signal_id"),
                "forecast_source": row.get("forecast_source"),
                "forecast_max_native": row.get("forecast_max_native"),
                "bracket_low_native": geometry["bracket_low_native"],
                "bracket_high_native": geometry["bracket_high_native"],
                "bracket_distance_available": geometry["bracket_distance_available"],
                "forecast_to_bracket_low_native": dist,
                "intended_hot_tail_dist_gt0": bool(
                    geometry["bracket_distance_available"] and dist is not None and float(dist) > 0.0
                ),
                "model_p_yes": row.get("model_p_yes"),
                "best_ask": best_ask,
                "best_ask_size": row.get("would_live_best_ask_size"),
                "shares": shares,
                "maker_limit_price": maker_limit,
                "settled": settled,
                "settlement_status": settlement.get("settlement_status") if settlement else "coverage_missing",
                "settlement_final_yes": final_yes,
                "win": int(final_yes == 1.0) if settled else None,
                "taker_fee_usd": fee,
                "taker_cost_usd": taker_cost,
                "taker_pnl_usd": (payout - taker_cost) if settled else None,
                "market_baseline_pnl_usd": -fee if settled else None,
                "maker_limit_cost_usd": maker_cost,
                "maker_limit_pnl_usd": (payout - maker_cost) if settled else None,
            }
        )

    captured_summary = summarize(evaluated, samples=args.bootstrap_samples)
    intended_hot_tail_rows = [row for row in evaluated if row["intended_hot_tail_dist_gt0"]]
    intended_hot_tail_summary = summarize(intended_hot_tail_rows, samples=args.bootstrap_samples)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "entries.csv", evaluated)
    write_csv(args.out_dir / "daily.csv", captured_summary["daily"])
    write_csv(args.out_dir / "intended_hot_tail_daily.csv", intended_hot_tail_summary["daily"])
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
        "trade_class": "zero_notional_shadow_would_live",
        "primary_execution_assumption": "taker_at_fresh_best_ask_plus_official_weather_fee",
        "maker_counterfactual_caveat": "No maker-fill or queue assumption; maker-limit figures are price-only counterfactuals.",
        "journal": str(args.journal.relative_to(ROOT)),
        "db": str(args.db.relative_to(ROOT)),
        "source_rows": len(source),
        "deduped_rows": len(deduped),
        "captured_summary_before_distance_enforcement": captured_summary,
        "intended_hot_tail_dist_gt0_summary": intended_hot_tail_summary,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
