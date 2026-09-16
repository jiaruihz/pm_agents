#!/usr/bin/env python3
"""Add PIT D-1 forecast support to the two-extreme-NO basket replay.

This is a point-forecast mechanism overlay, not a calibrated probability model.
It tests whether requiring the assigned PIT forecast maximum to exclude both
outer listed conditions improves the same executable basket denominator.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from scipy.stats import beta

import research_d1_extreme_no_basket_v1 as base


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUTPUT = (
    ROOT / "docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-27-d1-extreme-no-forecast-overlay-v2.md"
)
NUMBER_RE = re.compile(r"(?<!\d)-?\d+(?:\.\d+)?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def curve_max_f(curve_json: Any, target_date: str) -> float:
    try:
        rows = json.loads(str(curve_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return math.nan
    values: list[float] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        local_time = str(
            row.get("valid_time_local") or row.get("time_local") or ""
        )
        if local_time[:10] != str(target_date):
            continue
        try:
            value = float(row.get("temperature_f"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return max(values) if values else math.nan


def load_forecasts(db: Path) -> pd.DataFrame:
    query = """
    SELECT
        cs.ladder_snapshot_id,
        cs.decision_ts_utc,
        cs.target_date,
        cs.pit_status,
        cs.forecast_lineage_status,
        cs.forecast_available_at_utc,
        fc.forecast_capture_id,
        fc.forecast_source,
        fc.forecast_model,
        fc.available_at_utc,
        fc.forecast_first_seen_at_utc,
        fc.lineage_status AS forecast_capture_lineage_status,
        fc.normalized_hourly_curve_json
    FROM tmax_v2_canonical_state_effective cs
    JOIN tmax_v2_forecast_captures fc USING (forecast_capture_id)
    WHERE cs.forecast_capture_id IS NOT NULL
      AND cs.forecast_lineage_status = 'pit_verified_capture'
      AND fc.lineage_status = 'pit_verified_capture'
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        rows = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    rows["decision_ts"] = pd.to_datetime(
        rows["decision_ts_utc"], utc=True, errors="coerce"
    )
    rows["forecast_available_ts"] = pd.to_datetime(
        rows["forecast_available_at_utc"].fillna(rows["available_at_utc"]),
        utc=True,
        errors="coerce",
    )
    rows["forecast_pit_valid"] = (
        rows["decision_ts"].notna()
        & rows["forecast_available_ts"].notna()
        & rows["forecast_available_ts"].le(rows["decision_ts"])
    )
    rows["forecast_max_f"] = [
        curve_max_f(curve, target_date)
        for curve, target_date in zip(
            rows["normalized_hourly_curve_json"], rows["target_date"]
        )
    ]
    return rows.drop_duplicates("ladder_snapshot_id", keep="last")


def native_from_f(value: float, unit: str) -> float:
    if not math.isfinite(value):
        return math.nan
    return (value - 32.0) * 5.0 / 9.0 if str(unit).upper() == "C" else value


def condition_interval(
    bracket: Any, question: Any
) -> tuple[float, float] | None:
    label = str(bracket or "").strip()
    numbers = [float(value) for value in NUMBER_RE.findall(label)]
    if not numbers:
        return None
    question_text = str(question or "").lower()
    if "or below" in question_text or "or lower" in question_text:
        return (-math.inf, numbers[0] + 0.5)
    if "or higher" in question_text or "or above" in question_text:
        return (numbers[0] - 0.5, math.inf)
    if len(numbers) >= 2 and "-" in label:
        return (min(numbers[:2]) - 0.5, max(numbers[:2]) + 0.5)
    return (numbers[0] - 0.5, numbers[0] + 0.5)


def distance_outside(value: float, interval: tuple[float, float] | None) -> float:
    if interval is None or not math.isfinite(value):
        return math.nan
    low, high = interval
    if value < low:
        return low - value
    if value >= high:
        return value - high
    return 0.0


def add_forecast_geometry(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["forecast_max_native"] = [
        native_from_f(value, unit)
        for value, unit in zip(out["forecast_max_f"], out["market_unit"])
    ]
    low_intervals = [
        condition_interval(bracket, question)
        for bracket, question in zip(out["low_bracket"], out["low_question"])
    ]
    high_intervals = [
        condition_interval(bracket, question)
        for bracket, question in zip(out["high_bracket"], out["high_question"])
    ]
    out["forecast_distance_low_native"] = [
        distance_outside(value, interval)
        for value, interval in zip(out["forecast_max_native"], low_intervals)
    ]
    out["forecast_distance_high_native"] = [
        distance_outside(value, interval)
        for value, interval in zip(out["forecast_max_native"], high_intervals)
    ]
    out["native_step"] = out["market_unit"].map(
        lambda value: 1.0 if str(value).upper() == "C" else 2.0
    )
    out["forecast_min_cushion_steps"] = (
        out[
            ["forecast_distance_low_native", "forecast_distance_high_native"]
        ].min(axis=1)
        / out["native_step"]
    )
    out["forecast_excludes_both_extremes"] = (
        out["forecast_distance_low_native"].gt(0)
        & out["forecast_distance_high_native"].gt(0)
    )
    return out


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.2%}"


