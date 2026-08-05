#!/usr/bin/env python3
"""Develop a robust-tail D-1 weather-only challenger on legacy evidence.

This experiment keeps the v2 ensemble location mechanism and changes only the
weather distribution: a global residual scale temperature plus a small
climatology mixture prevent overconfident or near-zero native-rung mass.  The
first 18 reconstructed target dates select parameters.  The final 9 dates are
reported as a previously observed secondary holdout, never as clean forward.
The frozen weather distribution is also tested as a strongly regularized
log-probability offset from the same-row market baseline.  Market is never used
by the weather-only arm or its parameter selection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base  # noqa: E402
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as v2  # noqa: E402


DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/d1_legacy_weather_only_robust_tail"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-05-d1-weather-only-robust-tail-v1.md"
WEIGHTS = (0.50, 0.625, 0.75, 0.875, 1.0)
SCALE_TEMPERATURES = (0.75, 1.0, 1.25, 1.5, 2.0)
CLIMATE_MIXES = (0.0, 0.02, 0.05, 0.10)
CONSENSUS_STATS = ("median", "mean")
BIAS_MULTIPLIERS = (0.0, 0.5, 1.0)
MARKET_OFFSET_BETAS = (0.0, 0.025, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.0)


def _climate_values(state: dict[str, Any], fitted: dict[str, Any]) -> np.ndarray:
    climate = fitted["climatology"]
    month = int(pd.Timestamp(state["target_date"]).month)
    values = climate.loc[
        (climate["city"] == state["city"]) & (climate["month"] == month),
        "actual_max_f",
    ].to_numpy(dtype=float)
    if len(values) < 20:
        values = climate.loc[climate["city"] == state["city"], "actual_max_f"].to_numpy(dtype=float)
    if len(values) == 0:
        raise ValueError(f"missing climatology rows for city={state['city']}")
    return values


def robust_tail_vector(
    state: dict[str, Any],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    scale_temperature: float,
    climate_mix: float,
    consensus_stat: str = "median",
    bias_multiplier: float = 1.0,
) -> np.ndarray:
    spec = fitted["specs"][state["city"]]
    assigned = float(state["forecast_max_f"])
    consensus = float(state[f"ensemble_{consensus_stat}_f"])
    location = (1.0 - ensemble_weight) * assigned + ensemble_weight * consensus
    centered_errors = spec["partial_errors"] - spec["partial_center"]
    weather = v2.empirical_vector(
        state,
        location + bias_multiplier * spec["partial_center"] + scale_temperature * centered_errors,
        kernel_sd_f=v2.KERNEL_SD_F,
    )
    if climate_mix == 0:
        return weather
    climate = v2.empirical_vector(
        state,
        _climate_values(state, fitted),
        kernel_sd_f=v2.KERNEL_SD_F,
    )
    mixed = (1.0 - climate_mix) * weather + climate_mix * climate
    return mixed / mixed.sum()


def score_candidate(
    states: list[dict[str, Any]],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    scale_temperature: float,
    climate_mix: float,
    consensus_stat: str,
    bias_multiplier: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        candidates = {
            "F_v2": robust_tail_vector(
                state,
                fitted,
                ensemble_weight=0.75,
                scale_temperature=1.0,
                climate_mix=0.0,
                consensus_stat="median",
                bias_multiplier=1.0,
            ),
            "G_robust_tail": robust_tail_vector(
                state,
                fitted,
                ensemble_weight=ensemble_weight,
                scale_temperature=scale_temperature,
                climate_mix=climate_mix,
                consensus_stat=consensus_stat,
                bias_multiplier=bias_multiplier,
            ),
            "G_scale_only": robust_tail_vector(
                state,
                fitted,
                ensemble_weight=ensemble_weight,
                scale_temperature=scale_temperature,
                climate_mix=0.0,
                consensus_stat=consensus_stat,
                bias_multiplier=bias_multiplier,
            ),
            "G_climate_only": robust_tail_vector(
                state,
                fitted,
                ensemble_weight=ensemble_weight,
                scale_temperature=1.0,
                climate_mix=climate_mix,
                consensus_stat=consensus_stat,
                bias_multiplier=bias_multiplier,
            ),
            "H_location_only": robust_tail_vector(
                state,
                fitted,
                ensemble_weight=ensemble_weight,
                scale_temperature=1.0,
                climate_mix=0.0,
                consensus_stat=consensus_stat,
                bias_multiplier=bias_multiplier,
            ),
            "market": state["market_probs"],
        }
        for arm, vector in candidates.items():
            logloss, brier, rps, winner_probability, top1 = base.score_vector(vector, state["winner_index"])
            rows.append(
                {
                    "snapshot_key": state["snapshot_key"],
                    "policy": state["policy"],
                    "city": state["city"],
                    "target_date": state["target_date"],
                    "arm": arm,
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                    "probabilities_json": json.dumps(vector.tolist(), separators=(",", ":")),
                    "winner_index": int(state["winner_index"]),
                }
            )
    return pd.DataFrame(rows)


def select_parameters(states: list[dict[str, Any]], fitted: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for consensus_stat in CONSENSUS_STATS:
        for bias_multiplier in BIAS_MULTIPLIERS:
            for weight in WEIGHTS:
                for scale in SCALE_TEMPERATURES:
                    for climate_mix in CLIMATE_MIXES:
                        values: list[dict[str, Any]] = []
                        for state in states:
                            vector = robust_tail_vector(
                                state,
                                fitted,
                                ensemble_weight=weight,
                                scale_temperature=scale,
                                climate_mix=climate_mix,
                                consensus_stat=consensus_stat,
                                bias_multiplier=bias_multiplier,
                            )
                            logloss, brier, rps, _, _ = base.score_vector(vector, state["winner_index"])
                            values.append(
                                {
                                    "target_date": state["target_date"],
                                    "logloss": logloss,
                                    "brier": brier,
                                    "rps": rps,
                                }
                            )
                        frame = pd.DataFrame(values).groupby("target_date")[["logloss", "brier", "rps"]].mean()
                        rows.append(
                            {
                                "consensus_stat": consensus_stat,
                                "bias_multiplier": bias_multiplier,
                                "ensemble_weight": weight,
                                "scale_temperature": scale,
                                "climate_mix": climate_mix,
                                "date_equal_logloss": float(frame["logloss"].mean()),
                                "date_equal_brier": float(frame["brier"].mean()),
                                "date_equal_rps": float(frame["rps"].mean()),
                            }
                        )
    candidates = pd.DataFrame(rows).sort_values(
        ["date_equal_logloss", "date_equal_rps", "date_equal_brier"], ignore_index=True
    )
    return candidates.iloc[0].to_dict(), candidates


def _summary(frame: pd.DataFrame) -> pd.DataFrame:
    return v2.date_equal(frame)


def _paired(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            v2.bootstrap_delta(frame, left, right, metric)
            for metric in ("logloss", "brier", "rps")
            for left, right in (
                ("G_robust_tail", "F_v2"),
                ("G_robust_tail", "G_scale_only"),
                ("G_robust_tail", "G_climate_only"),
                ("G_robust_tail", "H_location_only"),
                ("G_robust_tail", "market"),
            )
        ]
    )


def market_offset_vector(market: np.ndarray, weather: np.ndarray, beta: float) -> np.ndarray:
    if beta == 0.0:
        return np.asarray(market, dtype=float).copy()
    log_posterior = np.log(np.clip(market, v2.EPS, None)) + beta * (
        np.log(np.clip(weather, v2.EPS, None)) - np.log(np.clip(market, v2.EPS, None))
    )
    log_posterior -= float(np.max(log_posterior))
    posterior = np.exp(log_posterior)
    return posterior / posterior.sum()


def weather_vectors(
    states: list[dict[str, Any]], fitted: dict[str, Any], selected: dict[str, Any]
) -> dict[str, np.ndarray]:
    return {
        state["snapshot_key"]: robust_tail_vector(
            state,
            fitted,
            ensemble_weight=float(selected["ensemble_weight"]),
            scale_temperature=float(selected["scale_temperature"]),
            climate_mix=float(selected["climate_mix"]),
            consensus_stat=str(selected["consensus_stat"]),
            bias_multiplier=float(selected["bias_multiplier"]),
        )
        for state in states
    }


def checkpoint_market_comparison(
    states: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Compare early/late D-1 markets only on identical city-date ladders.

    The later checkpoint is never substituted into the earlier decision.  This
    table only diagnoses which contemporaneous market distribution was more
    informative about settlement and how much forecast information changed
    between the two clocks.
    """
    by_city_date: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for state in states:
        by_city_date.setdefault((state["city"], state["target_date"]), {})[
            state["policy"]
        ] = state
    rows: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    early_policy = "D-1_12_18_first"
    late_policy = "D-1_18_24_first"
    for (city, target_date), policies in sorted(by_city_date.items()):
        if early_policy not in policies or late_policy not in policies:
            continue
        early, late = policies[early_policy], policies[late_policy]
        early_labels = [bracket.label for bracket in early["brackets"]]
        late_labels = [bracket.label for bracket in late["brackets"]]
        if early_labels != late_labels or early["winner_index"] != late["winner_index"]:
            continue
        early_scores = base.score_vector(early["market_probs"], early["winner_index"])
        late_scores = base.score_vector(late["market_probs"], late["winner_index"])
        gap_hours = (
            pd.Timestamp(late["decision_time_utc"]) - pd.Timestamp(early["decision_time_utc"])
        ).total_seconds() / 3600.0
        total_variation = 0.5 * float(
            np.abs(np.asarray(late["market_probs"]) - np.asarray(early["market_probs"])).sum()
        )
        row = {
            "city": city,
            "target_date": target_date,
            "early_decision_time_utc": early["decision_time_utc"],
            "late_decision_time_utc": late["decision_time_utc"],
            "decision_gap_hours": gap_hours,
            "market_total_variation": total_variation,
            "forecast_revision_f": float(late["forecast_max_f"]) - float(early["forecast_max_f"]),
            "ensemble_mean_revision_f": float(late["ensemble_mean_f"])
            - float(early["ensemble_mean_f"]),
            "early_winner_probability": early_scores[3],
            "late_winner_probability": late_scores[3],
            "early_logloss": early_scores[0],
            "late_logloss": late_scores[0],
            "late_minus_early_logloss": late_scores[0] - early_scores[0],
            "early_brier": early_scores[1],
            "late_brier": late_scores[1],
            "early_rps": early_scores[2],
            "late_rps": late_scores[2],
        }
        rows.append(row)
        for arm, scores in (("M0_early_12_18", early_scores), ("M0_late_18_24", late_scores)):
            long_rows.append(
                {
                    "snapshot_key": f"{city}|{target_date}|{arm}",
                    "city": city,
                    "target_date": target_date,
                    "arm": arm,
                    "logloss": scores[0],
                    "brier": scores[1],
                    "rps": scores[2],
                    "winner_probability": scores[3],
                    "top1_accuracy": scores[4],
                }
            )
    comparison = pd.DataFrame(rows)
    long_frame = pd.DataFrame(long_rows)
    if long_frame.empty:
        return comparison, pd.DataFrame(), pd.DataFrame()
    scores = _summary(long_frame)
    paired = pd.DataFrame(
        [
            v2.bootstrap_delta(
                long_frame, "M0_late_18_24", "M0_early_12_18", metric
            )
            for metric in ("logloss", "brier", "rps")
        ]
    )
    return comparison, scores, paired


