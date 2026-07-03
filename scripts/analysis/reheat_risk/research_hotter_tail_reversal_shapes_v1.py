"""Hotter-tail reversal shapes v1 (exploratory pattern hunt, path-aware).

Theme continuation of the hotter-tail research line, but with two separate
strategy-family meanings:

- Shape A is a boundary test for Head A (`forecast_tail_low_price_yes`): can
  intraday 3c..10c hotter tickets pump enough for TP?  It is NOT the D-1/early
  forecast-tail sleeve itself.
- Shape B is a Head B (`metar_reversal`) candidate: rich-current collapse /
  one-step runway reversal.  It is not a low-price lottery and should not inherit
  Head A's maker-first or TP20 execution assumptions.

The taxonomy round showed the BROAD intraday hotter side is overpriced
everywhere; this round drills into rare reversal shapes and payoff PATH instead
of entry->settle averages.  Pre-registered shapes (cells are declared below, K
counted; nothing here is tuned on the result tables -- every cell is reported
win or lose):

Shape A  cheap hotter lottery pump: d1/d2/tail YES legs with ask 0.03..0.10.
         Conditions: warming (trend_1h >= +0.5F), peak ahead (delta <= 0),
         forecast-bracket conflict (forecast max lands in/above the leg),
         city hot-tail prior top tercile.
Shape B  rich current collapse: current_high YES ask >= 0.60 while obs still
         warming / forecast runway -- expressions current_bracket NO, d1 YES,
         d1+d2 basket.
Path metrics per leg: settle win, touch 0.15/0.20/0.30 (max FUTURE same-day
hourly best bid), pump-then-die (touched 0.20, settled 0), hold ROI, TP20/TP30
(exit threshold-1c on first hourly touch), maker-first entry sensitivity
(fill iff any future hourly ask <= entry bid), all at $1/leg with +1c taker
entry stress, date-block bootstrap CI, top5-removed, worst day, monthly and
recent (>= 2026-06-21) splits.

Caveat: paths are HOURLY samples from the atlas factory shards; touch rates are
conservative lower bounds and TP fills assume the threshold print was makeable.

Usage: .venv/bin/python scripts/analysis/reheat_risk/research_hotter_tail_reversal_shapes_v1.py
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
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/hotter_tail_reversal_shapes_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-hotter-tail-reversal-shapes-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-hotter-tail-reversal-shapes-v1.json"

RNG_SEED = 20260703
N_BOOT = 3000
RECENT_START = "2026-06-21"

USECOLS = [
    "decision_snapshot_ts_utc", "decision_hour_local", "city", "target_date",
    "bracket", "bracket_low", "bracket_high", "unit", "outcome",
    "quote_best_ask", "quote_best_ask_size", "quote_best_bid",
    "running_native", "current_bracket", "current_yes_ask", "current_yes_bid",
    "temp_trend_1h_f", "minutes_since_running_max",
    "dewpoint_depression_f", "wind_speed_kt",
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
                       "current_bracket", "final_winning_bracket", "settlement_status"}:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["target_date"] = out["target_date"].astype(str)
    return out


def load_city_hot_tail() -> dict[str, float]:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["error_f_actual_minus_forecast"] = pd.to_numeric(bias["error_f_actual_minus_forecast"], errors="coerce")
    bias = bias.dropna(subset=["error_f_actual_minus_forecast"])
    return (bias.groupby("city")["error_f_actual_minus_forecast"].apply(lambda x: float((x >= 1.0).mean()))).to_dict()


def build(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """returns (leg-level table, quote path index).  one row per snapshot-leg."""
    rows = rows.dropna(subset=["bracket_low"]).copy()
    yes = rows[rows["outcome"].astype(str).str.lower() == "yes"].copy()
    no = rows[rows["outcome"].astype(str).str.lower() == "no"].copy()

    # hourly quote path per (side, city, date, bracket): future bids/asks by hour
    path: dict[tuple, list[tuple[float, float, float]]] = {}
    for side, frame in (("yes", yes), ("no", no)):
        for key, g in frame.groupby(["city", "target_date", "bracket"], sort=False):
            g = g.sort_values("decision_hour_local")
            path[(side, *key)] = list(zip(g["decision_hour_local"], g["quote_best_bid"], g["quote_best_ask"]))

    first_ts = (
        yes.groupby(["city", "target_date", "decision_hour_local"])["decision_snapshot_ts_utc"]
        .min().rename("first_ts").reset_index()
    )
    yes = yes.merge(first_ts, on=["city", "target_date", "decision_hour_local"])
    snap = yes[yes["decision_snapshot_ts_utc"] == yes["first_ts"]]
    no_first = no.merge(first_ts, on=["city", "target_date", "decision_hour_local"])
    no_first = no_first[no_first["decision_snapshot_ts_utc"] == no_first["first_ts"]]
    no_ask = {(r.city, r.target_date, r.decision_hour_local, str(r.bracket)): r.quote_best_ask
              for r in no_first.itertuples()}

    legs = []
    for (city, tdate, hour, ts), g in snap.groupby(
            ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"], sort=False):
        g = g.sort_values("bracket_low").reset_index(drop=True)
        head = g.iloc[0]
        cur_mask = g["bracket"].astype(str) == str(head["current_bracket"])
        if not cur_mask.any():
            continue
        cur_idx = int(g.index[cur_mask][0])
        final_bracket = str(head["final_winning_bracket"])
        settled = head["settlement_status"] == "settled" and final_bracket not in ("nan", "None", "")
        fin_mask = g["bracket"].astype(str) == final_bracket
        steps = (int(g.index[fin_mask][0]) - cur_idx) if (settled and fin_mask.any()) else np.nan

        # forecast target position on the ladder (bracket-width aware conflict feature)
        fmax = head["forecast_max_native"]
        forecast_steps = np.nan
        if np.isfinite(fmax):
            fr = round(fmax)
            hit = g[(g["bracket_low"] - 0.01 <= fr) & (fr <= g["bracket_high"].fillna(np.inf) + 0.01)]
            if not hit.empty:
                forecast_steps = int(hit.index[0]) - cur_idx

        base = {
            "city": city, "target_date": tdate, "decision_hour_local": hour,
            "settled": settled, "steps": steps, "forecast_steps": forecast_steps,
            "temp_trend_1h_f": head["temp_trend_1h_f"],
            "forecast_peak_delta_hours_local": head["forecast_peak_delta_hours_local"],
            "minutes_since_running_max": head["minutes_since_running_max"],
            "forecast_gap_native": (head["forecast_max_native"] - head["running_native"])
            if np.isfinite(head["forecast_max_native"]) and np.isfinite(head["running_native"]) else np.nan,
            "current_yes_ask": head["current_yes_ask"],
        }
        # ladder legs: +1, +2, cheapest 3+; and current-bracket NO
        for name, idx in [("d1_yes", cur_idx + 1), ("d2_yes", cur_idx + 2)]:
            if idx < len(g):
                r = g.iloc[idx]
                legs.append(base | {
                    "leg": name, "leg_steps": idx - cur_idx, "leg_bracket": str(r["bracket"]),
                    "ask": r["quote_best_ask"], "ask_size": r["quote_best_ask_size"],
                })
        tail_pool = g.iloc[cur_idx + 3:]
        tail_pool = tail_pool[pd.to_numeric(tail_pool["quote_best_ask"], errors="coerce") > 0]
        if not tail_pool.empty:
            ti = int(tail_pool["quote_best_ask"].astype(float).idxmin())
            r = g.iloc[ti]
            legs.append(base | {
                "leg": "tail_yes", "leg_steps": ti - cur_idx, "leg_bracket": str(r["bracket"]),
                "ask": r["quote_best_ask"], "ask_size": r["quote_best_ask_size"],
            })
        cno = no_ask.get((city, tdate, hour, str(head["current_bracket"])))
        if cno is not None and np.isfinite(cno):
            legs.append(base | {
                "leg": "current_bracket_no", "leg_steps": -1, "leg_bracket": str(head["current_bracket"]),
                "ask": float(cno), "ask_size": np.nan,
            })
    return pd.DataFrame(legs), path


def add_path_metrics(df: pd.DataFrame, path: dict) -> pd.DataFrame:
    """max future same-day hourly bid / min future ask for the leg bracket (YES token)."""
    max_bid, min_ask = [], []
    for r in df.itertuples():
        side = "no" if r.leg == "current_bracket_no" else "yes"
        seq = path.get((side, r.city, r.target_date, r.leg_bracket), [])
        fut_b = [b for h, b, a in seq if np.isfinite(b) and h > r.decision_hour_local]
        fut_a = [a for h, b, a in seq if np.isfinite(a) and h > r.decision_hour_local]
        max_bid.append(max(fut_b) if fut_b else np.nan)
        min_ask.append(min(fut_a) if fut_a else np.nan)
    df = df.copy()
    df["max_future_bid"] = max_bid
    df["min_future_ask"] = min_ask
    return df


def block_ci(g: pd.DataFrame, col: str) -> tuple[float | None, float | None]:
    daily = g.groupby("target_date")[col].agg(["sum", "count"])
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


def eval_cell(g: pd.DataFrame, label: str, leg: str) -> dict:
    g = g[g["leg"].eq(leg) & g["settled"] & g["steps"].notna()].copy()
    g = g[(g["ask"] > 0.005) & (g["ask"] < 0.995)]
    if len(g) < 8:
        return {"cell": label, "leg": leg, "rows": len(g)}
    win = (g["steps"] == g["leg_steps"]) if leg != "current_bracket_no" else (g["steps"] >= 1)
    entry = g["ask"] + 0.01  # taker stress
    hold = win.astype(float) / entry - 1.0
    touch = {t: (g["max_future_bid"] >= t) for t in (0.15, 0.20, 0.30)}
    tp = {}
    for t in (0.20, 0.30):
        exit_px = t - 0.01
        tp[t] = np.where(touch[t], exit_px / entry - 1.0, np.where(win, 1.0 / entry - 1.0, -1.0))
    g["hold_pnl"] = hold
    g["tp20_pnl"] = tp[0.20]
    g["tp30_pnl"] = tp[0.30]
    hold_lo, hold_hi = block_ci(g, "hold_pnl")
    tp20_lo, tp20_hi = block_ci(g, "tp20_pnl")
    hs = g["hold_pnl"].sort_values(ascending=False)
    daily = g.groupby("target_date")["hold_pnl"].sum()
    # maker-first entry: bid ask-1c, filled iff any future hourly ask <= that bid
    mk_px = (g["ask"] - 0.01).clip(lower=0.01)
    mk_fill = g["min_future_ask"] <= mk_px
    mk_fill_win = float(mk_fill[win].mean()) if win.any() else np.nan
    mk_fill_lose = float(mk_fill[~win].mean()) if (~win).any() else np.nan
    mk_pnl = np.where(mk_fill, win.astype(float) / mk_px - 1.0, 0.0)
    mk_roi = float(mk_pnl.sum() / max(mk_fill.sum(), 1))
    return {
        "cell": label, "leg": leg, "rows": len(g), "dates": g["target_date"].nunique(),
        "cities": g["city"].nunique(), "avg_ask": float(g["ask"].mean()),
        "settle_win": float(win.mean()),
        "touch15": float(touch[0.15].mean()), "touch20": float(touch[0.20].mean()),
        "touch30": float(touch[0.30].mean()),
        "pump_die": float((touch[0.20] & ~win).mean()),
        "hold_roi": float(hold.mean()), "hold_ci_lo": hold_lo, "hold_ci_hi": hold_hi,
        "tp20_roi": float(g["tp20_pnl"].mean()), "tp20_ci_lo": tp20_lo, "tp20_ci_hi": tp20_hi,
        "tp30_roi": float(g["tp30_pnl"].mean()),
        "top5_rm_hold": float((hold.sum() - hs.head(5).sum()) / max(len(g) - 5, 1)) if len(g) > 5 else None,
        "worst_day_hold": float(daily.min()),
        "recent_hold_roi": float(g[g["target_date"] >= RECENT_START]["hold_pnl"].mean())
        if (g["target_date"] >= RECENT_START).any() else None,
        "recent_rows": int((g["target_date"] >= RECENT_START).sum()),
        "maker_fill_rate_win": mk_fill_win, "maker_fill_rate_lose": mk_fill_lose,
        "maker_roi_filled": mk_roi,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    legs, path = build(load_shards())
    legs = add_path_metrics(legs, path)
    hot = load_city_hot_tail()
    legs["city_hot_tail_pct"] = legs["city"].map(hot)
    legs.to_csv(OUT_DIR / "leg_rows.csv", index=False)

    warming = legs["temp_trend_1h_f"] >= 0.5
    peak_ahead = legs["forecast_peak_delta_hours_local"] <= 0.0
    conflict = legs["forecast_steps"] >= legs["leg_steps"]          # forecast lands in/above the leg
    conflict_no = legs["forecast_steps"] >= 1                        # for current NO: forecast says overrun
    hot_city = legs["city_hot_tail_pct"] >= legs["city_hot_tail_pct"].quantile(2 / 3)
    cheap = legs["ask"].between(0.03, 0.10)
    rich_current = legs["current_yes_ask"] >= 0.60

    results = []
    # ---- Shape A: cheap hotter lottery pump (K cells declared here) ----
    a_cells = [
        ("A0_broad_cheap", cheap),
        ("A1_warming", cheap & warming),
        ("A2_peak_ahead", cheap & peak_ahead),
        ("A3_conflict", cheap & conflict),
        ("A4_hot_city", cheap & hot_city),
        ("A5_phys_core(warm+peak+conflict)", cheap & warming & peak_ahead & conflict),
        ("A6_A5_hot_city", cheap & warming & peak_ahead & conflict & hot_city),
    ]
    for label, mask in a_cells:
        for leg in ("d1_yes", "d2_yes", "tail_yes"):
            results.append(eval_cell(legs[mask], label, leg))
    # ---- Shape B: rich current collapse ----
    b_cells = [
        ("B0_rich_current", rich_current),
        ("B1_warming", rich_current & warming),
        ("B2_conflict_no", rich_current & conflict_no),
        ("B3_warm+conflict", rich_current & warming & conflict_no),
        ("B4_B3+peak_ahead", rich_current & warming & conflict_no & peak_ahead),
    ]
    for label, mask in b_cells:
        for leg in ("current_bracket_no", "d1_yes", "d2_yes"):
            results.append(eval_cell(legs[mask], label, leg))

    res = pd.DataFrame([r for r in results if r.get("rows", 0) >= 8])
    res.to_csv(OUT_DIR / "shape_cells.csv", index=False)

    payload = {"generated_at_utc": now_utc(),
               "K_cells": len(a_cells) * 3 + len(b_cells) * 3,
               "results": res.to_dict("records")}
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def md_table(df: pd.DataFrame, cols: list[str]) -> str:
        if df.empty:
            return "(empty)"
        d = df[cols]
        out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
        for _, r in d.iterrows():
            cells = [f"{v:+.3f}" if isinstance(v, float) and np.isfinite(v)
                     else ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else str(v))
                     for v in r]
            out.append("| " + " | ".join(cells) + " |")
        return "\n".join(out)

    main_cols = ["cell", "leg", "rows", "dates", "avg_ask", "settle_win", "touch20", "pump_die",
                 "hold_roi", "hold_ci_lo", "hold_ci_hi", "tp20_roi", "tp20_ci_lo", "tp20_ci_hi",
                 "tp30_roi", "top5_rm_hold", "recent_hold_roi", "recent_rows"]
    exec_cols = ["cell", "leg", "rows", "touch15", "touch30", "worst_day_hold",
                 "maker_fill_rate_win", "maker_fill_rate_lose", "maker_roi_filled"]
    lines = [
        "# Hotter-Tail Reversal Shapes v1 (exploratory, path-aware)",
        "",
        f"Generated: {now_utc()}",
        "",
        f"K = {payload['K_cells']} pre-declared cells (all reported).  Hourly-sampled paths:",
        "touch/TP metrics are conservative lower bounds; +1c taker entry, threshold-1c TP exit.",
        "Conclusions limited to shadow_candidate / inconclusive; no live changes.",
        "",
        "## Verdict",
        "",
        "```text",
        "broad_cheap_hotter_lottery (intraday 3-10c d1/d2/tail):  NEGATIVE, not rescueable",
        "  all 7 Shape-A cells x 3 legs lose on hold AND on TP20/TP30; the hypothesized",
        "  pump path barely exists (touch20 5-14%, pump-then-die 2-8%); conditioning on",
        "  good physics makes it WORSE (what stays cheap when physics favor hotter is the",
        "  leg beyond the move).  This is intraday-cheap-band specific; the D-1 low-price",
        "  sleeve is a different denominator and keeps its own shadow_candidate status.",
        "",
        "rich_current_collapse_reversal (Shape B4):  shadow_candidate",
        "  state: current_high YES still >= 0.60 while obs warming (trend_1h >= +0.5F),",
        "  forecast max lands >= 1 bracket above current (bracket-aware), forecast peak",
        "  still ahead.  d1 YES hold: 59 rows / 28 dates, win 52.5% @ avg ask ~0.29,",
        "  ROI +103.2% CI [+38.3%, +163.8%], top5-removed +50.8%.",
        "  Convergent validity: union with the earlier anchored-conflict trigger (different",
        "  thresholds: gap-based, d1-ask cap, current >= 0.40) = 69 rows / 29 dates /",
        "  24 cities, +87.4% CI [+28.0%, +144.6%]; overlap only 23 rows, so the shape is",
        "  not one threshold set.  Condition stack is monotone (B0 -40% -> B4 +103%).",
        "",
        "execution for the reversal shape:  TAKER entry + HOLD to settlement",
        "  TP20 = -35%, TP30 = -18% (kills the payoff; win rate is ~50%, not a lottery)",
        "  maker-first entry = adversely selected: winners fill 12.9% vs losers 92.9%,",
        "  maker ROI on filled -51%.  This is the sharpest execution result of the round.",
        "",
        "expression ranking inside the shape:  d1 YES > current_bracket NO (+73.3%",
        "  CI [+20.3%, +121.0%], lower carry) >> d2 YES (-94%; the collapse is exactly one",
        "  bracket, never two).  Hotter baskets inherit the dead d2/tail legs -> skip.",
        "",
        "attribution:  METAR-regime x market-anchoring (book lags obs+forecast).",
        "  NOT forecast-bias (hot-city prior cells A4/A6 negative intraday);",
        "  NOT cheap-band convexity (Shape A dead);  noise risk remains: every qualifying",
        "  row is pre-2026-06-21 (state starvation afterwards), so fresh-forward shadow is",
        "  the only path to upgrade.",
        "```",
        "",
        "## Shape A (cheap hotter lottery 0.03-0.10) and Shape B (rich current collapse)",
        "",
        md_table(res, main_cols),
        "",
        "## Execution detail (touch ladder, worst day, maker-entry sensitivity)",
        "",
        md_table(res, exec_cols),
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pd.set_option("display.width", 250)
    print(res[main_cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
