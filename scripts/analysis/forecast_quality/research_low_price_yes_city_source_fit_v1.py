"""HeadA city/source fit research v1.

This research keeps the current HeadA entry denominator fixed and decomposes
the apparent city effect into:

1. forecast source fit: whether the candidate's model is historically biased
   toward station actuals printing above forecast for that city;
2. market attention / book state: whether the quoted ticket was feasible or
   thin/wide/missing at the decision snapshot;
3. live execution quality: whether tiny live orders in that city actually fill.

No live selector is changed by this script.
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

from scripts.analysis.forecast_quality.research_low_price_yes_heada_refinement_v1 import (  # noqa: E402
    FRESH_FORWARD_START,
    RECENT_START,
    TRAIN_END,
    date_block_ci,
    load_base,
    weather_taker_fee,
)

BIAS_SUMMARY = (
    ROOT
    / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
)
CITY_FIT = (
    ROOT
    / "docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv"
)
LIVE_ORDERS = ROOT / "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"
CLOB_FILLS = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_city_source_fit_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.json"

N_BOOT = 5000
RNG_SEED = 20260705


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


def fmt_float(value: Any, digits: int = 2) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def shares_price_tier(entry: float) -> float:
    if entry <= 0.08:
        return 6.0
    if entry <= 0.14:
        return 8.0
    return 10.0


def simulate_price_tier(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        entry = float(row.entry)
        shares = shares_price_tier(entry)
        fee = weather_taker_fee(shares=shares, price=entry)
        cost = shares * entry + fee
        pnl = shares * float(row.payoff) - cost
        rows.append(
            {
                **row._asdict(),
                "shares": shares,
                "entry_fee": fee,
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


def top_removed_roi(frame: pd.DataFrame, n: int = 5) -> float:
    if len(frame) <= n:
        return math.nan
    sub = frame.sort_values("pnl", ascending=False).iloc[n:]
    cost = float(sub["cost"].sum())
    return float(sub["pnl"].sum() / cost) if cost > 0 else math.nan


def date_block_delta_ci(selected: pd.DataFrame, complement: pd.DataFrame) -> tuple[float | None, float | None]:
    if selected.empty or complement.empty:
        return None, None
    dates = sorted(set(selected["target_date"].astype(str)) | set(complement["target_date"].astype(str)))
    if len(dates) < 3:
        return None, None
    sel = selected.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    comp = complement.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    rng = np.random.default_rng(RNG_SEED)
    vals: list[float] = []
    for _ in range(N_BOOT):
        sample = rng.choice(dates, size=len(dates), replace=True)
        s_cost = float(sel.reindex(sample).fillna(0.0)["cost"].sum())
        s_pnl = float(sel.reindex(sample).fillna(0.0)["pnl"].sum())
        c_cost = float(comp.reindex(sample).fillna(0.0)["cost"].sum())
        c_pnl = float(comp.reindex(sample).fillna(0.0)["pnl"].sum())
        if s_cost > 0 and c_cost > 0:
            vals.append(s_pnl / s_cost - c_pnl / c_cost)
    if not vals:
        return None, None
    arr = np.asarray(vals, dtype=float)
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def summarize(frame: pd.DataFrame, *, label: str, period: str) -> dict[str, Any]:
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
        "avg_shares": float(frame["shares"].mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "top5_removed_roi": top_removed_roi(frame, 5),
        "losing_days": int((d["pnl"] < 0).sum()),
        "le_minus50pct_days": int((d["roi"] <= -0.5).sum()),
        "max_daily_loss_usd": float(d["pnl"].min()) if not d.empty else math.nan,
        "max_daily_loss_roi": float(d["roi"].min()) if not d.empty else math.nan,
    }


def load_source_profiles() -> pd.DataFrame:
    bias = pd.read_csv(BIAS_SUMMARY)
    bias["model"] = bias["model"].astype(str).str.lower()
    for col in [
        "n",
        "bias",
        "mae",
        "p50",
        "p90",
        "pct_actual_ge_forecast_plus_1",
        "pct_forecast_ge_actual_plus_1",
    ]:
        bias[col] = pd.to_numeric(bias[col], errors="coerce")
    bias["native_bracket_width"] = np.where(bias["unit"].astype(str).str.upper().eq("C"), 1.0, 2.0)
    bias["bias_br"] = bias["bias"] / bias["native_bracket_width"]
    bias["p50_br"] = bias["p50"] / bias["native_bracket_width"]
    bias["p90_br"] = bias["p90"] / bias["native_bracket_width"]
    bias["source_hot_tail_pct"] = bias["pct_actual_ge_forecast_plus_1"]
    bias["source_cold_tail_pct"] = bias["pct_forecast_ge_actual_plus_1"]

    hot_rank = bias[["city", "model", "source_hot_tail_pct"]].copy()
    hot_rank["source_hot_rank"] = hot_rank.groupby("city")["source_hot_tail_pct"].rank(
        method="dense", ascending=False
    )
    bias = bias.merge(hot_rank[["city", "model", "source_hot_rank"]], on=["city", "model"], how="left")

    best_hot = (
        bias.sort_values(["city", "source_hot_tail_pct", "bias"], ascending=[True, False, False])
        .drop_duplicates("city")
        .rename(
            columns={
                "model": "best_hot_model",
                "source_hot_tail_pct": "city_best_hot_tail_pct",
                "bias": "city_best_hot_bias",
            }
        )[["city", "best_hot_model", "city_best_hot_tail_pct", "city_best_hot_bias"]]
    )
    best_mae = (
        bias.sort_values(["city", "mae"], ascending=[True, True])
        .drop_duplicates("city")
        .rename(columns={"model": "best_mae_model", "mae": "city_best_mae"})
        [["city", "best_mae_model", "city_best_mae"]]
    )
    city_fit = pd.read_csv(CITY_FIT)
    city_fit["best_model"] = city_fit["best_model"].astype(str).str.lower()
    city_fit["fit_hot_tail_pct"] = pd.to_numeric(city_fit["hot_tail_pct"], errors="coerce") / 100.0
    city_fit["fit_cold_tail_pct"] = pd.to_numeric(city_fit["cold_tail_pct"], errors="coerce") / 100.0
    city_fit = city_fit[
        [
            "city",
            "best_model",
            "source_bias_regime",
            "runway_current_bracket_no_fit",
            "higher_yes_or_hot_break_fit",
            "fit_hot_tail_pct",
            "fit_cold_tail_pct",
        ]
    ].rename(columns={"best_model": "fit_best_model"})
    return bias.merge(best_hot, on="city", how="left").merge(best_mae, on="city", how="left").merge(
        city_fit, on="city", how="left"
    )


def attach_city_source_fit(base: pd.DataFrame) -> pd.DataFrame:
    profiles = load_source_profiles()
    keep = [
        "city",
        "model",
        "city_pool",
        "region",
        "unit",
        "n",
        "bias",
        "mae",
        "p50",
        "p90",
        "bias_br",
        "p50_br",
        "p90_br",
        "source_hot_tail_pct",
        "source_cold_tail_pct",
        "source_hot_rank",
        "best_hot_model",
        "city_best_hot_tail_pct",
        "city_best_hot_bias",
        "best_mae_model",
        "city_best_mae",
        "fit_best_model",
        "source_bias_regime",
        "runway_current_bracket_no_fit",
        "higher_yes_or_hot_break_fit",
        "fit_hot_tail_pct",
        "fit_cold_tail_pct",
    ]
    merged = base.merge(
        profiles[keep],
        left_on=["city", "forecast_model"],
        right_on=["city", "model"],
        how="left",
        suffixes=("", "_profile"),
    )
    merged["effective_best_model"] = merged["fit_best_model"].fillna(merged["best_mae_model"])
    merged["model_is_fit_best"] = merged["forecast_model"].eq(merged["effective_best_model"])
    merged["model_is_best_hot"] = merged["forecast_model"].eq(merged["best_hot_model"])
    merged["hot_tail_gap_vs_best"] = merged["city_best_hot_tail_pct"] - merged["source_hot_tail_pct"]
    merged["source_p90_br"] = merged["p90_br"]
    merged["source_fit_hot_clean"] = (
        merged["bias"].gt(0)
        & merged["source_hot_tail_pct"].ge(0.40)
        & merged["source_cold_tail_pct"].le(0.12)
    )
    merged["source_fit_hot_noisy"] = (
        merged["bias"].gt(0)
        & merged["source_hot_tail_pct"].ge(0.40)
        & ~merged["source_fit_hot_clean"]
    )
    merged["source_fit_neutral_or_cold"] = ~(merged["source_fit_hot_clean"] | merged["source_fit_hot_noisy"])
    merged["source_fit_bucket"] = np.select(
        [
            merged["source_fit_hot_clean"],
            merged["source_fit_hot_noisy"],
            merged["source_fit_neutral_or_cold"],
        ],
        ["hot_clean", "hot_noisy", "neutral_or_cold"],
        default="unknown",
    )
    merged["book_attention_thin_or_missing"] = merged["book_state_v1"].isin(["missing", "thin_wide"])
    merged["wrong_source_hot_alt"] = (
        ~merged["model_is_best_hot"].fillna(False)
        & merged["hot_tail_gap_vs_best"].ge(0.15)
    )
    merged["source_fit_score"] = (
        merged["bias"].gt(0).astype(int)
        + merged["source_hot_tail_pct"].ge(0.40).astype(int)
        + merged["source_cold_tail_pct"].le(0.12).astype(int)
        + merged["source_p90_br"].ge(0.75).astype(int)
        + merged["model_is_fit_best"].fillna(False).astype(int)
    )
    merged["source_attention_score"] = merged["source_fit_score"] + merged[
        "book_attention_thin_or_missing"
    ].astype(int)
    merged["source_fit_label"] = (
        merged["forecast_model"].astype(str)
        + "|"
        + merged["source_fit_bucket"].astype(str)
        + "|best="
        + merged["model_is_fit_best"].astype(str)
    )
    return merged


def period_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "full": pd.Series(True, index=rows.index),
        "train_le_2026_06_20": rows["target_date"].astype(str) <= TRAIN_END,
        "recent_ge_2026_06_21": rows["target_date"].astype(str) >= RECENT_START,
        "fresh_forward_ge_2026_07_04": rows["target_date"].astype(str) >= FRESH_FORWARD_START,
    }


def selector_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "baseline_hot_only": pd.Series(True, index=rows.index),
        "source_hot_clean": rows["source_fit_hot_clean"].fillna(False),
        "source_hot_clean_and_fit_best": rows["source_fit_hot_clean"].fillna(False)
        & rows["model_is_fit_best"].fillna(False),
        "source_hot_clean_attention": rows["source_fit_hot_clean"].fillna(False)
        & rows["book_attention_thin_or_missing"].fillna(False),
        "source_hot_clean_feasible_book": rows["source_fit_hot_clean"].fillna(False)
        & rows["book_state_v1"].eq("feasible"),
        "fit_best_model_match": rows["model_is_fit_best"].fillna(False),
        "best_hot_model_match": rows["model_is_best_hot"].fillna(False),
        "wrong_source_hot_alt": rows["wrong_source_hot_alt"].fillna(False),
        "source_score_ge4": rows["source_fit_score"].ge(4),
        "source_score_le2": rows["source_fit_score"].le(2),
        "source_attention_score_ge5": rows["source_attention_score"].ge(5),
    }


def summarize_selectors(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    masks = selector_masks(rows)
    periods = period_masks(rows)
    for period, p_mask in periods.items():
        period_rows = rows[p_mask].copy()
        if period_rows.empty:
            continue
        for label, mask in masks.items():
            selected = period_rows[mask.reindex(period_rows.index).fillna(False)].copy()
            if selected.empty:
                records.append({"label": label, "period": period, "rows": 0})
                continue
            comp = period_rows.drop(selected.index)
            rec = summarize(selected, label=label, period=period)
            rec["denom_rows"] = int(len(period_rows))
            rec["row_share"] = float(len(selected) / len(period_rows))
            if not comp.empty:
                comp_cost = float(comp["cost"].sum())
                comp_roi = float(comp["pnl"].sum() / comp_cost) if comp_cost > 0 else math.nan
                rec["complement_rows"] = int(len(comp))
                rec["complement_roi"] = comp_roi
                rec["roi_minus_complement"] = rec["roi"] - comp_roi
                d_low, d_high = date_block_delta_ci(selected, comp)
                rec["delta_ci_low"] = d_low
                rec["delta_ci_high"] = d_high
            records.append(rec)
    return pd.DataFrame(records)


def grouped_summary(rows: pd.DataFrame, group_cols: list[str], *, label_prefix: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for period, mask in period_masks(rows).items():
        sub = rows[mask].copy()
        if sub.empty:
            continue
        for key, g in sub.groupby(group_cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            rec = summarize(g, label=label_prefix + ":" + "|".join(str(x) for x in key), period=period)
            for col, val in zip(group_cols, key, strict=True):
                rec[col] = val
            records.append(rec)
    return pd.DataFrame(records)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_order_id(order: dict[str, Any]) -> str:
    exchange = order.get("exchange_response")
    if isinstance(exchange, dict):
        place = exchange.get("place")
        if isinstance(place, dict):
            return str(place.get("orderID") or place.get("order_id") or "")
    return str(order.get("order_id") or "")


def parse_order_status(order: dict[str, Any]) -> str:
    exchange = order.get("exchange_response")
    if isinstance(exchange, dict):
        place = exchange.get("place")
        if isinstance(place, dict) and place.get("status"):
            return str(place.get("status"))
    return str(order.get("status") or "")


def load_live_execution_quality() -> tuple[pd.DataFrame, pd.DataFrame]:
    orders = pd.DataFrame(load_jsonl(LIVE_ORDERS))
    fills = pd.DataFrame(load_jsonl(CLOB_FILLS))
    if orders.empty:
        return pd.DataFrame(), pd.DataFrame()

    orders = orders[orders.get("strategy_id", "").astype(str).eq("low_price_yes_lottery_tiny_live_v1")].copy()
    if orders.empty:
        return pd.DataFrame(), pd.DataFrame()
    orders["order_id"] = [parse_order_id(obj) for obj in orders.to_dict("records")]
    orders["place_status"] = [parse_order_status(obj) for obj in orders.to_dict("records")]
    orders["created_at_utc_ts"] = pd.to_datetime(orders["created_at_utc"], utc=True, errors="coerce")
    orders["size_num"] = pd.to_numeric(orders.get("size"), errors="coerce")
    orders["limit_price_num"] = pd.to_numeric(orders.get("limit_price"), errors="coerce")
    orders["opportunity_key"] = (
        orders["city"].astype(str)
        + "|"
        + orders["target_date"].astype(str)
        + "|"
        + orders["bracket"].astype(str)
        + "|"
        + orders["signal_id"].astype(str)
    )

    fill_by_order = pd.DataFrame()
    if not fills.empty and "order_id" in fills.columns:
        fills["filled_at_utc_ts"] = pd.to_datetime(fills["filled_at_utc"], utc=True, errors="coerce")
        fills["filled_shares_num"] = pd.to_numeric(fills["filled_shares"], errors="coerce").fillna(0.0)
        fills["filled_price_num"] = pd.to_numeric(fills["filled_price"], errors="coerce").fillna(0.0)
        fills["fill_cost"] = fills["filled_shares_num"] * fills["filled_price_num"]
        fill_by_order = fills.groupby("order_id", as_index=False).agg(
            filled_shares=("filled_shares_num", "sum"),
            fill_cost=("fill_cost", "sum"),
            first_fill_ts=("filled_at_utc_ts", "min"),
            fills=("fill_id", "nunique"),
        )
    if fill_by_order.empty:
        orders["filled_shares"] = 0.0
        orders["fill_cost"] = 0.0
        orders["first_fill_ts"] = pd.NaT
        orders["fills"] = 0
    else:
        orders = orders.merge(fill_by_order, on="order_id", how="left")
        orders["filled_shares"] = pd.to_numeric(orders["filled_shares"], errors="coerce").fillna(0.0)
        orders["fill_cost"] = pd.to_numeric(orders["fill_cost"], errors="coerce").fillna(0.0)
        orders["fills"] = pd.to_numeric(orders["fills"], errors="coerce").fillna(0).astype(int)
    orders["fill_ratio"] = np.where(orders["size_num"] > 0, orders["filled_shares"] / orders["size_num"], np.nan)
    orders["first_fill_wait_min"] = (
        pd.to_datetime(orders["first_fill_ts"], utc=True, errors="coerce") - orders["created_at_utc_ts"]
    ).dt.total_seconds() / 60.0

    opp = orders.groupby("opportunity_key", as_index=False).agg(
        city=("city", "first"),
        target_date=("target_date", "first"),
        bracket=("bracket", "first"),
        signal_id=("signal_id", "first"),
        orders=("order_id", "count"),
        submitted_orders=("order_id", lambda s: int(s.astype(str).ne("").sum())),
        first_created_at_utc=("created_at_utc_ts", "min"),
        last_created_at_utc=("created_at_utc_ts", "max"),
        requested_shares=("size_num", "max"),
        filled_shares=("filled_shares", "sum"),
        fill_cost=("fill_cost", "sum"),
        first_fill_wait_min=("first_fill_wait_min", "min"),
        maker_only=("maker_only", "max"),
        execution_mode=("execution_mode", "last"),
        place_status=("place_status", "last"),
    )
    opp["any_fill"] = opp["filled_shares"].gt(0)
    opp["full_fill"] = opp["filled_shares"].ge(opp["requested_shares"] * 0.999)
    opp["fill_ratio"] = np.where(opp["requested_shares"] > 0, opp["filled_shares"] / opp["requested_shares"], np.nan)

    city = opp.groupby("city", as_index=False).agg(
        opportunities=("opportunity_key", "count"),
        any_fill_rate=("any_fill", "mean"),
        full_fill_rate=("full_fill", "mean"),
        avg_fill_ratio=("fill_ratio", "mean"),
        total_requested_shares=("requested_shares", "sum"),
        total_filled_shares=("filled_shares", "sum"),
        total_fill_cost=("fill_cost", "sum"),
        median_first_fill_wait_min=("first_fill_wait_min", "median"),
    )
    return opp, city


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for col in cols:
            val = row.get(col, "")
            if col in {
                "win_rate",
                "avg_entry",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "complement_roi",
                "roi_minus_complement",
                "delta_ci_low",
                "delta_ci_high",
                "row_share",
                "any_fill_rate",
                "full_fill_rate",
                "avg_fill_ratio",
                "source_hot_tail_pct",
                "source_cold_tail_pct",
                "hot_tail_gap_vs_best",
            }:
                vals.append(fmt_pct(val, signed=col not in {"win_rate", "avg_entry", "row_share", "any_fill_rate", "full_fill_rate", "avg_fill_ratio", "source_hot_tail_pct", "source_cold_tail_pct", "hot_tail_gap_vs_best"}))
            elif col in {"cost", "pnl", "max_daily_loss_usd", "total_fill_cost"}:
                vals.append(fmt_usd(val))
            elif col in {"avg_shares", "median_first_fill_wait_min", "bias", "bias_br", "p90_br"}:
                vals.append(fmt_float(val, 2))
            elif isinstance(val, float):
                vals.append(fmt_float(val, 3))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(
    *,
    rows: pd.DataFrame,
    selector_summary: pd.DataFrame,
    source_group_summary: pd.DataFrame,
    city_source_summary: pd.DataFrame,
    live_city_quality: pd.DataFrame,
) -> str:
    baseline_full = selector_summary[
        selector_summary["period"].eq("full") & selector_summary["label"].eq("baseline_hot_only")
    ].iloc[0]
    selector_view = selector_summary[
        selector_summary["period"].eq("full")
        & selector_summary["label"].isin(
            [
                "baseline_hot_only",
                "source_hot_clean",
                "source_hot_clean_and_fit_best",
                "source_hot_clean_attention",
                "source_hot_clean_feasible_book",
                "fit_best_model_match",
                "best_hot_model_match",
                "wrong_source_hot_alt",
                "source_score_ge4",
                "source_score_le2",
                "source_attention_score_ge5",
            ]
        )
    ].copy()
    selector_view["_order"] = selector_view["label"].map(
        {
            "baseline_hot_only": 0,
            "source_hot_clean": 1,
            "source_hot_clean_and_fit_best": 2,
            "source_hot_clean_attention": 3,
            "source_hot_clean_feasible_book": 4,
            "fit_best_model_match": 5,
            "best_hot_model_match": 6,
            "wrong_source_hot_alt": 7,
            "source_score_ge4": 8,
            "source_score_le2": 9,
            "source_attention_score_ge5": 10,
        }
    )
    selector_view = selector_view.sort_values("_order")

    window_view = selector_summary[
        selector_summary["label"].isin(["baseline_hot_only", "source_hot_clean", "source_score_ge4"])
        & selector_summary["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21"])
    ].copy()
    window_view["_order"] = window_view["label"].map(
        {"baseline_hot_only": 0, "source_hot_clean": 1, "source_score_ge4": 2}
    )
    window_view = window_view.sort_values(["period", "_order"])

    source_view = source_group_summary[source_group_summary["period"].eq("full")].copy()
    source_view = source_view.sort_values(["forecast_model", "source_fit_bucket"])

    city_view = city_source_summary[city_source_summary["period"].eq("full")].copy()
    city_view = city_view.sort_values("pnl", ascending=False)
    city_bottom = city_view.sort_values("pnl", ascending=True)

    live_view = live_city_quality.sort_values(["opportunities", "total_fill_cost"], ascending=False)

    source_bucket_counts = rows.groupby(["forecast_model", "source_fit_bucket"], dropna=False).size().reset_index(name="rows")
    source_bucket_counts = source_bucket_counts.sort_values(["forecast_model", "source_fit_bucket"])

    data_window = f"{rows['target_date'].min()}..{rows['target_date'].max()}"
    train_rows = int((rows["target_date"].astype(str) <= TRAIN_END).sum())
    recent_rows = int((rows["target_date"].astype(str) >= RECENT_START).sum())
    fresh_rows = int((rows["target_date"].astype(str) >= FRESH_FORWARD_START).sum())

    return f"""# HeadA Low-Price YES City/Source Fit v1

