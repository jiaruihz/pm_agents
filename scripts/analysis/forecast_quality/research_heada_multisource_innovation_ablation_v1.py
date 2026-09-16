#!/usr/bin/env python3
"""Ablate multi-source, prior forecast bias, and morning innovation for HeadA.

Entry-time models use the fixed historical 333 and current-shadow 84 ticket
denominators. Morning innovation is evaluated separately because it occurs
after the original HeadA entry and therefore can only inform hold/exit/re-entry.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.special import logit
from scipy.stats import norm
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1/historical_enriched.csv"
CURRENT = ROOT / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1/current_enriched.csv"
ERRORS = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
    / "multisource_backfill/daily_error_rows.csv"
)
INNOVATION = ROOT / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/oof_predictions.csv"
CHECKPOINTS = ROOT / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv"
PROFILES = ROOT / "weather_data_feed/source_profiles.json"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/heada_multisource_innovation_ablation_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-28-heada-multisource-innovation-ablation-v1.md"

TRAIN_END = "2026-06-20"
RECENT_START = "2026-06-21"
RNG_SEED = 20260728
N_BOOT = 5000
SIGMA_F = 3.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_csv(HIST, low_memory=False)
    current = pd.read_csv(CURRENT, low_memory=False)
    for frame in (hist, current):
        frame["target_date"] = frame["target_date"].astype(str)
        frame["win"] = frame["win"].astype(bool)
        frame["ask"] = numeric(frame["ask"])
        frame["distance_br"] = numeric(frame["distance_br"])
        frame["multi_hot_share"] = numeric(frame["multi_hot_share"])
        frame["multi_spread_f"] = numeric(frame["multi_spread_f"])
        frame["assigned_minus_consensus_f"] = numeric(frame["assigned_minus_consensus_f"])
        frame["abs_lat"] = numeric(frame["abs_lat"])
        frame["market_logit"] = logit(frame["ask"].clip(0.001, 0.999))
        frame["source_x_archetype"] = (
            frame["source"].astype(str) + "|" + frame["city_archetype"].astype(str)
        )
    hist["_ticket_id"] = "hist|" + hist.index.astype(str)
    current["_ticket_id"] = "current|" + current.index.astype(str)
    return hist, current


def assigned_model(source: str) -> str:
    return "gfs_seamless" if str(source).lower() == "gfs" else "ecmwf_ifs025"


def add_prior_bias(frame: pd.DataFrame, errors: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    errors = errors.copy()
    errors["target_date"] = pd.to_datetime(errors["target_date"], errors="coerce")
    errors["error_f"] = numeric(errors["error_f"])
    grouped = {
        key: group.sort_values("target_date")
        for key, group in errors.groupby(["city", "model_key"])
    }
    values: list[dict[str, float]] = []
    for row in out.itertuples(index=False):
        day = pd.Timestamp(str(row.target_date))
        history = grouped.get((str(row.city), assigned_model(str(row.source))))
        if history is None:
            values.append(
                {
                    "prior_bias_n": 0,
                    "prior_bias_median_f": math.nan,
                    "prior_mae_f": math.nan,
                    "prior_underforecast_rate": math.nan,
                }
            )
            continue
        start = day - timedelta(days=14)
        prior = history[
            history["target_date"].lt(day) & history["target_date"].ge(start)
        ]["error_f"].dropna()
        values.append(
            {
                "prior_bias_n": int(len(prior)),
                "prior_bias_median_f": float(prior.median()) if len(prior) else math.nan,
                "prior_mae_f": float(prior.abs().mean()) if len(prior) else math.nan,
                "prior_underforecast_rate": float(prior.ge(1.0).mean()) if len(prior) else math.nan,
            }
        )
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(values)], axis=1)


def model_pipeline(numeric_cols: list[str], categorical_cols: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric_cols,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_cols,
            ),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    C=0.35,
                    max_iter=5000,
                    random_state=RNG_SEED,
                ),
            ),
        ]
    )


def metric_rows(frame: pd.DataFrame, probability: np.ndarray) -> pd.DataFrame:
    p = np.clip(np.asarray(probability, dtype=float), 1e-5, 1 - 1e-5)
    y = frame["win"].astype(float).to_numpy()
    return pd.DataFrame(
        {
            "_ticket_id": frame["_ticket_id"].to_numpy(),
            "target_date": frame["target_date"].to_numpy(),
            "city": frame["city"].to_numpy(),
            "source": frame["source"].to_numpy(),
            "win": y,
            "p": p,
            "brier": np.square(p - y),
            "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p)),
        }
    )


def summarize_metric(rows: pd.DataFrame, *, model: str, eval_window: str) -> dict[str, object]:
    return {
        "model": model,
        "eval_window": eval_window,
        "rows": len(rows),
        "dates": int(rows["target_date"].nunique()),
        "wins": int(rows["win"].sum()),
        "mean_p": float(rows["p"].mean()),
        "brier": float(brier_score_loss(rows["win"], rows["p"])),
        "logloss": float(log_loss(rows["win"], rows["p"], labels=[0.0, 1.0])),
    }


def block_delta(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    metric: str,
) -> tuple[float, float, float]:
    paired = candidate[["_ticket_id", "target_date", metric]].merge(
        baseline[["_ticket_id", metric]],
        on="_ticket_id",
        suffixes=("_candidate", "_baseline"),
    )
    paired["delta"] = paired[f"{metric}_candidate"] - paired[f"{metric}_baseline"]
    point = float(paired["delta"].mean())
    rng = np.random.default_rng(RNG_SEED)
    blocks = {
        day: group["delta"].to_numpy()
        for day, group in paired.groupby("target_date", sort=False)
    }
    dates = np.asarray(list(blocks))
    samples = []
    for _ in range(N_BOOT):
        picked = rng.choice(dates, size=len(dates), replace=True)
        samples.append(float(np.concatenate([blocks[day] for day in picked]).mean()))
    return point, float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def entry_ablation(
    hist: pd.DataFrame,
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = hist[hist["target_date"].le(TRAIN_END)].copy()
    recent = hist[hist["target_date"].ge(RECENT_START)].copy()
    specs = {
        "market_raw": ([], []),
        "entry_base": (
            ["market_logit", "distance_br", "abs_lat"],
            ["source", "city_archetype", "source_x_archetype"],
        ),
        "entry_plus_multisource": (
            [
                "market_logit",
                "distance_br",
                "abs_lat",
                "multi_hot_share",
                "multi_spread_f",
                "assigned_minus_consensus_f",
            ],
            ["source", "city_archetype", "source_x_archetype"],
        ),
        "entry_plus_prior_bias": (
            [
                "market_logit",
                "distance_br",
                "abs_lat",
                "prior_bias_median_f",
                "prior_mae_f",
                "prior_underforecast_rate",
            ],
            ["source", "city_archetype", "source_x_archetype"],
        ),
        "entry_plus_multisource_and_bias": (
            [
                "market_logit",
                "distance_br",
                "abs_lat",
                "multi_hot_share",
                "multi_spread_f",
                "assigned_minus_consensus_f",
                "prior_bias_median_f",
                "prior_mae_f",
                "prior_underforecast_rate",
            ],
            ["source", "city_archetype", "source_x_archetype"],
        ),
    }
    scores: list[dict[str, object]] = []
    predictions: dict[tuple[str, str], pd.DataFrame] = {}
    for name, (num, cat) in specs.items():
        if name == "market_raw":
            for label, frame in [("historical_recent", recent), ("current_shadow", current)]:
                rows = metric_rows(frame, frame["ask"].to_numpy())
                scores.append(summarize_metric(rows, model=name, eval_window=label))
                predictions[(name, label)] = rows
            continue
        model = model_pipeline(num, cat)
        model.fit(train[num + cat], train["win"])
        for label, frame in [("historical_recent", recent), ("current_shadow", current)]:
            rows = metric_rows(frame, model.predict_proba(frame[num + cat])[:, 1])
            scores.append(summarize_metric(rows, model=name, eval_window=label))
            predictions[(name, label)] = rows

    deltas: list[dict[str, object]] = []
    for eval_window in ["historical_recent", "current_shadow"]:
        baseline = predictions[("entry_base", eval_window)]
        for name in [
            "entry_plus_multisource",
            "entry_plus_prior_bias",
            "entry_plus_multisource_and_bias",
        ]:
            candidate = predictions[(name, eval_window)]
            row: dict[str, object] = {"eval_window": eval_window, "model": name}
            for metric in ["brier", "logloss"]:
                point, low, high = block_delta(candidate, baseline, metric=metric)
                row[f"{metric}_delta_vs_entry_base"] = point
                row[f"{metric}_delta_ci_low"] = low
                row[f"{metric}_delta_ci_high"] = high
            deltas.append(row)

    pred_current = []
    for (name, window), frame in predictions.items():
        if window == "current_shadow":
            copy = frame.copy()
            copy["model"] = name
            pred_current.append(copy)
    return pd.DataFrame(scores), pd.DataFrame(deltas), pd.concat(pred_current, ignore_index=True)


def exact_bounds_f(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    low = np.where(
        frame["unit"].eq("C"),
        (numeric(frame["bracket_low_native"]) - 0.5) * 9.0 / 5.0 + 32.0,
        numeric(frame["bracket_low_native"]) - 0.5,
    )
    high = np.where(
        frame["unit"].eq("C"),
        (numeric(frame["bracket_high_native"]) + 0.5) * 9.0 / 5.0 + 32.0,
        numeric(frame["bracket_high_native"]) + 0.5,
    )
    return low, high


def exact_probability(mu: pd.Series, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    return np.clip(
        norm.cdf((high - numeric(mu)) / SIGMA_F)
        - norm.cdf((low - numeric(mu)) / SIGMA_F),
        1e-5,
        1 - 1e-5,
    )


def late_innovation(
    current: pd.DataFrame,
    innovation_path: Path,
    checkpoints_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    innovation = pd.read_csv(innovation_path, low_memory=False)
    checkpoints = pd.read_csv(checkpoints_path, low_memory=False)
    score_rows: list[dict[str, object]] = []
    delta_rows: list[dict[str, object]] = []
    detail_frames: list[pd.DataFrame] = []
    for hour in [9, 12]:
        feature = (
            innovation[innovation["checkpoint_hour_local"].eq(hour)]
            .sort_values("tmax_state_id")
            .drop_duplicates(["city", "target_date"])
        )
        detail = current.merge(
            feature[
                [
                    "city",
                    "target_date",
                    "forecast_innovation_f",
                    "pred_rolling_bias_f",
                    "pred_innovation_f",
                    "pred_innovation_regime_f",
                    "settlement_mid_f",
                ]
            ],
            on=["city", "target_date"],
            how="inner",
        )
        low, high = exact_bounds_f(detail)
        probabilities = {
            "rolling_bias": exact_probability(detail["pred_rolling_bias_f"], low, high),
            "innovation": exact_probability(detail["pred_innovation_f"], low, high),
            "innovation_regime": exact_probability(
                detail["pred_innovation_regime_f"], low, high
            ),
        }
        metric_sets: dict[str, pd.DataFrame] = {}
        for name, probability in probabilities.items():
            rows = metric_rows(detail, probability)
            metric_sets[name] = rows
            score = summarize_metric(
                rows,
                model=name,
                eval_window=f"checkpoint_{hour}",
            )
            score["checkpoint_hour_local"] = hour
            score["tmax_mae_f"] = float(
                np.mean(
                    np.abs(
                        numeric(
                            detail[
                                "pred_rolling_bias_f"
                                if name == "rolling_bias"
                                else (
                                    "pred_innovation_f"
                                    if name == "innovation"
                                    else "pred_innovation_regime_f"
                                )
                            ]
                        )
                        - numeric(detail["settlement_mid_f"])
                    )
                )
            )
            score_rows.append(score)
        for name in ["innovation", "innovation_regime"]:
            row: dict[str, object] = {
                "checkpoint_hour_local": hour,
                "model": name,
                "rows": len(detail),
                "dates": int(detail["target_date"].nunique()),
                "wins": int(detail["win"].sum()),
            }
            for metric in ["brier", "logloss"]:
                point, low_ci, high_ci = block_delta(
                    metric_sets[name],
                    metric_sets["rolling_bias"],
                    metric=metric,
                )
                row[f"{metric}_delta_vs_rolling_bias"] = point
                row[f"{metric}_delta_ci_low"] = low_ci
                row[f"{metric}_delta_ci_high"] = high_ci
            delta_rows.append(row)

        detail["checkpoint_hour_local"] = hour
        detail["p_rolling_bias"] = probabilities["rolling_bias"]
        detail["p_innovation"] = probabilities["innovation"]
        detail["p_innovation_regime"] = probabilities["innovation_regime"]
        detail["brier_delta_innovation"] = np.square(
            detail["p_innovation"] - detail["win"].astype(float)
        ) - np.square(detail["p_rolling_bias"] - detail["win"].astype(float))
        y = detail["win"].astype(float)
        detail["logloss_delta_innovation"] = -(
            y * np.log(detail["p_innovation"])
            + (1 - y) * np.log(1 - detail["p_innovation"])
        ) + (
            y * np.log(detail["p_rolling_bias"])
            + (1 - y) * np.log(1 - detail["p_rolling_bias"])
        )
        detail_frames.append(detail)

    checkpoint_time = (
        checkpoints[checkpoints["checkpoint_hour_local"].isin([9, 12])]
        .sort_values("tmax_state_id")
        .drop_duplicates(["city", "target_date", "checkpoint_hour_local"])
        [["city", "target_date", "checkpoint_hour_local", "decision_ts_utc"]]
    )
    details = pd.concat(detail_frames, ignore_index=True)
    details = details.merge(
        checkpoint_time,
        on=["city", "target_date", "checkpoint_hour_local"],
        how="left",
    )
    details["entry_ts"] = pd.to_datetime(details["created_at_utc"], utc=True, errors="coerce")
    details["checkpoint_ts"] = pd.to_datetime(
        details["decision_ts_utc"], utc=True, errors="coerce"
    )
    details["hours_after_entry"] = (
        details["checkpoint_ts"] - details["entry_ts"]
    ).dt.total_seconds() / 3600.0
    return pd.DataFrame(score_rows), pd.DataFrame(delta_rows), details


def entry_current_source_deltas(predictions: pd.DataFrame) -> pd.DataFrame:
    baseline = predictions[predictions["model"].eq("entry_base")][
        ["_ticket_id", "brier", "logloss"]
    ]
    rows: list[dict[str, object]] = []
    for model in [
        "entry_plus_multisource",
        "entry_plus_prior_bias",
        "entry_plus_multisource_and_bias",
    ]:
        candidate = predictions[predictions["model"].eq(model)].merge(
            baseline,
            on="_ticket_id",
            suffixes=("", "_baseline"),
        )
        for source, group in candidate.groupby("source"):
            rows.append(
                {
                    "model": model,
                    "source": source,
                    "rows": len(group),
                    "wins": int(group["win"].sum()),
                    "brier_delta_vs_entry_base": float(
                        (group["brier"] - group["brier_baseline"]).mean()
                    ),
                    "logloss_delta_vs_entry_base": float(
                        (group["logloss"] - group["logloss_baseline"]).mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def late_slice_deltas(details: pd.DataFrame) -> pd.DataFrame:
    hour12 = details[details["checkpoint_hour_local"].eq(12)]
    rows: list[dict[str, object]] = []
    for dimension in ["source", "consensus_bucket", "weather_regime"]:
        for value, group in hour12.groupby(dimension, dropna=False):
            rows.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "rows": len(group),
                    "dates": int(group["target_date"].nunique()),
                    "wins": int(group["win"].sum()),
                    "brier_delta_innovation": float(
                        group["brier_delta_innovation"].mean()
                    ),
                    "logloss_delta_innovation": float(
                        group["logloss_delta_innovation"].mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def late_sigma_sensitivity(details: pd.DataFrame) -> pd.DataFrame:
    low, high = exact_bounds_f(details)
    y = details["win"].astype(float).to_numpy()
    rows: list[dict[str, object]] = []
    for sigma_f in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
        baseline = np.clip(
            norm.cdf((high - numeric(details["pred_rolling_bias_f"])) / sigma_f)
            - norm.cdf((low - numeric(details["pred_rolling_bias_f"])) / sigma_f),
            1e-5,
            1 - 1e-5,
        )
        candidate = np.clip(
            norm.cdf((high - numeric(details["pred_innovation_f"])) / sigma_f)
            - norm.cdf((low - numeric(details["pred_innovation_f"])) / sigma_f),
            1e-5,
            1 - 1e-5,
        )
        brier_delta = np.square(candidate - y) - np.square(baseline - y)
        logloss_delta = -(
            y * np.log(candidate) + (1 - y) * np.log(1 - candidate)
        ) + (y * np.log(baseline) + (1 - y) * np.log(1 - baseline))
        for hour in [9, 12]:
            mask = details["checkpoint_hour_local"].eq(hour).to_numpy()
            rows.append(
                {
                    "checkpoint_hour_local": hour,
                    "sigma_f": sigma_f,
                    "rows": int(mask.sum()),
                    "brier_delta_vs_rolling_bias": float(brier_delta[mask].mean()),
                    "logloss_delta_vs_rolling_bias": float(
                        logloss_delta[mask].mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def entry_clock_audit(current: pd.DataFrame, profiles_path: Path) -> dict[str, object]:
    raw = json.loads(profiles_path.read_text(encoding="utf-8"))
    timezones = {
        str(row["city"]): str(row.get("timezone_name") or "UTC")
        for row in raw.get("source_profiles", [])
    }
    local_hours = []
    local_dates = []
    for row in current.itertuples(index=False):
        ts = pd.Timestamp(row.created_at_utc).to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            local = ts.astimezone(ZoneInfo(timezones.get(str(row.city), "UTC")))
        except Exception:
            local = ts.astimezone(timezone.utc)
        local_hours.append(local.hour + local.minute / 60.0)
        local_dates.append(local.date().isoformat())
    target = current["target_date"].astype(str).to_numpy()
    local_dates_arr = np.asarray(local_dates)
    local_hours_arr = np.asarray(local_hours)
    before_target_09 = (local_dates_arr < target) | (
        (local_dates_arr == target) & (local_hours_arr < 9)
    )
    return {
        "rows": len(current),
        "before_target_local_09_rows": int(before_target_09.sum()),
        "on_prior_local_date_rows": int((local_dates_arr < target).sum()),
        "entry_local_hour_median": float(np.median(local_hours_arr)),
        "entry_local_hour_min": float(np.min(local_hours_arr)),
        "entry_local_hour_max": float(np.max(local_hours_arr)),
    }


def md_table(frame: pd.DataFrame, cols: list[str]) -> str:
    if frame.empty:
        return "_无数据_"
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in frame.iterrows():
        vals = []
        for col in cols:
            value = row.get(col)
            if pd.isna(value):
                vals.append("")
            elif isinstance(value, (float, np.floating)):
                vals.append(f"{float(value):.5f}")
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(
    *,
    clock: dict[str, object],
    entry_scores: pd.DataFrame,
    entry_deltas: pd.DataFrame,
    entry_source_deltas: pd.DataFrame,
    late_scores: pd.DataFrame,
    late_deltas: pd.DataFrame,
    late_slices: pd.DataFrame,
    sigma_sensitivity: pd.DataFrame,
    details: pd.DataFrame,
    hist: pd.DataFrame,
    current: pd.DataFrame,
) -> str:
    hour12 = details[details["checkpoint_hour_local"].eq(12)]
    drop_lucknow = hour12[hour12["city"].ne("Lucknow")]
    loser_only = hour12[~hour12["win"]]
    return f"""# HeadA 多源 + forecast bias/升温路径增量检验 v1

