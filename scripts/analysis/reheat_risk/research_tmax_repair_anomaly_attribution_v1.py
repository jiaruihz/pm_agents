#!/usr/bin/env python3
"""Attribute the tmax lineage repair selection delta to feature groups and states."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
import research_tmax_lineage_repair_replay_v1 as repair  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_repair_anomaly_attribution_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-repair-anomaly-attribution-v1.md"
JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-repair-anomaly-attribution-v1.json"
FORWARD_START = "2026-06-21"
OLD = "old_all_missing"
FULL = "full_repaired"
INTERACTION = "mechanism_interactions_full"
INTERACTION_COLUMNS = [
    "sky_solar_interaction",
    "moisture_path_interaction",
    "humidity_runway_interaction",
]

FEATURE_COLUMNS = [
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "relative_humidity_pct",
    "sky_cover_code",
]

VARIANT_MASKS = {
    OLD: FEATURE_COLUMNS,
    "gfs_gap_only": ["ecmwf_gap_to_running_native", "relative_humidity_pct", "sky_cover_code"],
    "ecmwf_gap_only": ["gfs_gap_to_running_native", "relative_humidity_pct", "sky_cover_code"],
    "both_gaps_only": ["relative_humidity_pct", "sky_cover_code"],
    "humidity_only": ["gfs_gap_to_running_native", "ecmwf_gap_to_running_native", "sky_cover_code"],
    "sky_only": ["gfs_gap_to_running_native", "ecmwf_gap_to_running_native", "relative_humidity_pct"],
    "humidity_sky_only": ["gfs_gap_to_running_native", "ecmwf_gap_to_running_native"],
    FULL: [],
}

CATEGORICAL_FEATURES = [
    "forecast_source",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "solar_window",
]


def expanding_ablation_predictions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    p1.MODEL_SPECS = p3._feature_specs(df)
    train_pre = df[df["target_date"].astype(str) < p1.TRAIN_CUTOFF].copy()
    selected = p1._select_model(train_pre, repair.MODEL_SPEC)["selected"]
    c_value = float(selected["c"])
    alpha = float(selected["cv_blend_alpha"])
    frames = []
    for target_date in sorted(df["target_date"].astype(str).unique()):
        fit = df[df["target_date"].astype(str) < target_date].copy()
        test = df[df["target_date"].astype(str) == target_date].copy()
        if fit["target_date"].nunique() < 5 or test.empty:
            continue
        for variant, masked_columns in VARIANT_MASKS.items():
            masked = test.copy()
            for column in masked_columns:
                if column in masked:
                    masked[column] = np.nan
            pred = p1._fit_predict(fit, masked, repair.MODEL_SPEC, c_value)
            pred = p1._blend_predictions(pred, alpha, repair.MODEL_METHOD)
            pred["variant"] = variant
            frames.append(pred)
    return pd.concat(frames, ignore_index=True), {"c": c_value, "alpha": alpha}


def add_mechanism_interactions(df: pd.DataFrame) -> pd.DataFrame:
    out = feature_rows(df)
    out["sky_solar_interaction"] = out["sky_bin"].astype(str) + "|" + out["solar_window"].fillna("unknown").astype(str)
    out["moisture_path_interaction"] = (
        out["moisture_cloud_regime"].fillna("unknown").astype(str)
        + "|"
        + out["intraday_state"].fillna("unknown").astype(str)
    )
    out["humidity_runway_interaction"] = out["humidity_bin"].astype(str) + "|" + out["day_regime"].fillna("unknown").astype(str)
    return out


def expanding_interaction_predictions(df: pd.DataFrame, c_value: float, alpha: float) -> pd.DataFrame:
    specs = p3._feature_specs(df)
    base = specs[repair.MODEL_SPEC]
    specs[INTERACTION] = {
        "numeric": list(base["numeric"]),
        "categorical": [*base["categorical"], *INTERACTION_COLUMNS],
        "description": "loo_no_city_source plus physical cloud/path and humidity/runway interactions",
    }
    p1.MODEL_SPECS = specs
    frames = []
    for target_date in sorted(df["target_date"].astype(str).unique()):
        fit = df[df["target_date"].astype(str) < target_date].copy()
        test = df[df["target_date"].astype(str) == target_date].copy()
        if fit["target_date"].nunique() < 5 or test.empty:
            continue
        pred = p1._fit_predict(fit, test, INTERACTION, c_value)
        pred = p1._blend_predictions(pred, alpha, repair.MODEL_METHOD)
        pred["variant"] = INTERACTION
        frames.append(pred)
    return pd.concat(frames, ignore_index=True)


def score_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    return repair.summarize_model_predictions(predictions)


def policy_summary(policy: pd.DataFrame) -> pd.DataFrame:
    return repair.summarize_policy(policy)


def feature_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in [
        "relative_humidity_pct",
        "wind_speed_kt",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "forecast_dist_to_upper_share",
    ]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    sky_text = out["sky_cover_code"].astype(str).str.upper()
    sky_map = {"CLR": 0.0, "SKC": 0.0, "CAVOK": 0.0, "FEW": 1.0, "SCT": 2.0, "BKN": 3.0, "OVC": 4.0, "VV": 4.0}
    sky_numeric = pd.to_numeric(out["sky_cover_code"], errors="coerce")
    for key, value in sky_map.items():
        sky_numeric = sky_numeric.where(~sky_text.str.contains(key, na=False), value)
    out["humidity_bin"] = pd.cut(
        out["relative_humidity_pct"],
        bins=[-math.inf, 40, 60, 80, math.inf],
        labels=["rh_lt40", "rh_40_60", "rh_60_80", "rh_ge80"],
    ).astype("object").fillna("unknown")
    out["sky_bin"] = pd.cut(
        sky_numeric,
        bins=[-math.inf, 0.5, 1.5, 2.5, math.inf],
        labels=["clear", "few", "scattered", "broken_overcast"],
    ).astype("object").fillna("unknown")
    out["wind_bin"] = pd.cut(
        out["wind_speed_kt"],
        bins=[-math.inf, 5, 10, 15, math.inf],
        labels=["wind_le5", "wind_5_10", "wind_10_15", "wind_gt15"],
    ).astype("object").fillna("unknown")
    out["trend3h_bin"] = pd.cut(
        out["temp_trend_3h_f"],
        bins=[-math.inf, -0.5, 0.5, 2.0, math.inf],
        labels=["cooling", "flat", "warming", "strong_warming"],
        right=False,
    ).astype("object").fillna("unknown")
    out["peak_clock_bin"] = pd.cut(
        out["forecast_peak_delta_hours_local"],
        bins=[-math.inf, -2, 0, 2, math.inf],
        labels=["gt2h_before_peak", "within2h_before_peak", "within2h_after_peak", "gt2h_after_peak"],
        right=False,
    ).astype("object").fillna("unknown")
    out["forecast_boundary_bin"] = pd.cut(
        out["forecast_dist_to_upper_share"],
        bins=[-math.inf, -0.25, 0.25, 0.75, 1.25, math.inf],
        labels=["below_upper", "near_upper", "above_upper_lt1step", "above_upper_1step", "far_above_upper"],
        right=False,
    ).astype("object").fillna("unknown")
    return out


def selection_margin_rows(hist: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    meta_cols = keys + [
        "temp_trend_3h_f",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
        "d1_no_bid",
        "d2_no_bid",
    ]
    meta = hist[meta_cols].drop_duplicates(keys)
    joined = predictions.merge(meta, on=keys, how="inner", validate="many_to_one")
    rows = []
    for item in joined.to_dict("records"):
        row = pd.Series(item)
        trend = repair.finite(row.get("temp_trend_3h_f"))
        if trend is None or -0.5 <= trend < 0.5:
            continue
        candidates = []
        for expression in repair.EXPRESSIONS:
            ask = repair.expression_ask(row, expression)
            if ask is None or ask < repair.ASK_FLOOR or ask > repair.ASK_CEILING:
                continue
            p_win = repair.expression_probability(row, expression)
            edge = p_win - ask - repair.fee(ask)
            model_roi = edge / (ask + repair.fee(ask))
            candidates.append((edge, model_roi, expression, ask, p_win))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        top = candidates[0]
        if top[0] < repair.EDGE_THRESHOLD:
            continue
        second_edge = candidates[1][0] if len(candidates) > 1 else math.nan
        rows.append(
            {
                "variant": row["variant"],
                "city": row["city"],
                "target_date": row["target_date"],
                "decision_hour_local": row["decision_hour_local"],
                "actual_bucket": row["actual_bucket"],
                "expression": top[2],
                "top_edge": top[0],
                "second_edge": second_edge,
                "top_second_margin": top[0] - second_edge if math.isfinite(second_edge) else math.nan,
                "edge_above_threshold": top[0] - repair.EDGE_THRESHOLD,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return (
        out.sort_values(["variant", "city", "target_date", "decision_hour_local"])
        .groupby(["variant", "city", "target_date"], as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def selected_with_features(policy: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local"]
    feature_cols = keys + FEATURE_COLUMNS + CATEGORICAL_FEATURES + [
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "forecast_dist_to_upper_share",
    ]
    features = feature_rows(hist[feature_cols].drop_duplicates(keys))
    return policy.merge(features, on=keys, how="left", validate="many_to_one")


def city_delta(policy: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for city, group in policy.groupby("city"):
        item: dict[str, Any] = {"city": city, "active_dates": group["target_date"].nunique()}
        for variant in [OLD, FULL]:
            part = group[group["variant"] == variant]
            item[f"{variant}_rows"] = len(part)
            item[f"{variant}_pnl"] = part["pnl"].sum()
            item[f"{variant}_cost"] = part["cost"].sum()
            item[f"{variant}_roi"] = part["pnl"].sum() / part["cost"].sum() if len(part) else math.nan
        item["delta_pnl"] = item[f"{FULL}_pnl"] - item[f"{OLD}_pnl"]
        rows.append(item)
    return pd.DataFrame(rows).sort_values("delta_pnl")


def changed_city_days(policy_features: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date"]
    old = policy_features[policy_features["variant"] == OLD]
    full = policy_features[policy_features["variant"] == FULL]
    merged = old.merge(full, on=keys, how="outer", suffixes=("_old", "_full"), indicator=True)
    merged["transition"] = np.select(
        [merged["_merge"].eq("left_only"), merged["_merge"].eq("right_only"), merged["expression_old"].eq(merged["expression_full"])],
        ["old_only", "full_only", "same_expression"],
        default="switched_expression",
    )
    merged["pnl_old_filled"] = merged["pnl_old"].fillna(0.0)
    merged["pnl_full_filled"] = merged["pnl_full"].fillna(0.0)
    merged["delta_pnl"] = merged["pnl_full_filled"] - merged["pnl_old_filled"]
    for feature in [*CATEGORICAL_FEATURES, "humidity_bin", "sky_bin", "wind_bin", "trend3h_bin", "peak_clock_bin", "forecast_boundary_bin"]:
        merged[f"attribution_{feature}"] = merged[f"{feature}_full"].where(
            merged[f"{feature}_full"].notna(), merged[f"{feature}_old"]
        )
    return merged


def feature_delta_summary(changed: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for feature in [*CATEGORICAL_FEATURES, "humidity_bin", "sky_bin", "wind_bin", "trend3h_bin", "peak_clock_bin", "forecast_boundary_bin"]:
        column = f"attribution_{feature}"
        for value, group in changed.groupby(column, dropna=False):
            rows.append(
                {
                    "feature": feature,
                    "value": str(value),
                    "city_days": len(group),
                    "dates": group["target_date"].nunique(),
                    "cities": group["city"].nunique(),
                    "delta_pnl": group["delta_pnl"].sum(),
                    "avg_delta_pnl": group["delta_pnl"].mean(),
                }
            )
    return pd.DataFrame(rows).sort_values(["feature", "delta_pnl"])


def margin_summary(margins: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for variant, group in margins.groupby("variant"):
        margin = group["top_second_margin"].dropna()
        rows.append(
            {
                "variant": variant,
                "rows": len(group),
                "median_top_second_margin": margin.median(),
                "p25_top_second_margin": margin.quantile(0.25),
                "share_margin_le_1c": (margin <= 0.01).mean(),
                "share_margin_le_2c": (margin <= 0.02).mean(),
                "share_edge_above_threshold_le_1c": (group["edge_above_threshold"] <= 0.01).mean(),
                "share_edge_above_threshold_le_2c": (group["edge_above_threshold"] <= 0.02).mean(),
            }
        )
    return pd.DataFrame(rows)


def feature_coverage_summary(hist: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scopes = {
        "all": hist,
        "verified_forward": hist[hist["target_date"].astype(str) >= FORWARD_START],
    }
    for scope, frame in scopes.items():
        for column in FEATURE_COLUMNS:
            present = frame[column].notna()
            dates = sorted(frame.loc[present, "target_date"].astype(str).unique())
            rows.append(
                {
                    "scope": scope,
                    "feature": column,
                    "rows": len(frame),
                    "non_null_rows": int(present.sum()),
                    "coverage": float(present.mean()),
                    "first_date": dates[0] if dates else "",
                    "last_date": dates[-1] if dates else "",
                }
            )
    return pd.DataFrame(rows)


def transition_summary(changed: pd.DataFrame) -> pd.DataFrame:
    return (
        changed.groupby("transition", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            old_pnl=("pnl_old_filled", "sum"),
            full_pnl=("pnl_full_filled", "sum"),
            delta_pnl=("delta_pnl", "sum"),
        )
        .sort_values("delta_pnl")
    )


def expression_transition_summary(changed: pd.DataFrame) -> pd.DataFrame:
    out = changed[changed["transition"].ne("same_expression")].copy()
    out["expression_transition"] = out["expression_old"].fillna("NONE") + " -> " + out["expression_full"].fillna("NONE")
    return (
        out.groupby("expression_transition", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            old_pnl=("pnl_old_filled", "sum"),
            full_pnl=("pnl_full_filled", "sum"),
            delta_pnl=("delta_pnl", "sum"),
        )
        .sort_values("delta_pnl")
    )


def counterfactual_selected_rows(policy: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local"]
    probability_cols = [f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]
    pred = predictions[keys + ["variant", *probability_cols]].drop_duplicates(keys + ["variant"])
    rows = []
    for source_variant, counterpart_variant in [(OLD, FULL), (FULL, OLD)]:
        selected = policy[policy["variant"].eq(source_variant)].copy()
        counterpart = pred[pred["variant"].eq(counterpart_variant)].drop(columns="variant")
        joined = selected.merge(counterpart, on=keys, how="left", validate="many_to_one")
        for item in joined.to_dict("records"):
            row = pd.Series(item)
            expression = str(row["expression"])
            counterpart_p = repair.expression_probability(row, expression)
            ask = float(row["ask"])
            counterpart_edge = counterpart_p - ask - repair.fee(ask)
            rows.append(
                {
                    "source_variant": source_variant,
                    "counterpart_variant": counterpart_variant,
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_hour_local": row["decision_hour_local"],
                    "expression": expression,
                    "ask": ask,
                    "win": row["win"],
                    "pnl": row["pnl"],
                    "source_p_win": row["p_win"],
                    "source_edge": row["edge"],
                    "counterpart_p_win": counterpart_p,
                    "counterpart_edge": counterpart_edge,
                    "probability_delta": counterpart_p - float(row["p_win"]),
                    "counterpart_eligible": counterpart_edge >= repair.EDGE_THRESHOLD,
                }
            )
    return pd.DataFrame(rows)


def counterfactual_summary(rows: pd.DataFrame) -> pd.DataFrame:
    output = []
    for source_variant, group in rows.groupby("source_variant"):
        lost = group[~group["counterpart_eligible"]]
        output.append(
            {
                "source_variant": source_variant,
                "selected_rows": len(group),
                "still_eligible_rows": int(group["counterpart_eligible"].sum()),
                "still_eligible_share": float(group["counterpart_eligible"].mean()),
                "mean_probability_delta": float(group["probability_delta"].mean()),
                "lost_rows": len(lost),
                "lost_realized_pnl": float(lost["pnl"].sum()),
                "lost_realized_roi": float(lost["pnl"].sum() / (lost["ask"] + lost["ask"].map(repair.fee)).sum()) if len(lost) else math.nan,
            }
        )
    return pd.DataFrame(output)


def counterfactual_lost_expression_summary(rows: pd.DataFrame) -> pd.DataFrame:
    lost = rows[(rows["source_variant"].eq(OLD)) & (~rows["counterpart_eligible"])].copy()
    out = (
        lost.groupby("expression", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            win_rate=("win", "mean"),
            avg_ask=("ask", "mean"),
            old_p_win=("source_p_win", "mean"),
            full_p_win=("counterpart_p_win", "mean"),
            mean_probability_delta=("probability_delta", "mean"),
            pnl=("pnl", "sum"),
        )
        .sort_values("pnl", ascending=False)
    )
    return out


def paired_roi_delta(policy: pd.DataFrame, samples: int = 10000) -> dict[str, float]:
    focus = policy[policy["variant"].isin([OLD, FULL])].copy()
    daily = focus.groupby(["variant", "target_date"], as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    dates = sorted(focus["target_date"].astype(str).unique())
    point = {}
    for variant in [OLD, FULL]:
        part = daily[daily["variant"].eq(variant)]
        point[variant] = float(part["pnl"].sum() / part["cost"].sum())
    rng = np.random.default_rng(20260710)
    draws = []
    indexed = {(str(r.variant), str(r.target_date)): (float(r.pnl), float(r.cost)) for r in daily.itertuples(index=False)}
    for _ in range(samples):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        rois = {}
        for variant in [OLD, FULL]:
            pnl = sum(indexed.get((variant, str(date)), (0.0, 0.0))[0] for date in sample_dates)
            cost = sum(indexed.get((variant, str(date)), (0.0, 0.0))[1] for date in sample_dates)
            rois[variant] = pnl / cost if cost else math.nan
        if math.isfinite(rois[OLD]) and math.isfinite(rois[FULL]):
            draws.append(rois[FULL] - rois[OLD])
    return {
        "old_roi": point[OLD],
        "full_roi": point[FULL],
        "delta": point[FULL] - point[OLD],
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def probability_score_delta_rows(hist: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    probability_cols = [f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]
    focus = predictions[
        predictions["variant"].isin([OLD, FULL]) & (predictions["target_date"].astype(str) >= FORWARD_START)
    ][keys + ["variant", *probability_cols]].copy()
    old = focus[focus["variant"].eq(OLD)].drop(columns="variant")
    full = focus[focus["variant"].eq(FULL)].drop(columns="variant")
    paired = old.merge(full, on=keys, suffixes=("_old", "_full"), validate="one_to_one")
    rows = []
    for item in paired.to_dict("records"):
        actual = str(item["actual_bucket"])
        record = {key: item[key] for key in keys}
        for variant in [OLD, FULL]:
            probabilities = {
                bucket: float(item[f"{repair.MODEL_METHOD}_p_{bucket}_{'old' if variant == OLD else 'full'}"])
                for bucket in repair.BUCKETS
            }
            actual_probability = max(repair.EPS, probabilities[actual])
            record[f"logloss_{variant}"] = -math.log(actual_probability)
            record[f"brier_{variant}"] = sum(
                (probability - (1.0 if bucket == actual else 0.0)) ** 2
                for bucket, probability in probabilities.items()
            )
        record["delta_logloss"] = record[f"logloss_{FULL}"] - record[f"logloss_{OLD}"]
        record["delta_brier"] = record[f"brier_{FULL}"] - record[f"brier_{OLD}"]
        rows.append(record)
    out = pd.DataFrame(rows)
    feature_cols = ["city", "target_date", "decision_hour_local", *CATEGORICAL_FEATURES, "relative_humidity_pct", "sky_cover_code", "wind_speed_kt", "temp_trend_3h_f", "forecast_peak_delta_hours_local", "forecast_dist_to_upper_share"]
    features = feature_rows(hist[feature_cols].drop_duplicates(["city", "target_date", "decision_hour_local"]))
    return out.merge(features, on=["city", "target_date", "decision_hour_local"], how="left", validate="one_to_one")


def probability_score_slice_summary(rows: pd.DataFrame) -> pd.DataFrame:
    output = []
    features = [*CATEGORICAL_FEATURES, "humidity_bin", "sky_bin", "wind_bin", "trend3h_bin", "peak_clock_bin", "forecast_boundary_bin"]
    for feature in features:
        for value, group in rows.groupby(feature, dropna=False):
            output.append(
                {
                    "feature": feature,
                    "value": str(value),
                    "rows": len(group),
                    "dates": group["target_date"].nunique(),
                    "cities": group["city"].nunique(),
                    "old_logloss": group[f"logloss_{OLD}"].mean(),
                    "full_logloss": group[f"logloss_{FULL}"].mean(),
                    "delta_logloss": group["delta_logloss"].mean(),
                    "delta_brier": group["delta_brier"].mean(),
                }
            )
    return pd.DataFrame(output).sort_values(["feature", "delta_logloss"])


def city_probability_score_summary(rows: pd.DataFrame) -> pd.DataFrame:
    return (
        rows.groupby("city", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            old_logloss=(f"logloss_{OLD}", "mean"),
            full_logloss=(f"logloss_{FULL}", "mean"),
            delta_logloss=("delta_logloss", "mean"),
            delta_brier=("delta_brier", "mean"),
        )
        .sort_values("delta_logloss")
    )


def expression_probability_score_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    probability_cols = [f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]
    focus = predictions[
        predictions["variant"].isin([OLD, FULL]) & (predictions["target_date"].astype(str) >= FORWARD_START)
    ][keys + ["variant", *probability_cols]].copy()
    old = focus[focus["variant"].eq(OLD)].drop(columns="variant")
    full = focus[focus["variant"].eq(FULL)].drop(columns="variant")
    paired = old.merge(full, on=keys, suffixes=("_old", "_full"), validate="one_to_one")
    rows = []
    for item in paired.to_dict("records"):
        actual = str(item["actual_bucket"])
        for expression in repair.EXPRESSIONS:
            target_bucket = expression.split("_")[0] if expression != "current_no" else "current"
            is_yes = expression.endswith("_yes")
            outcome = float((actual == target_bucket) if is_yes else (actual != target_bucket))
            record = {"expression": expression, "outcome": outcome}
            for variant, suffix in [(OLD, "old"), (FULL, "full")]:
                bucket_probability = float(item[f"{repair.MODEL_METHOD}_p_{target_bucket}_{suffix}"])
                probability = bucket_probability if is_yes else 1.0 - bucket_probability
                probability = min(1.0 - repair.EPS, max(repair.EPS, probability))
                record[f"p_{variant}"] = probability
                record[f"logloss_{variant}"] = -(outcome * math.log(probability) + (1.0 - outcome) * math.log(1.0 - probability))
                record[f"brier_{variant}"] = (probability - outcome) ** 2
            rows.append(record)
    detail = pd.DataFrame(rows)
    output = (
        detail.groupby("expression", as_index=False)
        .agg(
            rows=("outcome", "size"),
            base_rate=("outcome", "mean"),
            old_avg_p=(f"p_{OLD}", "mean"),
            full_avg_p=(f"p_{FULL}", "mean"),
            old_logloss=(f"logloss_{OLD}", "mean"),
            full_logloss=(f"logloss_{FULL}", "mean"),
            old_brier=(f"brier_{OLD}", "mean"),
            full_brier=(f"brier_{FULL}", "mean"),
        )
    )
    output["delta_logloss"] = output["full_logloss"] - output["old_logloss"]
    output["delta_brier"] = output["full_brier"] - output["old_brier"]
    return output.sort_values("delta_logloss")


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    if df.empty:
        return "_no rows_"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in df.reindex(columns=columns).iterrows():
        values = []
        for column in columns:
            value = row[column]
            if isinstance(value, (float, np.floating)):
                values.append("n/a" if not math.isfinite(float(value)) else f"{float(value):.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist, counters = p4._load_rows_extended()
    hist = add_mechanism_interactions(hist)
    predictions, model_meta = expanding_ablation_predictions(hist)
    interaction_predictions = expanding_interaction_predictions(hist, model_meta["c"], model_meta["alpha"])
    predictions = pd.concat([predictions, interaction_predictions], ignore_index=True)
    scores = score_summary(predictions)
    policy = repair.policy_replay(hist, predictions, "all_scored")
    summaries = policy_summary(policy)
    forward_policy = policy[policy["target_date"].astype(str) >= FORWARD_START].copy()
    forward_features = selected_with_features(forward_policy, hist)
    cities = city_delta(forward_policy[forward_policy["variant"].isin([OLD, FULL])])
    changed = changed_city_days(forward_features[forward_features["variant"].isin([OLD, FULL])])
    feature_deltas = feature_delta_summary(changed)
    margins = selection_margin_rows(hist, predictions[predictions["variant"].isin([OLD, FULL])])
    margins = margins[margins["target_date"].astype(str) >= FORWARD_START]
    margin_stats = margin_summary(margins)
    coverage = feature_coverage_summary(hist)
    transitions = transition_summary(changed)
    expression_transitions = expression_transition_summary(changed)
    counterfactual = counterfactual_selected_rows(
        forward_policy[forward_policy["variant"].isin([OLD, FULL])],
        predictions[predictions["variant"].isin([OLD, FULL])],
    )
    counterfactual_stats = counterfactual_summary(counterfactual)
    counterfactual_lost = counterfactual_lost_expression_summary(counterfactual)
    paired_delta = paired_roi_delta(forward_policy)
    score_delta_rows = probability_score_delta_rows(hist, predictions)
    score_slices = probability_score_slice_summary(score_delta_rows)
    city_scores = city_probability_score_summary(score_delta_rows)
    expression_scores = expression_probability_score_summary(predictions)

    scores.to_csv(OUT_DIR / "probability_score_summary.csv", index=False)
    summaries.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    policy.to_csv(OUT_DIR / "policy_rows.csv", index=False)
    cities.to_csv(OUT_DIR / "city_delta.csv", index=False)
    changed.to_csv(OUT_DIR / "changed_city_days.csv", index=False)
    feature_deltas.to_csv(OUT_DIR / "feature_delta_summary.csv", index=False)
    margins.to_csv(OUT_DIR / "selection_margin_rows.csv", index=False)
    margin_stats.to_csv(OUT_DIR / "selection_margin_summary.csv", index=False)
    coverage.to_csv(OUT_DIR / "feature_coverage_summary.csv", index=False)
    transitions.to_csv(OUT_DIR / "transition_summary.csv", index=False)
    expression_transitions.to_csv(OUT_DIR / "expression_transition_summary.csv", index=False)
    counterfactual.to_csv(OUT_DIR / "counterfactual_selected_rows.csv", index=False)
    counterfactual_stats.to_csv(OUT_DIR / "counterfactual_summary.csv", index=False)
    counterfactual_lost.to_csv(OUT_DIR / "counterfactual_lost_expression_summary.csv", index=False)
    score_delta_rows.to_csv(OUT_DIR / "probability_score_delta_rows.csv", index=False)
    score_slices.to_csv(OUT_DIR / "probability_score_slice_summary.csv", index=False)
    city_scores.to_csv(OUT_DIR / "city_probability_score_summary.csv", index=False)
    expression_scores.to_csv(OUT_DIR / "expression_probability_score_summary.csv", index=False)

    forward_summary = summaries[summaries["scope"] == "verified_forward"].copy()
    score_forward = scores[scores["scope"] == "verified_forward"].copy()
    top_city_negative = cities.head(12)
    top_city_positive = cities.tail(12).sort_values("delta_pnl", ascending=False)
    supported_feature_deltas = feature_deltas[(feature_deltas["city_days"] >= 10) & (feature_deltas["dates"] >= 5)]
    top_feature_negative = supported_feature_deltas.nsmallest(18, "delta_pnl")
    top_feature_positive = supported_feature_deltas.nlargest(12, "delta_pnl")
    supported_score_slices = score_slices[(score_slices["rows"] >= 50) & (score_slices["dates"] >= 8)]
    worsened_score_slices = supported_score_slices.nlargest(12, "delta_logloss")
    improved_score_slices = supported_score_slices.nsmallest(12, "delta_logloss")
    city_score_worse = city_scores.nlargest(10, "delta_logloss")
    city_score_better = city_scores.nsmallest(10, "delta_logloss")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": {
            "hist_rows": len(hist),
            "date_range": [str(hist["target_date"].min()), str(hist["target_date"].max())],
            "variants": list(VARIANT_MASKS),
            "diagnostic_variant": INTERACTION,
            "forward_policy_rows": len(forward_policy),
            "changed_city_days": len(changed),
        },
        "model_meta": model_meta,
        "p4_counters": counters,
        "paired_roi_delta": paired_delta,
        "verdict": "feature_repair_valid_expression_selector_needs_recalibration",
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    report = [
        "# Tmax Repair Anomaly Attribution v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> Frozen expanding model and execution policy. Feature groups are restored one at a time; no new gate, city exclusion, or threshold search.",
        "",
        "## 结论",
        "",
        "- 修复后的 selected ROI 从 12.0% 降到 8.1%，但 paired date-block delta 为 "
        f"{paired_delta['delta']:+.1%}，95% CI [{paired_delta['ci_low']:+.1%}, {paired_delta['ci_high']:+.1%}]，不能证明真实 edge 下降。",
        "- 这次历史对比实际只检验了 humidity/sky：6/21+ 的 GFS/ECMWF gap 覆盖率为 0%，所以 forecast-gap 修复尚无 forward ROI 证据。",
        "- humidity/sky 让分布 logloss 从 0.5926 改善到 0.5887，但 first-lock max-edge 选单把小概率变化离散成取消/新增/换表达；概率模型改善不等于当前 selector 的 ROI 必然改善。",
        "- 收益下降主要来自取消旧机会和新增弱机会，不来自换表达：old_only -5.22 PnL、full_only -1.29、switched_expression +1.45。",
        "- 负向归因集中在 RH 60-80%、cloud suppression / humid convective、late morning / solar peak、forecast capped；dry heat / open runway 改善。城市差异样本太薄，不支持城市 blacklist。",
        "- `mechanism_interactions_full` 只增加 cloud×solar/path 与 humidity×runway 交互，并冻结 C/alpha/执行阈值；它是本轮事后机制诊断，不是独立 forward 或 live 候选。",
        "- 该交互诊断版没有改善：forward logloss 0.5891（full 0.5887），selected ROI 6.0%（full 8.1%）。因此不采纳该改法。",
        "",
        "## Feature Coverage Audit",
        "",
        markdown_table(coverage, ["scope", "feature", "rows", "non_null_rows", "coverage", "first_date", "last_date"]),
        "",
        "## Feature-group Probability Scores",
        "",
        markdown_table(score_forward, ["variant", "rows", "dates", "logloss", "brier"]),
        "",
        "## Feature-group Selected Policy",
        "",
        markdown_table(forward_summary, ["variant", "rows", "dates", "cities", "win_rate", "avg_ask", "pnl", "roi", "roi_ci_low", "roi_ci_high", "yes_rows", "no_rows"]),
        "",
        "## Selection Transition Attribution",
        "",
        markdown_table(transitions, ["transition", "rows", "dates", "old_pnl", "full_pnl", "delta_pnl"]),
        "",
        "`old_only/full_only` 表示同一 city-day 只在一个版本跨过 edge 门；`switched_expression` 才是真正换方向/换档。",
        "",
        "## Same-state Same-expression Counterfactual",
        "",
        markdown_table(counterfactual_stats, list(counterfactual_stats.columns)),
        "",
        "下面是旧版机会在补入 humidity/sky 后，于同一时点、同一表达跌破 edge 门的明细汇总：",
        "",
        markdown_table(counterfactual_lost, list(counterfactual_lost.columns)),
        "",
        "## Expression-level Probability Skill",
        "",
        markdown_table(expression_scores, list(expression_scores.columns)),
        "",
        "全量 expression score 与 edge 门附近 selected slice 是两层问题；下一版应做 date-block cross-fitted expression calibration，而不是把全局 logloss 改善直接当作可交易 EV。",
        "",
        "## Probability Skill by Weather State",
        "",
        "`delta_logloss < 0` 表示补入 humidity/sky 后概率更准；这里使用全部 forward state，不受交易阈值和选单数量影响。",
        "",
        "### Worsened slices",
        "",
        markdown_table(worsened_score_slices, ["feature", "value", "rows", "dates", "cities", "old_logloss", "full_logloss", "delta_logloss", "delta_brier"]),
        "",
        "### Improved slices",
        "",
        markdown_table(improved_score_slices, ["feature", "value", "rows", "dates", "cities", "old_logloss", "full_logloss", "delta_logloss", "delta_brier"]),
        "",
        "## Argmax Stability",
        "",
        markdown_table(margin_stats, list(margin_stats.columns)),
        "",
        "`top_second_margin` is the fee-adjusted edge difference between the selected expression and runner-up at the first eligible city-day state.",
        "",
        "## City Delta: Most Negative",
        "",
        markdown_table(top_city_negative, ["city", "active_dates", f"{OLD}_rows", f"{OLD}_roi", f"{FULL}_rows", f"{FULL}_roi", "delta_pnl"]),
        "",
        "## City Delta: Most Positive",
        "",
        markdown_table(top_city_positive, ["city", "active_dates", f"{OLD}_rows", f"{OLD}_roi", f"{FULL}_rows", f"{FULL}_roi", "delta_pnl"]),
        "",
        "## City Probability Skill Delta",
        "",
        "### Most worsened",
        "",
        markdown_table(city_score_worse, ["city", "rows", "dates", "old_logloss", "full_logloss", "delta_logloss", "delta_brier"]),
        "",
        "### Most improved",
        "",
        markdown_table(city_score_better, ["city", "rows", "dates", "old_logloss", "full_logloss", "delta_logloss", "delta_brier"]),
        "",
        "## Mechanism Slices: Negative Delta",
        "",
        markdown_table(top_feature_negative, ["feature", "value", "city_days", "dates", "cities", "delta_pnl", "avg_delta_pnl"]),
        "",
        "## Mechanism Slices: Positive Delta",
        "",
        markdown_table(top_feature_positive, ["feature", "value", "city_days", "dates", "cities", "delta_pnl", "avg_delta_pnl"]),
        "",
        "## Boundaries",
        "",
        "- GFS/ECMWF gap 在 6/21+ canonical atlas 中完全缺失；7/4 后只保存了每城配置模型的 PIT hourly curve，不能诚实重建双模型 gap 的完整 6/21+ 分母。",
        "- Historical full-ladder/fresh direct YES data remain unavailable before the repair; this study attributes feature parity only.",
        "- Slices share city-days and are not independent multiple tests. They identify mechanisms for a preregistered selector experiment, not whitelist/gate approval.",
        "- `live_real` fill coverage gate currently fails on historical over-order/cache mismatch; no live PnL is used here.",
        "",
        "## Three Gates",
        "",
        f"- significance: FAIL for ROI delta; paired 95% CI [{paired_delta['ci_low']:+.1%}, {paired_delta['ci_high']:+.1%}] crosses zero.",
        "- baseline: same frozen old-missing policy is the A/B baseline.",
        "- forward: 2026-06-21+ expanding forward; complete-ladder post-repair forward remains pending.",
        "- conclusion: feature parity remains mandatory; current max-edge selector is `shadow_candidate` for recalibration; every city/regime exclusion remains `inconclusive`.",
    ]
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
