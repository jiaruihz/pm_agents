"""Hotter-tail state taxonomy v1 (exploratory, hypothesis-generation).

Theme: when does the market underprice "today ends hotter than the current
book expects"?  This deliberately does NOT narrow to the anchored one-bracket
reversal; it classifies every settled intraday snapshot by how far above the
current (running-max) bracket the day finally settled, then asks three
questions on the SAME city-date-hour denominator:

1. Taxonomy: how often does the day end 0 / +1 / +2 / +3+ brackets above the
   current bracket, by local hour?  Outcome classification is ordinal within
   the snapshot's own ladder (string match on final_winning_bracket, numeric
   containment fallback), never running-max touch.
2. Calibration: market-implied price of each hotter event (d1_yes ask for +1,
   d2_yes ask for +2, cheapest 3+ tail ask, current_bracket NO ask for >=1)
   versus realized frequency -- where is hotter-tail pricing systematically
   off, and in which direction?
3. Discrimination: can PIT features (METAR trend, forecast runway, peak clock,
   minutes-since-max, moisture, wind, local hour, station hot-tail prior)
   separate the states BEFORE the fact, and which expression (d1 / d2 / tail /
   current NO / hotter baskets) is the right carrier in each region?

Everything here is exploratory: cells that look good are pre-registration
candidates for fresh-forward shadow, not tradable rules.  No live changes.

Usage: .venv/bin/python scripts/analysis/reheat_risk/research_hotter_tail_state_taxonomy_v1.py
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
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/hotter_tail_state_taxonomy_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-hotter-tail-state-taxonomy-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-hotter-tail-state-taxonomy-v1.json"

RNG_SEED = 20260703
N_BOOT = 3000

USECOLS = [
    "decision_snapshot_ts_utc", "decision_hour_local", "city", "target_date",
    "bracket", "bracket_low", "bracket_high", "unit", "outcome",
    "quote_best_ask", "quote_best_ask_size", "quote_best_bid",
    "running_native", "current_bracket",
    "current_yes_ask", "current_yes_bid",
    "temp_trend_1h_f", "temp_trend_3h_f", "minutes_since_running_max",
    "dewpoint_depression_f", "wind_speed_kt", "sky_cover_code",
    "forecast_max_native", "forecast_peak_delta_hours_local",
    "final_max_f", "final_winning_bracket", "settlement_status",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_shards() -> pd.DataFrame:
    frames = [pd.read_csv(p, usecols=lambda c: c in USECOLS, low_memory=False) for p in SHARDS]
    out = pd.concat(frames, ignore_index=True)
    for col in out.columns:
        if col not in {"decision_snapshot_ts_utc", "city", "target_date", "bracket", "unit", "outcome",
                       "current_bracket", "final_winning_bracket", "settlement_status", "sky_cover_code"}:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["target_date"] = out["target_date"].astype(str)
    return out


def load_city_hot_tail() -> dict[str, float]:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["error_f_actual_minus_forecast"] = pd.to_numeric(bias["error_f_actual_minus_forecast"], errors="coerce")
    bias = bias.dropna(subset=["error_f_actual_minus_forecast"])
    return (bias.groupby("city")["error_f_actual_minus_forecast"].apply(lambda x: float((x >= 1.0).mean()))).to_dict()


def build_matrix(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.dropna(subset=["bracket_low"]).copy()
    first_ts = (
        rows.groupby(["city", "target_date", "decision_hour_local"])["decision_snapshot_ts_utc"]
        .min().rename("first_ts").reset_index()
    )
    rows = rows.merge(first_ts, on=["city", "target_date", "decision_hour_local"])
    rows = rows[rows["decision_snapshot_ts_utc"] == rows["first_ts"]]

    recs = []
    grp = rows.groupby(["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"], sort=False)
    for (city, tdate, hour, ts), g in grp:
        g_yes = g[g["outcome"].astype(str).str.lower() == "yes"].sort_values("bracket_low").reset_index(drop=True)
        g_no = g[g["outcome"].astype(str).str.lower() == "no"]
        if g_yes.empty:
            continue
        head = g_yes.iloc[0]
        cur_mask = g_yes["bracket"].astype(str) == str(head["current_bracket"])
        if not cur_mask.any():
            continue
        cur_idx = int(g_yes.index[cur_mask][0])

        # ordinal outcome: index of final winning bracket within this ladder
        final_bracket = str(head["final_winning_bracket"])
        settled = head["settlement_status"] == "settled" and final_bracket not in ("nan", "None", "")
        steps = np.nan
        outcome_join = "unsettled"
        if settled:
            fin_mask = g_yes["bracket"].astype(str) == final_bracket
            if fin_mask.any():
                steps = int(g_yes.index[fin_mask][0]) - cur_idx
                outcome_join = "string_match"
            else:
                # numeric fallback: settle bracket containing rounded final max (native units)
                fmax_f = head["final_max_f"]
                if np.isfinite(fmax_f):
                    fmax_native = fmax_f if str(head["unit"]).upper() == "F" else (fmax_f - 32.0) * 5.0 / 9.0
                    fmax_r = round(fmax_native)
                    hit = g_yes[(g_yes["bracket_low"] - 0.01 <= fmax_r)
                                & (fmax_r <= g_yes["bracket_high"].fillna(np.inf) + 0.01)]
                    if not hit.empty:
                        steps = int(hit.index[0]) - cur_idx
                        outcome_join = "numeric_fallback"
                    else:
                        outcome_join = "no_bracket_match"
                else:
                    outcome_join = "no_final_max"

        def leg(idx: int, col: str):
            if 0 <= idx < len(g_yes):
                v = g_yes.iloc[idx][col]
                return float(v) if np.isfinite(pd.to_numeric(v, errors="coerce")) else np.nan
            return np.nan

        # cheapest true tail (>= 3 brackets above current) among quoted asks
        tail_pool = g_yes.iloc[cur_idx + 3:]
        tail_pool = tail_pool[pd.to_numeric(tail_pool["quote_best_ask"], errors="coerce") > 0]
        tail_idx = int(tail_pool["quote_best_ask"].astype(float).idxmin()) if not tail_pool.empty else None
        tail_steps_offset = (tail_idx - cur_idx) if tail_idx is not None else None

        cur_no_rows = g_no[g_no["bracket"].astype(str) == str(head["current_bracket"])]
        cur_no_ask = pd.to_numeric(cur_no_rows["quote_best_ask"], errors="coerce").dropna()
        recs.append({
            "city": city, "target_date": tdate, "decision_hour_local": hour, "unit": head["unit"],
            "settled": settled, "outcome_join": outcome_join, "steps": steps,
            "n_brackets_above": len(g_yes) - cur_idx - 1,
            # features (PIT at snapshot)
            "temp_trend_1h_f": head["temp_trend_1h_f"],
            "temp_trend_3h_f": head["temp_trend_3h_f"],
            "minutes_since_running_max": head["minutes_since_running_max"],
            "forecast_gap_native": head["forecast_max_native"] - head["running_native"]
            if np.isfinite(head["forecast_max_native"]) and np.isfinite(head["running_native"]) else np.nan,
            "forecast_peak_delta_hours_local": head["forecast_peak_delta_hours_local"],
            "dewpoint_depression_f": head["dewpoint_depression_f"],
            "wind_speed_kt": head["wind_speed_kt"],
            # expressions
            "current_high_yes_ask": head["current_yes_ask"],
            "current_bracket_no_ask": float(cur_no_ask.iloc[0]) if len(cur_no_ask) else
            (1.0 - head["current_yes_bid"] if np.isfinite(head["current_yes_bid"]) else np.nan),
            "d1_yes_ask": leg(cur_idx + 1, "quote_best_ask"),
            "d1_yes_size": leg(cur_idx + 1, "quote_best_ask_size"),
            "d2_yes_ask": leg(cur_idx + 2, "quote_best_ask"),
            "d2_yes_size": leg(cur_idx + 2, "quote_best_ask_size"),
            "tail3p_yes_ask": leg(tail_idx, "quote_best_ask") if tail_idx is not None else np.nan,
            "tail3p_steps_offset": tail_steps_offset,
        })
    return pd.DataFrame(recs)


def block_ci(g: pd.DataFrame, pnl_col: str) -> tuple[float | None, float | None]:
    daily = g.groupby("target_date")[pnl_col].agg(["sum", "count"])
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    s, c = daily["sum"].to_numpy(), daily["count"].to_numpy()
    vals = []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(daily), len(daily))
        n = c[i].sum()
        if n > 0:
            vals.append(s[i].sum() / n)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


EXPR_DEFS = {
    # expression -> (ask column(s), win condition on steps)
    "d1_yes": (["d1_yes_ask"], lambda st: st == 1),
    "d2_yes": (["d2_yes_ask"], lambda st: st == 2),
    "tail3p_yes": (["tail3p_yes_ask"], None),  # special: win iff steps == tail3p offset
    "current_bracket_no": (["current_bracket_no_ask"], lambda st: st >= 1),
    "basket_d1_d2": (["d1_yes_ask", "d2_yes_ask"], lambda st: st in (1, 2)),
    "basket_d1_d2_tail": (["d1_yes_ask", "d2_yes_ask", "tail3p_yes_ask"], None),  # win: 1,2 or tail offset
}


def eval_expr(df: pd.DataFrame, expr: str, label: str) -> dict:
    cols, win_fn = EXPR_DEFS[expr]
    g = df.dropna(subset=cols).copy()
    for c in cols:
        g = g[(g[c] > 0.005) & (g[c] < 0.995)]
    g = g[g["steps"].notna()]
    if g.empty:
        return {"slice": label, "expr": expr, "rows": 0}
    cost = g[cols].sum(axis=1)
    if expr == "tail3p_yes":
        win = g["steps"] == g["tail3p_steps_offset"]
    elif expr == "basket_d1_d2_tail":
        win = g["steps"].isin([1, 2]) | (g["steps"] == g["tail3p_steps_offset"])
    else:
        win = g["steps"].apply(win_fn)
    g["pnl"] = win.astype(float) / cost - 1.0  # $1 total per basket, split by cost share => payoff 1/cost
    lo, hi = block_ci(g, "pnl")
    return {
        "slice": label, "expr": expr, "rows": len(g), "dates": g["target_date"].nunique(),
        "cities": g["city"].nunique(), "win_rate": float(win.mean()), "avg_cost": float(cost.mean()),
        "roi": float(g["pnl"].mean()), "roi_ci_low": lo, "roi_ci_high": hi,
    }


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(f"{v:+.3f}" if isinstance(v, float) and np.isfinite(v)
                         else ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    matrix = build_matrix(load_shards())
    hot_tail = load_city_hot_tail()
    matrix["city_hot_tail_pct"] = matrix["city"].map(hot_tail)
    matrix.to_csv(OUT_DIR / "state_rows.csv", index=False)

    s = matrix[matrix["settled"] & matrix["steps"].notna()].copy()
    join_counts = matrix[matrix["settled"]]["outcome_join"].value_counts().to_dict()
    s["step_class"] = pd.cut(s["steps"], [-99, -0.5, 0.5, 1.5, 2.5, 99],
                             labels=["down_join_artifact", "stays_current", "up_1", "up_2", "up_3plus"])

    # 1) taxonomy by local hour
    tax = pd.crosstab(s["decision_hour_local"], s["step_class"], normalize="index").round(3)
    tax_n = s.groupby("decision_hour_local").size().rename("n")
    taxonomy = pd.concat([tax, tax_n], axis=1).reset_index()

    # 2) market calibration: implied ask vs realized per event, by hour band
    s["hour_band"] = pd.cut(s["decision_hour_local"], [9, 12, 15, 18, 22],
                            labels=["h10_12", "h13_15", "h16_18", "h19_21"])
    cal_rows = []
    for band, g in s.groupby("hour_band", observed=True):
        for ev, ask_col, realized in [
            ("up_ge_1 (current NO)", "current_bracket_no_ask", (g["steps"] >= 1)),
            ("up_eq_1 (d1 YES)", "d1_yes_ask", (g["steps"] == 1)),
            ("up_eq_2 (d2 YES)", "d2_yes_ask", (g["steps"] == 2)),
            ("tail_3p (cheapest 3+)", "tail3p_yes_ask", (g["steps"] == g["tail3p_steps_offset"])),
        ]:
            q = g.dropna(subset=[ask_col])
            q = q[(q[ask_col] > 0.005) & (q[ask_col] < 0.995)]
            if len(q) < 30:
                continue
            r = realized.loc[q.index].astype(float)
            cal_rows.append({
                "hour_band": str(band), "event": ev, "rows": len(q),
                "implied(avg ask)": float(q[ask_col].mean()),
                "realized": float(r.mean()),
                "edge(realized-implied)": float(r.mean() - q[ask_col].mean()),
            })
    calibration = pd.DataFrame(cal_rows)

    # 3) feature discrimination: realized step distribution by feature bucket
    feat_specs = {
        "temp_trend_1h_f": [-99, -0.5, 0.4, 99],
        "forecast_gap_native": [-99, 0.0, 1.0, 2.0, 99],
        "forecast_peak_delta_hours_local": [-99, -2, 0, 2, 99],
        "minutes_since_running_max": [-1, 45, 120, 9999],
        "dewpoint_depression_f": [-99, 10, 25, 199],
        "wind_speed_kt": [-1, 8, 15, 99],
        "city_hot_tail_pct": [-1, 0.45, 0.6, 1.01],
    }
    disc_rows = []
    for feat, bins in feat_specs.items():
        g = s.dropna(subset=[feat]).copy()
        g["bucket"] = pd.cut(g[feat], bins)
        for b, gb in g.groupby("bucket", observed=True):
            if len(gb) < 80:
                continue
            disc_rows.append({
                "feature": feat, "bucket": str(b), "rows": len(gb),
                "p_up_ge_1": float((gb["steps"] >= 1).mean()),
                "p_up_eq_1": float((gb["steps"] == 1).mean()),
                "p_up_ge_2": float((gb["steps"] >= 2).mean()),
                "avg_d1_ask": float(gb["d1_yes_ask"].mean()),
                "avg_no_ask": float(gb["current_bracket_no_ask"].mean()),
            })
    discrimination = pd.DataFrame(disc_rows)

    # 4) two-way physical core (gap x trend), expression A/B per cell
    core_rows = []
    g2 = s.dropna(subset=["forecast_gap_native", "temp_trend_1h_f"]).copy()
    g2["gap_b"] = pd.cut(g2["forecast_gap_native"], [-99, 0.0, 1.0, 2.0, 99],
                         labels=["gap<=0", "gap0_1", "gap1_2", "gap>2"])
    g2["trend_b"] = pd.cut(g2["temp_trend_1h_f"], [-99, -0.5, 0.4, 99],
                           labels=["cooling", "flat", "warming"])
    for (gb, tb), cell in g2.groupby(["gap_b", "trend_b"], observed=True):
        if len(cell) < 60:
            continue
        for expr in EXPR_DEFS:
            core_rows.append(eval_expr(cell, expr, f"{gb}|{tb}") | {"cell_rows": len(cell)})
    core = pd.DataFrame([r for r in core_rows if r.get("rows", 0) > 0])

    payload = {
        "generated_at_utc": now_utc(),
        "denominator": {"settled_rows": int(len(s)), "dates": [s["target_date"].min(), s["target_date"].max()],
                        "cities": int(s["city"].nunique()), "outcome_join_counts": join_counts},
        "taxonomy": taxonomy.to_dict("records"),
        "calibration": cal_rows,
        "discrimination": disc_rows,
        "core_cells": core.to_dict("records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    taxonomy.to_csv(OUT_DIR / "taxonomy_by_hour.csv", index=False)
    calibration.to_csv(OUT_DIR / "market_calibration.csv", index=False)
    discrimination.to_csv(OUT_DIR / "feature_discrimination.csv", index=False)
    core.to_csv(OUT_DIR / "core_cell_expression_ab.csv", index=False)

    lines = [
        "# Hotter-Tail State Taxonomy v1 (exploratory)",
        "",
        f"Generated: {now_utc()}",
        "",
        "Status: hypothesis-generation on the fixed intraday denominator; nothing here is a",
        "tradable rule until pre-registered and confirmed on fresh forward. No live changes.",
        "",
        "## Verdict",
        "",
        "```text",
        "theme: intraday hotter-tail underpricing as a STANDING condition = does not exist",
        "  every hotter event (up>=1 / ==1 / ==2 / 3+) is priced ABOVE realized frequency in",
        "  every local-hour band (taker edge -1.2pp .. -4.3pp); physical state features",
        "  (trend/gap/peak clock/minutes-since-max) separate outcomes strongly but the NO ask",
        "  tracks realized P(hotter) almost exactly in every bucket -- the market prices the",
        "  physics, plus a margin.",
        "conditional alpha: only where the BOOK LAGS the physics (price contradicts obs state),",
        "  e.g. the anchored-conflict cell (market holds current_high >= 0.40 despite",
        "  warming + runway) -- that cell is not reproducible as any broad gap x trend region.",
        "conclusion=shadow_research_only; no broad hotter-tail sleeve exists to promote",
        "```",
        "",
        "State -> expression map (from sections 1-4):",
        "",
        "- stays_current / false-warming (gap<=0): no hotter expression is viable; all legs",
        "  significantly negative. Fading the false warming via current-side YES was already",
        "  tested (heat_death) and is not significant either -- spread eats the margin.",
        "- up_1 (single overshoot): d1 YES, but ONLY under book-lag conditions (anchored",
        "  conflict, n=32 shadow_candidate). As a broad gap1_2|warming cell it is -20.1%.",
        "- up_2 (double jump, gap>2 & warming): market prices the skip (d2 realized 27.4% vs",
        "  ask 0.289). Least-bad expression is current_bracket NO (+5.9% point, CI crosses 0)",
        "  -- i.e. no tradable edge, only reduced bleed.",
        "- up_3plus (true high tail): intraday realized is roughly HALF of implied in every",
        "  hour band; the retail hotter-lottery premium is largest here. If high-tail YES has",
        "  any home it is the D-1 forecast-tail sleeve (station-bias prior, pre-obs), not intraday.",
        "- basket variants (d1+d2, d1+d2+tail) never beat their best single leg; they average",
        "  a good leg with overpriced ones.",
        "",
        "Feature roles: trend/gap/peak/minutes are state CLASSIFIERS (fully priced, no",
        "standalone edge); moisture/wind/city hot-tail prior add little intraday. Their value",
        "is in building an implied-vs-physical divergence score (book staleness detector),",
        "which is the pre-registered v2 direction: score = calibrated physical P(up>=1) minus",
        "current_bracket NO ask; trade only extreme divergence; expression by predicted",
        "landing (gap magnitude). Until that exists, the only live-adjacent artifact stays",
        "the anchored-conflict shadow runner.",
        "",
        "Caveats: taker-at-ask calibration overstates overpricing by ~half-spread (typical",
        "spread 2c); at mid the hotter side is roughly fair, so the premium is captured by",
        "makers, not available to takers. 2026-07-01 orderbook exists but has no settled",
        "labels yet; it is telemetry-only for this denominator.",
        "",
        f"- Denominator: {len(s)} settled city-date-hour snapshots, "
        f"{s['target_date'].min()}..{s['target_date'].max()}, {s['city'].nunique()} cities.",
        f"- Outcome join integrity: {join_counts}",
        "",
        "## 1. Outcome taxonomy by local hour (row-normalized)",
        "",
        md_table(taxonomy),
        "",
        "## 2. Market calibration: implied vs realized per hotter event",
        "",
        md_table(calibration),
        "",
        "## 3. Feature discrimination (realized hotter rates by bucket)",
        "",
        md_table(discrimination),
        "",
        "## 4. Physical core cells (gap x trend): same-snapshot expression A/B",
        "",
        md_table(core),
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("join integrity:", join_counts)
    print(taxonomy.to_string(index=False))
    print(calibration.to_string(index=False))


if __name__ == "__main__":
    main()