Generated: {now_utc()}

## 结论与动作

**有帮助，但帮助发生在两个不同阶段，不能把两类信息直接混成原 entry selector。**

- D-1/前夜 entry：multi-source 单独加入后两个测试窗都变差；严格 prior rolling bias 在
  historical recent 明显改善、current shadow 仅小幅改善且 CI 跨 0。不改 HeadA eligibility。
- 当天 09:00/12:00：actual warming innovation 对已经持有的 exact ticket 有条件增量，12:00 的
  exact-ticket Brier / logloss 相对 rolling-bias baseline 改善且 date-block CI 不跨 0。
  但只有 32 tickets / 6 dates / 3 wins，且没有同刻 fresh book，当前只能作为 hold/exit/re-entry
  shadow feature。

裁决：`entry=inconclusive`；`intraday=shadow_candidate`；`live_action=none`。

## 时钟与 leakage 边界

当前 84 张：

```json
{json.dumps(clock, ensure_ascii=False, indent=2)}
```

84/84 都在目标日当地 09:00 前进入，其中 {clock["on_prior_local_date_rows"]}/84 在目标日前一当地日期。
所以 09:00/12:00 的 `observed - model` innovation 在原 entry 尚未发生。把它回填到 entry 模型会产生
future leakage；正确架构是：

