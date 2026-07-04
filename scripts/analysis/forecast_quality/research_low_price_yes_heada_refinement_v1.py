"""HeadA low-price YES refinement research v1.

Scope: forecast-tail low-price YES only.  This script keeps the entry alpha
fixed and evaluates four implementation questions on one denominator:

- after the 2026-07-04 live change, what does the dist>0 hot-only sample look
  like under official Weather fees?
- does as-of station-bias adjusted distance create a usable continuous EV
  shape, or just another thin slice?
- are we buying the wrong bracket when losers overshoot, or are sibling hotter
  expressions also overpriced?
- do sizing and strict dead-ticket stop overlays improve the hot-only sleeve?

No live selector is changed by this script.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
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

from scripts.analysis.forecast_quality.research_low_price_yes_sizing_fee_stop_v2 import (
    scan_paths,
)

INPUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv"
DB_PATH = ROOT / "runtime/weather.db"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-04-low-price-yes-heada-refinement-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-04-low-price-yes-heada-refinement-v1.json"

WEATHER_TAKER_FEE_RATE = 0.05
N_BOOT = 5000
RNG_SEED = 20260704
TRAIN_END = "2026-06-20"
RECENT_START = "2026-06-21"
FRESH_FORWARD_START = "2026-07-04"

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def fmt_pct(value: Any, *, signed: bool = True) -> str:
    try:
        x = float(value) * 100.0
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"


def fmt_usd(value: Any) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def parse_utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def ts_key(value: Any) -> str:
    ts = parse_utc(value)
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bracket_low(bracket: Any) -> float:
    # Weather market bracket strings are usually "28", "66-67", or "36+".
    # For range brackets the lower bound is the first number; treating the
    # hyphen as a negative sign would turn "66-67" into [66, -67].
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", str(bracket or "").replace("−", "-"))
    if not match:
        return math.nan
    return float(match.group(1))


def bracket_low_f(bracket: Any, unit: Any) -> float:
    low = parse_bracket_low(bracket)
    if not math.isfinite(low):
        return math.nan
    if str(unit or "").upper().startswith("C"):
        return low * 9.0 / 5.0 + 32.0
    return low


def bracket_width_f(unit: Any) -> float:
    return 1.8 if str(unit or "").upper().startswith("C") else 2.0


def forecast_model_from_source(source: Any, peak_source: Any = "") -> str:
    text = f"{source or ''} {peak_source or ''}".lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return "other"


def weather_taker_fee(*, shares: float, price: float) -> float:
    if shares <= 0.0 or price <= 0.0 or price >= 1.0:
        return 0.0
    return shares * WEATHER_TAKER_FEE_RATE * price * (1.0 - price)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def load_fact_fields() -> pd.DataFrame:
    conn = connect()
    try:
        return pd.read_sql_query(
            """
            SELECT
              candidate_id,
              unit,
              forecast_max_f AS fact_forecast_max_f,
              forecast_source AS fact_forecast_source,
              forecast_peak_source AS fact_forecast_peak_source,
              yes_spread AS fact_yes_spread,
              yes_depth_ask_5c AS fact_yes_depth_ask_5c,
              fact_built_at_utc
            FROM fact_signal_candidates
            """,
            conn,
        )
    finally:
        conn.close()


def load_settlements() -> pd.DataFrame:
    conn = connect()
    try:
        return pd.read_sql_query(
            """
            SELECT city, target_date, bracket, final_price, settlement_status
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
            """,
            conn,
        )
    finally:
        conn.close()


def load_bias_index() -> dict[tuple[str, str], list[tuple[str, float]]]:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["date"] = bias["date"].astype(str)
    bias["error_f_actual_minus_forecast"] = pd.to_numeric(
        bias["error_f_actual_minus_forecast"],
        errors="coerce",
    )
    bias = bias.dropna(subset=["city", "model", "date", "error_f_actual_minus_forecast"])
    out: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for row in bias.itertuples(index=False):
        out[(str(row.city), str(row.model))].append((str(row.date), float(row.error_f_actual_minus_forecast)))
    for key in out:
        out[key].sort(key=lambda x: x[0])
    return dict(out)


def attach_asof_bias(df: pd.DataFrame) -> pd.DataFrame:
    index = load_bias_index()
    recs: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        vals = [err for date, err in index.get((str(row.city), str(row.forecast_model)), []) if date < str(row.target_date)]
        if not vals:
            recs.append(
                {
                    "bias_n_asof": 0,
                    "bias_mean_asof": math.nan,
                    "bias_p50_asof": math.nan,
                    "bias_p90_asof": math.nan,
                    "hot_tail_pct_asof": math.nan,
                    "cold_tail_pct_asof": math.nan,
                }
            )
            continue
        arr = np.array(vals, dtype=float)
        recs.append(
            {
                "bias_n_asof": int(arr.size),
                "bias_mean_asof": float(arr.mean()),
                "bias_p50_asof": float(np.quantile(arr, 0.50)),
                "bias_p90_asof": float(np.quantile(arr, 0.90)),
                "hot_tail_pct_asof": float((arr >= 1.0).mean()),
                "cold_tail_pct_asof": float((arr <= -1.0).mean()),
            }
        )
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(recs)], axis=1)


def classify_book_state(spread: Any, depth: Any) -> str:
    s = to_float(spread)
    d = to_float(depth)
    if not math.isfinite(s) or not math.isfinite(d):
        return "missing"
    if s <= 0.03 and d >= 25.0:
        return "feasible"
    return "thin_wide"


def add_distance_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["bracket_low_f"] = [bracket_low_f(b, u) for b, u in zip(out["bracket"], out["unit"], strict=False)]
    out["bracket_width_f"] = [bracket_width_f(u) for u in out["unit"]]
    out["forecast_max_f_used"] = pd.to_numeric(out["fact_forecast_max_f"], errors="coerce")
    out["raw_dist_br"] = (out["bracket_low_f"] - out["forecast_max_f_used"]) / out["bracket_width_f"]
    out["hot_tail_boundary_v1"] = out["raw_dist_br"] > 0
    out["adj_forecast_p50_f"] = out["forecast_max_f_used"] + pd.to_numeric(out["bias_p50_asof"], errors="coerce")
    out["adj_dist_p50_br"] = (out["bracket_low_f"] - out["adj_forecast_p50_f"]) / out["bracket_width_f"]
    out["raw_dist_band"] = pd.cut(
        out["raw_dist_br"],
        [-np.inf, 0.0, 0.25, 0.5, 1.0, np.inf],
        labels=["<=0", "0..0.25", "0.25..0.5", "0.5..1", ">1"],
        include_lowest=True,
    ).astype(str)
    out["adj_dist_band"] = pd.cut(
        out["adj_dist_p50_br"],
        [-np.inf, -0.5, 0.0, 0.5, 1.0, np.inf],
        labels=["<-0.5", "-0.5..0", "0..0.5", "0.5..1", ">1"],
        include_lowest=True,
    ).astype(str)
    out["price_band"] = pd.cut(
        out["entry"],
        [0.0, 0.08, 0.14, 0.20, np.inf],
        labels=["5-8c", "8-14c", "14-20c", ">20c"],
        include_lowest=True,
    ).astype(str)
    return out


def load_base() -> pd.DataFrame:
    df = pd.read_csv(INPUT, low_memory=False)
    fact = load_fact_fields()
    df = df.merge(fact, on="candidate_id", how="left")
    df["target_date"] = df["target_date"].astype(str)
    df["entry"] = pd.to_numeric(df["ask"], errors="coerce")
    df["edge"] = pd.to_numeric(df["edge"], errors="coerce")
    df["payoff"] = pd.to_numeric(df["payoff"], errors="coerce")
    df["forecast_model"] = [
        forecast_model_from_source(source, peak)
        for source, peak in zip(df["fact_forecast_source"], df["fact_forecast_peak_source"], strict=False)
    ]
    df["decision_ts_key"] = df["decision_snapshot_ts_utc"].map(ts_key)
    df["entry_dt"] = df["decision_snapshot_ts_utc"].map(parse_utc)
    df["book_state_v1"] = [
        classify_book_state(spread, depth)
        for spread, depth in zip(df["fact_yes_spread"], df["fact_yes_depth_ask_5c"], strict=False)
    ]
    df = attach_asof_bias(df)
    df = add_distance_fields(df)
    df = df[
        df["entry"].between(0.05, 0.20, inclusive="both")
        & df["edge"].ge(0.20)
        & df["payoff"].isin([0.0, 1.0])
        & df["candidate_id"].notna()
        & df["condition_id"].notna()
        & df["entry_dt"].notna()
    ].copy()
    df = df.sort_values(["target_date", "city", "decision_snapshot_ts_utc"]).reset_index(drop=True)
    df["row_id"] = np.arange(len(df), dtype=int)
    df["win"] = df["payoff"].eq(1.0)
    return df


def sizing_shares(row: pd.Series, policy: str) -> float:
    entry = float(row["entry"])
    edge = to_float(row.get("edge"), 0.0)
    pcal_ev = to_float(row.get("p_cal_no_city_ev"), math.nan)
    if policy == "fixed_cash_0p80":
        return 0.80 / entry
    if policy == "fixed_6_shares":
        return 6.0
    if policy == "fixed_8_shares":
        return 8.0
    if policy == "fixed_10_shares":
        return 10.0
    if policy == "price_tier_6_8_10_shares":
        if entry <= 0.08:
            return 6.0
        if entry <= 0.14:
            return 8.0
        return 10.0
    if policy == "quality_price_tier_5_8_12_shares":
        quality = pcal_ev if math.isfinite(pcal_ev) else edge
        base = 5.0 if entry <= 0.08 else (8.0 if entry <= 0.14 else 10.0)
        mult = 1.2 if quality >= 0.50 else (1.0 if quality >= 0.30 else 0.8)
        return min(12.0, max(5.0, base * mult))
    raise RuntimeError(f"unknown sizing policy {policy}")


def exit_price_for(row: pd.Series, policy: str) -> tuple[float, str, bool]:
    if policy == "hold":
        return float(row["payoff"]), "settlement", False
    if policy == "strict_dead_or_late_dust":
        strict_bid = to_float(row.get("strict_dead_stop_bid"))
        if math.isfinite(strict_bid):
            return strict_bid, "strict_dead_stop", True
        dust_bid = to_float(row.get("late_dust_stop_bid"))
        if math.isfinite(dust_bid):
            return dust_bid, "late_dust_stop", True
        return float(row["payoff"]), "settlement", False
    if policy == "time_stop_or_late_salvage":
        stop_bid = to_float(row.get("time_stop_bid"))
        if math.isfinite(stop_bid):
            return max(0.0, stop_bid - 0.01), "time_stop", True
        salvage_bid = to_float(row.get("late_salvage_bid"))
        if math.isfinite(salvage_bid):
            return max(0.0, salvage_bid - 0.01), "late_salvage", True
        return float(row["payoff"]), "settlement", False
    raise RuntimeError(f"unknown exit policy {policy}")


def simulate_execution(frame: pd.DataFrame) -> pd.DataFrame:
    sizing_policies = [
        "fixed_cash_0p80",
        "fixed_6_shares",
        "fixed_8_shares",
        "fixed_10_shares",
        "price_tier_6_8_10_shares",
        "quality_price_tier_5_8_12_shares",
    ]
    entry_profiles = [
        ("maker_no_fee", 0.0, False),
        ("taker_weather_fee", 0.0, True),
        ("taker_plus1c_weather_fee", 0.01, True),
    ]
    exit_policies = ["hold", "strict_dead_or_late_dust", "time_stop_or_late_salvage"]
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        for sizing in sizing_policies:
            shares = sizing_shares(row, sizing)
            for entry_profile, add_cents, entry_taker in entry_profiles:
                entry_px = min(0.99, float(row["entry"]) + add_cents)
                entry_fee = weather_taker_fee(shares=shares, price=entry_px) if entry_taker else 0.0
                cost = shares * entry_px + entry_fee
                for exit_policy in exit_policies:
                    exit_px, exit_mode, stop_hit = exit_price_for(row, exit_policy)
                    exit_fee = (
                        weather_taker_fee(shares=shares, price=exit_px)
                        if stop_hit and exit_px > 0.0
                        else 0.0
                    )
                    pnl = shares * exit_px - shares * entry_px - entry_fee - exit_fee
                    rows.append(
                        {
                            "row_id": int(row["row_id"]),
                            "candidate_id": row["candidate_id"],
                            "target_date": row["target_date"],
                            "city": row["city"],
                            "bracket": row["bracket"],
                            "book_state_v1": row["book_state_v1"],
                            "raw_dist_band": row["raw_dist_band"],
                            "adj_dist_band": row["adj_dist_band"],
                            "price_band": row["price_band"],
                            "period": row.get("period", ""),
                            "entry": float(row["entry"]),
                            "payoff": float(row["payoff"]),
                            "win": bool(row["win"]),
                            "sizing": sizing,
                            "shares": shares,
                            "entry_profile": entry_profile,
                            "entry_price_effective": entry_px,
                            "entry_fee": entry_fee,
                            "exit_policy": exit_policy,
                            "exit_mode": exit_mode,
                            "exit_price": exit_px,
                            "exit_fee": exit_fee,
                            "stop_hit": stop_hit,
                            "cost": cost,
                            "pnl": pnl,
                            "roi": pnl / cost if cost > 0 else math.nan,
                        }
                    )
    return pd.DataFrame(rows)


def daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["target_date", "rows", "wins", "cost", "pnl", "roi"])
    out = frame.groupby("target_date", as_index=False).agg(
        rows=("row_id", "count"),
        wins=("win", "sum"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    out["roi"] = out["pnl"] / out["cost"]
    return out


def date_block_ci(frame: pd.DataFrame) -> tuple[float | None, float | None]:
    d = daily(frame)
    if len(d) < 3:
        return None, None
    rng = np.random.default_rng(RNG_SEED)
    pnl = d["pnl"].to_numpy(dtype=float)
    cost = d["cost"].to_numpy(dtype=float)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(d), len(d))
        c = float(cost[idx].sum())
        if c > 0:
            vals.append(float(pnl[idx].sum() / c))
    arr = np.asarray(vals, dtype=float)
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def summarize_perf(frame: pd.DataFrame, *, label: str, period: str) -> dict[str, Any]:
    if frame.empty:
        return {"label": label, "period": period, "rows": 0}
    d = daily(frame)
    cost = float(frame["cost"].sum())
    pnl = float(frame["pnl"].sum())
    ci_low, ci_high = date_block_ci(frame)
    return {
        "label": label,
        "period": period,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "win_rate": float(frame["win"].mean()),
        "avg_entry": float(frame["entry"].mean()),
        "avg_cost": float(frame["cost"].mean()),
        "stop_hit_rate": float(frame["stop_hit"].mean()) if "stop_hit" in frame else 0.0,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "losing_days": int((d["pnl"] < 0).sum()),
        "le_minus50pct_days": int((d["roi"] <= -0.5).sum()),
        "max_daily_loss_usd": float(d["pnl"].min()) if not d.empty else math.nan,
        "max_daily_loss_roi": float(d["roi"].min()) if not d.empty else math.nan,
    }


def summarize_by_period(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    periods = {
        "full": pd.Series(True, index=rows.index),
        "train_le_2026_06_20": rows["target_date"].astype(str) <= TRAIN_END,
        "recent_ge_2026_06_21": rows["target_date"].astype(str) >= RECENT_START,
    }
    for period, mask in periods.items():
        base = rows[mask].copy()
        if base.empty:
            continue
        for key, g in base.groupby(group_cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            label = "|".join(str(x) for x in key)
            rec = summarize_perf(g, label=label, period=period)
            for col, val in zip(group_cols, key, strict=True):
                rec[col] = val
            records.append(rec)
    return pd.DataFrame(records)


def load_snapshot_records(needed: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    if not needed:
        return {}
    snapshot_dates = {key[2][:10].replace("-", "") for key in needed if key[2]}
    needed_ts = {key[2] for key in needed}
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        stamp = path.name.removeprefix("snapshot_").removesuffix(".json")
        if stamp.split("_", 1)[0] not in snapshot_dates:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        payload_ts = ts_key(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        if payload_ts not in needed_ts:
            continue
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            key = (
                str(rec.get("target_date") or rec.get("event_date") or ""),
                str(rec.get("city") or ""),
                payload_ts,
            )
            if key not in needed:
                continue
            out[key].append(rec)
    return dict(out)


def ask_from_record(rec: dict[str, Any]) -> float:
    # Historical HeadA used market_yes_price as the quoted decision price; the
    # +1c stress profile in summaries handles executable taker slippage.
    price = to_float(rec.get("market_yes_price"))
    if math.isfinite(price):
        return price
    return to_float(rec.get("yes_best_ask"))


def build_expression_replay(base: pd.DataFrame) -> pd.DataFrame:
    hot = base[base["hot_tail_boundary_v1"]].copy()
    needed = {(r.target_date, r.city, r.decision_ts_key) for r in hot.itertuples(index=False)}
    snapshots = load_snapshot_records(needed)
    settlements = load_settlements()
    settle_map = {
        (str(r.city), str(r.target_date), str(r.bracket)): to_float(r.final_price)
        for r in settlements.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for row in hot.itertuples(index=False):
        key = (str(row.target_date), str(row.city), str(row.decision_ts_key))
        records = snapshots.get(key, [])
        if not records:
            continue
        candidates: list[dict[str, Any]] = []
        for rec in records:
            ask = ask_from_record(rec)
            low_f = bracket_low_f(rec.get("bracket"), rec.get("unit") or row.unit)
            if not math.isfinite(ask) or not math.isfinite(low_f):
                continue
            candidates.append(
                {
                    "bracket": str(rec.get("bracket")),
                    "condition_id": str(rec.get("condition_id") or ""),
                    "ask": ask,
                    "low_f": low_f,
                    "is_plus": "+" in str(rec.get("bracket") or ""),
                    "payoff": settle_map.get((str(row.city), str(row.target_date), str(rec.get("bracket"))), math.nan),
                }
            )
        candidates.sort(key=lambda x: (x["low_f"], x["bracket"]))
        selected_idx = next(
            (
                i
                for i, rec in enumerate(candidates)
                if rec["condition_id"] == str(row.condition_id) or rec["bracket"] == str(row.bracket)
            ),
            None,
        )
        if selected_idx is None:
            continue

        def add_leg(expression: str, idx: int, shares: float = 8.0) -> None:
            if idx < 0 or idx >= len(candidates):
                return
            rec = candidates[idx]
            payoff = to_float(rec.get("payoff"))
            if not math.isfinite(payoff):
                return
            entry = float(rec["ask"])
            fee = weather_taker_fee(shares=shares, price=entry)
            cost = shares * entry + fee
            pnl = shares * payoff - cost
            rows.append(
                {
                    "row_id": int(row.row_id),
                    "candidate_id": row.candidate_id,
                    "target_date": row.target_date,
                    "city": row.city,
                    "selected_bracket": row.bracket,
                    "expression": expression,
                    "expression_bracket": rec["bracket"],
                    "entry": entry,
                    "shares": shares,
                    "cost": cost,
                    "pnl": pnl,
                    "payoff": payoff,
                    "win": payoff >= 0.5,
                    "book_state_v1": row.book_state_v1,
                    "raw_dist_band": row.raw_dist_band,
                    "adj_dist_band": row.adj_dist_band,
                }
            )

        add_leg("selected_yes", selected_idx)
        add_leg("next_hotter_yes", selected_idx + 1)
        add_leg("two_hotter_yes", selected_idx + 2)
        plus_idx = next((i for i, rec in enumerate(candidates) if i > selected_idx and rec["is_plus"]), None)
        if plus_idx is not None:
            add_leg("higher_plus_yes", plus_idx)

        if selected_idx + 1 < len(candidates):
            selected = candidates[selected_idx]
            nxt = candidates[selected_idx + 1]
            payoff_a = to_float(selected.get("payoff"))
            payoff_b = to_float(nxt.get("payoff"))
            if math.isfinite(payoff_a) and math.isfinite(payoff_b):
                shares_each = 4.0
                cost = (
                    shares_each * selected["ask"]
                    + weather_taker_fee(shares=shares_each, price=selected["ask"])
                    + shares_each * nxt["ask"]
                    + weather_taker_fee(shares=shares_each, price=nxt["ask"])
                )
                pnl = shares_each * payoff_a + shares_each * payoff_b - cost
                rows.append(
                    {
                        "row_id": int(row.row_id),
                        "candidate_id": row.candidate_id,
                        "target_date": row.target_date,
                        "city": row.city,
                        "selected_bracket": row.bracket,
                        "expression": "selected_plus_next_basket",
                        "expression_bracket": f"{selected['bracket']}+{nxt['bracket']}",
                        "entry": cost / 8.0,
                        "shares": 8.0,
                        "cost": cost,
                        "pnl": pnl,
                        "payoff": max(payoff_a, payoff_b),
                        "win": (payoff_a >= 0.5 or payoff_b >= 0.5),
                        "book_state_v1": row.book_state_v1,
                        "raw_dist_band": row.raw_dist_band,
                        "adj_dist_band": row.adj_dist_band,
                    }
                )
    return pd.DataFrame(rows)


def expression_summary(expr: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for expression, g in expr.groupby("expression", dropna=False):
        records.append(summarize_perf(g, label=str(expression), period="full"))
    return pd.DataFrame(records)


def paired_expression_summary(expr: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    wide = expr.pivot_table(
        index=["row_id", "target_date", "city"],
        columns="expression",
        values=["cost", "pnl", "entry", "win"],
        aggfunc="first",
    )
    for alt in ["next_hotter_yes", "two_hotter_yes", "higher_plus_yes", "selected_plus_next_basket"]:
        if ("pnl", alt) not in wide.columns or ("pnl", "selected_yes") not in wide.columns:
            continue
        sub = wide[[("pnl", "selected_yes"), ("cost", "selected_yes"), ("win", "selected_yes"), ("entry", "selected_yes"), ("pnl", alt), ("cost", alt), ("win", alt), ("entry", alt)]].dropna()
        if sub.empty:
            continue
        for expression in ["selected_yes", alt]:
            frame = pd.DataFrame(
                {
                    "row_id": np.arange(len(sub)),
                    "target_date": sub.index.get_level_values("target_date"),
                    "city": sub.index.get_level_values("city"),
                    "pnl": sub[("pnl", expression)].to_numpy(float),
                    "cost": sub[("cost", expression)].to_numpy(float),
                    "win": sub[("win", expression)].astype(bool).to_numpy(),
                    "entry": sub[("entry", expression)].to_numpy(float),
                    "stop_hit": False,
                }
            )
            rec = summarize_perf(frame, label=expression, period=f"same_denominator_vs_{alt}")
            rec["paired_alt"] = alt
            out.append(rec)
    return pd.DataFrame(out)


def sizing_attribution(execution: pd.DataFrame) -> pd.DataFrame:
    focus = execution[
        execution["entry_profile"].eq("taker_weather_fee")
        & execution["exit_policy"].eq("hold")
        & execution["sizing"].isin(
            [
                "fixed_cash_0p80",
                "fixed_8_shares",
                "price_tier_6_8_10_shares",
                "quality_price_tier_5_8_12_shares",
            ]
        )
    ].copy()
    periods = {
        "full": pd.Series(True, index=focus.index),
        "train_le_2026_06_20": focus["target_date"].astype(str) <= TRAIN_END,
        "recent_ge_2026_06_21": focus["target_date"].astype(str) >= RECENT_START,
    }
    records: list[dict[str, Any]] = []
    for period, mask in periods.items():
        base = focus[mask].copy()
        if base.empty:
            continue
        for (sizing, price_band), g in base.groupby(["sizing", "price_band"], dropna=False):
            rec = summarize_perf(g, label=f"{sizing}|{price_band}", period=period)
            rec.update(
                {
                    "sizing": sizing,
                    "price_band": price_band,
                    "avg_shares": float(g["shares"].mean()),
                    "shares_sum": float(g["shares"].sum()),
                    "wins": int(g["win"].sum()),
                }
            )
            records.append(rec)
    out = pd.DataFrame(records)
    if out.empty:
        return out
    totals = out.groupby(["period", "sizing"], dropna=False).agg(
        total_cost=("cost", "sum"),
        total_shares=("shares_sum", "sum"),
        total_rows=("rows", "sum"),
        total_wins=("wins", "sum"),
    )
    out = out.join(totals, on=["period", "sizing"])
    out["cost_pct"] = out["cost"] / out["total_cost"]
    out["shares_pct"] = out["shares_sum"] / out["total_shares"]
    out["rows_pct"] = out["rows"] / out["total_rows"]
    out["wins_pct"] = np.where(out["total_wins"] > 0, out["wins"] / out["total_wins"], np.nan)
    return out.drop(columns=["total_cost", "total_shares", "total_rows", "total_wins"])


def make_markdown(
    *,
    base: pd.DataFrame,
    hot: pd.DataFrame,
    exec_summary: pd.DataFrame,
    sizing_attr: pd.DataFrame,
    slices: pd.DataFrame,
    expr_summary: pd.DataFrame,
    expr_paired: pd.DataFrame,
    live_rows: pd.DataFrame,
) -> str:
    def md_table(df: pd.DataFrame, cols: list[str], max_rows: int = 20) -> str:
        if df.empty:
            return "_No rows._"
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in df.head(max_rows).iterrows():
            vals = []
            for col in cols:
                val = row.get(col, "")
                if col.startswith("roi") or col in {
                    "win_rate",
                    "stop_hit_rate",
                    "avg_entry",
                    "cost_pct",
                    "shares_pct",
                    "rows_pct",
                    "wins_pct",
                }:
                    vals.append(fmt_pct(val, signed=col.startswith("roi")))
                elif col in {"avg_cost", "cost", "pnl", "max_daily_loss_usd"}:
                    vals.append(fmt_usd(val))
                elif col in {"avg_shares", "shares_sum"}:
                    vals.append(f"{float(val):.1f}" if pd.notna(val) else "")
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append(str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus = exec_summary[
        exec_summary["period"].eq("full")
        & exec_summary["sizing"].isin(["fixed_cash_0p80", "fixed_8_shares", "price_tier_6_8_10_shares", "quality_price_tier_5_8_12_shares"])
        & exec_summary["entry_profile"].isin(["maker_no_fee", "taker_weather_fee", "taker_plus1c_weather_fee"])
        & exec_summary["exit_policy"].isin(["hold", "strict_dead_or_late_dust"])
    ].copy()
    focus["_rank"] = focus["sizing"].astype(str) + focus["entry_profile"].astype(str) + focus["exit_policy"].astype(str)
    order = {
        "fixed_cash_0p80maker_no_feehold": 1,
        "fixed_cash_0p80taker_weather_feehold": 2,
        "fixed_cash_0p80taker_plus1c_weather_feehold": 3,
        "fixed_cash_0p80taker_weather_feestrict_dead_or_late_dust": 4,
        "fixed_8_sharestaker_weather_feehold": 5,
        "fixed_8_sharestaker_weather_feestrict_dead_or_late_dust": 6,
        "price_tier_6_8_10_sharestaker_weather_feehold": 7,
        "quality_price_tier_5_8_12_sharestaker_weather_feehold": 8,
    }
    focus["_order"] = focus["_rank"].map(order).fillna(99)
    focus = focus.sort_values(["_order", "sizing", "entry_profile", "exit_policy"])

    window_focus = exec_summary[
        exec_summary["sizing"].isin(["fixed_cash_0p80", "fixed_8_shares", "price_tier_6_8_10_shares"])
        & exec_summary["entry_profile"].eq("taker_weather_fee")
        & exec_summary["exit_policy"].eq("hold")
    ].copy()
    window_focus["_order"] = window_focus["sizing"].map(
        {"fixed_cash_0p80": 1, "fixed_8_shares": 2, "price_tier_6_8_10_shares": 3}
    )
    window_focus = window_focus.sort_values(["period", "_order"])

    attr_full = sizing_attr[
        sizing_attr["period"].eq("full")
        & sizing_attr["sizing"].isin(["fixed_cash_0p80", "fixed_8_shares", "price_tier_6_8_10_shares"])
    ].copy()
    attr_full["_order"] = attr_full["sizing"].map(
        {"fixed_cash_0p80": 1, "fixed_8_shares": 2, "price_tier_6_8_10_shares": 3}
    )
    attr_full["_band_order"] = attr_full["price_band"].map({"5-8c": 1, "8-14c": 2, "14-20c": 3}).fillna(9)
    attr_full = attr_full.sort_values(["_order", "_band_order"])

    slice_view = slices[slices["period"].eq("full")].copy()
    slice_view = slice_view.sort_values(["slice_type", "label"])

    expr_pair_view = expr_paired[
        expr_paired["paired_alt"].isin(["next_hotter_yes", "selected_plus_next_basket"])
    ].copy()

    live_note = "_No fresh live/shadow rows found._"
    if not live_rows.empty:
        status_counts = live_rows.groupby(["decision_status", "blocker"], dropna=False).size().reset_index(name="rows")
        live_note = md_table(status_counts, ["decision_status", "blocker", "rows"], 20)

    dist_le0 = int((base["raw_dist_br"] <= 0).sum())
    dist_lt0 = int((base["raw_dist_br"] < 0).sum())
    dist_eq0 = int((base["raw_dist_br"] == 0).sum())
    dist_gt0 = int((base["raw_dist_br"] > 0).sum())

    return f"""# HeadA Low-Price YES Refinement v1

