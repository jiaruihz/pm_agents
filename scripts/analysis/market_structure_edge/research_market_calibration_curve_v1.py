#!/usr/bin/env python3
"""Full-denominator market calibration curve on atlas PIT state rows.

Question: across all settled city-days and all five book expressions, is the
market's implied probability (book mid) miscalibrated anywhere by more than
taker friction (half-spread + 0.05*p*(1-p) fee)?  This is the denominator-level
answer to "is there any static taker price pattern left to find", plus a
focused pre-registered probe of the single cell that clears CI
(d1 YES at yes-mid >= 0.80).

Grain: (city, target_date, decision_hour_local, expression); probabilities on
the YES side of the underlying bracket; labels from settled winning bracket;
CI = block bootstrap over target_date.  Deduped variant keeps the first
qualifying hour per city-date.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/generated/market_calibration_curve_v1/summary.json"
FORWARD_START = "2026-06-21"
BUCKET_EDGES = [0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0]


def fee(p):
    return 0.05 * p * (1.0 - p)


def build_long(frame: pd.DataFrame) -> pd.DataFrame:
    cur_yes_mid = (frame["current_yes_ask"] + (1.0 - frame["current_no_ask"])) / 2.0
    parts = []

    def add(expr, yes_mid, win_yes, buy_ask, buy_side, spread):
        parts.append(
            pd.DataFrame(
                {
                    "city": frame["city"],
                    "target_date": frame["target_date"],
                    "hour": frame["decision_hour_local"],
                    "expr": expr,
                    "yes_mid": yes_mid,
                    "win_yes": win_yes,
                    "buy_ask": buy_ask,
                    "buy_side": buy_side,
                    "spread": spread,
                }
            )
        )

    add("current_bracket", cur_yes_mid, frame["current_bracket_held"],
        frame["current_yes_ask"], "YES", frame["current_no_spread"])
    add("current_bracket_no", cur_yes_mid, frame["current_bracket_held"],
        frame["current_no_ask"], "NO", frame["current_no_spread"])
    add("d1", 1.0 - (frame["d1_no_ask"] + frame["d1_no_bid"]) / 2.0, frame["d1_hit"],
        frame["d1_no_ask"], "NO", frame["d1_no_spread"])
    add("d2", 1.0 - (frame["d2_no_ask"] + frame["d2_no_bid"]) / 2.0, frame["d2_hit"],
        frame["d2_no_ask"], "NO", frame["d2_no_spread"])
    add("lottery", (frame["lottery_yes_ask"] + frame["lottery_yes_bid"]) / 2.0,
        frame["lottery_yes_hit"], frame["lottery_yes_ask"], "YES", frame["lottery_yes_spread"])

    long = pd.concat(parts, ignore_index=True)
    long = long.dropna(subset=["yes_mid", "win_yes", "buy_ask"])
    long = long[(long["yes_mid"] > 0) & (long["yes_mid"] < 1)].copy()
    long["win_yes"] = long["win_yes"].astype(float)
    return long


def bias_ci(df: pd.DataFrame, draws: int = 2000, seed: int = 7) -> tuple[float, float]:
    dates = df["target_date"].unique()
    if len(dates) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    g = df.groupby("target_date").agg(n=("win_yes", "size"), w=("win_yes", "sum"), m=("yes_mid", "sum"))
    vals = []
    for _ in range(draws):
        s = g.loc[rng.choice(dates, size=len(dates), replace=True)]
        vals.append((s["w"].sum() - s["m"].sum()) / s["n"].sum())
    return tuple(float(x) for x in np.quantile(vals, [0.025, 0.975]))


def calibration_rows(long: pd.DataFrame, label: str, min_n: int = 30) -> list[dict]:
    out = []
    df = long.copy()
    df["bucket"] = pd.cut(df["yes_mid"], BUCKET_EDGES)
    for b, grp in df.groupby("bucket", observed=True):
        if len(grp) < min_n:
            continue
        lo, hi = bias_ci(grp)
        out.append(
            {
                "slice": label,
                "bucket": str(b),
                "n": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "mid": round(float(grp["yes_mid"].mean()), 4),
                "freq": round(float(grp["win_yes"].mean()), 4),
                "bias": round(float(grp["win_yes"].mean() - grp["yes_mid"].mean()), 4),
                "bias_ci": [round(lo, 4), round(hi, 4)],
                "half_spread": round(float(grp["spread"].mean()) / 2, 4) if grp["spread"].notna().any() else None,
                "taker_fee_at_mid": round(float(fee(grp["yes_mid"].mean())), 4),
            }
        )
    return out


def taker_edge_rows(long: pd.DataFrame, min_n: int = 50) -> list[dict]:
    df = long.copy()
    df["p_win_side"] = np.where(df["buy_side"] == "YES", df["win_yes"], 1 - df["win_yes"])
    df["ask_bucket"] = pd.cut(df["buy_ask"], BUCKET_EDGES)
    out = []
    for (expr, b), grp in df.groupby(["expr", "ask_bucket"], observed=True):
        if len(grp) < min_n:
            continue
        ask = float(grp["buy_ask"].mean())
        dates = grp["target_date"].unique()
        rng = np.random.default_rng(11)
        g = grp.groupby("target_date").agg(n=("p_win_side", "size"), w=("p_win_side", "sum"), a=("buy_ask", "sum"))
        vals = []
        for _ in range(2000):
            s = g.loc[rng.choice(dates, size=len(dates), replace=True)]
            vals.append((s["w"].sum() - s["a"].sum()) / s["n"].sum() - fee(ask))
        lo, hi = np.quantile(vals, [0.025, 0.975])
        out.append(
            {
                "expr": expr,
                "ask_bucket": str(b),
                "n": int(len(grp)),
                "dates": int(len(dates)),
                "avg_ask": round(ask, 4),
                "p_win": round(float(grp["p_win_side"].mean()), 4),
                "edge_fee_adjusted": round(float(grp["p_win_side"].mean() - ask - fee(ask)), 4),
                "edge_ci": [round(float(lo), 4), round(float(hi), 4)],
            }
        )
    return out


def roi_summary(rows: pd.DataFrame, label: str, draws: int = 4000, seed: int = 3) -> dict:
    if len(rows) == 0:
        return {"slice": label, "rows": 0}
    dates = sorted(rows["target_date"].unique())
    lo = hi = float("nan")
    if len(dates) >= 3:
        daily = rows.groupby("target_date")[["pnl", "cost"]].sum()
        rng = np.random.default_rng(seed)
        vals = []
        for _ in range(draws):
            s = daily.loc[rng.choice(dates, size=len(dates), replace=True)].sum()
            vals.append(s["pnl"] / s["cost"])
        lo, hi = (float(x) for x in np.quantile(vals, [0.025, 0.975]))
    return {
        "slice": label,
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "win_rate": round(float(rows["win"].mean()), 4),
        "avg_cost": round(float(rows["cost"].mean()), 4),
        "roi": round(float(rows["pnl"].sum() / rows["cost"].sum()), 4),
        "roi_ci": [round(lo, 4), round(hi, 4)],
    }


def d1_yes_probe(frame: pd.DataFrame, thresh: float) -> dict:
    f = frame.copy()
    f["d1_yes_mid"] = 1.0 - (f["d1_no_ask"] + f["d1_no_bid"]) / 2.0
    f["d1_yes_ask"] = 1.0 - f["d1_no_bid"]
    sel = f[(f["d1_yes_mid"] >= thresh) & f["d1_no_bid"].notna() & f["d1_hit"].notna()].copy()
    sel["win"] = sel["d1_hit"].astype(float)
    sel["cost"] = sel["d1_yes_ask"] + fee(sel["d1_yes_ask"])
    sel["pnl"] = sel["win"] - sel["cost"]
    first = sel.sort_values("decision_hour_local").groupby(["city", "target_date"], as_index=False).first()
    by_city = {
        c: {"n": int(len(g)), "win": round(float(g["win"].mean()), 3),
            "roi": round(float(g["pnl"].sum() / g["cost"].sum()), 4)}
        for c, g in first.groupby("city") if len(g) >= 3
    }
    return {
        "threshold": thresh,
        "all_rows": roi_summary(sel, "all_rows"),
        "first_row_per_city_date": roi_summary(first, "first_row"),
        "train": roi_summary(first[first["target_date"] < FORWARD_START], "train"),
        "forward": roi_summary(first[first["target_date"] >= FORWARD_START], "forward"),
        "by_city_first_row_min3": by_city,
        "hour_distribution": {int(k): int(v) for k, v in
                              first["decision_hour_local"].value_counts().sort_index().items()},
    }


def main() -> None:
    frame = pd.read_csv(DEFAULT_INPUT)
    long = build_long(frame)
    summary = {
        "contract": {
            "input": str(DEFAULT_INPUT.relative_to(ROOT)),
            "forward_start": FORWARD_START,
            "fee": "0.05*p*(1-p) per share",
            "grain": "city x target_date x decision_hour_local x expression",
            "ci": "block bootstrap over target_date, 95%",
            "yes_taker_price": "1 - no_best_bid (mirror book)",
        },
        "inventory": {
            "long_rows": int(len(long)),
            "dates": int(long["target_date"].nunique()),
            "cities": int(long["city"].nunique()),
        },
        "calibration": (
            calibration_rows(long, "pooled")
            + [r for e in ["current_bracket", "d1", "d2", "lottery"]
               for r in calibration_rows(long[long["expr"] == e], e)]
            + [r for lo_h, hi_h, lab in [(0, 11, "hour<12"), (12, 15, "hour12-15"), (16, 23, "hour>=16")]
               for r in calibration_rows(long[(long["hour"] >= lo_h) & (long["hour"] <= hi_h)], lab)]
        ),
        "taker_edge_at_ask": taker_edge_rows(long),
        "d1_yes_high_mid_probe": {str(t): d1_yes_probe(frame, t) for t in (0.80, 0.85)},
    }
    DEFAULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"wrote {DEFAULT_JSON}")


if __name__ == "__main__":
    main()