```text
D-1 entry:
  market + multi-source state + prior rolling bias

target-day update:
  actual warming path innovation
  -> hold / exit / re-entry / exact-bracket redistribution
```

## 固定分母与证据漏斗

### Signal funnel

- historical entry denominator：{len(hist)} tickets / {hist.target_date.nunique()} dates。
- current frozen shadow：{len(current)} tickets / {current.target_date.nunique()} dates。
- 09:00 overlap：{int((details.checkpoint_hour_local == 9).sum())} tickets。
- 12:00 overlap：{int((details.checkpoint_hour_local == 12).sum())} tickets。

### Evidence funnel

- entry fresh ask / settlement：current 84/84。
- multi-source archive replay：current {int(current.multi_model_n.ge(5).sum())}/84；
  无精确 decision-version alternate-source timestamp，只作 post-hoc。
- prior bias：只使用 target_date 之前 14 天 settlement error，避免 outcome leakage。
  current 有 prior error 的票为 {int(current.prior_bias_n.gt(0).sum())}/84。
- morning innovation：PIT observation + assigned curve OOF，但 overlap 只有 6 dates。
- morning fresh exact-ticket quote：缺失；因此不发布 intraday fee-adjusted ROI。
- actual fill：0，zero-notional/research replay。

## A. Entry-time ablation

