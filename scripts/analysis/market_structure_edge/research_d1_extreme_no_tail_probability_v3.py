#!/usr/bin/env python3
"""Expanding-OOF forecast tail probability for the D-1 extreme-NO basket.

The model estimates the binary probability that either outer listed condition
wins.  Training uses only earlier target dates and PIT forecast geometry.
Trading evaluation stays on the v1 first-executable direct-NO-ask denominator.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import beta
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import research_d1_extreme_no_basket_v1 as base
import research_d1_extreme_no_forecast_overlay_v2 as overlay


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-27-d1-extreme-no-tail-probability-v3.md"
)
FEATURES = [
    "forecast_low_distance_steps",
    "forecast_high_distance_steps",
    "forecast_min_cushion_steps",
    "forecast_inside_outer_condition",
    "hours_to_target",
    "rung_count",
]
MIN_TRAIN_DATES = 5
MODEL_C = 0.1
BUFFERS = {
    "forecast_ev_buffer_0bp": 0.0,
    "forecast_ev_buffer_50bp": 0.005,
    "forecast_ev_buffer_100bp": 0.01,
}
SEED = 2026072730


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def add_model_features(rows: pd.DataFrame) -> pd.DataFrame:
    out = overlay.add_forecast_geometry(rows)
    step = pd.to_numeric(out["native_step"], errors="coerce")
    out["forecast_low_distance_steps"] = (
        pd.to_numeric(out["forecast_distance_low_native"], errors="coerce")
        / step
    )
    out["forecast_high_distance_steps"] = (
        pd.to_numeric(out["forecast_distance_high_native"], errors="coerce")
        / step
    )
    out["forecast_inside_outer_condition"] = (
        ~out["forecast_excludes_both_extremes"].astype(bool)
    ).astype(float)
    out["hours_to_target"] = -pd.to_numeric(
        out["local_hours_from_target_midnight"], errors="coerce"
    )
    for column in (
        "forecast_low_distance_steps",
        "forecast_high_distance_steps",
        "forecast_min_cushion_steps",
        "hours_to_target",
        "rung_count",
    ):
        out[column] = pd.to_numeric(out[column], errors="coerce").clip(-24, 24)
    return out


def forecast_ready_mask(rows: pd.DataFrame) -> pd.Series:
    pit = rows["forecast_pit_valid"].astype("boolean").fillna(False)
    return pit & rows["forecast_max_f"].notna()


def calibration_universe(rows: pd.DataFrame) -> pd.DataFrame:
    ready = rows[
        rows["local_hours_from_target_midnight"].between(
            -24.0, 0.0, inclusive="left"
        )
        & forecast_ready_mask(rows)
    ].copy()
    outputs: list[pd.DataFrame] = []
    for policy, (start_hour, end_hour) in base.POLICIES.items():
        eligible = ready[
            ready["local_hours_from_target_midnight"].ge(start_hour)
            & ready["local_hours_from_target_midnight"].lt(end_hour)
        ].sort_values(["city", "target_date", "decision_ts_utc"])
        first = eligible.drop_duplicates(
            ["city", "target_date"], keep="first"
        ).copy()
        first["policy"] = policy
        outputs.append(first)
    return pd.concat(outputs, ignore_index=True)


def model_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    solver="lbfgs",
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def expanding_oof(
    calibration: pd.DataFrame, evaluation: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored: list[pd.DataFrame] = []
    training_audit: list[dict[str, Any]] = []
    for policy, test_policy in evaluation.groupby("policy"):
        train_policy = calibration[calibration["policy"].eq(policy)]
        for target_date in sorted(test_policy["target_date"].unique()):
            train = train_policy[train_policy["target_date"].lt(target_date)]
            test = test_policy[test_policy["target_date"].eq(target_date)].copy()
            train_dates = int(train["target_date"].nunique())
            positives = int(train["tail_hit"].sum())
            negatives = int(len(train) - positives)
            audit = {
                "policy": policy,
                "target_date": target_date,
                "train_rows": int(len(train)),
                "train_dates": train_dates,
                "train_tail_hits": positives,
                "test_rows": int(len(test)),
                "scored": 0,
                "skip_reason": None,
            }
            if train_dates < MIN_TRAIN_DATES:
                audit["skip_reason"] = "insufficient_prior_dates"
                training_audit.append(audit)
                continue
            if positives == 0 or negatives == 0:
                audit["skip_reason"] = "single_class_prior"
                training_audit.append(audit)
                continue
            model = model_pipeline()
            model.fit(train[FEATURES], train["tail_hit"].astype(int))
            test["forecast_p_tail"] = model.predict_proba(test[FEATURES])[:, 1]
            test["train_rows"] = len(train)
            test["train_dates"] = train_dates
            test["train_tail_hits"] = positives
            scored.append(test)
            audit["scored"] = int(len(test))
            training_audit.append(audit)
    if not scored:
        return pd.DataFrame(), pd.DataFrame(training_audit)
    out = pd.concat(scored, ignore_index=True)
    out["forecast_p_tail"] = out["forecast_p_tail"].clip(1e-6, 1 - 1e-6)
    out["market_p_tail"] = pd.to_numeric(
        out["market_tail_probability"], errors="coerce"
    ).clip(1e-6, 1 - 1e-6)
    label = out["tail_hit"].astype(float)
    out["forecast_logloss"] = -(
        label * np.log(out["forecast_p_tail"])
        + (1.0 - label) * np.log(1.0 - out["forecast_p_tail"])
    )
    out["market_logloss"] = -(
        label * np.log(out["market_p_tail"])
        + (1.0 - label) * np.log(1.0 - out["market_p_tail"])
    )
    out["forecast_brier"] = np.square(out["forecast_p_tail"] - label)
    out["market_brier"] = np.square(out["market_p_tail"] - label)
    return out, pd.DataFrame(training_audit)


def date_mean_ci(
    rows: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    daily = rows.groupby("target_date")[column].mean()
    if len(daily) < 3:
        return (math.nan, math.nan)
    values = daily.to_numpy(float)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(values, size=(draws, len(values)), replace=True).mean(
        axis=1
    )
    low, high = np.quantile(sampled, [0.025, 0.975])
    return float(low), float(high)


def probability_summary(
    rows: pd.DataFrame, policy: str, *, draws: int, seed: int
) -> dict[str, Any]:
    group = rows[rows["policy"].eq(policy)].copy()
    group["logloss_delta"] = (
        group["forecast_logloss"] - group["market_logloss"]
    )
    group["brier_delta"] = group["forecast_brier"] - group["market_brier"]
    ll_ci = date_mean_ci(
        group, "logloss_delta", draws=draws, seed=seed
    )
    br_ci = date_mean_ci(
        group, "brier_delta", draws=draws, seed=seed + 1
    )
    return {
        "policy": policy,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()),
        "tail_hits": int(group["tail_hit"].sum()),
        "forecast_mean_p_tail": float(group["forecast_p_tail"].mean()),
        "market_mean_p_tail": float(group["market_p_tail"].mean()),
        "observed_tail_rate": float(group["tail_hit"].mean()),
        "forecast_logloss": float(
            group.groupby("target_date")["forecast_logloss"].mean().mean()
        ),
        "market_logloss": float(
            group.groupby("target_date")["market_logloss"].mean().mean()
        ),
        "logloss_delta": float(
            group.groupby("target_date")["logloss_delta"].mean().mean()
        ),
        "logloss_delta_ci_low": ll_ci[0],
        "logloss_delta_ci_high": ll_ci[1],
        "forecast_brier": float(
            group.groupby("target_date")["forecast_brier"].mean().mean()
        ),
        "market_brier": float(
            group.groupby("target_date")["market_brier"].mean().mean()
        ),
        "brier_delta": float(
            group.groupby("target_date")["brier_delta"].mean().mean()
        ),
        "brier_delta_ci_low": br_ci[0],
        "brier_delta_ci_high": br_ci[1],
    }


def exact_tail_ci(hits: int, baskets: int) -> tuple[float, float]:
    if baskets == 0:
        return (math.nan, math.nan)
    low = 0.0 if hits == 0 else float(
        beta.ppf(0.025, hits, baskets - hits + 1)
    )
    high = 1.0 if hits == baskets else float(
        beta.ppf(0.975, hits + 1, baskets - hits)
    )
    return low, high


def trade_summaries(
    scored: pd.DataFrame, *, draws: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []
    for policy_index, (policy, policy_rows) in enumerate(
        scored.groupby("policy")
    ):
        for buffer_index, (name, buffer) in enumerate(BUFFERS.items()):
            selected = policy_rows[
                policy_rows["forecast_p_tail"].lt(
                    policy_rows["break_even_tail_probability"] - buffer
                )
            ].copy()
            selected["selector"] = name
            selected["probability_buffer"] = buffer
            record = base.summarize(
                selected,
                f"{policy}|{name}",
                draws=draws,
                seed=SEED + policy_index * 20 + buffer_index * 2,
            )
            hits = int(selected["tail_hit"].sum()) if len(selected) else 0
            low, high = exact_tail_ci(hits, len(selected))
            record.update(
                {
                    "policy": policy,
                    "selector": name,
                    "probability_buffer": buffer,
                    "tail_hit_ci_low": low,
                    "tail_hit_ci_high": high,
                }
            )
            summaries.append(record)
            selected_parts.append(selected)
    selected_frame = (
        pd.concat(selected_parts, ignore_index=True)
        if selected_parts
        else pd.DataFrame()
    )
    return pd.DataFrame(summaries), selected_frame


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.2%}"


def probability_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | rows | dates | observed tail | forecast p | market p | logloss Δ vs market (95% CI) | Brier Δ vs market (95% CI) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {int(row['rows'])} | {int(row['dates'])} | "
            f"{fmt_pct(row['observed_tail_rate'])} | "
            f"{fmt_pct(row['forecast_mean_p_tail'])} | "
            f"{fmt_pct(row['market_mean_p_tail'])} | "
            f"{row['logloss_delta']:+.5f} "
            f"[{row['logloss_delta_ci_low']:+.5f}, {row['logloss_delta_ci_high']:+.5f}] | "
            f"{row['brier_delta']:+.5f} "
            f"[{row['brier_delta_ci_low']:+.5f}, {row['brier_delta_ci_high']:+.5f}] |"
        )
    return lines


def trade_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | selector | baskets | dates | tail hit (exact 95% CI) | break-even tail | ROI (95% CI) | excess vs market (95% CI) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {row['selector']} | "
            f"{int(row['baskets'])} | {int(row['dates'])} | "
            f"{fmt_pct(row.get('tail_hit_rate'))} "
            f"[{fmt_pct(row.get('tail_hit_ci_low'))}, "
            f"{fmt_pct(row.get('tail_hit_ci_high'))}] | "
            f"{fmt_pct(row.get('break_even_tail_rate'))} | "
            f"{fmt_pct(row.get('fee_adjusted_roi'))} "
            f"[{fmt_pct(row.get('roi_ci_low'))}, "
            f"{fmt_pct(row.get('roi_ci_high'))}] | "
            f"{fmt_pct(row.get('excess_roi_vs_market'))} "
            f"[{fmt_pct(row.get('excess_ci_low'))}, "
            f"{fmt_pct(row.get('excess_ci_high'))}] |"
        )
    return lines


def db_snapshot(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        tmax = conn.execute(
            """
            SELECT COUNT(*), MIN(target_date), MAX(target_date)
            FROM tmax_v2_canonical_state_effective
            """
        ).fetchone()
        unknown = conn.execute(
            """
            SELECT COUNT(*)
            FROM tmax_v2_forecast_captures
            WHERE target_date BETWEEN '2026-07-04' AND '2026-07-08'
              AND lineage_status <> 'pit_verified_capture'
            """
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "db_mtime_utc": pd.Timestamp(
            db.stat().st_mtime, unit="s", tz="UTC"
        ).isoformat(),
        "canonical_state_rows": int(tmax[0]),
        "canonical_first_target_date": tmax[1],
        "canonical_last_target_date": tmax[2],
        "historical_unknown_available_at_forecast_captures_20260704_08": int(
            unknown
        ),
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    raw = base.load_rows(args.db)
    snapshots, base_funnel = base.build_snapshot_rows(raw)
    forecasts = overlay.load_forecasts(args.db)
    all_joined = add_model_features(
        snapshots.merge(
            forecasts,
            on="ladder_snapshot_id",
            how="left",
            suffixes=("", "_forecast"),
            validate="one_to_one",
        )
    )
    calibration = calibration_universe(all_joined)

    executable = base.select_policies(snapshots)
    evaluation = add_model_features(
        executable.merge(
            forecasts,
            on="ladder_snapshot_id",
            how="left",
            suffixes=("", "_forecast"),
            validate="one_to_one",
        )
    )
    evaluation = evaluation[forecast_ready_mask(evaluation)].copy()
    scored, training_audit = expanding_oof(calibration, evaluation)
    if scored.empty:
        raise RuntimeError("no expanding-OOF rows could be scored")

    probability_rows = [
        probability_summary(
            scored,
            policy,
            draws=args.draws,
            seed=SEED + index * 10,
        )
        for index, policy in enumerate(sorted(scored["policy"].unique()))
    ]
    probability_frame = pd.DataFrame(probability_rows)
    trade_frame, selected_frame = trade_summaries(
        scored, draws=args.draws
    )

    probability_frame.to_csv(
        args.output_dir / "probability_summary.csv", index=False
    )
    trade_frame.to_csv(args.output_dir / "trade_summary.csv", index=False)
    training_audit.to_csv(
        args.output_dir / "training_audit.csv", index=False
    )
    scored.to_csv(args.output_dir / "oof_scored_baskets.csv", index=False)
    selected_frame.to_csv(
        args.output_dir / "selected_baskets.csv", index=False
    )

    history_dates = sorted(calibration["target_date"].unique())
    snapshot_info = db_snapshot(args.db)
    funnel = {
        **base_funnel,
        "calibration_city_date_policy_rows": int(len(calibration)),
        "calibration_dates": int(calibration["target_date"].nunique()),
        "calibration_tail_hits": int(calibration["tail_hit"].sum()),
        "base_executable_baskets": int(len(executable)),
        "base_executable_dates": int(executable["target_date"].nunique()),
        "forecast_ready_executable_baskets": int(len(evaluation)),
        "expanding_oof_scored_baskets": int(len(scored)),
        "expanding_oof_scored_dates": int(scored["target_date"].nunique()),
        "expanding_oof_tail_hits": int(scored["tail_hit"].sum()),
        "primary_50bp_selected_baskets": int(
            len(
                selected_frame[
                    selected_frame["selector"].eq(
                        "forecast_ev_buffer_50bp"
                    )
                ]
            )
        ),
        "primary_50bp_selected_city_dates": int(
            selected_frame[
                selected_frame["selector"].eq(
                    "forecast_ev_buffer_50bp"
                )
            ][["city", "target_date"]]
            .drop_duplicates()
            .shape[0]
        ),
    }
    probability_pass = bool(
        (probability_frame["logloss_delta_ci_high"] < 0).all()
        and (probability_frame["brier_delta_ci_high"] < 0).all()
    )
    primary = trade_frame[
        trade_frame["selector"].eq("forecast_ev_buffer_50bp")
    ]
    trade_pass = bool(
        not primary.empty
        and primary["baskets"].gt(0).all()
        and primary["roi_ci_low"].gt(0).all()
        and primary["excess_ci_low"].gt(0).all()
    )
    verdict = (
        "shadow_candidate"
        if probability_pass and trade_pass
        else "inconclusive"
    )
    payload = {
        "contract": {
            "target": "estimate PIT P(low outer hit)+P(high outer hit) and compare with same-row market tail probability",
            "state_grain": "first forecast-ready D-1 ladder per city-target_date-policy for training; v1 first-executable basket for evaluation",
            "label": "either outer listed condition is the exact settlement winner",
            "features": FEATURES,
            "model": f"L2 logistic C={MODEL_C}",
            "validation": "strict earlier-target-date expanding OOF",
            "minimum_prior_dates": MIN_TRAIN_DATES,
            "primary_trade_rule": "forecast_p_tail < break_even_tail_probability - 0.005",
            "fee": "official Weather taker feeRate 0.05 on both NO legs",
            "variants_k": len(BUFFERS),
            "trade_class": "research_replay",
        },
        "data_snapshot": snapshot_info,
        "training_target_dates": history_dates,
        "funnel": funnel,
        "probability_summary": probability_rows,
        "trade_summary": trade_frame.to_dict("records"),
        "gates": {
            "significance": "PASS" if trade_pass else "FAIL",
            "baseline": "PASS" if probability_pass else "FAIL",
            "forward": "NA_post_hoc_expanding_oof_not_frozen_forward",
            "conclusion": verdict,
        },
        "verdict": verdict,
        "action": "do_not_shadow_or_live"
        if verdict == "inconclusive"
        else "zero_notional_shadow_only",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    primary_selected = primary["baskets"].sum() if not primary.empty else 0
    lines = [
        "# D-1 Extreme NO Tail Probability v3",
        "",
        "> 2026-07-27；research replay；zero notional；不改 live。",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`runtime/weather.db` 的 canonical Tmax v2 ladder / PIT forecast / `settlement_outcomes`；DB mtime UTC `{snapshot_info['db_mtime_utc']}`。",
        f"- canonical state：{snapshot_info['canonical_state_rows']:,} rows，target_date {snapshot_info['canonical_first_target_date']}..{snapshot_info['canonical_last_target_date']}。",
        f"- 本研究 OOF 记录：{len(scored):,} 个 settled executable baskets；unsettled=0，missing_bracket=0。",
        f"- 7/4–8 定向补入的旧 forecast 中，有 {snapshot_info['historical_unknown_available_at_forecast_captures_20260704_08']:,} 条缺可靠 `available_at_utc`，全部排除，不作为 PIT 训练。",
        "",
        "## 结论",
        "",
        (
            "把 point forecast 改成了真正的二元概率问题：用先前 target dates 的"
            " forecast-to-outer-bracket geometry 估计 "
            "`P(low hit)+P(high hit)`，逐日 expanding OOF；再在完全相同的"
            " direct-NO-ask basket 上检验概率和交易。"
        ),
        "",
        "### 概率层",
        "",
    ]
    lines.extend(probability_table(probability_frame))
    lines += [
        "",
        "负 delta 才表示 forecast 模型优于同 rows 的 market tail probability。",
        "",
        "### 交易层",
        "",
    ]
    lines.extend(trade_table(trade_frame))
    lines += [
        "",
        (
            f"主规则固定为 50bp probability safety buffer，共选择 "
            f"{int(primary_selected)} 个 policy-basket，但只有 "
            f"{funnel['primary_50bp_selected_city_dates']} 个独立 city-date。"
            f"裁决：`{verdict}`。"
        ),
        "",
        "## Signal / Evidence Funnel",
        "",
        "Signal funnel（city-date-policy / basket）:",
        "",
        f"- PIT forecast mechanism states：{len(calibration):,}。",
        f"- prior-date expanding OOF scored：{len(scored):,} baskets / {scored['target_date'].nunique()} dates。",
        f"- 50bp primary selected：{int(primary_selected)} policy-baskets / {funnel['primary_50bp_selected_city_dates']} unique city-dates。",
        "",
        "Evidence funnel（basket）:",
        "",
        f"- complete PIT settled ladder snapshots：{base_funnel['complete_pit_settled_snapshots']:,}。",
        f"- v1 executable D-1 baskets：{len(executable):,}。",
        f"- PIT forecast-ready executable：{len(evaluation):,}。",
        f"- expanding OOF + same-row market baseline：{len(scored):,}。",
        "- actual fill：0（research replay；不冒充 paper/live fill）。",
        "",
        "## Gate 与边界",
        "",
        f"- significance：{payload['gates']['significance']}；交易 ROI / excess 按 target_date block bootstrap。",
        f"- baseline：{payload['gates']['baseline']}；概率层用同 rows market tail proper score。",
        "- forward：NA；虽然每行预测只用更早 target dates，且要求至少 5 个独立训练日，但规则是本轮事后提出，不是 frozen forward。",
        f"- conclusion：`{verdict}`。",
        "",
        "8 环：覆盖概率判别/校准、统计推断、PIT 盘口、fee-adjusted execution replay、"
        "date correlation 与 market baseline；没有真实 fill、容量和 frozen forward。",
        "",
        "Artifacts:",
        "",
        "- `scripts/analysis/market_structure_edge/research_d1_extreme_no_tail_probability_v3.py`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/summary.json`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/probability_summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/trade_summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/training_audit.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/oof_scored_baskets.csv`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
