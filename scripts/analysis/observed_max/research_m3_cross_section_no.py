#!/usr/bin/env python3
"""Cross-sectional NO selection in whitelist cities.

Question (user): the 0.81-0.86 asks in whitelist cities reflect *average*
secondary-warming risk. If a model of P(bucket jump | city, hour, decline)
can identify city-days where the true risk is near zero, buying NO on those
should be +EV even in efficient-station cities.

Method:
- Train P(jump_c == d) per (city, decision_hour, decline_bucket) on the wu_obs
  residual history STRICTLY BEFORE the orderbook eval window (train < 2026-05-19),
  with Beta-binomial shrinkage toward the (hour, decline_bucket) pooled rate.
- Score every whitelist tail-NO quote in the eval window (2026-05-19..06-09):
  model_win_prob = 1 - P(jump == distance), model_ev = win_prob - ask.
- Buy when model_ev >= threshold; compare realized ROI of selected vs rejected.

If selection works -> the cross-section is harvestable. If selected subset is
still flat/negative -> the market already prices the cross-section and the
taker side stays closed in whitelist cities.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[3]

TRAIN_END = "2026-05-19"  # exclusive; orderbook eval starts here
SHRINK_K = 50.0  # pseudo-observations toward pooled (hour, decline_bucket) rate


def decline_bucket(x: float) -> str:
    if x < 0.5:
        return "<0.5"
    if x < 1.0:
        return "0.5-1.0"
    if x < 2.0:
        return "1.0-2.0"
    return ">=2.0"


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--wu-detail",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_observed_max_v3_h10_21/m3_observed_max_residual_detail.csv"),
    )
    parser.add_argument(
        "--quotes",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_tail_no_quotes.csv"),
    )
    parser.add_argument("--ev-thresholds", default="0.0,0.02,0.05")
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_cross_section_no_v0"),
    )
    args = parser.parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- training set: physical history strictly before eval window
    wu = pd.read_csv(
        args.wu_detail,
        usecols=["city", "target_date", "decision_hour_local", "running_max_c", "final_max_c", "current_temp_c"],
    ).dropna()
    wu = wu[wu["target_date"] < TRAIN_END].copy()
    wu["jump"] = wu.apply(lambda r: round_half_up(r["final_max_c"]) - round_half_up(r["running_max_c"]), axis=1)
    wu["decline"] = wu["running_max_c"] - wu["current_temp_c"]
    wu["db"] = wu["decline"].apply(decline_bucket)

    # pooled P(jump == d) per (hour, db)
    pooled: dict[tuple[int, str, int], float] = {}
    pooled_n: dict[tuple[int, str], int] = {}
    for (hour, db), g in wu.groupby(["decision_hour_local", "db"]):
        pooled_n[(hour, db)] = len(g)
        for d in (1, 2, 3):
            pooled[(hour, db, d)] = float((g["jump"] == d).mean())

    # city-level with shrinkage
    city_rate: dict[tuple[str, int, str, int], float] = {}
    for (city, hour, db), g in wu.groupby(["city", "decision_hour_local", "db"]):
        n = len(g)
        for d in (1, 2, 3):
            p_city = float((g["jump"] == d).mean())
            p_pool = pooled.get((hour, db, d), 0.0)
            city_rate[(city, hour, db, d)] = (n * p_city + SHRINK_K * p_pool) / (n + SHRINK_K)

    def model_p_land(city: str, hour: int, db: str, d: int) -> float | None:
        if (city, hour, db, d) in city_rate:
            return city_rate[(city, hour, db, d)]
        return pooled.get((hour, db, d))

    # ---- eval: whitelist tail-NO quotes from the exhaustion study
    q = pd.read_csv(args.quotes)
    q = q[q["group"].eq("whitelist")].copy()
    q["db"] = q["decline"].apply(decline_bucket)
    q["p_land"] = q.apply(
        lambda r: model_p_land(r["city"], int(r["decision_hour_local"]), r["db"], int(r["distance"])),
        axis=1,
    )
    q = q[q["p_land"].notna()].copy()
    q["model_win_prob"] = 1.0 - q["p_land"]
    q["model_ev"] = q["model_win_prob"] - q["best_ask"]

    rows = []
    for thr in [float(x) for x in args.ev_thresholds.split(",")]:
        for d_max, tag in ((1, "d1"), (3, "d1-3")):
            sel = q[(q["model_ev"] >= thr) & (q["distance"] <= d_max)]
            rej = q[(q["model_ev"] < thr) & (q["distance"] <= d_max)]
            for name, sub in (("selected", sel), ("rejected", rej)):
                if sub.empty:
                    continue
                # one entry per (city, day, bracket): earliest hour
                u = sub.sort_values("decision_hour_local").drop_duplicates(
                    subset=["city", "target_date", "bracket"], keep="first"
                )
                daily = u.groupby("target_date")["pnl"].sum()
                rows.append(
                    {
                        "ev_thr": thr,
                        "distances": tag,
                        "set": name,
                        "trades": len(u),
                        "cities": u["city"].nunique(),
                        "days": len(daily),
                        "pos_days": int((daily > 0).sum()),
                        "avg_ask": round(float(u["best_ask"].mean()), 3),
                        "avg_model_win": round(float(u["model_win_prob"].mean()), 3),
                        "win_rate": round(float(1 - u["lose"].mean()), 3),
                        "cost": round(float(u["best_ask"].sum()), 2),
                        "pnl": round(float(u["pnl"].sum()), 2),
                        "roi": round(float(u["pnl"].sum() / u["best_ask"].sum()), 4),
                        "daily_tstat": round(
                            float(daily.mean() / daily.std() * math.sqrt(len(daily)))
                            if len(daily) > 1 and daily.std() > 0
                            else float("nan"),
                            2,
                        ),
                    }
                )
    res = pd.DataFrame(rows)
    res.to_csv(out_dir / "cross_section_selection_results.csv", index=False)
    q.to_csv(out_dir / "cross_section_scored_quotes.csv", index=False)

    # calibration: does the model's win prob match realized, and does the market ask track the model?
    q["model_bucket"] = pd.cut(q["model_win_prob"], [0, 0.8, 0.9, 0.95, 0.98, 1.0])
    calib = (
        q.groupby("model_bucket", observed=True)
        .agg(
            n=("lose", "size"),
            realized_win=("lose", lambda s: 1 - s.mean()),
            avg_ask=("best_ask", "mean"),
            avg_model=("model_win_prob", "mean"),
        )
        .round(3)
    )
    calib.to_csv(out_dir / "cross_section_calibration.csv")

    manifest = {
        "experiment": "m3_cross_section_no_v0",
        "train_end_exclusive": TRAIN_END,
        "shrink_k": SHRINK_K,
        "scored_quotes": int(len(q)),
        "notes": [
            "Model trained strictly before the orderbook eval window (no leakage).",
            "model_win_prob = 1 - P(jump == distance | city, hour, decline bucket), shrunk to pooled.",
            "Settlement labels reuse exhaustion study (official pm_history winners).",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    pd.set_option("display.width", 220)
    print("=== calibration: model win prob vs realized vs market ask ===")
    print(calib.to_string())
    print("\n=== selection results ===")
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
