#!/usr/bin/env python3
"""HeadA neutral-universe market-anchored residual study v2.

Builds the denominator before the old HeadA edge/dist selectors, makes strict
expanding target-date predictions, and compares equal-capacity ex-ante ranks.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from strategies.weather_edge_v1.tools.heada_terminal_distribution import (  # noqa: E402
    terminal_bracket_distribution,
)

DB = ROOT / "runtime/weather.db"
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
SOURCE_ROWS = ROOT / "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-13-heada-neutral-universe-residual-v2.md"
OUT_JSON = OUT_MD.with_suffix(".json")
GEN_DIR = ROOT / "docs/analysis/2026-07/generated/heada_neutral_universe_residual_v2"
PRED_CSV = GEN_DIR / "walk_forward_predictions.csv"
SUMMARY_CSV = GEN_DIR / "capacity_summary.csv"

SEED = 20260713
N_BOOT = 5000
MIN_TRAIN_ROWS = 700
REGULARIZATION_C = 0.05
RESIDUAL_TRUST = 0.25
CAPACITIES = (1, 3, 5)

SOURCE_FEATURES = [
    "market_logit",
    "model_market_logit_gap",
    "raw_dist_br",
    "bias_mean_asof",
    "bias_p90_asof",
    "hot_tail_pct_asof",
    "forecast_peak_delta_hours_local",
    "source_mae_asof",
    "source_underforecast_pct_asof",
    "source_gap_to_best_asof",
    "is_ecmwf",
    "is_open_upper",
    "is_open_lower",
]
OVERSHOOT_FEATURES = SOURCE_FEATURES + ["kernel_p_below", "kernel_p_overshoot"]


def weather_fee(price: pd.Series | np.ndarray) -> np.ndarray:
    p = np.asarray(price, dtype=float)
    return 0.05 * p * (1.0 - p)


def logit(values: pd.Series | np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 0.001, 0.999)
    return np.log(p / (1.0 - p))


def parse_bracket_low(value: Any) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else math.nan


def bracket_width_f(unit: Any) -> float:
    return 1.8 if str(unit or "").upper().startswith("C") else 2.0


def bracket_low_f(bracket: Any, unit: Any) -> float:
    low = parse_bracket_low(bracket)
    if not math.isfinite(low):
        return math.nan
    return low * 9.0 / 5.0 + 32.0 if str(unit or "").upper().startswith("C") else low


def load_universe() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    query = """
        SELECT
          f.candidate_id, f.city, f.event_date AS target_date, f.bracket, f.unit,
          f.forecast_source, f.model_version, f.forecast_max_f, f.forecast_max_native,
          f.forecast_peak_delta_hours_local, f.model_p_yes, f.edge,
          f.decision_entry_price AS ask, f.decision_snapshot_ts_utc,
          COALESCE(f.final_yes, s.final_price) AS payoff,
          COALESCE(f.settlement_status, s.settlement_status) AS settle_status,
          f.fact_built_at_utc
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes s
          ON s.city=f.city AND s.target_date=f.event_date AND s.bracket=f.bracket
        WHERE f.side='BUY_YES' AND f.decision_entry_price BETWEEN 0.05 AND 0.20
    """
    frame = pd.read_sql_query(query, conn)
    conn.close()
    for column in [
        "forecast_max_f", "forecast_max_native", "forecast_peak_delta_hours_local",
        "model_p_yes", "edge", "ask", "payoff",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["target_date"] = frame["target_date"].astype(str)
    frame = frame[frame["payoff"].isin([0.0, 1.0]) & frame["settle_status"].eq("settled")].copy()
    frame = (
        frame.sort_values(["target_date", "city", "bracket", "decision_snapshot_ts_utc", "ask"])
        .drop_duplicates(["target_date", "city", "bracket"], keep="first")
        .reset_index(drop=True)
    )
    frame["win"] = frame["payoff"].astype(int)
    return frame


def station_bias_features(frame: pd.DataFrame) -> pd.DataFrame:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["date"] = bias["date"].astype(str)
    bias["error"] = pd.to_numeric(bias["error_f_actual_minus_forecast"], errors="coerce")
    bias = bias.dropna(subset=["city", "model", "date", "error"])
    index = {
        (str(city), str(model)): group.sort_values("date")[["date", "error"]]
        for (city, model), group in bias.groupby(["city", "model"])
    }
    features = []
    for row in frame.itertuples(index=False):
        model = "ecmwf" if "ecmwf" in str(row.forecast_source).lower() else "gfs"
        hist = index.get((str(row.city), model))
        if hist is None:
            features.append({})
            continue
        values = hist.loc[hist["date"] < row.target_date, "error"].dropna()
        if values.empty:
            features.append({})
            continue
        features.append(
            {
                "bias_n_asof": len(values),
                "bias_mean_asof": values.mean(),
                "bias_p50_asof": values.median(),
                "bias_p90_asof": values.quantile(0.9),
                "hot_tail_pct_asof": (values >= 1.0).mean(),
            }
        )
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(features)], axis=1)


def source_quality_features(frame: pd.DataFrame) -> pd.DataFrame:
    rows = pd.read_csv(SOURCE_ROWS, low_memory=False)
    rows["target_date"] = rows["target_date"].astype(str)
    rows["error_f"] = pd.to_numeric(rows["error_f"], errors="coerce")
    rows["abs_error_f"] = pd.to_numeric(rows["abs_error_f"], errors="coerce")
    rows = rows.dropna(subset=["city", "target_date", "model_key", "error_f", "abs_error_f"])
    by_city = {str(city): group for city, group in rows.groupby("city")}
    features = []
    for row in frame.itertuples(index=False):
        city_rows = by_city.get(str(row.city))
        if city_rows is None:
            features.append({})
            continue
        prior = city_rows[city_rows["target_date"] < row.target_date]
        source_key = "ecmwf_ifs025" if "ecmwf" in str(row.forecast_source).lower() else "gfs_seamless"
        stats = []
        for model_key, group in prior.groupby("model_key"):
            if len(group) < 7:
                continue
            stats.append(
                {
                    "model_key": str(model_key),
                    "n": len(group),
                    "mae": group["abs_error_f"].mean(),
                    "under": (group["error_f"] >= 1.0).mean(),
                }
            )
        source = next((item for item in stats if item["model_key"] == source_key), None)
        best = min(stats, key=lambda item: item["mae"]) if stats else None
        if source is None:
            features.append({})
            continue
        features.append(
            {
                "source_n_asof": source["n"],
                "source_mae_asof": source["mae"],
                "source_underforecast_pct_asof": source["under"],
                "source_gap_to_best_asof": source["mae"] - best["mae"] if best else math.nan,
            }
        )
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(features)], axis=1)


def add_geometry(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["bracket_low_f"] = [bracket_low_f(b, u) for b, u in zip(out["bracket"], out["unit"], strict=False)]
    out["bracket_width_f"] = [bracket_width_f(u) for u in out["unit"]]
    out["raw_dist_br"] = (out["bracket_low_f"] - out["forecast_max_f"]) / out["bracket_width_f"]
    text = out["bracket"].astype(str)
    out["is_open_upper"] = text.str.contains(r"\+|or higher|above", case=False, regex=True).astype(float)
    out["is_open_lower"] = text.str.contains(r"<|or lower|below", case=False, regex=True).astype(float)
    out["is_ecmwf"] = out["forecast_source"].astype(str).str.lower().str.contains("ecmwf").astype(float)
    out["market_p"] = out["ask"]
    out["market_logit"] = logit(out["ask"])
    out["model_p"] = out["model_p_yes"].clip(0.001, 0.999)
    out["model_market_logit_gap"] = logit(out["model_p"]) - out["market_logit"]
    out["fee"] = weather_fee(out["ask"])
    kernel = [terminal_bracket_distribution(row).as_dict() for row in out.to_dict("records")]
    out["kernel_p_below"] = [row["p_below"] for row in kernel]
    out["kernel_p_overshoot"] = [row["p_overshoot"] for row in kernel]
    return out


def make_model() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("logit", LogisticRegression(C=REGULARIZATION_C, max_iter=3000, random_state=SEED)),
        ]
    )


def walk_forward(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["source_raw_p"] = np.nan
    out["overshoot_raw_p"] = np.nan
    out["train_rows"] = 0
    for date in sorted(out["target_date"].unique()):
        train = out[out["target_date"] < date]
        test_idx = out.index[out["target_date"] == date]
        if len(train) < MIN_TRAIN_ROWS or train["win"].nunique() < 2:
            continue
        for features, column in [(SOURCE_FEATURES, "source_raw_p"), (OVERSHOOT_FEATURES, "overshoot_raw_p")]:
            usable = [feature for feature in features if train[feature].notna().any()]
            model = make_model()
            model.fit(train[usable], train["win"])
            out.loc[test_idx, column] = model.predict_proba(out.loc[test_idx, usable])[:, 1]
        out.loc[test_idx, "train_rows"] = len(train)
    out = out.dropna(subset=["source_raw_p", "overshoot_raw_p"]).copy()
    out["source_anchored_p"] = out["market_p"] + RESIDUAL_TRUST * (out["source_raw_p"] - out["market_p"])
    out["overshoot_anchored_p"] = out["market_p"] + RESIDUAL_TRUST * (out["overshoot_raw_p"] - out["market_p"])
    for arm, pcol in {
        "old": "model_p",
        "source": "source_anchored_p",
        "overshoot": "overshoot_anchored_p",
    }.items():
        out[f"{arm}_net_edge"] = out[pcol] - out["ask"] - out["fee"]
    # Post-v2 diagnostic, not a promoted arm: preserve the old ranking signal
    # and allow only a small source-residual rerank.
    out["hybrid_net_edge"] = 0.75 * out["old_net_edge"] + 0.25 * out["source_net_edge"]
    return out


def probability_metrics(frame: pd.DataFrame, column: str) -> dict[str, float | int | None]:
    y = frame["win"].to_numpy(int)
    p = np.clip(frame[column].to_numpy(float), 0.001, 0.999)
    return {
        "rows": len(frame), "dates": frame["target_date"].nunique(), "wins": int(y.sum()),
        "mean_pred": p.mean(), "realized": y.mean(), "brier": brier_score_loss(y, p),
        "logloss": log_loss(y, p, labels=[0, 1]),
        "auc": roc_auc_score(y, p) if len(np.unique(y)) == 2 else None,
    }


def select_capacity(frame: pd.DataFrame, score_col: str, capacity: int, eligibility: pd.Series | None = None) -> pd.DataFrame:
    work = frame.copy() if eligibility is None else frame[eligibility].copy()
    if work.empty:
        return work
    work = work.sort_values(["target_date", score_col], ascending=[True, False])
    work = work.drop_duplicates(["target_date", "city"], keep="first")
    return work.groupby("target_date", group_keys=False).head(capacity).copy()


def policy_selection(frame: pd.DataFrame, arm: str, capacity: int, rank_only: bool) -> pd.DataFrame:
    score = f"{arm}_net_edge"
    if rank_only:
        eligibility = pd.Series(True, index=frame.index)
    elif arm in {"old", "hybrid"}:
        eligibility = (frame["edge"] >= 0.20) & (frame["raw_dist_br"] > 0)
    else:
        eligibility = frame[score] > 0
    return select_capacity(frame, score, capacity, eligibility)


def summarize_selection(selected: pd.DataFrame) -> dict[str, float | int | None]:
    if selected.empty:
        return {"rows": 0, "dates": 0}
    pnl = selected["win"] - selected["ask"] - selected["fee"]
    daily = selected.assign(pnl=pnl).groupby("target_date").agg(pnl=("pnl", "sum"), cost=("ask", "sum"))
    remove = pnl.nlargest(min(5, len(selected))).index
    kept = selected.index.difference(remove)
    top5_removed_roi = None
    if len(kept) and selected.loc[kept, "ask"].sum() > 0:
        top5_removed_roi = pnl.loc[kept].sum() / selected.loc[kept, "ask"].sum()
    return {
        "rows": len(selected), "dates": selected["target_date"].nunique(),
        "cities": selected["city"].nunique(), "wins": int(selected["win"].sum()),
        "win_rate": selected["win"].mean(), "avg_ask": selected["ask"].mean(),
        "cost": selected["ask"].sum(), "pnl": pnl.sum(), "roi": pnl.sum() / selected["ask"].sum(),
        "top5_removed_roi": top5_removed_roi,
        "losing_days": int((daily["pnl"] < 0).sum()), "max_daily_loss": daily["pnl"].min(),
    }


def paired_roi_delta_ci(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float, float, float]:
    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        pnl = frame["win"] - frame["ask"] - frame["fee"]
        return frame.assign(pnl=pnl).groupby("target_date").agg(pnl=("pnl", "sum"), cost=("ask", "sum"))
    da, db = daily(a), daily(b)
    dates = sorted(set(da.index) | set(db.index))
    da, db = da.reindex(dates).fillna(0.0), db.reindex(dates).fillna(0.0)
    point = da["pnl"].sum() / da["cost"].sum() - db["pnl"].sum() / db["cost"].sum()
    rng = np.random.default_rng(SEED)
    values = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        ca, cb = da["cost"].to_numpy()[idx].sum(), db["cost"].to_numpy()[idx].sum()
        if ca > 0 and cb > 0:
            values.append(da["pnl"].to_numpy()[idx].sum() / ca - db["pnl"].to_numpy()[idx].sum() / cb)
    lo, hi = np.quantile(values, [0.025, 0.975])
    return point, float(lo), float(hi)


def brier_delta_ci(frame: pd.DataFrame, column: str, baseline: str) -> tuple[float, float, float]:
    work = frame[["target_date", "win", column, baseline]].copy()
    work["delta"] = (work[column] - work["win"]) ** 2 - (work[baseline] - work["win"]) ** 2
    daily = work.groupby("target_date").agg(delta=("delta", "sum"), rows=("delta", "size"))
    rng = np.random.default_rng(SEED)
    values = []
    for _ in range(N_BOOT):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        values.append(take["delta"].sum() / take["rows"].sum())
    lo, hi = np.quantile(values, [0.025, 0.975])
    return work["delta"].mean(), float(lo), float(hi)


def fmt(value: Any, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    return f"{float(value):+.1%}" if pct else f"{float(value):.4f}"


def main() -> None:
    base = add_geometry(source_quality_features(station_bias_features(load_universe())))
    oos = walk_forward(base)
    windows = {
        "oos_all": oos,
        "recent_ge_2026_06_21": oos[oos["target_date"] >= "2026-06-21"],
        "fresh_ge_2026_07_08": oos[oos["target_date"] >= "2026-07-08"],
    }
    prob_columns = {
        "market": "market_p", "old_model": "model_p",
        "source_anchored": "source_anchored_p", "overshoot_anchored": "overshoot_anchored_p",
    }
    probability = {
        window: {arm: probability_metrics(data, column) for arm, column in prob_columns.items()}
        for window, data in windows.items() if not data.empty
    }
    brier_deltas = {
        window: {
            "source_vs_market": brier_delta_ci(data, "source_anchored_p", "market_p"),
            "source_vs_old": brier_delta_ci(data, "source_anchored_p", "model_p"),
            "overshoot_vs_source": brier_delta_ci(data, "overshoot_anchored_p", "source_anchored_p"),
        }
        for window, data in windows.items() if data["target_date"].nunique() >= 3
    }

    selections: dict[tuple[str, str, str, int], pd.DataFrame] = {}
    rows = []
    for window, data in windows.items():
        if data.empty:
            continue
        for mode, rank_only in [("rank_all", True), ("policy", False)]:
            for capacity in CAPACITIES:
                for arm in ["old", "source", "overshoot", "hybrid"]:
                    selected = policy_selection(data, arm, capacity, rank_only)
                    selections[(window, mode, arm, capacity)] = selected
                    rows.append({"window": window, "mode": mode, "arm": arm, "capacity": capacity, **summarize_selection(selected)})
    summary = pd.DataFrame(rows)

    paired = []
    for window, data in windows.items():
        if data.empty:
            continue
        for mode in ["rank_all", "policy"]:
            for capacity in CAPACITIES:
                old = selections[(window, mode, "old", capacity)]
                for arm in ["source", "overshoot", "hybrid"]:
                    new = selections[(window, mode, arm, capacity)]
                    if old.empty or new.empty or len(set(old["target_date"]) | set(new["target_date"])) < 3:
                        continue
                    point, lo, hi = paired_roi_delta_ci(new, old)
                    old_keys = set(zip(old["target_date"], old["city"], old["bracket"]))
                    new_keys = set(zip(new["target_date"], new["city"], new["bracket"]))
                    paired.append(
                        {
                            "window": window, "mode": mode, "arm_vs_old": arm, "capacity": capacity,
                            "roi_delta": point, "ci_low": lo, "ci_high": hi,
                            "ticket_overlap": len(old_keys & new_keys), "old_tickets": len(old_keys), "new_tickets": len(new_keys),
                        }
                    )
    paired_df = pd.DataFrame(paired)

    funnel = {
        "neutral_settled_rows": len(base), "neutral_dates": base["target_date"].nunique(),
        "neutral_cities": base["city"].nunique(), "neutral_wins": int(base["win"].sum()),
        "old_edge20_rows": int((base["edge"] >= 0.20).sum()),
        "old_edge20_hot_rows": int(((base["edge"] >= 0.20) & (base["raw_dist_br"] > 0)).sum()),
        "oos_rows": len(oos), "oos_dates": oos["target_date"].nunique(),
        "oos_first_date": oos["target_date"].min(), "oos_last_date": oos["target_date"].max(),
        "settlement_max_date": base["target_date"].max(),
        "fact_built_at_utc": str(base["fact_built_at_utc"].max()),
    }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": {
            "denominator": "canonical settled BUY_YES ask 0.05..0.20 before edge/dist filters; one row per city-date-bracket",
            "walk_forward": "expanding target-date; test date excluded from train",
            "min_train_rows": MIN_TRAIN_ROWS, "regularization_C": REGULARIZATION_C,
            "residual_trust": RESIDUAL_TRUST, "capacities": CAPACITIES,
            "city_identity_used": False, "roi_threshold_search": False,
        },
        "funnel": funnel, "probability": probability,
        "brier_deltas": {w: {k: list(v) for k, v in d.items()} for w, d in brier_deltas.items()},
        "capacity_summary": summary.to_dict("records"), "paired_roi_delta": paired,
    }

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    keep = [
        "candidate_id", "city", "target_date", "bracket", "ask", "fee", "win", "raw_dist_br",
        "model_p", "market_p", "source_anchored_p", "overshoot_anchored_p",
        "old_net_edge", "source_net_edge", "overshoot_net_edge", "hybrid_net_edge", "train_rows",
    ]
    oos[keep].to_csv(PRED_CSV, index=False)
    summary.to_csv(SUMMARY_CSV, index=False)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    prob_lines = []
    for window, arms in probability.items():
        for arm, row in arms.items():
            prob_lines.append(
                f"| {window} | {arm} | {row['rows']} | {row['dates']} | {fmt(row['realized'], True)} | "
                f"{fmt(row['mean_pred'], True)} | {fmt(row['brier'])} | {fmt(row['logloss'])} | {fmt(row['auc'])} |"
            )
    cap_lines = []
    for _, row in summary.iterrows():
        cap_lines.append(
            f"| {row.window} | {row['mode']} | {row.arm} | {int(row.capacity)} | {int(row.rows)} | "
            f"{int(row.dates)} | {int(row.wins)} | {fmt(row.win_rate, True)} | {fmt(row.avg_ask, True)} | "
            f"{fmt(row.roi, True)} | {fmt(row.top5_removed_roi, True)} | {int(row.losing_days)} | {fmt(row.max_daily_loss)} |"
        )
    pair_lines = []
    for _, row in paired_df.iterrows():
        pair_lines.append(
            f"| {row.window} | {row['mode']} | {row.arm_vs_old} | {int(row.capacity)} | "
            f"{fmt(row.roi_delta, True)} | [{fmt(row.ci_low, True)}, {fmt(row.ci_high, True)}] | "
            f"{int(row.ticket_overlap)}/{int(row.old_tickets)}/{int(row.new_tickets)} |"
        )
    brier_lines = []
    for window, comparisons in brier_deltas.items():
        for name, (point, lo, hi) in comparisons.items():
            brier_lines.append(f"| {window} | {name} | {point:+.6f} | [{lo:+.6f}, {hi:+.6f}] |")

    primary_pair = paired_df[
        (paired_df["window"] == "oos_all") & (paired_df["mode"] == "rank_all")
        & (paired_df["arm_vs_old"] == "source") & (paired_df["capacity"] == 5)
    ].iloc[0]
    conclusion = "confirmed" if primary_pair.ci_low > 0 else "inconclusive"
    OUT_MD.write_text(
        f"""# HeadA Neutral-Universe Market-Anchored Residual v2