训练：historical ≤{TRAIN_END}；测试：historical {RECENT_START}+ 与 current frozen shadow。
固定 rows/labels/ask；L2 logistic 不扫阈值。

{md_table(entry_scores, ["eval_window", "model", "rows", "dates", "wins", "mean_p", "brier", "logloss"])}

candidate 相对同 rows `entry_base` 的 paired target-date block delta；负数才是改善：

{md_table(entry_deltas, ["eval_window", "model", "brier_delta_vs_entry_base", "brier_delta_ci_low", "brier_delta_ci_high", "logloss_delta_vs_entry_base", "logloss_delta_ci_low", "logloss_delta_ci_high"])}

解读：

1. multi-source 的价值目前只是 uncertainty / reach-hot-tail state，**没有转化为 exact-ticket alpha**：
   current Brier delta `+0.00461`，joint model `+0.00760`，方向都是变差。
2. prior bias 能修正每城/每源长期偏热偏冷：historical recent Brier delta `-0.00447`
   （CI 略跨 0），logloss delta `-0.01851`（CI 刚好不跨 0）；current 只有
   Brier `-0.00070`，CI 跨 0。exact bracket 仍受 overshoot 与分布宽度控制。
3. 若 joint model 不能在 historical recent 和 current 同时保持负 delta，就只能继续 telemetry，
   不能按 score 过滤彩票。

