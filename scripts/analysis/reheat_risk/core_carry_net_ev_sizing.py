#!/usr/bin/env python3
"""Helper for the Core Carry runner's continuous net-EV sizing mode."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/analysis/2026-07/generated/current_yes_core_carry_no_obs_age_freeze_pre_live_v5/frozen_policy_entries.csv"
OUT_DIR = ROOT / "docs/analysis/2026-08/generated/current_yes_core_carry_net_ev_sizing_v1"
REPORT = ROOT / "docs/analysis/2026-08/2026-08-06-current-yes-core-carry-net-ev-sizing-v1.md"
SEED = 20260806
REPS = 5000
FORWARD_DATES = 8


def scale_to_mean(score: np.ndarray, target: float = 10.0) -> float:
    lo, hi = 0.0, 1e6
    for _ in range(100):
        mid = (lo + hi) / 2
        if np.clip(score * mid, 5.0, 15.0).mean() < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def summary(frame: pd.DataFrame, shares: str) -> dict[str, float | int]:
    cost = frame[shares] * frame["five_share_cost_per_share"]
    pnl = frame[shares] * (frame["label"] - frame["five_share_cost_per_share"])
    return {
        "rows": len(frame),
        "dates": int(frame.target_date.nunique()),
        "wins": int(frame.label.sum()),
        "accuracy": float(frame.label.mean()),
        "shares": float(frame[shares].sum()),
        "cost_usd": float(cost.sum()),
        "pnl_usd": float(pnl.sum()),
        "roi": float(pnl.sum() / cost.sum()),
        "avg_shares": float(frame[shares].mean()),
        "worst_date_pnl": float(frame.assign(_pnl=pnl).groupby("target_date")._pnl.sum().min()),
    }


def paired_bootstrap(frame: pd.DataFrame, candidate: str) -> list[float]:
    daily = frame.assign(
        base=10.0 * (frame.label - frame.five_share_cost_per_share),
        cand=frame[candidate] * (frame.label - frame.five_share_cost_per_share),
    ).groupby("target_date")[["base", "cand"]].sum()
    delta = (daily.cand - daily.base).to_numpy()
    rng = np.random.default_rng(SEED)
    draws = [float(delta[rng.integers(0, len(delta), len(delta))].mean()) for _ in range(REPS)]
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def main() -> int:
    frame = pd.read_csv(INPUT).sort_values(["target_date", "city"]).reset_index(drop=True)
    dates = sorted(frame.target_date.unique())
    forward = set(dates[-FORWARD_DATES:])
    development = frame[~frame.target_date.isin(forward)].copy()
    frame["net_edge"] = frame.model_probability - frame.five_share_cost_per_share
    frame["kelly_score"] = frame.net_edge / (1.0 - frame.five_share_cost_per_share).clip(lower=1e-6)
    dev_net = development.model_probability - development.five_share_cost_per_share
    dev_kelly = dev_net / (1.0 - development.five_share_cost_per_share).clip(lower=1e-6)
    net_scale = scale_to_mean(dev_net.to_numpy(float))
    kelly_scale = scale_to_mean(dev_kelly.to_numpy(float))
    frame["fixed_10"] = 10.0
    frame["net_ev_linear_5_15"] = np.clip(frame.net_edge * net_scale, 5.0, 15.0)
    frame["kelly_linear_5_15"] = np.clip(frame.kelly_score * kelly_scale, 5.0, 15.0)
    policies = ["fixed_10", "net_ev_linear_5_15", "kelly_linear_5_15"]
    result = {
        "denominator": {
            "rows": len(frame), "dates": len(dates),
            "start": dates[0], "end": dates[-1],
            "forward_dates": sorted(forward),
            "cost_note": "PIT five-share effective per-share cost held fixed; 114/136 rows had top-ask depth >=15 shares",
        },
        "development": {p: summary(frame[~frame.target_date.isin(forward)], p) for p in policies},
        "frozen_forward": {p: summary(frame[frame.target_date.isin(forward)], p) for p in policies},
        "full": {p: summary(frame, p) for p in policies},
        "paired_daily_pnl_delta_ci95": {p: paired_bootstrap(frame, p) for p in policies[1:]},
        "scales_fit_on_development_only": {"net_ev": net_scale, "kelly": kelly_scale},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "sized_entries.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    full = result["full"]
    fwd = result["frozen_forward"]
    REPORT.write_text(f"""# Core Carry continuous net-EV sizing v1

