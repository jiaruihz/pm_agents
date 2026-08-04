#!/usr/bin/env python3
"""Evaluate settled low-price YES lottery shadow candidates via CLOB markets.

This script does not write canonical settlement tables. It reads the
zero-notional shadow journal and overlays current Polymarket CLOB market
settlement state for forward research only.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[3]
JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/shadow_candidates.jsonl"
OUT_DIR_DEFAULT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_shadow_v1"
CLOB_BASE_DEFAULT = "https://clob.polymarket.com"
RNG_SEED = 20260701


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--clob-base", default=CLOB_BASE_DEFAULT)
    parser.add_argument("--sleep-sec", type=float, default=0.15)
    parser.add_argument("--bootstrap-samples", type=int, default=20000)
    return parser.parse_args()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_journal(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    return session


def fetch_market(session: requests.Session, base: str, condition_id: str) -> dict[str, Any]:
    response = session.get(
        f"{base.rstrip('/')}/markets/{condition_id}",
        timeout=20,
        headers={"Accept": "application/json", "User-Agent": "pm-agents-research/1.0"},
    )
    response.raise_for_status()
    market = response.json()
    yes_price = None
    yes_winner = None
    for token in market.get("tokens") or []:
        if str(token.get("outcome") or "").lower() == "yes":
            if token.get("price") is not None:
                yes_price = float(token["price"])
            if token.get("winner") is not None:
                yes_winner = bool(token["winner"])
    return {
        "closed": bool(market.get("closed")),
        "active": bool(market.get("active")),
        "question": market.get("question"),
        "yes_price": yes_price,
        "yes_winner": yes_winner,
    }


def block_bootstrap_ci(daily: list[dict[str, Any]], n_boot: int) -> tuple[float | None, float | None]:
    if len(daily) < 2:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = np.array([float(row["cost"]) for row in daily])
    pnls = np.array([float(row["pnl"]) for row in daily])
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    q = np.quantile(vals, [0.025, 0.975])
    return (float(q[0]), float(q[1]))


def summarize(rows: list[dict[str, Any]], n_boot: int) -> dict[str, Any]:
    settled = [row for row in rows if row.get("is_settled")]
    if not settled:
        return {"rows": 0, "settled_rows": 0}

    by_date: dict[str, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "wins": 0, "cost": 0.0, "pnl": 0.0})
    for row in settled:
        item = by_date[str(row["target_date"])]
        item["rows"] += 1
        item["wins"] += int(row["win"])
        item["cost"] += float(row["cost"])
        item["pnl"] += float(row["pnl"])

    daily = [{"target_date": key, **value, "roi": value["pnl"] / value["cost"]} for key, value in sorted(by_date.items())]
    cost = sum(float(row["cost"]) for row in settled)
    pnl = sum(float(row["pnl"]) for row in settled)
    winners = sorted([row for row in settled if row["win"]], key=lambda row: float(row["pnl"]), reverse=True)
    top_removed = sorted(settled, key=lambda row: float(row["pnl"]), reverse=True)[1:]
    top_removed_cost = sum(float(row["cost"]) for row in top_removed)
    top_removed_pnl = sum(float(row["pnl"]) for row in top_removed)
    ci_low, ci_high = block_bootstrap_ci(daily, n_boot)

    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "open_rows": len(rows) - len(settled),
        "dates": len(daily),
        "cities": len({row["city"] for row in settled}),
        "avg_ask": sum(float(row["evaluation_ask"]) for row in settled) / len(settled),
        "win_rate": sum(int(row["win"]) for row in settled) / len(settled),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "losing_days": sum(1 for row in daily if row["pnl"] < 0),
        "roi_le_minus_50_days": sum(1 for row in daily if row["roi"] <= -0.50),
        "max_daily_loss": min(float(row["pnl"]) for row in daily),
        "top_trade_removed_roi": top_removed_pnl / top_removed_cost if top_removed_cost else None,
        "top_trade_removed_pnl": top_removed_pnl,
        "winners": [
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "bracket": row["bracket"],
                "ask": row["evaluation_ask"],
                "pnl": row["pnl"],
            }
            for row in winners
        ],
        "daily": daily,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = sorted({key for row in rows for key in row.keys() if not isinstance(row.get(key), (dict, list))})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in cols})


def main() -> int:
    args = parse_args()
    journal_path = Path(args.journal)
    out_dir = Path(args.out_dir)
    rows = load_journal(journal_path)
    session = make_session()

    market_cache: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row["condition_id"])
        if cid in market_cache:
            continue
        try:
            market_cache[cid] = fetch_market(session, args.clob_base, cid)
        except Exception as exc:  # external API diagnostic, not canonical ingest
            errors.append({"condition_id": cid, "error": str(exc)})
        time.sleep(max(0.0, args.sleep_sec))

    evaluated: list[dict[str, Any]] = []
    for row in rows:
        market = market_cache.get(str(row["condition_id"]))
        item = dict(row)
        item["clob_closed"] = bool(market.get("closed")) if market else False
        item["clob_yes_price"] = market.get("yes_price") if market else None
        item["clob_yes_winner"] = market.get("yes_winner") if market else None
        item["is_settled"] = item["clob_closed"] and item["clob_yes_price"] in (0.0, 1.0)
        if item["is_settled"]:
            ask = float(
                item.get("ask")
                or item.get("would_live_best_ask")
                or item.get("fresh_best_ask")
                or item.get("decision_entry_price")
            )
            shares = item.get("would_live_shares") or item.get("planned_shares")
            if shares is not None:
                shares = float(shares)
                fee_per_share = 0.05 * ask * (1.0 - ask)
                cost = shares * (ask + fee_per_share)
                payout = shares
                item["evaluation_sizing"] = "fixed_shares_weather_taker_fee"
                item["evaluation_shares"] = shares
                item["evaluation_fee_per_share"] = fee_per_share
            else:
                cost = float(item.get("hypothetical_notional_usd") or 5.0)
                payout = cost / ask
                item["evaluation_sizing"] = "legacy_fixed_notional_no_fee"
                item["evaluation_shares"] = payout
                item["evaluation_fee_per_share"] = 0.0
            item["evaluation_ask"] = ask
            item["cost"] = cost
            item["win"] = int(float(item["clob_yes_price"]) == 1.0)
            item["pnl"] = float(item["clob_yes_price"]) * payout - cost
            item["roi"] = item["pnl"] / cost
        evaluated.append(item)

    summary = {
        "journal": rel(journal_path),
        "clob_base": args.clob_base,
        "market_api_calls": len(market_cache),
        "market_api_errors": errors,
        "summary": summarize(evaluated, args.bootstrap_samples),
        "artifacts": {
            "details_csv": rel(out_dir / "settlement_details.csv"),
            "daily_csv": rel(out_dir / "settlement_daily.csv"),
            "summary_json": rel(out_dir / "settlement_summary.json"),
        },
    }

    daily_rows = summary["summary"].get("daily", [])
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "settlement_details.csv", evaluated)
    write_csv(out_dir / "settlement_daily.csv", daily_rows)
    (out_dir / "settlement_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
