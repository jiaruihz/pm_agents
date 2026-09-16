"""HeadA low-price YES sizing / fee / strict-stop replay v2.

This is a same-denominator follow-up to the HeadA TP replay. It keeps the
entry selector fixed and tests only execution overlays:

- price/edge-aware sizing that changes dollars at risk, not which rows qualify;
- official Polymarket weather taker fee math, not an invented flat fee;
- strict "dead ticket" stops after the forecast peak window or near settlement.

The script intentionally uses a small pre-declared policy family. It is not a
threshold search.
Polymarket weather fee reference, checked 2026-07-03:
fee = shares * fee_rate * price * (1 - price), Weather fee_rate=0.05,
maker fee=0. Per-token CLOB market info exposes fd={r: 0.05, e: 1, to: true}
on the live Manila weather token; /fee-rate returned raw base_fee=1000.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_sizing_fee_stop_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-sizing-fee-stop-v2.md"

RNG_SEED = 20260703
N_BOOT = 5000
WEATHER_TAKER_FEE_RATE = 0.05


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
    if x is None:
        return ""
    try:
        val = float(x) * 100.0
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(val):
        return ""
    return f"{val:+.1f}%" if signed else f"{val:.1f}%"


def fmt_usd(x: Any) -> str:
    try:
        val = float(x)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(val):
        return ""
    return f"${val:+.2f}"


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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
        "edge",
        "model_p_yes",
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
        & out["entry"].between(0.05, 0.20, inclusive="both")
        & (pd.to_numeric(out["edge"], errors="coerce") >= 0.20)
        & out["payoff"].notna()
    ].copy()
    out["row_id"] = np.arange(len(out))
    for col in ["target_date", "city", "bracket", "condition_id"]:
        out[col] = out[col].astype(str)
    return out


@dataclass
class QuotePath:
    future_quotes: int = 0
    future_bid_quotes: int = 0
    first_future_ts_utc: str = ""
    last_future_ts_utc: str = ""
    max_future_yes_bid: float = math.nan
    max_bid_ts_utc: str = ""
    first_tp20_ts_utc: str = ""
    first_tp20_bid: float = math.nan
    first_tp30_ts_utc: str = ""
    first_tp30_bid: float = math.nan
    time_stop_ts_utc: str = ""
    time_stop_bid: float = math.nan
    time_stop_max_bid_so_far: float = math.nan
    strict_dead_stop_ts_utc: str = ""
    strict_dead_stop_bid: float = math.nan
    strict_dead_stop_max_bid_so_far: float = math.nan
    late_salvage_ts_utc: str = ""
    late_salvage_bid: float = math.nan
    late_dust_stop_ts_utc: str = ""
    late_dust_stop_bid: float = math.nan
    late_dust_stop_max_bid_so_far: float = math.nan


def scan_paths(base: pd.DataFrame) -> pd.DataFrame:
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    entry_dt: dict[int, pd.Timestamp] = {}
    entry_px: dict[int, float] = {}
    stop_dt: dict[int, pd.Timestamp] = {}
    max_so_far: dict[int, float] = defaultdict(lambda: math.nan)
    paths: dict[int, QuotePath] = {}

    for row in base.itertuples(index=False):
        row_id = int(row.row_id)
        key = (str(row.condition_id), str(row.city), str(row.target_date))
        by_key[key].append(row_id)
        entry = row.entry_dt
        entry_dt[row_id] = entry
        entry_px[row_id] = float(row.entry)
        peak_delta_h = to_float(getattr(row, "forecast_peak_delta_hours_local", math.nan))
        if math.isfinite(peak_delta_h):
            # "Peak window passed": two hours after forecast peak, or one hour
            # after entry if the forecast peak was already behind us.
            stop_h = max(1.0, peak_delta_h + 2.0)
        else:
            stop_h = 8.0
        stop_dt[row_id] = entry + pd.Timedelta(hours=stop_h)
        paths[row_id] = QuotePath()

    min_date = base["target_date"].min().replace("-", "")
    max_date = base["target_date"].max().replace("-", "")
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
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            key = (
                str(rec.get("condition_id")),
                str(rec.get("city")),
                str(rec.get("target_date") or rec.get("event_date")),
            )
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
            for row_id in row_ids:
                if ts <= entry_dt[row_id]:
                    continue
                path = paths[row_id]
                path.future_quotes += 1
                ts_s = ts.isoformat()
                if not path.first_future_ts_utc:
                    path.first_future_ts_utc = ts_s
                path.last_future_ts_utc = ts_s
                if not math.isfinite(bid):
                    continue
                path.future_bid_quotes += 1
                if not math.isfinite(max_so_far[row_id]) or bid > max_so_far[row_id]:
                    max_so_far[row_id] = bid
                if not math.isfinite(path.max_future_yes_bid) or bid > path.max_future_yes_bid:
                    path.max_future_yes_bid = bid
                    path.max_bid_ts_utc = ts_s
                if bid >= 0.20 and not path.first_tp20_ts_utc:
                    path.first_tp20_ts_utc = ts_s
                    path.first_tp20_bid = bid
                if bid >= 0.30 and not path.first_tp30_ts_utc:
                    path.first_tp30_ts_utc = ts_s
                    path.first_tp30_bid = bid
                if (
                    not path.time_stop_ts_utc
                    and ts >= stop_dt[row_id]
                    and math.isfinite(max_so_far[row_id])
                    and max_so_far[row_id] < 0.15
                    and bid >= 0.03
                ):
                    path.time_stop_ts_utc = ts_s
                    path.time_stop_bid = bid
                    path.time_stop_max_bid_so_far = max_so_far[row_id]
                no_meaningful_pump = (
                    math.isfinite(max_so_far[row_id])
                    and max_so_far[row_id] < max(0.12, entry_px[row_id] + 0.03)
                )
                peak_window_over = ts >= stop_dt[row_id]
                bid_is_salvage = 0.02 <= bid <= min(0.07, max(entry_px[row_id], 0.04))
                if (
                    not path.strict_dead_stop_ts_utc
                    and peak_window_over
                    and no_meaningful_pump
                    and bid_is_salvage
                ):
                    path.strict_dead_stop_ts_utc = ts_s
                    path.strict_dead_stop_bid = bid
                    path.strict_dead_stop_max_bid_so_far = max_so_far[row_id]
                if math.isfinite(hours_to_settle) and 0 <= hours_to_settle <= 3.0 and bid >= 0.02:
                    path.late_salvage_ts_utc = ts_s
                    path.late_salvage_bid = bid
                if (
                    math.isfinite(hours_to_settle)
                    and 0 <= hours_to_settle <= 3.0
                    and no_meaningful_pump
                    and 0.02 <= bid <= 0.06
                ):
                    path.late_dust_stop_ts_utc = ts_s
                    path.late_dust_stop_bid = bid
                    path.late_dust_stop_max_bid_so_far = max_so_far[row_id]

    return pd.DataFrame([{"row_id": row_id, **vars(path)} for row_id, path in paths.items()])


def sizing_shares(row: pd.Series, sizing: str) -> float:
    entry = float(row["entry"])
    edge = to_float(row.get("edge"), 0.0)
    pcal_ev = to_float(row.get("p_cal_no_city_ev"), math.nan)
    model_p = to_float(row.get("model_p_yes"), math.nan)
    if sizing == "fixed_cash_0p80":
        return 0.80 / entry
    if sizing == "fixed_8_shares":
        return 8.0
    if sizing == "fixed_12_shares":
        return 12.0
    if sizing == "edge_scaled_8_shares":
        return 8.0 * min(1.5, max(0.5, edge / 0.30))
    if sizing == "pcal_scaled_8_shares":
        score = pcal_ev if math.isfinite(pcal_ev) else edge
        return 8.0 * min(1.5, max(0.5, score / 0.40))
    if sizing == "modelp_scaled_8_shares":
        score = model_p if math.isfinite(model_p) else 0.35
        return 8.0 * min(1.5, max(0.5, score / 0.40))
    if sizing == "payout25_cap5":
        return min(5.0, entry * 25.0) / entry
    if sizing == "price_tier_6_8_10_shares":
        if entry <= 0.08:
            return 6.0
        if entry <= 0.14:
            return 8.0
        return 10.0
    if sizing == "quality_price_tier_5_8_12_shares":
        quality = pcal_ev if math.isfinite(pcal_ev) else edge
        base = 5.0 if entry <= 0.08 else (8.0 if entry <= 0.14 else 10.0)
        mult = 1.2 if quality >= 0.50 else (1.0 if quality >= 0.30 else 0.8)
        return min(12.0, max(5.0, base * mult))
    raise RuntimeError(f"unknown sizing: {sizing}")


SIZING_POLICIES = [
    "fixed_cash_0p80",
    "fixed_8_shares",
    "fixed_12_shares",
    "edge_scaled_8_shares",
    "pcal_scaled_8_shares",
    "modelp_scaled_8_shares",
    "payout25_cap5",
    "price_tier_6_8_10_shares",
    "quality_price_tier_5_8_12_shares",
]


EXIT_POLICIES = [
    "hold",
    "hold_plus_time_stop",
    "hold_plus_late_salvage",
    "hold_plus_time_stop_or_late_salvage",
    "hold_plus_strict_dead_stop",
    "hold_plus_late_dust_stop",
    "hold_plus_strict_dead_or_late_dust",
    "tp20_maxbid_backtest_style",
    "tp20_live_fixed20",
    "tp20_live_fixed20_plus_late_salvage",
    "tp20_live_fixed20_plus_time_stop",
    "tp20_live_fixed20_plus_time_stop_or_late_salvage",
    "tp30_live_fixed30",
    "recover_stake_tp30_live_fixed30",
]

FEE_PROFILES = [
    "maker_entry_no_fee",
    "taker_entry_weather_fee",
    "taker_entry_plus1c_weather_fee",
    "taker_entry_weather_fee_stop_taker_fee",
    "taker_entry_plus1c_weather_fee_stop_taker_fee",
]


def weather_taker_fee(*, shares: float, price: float) -> float:
    if shares <= 0 or price <= 0 or price >= 1:
        return 0.0
    fee = shares * WEATHER_TAKER_FEE_RATE * price * (1.0 - price)
    if fee < 0.000005:
        return 0.0
    return round(fee, 5)


def fee_profile_entry(profile: str, entry: float, shares: float) -> tuple[float, float, str]:
    entry_price = entry
    role = "maker"
    if "plus1c" in profile:
        entry_price = min(0.99, entry + 0.01)
    if profile.startswith("taker_"):
        role = "taker"
        return entry_price, weather_taker_fee(shares=shares, price=entry_price), role
    return entry_price, 0.0, role


def fee_profile_exit(profile: str, exit_mode: str, exit_price: float, shares: float) -> tuple[float, str]:
    if exit_mode == "settlement" or exit_price <= 0:
        return 0.0, "settlement"
    if "stop_taker_fee" in profile and exit_mode in {"time_stop", "late_salvage", "strict_dead_stop", "late_dust_stop"}:
        return weather_taker_fee(shares=shares, price=exit_price), "taker"
    # TP20 pre-posted maker exits are modeled as maker/no-fee here; fill
    # probability is handled separately by comparing against hold.
    return 0.0, "maker_or_unmodeled"


def exit_decision(row: pd.Series, policy: str, entry: float) -> tuple[float, str, bool, bool]:
    payoff = float(row["payoff"])
    max_bid = to_float(row.get("max_future_yes_bid"))
    if policy == "hold":
        return payoff, "settlement", False, False
    if policy in {"hold_plus_time_stop", "hold_plus_time_stop_or_late_salvage"}:
        stop_bid = to_float(row.get("time_stop_bid"))
        if math.isfinite(stop_bid):
            return max(0.0, stop_bid - 0.01), "time_stop", False, True
        if policy == "hold_plus_time_stop":
            return payoff, "settlement", False, False
    if policy in {"hold_plus_late_salvage", "hold_plus_time_stop_or_late_salvage"}:
        salvage_bid = to_float(row.get("late_salvage_bid"))
        if math.isfinite(salvage_bid):
            return max(0.0, salvage_bid), "late_salvage", False, True
        return payoff, "settlement", False, False
    if policy in {"hold_plus_strict_dead_stop", "hold_plus_strict_dead_or_late_dust"}:
        stop_bid = to_float(row.get("strict_dead_stop_bid"))
        if math.isfinite(stop_bid):
            return max(0.0, stop_bid), "strict_dead_stop", False, True
        if policy == "hold_plus_strict_dead_stop":
            return payoff, "settlement", False, False
    if policy in {"hold_plus_late_dust_stop", "hold_plus_strict_dead_or_late_dust"}:
        dust_bid = to_float(row.get("late_dust_stop_bid"))
        if math.isfinite(dust_bid):
            return max(0.0, dust_bid), "late_dust_stop", False, True
        return payoff, "settlement", False, False
    if policy == "tp20_maxbid_backtest_style":
        if math.isfinite(max_bid) and max_bid >= 0.20:
            return max_bid, "tp20_maxbid", True, False
        return payoff, "settlement", False, False
    if policy in {
        "tp20_live_fixed20",
        "tp20_live_fixed20_plus_late_salvage",
        "tp20_live_fixed20_plus_time_stop",
        "tp20_live_fixed20_plus_time_stop_or_late_salvage",
    }:
        if bool(row.get("first_tp20_ts_utc")):
            return 0.20, "tp20_fixed20", True, False
        if policy in {"tp20_live_fixed20_plus_time_stop", "tp20_live_fixed20_plus_time_stop_or_late_salvage"}:
            stop_bid = to_float(row.get("time_stop_bid"))
            if math.isfinite(stop_bid):
                return max(0.0, stop_bid - 0.01), "time_stop", False, True
        if policy in {"tp20_live_fixed20_plus_late_salvage", "tp20_live_fixed20_plus_time_stop_or_late_salvage"}:
            salvage_bid = to_float(row.get("late_salvage_bid"))
            if math.isfinite(salvage_bid):
                return max(0.0, salvage_bid - 0.01), "late_salvage", False, True
        return payoff, "settlement", False, False
    if policy == "tp30_live_fixed30":
        if bool(row.get("first_tp30_ts_utc")):
            return 0.30, "tp30_fixed30", True, False
        return payoff, "settlement", False, False
    if policy == "recover_stake_tp30_live_fixed30":
        if bool(row.get("first_tp30_ts_utc")) and 0.30 > entry:
            return 0.30, "recover_stake_tp30", True, False
        return payoff, "settlement", False, False
    raise RuntimeError(f"unknown exit policy: {policy}")


def apply_replay(frame: pd.DataFrame) -> pd.DataFrame:
    recs: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        entry = float(row["entry"])
        payoff = float(row["payoff"])
        for sizing in SIZING_POLICIES:
            shares = sizing_shares(row, sizing)
            if sizing.startswith("fixed_") and sizing.endswith("_shares"):
                shares = max(5.0, shares)
            for fee_profile in FEE_PROFILES:
                entry_px, entry_fee, entry_role = fee_profile_entry(fee_profile, entry, shares)
                entry_cash = shares * entry_px
                cost = entry_cash + entry_fee
                for policy in EXIT_POLICIES:
                    exit_px, exit_mode, tp_hit, stop_hit = exit_decision(row, policy, entry_px)
                    exit_fee, exit_role = fee_profile_exit(fee_profile, exit_mode, exit_px, shares)
                    if policy == "recover_stake_tp30_live_fixed30" and exit_mode == "recover_stake_tp30":
                        shares_sold = min(shares, cost / exit_px)
                        pnl = (
                            shares_sold * (exit_px - entry_px)
                            + (shares - shares_sold) * (payoff - entry_px)
                            - entry_fee
                            - weather_taker_fee(shares=shares_sold, price=exit_px)
                        )
                    else:
                        pnl = shares * (exit_px - entry_px) - entry_fee - exit_fee
                    recs.append(
                        {
                            "row_id": int(row["row_id"]),
                            "city": row["city"],
                            "target_date": row["target_date"],
                            "bracket": row["bracket"],
                            "forecast_source": row.get("forecast_source", ""),
                            "period": row.get("period", ""),
                            "entry": entry,
                            "entry_price_effective": entry_px,
                            "entry_fee": entry_fee,
                            "entry_role": entry_role,
                            "payoff": payoff,
                            "win": payoff >= 0.5,
                            "edge": row.get("edge", math.nan),
                            "model_p_yes": row.get("model_p_yes", math.nan),
                            "p_cal_no_city_ev": row.get("p_cal_no_city_ev", math.nan),
                            "source_aware_v3": truthy(row.get("source_aware_v3")),
                            "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc", ""),
                            "future_quotes": int(row.get("future_quotes") or 0),
                            "future_bid_quotes": int(row.get("future_bid_quotes") or 0),
                            "max_future_yes_bid": row.get("max_future_yes_bid", math.nan),
                            "max_bid_ts_utc": row.get("max_bid_ts_utc", ""),
                            "first_tp20_ts_utc": row.get("first_tp20_ts_utc", ""),
                            "first_tp30_ts_utc": row.get("first_tp30_ts_utc", ""),
                            "time_stop_ts_utc": row.get("time_stop_ts_utc", ""),
                            "time_stop_bid": row.get("time_stop_bid", math.nan),
                            "strict_dead_stop_ts_utc": row.get("strict_dead_stop_ts_utc", ""),
                            "strict_dead_stop_bid": row.get("strict_dead_stop_bid", math.nan),
                            "late_salvage_ts_utc": row.get("late_salvage_ts_utc", ""),
                            "late_salvage_bid": row.get("late_salvage_bid", math.nan),
                            "late_dust_stop_ts_utc": row.get("late_dust_stop_ts_utc", ""),
                            "late_dust_stop_bid": row.get("late_dust_stop_bid", math.nan),
                            "sizing": sizing,
                            "shares": shares,
                            "entry_cash": entry_cash,
                            "cost": cost,
                            "fee_profile": fee_profile,
                            "exit_policy": policy,
                            "exit_mode": exit_mode,
                            "exit_price": exit_px,
                            "exit_fee": exit_fee,
                            "exit_role": exit_role,
                            "tp_hit": tp_hit,
                            "stop_hit": stop_hit,
                            "pnl": pnl,
                            "roi": pnl / cost if cost > 0 else math.nan,
                        }
                    )
    return pd.DataFrame(recs)


def date_block_ci(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    pnl = daily["pnl"].to_numpy(dtype=float)
    cost = daily["cost"].to_numpy(dtype=float)
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(pnl), len(pnl))
        c = float(cost[idx].sum())
        vals.append(float(pnl[idx].sum() / c) if c > 0 else math.nan)
    vals = np.asarray([x for x in vals if math.isfinite(x)])
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def summarize(rows: pd.DataFrame, period_name: str, period_mask: pd.Series) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    g0 = rows[period_mask].copy()
    for (sizing, fee_profile, exit_policy), g in g0.groupby(["sizing", "fee_profile", "exit_policy"], dropna=False):
        if g.empty:
            continue
        daily = g.groupby("target_date", as_index=False).agg(
            rows=("row_id", "count"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
        )
        daily["roi"] = daily["pnl"] / daily["cost"]
        ci_low, ci_high = date_block_ci(daily)
        cost = float(g["cost"].sum())
        pnl = float(g["pnl"].sum())
        out.append(
            {
                "period": period_name,
                "sizing": sizing,
                "fee_profile": fee_profile,
                "exit_policy": exit_policy,
                "rows": int(len(g)),
                "dates": int(g["target_date"].nunique()),
                "cities": int(g["city"].nunique()),
                "win_rate_final": float(g["win"].mean()),
                "avg_entry": float(g["entry"].mean()),
                "avg_cost": float(g["cost"].mean()),
                "avg_entry_fee": float(g["entry_fee"].mean()),
                "avg_exit_fee": float(g["exit_fee"].mean()),
                "total_cost": cost,
                "total_fees": float(g["entry_fee"].sum() + g["exit_fee"].sum()),
                "tp_hit_rate": float(g["tp_hit"].mean()),
                "stop_hit_rate": float(g["stop_hit"].mean()),
                "roi": pnl / cost if cost > 0 else math.nan,
                "pnl": pnl,
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
                "losing_days": int((daily["pnl"] < 0).sum()),
                "le_minus50pct_days": int((daily["roi"] <= -0.5).sum()),
                "max_daily_loss_usd": float(daily["pnl"].min()),
                "max_daily_loss_roi": float(daily["roi"].min()),
                "path_coverage": float((g["future_bid_quotes"] > 0).mean()),
            }
        )
    return pd.DataFrame(out)


def make_markdown(summary: pd.DataFrame, replay: pd.DataFrame, base: pd.DataFrame, paths: pd.DataFrame) -> str:
    full = summary[summary["period"] == "full"].copy()
    recent = summary[summary["period"] == "recent_ge_2026_06_21"].copy()
    forward = summary[summary["period"] == "closed_forward_2026_06_27_30"].copy()

    def pick(df: pd.DataFrame, sizing: str, fee_profile: str, policy: str) -> dict[str, Any]:
        sub = df[
            (df["sizing"] == sizing)
            & (df["fee_profile"] == fee_profile)
            & (df["exit_policy"] == policy)
        ]
        return sub.iloc[0].to_dict() if not sub.empty else {}

    focus_rows = [
        ("fixed_cash_0p80", "maker_entry_no_fee", "hold"),
        ("fixed_cash_0p80", "taker_entry_weather_fee", "hold"),
        ("fixed_cash_0p80", "taker_entry_plus1c_weather_fee", "hold"),
        ("fixed_cash_0p80", "taker_entry_plus1c_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
        ("fixed_8_shares", "maker_entry_no_fee", "hold"),
        ("fixed_8_shares", "taker_entry_weather_fee", "hold"),
        ("fixed_8_shares", "taker_entry_plus1c_weather_fee", "hold"),
        ("fixed_8_shares", "taker_entry_plus1c_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
        ("price_tier_6_8_10_shares", "taker_entry_weather_fee", "hold"),
        ("price_tier_6_8_10_shares", "taker_entry_plus1c_weather_fee", "hold"),
        ("price_tier_6_8_10_shares", "taker_entry_plus1c_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
        ("quality_price_tier_5_8_12_shares", "taker_entry_weather_fee", "hold"),
        ("quality_price_tier_5_8_12_shares", "taker_entry_plus1c_weather_fee", "hold"),
        ("quality_price_tier_5_8_12_shares", "taker_entry_plus1c_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
    ]

    stop_rows = [
        ("fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold"),
        ("fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_time_stop_or_late_salvage"),
        ("fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_strict_dead_stop"),
        ("fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_late_dust_stop"),
        ("fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
        ("fixed_8_shares", "taker_entry_weather_fee_stop_taker_fee", "hold"),
        ("fixed_8_shares", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
        ("quality_price_tier_5_8_12_shares", "taker_entry_weather_fee_stop_taker_fee", "hold"),
        ("quality_price_tier_5_8_12_shares", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust"),
    ]

    def table(df: pd.DataFrame, rows: list[tuple[str, str, str]]) -> str:
        lines = [
            "| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sizing, fee_profile, policy in rows:
            r = pick(df, sizing, fee_profile, policy)
            if not r:
                continue
            ci = f"[{fmt_pct(r.get('roi_ci_low'))}, {fmt_pct(r.get('roi_ci_high'))}]"
            avg_fees = float(r.get("total_fees", 0.0)) / max(float(r.get("rows", 1)), 1.0)
            lines.append(
                "| "
                + " | ".join(
                    [
                        sizing,
                        fee_profile,
                        policy,
                        str(int(r["rows"])),
                        str(int(r["dates"])),
                        f"${float(r['avg_cost']):.3f}",
                        f"${avg_fees:.4f}",
                        fmt_pct(r["stop_hit_rate"], signed=False),
                        fmt_pct(r["roi"]),
                        ci,
                        str(int(r["losing_days"])),
                        str(int(r["le_minus50pct_days"])),
                        f"{fmt_usd(r['max_daily_loss_usd'])} / {fmt_pct(r['max_daily_loss_roi'])}",
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    def best_rows(df: pd.DataFrame, period_label: str) -> str:
        sub = df[
            df["fee_profile"].isin(
                [
                    "taker_entry_weather_fee",
                    "taker_entry_plus1c_weather_fee",
                    "taker_entry_plus1c_weather_fee_stop_taker_fee",
                ]
            )
            & df["exit_policy"].isin(["hold", "hold_plus_strict_dead_or_late_dust"])
        ].copy()
        sub = sub.sort_values(["roi", "rows"], ascending=[False, False]).head(8)
        lines = [
            f"| {period_label} rank | sizing | fee/profile | exit | ROI | CI | rows | dates | total cost | max daily loss |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for i, r in enumerate(sub.to_dict("records"), start=1):
            ci = f"[{fmt_pct(r.get('roi_ci_low'))}, {fmt_pct(r.get('roi_ci_high'))}]"
            lines.append(
                f"| {i} | {r['sizing']} | {r['fee_profile']} | {r['exit_policy']} | "
                f"{fmt_pct(r['roi'])} | {ci} | {int(r['rows'])} | {int(r['dates'])} | "
                f"${float(r['total_cost']):.2f} | {fmt_usd(r['max_daily_loss_usd'])} |"
            )
        return "\n".join(lines)

    leakage_checks = {
        "rows": int(len(base)),
        "decision_ts_missing": int(base["entry_dt"].isna().sum()),
        "future_path_rows": int((paths["future_quotes"] > 0).sum()),
        "future_bid_path_rows": int((paths["future_bid_quotes"] > 0).sum()),
        "first_tp20_rows": int((paths["first_tp20_ts_utc"].astype(str) != "").sum()),
        "strict_dead_stop_rows": int((paths["strict_dead_stop_ts_utc"].astype(str) != "").sum()),
        "late_dust_stop_rows": int((paths["late_dust_stop_ts_utc"].astype(str) != "").sum()),
    }

    current_hold = pick(full, "fixed_cash_0p80", "maker_entry_no_fee", "hold")
    fee_hold = pick(full, "fixed_cash_0p80", "taker_entry_weather_fee", "hold")
    plus_hold = pick(full, "fixed_cash_0p80", "taker_entry_plus1c_weather_fee", "hold")
    strict_stop = pick(full, "fixed_cash_0p80", "taker_entry_weather_fee_stop_taker_fee", "hold_plus_strict_dead_or_late_dust")

    return f"""# HeadA Low-Price YES Sizing / Fee / Strict-Stop Replay v2