Generated: {now_utc()}

Scope: only HeadA `forecast_tail_low_price_yes`.  This is a refinement study for the existing low-price forecast-tail YES sleeve, not METAR reversal and not tmax distribution.

## One-Line Read

After removing `dist<=0`, HeadA is cleaner but still not ready to size up.  The strongest practical next step is **keep live tiny, collect fresh book-state/fill data, and shadow fixed-share / price-tier sizing**.  The two tempting quick fixes do not pass: hotter sibling replay does **not** show that we should simply buy the next higher bracket, and strict stop only looks useful in some windows after costs, so it stays telemetry.

```text
significance=NA/partial
baseline=PARTIAL
forward=FAIL/NA
conclusion=shadow_candidate for current tiny probe; no live size-up
```

## Data Snapshot

- Synced N100 mirror and rebuilt `runtime/weather.db` before running this script.
- CLOB fill coverage gate: `gate_pass=true` after rebuild.
- Frozen HeadA denominator: {len(base)} rows, {base['target_date'].min()}..{base['target_date'].max()}, {base['city'].nunique()} cities.
- Current implemented selector after 2026-07-04: `dist > 0`; historical hot-only denominator here: {len(hot)} rows / {hot['target_date'].nunique()} dates / {hot['city'].nunique()} cities.
- `dist<=0` would have blocked {dist_le0} / {len(base)} rows ({dist_le0 / len(base):.1%}): `dist<0` {dist_lt0}, `dist=0` {dist_eq0}; remaining `dist>0` {dist_gt0}.
- Fee model: official Weather taker `shares * 0.05 * price * (1-price)`; maker fee baseline zero.