Generated: {now_utc()}

## 数据快照

- 数据已同步 Mac weather-data-feed，并重建 `runtime/weather.db`；`weather_clob_fill_coverage_gate.py` 通过，`gate_pass=true`。
- 分母：HeadA 当前 hot-only `dist>0` denominator，{len(rows)} rows / {rows['target_date'].nunique()} dates / {rows['city'].nunique()} cities，窗口 {data_window}。
- 训练/近期：train<=2026-06-20 为 {train_rows} rows；recent>=2026-06-21 为 {recent_rows} rows；fresh>=2026-07-04 为 {fresh_rows} rows。
- 绩效口径：`price_tier_6_8_10_shares` + taker at ask + Polymarket Weather 官方 fee `shares * 0.05 * price * (1-price)` + hold to settlement。
- 历史 source-fit 来自 `city_model_error_summary.csv` 和 `city_strategy_fit_by_forecast_bias.csv`；live 执行质量只作诊断，不进入历史 selector。

## 结论

一句话：**城市确实有相关性，但现在不该把 city 做成 hard filter；更干净的方向是把 city 拆成 forecast source fit + book attention + execution quality，先进入 shadow telemetry。**

当前 baseline：{int(baseline_full['rows'])} rows / {int(baseline_full['dates'])} dates / ROI {fmt_pct(baseline_full['roi'])}，date-block CI [{fmt_pct(baseline_full['roi_ci_low'])}, {fmt_pct(baseline_full['roi_ci_high'])}]，win {fmt_pct(baseline_full['win_rate'], signed=False)}。

