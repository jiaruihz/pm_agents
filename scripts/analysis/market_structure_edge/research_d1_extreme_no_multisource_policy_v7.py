#!/usr/bin/env python3
"""Audit and freeze a multi-source city policy for the D-1 extreme-NO basket.

The trade denominator is the immutable v4 executable basket.  City selection
is learned only from historical multi-model rows dated on or before the v6
training cutoff.  Historical Open-Meteo replay is not treated as decision-time
PIT evidence; the live raw capture is audited separately for strict as-of
coverage.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_d1_extreme_no_basket_v1 as base
import research_d1_extreme_no_reliable_city_source_v6 as v6
from weather_data_feed.forecast_sources import OPEN_METEO_MULTI_MODEL_SPECS
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)


DEFAULT_BASKETS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_RELIABILITY = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_reliable_city_source_v6/frozen_city_reliability.csv"
)
DEFAULT_HISTORICAL = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
)
DEFAULT_RAW = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/forecast_enrichment"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_multisource_policy_v7"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-extreme-no-multisource-policy-v7.md"
)
SEED = 2026072807
MIN_MODEL_DATES = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=v6.DEFAULT_DB)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--reliability", type=Path, default=DEFAULT_RELIABILITY)
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def source_inventory() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"model_key": key, **spec}
            for key, spec in OPEN_METEO_MULTI_MODEL_SPECS.items()
        ]
    )


def model_geography_is_valid(city: str, model_key: str) -> bool:
    """Reject regional-model values outside their documented geographic tier."""
    cfg = FULL_CITY_CONFIGS.get(city) or {}
    lat = float(cfg.get("lat", math.nan))
    lon = float(cfg.get("lon", math.nan))
    if not math.isfinite(lat) or not math.isfinite(lon):
        return False
    if model_key in {
        "ncep_hrrr_conus",
        "ncep_nbm_conus",
        "ncep_nam_conus",
        "gem_regional",
        "gem_hrdps_continental",
    }:
        return 15.0 <= lat <= 75.0 and -170.0 <= lon <= -45.0
    if model_key in {"icon_eu", "icon_d2"}:
        return 25.0 <= lat <= 72.0 and -25.0 <= lon <= 45.0
    if model_key == "meteofrance_arome_france_hd":
        return 38.0 <= lat <= 56.0 and -12.0 <= lon <= 16.0
    return True


def frozen_multisource_policy(
    historical: pd.DataFrame,
    reliability: pd.DataFrame,
    cutoff: str,
) -> pd.DataFrame:
    train = historical[historical["target_date"].astype(str).le(cutoff)].copy()
    train = train[
        [
            model_geography_is_valid(str(row.city), str(row.model_key))
            for row in train.itertuples(index=False)
        ]
    ].copy()
    model = (
        train.groupby(
            ["city", "model_key", "model_label", "provider", "tier"],
            as_index=False,
        )
        .agg(
            train_dates=("target_date", "nunique"),
            train_mae_f=("abs_error_f", "mean"),
            train_bias_f=("error_f", "mean"),
        )
    )
    model = model[model["train_dates"].ge(MIN_MODEL_DATES)].copy()
    model = model.sort_values(
        ["city", "train_mae_f", "train_dates", "model_label"],
        ascending=[True, True, False, True],
    )
    best = model.groupby("city", as_index=False).first().rename(
        columns={
            "model_key": "best_model_key",
            "model_label": "best_model_label",
            "provider": "best_provider",
            "tier": "best_tier",
            "train_dates": "best_train_dates",
            "train_mae_f": "best_train_mae_f",
            "train_bias_f": "best_train_bias_f",
        }
    )
    assigned_labels = {"gfs": "GFS", "ecmwf": "ECMWF"}
    eligible = reliability[reliability["eligible"]].copy()
    eligible["assigned_model_label"] = eligible["assigned_family"].map(
        assigned_labels
    )
    assigned = model[
        ["city", "model_label", "train_dates", "train_mae_f", "train_bias_f"]
    ].rename(
        columns={
            "model_label": "assigned_model_label",
            "train_dates": "assigned_train_dates",
            "train_mae_f": "assigned_train_mae_f",
            "train_bias_f": "assigned_train_bias_f",
        }
    )
    policy = eligible.merge(best, on="city", how="left", validate="many_to_one")
    policy = policy.merge(
        assigned,
        on=["city", "assigned_model_label"],
        how="left",
        validate="many_to_one",
    )
    policy["gain_vs_assigned_mae_f"] = (
        policy["assigned_train_mae_f"] - policy["best_train_mae_f"]
    )
    policy["multisource_accurate_half"] = False
    policy["multisource_accurate_quartile"] = False
    for _, idx in policy.dropna(subset=["best_train_mae_f"]).groupby(
        "policy"
    ).groups.items():
        ranked = policy.loc[idx].sort_values(
            ["best_train_mae_f", "best_train_dates", "city"],
            ascending=[True, False, True],
        )
        half_n = math.ceil(len(ranked) / 2)
        quartile_n = math.ceil(len(ranked) / 4)
        policy.loc[ranked.index[:half_n], "multisource_accurate_half"] = True
        policy.loc[
            ranked.index[:quartile_n], "multisource_accurate_quartile"
        ] = True
    return policy


def summarize_cohort(
    rows: pd.DataFrame,
    *,
    policy: str,
    cohort: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    selected = rows[rows["policy"].eq(policy)].copy()
    record = base.summarize(
        selected,
        f"{policy}|mechanical|{cohort}",
        draws=draws,
        seed=seed,
    )
    record.update(
        {"policy": policy, "expression": "mechanical", "cohort": cohort}
    )
    return record


def strict_pit_coverage(
    baskets: pd.DataFrame, raw_root: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    files = sorted(raw_root.glob("2026-07-*/forecast_enrichment.jsonl"))
    if not files:
        return pd.DataFrame(), {"files": 0, "raw_rows": 0, "covered": 0}
    jq_filter = (
        '[.snapshot_ts_utc,.city,.target_date,'
        '.open_meteo_multi_model.result.status,'
        '((.open_meteo_multi_model.target_date.models // {})|tojson)]|@tsv'
    )
    result = subprocess.run(
        ["jq", "-r", jq_filter, *map(str, files)],
        check=True,
        capture_output=True,
        text=True,
    )
    indexed: dict[tuple[str, str], list[tuple[pd.Timestamp, dict[str, float]]]] = {}
    raw_rows = 0
    usable_rows = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t", 4)
        raw_rows += 1
        if len(parts) != 5 or parts[3] != "ok":
            continue
        models = json.loads(parts[4])
        if not models:
            continue
        indexed.setdefault((parts[1], parts[2]), []).append(
            (pd.Timestamp(parts[0]), models)
        )
        usable_rows += 1
    for values in indexed.values():
        values.sort(key=lambda item: item[0])
    covered: list[dict[str, Any]] = []
    for row in baskets.itertuples(index=False):
        candidates = indexed.get((str(row.city), str(row.target_date)), [])
        visible = [
            item for item in candidates if item[0] <= row.decision_ts_utc
        ]
        if not visible:
            continue
        captured, models = visible[-1]
        covered.append(
            {
                "snapshot_key": row.snapshot_key,
                "policy": row.policy,
                "city": row.city,
                "target_date": row.target_date,
                "decision_ts_utc": row.decision_ts_utc.isoformat(),
                "enrichment_snapshot_ts_utc": captured.isoformat(),
                "age_minutes": (
                    row.decision_ts_utc - captured
                ).total_seconds()
                / 60.0,
                "model_count": len(models),
                "models": json.dumps(models, sort_keys=True),
            }
        )
    frame = pd.DataFrame(covered)
    meta = {
        "files": len(files),
        "raw_rows": raw_rows,
        "usable_model_rows": usable_rows,
        "basket_rows": len(baskets),
        "covered": len(frame),
        "covered_dates": int(frame["target_date"].nunique())
        if not frame.empty
        else 0,
        "covered_cities": int(frame["city"].nunique())
        if not frame.empty
        else 0,
        "coverage_rate": len(frame) / len(baskets) if len(baskets) else 0.0,
    }
    return frame, meta


def pct(value: Any) -> str:
    return "n/a" if pd.isna(value) else f"{float(value):+.2%}"


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    baskets = pd.read_csv(args.baskets)
    baskets["target_date"] = baskets["target_date"].astype(str)
    baskets["decision_ts_utc"] = pd.to_datetime(
        baskets["decision_ts_utc"], utc=True
    )
    reliability = pd.read_csv(args.reliability)
    historical = pd.read_csv(args.historical)
    historical["target_date"] = historical["target_date"].astype(str)
    train_dates = sorted(baskets["target_date"].unique())[
        : len(sorted(baskets["target_date"].unique())) // 2
    ]
    cutoff = train_dates[-1]
    holdout_dates = sorted(baskets["target_date"].unique())[
        len(sorted(baskets["target_date"].unique())) // 2 :
    ]

    policy = frozen_multisource_policy(historical, reliability, cutoff)
    holdout = baskets[baskets["target_date"].isin(holdout_dates)].merge(
        policy[
            [
                "policy",
                "city",
                "accurate_half",
                "accurate_quartile",
                "best_model_label",
                "best_provider",
                "best_tier",
                "best_train_dates",
                "best_train_mae_f",
                "assigned_train_mae_f",
                "gain_vs_assigned_mae_f",
                "multisource_accurate_half",
                "multisource_accurate_quartile",
            ]
        ],
        on=["policy", "city"],
        how="left",
        validate="many_to_one",
    )
    masks = {
        "all_holdout": pd.Series(True, index=holdout.index),
        "assigned_accurate_half": holdout["accurate_half"].fillna(False),
        "multisource_accurate_half": holdout[
            "multisource_accurate_half"
        ].fillna(False),
        "multisource_accurate_quartile": holdout[
            "multisource_accurate_quartile"
        ].fillna(False),
        "alternative_gain_ge_0_5f": holdout[
            "gain_vs_assigned_mae_f"
        ].ge(0.5),
        "alternative_gain_ge_1_0f": holdout[
            "gain_vs_assigned_mae_f"
        ].ge(1.0),
    }
    summaries: list[dict[str, Any]] = []
    for cohort_index, (cohort, mask) in enumerate(masks.items()):
        for policy_index, name in enumerate(sorted(holdout["policy"].unique())):
            summaries.append(
                summarize_cohort(
                    holdout[mask],
                    policy=name,
                    cohort=cohort,
                    draws=args.draws,
                    seed=SEED + cohort_index * 20 + policy_index,
                )
            )
    summary = pd.DataFrame(summaries)

    ab_rows: list[dict[str, Any]] = []
    for policy_index, name in enumerate(sorted(holdout["policy"].unique())):
        rows = holdout[holdout["policy"].eq(name)]
        multi = rows[rows["multisource_accurate_half"].fillna(False)]
        complement = rows[~rows["multisource_accurate_half"].fillna(False)]
        assigned = rows[rows["accurate_half"].fillna(False)]
        for comparison, right in (
            ("multisource_half_vs_complement", complement),
            ("multisource_half_vs_assigned_half", assigned),
        ):
            delta, low, high = v6.roi_delta_ci(
                multi,
                right,
                draws=args.draws,
                seed=SEED + 200 + policy_index * 10 + len(ab_rows),
            )
            ab_rows.append(
                {
                    "policy": name,
                    "comparison": comparison,
                    "left_baskets": len(multi),
                    "right_baskets": len(right),
                    "roi_delta": delta,
                    "roi_delta_ci_low": low,
                    "roi_delta_ci_high": high,
                }
            )
    ab = pd.DataFrame(ab_rows)
    pit_rows, pit_meta = strict_pit_coverage(baskets, args.raw_root)
    inventory = source_inventory()

    inventory.to_csv(args.output_dir / "source_inventory.csv", index=False)
    policy.to_csv(
        args.output_dir / "frozen_multisource_city_policy.csv", index=False
    )
    holdout.to_csv(args.output_dir / "holdout_baskets.csv", index=False)
    summary.to_csv(args.output_dir / "trade_summary.csv", index=False)
    ab.to_csv(args.output_dir / "paired_ab_summary.csv", index=False)
    pit_rows.to_csv(args.output_dir / "strict_pit_coverage_rows.csv", index=False)

    source_counts = (
        policy[policy["multisource_accurate_half"]]
        .groupby(["best_model_label", "best_provider"])
        .size()
        .sort_values(ascending=False)
    )
    payload = {
        "generated_for": "D-1 extreme NO multi-source city policy",
        "train_cutoff": cutoff,
        "holdout_start": holdout_dates[0],
        "holdout_end": holdout_dates[-1],
        "models_configured": len(inventory),
        "providers": sorted(inventory["provider"].unique()),
        "historical_replay_warning": (
            "Historical Open-Meteo daily replay is not decision-time PIT."
        ),
        "strict_pit": pit_meta,
        "source_counts_in_frozen_half": {
            f"{model}|{provider}": int(count)
            for (model, provider), count in source_counts.items()
        },
        "signal_funnel": {
            "raw_executable_baskets": len(baskets),
            "holdout_baskets": len(holdout),
            "multisource_half_baskets": int(
                holdout["multisource_accurate_half"].fillna(False).sum()
            ),
            "multisource_quartile_baskets": int(
                holdout["multisource_accurate_quartile"].fillna(False).sum()
            ),
        },
        "evidence_funnel": {
            "historical_city_model_rows_before_cutoff": int(
                len(
                    historical[
                        historical["target_date"].astype(str).le(cutoff)
                    ]
                )
            ),
            "strict_pit_covered_baskets": pit_meta["covered"],
            "strict_pit_covered_dates": pit_meta["covered_dates"],
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    trade_lines = [
        "| policy | cohort | baskets / dates | ROI (95% CI) |",
        "|---|---|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        trade_lines.append(
            f"| {row.policy} | {row.cohort} | {int(row.baskets)} / "
            f"{int(row.dates)} | {pct(row.fee_adjusted_roi)} "
            f"[{pct(row.roi_ci_low)}, {pct(row.roi_ci_high)}] |"
        )
    ab_lines = [
        "| policy | comparison | ROI delta (95% CI) |",
        "|---|---|---:|",
    ]
    for row in ab.itertuples(index=False):
        ab_lines.append(
            f"| {row.policy} | {row.comparison} | {pct(row.roi_delta)} "
            f"[{pct(row.roi_delta_ci_low)}, {pct(row.roi_delta_ci_high)}] |"
        )

    lines = [
        "# D-1 两端 NO：多预测源 city policy v7",
        "",
        "## 结论",
        "",
        "系统并非只有 ECMWF/GFS：采集器配置了 "
        f"{len(inventory)} 个模型、{inventory['provider'].nunique()} 个 provider。"
        "但历史研究与严格 PIT 采集必须分开使用。",
        "",
        "- 历史 multi-model replay 只用于在训练窗冻结城市/source policy；"
        "它不是 decision-time 版本，不能直接拿来生成交易概率。",
        f"- 当前 D-1 immutable denominator 中，严格 as-of multi-model 仅覆盖 "
        f"{pit_meta['covered']}/{pit_meta['basket_rows']} baskets、"
        f"{pit_meta['covered_dates']} 个 target dates（"
        f"{pit_meta['coverage_rate']:.2%}），不足以判断 alpha。",
        "- 本地采集契约此前只落当前城市日的模型值；本次已改为额外保留请求返回的"
        "全部 forecast dates，后续 D-1 才能积累真正 PIT evidence。",
        "- 关键反证：按训练期最佳多源 MAE 选城，并没有改善 holdout。"
        "因此不要把“预报最准城市”直接当交易 gate；多源更适合衡量"
        " assigned forecast 与独立 consensus 的偏离。",
        "- 更直接的 frozen 检验也失败：训练期替代模型相对 assigned source "
        "MAE 改善至少 0.5°F 的城市组，在 holdout 的机械两端 NO 仍为负。"
        "天气预报更准没有自动转化成交易 alpha。",
        "",
        "## Frozen holdout：城市池是否改变",
        "",
        f"- Train cutoff：{cutoff}；holdout：{holdout_dates[0]}.."
        f"{holdout_dates[-1]}。交易价格、fee、结算与 v4/v6 完全同分母。",
        "- `multisource_accurate_half` 按训练窗内每城最佳可用模型 MAE 排名前半；"
        "不是逐笔挑事后最优模型。",
        "",
        *trade_lines,
        "",
        *ab_lines,
        "",
        "## Source policy",
        "",
        "现阶段推荐把多源作为 universe/reliability 与 uncertainty feature：",
        "",
        "1. 保留现有 city-assigned GFS/ECMWF 作为稳定基线，并做只用过去数据的"
        " rolling bias correction。",
        "2. 欧洲城市增加 ICON-D2 / ICON-EU / AROME；全球补充 ICON、GDPS、"
        "AIFS、AI-GFS/JMA，使用 ensemble median、spread 和 assigned-minus-consensus。",
        "3. 在严格 PIT 覆盖形成 frozen forward 前，不把“某城历史最佳模型”硬编码成"
        " live gate，也不据此扩大真实交易。",
        "",
        "## 双漏斗",
        "",
        f"- signal funnel：raw executable {len(baskets):,} → holdout "
        f"{len(holdout):,} → multisource half "
        f"{int(holdout['multisource_accurate_half'].fillna(False).sum()):,}。",
        f"- evidence funnel：strict PIT as-of {pit_meta['covered']:,} baskets / "
        f"{pit_meta['covered_dates']} dates；缺失属于 collector coverage gap，"
        "不是策略筛除。",
        "",
        "## 产物",
        "",
        f"- `{args.output_dir / 'frozen_multisource_city_policy.csv'}`",
        f"- `{args.output_dir / 'trade_summary.csv'}`",
        f"- `{args.output_dir / 'paired_ab_summary.csv'}`",
        f"- `{args.output_dir / 'strict_pit_coverage_rows.csv'}`",
        f"- `{args.output_dir / 'source_inventory.csv'}`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