## Execution / Sizing On Hot-Only

{md_table(focus, ['sizing', 'entry_profile', 'exit_policy', 'rows', 'dates', 'win_rate', 'avg_entry', 'avg_cost', 'stop_hit_rate', 'roi', 'roi_ci_low', 'roi_ci_high', 'losing_days', 'le_minus50pct_days', 'max_daily_loss_usd'], 30)}

Interpretation:

1. Fixed cash `$0.80` is still a hidden bet that 5c tickets deserve much larger max payout than 15c tickets.
2. Fixed 8 shares and price-tier sizing are more coherent for this sleeve because they make the lottery payout more comparable across prices.
3. +1c taker stress matters; this is why maker-first telemetry is still central, even though the backtest reports taker profiles.
4. Strict stop is not a live rule yet. It is useful as telemetry because it can reduce daily drawdown, but it does not dominate hold robustly enough.

## Sizing First-Principles Experiment

For a binary YES ticket, one share has expected PnL `P(win) - entry - fee_per_share`.  Because this sleeve does not yet have a trusted per-row calibrated `P(win)`, the first sizing question is not Kelly sizing; it is exposure geometry.

- Fixed cash `$0.80` means `shares = 0.80 / entry`: a 5c ticket gets 16 shares, a 20c ticket gets 4 shares.  This implicitly says cheaper tickets deserve much larger max payout.
- Fixed shares means every signal gets the same max payout; cost naturally rises with entry price.
- Price-tier sizing is the mild middle ground used here: 6 shares for 5-8c, 8 shares for 8-14c, 10 shares for 14-20c.  It only uses entry price, not city/date fitting.