Generated: {now_utc()}

## Verdict

This is still an execution-layer audit for HeadA (`forecast_tail_low_price_yes`), not a new entry alpha search.

```text
conclusion=shadow_research_only
entry_selector=unchanged
live_action=keep_current_hold; do_not_add_stop_yet; do_not_size_up
```

Plain English: after official weather taker fees, the sleeve still can be positive, but the margin is thinner. The current fixed-cash sizing overweights the cheapest longshots. Fixed-share / price-tier sizing is more logically aligned with a lottery sleeve because each trade has a more similar max payout. The strict dead-ticket stop is not a clear win yet; it can reduce some dead exposure, but it has not beaten hold cleanly enough on recent/frozen windows to deploy.

## Fee Rule

Official Polymarket docs define trading fees as:

```text
fee = C * feeRate * p * (1 - p)
```

For Weather, the official category table lists taker `feeRate=0.05`, maker fee `0`, and maker rebate `25%`. Fees are applied at match time and markets expose fee parameters through CLOB market info. The live Manila weather market checked during this run returned `fd={{"r":0.05,"e":1,"to":true}}`; `/fee-rate` returned raw `base_fee=1000`. This report therefore uses `shares * 0.05 * price * (1-price)` for taker fills and zero fee for maker fills. No invented flat fee is used.

