#!/usr/bin/env python3
"""Evaluate the zero-notional Tmin cross/previous-warmer-NO shadow.

The evaluator freezes the two append-only runtime journals at read time, joins
each candidate to its exact quote checkpoint, and accepts a settlement label
only when both the Gamma event and matched market are closed with a binary
outcome.  Open near-binary markets are deliberately not settlement truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = "weather_tmin_cross_prev_no_performance_v1"
FROZEN_POLICY_ID = "tmin_cross_prev_no_cap90_first_cityday_v1"
FEE_RATE = 0.05
MIN_SHARES = 5.0
MAX_NO_ASK = 0.90
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260812


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _snapshot_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        payload = handle.read(size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line]
    return rows, {
        "path": str(path),
        "snapshot_size_bytes": size,
        "complete_size_bytes": complete_size,
        "complete_sha256": hashlib.sha256(complete).hexdigest(),
        "rows": len(rows),
    }


def _event_path(root: Path, city: str, target_date: str) -> Path:
    day = pd.Timestamp(target_date)
    slug = (
        f"lowest-temperature-in-{city.lower()}-on-"
        f"{day.strftime('%B').lower()}-{day.day}-{day.year}.json"
    )
    return root / slug


def _settlement_label(
    *, root: Path, city: str, target_date: str, condition_id: str, side: str = "no"
) -> tuple[int | None, str, str | None]:
    path = _event_path(root, city, target_date)
    if not path.exists():
        return None, "missing_gamma_event", None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None, "invalid_gamma_event", str(path)
    event = payload[0]
    if not bool(event.get("closed")):
        return None, "open_event_not_settlement", str(path)
    matches = [
        market
        for market in event.get("markets") or []
        if str(market.get("conditionId") or market.get("condition_id") or "")
        == condition_id
    ]
    if len(matches) != 1:
        return None, f"condition_match_count_{len(matches)}", str(path)
    market = matches[0]
    if not bool(market.get("closed")):
        return None, "open_market_not_settlement", str(path)
    prices = [float(value) for value in _json_list(market.get("outcomePrices"))]
    if len(prices) < 2:
        return None, "missing_outcome_prices", str(path)
    side_index = 1 if side == "no" else 0
    other_index = 1 - side_index
    if prices[side_index] >= 0.99 and prices[other_index] <= 0.01:
        return 1, "gamma_closed_binary", str(path)
    if prices[side_index] <= 0.01 and prices[other_index] >= 0.99:
        return 0, "gamma_closed_binary", str(path)
    return None, "closed_nonbinary_outcome", str(path)


def _ts_key(value: Any) -> int:
    return int(pd.Timestamp(str(value)).value)


def _fee_per_share(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def _bootstrap_roi(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"roi": None, "ci_low": None, "ci_high": None, "target_dates": 0}
    daily = frame.groupby("target_date", sort=True)[["pnl_usd", "cost_usd"]].sum()
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    count = len(daily)
    indices = rng.integers(0, count, size=(BOOTSTRAP_DRAWS, count))
    pnl = daily["pnl_usd"].to_numpy()[indices].sum(axis=1)
    cost = daily["cost_usd"].to_numpy()[indices].sum(axis=1)
    draws = np.divide(pnl, cost, out=np.full_like(pnl, np.nan), where=cost > 0)
    point = float(frame["pnl_usd"].sum() / frame["cost_usd"].sum())
    low, high = np.nanquantile(draws, [0.025, 0.975])
    return {
        "roi": point,
        "ci_low": float(low),
        "ci_high": float(high),
        "draws": BOOTSTRAP_DRAWS,
        "target_dates": count,
    }


def _trade_summary(frame: pd.DataFrame) -> dict[str, Any]:
    base = _pnl_summary(frame)
    return base | {
        "wins": int(frame["settlement_no_label"].sum()) if not frame.empty else 0,
        "losses": int((1 - frame["settlement_no_label"]).sum()) if not frame.empty else 0,
    }


def _pnl_summary(frame: pd.DataFrame) -> dict[str, Any]:
    base = {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()) if not frame.empty else 0,
        "city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0])
        if not frame.empty
        else 0,
        "positive_pnl": int(frame["pnl_usd"].gt(0).sum()) if not frame.empty else 0,
        "negative_pnl": int(frame["pnl_usd"].lt(0).sum()) if not frame.empty else 0,
        "cost_usd": float(frame["cost_usd"].sum()) if not frame.empty else 0.0,
        "pnl_usd": float(frame["pnl_usd"].sum()) if not frame.empty else 0.0,
    }
    return base | _bootstrap_roi(frame)


def evaluate(
    *, candidates_path: Path, quotes_path: Path, gamma_root: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates, candidate_snapshot = _snapshot_jsonl(candidates_path)
    quotes, quote_snapshot = _snapshot_jsonl(quotes_path)
    needed = {
        (str(row["input_refs"][0]["event_key"]), _ts_key(row["decision_ts_utc"]))
        for row in candidates
    }
    quote_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    candidate_tokens = {
        (str(row["input_refs"][0]["event_key"]), str(row["token_id"]))
        for row in candidates
    }
    quote_tape: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in candidate_tokens
    }
    for quote in quotes:
        key = (str(quote.get("event_key") or ""), _ts_key(quote.get("ts_utc")))
        if key in needed:
            quote_by_key[key] = quote
        no_quote = (
            (((quote.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {})
        )
        tape_key = (
            str(quote.get("event_key") or ""),
            str(no_quote.get("token_id") or ""),
        )
        if tape_key in quote_tape:
            quote_tape[tape_key].append(
                {
                    "ts_utc": pd.Timestamp(str(quote.get("ts_utc"))),
                    "best_bid": no_quote.get("fresh_best_bid"),
                    "bid_size": no_quote.get("fresh_bid_size"),
                }
            )

    rows: list[dict[str, Any]] = []
    gamma_paths: set[str] = set()
    for candidate in candidates:
        event_key = str(candidate["input_refs"][0]["event_key"])
        quote = quote_by_key.get((event_key, _ts_key(candidate["decision_ts_utc"])), {})
        no_quote = (
            (((quote.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {})
        )
        current_yes_quote = (
            (((quote.get("quotes") or {}).get("source_round") or {}).get("yes") or {})
        )
        next_no_quote = (
            (((quote.get("quotes") or {}).get("source_plus_1") or {}).get("no") or {})
        )
        metadata = candidate.get("metadata") or {}
        ask = metadata.get("raw_no_best_ask")
        ask = float(ask) if ask is not None else None
        size = no_quote.get("fresh_ask_size")
        size = float(size) if size is not None else None
        label, settlement_status, gamma_path = _settlement_label(
            root=gamma_root,
            city=str(candidate["city"]),
            target_date=str(candidate["target_date"]),
            condition_id=str(candidate["condition_id"]),
        )
        current_yes_label, _, _ = _settlement_label(
            root=gamma_root,
            city=str(candidate["city"]),
            target_date=str(candidate["target_date"]),
            condition_id=str(current_yes_quote.get("condition_id") or ""),
            side="yes",
        )
        next_no_label, _, _ = _settlement_label(
            root=gamma_root,
            city=str(candidate["city"]),
            target_date=str(candidate["target_date"]),
            condition_id=str(next_no_quote.get("condition_id") or ""),
            side="no",
        )
        if gamma_path:
            gamma_paths.add(gamma_path)
        fee = _fee_per_share(ask) if ask is not None else None
        executable = ask is not None and size is not None and size >= MIN_SHARES
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "checkpoint_id": candidate["checkpoint_id"],
                "city": candidate["city"],
                "target_date": candidate["target_date"],
                "decision_ts_utc": candidate["decision_ts_utc"],
                "condition_id": candidate["condition_id"],
                "token_id": candidate["token_id"],
                "event_key": event_key,
                "bracket": candidate["bracket"],
                "source": event_key.split("|")[2],
                "cold_cross_margin_native": metadata.get("cold_cross_margin_native"),
                "no_best_ask": ask,
                "no_best_ask_size": size,
                "current_yes_best_ask": current_yes_quote.get("fresh_best_ask"),
                "current_yes_best_ask_size": current_yes_quote.get("fresh_ask_size"),
                "current_yes_settlement_label": current_yes_label,
                "next_no_best_ask": next_no_quote.get("fresh_best_ask"),
                "next_no_best_ask_size": next_no_quote.get("fresh_ask_size"),
                "next_no_settlement_label": next_no_label,
                "fee_per_share": fee,
                "settlement_no_label": label,
                "settlement_status": settlement_status,
                "settlement_gamma_path": gamma_path,
                "quote_match_status": "matched" if quote else "missing_exact_quote",
                "min_5_share_executable": executable,
                "five_share_cost_usd": MIN_SHARES * (ask + fee)
                if executable and fee is not None
                else None,
                "five_share_pnl_usd": MIN_SHARES * (label - ask - fee)
                if executable and label is not None and fee is not None
                else None,
            }
        )
    frame = pd.DataFrame(rows)
    frame["decision_ts_utc"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)

    eligible = frame[
        frame["min_5_share_executable"] & frame["settlement_no_label"].notna()
    ].copy()
    eligible["cost_usd"] = eligible["five_share_cost_usd"]
    eligible["pnl_usd"] = eligible["five_share_pnl_usd"]

    cap90 = eligible[eligible["no_best_ask"] <= MAX_NO_ASK].sort_values(
        ["decision_ts_utc", "candidate_id"]
    )
    cap90 = cap90.drop_duplicates(["city", "target_date"], keep="first")
    selected_ids = set(cap90["candidate_id"])
    frame["frozen_policy_selected"] = frame["candidate_id"].isin(selected_ids)

    repricing = {}
    for horizon_minutes in (10, 30, 60, 120, 240):
        exit_rows = []
        bid_column = f"exit_{horizon_minutes}m_best_bid"
        frame[bid_column] = np.nan
        for index, row in frame.iterrows():
            if not bool(row["min_5_share_executable"]):
                continue
            start = row["decision_ts_utc"] + pd.Timedelta(minutes=horizon_minutes)
            deadline = start + pd.Timedelta(minutes=12)
            tape = sorted(
                quote_tape.get((str(row["event_key"]), str(row["token_id"])), []),
                key=lambda item: item["ts_utc"],
            )
            exit_quote = next(
                (
                    item
                    for item in tape
                    if start <= item["ts_utc"] <= deadline
                    and item["best_bid"] is not None
                    and item["bid_size"] is not None
                    and float(item["bid_size"]) >= MIN_SHARES
                ),
                None,
            )
            if exit_quote is None:
                continue
            bid = float(exit_quote["best_bid"])
            frame.at[index, bid_column] = bid
            ask = float(row["no_best_ask"])
            cost = MIN_SHARES * (ask + _fee_per_share(ask))
            proceeds = MIN_SHARES * (bid - _fee_per_share(bid))
            exit_rows.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "cost_usd": cost,
                    "pnl_usd": proceeds - cost,
                }
            )
        exit_frame = pd.DataFrame(exit_rows)
        first_city_day = (
            exit_frame.sort_values("decision_ts_utc").drop_duplicates(
                ["city", "target_date"], keep="first"
            )
            if not exit_frame.empty
            else exit_frame
        )
        repricing[f"{horizon_minutes}m"] = {
            "quote_tolerance_minutes": 12,
            "all_entries": _pnl_summary(exit_frame),
            "first_entry_per_city_day": _pnl_summary(first_city_day),
        }

    basket_specs = {
        "previous_no_plus_current_yes": (
            ("no_best_ask", "no_best_ask_size", "settlement_no_label"),
            ("current_yes_best_ask", "current_yes_best_ask_size", "current_yes_settlement_label"),
        ),
        "current_yes_plus_next_no": (
            ("current_yes_best_ask", "current_yes_best_ask_size", "current_yes_settlement_label"),
            ("next_no_best_ask", "next_no_best_ask_size", "next_no_settlement_label"),
        ),
        "previous_no_plus_current_yes_plus_next_no": (
            ("no_best_ask", "no_best_ask_size", "settlement_no_label"),
            ("current_yes_best_ask", "current_yes_best_ask_size", "current_yes_settlement_label"),
            ("next_no_best_ask", "next_no_best_ask_size", "next_no_settlement_label"),
        ),
    }
    basket_diagnostics = {}
    for name, legs in basket_specs.items():
        mask = pd.Series(True, index=frame.index)
        for ask_column, size_column, label_column in legs:
            mask &= (
                frame[ask_column].notna()
                & pd.to_numeric(frame[size_column], errors="coerce").fillna(0).ge(MIN_SHARES)
                & frame[label_column].notna()
            )
        basket = frame[mask].copy()
        basket["cost_usd"] = MIN_SHARES * sum(
            basket[ask_column].astype(float)
            + basket[ask_column].astype(float).map(_fee_per_share)
            for ask_column, _, _ in legs
        )
        basket["pnl_usd"] = MIN_SHARES * sum(
            basket[label_column].astype(float) for _, _, label_column in legs
        ) - basket["cost_usd"]
        under_one = basket[basket["cost_usd"] < MIN_SHARES].sort_values(
            ["decision_ts_utc", "candidate_id"]
        )
        first_city_day = under_one.drop_duplicates(
            ["city", "target_date"], keep="first"
        )
        basket_diagnostics[name] = {
            "all_legs_5_share_covered": int(len(basket)),
            "fee_adjusted_cost_below_1_per_bundle": _pnl_summary(under_one),
            "first_eligible_per_city_day": _pnl_summary(first_city_day),
        }

    cap_grid = []
    for cap in (0.85, 0.90, 0.92, 0.95, 0.98, 1.00):
        sliced = eligible[eligible["no_best_ask"] <= cap]
        cap_grid.append({"max_no_ask": cap} | _trade_summary(sliced))

    gamma_manifest = []
    for raw_path in sorted(gamma_paths):
        path = Path(raw_path)
        gamma_manifest.append(
            {
                "path": raw_path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "strategy_key": "weather.tmin.cross_prev_no",
        "status": "inconclusive_keep_zero_notional_shadow",
        "observed_input_snapshots": {
            "candidates": candidate_snapshot,
            "quotes": quote_snapshot,
            "gamma_closed_events": gamma_manifest,
        },
        "denominator_scope": {
            "unit": "first source cross event/rung expression",
            "candidate_rows": int(len(frame)),
            "target_dates": int(frame["target_date"].nunique()),
            "cities": sorted(frame["city"].unique()),
        },
        "signal_funnel": {
            "raw_cross_candidates": int(len(frame)),
            "observed_mechanism_candidates": int(len(frame)),
            "frozen_policy_historical_selected": int(len(cap90)),
        },
        "evidence_funnel": {
            "exact_quote_joined": int(frame["quote_match_status"].eq("matched").sum()),
            "raw_no_ask_available": int(frame["no_best_ask"].notna().sum()),
            "top_ask_depth_at_least_5_shares": int(frame["min_5_share_executable"].sum()),
            "closed_binary_settlement": int(frame["settlement_no_label"].notna().sum()),
            "settled_and_5_share_executable": int(len(eligible)),
            "actual_fills": 0,
        },
        "all_5_share_executable": _trade_summary(eligible),
        "frozen_policy": {
            "policy_id": FROZEN_POLICY_ID,
            "max_no_ask": MAX_NO_ASK,
            "min_top_ask_shares": MIN_SHARES,
            "dedupe": "first eligible event per city and target_date",
            "entry_clock": "same-checkpoint fresh direct NO ask",
            "execution_mode": "zero_notional_forward_only",
            "historical_replay": _trade_summary(cap90),
        },
        "exploratory_price_cap_grid": {
            "comparisons": 6,
            "multiple_testing_adjustment": "none; development diagnostic only",
            "rows": cap_grid,
        },
        "taker_repricing_exit": repricing,
        "basket_diagnostics": {
            "comparisons": 3,
            "multiple_testing_adjustment": "none; development diagnostic only",
            "expressions": basket_diagnostics,
        },
        "gates": {
            "same_denominator_probability_vs_market": "FAIL_no_frozen_probability_model",
            "fee_adjusted_significance": "FAIL_ci_crosses_zero",
            "frozen_forward": "FAIL_not_started_for_this_policy",
            "direct_execution_evidence": "PARTIAL_direct_ask_and_top_depth_no_fills",
            "taker_repricing_10m": "FAIL_fee_adjusted_ci_below_zero",
        },
        "decision": (
            "Freeze cap90/first-city-day as a zero-notional challenger; keep the full "
            "candidate denominator and do not change live behavior."
        ),
    }
    return frame, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--quotes", type=Path, required=True)
    parser.add_argument("--gamma-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame, summary = evaluate(
        candidates_path=args.candidates,
        quotes_path=args.quotes,
        gamma_root=args.gamma_root,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / "candidate_evaluation.csv.gz", index=False, compression="gzip")
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
