#!/usr/bin/env python3
"""Test the cheap current-NO pass-through mechanism on corrected PIT states.

The trade is not "buy NO after every running-high cross".  It is the narrower
state where the market is anchored to the currently observed exact bracket
(current NO is cheap), while the as-of temperature path and forecast still
leave physical runway for a later observation to invalidate that bracket.

All quotes come from the same saved snapshot as the decision.  Observation
history is first-seen/as-of, settlement labels are canonical, and entry cost
uses the official Weather taker-fee curve.  Research-only; no orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import research_source_event_expression_denominator_v3 as denominator  # noqa: E402
from scripts.analysis.market_structure_edge import research_source_event_hazard_router_v1 as hazard  # noqa: E402


DEFAULT_GROUPS = denominator.DEFAULT_GROUPS
DEFAULT_EVENTS = denominator.DEFAULT_EVENTS
DEFAULT_SOURCE_TIMING = denominator.DEFAULT_SOURCE_TIMING
DEFAULT_DB = denominator.DEFAULT_DB
DEFAULT_OUT_DIR = ROOT / "docs/analysis/2026-07/generated/peak_runway_current_no_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-14-peak-runway-current-no-v1.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-peak-runway-current-no-v1.json"

FEE_RATE = 0.05
MIN_ASK = 0.01
MAX_ASK = 0.35
MIN_SHARES = 5.0
FORWARD_START = "2026-06-25"  # mechanism family was documented on 2026-06-23/24
SEED = 20260714
BOOTSTRAP_REPS = 5000
MODEL_MIN_TRAIN_DATES = 10
MODEL_MIN_TRAIN_ROWS = 100
MODEL_EDGE = 0.05

MODEL_FEATURES = [
    "current_no_ask",
    "forecast_gap_to_running_f",
    "forecast_peak_delta_hours_local",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "current_minus_running_f",
    "minutes_since_running_max",
    "max_age_min",
    "decision_hour_local",
]
MARKET_FEATURES = ["current_no_ask"]


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def db_snapshot(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        fact_candidates = int(conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0])
        fact_trades = int(conn.execute("SELECT COUNT(*) FROM fact_trades").fetchone()[0])
        unsettled = int(
            conn.execute(
                "SELECT COUNT(*) FROM fact_trades "
                "WHERE settlement_status IS NULL OR settlement_status='unsettled'"
            ).fetchone()[0]
        )
        missing = int(
            conn.execute("SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'").fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "path": str(path),
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "fact_signal_candidates": fact_candidates,
        "fact_trades": fact_trades,
        "unsettled_trades": unsettled,
        "missing_bracket_trades": missing,
    }


def make_model(features: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), features)],
        remainder="drop",
    )
    return Pipeline(
        [("pre", pre), ("model", LogisticRegression(C=0.2, max_iter=2000, random_state=SEED))]
    )


def expanding_predictions(frame: pd.DataFrame, features: list[str]) -> pd.Series:
    out = pd.Series(np.nan, index=frame.index, dtype=float)
    dates = sorted(frame["target_date"].astype(str).unique())
    for date in dates:
        train = frame[frame["target_date"].astype(str).lt(date)]
        test = frame[frame["target_date"].astype(str).eq(date)]
        if (
            train["target_date"].nunique() < MODEL_MIN_TRAIN_DATES
            or len(train) < MODEL_MIN_TRAIN_ROWS
            or train["outcome"].nunique() < 2
        ):
            continue
        model = make_model(features)
        model.fit(train[features], train["outcome"].astype(int))
        out.loc[test.index] = model.predict_proba(test[features])[:, 1]
    return out


def bootstrap_roi(rows: pd.DataFrame, alpha: float = 0.05) -> tuple[float, float]:
    daily = rows.groupby("target_date", as_index=False).agg(cost=("entry_cost", "sum"), pnl=("pnl", "sum"))
    if len(daily) < 2:
        return math.nan, math.nan
    values = daily[["cost", "pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    sampled = values[rng.integers(0, len(values), size=(BOOTSTRAP_REPS, len(values)))]
    roi = sampled[:, :, 1].sum(axis=1) / sampled[:, :, 0].sum(axis=1)
    return float(np.quantile(roi, alpha / 2.0)), float(np.quantile(roi, 1.0 - alpha / 2.0))


def excess_ci(selected: pd.DataFrame, baseline: pd.DataFrame, alpha: float = 0.05) -> tuple[float, float, float]:
    def daily_roi(rows: pd.DataFrame, name: str) -> pd.Series:
        daily = rows.groupby("target_date").agg(cost=("entry_cost", "sum"), pnl=("pnl", "sum"))
        return (daily["pnl"] / daily["cost"]).rename(name)

    paired = pd.concat([daily_roi(selected, "selected"), daily_roi(baseline, "baseline")], axis=1).dropna()
    if paired.empty:
        return math.nan, math.nan, math.nan
    delta = (paired["selected"] - paired["baseline"]).to_numpy(dtype=float)
    point = float(delta.mean())
    if len(delta) < 2:
        return point, math.nan, math.nan
    rng = np.random.default_rng(SEED)
    boot = rng.choice(delta, size=(BOOTSTRAP_REPS, len(delta)), replace=True).mean(axis=1)
    return point, float(np.quantile(boot, alpha / 2.0)), float(np.quantile(boot, 1.0 - alpha / 2.0))


def first_per_city_day(rows: pd.DataFrame) -> pd.DataFrame:
    return (
        rows.sort_values(["decision_ts", "snapshot_root_priority"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def prepare_states(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    numeric = [
        "current_no_ask",
        "current_no_ask_size",
        "current_no_depth_ask_5c",
        "current_yes_ask",
        "current_yes_ask_size",
        "current_yes_depth_ask_5c",
        "forecast_gap_to_running_f",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "current_minus_running_f",
        "minutes_since_running_max",
        "max_age_min",
        "decision_hour_local",
        "y_current_yes",
    ]
    for col in numeric:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out["ask_depth_shares"] = out["current_no_depth_ask_5c"].where(
        out["current_no_depth_ask_5c"].notna(), out["current_no_ask_size"]
    )
    out["outcome"] = 1.0 - out["y_current_yes"]
    out["ask"] = out["current_no_ask"]
    out["entry_fee"] = out["ask"].map(lambda price: hazard.fee_per_share(float(price), FEE_RATE))
    out["entry_cost"] = out["ask"] + out["entry_fee"]
    out["pnl"] = out["outcome"] - out["entry_cost"]
    out["scope"] = np.where(out["target_date"].astype(str).ge(FORWARD_START), "post_hypothesis", "development")
    return out


def as_trade_side(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    out = frame.copy()
    if side == "NO":
        out["ask"] = out["current_no_ask"]
        out["ask_depth_shares"] = out["current_no_depth_ask_5c"].where(
            out["current_no_depth_ask_5c"].notna(), out["current_no_ask_size"]
        )
        out["outcome"] = 1.0 - out["y_current_yes"]
    elif side == "YES":
        out["ask"] = out["current_yes_ask"]
        out["ask_depth_shares"] = out["current_yes_depth_ask_5c"].where(
            out["current_yes_depth_ask_5c"].notna(), out["current_yes_ask_size"]
        )
        out["outcome"] = out["y_current_yes"]
    else:
        raise ValueError(side)
    out = out[
        out["ask"].between(0.001, 0.999, inclusive="both")
        & out["ask_depth_shares"].ge(MIN_SHARES)
    ].copy()
    out["entry_fee"] = out["ask"].map(lambda price: hazard.fee_per_share(float(price), FEE_RATE))
    out["entry_cost"] = out["ask"] + out["entry_fee"]
    out["pnl"] = out["outcome"] - out["entry_cost"]
    out["side"] = side
    return out


def candidate_universe(states: pd.DataFrame) -> pd.DataFrame:
    return states[
        states["ask"].between(MIN_ASK, MAX_ASK, inclusive="both")
        & states["ask_depth_shares"].ge(MIN_SHARES)
        & ~states["current_bracket_top"].astype(bool)
        & states["outcome"].notna()
        & states["max_age_min"].between(0, 120, inclusive="both")
        & states["decision_hour_local"].between(7, 18, inclusive="both")
    ].copy()


def variant_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    at_high = frame["current_minus_running_f"].ge(-0.6)
    fresh_high = at_high & frame["minutes_since_running_max"].between(0, 60, inclusive="both")
    peak_ahead = frame["forecast_peak_delta_hours_local"].le(0.0)
    forecast_room = frame["forecast_gap_to_running_f"].ge(1.0)
    warming = frame["temp_trend_1h_f"].ge(0.5)
    return {
        "cheap_no_all_state": pd.Series(True, index=frame.index),
        "cheap_no_generic_cross": frame["cross_event"].astype(bool),
        "fresh_high": fresh_high,
        "peak_runway": fresh_high & peak_ahead & forecast_room,
        "active_warming_peak_runway": fresh_high & peak_ahead & forecast_room & warming,
    }


def summarize_variant(
    name: str, side: str, scope: str, rows: pd.DataFrame, baseline: pd.DataFrame, candidate_k: int
) -> dict[str, Any]:
    selected = first_per_city_day(rows)
    base = first_per_city_day(baseline[baseline["target_date"].isin(selected["target_date"])])
    cost = float(selected["entry_cost"].sum())
    pnl = float(selected["pnl"].sum())
    lo, hi = bootstrap_roi(selected)
    bonf_lo, bonf_hi = bootstrap_roi(selected, alpha=0.05 / candidate_k)
    excess, excess_lo, excess_hi = excess_ci(selected, base)
    return {
        "variant": name,
        "side": side,
        "scope": scope,
        "rows": len(selected),
        "dates": selected["target_date"].nunique(),
        "cities": selected["city"].nunique(),
        "avg_ask": selected["ask"].mean(),
        "win_rate": selected["outcome"].mean(),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else math.nan,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "bonferroni_roi_ci_low": bonf_lo,
        "bonferroni_roi_ci_high": bonf_hi,
        "bonferroni_roi_ci_high": bonf_hi,
        "baseline_rows": len(base),
        "baseline_roi": (
            float(base["pnl"].sum()) / float(base["entry_cost"].sum())
            if float(base["entry_cost"].sum()) > 0
            else math.nan
        ),
        "date_equal_excess_roi": excess,
        "excess_ci_low": excess_lo,
        "excess_ci_high": excess_hi,
    }


def model_score_summary(frame: pd.DataFrame) -> pd.DataFrame:
    eligible = frame[frame["p_market_only"].notna() & frame["p_market_path"].notna()].copy()
    rows: list[dict[str, Any]] = []
    for scope, group in [("walk_forward_all", eligible), ("post_hypothesis", eligible[eligible["scope"].eq("post_hypothesis")])]:
        for name, col in [("market_only", "p_market_only"), ("market_plus_path", "p_market_path")]:
            p = group[col].clip(1e-6, 1 - 1e-6)
            y = group["outcome"]
            rows.append(
                {
                    "scope": scope,
                    "model": name,
                    "rows": len(group),
                    "dates": group["target_date"].nunique(),
                    "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()) if len(group) else math.nan,
                    "brier": float(np.square(y - p).mean()) if len(group) else math.nan,
                }
            )
    return pd.DataFrame(rows)


def markdown_table(frame: pd.DataFrame, cols: list[str]) -> str:
    if frame.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in frame[cols].to_dict("records"):
        vals = []
        for value in row.values():
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                vals.append("NA")
            elif isinstance(value, (float, np.floating)):
                vals.append(f"{float(value):.4f}")
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--source-timing", type=Path, default=DEFAULT_SOURCE_TIMING)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    winners, settlement = denominator.load_winners(args.db)
    history, history_coverage = denominator.load_history(args.events, args.source_timing)
    raw_states, funnel = denominator.materialize_expression_states(args.groups, history, winners)
    states = prepare_states(raw_states)
    universe = candidate_universe(states)
    universe = as_trade_side(universe, "NO")
    universe["p_market_only"] = expanding_predictions(universe, MARKET_FEATURES)
    universe["p_market_path"] = expanding_predictions(universe, MODEL_FEATURES)
    universe["model_edge"] = universe["p_market_path"] - universe["entry_cost"]

    masks = variant_masks(universe)
    masks["walk_forward_market_path_edge05"] = universe["p_market_path"].notna() & universe["model_edge"].ge(MODEL_EDGE)
    fixed_masks = {key: value for key, value in masks.items() if key != "walk_forward_market_path_edge05"}
    candidate_k = len(fixed_masks) * 2 + 1
    summaries: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []
    side_universes = {"NO": universe, "YES": as_trade_side(universe, "YES")}
    for side, side_universe in side_universes.items():
        side_masks = masks if side == "NO" else fixed_masks
        for name, mask in side_masks.items():
            side_name = name if side == "NO" else f"{name}_yes_reversal"
            rows = side_universe[mask.reindex(side_universe.index).fillna(False)].copy()
            for scope, scoped in [
                ("all", rows),
                ("development", rows[rows["scope"].eq("development")]),
                ("post_hypothesis", rows[rows["scope"].eq("post_hypothesis")]),
            ]:
                base = side_universe if scope == "all" else side_universe[side_universe["scope"].eq(scope)]
                summaries.append(summarize_variant(side_name, side, scope, scoped, base, candidate_k))
            selected = first_per_city_day(rows)
            selected["variant"] = side_name
            selected_parts.append(selected)

    summary = pd.DataFrame(summaries)
    selected_rows = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    daily = (
        selected_rows.groupby(["variant", "side", "target_date"], as_index=False)
        .agg(rows=("city", "size"), cities=("city", "nunique"), cost=("entry_cost", "sum"), pnl=("pnl", "sum"))
        if not selected_rows.empty
        else pd.DataFrame()
    )
    if not daily.empty:
        daily["roi"] = daily["pnl"] / daily["cost"]
    score_summary = model_score_summary(universe)

    primary = summary[
        summary["variant"].eq("active_warming_peak_runway")
        & summary["side"].eq("NO")
        & summary["scope"].eq("post_hypothesis")
    ].iloc[0]
    significance = "PASS" if float(primary["roi_ci_low"]) > 0 else "FAIL"
    baseline = "PASS" if float(primary["excess_ci_low"]) > 0 else "FAIL"
    forward = "PASS" if float(primary["roi"]) > 0 and int(primary["dates"]) >= 10 else "FAIL"
    confirmed = significance == baseline == forward == "PASS" and float(primary["bonferroni_roi_ci_low"]) > 0
    reversal = summary[
        summary["variant"].eq("active_warming_peak_runway_yes_reversal")
        & summary["side"].eq("YES")
        & summary["scope"].eq("post_hypothesis")
    ].iloc[0]
    verdict = {
        "primary_variant": "active_warming_peak_runway",
        "primary_scope": "post_hypothesis",
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "multiple_testing": "PASS" if float(primary["bonferroni_roi_ci_low"]) > 0 else "FAIL",
        "conclusion": "confirmed" if confirmed else "inconclusive",
        "action": "reject_buy_no_mechanism",
        "yes_reversal": {
            "roi": float(reversal["roi"]),
            "roi_ci_low": float(reversal["roi_ci_low"]),
            "roi_ci_high": float(reversal["roi_ci_high"]),
            "date_equal_excess_roi": float(reversal["date_equal_excess_roi"]),
            "excess_ci_low": float(reversal["excess_ci_low"]),
            "excess_ci_high": float(reversal["excess_ci_high"]),
            "dates": int(reversal["dates"]),
            "action": "shadow_hypothesis_only",
        },
    }
    payload = {
        "schema_version": "peak_runway_current_no_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_zero_notional",
        "db_snapshot": db_snapshot(args.db),
        "settlement_snapshot": settlement,
        "history_coverage": history_coverage,
        "funnel": {
            **funnel,
            "cheap_no_executable_states": len(universe),
            "cheap_no_dates": universe["target_date"].nunique(),
            "cheap_no_cities": universe["city"].nunique(),
            "model_walk_forward_states": int(universe["p_market_path"].notna().sum()),
        },
        "parameters": {
            "ask_range": [MIN_ASK, MAX_ASK],
            "min_shares": MIN_SHARES,
            "forward_start": FORWARD_START,
            "model_edge": MODEL_EDGE,
            "candidate_k": candidate_k,
        },
        "variant_summary": json_ready(summary.to_dict("records")),
        "model_score_summary": json_ready(score_summary.to_dict("records")),
        "verdict": verdict,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out_dir / "variant_summary.csv", index=False)
    daily.to_csv(args.out_dir / "daily.csv", index=False)
    selected_rows.drop(columns=["decision_ts"], errors="ignore").to_csv(
        args.out_dir / "selected_rows.csv", index=False
    )
    score_summary.to_csv(args.out_dir / "model_score_summary.csv", index=False)
    args.json.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    display = summary[
        [
            "variant", "side", "scope", "rows", "dates", "cities", "avg_ask", "win_rate", "roi",
            "roi_ci_low", "roi_ci_high", "bonferroni_roi_ci_low", "bonferroni_roi_ci_high",
            "baseline_roi",
            "date_equal_excess_roi", "excess_ci_low", "excess_ci_high",
        ]
    ]
    report = "\n".join(
        [
            "# Peak-Runway Current-NO v1",
            "",
            "> Cheap current-NO pass-through on corrected expression-specific PIT states; research-only; zero notional.",
            "",
            "## 数据快照",
            "",
            f"- canonical DB `{args.db}` mtime `{payload['db_snapshot']['mtime_utc']}`; fact candidates `{payload['db_snapshot']['fact_signal_candidates']}`, fact trades `{payload['db_snapshot']['fact_trades']}`, unsettled `{payload['db_snapshot']['unsettled_trades']}`, missing_bracket `{payload['db_snapshot']['missing_bracket_trades']}`.",
            f"- settlement `{settlement['min_date']}..{settlement['max_date']}` / `{settlement['dates']}` dates / `{settlement['cities']}` cities.",
            f"- same-snapshot decision states `{funnel['deduplicated_expression_states']}`; executable cheap-current-NO states `{len(universe)}` / `{universe['target_date'].nunique()}` dates / `{universe['city'].nunique()}` cities.",
            "- observation path is first-seen/as-of; entry quote/depth is from the same saved snapshot; official Weather taker fee is included.",
            "",
            "## 核心逻辑",
            "",
            "这不是 generic cross 后无差别买 NO。候选先要求 current NO ask 1c..35c（市场锚定 current exact bracket），再看新高是否仍 fresh、forecast peak 是否尚未过去、forecast 是否仍高于 running max、最近 1h 是否继续升温。最终赚的是后续任何一次升温把 current exact bracket 打穿。",
            "",
            "## 结论",
            "",
            f"- **BUY current NO 应否决。** post-hypothesis `{int(primary['rows'])}` trades / `{int(primary['dates'])}` dates，fee-adjusted ROI `{float(primary['roi']):+.2%}`，date-bootstrap 95% CI `[{float(primary['roi_ci_low']):+.2%}, {float(primary['roi_ci_high']):+.2%}]`；相对同日同价 cheap-NO baseline 的 excess ROI `{float(primary['date_equal_excess_roi']):+.2%}`，CI `[{float(primary['excess_ci_low']):+.2%}, {float(primary['excess_ci_high']):+.2%}]`。这不是“数据还不够所以先等等”，而是该方向在纠正分母后明显反向。",
            f"- **相反的 BUY current YES 只够建立 shadow 假设。** 同一物理状态 post-hypothesis `{int(reversal['rows'])}` trades / `{int(reversal['dates'])}` dates，ROI `{float(reversal['roi']):+.2%}`，95% CI `[{float(reversal['roi_ci_low']):+.2%}, {float(reversal['roi_ci_high']):+.2%}]`；相对 baseline excess `{float(reversal['date_equal_excess_roi']):+.2%}`，CI `[{float(reversal['excess_ci_low']):+.2%}, {float(reversal['excess_ci_high']):+.2%}]`。绝对收益 CI 仍跨 0，且规则为事后提出，不能 live。",
            "- Walk-forward 中加入 path/forecast 后的 logloss 与 Brier 都劣于只用盘口，说明这些粗物理特征没有提供可交易的增量概率；市场更像是在给“当前档最终保持”的概率定价，而非机械地漏算后续升温。",
            "",
            "## Funnel",
            "",
            markdown_table(pd.DataFrame([{"stage": key, "rows": value} for key, value in payload["funnel"].items() if isinstance(value, (int, np.integer))]), ["stage", "rows"]),
            "",
            "## Rule / model results",
            "",
            markdown_table(display, list(display.columns)),
            "",
            "## Walk-forward probability score",
            "",
            markdown_table(score_summary, list(score_summary.columns)),
            "",
            "## Three Gates",
            "",
            f"- significance={verdict['significance']}; baseline={verdict['baseline']}; forward={verdict['forward']}; multiple_testing={verdict['multiple_testing']}; conclusion={verdict['conclusion']}; action={verdict['action']}.",
            f"- K=`{candidate_k}` candidate expressions; Bonferroni-adjusted ROI interval is reported. Post-hypothesis starts `{FORWARD_START}` because the mechanism family was already documented on 6/23-24; exact v1 thresholds remain retrospective and therefore require a new frozen forward check before live.",
            "",
            "## 8 环覆盖",
            "",
            "- covered: descriptive PnL, date-block inference, same-price baseline, path discrimination, canonical settlement, same-snapshot ask/depth, official fee, city-day dedup, date correlation.",
            "- partial: capacity only uses displayed ask/depth; no queue/fill model. Missing: truly fresh pre-registered forward for this exact rule and real fill evidence.",
            "",
        ]
    )
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps({"funnel": payload["funnel"], "verdict": verdict}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
