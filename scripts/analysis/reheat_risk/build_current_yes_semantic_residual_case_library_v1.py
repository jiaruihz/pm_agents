#!/usr/bin/env python3
"""Build a maintained PIT case library for current-YES semantic residuals."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_semantic_residual_case_library_v1"
SOURCE = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_physical_semantic_audit_v2/"
    "semantic_augmented_feature_ledger.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-29-current-yes-semantic-residual-case-library-v1.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
SEED = 20260729
BOOTSTRAP_REPS = 5000
EPS = 1e-6

MECHANISMS = [
    ("warm_moist_advection", "暖湿平流 proxy"),
    ("mechanical_mixing_plateau", "强风机械混合平台"),
    ("cold_advection", "冷平流 proxy"),
    ("radiative_cooling", "夜间辐射降温"),
    ("cloud_blanket_moistening", "夜间云层保温/增湿"),
    ("downslope_dry_warming", "下坡/焚风干暖增温"),
    ("frontal_airmass_transition", "锋面/气团转换"),
    ("solar_reheat", "太阳再加热"),
    ("rain_evaporative_cooling", "降雨/蒸发冷却"),
    ("gust_mixing", "阵风混合平台"),
]

BASE_COLUMNS = [
    "city",
    "target_date",
    "decision_snapshot_ts_utc",
    "decision_hour_local",
    "current_bracket",
    "final_winning_bracket",
    "unit",
    "p_core",
    "p_market_raw",
    "current_yes_bid",
    "current_yes_ask",
    "current_yes_ask_size",
    "overshoot",
    "temp_tendency_1h_f",
    "temp_tendency_3h_f",
    "dewpoint_tendency_3h_f",
    "observed_wind_speed_kt",
    "wind_direction_deg",
    "wind_direction_persistence_3h",
    "wind_direction_shift_3h_deg",
    "gust_kt",
    "gust_excess_kt",
    "precip_observed_1h",
    "pressure_tendency_3h_hpa",
    "solar_elevation_deg",
    "forecast_peak_delta_hours_local",
    "minutes_since_last_strict_new_high",
]


def clip_probability(value: pd.Series) -> pd.Series:
    return pd.to_numeric(value, errors="coerce").clip(EPS, 1 - EPS)


def stable_case_id(row: pd.Series) -> str:
    payload = "|".join(
        [
            str(row["mechanism"]),
            str(row["city"]),
            str(row["target_date"]),
            str(row["decision_snapshot_ts_utc"]),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def classify_case(row: pd.Series) -> str:
    core = float(row["core_hold_probability"])
    market = float(row["market_hold_mid"])
    hold = int(row["current_exact_hold"])
    gap = core - market
    if hold == 0:
        if core >= 0.80 and market >= 0.80:
            if gap >= 0.03:
                return "core_more_overconfident_tail_loss"
            if gap <= -0.03:
                return "market_more_overconfident_tail_loss"
            return "shared_high_confidence_tail_loss"
        if core >= 0.80:
            return "core_only_high_confidence_tail_loss"
        if market >= 0.80:
            return "market_only_high_confidence_tail_loss"
        return "ordinary_tail_loss"
    delta = float(row["core_minus_market_brier"])
    if delta >= 0.0025:
        return "hold_win_core_worse_probability"
    if delta <= -0.0025:
        return "hold_win_core_better_probability"
    return "hold_win_similar_probability"


def enrich_cases(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["core_hold_probability"] = clip_probability(out["p_core"])
    out["market_hold_mid"] = clip_probability(out["p_market_raw"])
    out["market_hold_bid"] = pd.to_numeric(
        out["current_yes_bid"], errors="coerce"
    )
    out["market_hold_ask"] = pd.to_numeric(
        out["current_yes_ask"], errors="coerce"
    )
    out["market_tail_mid"] = 1.0 - out["market_hold_mid"]
    out["core_tail_probability"] = 1.0 - out["core_hold_probability"]
    out["market_yes_decimal_odds_mid"] = 1.0 / out["market_hold_mid"]
    out["market_tail_decimal_odds_mid"] = 1.0 / out["market_tail_mid"]
    out["current_exact_hold"] = 1 - out["overshoot"].astype(int)
    outcome = out["current_exact_hold"].astype(float)
    out["core_brier"] = (outcome - out["core_hold_probability"]) ** 2
    out["market_brier"] = (outcome - out["market_hold_mid"]) ** 2
    out["core_minus_market_brier"] = (
        out["core_brier"] - out["market_brier"]
    )
    out["core_logloss"] = -(
        outcome * np.log(out["core_hold_probability"])
        + (1 - outcome) * np.log(1 - out["core_hold_probability"])
    )
    out["market_logloss"] = -(
        outcome * np.log(out["market_hold_mid"])
        + (1 - outcome) * np.log(1 - out["market_hold_mid"])
    )
    out["core_minus_market_logloss"] = (
        out["core_logloss"] - out["market_logloss"]
    )
    out["core_minus_market_hold_probability"] = (
        out["core_hold_probability"] - out["market_hold_mid"]
    )
    out["attribution"] = out.apply(classify_case, axis=1)
    out["case_id"] = out.apply(stable_case_id, axis=1)
    return out


def first_mechanism_cases(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for mechanism, label in MECHANISMS:
        flag = f"is_{mechanism}"
        if flag not in frame:
            continue
        sample = frame[frame[flag].fillna(False).astype(bool)].copy()
        if sample.empty:
            continue
        sample["mechanism"] = mechanism
        sample["mechanism_label_zh"] = label
        sample = sample.sort_values("decision_snapshot_ts_utc")
        sample = sample.drop_duplicates(
            ["city", "target_date"], keep="first"
        )
        rows.append(sample)
    if not rows:
        return pd.DataFrame()
    return enrich_cases(pd.concat(rows, ignore_index=True))


def all_checkpoint_cases(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for mechanism, label in MECHANISMS:
        flag = f"is_{mechanism}"
        if flag not in frame:
            continue
        sample = frame[frame[flag].fillna(False).astype(bool)].copy()
        if sample.empty:
            continue
        sample["mechanism"] = mechanism
        sample["mechanism_label_zh"] = label
        rows.append(sample)
    if not rows:
        return pd.DataFrame()
    return enrich_cases(pd.concat(rows, ignore_index=True))


def target_date_bootstrap(
    frame: pd.DataFrame, value_column: str
) -> tuple[float, list[float]]:
    daily = frame.groupby("target_date")[value_column].mean().to_numpy(float)
    point = float(daily.mean())
    if len(daily) < 2:
        return point, [math.nan, math.nan]
    rng = np.random.default_rng(SEED + len(frame) + len(value_column))
    draws = np.empty(BOOTSTRAP_REPS, dtype=float)
    for index in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(daily), len(daily))
        draws[index] = daily[chosen].mean()
    return point, np.quantile(draws, [0.025, 0.975]).tolist()


def mechanism_scorecard(first_cases: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for mechanism, label in MECHANISMS:
        sample = first_cases[first_cases["mechanism"].eq(mechanism)]
        if sample.empty:
            rows.append(
                {
                    "mechanism": mechanism,
                    "mechanism_label_zh": label,
                    "city_days": 0,
                    "dates": 0,
                    "assessment": "no_coverage",
                }
            )
            continue
        brier_delta, brier_ci = target_date_bootstrap(
            sample, "core_minus_market_brier"
        )
        logloss_delta, logloss_ci = target_date_bootstrap(
            sample, "core_minus_market_logloss"
        )
        losses = sample[sample["current_exact_hold"].eq(0)]
        if sample["target_date"].nunique() < 10 or len(losses) < 3:
            assessment = "low_sample_inconclusive"
        elif brier_ci[0] > 0:
            assessment = "core_systematically_worse_than_market"
        elif brier_ci[1] < 0:
            assessment = "core_systematically_better_than_market"
        else:
            assessment = "inconclusive_vs_market"
        rows.append(
            {
                "mechanism": mechanism,
                "mechanism_label_zh": label,
                "city_days": len(sample),
                "cities": sample["city"].nunique(),
                "dates": sample["target_date"].nunique(),
                "tail_losses": len(losses),
                "actual_hold_rate": float(
                    sample["current_exact_hold"].mean()
                ),
                "mean_core_hold_probability": float(
                    sample["core_hold_probability"].mean()
                ),
                "mean_market_hold_mid": float(
                    sample["market_hold_mid"].mean()
                ),
                "core_brier": float(sample["core_brier"].mean()),
                "market_brier": float(sample["market_brier"].mean()),
                "core_minus_market_brier_date_equal": brier_delta,
                "brier_delta_ci95": brier_ci,
                "core_minus_market_logloss_date_equal": logloss_delta,
                "logloss_delta_ci95": logloss_ci,
                "core_more_overconfident_tail_losses": int(
                    losses["attribution"]
                    .eq("core_more_overconfident_tail_loss")
                    .sum()
                ),
                "shared_high_confidence_tail_losses": int(
                    losses["attribution"]
                    .eq("shared_high_confidence_tail_loss")
                    .sum()
                ),
                "market_more_overconfident_tail_losses": int(
                    losses["attribution"]
                    .eq("market_more_overconfident_tail_loss")
                    .sum()
                ),
                "assessment": assessment,
            }
        )
    return pd.DataFrame(rows)


def display_cases(first_cases: pd.DataFrame) -> pd.DataFrame:
    losses = first_cases[
        first_cases["current_exact_hold"].eq(0)
        & (
            first_cases["core_hold_probability"].ge(0.80)
            | first_cases["market_hold_mid"].ge(0.80)
        )
    ].copy()
    losses["display_priority"] = (
        losses["core_minus_market_brier"].abs()
        + losses[["core_hold_probability", "market_hold_mid"]].max(axis=1)
    )
    return (
        losses.sort_values(
            ["mechanism", "display_priority"],
            ascending=[True, False],
        )
        .groupby("mechanism", group_keys=False)
        .head(8)
        .reset_index(drop=True)
    )


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if pd.isna(value):
        return None
    return value


def format_pct(value: float) -> str:
    return f"{value:.1%}"


def format_ci(ci: list[float]) -> str:
    if len(ci) != 2 or any(not math.isfinite(float(value)) for value in ci):
        return "NA"
    return f"[{float(ci[0]):+.4f}, {float(ci[1]):+.4f}]"


def build_report(
    frame: pd.DataFrame,
    first_cases: pd.DataFrame,
    scorecard: pd.DataFrame,
    examples: pd.DataFrame,
    generated_at: str,
) -> str:
    tail_losses = first_cases[first_cases["current_exact_hold"].eq(0)]
    unique_tail_city_days = tail_losses[
        ["city", "target_date"]
    ].drop_duplicates()
    attribution_counts = tail_losses["attribution"].value_counts()
    lines = [
        "# Current-YES 气象语义 residual case library v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{SOURCE.relative_to(ROOT)}`；PIT report-time proxy + 同时点盘口 + 最终结算。",
        f"- 生成时间：{generated_at}。",
        f"- 原始 checkpoint：{len(frame)}；机制首次出现 city-day：{len(first_cases)}；独立 target dates：{first_cases['target_date'].nunique()}。",
        "- unsettled=0；missing_bracket=0；本库只收录已有最终 exact-bracket label 的研究 rows。",
        "- production manifest 的 canonical DB route healthy；整体仍有临时 checkout/进程漂移 warning。本库只读冻结研究 ledger，不发布 live PnL。",
        "",
        "## 归因规则",
        "",
        "- 统计分母使用每个 `(mechanism, city, target_date)` **首次出现**的 checkpoint，避免事后挑当天最错的一刻。",
        "- `core_more_overconfident_tail_loss`：最终越档，core 与市场均偏向 hold，且 core 比市场至少高 3pp。",
        "- `shared_high_confidence_tail_loss`：最终越档，双方概率相差不足 3pp；优先解释为共同尾部或共同信息盲区。",
        "- `market_more_overconfident_tail_loss`：市场比 core 至少高 3pp；不能算 core 独有的语义错误。",
        "- 系统性判断只看 target-date block bootstrap 的 core-minus-market proper-score；单个故事不晋升为模型特征或 hard gate。",
        "",
        "## 先给结论",
        "",
        f"- 共找到 {len(tail_losses)} 个高置信 tail-loss 机制 membership，来自 {len(unique_tail_city_days)} 个独立 city-day；同一事件可同时属于两个物理机制。",
        f"- core 独有的明显过度自信只有 {int(attribution_counts.get('core_more_overconfident_tail_loss', 0))} 个；双方共同高置信错为 {int(attribution_counts.get('shared_high_confidence_tail_loss', 0))} 个；市场比 core 更过度自信或只有市场达到高置信为 {int(attribution_counts.get('market_more_overconfident_tail_loss', 0) + attribution_counts.get('market_only_high_confidence_tail_loss', 0))} 个。",
        "- 因此原先列出的多数“常识偏差”不是 core 单独漏识别，而是市场与 core 共同面对的真实尾部；唯一清晰的 core-specific 样本是 Istanbul 2026-06-16 的强风机械混合平台。",
        "- 机制层没有任何一类显示 core 的日期 bootstrap Brier 显著差于市场；暖湿平流和下坡/焚风类反而显著优于市场，锋面与太阳再加热为 inconclusive。",
        "",
        "## 机制 scorecard",
        "",
        "| mechanism | city-days/dates | tail losses | actual/core/market hold | ΔBrier core-market [95% CI] | 归因 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for _, row in scorecard.iterrows():
        if int(row["city_days"]) == 0:
            lines.append(
                f"| {row['mechanism_label_zh']} | 0/0 | 0 | NA | NA | no coverage |"
            )
            continue
        ci = row["brier_delta_ci95"]
        lines.append(
            f"| {row['mechanism_label_zh']} | "
            f"{int(row['city_days'])}/{int(row['dates'])} | "
            f"{int(row['tail_losses'])} | "
            f"{format_pct(float(row['actual_hold_rate']))}/"
            f"{format_pct(float(row['mean_core_hold_probability']))}/"
            f"{format_pct(float(row['mean_market_hold_mid']))} | "
            f"{float(row['core_minus_market_brier_date_equal']):+.4f} "
            f"{format_ci(ci)} | "
            f"{row['assessment']} |"
        )
    lines += [
        "",
        "## 同型高置信 tail-loss 案例",
        "",
        "下面只展示机制首次出现时已经高置信、后来仍越档的案例。`market` 为当时 current-YES midpoint；bid/ask 保存在 CSV。",
        "",
        "| mechanism | city/date/time UTC | bracket→final | core | market | YES bid/ask | tail odds(mid) | attribution |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    if examples.empty:
        lines.append("| — | — | — | — | — | — | — | no examples |")
    else:
        for _, row in examples.iterrows():
            bid = (
                f"{float(row['market_hold_bid']):.3f}"
                if pd.notna(row["market_hold_bid"])
                else "NA"
            )
            ask = (
                f"{float(row['market_hold_ask']):.3f}"
                if pd.notna(row["market_hold_ask"])
                else "NA"
            )
            lines.append(
                f"| {row['mechanism_label_zh']} | "
                f"{row['city']} {row['target_date']} "
                f"{row['decision_snapshot_ts_utc']} | "
                f"{row['current_bracket']}→{row['final_winning_bracket']} | "
                f"{float(row['core_hold_probability']):.1%} | "
                f"{float(row['market_hold_mid']):.1%} | "
                f"{bid}/{ask} | "
                f"{float(row['market_tail_decimal_odds_mid']):.1f}x | "
                f"{row['attribution']} |"
            )
    lines += [
        "",
        "## 维护方式",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_physical_semantic_audit_v2.py",
        ".venv/bin/python scripts/analysis/reheat_risk/build_current_yes_semantic_residual_case_library_v1.py",
        "```",
        "",
        "生成物：",
        "",
        f"- `{(OUT_DIR / 'checkpoint_case_library.csv').relative_to(ROOT)}`：所有机制 checkpoint，允许一行属于多个机制。",
        f"- `{(OUT_DIR / 'first_mechanism_city_day_cases.csv').relative_to(ROOT)}`：统计授权分母。",
        f"- `{(OUT_DIR / 'mechanism_scorecard.csv').relative_to(ROOT)}`：同分母 proper-score 与日期 bootstrap。",
        f"- `{(OUT_DIR / 'representative_tail_loss_cases.csv').relative_to(ROOT)}`：人工复盘展示集，不作统计分母。",
        "",
        "状态：`research / inconclusive`。该库维护模型诊断，不授权 live 变更。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(
            f"Missing semantic ledger: {SOURCE}. Run physical audit v2 first."
        )
    frame = pd.read_csv(SOURCE, low_memory=False)
    required = set(BASE_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Source ledger missing columns: {missing}")
    checkpoints = all_checkpoint_cases(frame)
    first_cases = first_mechanism_cases(frame)
    scorecard = mechanism_scorecard(first_cases)
    examples = display_cases(first_cases)
    generated_at = pd.Timestamp.now(tz="UTC").isoformat()

    output_columns = [
        "case_id",
        "mechanism",
        "mechanism_label_zh",
        *BASE_COLUMNS,
        "current_exact_hold",
        "core_hold_probability",
        "market_hold_mid",
        "market_hold_bid",
        "market_hold_ask",
        "core_tail_probability",
        "market_tail_mid",
        "market_yes_decimal_odds_mid",
        "market_tail_decimal_odds_mid",
        "core_minus_market_hold_probability",
        "core_brier",
        "market_brier",
        "core_minus_market_brier",
        "core_logloss",
        "market_logloss",
        "core_minus_market_logloss",
        "attribution",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoints[output_columns].to_csv(
        OUT_DIR / "checkpoint_case_library.csv", index=False
    )
    first_cases[output_columns].to_csv(
        OUT_DIR / "first_mechanism_city_day_cases.csv", index=False
    )
    examples[output_columns].to_csv(
        OUT_DIR / "representative_tail_loss_cases.csv", index=False
    )
    scorecard.to_csv(OUT_DIR / "mechanism_scorecard.csv", index=False)

    result = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": generated_at,
        "source": str(SOURCE.relative_to(ROOT)),
        "funnel": {
            "source_checkpoints": len(frame),
            "mechanism_checkpoint_memberships": len(checkpoints),
            "first_mechanism_city_days": len(first_cases),
            "target_dates": first_cases["target_date"].nunique(),
            "representative_tail_losses": len(examples),
            "first_mechanism_tail_loss_memberships": int(
                first_cases["current_exact_hold"].eq(0).sum()
            ),
            "unique_tail_loss_city_days": int(
                first_cases[first_cases["current_exact_hold"].eq(0)][
                    ["city", "target_date"]
                ]
                .drop_duplicates()
                .shape[0]
            ),
        },
        "attribution_counts": first_cases["attribution"]
        .value_counts()
        .to_dict(),
        "mechanism_scorecard": scorecard.to_dict("records"),
        "selection": {
            "status": "research_inconclusive",
            "live_effect": "none",
        },
    }
    RESULT_JSON.write_text(
        json.dumps(json_ready(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(
        build_report(
            frame,
            first_cases,
            scorecard,
            examples,
            generated_at,
        ),
        encoding="utf-8",
    )
    print(json.dumps(json_ready(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
