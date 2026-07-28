#!/usr/bin/env python3
"""Test HeadA base gates and expanded p_cal on their correct denominators.

This separates two questions that were previously mixed:
1. Can p_cal replace the original HeadA mechanism gates on all cheap YES rows?
2. Can p_cal rank tickets after the original HeadA mechanism gates?

The first question uses the canonical no-edge cheap-YES city-date denominator.
The second uses the captured first would-live HeadA ticket denominator.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import (  # noqa: E402
    research_low_price_yes_tail_pcal_expanded_v3 as pcal_v3,
)


HISTORICAL_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "low_price_yes_heada_refinement_v1/base_rows.csv"
)
CURRENT_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "heada_would_live_shadow_v2/entries.csv"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-heada-gate-pcal-stability-v1.md"
)
OUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "heada_gate_pcal_stability_v1"
)
OLD_ARTIFACT_PATH = (
    ROOT
    / "src/strategies/weather_edge_v1/config/"
    "low_price_yes_tail_pcal_v2.json"
)
NEW_ARTIFACT_PATH = (
    ROOT / "configs/weather/low_price_yes_tail_pcal_expanded_v3_research.json"
)
FEE_RATE = 0.05
N_BOOT = 10_000
RNG_SEED = 20260728


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def md_value(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            return "NA"
        return f"{float(value):.4f}"
    return str(value)


def md_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_none_"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in frame[columns].itertuples(index=False, name=None):
        lines.append("| " + " | ".join(md_value(v) for v in row) + " |")
    return "\n".join(lines)


def roi_ci(
    frame: pd.DataFrame,
    *,
    cost_col: str,
    pnl_col: str,
) -> tuple[float, float]:
    daily = (
        frame.groupby("target_date")[[cost_col, pnl_col]]
        .sum()
        .to_numpy(float)
    )
    if len(daily) < 3 or daily[:, 0].sum() <= 0:
        return math.nan, math.nan
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(daily), size=(N_BOOT, len(daily)))
    draws = daily[indices].sum(axis=1)
    valid = draws[:, 0] > 0
    values = draws[valid, 1] / draws[valid, 0]
    return float(np.quantile(values, 0.025)), float(
        np.quantile(values, 0.975)
    )


def roi_delta_ci(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    cost_col: str,
    pnl_col: str,
) -> tuple[float, float, float]:
    dates = sorted(set(left["target_date"]) | set(right["target_date"]))
    left_daily = (
        left.groupby("target_date")[[cost_col, pnl_col]]
        .sum()
        .reindex(dates, fill_value=0.0)
        .to_numpy(float)
    )
    right_daily = (
        right.groupby("target_date")[[cost_col, pnl_col]]
        .sum()
        .reindex(dates, fill_value=0.0)
        .to_numpy(float)
    )
    point = (
        left[pnl_col].sum() / left[cost_col].sum()
        - right[pnl_col].sum() / right[cost_col].sum()
    )
    if len(dates) < 3:
        return float(point), math.nan, math.nan
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(dates), size=(N_BOOT, len(dates)))
    ldraw = left_daily[indices].sum(axis=1)
    rdraw = right_daily[indices].sum(axis=1)
    valid = (ldraw[:, 0] > 0) & (rdraw[:, 0] > 0)
    delta = (
        ldraw[valid, 1] / ldraw[valid, 0]
        - rdraw[valid, 1] / rdraw[valid, 0]
    )
    return (
        float(point),
        float(np.quantile(delta, 0.025)),
        float(np.quantile(delta, 0.975)),
    )


def summarize(
    frame: pd.DataFrame,
    *,
    window: str,
    selector: str,
    cost_col: str,
    pnl_col: str,
) -> dict[str, Any]:
    cost = float(frame[cost_col].sum())
    pnl = float(frame[pnl_col].sum())
    low, high = roi_ci(frame, cost_col=cost_col, pnl_col=pnl_col)
    ordered = frame[pnl_col].sort_values(ascending=False)
    top5 = ordered.head(5).index
    remaining = frame.drop(top5)
    top5_removed_roi = (
        float(remaining[pnl_col].sum() / remaining[cost_col].sum())
        if len(remaining) and remaining[cost_col].sum() > 0
        else math.nan
    )
    return {
        "window": window,
        "selector": selector,
        "rows": len(frame),
        "dates": int(frame["target_date"].nunique()),
        "wins": int(frame["win"].sum()),
        "win_rate": float(frame["win"].mean()) if len(frame) else math.nan,
        "avg_price": (
            float(frame[cost_col].sum() / len(frame))
            if len(frame)
            else math.nan
        ),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": low,
        "roi_ci_high": high,
        "top5_removed_roi": top5_removed_roi,
    }


def load_historical() -> pd.DataFrame:
    frame = pd.read_csv(HISTORICAL_PATH, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["entry"] = pd.to_numeric(frame["entry"], errors="coerce")
    frame["payoff"] = pd.to_numeric(frame["payoff"], errors="coerce")
    frame["raw_dist_br"] = pd.to_numeric(
        frame["raw_dist_br"],
        errors="coerce",
    )
    frame["win"] = frame["payoff"].eq(1.0).astype(int)
    frame["unit_fee"] = FEE_RATE * frame["entry"] * (1.0 - frame["entry"])
    frame["unit_cost"] = frame["entry"] + frame["unit_fee"]
    frame["unit_pnl"] = frame["payoff"] - frame["unit_cost"]
    return frame


def load_current() -> pd.DataFrame:
    frame = pd.read_csv(CURRENT_PATH, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["win"] = pd.to_numeric(frame["win"], errors="coerce").fillna(0)
    return frame


def build_edge_stability() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = pcal_v3.load_rows()
    settled = rows[
        rows["settled_binary"]
        & rows[pcal_v3.pcal_v2.NUM_FEATURES].notna().all(axis=1)
    ].copy()
    blocks = [
        ("old_train", "2026-05-06", "2026-06-20"),
        ("extension", "2026-06-21", "2026-06-30"),
        ("development", "2026-07-01", "2026-07-12"),
        ("retro_forward", "2026-07-13", "2026-07-27"),
    ]
    summaries: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    for name, start, end in blocks:
        block = settled[settled["target_date"].between(start, end)]
        selected = block[block["edge"].ge(0.20)]
        rejected = block[block["edge"].lt(0.20)]
        summaries.extend(
            [
                summarize(
                    selected,
                    window=name,
                    selector="edge_ge_0p20",
                    cost_col="unit_cost",
                    pnl_col="unit_pnl",
                ),
                summarize(
                    rejected,
                    window=name,
                    selector="edge_lt_0p20",
                    cost_col="unit_cost",
                    pnl_col="unit_pnl",
                ),
            ]
        )
        point, low, high = roi_delta_ci(
            selected,
            rejected,
            cost_col="unit_cost",
            pnl_col="unit_pnl",
        )
        deltas.append(
            {
                "window": name,
                "selected_minus_rejected_roi": point,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(summaries), pd.DataFrame(deltas)


def build_distance_stability(
    historical: pd.DataFrame,
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    blocks = [
        (
            "historical_le_2026_06_20",
            historical[historical["target_date"].le("2026-06-20")],
            "unit_cost",
            "unit_pnl",
            historical["raw_dist_br"].gt(0),
        ),
        (
            "historical_2026_06_21_30",
            historical[
                historical["target_date"].between(
                    "2026-06-21",
                    "2026-06-30",
                )
            ],
            "unit_cost",
            "unit_pnl",
            historical["raw_dist_br"].gt(0),
        ),
        (
            "current_2026_07_16_25",
            current,
            "taker_cost_usd",
            "taker_pnl_usd",
            current["intended_hot_tail_dist_gt0"].astype(bool),
        ),
    ]
    for name, block, cost_col, pnl_col, global_mask in blocks:
        mask = global_mask.reindex(block.index).fillna(False)
        selected = block[mask]
        rejected = block[~mask]
        summaries.extend(
            [
                summarize(
                    selected,
                    window=name,
                    selector="dist_gt_0",
                    cost_col=cost_col,
                    pnl_col=pnl_col,
                ),
                summarize(
                    rejected,
                    window=name,
                    selector="dist_le_0",
                    cost_col=cost_col,
                    pnl_col=pnl_col,
                ),
            ]
        )
        point, low, high = roi_delta_ci(
            selected,
            rejected,
            cost_col=cost_col,
            pnl_col=pnl_col,
        )
        deltas.append(
            {
                "window": name,
                "dist_gt0_minus_le0_roi": point,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(summaries), pd.DataFrame(deltas)


def build_base_windows(
    historical: pd.DataFrame,
    current: pd.DataFrame,
) -> pd.DataFrame:
    frames = [
        (
            "historical_full",
            historical[historical["raw_dist_br"].gt(0)],
            "unit_cost",
            "unit_pnl",
        ),
        (
            "historical_may",
            historical[
                historical["target_date"].between(
                    "2026-05-06",
                    "2026-05-31",
                )
                & historical["raw_dist_br"].gt(0)
            ],
            "unit_cost",
            "unit_pnl",
        ),
        (
            "historical_jun_01_20",
            historical[
                historical["target_date"].between(
                    "2026-06-01",
                    "2026-06-20",
                )
                & historical["raw_dist_br"].gt(0)
            ],
            "unit_cost",
            "unit_pnl",
        ),
        (
            "historical_jun_21_30",
            historical[
                historical["target_date"].between(
                    "2026-06-21",
                    "2026-06-30",
                )
                & historical["raw_dist_br"].gt(0)
            ],
            "unit_cost",
            "unit_pnl",
        ),
        (
            "current_jul_16_25",
            current[current["intended_hot_tail_dist_gt0"].astype(bool)],
            "taker_cost_usd",
            "taker_pnl_usd",
        ),
    ]
    return pd.DataFrame(
        [
            summarize(
                frame,
                window=name,
                selector="base_headA",
                cost_col=cost_col,
                pnl_col=pnl_col,
            )
            for name, frame, cost_col, pnl_col in frames
        ]
    )


def build_price_stability(
    historical: pd.DataFrame,
    current: pd.DataFrame,
) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    hist = historical[historical["raw_dist_br"].gt(0)].copy()
    hist["price_band"] = pd.cut(
        hist["entry"],
        [-math.inf, 0.08, 0.14, math.inf],
        labels=["5_8c", "8_14c", "14_20c"],
    )
    cur = current[current["intended_hot_tail_dist_gt0"].astype(bool)].copy()
    cur["price_band"] = pd.cut(
        cur["best_ask"],
        [-math.inf, 0.08, 0.14, math.inf],
        labels=["5_8c", "8_14c", "14_20c"],
    )
    for window, frame, cost_col, pnl_col in [
        ("historical_2026_05_06_06_30", hist, "unit_cost", "unit_pnl"),
        ("current_2026_07_16_25", cur, "taker_cost_usd", "taker_pnl_usd"),
    ]:
        for band, group in frame.groupby("price_band", observed=True):
            out.append(
                summarize(
                    group,
                    window=window,
                    selector=str(band),
                    cost_col=cost_col,
                    pnl_col=pnl_col,
                )
            )
    return pd.DataFrame(out)


def prepare_current_pcal(current: pd.DataFrame) -> pd.DataFrame:
    frame = current[current["intended_hot_tail_dist_gt0"].astype(bool)].copy()
    frame["logit_model_p"] = pcal_v3.pcal_v2.logit(
        frame["model_p_yes"].to_numpy(float)
    )
    frame["logit_ask"] = pcal_v3.pcal_v2.logit(
        frame["best_ask"].to_numpy(float)
    )
    source = frame["forecast_source"].fillna("").str.lower()
    frame["forecast_model"] = np.where(
        source.str.contains("ecmwf"),
        "ecmwf",
        np.where(source.str.contains("gfs"), "gfs", "other"),
    )
    hour = pd.to_datetime(
        frame["created_at_utc"],
        utc=True,
        errors="coerce",
    ).dt.hour
    frame["dec_hour_bucket"] = pd.cut(
        hour,
        [-1, 5, 11, 17, 23],
        labels=["h00_05", "h06_11", "h12_17", "h18_23"],
    ).astype(str)
    frame = pcal_v3.pcal_v2.attach_asof_bias(
        frame,
        pcal_v3.pcal_v2.load_bias_index(),
    )
    return frame


def probability_summary(
    frame: pd.DataFrame,
    *,
    model: str,
    p: np.ndarray,
) -> dict[str, Any]:
    y = frame["win"].to_numpy(float)
    market = np.clip(frame["best_ask"].to_numpy(float), 1e-5, 1 - 1e-5)
    p = np.clip(np.asarray(p, dtype=float), 1e-5, 1 - 1e-5)
    brier_delta = np.square(p - y) - np.square(market - y)
    logloss_delta = -(
        y * np.log(p) + (1 - y) * np.log(1 - p)
    ) + y * np.log(market) + (1 - y) * np.log(1 - market)
    daily = pd.DataFrame(
        {
            "target_date": frame["target_date"].to_numpy(),
            "brier_delta": brier_delta,
            "logloss_delta": logloss_delta,
        }
    ).groupby("target_date").mean()
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(
        0,
        len(daily),
        size=(N_BOOT, len(daily)),
    )
    bdraw = daily["brier_delta"].to_numpy()[indices].mean(axis=1)
    ldraw = daily["logloss_delta"].to_numpy()[indices].mean(axis=1)
    return {
        "model": model,
        "rows": len(frame),
        "dates": int(frame["target_date"].nunique()),
        "observed_rate": float(y.mean()),
        "mean_market": float(market.mean()),
        "mean_p": float(p.mean()),
        "brier_delta_vs_market": float(brier_delta.mean()),
        "brier_ci_low": float(np.quantile(bdraw, 0.025)),
        "brier_ci_high": float(np.quantile(bdraw, 0.975)),
        "logloss_delta_vs_market": float(logloss_delta.mean()),
        "logloss_ci_low": float(np.quantile(ldraw, 0.025)),
        "logloss_ci_high": float(np.quantile(ldraw, 0.975)),
    }


def build_pcal_overlay(
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = prepare_current_pcal(current)
    old = json.loads(OLD_ARTIFACT_PATH.read_text(encoding="utf-8"))[
        "frozen_selector"
    ]
    new = json.loads(NEW_ARTIFACT_PATH.read_text(encoding="utf-8"))
    summaries: list[dict[str, Any]] = []
    probability: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    for name, artifact in [("old_pcal_v2", old), ("expanded_pcal_v3", new)]:
        p = pcal_v3.score_artifact(frame, artifact)
        selected = frame[
            p - frame["best_ask"].to_numpy(float)
            >= float(artifact["theta"])
        ].copy()
        rejected = frame.drop(selected.index)
        summaries.extend(
            [
                summarize(
                    selected,
                    window="current_headA_2026_07_16_25",
                    selector=f"{name}_selected",
                    cost_col="taker_cost_usd",
                    pnl_col="taker_pnl_usd",
                ),
                summarize(
                    rejected,
                    window="current_headA_2026_07_16_25",
                    selector=f"{name}_rejected",
                    cost_col="taker_cost_usd",
                    pnl_col="taker_pnl_usd",
                ),
            ]
        )
        point, low, high = roi_delta_ci(
            selected,
            rejected,
            cost_col="taker_cost_usd",
            pnl_col="taker_pnl_usd",
        )
        deltas.append(
            {
                "model": name,
                "comparison": "selected_minus_rejected",
                "roi_delta": point,
                "ci_low": low,
                "ci_high": high,
            }
        )
        point, low, high = roi_delta_ci(
            selected,
            frame,
            cost_col="taker_cost_usd",
            pnl_col="taker_pnl_usd",
        )
        deltas.append(
            {
                "model": name,
                "comparison": "selected_minus_all_headA",
                "roi_delta": point,
                "ci_low": low,
                "ci_high": high,
            }
        )
        probability.append(probability_summary(frame, model=name, p=p))

    old_names = old["num_features"] + old["cat_columns"]
    new_names = new["num_features"] + new["cat_columns"]
    old_coef = dict(zip(old_names, old["coef"], strict=True))
    new_coef = dict(zip(new_names, new["coef"], strict=True))
    coefficient_rows = []
    for feature in list(dict.fromkeys(old_names + new_names)):
        coefficient_rows.append(
            {
                "feature": feature,
                "old_coef": old_coef.get(feature),
                "new_coef": new_coef.get(feature),
                "delta": (
                    new_coef.get(feature, math.nan)
                    - old_coef.get(feature, math.nan)
                ),
            }
        )
    return (
        pd.DataFrame(summaries),
        pd.DataFrame(probability),
        pd.DataFrame(coefficient_rows),
        pd.DataFrame(deltas),
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    historical = load_historical()
    current = load_current()

    edge, edge_delta = build_edge_stability()
    distance, distance_delta = build_distance_stability(
        historical,
        current,
    )
    base_windows = build_base_windows(historical, current)
    price = build_price_stability(historical, current)
    (
        pcal_overlay,
        pcal_probability,
        coefficients,
        pcal_overlay_delta,
    ) = build_pcal_overlay(current)

    artifacts = {
        "edge_stability.csv": edge,
        "edge_delta.csv": edge_delta,
        "distance_stability.csv": distance,
        "distance_delta.csv": distance_delta,
        "base_window_stability.csv": base_windows,
        "price_stability.csv": price,
        "pcal_overlay.csv": pcal_overlay,
        "pcal_probability.csv": pcal_probability,
        "pcal_coefficients.csv": coefficients,
        "pcal_overlay_delta.csv": pcal_overlay_delta,
    }
    for name, frame in artifacts.items():
        frame.to_csv(OUT_DIR / name, index=False)

    old = json.loads(OLD_ARTIFACT_PATH.read_text(encoding="utf-8"))[
        "frozen_selector"
    ]
    new = json.loads(NEW_ARTIFACT_PATH.read_text(encoding="utf-8"))
    generated = now_utc()
    summary = {
        "generated_at_utc": generated,
        "historical_rows": len(historical),
        "historical_dates": int(historical["target_date"].nunique()),
        "current_rows": len(current),
        "current_dates": int(current["target_date"].nunique()),
        "pcal_change": {
            "old_train_end": old["train_end"],
            "new_train_end": new["train_end"],
            "new_train_rows": new["train_rows"],
            "new_train_dates": new["train_dates"],
            "old_theta": old["theta"],
            "new_theta": new["theta"],
            "new_features_added": [],
        },
        "deployment": "none",
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = f"""# HeadA 原筛选条件与 expanded p_cal 稳定性 v1

