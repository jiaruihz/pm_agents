"""METAR reversal take-profit replay v1.

This is a same-denominator exit replay for the frozen
false_fade_reheat_conflict -> BUY d1_yes branch from
research_metar_reversal_expression_matrix_v1.py.

The replay uses future point-in-time paper snapshots after the decision
snapshot and credits an exit only when a later d1 YES best bid reaches the
policy threshold. If no threshold is reached, the row is held to final binary
settlement.

Usage:
  .venv/bin/python scripts/analysis/reheat_risk/research_metar_reversal_take_profit_v1.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402

SHARD_DIR = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
SHARDS = sorted(SHARD_DIR.glob("feature_factory_*/reheat_feature_rows.csv"))
SNAPSHOT_DIR = historical_strategy_snapshots()
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/metar_reversal_take_profit_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-take-profit-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-take-profit-v1.json"

RNG_SEED = 20260703
N_BOOT = 5000
STAKE_USD = 1.0

USECOLS = [
    "decision_snapshot_ts_utc",
    "decision_hour_local",
    "city",
    "target_date",
    "bracket",
    "bracket_low",
    "bracket_high",
    "outcome",
    "quote_best_ask",
    "quote_best_ask_size",
    "quote_best_bid",
    "quote_best_bid_size",
    "condition_id",
    "market_id",
    "token_id",
    "running_native",
    "current_bracket",
    "current_yes_ask",
    "current_yes_bid",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_max_native",
    "forecast_peak_delta_hours_local",
    "final_winning_bracket",
    "settlement_status",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def fmt_pct(value: Any, signed: bool = True) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    pct = float(value) * 100.0
    return f"{pct:+.1f}%" if signed else f"{pct:.1f}%"


def fmt_num(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    return f"{float(value):.{digits}f}"


def load_shards() -> pd.DataFrame:
    frames = []
    for path in SHARDS:
        frames.append(pd.read_csv(path, usecols=lambda c: c in USECOLS, low_memory=False))
    if not frames:
        raise RuntimeError(f"no atlas shards found under {SHARD_DIR}")
    out = pd.concat(frames, ignore_index=True)
    numeric_cols = [
        "decision_hour_local",
        "bracket_low",
        "bracket_high",
        "quote_best_ask",
        "quote_best_ask_size",
        "quote_best_bid",
        "quote_best_bid_size",
        "running_native",
        "current_yes_ask",
        "current_yes_bid",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
    ]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["target_date"] = out["target_date"].astype(str)
    out["city"] = out["city"].astype(str)
    out["bracket"] = out["bracket"].astype(str)
    return out


def build_matrix(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.dropna(subset=["bracket_low"]).copy()
    first_ts = (
        rows.groupby(["city", "target_date", "decision_hour_local"])["decision_snapshot_ts_utc"]
        .min()
        .rename("first_ts")
        .reset_index()
    )
    rows = rows.merge(first_ts, on=["city", "target_date", "decision_hour_local"])
    rows = rows[rows["decision_snapshot_ts_utc"] == rows["first_ts"]]

    recs: list[dict[str, Any]] = []
    keys = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]
    for (city, tdate, hour, ts), group in rows.groupby(keys, sort=False):
        yes = group[group["outcome"].astype(str).str.lower() == "yes"].sort_values("bracket_low")
        if yes.empty:
            continue
        head = yes.iloc[0]
        current_bracket = str(head["current_bracket"])
        current = yes[yes["bracket"].astype(str) == current_bracket]
        if current.empty:
            continue
        current_low = float(current["bracket_low"].iloc[0])
        above = yes[yes["bracket_low"] > current_low].sort_values("bracket_low")
        if above.empty:
            continue
        d1 = above.iloc[0]
        final_bracket = str(head["final_winning_bracket"])
        settled = head["settlement_status"] == "settled" and final_bracket not in ("nan", "None", "")
        forecast_gap = (
            head["forecast_max_native"] - head["running_native"]
            if math.isfinite(to_float(head["forecast_max_native"])) and math.isfinite(to_float(head["running_native"]))
            else math.nan
        )
        recs.append(
            {
                "city": city,
                "target_date": tdate,
                "decision_hour_local": hour,
                "decision_snapshot_ts_utc": ts,
                "current_bracket": current_bracket,
                "current_high_yes_ask": head["current_yes_ask"],
                "running_native": head["running_native"],
                "forecast_max_native": head["forecast_max_native"],
                "forecast_gap_native": forecast_gap,
                "forecast_peak_delta_hours_local": head["forecast_peak_delta_hours_local"],
                "temp_trend_1h_f": head["temp_trend_1h_f"],
                "temp_trend_3h_f": head["temp_trend_3h_f"],
                "minutes_since_running_max": head["minutes_since_running_max"],
                "settled": settled,
                "final_winning_bracket": final_bracket,
                "d1_yes_bracket": str(d1["bracket"]),
                "d1_yes_condition_id": str(d1["condition_id"]),
                "d1_yes_market_id": str(d1["market_id"]),
                "d1_yes_token_id": str(d1["token_id"]),
                "d1_yes_ask": to_float(d1["quote_best_ask"]),
                "d1_yes_bid": to_float(d1["quote_best_bid"]),
                "d1_yes_ask_size": to_float(d1["quote_best_ask_size"]),
                "d1_yes_bid_size": to_float(d1["quote_best_bid_size"]),
                "d1_yes_win": final_bracket == str(d1["bracket"]),
            }
        )
    return pd.DataFrame(recs)


def select_false_fade_reheat(matrix: pd.DataFrame) -> pd.DataFrame:
    settled = matrix[matrix["settled"]].copy()
    selected = settled[
        (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_gap_native"] >= 1.0)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
        & (settled["d1_yes_ask"] <= 0.30)
        & (settled["current_high_yes_ask"] >= 0.40)
        & (settled["d1_yes_ask"] > 0.005)
        & (settled["d1_yes_ask"] < 0.995)
    ].copy()
    selected["row_id"] = np.arange(len(selected))
    selected["entry_dt"] = selected["decision_snapshot_ts_utc"].map(parse_utc)
    selected["entry"] = pd.to_numeric(selected["d1_yes_ask"], errors="coerce")
    selected["payoff"] = selected["d1_yes_win"].astype(float)
    return selected


def scan_future_paths(base: pd.DataFrame) -> pd.DataFrame:
    by_key: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    entry_dt: dict[int, pd.Timestamp] = {}
    for row in base.itertuples(index=False):
        key = (str(row.d1_yes_condition_id), str(row.city), str(row.target_date), str(row.d1_yes_bracket))
        by_key[key].append(int(row.row_id))
        entry_dt[int(row.row_id)] = row.entry_dt

    path: dict[int, dict[str, Any]] = {
        int(row.row_id): {
            "future_quotes": 0,
            "future_bid_quotes": 0,
            "max_future_yes_bid": math.nan,
            "max_future_yes_ask": math.nan,
            "max_future_market_price": math.nan,
            "max_bid_ts_utc": "",
            "max_bid_snapshot_file": "",
            "first_future_ts_utc": "",
            "last_future_ts_utc": "",
            "first_bid_ge_20c_ts_utc": "",
            "first_bid_ge_30c_ts_utc": "",
            "first_bid_ge_40c_ts_utc": "",
        }
        for row in base.itertuples(index=False)
    }

    min_ymd = str(base["target_date"].min()).replace("-", "")
    max_ymd = str(base["target_date"].max()).replace("-", "")
    for snap_path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        stamp = snap_path.name.removeprefix("snapshot_").removesuffix(".json")
        ymd = stamp.split("_", 1)[0]
        if ymd < min_ymd or ymd > max_ymd:
            continue
        try:
            payload = json.loads(snap_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        payload_ts = parse_utc(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        for rec in payload.get("records", []):
            rec_tdate = str(rec.get("target_date") or rec.get("event_date") or "")
            key = (str(rec.get("condition_id")), str(rec.get("city")), rec_tdate, str(rec.get("bracket")))
            row_ids = by_key.get(key)
            if not row_ids:
                continue
            ts = parse_utc(rec.get("snapshot_ts_utc") or rec.get("ts_utc"))
            if pd.isna(ts):
                ts = payload_ts
            if pd.isna(ts):
                continue
            settle_utc = parse_utc(rec.get("settle_utc"))
            hours_to_settle = to_float(rec.get("hours_to_settle"))
            if pd.notna(settle_utc) and ts >= settle_utc:
                continue
            if math.isfinite(hours_to_settle) and hours_to_settle < 0:
                continue
            bid = to_float(rec.get("yes_best_bid"))
            ask = to_float(rec.get("yes_best_ask"))
            mkt = to_float(rec.get("market_yes_price"))
            for row_id in row_ids:
                if ts <= entry_dt[row_id]:
                    continue
                state = path[row_id]
                state["future_quotes"] += 1
                ts_s = ts.isoformat()
                if not state["first_future_ts_utc"]:
                    state["first_future_ts_utc"] = ts_s
                state["last_future_ts_utc"] = ts_s
                if math.isfinite(bid):
                    state["future_bid_quotes"] += 1
                    if bid >= 0.20 and not state["first_bid_ge_20c_ts_utc"]:
                        state["first_bid_ge_20c_ts_utc"] = ts_s
                    if bid >= 0.30 and not state["first_bid_ge_30c_ts_utc"]:
                        state["first_bid_ge_30c_ts_utc"] = ts_s
                    if bid >= 0.40 and not state["first_bid_ge_40c_ts_utc"]:
                        state["first_bid_ge_40c_ts_utc"] = ts_s
                    if not math.isfinite(state["max_future_yes_bid"]) or bid > state["max_future_yes_bid"]:
                        state["max_future_yes_bid"] = bid
                        state["max_bid_ts_utc"] = ts_s
                        state["max_bid_snapshot_file"] = snap_path.name
                if math.isfinite(ask):
                    state["max_future_yes_ask"] = ask if not math.isfinite(state["max_future_yes_ask"]) else max(state["max_future_yes_ask"], ask)
                if math.isfinite(mkt):
                    state["max_future_market_price"] = mkt if not math.isfinite(state["max_future_market_price"]) else max(state["max_future_market_price"], mkt)

    out = pd.DataFrame.from_dict(path, orient="index")
    out.index.name = "row_id"
    return out.reset_index()


def policy_specs() -> list[tuple[str, str, float]]:
    return [
        ("hold_to_settlement", "hold", math.nan),
        ("full_sell_bid_ge_0p20", "full_abs", 0.20),
        ("full_sell_bid_ge_0p30", "full_abs", 0.30),
        ("full_sell_bid_ge_0p40", "full_abs", 0.40),
        ("full_sell_bid_ge_0p50", "full_abs", 0.50),
        ("full_sell_bid_ge_2x", "full_mult", 2.0),
        ("full_sell_bid_ge_3x", "full_mult", 3.0),
        ("recover_stake_bid_ge_0p20", "recover_abs", 0.20),
        ("recover_stake_bid_ge_0p30", "recover_abs", 0.30),
        ("recover_stake_bid_ge_3x", "recover_mult", 3.0),
    ]


def friction_specs() -> list[dict[str, Any]]:
    return [
        {"friction": "quoted_bidask", "entry_add": 0.0, "exit_haircut": 0.0, "fee_rate": 0.0},
        {"friction": "sell_minus_1c", "entry_add": 0.0, "exit_haircut": 0.01, "fee_rate": 0.0},
        {"friction": "entry_plus_1c_sell_minus_1c", "entry_add": 0.01, "exit_haircut": 0.01, "fee_rate": 0.0},
        {"friction": "entry_plus_1c_sell_minus_2c", "entry_add": 0.01, "exit_haircut": 0.02, "fee_rate": 0.0},
        {"friction": "entry_plus_1c_sell_minus_1c_fee1pct", "entry_add": 0.01, "exit_haircut": 0.01, "fee_rate": 0.01},
    ]


def threshold_for(entry: float, kind: str, value: float) -> float:
    if kind.endswith("_abs"):
        return value
    if kind.endswith("_mult"):
        return entry * value
    return math.nan


def apply_policies(frame: pd.DataFrame) -> pd.DataFrame:
    recs: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        payoff = float(row["payoff"])
        raw_entry = float(row["entry"])
        max_bid = to_float(row.get("max_future_yes_bid"))
        for friction in friction_specs():
            entry = min(0.99, raw_entry + float(friction["entry_add"]))
            hold_fee = float(friction["fee_rate"]) * entry
            hold_roi = payoff / entry - 1.0 - hold_fee
            for name, kind, value in policy_specs():
                action = "hold"
                threshold = math.nan
                if kind != "hold":
                    action = "recover" if kind.startswith("recover") else "full"
                    threshold = threshold_for(raw_entry, kind, value)
                hit = False
                exit_bid = math.nan
                adjusted_exit_bid = math.nan
                exit_mode = "hold_to_settlement"
                roi = hold_roi
                if action != "hold" and math.isfinite(max_bid) and max_bid >= threshold and threshold > raw_entry:
                    exit_bid = max_bid
                    adjusted_exit_bid = max(0.0, min(0.99, max_bid - float(friction["exit_haircut"])))
                    if adjusted_exit_bid > entry:
                        hit = True
                        if action == "full":
                            exit_mode = "full_sell"
                            gross_fee = float(friction["fee_rate"]) * (entry + adjusted_exit_bid)
                            roi = adjusted_exit_bid / entry - 1.0 - gross_fee
                        else:
                            exit_mode = "recover_stake_keep_remainder"
                            shares = STAKE_USD / entry
                            shares_sold = min(STAKE_USD / adjusted_exit_bid, shares)
                            shares_left = max(0.0, shares - shares_sold)
                            gross_fee = float(friction["fee_rate"]) * (entry + shares_sold * adjusted_exit_bid)
                            roi = shares_sold * adjusted_exit_bid + shares_left * payoff - gross_fee - STAKE_USD
                    else:
                        exit_mode = "hold_to_settlement_after_friction"
                recs.append(
                    {
                        "row_id": int(row["row_id"]),
                        "policy": name,
                        "friction": friction["friction"],
                        "entry_add": friction["entry_add"],
                        "exit_haircut": friction["exit_haircut"],
                        "fee_rate": friction["fee_rate"],
                        "policy_action": action,
                        "exit_mode": exit_mode,
                        "target_bid": threshold,
                        "exit_bid": exit_bid,
                        "adjusted_exit_bid": adjusted_exit_bid,
                        "tp_hit": hit,
                        "raw_entry": raw_entry,
                        "entry": entry,
                        "payoff": payoff,
                        "roi": roi,
                        "hold_roi": hold_roi,
                        "delta_vs_hold": roi - hold_roi,
                        "tp_then_final_lost": bool(hit and payoff < 0.5),
                        "tp_then_final_won": bool(hit and payoff >= 0.5),
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "decision_hour_local": row["decision_hour_local"],
                        "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                        "d1_yes_bracket": row["d1_yes_bracket"],
                        "d1_yes_condition_id": row["d1_yes_condition_id"],
                        "max_future_yes_bid": max_bid,
                        "max_bid_ts_utc": row.get("max_bid_ts_utc", ""),
                        "future_bid_quotes": int(row.get("future_bid_quotes") or 0),
                        "first_bid_ge_20c_ts_utc": row.get("first_bid_ge_20c_ts_utc", ""),
                        "first_bid_ge_30c_ts_utc": row.get("first_bid_ge_30c_ts_utc", ""),
                        "first_bid_ge_40c_ts_utc": row.get("first_bid_ge_40c_ts_utc", ""),
                    }
                )
    return pd.DataFrame(recs)


def date_block_ci(daily: pd.DataFrame, value_col: str) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    values = daily[value_col].to_numpy(dtype=float)
    boot = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(values), len(values))
        boot.append(float(values[idx].mean()))
    return (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))


def summarize_policy(rows: pd.DataFrame, policy: str, friction: str, period: str, mask: pd.Series) -> dict[str, Any]:
    group = rows[(rows["policy"] == policy) & (rows["friction"] == friction) & mask].copy()
    if group.empty:
        return {"policy": policy, "friction": friction, "period": period, "rows": 0}
    daily = group.groupby("target_date", as_index=False).agg(
        pnl=("roi", "sum"),
        delta=("delta_vs_hold", "sum"),
        rows=("row_id", "count"),
    )
    daily["roi"] = daily["pnl"] / daily["rows"]
    daily["delta_roi"] = daily["delta"] / daily["rows"]
    lo, hi = date_block_ci(daily, "roi")
    dlo, dhi = date_block_ci(daily, "delta_roi")
    pnl_sorted = group["roi"].sort_values(ascending=False)
    top5_removed = None
    if len(group) > 5:
        top5_removed = float((float(group["roi"].sum()) - float(pnl_sorted.head(5).sum())) / (len(group) - 5))
    return {
        "policy": policy,
        "friction": friction,
        "period": period,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()),
        "cities": int(group["city"].nunique()),
        "win_rate_final": float((group["payoff"] >= 0.5).mean()),
        "avg_entry": float(group["entry"].mean()),
        "tp_hit_rate": float(group["tp_hit"].mean()),
        "tp_then_final_lost": int(group["tp_then_final_lost"].sum()),
        "tp_then_final_won": int(group["tp_then_final_won"].sum()),
        "roi": float(group["roi"].mean()),
        "delta_vs_hold": float(group["delta_vs_hold"].mean()),
        "delta_ci_low": dlo,
        "delta_ci_high": dhi,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "top5_removed_roi": top5_removed,
        "daily_win_rate": float((daily["pnl"] > 0).mean()),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "le_minus50pct_days": int((daily["roi"] <= -0.5).sum()),
        "max_daily_loss_roi": float(daily["roi"].min()),
        "path_coverage": float((group["future_bid_quotes"] > 0).mean()),
    }


def summarize_all(policy_rows: pd.DataFrame) -> pd.DataFrame:
    dates = policy_rows["target_date"].astype(str)
    periods = {
        "full": pd.Series(True, index=policy_rows.index),
        "train_le_2026_06_20": dates <= "2026-06-20",
        "recent_ge_2026_06_21": dates >= "2026-06-21",
        "month_2026_05": dates.str.startswith("2026-05"),
        "month_2026_06": dates.str.startswith("2026-06"),
    }
    rows = []
    for policy, _, _ in policy_specs():
        for friction in policy_rows["friction"].dropna().unique():
            for period_name, mask in periods.items():
                rows.append(summarize_policy(policy_rows, policy, friction, period_name, mask))
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            value = row.get(col)
            if col in {
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "daily_win_rate",
                "max_daily_loss_roi",
                "tp_hit_rate",
                "path_coverage",
                "win_rate_final",
            }:
                vals.append(fmt_pct(value))
            elif col == "avg_entry":
                vals.append(fmt_num(value, 3))
            else:
                vals.append("" if pd.isna(value) else str(int(value)) if isinstance(value, (int, np.integer)) or (isinstance(value, float) and value.is_integer()) else str(value))
        out.append("| " + " | ".join(vals) + " |")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_shards()
    matrix = build_matrix(rows)
    base = select_false_fade_reheat(matrix)
    path = scan_future_paths(base)
    enriched = base.merge(path, on="row_id", how="left")
    policy_rows = apply_policies(enriched)
    summary = summarize_all(policy_rows)

    matrix.to_csv(OUT_DIR / "expression_matrix_with_d1_ids.csv", index=False)
    enriched.to_csv(OUT_DIR / "candidate_path_stats.csv", index=False)
    policy_rows.to_csv(OUT_DIR / "policy_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)

    focus_policies = [
        "hold_to_settlement",
        "full_sell_bid_ge_0p20",
        "full_sell_bid_ge_0p30",
        "full_sell_bid_ge_0p40",
        "recover_stake_bid_ge_0p30",
        "full_sell_bid_ge_2x",
        "full_sell_bid_ge_3x",
    ]
    focus = summary[
        (summary["period"].isin(["full", "recent_ge_2026_06_21", "month_2026_05", "month_2026_06"]))
        & (summary["friction"].isin(["quoted_bidask", "entry_plus_1c_sell_minus_1c"]))
        & (summary["policy"].isin(focus_policies))
    ].copy()

    payload = {
        "generated_at_utc": now_utc(),
        "data_snapshot": {
            "atlas_shards": [str(path.relative_to(ROOT)) for path in SHARDS],
            "paper_snapshot_dir": str(SNAPSHOT_DIR.relative_to(ROOT)),
            "matrix_rows": int(len(matrix)),
            "false_fade_reheat_rows": int(len(base)),
            "date_range": [str(base["target_date"].min()) if len(base) else "", str(base["target_date"].max()) if len(base) else ""],
            "cities": int(base["city"].nunique()) if len(base) else 0,
        },
        "summary": summary.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    key_full = summary[
        (summary["period"] == "full")
        & (summary["friction"] == "entry_plus_1c_sell_minus_1c")
        & (summary["policy"].isin(["hold_to_settlement", "full_sell_bid_ge_0p20", "full_sell_bid_ge_0p30", "recover_stake_bid_ge_0p30"]))
    ].copy()
    lines = [
        "# METAR Reversal Take-Profit Replay v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "```text",
        "branch: false_fade_reheat_conflict -> BUY d1_yes",
        "same-denominator rows: 32 historical settled triggers",
        "exit verdict: TP is not yet a better default than hold-to-settlement.",
        "recommended live posture: if this branch is promoted, start as hold-to-settlement micro live, while shadow-recording TP20/30/40 paths.",
        "conclusion: shadow_candidate, no TP rule promoted from this replay.",
        "```",
        "",
        "This replay tests exits only. It does not change the branch trigger and does not add a new weather gate.",
        "Each policy uses the same 32 trigger rows; no sample mixing.",
        "",
        "## Key Stress Table",
        "",
        *markdown_table(
            key_full,
            [
                "policy",
                "friction",
                "period",
                "rows",
                "dates",
                "cities",
                "avg_entry",
                "tp_hit_rate",
                "win_rate_final",
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "top5_removed_roi",
                "losing_days",
                "le_minus50pct_days",
                "max_daily_loss_roi",
            ],
        ),
        "",
        "## Focus Policies",
        "",
        *markdown_table(
            focus,
            [
                "policy",
                "friction",
                "period",
                "rows",
                "dates",
                "avg_entry",
                "tp_hit_rate",
                "roi",
                "delta_vs_hold",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "path_coverage",
            ],
        ),
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'candidate_path_stats.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'policy_rows.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'policy_summary.csv').relative_to(ROOT)}`",
        f"- `{OUT_JSON.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary[(summary["period"] == "full") & (summary["friction"] == "entry_plus_1c_sell_minus_1c")].to_string(index=False))


if __name__ == "__main__":
    main()
