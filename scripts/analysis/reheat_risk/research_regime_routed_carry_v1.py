#!/usr/bin/env python3
"""Regime-routed carry v1 -- historical candidate, superseded by the 7/22 audit.

Thesis
------
Buying the current bracket YES is a *carry* trade: you pay a high price for a
nearly-decided outcome.  It only works where the day is physically over.  Two
independent conditions define "over", and both must hold:

  1. Ceiling busted  -- the forecast's own maximum has already been exceeded by
     the running max, AFTER correcting that forecast for the city's own
     historical bias.  No modelled headroom left to climb.
  2. Path faded      -- the observed temperature path has turned over
     (canonical ``intraday_state``/``running_max_state`` fade or pullback).

The bias correction matters: raw ``forecast_max - running_max`` inherits each
city's systematic forecast error (measured here: Lucknow +5.1F, Dallas -3.3F),
which mislabels biased cities in both directions.

What is NEW vs the incumbent H1/H2 heads
----------------------------------------
H1/H2 share one binary heat-death gate (decline>=0.5, high age>=60m, forecast
peak passed) and then split on *entry price* (H1 ask>=0.95 carry, H2 ask<=0.93
dislocation).  This rule instead:

  * replaces the peak-clock condition with the bias-corrected forecast ceiling;
  * routes on canonical regime labels rather than raw thresholds;
  * does NOT split on entry price at all -- price is an input to the cost, not
    an eligibility gate.

Honest scope
------------
* ``maker`` numbers assume a passive fill at the bid with zero maker fee.  That
  is an UPPER BOUND: the one real paired-fill study (2026-07-20) found 3/6
  passive fills with the two misses being winners, i.e. maker was 0.493pp WORSE
  than taker on the planned denominator.  Treat maker ROI as the size of the
  prize, never as expected return.
* Book fields here are the decision-snapshot cross-section.  There is no
  intra-decision book time series and no fill events, so this can measure which
  book states precede bad outcomes but NOT fill probability.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from weather_feature_layer.regimes import add_regime_labels, unit_step  # noqa: E402
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import CITY_MODEL  # noqa: E402

SHARD_GLOB = str(ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_*/reheat_feature_rows.csv")
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_carry_v1"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-21-regime-routed-carry-preregistration-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-21-regime-routed-carry-preregistration-v1.md"

FEE_RATE = 0.05
SEED = 20260721
CONTAMINATED = {"2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"}
SPLIT = "2026-06-17"
FADE_INTRADAY = {"mature_fade", "pullback_uncertain"}
FADE_RUNNING = {"mature_fade", "pullback_from_high"}


def json_ready(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): json_ready(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_ready(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def load(*, legacy_semantics: bool = False, audit_mixed_forecast: bool = False) -> pd.DataFrame:
    """Load the current-YES research universe.

    The default path is expression-clean and model-lineage-clean: it selects
    the current-bracket YES row explicitly, uses the per-city ``CITY_MODEL``
    column, and estimates forecast bias with one equal-weighted value per prior
    city-day. ``legacy_semantics`` and ``audit_mixed_forecast`` exist only so
    the 2026-07-22 audit can reproduce the two earlier invalid stages and
    quantify their impact. New research must not use either audit mode.
    """
    if legacy_semantics and audit_mixed_forecast:
        raise ValueError("legacy_semantics and audit_mixed_forecast are mutually exclusive")
    frames = []
    want = [
        "city", "target_date", "decision_hour_local", "bracket", "current_bracket", "outcome", "orderbook_file",
        "settlement_status", "unit",
        "current_yes_ask", "current_yes_bid", "current_yes_ask_size", "current_bracket_held",
        "running_native", "decline_native", "minutes_since_running_max", "forecast_max_native",
        "forecast_source", "forecast_clock_source", "forecast_join_status", "forecast_values_hash",
        "gfs_forecast_max_native", "ecmwf_forecast_max_native",
        "gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_model_name", "ecmwf_forecast_model_name",
        "gfs_forecast_cache_status", "ecmwf_forecast_cache_status",
        "final_max_f", "final_max_c", "is_f", "temp_trend_1h_f", "temp_trend_3h_f",
        "relative_humidity_pct", "sky_cover_code", "dewpoint_depression_f", "wind_speed_kt",
        "quote_best_bid_size", "quote_best_ask_size", "quote_depth_bid_5c", "quote_depth_ask_5c",
        "decision_snapshot_ts_utc", "decision_last_obs_utc", "obs_count_to_decision",
        "forecast_peak_delta_hours_local",
    ]
    for p in sorted(glob.glob(SHARD_GLOB)):
        cols = set(pd.read_csv(p, nrows=0).columns)
        d = pd.read_csv(p, usecols=[c for c in want if c in cols], low_memory=False)
        d["target_date"] = d["target_date"].astype(str)
        current = d[d["bracket"] == d["current_bracket"]].copy()
        if not legacy_semantics:
            current = current[current["outcome"].astype(str).str.lower().eq("yes")]
        frames.append(current)
    r = pd.concat(frames, ignore_index=True).drop_duplicates(["city", "target_date", "decision_hour_local"])
    num = [c for c in want if c not in (
        "city", "target_date", "bracket", "current_bracket", "outcome", "orderbook_file",
        "settlement_status", "unit", "decision_snapshot_ts_utc", "decision_last_obs_utc",
        "forecast_source", "forecast_clock_source", "forecast_join_status", "forecast_values_hash",
        "gfs_forecast_model_name", "ecmwf_forecast_model_name",
        "gfs_forecast_cache_status", "ecmwf_forecast_cache_status",
    )]
    for c in num:
        if c in r:
            r[c] = pd.to_numeric(r[c], errors="coerce")
    r = r[~r["target_date"].isin(CONTAMINATED)].copy()
    r["forecast_source_original"] = r["forecast_source"]
    r["forecast_clock_source_original"] = r["forecast_clock_source"]
    r["forecast_assigned_model"] = r["city"].map(CITY_MODEL).fillna("gfs")
    if not legacy_semantics and not audit_mixed_forecast:
        assigned_ecmwf = r["forecast_assigned_model"].eq("ecmwf")
        r["forecast_max_native"] = np.where(
            assigned_ecmwf, r["ecmwf_forecast_max_native"], r["gfs_forecast_max_native"]
        )
        r["forecast_peak_delta_hours_local"] = np.where(
            assigned_ecmwf,
            r["ecmwf_forecast_peak_delta_hours_local"],
            r["gfs_forecast_peak_delta_hours_local"],
        )
        r["forecast_source"] = np.where(
            assigned_ecmwf, r["ecmwf_forecast_model_name"], r["gfs_forecast_model_name"]
        )
        r["forecast_clock_source"] = "assigned_city_model_dual_single_run_backfill"
    r["assigned_forecast_available"] = r["forecast_max_native"].notna()
    r["final_native"] = np.where(r["is_f"] == 1, r["final_max_f"], r["final_max_c"])
    r["forecast_gap_to_running_native"] = r["forecast_max_native"] - r["running_native"]

    # PIT per-city forecast bias: only strictly earlier dates.
    r = r.sort_values("target_date")
    r["fc_err"] = r["forecast_max_native"] - r["final_native"]
    dates = sorted(r["target_date"].unique())
    bias = {}
    if legacy_semantics:
        # Audit-only reproduction of the old behavior: days with more retained
        # states received more weight in the city forecast-error average.
        bias_source = r[["city", "target_date", "fc_err"]].copy()
    else:
        # Settlement is one outcome per city-day, so historical forecast bias
        # must give each prior city-day one vote rather than each state row.
        bias_source = r.groupby(["city", "target_date"], as_index=False)["fc_err"].mean()
    for d in dates:
        past = bias_source[bias_source["target_date"] < d]
        bias[d] = past.groupby("city")["fc_err"].mean() if len(past) else pd.Series(dtype=float)
    r["city_bias_pit"] = [bias[d].get(c, np.nan) for c, d in zip(r["city"], r["target_date"])]
    r["bias_known"] = r["city_bias_pit"].notna()
    r = add_regime_labels(r)
    r["_step"] = r.apply(unit_step, axis=1)
    bias_for_gap = r["city_bias_pit"].fillna(0.0) if legacy_semantics else r["city_bias_pit"]
    r["gap_debiased"] = r["forecast_gap_to_running_native"] - bias_for_gap

    # report clock: minutes since the last observation that fed this decision
    ts = pd.to_datetime(r["decision_snapshot_ts_utc"], errors="coerce", utc=True)
    ob = pd.to_datetime(r["decision_last_obs_utc"], errors="coerce", utc=True)
    r["obs_age_min"] = (ts - ob).dt.total_seconds() / 60.0

    u = r[
        r["decision_hour_local"].between(13, 17)
        & r["current_yes_ask"].between(0.01, 0.99)
        & r["current_yes_bid"].between(0.01, 0.99)
        & r["current_bracket_held"].notna()
        & r["settlement_status"].eq("settled")
    ].copy()
    u["label"] = u["current_bracket_held"].astype(int)
    u["taker_cost"] = u["current_yes_ask"] + np.round(FEE_RATE * u["current_yes_ask"] * (1 - u["current_yes_ask"]), 5)
    u["maker_cost"] = u["current_yes_bid"]
    u["front"] = u["target_date"] <= SPLIT

    # --- the rule ---
    u["ceiling_busted"] = u["gap_debiased"] < -u["_step"]
    if not legacy_semantics:
        u["ceiling_busted"] &= u["bias_known"]
    u["path_faded"] = u["intraday_state"].isin(FADE_INTRADAY) | u["running_max_state"].isin(FADE_RUNNING)
    u["route"] = u["ceiling_busted"] & u["path_faded"]
    # incumbent heads for comparison
    u["incumbent_gate"] = (u["decline_native"].ge(0.5) & u["minutes_since_running_max"].ge(60)
                           & u["forecast_peak_delta_hours_local"].ge(0.25))
    u["h1"] = u["incumbent_gate"] & u["current_yes_ask"].ge(0.95)
    u["h2"] = u["incumbent_gate"] & u["current_yes_ask"].le(0.93)
    return u.reset_index(drop=True)


def roi(s: pd.DataFrame, cost: str) -> float | None:
    tot = float(s[cost].sum())
    return float((s["label"] - s[cost]).sum() / tot) if len(s) and tot > 0 else None


def boot_ci(s: pd.DataFrame, cost: str, n: int = 5000) -> list[float | None]:
    if len(s) == 0:
        return [None, None]
    d = s.groupby("target_date").apply(lambda x: pd.Series({"p": (x["label"] - x[cost]).sum(), "c": x[cost].sum()}),
                                       include_groups=False)
    if len(d) < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    P, C = d["p"].to_numpy(float), d["c"].to_numpy(float)
    draws = []
    for _ in range(n):
        i = rng.integers(0, len(d), len(d))
        c = float(C[i].sum())
        if c > 0:
            draws.append(float(P[i].sum() / c))
    return [float(np.quantile(draws, .025)), float(np.quantile(draws, .975))] if draws else [None, None]


def stats(s: pd.DataFrame, tag: str) -> dict[str, Any]:
    cd = s.groupby(["city", "target_date"]).ngroups if len(s) else 0
    return {
        "selector": tag, "state_rows": int(len(s)), "city_days": int(cd),
        "dates": int(s["target_date"].nunique()) if len(s) else 0,
        "cities": int(s["city"].nunique()) if len(s) else 0,
        "win_rate": float(s["label"].mean()) if len(s) else None,
        "avg_ask": float(s["current_yes_ask"].mean()) if len(s) else None,
        "avg_half_spread": float(((s["current_yes_ask"] - s["current_yes_bid"]) / 2).mean()) if len(s) else None,
        "taker_roi": roi(s, "taker_cost"), "taker_ci95": boot_ci(s, "taker_cost"),
        "maker_roi_upper": roi(s, "maker_cost"), "maker_ci95": boot_ci(s, "maker_cost"),
    }


def threshold_sensitivity(u: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for mult in (0.0, -0.5, -1.0, -1.5, -2.0):
        busted = u["gap_debiased"] < mult * u["_step"]
        s = u[busted & u["path_faded"]]
        out.append({"busted_threshold_x_step": mult} | stats(s, f"busted<{mult}*step"))
    for fade_name, mask in (("intraday_only", u["intraday_state"].isin(FADE_INTRADAY)),
                            ("running_only", u["running_max_state"].isin(FADE_RUNNING)),
                            ("either(default)", u["path_faded"]),
                            ("both", u["intraday_state"].isin(FADE_INTRADAY) & u["running_max_state"].isin(FADE_RUNNING))):
        out.append({"fade_definition": fade_name} | stats(u[u["ceiling_busted"] & mask], f"fade={fade_name}"))
    # does de-biasing matter?
    raw_busted = u["forecast_gap_to_running_native"] < -u["_step"]
    out.append({"variant": "raw_gap_no_debias"} | stats(u[raw_busted & u["path_faded"]], "raw_gap"))
    return out


def leave_one_out(s: pd.DataFrame, cost: str) -> dict[str, Any]:
    if len(s) < 10:
        return {"note": "too few rows"}
    lodo = [roi(s[s["target_date"] != d], cost) for d in s["target_date"].unique()]
    loco = [roi(s[s["city"] != c], cost) for c in s["city"].unique()]
    lodo = [x for x in lodo if x is not None]
    loco = [x for x in loco if x is not None]
    by_city = s.groupby("city").apply(lambda x: (x["label"] - x[cost]).sum(), include_groups=False)
    return {
        "leave_one_date_out": [min(lodo), max(lodo)] if lodo else None,
        "leave_one_city_out": [min(loco), max(loco)] if loco else None,
        "top_city_abs_pnl_share": float(by_city.abs().max() / by_city.abs().sum()) if len(by_city) else None,
        "losers": int((s["label"] == 0).sum()), "loser_dates": int(s[s["label"] == 0]["target_date"].nunique()),
    }


def placebo(u: pd.DataFrame, reps: int = 200) -> dict[str, Any]:
    """Shuffle the ROUTE flag within date, keeping the same number selected per date."""
    rng = np.random.default_rng(SEED)
    real = roi(u[u["route"]], "taker_cost")
    draws = []
    for _ in range(reps):
        up = u.copy()
        up["fake"] = up.groupby("target_date")["route"].transform(lambda s: rng.permutation(s.values))
        v = roi(up[up["fake"]], "taker_cost")
        if v is not None:
            draws.append(v)
    return {"reps": reps, "real_taker_roi": real, "placebo_mean": float(np.mean(draws)),
            "placebo_p95": float(np.percentile(draws, 95)),
            "pass": bool(real is not None and real > np.percentile(draws, 95))}


def book_and_report_clock(u: pd.DataFrame) -> dict[str, Any]:
    s = u[u["route"]].copy()
    out: dict[str, Any] = {}
    if len(s) < 20:
        return {"note": "too few routed rows"}
    s["imbalance"] = (s["quote_depth_bid_5c"] - s["quote_depth_ask_5c"]) / (
        s["quote_depth_bid_5c"] + s["quote_depth_ask_5c"]).replace(0, np.nan)
    for col, edges, name in (
        ("obs_age_min", [-1, 15, 30, 60, 1e9], "报文年龄(分钟)"),
        ("quote_depth_ask_5c", [-1, 10, 50, 200, 1e9], "ask 5c 深度"),
        ("current_yes_ask_size", [-1, 10, 25, 100, 1e9], "best ask size"),
        ("imbalance", [-1.01, -0.33, 0.33, 1.01], "深度失衡(bid-ask)/(bid+ask)"),
    ):
        cat = pd.cut(s[col], edges)
        rows = []
        for k, g in s.groupby(cat, observed=True):
            if len(g) < 8:
                continue
            rows.append({"bucket": str(k), "n": int(len(g)), "win_rate": float(g["label"].mean()),
                         "taker_roi": roi(g, "taker_cost"), "maker_roi_upper": roi(g, "maker_cost"),
                         "avg_half_spread": float(((g["current_yes_ask"] - g["current_yes_bid"]) / 2).mean())})
        out[name] = rows
    out["obs_age_coverage"] = float(s["obs_age_min"].notna().mean())
    return out


def overlap_vs_incumbent(u: pd.DataFrame) -> dict[str, Any]:
    r = u["route"]
    return {
        "route_rows": int(r.sum()),
        "route_and_h1": int((r & u["h1"]).sum()),
        "route_and_h2": int((r & u["h2"]).sum()),
        "route_and_incumbent_gate": int((r & u["incumbent_gate"]).sum()),
        "route_not_incumbent": int((r & ~u["incumbent_gate"]).sum()),
        "incumbent_not_route": int((u["incumbent_gate"] & ~r).sum()),
        "route_ask_distribution": {k: float(v) for k, v in
                                   u.loc[r, "current_yes_ask"].quantile([.1, .25, .5, .75, .9]).items()},
    }


def pct(v: Any) -> str:
    return "NA" if v is None else f"{float(v) * 100:+.1f}%"


def num(v: Any, d: int = 3) -> str:
    return "NA" if v is None else f"{float(v):.{d}f}"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    u = load()
    routed = u[u["route"]]
    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy_id": "regime_routed_carry_v1",
        "family": "current_yes_heat_death_physical_v1",
        "status": "superseded_by_2026_07_22_current_yes_carry_mechanism_timing_audit_v1",
        "universe": {
            "rows": int(len(u)), "city_days": int(u.groupby(["city", "target_date"]).ngroups),
            "dates": int(u["target_date"].nunique()), "cities": int(u["city"].nunique()),
            "date_min": str(u["target_date"].min()), "date_max": str(u["target_date"].max()),
            "contaminated_dates_excluded": sorted(CONTAMINATED),
            "base_hold_rate": float(u["label"].mean()),
            "pit_bias_known_share": float(u["bias_known"].mean()),
        },
        "rule": {
            "ceiling_busted": "(forecast_max - running_max) - city_bias_PIT < -unit_step",
            "path_faded": f"intraday_state in {sorted(FADE_INTRADAY)} OR running_max_state in {sorted(FADE_RUNNING)}",
            "route": "ceiling_busted AND path_faded",
            "expression": "buy current bracket YES",
            "no_price_gate": "entry price enters only through cost, never as eligibility",
        },
        "headline": {
            "all": stats(u, "no_routing"),
            "routed": stats(routed, "regime_routed_carry"),
            "routed_front": stats(routed[routed["front"]], "routed_front"),
            "routed_back": stats(routed[~routed["front"]], "routed_back"),
        },
        "component_selectors": {
            "ceiling_busted_only": stats(u[u["ceiling_busted"]], "ceiling_busted_only"),
            "path_faded_only": stats(u[u["path_faded"]], "path_faded_only"),
        },
        "incumbent": {
            "incumbent_gate": stats(u[u["incumbent_gate"]], "incumbent_gate"),
            "h1_ask_ge_0p95": stats(u[u["h1"]], "H1"),
            "h2_ask_le_0p93": stats(u[u["h2"]], "H2"),
        },
        "overlap_vs_incumbent": overlap_vs_incumbent(u),
        "threshold_sensitivity": threshold_sensitivity(u),
        "leave_one_out_taker": leave_one_out(routed, "taker_cost"),
        "placebo": placebo(u),
        "book_and_report_clock": book_and_report_clock(u),
        "data_availability": {
            "book_fields_in_factory": ["quote_best_bid/ask", "quote_best_bid_size/ask_size",
                                       "quote_depth_bid_5c/ask_5c", "current_yes_ask_size", "current_yes_spread"],
            "book_coverage_dates": 49,
            "book_nonnull_share_approx": 0.946,
            "report_clock": "decision_snapshot_ts_utc - decision_last_obs_utc = obs age",
            "missing_for_microstructure": [
                "intra-decision book time series (how the book evolves while an order rests)",
                "queue-ahead at own price level and per-reprice size journal",
                "real exchange fill event time and 1/5/15m + next-report markout",
            ],
            "schema_to_extend_not_replace": "tmax_v2_ladder_rung_quotes already carries yes_direct_bid/ask, "
                                            "*_size, depth_bid/ask_5c/10c and book_status; extend that table's "
                                            "cadence and add queue/fill-event columns rather than inventing a new format",
        },
    }
    routed.to_csv(OUT_DIR / "routed_rows.csv", index=False)
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_md(payload)
    print(json.dumps(json_ready({"routed": payload["headline"]["routed"],
                                 "front": payload["headline"]["routed_front"],
                                 "back": payload["headline"]["routed_back"],
                                 "placebo": payload["placebo"],
                                 "overlap": payload["overlap_vs_incumbent"]}), ensure_ascii=False))
    return 0


def write_md(p: dict[str, Any]) -> None:
    U = p["universe"]; H = p["headline"]; I = p["incumbent"]; O = p["overlap_vs_incumbent"]
    R = H["routed"]; F = H["routed_front"]; B = H["routed_back"]; pl = p["placebo"]; lo = p["leave_one_out_taker"]
    def row(s: dict[str, Any]) -> str:
        return (f"| {s['selector']} | {s['state_rows']} | {s['city_days']} | {s['dates']} | {pct(s['win_rate'])} | "
                f"{num(s['avg_ask'])} | {pct(s['taker_roi'])} [{pct(s['taker_ci95'][0])},{pct(s['taker_ci95'][1])}] | "
                f"{pct(s['maker_roi_upper'])} |")
    L = [
        "# Regime-Routed Carry v1 — 历史候选（已被 7/22 审计取代）",
        "",
        "Status: `superseded_for_decision_use`（保留机制研究；不得据此建新算法或改 live）",
        "Date: 2026-07-21",
        f"Family: `{p['family']}` · Strategy id: `{p['strategy_id']}`",
        "",
        "权威后续：[2026-07-22 carry 机制 / timing / execution 审计](2026-07-22-current-yes-carry-mechanism-timing-audit-v1.md)。"
        "后续审计修正了 expression 行选择、PIT bias 权重、first-city-day 分母、official taker fee 和 proper-score baseline；"
        "本文数字只作历史机制线索。",
        "",
        "## 1. 物理论点",
        "",
        "买当前档 YES 是一笔 **carry**：为一个几乎已定的结果付高价。它只在「这一天物理上已经结束」时成立。",
        "两个**独立**条件定义「结束」，必须同时满足：",
        "",
        "1. **天花板已破 `ceiling_busted`** — running max 已超过预报最高温，**且该预报先按该城历史偏差校正**。",
        "   校正是必需的：原始 gap 继承各城系统性预报误差（实测 Lucknow +5.1F、Dallas −3.3F），会把偏差城市双向误标。",
        "2. **路径已消退 `path_faded`** — 观测路径已经掉头（canonical `intraday_state` / `running_max_state` 的 fade/pullback）。",
        "",
        "```text",
        f"ceiling_busted : {p['rule']['ceiling_busted']}",
        f"path_faded     : {p['rule']['path_faded']}",
        f"route          : {p['rule']['route']}",
        f"expression     : {p['rule']['expression']}",
        f"price          : {p['rule']['no_price_gate']}",
        "```",
        "",
        "## 2. 分母与数据",
        "",
        f"- 全量午后（13-17 local）双边报价、已结算：**{U['rows']} 行 / {U['city_days']} city-day / {U['dates']} 天 / {U['cities']} 城**，{U['date_min']}..{U['date_max']}。",
        f"- 已剔除静默 ECMWF→GFS fallback 日期：{', '.join(U['contaminated_dates_excluded'])}。",
        f"- 分母 base 守住率 {U['base_hold_rate']:.1%}；PIT 城市偏差可用占比 {U['pit_bias_known_share']:.1%}。",
        "",
        "## 3. 主结果",
        "",
        "| selector | state行 | city-day | 天 | 胜率 | 均ask | **taker ROI [95%CI]** | maker上界 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        row(H["all"]), row(p["component_selectors"]["ceiling_busted_only"]),
        row(p["component_selectors"]["path_faded_only"]), row(R), row(F), row(B),
        "",
        f"**历史 state-row 描述：路由 taker ROI {pct(R['taker_roi'])}，CI "
        f"[{pct(R['taker_ci95'][0])}, {pct(R['taker_ci95'][1])}]，胜率 {pct(R['win_rate'])}。** "
        "这个分母会让同一 city-day 的多个状态重复计权，只保留为影响对照；权威结论必须看 7/22 审计的 first-city-day 分母。",
        "两个组件及其交集均未通过独立 frozen-forward 验证，不能据此宣称 AND 后有效。",
        "",
        "## 4. 与现有 H1 / H2 的关系",
        "",
        "| selector | state行 | city-day | 天 | 胜率 | 均ask | taker ROI [95%CI] | maker上界 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        row(I["incumbent_gate"]), row(I["h1_ask_ge_0p95"]), row(I["h2_ask_le_0p93"]), row(R),
        "",
        "**区别（三点，都是结构性的）：**",
        "",
        "1. **换掉了 peak clock**：现有门用「预报峰值已过 ≥0.25h」，本规则用**去偏后的预报天花板是否被打穿**。"
        "   peak clock 的预报可靠性实测很差（预报峰值时刻 vs 实际 corr 仅 0.458），而预报最高温 corr 0.893。",
        "2. **不按入场价拆头**：H1/H2 用 ask≥0.95 / ≤0.93 把同一信号劈成两个策略。本规则**完全不用价格做资格门**，"
        "   价格只进成本。价格带实测跨期持续性为 0（pearson 0.010 / spearman 0.000），按价格拆头没有统计依据。",
        "3. **消费 canonical regime 标签**而不是自造阈值，新自由度更少。",
        "",
        "**重叠情况：**",
        "",
        f"- 路由命中 {O['route_rows']} 行；其中同时被现有 gate 命中 {O['route_and_incumbent_gate']} 行，"
        f"**现有 gate 抓不到的有 {O['route_not_incumbent']} 行**；现有 gate 命中但本规则不要的有 {O['incumbent_not_route']} 行。",
        f"- 路由行里同时满足 H1(ask≥0.95) 的 {O['route_and_h1']} 行、H2(ask≤0.93) 的 {O['route_and_h2']} 行。",
        f"- 路由行的 ask 分位：{ {k: round(v,3) for k,v in O['route_ask_distribution'].items()} }。",
        "",
        "**当前取舍**：本规则只保留为 frozen-forward 的候选机制分量；现有证据不支持它取代 H1/H2，"
        "也不支持把两套条件强行 AND 合并。H1/H2 的血缘、config、execution 资产继续保留。",
        "",
        "## 5. 稳健性",
        "",
        f"- **跨期**：前段 taker {pct(F['taker_roi'])}、后段 taker {pct(B['taker_roi'])}；"
        f"maker 上界 前 {pct(F['maker_roi_upper'])} / 后 {pct(B['maker_roi_upper'])}。",
        f"- **leave-one-date-out** taker ROI 区间 {[pct(x) for x in (lo.get('leave_one_date_out') or [None,None])]}；"
        f"**leave-one-city-out** {[pct(x) for x in (lo.get('leave_one_city_out') or [None,None])]}。",
        f"- 单城最大 |PnL| 占比 {num(lo.get('top_city_abs_pnl_share'))}；{lo.get('losers')} 个输家散在 {lo.get('loser_dates')} 天。",
        f"- **安慰剂**（每日内随机打乱路由标记、保持每日选中数量不变）：真实 taker {pct(pl['real_taker_roi'])} vs "
        f"随机均值 {pct(pl['placebo_mean'])}、p95 {pct(pl['placebo_p95'])} → **{'PASS' if pl['pass'] else 'FAIL'}**。",
        "",
        "### 阈值敏感性（不是调参，是看规则稳不稳）",
        "",
        "| 变体 | state行 | 胜率 | taker ROI | maker上界 |",
        "|---|---:|---:|---:|---:|",
    ]
    for t in p["threshold_sensitivity"]:
        tag = t.get("busted_threshold_x_step", t.get("fade_definition", t.get("variant", "")))
        L.append(f"| {tag} | {t['state_rows']} | {pct(t['win_rate'])} | {pct(t['taker_roi'])} | {pct(t['maker_roi_upper'])} |")
    bc = p["book_and_report_clock"]
    L += [
        "",
        "## 6. 盘口与报文时钟（用**现有**数据，无需新采集）",
        "",
        f"factory 9 个分片**全部带盘口字段**（bid/ask、两侧 size、5c 深度），覆盖 49 天、非空约 94.6%；"
        f"`decision_snapshot_ts_utc − decision_last_obs_utc` 即报文年龄（本路由样本覆盖 {num(bc.get('obs_age_coverage'))}）。",
        "",
    ]
    for name, rows in bc.items():
        if not isinstance(rows, list) or not rows:
            continue
        L += [f"**{name}**", "", "| 桶 | n | 胜率 | taker ROI | maker上界 | 均半价差 |", "|---|---:|---:|---:|---:|---:|"]
        for x in rows:
            L.append(f"| {x['bucket']} | {x['n']} | {pct(x['win_rate'])} | {pct(x['taker_roi'])} | {pct(x['maker_roi_upper'])} | {num(x['avg_half_spread'])} |")
        L.append("")
    da = p["data_availability"]
    L += [
        "## 7. 微观结构研究：能做什么、缺什么、格式怎么统一",
        "",
        "**现在就能做**（上面第 6 节已经是）：静态截面的深度/价差/失衡/报文年龄与结果的关系。",
        "",
        "**确实缺、必须 forward 采的：**",
        "",
    ] + [f"- {x}" for x in da["missing_for_microstructure"]] + [
        "",
        f"**格式统一原则**：{da['schema_to_extend_not_replace']}",
        "",
        "即：**扩展 `tmax_v2_ladder_*` 的采集频率并补 queue/fill-event 列，不要新建平行表**；"
        "报文时钟直接复用 factory 已有的 `decision_last_obs_utc`，不引入第二套时间定义。",
        "",
        "## 8. 冻结判据（进入 forward 前）",
        "",
        "```text",
        "primary expression = current bracket YES（不设价格资格门）",
        "primary metric     = fee-adjusted taker ROI（maker 只作上界参考，永不作为晋升依据）",
        "样本门   = >=30 个独立 target_date 的 forward，且 >=8 城",
        "显著性门 = taker ROI 的 target_date block bootstrap 95% CI 下界 > 0",
        "机制门   = ceiling_busted 与 path_faded 单独都不得达标（证明是交集机制而非其一）",
        "失效门   = forward 窗口内 taker ROI 95% CI 上界 < 0 则降级 rejected_for_expression",
        "执行     = maker 仅在补齐 queue/markout 仪表后才允许评估；在此之前一律按 taker 记账",
        "```",
        "",
        "## 8b. 与既有冻结文档的关系（重要，勿在读旧文档时被误导）",
        "",
        "[2026-07-15 H1/H2 晋升判据预注册](2026-07-15-heat-death-live-promotion-preregistration-v1.md) 是**冻结文档**，"
        "本文**不修改它**。但本文的两项测量与它的前提冲突，读那份文档时必须同时知道：",
        "",
        "| 那份冻结文档的前提 | 本文的测量 |",
        "|---|---|",
        "| 按入场价把同一信号拆成 H1(ask≥0.95) / H2(ask≤0.93) 两条独立线 | **价格带跨期持续性 pearson 0.010 / spearman 0.000（≈零）**；按价格拆头缺乏统计依据 |",
        "| 共同硬门含「forecast peak 已过 ≥0.25h」 | **峰值时刻预报可靠性远低于最高温预报**（城内归一化后 corr 0.458 vs 0.893）；本文改用去偏后的预报天花板 |",
        "",
        "若要按本文结论调整 H1/H2 判据，按那份文档自身的规定应**新开 v2 判据文档**，不得原地改。",
        "",
        "同时作废的还有本家族本轮的两份中间产物，两者都已在文首标注 SUPERSEDED：",
        "[overshoot-hazard-calibration v1](2026-07-20-current-yes-overshoot-hazard-calibration-v1.md)、"
        "[overshoot-edge strategy v1](2026-07-21-heat-death-overshoot-edge-strategy-v1.md)。",
        "",
        "## 9. 诚实边界",
        "",
        "- `maker` 全部是**在 bid 全成交的上界**。唯一真实成交证据（2026-07-20，6 对同信号）是 3/6 成交、"
        "**漏掉的两单是赢家**、净比 taker 差 0.493pp。**maker 数字是奖品尺寸，不是收益预期。**",
        "- 本轮路由是在看过分期结果之后收敛的，因此**它是预注册候选，不是已验证结论**；判据冻结后只能 forward 复核。",
        "- 后段样本仍薄，且我在收敛过程中比较了约 18 个 regime 格子，**未做多重检验校正**。",
        "- 历史层仍缺 solar geometry / 降雨 / 风向；`running_max_state_v2` 因缺 strict-high 时钟无法物化。",
        "",
        "## 10. 产物",
        "",
        f"- Script: `scripts/analysis/reheat_risk/{Path(__file__).name}`",
        "- 路由明细: `docs/analysis/2026-07/generated/regime_routed_carry_v1/routed_rows.csv`",
        "- 前序: [overshoot-edge v2（跨期证伪）](2026-07-21-heat-death-overshoot-edge-strategy-v2.md)",
    ]
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