Generated: {generated}

## 数据快照

| 字段 | 值 |
| --- | --- |
| historical HeadA | `{HISTORICAL_PATH.relative_to(ROOT)}`；{len(historical)} rows / {historical["target_date"].nunique()} dates / 2026-05-06..06-30 |
| current would-live | `{CURRENT_PATH.relative_to(ROOT)}`；{len(current)} rows / {current["target_date"].nunique()} dates / 2026-07-16..25 |
| broad cheap-YES | canonical `fact_signal_candidates` loader；settled through 2026-07-27 |
| settlement | scored rows 100% settled；missing_bracket=0 |
| execution | historical decision ask / current captured fresh ask；Weather taker feeRate={FEE_RATE:.2f} |
| trade class | research replay / zero-notional shadow；actual fill=0 |

## 先给结论

1. **expanded p_cal 没有加新特征，也尚未证明比旧 p_cal 更好。** 只把训练截止从 6/20 扩到 7/12，
   训练量增至 {new["train_rows"]} rows / {new["train_dates"]} dates，并重估原来的
   `model_p、ask、bias mean/p90、hot/cold tail、assigned source、UTC decision-hour bucket`。
   theta 从 {old["theta"]:.4f} 提到 {new["theta"]:.4f}。
2. **原 HeadA 里最像稳定机制的是 `dist>0`，但它不是“dist<=0 永远不会中”。**
   历史和当前都提高点估 ROI；当前被剔除的 20 张仍有 3 个赢家。