def best_market_beta(
    states: list[dict[str, Any]], vectors: dict[str, np.ndarray]
) -> tuple[float, pd.DataFrame]:
    rows: list[dict[str, float]] = []
    for beta in MARKET_OFFSET_BETAS:
        losses: list[dict[str, Any]] = []
        for state in states:
            posterior = market_offset_vector(
                state["market_probs"], vectors[state["snapshot_key"]], beta
            )
            logloss, brier, rps, _, _ = base.score_vector(posterior, state["winner_index"])
            losses.append(
                {"target_date": state["target_date"], "logloss": logloss, "brier": brier, "rps": rps}
            )
        daily = pd.DataFrame(losses).groupby("target_date")[["logloss", "brier", "rps"]].mean()
        rows.append(
            {
                "beta": beta,
                "date_equal_logloss": float(daily["logloss"].mean()),
                "date_equal_brier": float(daily["brier"].mean()),
                "date_equal_rps": float(daily["rps"].mean()),
            }
        )
    candidates = pd.DataFrame(rows).sort_values(
        ["date_equal_logloss", "date_equal_rps", "date_equal_brier"], ignore_index=True
    )
    return float(candidates.iloc[0]["beta"]), candidates


def fit_partial_market_betas(
    states: list[dict[str, Any]],
    vectors: dict[str, np.ndarray],
    global_beta: float,
) -> dict[str, Any]:
    source_beta: dict[str, float] = {}
    source_rows: dict[str, int] = {}
    for source in sorted({str(state["model_key"]) for state in states}):
        subset = [state for state in states if str(state["model_key"]) == source]
        raw_beta, _ = best_market_beta(subset, vectors)
        n = len(subset)
        source_rows[source] = n
        source_beta[source] = global_beta + (n / (n + 60.0)) * (raw_beta - global_beta)
    city_source_beta: dict[str, float] = {}
    city_source_rows: dict[str, int] = {}
    for city, source in sorted({(str(state["city"]), str(state["model_key"])) for state in states}):
        subset = [
            state
            for state in states
            if str(state["city"]) == city and str(state["model_key"]) == source
        ]
        raw_beta, _ = best_market_beta(subset, vectors)
        n = len(subset)
        key = f"{city}|{source}"
        city_source_rows[key] = n
        base_beta = source_beta[source]
        city_source_beta[key] = base_beta + (n / (n + 120.0)) * (raw_beta - base_beta)
    return {
        "global_beta": global_beta,
        "source_shrinkage_lambda": 60.0,
        "city_source_shrinkage_lambda": 120.0,
        "source_beta": source_beta,
        "source_rows": source_rows,
        "city_source_beta": city_source_beta,
        "city_source_rows": city_source_rows,
    }