Sources: [Polymarket Fees](https://docs.polymarket.com/trading/fees), [CLOB fee-rate endpoint](https://docs.polymarket.com/api-reference/market-data/get-fee-rate), [Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates).

## Data Snapshot

- Input denominator: `{INPUT.relative_to(ROOT)}`.
- Rows: {len(base)} candidates; dates {base['target_date'].min()}..{base['target_date'].max()}; cities {base['city'].nunique()}.
- Snapshot files: `{SNAPSHOT_DIR.relative_to(ROOT)}`; path rows with future quote {leakage_checks['future_path_rows']}/{len(base)}, with future bid {leakage_checks['future_bid_path_rows']}/{len(base)}.
- Path events found: TP20 rows {leakage_checks['first_tp20_rows']}, strict-dead stop rows {leakage_checks['strict_dead_stop_rows']}, late-dust stop rows {leakage_checks['late_dust_stop_rows']}.
- This replay uses only snapshots after `decision_snapshot_ts_utc` for exits. It does not change the entry selector.

## Current Sizing + Fee Hit

| profile | ROI | CI | note |
| --- | ---: | ---: | --- |
| fixed cash $0.80, maker/no-fee hold | {fmt_pct(current_hold.get('roi'))} | [{fmt_pct(current_hold.get('roi_ci_low'))}, {fmt_pct(current_hold.get('roi_ci_high'))}] | old optimistic executable baseline |
| fixed cash $0.80, taker fee hold | {fmt_pct(fee_hold.get('roi'))} | [{fmt_pct(fee_hold.get('roi_ci_low'))}, {fmt_pct(fee_hold.get('roi_ci_high'))}] | official weather taker fee only |
| fixed cash $0.80, +1c entry + taker fee hold | {fmt_pct(plus_hold.get('roi'))} | [{fmt_pct(plus_hold.get('roi_ci_low'))}, {fmt_pct(plus_hold.get('roi_ci_high'))}] | conservative taker/slippage profile |
| fixed cash $0.80, taker fee + strict stop | {fmt_pct(strict_stop.get('roi'))} | [{fmt_pct(strict_stop.get('roi_ci_low'))}, {fmt_pct(strict_stop.get('roi_ci_high'))}] | tests dead-ticket stop, no TP |

## Main Same-Denominator Results

Full window:

{table(full, focus_rows)}

Recent window (`target_date >= 2026-06-21`):

{table(recent, focus_rows)}

Closed forward (`2026-06-27..2026-06-30`):

{table(forward, focus_rows)}

## Stop Study

Strict stop definition: after the forecast peak window, if the ticket never had a meaningful pump (`max_bid_so_far < max(0.12, entry+0.03)`) and the current bid is only residual value (`2c..7c`), sell at the bid and charge taker fee if the profile says the stop is taker-executed. `late_dust_stop` is the same idea inside the last three hours before settlement with bid `2c..6c`.

Full:

{table(full, stop_rows)}

Recent:

{table(recent, stop_rows)}

Closed forward:

{table(forward, stop_rows)}

## Best Rows Under Fee-Aware Profiles

{best_rows(full, "full")}

{best_rows(recent, "recent")}

{best_rows(forward, "closed_forward")}

## Interpretation

1. `fixed_cash_0p80` is not neutral. It buys many more shares at 5c than at 15c, so it implicitly says the cheapest tickets deserve larger max payout. That is not obviously true for this sleeve.
2. Fixed-share and price-tier policies are more defensible first-principles sizing because they keep max payout closer across rows and let cash risk rise with price/quality.
3. Official weather taker fees are large enough to matter for $0.80 probes: around 4-5% of entry cash in the 5c-20c band. Maker fills avoid this fee, but maker fill probability and adverse selection must be measured live.
4. The strict stop is a useful shadow telemetry field, not a live change. It should be logged forward as `strict_dead_stop_would_trigger`, `late_dust_stop_would_trigger`, bid, time-to-settle, and eventual settlement.

## Artifacts

- Script: `{Path(__file__).relative_to(ROOT)}`
- Replay rows: `{(OUT_DIR / 'replay_rows.csv').relative_to(ROOT)}`
- Summary: `{(OUT_DIR / 'summary.csv').relative_to(ROOT)}`
- Machine-readable result: `{(OUT_DIR / 'summary.csv').relative_to(ROOT)}`
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    paths = scan_paths(base)
    frame = base.merge(paths, on="row_id", how="left")
    replay = apply_replay(frame)
    dates = replay["target_date"].astype(str)
    periods = {
        "full": pd.Series(True, index=replay.index),
        "train_le_2026_06_20": dates <= "2026-06-20",
        "recent_ge_2026_06_21": dates >= "2026-06-21",
        "closed_forward_2026_06_27_30": (dates >= "2026-06-27") & (dates <= "2026-06-30"),
    }
    summary = pd.concat([summarize(replay, name, mask) for name, mask in periods.items()], ignore_index=True)

    replay.to_csv(OUT_DIR / "replay_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    paths.to_csv(OUT_DIR / "paths.csv", index=False)

    OUT_MD.write_text(make_markdown(summary, replay, base, paths), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {(OUT_DIR / 'summary.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
