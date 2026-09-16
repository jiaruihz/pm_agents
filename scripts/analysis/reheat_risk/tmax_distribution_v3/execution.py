"""Frozen-policy execution replays for tmax_distribution_v3.

- first-lock: legacy-comparable, first eligible city-day expression.
- target-book: position-aware; later posteriors may hold/close/rebalance a
  single tracked position per city-day, priced off the complement book with
  double fees and an adverse-selection buffer.  Position lineage is explicit,
  so a YES/NO self-cross without an open position is structurally impossible.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import BUCKETS, official_fee

from .common import FROZEN_POLICY, city_family, roi_date_block_ci

KEY = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]
EXPRESSION_FIELDS = {
    "current_no": ("current_no_ask", "current_no_ask_size", "current", "NO"),
    "d1_no": ("d1_no_ask", "d1_no_ask_size", "d1", "NO"),
    "d2_no": ("d2_no_ask", "d2_no_ask_size", "d2", "NO"),
    "d1_yes": ("d1_yes_direct_ask", "d1_yes_direct_ask_size", "d1", "YES"),
    "d2_yes": ("d2_yes_direct_ask", "d2_yes_direct_ask_size", "d2", "YES"),
}
BUCKET_TO_BRACKET = {"current": "current_bracket", "d1": "d1_bracket", "d2": "d2_bracket"}


def _eligible_expressions(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for expression, (ask_field, size_field, bucket, side) in EXPRESSION_FIELDS.items():
        ask, size = row.get(ask_field), row.get(size_field)
        if ask is None or size is None or not np.isfinite(float(ask or np.nan)) or not np.isfinite(float(size or np.nan)):
            continue
        ask, size = float(ask), float(size)
        if size < FROZEN_POLICY["min_ask_size"]:
            continue
        if not (FROZEN_POLICY["ask_floor"] <= ask <= FROZEN_POLICY["ask_ceiling"]):
            continue
        p_bucket = float(row[f"route_p_{bucket}"])
        p_win = 1.0 - p_bucket if side == "NO" else p_bucket
        fee = official_fee(ask)
        edge = p_win - ask - fee
        if edge < FROZEN_POLICY["edge_threshold"]:
            continue
        out.append(
            {
                "expression": expression,
                "side": side,
                "bucket": bucket,
                "bracket": str(row[BUCKET_TO_BRACKET[bucket]]),
                "ask": ask,
                "ask_size": size,
                "fee": fee,
                "p_win": p_win,
                "edge": edge,
            }
        )
    return sorted(out, key=lambda item: -item["edge"])


def _win(side: str, bracket: str, winner_bracket: str | None) -> float | None:
    if winner_bracket is None:
        return None
    hit = str(bracket) == str(winner_bracket)
    return float(hit) if side == "YES" else float(not hit)


def first_lock_replay(states_with_probs: pd.DataFrame, route: str) -> pd.DataFrame:
    shares = float(FROZEN_POLICY["shares"])
    rows = []
    ordered = states_with_probs.sort_values(["city", "target_date", "decision_snapshot_ts_utc"])
    for (city, date), group in ordered.groupby(["city", "target_date"], sort=False):
        for record in group.to_dict("records"):
            candidates = _eligible_expressions(record)
            if not candidates:
                continue
            best = candidates[0]
            win = _win(best["side"], best["bracket"], record.get("settlement_winning_bracket_label"))
            rows.append(
                {
                    "route": route, "city": city, "target_date": date,
                    "decision_snapshot_ts_utc": record["decision_snapshot_ts_utc"],
                    "decision_hour_local": record["decision_hour_local"],
                    "expression": best["expression"], "side": best["side"], "bracket": best["bracket"],
                    "unit": record["unit"], "forecast_source": record.get("forecast_source"),
                    "family": city_family(city), "shares": shares,
                    "ask": best["ask"], "ask_size": best["ask_size"],
                    "fee_per_share": best["fee"], "edge": best["edge"],
                    "p_win": best["p_win"], "win": win,
                    "cost": shares * (best["ask"] + best["fee"]),
                    "pnl": shares * (win - best["ask"] - best["fee"]) if win is not None else math.nan,
                }
            )
            break
    return pd.DataFrame(rows)


def _revalue(position: dict[str, Any], record: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    """P(position wins) under the current posterior + complement close quote."""
    book = json.loads(record["ladder_book_json"])
    labels = [str(r["bracket"]) for r in book]
    if position["bracket"] not in labels:
        return None, None
    rung = labels.index(position["bracket"])
    anchor = int(record["anchor_rung_index"])
    p_bucket_map = {b: float(record[f"route_p_{b}"]) for b in BUCKETS}
    step = rung - anchor
    if step < 0:
        p_bracket = p_bucket_map["below"]
    elif step == 0:
        p_bracket = p_bucket_map["current"]
    elif step == 1:
        p_bracket = p_bucket_map["d1"]
    elif step == 2:
        p_bracket = p_bucket_map["d2"]
    else:
        tail_rungs = len(book) - anchor - 3
        h = float(np.clip(record.get("h_cont", 0.3), 1e-6, 1 - 1e-6))
        k = step - 3
        if tail_rungs <= 1 or k >= tail_rungs - 1:
            p_bracket = p_bucket_map["tail"] * (h ** max(tail_rungs - 1, 0))
        else:
            p_bracket = p_bucket_map["tail"] * (1 - h) * h**k
    p_win = p_bracket if position["side"] == "YES" else 1.0 - p_bracket
    quote = book[rung]
    return p_win, quote


def target_book_replay(states_with_probs: pd.DataFrame, route: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    shares = float(FROZEN_POLICY["shares"])
    buffer = float(FROZEN_POLICY["rebalance_buffer"])
    adverse = float(FROZEN_POLICY["adverse_selection_buffer"])
    ledger, positions = [], []
    ordered = states_with_probs.sort_values(["city", "target_date", "decision_snapshot_ts_utc"])
    for (city, date), group in ordered.groupby(["city", "target_date"], sort=False):
        active: dict[str, Any] | None = None
        position_seq = 0
        winner = None
        for record in group.to_dict("records"):
            winner = record.get("settlement_winning_bracket_label") or winner
            ts = record["decision_snapshot_ts_utc"]
            if active is None:
                candidates = _eligible_expressions(record)
                if candidates:
                    best = candidates[0]
                    position_seq += 1
                    active = {
                        "position_id": f"{city}|{date}|{position_seq}",
                        "city": city, "target_date": date, "opened_ts": ts,
                        "expression": best["expression"], "side": best["side"],
                        "bracket": best["bracket"], "entry_ask": best["ask"],
                        "entry_fee": best["fee"], "locked": False,
                    }
                    ledger.append({"city": city, "target_date": date, "ts": ts, "action": "open",
                                   "position_id": active["position_id"], "expression": best["expression"],
                                   "side": best["side"], "bracket": best["bracket"],
                                   "cash": -shares * (best["ask"] + best["fee"]), "p_win": best["p_win"],
                                   "detail": f"edge={best['edge']:.4f}"})
                continue
            if active["locked"]:
                continue
            p_old, quote = _revalue(active, record)
            if p_old is None or quote is None:
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "hold_missing_revalue",
                               "position_id": active["position_id"], "expression": active["expression"],
                               "side": active["side"], "bracket": active["bracket"], "cash": 0.0,
                               "p_win": math.nan, "detail": "bracket_not_in_current_ladder"})
                continue
            comp_ask = quote["yes_ask"] if active["side"] == "NO" else quote["no_ask"]
            if comp_ask is None or not (0.0 < float(comp_ask) < 1.0):
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "hold_missing_complement_book",
                               "position_id": active["position_id"], "expression": active["expression"],
                               "side": active["side"], "bracket": active["bracket"], "cash": 0.0,
                               "p_win": p_old, "detail": "no_complement_ask"})
                continue
            comp_ask = float(comp_ask)
            close_cost = comp_ask + official_fee(comp_ask)
            close_value = 1.0 - close_cost
            candidates = [c for c in _eligible_expressions(record) if c["bracket"] != active["bracket"] or c["side"] != active["side"]]
            best_new = candidates[0] if candidates else None
            close_ev = close_value - p_old
            rebalance_ev = -math.inf
            if best_new is not None:
                new_cost = (best_new["ask"] + adverse) + official_fee(best_new["ask"] + adverse)
                rebalance_ev = close_value + (best_new["p_win"] - new_cost) - p_old
            if rebalance_ev > buffer and rebalance_ev >= close_ev:
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "close_for_rebalance",
                               "position_id": active["position_id"], "expression": active["expression"],
                               "side": active["side"], "bracket": active["bracket"],
                               "cash": -shares * close_cost, "p_win": p_old,
                               "detail": f"lock_complement_ask={comp_ask:.3f}"})
                positions.append({**active, "closed_ts": ts, "exit": "locked_complement", "payout_per_share": 1.0})
                position_seq += 1
                new_ask = best_new["ask"] + adverse
                active = {"position_id": f"{city}|{date}|{position_seq}", "city": city, "target_date": date,
                          "opened_ts": ts, "expression": best_new["expression"], "side": best_new["side"],
                          "bracket": best_new["bracket"], "entry_ask": new_ask,
                          "entry_fee": official_fee(new_ask), "locked": False}
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "reopen",
                               "position_id": active["position_id"], "expression": best_new["expression"],
                               "side": best_new["side"], "bracket": best_new["bracket"],
                               "cash": -shares * (new_ask + active["entry_fee"]), "p_win": best_new["p_win"],
                               "detail": f"rebalance_ev={rebalance_ev:.4f}"})
            elif close_ev > buffer:
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "close_lock",
                               "position_id": active["position_id"], "expression": active["expression"],
                               "side": active["side"], "bracket": active["bracket"],
                               "cash": -shares * close_cost, "p_win": p_old,
                               "detail": f"close_ev={close_ev:.4f}"})
                positions.append({**active, "closed_ts": ts, "exit": "locked_complement", "payout_per_share": 1.0})
                active = {**active, "locked": True}
            else:
                ledger.append({"city": city, "target_date": date, "ts": ts, "action": "hold",
                               "position_id": active["position_id"], "expression": active["expression"],
                               "side": active["side"], "bracket": active["bracket"], "cash": 0.0,
                               "p_win": p_old, "detail": f"close_ev={close_ev:.4f}"})
        if active is not None and not active["locked"]:
            win = _win(active["side"], active["bracket"], winner)
            positions.append({**active, "closed_ts": None, "exit": "held_to_settlement",
                              "payout_per_share": win if win is not None else math.nan})
    ledger_frame = pd.DataFrame(ledger)
    position_frame = pd.DataFrame(positions)
    if not ledger_frame.empty:
        payout_rows = []
        for pos in positions:
            payout = pos.get("payout_per_share")
            if payout is not None and np.isfinite(float(payout or np.nan)) and float(payout) > 0:
                payout_rows.append({"city": pos["city"], "target_date": pos["target_date"],
                                    "ts": pos.get("closed_ts") or "settlement", "action": "payout",
                                    "position_id": pos["position_id"], "expression": pos["expression"],
                                    "side": pos["side"], "bracket": pos["bracket"],
                                    "cash": shares * float(payout), "p_win": math.nan,
                                    "detail": pos["exit"]})
        ledger_frame = pd.concat([ledger_frame, pd.DataFrame(payout_rows)], ignore_index=True)
        ledger_frame["route"] = route
    return ledger_frame, position_frame


def execution_summary(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    rows = []
    settled = trades.dropna(subset=["pnl"])
    for keys, group in settled.groupby(by, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        daily = group.groupby("target_date", as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
        roi, low, high = roi_date_block_ci(daily)
        rows.append({**dict(zip(by, keys)), "rows": len(group), "dates": group["target_date"].nunique(),
                     "wins": float(group["win"].sum()), "cost": float(group["cost"].sum()),
                     "pnl": float(group["pnl"].sum()), "roi": roi, "roi_ci_low": low, "roi_ci_high": high})
    return pd.DataFrame(rows)


def stability_slices(trades: pd.DataFrame, route: str) -> dict[str, Any]:
    settled = trades.dropna(subset=["pnl"])
    if settled.empty:
        return {"route": route, "status": "no_settled_trades"}
    daily = settled.groupby("target_date", as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    cum = daily.sort_values("target_date")["pnl"].cumsum()
    drawdown = float((cum - cum.cummax()).min())
    top2 = daily.nlargest(2, "pnl")["target_date"].tolist()
    rest = settled[~settled["target_date"].isin(top2)]
    rest_daily = rest.groupby("target_date", as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    roi_all, _, _ = roi_date_block_ci(daily)
    roi_top_removed, low, high = roi_date_block_ci(rest_daily)
    return {"route": route, "roi": roi_all, "top2_days_removed_roi": roi_top_removed,
            "top2_days_removed_ci": [low, high], "top2_days": top2, "max_drawdown_usd": drawdown,
            "trades": len(settled), "dates": int(daily.shape[0])}
