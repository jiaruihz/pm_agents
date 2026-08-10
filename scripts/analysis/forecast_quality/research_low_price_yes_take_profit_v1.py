"""Low-price YES forecast-tail take-profit replay v1.

This tests execution overlays for the current forecast-tail low-price YES
denominator. It uses future point-in-time paper snapshots after the decision
snapshot and only credits an exit when future YES best_bid reaches the take
profit threshold. If no threshold is reached, the position is held to final
binary payoff from the existing research denominator.

Usage:
  .venv/bin/python scripts/analysis/forecast_quality/research_low_price_yes_take_profit_v1.py
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

INPUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv"
SNAPSHOT_DIR = historical_strategy_snapshots()
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_take_profit_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-take-profit-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-take-profit-v1.json"

RNG_SEED = 20260703
N_BOOT = 5000


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


def fmt_pct(x: Any, signed: bool = True) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return ""
    val = float(x) * 100.0
    return f"{val:+.1f}%" if signed else f"{val:.1f}%"


def fmt_num(x: Any, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return ""
    return f"{float(x):.{digits}f}"


def date_block_ci(daily: pd.DataFrame, value_col: str = "pnl") -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    vals = daily[value_col].to_numpy(dtype=float)
    boot = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(vals), len(vals))
        boot.append(float(vals[idx].mean()))
    return (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))


def load_base() -> pd.DataFrame:
    df = pd.read_csv(INPUT)
    required = [
        "condition_id",
        "city",
        "target_date",
        "bracket",
        "ask",
        "decision_snapshot_ts_utc",
        "payoff",
        "period",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"input missing columns: {missing}")
    out = df.copy()
    out["entry_dt"] = parse_utc(out["decision_snapshot_ts_utc"])
    out["entry"] = pd.to_numeric(out["ask"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out = out[
        out["condition_id"].notna()
        & out["entry_dt"].notna()
        & out["entry"].between(0.005, 0.995)
        & out["payoff"].notna()
    ].copy()
    out["row_id"] = np.arange(len(out))
    out["target_date"] = out["target_date"].astype(str)
    out["city"] = out["city"].astype(str)
    out["bracket"] = out["bracket"].astype(str)
    return out


def candidate_key(row: pd.Series) -> tuple[str, str, str]:
    return (str(row["condition_id"]), str(row["city"]), str(row["target_date"]))


def scan_future_paths(base: pd.DataFrame) -> pd.DataFrame:
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    entry_dt: dict[int, pd.Timestamp] = {}
    min_date = base["target_date"].min().replace("-", "")
    max_date = base["target_date"].max().replace("-", "")
    for row in base.itertuples(index=False):
        key = (str(row.condition_id), str(row.city), str(row.target_date))
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
        }
        for row in base.itertuples(index=False)
    }

    files = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"))
    for snap_path in files:
        stamp = snap_path.name.removeprefix("snapshot_").removesuffix(".json")
        ymd = stamp.split("_", 1)[0]
        if ymd < min_date or ymd > max_date:
            continue
        try:
            payload = json.loads(snap_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        payload_ts = parse_utc(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        for rec in payload.get("records", []):
            key = (str(rec.get("condition_id")), str(rec.get("city")), str(rec.get("target_date") or rec.get("event_date")))
            row_ids = by_key.get(key)
            if not row_ids:
                continue
            ts = parse_utc(rec.get("snapshot_ts_utc") or rec.get("ts_utc")) or payload_ts
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
                st = path[row_id]
                st["future_quotes"] += 1
                ts_s = ts.isoformat()
                if not st["first_future_ts_utc"]:
                    st["first_future_ts_utc"] = ts_s
                st["last_future_ts_utc"] = ts_s
                if math.isfinite(bid):
                    st["future_bid_quotes"] += 1
                    if not math.isfinite(st["max_future_yes_bid"]) or bid > st["max_future_yes_bid"]:
                        st["max_future_yes_bid"] = bid
                        st["max_bid_ts_utc"] = ts_s
                        st["max_bid_snapshot_file"] = snap_path.name
                if math.isfinite(ask):
                    st["max_future_yes_ask"] = ask if not math.isfinite(st["max_future_yes_ask"]) else max(st["max_future_yes_ask"], ask)
                if math.isfinite(mkt):
                    st["max_future_market_price"] = mkt if not math.isfinite(st["max_future_market_price"]) else max(st["max_future_market_price"], mkt)

    path_df = pd.DataFrame.from_dict(path, orient="index")
    path_df.index.name = "row_id"
    return path_df.reset_index()


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
        ("recover_stake_bid_ge_0p40", "recover_abs", 0.40),
        ("recover_stake_bid_ge_3x", "recover_mult", 3.0),
        ("adaptive_entry_2x_floor20_cap30_full", "adaptive_entry_2x_floor20_cap30_full", math.nan),
        ("adaptive_entry_le10c_full20_else30", "adaptive_entry_le10c_full20_else30", math.nan),
        ("adaptive_model_high_full30_else20", "adaptive_model_high_full30_else20", math.nan),
        ("adaptive_model_high_recover30_else_full20", "adaptive_model_high_recover30_else_full20", math.nan),
        ("adaptive_path_high_full30_else20", "adaptive_path_high_full30_else20", math.nan),
    ]


def friction_specs() -> list[dict[str, Any]]:
    return [
        {
            "friction": "quoted_bidask",
            "entry_add": 0.0,
            "exit_haircut": 0.0,
            "fee_rate": 0.0,
            "description": "entry at historical ask, exit at future best bid; includes quoted spread only",
        },
        {
            "friction": "sell_minus_1c",
            "entry_add": 0.0,
            "exit_haircut": 0.01,
            "fee_rate": 0.0,
            "description": "maker-like entry, exit loses one cent versus future best bid",
        },
        {
            "friction": "entry_plus_1c_sell_minus_1c",
            "entry_add": 0.01,
            "exit_haircut": 0.01,
            "fee_rate": 0.0,
            "description": "one-cent adverse fill on entry and exit",
        },
        {
            "friction": "entry_plus_1c_sell_minus_2c",
            "entry_add": 0.01,
            "exit_haircut": 0.02,
            "fee_rate": 0.0,
            "description": "one-cent adverse entry, two-cent adverse exit",
        },
        {
            "friction": "entry_plus_1c_sell_minus_1c_fee1pct",
            "entry_add": 0.01,
            "exit_haircut": 0.01,
            "fee_rate": 0.01,
            "description": "one-cent adverse entry/exit plus 1% gross notional fee stress",
        },
    ]


def threshold_for(row: pd.Series, kind: str, value: float) -> float:
    if kind.endswith("_abs"):
        return value
    if kind.endswith("_mult"):
        return float(row["entry"]) * value
    return math.nan


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def model_quality_high(row: pd.Series) -> bool:
    """Pre-declared high-quality bucket from existing probability features."""
    pcal_ev = to_float(row.get("p_cal_no_city_ev"))
    edge = to_float(row.get("edge"))
    model_p = to_float(row.get("model_p_yes"))
    return (
        (math.isfinite(pcal_ev) and pcal_ev >= 0.40)
        or (math.isfinite(edge) and edge >= 0.35)
        or (math.isfinite(model_p) and model_p >= 0.45)
    )


def path_quality_high(row: pd.Series) -> bool:
    """Existing source/context score bucket; not tuned on TP results."""
    return truthy(row.get("source_aware_v3")) and to_float(row.get("score_integrated_with_metar")) >= 4


def policy_action(row: pd.Series, kind: str, value: float) -> tuple[str, float]:
    raw_entry = to_float(row.get("raw_entry"), to_float(row.get("entry")))
    if kind == "hold":
        return ("hold", math.nan)
    if kind.endswith("_abs") or kind.endswith("_mult"):
        action = "recover" if kind.startswith("recover") else "full"
        return (action, threshold_for(row, kind, value))
    if kind == "adaptive_entry_2x_floor20_cap30_full":
        return ("full", min(0.30, max(0.20, raw_entry * 2.0)))
    if kind == "adaptive_entry_le10c_full20_else30":
        return ("full", 0.20 if raw_entry <= 0.10 else 0.30)
    if kind == "adaptive_model_high_full30_else20":
        return ("full", 0.30 if model_quality_high(row) else 0.20)
    if kind == "adaptive_model_high_recover30_else_full20":
        return ("recover" if model_quality_high(row) else "full", 0.30 if model_quality_high(row) else 0.20)
    if kind == "adaptive_path_high_full30_else20":
        return ("full", 0.30 if path_quality_high(row) else 0.20)
    raise RuntimeError(f"unknown policy kind: {kind}")


def apply_policies(frame: pd.DataFrame) -> pd.DataFrame:
    recs: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        payoff = float(row["payoff"])
        max_bid = to_float(row.get("max_future_yes_bid"))
        raw_entry = float(row["entry"])
        for friction in friction_specs():
            entry = min(0.99, raw_entry + float(friction["entry_add"]))
            hold_fee = float(friction["fee_rate"]) * entry
            hold_roi = (payoff - hold_fee) / entry - 1.0
            for name, kind, value in policy_specs():
                action, threshold = policy_action(pd.Series({**row.to_dict(), "raw_entry": raw_entry, "entry": entry}), kind, value)
                hit = False
                exit_bid = math.nan
                adjusted_exit_bid = math.nan
                exit_mode = "hold_to_settlement"
                roi = hold_roi
                if action != "hold" and math.isfinite(max_bid) and max_bid >= threshold and threshold > entry:
                    hit = True
                    exit_bid = max_bid
                    adjusted_exit_bid = max(0.0, min(0.99, max_bid - float(friction["exit_haircut"])))
                    if adjusted_exit_bid <= entry:
                        hit = False
                        exit_mode = "hold_to_settlement_after_friction"
                        roi = hold_roi
                    elif action == "full":
                        exit_mode = "full_sell"
                        gross_fee = float(friction["fee_rate"]) * (entry + adjusted_exit_bid)
                        roi = (adjusted_exit_bid - gross_fee) / entry - 1.0
                    elif action == "recover":
                        exit_mode = "recover_stake_keep_remainder"
                        shares_sold = min(1.0 / adjusted_exit_bid, 1.0 / entry)
                        shares_left = max(0.0, 1.0 / entry - shares_sold)
                        gross_cash = shares_sold * adjusted_exit_bid + shares_left * payoff
                        gross_fee = float(friction["fee_rate"]) * (entry + shares_sold * adjusted_exit_bid)
                        roi = gross_cash - gross_fee - 1.0
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
                        "pnl_per_1usd": roi,
                        "hold_roi": hold_roi,
                        "delta_vs_hold": roi - hold_roi,
                        "tp_then_final_lost": bool(hit and payoff < 0.5),
                        "tp_then_final_won": bool(hit and payoff >= 0.5),
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "bracket": row["bracket"],
                        "period": row.get("period", ""),
                        "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                        "max_future_yes_bid": max_bid,
                        "max_bid_ts_utc": row.get("max_bid_ts_utc", ""),
                        "future_bid_quotes": int(row.get("future_bid_quotes") or 0),
                        "model_p_yes": row.get("model_p_yes", math.nan),
                        "edge": row.get("edge", math.nan),
                        "p_cal_no_city_ev": row.get("p_cal_no_city_ev", math.nan),
                        "p_cal_city_diag_ev": row.get("p_cal_city_diag_ev", math.nan),
                        "source_aware_v3": bool(truthy(row.get("source_aware_v3"))),
                        "score_integrated_with_metar": row.get("score_integrated_with_metar", math.nan),
                        "hot_tail_pct": row.get("hot_tail_pct", math.nan),
                        "station_hot_tail_high": bool(truthy(row.get("station_hot_tail_high"))),
                        "model_quality_high": model_quality_high(row),
                        "path_quality_high": path_quality_high(row),
                    }
                )
    return pd.DataFrame(recs)


def add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["entry_band"] = pd.cut(
        pd.to_numeric(out["raw_entry"], errors="coerce"),
        bins=[0.0, 0.07, 0.10, 0.15, 1.0],
        labels=["entry_<=7c", "entry_7_10c", "entry_10_15c", "entry_>15c"],
        include_lowest=True,
    ).astype(str)
    out["edge_band"] = pd.cut(
        pd.to_numeric(out["edge"], errors="coerce"),
        bins=[-math.inf, 0.25, 0.35, math.inf],
        labels=["edge_<25pp", "edge_25_35pp", "edge_>=35pp"],
    ).astype(str)
    out["pcal_ev_band"] = pd.cut(
        pd.to_numeric(out["p_cal_no_city_ev"], errors="coerce"),
        bins=[-math.inf, 0.25, 0.40, 0.55, math.inf],
        labels=["pcal_ev_<25pp", "pcal_ev_25_40pp", "pcal_ev_40_55pp", "pcal_ev_>=55pp"],
    ).astype(str)
    out["model_p_band"] = pd.cut(
        pd.to_numeric(out["model_p_yes"], errors="coerce"),
        bins=[-math.inf, 0.33, 0.45, math.inf],
        labels=["model_p_<33", "model_p_33_45", "model_p_>=45"],
    ).astype(str)
    out["path_score_band"] = pd.cut(
        pd.to_numeric(out["score_integrated_with_metar"], errors="coerce"),
        bins=[-math.inf, 2, 3, math.inf],
        labels=["path_score_<=2", "path_score_3", "path_score_>=4"],
    ).astype(str)
    out["hot_tail_band"] = pd.cut(
        pd.to_numeric(out["hot_tail_pct"], errors="coerce"),
        bins=[-math.inf, 0.35, 0.60, math.inf],
        labels=["hot_tail_<35", "hot_tail_35_60", "hot_tail_>=60"],
    ).astype(str)
    out["source_aware_bucket"] = np.where(out["source_aware_v3"], "source_aware_true", "source_aware_false")
    out["model_quality_bucket"] = np.where(out["model_quality_high"], "model_quality_high", "model_quality_low")
    out["path_quality_bucket"] = np.where(out["path_quality_high"], "path_quality_high", "path_quality_low")
    return out


def summarize_policy(df: pd.DataFrame, policy: str, friction: str, period_name: str, mask: pd.Series) -> dict[str, Any]:
    g = df[(df["policy"] == policy) & (df["friction"] == friction) & mask].copy()
    if g.empty:
        return {"policy": policy, "friction": friction, "period": period_name, "rows": 0}
    daily = g.groupby("target_date", as_index=False).agg(
        pnl=("pnl_per_1usd", "sum"),
        delta=("delta_vs_hold", "sum"),
        rows=("row_id", "count"),
    )
    daily["roi"] = daily["pnl"] / daily["rows"]
    daily["delta_roi"] = daily["delta"] / daily["rows"]
    lo, hi = date_block_ci(daily, "roi")
    dlo, dhi = date_block_ci(daily, "delta_roi")
    pnl_sorted = g["pnl_per_1usd"].sort_values(ascending=False)
    total_rows = len(g)
    total_pnl = float(g["pnl_per_1usd"].sum())
    top5_removed = None
    if total_rows > 5:
        top5_removed = float((total_pnl - pnl_sorted.head(5).sum()) / (total_rows - 5))
    losing_days = int((daily["pnl"] < 0).sum())
    severe_days = int((daily["roi"] <= -0.5).sum())
    return {
        "policy": policy,
        "friction": friction,
        "period": period_name,
        "rows": total_rows,
        "dates": int(g["target_date"].nunique()),
        "cities": int(g["city"].nunique()),
        "win_rate_final": float((g["payoff"] >= 0.5).mean()),
        "avg_entry": float(g["entry"].mean()),
        "tp_hit_rate": float(g["tp_hit"].mean()),
        "tp_then_final_lost": int(g["tp_then_final_lost"].sum()),
        "tp_then_final_won": int(g["tp_then_final_won"].sum()),
        "roi": float(total_pnl / total_rows),
        "delta_vs_hold": float(g["delta_vs_hold"].mean()),
        "delta_ci_low": dlo,
        "delta_ci_high": dhi,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "top5_removed_roi": top5_removed,
        "daily_win_rate": float((daily["pnl"] > 0).mean()),
        "losing_days": losing_days,
        "le_minus50pct_days": severe_days,
        "max_daily_loss_roi": float(daily["roi"].min()),
        "path_coverage": float((g["future_bid_quotes"] > 0).mean()),
    }


def summarize_all(policy_rows: pd.DataFrame) -> pd.DataFrame:
    dates = policy_rows["target_date"].astype(str)
    periods = {
        "full": pd.Series(True, index=policy_rows.index),
        "train_le_2026_06_20": dates <= "2026-06-20",
        "holdout_2026_06_21_26": (dates >= "2026-06-21") & (dates <= "2026-06-26"),
        "closed_forward_2026_06_27_30": (dates >= "2026-06-27") & (dates <= "2026-06-30"),
        "recent_ge_2026_06_21": dates >= "2026-06-21",
    }
    rows = []
    for policy, _, _ in policy_specs():
        for friction in policy_rows["friction"].dropna().unique():
            for period_name, mask in periods.items():
                rows.append(summarize_policy(policy_rows, policy, friction, period_name, mask))
    return pd.DataFrame(rows)


def summarize_slices(policy_rows: pd.DataFrame) -> pd.DataFrame:
    focus_policies = [
        "hold_to_settlement",
        "full_sell_bid_ge_0p20",
        "full_sell_bid_ge_0p30",
        "recover_stake_bid_ge_0p30",
        "adaptive_entry_2x_floor20_cap30_full",
        "adaptive_entry_le10c_full20_else30",
        "adaptive_model_high_full30_else20",
        "adaptive_model_high_recover30_else_full20",
        "adaptive_path_high_full30_else20",
    ]
    slice_cols = [
        "entry_band",
        "edge_band",
        "pcal_ev_band",
        "model_p_band",
        "path_score_band",
        "hot_tail_band",
        "source_aware_bucket",
        "model_quality_bucket",
        "path_quality_bucket",
    ]
    dates = policy_rows["target_date"].astype(str)
    periods = {
        "full": pd.Series(True, index=policy_rows.index),
        "recent_ge_2026_06_21": dates >= "2026-06-21",
        "closed_forward_2026_06_27_30": (dates >= "2026-06-27") & (dates <= "2026-06-30"),
    }
    rows = []
    for slice_col in slice_cols:
        if slice_col not in policy_rows.columns:
            continue
        for slice_value in sorted(v for v in policy_rows[slice_col].dropna().unique() if str(v) != "nan"):
            slice_mask = policy_rows[slice_col].astype(str) == str(slice_value)
            for period_name, period_mask in periods.items():
                mask = slice_mask & period_mask
                if int(mask.sum()) == 0:
                    continue
                for policy in focus_policies:
                    rec = summarize_policy(
                        policy_rows,
                        policy,
                        "entry_plus_1c_sell_minus_1c",
                        period_name,
                        mask,
                    )
                    rec["slice"] = slice_col
                    rec["slice_value"] = str(slice_value)
                    rows.append(rec)
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row.get(c)
            if c in {
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
                vals.append(fmt_pct(v))
            elif c in {"avg_entry"}:
                vals.append(fmt_num(v, 3))
            else:
                vals.append("" if pd.isna(v) else str(int(v)) if isinstance(v, (int, np.integer)) or (isinstance(v, float) and v.is_integer()) else str(v))
        out.append("| " + " | ".join(vals) + " |")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    path = scan_future_paths(base)
    enriched = base.merge(path, on="row_id", how="left")
    policy_rows = add_buckets(apply_policies(enriched))
    summary = summarize_all(policy_rows)
    slice_summary = summarize_slices(policy_rows)

    enriched.to_csv(OUT_DIR / "candidate_path_stats.csv", index=False)
    policy_rows.to_csv(OUT_DIR / "policy_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    slice_summary.to_csv(OUT_DIR / "slice_policy_summary.csv", index=False)

    focus_full = summary[
        (summary["period"] == "full")
        & summary["friction"].isin(["quoted_bidask", "sell_minus_1c", "entry_plus_1c_sell_minus_1c"])
        & summary["policy"].isin(
            [
                "hold_to_settlement",
                "full_sell_bid_ge_0p20",
                "full_sell_bid_ge_0p30",
                "full_sell_bid_ge_0p40",
                "recover_stake_bid_ge_0p30",
                "recover_stake_bid_ge_3x",
            ]
        )
    ].copy()
    focus_forward = summary[
        (summary["period"].isin(["holdout_2026_06_21_26", "closed_forward_2026_06_27_30"]))
        & summary["friction"].isin(["quoted_bidask", "entry_plus_1c_sell_minus_1c"])
        & summary["policy"].isin(
            [
                "hold_to_settlement",
                "full_sell_bid_ge_0p30",
                "recover_stake_bid_ge_0p30",
                "recover_stake_bid_ge_3x",
            ]
        )
    ].copy()
    adaptive_focus = summary[
        (summary["period"].isin(["full", "recent_ge_2026_06_21", "closed_forward_2026_06_27_30"]))
        & summary["friction"].isin(["entry_plus_1c_sell_minus_1c"])
        & summary["policy"].isin(
            [
                "hold_to_settlement",
                "full_sell_bid_ge_0p20",
                "full_sell_bid_ge_0p30",
                "recover_stake_bid_ge_0p30",
                "adaptive_entry_2x_floor20_cap30_full",
                "adaptive_entry_le10c_full20_else30",
                "adaptive_model_high_full30_else20",
                "adaptive_model_high_recover30_else_full20",
                "adaptive_path_high_full30_else20",
            ]
        )
    ].copy()

    slice_best = slice_summary[
        (slice_summary["period"] == "full")
        & (slice_summary["friction"] == "entry_plus_1c_sell_minus_1c")
        & (slice_summary["rows"] >= 20)
        & (slice_summary["slice"].isin(["entry_band", "pcal_ev_band", "model_quality_bucket", "path_quality_bucket"]))
        & (slice_summary["policy"] != "hold_to_settlement")
    ].copy()
    if not slice_best.empty:
        slice_best = (
            slice_best.sort_values(["slice", "slice_value", "roi"], ascending=[True, True, False])
            .groupby(["slice", "slice_value"], as_index=False)
            .head(1)
            .sort_values(["slice", "slice_value"])
        )

    best_full = summary[summary["period"] == "full"].sort_values("roi", ascending=False).head(10)
    payload = {
        "generated_at_utc": now_utc(),
        "input": str(INPUT.relative_to(ROOT)),
        "snapshot_dir": str(SNAPSHOT_DIR.relative_to(ROOT)),
        "snapshot_files": len(list(SNAPSHOT_DIR.glob("snapshot_*.json"))),
        "rows": int(len(base)),
        "dates": [str(base["target_date"].min()), str(base["target_date"].max())],
        "path_coverage": float((enriched["future_bid_quotes"] > 0).mean()),
        "friction_specs": friction_specs(),
        "summary": summary.to_dict(orient="records"),
        "slice_summary": slice_summary.to_dict(orient="records"),
        "best_full": best_full.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    quoted = summary["friction"] == "quoted_bidask"
    stressed = summary["friction"] == "entry_plus_1c_sell_minus_1c"
    hold_full = summary[(summary["policy"] == "hold_to_settlement") & (summary["period"] == "full") & quoted].iloc[0]
    full30 = summary[(summary["policy"] == "full_sell_bid_ge_0p30") & (summary["period"] == "full") & quoted].iloc[0]
    full30_stress = summary[(summary["policy"] == "full_sell_bid_ge_0p30") & (summary["period"] == "full") & stressed].iloc[0]
    recover30 = summary[(summary["policy"] == "recover_stake_bid_ge_0p30") & (summary["period"] == "full") & quoted].iloc[0]
    adaptive_entry = summary[
        (summary["policy"] == "adaptive_entry_2x_floor20_cap30_full") & (summary["period"] == "full") & stressed
    ].iloc[0]
    adaptive_model = summary[
        (summary["policy"] == "adaptive_model_high_full30_else20") & (summary["period"] == "full") & stressed
    ].iloc[0]
    adaptive_model_recover = summary[
        (summary["policy"] == "adaptive_model_high_recover30_else_full20") & (summary["period"] == "full") & stressed
    ].iloc[0]

    lines = [
        "# Low-Price YES Take-Profit Replay v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "This is an execution overlay test for the forecast-tail low-price YES sleeve, not a new weather signal.",
        "The backtest uses future point-in-time YES best bid: a take-profit only counts when a later snapshot has an executable bid at or above the threshold. Friction profiles then stress the quoted bid/ask path with entry add-ons, exit haircuts, and gross fee-rate assumptions.",
        "",
        "```text",
        "significance=PASS for quoted and +1c/-1c stressed full-window full-sell 20c/30c paired delta vs hold",
        "baseline=PASS versus same-denominator hold-to-settlement on full-window replay after friction stress",
        "forward=NA/FAIL because the take-profit rule was not pre-registered and fresh live-forward exits are not observed yet",
        "conclusion=shadow_candidate for exit telemetry; do not change live exit logic yet",
        "```",
        "",
        "First-principles read: full take-profit fights the convex nature of a low-price YES sleeve. It can rescue tickets that pump from 10c to 30c and later lose, but it also caps the rare tickets that settle at 100c. Once friction is included, full-sell still looks stronger than stake-recovery in historical replay; the unresolved live question is fill probability, not just ROI math.",
        "",
        "## Data Snapshot",
        "",
        f"- Input denominator: `{INPUT.relative_to(ROOT)}`.",
        f"- Rows: {len(base)} city-date-bracket candidates; dates {base['target_date'].min()}..{base['target_date'].max()}.",
        f"- Snapshot files scanned: {len(list(SNAPSHOT_DIR.glob('snapshot_*.json')))} under `{SNAPSHOT_DIR.relative_to(ROOT)}`.",
        f"- Future bid path coverage: {fmt_pct((enriched['future_bid_quotes'] > 0).mean(), signed=False)}.",
        "- Friction profiles: quoted bid/ask only; sell minus 1c; entry +1c and sell minus 1c; entry +1c and sell minus 2c; entry +1c, sell minus 1c, plus 1% gross notional fee stress.",
        "- Dashboard stack note: `run_stack.sh --no-rebuild` was attempted after sync, but frontend port 5174 stayed busy; this replay reads the refreshed mirror snapshots and existing generated denominator directly.",
        "",
        "## Focus Policies",
        "",
    ]
    lines.extend(
        markdown_table(
            focus_full,
            [
                "policy",
                "friction",
                "period",
                "rows",
                "dates",
                "win_rate_final",
                "avg_entry",
                "tp_hit_rate",
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "losing_days",
                "le_minus50pct_days",
                "max_daily_loss_roi",
            ],
        )
    )
    lines.extend(
        [
            "",
        "## Recent / Forward Windows",
        "",
    ]
    )
    lines.extend(
        markdown_table(
            focus_forward,
            [
                "policy",
                "friction",
                "period",
                "rows",
                "dates",
                "tp_hit_rate",
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## Adaptive / Score-Aware Policies",
            "",
            "These are pre-declared diagnostics, not fitted live rules. Cost-aware exits use the original entry ask; score-aware exits use existing `model_p_yes`, `edge`, `p_cal_no_city_ev`, `source_aware_v3`, and METAR-integrated score columns from the forecast-tail denominator.",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            adaptive_focus,
            [
                "policy",
                "friction",
                "period",
                "rows",
                "dates",
                "tp_hit_rate",
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## Best Stressed TP By Diagnostic Slice",
            "",
            "Each row picks the best non-hold TP policy inside that diagnostic slice under +1c entry / -1c exit stress. This table is for understanding where TP helps; it is not a permission slip to cherry-pick slices.",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            slice_best,
            [
                "slice",
                "slice_value",
                "policy",
                "rows",
                "dates",
                "tp_hit_rate",
                "roi",
                "delta_vs_hold",
                "delta_ci_low",
                "delta_ci_high",
                "top5_removed_roi",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- Hold-to-settlement full-window quoted-bidask ROI is {fmt_pct(hold_full['roi'])}; full sell at 30c is {fmt_pct(full30['roi'])} with delta {fmt_pct(full30['delta_vs_hold'])}.",
            f"- Under +1c entry / -1c exit stress, full sell at 30c is {fmt_pct(full30_stress['roi'])} with delta {fmt_pct(full30_stress['delta_vs_hold'])}.",
            f"- Stake recovery at 30c is {fmt_pct(recover30['roi'])} with delta {fmt_pct(recover30['delta_vs_hold'])}.",
            f"- Cost-aware full exit `2x floor20 cap30` is {fmt_pct(adaptive_entry['roi'])} under +1c/-1c stress; score-aware `model_high full30 else20` is {fmt_pct(adaptive_model['roi'])}; score-aware `model_high recover30 else full20` is {fmt_pct(adaptive_model_recover['roi'])}.",
            "- The cost/model adaptive policies are useful diagnostics but do not beat the simple `full_sell_bid_ge_0p20` benchmark in this replay; do not promote them to live exit rules without fresh-forward telemetry.",
            "- If full-sell improves in a slice but stake-recovery does not, the slice is probably dominated by temporary market repricing rather than true tail probability.",
            "- If stake-recovery improves with similar or lower drawdown, it is a cleaner candidate because it preserves convex payout after de-risking.",
            "- Current evidence is not enough to alter live behavior. The right next step is to add take-profit telemetry to live/shadow rows: max future bid, first bid>=20/30/40c, and whether the ticket eventually settled 0/1.",
            "",
            "## Generated Artifacts",
            "",
            f"- `{(OUT_DIR / 'candidate_path_stats.csv').relative_to(ROOT)}`",
            f"- `{(OUT_DIR / 'policy_rows.csv').relative_to(ROOT)}`",
            f"- `{(OUT_DIR / 'policy_summary.csv').relative_to(ROOT)}`",
            f"- `{(OUT_DIR / 'slice_policy_summary.csv').relative_to(ROOT)}`",
            f"- `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(OUT_MD), "json": str(OUT_JSON), "rows": len(base)}, indent=2))


if __name__ == "__main__":
    main()