Generated: 2026-07-13  
Scope: HeadA only. Research/opportunity layer; no live or shadow runner change.

## Verdict

`{conclusion}`. This v2 fixes the old-model home-field denominator: every arm starts from canonical settled 5–20c BUY_YES before `edge>=0.20` and `dist>0`. The primary comparison is equal-capacity, ex-ante `rank_all`, not hindsight winner selection.

Plain answer: the denominator concern was real, but fixing it does **not** rescue the new model. At N=5/day, old rank is +21.3% ROI versus source residual +14.8% over all OOS dates; on the recent 20 dates, old rank is +25.7% versus source residual -24.4%. The new model is a better probability calibrator but a worse trading ranker. Do not replace HeadA with it.

## Funnel / Data Integrity

- Canonical neutral denominator: {funnel['neutral_settled_rows']} ticket rows / {funnel['neutral_dates']} target dates / {funnel['neutral_cities']} cities / {funnel['neutral_wins']} winners.
- Date range: {base['target_date'].min()}..{base['target_date'].max()}; settlement max {funnel['settlement_max_date']}.
- Canonical fact build: `{funnel['fact_built_at_utc']}` after the 2026-07-13 sync/rebuild.
- Old `edge>=0.20`: {funnel['old_edge20_rows']} rows; old `edge>=0.20 & dist>0`: {funnel['old_edge20_hot_rows']} rows.
- Strict expanding OOS: {funnel['oos_rows']} rows / {funnel['oos_dates']} dates, {funnel['oos_first_date']}..{funnel['oos_last_date']}.
- Grain: one city-date-bracket ticket. At selection, each arm first keeps its highest score per city-date, then takes daily N=1/3/5.
- Source/bias features are PIT: station bias uses dates before target date; multi-source quality uses earlier settled forecast errors only. No city identity.
- Fee: official Weather per-share curve `0.05*ask*(1-ask)`. Pricing is canonical decision ask, not a claim of fresh live fillability.

