#!/usr/bin/env python3
"""Frozen-forward Tokyo JMA source-specific path feature ablation.

The model predicts a JMA next-lattice source cross, not the final exact market
bracket.  Enriched exact fields are admitted only after the timestamp/hash
audit proves they were present in the payload captured at first-seen.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
V2_PATH = (
    ROOT
    / "scripts/analysis/market_structure_edge"
    / "research_three_city_pre_cross_path_pretrain_v2.py"
)
SPEC = importlib.util.spec_from_file_location("three_city_pretrain_v2", V2_PATH)
assert SPEC and SPEC.loader
V2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V2)

DEFAULT_BASE = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "three_city_pre_cross_path_pretrain_v2"
)
DEFAULT_AUDIT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_feature_timestamp_audit_v1"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_source_specific_path_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07"
    / "2026-07-30-tokyo-jma-source-specific-path-plan-v1.md"
)
SNAPSHOT_FEATURES = (
    "source_temp_c",
    "distance_to_next_lattice_c",
    "local_hour_sin",
    "local_hour_cos",
)
TOKYO_JMA_FEATURES = (
    *V2.BASE_FEATURES,
    "source_wind_dir_sin",
    "source_wind_dir_cos",
    "source_gust_factor_kt",
    "source_wind_dir_change_60m_deg",
    "source_wind_speed_change_60m_kt",
    "source_precipitating_10m",
)


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows: list[dict[str, Any]] = list(csv.DictReader(handle))
    for row in rows:
        for key, value in list(row.items()):
            if value == "":
                row[key] = None
    return rows


def number(value: Any) -> float | None:
    return V2.number(value)


def parse_dt(value: Any) -> datetime | None:
    return V2.parse_dt(value)


def merge_exact_enrichment(
    states: list[dict[str, Any]], enriched: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_obs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        by_obs[str(row.get("observation_time_utc") or "")].append(row)
    merged: list[dict[str, Any]] = []
    audit = Counter()
    for state in states:
        obs = str(state.get("source_observation_ts_utc") or "")
        candidates = by_obs.get(obs, [])
        exact = [
            row
            for row in candidates
            if row.get("hash_verified_against_jma_point_archive") == "1"
            and parse_dt(row.get("decision_ts_utc"))
            == parse_dt(state.get("decision_clock_ts_utc"))
        ]
        out = dict(state)
        if len(exact) == 1:
            row = exact[0]
            direction = number(row.get("wind_dir_deg"))
            out.update(
                {
                    "source_specific_exact_available": 1,
                    "source_wind_speed_kt": number(row.get("wind_speed_kt")),
                    "source_wind_dir_deg": direction,
                    "source_wind_dir_sin": (
                        math.sin(math.radians(direction))
                        if direction is not None
                        else None
                    ),
                    "source_wind_dir_cos": (
                        math.cos(math.radians(direction))
                        if direction is not None
                        else None
                    ),
                    "source_wind_gust_kt": number(row.get("wind_gust_kt")),
                    "source_precipitation_10m_mm": number(
                        row.get("precipitation_10m_mm")
                    ),
                    "source_first_seen_age_min": number(
                        row.get("first_seen_age_min")
                    ),
                }
            )
            audit["hash_verified_joined"] += 1
        else:
            out["source_specific_exact_available"] = 0
            audit["missing_or_ambiguous"] += 1
        merged.append(out)
    return merged, dict(audit)


def circular_delta(current: float, prior: float) -> float:
    return (current - prior + 180.0) % 360.0 - 180.0


def add_past_only_dynamics(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["target_date"])].append(row)
    for day_rows in grouped.values():
        day_rows.sort(key=lambda row: str(row["decision_clock_ts_utc"]))
        history: list[dict[str, Any]] = []
        for row in day_rows:
            current_ts = parse_dt(row.get("decision_clock_ts_utc"))
            wind = number(row.get("source_wind_speed_kt"))
            gust = number(row.get("source_wind_gust_kt"))
            direction = number(row.get("source_wind_dir_deg"))
            candidates = []
            if current_ts is not None:
                for prior in history:
                    prior_ts = parse_dt(prior.get("decision_clock_ts_utc"))
                    if prior_ts is None:
                        continue
                    age = (current_ts - prior_ts).total_seconds() / 60.0
                    if 20.0 <= age <= 80.0:
                        candidates.append((abs(age - 60.0), prior))
            prior = min(candidates, default=(math.inf, None), key=lambda item: item[0])[1]
            prior_wind = number(prior.get("source_wind_speed_kt")) if prior else None
            prior_direction = number(prior.get("source_wind_dir_deg")) if prior else None
            row["source_gust_factor_kt"] = (
                gust - wind if gust is not None and wind is not None else None
            )
            row["source_wind_speed_change_60m_kt"] = (
                wind - prior_wind
                if wind is not None and prior_wind is not None
                else None
            )
            row["source_wind_dir_change_60m_deg"] = (
                circular_delta(direction, prior_direction)
                if direction is not None and prior_direction is not None
                else None
            )
            precip = number(row.get("source_precipitation_10m_mm"))
            row["source_precipitating_10m"] = (
                int(precip > 0) if precip is not None else None
            )
            history.append(row)


def date_weighted_metrics(
    rows: list[dict[str, Any]], probabilities: np.ndarray, label: str
) -> dict[str, float]:
    dates = Counter(str(row["target_date"]) for row in rows)
    weights = np.asarray([1.0 / dates[str(row["target_date"])] for row in rows])
    weights /= weights.sum()
    y = np.asarray([int(row[label]) for row in rows], dtype=float)
    p = np.asarray(probabilities, dtype=float)
    return {
        "brier": float(np.sum(weights * (p - y) ** 2)),
        "logloss": float(
            -np.sum(
                weights
                * (
                    y * np.log(np.clip(p, V2.EPS, 1 - V2.EPS))
                    + (1 - y)
                    * np.log(np.clip(1 - p, V2.EPS, 1 - V2.EPS))
                )
            )
        ),
        "auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else math.nan,
    }


def bootstrap_delta(
    rows: list[dict[str, Any]],
    left: np.ndarray,
    right: np.ndarray,
    label: str,
) -> tuple[float, float]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for row, p_left, p_right in zip(rows, left, right):
        y = int(row[label])
        by_date[str(row["target_date"])].append(
            (float(p_right) - y) ** 2 - (float(p_left) - y) ** 2
        )
    date_deltas = np.asarray(
        [np.mean(values) for _, values in sorted(by_date.items())]
    )
    rng = np.random.default_rng(20260730)
    draws = np.asarray(
        [
            np.mean(rng.choice(date_deltas, len(date_deltas), replace=True))
            for _ in range(4000)
        ]
    )
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def render_report(
    path: Path,
    *,
    audit_summary: dict[str, Any],
    join_audit: dict[str, int],
    comparisons: list[dict[str, Any]],
) -> None:
    lines = [
        "# Tokyo JMA Source-Specific Path Plan v1",
        "",
        "Status: `research_only`; no plan/order/fill/exit change",
        "",
        "## 结论与动作",
        "",
        "保持 research，不改 live。Tokyo 的 JMA 原始 payload 确实包含此前被 collector "
        "丢掉的风速、风向、阵风和降水；本次已补进共享 parser 与完整历史路径。旧 exact "
        "事件只在当前 JMA archive 内容与原始 `raw_payload_hash` 完全一致时才补特征，"
        "不一致行保持缺失，禁止用晚到值伪造 PIT。",
        "",
        "source-specific 模型仍只预测 JMA next-lattice cross，不代表 final exact bracket；"
        "只有后续与同 checkpoint market probability 比较，才可能形成策略 residual。",
        "",
        "本轮消融没有发现新增 JMA 风/阵风/降水维度带来稳定增益：30m 基本持平，"
        "60m 明确变差，120m 的微小改善 CI 跨 0。因此补字段是正确的数据建设，"
        "但当前不能据此制造 Tokyo eligibility gate。",
        "",
        "## Timestamp / feature audit",
        "",
        f"- raw Tokyo JMA rows: `{audit_summary['raw_matching_rows']}`",
        f"- distinct observation revisions: `{audit_summary['distinct_observation_revisions']}`",
        f"- hash-verified exact enrichments: `{audit_summary['hash_verified_enriched_rows']}`",
        f"- archive missing/hash mismatch excluded: `{audit_summary['hash_mismatch_or_archive_missing']}`",
        f"- timestamp violations: `{audit_summary['timestamp_violations']}`",
        f"- first-seen age min/median/max: "
        f"`{audit_summary['first_seen_age_min']['min']:.2f}/"
        f"{audit_summary['first_seen_age_min']['median']:.2f}/"
        f"{audit_summary['first_seen_age_min']['max']:.2f}` minutes",
        f"- exact-state joins: `{join_audit}`",
        "",
        "`decision_ts = source_first_seen_at_utc`。`observation_time_utc` 只描述气象观测时刻；"
        "`source_published_at_utc` 是 collector poll publication，不是 JMA issue time，"
        "不得当作更早的信息时钟。`maxTemp/maxTempTime/minTemp/minTempTime/gustTime` 因"
        "跨日语义不安全，保持 raw-only。",
        "",
        "## Current Tokyo capture dimensions",
        "",
        "| source | cadence | current production raw | implemented locally | PIT clock |",
        "|---|---:|---|---|---|",
        "| JMA Haneda AMeDAS | 10m | journal 仅 temp + hashes/timestamps | parser 补 wind speed/direction、gust、10m/1h/3h/24h precip、QC；未部署 | collector exact first-seen |",
        "| RJTT routine METAR | ~30m | temp、dewpoint/RH、wind、cloud/ceiling、weather/precip；QNH 仅在 raw METAR | QNH 规范化为 pressure_hpa；未部署 | 必须用 report first-seen 做 as-of join |",
        "| forecast curve | hourly | temp、cloud、precip probability、wind speed/direction | 不改字段，只要求保存/选择 issue/run/first-seen 版本 | forecast first-seen |",
        "",
        "## Frozen-forward feature ablation",
        "",
        "| horizon | exact dates/events | snapshot Brier | generic path | Tokyo JMA path | delta vs generic (95% date CI) |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparisons:
        lines.append(
            f"| {row['horizon_min']}m | {row['target_dates']} / {row['events']} | "
            f"{row['snapshot_brier']:.4f} | {row['generic_brier']:.4f} | "
            f"{row['tokyo_jma_brier']:.4f} | {row['delta_vs_generic']:+.4f} "
            f"([{row['delta_ci_low']:+.4f}, {row['delta_ci_high']:+.4f}]) |"
        )
    lines.extend(
        [
            "",
            "三组模型固定相同 exact rows/labels；历史训练截止 exact collector 首日前。"
            "负 delta 表示新增 JMA 风/阵风/降水路径改善。日期数仍少，只用于决定是否继续"
            "采集，不用于 live gate。",
            "",
            "## Strategy research plan",
            "",
            "### P0 — Tokyo source probability head（已具备）",
            "",
            "- grain: 每个 hash-identified JMA first-seen observation event。",
            "- target: `P(JMA next-lattice cross within 30/60/120m | PIT state)`。",
            "- baseline: snapshot、generic path、Tokyo JMA path，同 rows 做 Brier/logloss/calibration。",
            "- 输出所有事件概率，不按阈值只保存赢家。",
            "",
            "### P1 — PIT METAR/forecast fusion",
            "",
            "- 对每个 JMA decision checkpoint，只允许连接 `METAR first_seen <= decision_ts` "
            "的最近 RJTT 报文；补湿度、露点、云层、降水、QNH 与风向。",
            "- forecast 必须按 `issue/run/first_seen <= decision_ts` 选版本，加入 peak clock、"
            "remaining heating window、cloud/rain/wind forecast。",
            "- 后到 METAR/WU/JMA revision 只作 label/basis audit，绝不回填为特征。",
            "",
            "### P2 — Final-exact distribution bridge",
            "",
            "- 把 source-cross probability 转成全 ladder terminal distribution，显式建模"
            " source→settlement basis 与 overshoot；不能直接把 touch/cross 买成 exact YES。",
            "- 同 checkpoint 保存 normalized market ladder，主信号是 "
            "`P(final bracket)-market_probability`。",
            "",
            "### P3 — Zero-notional forward telemetry",
            "",
            "- 写 `fact_signal_candidates v2` opportunity：全 bracket×side、p_before/p_after、"
            "market before/after、fresh executable cost；notional 固定 0。",
            "- signal funnel 与 evidence funnel 分开；按 target_date block bootstrap。",
            "- 至少新增 20 个 Tokyo exact 日期后冻结复核；未打败同分母 market 前不进入"
            " plan/order/fill/exit。",
            "",
            "## Gate",
            "",
            "`market_baseline=NOT_YET_TESTED forward=COLLECTING "
            "execution=ZERO_NOTIONAL_ONLY conclusion=inconclusive`",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE))
    parser.add_argument("--audit-dir", default=str(DEFAULT_AUDIT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    base = Path(args.base_dir)
    audit_dir = Path(args.audit_dir)
    history = [
        row for row in read_csv(base / "archive_path_states.csv")
        if row["city"] == "Tokyo"
    ]
    exact = [
        row for row in read_csv(base / "exact_path_states.csv")
        if row["city"] == "Tokyo"
    ]
    enriched = read_csv(audit_dir / "tokyo_jma_exact_enriched.csv")
    exact, join_audit = merge_exact_enrichment(exact, enriched)
    add_past_only_dynamics(history)
    add_past_only_dynamics(exact)
    comparisons: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    for horizon in V2.HORIZONS:
        label = f"cross_next_lattice_within_{horizon}m"
        train = [row for row in history if row.get(label) is not None]
        test = [row for row in exact if row.get(label) is not None]
        fitted = {
            "snapshot": V2.fit_logistic(train, label, SNAPSHOT_FEATURES),
            "generic_path": V2.fit_logistic(
                train, label, V2.BASE_FEATURES
            ),
            "tokyo_jma_path": V2.fit_logistic(
                train, label, TOKYO_JMA_FEATURES
            ),
        }
        if any(model is None for model in fitted.values()):
            raise RuntimeError(f"model fit unavailable for {horizon}m")
        probabilities = {
            name: V2.predict(model, test)  # type: ignore[arg-type]
            for name, model in fitted.items()
        }
        metrics = {
            name: date_weighted_metrics(test, values, label)
            for name, values in probabilities.items()
        }
        ci_low, ci_high = bootstrap_delta(
            test,
            probabilities["generic_path"],
            probabilities["tokyo_jma_path"],
            label,
        )
        comparisons.append(
            {
                "horizon_min": horizon,
                "events": len(test),
                "target_dates": len({row["target_date"] for row in test}),
                "snapshot_brier": metrics["snapshot"]["brier"],
                "generic_brier": metrics["generic_path"]["brier"],
                "tokyo_jma_brier": metrics["tokyo_jma_path"]["brier"],
                "delta_vs_generic": (
                    metrics["tokyo_jma_path"]["brier"]
                    - metrics["generic_path"]["brier"]
                ),
                "delta_ci_low": ci_low,
                "delta_ci_high": ci_high,
                "snapshot_auc": metrics["snapshot"]["auc"],
                "generic_auc": metrics["generic_path"]["auc"],
                "tokyo_jma_auc": metrics["tokyo_jma_path"]["auc"],
            }
        )
        for index, row in enumerate(test):
            for model_name, values in probabilities.items():
                prediction_rows.append(
                    {
                        "event_id": row["event_id"],
                        "target_date": row["target_date"],
                        "decision_ts_utc": row["decision_clock_ts_utc"],
                        "horizon_min": horizon,
                        "model": model_name,
                        "probability": float(values[index]),
                        "label": int(row[label]),
                        "source_specific_exact_available": row.get(
                            "source_specific_exact_available"
                        ),
                    }
                )
    audit_summary = json.loads((audit_dir / "summary.json").read_text())
    out = Path(args.out_dir)
    write_csv(out / "model_comparison.csv", comparisons)
    write_csv(out / "predictions.csv", prediction_rows)
    summary = {
        "schema_version": "tokyo_jma_source_specific_path_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "history_dates": len({row["target_date"] for row in history}),
        "exact_dates": len({row["target_date"] for row in exact}),
        "join_audit": join_audit,
        "comparisons": comparisons,
        "market_baseline_rows": 0,
        "policy_selected": 0,
        "live_behavior_changes": 0,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    render_report(
        Path(args.report),
        audit_summary=audit_summary,
        join_audit=join_audit,
        comparisons=comparisons,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