```text
significance=PARTIAL
baseline=PARTIAL
forward=FAIL/NA
conclusion=shadow_candidate / no live selector change
```

最有启发的点：

1. `source_hot_clean` 能保留大部分赢家，但点估没有稳定压过 baseline，不能直接切 live。
2. `source_hot_clean + thin/missing book` 历史点估更好，说明“source fit 解释天气方向，book state 解释市场是否没跟上”这条机制值得继续 shadow。
3. `wrong_source_hot_alt` 不是简单坏分支；说明“当前 model 不是城市历史最热尾模型”不能直接当过滤器，因为市场/runner 已经隐含很多 source selection。
4. live 执行质量按城市样本还很薄，不能用今天几张 Shanghai/Amsterdam 订单决定城市池。

## 同分母 selector A/B

{md_table(selector_view, ['label', 'rows', 'row_share', 'dates', 'cities', 'win_rate', 'avg_entry', 'avg_shares', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'complement_roi', 'roi_minus_complement', 'delta_ci_low', 'delta_ci_high', 'losing_days', 'max_daily_loss_usd'], 30)}

读法：

- `source_hot_clean` = 当前使用的 forecast source 在该城市历史上 `bias>0 && hot_tail_pct>=40% && cold_tail_pct<=12%`。
- `fit_best_model_match` = 当前 source/model 等于 city-strategy-fit 文档里的 best model（缺失时回落 MAE best）。
- `best_hot_model_match` = 当前 source/model 等于历史 hot-tail rate 最高的 model。
- `wrong_source_hot_alt` = 当前 source/model 不是 hot-tail best，且与 best hot source 差距 >=15pp；这是诊断，不是 sell/avoid 规则。
- `source_attention_score_ge5` = source-fit score 高且 book_state 为 `missing/thin_wide`，用来观察“天气偏差 + 市场注意力/薄书”的交互。