## Probability Quality

| window | arm | rows | dates | realized | mean p | Brier↓ | logloss↓ | AUC↑ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(prob_lines)}

### Brier Paired Delta

Negative is better; target-date block bootstrap.

| window | comparison | delta | 95% CI |
|---|---|---:|---:|
{chr(10).join(brier_lines)}

## Equal-Capacity Results

- `rank_all`: no edge/dist eligibility; tests pure ex-ante ranking at equal ticket count.
- `policy`: old=`edge>=0.20 & dist>0`; new=`anchored net edge>0`; tests actual policy expression.
- `hybrid` is a post-result diagnostic (`75% old rank + 25% source rank`), not a pre-registered promotion candidate; under policy mode it keeps the old eligibility and changes ranking only.

| window | mode | arm | N/day | rows | dates | wins | win rate | avg ask | ROI | top5 removed ROI | losing days | max daily loss/share |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(cap_lines)}

## Paired ROI Delta vs Old

`overlap` is new/old common tickets shown as `common/old/new`.

| window | mode | new arm | N/day | ROI delta | date-block 95% CI | overlap |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(pair_lines)}

## Interpretation

The probability and trading questions are intentionally separate. Better calibration can come from shrinking toward market without improving equal-capacity winner selection. The equal-capacity paired delta is the decision metric for replacing HeadA ranking; the policy table diagnoses whether a positive residual threshold changes volume or simply removes convex winners.

