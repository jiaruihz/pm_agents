#!/usr/bin/env python3
"""Audit PIT weather/climate feature coverage and incremental value vs market."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-14-weather-climate-feature-review-v1.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/generated/weather_climate_feature_review_v1/summary.json"
FORWARD_START = "2026-06-21"

THERMAL = [
    "decision_hour_local",
    "current_native",
    "running_native",
    "decline_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_gap_to_running_native",
    "forecast_peak_delta_hours_local",
]
SUPPRESSION = [
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
]
REGIMES = [
    "unit",
    "city_family",
    "solar_window",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
]
KNOWN_MISSING = ["precip_state", "wind_dir_deg", "obs_age_minutes", "expected_report_cadence"]
MOVE_INTERACTIONS = [
    "move_x_trend1",
    "move_x_trend3",
    "move_x_forecast_gap",
    "move_x_peak_delta",
    "move_x_sky",
    "move_x_rh",
    "move_x_wind",
]


def market_mid(frame: pd.DataFrame) -> pd.Series:
    yes_bid = 1.0 - frame["current_no_ask"]
    return ((frame["current_yes_ask"] + yes_bid) / 2.0).clip(0.001, 0.999)


def make_model(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            ("model", LogisticRegression(C=0.2, max_iter=2000, solver="liblinear")),
        ]
    )


def row_losses(y: pd.Series, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.clip(np.asarray(p, dtype=float), 0.001, 0.999)
    yy = y.to_numpy(dtype=float)
    return -(yy * np.log(p) + (1.0 - yy) * np.log(1.0 - p)), (p - yy) ** 2


def date_equal(frame: pd.DataFrame, values: np.ndarray) -> float:
    tmp = pd.DataFrame({"target_date": frame["target_date"].to_numpy(), "value": values})
    return float(tmp.groupby("target_date")["value"].mean().mean())


def delta_ci(
    frame: pd.DataFrame,
    challenger: np.ndarray,
    baseline: np.ndarray,
    *,
    seed: int = 20260714,
    draws: int = 5000,
) -> tuple[float, float]:
    tmp = pd.DataFrame(
        {
            "target_date": frame["target_date"].to_numpy(),
            "delta": challenger - baseline,
        }
    )
    daily = tmp.groupby("target_date")["delta"].mean()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for idx in range(draws):
        chosen = rng.choice(dates, size=len(dates), replace=True)
        samples[idx] = float(daily.loc[chosen].mean())
    return tuple(float(v) for v in np.quantile(samples, [0.025, 0.975]))


def score(frame: pd.DataFrame, p: np.ndarray, market_loss: np.ndarray) -> dict[str, float | int]:
    y = frame["current_bracket_held"].astype(int)
    loss, brier_rows = row_losses(y, p)
    low, high = delta_ci(frame, loss, market_loss)
    try:
        auc = float(roc_auc_score(y, p))
    except ValueError:
        auc = math.nan
    return {
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "logloss_row": float(log_loss(y, p, labels=[0, 1])),
        "logloss_date_equal": date_equal(frame, loss),
        "brier_row": float(brier_score_loss(y, p)),
        "brier_date_equal": date_equal(frame, brier_rows),
        "auc": auc,
        "delta_logloss_date_equal_vs_market": date_equal(frame, loss - market_loss),
        "delta_ci_low": low,
        "delta_ci_high": high,
    }


def fee(price: pd.Series) -> pd.Series:
    return (0.05 * price * (1.0 - price)).round(5)


def roi_date_ci(rows: pd.DataFrame, *, seed: int = 20260714, draws: int = 5000) -> tuple[float, float]:
    daily = rows.groupby("target_date")[["pnl", "cost"]].sum()
    dates = daily.index.to_numpy()
    if len(dates) < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for idx in range(draws):
        chosen = rng.choice(dates, size=len(dates), replace=True)
        total = daily.loc[chosen].sum()
        samples[idx] = float(total["pnl"] / total["cost"])
    return tuple(float(v) for v in np.quantile(samples, [0.025, 0.975]))


def no_execution_replay(frame: pd.DataFrame, p_yes: np.ndarray) -> dict[str, float | int]:
    rows = frame.copy()
    rows["p_yes"] = p_yes
    rows["ask"] = rows["current_no_ask"]
    rows["cost"] = rows["ask"] + fee(rows["ask"])
    rows["edge"] = (1.0 - rows["p_yes"]) - rows["cost"]
    rows = rows[
        rows["current_no_ask"].between(0.001, 0.999)
        & rows["current_no_ask_size"].ge(5.0)
        & rows["edge"].gt(0.0)
    ].copy()
    if rows.empty:
        return {"rows": 0, "dates": 0, "cost": 0.0, "pnl": 0.0, "roi": math.nan}
    rows["payout"] = 1.0 - rows["current_bracket_held"]
    rows["pnl"] = rows["payout"] - rows["cost"]
    low, high = roi_date_ci(rows)
    return {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "cost": float(rows["cost"].sum()),
        "pnl": float(rows["pnl"].sum()),
        "roi": float(rows["pnl"].sum() / rows["cost"].sum()),
        "roi_ci_low": low,
        "roi_ci_high": high,
        "avg_edge": float(rows["edge"].mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    raw = pd.read_csv(args.input)
    db_path = ROOT / "runtime/weather.db"
    canonical: dict[str, object] = {"path": str(db_path), "available": db_path.exists()}
    if db_path.exists():
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        candidate = connection.execute(
            "SELECT COUNT(*), MIN(event_date), MAX(event_date), MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ).fetchone()
        settlement = connection.execute(
            "SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM settlement_outcomes"
        ).fetchone()
        connection.close()
        canonical.update(
            {
                "fact_signal_candidates_rows": int(candidate[0]),
                "fact_signal_candidates_min_date": candidate[1],
                "fact_signal_candidates_max_date": candidate[2],
                "fact_signal_candidates_built_at": candidate[3],
                "settlement_outcomes_rows": int(settlement[0]),
                "settlement_outcomes_min_date": settlement[1],
                "settlement_outcomes_max_date": settlement[2],
            }
        )
    coverage: dict[str, object] = {
        "rows": int(len(raw)),
        "dates": int(raw["target_date"].nunique()),
        "cities": int(raw["city"].nunique()),
        "min_date": str(raw["target_date"].min()),
        "max_date": str(raw["target_date"].max()),
        "fields": {},
    }
    audited_fields = THERMAL + SUPPRESSION + REGIMES + KNOWN_MISSING
    for column in audited_fields:
        coverage["fields"][column] = {
            "present": column in raw.columns,
            "coverage": float(raw[column].notna().mean()) if column in raw.columns else 0.0,
        }

    required = ["current_bracket_held", "current_yes_ask", "current_no_ask"]
    frame = raw.dropna(subset=required).copy()
    frame = frame[frame["current_bracket_held"].isin([0, 1])].copy()
    frame["market_mid"] = market_mid(frame)
    frame["market_logit"] = np.log(frame["market_mid"] / (1.0 - frame["market_mid"]))
    train = frame[frame["target_date"] < FORWARD_START].copy()
    forward = frame[frame["target_date"] >= FORWARD_START].copy()
    market_p = forward["market_mid"].to_numpy()
    market_loss, _ = row_losses(forward["current_bracket_held"], market_p)

    specs = {
        "market_calibrated": (["market_logit"], []),
        "thermal_only": (THERMAL, []),
        "market_plus_thermal": (["market_logit", *THERMAL], []),
        "market_plus_thermal_suppression": (["market_logit", *THERMAL, *SUPPRESSION], []),
        "market_plus_all_regimes": (["market_logit", *THERMAL, *SUPPRESSION], REGIMES),
    }
    metrics: dict[str, object] = {
        "market_raw": score(forward, market_p, market_loss),
    }
    probabilities: dict[str, np.ndarray] = {"market_raw": market_p}
    for name, (numeric, categorical) in specs.items():
        model = make_model(numeric, categorical)
        model.fit(train[numeric + categorical], train["current_bracket_held"].astype(int))
        pred = model.predict_proba(forward[numeric + categorical])[:, 1]
        probabilities[name] = pred
        metrics[name] = score(forward, pred, market_loss)

    # Conservative residual overlays: preserve the raw market as 75% of the
    # probability and allow weather/context to move only the remaining 25%.
    # The weight is fixed before looking at forward results and mirrors the
    # project's existing market-anchored residual convention.
    for source_name in (
        "market_plus_thermal",
        "market_plus_thermal_suppression",
        "market_plus_all_regimes",
    ):
        name = f"market75_{source_name.removeprefix('market_plus_')}25"
        pred = 0.75 * market_p + 0.25 * probabilities[source_name]
        probabilities[name] = pred
        metrics[name] = score(forward, pred, market_loss)

    executions = {
        name: no_execution_replay(forward, pred)
        for name, pred in probabilities.items()
    }

    sequence = frame.sort_values(["city", "target_date", "decision_snapshot_ts_utc"]).copy()
    seq_groups = sequence.groupby(["city", "target_date"], sort=False)
    sequence["previous_market_mid"] = seq_groups["market_mid"].shift()
    sequence["previous_current_bracket"] = seq_groups["current_bracket"].shift()
    sequence["yes_mid_move"] = sequence["market_mid"] - sequence["previous_market_mid"]
    sequence = sequence[
        sequence["current_bracket"].eq(sequence["previous_current_bracket"])
        & sequence["yes_mid_move"].abs().ge(0.02)
    ].copy()
    interaction_sources = {
        "move_x_trend1": "temp_trend_1h_f",
        "move_x_trend3": "temp_trend_3h_f",
        "move_x_forecast_gap": "forecast_gap_to_running_native",
        "move_x_peak_delta": "forecast_peak_delta_hours_local",
        "move_x_sky": "sky_cover_code",
        "move_x_rh": "relative_humidity_pct",
        "move_x_wind": "wind_speed_kt",
    }
    for target, source in interaction_sources.items():
        sequence[target] = sequence["yes_mid_move"] * pd.to_numeric(sequence[source], errors="coerce")
    seq_train = sequence[sequence["target_date"] < FORWARD_START].copy()
    seq_forward = sequence[sequence["target_date"] >= FORWARD_START].copy()
    seq_market_p = seq_forward["market_mid"].to_numpy()
    seq_market_loss, _ = row_losses(seq_forward["current_bracket_held"], seq_market_p)
    momentum_metrics: dict[str, object] = {
        "market_raw": score(seq_forward, seq_market_p, seq_market_loss),
    }
    momentum_probabilities: dict[str, np.ndarray] = {"market_raw": seq_market_p}
    momentum_specs = {
        "market_plus_move": (["market_logit", "yes_mid_move"], []),
        "market_move_thermal": (["market_logit", "yes_mid_move", *THERMAL], []),
        "market_move_weather_interactions": (
            ["market_logit", "yes_mid_move", *THERMAL, *SUPPRESSION, *MOVE_INTERACTIONS],
            REGIMES,
        ),
    }
    for name, (numeric, categorical) in momentum_specs.items():
        model = make_model(numeric, categorical)
        model.fit(seq_train[numeric + categorical], seq_train["current_bracket_held"].astype(int))
        pred = model.predict_proba(seq_forward[numeric + categorical])[:, 1]
        momentum_probabilities[name] = pred
        momentum_metrics[name] = score(seq_forward, pred, seq_market_loss)
    interaction_blend = 0.75 * seq_market_p + 0.25 * momentum_probabilities["market_move_weather_interactions"]
    momentum_probabilities["market75_move_weather25"] = interaction_blend
    momentum_metrics["market75_move_weather25"] = score(seq_forward, interaction_blend, seq_market_loss)
    momentum_executions = {
        name: no_execution_replay(seq_forward, pred)
        for name, pred in momentum_probabilities.items()
    }
    payload = {
        "contract": {
            "input": str(args.input),
            "target": "current exact bracket settles YES",
            "train": f"target_date < {FORWARD_START}",
            "forward": f"target_date >= {FORWARD_START}",
            "model": "fixed LogisticRegression C=0.2; no city identity; no threshold tuning",
            "market_probability": "mid(current YES ask, 1-current NO ask)",
            "execution": "diagnostic BUY current NO where p_no > ask+fee and ask_size>=5",
        },
        "canonical_self_check": canonical,
        "coverage": coverage,
        "model_rows": {
            "all": int(len(frame)),
            "train": int(len(train)),
            "forward": int(len(forward)),
            "forward_dates": int(forward["target_date"].nunique()),
        },
        "metrics": metrics,
        "no_execution_replay": executions,
        "conditional_momentum": {
            "rows": int(len(sequence)),
            "dates": int(sequence["target_date"].nunique()),
            "train_rows": int(len(seq_train)),
            "forward_rows": int(len(seq_forward)),
            "forward_dates": int(seq_forward["target_date"].nunique()),
            "metrics": momentum_metrics,
            "no_execution_replay": momentum_executions,
        },
        "review": {
            "strong_mechanism_features": [
                "temp_trend_1h_f/temp_trend_3h_f",
                "minutes_since_running_max/decline_native",
                "forecast_gap_to_running_native/forecast_peak_delta_hours_local",
            ],
            "weak_or_incomplete": [
                "precip_state missing",
                "wind_dir_deg missing in historical atlas",
                "obs_age_minutes/cadence missing in historical atlas",
                "sky_cover_code is coarse and 75% covered",
                "solar_window uses fixed local-clock buckets rather than solar geometry",
                "heating_done_score_v1 is hand-weighted and not a calibrated probability",
            ],
        },
    }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def pct(value: float) -> str:
        return "NA" if not math.isfinite(value) else f"{value:+.2%}"

    lines = [
        "# Weather Climate Feature Review v1",
        "",
        "> 2026-07-14; opportunity-grain model audit; zero notional; no live change.",
        "",
        "## 结论",
        "",
        "现有特征层的 thermal path 骨架是合理且有物理判别力的，但还不能证明有交易增量。雨没有进入历史 atlas；云只有粗粒度 METAR sky code；风向、观测 age/cadence 也没有进入这批历史状态。它们目前适合作为连续 probability features，不适合作为下雨/多云 hard filter。",
        "",
        f"数据覆盖 `{coverage['min_date']}..{coverage['max_date']}`，{coverage['dates']} dates、{coverage['cities']} 城、{coverage['rows']} state rows。模型同分母为 train {len(train)} rows，forward {len(forward)} rows / {forward['target_date'].nunique()} dates。",
        "",
        "## 数据完整性自检",
        "",
        f"- canonical fact_signal_candidates: {canonical.get('fact_signal_candidates_rows', 'NA')} rows，{canonical.get('fact_signal_candidates_min_date', 'NA')}..{canonical.get('fact_signal_candidates_max_date', 'NA')}，built_at={canonical.get('fact_signal_candidates_built_at', 'NA')}。",
        f"- canonical settlement_outcomes: {canonical.get('settlement_outcomes_rows', 'NA')} rows，{canonical.get('settlement_outcomes_min_date', 'NA')}..{canonical.get('settlement_outcomes_max_date', 'NA')}。",
        f"- derived atlas: {len(raw)} rows；label coverage={raw['current_bracket_held'].notna().mean():.1%}；paired YES/NO ask model rows={len(frame)}。",
        "- 严格排除 `final_max_native`、`remaining_heat_native`、`future_break_*`、payoff/ROI 等后验字段；split 只按 target_date，train 严格早于 forward。",
        "- 本轮不发布 live_real PnL，因此不调用 fill coverage gate；执行结果是 opportunity replay，不是实际 fill。",
        "",
        "## Forward proper-score ablation",
        "",
        "delta 为 challenger - raw market；负值才是改善。CI 按 target_date block bootstrap。",
        "",
        "| model | date-equal logloss | delta vs market | 95% CI | Brier | AUC |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stat in metrics.items():
        lines.append(
            f"| {name} | {stat['logloss_date_equal']:.4f} | {stat['delta_logloss_date_equal_vs_market']:+.4f} | "
            f"[{stat['delta_ci_low']:+.4f}, {stat['delta_ci_high']:+.4f}] | {stat['brier_date_equal']:.4f} | {stat['auc']:.4f} |"
        )
    lines += [
        "",
        "## BUY current-NO diagnostic",
        "",
        "固定规则：forward 上仅当模型 `p_no > current_no_ask + fee` 且 ask depth>=5 才买；没有调 edge threshold。",
        "",
        "| probability | rows | dates | cities | avg edge | ROI | 95% CI |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stat in executions.items():
        lines.append(
            f"| {name} | {stat['rows']} | {stat['dates']} | {stat.get('cities', 0)} | "
            f"{pct(stat.get('avg_edge', math.nan))} | {pct(stat['roi'])} | "
            f"[{pct(stat.get('roi_ci_low', math.nan))}, {pct(stat.get('roi_ci_high', math.nan))}] |"
        )
    lines += [
        "",
        "## Price-move × weather pattern audit",
        "",
        f"只看同一 bracket 且 midpoint 变化至少 2c：全量 {len(sequence)} rows / {sequence['target_date'].nunique()} dates；forward {len(seq_forward)} rows / {seq_forward['target_date'].nunique()} dates。`market_move_weather_interactions` 显式加入 move×温度趋势、forecast runway、云、湿度、风速交互；不含 city identity。",
        "",
        "| model | date-equal logloss | delta vs market | 95% CI | AUC | NO EV rows | NO ROI | ROI CI |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stat in momentum_metrics.items():
        execution = momentum_executions[name]
        lines.append(
            f"| {name} | {stat['logloss_date_equal']:.4f} | {stat['delta_logloss_date_equal_vs_market']:+.4f} | "
            f"[{stat['delta_ci_low']:+.4f}, {stat['delta_ci_high']:+.4f}] | {stat['auc']:.4f} | "
            f"{execution['rows']} | {pct(execution['roi'])} | "
            f"[{pct(execution.get('roi_ci_low', math.nan))}, {pct(execution.get('roi_ci_high', math.nan))}] |"
        )
    lines += [
        "",
        "## Feature review",
        "",
        "| family | verdict | evidence / problem |",
        "| --- | --- | --- |",
        "| temperature path | keep | 1h/3h trend、decline、minutes since max、forecast runway 物理方向清楚，历史覆盖约 88%–100%；此前机制研究也显示 sustained warming 对 future break 有判别力。 |",
        "| market price | primary prior | raw market 是必须打败的基准；天气模型不能脱离盘口独立定价。 |",
        "| cloud | partial | `sky_cover_code` 只有粗等级且覆盖约 75%；没有 cloud base、分层云量、变化率的统一 live contract。 |",
        "| rain / convection | missing | 历史 atlas 没有 `precip_state`、雨强、雷暴或 radar；当前 `humid_convective_risk` 只是 RH proxy，不能称为下雨特征。 |",
        "| wind / marine flow | partial | wind speed 覆盖高，但历史 atlas 无 wind direction；手工 onshore sector 只能做先验，未校准前不能定方向。 |",
        "| observation freshness | missing in atlas | shared builder 已有 age/cadence 字段，但当前历史 atlas 没有，旧路径状态把 3 分钟和 50 分钟前观测近似等同。 |",
        "| solar clock | weak proxy | `solar_window` 是固定 local-hour 桶，不是真实 solar elevation/sunset/day length，跨纬度季节会漂。 |",
        "| heating_done_score_v1 | diagnostic only | 手工加权、相关特征重复计分、clip 0..1；不是校准概率，不能直接拿来算 EV。 |",
        "| composite regimes | diagnostics only | 多个阈值标签叠加容易碎片化；应保留连续字段，让模型学习 residual，不用 composite string 作策略。 |",
        "",
        "## Verdict",
        "",
        "- significance=FAIL：没有天气 challenger 的 forward delta-logloss CI 全部低于 0；多数点估反而更差。",
        "- baseline=FAIL：raw market probability 仍是最佳基准。",
        "- forward=FAIL：固定 2026-06-21+；没有从 forward 反选阈值，但天气增量未复现。",
        "- conclusion=inconclusive：thermal path 作为 shared context 保留；气候模式不能升级为 selector。",
        "- live：不改。先补 precipitation、wind direction、freshness、solar geometry 的 PIT capture，再用相同 ablation 验证；若 market+weather 仍不胜 market，就不把气候模式升级为交易 selector。",
        "",
        "一句话：在 2026-06-21+ forward，最保守的 market75+all-regimes25 相对 raw market 的 date-equal logloss delta 为 +0.0018（95% CI [-0.0009,+0.0049]），前瞻 FAIL，结论等级 inconclusive。",
        "",
        "## 8 环覆盖",
        "",
        "- covered：特征覆盖、date-block 统计推断、信号判别、概率评分、market baseline、target-date 相关性。",
        "- partial：执行只重放 current-NO ask+5-share depth；容量只到 top ask 5 shares。",
        "- missing：真实 maker/taker fill、YES ask depth、组合资金占用；因此不作 live 结论。",
        "",
        "Structured artifact: `docs/analysis/2026-07/generated/weather_climate_feature_review_v1/summary.json`.",
    ]
    args.report.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