def score_market_models(
    states: list[dict[str, Any]],
    vectors: dict[str, np.ndarray],
    *,
    global_beta: float,
    partial: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        weather = vectors[state["snapshot_key"]]
        key = f"{state['city']}|{state['model_key']}"
        partial_beta = float(partial["city_source_beta"].get(key, global_beta))
        arms = {
            "M0_market": state["market_probs"],
            "M1_weather": weather,
            "M2_global_offset": market_offset_vector(state["market_probs"], weather, global_beta),
            "M3_partial_offset": market_offset_vector(state["market_probs"], weather, partial_beta),
        }
        for arm, vector in arms.items():
            logloss, brier, rps, winner_probability, top1 = base.score_vector(vector, state["winner_index"])
            rows.append(
                {
                    "snapshot_key": state["snapshot_key"],
                    "target_date": state["target_date"],
                    "city": state["city"],
                    "model_key": state["model_key"],
                    "arm": arm,
                    "beta": (
                        0.0
                        if arm == "M0_market"
                        else 1.0
                        if arm == "M1_weather"
                        else partial_beta
                        if arm == "M3_partial_offset"
                        else global_beta
                    ),
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                }
            )
    return pd.DataFrame(rows)


def market_paired(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            v2.bootstrap_delta(frame, arm, "M0_market", metric)
            for metric in ("logloss", "brier", "rps")
            for arm in ("M1_weather", "M2_global_offset", "M3_partial_offset")
        ]
    )