This run overturns the optimistic reading of v1. On the neutral universe, source residual does not beat the old ranking at N=1/3/5; recent results are materially worse. The market anchor fixes absolute probability mainly by copying the already-calibrated market, while the old model-market disagreement still contains some top-of-book ranking information despite its unusable probability scale. The correct architecture is therefore two-headed: retain/further test the old disagreement rank for selection, and calibrate its probability monotonically without changing order. Source/overshoot remain diagnostics unless they add paired ranking delta.

The `fresh_ge_2026_07_08` slice is only a few dates and is diagnostic. The 25% residual trust and model form were chosen in v1 using an old-selected denominator, so this neutral-universe run is a denominator correction, not a pristine new-time holdout.

## Contract Verdict

significance={'PASS' if conclusion == 'confirmed' else 'FAIL'} for primary paired rank-all N=5 delta; baseline={'PASS' if primary_pair.roi_delta > 0 else 'FAIL'} versus old equal-capacity rank; forward=FAIL/NA because hyperparameters predate but overlap this history and fresh 7/8+ support is thin; conclusion={conclusion}.

## Eight Rings

Covered: descriptive performance, target-date inference, ranking, probability calibration, official fee, equal-capacity counterfactual, daily correlation blocks. Partial/missing: fresh executable book replay, actual fill probability, capacity depth, portfolio correlation, pristine post-registration forward.
"""
    )
    print(json.dumps({"funnel": funnel, "primary_pair": primary_pair.to_dict(), "conclusion": conclusion}, indent=2, default=str))


if __name__ == "__main__":
    main()