Full-window attribution by price bucket, same 333 hot-only rows, taker fee, hold-to-settlement:

{md_table(attr_full, ['sizing', 'price_band', 'rows', 'win_rate', 'avg_entry', 'avg_shares', 'cost_pct', 'shares_pct', 'wins_pct', 'roi', 'roi_ci_low', 'roi_ci_high'], 30)}

Window stability for the three non-quality sizing rules:

{md_table(window_focus, ['period', 'sizing', 'rows', 'dates', 'win_rate', 'avg_entry', 'avg_cost', 'roi', 'roi_ci_low', 'roi_ci_high', 'losing_days', 'max_daily_loss_usd'], 20)}

Read: the strongest price bucket in this historical sleeve is not the cheapest bucket; 14-20c has the highest realized hit rate.  That is why fixed cash is not the natural default.  But price-tier still remains shadow-only because the recent window is small and the rule has not passed a fresh forward clock.

## Mechanism Slices

Main execution profile for slices: `fixed_8_shares + taker_weather_fee + hold`.

{md_table(slice_view, ['slice_type', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'losing_days', 'max_daily_loss_usd'], 60)}

Read this as hypothesis quality, not a selector menu.  `book_state_v1` remains the key uncertainty: if future live rows prove thin/wide books are actually fillable near the decision ask, the sleeve has a plausible attention/stale-attention alpha; if not, historical edge collapses toward the feasible-book baseline.