def render_report(payload: dict[str, Any]) -> str:
    primary = {row["arm"]: row for row in payload["primary_holdout_scores"]}
    deltas = {
        (row["left"], row["right"], row["metric"]): row
        for row in payload["primary_holdout_deltas"]
    }
    g_f = deltas[("G_robust_tail", "F_v2", "logloss")]
    g_m = deltas[("G_robust_tail", "market", "logloss")]
    diagnostic = payload["primary_diagnostics"]
    market_scores = {row["arm"]: row for row in payload["market_offset"]["holdout_scores"]}
    market_deltas = {
        (row["left"], row["metric"]): row
        for row in payload["market_offset"]["holdout_deltas_vs_market"]
    }
    checkpoint_scores = {
        row["arm"]: row for row in payload["checkpoint_market_efficiency"]["scores"]
    }
    checkpoint_delta = next(
        row
        for row in payload["checkpoint_market_efficiency"]["paired_deltas"]
        if row["metric"] == "logloss"
    )
    lines = [
        "# D-1 weather-only robust-tail 开发报告 v1",
        "",
        "weather-only:",
        f"significance={'improves_v2_secondary_holdout' if g_f['ci_high'] < 0 else 'not_significant_vs_v2'}",
        "calibration=legacy_secondary_holdout_not_clean_forward",
        "pooled_baseline=F_v2_ensemble_location",
        "forward=not_clean; original holdout was previously inspected before this mechanism",
        "",
        "market residual:",
        "baseline=market_same_rows",
        "forward=legacy_exploratory_only_weather_gate_not_clean",
        "execution=not_run_no_probability_gate",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 数据快照",
        "",
        f"- legacy long-history training：{payload['training_rows']} rows / {payload['training_cities']} cities。",
        f"- reconstructed D-1：开发 {payload['development_states']} states / {payload['development_dates']} dates；secondary holdout {payload['holdout_states']} states / {payload['holdout_dates']} dates。",
        "- settlement 来自 canonical `settlement_outcomes`；missing settlement=0，unsettled=0。",
        "- 本轮不是 fill/ROI 研究，missing_bracket 不适用；market 仅作同 rows probability baseline。",
        "",
        "## 结论",
        "",
        f"开发集在 K={payload['candidate_count']} 个预定义组合中选择 consensus={payload['selected']['consensus_stat']}、ensemble weight={payload['selected']['ensemble_weight']:.3f}、bias multiplier={payload['selected']['bias_multiplier']:.2f}、residual scale={payload['selected']['scale_temperature']:.2f}、climatology mix={payload['selected']['climate_mix']:.2f}。",
        f"secondary holdout 上 G logloss={primary['G_robust_tail']['logloss']:.4f}，F v2={primary['F_v2']['logloss']:.4f}，paired Δ={g_f['delta']:+.4f}（95% CI {g_f['ci_low']:+.4f}..{g_f['ci_high']:+.4f}）。相对 market Δ={g_m['delta']:+.4f}（{g_m['ci_low']:+.4f}..{g_m['ci_high']:+.4f}）。",
        "",
        "动作：把 G 锁定为 W0 legacy reference，不再读取这 9 个已查看日期调参数；它不是新 W1/M2/M3 的最终冻结模型。W1 必须先在 clean exact-run development 上完成 revision/spread/run-age 模型选择，随后 M2/M3 才能在同一 development 分母上选择正则，二者评审后再产生新的 freeze artifact 与 untouched forward。",
        "",
        "| arm | logloss | Brier | RPS | winner P | top-1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("market", "F_v2", "H_location_only", "G_scale_only", "G_climate_only", "G_robust_tail"):
        row = primary[arm]
        lines.append(
            f"| {arm} | {row['logloss']:.4f} | {row['brier']:.4f} | {row['rps']:.4f} | {row['winner_probability']:.3f} | {row['top1_accuracy']:.1%} |"
        )
    lines += [
        "",
        "## Calibration / tail",
        "",
        f"- G rung ECE={diagnostic['G_robust_tail']['rung_ece']:.4f}，F={diagnostic['F_v2']['rung_ece']:.4f}，market={diagnostic['market']['rung_ece']:.4f}。",
        f"- G winner≤1% states={diagnostic['G_robust_tail']['winner_probability_le_001']}，F={diagnostic['F_v2']['winner_probability_le_001']}。",
        f"- G bottom tail predicted/actual={diagnostic['G_robust_tail']['bottom_predicted']:.1%}/{diagnostic['G_robust_tail']['bottom_actual']:.1%}；top={diagnostic['G_robust_tail']['top_predicted']:.1%}/{diagnostic['G_robust_tail']['top_actual']:.1%}。",
        f"- location-only 已把 top-1 从 F 的 {primary['F_v2']['top1_accuracy']:.1%} 提到 {primary['H_location_only']['top1_accuracy']:.1%}；scale={payload['selected']['scale_temperature']:.2f} 进一步修复过度自信。{payload['selected']['climate_mix']:.0%} climatology mix 主要作极端档保底，在 secondary holdout 上没有独立 logloss 增益。",
        "",
        "## D-1 checkpoint market efficiency",
        "",
        f"在完全相同的 city-date 与 native ladder 上配对 {payload['checkpoint_market_efficiency']['paired_city_dates']} 个状态 / {payload['checkpoint_market_efficiency']['paired_target_dates']} 个日期。12–18h checkpoint market logloss={checkpoint_scores['M0_early_12_18']['logloss']:.4f}，18–24h={checkpoint_scores['M0_late_18_24']['logloss']:.4f}；late-minus-early={checkpoint_delta['delta']:+.4f}（95% CI {checkpoint_delta['ci_low']:+.4f}..{checkpoint_delta['ci_high']:+.4f}）。",
        f"两 checkpoint 平均相隔 {payload['checkpoint_market_efficiency']['mean_gap_hours']:.2f} 小时，market ladder total variation 均值={payload['checkpoint_market_efficiency']['mean_total_variation']:.3f}；assigned forecast 绝对变化均值={payload['checkpoint_market_efficiency']['mean_absolute_forecast_revision_f']:.3f}°F，ensemble mean 绝对变化均值={payload['checkpoint_market_efficiency']['mean_absolute_ensemble_revision_f']:.3f}°F。这里 later book 只用于判断市场信息效率，不会事后替换 early decision book。",
        "",
        "## 证据边界",
        "",
        "- 改动只作用于 weather distribution；没有使用 market、first_seen reaction、ROI 或价格切片选参数。",
        "- 原 9-date holdout 在提出本机制前已被查看，因此本报告只算 secondary exploratory validation，不重新标成 frozen forward。",
        f"- K={payload['candidate_count']} 未做多重检验校正；这也是必须停止 legacy 调参并转 clean forward 的原因。",
        "- 当前参数只锁定为 W0 legacy reference。新 exact-run 数据先划出 clean development 供 W1 与 M2/M3 模型选择；只有显式生成新 freeze artifact 之后的日期才属于 untouched forward。",
        "- 8环中本轮覆盖统计推断、概率分布、同分母 market baseline；不覆盖执行、容量、fills 或 live 动作。",
        "",
        "W0 锁定参考参数见 [`2026-08-05-d1-weather-only-clean-forward-freeze-v1.json`](2026-08-05-d1-weather-only-clean-forward-freeze-v1.json)；文件名沿用既有审计身份，但不代表 W1 或 market residual 已冻结。",
        "",
        "## Market-offset exploratory",
        "",
        "固定同一批 rows、labels 和 reconstructed market distribution，使用 `log P_post = log P_market + beta * (log P_weather - log P_market) - log Z`。beta=0 严格退化为 M0 market；beta 只在前 18 个开发日期选择。M3 的 source 与 city×source beta 进一步向 global beta 强收缩。",
        "",
        f"开发集选择 global beta={payload['market_offset']['global_beta']:.3f}；以下仍是已经看过的 9-date secondary holdout，不是 clean forward。",
        "",
        "| arm | logloss | Brier | RPS | winner P | top-1 | Δlogloss vs M0 (95% CI) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("M0_market", "M1_weather", "M2_global_offset", "M3_partial_offset"):
        row = market_scores[arm]
        if arm == "M0_market":
            delta_text = "reference"
        else:
            delta = market_deltas[(arm, "logloss")]
            delta_text = f"{delta['delta']:+.4f} ({delta['ci_low']:+.4f}..{delta['ci_high']:+.4f})"
        lines.append(
            f"| {arm} | {row['logloss']:.4f} | {row['brier']:.4f} | {row['rps']:.4f} | {row['winner_probability']:.3f} | {row['top1_accuracy']:.1%} | {delta_text} |"
        )
    lines += [
        "",
        "判定只看 M2/M3 相对 M0：若 paired CI 未整体低于 0，就没有可确认的 market residual；weather-only 相对自身旧版的改善不能替代这个条件。当前 market 是 reconstructed/non-executable probability baseline，本轮不计算 ask、fee、slippage、depth 或 ROI。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecasts", type=Path, default=base.DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=base.DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=base.DEFAULT_HISTORY)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    baskets = pd.read_csv(args.baskets, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    assignments = base.model_assignments(history)
    states, funnels = base.build_states(forecasts, baskets, assignments, base.load_settlements(args.db))
    v2.attach_multi_model(states, forecasts)
    checkpoint_rows, checkpoint_scores, checkpoint_paired = checkpoint_market_comparison(states)
    if checkpoint_rows.empty:
        raise ValueError("no identical-ladder early/late D-1 checkpoint pairs")
    primary = [state for state in states if state["policy"] == base.PRIMARY_POLICY]
    dates = sorted({state["target_date"] for state in primary})
    split = min(18, len(dates) - 5)
    dev_dates, holdout_dates = set(dates[:split]), set(dates[split:])
    development = [state for state in primary if state["target_date"] in dev_dates]
    holdout = [state for state in primary if state["target_date"] in holdout_dates]
    fitted = v2.fit_long_history(history, min(dates))
    selected, candidates = select_parameters(development, fitted)
    kwargs = {
        "ensemble_weight": selected["ensemble_weight"],
        "scale_temperature": selected["scale_temperature"],
        "climate_mix": selected["climate_mix"],
        "consensus_stat": selected["consensus_stat"],
        "bias_multiplier": selected["bias_multiplier"],
    }
    holdout_scored = score_candidate(holdout, fitted, **kwargs)
    secondary = [state for state in states if state["policy"] != base.PRIMARY_POLICY]
    secondary_scored = score_candidate(secondary, fitted, **kwargs)
    primary_scores = _summary(holdout_scored)
    primary_deltas = _paired(holdout_scored)
    diagnostics = v2.probability_diagnostics(holdout_scored)
    development_vectors = weather_vectors(development, fitted, selected)
    holdout_vectors = weather_vectors(holdout, fitted, selected)
    global_beta, market_beta_candidates = best_market_beta(development, development_vectors)
    partial_betas = fit_partial_market_betas(development, development_vectors, global_beta)
    market_scored = score_market_models(
        holdout,
        holdout_vectors,
        global_beta=global_beta,
        partial=partial_betas,
    )
    market_scores = _summary(market_scored)
    market_deltas = market_paired(market_scored)
    payload = {
        "schema_version": "d1_legacy_weather_only_robust_tail_v1",
        "candidate_count": int(len(candidates)),
        "selected": selected,
        "training_rows": int(len(fitted["train"])),
        "training_cities": int(fitted["train"]["city"].nunique()),
        "development_states": len(development),
        "development_dates": len(dev_dates),
        "holdout_states": len(holdout),
        "holdout_dates": len(holdout_dates),
        "funnels": funnels,
        "primary_holdout_scores": primary_scores.to_dict("records"),
        "primary_holdout_deltas": primary_deltas.to_dict("records"),
        "primary_diagnostics": {row["arm"]: row for row in diagnostics.to_dict("records")},
        "secondary_policy_scores": _summary(secondary_scored).to_dict("records"),
        "evidence_status": "legacy_secondary_validation_not_clean_forward",
        "market_offset": {
            "status": "legacy_exploratory_only_weather_gate_not_clean",
            "equation": "log_P_post=log_P_market+beta*(log_P_weather-log_P_market)-log_Z",
            "global_beta": global_beta,
            "partial_betas": partial_betas,
            "holdout_scores": market_scores.to_dict("records"),
            "holdout_deltas_vs_market": market_deltas.to_dict("records"),
        },
        "checkpoint_market_efficiency": {
            "paired_city_dates": int(len(checkpoint_rows)),
            "paired_target_dates": int(checkpoint_rows["target_date"].nunique()),
            "paired_cities": int(checkpoint_rows["city"].nunique()),
            "mean_gap_hours": float(checkpoint_rows["decision_gap_hours"].mean()),
            "mean_total_variation": float(checkpoint_rows["market_total_variation"].mean()),
            "mean_absolute_forecast_revision_f": float(
                checkpoint_rows["forecast_revision_f"].abs().mean()
            ),
            "mean_absolute_ensemble_revision_f": float(
                checkpoint_rows["ensemble_mean_revision_f"].abs().mean()
            ),
            "scores": checkpoint_scores.to_dict("records"),
            "paired_deltas": checkpoint_paired.to_dict("records"),
        },
        "market_residual": "legacy_exploratory_only_not_clean_gate",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(args.out / "development_candidate_grid.csv", index=False)
    holdout_scored.to_csv(args.out / "primary_holdout_scored.csv", index=False)
    primary_scores.to_csv(args.out / "primary_holdout_scores.csv", index=False)
    primary_deltas.to_csv(args.out / "primary_holdout_paired_bootstrap.csv", index=False)
    diagnostics.to_csv(args.out / "primary_holdout_diagnostics.csv", index=False)
    _summary(secondary_scored).to_csv(args.out / "secondary_policy_scores.csv", index=False)
    market_beta_candidates.to_csv(args.out / "market_offset_development_beta_grid.csv", index=False)
    market_scored.to_csv(args.out / "market_offset_holdout_scored.csv", index=False)
    market_scores.to_csv(args.out / "market_offset_holdout_scores.csv", index=False)
    market_deltas.to_csv(args.out / "market_offset_holdout_paired_bootstrap.csv", index=False)
    checkpoint_rows.to_csv(args.out / "checkpoint_market_efficiency_pairs.csv", index=False)
    checkpoint_scores.to_csv(args.out / "checkpoint_market_efficiency_scores.csv", index=False)
    checkpoint_paired.to_csv(args.out / "checkpoint_market_efficiency_paired_bootstrap.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
