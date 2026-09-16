"""
P0 scorecard for intraday Tmax local distribution anchors.

This is an offline diagnostic. It does not change live behavior.

The intraday atlas row is not a full bracket ladder. It only has enough quote
state for the local expression neighborhood: current bracket, d1, d2, and the
remaining upper tail. This script therefore scores a local four-bucket
distribution:

    current / d1 / d2 / tail

Baselines:
    - market_local_norm: normalize local market-implied midpoint weights.
    - forecast_anchor: a smooth anchor around forecast_max_native.
    - runningmax_anchor: a smooth anchor around running_native.
    - uniform: no-information four-bucket baseline.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ATLAS_PATH = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    / "intraday_weather_regime_state_rows.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.json"

BUCKETS = ["current", "d1", "d2", "tail"]
METHODS = ["uniform", "market_local_norm", "forecast_anchor", "runningmax_anchor"]
EPS = 1e-9


def _as_float(x: object) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _num_parts(label: object) -> list[float]:
    if label is None or (isinstance(label, float) and math.isnan(label)):
        return []
    return [float(x) for x in re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", str(label))]


def _interval(label: object) -> tuple[float, float] | None:
    """Approximate a settlement bracket label as a native-unit interval."""
    text = "" if label is None else str(label).lower()
    nums = _num_parts(label)
    if not nums:
        return None
    if "+" in text or "above" in text:
        return (nums[0] - 0.5, math.inf)
    if "below" in text or "or less" in text:
        return (-math.inf, nums[0] + 0.5)
    if len(nums) >= 2:
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        return (lo - 0.5, hi + 0.5)
    v = nums[0]
    return (v - 0.5, v + 0.5)


def _interval_distance(x: float, interval: tuple[float, float]) -> float:
    lo, hi = interval
    if x < lo:
        return lo - x
    if x > hi:
        return x - hi
    return 0.0


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    clipped = {k: max(EPS, float(v)) for k, v in weights.items()}
    total = sum(clipped.values())
    return {k: v / total for k, v in clipped.items()}


def _soft_anchor_distribution(
    x: float | None,
    current_interval: tuple[float, float],
    d1_interval: tuple[float, float],
    d2_interval: tuple[float, float],
    *,
    sigma: float = 0.75,
) -> dict[str, float]:
    if x is None:
        return {b: 1.0 / len(BUCKETS) for b in BUCKETS}
    tail_lower = d2_interval[1]
    if math.isinf(tail_lower):
        tail_lower = d2_interval[0] + 1.0
    intervals = {
        "current": current_interval,
        "d1": d1_interval,
        "d2": d2_interval,
        "tail": (tail_lower, math.inf),
    }
    weights: dict[str, float] = {}
    for bucket, interval in intervals.items():
        dist = _interval_distance(float(x), interval)
        weights[bucket] = math.exp(-0.5 * (dist / sigma) ** 2)
    return _normalize(weights)


def _market_raw_weights(row: pd.Series) -> dict[str, float] | None:
    current_yes_ask = _as_float(row.get("current_yes_ask"))
    current_no = _as_float(row.get("current_bracket_no_ask"))
    current_no_bid = _as_float(row.get("current_no_bid"))
    d1_no = _as_float(row.get("d1_no_ask"))
    d1_no_bid = _as_float(row.get("d1_no_bid"))
    d2_no = _as_float(row.get("d2_no_ask"))
    d2_no_bid = _as_float(row.get("d2_no_bid"))
    if current_yes_ask is None or current_no is None or d1_no is None or d2_no is None:
        return None
    current_yes_bid = 1.0 - current_no
    current_yes = (current_yes_bid + current_yes_ask) / 2.0
    if current_no_bid is not None:
        current_yes = (current_yes + (1.0 - current_no_bid)) / 2.0
    d1_yes = 1.0 - d1_no
    if d1_no_bid is not None:
        d1_yes = 1.0 - ((d1_no + d1_no_bid) / 2.0)
    d2_yes = 1.0 - d2_no
    if d2_no_bid is not None:
        d2_yes = 1.0 - ((d2_no + d2_no_bid) / 2.0)
    weights = {
        "current": min(1.0, max(EPS, current_yes)),
        "d1": min(1.0, max(EPS, d1_yes)),
        "d2": min(1.0, max(EPS, d2_yes)),
    }
    raw_sum = sum(weights.values())
    weights["tail"] = max(EPS, 1.0 - raw_sum)
    return weights


def _market_distribution(row: pd.Series) -> dict[str, float] | None:
    weights = _market_raw_weights(row)
    if weights is None:
        return None
    return _normalize(weights)


def _label(row: pd.Series) -> str | None:
    final = row.get("final_winning_bracket")
    cur = row.get("current_bracket")
    d1 = row.get("d1_no_bracket")
    d2 = row.get("d2_no_bracket")
    if pd.isna(final) or pd.isna(cur) or pd.isna(d1) or pd.isna(d2):
        return None
    final_s, cur_s, d1_s, d2_s = str(final), str(cur), str(d1), str(d2)
    if len({cur_s, d1_s, d2_s}) != 3:
        return None
    final_iv = _interval(final_s)
    cur_iv = _interval(cur_s)
    if final_iv is None or cur_iv is None:
        return None
    if final_iv[0] < cur_iv[0] - 1e-6:
        return None
    if final_s == cur_s:
        return "current"
    if final_s == d1_s:
        return "d1"
    if final_s == d2_s:
        return "d2"
    return "tail"


def _entropy_norm(probs: dict[str, float]) -> float:
    h = -sum(p * math.log(max(EPS, p)) for p in probs.values())
    return h / math.log(len(probs))


def _score_distribution(probs: dict[str, float], actual: str) -> dict[str, float]:
    p_win = max(EPS, probs[actual])
    brier = 0.0
    for bucket in BUCKETS:
        y = 1.0 if bucket == actual else 0.0
        brier += (probs[bucket] - y) ** 2
    top = max(probs, key=probs.get)
    rank = 1 + sum(1 for p in probs.values() if p > probs[actual])
    return {
        "logloss": -math.log(p_win),
        "brier": brier,
        "top1": float(top == actual),
        "winner_prob": p_win,
        "winner_rank": float(rank),
        "entropy": _entropy_norm(probs),
    }


def _hour_bucket(hour: object) -> str:
    h = _as_float(hour)
    if h is None:
        return "hour_unknown"
    if h <= 11:
        return "10_11"
    if h <= 13:
        return "12_13"
    if h <= 16:
        return "14_16"
    return "17_21"


def _split_name(target_date: str) -> str:
    if str(target_date) < "2026-06-21":
        return "train_pre_2026_06_21"
    return "forward_2026_06_21_plus"


def _slice_value(row: pd.Series, slice_name: str) -> str:
    if slice_name == "full":
        return "full"
    if slice_name == "split":
        return str(row["split"])
    if slice_name == "day_regime":
        return str(row.get("day_regime") or "unknown")
    if slice_name == "intraday_state":
        return str(row.get("intraday_state") or "unknown")
    if slice_name == "hour_bucket":
        return str(row["hour_bucket"])
    if slice_name == "forecast_source":
        return str(row.get("forecast_source") or "unknown")
    raise ValueError(slice_name)


def _load_scored_rows() -> tuple[pd.DataFrame, dict[str, int]]:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "final_winning_bracket",
        "current_yes_ask",
        "current_bracket_no_ask",
        "current_no_bid",
        "d1_no_ask",
        "d1_no_bid",
        "d2_no_ask",
        "d2_no_bid",
        "forecast_max_native",
        "running_native",
        "final_max_native",
        "forecast_source",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "city_family",
    ]
    raw = pd.read_csv(ATLAS_PATH, usecols=lambda c: c in cols)
    counters = {"raw_rows": int(len(raw))}
    scored: list[dict[str, object]] = []
    skipped = {
        "missing_or_invalid_label": 0,
        "missing_interval": 0,
        "missing_market_quote": 0,
    }
    for row in raw.to_dict("records"):
        s = pd.Series(row)
        actual = _label(s)
        if actual is None:
            skipped["missing_or_invalid_label"] += 1
            continue
        cur_iv = _interval(s.get("current_bracket"))
        d1_iv = _interval(s.get("d1_no_bracket"))
        d2_iv = _interval(s.get("d2_no_bracket"))
        if cur_iv is None or d1_iv is None or d2_iv is None:
            skipped["missing_interval"] += 1
            continue
        dists: dict[str, dict[str, float]] = {
            "uniform": {b: 1.0 / len(BUCKETS) for b in BUCKETS},
            "forecast_anchor": _soft_anchor_distribution(
                _as_float(s.get("forecast_max_native")), cur_iv, d1_iv, d2_iv
            ),
            "runningmax_anchor": _soft_anchor_distribution(
                _as_float(s.get("running_native")), cur_iv, d1_iv, d2_iv
            ),
        }
        raw_market = _market_raw_weights(s)
        market = _normalize(raw_market) if raw_market is not None else None
        if market is None:
            skipped["missing_market_quote"] += 1
            continue
        dists["market_local_norm"] = market
        out = dict(row)
        out["actual_bucket"] = actual
        out["split"] = _split_name(str(row.get("target_date")))
        out["hour_bucket"] = _hour_bucket(row.get("decision_hour_local"))
        raw_three_bucket_mass = float(raw_market["current"] + raw_market["d1"] + raw_market["d2"])
        out["market_raw_local_mass"] = raw_three_bucket_mass
        out["market_tail_residual_raw"] = float(raw_market["tail"])
        out["market_local_overround_raw"] = max(0.0, raw_three_bucket_mass - 1.0)
        out["market_local_undermass_raw"] = max(0.0, 1.0 - raw_three_bucket_mass)
        for method, probs in dists.items():
            for bucket, p in probs.items():
                out[f"{method}_p_{bucket}"] = p
            scores = _score_distribution(probs, actual)
            for k, v in scores.items():
                out[f"{method}_{k}"] = v
        scored.append(out)
    counters.update(skipped)
    counters["scored_rows"] = int(len(scored))
    return pd.DataFrame(scored), counters


def _summary(df: pd.DataFrame, slice_cols: Iterable[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for slice_col in slice_cols:
        for slice_value, grp in df.groupby(slice_col, dropna=False):
            if len(grp) < 20 and slice_col != "split":
                continue
            for method in METHODS:
                rows.append(
                    {
                        "slice_type": slice_col,
                        "slice": slice_value,
                        "method": method,
                        "n": int(len(grp)),
                        "dates": int(grp["target_date"].nunique()),
                        "cities": int(grp["city"].nunique()),
                        "logloss": float(grp[f"{method}_logloss"].mean()),
                        "brier": float(grp[f"{method}_brier"].mean()),
                        "top1": float(grp[f"{method}_top1"].mean()),
                        "winner_prob": float(grp[f"{method}_winner_prob"].mean()),
                        "winner_rank": float(grp[f"{method}_winner_rank"].mean()),
                        "entropy": float(grp[f"{method}_entropy"].mean()),
                    }
                )
    out = pd.DataFrame(rows)
    return out.sort_values(["slice_type", "slice", "method"]).reset_index(drop=True)


def _pairwise_summary(df: pd.DataFrame, slice_cols: Iterable[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    comparisons = [
        ("forecast_anchor", "market_local_norm"),
        ("runningmax_anchor", "market_local_norm"),
        ("forecast_anchor", "runningmax_anchor"),
        ("market_local_norm", "uniform"),
    ]
    for slice_col in slice_cols:
        for slice_value, grp in df.groupby(slice_col, dropna=False):
            if len(grp) < 20 and slice_col != "split":
                continue
            for left, right in comparisons:
                rows.append(
                    {
                        "slice_type": slice_col,
                        "slice": slice_value,
                        "left": left,
                        "right": right,
                        "n": int(len(grp)),
                        "dates": int(grp["target_date"].nunique()),
                        "logloss_delta_left_minus_right": float(
                            (grp[f"{left}_logloss"] - grp[f"{right}_logloss"]).mean()
                        ),
                        "brier_delta_left_minus_right": float(
                            (grp[f"{left}_brier"] - grp[f"{right}_brier"]).mean()
                        ),
                    }
                )
    return pd.DataFrame(rows).sort_values(["slice_type", "slice", "left", "right"]).reset_index(drop=True)


def _date_block_ci(
    df: pd.DataFrame,
    *,
    left: str,
    right: str,
    metric: str,
    n_boot: int = 1000,
    seed: int = 7,
) -> dict[str, float]:
    dates = sorted(df["target_date"].dropna().unique())
    if len(dates) < 3:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n_dates": len(dates)}
    per_date = []
    for d in dates:
        g = df[df["target_date"] == d]
        per_date.append(float((g[f"{left}_{metric}"] - g[f"{right}_{metric}"]).mean()))
    arr = np.asarray(per_date, dtype=float)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        boot.append(float(np.mean(sample)))
    return {
        "mean": float(np.mean(arr)),
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
        "n_dates": int(len(dates)),
    }


def _compact_table(summary: pd.DataFrame, slice_type: str, slice_value: str) -> list[str]:
    sub = summary[(summary["slice_type"] == slice_type) & (summary["slice"].astype(str) == str(slice_value))]
    lines = [
        "| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sub.itertuples(index=False):
        lines.append(
            f"| {r.method} | {int(r.n)} | {int(r.dates)} | {int(r.cities)} | "
            f"{r.logloss:.4f} | {r.brier:.4f} | {r.top1*100:.1f}% | "
            f"{r.winner_prob:.4f} | {r.entropy:.3f} |"
        )
    return lines


def _write_report(
    scored: pd.DataFrame,
    summary: pd.DataFrame,
    pairwise: pd.DataFrame,
    counters: dict[str, int],
    report: dict[str, object],
) -> None:
    full_market = summary[
        (summary["slice_type"] == "all") & (summary["slice"] == "all") & (summary["method"] == "market_local_norm")
    ].iloc[0]
    full_forecast = summary[
        (summary["slice_type"] == "all") & (summary["slice"] == "all") & (summary["method"] == "forecast_anchor")
    ].iloc[0]
    full_running = summary[
        (summary["slice_type"] == "all") & (summary["slice"] == "all") & (summary["method"] == "runningmax_anchor")
    ].iloc[0]
    market_vs_forecast = report["date_block_ci"]["forecast_minus_market_logloss"]
    market_vs_running = report["date_block_ci"]["runningmax_minus_market_logloss"]
    fwd = summary[(summary["slice_type"] == "split") & (summary["slice"] == "forward_2026_06_21_plus")]
    fwd_best = fwd.sort_values("logloss").iloc[0]

    lines = [
        "# Tmax Distribution P0 Anchor Scorecard v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> atlas: `{ATLAS_PATH.relative_to(ROOT)}`",
        "> Scope: offline intraday local-distribution diagnostic only; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        (
            "- 这不是全新问题：6 月已经做过 city-day 全盘口分布质量研究，结论是 `market_norm` "
            "通常强于 raw forecast/model 分布。"
        ),
        (
            "- 这次补的是当前策略缺的 P0-local：在 intraday atlas 的 current/d1/d2/tail 四桶上，"
            "同台比较 market、forecast anchor、running-max anchor。"
        ),
        (
            f"- 全样本 {int(full_market.n)} 行 / {int(full_market.dates)} 天 / {int(full_market.cities)} 城："
            f"`market_local_norm` logloss `{full_market.logloss:.4f}`，"
            f"`forecast_anchor` `{full_forecast.logloss:.4f}`，"
            f"`runningmax_anchor` `{full_running.logloss:.4f}`。"
        ),
        (
            f"- date-block delta：forecast - market logloss = `{market_vs_forecast['mean']:.4f}` "
            f"CI [`{market_vs_forecast['ci_low']:.4f}`, `{market_vs_forecast['ci_high']:.4f}`]；"
            f"runningmax - market = `{market_vs_running['mean']:.4f}` "
            f"CI [`{market_vs_running['ci_low']:.4f}`, `{market_vs_running['ci_high']:.4f}`]。"
        ),
        (
            f"- 6/21+ forward slice 的最佳 logloss 方法是 `{fwd_best.method}` "
            f"({int(fwd_best.n)} 行，logloss `{fwd_best.logloss:.4f}`)。"
        ),
        "",
        "交易含义：P0 没有证明“只靠 forecast 或 running max 的朴素锚”能打败盘口。下一步如果继续这条线，应该做的是融合模型：",
        "`P(Tmax bucket | realized path, forecast curve, bracket fractional position, solar clock, city/source basis)`，",
        "然后用 proper scoring rule 先打败 market-local，再谈 route/gate/ROI。当前结论不支持直接改 live。", 
        "",
        "## 方法",
        "",
        "- `current/d1/d2/tail` 是 atlas 当前行可观察到的局部分布，不是完整 bracket ladder。",
        "- `market_local_norm`：尽量用 current/d1/d2 的 bid/ask midpoint 推 implied YES，再加 residual tail 后归一化。",
        "- 同时落 `market_raw_local_mass`、`market_tail_residual_raw`、`market_local_overround_raw`，供 P2 检查 proxy 是否失真；这些字段不参与 P0 scoring。",
        "- `forecast_anchor`：把 `forecast_max_native` 到 current/d1/d2/tail interval 的距离做 Gaussian soft anchor。",
        "- `runningmax_anchor`：同上，但锚点换成 `running_native`。",
        "- 指标：logloss / multiclass brier 越低越好；top1 / winner probability 越高越好。",
        "- 没有同步 N100 或重建 fact 表；本实验只消费已生成 atlas CSV，不发布 live_real PnL。",
        "",
        "## 数据覆盖",
        "",
        f"- raw atlas rows: `{counters['raw_rows']}`",
        f"- scored rows: `{counters['scored_rows']}`",
        f"- date range: `{scored['target_date'].min()}` .. `{scored['target_date'].max()}`",
        f"- cities: `{scored['city'].nunique()}`",
        f"- skipped invalid label/grid: `{counters['missing_or_invalid_label']}`",
        f"- skipped missing market local quote: `{counters['missing_market_quote']}`",
        "",
        "## Full Scorecard",
        "",
        *_compact_table(summary, "all", "all"),
        "",
        "## Train / Forward",
        "",
        *_compact_table(summary, "split", "train_pre_2026_06_21"),
        "",
        *_compact_table(summary, "split", "forward_2026_06_21_plus"),
        "",
        "## Day Regime Slice",
        "",
        "| day_regime | method | n | logloss | brier | top1 | winner_p |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    day = summary[summary["slice_type"] == "day_regime"].sort_values(["slice", "method"])
    for r in day.itertuples(index=False):
        lines.append(
            f"| {r.slice} | {r.method} | {int(r.n)} | {r.logloss:.4f} | "
            f"{r.brier:.4f} | {r.top1*100:.1f}% | {r.winner_prob:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Pairwise Delta",
            "",
            "Positive logloss delta means the left method is worse than the right method.",
            "",
            "| slice_type | slice | left - right | n | logloss_delta | brier_delta |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    important = pairwise[
        (pairwise["slice_type"].isin(["all", "split", "day_regime"]))
        & (
            pairwise["left"].isin(["forecast_anchor", "runningmax_anchor", "market_local_norm"])
        )
    ]
    for r in important.itertuples(index=False):
        lines.append(
            f"| {r.slice_type} | {r.slice} | {r.left} - {r.right} | {int(r.n)} | "
            f"{r.logloss_delta_left_minus_right:.4f} | {r.brier_delta_left_minus_right:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 产物",
            "",
            f"- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv`",
            f"- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/summary_by_slice.csv`",
            f"- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/pairwise_deltas.csv`",
            f"- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.json`",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not ATLAS_PATH.exists():
        raise FileNotFoundError(ATLAS_PATH)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scored, counters = _load_scored_rows()
    if scored.empty:
        raise RuntimeError("No scored rows produced")
    scored.insert(0, "all", "all")
    slice_cols = ["all", "split", "day_regime", "intraday_state", "hour_bucket", "forecast_source"]
    summary = _summary(scored, slice_cols)
    pairwise = _pairwise_summary(scored, slice_cols)
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "report_path": str(REPORT_PATH.relative_to(ROOT)),
        "counters": counters,
        "date_range": [str(scored["target_date"].min()), str(scored["target_date"].max())],
        "cities": int(scored["city"].nunique()),
        "date_block_ci": {
            "forecast_minus_market_logloss": _date_block_ci(
                scored, left="forecast_anchor", right="market_local_norm", metric="logloss"
            ),
            "runningmax_minus_market_logloss": _date_block_ci(
                scored, left="runningmax_anchor", right="market_local_norm", metric="logloss"
            ),
            "market_minus_uniform_logloss": _date_block_ci(
                scored, left="market_local_norm", right="uniform", metric="logloss"
            ),
        },
    }
    scored.to_csv(OUT_DIR / "scored_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "summary_by_slice.csv", index=False)
    pairwise.to_csv(OUT_DIR / "pairwise_deltas.csv", index=False)
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _write_report(scored, summary, pairwise, counters, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