结论：`不升级 live sizing`。同一 136 个 frozen entry 上，连续 sizing 没有增加准确率（信号集合未变），也没有稳定增加美元 PnL。

| policy | full shares | full PnL | ROI | frozen-forward PnL | forward avg shares |
|---|---:|---:|---:|---:|---:|
| fixed 10 | {full['fixed_10']['shares']:.1f} | ${full['fixed_10']['pnl_usd']:+.2f} | {full['fixed_10']['roi']:+.2%} | ${fwd['fixed_10']['pnl_usd']:+.2f} | {fwd['fixed_10']['avg_shares']:.2f} |
| raw net-EV 5–15 | {full['net_ev_linear_5_15']['shares']:.1f} | ${full['net_ev_linear_5_15']['pnl_usd']:+.2f} | {full['net_ev_linear_5_15']['roi']:+.2%} | ${fwd['net_ev_linear_5_15']['pnl_usd']:+.2f} | {fwd['net_ev_linear_5_15']['avg_shares']:.2f} |
| Kelly score 5–15 | {full['kelly_linear_5_15']['shares']:.1f} | ${full['kelly_linear_5_15']['pnl_usd']:+.2f} | {full['kelly_linear_5_15']['roi']:+.2%} | ${fwd['kelly_linear_5_15']['pnl_usd']:+.2f} | {fwd['kelly_linear_5_15']['avg_shares']:.2f} |

两条连续曲线只在 development dates 上校准到平均 10 股，然后原样应用到最后 8 个 frozen-forward target dates。信号、label、PIT quote 与 eligibility 完全相同；变化只有 shares。

- raw net-EV 的 full-window PnL 比 fixed 10 高 `${full['net_ev_linear_5_15']['pnl_usd'] - full['fixed_10']['pnl_usd']:.2f}`，但 frozen-forward 低 `${fwd['fixed_10']['pnl_usd'] - fwd['net_ev_linear_5_15']['pnl_usd']:.2f}`；paired target-date mean-daily PnL delta 95% CI `[{result['paired_daily_pnl_delta_ci95']['net_ev_linear_5_15'][0]:+.2f}, {result['paired_daily_pnl_delta_ci95']['net_ev_linear_5_15'][1]:+.2f}]`。
- Kelly score 的 full-window PnL 高 `${full['kelly_linear_5_15']['pnl_usd'] - full['fixed_10']['pnl_usd']:.2f}`，frozen-forward 低 `${fwd['fixed_10']['pnl_usd'] - fwd['kelly_linear_5_15']['pnl_usd']:.2f}`；paired CI `[{result['paired_daily_pnl_delta_ci95']['kelly_linear_5_15'][0]:+.2f}, {result['paired_daily_pnl_delta_ci95']['kelly_linear_5_15'][1]:+.2f}]`。
- tail 没改善：full-window worst-date PnL 从 fixed 10 的 `${full['fixed_10']['worst_date_pnl']:.2f}` 恶化到 raw net-EV `${full['net_ev_linear_5_15']['worst_date_pnl']:.2f}`、Kelly `${full['kelly_linear_5_15']['worst_date_pnl']:.2f}`。

局限：本轮用 frozen 5-share fee-adjusted effective cost 作逐股成本近似。136 行中 114 行的 top ask 本身覆盖 15 股；其余 22 行需要完整 15-share ladder 才能做严格容量结论。因此这足以否定“现有 net-EV 单变量立即升 live”，不用于批准 15 股实际下单。
""")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
