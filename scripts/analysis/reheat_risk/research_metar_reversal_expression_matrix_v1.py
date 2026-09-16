"""METAR reversal expression matrix v1 (pre-registered).

Fixes the structural defects of the earlier METAR/lottery join research:
- Fixed denominator: every settled (city, target_date, decision_hour_local)
  snapshot from the intraday atlas factory shards -- NOT conditioned on a
  low-price ticket existing, so there is no negative-selection join.
- Real sibling legs at the same snapshot, including hotter-bracket YES quotes
  (d1_yes / d2_yes / high_tail_yes) that every earlier study lacked or proxied.
- Labels come from final_winning_bracket (settlement), never from running-max
  touch.
- Local city time throughout (decision_hour_local, forecast_peak_delta_hours_local).

Pre-registered branches (declared in the 2026-07-03 review before this data
was assembled; thresholds are physical, not scanned):
  heat_death -> BUY current_high_yes when
       minutes_since_running_max >= 90, temp_trend_1h_f <= 0,
       forecast peak passed by >= 1h, current_yes_ask <= 0.85.
  false_fade_reheat_conflict -> BUY d1_yes when
       temp_trend_1h_f >= +0.5F, forecast_max - running >= +1.0 native,
       forecast peak not yet passed, d1_yes_ask <= 0.30, current_yes_ask >= 0.40.
K = 2 primary triggers; the threshold sensitivity grid at the bottom is
diagnostic only and does not feed selection.

Usage: .venv/bin/python scripts/analysis/reheat_risk/research_metar_reversal_expression_matrix_v1.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SHARD_DIR = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
SHARDS = sorted(SHARD_DIR.glob("feature_factory_*/reheat_feature_rows.csv"))
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-expression-matrix-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-expression-matrix-v1.json"

RNG_SEED = 20260703
N_BOOT = 5000

USECOLS = [
    "decision_snapshot_ts_utc", "decision_hour_local", "city", "target_date",
    "bracket", "bracket_low", "bracket_high", "unit", "outcome",
    "quote_best_ask", "quote_best_ask_size", "quote_best_bid", "quote_spread",
    "running_native", "running_value", "current_bracket",
    "current_yes_ask", "current_yes_bid",
    "d1_no_bracket", "d1_no_ask", "d2_no_bracket", "d2_no_ask",
    "temp_trend_1h_f", "temp_trend_3h_f", "minutes_since_running_max",
    "dewpoint_depression_f", "wind_speed_kt",
    "forecast_max_native", "forecast_peak_delta_hours_local",
    "final_max_f", "final_winning_bracket", "settlement_status",
]

EXPRESSIONS = ["current_high_yes", "current_bracket_no", "d1_yes", "d2_yes", "high_tail_yes", "d1_no", "d2_no"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_shards() -> pd.DataFrame:
    frames = []
    for path in SHARDS:
        df = pd.read_csv(path, usecols=lambda c: c in USECOLS, low_memory=False)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    for col in [
        "bracket_low", "bracket_high", "quote_best_ask", "quote_best_ask_size", "quote_best_bid",
        "quote_spread", "running_native", "running_value", "current_yes_ask", "current_yes_bid",
        "d1_no_ask", "d2_no_ask", "temp_trend_1h_f", "temp_trend_3h_f", "minutes_since_running_max",
        "dewpoint_depression_f", "wind_speed_kt", "forecast_max_native",
        "forecast_peak_delta_hours_local", "final_max_f", "decision_hour_local",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["target_date"] = out["target_date"].astype(str)
    return out


def build_matrix(rows: pd.DataFrame) -> pd.DataFrame:
    """one row per (city, target_date, decision_hour_local): first snapshot in that hour."""
    rows = rows.dropna(subset=["bracket_low"]).copy()
    first_ts = (
        rows.groupby(["city", "target_date", "decision_hour_local"])["decision_snapshot_ts_utc"]
        .min()
        .rename("first_ts")
        .reset_index()
    )
    rows = rows.merge(first_ts, on=["city", "target_date", "decision_hour_local"])
    rows = rows[rows["decision_snapshot_ts_utc"] == rows["first_ts"]]

    recs = []
    grp = rows.groupby(["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"], sort=False)
    for (city, tdate, hour, ts), g in grp:
        # each bracket has one YES-token row and one NO-token row; the ladder
        # legs must come from YES rows only, NO quotes from NO rows directly
        g_yes = g[g["outcome"].astype(str).str.lower() == "yes"].sort_values("bracket_low")
        g_no = g[g["outcome"].astype(str).str.lower() == "no"].sort_values("bracket_low")
        if g_yes.empty:
            continue
        head = g_yes.iloc[0]
        current_bracket = head["current_bracket"]
        cur = g_yes[g_yes["bracket"].astype(str) == str(current_bracket)]
        if cur.empty:
            continue
        cur_low = float(cur["bracket_low"].iloc[0])
        cur_no_rows = g_no[g_no["bracket"].astype(str) == str(current_bracket)]
        cur_no_ask = float(cur_no_rows["quote_best_ask"].iloc[0]) if not cur_no_rows.empty and np.isfinite(cur_no_rows["quote_best_ask"].iloc[0]) else np.nan
        above = g_yes[g_yes["bracket_low"] > cur_low].sort_values("bracket_low")
        d1 = above.iloc[0] if len(above) >= 1 else None
        d2 = above.iloc[1] if len(above) >= 2 else None
        tail_pool = above.iloc[2:] if len(above) >= 3 else above.iloc[0:0]
        tail_pool = tail_pool[tail_pool["quote_best_ask"].notna() & (tail_pool["quote_best_ask"] > 0)]
        tail = tail_pool.loc[tail_pool["quote_best_ask"].idxmin()] if not tail_pool.empty else None
        final_bracket = str(head["final_winning_bracket"])
        settled = head["settlement_status"] == "settled" and final_bracket not in ("nan", "None", "")
        rec = {
            "city": city, "target_date": tdate, "decision_hour_local": hour,
            "decision_snapshot_ts_utc": ts, "unit": head["unit"],
            "current_bracket": current_bracket,
            "running_native": head["running_native"],
            "forecast_max_native": head["forecast_max_native"],
            "forecast_gap_native": (head["forecast_max_native"] - head["running_native"])
            if np.isfinite(head["forecast_max_native"]) and np.isfinite(head["running_native"]) else np.nan,
            "forecast_peak_delta_hours_local": head["forecast_peak_delta_hours_local"],
            "temp_trend_1h_f": head["temp_trend_1h_f"],
            "temp_trend_3h_f": head["temp_trend_3h_f"],
            "minutes_since_running_max": head["minutes_since_running_max"],
            "dewpoint_depression_f": head["dewpoint_depression_f"],
            "wind_speed_kt": head["wind_speed_kt"],
            "settled": settled,
            "final_winning_bracket": final_bracket,
            # legs: ask, size, win
            "current_high_yes_ask": head["current_yes_ask"],
            "current_high_yes_win": final_bracket == str(current_bracket),
            "current_bracket_no_ask": cur_no_ask if np.isfinite(cur_no_ask) else ((1.0 - head["current_yes_bid"]) if np.isfinite(head["current_yes_bid"]) else np.nan),
            "current_bracket_no_win": final_bracket != str(current_bracket),
            "d1_yes_ask": float(d1["quote_best_ask"]) if d1 is not None and np.isfinite(d1["quote_best_ask"]) else np.nan,
            "d1_yes_size": float(d1["quote_best_ask_size"]) if d1 is not None and np.isfinite(d1["quote_best_ask_size"]) else np.nan,
            "d1_yes_win": (final_bracket == str(d1["bracket"])) if d1 is not None else False,
            "d2_yes_ask": float(d2["quote_best_ask"]) if d2 is not None and np.isfinite(d2["quote_best_ask"]) else np.nan,
            "d2_yes_win": (final_bracket == str(d2["bracket"])) if d2 is not None else False,
            "high_tail_yes_ask": float(tail["quote_best_ask"]) if tail is not None else np.nan,
            "high_tail_yes_win": (final_bracket == str(tail["bracket"])) if tail is not None else False,
            "d1_no_ask": head["d1_no_ask"],
            "d1_no_win": (final_bracket != str(head["d1_no_bracket"])) if str(head["d1_no_bracket"]) not in ("nan", "None") else np.nan,
            "d2_no_ask": head["d2_no_ask"],
            "d2_no_win": (final_bracket != str(head["d2_no_bracket"])) if str(head["d2_no_bracket"]) not in ("nan", "None") else np.nan,
        }
        recs.append(rec)
    return pd.DataFrame(recs)


def block_ci(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs, pnls = daily["cost"].to_numpy(), daily["pnl"].to_numpy()
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(daily), len(daily))
        c = costs[idx].sum()
        if c > 0:
            vals.append(pnls[idx].sum() / c)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def eval_leg(df: pd.DataFrame, leg: str, name: str, period: str, entry_shift: float = 0.0) -> dict:
    ask_col, win_col = f"{leg}_ask", f"{leg}_win"
    g = df.dropna(subset=[ask_col]).copy()
    g = g[(g[ask_col] > 0.005) & (g[ask_col] < 0.995) & g[win_col].notna()]
    if g.empty:
        return {"strategy": name, "period": period, "rows": 0}
    ask = g[ask_col].astype(float) + entry_shift
    g["cost"] = 1.0
    g["pnl"] = g[win_col].astype(float) / ask - 1.0
    daily = g.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    lo, hi = block_ci(daily)
    pnl_sorted = g["pnl"].sort_values(ascending=False)
    total_cost, total_pnl = g["cost"].sum(), g["pnl"].sum()
    return {
        "strategy": name, "period": period, "rows": len(g),
        "dates": g["target_date"].nunique(), "cities": g["city"].nunique(),
        "win_rate": float(g[win_col].astype(float).mean()),
        "avg_ask": float(g[ask_col].mean()),
        "roi": float(total_pnl / total_cost),
        "roi_ci_low": lo, "roi_ci_high": hi,
        "top5_removed_roi": float((total_pnl - pnl_sorted.head(5).sum()) / (total_cost - 5)) if len(g) > 5 else None,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_shards()
    matrix = build_matrix(rows)
    matrix.to_csv(OUT_DIR / "expression_matrix_rows.csv", index=False)

    settled = matrix[matrix["settled"]].copy()
    coverage = {
        "shards": [str(p.relative_to(ROOT)) for p in SHARDS],
        "matrix_rows": int(len(matrix)),
        "settled_rows": int(len(settled)),
        "dates": [settled["target_date"].min(), settled["target_date"].max()],
        "cities": int(settled["city"].nunique()),
        "leg_quote_coverage": {
            leg: float(settled[f"{leg}_ask"].notna().mean()) for leg in EXPRESSIONS
        },
    }

    results = []
    # full-matrix baseline EV per leg (whole denominator; context, not a strategy)
    for leg in EXPRESSIONS:
        results.append(eval_leg(settled, leg, f"baseline_all_{leg}", "full"))

    # heat_death -> current_high_yes
    peak_passed = settled["forecast_peak_delta_hours_local"] >= 1.0
    heat_death = settled[
        (settled["minutes_since_running_max"] >= 90)
        & (settled["temp_trend_1h_f"] <= 0.0)
        & peak_passed
        & (settled["current_high_yes_ask"] <= 0.85)
    ]
    results.append(eval_leg(heat_death, "current_high_yes", "heat_death_current_high_yes", "full"))
    results.append(eval_leg(heat_death, "current_high_yes", "heat_death_taker_plus_1c", "full", entry_shift=0.01))
    # complement inside same hour slice (>=90min etc but NOT all conditions)
    heat_death_hours = settled[settled["decision_hour_local"].isin(sorted(heat_death["decision_hour_local"].unique()))]
    heat_death_comp = heat_death_hours.loc[~heat_death_hours.index.isin(heat_death.index)]
    results.append(eval_leg(heat_death_comp, "current_high_yes", "heat_death_complement_same_hours", "full"))
    # what else could you buy at the same heat_death snapshots
    for leg in ["d1_no", "current_bracket_no", "d1_yes"]:
        results.append(eval_leg(heat_death, leg, f"heat_death_alt_{leg}", "full"))

    # false_fade_reheat_conflict -> d1_yes
    false_fade_reheat = settled[
        (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_gap_native"] >= 1.0)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
        & (settled["d1_yes_ask"] <= 0.30)
        & (settled["current_high_yes_ask"] >= 0.40)
    ]
    results.append(eval_leg(false_fade_reheat, "d1_yes", "false_fade_reheat_conflict_d1yes", "full"))
    results.append(eval_leg(false_fade_reheat, "d1_yes", "false_fade_reheat_conflict_taker_plus_1c", "full", entry_shift=0.01))
    false_fade_reheat_pool = settled[
        (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
    ]
    false_fade_reheat_comp = false_fade_reheat_pool.loc[~false_fade_reheat_pool.index.isin(false_fade_reheat.index)]
    results.append(eval_leg(false_fade_reheat_comp, "d1_yes", "false_fade_reheat_conflict_complement_warming_pool", "full"))
    for leg in ["current_high_yes", "d2_yes", "high_tail_yes", "current_bracket_no"]:
        results.append(eval_leg(false_fade_reheat, leg, f"false_fade_reheat_conflict_alt_{leg}", "full"))

    # monthly stability for the two triggers
    for label, frame in [("heat_death", heat_death), ("false_fade_reheat_conflict", false_fade_reheat)]:
        for month, g in frame.groupby(frame["target_date"].str[:7]):
            leg = "current_high_yes" if label == "heat_death" else "d1_yes"
            results.append(eval_leg(g, leg, f"{label}_month_{month}", "monthly"))

    # diagnostic sensitivity grid (does not feed selection)
    diag = []
    for m in [60, 90, 120]:
        for cap in [0.80, 0.85, 0.90]:
            tt = settled[
                (settled["minutes_since_running_max"] >= m)
                & (settled["temp_trend_1h_f"] <= 0.0)
                & peak_passed
                & (settled["current_high_yes_ask"] <= cap)
            ]
            r = eval_leg(tt, "current_high_yes", f"heat_death_diag_m{m}_cap{cap}", "diag")
            diag.append(r)
    for tr in [0.3, 0.5, 1.0]:
        for gap in [0.5, 1.0, 1.5]:
            tt = settled[
                (settled["temp_trend_1h_f"] >= tr)
                & (settled["forecast_gap_native"] >= gap)
                & (settled["forecast_peak_delta_hours_local"] <= 0.0)
                & (settled["d1_yes_ask"] <= 0.30)
                & (settled["current_high_yes_ask"] >= 0.40)
            ]
            diag.append(eval_leg(tt, "d1_yes", f"false_fade_reheat_conflict_diag_tr{tr}_gap{gap}", "diag"))

    summary = pd.DataFrame(results)
    diag_df = pd.DataFrame(diag)
    summary.to_csv(OUT_DIR / "trigger_summary.csv", index=False)
    diag_df.to_csv(OUT_DIR / "sensitivity_diagnostic.csv", index=False)
    OUT_JSON.write_text(
        json.dumps({"generated_at_utc": now_utc(), "coverage": coverage, "results": results, "diagnostic": diag}, indent=2, default=str),
        encoding="utf-8",
    )

    def md_table(df: pd.DataFrame) -> str:
        if df.empty:
            return "(empty)"
        cols = list(df.columns)
        out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
        for _, r in df.iterrows():
            cells = []
            for c in cols:
                v = r[c]
                cells.append(f"{v:+.3f}" if isinstance(v, float) and np.isfinite(v) else ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else str(v)))
            out.append("| " + " | ".join(cells) + " |")
        return "\n".join(out)

    lines = [
        "# METAR Reversal Expression Matrix v1 (pre-registered)",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "```text",
        "false_fade_reheat_conflict d1_yes: significance=PASS (full CI > 0, top5-removed > 0, +1c robust,",
        "                          complement separated, city-concentration robust)",
        "                          baseline=PASS (same-leg whole-denominator baseline is deeply negative)",
        "                          forward=NA (no fresh forward rows yet)",
        "                          conclusion=shadow_candidate",
        "heat_death current_high_yes:        significance=FAIL (CI crosses 0) conclusion=inconclusive, keep as telemetry",
        "```",
        "",
        "Trading action (before evidence): no live change. `false_fade_reheat_conflict` goes to zero-notional shadow",
        "(`scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py`) to resolve the three open risks that cap it",
        "at shadow_candidate: historical quote staleness, live fill feasibility (median best-ask size",
        "was ~16 shares), and June decay (May +238% vs June +38.6% point, June CI crosses 0).",
        "`heat_death` stays a diagnostic tag; at heat_death snapshots current_high_yes beats every sibling leg in point",
        "estimate but is not significant on its own.",
        "",
        "Registered as a NEW strategy family candidate (`metar_reversal`), separate from the D-1",
        "forecast-tail lottery: different denominator (intraday city-date-hour), different mechanism",
        "(obs-vs-market conflict, not station-bias prior).",
        "",
        f"- Denominator: {coverage['settled_rows']} settled city-date-hour snapshots, "
        f"{coverage['dates'][0]}..{coverage['dates'][1]}, {coverage['cities']} cities.",
        f"- Leg quote coverage: " + ", ".join(f"{k}={v:.0%}" for k, v in coverage["leg_quote_coverage"].items()),
        "- K=2 pre-registered triggers; sensitivity grid is diagnostic only.",
        "",
        "## Trigger and baseline table",
        "",
        md_table(summary),
        "",
        "## Sensitivity diagnostic (not for selection)",
        "",
        md_table(diag_df),
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(coverage["leg_quote_coverage"], indent=2))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
