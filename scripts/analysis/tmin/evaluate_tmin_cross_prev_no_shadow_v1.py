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


SCHEMA_VERSION = "weather_tmin_cross_prev_no_performance_v2"
FROZEN_POLICY_ID = "tmin_cross_prev_no_cap90_first_cityday_v1"
REPRICING_CHALLENGER_POLICY_ID = "tmin_cross_prev_no_first_cityday_exit60m_v1"
FEE_RATE = 0.05
MIN_SHARES = 5.0
MAX_NO_ASK = 0.90
REPRICING_CHALLENGER_HORIZON_MINUTES = 60
REPRICING_QUOTE_TOLERANCE_MINUTES = 12
MAX_FRESH_QUOTE_AGE_SECONDS = 300.0
REPRICING_DEVELOPMENT_END_TARGET_DATE = "2026-08-27"
REPRICING_FORWARD_START_TARGET_DATE = "2026-08-28"
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260812


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _snapshot_jsonl(
    path: Path, *, snapshot_size_bytes: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_size = path.stat().st_size
    size = (
        source_size
        if snapshot_size_bytes is None
        else min(source_size, snapshot_size_bytes)
    )
    with path.open("rb") as handle:
        payload = handle.read(size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line]
    return rows, {
        "path": str(path),
        "source_size_at_read_bytes": source_size,
        "snapshot_size_bytes": size,
        "complete_size_bytes": complete_size,
        "complete_sha256": hashlib.sha256(complete).hexdigest(),
        "rows": len(rows),
    }


def _require_candidate_schema(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        raise ValueError("candidate journal contains no complete rows")
    required = {
        "candidate_id",
        "checkpoint_id",
        "city",
        "target_date",
        "decision_ts_utc",
        "condition_id",
        "token_id",
        "bracket",
        "input_refs",
    }
    candidate_ids: set[str] = set()
    for index, row in enumerate(candidates):
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"candidate row {index} missing required fields: {missing}")
        references = row.get("input_refs")
        if (
            not isinstance(references, list)
            or not references
            or not isinstance(references[0], dict)
            or not references[0].get("event_key")
        ):
            raise ValueError(f"candidate row {index} missing input_refs[0].event_key")
        try:
            pd.Timestamp(str(row["decision_ts_utc"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"candidate row {index} has invalid decision_ts_utc"
            ) from exc
        candidate_id = str(row["candidate_id"])
        if candidate_id in candidate_ids:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        candidate_ids.add(candidate_id)


def _exact_quote_map(
    quotes: list[dict[str, Any]],
    *,
    needed: set[tuple[str, int]],
    snapshot_name: str,
) -> dict[tuple[str, int], dict[str, Any]]:
    output: dict[tuple[str, int], dict[str, Any]] = {}
    for index, quote in enumerate(quotes):
        if not quote.get("ts_utc"):
            continue
        key = (str(quote.get("event_key") or ""), _ts_key(quote.get("ts_utc")))
        if key not in needed:
            continue
        if key in output:
            raise ValueError(
                f"duplicate exact quote key in {snapshot_name}: {key} at row {index}"
            )
        output[key] = quote
    return output


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
    if not np.isfinite(price) or not 0.0 <= price <= 1.0:
        raise ValueError(f"price must be finite and within [0, 1], got {price}")
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


def _build_quote_tape(
    quotes: list[dict[str, Any]],
    *,
    candidate_tokens: set[tuple[str, str]],
    snapshot_name: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    tape: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in candidate_tokens
    }
    seen: set[tuple[str, str, int]] = set()
    for index, quote in enumerate(quotes):
        if not quote.get("ts_utc"):
            continue
        no_quote = (
            (((quote.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {})
        )
        tape_key = (
            str(quote.get("event_key") or ""),
            str(no_quote.get("token_id") or ""),
        )
        if tape_key not in tape:
            continue
        timestamp = pd.Timestamp(str(quote.get("ts_utc")))
        unique_key = (*tape_key, int(timestamp.value))
        if unique_key in seen:
            raise ValueError(
                f"duplicate quote tape key in {snapshot_name}: {unique_key} at row {index}"
            )
        seen.add(unique_key)
        bid = no_quote.get("fresh_best_bid")
        bid_size = no_quote.get("fresh_bid_size")
        if bid is not None:
            _fee_per_share(float(bid))
        if bid_size is not None and (
            not np.isfinite(float(bid_size)) or float(bid_size) < 0
        ):
            raise ValueError(f"invalid fresh_bid_size in {snapshot_name} row {index}")
        fetched_at_raw = no_quote.get("fresh_fetched_at_utc")
        tape[tape_key].append(
            {
                "ts_utc": timestamp,
                "fresh_fetched_at_utc": (
                    pd.Timestamp(str(fetched_at_raw)) if fetched_at_raw else None
                ),
                "fresh_status": no_quote.get("fresh_status"),
                "best_bid": bid,
                "bid_size": bid_size,
            }
        )
    return tape


def evaluate(
    *,
    candidates_path: Path,
    quotes_path: Path,
    gamma_root: Path,
    development_candidates_snapshot_size_bytes: int | None = None,
    development_quotes_snapshot_size_bytes: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates, candidate_snapshot = _snapshot_jsonl(candidates_path)
    quotes, quote_snapshot = _snapshot_jsonl(quotes_path)
    _require_candidate_schema(candidates)
    development_candidates, development_candidate_snapshot = _snapshot_jsonl(
        candidates_path,
        snapshot_size_bytes=development_candidates_snapshot_size_bytes,
    )
    development_quotes, development_quote_snapshot = _snapshot_jsonl(
        quotes_path,
        snapshot_size_bytes=development_quotes_snapshot_size_bytes,
    )
    _require_candidate_schema(development_candidates)
    development_candidate_ids = {
        str(row["candidate_id"])
        for row in development_candidates
        if str(row["target_date"]) <= REPRICING_DEVELOPMENT_END_TARGET_DATE
    }
    needed = {
        (str(row["input_refs"][0]["event_key"]), _ts_key(row["decision_ts_utc"]))
        for row in candidates
        if str(row["candidate_id"]) not in development_candidate_ids
    }
    development_needed = {
        (str(row["input_refs"][0]["event_key"]), _ts_key(row["decision_ts_utc"]))
        for row in development_candidates
        if str(row["candidate_id"]) in development_candidate_ids
    }
    quote_by_key = _exact_quote_map(
        quotes,
        needed=needed,
        snapshot_name="full quote snapshot",
    )
    development_quote_by_key = _exact_quote_map(
        development_quotes,
        needed=development_needed,
        snapshot_name="development quote snapshot",
    )
    candidate_tokens = {
        (str(row["input_refs"][0]["event_key"]), str(row["token_id"]))
        for row in candidates
        if str(row["candidate_id"]) not in development_candidate_ids
    }
    development_candidate_tokens = {
        (str(row["input_refs"][0]["event_key"]), str(row["token_id"]))
        for row in development_candidates
        if str(row["candidate_id"]) in development_candidate_ids
    }
    quote_tape = _build_quote_tape(
        quotes,
        candidate_tokens=candidate_tokens,
        snapshot_name="full quote snapshot",
    )
    development_quote_tape = _build_quote_tape(
        development_quotes,
        candidate_tokens=development_candidate_tokens,
        snapshot_name="development quote snapshot",
    )

    rows: list[dict[str, Any]] = []
    gamma_paths: set[str] = set()
    for candidate in candidates:
        event_key = str(candidate["input_refs"][0]["event_key"])
        is_frozen_development_candidate = (
            str(candidate["candidate_id"]) in development_candidate_ids
            and str(candidate["target_date"]) <= REPRICING_DEVELOPMENT_END_TARGET_DATE
        )
        exact_quotes = (
            development_quote_by_key
            if is_frozen_development_candidate
            else quote_by_key
        )
        quote = exact_quotes.get(
            (event_key, _ts_key(candidate["decision_ts_utc"])), {}
        )
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
        candidate_raw_ask = metadata.get("raw_no_best_ask")
        candidate_raw_ask = (
            float(candidate_raw_ask) if candidate_raw_ask is not None else None
        )
        ask = no_quote.get("fresh_best_ask")
        ask = float(ask) if ask is not None else None
        if candidate_raw_ask is not None:
            _fee_per_share(candidate_raw_ask)
        if ask is not None:
            _fee_per_share(ask)
        size = no_quote.get("fresh_ask_size")
        size = float(size) if size is not None else None
        if size is not None and (not np.isfinite(size) or size < 0):
            raise ValueError(
                f"invalid fresh_ask_size for candidate {candidate['candidate_id']}"
            )
        quote_timestamp = (
            pd.Timestamp(str(quote.get("ts_utc"))) if quote.get("ts_utc") else None
        )
        fresh_fetched_at_raw = no_quote.get("fresh_fetched_at_utc")
        fresh_fetched_at = (
            pd.Timestamp(str(fresh_fetched_at_raw)) if fresh_fetched_at_raw else None
        )
        fresh_age_seconds = (
            float((quote_timestamp - fresh_fetched_at).total_seconds())
            if quote_timestamp is not None and fresh_fetched_at is not None
            else None
        )
        candidate_ask_matches_exact_quote = (
            ask is not None
            and candidate_raw_ask is not None
            and abs(ask - candidate_raw_ask) <= 1e-12
        )
        token_matches = str(no_quote.get("token_id") or "") == str(
            candidate.get("token_id") or ""
        )
        fresh_quote_valid = (
            str(no_quote.get("fresh_status") or "") == "ok"
            and fresh_age_seconds is not None
            and 0.0 <= fresh_age_seconds <= MAX_FRESH_QUOTE_AGE_SECONDS
        )
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
        executable = (
            bool(quote)
            and ask is not None
            and ask > 0.0
            and size is not None
            and size >= MIN_SHARES
            and candidate_ask_matches_exact_quote
            and token_matches
            and fresh_quote_valid
        )
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "frozen_development_candidate": is_frozen_development_candidate,
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
                "candidate_raw_no_best_ask": candidate_raw_ask,
                "no_best_ask": ask,
                "no_best_ask_size": size,
                "entry_quote_fresh_status": no_quote.get("fresh_status"),
                "entry_quote_fetched_at_utc": fresh_fetched_at_raw,
                "entry_quote_age_seconds": fresh_age_seconds,
                "candidate_ask_matches_exact_quote": candidate_ask_matches_exact_quote,
                "candidate_token_matches_exact_quote": token_matches,
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
    repricing_exit_frames: dict[int, pd.DataFrame] = {}
    strict_entry_signals = frame[frame["min_5_share_executable"]].sort_values(
        ["decision_ts_utc", "candidate_id"]
    )
    first_entry_signals = strict_entry_signals.drop_duplicates(
        ["city", "target_date"], keep="first"
    )
    frozen_development_entry_signals = strict_entry_signals[
        strict_entry_signals["frozen_development_candidate"]
    ].drop_duplicates(["city", "target_date"], keep="first")
    for horizon_minutes in (10, 30, 60, 120, 240):
        exit_rows = []
        bid_column = f"exit_{horizon_minutes}m_best_bid"
        exit_ts_column = f"exit_{horizon_minutes}m_quote_ts_utc"
        exit_age_column = f"exit_{horizon_minutes}m_quote_age_seconds"
        frame[bid_column] = np.nan
        frame[exit_ts_column] = None
        frame[exit_age_column] = np.nan
        for index, row in frame.iterrows():
            if not bool(row["min_5_share_executable"]):
                continue
            start = row["decision_ts_utc"] + pd.Timedelta(minutes=horizon_minutes)
            deadline = start + pd.Timedelta(
                minutes=REPRICING_QUOTE_TOLERANCE_MINUTES
            )
            selected_tape = (
                development_quote_tape
                if bool(row["frozen_development_candidate"])
                else quote_tape
            )
            tape = sorted(
                selected_tape.get(
                    (str(row["event_key"]), str(row["token_id"])), []
                ),
                key=lambda item: item["ts_utc"],
            )
            exit_quote = next(
                (
                    item
                    for item in tape
                    if start <= item["ts_utc"] <= deadline
                    and item["fresh_fetched_at_utc"] is not None
                    and item["fresh_status"] == "ok"
                    and 0.0
                    <= (item["ts_utc"] - item["fresh_fetched_at_utc"]).total_seconds()
                    <= MAX_FRESH_QUOTE_AGE_SECONDS
                    and item["best_bid"] is not None
                    and item["bid_size"] is not None
                    and float(item["bid_size"]) >= MIN_SHARES
                ),
                None,
            )
            if exit_quote is None:
                continue
            bid = float(exit_quote["best_bid"])
            exit_age_seconds = float(
                (
                    exit_quote["ts_utc"] - exit_quote["fresh_fetched_at_utc"]
                ).total_seconds()
            )
            frame.at[index, bid_column] = bid
            frame.at[index, exit_ts_column] = exit_quote["ts_utc"].isoformat()
            frame.at[index, exit_age_column] = exit_age_seconds
            ask = float(row["no_best_ask"])
            cost = MIN_SHARES * (ask + _fee_per_share(ask))
            proceeds = MIN_SHARES * (bid - _fee_per_share(bid))
            exit_rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "entry_ask": ask,
                    "entry_quote_age_seconds": row["entry_quote_age_seconds"],
                    "exit_best_bid": bid,
                    "exit_quote_ts_utc": exit_quote["ts_utc"],
                    "exit_quote_age_seconds": exit_age_seconds,
                    "cost_usd": cost,
                    "pnl_usd": proceeds - cost,
                }
            )
        exit_frame = pd.DataFrame(exit_rows)
        repricing_exit_frames[horizon_minutes] = exit_frame
        first_signal_ids = set(first_entry_signals["candidate_id"])
        first_city_day = (
            exit_frame[exit_frame["candidate_id"].isin(first_signal_ids)].copy()
            if not exit_frame.empty
            else exit_frame
        )
        first_summary = _pnl_summary(first_city_day)
        first_summary.update(
            {
                "signal_rows": int(len(first_entry_signals)),
                "signals_with_fresh_exit_quote": int(len(first_city_day)),
                "signals_missing_fresh_exit_quote": int(
                    len(first_entry_signals) - len(first_city_day)
                ),
            }
        )
        repricing[f"{horizon_minutes}m"] = {
            "quote_tolerance_minutes": REPRICING_QUOTE_TOLERANCE_MINUTES,
            "max_underlying_quote_age_seconds": MAX_FRESH_QUOTE_AGE_SECONDS,
            "all_entries": _pnl_summary(exit_frame),
            "first_entry_per_city_day": first_summary,
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

    challenger_exit_frame = repricing_exit_frames[
        REPRICING_CHALLENGER_HORIZON_MINUTES
    ]
    development_signals = frozen_development_entry_signals[
        frozen_development_entry_signals["target_date"].astype(str).le(
            REPRICING_DEVELOPMENT_END_TARGET_DATE
        )
    ].copy()
    development_signal_ids = set(development_signals["candidate_id"])
    development_exits = (
        challenger_exit_frame[
            challenger_exit_frame["candidate_id"].isin(development_signal_ids)
        ].copy()
        if not challenger_exit_frame.empty
        else challenger_exit_frame
    )
    forward_signals = first_entry_signals[
        first_entry_signals["target_date"].astype(str).ge(
            REPRICING_FORWARD_START_TARGET_DATE
        )
    ].copy()
    forward_signal_ids = set(forward_signals["candidate_id"])
    forward_exits = (
        challenger_exit_frame[
            challenger_exit_frame["candidate_id"].isin(forward_signal_ids)
        ].copy()
        if not challenger_exit_frame.empty
        else challenger_exit_frame
    )
    frame["repricing_challenger_development_selected"] = frame["candidate_id"].isin(
        development_signal_ids
    )
    frame["repricing_challenger_forward_selected"] = frame["candidate_id"].isin(
        forward_signal_ids
    )
    development_repricing = _pnl_summary(development_exits)
    development_repricing.update(
        {
            "signal_rows": int(len(development_signals)),
            "signals_with_fresh_exit_quote": int(len(development_exits)),
            "signals_missing_fresh_exit_quote": int(
                len(development_signals) - len(development_exits)
            ),
        }
    )
    development_horizon_summaries = {}
    for horizon in (10, 30, 60, 120, 240):
        horizon_frame = repricing_exit_frames[horizon]
        horizon_development = (
            horizon_frame[
                horizon_frame["candidate_id"].isin(development_signal_ids)
            ].copy()
            if not horizon_frame.empty
            else horizon_frame
        )
        horizon_summary = _pnl_summary(horizon_development)
        horizon_summary.update(
            {
                "signal_rows": int(len(development_signals)),
                "signals_with_fresh_exit_quote": int(len(horizon_development)),
                "signals_missing_fresh_exit_quote": int(
                    len(development_signals) - len(horizon_development)
                ),
            }
        )
        development_horizon_summaries[f"{horizon}m"] = horizon_summary
    positive_ci_horizons = [
        horizon
        for horizon in (10, 30, 60, 120, 240)
        if development_horizon_summaries[f"{horizon}m"]["ci_low"]
        is not None
        and development_horizon_summaries[f"{horizon}m"]["ci_low"] > 0
    ]
    qualifying_horizons = [
        horizon
        for horizon in positive_ci_horizons
        if development_horizon_summaries[f"{horizon}m"][
            "signals_missing_fresh_exit_quote"
        ]
        == 0
    ]
    earliest_positive_ci_horizon = min(qualifying_horizons) if qualifying_horizons else None
    repricing_freeze_eligible = (
        earliest_positive_ci_horizon == REPRICING_CHALLENGER_HORIZON_MINUTES
        and development_repricing["target_dates"] >= 10
        and development_repricing["ci_low"] is not None
        and development_repricing["ci_low"] > 0
        and len(development_exits) == len(development_signals)
    )
    repricing_challenger = {
        "policy_id": REPRICING_CHALLENGER_POLICY_ID,
        "status": (
            "eligible_for_prospective_zero_notional_frozen_forward"
            if repricing_freeze_eligible
            else "development_gate_failed_do_not_freeze"
        ),
        "mechanism": (
            "buy previous-warmer exact-bracket NO on the first strict source cross per "
            "city and target_date, then taker-exit at the first fresh executable bid in "
            "the fixed 60-to-72 minute observation window"
        ),
        "objective": "event-driven_60m_fee_adjusted_round_trip_not_settlement_hold",
        "entry_policy": {
            "dedupe": "first strict 5-share executable source cross per city and target_date",
            "entry_clock": "exact-checkpoint direct fresh NO ask",
            "min_top_ask_shares": MIN_SHARES,
            "max_underlying_quote_age_seconds": MAX_FRESH_QUOTE_AGE_SECONDS,
            "price_cap": None,
        },
        "exit_policy": {
            "horizon_minutes": REPRICING_CHALLENGER_HORIZON_MINUTES,
            "first_quote_window_minutes": [
                REPRICING_CHALLENGER_HORIZON_MINUTES,
                REPRICING_CHALLENGER_HORIZON_MINUTES
                + REPRICING_QUOTE_TOLERANCE_MINUTES,
            ],
            "min_top_bid_shares": MIN_SHARES,
            "max_underlying_quote_age_seconds": MAX_FRESH_QUOTE_AGE_SECONDS,
            "fees": "official Weather taker fee applied at entry and exit",
        },
        "development_end_target_date": REPRICING_DEVELOPMENT_END_TARGET_DATE,
        "forward_start_target_date": REPRICING_FORWARD_START_TARGET_DATE,
        "development_replay": development_repricing,
        "development_horizon_diagnostics": development_horizon_summaries,
        "development_quote_lineage": {
            "entry_quote_age_seconds_max": (
                None
                if development_signals.empty
                else float(development_signals["entry_quote_age_seconds"].max())
            ),
            "exit_quote_age_seconds_max": (
                None
                if development_exits.empty
                else float(development_exits["exit_quote_age_seconds"].max())
            ),
            "all_entry_candidate_asks_match_exact_quotes": bool(
                development_signals["candidate_ask_matches_exact_quote"].all()
            )
            if not development_signals.empty
            else False,
            "all_entry_tokens_match_exact_quotes": bool(
                development_signals["candidate_token_matches_exact_quote"].all()
            )
            if not development_signals.empty
            else False,
        },
        "development_by_city": [
            {"city": str(city), **_pnl_summary(group)}
            for city, group in development_exits.groupby("city", sort=True)
        ]
        if not development_exits.empty
        else [],
        "multiple_testing": {
            "horizons_examined_minutes": [10, 30, 60, 120, 240],
            "cohort_forms_per_horizon": ["all_entries", "first_entry_per_city_day"],
            "comparisons": 10,
            "selection_rule": (
                "earliest horizon whose development target-date bootstrap lower bound "
                "is above zero and whose fixed signal denominator has complete fresh-exit "
                "coverage; no multiplicity-adjusted significance claim"
            ),
            "positive_ci_horizons_before_coverage_gate_minutes": positive_ci_horizons,
            "qualifying_horizons_minutes": qualifying_horizons,
            "selected_horizon_minutes": REPRICING_CHALLENGER_HORIZON_MINUTES,
        },
        "freeze_qualification": {
            "minimum_development_target_dates": 10,
            "development_target_dates": int(development_repricing["target_dates"]),
            "fee_adjusted_target_date_bootstrap_ci_low_above_zero": bool(
                development_repricing["ci_low"] is not None
                and development_repricing["ci_low"] > 0
            ),
            "complete_fresh_exit_coverage": bool(
                len(development_exits) == len(development_signals)
            ),
            "eligible": repricing_freeze_eligible,
            "note": (
                "eligibility freezes a prospective zero-notional test; development data "
                "were used to choose the expression and cannot validate it"
            ),
        },
        "forward_funnel": {
            "signal_rows": int(len(forward_signals)),
            "signals_with_fresh_exit_quote": int(len(forward_exits)),
            "target_dates": int(forward_signals["target_date"].nunique())
            if not forward_signals.empty
            else 0,
            "performance": _pnl_summary(forward_exits),
            "status": "running" if not forward_signals.empty else "not_started",
        },
    }

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
            "development_candidates": development_candidate_snapshot,
            "development_quotes": development_quote_snapshot,
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
            "candidate_raw_ask_matches_exact_quote": int(
                frame["candidate_ask_matches_exact_quote"].sum()
            ),
            "entry_quote_status_ok_and_age_at_most_300s": int(
                (
                    frame["entry_quote_fresh_status"].eq("ok")
                    & frame["entry_quote_age_seconds"].between(
                        0.0, MAX_FRESH_QUOTE_AGE_SECONDS
                    )
                ).sum()
            ),
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
        "prospective_repricing_challenger": repricing_challenger,
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
            "prospective_repricing_challenger_freeze": (
                "PASS_ZERO_NOTIONAL_FREEZE_ONLY"
                if repricing_freeze_eligible
                else "FAIL_DEVELOPMENT_QUALIFICATION"
            ),
            "prospective_repricing_forward": (
                "RUNNING" if not forward_signals.empty else "NOT_STARTED"
            ),
        },
        "decision": (
            "Keep cap90/settlement-hold as the incumbent; freeze the first-city-day 60m "
            "round-trip expression as a separate prospective zero-notional challenger. "
            "Do not change live behavior."
        ),
    }
    return frame, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--quotes", type=Path, required=True)
    parser.add_argument("--gamma-root", type=Path, required=True)
    parser.add_argument(
        "--development-candidates-snapshot-size-bytes",
        type=int,
        help="Immutable candidate journal byte boundary used for challenger development.",
    )
    parser.add_argument(
        "--development-quotes-snapshot-size-bytes",
        type=int,
        help="Immutable quote journal byte boundary used for challenger development.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame, summary = evaluate(
        candidates_path=args.candidates,
        quotes_path=args.quotes,
        gamma_root=args.gamma_root,
        development_candidates_snapshot_size_bytes=(
            args.development_candidates_snapshot_size_bytes
        ),
        development_quotes_snapshot_size_bytes=(
            args.development_quotes_snapshot_size_bytes
        ),
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
