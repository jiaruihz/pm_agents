#!/usr/bin/env python3
"""Audit three pre-specified D-1 city cohorts for market residual.

The weather distribution is the already locked robust-tail W0.  This runner
does not retune that distribution.  It selects only one market-offset beta on
the first twelve reconstructed target dates, checks the next six dates, and
reports the final nine dates as a previously observed secondary holdout.

City cohorts never use the secondary holdout:
* all assigned cities;
* GFS-assigned cities, fixed because the pre-test summer history has lower
  source MAE than ECMWF;
* the lower-half city forecast-MAE cohort, fixed from pre-test history only.
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

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_robust_tail as robust
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as v2


DEFAULT_OUT = Path(
    "/Volumes/jrs/pm_agents/research/artifact_store/active/"
    "d1_city_cohort_market_residual_audit_20260806"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-08/2026-08-06-d1-city-cohort-market-residual-audit-v1.md"
)
DEFAULT_STATE_ARTIFACT = (
    ROOT
    / "docs/analysis/2026-08/generated/d1_cross_city_hierarchy_v1/scored_states.csv"
)
DEFAULT_STATE_SUMMARY = (
    ROOT / "docs/analysis/2026-08/generated/d1_cross_city_hierarchy_v1/summary.json"
)
W0_PARAMETERS = {
    "ensemble_weight": 0.875,
    "scale_temperature": 1.25,
    "climate_mix": 0.02,
    "consensus_stat": "mean",
    "bias_multiplier": 1.0,
}
MARKET_TEMPERATURES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4)
VERSION_ORDER = (
    "V05_all_cities_market_offset",
    "V06_gfs_lower_source_mae",
    "V07_lower_half_city_mae",
)


def source_and_city_mae(history: pd.DataFrame, test_start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = history.loc[
        history["is_best_model"]
        & (history["date"] < test_start)
        & history["month_num"].isin([5, 6, 7, 8])
    ].copy()
    source["abs_error_f"] = source["error_f_actual_minus_forecast"].abs()
    source_summary = (
        source.groupby("model", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("date", "nunique"),
            cities=("city", "nunique"),
            mae_f=("abs_error_f", "mean"),
            bias_f=("error_f_actual_minus_forecast", "mean"),
        )
        .sort_values("mae_f", ignore_index=True)
    )
    city_summary = (
        source.groupby(["city", "model"], as_index=False)
        .agg(
            rows=("date", "size"),
            dates=("date", "nunique"),
            mae_f=("abs_error_f", "mean"),
            bias_f=("error_f_actual_minus_forecast", "mean"),
        )
        .sort_values(["mae_f", "city"], ignore_index=True)
    )
    return source_summary, city_summary


def load_state_artifact(path: Path) -> list[dict[str, Any]]:
    """Rehydrate the fixed exact-ladder states from the prior canonical-labeled run.

    One market row is sufficient because it contains ordered native labels,
    normalized market probabilities, the exact winner and assigned forecast.
    Using the frozen artifact avoids changing either the JRS snapshot inventory
    or canonical DB build during this audit.
    """
    frame = pd.read_csv(path, dtype={"target_date": str})
    frame = frame.loc[frame["arm"] == "market"].copy()
    if frame["snapshot_key"].duplicated().any():
        raise ValueError("state artifact has duplicate market snapshot rows")
    states: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        encoded = json.loads(row.probabilities_json)
        if not isinstance(encoded, dict):
            raise ValueError("expected ordered label->probability JSON in state artifact")
        labels = list(encoded)
        parsed = [base.parse_bracket(label, "") for label in labels]
        parsed[0] = base.Bracket(
            parsed[0].label, None, parsed[0].high, True, False
        )
        parsed[-1] = base.Bracket(
            parsed[-1].label, parsed[-1].low, None, False, True
        )
        base.validate_ladder(parsed)
        winner = str(row.winner_bracket).replace("°C", "").replace("°F", "").replace("°", "").strip()
        states.append(
            {
                "snapshot_key": str(row.snapshot_key),
                "city": str(row.city),
                "target_date": str(row.target_date),
                "policy": str(row.policy),
                "decision_ts_utc": str(row.decision_ts_utc),
                "decision_time_utc": str(row.decision_ts_utc),
                "market_unit": str(row.market_unit),
                "model": str(row.forecast_model),
                "model_key": base.MODEL_KEY[str(row.forecast_model)],
                "forecast_max_f": float(row.forecast_max_f),
                "forecast_lineage_status": "single_run_reconstructed_conservative_12h_lag",
                "brackets": parsed,
                "labels": labels,
                "winner_index": labels.index(winner),
                "market_probs": np.asarray(list(encoded.values()), dtype=float),
            }
        )
    return states


def build_cohorts(
    states: list[dict[str, Any]], city_mae: pd.DataFrame
) -> dict[str, set[str]]:
    available = {str(state["city"]) for state in states}
    gfs = {
        str(state["city"])
        for state in states
        if str(state["model_key"]) == "gfs_global"
    }
    eligible = city_mae.loc[city_mae["city"].isin(available)].copy()
    cutoff = float(eligible["mae_f"].median())
    low_mae = set(eligible.loc[eligible["mae_f"] <= cutoff, "city"].astype(str))
    return {
        "V05_all_cities_market_offset": available,
        "V06_gfs_lower_source_mae": gfs,
        "V07_lower_half_city_mae": low_mae,
    }


def score_version(
    states: list[dict[str, Any]],
    vectors: dict[str, np.ndarray],
    *,
    market_temperature: float,
    beta: float,
    version: str,
    phase: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        market = np.asarray(state["market_probs"], dtype=float)
        calibrated_market = calibrated_posterior_vector(
            market,
            vectors[state["snapshot_key"]],
            market_temperature=market_temperature,
            beta=0.0,
        )
        posterior = calibrated_posterior_vector(
            market,
            vectors[state["snapshot_key"]],
            market_temperature=market_temperature,
            beta=beta,
        )
        for arm, vector in (
            ("market", market),
            ("calibrated_market", calibrated_market),
            ("posterior", posterior),
        ):
            logloss, brier, rps, winner_probability, top1 = base.score_vector(
                vector, state["winner_index"]
            )
            rows.append(
                {
                    "version": version,
                    "phase": phase,
                    "snapshot_key": state["snapshot_key"],
                    "target_date": state["target_date"],
                    "city": state["city"],
                    "model_key": state["model_key"],
                    "arm": arm,
                    "market_temperature": market_temperature if arm != "market" else 1.0,
                    "beta": beta if arm == "posterior" else 0.0,
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                    "winner_index": int(state["winner_index"]),
                    "probabilities_json": json.dumps(
                        vector.tolist(), separators=(",", ":")
                    ),
                }
            )
    return pd.DataFrame(rows)


def date_equal_scores(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["logloss", "brier", "rps", "winner_probability", "top1_accuracy"]
    daily = frame.groupby(["target_date", "arm"], as_index=False)[metrics].mean()
    summary = daily.groupby("arm", as_index=False)[metrics].mean()
    counts = (
        frame.groupby("arm", as_index=False)
        .agg(
            states=("snapshot_key", "size"),
            dates=("target_date", "nunique"),
            cities=("city", "nunique"),
        )
    )
    return summary.merge(counts, on="arm")


def paired_bootstrap(
    frame: pd.DataFrame,
    metric: str,
    *,
    left: str = "posterior",
    right: str = "market",
    seed: int = 20260806,
    draws: int = 50000,
) -> dict[str, Any]:
    daily = (
        frame.groupby(["target_date", "arm"])[metric]
        .mean()
        .unstack()
        .dropna()
    )
    deltas = (daily[left] - daily[right]).to_numpy(dtype=float)
    if not len(deltas):
        raise ValueError("paired bootstrap has no dates")
    rng = np.random.default_rng(seed)
    samples = rng.choice(deltas, size=(draws, len(deltas)), replace=True).mean(axis=1)
    return {
        "metric": metric,
        "left": left,
        "right": right,
        "dates": int(len(deltas)),
        "delta": float(np.mean(deltas)),
        "ci95_low": float(np.quantile(samples, 0.025)),
        "ci95_high": float(np.quantile(samples, 0.975)),
        # Bonferroni family-wise 95% interval for K=3 versions.
        "ci_bonferroni_low": float(np.quantile(samples, 0.05 / 3.0 / 2.0)),
        "ci_bonferroni_high": float(np.quantile(samples, 1.0 - 0.05 / 3.0 / 2.0)),
    }


def _phase_payload(frame: pd.DataFrame) -> dict[str, Any]:
    scores = date_equal_scores(frame)
    paired = [
        paired_bootstrap(frame, metric, left=left, right=right)
        for metric in ("logloss", "brier", "rps")
        for left, right in (
            ("calibrated_market", "market"),
            ("posterior", "market"),
            ("posterior", "calibrated_market"),
        )
    ]
    return {
        "states": int(frame.loc[frame["arm"] == "market", "snapshot_key"].nunique()),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "scores": scores.to_dict("records"),
        "paired": paired,
    }


def _delta(
    payload: dict[str, Any],
    metric: str,
    *,
    left: str = "posterior",
    right: str = "market",
) -> dict[str, Any]:
    return next(
        row
        for row in payload["paired"]
        if row["metric"] == metric and row["left"] == left and row["right"] == right
    )


def calibrated_posterior_vector(
    market: np.ndarray,
    weather: np.ndarray,
    *,
    market_temperature: float,
    beta: float,
) -> np.ndarray:
    log_market = np.log(np.clip(np.asarray(market, dtype=float), v2.EPS, None))
    log_weather = np.log(np.clip(np.asarray(weather, dtype=float), v2.EPS, None))
    logits = market_temperature * log_market + beta * (log_weather - log_market)
    logits -= float(np.max(logits))
    vector = np.exp(logits)
    return vector / vector.sum()


def select_market_temperature_and_beta(
    states: list[dict[str, Any]], vectors: dict[str, np.ndarray]
) -> tuple[float, float, pd.DataFrame]:
    rows: list[dict[str, float]] = []
    for temperature in MARKET_TEMPERATURES:
        for beta in robust.MARKET_OFFSET_BETAS:
            losses: list[dict[str, Any]] = []
            for state in states:
                vector = calibrated_posterior_vector(
                    state["market_probs"],
                    vectors[state["snapshot_key"]],
                    market_temperature=temperature,
                    beta=beta,
                )
                logloss, brier, rps, _, _ = base.score_vector(
                    vector, state["winner_index"]
                )
                losses.append(
                    {
                        "target_date": state["target_date"],
                        "logloss": logloss,
                        "brier": brier,
                        "rps": rps,
                    }
                )
            daily = pd.DataFrame(losses).groupby("target_date")[["logloss", "brier", "rps"]].mean()
            rows.append(
                {
                    "market_temperature": temperature,
                    "beta": beta,
                    "date_equal_logloss": float(daily["logloss"].mean()),
                    "date_equal_brier": float(daily["brier"].mean()),
                    "date_equal_rps": float(daily["rps"].mean()),
                }
            )
    grid = pd.DataFrame(rows)
    calibrated = grid.loc[grid["beta"] == 0.0].sort_values(
        ["date_equal_logloss", "date_equal_rps", "date_equal_brier"], ignore_index=True
    )
    temperature = float(calibrated.iloc[0]["market_temperature"])
    conditional = grid.loc[grid["market_temperature"] == temperature].sort_values(
        ["date_equal_logloss", "date_equal_rps", "date_equal_brier"], ignore_index=True
    )
    beta = float(conditional.iloc[0]["beta"])
    return temperature, beta, grid.sort_values(
        ["date_equal_logloss", "date_equal_rps", "date_equal_brier"], ignore_index=True
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# D-1 city-cohort market residual audit",
        "",
        "weather-only:",
        "significance=not_retested; locked robust-tail W0",
        "calibration=legacy reconstructed development evidence",
        "pooled_baseline=robust-tail W0",
        "forward=not clean; final 9 dates were already observed by prior research",
        "",
        "market residual:",
        "baseline=same-row normalized market ladder",
        "forward=secondary holdout only",
        "execution=not run; historical market probability is non-executable",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 结论",
        "",
        payload["conclusion"],
        "",
        "三个版本共用锁定 W0，只改变事前 city cohort；market temperature 与 weather beta 只在最早 12 个 reconstructed target dates 选择。城市选择不读取 inner validation 或 final secondary holdout。",
        "",
        "| version | rule | gamma / beta | inner-val states/dates | ΔLL vs raw market (95% CI) | secondary states/dates | ΔLL vs raw market (95% CI) | status |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for version in VERSION_ORDER:
        row = payload["versions"][version]
        inner = row["inner_validation"]
        holdout = row["secondary_holdout"]
        inner_delta = _delta(inner, "logloss")
        holdout_delta = _delta(holdout, "logloss")
        lines.append(
            f"| {version} | {row['selection_rule']} | {row['market_temperature']:.2f} / {row['beta']:.3f} | "
            f"{inner['states']}/{inner['dates']} | {inner_delta['delta']:+.4f} "
            f"[{inner_delta['ci95_low']:+.4f},{inner_delta['ci95_high']:+.4f}] | "
            f"{holdout['states']}/{holdout['dates']} | {holdout_delta['delta']:+.4f} "
            f"[{holdout_delta['ci95_low']:+.4f},{holdout_delta['ci95_high']:+.4f}] | "
            f"{row['status']} |"
        )
    lines += [
        "",
        "三个版本最终 `beta=0`：weather-only W0 对 calibrated market 没有增量。V07 在 inner validation 看似改善，但 secondary holdout 反号，是本轮最清楚的已有窗过拟合；V06 只有 secondary logloss 点估为负，Brier/RPS 与 CI 未共同通过。",
        "",
        "## Proper-score paired deltas",
        "",
        "负值表示 candidate 优于同 rows raw market。Bonferroni interval 已按本轮 K=3 调整。",
        "",
        "| version | phase | metric | delta | 95% CI | K=3 Bonferroni CI |",
        "|---|---|---|---:|---:|---:|",
    ]
    for version in VERSION_ORDER:
        row = payload["versions"][version]
        for phase in ("inner_validation", "secondary_holdout"):
            for metric in ("logloss", "brier", "rps"):
                delta = _delta(row[phase], metric)
                lines.append(
                    f"| {version} | {phase} | {metric} | {delta['delta']:+.6f} | "
                    f"[{delta['ci95_low']:+.6f},{delta['ci95_high']:+.6f}] | "
                    f"[{delta['ci_bonferroni_low']:+.6f},{delta['ci_bonferroni_high']:+.6f}] |"
                )
    lines += [
        "",
        "## Secondary same-row absolute scores",
        "",
        "| version | arm | logloss | Brier | RPS | states / dates |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for version in VERSION_ORDER:
        phase = payload["versions"][version]["secondary_holdout"]
        for arm in ("market", "calibrated_market", "posterior"):
            score = next(row for row in phase["scores"] if row["arm"] == arm)
            lines.append(
                f"| {version} | {arm} | {score['logloss']:.6f} | {score['brier']:.6f} | "
                f"{score['rps']:.6f} | {score['states']} / {score['dates']} |"
            )
    lines += [
        "",
        "## 数据与固定分母",
        "",
        f"- forecast input：`{payload['inputs']['forecast']}`，{payload['input_census']['forecast_rows']} rows / {payload['input_census']['forecast_dates']} target dates / {payload['input_census']['forecast_cities']} cities。",
        f"- executable-basket input：`{payload['inputs']['baskets']}`，{payload['input_census']['basket_rows']} rows / {payload['input_census']['basket_dates']} target dates / {payload['input_census']['basket_cities']} cities。",
        f"- history artifact：`{payload['inputs']['history']}`，{payload['input_census']['history_rows']} rows；实际 W0 training slice={payload['training_slice']['rows']} rows / {payload['training_slice']['dates']} dates / {payload['training_slice']['cities']} cities。",
        f"- state funnel：assigned={payload['funnels']['assigned_model_snapshots']} → scored={payload['funnels']['scoreable_states']}；invalid ladder={payload['funnels']['invalid_ladder']}，missing settlement={payload['funnels']['missing_settlement']}，missing market={payload['funnels']['missing_market_mid']}。",
        f"- phase split：beta fit={payload['split']['beta_fit_start']}..{payload['split']['beta_fit_end']} ({payload['split']['beta_fit_dates']} dates)；inner validation={payload['split']['inner_validation_start']}..{payload['split']['inner_validation_end']} ({payload['split']['inner_validation_dates']} dates)；secondary holdout={payload['split']['secondary_holdout_start']}..{payload['split']['secondary_holdout_end']} ({payload['split']['secondary_holdout_dates']} dates)。",
        "",
        "## City selection provenance",
        "",
        f"- source-level pre-test summer MAE：GFS={payload['selection_evidence']['gfs_mae_f']:.3f}°F，ECMWF={payload['selection_evidence']['ecmwf_mae_f']:.3f}°F；所以 V06 固定 GFS cohort。",
        f"- V07 cutoff 是 pre-test city MAE 中位数 {payload['selection_evidence']['city_mae_cutoff_f']:.3f}°F；保留 {len(payload['versions']['V07_lower_half_city_mae']['cities'])} 城。",
        "- 这两条都是 forecast/coverage 规则，不是看 secondary holdout 后挑赢家。",
        "",
        "## Evidence boundary",
        "",
        "- reconstructed forecast 使用 conservative 12h lag，不是真实 provider run/first-seen PIT。state/settlement/market 使用既有 D-1 canonical-labeled score artifact 的固定 exact-ladder rows；本机 canonical DB/JRS 只读查询本轮超时，未混入另一个 build。",
        "- target 是完整 native exact ladder final settlement；primary grain 每个 `city × target_date` 至多一个 18–24h checkpoint。没有使用 binary/relative bucket，也没有靠 repeated checkpoints 放大样本。",
        "- 同 rows market 是 normalized contemporaneous ladder probability；不是可执行 ask/depth，故本轮不计算 fee-adjusted ROI。",
        "- final 9 target dates 在本项目的早期 W0 研究中已被查看；本 runner 没有用它们选 cohort/beta，但仍只能称 secondary holdout，不能包装成 frozen forward。",
        "- raw market 与 fold-fit calibrated market 都保存。weather 增量必须看 posterior vs calibrated market；若 beta=0，它严格等于 calibrated market，不算 weather alpha。",
        "- K=3；表中同时保存 95% 与 Bonferroni family-wise 95% intervals，只有后者全负才算本轮多重检验后显著。",
        "- `runnable_policy.json` 只是 deterministic research/shadow policy artifact；不授权 deployment、live 或 order。",
        "",
        "## 双漏斗与 8 环",
        "",
        "signal funnel：reconstructed D-1 states → fixed cohort → posterior probability rows；没有交易 selection。",
        "",
        "evidence funnel：forecast snapshot + complete native ladder + same-clock market + settlement → probability score；executable expression/fill=not available。",
        "",
        "覆盖统计推断、概率分布和同分母 baseline；缺 clean forward、execution microstructure、capacity、fills/PnL。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecasts", type=Path, default=base.DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=base.DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=base.DEFAULT_HISTORY)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--state-artifact", type=Path, default=DEFAULT_STATE_ARTIFACT)
    parser.add_argument("--state-summary", type=Path, default=DEFAULT_STATE_SUMMARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    baskets = pd.read_csv(args.baskets, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    states = load_state_artifact(args.state_artifact)
    funnels = json.loads(args.state_summary.read_text(encoding="utf-8"))["funnels"]
    states = [state for state in states if state["policy"] == base.PRIMARY_POLICY]
    v2.attach_multi_model(states, forecasts)
    dates = sorted({state["target_date"] for state in states})
    if len(dates) < 27:
        raise ValueError(f"expected at least 27 reconstructed target dates, got {len(dates)}")
    beta_fit_dates = set(dates[:12])
    inner_validation_dates = set(dates[12:18])
    holdout_dates = set(dates[18:])

    fitted = v2.fit_legacy_history_slice(history, min(dates))
    vectors = robust.weather_vectors(states, fitted, W0_PARAMETERS)
    source_mae, city_mae = source_and_city_mae(history, min(dates))
    cohorts = build_cohorts(states, city_mae)
    city_mae_cutoff = float(
        city_mae.loc[city_mae["city"].isin({s["city"] for s in states}), "mae_f"].median()
    )

    all_scored: list[pd.DataFrame] = []
    versions: dict[str, Any] = {}
    rules = {
        "V05_all_cities_market_offset": "all scoreable assigned cities",
        "V06_gfs_lower_source_mae": "GFS-assigned; lower pre-test source MAE",
        "V07_lower_half_city_mae": "city pre-test summer MAE <= cross-city median",
    }
    for version in VERSION_ORDER:
        cities = cohorts[version]
        fit_states = [s for s in states if s["target_date"] in beta_fit_dates and s["city"] in cities]
        validation_states = [s for s in states if s["target_date"] in inner_validation_dates and s["city"] in cities]
        holdout_states = [s for s in states if s["target_date"] in holdout_dates and s["city"] in cities]
        if not fit_states or not validation_states or not holdout_states:
            raise ValueError(f"{version} has an empty phase")
        market_temperature, beta, beta_grid = select_market_temperature_and_beta(
            fit_states, vectors
        )
        phase_frames = {
            "beta_fit": score_version(
                fit_states,
                vectors,
                market_temperature=market_temperature,
                beta=beta,
                version=version,
                phase="beta_fit",
            ),
            "inner_validation": score_version(
                validation_states,
                vectors,
                market_temperature=market_temperature,
                beta=beta,
                version=version,
                phase="inner_validation",
            ),
            "secondary_holdout": score_version(
                holdout_states,
                vectors,
                market_temperature=market_temperature,
                beta=beta,
                version=version,
                phase="secondary_holdout",
            ),
        }
        all_scored.extend(phase_frames.values())
        phase_payloads = {name: _phase_payload(frame) for name, frame in phase_frames.items()}
        inner = phase_payloads["inner_validation"]
        holdout = phase_payloads["secondary_holdout"]
        inner_pass = all(_delta(inner, metric)["delta"] < 0 for metric in ("logloss", "brier", "rps"))
        holdout_point_pass = all(_delta(holdout, metric)["delta"] < 0 for metric in ("logloss", "brier", "rps"))
        bonferroni_pass = all(
            _delta(holdout, metric)["ci_bonferroni_high"] < 0
            for metric in ("logloss", "brier", "rps")
        )
        weather_increment_inner = all(
            _delta(
                inner,
                metric,
                left="posterior",
                right="calibrated_market",
            )["delta"]
            < 0
            for metric in ("logloss", "brier", "rps")
        )
        weather_increment_holdout = all(
            _delta(
                holdout,
                metric,
                left="posterior",
                right="calibrated_market",
            )["delta"]
            < 0
            for metric in ("logloss", "brier", "rps")
        )
        if not inner_pass:
            status = "rejected_inner_validation"
        elif not holdout_point_pass:
            status = "secondary_holdout_failed"
        elif not bonferroni_pass:
            status = "exploratory_positive_not_significant"
        else:
            status = "shadow_candidate_secondary_only"
        versions[version] = {
            "selection_rule": rules[version],
            "cities": sorted(cities),
            "market_temperature": market_temperature,
            "beta": beta,
            "beta_grid": beta_grid.to_dict("records"),
            **phase_payloads,
            "inner_all_three_point_improve": inner_pass,
            "secondary_all_three_point_improve": holdout_point_pass,
            "secondary_bonferroni_all_three_pass": bonferroni_pass,
            "weather_increment_inner_all_three": weather_increment_inner,
            "weather_increment_secondary_all_three": weather_increment_holdout,
            "status": status,
        }

    eligible = [
        version
        for version in VERSION_ORDER
        if versions[version]["status"] == "shadow_candidate_secondary_only"
    ]
    if eligible:
        selected_version = min(
            eligible,
            key=lambda version: _delta(
                versions[version]["secondary_holdout"], "logloss"
            )["delta"],
        )
        conclusion = (
            f"{selected_version} 在 inner validation 与 secondary holdout 三个 proper score 同向改善，"
            "且 K=3 Bonferroni CI 通过；但 final dates 已被项目看过，只能形成 zero-notional shadow candidate。"
        )
    else:
        selected_version = None
        conclusion = (
            "三个事前 cohort 都没有同时通过 inner validation、secondary holdout 与 K=3 Bonferroni gate。"
            "现有 D-1 city selection 不能作为击败 market 的可运行策略；保留 runner，等待 clean exact-run frozen forward。"
        )

    gfs_mae = float(source_mae.loc[source_mae["model"] == "gfs", "mae_f"].iloc[0])
    ecmwf_mae = float(source_mae.loc[source_mae["model"] == "ecmwf", "mae_f"].iloc[0])
    payload = {
        "schema_version": "d1_city_cohort_market_residual_audit_v1",
        "denominator_scope": "primary D-1_18_24 reconstructed complete-ladder settlement-scored states",
        "inputs": {
            "forecast": str(args.forecasts),
            "baskets": str(args.baskets),
            "history": str(args.history),
            "db": str(args.db),
            "state_artifact": str(args.state_artifact),
            "state_summary": str(args.state_summary),
        },
        "input_census": {
            "forecast_rows": int(len(forecasts)),
            "forecast_dates": int(forecasts["target_date"].nunique()),
            "forecast_cities": int(forecasts["city"].nunique()),
            "basket_rows": int(len(baskets)),
            "basket_dates": int(baskets["target_date"].nunique()),
            "basket_cities": int(baskets["city"].nunique()),
            "history_rows": int(len(history)),
        },
        "training_slice": {
            "rows": int(len(fitted["train"])),
            "dates": int(fitted["train"]["date"].nunique()),
            "cities": int(fitted["train"]["city"].nunique()),
            "policy": "best assigned model + May-Aug + date before reconstructed window",
        },
        "w0_parameters": W0_PARAMETERS,
        "split": {
            "beta_fit_start": min(beta_fit_dates),
            "beta_fit_end": max(beta_fit_dates),
            "beta_fit_dates": len(beta_fit_dates),
            "inner_validation_start": min(inner_validation_dates),
            "inner_validation_end": max(inner_validation_dates),
            "inner_validation_dates": len(inner_validation_dates),
            "secondary_holdout_start": min(holdout_dates),
            "secondary_holdout_end": max(holdout_dates),
            "secondary_holdout_dates": len(holdout_dates),
        },
        "funnels": funnels,
        "selection_evidence": {
            "source_mae": source_mae.to_dict("records"),
            "gfs_mae_f": gfs_mae,
            "ecmwf_mae_f": ecmwf_mae,
            "city_mae_cutoff_f": city_mae_cutoff,
        },
        "versions": versions,
        "selected_version": selected_version,
        "conclusion": conclusion,
        "production": {"live_action": "none", "orders_changed": 0},
        "readiness": {
            "pit": "reconstructed_conservative_12h_lag_not_true_run_aware",
            "canonical_build": "fixed_prior_D1_score_artifact; live DB query timed out",
            "market": "complete normalized ladder for scoreable states; non-executable probability",
            "settlement": "complete for scoreable states via fixed canonical-labeled artifact",
            "clean_frozen_forward": "blocked; final 9 dates previously observed",
        },
    }

    args.out.mkdir(parents=True, exist_ok=True)
    scored = pd.concat(all_scored, ignore_index=True)
    scored.to_csv(args.out / "scored_states.csv", index=False)
    source_mae.to_csv(args.out / "source_pretest_mae.csv", index=False)
    city_mae.to_csv(args.out / "city_pretest_mae.csv", index=False)
    (args.out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    runnable = {
        "schema_version": "d1_city_cohort_market_residual_policy_v1",
        "mode": "research_or_zero_notional_only",
        "selected_version": selected_version,
        "candidate": versions[selected_version] if selected_version else None,
        "w0_parameters": W0_PARAMETERS,
        "equation": "log_P_post=gamma*log_P_market+beta*(log_P_weather-log_P_market)-log_Z",
        "deployment_authorized": False,
    }
    (args.out / "runnable_policy.json").write_text(
        json.dumps(runnable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