current 按 assigned source 拆开（只是诊断，不是独立显著性检验）：

{md_table(entry_source_deltas, ["model", "source", "rows", "wins", "brier_delta_vs_entry_base", "logloss_delta_vs_entry_base"])}

prior bias 的 current 改善来自 ECMWF；GFS 反而变差，因此它不能解释为“加入 bias 后可恢复 GFS”。

## B. Target-day warming innovation

概率构造固定为 `Normal(predicted Tmax, σ={SIGMA_F:.1f}F)` 在所买 exact bracket settlement interval
上的质量；只比较 rolling bias 与 expanding-OOF innovation，同 rows、同 label。

{md_table(late_scores, ["checkpoint_hour_local", "model", "rows", "dates", "wins", "mean_p", "brier", "logloss", "tmax_mae_f"])}

paired target-date block delta：

{md_table(late_deltas, ["checkpoint_hour_local", "model", "rows", "dates", "wins", "brier_delta_vs_rolling_bias", "brier_delta_ci_low", "brier_delta_ci_high", "logloss_delta_vs_rolling_bias", "logloss_delta_ci_low", "logloss_delta_ci_high"])}

12:00 plain innovation 的改善不是完全普遍：

- 全部 rows mean Brier delta：
  {hour12.brier_delta_innovation.mean():+.5f}。