## Forward / Recent

{md_table(window_view, ['period', 'label', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'losing_days', 'max_daily_loss_usd'], 20)}

这里最重要的不是谁点估最高，而是 recent 仍然太薄且日期相关性强。任何 source/city selector 都不能因为 full-window 数字好看就上线。

## Source Fit 贡献

按 `forecast_model + source_fit_bucket`：

{md_table(source_view, ['forecast_model', 'source_fit_bucket', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'pnl'], 20)}

分母构成：

{md_table(source_bucket_counts, ['forecast_model', 'source_fit_bucket', 'rows'], 20)}

这说明 source fit 有解释力，但不是单独足够的 alpha。GFS/ECMWF 不能全局选一个，要按城市/source profile 进概率层；同时还要检查盘口是否给了可买价。

## City + Source 贡献

Top positive city/source:

{md_table(city_view, ['city', 'forecast_model', 'source_fit_bucket', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'top5_removed_roi', 'pnl'], 18)}

Bottom city/source:

{md_table(city_bottom, ['city', 'forecast_model', 'source_fit_bucket', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'top5_removed_roi', 'pnl'], 18)}

这张表只用于 case review 和 forward telemetry。低样本城市不满足 `active_days>=10 && settled_fills>=30` 的 city-level live 动作门槛，不能直接 keep/cut。

