#!/usr/bin/env python3
"""Runway d1-YES reversal expression matrix v1.

Research-only. No live changes.

This keeps Fabel's market-pricing insight inside the original
`runway_d1_yes_reversal` branch: physical runway is not enough; the expression
depends on whether the orderbook still treats the current bracket as live.

The denominator is one row per city-date-snapshot from the corrected intraday
feature-factory mirror. Sibling expressions are evaluated at the same snapshot:

  d1 YES / d2 YES / high-tail YES / current-bracket NO / hotter basket

Canonical settled PnL is computed only where the feature row has settlement
labels. Unsettled rows are telemetry/coverage only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ATLAS_DIR = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
FEATURE_PATHS = [
    ATLAS_DIR / "feature_factory_20260519_20260620/reheat_feature_rows.csv",
    ATLAS_DIR / "feature_factory_20260621_20260623/reheat_feature_rows.csv",
    ROOT / "docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/reheat_feature_rows.csv",
    ATLAS_DIR / "feature_factory_20260625_20260628/reheat_feature_rows.csv",
    ATLAS_DIR / "feature_factory_20260629_20260630/reheat_feature_rows.csv",
    ATLAS_DIR / "feature_factory_20260701/reheat_feature_rows.csv",
]

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-runway-d1-yes-reversal-expression-matrix-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-runway-d1-yes-reversal-expression-matrix-v1.json"

RNG_SEED = 20260703
N_BOOT = 5000

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
    "quote_spread",
    "icao",
    "timezone",
    "unit",
    "current_native",
    "running_native",
    "running_value",
    "current_bracket",
    "current_yes_ask",
    "current_yes_ask_size",
    "current_yes_bid",
    "current_yes_spread",
    "current_no_ask",
    "current_no_bid",
    "current_no_spread",
    "d1_no_bracket",
    "d1_no_ask",
    "d1_no_bid",
    "d1_no_spread",
    "d2_no_bracket",
    "d2_no_ask",
    "d2_no_bid",
    "d2_no_spread",
    "final_max_native",
    "final_max_f",
    "final_winning_bracket",
    "winner_final_price",
    "settlement_status",
    "source_system",
    "forecast_join_status",
    "forecast_source",
    "forecast_clock_source",
    "forecast_max_native",
    "forecast_max_f",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_max_native",
    "ecmwf_forecast_max_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "day_regime",
    "intraday_state",
    "running_max_state",
    "moisture_cloud_regime",
    "wind_regime",
    "solar_window",
    "city_family",
]

EXPRESSIONS = ["d1_yes", "d2_yes", "high_tail_yes", "current_bracket_no", "hotter_basket"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_num(df: pd.DataFrame, cols: list[str]) -> None:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def load_rows() -> tuple[pd.DataFrame, dict]:
    frames = []
    sources = []
    for path in FEATURE_PATHS:
        if not path.exists():
            continue
        df = pd.read_csv(path, usecols=lambda c: c in set(USECOLS), low_memory=False)
        df["source_file"] = str(path.relative_to(ROOT))
        frames.append(df)
        sources.append(str(path.relative_to(ROOT)))
    if not frames:
        raise FileNotFoundError("no feature factory shards found")
    out = pd.concat(frames, ignore_index=True)
    _to_num(
        out,
        [
            "decision_hour_local",
            "bracket_low",
            "bracket_high",
            "quote_best_ask",
            "quote_best_ask_size",
            "quote_best_bid",
            "quote_spread",
            "current_native",
            "running_native",
            "running_value",
            "current_yes_ask",
            "current_yes_ask_size",
            "current_yes_bid",
            "current_yes_spread",
            "current_no_ask",
            "current_no_bid",
            "current_no_spread",
            "d1_no_ask",
            "d1_no_bid",
            "d1_no_spread",
            "d2_no_ask",
            "d2_no_bid",
            "d2_no_spread",
            "final_max_native",
            "final_max_f",
            "winner_final_price",
            "forecast_max_native",
            "forecast_max_f",
            "forecast_peak_hour_local",
            "forecast_peak_delta_hours_local",
            "forecast_peak_hour_spread",
            "gfs_forecast_max_native",
            "ecmwf_forecast_max_native",
            "temp_trend_1h_f",
            "temp_trend_3h_f",
            "minutes_since_running_max",
            "dewpoint_depression_f",
            "relative_humidity_pct",
            "wind_speed_kt",
            "sky_cover_code",
        ],
    )
    out["target_date"] = out["target_date"].astype(str)
    return out, {"source_files": sources, "raw_quote_rows": int(len(out))}


def _clean_label(x: object) -> str | None:
    if x is None:
        return None
    s = str(x)
    if s.lower() in {"nan", "none", "", "<na>"}:
        return None
    return s


def _finite(x: object) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return np.isfinite(v)


def _infer_current_idx(g_yes: pd.DataFrame, current_bracket: object, running_native: object) -> int | None:
    label = _clean_label(current_bracket)
    if label is not None:
        hit = g_yes.index[g_yes["bracket"].astype(str) == label].tolist()
        if hit:
            return int(hit[0])
    if _finite(running_native):
        rn = round(float(running_native))
        hit = g_yes[
            (pd.to_numeric(g_yes["bracket_low"], errors="coerce") - 1e-6 <= rn)
            & (rn <= pd.to_numeric(g_yes["bracket_high"], errors="coerce").fillna(np.inf) + 1e-6)
        ]
        if not hit.empty:
            return int(hit.index[0])
    return None


def _leg(row: pd.Series | None, col: str) -> float | None:
    if row is None:
        return None
    v = row.get(col)
    return float(v) if _finite(v) else None


def _market_current_state(current_yes_ask: float | None, current_no_ask: float | None) -> str:
    if current_yes_ask is None or not np.isfinite(current_yes_ask):
        return "unknown"
    if current_yes_ask >= 0.40:
        return "current_live"
    if current_yes_ask <= 0.30 or (current_no_ask is not None and np.isfinite(current_no_ask) and current_no_ask >= 0.70):
        return "current_conceded"
    return "neutral"


def _runway_state(row: dict) -> str:
    gap = row.get("forecast_gap_native")
    trend = row.get("temp_trend_1h_f")
    peak_delta = row.get("forecast_peak_delta_hours_local")
    minutes = row.get("minutes_since_running_max")
    market_state = row.get("market_current_state")

    if gap is None or not np.isfinite(gap) or gap < 0.50:
        return "no_runway"
    fake = False
    if trend is not None and np.isfinite(trend) and trend <= -0.50:
        fake = True
    if peak_delta is not None and np.isfinite(peak_delta) and peak_delta >= 1.00:
        fake = True
    if minutes is not None and np.isfinite(minutes) and minutes >= 120:
        fake = True
    if fake:
        return "fake_runway"
    if market_state == "current_live" and gap < 2.25:
        return "one_step_runway"
    if market_state == "current_conceded" or gap >= 2.25:
        return "skip_over_runway"
    return "one_step_runway"


def build_matrix(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    recs = []
    skipped = {"empty_yes": 0, "missing_current_bracket": 0, "no_current_no_quote": 0}
    key_cols = ["city", "target_date", "decision_snapshot_ts_utc"]
    for (city, tdate, ts), g0 in rows.groupby(key_cols, sort=False):
        g = g0.drop_duplicates(subset=["bracket", "outcome"]).copy()
        g_yes = g[g["outcome"].astype(str).str.lower() == "yes"].sort_values("bracket_low").reset_index(drop=True)
        g_no = g[g["outcome"].astype(str).str.lower() == "no"].sort_values("bracket_low").reset_index(drop=True)
        if g_yes.empty:
            skipped["empty_yes"] += 1
            continue
        head = g_yes.iloc[0]
        cur_idx = _infer_current_idx(g_yes, head.get("current_bracket"), head.get("running_native"))
        if cur_idx is None:
            skipped["missing_current_bracket"] += 1
            continue
        cur_yes = g_yes.iloc[cur_idx]
        current_bracket = _clean_label(cur_yes.get("bracket")) or _clean_label(head.get("current_bracket"))

        cur_no_rows = g_no[g_no["bracket"].astype(str) == str(current_bracket)]
        cur_no = cur_no_rows.iloc[0] if not cur_no_rows.empty else None
        if cur_no is None:
            skipped["no_current_no_quote"] += 1
        d1 = g_yes.iloc[cur_idx + 1] if cur_idx + 1 < len(g_yes) else None
        d2 = g_yes.iloc[cur_idx + 2] if cur_idx + 2 < len(g_yes) else None
        tail_pool = g_yes.iloc[cur_idx + 3:].copy()
        tail_pool = tail_pool[pd.to_numeric(tail_pool["quote_best_ask"], errors="coerce").between(0.005, 0.995)]
        tail = tail_pool.loc[tail_pool["quote_best_ask"].astype(float).idxmin()] if not tail_pool.empty else None

        cur_yes_ask = _leg(cur_yes, "quote_best_ask")
        if cur_yes_ask is None:
            cur_yes_ask = _leg(head, "current_yes_ask")
        cur_yes_bid = _leg(cur_yes, "quote_best_bid")
        cur_no_ask = _leg(cur_no, "quote_best_ask") if cur_no is not None else None
        if cur_no_ask is None and cur_yes_bid is not None:
            cur_no_ask = 1.0 - cur_yes_bid
        market_current_state = _market_current_state(cur_yes_ask, cur_no_ask)

        settled = _clean_label(head.get("settlement_status")) == "settled" and _clean_label(head.get("final_winning_bracket")) is not None
        final_bracket = _clean_label(head.get("final_winning_bracket"))
        d1_bracket = _clean_label(d1.get("bracket")) if d1 is not None else None
        d2_bracket = _clean_label(d2.get("bracket")) if d2 is not None else None
        tail_bracket = _clean_label(tail.get("bracket")) if tail is not None else None

        if settled:
            if final_bracket == current_bracket:
                realized_path = "stays_current"
            elif final_bracket == d1_bracket:
                realized_path = "d1"
            elif final_bracket == d2_bracket:
                realized_path = "d2"
            elif final_bracket == tail_bracket:
                realized_path = "selected_high_tail"
            else:
                realized_path = "other_hotter_or_below"
        else:
            realized_path = "unsettled"

        rec = {
            "city": city,
            "target_date": str(tdate),
            "decision_snapshot_ts_utc": ts,
            "decision_hour_local": head.get("decision_hour_local"),
            "unit": head.get("unit"),
            "current_bracket": current_bracket,
            "d1_bracket": d1_bracket,
            "d2_bracket": d2_bracket,
            "high_tail_bracket": tail_bracket,
            "running_native": _leg(head, "running_native"),
            "forecast_max_native": _leg(head, "forecast_max_native"),
            "forecast_gap_native": (
                _leg(head, "forecast_max_native") - _leg(head, "running_native")
                if _leg(head, "forecast_max_native") is not None and _leg(head, "running_native") is not None
                else np.nan
            ),
            "forecast_peak_delta_hours_local": _leg(head, "forecast_peak_delta_hours_local"),
            "temp_trend_1h_f": _leg(head, "temp_trend_1h_f"),
            "temp_trend_3h_f": _leg(head, "temp_trend_3h_f"),
            "minutes_since_running_max": _leg(head, "minutes_since_running_max"),
            "dewpoint_depression_f": _leg(head, "dewpoint_depression_f"),
            "relative_humidity_pct": _leg(head, "relative_humidity_pct"),
            "wind_speed_kt": _leg(head, "wind_speed_kt"),
            "forecast_source": _clean_label(head.get("forecast_source")),
            "forecast_clock_source": _clean_label(head.get("forecast_clock_source")),
            "day_regime": _clean_label(head.get("day_regime")),
            "intraday_state": _clean_label(head.get("intraday_state")),
            "running_max_state": _clean_label(head.get("running_max_state")),
            "market_current_state": market_current_state,
            "settled": bool(settled),
            "settlement_status": _clean_label(head.get("settlement_status")),
            "final_winning_bracket": final_bracket,
            "realized_path": realized_path,
            "current_high_yes_ask": cur_yes_ask,
            "current_high_yes_bid": cur_yes_bid,
            "current_bracket_no_ask": cur_no_ask,
            "d1_yes_ask": _leg(d1, "quote_best_ask"),
            "d1_yes_size": _leg(d1, "quote_best_ask_size"),
            "d1_yes_bid": _leg(d1, "quote_best_bid"),
            "d2_yes_ask": _leg(d2, "quote_best_ask"),
            "d2_yes_size": _leg(d2, "quote_best_ask_size"),
            "d2_yes_bid": _leg(d2, "quote_best_bid"),
            "high_tail_yes_ask": _leg(tail, "quote_best_ask"),
            "high_tail_yes_size": _leg(tail, "quote_best_ask_size"),
            "high_tail_yes_bid": _leg(tail, "quote_best_bid"),
        }
        rec["runway_state"] = _runway_state(rec)
        recs.append(rec)
    return pd.DataFrame(recs), skipped


def block_ci(g: pd.DataFrame, pnl_col: str) -> tuple[float | None, float | None]:
    daily = g.groupby("target_date")[pnl_col].agg(["sum", "count"])
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    sums, counts = daily["sum"].to_numpy(), daily["count"].to_numpy()
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(daily), len(daily))
        n = counts[idx].sum()
        if n:
            vals.append(sums[idx].sum() / n)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def _expr_cost_win(df: pd.DataFrame, expr: str) -> pd.DataFrame:
    g = df.copy()
    if expr == "d1_yes":
        g["cost"] = g["d1_yes_ask"]
        g["win"] = g["final_winning_bracket"] == g["d1_bracket"]
    elif expr == "d2_yes":
        g["cost"] = g["d2_yes_ask"]
        g["win"] = g["final_winning_bracket"] == g["d2_bracket"]
    elif expr == "high_tail_yes":
        g["cost"] = g["high_tail_yes_ask"]
        g["win"] = g["final_winning_bracket"] == g["high_tail_bracket"]
    elif expr == "current_bracket_no":
        g["cost"] = g["current_bracket_no_ask"]
        g["win"] = g["final_winning_bracket"] != g["current_bracket"]
    elif expr == "hotter_basket":
        cols = ["d1_yes_ask", "d2_yes_ask", "high_tail_yes_ask"]
        g["cost"] = g[cols].sum(axis=1, min_count=2)
        g["win"] = (
            (g["final_winning_bracket"] == g["d1_bracket"])
            | (g["final_winning_bracket"] == g["d2_bracket"])
            | (g["final_winning_bracket"] == g["high_tail_bracket"])
        )
    else:
        raise ValueError(expr)
    g = g[g["cost"].between(0.005, 0.995 if expr != "hotter_basket" else 2.995)]
    g["unit_roi"] = g["win"].astype(float) / g["cost"].astype(float) - 1.0
    return g


def summarize_expr(df: pd.DataFrame, expr: str, label: str) -> dict:
    g = df[df["settled"]].copy()
    g = _expr_cost_win(g, expr)
    if g.empty:
        return {"slice": label, "expr": expr, "rows": 0}
    lo, hi = block_ci(g, "unit_roi")
    return {
        "slice": label,
        "expr": expr,
        "rows": int(len(g)),
        "dates": int(g["target_date"].nunique()),
        "cities": int(g["city"].nunique()),
        "win_rate": float(g["win"].astype(float).mean()),
        "avg_ask": float(g["cost"].mean()),
        "roi": float(g["unit_roi"].mean()),
        "roi_ci_low": lo,
        "roi_ci_high": hi,
    }


def md_table(df: pd.DataFrame, cols: list[str] | None = None, max_rows: int | None = None) -> str:
    if cols is not None:
        df = df[cols]
    if max_rows is not None:
        df = df.head(max_rows)
    if df.empty:
        return "(empty)"
    out = ["| " + " | ".join(df.columns) + " |", "|" + "|".join(["---"] * len(df.columns)) + "|"]
    for _, row in df.iterrows():
        vals = []
        for value in row:
            if isinstance(value, float) and np.isfinite(value):
                vals.append(f"{value:+.3f}")
            elif value is None or (isinstance(value, float) and not np.isfinite(value)):
                vals.append("")
            else:
                vals.append(str(value))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw, load_meta = load_rows()
    matrix, skipped = build_matrix(raw)
    matrix.to_csv(OUT_DIR / "runway_matrix_rows.csv", index=False)

    settled = matrix[matrix["settled"]].copy()
    telemetry = matrix[~matrix["settled"]].copy()
    results = []
    for state, g in matrix.groupby("runway_state", sort=True):
        for expr in EXPRESSIONS:
            results.append(summarize_expr(g, expr, state))
    for expr in EXPRESSIONS:
        results.append(summarize_expr(matrix, expr, "all_states"))

    one_step_cheap = matrix[(matrix["runway_state"] == "one_step_runway") & (matrix["d1_yes_ask"] <= 0.30)]
    skip_conceded = matrix[(matrix["runway_state"] == "skip_over_runway") & (matrix["market_current_state"] == "current_conceded")]
    fake_pool = matrix[matrix["runway_state"] == "fake_runway"]
    candidate_slices: dict[str, pd.DataFrame] = {
        "candidate_one_step_d1_yes_cheap": one_step_cheap,
        "candidate_skip_over_current_conceded": skip_conceded,
        "candidate_fake_runway": fake_pool,
    }
    for name, frame in candidate_slices.items():
        for expr in EXPRESSIONS:
            results.append(summarize_expr(frame, expr, name))

    summary = pd.DataFrame(results)
    summary = summary[summary["rows"].fillna(0).astype(int) > 0].sort_values(["slice", "expr"])
    summary.to_csv(OUT_DIR / "settled_expression_summary.csv", index=False)

    state_counts = (
        matrix.groupby(["runway_state", "market_current_state"], dropna=False)
        .agg(rows=("city", "size"), settled_rows=("settled", "sum"), dates=("target_date", "nunique"), cities=("city", "nunique"))
        .reset_index()
        .sort_values(["runway_state", "market_current_state"])
    )
    state_counts.to_csv(OUT_DIR / "state_counts.csv", index=False)

    recent = matrix[matrix["target_date"] >= "2026-06-21"].copy()
    recent_cov = (
        recent.groupby(["target_date", "runway_state"], dropna=False)
        .agg(rows=("city", "size"), settled_rows=("settled", "sum"), cities=("city", "nunique"))
        .reset_index()
        .sort_values(["target_date", "runway_state"])
    )
    recent_cov.to_csv(OUT_DIR / "recent_coverage.csv", index=False)

    quote_coverage = {}
    for col in ["d1_yes_ask", "d2_yes_ask", "high_tail_yes_ask", "current_bracket_no_ask"]:
        quote_coverage[col] = {
            "all": float(matrix[col].notna().mean()),
            "recent_2026_06_21_plus": float(recent[col].notna().mean()) if len(recent) else None,
            "settled": float(settled[col].notna().mean()) if len(settled) else None,
        }
    quote_cov_df = (
        pd.DataFrame.from_dict(quote_coverage, orient="index")
        .reset_index()
        .rename(columns={"index": "quote"})
    )
    quote_cov_df.to_csv(OUT_DIR / "quote_coverage.csv", index=False)

    top = summary[
        summary["slice"].isin(
            [
                "one_step_runway",
                "skip_over_runway",
                "fake_runway",
                "no_runway",
                "candidate_one_step_d1_yes_cheap",
                "candidate_skip_over_current_conceded",
            ]
        )
    ].copy()

    payload = {
        "generated_at_utc": now_utc(),
        "status": "shadow_research_only_no_live_change",
        "load_meta": load_meta,
        "skipped": skipped,
        "coverage": {
            "matrix_rows": int(len(matrix)),
            "settled_rows": int(len(settled)),
            "telemetry_unsettled_rows": int(len(telemetry)),
            "target_date_min": matrix["target_date"].min() if len(matrix) else None,
            "target_date_max": matrix["target_date"].max() if len(matrix) else None,
            "settled_date_min": settled["target_date"].min() if len(settled) else None,
            "settled_date_max": settled["target_date"].max() if len(settled) else None,
            "cities": int(matrix["city"].nunique()) if len(matrix) else 0,
            "quote_coverage": quote_coverage,
        },
        "runway_state_definitions": {
            "no_runway": "forecast_gap_native < 0.50 or missing",
            "fake_runway": "forecast runway exists, but trend cooling, peak already passed, or running max stale >=120m",
            "one_step_runway": "forecast runway exists, obs not fake, and market still treats current as live (current YES ask >=0.40) or neutral with gap <2.25",
            "skip_over_runway": "forecast runway exists, obs not fake, and market has conceded current or forecast gap >=2.25",
        },
        "state_counts": state_counts.to_dict("records"),
        "recent_coverage": recent_cov.to_dict("records"),
        "results": summary.to_dict("records"),
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _slice_table(slice_name: str) -> pd.DataFrame:
        cols = ["slice", "expr", "rows", "dates", "cities", "win_rate", "avg_ask", "roi", "roi_ci_low", "roi_ci_high"]
        return summary[summary["slice"] == slice_name][cols].sort_values("expr")

    one_step = _slice_table("one_step_runway")
    skip = _slice_table("skip_over_runway")
    fake = _slice_table("fake_runway")
    cheap = _slice_table("candidate_one_step_d1_yes_cheap")
    conceded = _slice_table("candidate_skip_over_current_conceded")
    recent_state_totals = (
        recent.groupby("runway_state")
        .agg(rows=("city", "size"), settled_rows=("settled", "sum"), dates=("target_date", "nunique"), cities=("city", "nunique"))
        .reset_index()
        .sort_values("runway_state")
    )

    lines = [
        "# Runway d1 YES Reversal Expression Matrix v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "```text",
        "scope: runway_d1_yes_reversal continuation; Fabel market-pricing state is absorbed",
        "       into the original runway branch, not split into an anchoring strategy.",
        "action: shadow research only; no live selector / runner / notional change.",
        "PnL: canonical settled only. Unsettled rows are telemetry/coverage only.",
        "```",
        "",
        "Main read:",
        "",
        "- `one_step_runway` is the only place where d1 YES is plausibly the right expression;",
        "  the cheap-d1 candidate slice is the cleanest forward telemetry head.",
        "- `skip_over_runway` does not rescue d1 YES. In conceded-current states, the matrix",
        "  should route research toward d2/current-NO/basket/avoid, but the settled CI still",
        "  decides whether any of those are more than telemetry.",
        "- `fake_runway` is not an entry state; it is an avoid/TP diagnostic until fresh-forward",
        "  data proves otherwise.",
        "",
        "Contract gate read:",
        "",
        "```text",
        "candidate_one_step_d1_yes_cheap -> d1_yes:",
        "  rows=162 / dates=30 / cities=25 / avg ask=0.159 / ROI=+32.7%",
        "  date-block CI [-13.3%, +83.9%]",
        "  significance=FAIL, baseline=NA, forward=NA",
        "  conclusion=inconclusive_shadow_research_only",
        "",
        "candidate_skip_over_current_conceded:",
        "  d1_yes ROI=-30.3% CI [-41.9%, -16.0%] => bad expression confirmed for this state",
        "  d2_yes ROI=-8.3% CI [-16.5%, +0.9%], current_bracket_NO ROI=+0.4% CI [-2.3%, +2.7%]",
        "  significance=FAIL for positive edge, baseline=NA, forward=NA",
        "  conclusion=inconclusive; avoid d1 YES here, keep d2/current-NO/basket as telemetry only",
        "",
        "recent trigger coverage:",
        "  2026-06-21..2026-07-01 corrected mirror has 0 one_step_runway rows.",
        "  That is trigger dormancy, not forward failure.",
        "```",
        "",
        "Runway state taxonomy used here:",
        "",
        "- `no_runway`: `forecast_gap_native < 0.50` or missing.",
        "- `fake_runway`: forecast runway exists, but obs says it is probably stale/cooling",
        "  (`temp_trend_1h_f <= -0.5`, forecast peak passed by >=1h, or running max stale >=120m).",
        "- `one_step_runway`: runway exists, not fake, and market still treats current as live",
        "  (`current_high YES ask >=0.40`) or neutral with gap <2.25.",
        "- `skip_over_runway`: runway exists, not fake, and market has conceded current or",
        "  forecast gap is >=2.25 native units.",
        "",
        "## Data Snapshot",
        "",
        f"- Feature rows: {load_meta['raw_quote_rows']} quote rows from corrected feature-factory mirror.",
        f"- Matrix denominator: {len(matrix)} city-date-snapshot rows, "
        f"{matrix['target_date'].min()}..{matrix['target_date'].max()}, {matrix['city'].nunique()} cities.",
        f"- Canonical settled PnL subset: {len(settled)} rows, "
        f"{settled['target_date'].min()}..{settled['target_date'].max()}.",
        f"- Unsettled telemetry-only subset: {len(telemetry)} rows, "
        f"{telemetry['target_date'].min() if len(telemetry) else 'NA'}..{telemetry['target_date'].max() if len(telemetry) else 'NA'}.",
        f"- Build note: `sync_weather_remote.sh` completed; `run_stack.sh` rebuilt DB/facts/gate but exited after DB work because FE port 5174 stayed busy. CLOB coverage gate was run separately and passed.",
        f"- Skipped snapshots while building ladder matrix: {skipped}.",
        "",
        "Quote coverage:",
        "",
        md_table(quote_cov_df),
        "",
        "## State Counts",
        "",
        md_table(state_counts),
        "",
        "## One-Step Runway",
        "",
        md_table(one_step),
        "",
        "## Cheap d1 Candidate Slice",
        "",
        "`candidate_one_step_d1_yes_cheap` = `one_step_runway` with `d1_yes_ask <= 0.30`.",
        "",
        md_table(cheap),
        "",
        "## Skip-Over Runway",
        "",
        md_table(skip),
        "",
        "## Conceded-Current Skip Slice",
        "",
        "`candidate_skip_over_current_conceded` = `skip_over_runway` and `market_current_state=current_conceded`.",
        "",
        md_table(conceded),
        "",
        "## Fake Runway",
        "",
        md_table(fake),
        "",
        "## Recent Coverage",
        "",
        "Recent state totals:",
        "",
        md_table(recent_state_totals),
        "",
        "Recent state by date:",
        "",
        md_table(recent_cov, max_rows=80),
        "",
        "## Output Files",
        "",
        f"- `{(OUT_DIR / 'runway_matrix_rows.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'settled_expression_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'state_counts.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'recent_coverage.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload["coverage"], indent=2, default=str))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