- 去掉贡献最大的 Lucknow city-date 后：
  {drop_lucknow.brier_delta_innovation.mean():+.5f}。
- 只看 losers：
  {loser_only.brier_delta_innovation.mean():+.5f}。
- checkpoint 在原 entry 后的中位时间：
  {details.hours_after_entry.median():.1f} 小时；最小
  {details.hours_after_entry.min():.1f} 小时。

12:00 的机制切片：

{md_table(late_slices, ["dimension", "value", "rows", "dates", "wins", "brier_delta_innovation", "logloss_delta_innovation"])}

改善主要出现在 mixed-hot 共识（3 个 winners 全在该组）与 rain/convective，但都是同一小样本的
post-hoc 解释；strong-hot 组只有微弱改善。GFS 的 12 张虽有小幅 Brier 改善，但 0 win，
只能说明概率被往低处校准得更合理，不能证明 GFS 彩票变得可买。

分布宽度敏感性（负数为改善）：

{md_table(sigma_sensitivity, ["checkpoint_hour_local", "sigma_f", "rows", "brier_delta_vs_rolling_bias", "logloss_delta_vs_rolling_bias"])}

从 σ=2F 到 5F，point estimate 方向一致；因此 12:00 改善不是 σ=3F 的单点产物。

这说明 innovation 有物理信息，但主要用途是**重新分配 exact-bracket 概率**：