## Live 执行质量诊断

`low_price_yes_lottery_tiny_live_v1` 当前真实订单按 opportunity 去重后的 city fill 质量：

{md_table(live_view, ['city', 'opportunities', 'any_fill_rate', 'full_fill_rate', 'avg_fill_ratio', 'total_requested_shares', 'total_filled_shares', 'total_fill_cost', 'median_first_fill_wait_min'], 30)}

这层回答的是“信号出来以后能不能买到”，不是 forecast alpha。样本还太少，但以后要把 city/source fit 与 fillability 一起看：有 forecast bias 但订单薄、maker 买不到，实盘 EV 仍然可能没有。

## 研究动作

下一版不建议继续加城市黑名单，而是把这些字段进 shadow 账本：

- `source_fit_bucket`
- `source_fit_score`
- `source_attention_score`
- `model_is_fit_best`
- `model_is_best_hot`
- `hot_tail_gap_vs_best`
- `live_city_fill_quality_bucket`

后续裁决方式：固定当前 HeadA live selector，只在 fresh forward 上比较这些 shadow tags 的 realized PnL、fill rate、missed-winner cost。若某个 tag 在 fresh forward 同时满足正 ROI、CI 不跨 0、top-winner removed 仍正、且 fillability 不差，再设计接回 live。