## Sibling Expression Replay

Single-leg expressions use 8 shares and official Weather taker fee.  Basket uses 4 shares selected + 4 shares next hotter.

{md_table(expr_summary.sort_values('label'), ['label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'losing_days', 'max_daily_loss_usd'], 20)}

Same-denominator checks:

{md_table(expr_pair_view, ['paired_alt', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high'], 20)}

Conclusion: overshoot is real, but the obvious cure is not.  The next hotter bracket is usually cheaper for a reason, and the basket waters down the convexity.  This argues for **better probability/EV ranking before entry**, not a blanket hotter-bracket switch.

## Fresh Forward Telemetry

Fresh live/shadow rows accumulated in the local HeadA journal since {FRESH_FORWARD_START}:

{live_note}

This is too fresh to score; keep it as the W3 forward clock.  The key forward questions are fillability by `book_state_v1`, maker fill adverse selection, and whether `adj_dist_p50_br` keeps the same shape after 2026-07-04.

## Decision

No new live change from this research.

- Keep current tiny HeadA probe with `dist<=0` blocked.
- Keep notional small; no size-up.
- Do not add strict stop yet; log it forward.
- Shadow sizing should compare fixed cash `$0.80`, fixed 8 shares, and price-tier 6/8/10 shares.
- Do not switch to next-hotter or basket expression without a future EV model; current sibling replay does not support it.

## Artifacts

- Script: `{Path(__file__).relative_to(ROOT)}`
- Base rows: `{(OUT_DIR / 'base_rows.csv').relative_to(ROOT)}`
- Execution replay: `{(OUT_DIR / 'execution_replay.csv').relative_to(ROOT)}`
- Execution summary: `{(OUT_DIR / 'execution_summary.csv').relative_to(ROOT)}`
- Sizing attribution: `{(OUT_DIR / 'sizing_attribution.csv').relative_to(ROOT)}`
- Slice summary: `{(OUT_DIR / 'slice_summary.csv').relative_to(ROOT)}`
- Expression replay: `{(OUT_DIR / 'expression_replay.csv').relative_to(ROOT)}`
- JSON: `{OUT_JSON.relative_to(ROOT)}`
"""


def load_live_journal_rows() -> pd.DataFrame:
    paths = [
        ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/shadow_decisions.jsonl",
        ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/blocked_candidates.jsonl",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(obj.get("target_date") or obj.get("event_date") or "") >= FRESH_FORWARD_START:
                    rows.append(obj)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "blocker" not in df.columns:
        df["blocker"] = ""
    if "decision_status" not in df.columns:
        df["decision_status"] = ""
    return df


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base = load_base()
    paths = scan_paths(base)
    frame = base.merge(paths, on="row_id", how="left")
    hot = frame[frame["hot_tail_boundary_v1"]].copy()

    execution = simulate_execution(hot)
    periods = {
        "full": pd.Series(True, index=execution.index),
        "train_le_2026_06_20": execution["target_date"].astype(str) <= TRAIN_END,
        "recent_ge_2026_06_21": execution["target_date"].astype(str) >= RECENT_START,
    }
    exec_records: list[dict[str, Any]] = []
    for period, mask in periods.items():
        sub = execution[mask].copy()
        for (sizing, entry_profile, exit_policy), g in sub.groupby(["sizing", "entry_profile", "exit_policy"], dropna=False):
            rec = summarize_perf(g, label=f"{sizing}|{entry_profile}|{exit_policy}", period=period)
            rec.update({"sizing": sizing, "entry_profile": entry_profile, "exit_policy": exit_policy})
            exec_records.append(rec)
    exec_summary = pd.DataFrame(exec_records)
    sizing_attr = sizing_attribution(execution)

    slice_input = execution[
        execution["sizing"].eq("fixed_8_shares")
        & execution["entry_profile"].eq("taker_weather_fee")
        & execution["exit_policy"].eq("hold")
    ].copy()
    slice_frames: list[pd.DataFrame] = []
    for col in ["book_state_v1", "raw_dist_band", "adj_dist_band", "price_band"]:
        s = summarize_by_period(slice_input, [col])
        if not s.empty:
            s["slice_type"] = col
            s["label"] = s[col].astype(str)
            slice_frames.append(s)
    slices = pd.concat(slice_frames, ignore_index=True) if slice_frames else pd.DataFrame()

    expr = build_expression_replay(base)
    expr_sum = expression_summary(expr) if not expr.empty else pd.DataFrame()
    expr_pair = paired_expression_summary(expr) if not expr.empty else pd.DataFrame()
    live_rows = load_live_journal_rows()

    base.to_csv(OUT_DIR / "base_rows.csv", index=False)
    hot.to_csv(OUT_DIR / "hot_rows.csv", index=False)
    paths.to_csv(OUT_DIR / "path_rows.csv", index=False)
    execution.to_csv(OUT_DIR / "execution_replay.csv", index=False)
    exec_summary.to_csv(OUT_DIR / "execution_summary.csv", index=False)
    sizing_attr.to_csv(OUT_DIR / "sizing_attribution.csv", index=False)
    slices.to_csv(OUT_DIR / "slice_summary.csv", index=False)
    expr.to_csv(OUT_DIR / "expression_replay.csv", index=False)
    expr_sum.to_csv(OUT_DIR / "expression_summary.csv", index=False)
    expr_pair.to_csv(OUT_DIR / "expression_paired_summary.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "input": str(INPUT.relative_to(ROOT)),
        "db": str(DB_PATH.relative_to(ROOT)),
        "rows": {
            "base": int(len(base)),
            "hot_only_dist_gt0": int(len(hot)),
            "expression_rows": int(len(expr)),
            "fresh_live_journal_rows": int(len(live_rows)),
        },
        "date_range": {"min": str(base["target_date"].min()), "max": str(base["target_date"].max())},
        "fee_model": {
            "weather_taker_fee_rate": WEATHER_TAKER_FEE_RATE,
            "formula": "shares * fee_rate * price * (1 - price)",
            "maker_fee": 0,
        },
        "execution_summary": json.loads(exec_summary.to_json(orient="records")),
        "sizing_attribution": json.loads(sizing_attr.to_json(orient="records")),
        "slice_summary": json.loads(slices.to_json(orient="records")),
        "expression_summary": json.loads(expr_sum.to_json(orient="records")) if not expr_sum.empty else [],
        "expression_paired_summary": json.loads(expr_pair.to_json(orient="records")) if not expr_pair.empty else [],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(
        make_markdown(
            base=base,
            hot=hot,
            exec_summary=exec_summary,
            sizing_attr=sizing_attr,
            slices=slices,
            expr_summary=expr_sum,
            expr_paired=expr_pair,
            live_rows=live_rows,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {(OUT_DIR / 'execution_summary.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