3. **`edge>=0.20` 不稳定。** 它在 6/20 前、6/21..30、7/1..12 都为正，
   到 7/13..27 同分母转为负，且低于 `edge<0.20` complement；这更像旧
   `model_p` 过度自信，不应继续当永久物理 gate。
4. **5–20c 是表达边界，不是越便宜越有 edge。** 14–20c 历史和当前都正；
   5–8c 历史为正、当前 18/18 全输，说明价格桶会换 regime。
5. **fresh-book 是证据有效性 gate，不是天气 alpha。** 历史 missing/thin book
   反而贡献高 ROI，说明晚/缺盘口会制造 archive bias；只有拿到 fresh executable ask
   才能谈真实策略。
6. `HeadA mechanism → p_cal overlay` 是值得并行验证的一个 profile，不是唯一正确架构。
   expanded p_cal 单独替换 HeadA gates 失败，但在当前 84 张 HeadA 上二次筛到 63 张、12 中、ROI +44.3%；
   这是 8 日 retrospective 结果；而且去掉最高5个赢家后仍为负，不能当 fresh confirmed。

## 新 p_cal 到底升级了什么

没有 feature upgrade，只有 sample/recalibration upgrade。不同训练集的 scaler 也不同，
所以 coefficient 大小不能机械横比；可看符号和相对结构：

