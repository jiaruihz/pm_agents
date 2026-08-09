#!/usr/bin/env python3
"""Train the market-residual current-YES future-break hazard family.

V3.1 keeps the V3 future-break label, but changes the modeling target:
the model first calibrates the market ask, then lets weather/plateau
features explain only the remaining residual edge.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

if __package__:
    from . import current_yes_future_break_hazard as v3
else:
    import current_yes_future_break_hazard as v3


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v31"
OUT_SCORED = OUT_DIR / "future_break_hazard_v31_scored_rows.csv"
OUT_METRICS = OUT_DIR / "future_break_hazard_v31_model_metrics.csv"
OUT_RULES = OUT_DIR / "future_break_hazard_v31_rule_comparison.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = OUT_DIR / "report.md"

SEED = 20260623
INNER_VALID_START = "2026-05-28"
ALPHAS = [0.3, 1.0, 3.0, 10.0, 30.0, 100.0]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    return v3.json_ready(value)


def pct(value: Any, digits: int = 1) -> str:
    return v3.pct(value, digits)


def num(value: Any, digits: int = 3) -> str:
    return v3.num(value, digits)


def logit(values: pd.Series) -> pd.Series:
    p = pd.to_numeric(values, errors="coerce").clip(1e-5, 1 - 1e-5)
    return np.log(p / (1 - p))


def make_direct_pipeline(numeric_features: list[str], c: float = 0.3) -> Pipeline:
    return v3.make_pipeline(numeric_features, c=c)


def make_residual_pipeline(alpha: float) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
                v3.WEATHER_NUMERIC,
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore"), v3.CAT_FEATURES),
        ]
    )
    return Pipeline([("pre", pre), ("model", Ridge(alpha=alpha, random_state=SEED))])


def fit_market_calibrator(train: pd.DataFrame) -> Pipeline:
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)),
        ]
    )
    model.fit(train[["market_logit"]], train["label_survive"])
    return model


def choose_alpha(train: pd.DataFrame) -> dict[str, Any]:
    inner_train = train[train["target_date"].lt(INNER_VALID_START)].copy()
    inner_valid = train[train["target_date"].ge(INNER_VALID_START)].copy()
    if inner_train.empty or inner_valid.empty or inner_valid["label_survive"].nunique() < 2:
        return {"alpha": 10.0, "rows": [], "reason": "fallback_insufficient_inner_split"}

    rows = []
    for alpha in ALPHAS:
        model = make_residual_pipeline(alpha)
        model.fit(inner_train[v3.WEATHER_NUMERIC + v3.CAT_FEATURES], inner_train["label_survive"] - inner_train["p_market_cal"])
        residual = model.predict(inner_valid[v3.WEATHER_NUMERIC + v3.CAT_FEATURES])
        p = np.clip(inner_valid["p_market_cal"].to_numpy(dtype=float) + residual, 1e-5, 1 - 1e-5)
        rows.append(
            {
                "alpha": alpha,
                "inner_valid_rows": int(len(inner_valid)),
                "inner_valid_dates": int(inner_valid["target_date"].nunique()),
                "brier": float(brier_score_loss(inner_valid["label_survive"], p)),
                "logloss": float(log_loss(inner_valid["label_survive"], p)),
                "mean_residual_pred": float(np.mean(residual)),
            }
        )
    rows.sort(key=lambda r: (r["brier"], r["logloss"]))
    return {"alpha": float(rows[0]["alpha"]), "rows": rows, "reason": "min_inner_valid_brier"}


def model_metrics(frame: pd.DataFrame, model: str, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"model": model, "rows": 0}
    y = frame["label_survive"].to_numpy(dtype=int)
    p = np.clip(frame[p_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    return {
        "model": model,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "actual_survive_rate": float(y.mean()),
        "mean_pred_survive": float(p.mean()),
        "auc_survive": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
        "auc_future_break": float(roc_auc_score(1 - y, 1 - p)) if len(np.unique(y)) > 1 else None,
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)) if len(np.unique(y)) > 1 else None,
        "accuracy_at_0_5": float(accuracy_score(y, pred)),
        "balanced_accuracy_at_0_5": float(balanced_accuracy_score(y, pred)),
        "mean_edge_vs_ask": float((frame[p_col] - frame["current_yes_ask"]).mean()),
    }


def rule_masks(frame: pd.DataFrame) -> dict[str, tuple[pd.Series, str]]:
    tradable = (
        frame["decision_hour_local"].between(12, 18)
        & frame["current_yes_ask"].between(0.35, 0.97, inclusive="both")
        & frame["current_yes_ask_size"].fillna(0).ge(5.0)
    )
    forecast_live = pd.to_numeric(frame["min_forecast_peak_delta_hours_local"], errors="coerce").fillna(-999).ge(-1.0)
    live_like = (
        frame["decision_hour_local"].between(13, 17)
        & frame["current_yes_ask"].between(0.50, 0.97, inclusive="both")
        & forecast_live
    )
    stalled = frame["plateau_obs_count_at_high"].ge(2)
    strict_stalled = stalled & frame["plateau_duration_min"].ge(30.0)
    no_warming = pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce").le(2.0)
    peak_not_ahead = pd.to_numeric(frame["min_forecast_peak_delta_hours_local"], errors="coerce").le(0.0)
    late = frame["decision_hour_local"].between(14, 18)
    edge02 = (frame["p_v31"] - frame["current_yes_ask"]).ge(0.02)
    edge05 = (frame["p_v31"] - frame["current_yes_ask"]).ge(0.05)
    return {
        "market_tradable_all": (tradable, "p_market_raw"),
        "v3_live_like_edge_ge_02": (
            live_like & frame["p_v3_direct"].ge(0.60) & (frame["p_v3_direct"] - frame["current_yes_ask"]).ge(0.02),
            "p_v3_direct",
        ),
        "v31_resid_edge_ge_02": (tradable & frame["p_v31"].ge(0.55) & edge02, "p_v31"),
        "v31_resid_edge_ge_05": (tradable & frame["p_v31"].ge(0.60) & edge05, "p_v31"),
        "v31_live_like_edge_ge_02": (live_like & frame["p_v31"].ge(0.60) & edge02, "p_v31"),
        "v31_strict_stalled_edge_ge_02": (live_like & strict_stalled & frame["p_v31"].ge(0.60) & edge02, "p_v31"),
        "v31_late_no_warming_edge_ge_02": (live_like & late & no_warming & frame["p_v31"].ge(0.60) & edge02, "p_v31"),
        "v31_peak_not_ahead_edge_ge_02": (live_like & peak_not_ahead & frame["p_v31"].ge(0.60) & edge02, "p_v31"),
        "v31_strict_combo_edge_ge_02": (
            live_like & strict_stalled & no_warming & peak_not_ahead & frame["p_v31"].ge(0.60) & edge02,
            "p_v31",
        ),
    }


def grid_select(train: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for min_p in [0.55, 0.60, 0.65, 0.70, 0.75]:
        for min_edge in [0.02, 0.04, 0.06, 0.08, 0.10]:
            for min_hour in [12, 13, 14, 15]:
                for require_stalled in [False, True]:
                    for require_no_warming in [False, True]:
                        mask = (
                            train["decision_hour_local"].between(min_hour, 18)
                            & train["current_yes_ask"].between(0.35, 0.97, inclusive="both")
                            & train["current_yes_ask_size"].fillna(0).ge(5.0)
                            & train["p_v31"].ge(min_p)
                            & (train["p_v31"] - train["current_yes_ask"]).ge(min_edge)
                        )
                        if require_stalled:
                            mask &= train["plateau_obs_count_at_high"].ge(2)
                        if require_no_warming:
                            mask &= pd.to_numeric(train["temp_trend_3h_f"], errors="coerce").le(2.0)
                        sub = train[mask].copy()
                        if len(sub) < 25 or sub["target_date"].nunique() < 5:
                            continue
                        row = v3.summarize_trade(
                            sub,
                            "p_v31",
                            (
                                f"grid_h{min_hour}_p{min_p:.2f}_edge{min_edge:.2f}"
                                f"_stalled{int(require_stalled)}_nowarm{int(require_no_warming)}"
                            ),
                        )
                        row.update(
                            {
                                "min_p": min_p,
                                "min_edge": min_edge,
                                "min_hour": min_hour,
                                "require_stalled": require_stalled,
                                "require_no_warming": require_no_warming,
                            }
                        )
                        rows.append(row)
    rows.sort(key=lambda r: (r["roi"] if r["roi"] is not None else -999, r["active_dates"], r["orders"]), reverse=True)
    return rows[:12]


def apply_grid_rule(frame: pd.DataFrame, grid: dict[str, Any]) -> pd.Series:
    mask = (
        frame["decision_hour_local"].between(int(grid["min_hour"]), 18)
        & frame["current_yes_ask"].between(0.35, 0.97, inclusive="both")
        & frame["current_yes_ask_size"].fillna(0).ge(5.0)
        & frame["p_v31"].ge(float(grid["min_p"]))
        & (frame["p_v31"] - frame["current_yes_ask"]).ge(float(grid["min_edge"]))
    )
    if bool(grid["require_stalled"]):
        mask &= frame["plateau_obs_count_at_high"].ge(2)
    if bool(grid["require_no_warming"]):
        mask &= pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce").le(2.0)
    return mask


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES Future-Break Hazard V3.1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `future_break_hazard_v31` keeps the V3 survive/break label but models only residual edge after market-ask calibration.",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `{payload['data_snapshot']['feature_rows']}` + `runtime/weather.db` self-check.",
        f"- Feature target-date range: `{payload['coverage']['source_min_target_date']}`..`{payload['coverage']['source_max_target_date']}`.",
        f"- Row grain: one peak current-YES state = city + target_date + orderbook snapshot + current running-max bracket.",
        f"- Records: source rows {payload['coverage']['source_rows']}, current-YES rows {payload['coverage']['current_yes_rows']}, peak rows {payload['coverage']['peak_rows']}.",
        f"- Residual model: market logit calibrator + weather/plateau Ridge residual; alpha={payload['model_selection']['selected_alpha']} ({payload['model_selection']['reason']}).",
        f"- DB fact_trades self-check: `{payload['data_self_check']['fact_trades']}`.",
        "",
        "## Funnel",
        "",
        "| step | rows | dates | cities |",
        "|---|---:|---:|---:|",
        f"| source feature rows | {payload['coverage']['source_rows']} | {payload['coverage']['source_dates']} | {payload['coverage']['source_cities']} |",
        f"| current YES rows | {payload['coverage']['current_yes_rows']} | {payload['coverage']['current_yes_dates']} | {payload['coverage']['current_yes_cities']} |",
        f"| peak-forming rows | {payload['coverage']['peak_rows']} | {payload['coverage']['peak_dates']} | {payload['coverage']['peak_cities']} |",
        f"| train peak rows | {payload['coverage']['train_rows']} | {payload['coverage']['train_dates']} | {payload['coverage']['train_cities']} |",
        f"| holdout peak rows | {payload['coverage']['holdout_rows']} | {payload['coverage']['holdout_dates']} | {payload['coverage']['holdout_cities']} |",
        f"| forward-tail rows | {payload['coverage']['forward_tail_rows']} | {payload['coverage']['forward_tail_dates']} | {payload['coverage']['forward_tail_cities']} |",
        "",
        "## Model Accuracy",
        "",
        "| model | rows | actual survive | mean p | AUC survive | Brier | Logloss | accuracy | balanced acc | avg edge |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["holdout_model_metrics"]:
        lines.append(
            f"| {row['model']} | {row['rows']} | {pct(row.get('actual_survive_rate'))} | {pct(row.get('mean_pred_survive'))} | "
            f"{num(row.get('auc_survive'))} | {num(row.get('brier'))} | {num(row.get('logloss'))} | "
            f"{pct(row.get('accuracy_at_0_5'))} | {pct(row.get('balanced_accuracy_at_0_5'))} | {pct(row.get('mean_edge_vs_ask'))} |"
        )
    lines.extend(
        [
            "",
            "## Trading Backtest",
            "",
            "| rule | orders | dates | cities | avg ask | win | ROI | CI | forward rows | forward ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["holdout_rule_comparison"]:
        ci = row.get("bootstrap_roi_ci95") or [None, None]
        lines.append(
            f"| {row['rule']} | {row['orders']} | {row['active_dates']} | {row['cities']} | {num(row.get('avg_ask'))} | "
            f"{pct(row.get('win_rate'))} | {pct(row.get('roi'))} | [{pct(ci[0])}, {pct(ci[1])}] | "
            f"{row.get('forward_tail_orders', 0)} | {pct(row.get('forward_tail_roi'))} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["plain_text"],
            "",
            "## 8-Ring Coverage",
            "",
            "- Covered: descriptive slices, date bootstrap, signal discrimination, probability calibration, market-price baseline, target-date block correlation.",
            "- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask size, post-2026-06-20 complete settled feature rows.",
            "",
            "## Outputs",
            "",
            f"- scored rows: `{payload['outputs']['scored_rows']}`",
            f"- model metrics: `{payload['outputs']['metrics']}`",
            f"- rule comparison: `{payload['outputs']['rule_comparison']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    current_yes_rows = v3.prepare_current_yes_rows()
    rows = v3.prepare_peak_rows(current_yes_rows)
    rows["market_logit"] = logit(rows["current_yes_ask"])

    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()

    market_cal = fit_market_calibrator(train)
    rows["p_market_raw"] = rows["current_yes_ask"].clip(1e-5, 1 - 1e-5)
    rows["p_market_cal"] = market_cal.predict_proba(rows[["market_logit"]])[:, 1]
    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()

    weather_model = make_direct_pipeline(v3.WEATHER_NUMERIC, c=0.3)
    direct_model = make_direct_pipeline(v3.WEATHER_NUMERIC + v3.MARKET_NUMERIC, c=0.3)
    weather_model.fit(train[v3.WEATHER_NUMERIC + v3.CAT_FEATURES], train["label_survive"])
    direct_model.fit(train[v3.WEATHER_NUMERIC + v3.MARKET_NUMERIC + v3.CAT_FEATURES], train["label_survive"])
    rows["p_weather_direct"] = weather_model.predict_proba(rows[v3.WEATHER_NUMERIC + v3.CAT_FEATURES])[:, 1]
    rows["p_v3_direct"] = direct_model.predict_proba(rows[v3.WEATHER_NUMERIC + v3.MARKET_NUMERIC + v3.CAT_FEATURES])[:, 1]

    train = rows[rows["period"].eq("train")].copy()
    alpha_choice = choose_alpha(train)
    residual_model = make_residual_pipeline(float(alpha_choice["alpha"]))
    residual_model.fit(train[v3.WEATHER_NUMERIC + v3.CAT_FEATURES], train["label_survive"] - train["p_market_cal"])
    rows["residual_v31"] = residual_model.predict(rows[v3.WEATHER_NUMERIC + v3.CAT_FEATURES])
    rows["p_v31"] = np.clip(rows["p_market_cal"] + rows["residual_v31"], 1e-5, 1 - 1e-5)
    rows["p_future_break_v31"] = 1.0 - rows["p_v31"]
    rows["edge_v31"] = rows["p_v31"] - rows["current_yes_ask"]
    rows.to_csv(OUT_SCORED, index=False)

    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()
    metrics = pd.DataFrame(
        [
            model_metrics(holdout, "market_price_as_probability", "p_market_raw"),
            model_metrics(holdout, "market_logit_calibrated", "p_market_cal"),
            model_metrics(holdout, "weather_only_direct_v3_shape", "p_weather_direct"),
            model_metrics(holdout, "market_plus_weather_direct_v3_shape", "p_v3_direct"),
            model_metrics(holdout, "v31_market_cal_plus_weather_residual", "p_v31"),
        ]
    )
    metrics.to_csv(OUT_METRICS, index=False)

    rule_rows = []
    for name, (mask, p_col) in rule_masks(holdout).items():
        rule_rows.append(v3.summarize_trade(holdout[mask].copy(), p_col, name))
    grid_rows = grid_select(train)
    for grid in grid_rows[:4]:
        mask = apply_grid_rule(holdout, grid)
        rule_rows.append(v3.summarize_trade(holdout[mask].copy(), "p_v31", "train_selected_" + str(grid["rule"])))
    pd.DataFrame(rule_rows).to_csv(OUT_RULES, index=False)

    primary = next(row for row in rule_rows if row["rule"] == "v31_live_like_edge_ge_02")
    ci = primary.get("bootstrap_roi_ci95") or [None, None]
    significance_pass = bool(ci[0] is not None and ci[0] > 0)
    baseline_pass = bool(primary["roi"] is not None and primary["roi"] > 0 and primary["avg_edge"] is not None and primary["avg_edge"] > 0)
    forward_pass = bool(
        primary["forward_tail_orders"] >= 10
        and primary["forward_tail_roi"] is not None
        and primary["forward_tail_roi"] > 0
    )
    conclusion = "confirmed" if significance_pass and baseline_pass and forward_pass else "inconclusive"

    source = pd.read_csv(v3.FEATURE_ROWS, usecols=["city", "target_date"])
    factory = json.loads(v3.FACTORY_SUMMARY.read_text(encoding="utf-8"))
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_yes_future_break_hazard_v31_residual",
        "data_snapshot": {
            "feature_rows": str(v3.FEATURE_ROWS.relative_to(ROOT)),
            "factory_summary": str(v3.FACTORY_SUMMARY.relative_to(ROOT)),
            "factory_generated_at_utc": factory.get("generated_at_utc"),
            "train_end": v3.TRAIN_END,
            "holdout_start": v3.HOLDOUT_START,
            "forward_tail_start": v3.FORWARD_START,
        },
        "data_self_check": v3.data_self_check(),
        "model_selection": {
            "selected_alpha": float(alpha_choice["alpha"]),
            "reason": alpha_choice["reason"],
            "inner_validation": alpha_choice["rows"],
        },
        "coverage": {
            "source_rows": int(len(source)),
            "source_min_target_date": str(source["target_date"].min()),
            "source_max_target_date": str(source["target_date"].max()),
            "source_dates": int(source["target_date"].nunique()),
            "source_cities": int(source["city"].nunique()),
            "current_yes_rows": int(len(current_yes_rows)),
            "current_yes_dates": int(current_yes_rows["target_date"].nunique()),
            "current_yes_cities": int(current_yes_rows["city"].nunique()),
            "peak_rows": int(len(rows)),
            "peak_dates": int(rows["target_date"].nunique()),
            "peak_cities": int(rows["city"].nunique()),
            "train_rows": int(len(train)),
            "train_dates": int(train["target_date"].nunique()),
            "train_cities": int(train["city"].nunique()),
            "holdout_rows": int(len(holdout)),
            "holdout_dates": int(holdout["target_date"].nunique()),
            "holdout_cities": int(holdout["city"].nunique()),
            "forward_tail_rows": int((holdout["target_date"] >= v3.FORWARD_START).sum()),
            "forward_tail_dates": int(holdout.loc[holdout["target_date"] >= v3.FORWARD_START, "target_date"].nunique()),
            "forward_tail_cities": int(holdout.loc[holdout["target_date"] >= v3.FORWARD_START, "city"].nunique()),
        },
        "holdout_model_metrics": metrics.to_dict(orient="records"),
        "holdout_rule_comparison": rule_rows,
        "train_selected_grid_top12": grid_rows,
        "verdict": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "conclusion": conclusion,
            "shadow": "telemetry_only" if conclusion == "inconclusive" else "shadow_candidate",
            "live": "no_live_change",
            "plain_text": (
                f"在 {v3.HOLDOUT_START}..{source['target_date'].max()} holdout，V3.1 residual live-like edge>=0.02 "
                f"规则 ROI 为 {pct(primary['roi'])}，95% 日期 bootstrap CI [{pct(ci[0])}, {pct(ci[1])}]；"
                f"forward-tail 自 {v3.FORWARD_START} 起 {primary['forward_tail_orders']} rows，ROI {pct(primary['forward_tail_roi'])}。"
                "未同时通过显著性、基准、前瞻三门，不能上 live。"
            ),
        },
        "outputs": {
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "metrics": str(OUT_METRICS.relative_to(ROOT)),
            "rule_comparison": str(OUT_RULES.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(json_ready(payload))
    print(json.dumps(json_ready(payload["verdict"]), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