def report_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | overlay | baskets | dates | tail hit (exact 95% CI) | break-even tail | avg cost | ROI (95% CI) | excess vs market (95% CI) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {row['overlay']} | {int(row['baskets'])} | "
            f"{int(row['dates'])} | {fmt_pct(row['tail_hit_rate'])} "
            f"[{fmt_pct(row['tail_hit_ci_low'])}, {fmt_pct(row['tail_hit_ci_high'])}] | "
            f"{fmt_pct(row['break_even_tail_rate'])} | "
            f"{row['avg_cost']:.4f} | {fmt_pct(row['fee_adjusted_roi'])} "
            f"[{fmt_pct(row['roi_ci_low'])}, {fmt_pct(row['roi_ci_high'])}] | "
            f"{fmt_pct(row['excess_roi_vs_market'])} "
            f"[{fmt_pct(row['excess_ci_low'])}, {fmt_pct(row['excess_ci_high'])}] |"
        )
    return lines


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    raw = base.load_rows(args.db)
    snapshots, base_funnel = base.build_snapshot_rows(raw)
    selected = base.select_policies(snapshots)
    forecasts = load_forecasts(args.db)
    joined = selected.merge(
        forecasts,
        on="ladder_snapshot_id",
        how="left",
        suffixes=("", "_forecast"),
        validate="one_to_one",
    )
    joined = add_forecast_geometry(joined)
    forecast_ready = joined[
        joined["forecast_pit_valid"].astype("boolean").fillna(False)
        & joined["forecast_max_f"].notna()
    ].copy()

    overlays = {
        "same_rows_no_forecast_filter": lambda frame: frame.index == frame.index,
        "forecast_excludes_both_extremes": lambda frame: frame[
            "forecast_excludes_both_extremes"
        ],
        "forecast_cushion_ge_1step": lambda frame: frame[
            "forecast_min_cushion_steps"
        ].ge(1.0),
        "forecast_cushion_ge_2steps": lambda frame: frame[
            "forecast_min_cushion_steps"
        ].ge(2.0),
    }
    summaries: list[dict[str, Any]] = []
    selected_variants: list[pd.DataFrame] = []
    for policy_index, (policy, policy_rows) in enumerate(
        forecast_ready.groupby("policy")
    ):
        for overlay_index, (overlay, selector) in enumerate(overlays.items()):
            subset = policy_rows[selector(policy_rows)].copy()
            record = base.summarize(
                subset,
                f"{policy}|{overlay}",
                draws=args.draws,
                seed=2026072710 + policy_index * 20 + overlay_index * 2,
            )
            hits = int(subset["tail_hit"].sum())
            baskets = len(subset)
            record["tail_hit_ci_low"] = (
                0.0
                if hits == 0
                else float(beta.ppf(0.025, hits, baskets - hits + 1))
            )
            record["tail_hit_ci_high"] = (
                1.0
                if hits == baskets
                else float(beta.ppf(0.975, hits + 1, baskets - hits))
            )
            record["policy"] = policy
            record["overlay"] = overlay
            summaries.append(record)
            subset["overlay"] = overlay
            selected_variants.append(subset)
    summary_frame = pd.DataFrame(summaries)
    variant_rows = pd.concat(selected_variants, ignore_index=True)

    funnel = {
        **base_funnel,
        "base_selected_baskets": int(len(selected)),
        "base_selected_dates": int(selected["target_date"].nunique()),
        "pit_forecast_ready_baskets": int(len(forecast_ready)),
        "pit_forecast_ready_dates": int(forecast_ready["target_date"].nunique()),
        "pit_forecast_ready_cities": int(forecast_ready["city"].nunique()),
        "base_tail_hit_rows": int(selected["tail_hit"].sum()),
        "forecast_ready_tail_hit_rows": int(forecast_ready["tail_hit"].sum()),
        "forecast_missing_tail_hit_rows": int(
            selected.loc[
                ~selected["ladder_snapshot_id"].isin(
                    forecast_ready["ladder_snapshot_id"]
                ),
                "tail_hit",
            ].sum()
        ),
    }
    coverage_work = joined.assign(
        forecast_ready=(
            joined["forecast_pit_valid"].astype("boolean").fillna(False)
            & joined["forecast_max_f"].notna()
        ).astype(int)
    )
    coverage_by_date = (
        coverage_work
        .groupby("target_date", as_index=False)
        .agg(
            base_baskets=("city", "size"),
            forecast_ready_baskets=("forecast_ready", "sum"),
            base_tail_hits=("tail_hit", "sum"),
            forecast_ready_tail_hits=(
                "tail_hit",
                lambda values: int(
                    values[
                        coverage_work.loc[
                            values.index, "forecast_ready"
                        ].astype(bool)
                    ].sum()
                ),
            ),
        )
    )
    coverage_by_date.to_csv(
        args.output_dir / "forecast_coverage_by_date.csv", index=False
    )
    payload = {
        "contract": {
            "grain": "same D-1 executable basket rows as v1",
            "forecast": "assigned canonical PIT hourly curve daily maximum",
            "overlay": "point forecast excludes both outer listed conditions; 1/2-step cushion sensitivity",
            "probability_status": "point_forecast_only_not_calibrated_probability",
            "variants_k": len(overlays),
            "trade_class": "research_replay",
        },
        "funnel": funnel,
        "summary": summaries,
        "verdict": "inconclusive",
        "action": "do_not_shadow_or_live",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    summary_frame.to_csv(args.output_dir / "summary.csv", index=False)
    detail_columns = [
        "policy",
        "overlay",
        "city",
        "target_date",
        "decision_ts_utc",
        "forecast_source",
        "forecast_model",
        "forecast_available_at_utc",
        "forecast_max_f",
        "forecast_max_native",
        "forecast_min_cushion_steps",
        "low_bracket",
        "high_bracket",
        "low_no_ask",
        "high_no_ask",
        "market_tail_probability",
        "tail_hit",
        "cost",
        "pnl",
    ]
    variant_rows[detail_columns].to_csv(
        args.output_dir / "selected_variants.csv", index=False
    )

    lines = [
        "# D-1 Extreme NO + Forecast Overlay v2",
        "",
        "> 2026-07-27；research replay；zero notional；不改 live。",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db` canonical Tmax v2 ladder / effective PIT forecast / `settlement_outcomes`。",
        f"- DB mtime UTC：`{pd.Timestamp(args.db.stat().st_mtime, unit='s', tz='UTC').isoformat()}`。",
        f"- 记录：{len(raw):,} rung rows；base={len(selected):,} settled policy-baskets；PIT forecast-ready={len(forecast_ready):,}；unsettled=0，missing_bracket=0。",
        "",
        "## 结论",
        "",
        "上一版已经是 D-1。本版只在完全相同的 direct-NO-ask basket 分母上加入"
        " assigned canonical PIT forecast：forecast daily max 必须同时避开最低、最高"
        "两个挂牌 condition；再报告距离两端至少 1/2 个 native step 的敏感性。",
        "",
    ]
    lines.extend(report_table(summary_frame))
    lines += [
        "",
        "该 overlay 没有形成可发布的 forecast alpha：可用 forecast 的独立 target dates "
        "太少，而且这里只有 point forecast，不是经过 expanding/OOF 校准的 "
        "`P(low hit)+P(high hit)`。即使某个小切片 ROI 为正，也不能与 market "
        "probability 作 proper-score 比较，更不能据此挑阈值。",
        "",
        (
            f"更关键的是 coverage：原始两种 policy 合计 {funnel['base_tail_hit_rows']} 条 "
            f"tail-hit 记录中，forecast-ready 只覆盖 {funnel['forecast_ready_tail_hit_rows']} 条，"
            f"缺 forecast 的日期漏掉 {funnel['forecast_missing_tail_hit_rows']} 条。"
            "覆盖缺口包括 7/4–7 的历史 forecast 无可靠 available_at（漏 4 条 tail hit），"
            "以及 7/17–19 的部分覆盖（再漏 4 条）；这些缺口不能被记成 forecast filter 的成功。"
        ),
        "",
        "零 tail-hit 切片的 date bootstrap 只会重采样“没发生 tail”的日期，不能表达未观察到的"
        " rare-event 风险。因此表中同时给出 exact binomial 95% CI；其 tail-hit 上界仍明显高于"
        "策略 break-even tail rate，不能据正的点估 ROI 推进。",
        "",
        "已有宽分母 `d1 exact landing v2` 也给出相同边界：加入 forecast ceiling、"
        "distance、peak clock 等物理因子后，OOF logloss/Brier 均没有打败同 rows "
        "的 market。因此当前没有证据证明“加 forecast”能把机械两端 NO 变成 alpha。",
        "",
        "## Signal / Evidence Funnel",
        "",
    ]
    for key, value in funnel.items():
        lines.append(f"- `{key}` = {value:,}")
    lines += [
        "",
        "## Gate",
        "",
        "- significance：所有 overlay 按 target_date block bootstrap；不从 4 个版本中事后挑正 ROI。",
        "- baseline：仍用同 snapshot normalized full-ladder market tail mass。",
        "- probability：FAIL/NA；point forecast 不能替代 calibrated tail probability。",
        "- forward：NA；forecast-ready 独立日期不足，且本轮不是样本开始前冻结。",
        "- conclusion：`inconclusive`；不启 shadow/live。",
        "",
        "要让这个方向成立，下一版必须直接输出 expanding/OOF "
        "`p_tail_forecast=P(low hit)+P(high hit)`，并验证其 proper score 相对 market "
        "tail probability 为负 delta；交易只在 "
        "`p_tail_forecast < 2 - executable_cost - safety_buffer` 时发生。",
        "",
        "Artifacts:",
        "",
        "- `scripts/analysis/market_structure_edge/research_d1_extreme_no_forecast_overlay_v2.py`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/summary.json`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/selected_variants.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/forecast_coverage_by_date.csv`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