- 正 innovation 可能提高 reach，同时增加 hotter-bracket overshoot；
- 负 innovation 可能降低 reach，但也可能把过热预测拉回当前 ticket；
- 因此不能用 `innovation > 0` 做 BUY-YES hard gate，应把整条 Tmax distribution 平移/缩放后重算每档概率。

## 最终答案

1. **混合预测源：有解释价值，但这轮没有可量化的 entry score 增益。**
   post-hoc coverage 和 forward baseline 都未过门，不能替换 assigned source 或筛 GFS。
2. **prior forecast bias：值得作为 D-1 概率头的 shadow candidate。** 它事前可用、机制清楚；
   但 current 增益尚不显著，只应连续校准中心，不做 city/source blacklist。
3. **实际升温路径 innovation：对尾部彩票更可能有用在 target-day 管理，不是初始进场。**
   12:00 exact-ticket proper score 有初步正增量，但样本和 book coverage 不足。
4. **最合理的下一版是两阶段概率策略**，不是再加一条 filter：

```text
entry distribution = multi-source consensus + prior city/source bias
intraday posterior = entry distribution + beta(clock, regime) * forecast innovation
trade decision = posterior exact-bracket probability - fresh executable cost
```

三门：

```text
entry significance/baseline = FAIL
intraday feature significance = PARTIAL PASS (12:00 only, 6 dates)
same-time market/execution = FAIL/NA
fresh frozen forward = NA
conclusion = intraday shadow_candidate; no live change
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_multisource_innovation_ablation_v1.py`
- structured：`generated/heada_multisource_innovation_ablation_v1/summary.json`
- entry：`entry_probability_scorecard.csv`, `entry_paired_deltas.csv`
- intraday：`late_probability_scorecard.csv`, `late_paired_deltas.csv`, `late_details.csv`
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    hist, current = load_frames()
    errors = pd.read_csv(ERRORS, low_memory=False)
    hist = add_prior_bias(hist, errors)
    current = add_prior_bias(current, errors)
    entry_scores, entry_deltas, entry_predictions = entry_ablation(hist, current)
    entry_source_deltas = entry_current_source_deltas(entry_predictions)
    late_scores, late_deltas, late_details = late_innovation(
        current,
        INNOVATION,
        CHECKPOINTS,
    )
    late_slices = late_slice_deltas(late_details)
    sigma_sensitivity = late_sigma_sensitivity(late_details)
    clock = entry_clock_audit(current, PROFILES)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "entry_probability_scorecard.csv": entry_scores,
        "entry_paired_deltas.csv": entry_deltas,
        "entry_current_predictions.csv": entry_predictions,
        "entry_current_source_deltas.csv": entry_source_deltas,
        "late_probability_scorecard.csv": late_scores,
        "late_paired_deltas.csv": late_deltas,
        "late_slice_deltas.csv": late_slices,
        "late_sigma_sensitivity.csv": sigma_sensitivity,
        "late_details.csv": late_details,
    }
    for name, frame in outputs.items():
        frame.to_csv(args.out_dir / name, index=False)
    summary = {
        "generated_at_utc": now_utc(),
        "entry_clock": clock,
        "denominator": {
            "historical_rows": len(hist),
            "historical_dates": int(hist["target_date"].nunique()),
            "current_rows": len(current),
            "current_dates": int(current["target_date"].nunique()),
        },
        "late_coverage": {
            str(hour): {
                "rows": int((late_details["checkpoint_hour_local"] == hour).sum()),
                "dates": int(
                    late_details.loc[
                        late_details["checkpoint_hour_local"] == hour,
                        "target_date",
                    ].nunique()
                ),
            }
            for hour in [9, 12]
        },
        "verdict": {
            "entry": "inconclusive",
            "intraday": "shadow_candidate",
            "live_action": "none",
        },
        "report": str(args.report),
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.report.write_text(
        build_report(
            clock=clock,
            entry_scores=entry_scores,
            entry_deltas=entry_deltas,
            entry_source_deltas=entry_source_deltas,
            late_scores=late_scores,
            late_deltas=late_deltas,
            late_slices=late_slices,
            sigma_sensitivity=sigma_sensitivity,
            details=late_details,
            hist=hist,
            current=current,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
