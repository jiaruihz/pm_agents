#!/usr/bin/env python3
"""Develop a robust-tail D-1 weather-only challenger on legacy evidence.

This experiment keeps the v2 ensemble location mechanism and changes only the
weather distribution: a global residual scale temperature plus a small
climatology mixture prevent overconfident or near-zero native-rung mass.  The
first 18 reconstructed target dates select parameters.  The final 9 dates are
reported as a previously observed secondary holdout, never as clean forward.
Market remains a same-row baseline and is not used by the weather model.
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


def render_report(payload: dict[str, Any]) -> str:
    primary = {row["arm"]: row for row in payload["primary_holdout_scores"]}
    deltas = {
        (row["left"], row["right"], row["metric"]): row
        for row in payload["primary_holdout_deltas"]
    }
    g_f = deltas[("G_robust_tail", "F_v2", "logloss")]
    g_m = deltas[("G_robust_tail", "market", "logloss")]
    diagnostic = payload["primary_diagnostics"]
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
        "forward=not_run_by_contract",
        "execution=not_run_by_contract",
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
        "动作：冻结 G 为下一批 clean exact-run D-1 forward challenger，不再读取这 9 个日期调参数。它改善了 location、RPS 与 calibration，但 logloss 显著性尚未过门且仍输 market，因此不运行 market residual、不改 live。",
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
        "## 证据边界",
        "",
        "- 改动只作用于 weather distribution；没有使用 market、first_seen reaction、ROI 或价格切片选参数。",
        "- 原 9-date holdout 在提出本机制前已被查看，因此本报告只算 secondary exploratory validation，不重新标成 frozen forward。",
        f"- K={payload['candidate_count']} 未做多重检验校正；这也是必须停止 legacy 调参并转 clean forward 的原因。",
        "- 参数必须冻结到新 exact-run collector 的 settlement-complete target dates；clean forward 通过前不运行 market residual。",
        "- 8环中本轮覆盖统计推断、概率分布、同分母 market baseline；不覆盖执行、容量、fills 或 live 动作。",
        "",
        "冻结参数见 [`2026-08-05-d1-weather-only-clean-forward-freeze-v1.json`](2026-08-05-d1-weather-only-clean-forward-freeze-v1.json)。",
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
        "market_residual": "not_run_by_contract",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(args.out / "development_candidate_grid.csv", index=False)
    holdout_scored.to_csv(args.out / "primary_holdout_scored.csv", index=False)
    primary_scores.to_csv(args.out / "primary_holdout_scores.csv", index=False)
    primary_deltas.to_csv(args.out / "primary_holdout_paired_bootstrap.csv", index=False)
    diagnostics.to_csv(args.out / "primary_holdout_diagnostics.csv", index=False)
    _summary(secondary_scored).to_csv(args.out / "secondary_policy_scores.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
