#!/usr/bin/env python3
"""Freeze the no-observation-age Current-YES residual carry candidate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (
    research_current_yes_carry_residual_entry_v2 as v2,
)
from scripts.analysis.reheat_risk import (
    research_current_yes_core_carry_freeze_pre_live_v4 as base,
)


base.CORE = list(v2.FEATURE_SETS["core_no_obs_age"])
base.FEATURE_SET_NAME = "core_no_obs_age"
base.PROBABILITY_COLUMN = "p_core_no_obs_age"
base.OUT_DIR = (
    base.ROOT
    / "docs/analysis/2026-07/generated/current_yes_core_carry_no_obs_age_freeze_pre_live_v5"
)
base.OUT_JSON = (
    base.ROOT
    / "docs/analysis/2026-07/2026-07-24-current-yes-core-carry-no-obs-age-freeze-pre-live-v5.json"
)
base.OUT_MD = (
    base.ROOT
    / "docs/analysis/2026-07/2026-07-24-current-yes-core-carry-no-obs-age-freeze-pre-live-v5.md"
)
base.ARTIFACT_PATH = (
    base.ROOT
    / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2.json"
)
base.ARTIFACT_FROZEN_AT_UTC = "2026-07-23T16:20:00+00:00"
base.ARTIFACT_VERSION = "current_yes_core_carry_model_v2"
base.ARTIFACT_LIFECYCLE = "pre_live_frozen_zero_notional_only"
base.ENTRY_POLICY_EXTRAS = {
    "observation_age_probability_feature": False,
    "observation_freshness_policy": (
        "source age/cadence is execution-data validity evidence; it is not an alpha feature"
    ),
}
base.REPORT_TITLE = "Current-YES Residual Carry v2（no obs-age）冻结与 pre-live 准备"
base.REPORT_STATUS = "frozen / zero-notional pre-live / no real-live activation"
base.POLICY_LABEL = "v2"
base.OBSERVATION_AGE_NOTE = (
    "`obs_age_min` 已从概率模型移除；source age 与 expected cadence 只作 PIT 数据有效性证据，"
    "不再让 collector cadence 改变 p_hold。"
)


def _entry_keys(frame: pd.DataFrame, columns: list[str]) -> set[tuple[str, ...]]:
    return set(map(tuple, frame[columns].astype(str).to_numpy()))


def main() -> int:
    result = base.main()
    v1_payload = json.loads(
        (
            base.ROOT
            / "docs/analysis/2026-07/2026-07-23-current-yes-core-carry-freeze-pre-live-v4.json"
        ).read_text(encoding="utf-8")
    )
    payload = json.loads(base.OUT_JSON.read_text(encoding="utf-8"))
    v1_entries = pd.read_csv(
        base.ROOT
        / "docs/analysis/2026-07/generated/current_yes_core_carry_freeze_pre_live_v4/"
        "frozen_policy_entries.csv"
    )
    v2_entries = pd.read_csv(base.OUT_DIR / "frozen_policy_entries.csv")
    city_day = ["city", "target_date"]
    exact_entry = [*city_day, "decision_snapshot_ts_utc", "current_bracket"]
    v1_city_days = _entry_keys(v1_entries, city_day)
    v2_city_days = _entry_keys(v2_entries, city_day)
    v1_exact = _entry_keys(v1_entries, exact_entry)
    v2_exact = _entry_keys(v2_entries, exact_entry)
    oof = pd.read_csv(
        base.ROOT
        / "docs/analysis/2026-07/generated/current_yes_carry_residual_entry_v2/"
        "oof_state_predictions.csv"
    )
    carry = oof[oof["market_mid"].ge(0.80)].copy()
    probability_delta = v2.audit.loss_delta_ci(
        carry,
        "p_core_no_obs_age",
        "p_core",
    )
    v1_metric = v1_payload["frozen_policy_result"]
    v2_metric = payload["frozen_policy_result"]
    comparison = {
        "grain": {
            "probability": "same 1,350 carry PIT states / 773 city-days / 31 target dates",
            "trade": "first positive-EV city-day with full five-share ask-ladder and official fee",
        },
        "probability_v2_minus_v1": probability_delta,
        "trade_metrics": {
            "v1": v1_metric,
            "v2_no_obs_age": v2_metric,
            "roi_delta_v2_minus_v1": v2_metric["roi"] - v1_metric["roi"],
            "win_rate_delta_v2_minus_v1": v2_metric["win_rate"] - v1_metric["win_rate"],
            "signals_per_covered_date_delta": (
                v2_metric["signals_per_covered_date"]
                - v1_metric["signals_per_covered_date"]
            ),
        },
        "selection_overlap": {
            "shared_city_days": len(v1_city_days & v2_city_days),
            "v1_only_city_days": len(v1_city_days - v2_city_days),
            "v2_only_city_days": len(v2_city_days - v1_city_days),
            "shared_exact_entry_rows": len(v1_exact & v2_exact),
            "v1_only_exact_entry_rows": len(v1_exact - v2_exact),
            "v2_only_exact_entry_rows": len(v2_exact - v1_exact),
        },
        "frozen_version_decision": {
            "future_pre_live_default": "current_yes_core_carry_model_v2",
            "v1_status": "superseded_for_now_retained_for_historical_lineage",
            "v2_status": "frozen_zero_notional_pre_live",
            "real_live_action": "none",
            "reason": (
                "v2 removes a known train/serve cadence skew without material probability-score "
                "degradation and retains the full-ladder historical result"
            ),
        },
    }
    payload["v1_vs_v2"] = comparison
    base.OUT_JSON.write_text(
        json.dumps(base.json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = base.OUT_MD.read_text(encoding="utf-8")
    brier = probability_delta["candidate_minus_baseline_brier"]
    brier_ci = probability_delta["brier_delta_ci95"]
    logloss = probability_delta["candidate_minus_baseline_logloss"]
    logloss_ci = probability_delta["logloss_delta_ci95"]
    report += (
        "\n## 与 v1 的同分母结论\n\n"
        f"- 概率层（v2−v1）：Brier Δ `{brier:+.6f}`，95% CI "
        f"`[{brier_ci[0]:+.6f}, {brier_ci[1]:+.6f}]`；logloss Δ "
        f"`{logloss:+.6f}`，95% CI `[{logloss_ci[0]:+.6f}, {logloss_ci[1]:+.6f}]`。"
        "两项 CI 都跨 0，去掉 age 没有可辨别的概率质量损失。\n"
        f"- 5-share 全 ladder：v1 `{v1_metric['city_days']}` 笔、胜率 "
        f"`{v1_metric['win_rate']:.2%}`、ROI `{v1_metric['roi']:+.2%}`；v2 "
        f"`{v2_metric['city_days']}` 笔、胜率 `{v2_metric['win_rate']:.2%}`、"
        f"ROI `{v2_metric['roi']:+.2%}`。\n"
        f"- city-day overlap：共同 `{len(v1_city_days & v2_city_days)}`，"
        f"仅 v1 `{len(v1_city_days - v2_city_days)}`，仅 v2 "
        f"`{len(v2_city_days - v1_city_days)}`。变化集中在临界 EV，而不是策略主体翻转。\n"
        "- 冻结决定：未来 pre-live 默认切到 `current_yes_core_carry_model_v2`；"
        "v1 标为 superseded-for-now 并保留历史血缘。v2 仍是 zero-notional pre-live，"
        "本报告不启动真实订单。\n"
    )
    base.OUT_MD.write_text(report, encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