{md_table(coefficients, ["feature", "old_coef", "new_coef", "delta"])}

主要变化：`model_p`、`ask`、`hot_tail_pct` 权重上升；`bias_mean` 从正转负；
ECMWF/GFS 类别差从旧版约 0.36 logit 缩到约 0.08。新增训练窗中 GFS 城组短期变好，
模型因此弱化了原 source 差异，但该关系在 7/13..27 又反转。模型仍没有 multi-source
spread、forecast innovation、云雨风 regime、settlement-lattice overshoot 或 fresh-book 特征。

## 条件一：`edge>=0.20`

固定 broad cheap-YES city-date denominator；这是在测旧 model edge，不是 HeadA 全机制：

{md_table(edge, ["window", "selector", "rows", "dates", "wins", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"])}

同窗 `edge>=0.20 − edge<0.20`：

{md_table(edge_delta, ["window", "selected_minus_rejected_roi", "ci_low", "ci_high"])}

结论：训练期漂亮、7/13 后翻号。它是 regime-sensitive model output，不是稳定天气边界。

## 条件二：`dist>0`

`dist>0` 表示买的 exact bracket 下沿仍高于 assigned forecast，确实对应“押热尾”
这一物理机制：

{md_table(distance, ["window", "selector", "rows", "dates", "wins", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"])}

`dist>0 − dist<=0`：

{md_table(distance_delta, ["window", "dist_gt0_minus_le0_roi", "ci_low", "ci_high"])}

历史总体支持它；当前方向仍正但差距变小、CI 宽。应保留为 mechanism boundary，
不能描述为“另一侧完全不会中”。

## 原 HeadA 跨窗稳定性

{md_table(base_windows, ["window", "rows", "dates", "wins", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"])}

四段 point ROI 都为正，说明不是只靠单一日期；但 6 月两段和当前窗去掉最高
5 个赢家后均转负，当前 8 日 CI 很宽。它有历史模式，但仍是高度凸、赢家集中的彩票表达。

## 价格条件

{md_table(price, ["window", "selector", "rows", "dates", "wins", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"])}

稳定信息不是“越便宜越好”，而是 14–20c 档在两阶段都维持正点估；
5–8c 明显换 regime。现阶段价格应进入概率/EV与 sizing，不新增事后 hard block。

## p_cal 放回正确 HeadA 分母

在 current 84 张 `dist>0 + edge/time/fresh-book` ticket 上：

{md_table(pcal_overlay, ["selector", "rows", "dates", "wins", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"])}

同84张的概率质量，负 delta 才优于 fresh market：

{md_table(pcal_probability, ["model", "rows", "dates", "observed_rate", "mean_market", "mean_p", "brier_delta_vs_market", "brier_ci_low", "brier_ci_high", "logloss_delta_vs_market", "logloss_ci_low", "logloss_ci_high"])}

expanded 点估优于 market，但 proper-score CI 跨0；selected ROI 仍高度依赖少数赢家。
旧 p_cal 在同84张上的 Brier/logloss 点估还略好于 expanded；expanded 只是通过新 theta
换票后多保住一个赢家，尚不是概率模型意义上的升级。

同窗 ranking delta：

{md_table(pcal_overlay_delta, ["model", "comparison", "roi_delta", "ci_low", "ci_high"])}

所以 overlay 只作为并行 frozen profile，不能用这 8 日结果回头改 gate。

## 其余原条件怎么理解

- `22–24h to settlement`：当前历史表本身已被这个窗口选择，缺同源 fresh-book 的
  其他时段反事实，**无法识别它是否产生 alpha**。它目前只是 D-1 信息时钟边界。
- `one per city-date/signal`：是去重和风险控制，不是预测条件。
- `fresh ask / depth / snapshot age`：属于 evidence/execution funnel；其作用是防止
  stale quote 假收益，不应和天气筛选混成一层。
- `maker-first`：当前没有真实 shadow fills，不能把 maker limit 当已成交收益；
  本报告统一用 taker-at-fresh-ask。

## 双漏斗与裁决

Signal funnel：broad cheap YES → original `edge/time` → `dist>0` exact-tail ticket
→ optional p_cal overlay。

Evidence funnel：PIT forecast/bias → decision/fresh ask → settlement
→ fee-adjusted taker replay → actual fill=0。

```text
significance = historical HeadA absolute ROI PASS；dist 增量与 current p_cal proper score FAIL（CI跨0）
baseline = expanded overlay 点估优于 fresh market，但 CI 跨0
forward = FAIL/NA（7/28..8/11 尚未开封）
conclusion = shadow_candidate / inconclusive
live_action = none
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_gate_pcal_stability_v1.py`
- structured：`generated/heada_gate_pcal_stability_v1/*.csv`
"""
    REPORT.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