## 8 环覆盖自检

- 1 描述性绩效切片：PASS
- 2 统计推断：PARTIAL，date-block bootstrap 已做，但多 selector K=11，未做正式多重检验校正
- 3 信号判别：PARTIAL，source-fit score 有排序诊断但不是概率模型
- 4 概率分布评估：NA，本轮不校准 P(win)
- 5 执行微结构：PARTIAL，live fill 诊断已接，但样本薄
- 6 容量：FAIL，不能由 tiny rows 推 $3/$5
- 7 组合相关性：PARTIAL，按 target_date block bootstrap
- 8 基准/反事实：PARTIAL，同分母 complement 有，零模型/NO 反事实本轮未重跑

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_city_source_fit_v1.py`
- Report: `docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.md`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.json`
- Generated CSVs: `docs/analysis/2026-07/generated/low_price_yes_city_source_fit_v1/`
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    hot = base[base["hot_tail_boundary_v1"]].copy()
    enriched = attach_city_source_fit(hot)
    rows = simulate_price_tier(enriched)

    selector_summary = summarize_selectors(rows)
    source_group_summary = grouped_summary(rows, ["forecast_model", "source_fit_bucket"], label_prefix="source")
    city_source_summary = grouped_summary(rows, ["city", "forecast_model", "source_fit_bucket"], label_prefix="city_source")
    book_source_summary = grouped_summary(
        rows, ["source_fit_bucket", "book_state_v1"], label_prefix="source_book"
    )
    live_opp, live_city = load_live_execution_quality()

    rows.to_csv(OUT_DIR / "candidate_rows.csv", index=False)
    selector_summary.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    source_group_summary.to_csv(OUT_DIR / "source_group_summary.csv", index=False)
    city_source_summary.to_csv(OUT_DIR / "city_source_summary.csv", index=False)
    book_source_summary.to_csv(OUT_DIR / "book_source_summary.csv", index=False)
    live_opp.to_csv(OUT_DIR / "live_execution_opportunities.csv", index=False)
    live_city.to_csv(OUT_DIR / "live_execution_city_quality.csv", index=False)
    daily(rows).to_csv(OUT_DIR / "baseline_daily_pnl.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "denominator": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "cities": int(rows["city"].nunique()),
            "start": str(rows["target_date"].min()),
            "end": str(rows["target_date"].max()),
        },
        "baseline": selector_summary[
            selector_summary["period"].eq("full") & selector_summary["label"].eq("baseline_hot_only")
        ].iloc[0].to_dict(),
        "verdict": "shadow_candidate_no_live_change",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(
        build_report(
            rows=rows,
            selector_summary=selector_summary,
            source_group_summary=source_group_summary,
            city_source_summary=city_source_summary,
            live_city_quality=live_city,
        ),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
