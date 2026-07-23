#!/usr/bin/env python3
"""Continuous current-YES carry residual and entry-timing study.

This follows the 2026-07-22 lineage audit with a mechanism-first ablation.  It
does not create a live selector.  Every probability model is market-anchored,
trained expanding-window on strictly earlier target dates, and evaluated on a
single first city-day taker expression after fees.
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
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_mechanism_timing_audit_v1 as audit,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_yes_carry_residual_entry_v2"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-residual-entry-v2.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-residual-entry-v2.md"
FORWARD_LEDGER = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_transition_aware_carry_shadow_v1/state_decisions.jsonl"
)

CARRY_MARKET_MID_FLOOR = 0.80

FEATURE_SETS = {
    "market_cal": ["market_logit"],
    "market_plus_clock": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
    ],
    "core": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "obs_age_min",
    ],
    # Train/serve audit: age is a collector-timing variable in the historical
    # archive and currently has a different live distribution.  Keep this
    # ablation next to core rather than silently treating freshness as alpha.
    "core_no_obs_age": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "wind_speed_kt",
    ],
    "core_no_clock": [
        "market_logit",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "obs_age_min",
    ],
    "core_no_wind": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "obs_age_min",
    ],
    "no_path": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "relative_humidity_pct",
        "sky_cover_code",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "obs_age_min",
    ],
    "path_added": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "relative_humidity_pct",
        "sky_cover_code",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "obs_age_min",
        "decline_steps",
        "minutes_since_running_max_log",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
    ],
    "cc_added": [
        "market_logit",
        "decision_hour_local",
        "forecast_peak_delta_hours_local",
        "relative_humidity_pct",
        "sky_cover_code",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "obs_age_min",
        "decline_steps",
        "minutes_since_running_max_log",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "gap_debiased_steps",
        "bias_known_num",
        "path_faded_num",
        "route_num",
    ],
}


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def prepare_universe() -> pd.DataFrame:
    frame = audit.load().copy()
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    frame["market_mid"] = (frame["current_yes_bid"] + frame["current_yes_ask"]) / 2.0
    clipped = frame["market_mid"].clip(1e-5, 1 - 1e-5)
    frame["market_logit"] = np.log(clipped / (1 - clipped))
    frame["gap_debiased_steps"] = np.where(
        frame["bias_known"], frame["gap_debiased"] / frame["_step"], np.nan
    )
    frame["decline_steps"] = frame["decline_native"] / frame["_step"]
    frame["minutes_since_running_max_log"] = np.log1p(
        frame["minutes_since_running_max"].clip(lower=0, upper=720)
    )
    frame["bias_known_num"] = frame["bias_known"].astype(int)
    frame["path_faded_num"] = frame["path_faded"].astype(int)
    frame["cc_route_valid"] = frame["route"] & frame["bias_known"]
    frame["route_num"] = frame["cc_route_valid"].astype(int)
    return frame.reset_index(drop=True)


def expanding_predictions(universe: pd.DataFrame) -> pd.DataFrame:
    base = audit.expanding_oof(universe, FEATURE_SETS)
    keep = [
        "city",
        "target_date",
        "decision_snapshot_dt",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "current_yes_bid",
        "current_yes_ask",
        "current_yes_ask_size",
        "taker_cost",
        "orderbook_file",
        "front",
    ]
    context = universe[keep].drop_duplicates(
        ["city", "target_date", "decision_snapshot_dt"], keep="first"
    )
    merged = base.merge(
        context,
        on=["city", "target_date", "decision_snapshot_dt"],
        how="left",
        validate="one_to_one",
    )
    return merged.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)


def choose_first_positive_ev(
    carry: pd.DataFrame, probability_column: str
) -> pd.DataFrame:
    selected = carry[carry[probability_column].gt(carry["taker_cost"])].copy()
    selected["model_probability"] = selected[probability_column]
    selected["model_edge_after_fee"] = selected[probability_column] - selected["taker_cost"]
    return audit.first_city_day(selected)


def robustness(entries: pd.DataFrame) -> dict[str, Any]:
    by_date = []
    for block in sorted(entries["target_date"].unique()):
        sample = entries[entries["target_date"].ne(block)]
        by_date.append(audit.roi(sample))
    by_city = []
    for block in sorted(entries["city"].unique()):
        sample = entries[entries["city"].ne(block)]
        by_city.append(audit.roi(sample))
    dates = sorted(entries["target_date"].unique())
    split = max(1, len(dates) // 2)
    front_dates, back_dates = set(dates[:split]), set(dates[split:])
    daily = entries.assign(
        pnl_per_share=entries["label"] - entries["taker_cost"]
    ).groupby("target_date", as_index=False).agg(
        signals=("city", "size"), pnl_per_share=("pnl_per_share", "sum")
    )
    return {
        "leave_one_date_out_roi_min": min(by_date) if by_date else None,
        "leave_one_date_out_roi_max": max(by_date) if by_date else None,
        "leave_one_city_out_roi_min": min(by_city) if by_city else None,
        "leave_one_city_out_roi_max": max(by_city) if by_city else None,
        "front_roi": audit.roi(entries[entries["target_date"].isin(front_dates)]),
        "back_roi": audit.roi(entries[entries["target_date"].isin(back_dates)]),
        "covered_oof_dates": len(dates),
        "active_dates": int(len(daily)),
        "signals_per_covered_date": float(len(entries) / len(dates)) if dates else None,
        "signals_per_active_date": float(daily["signals"].mean()) if len(daily) else None,
        "signals_p90_active_date": float(daily["signals"].quantile(0.90)) if len(daily) else None,
        "signals_max_active_date": int(daily["signals"].max()) if len(daily) else None,
        "worst_date": str(daily.loc[daily["pnl_per_share"].idxmin(), "target_date"])
        if len(daily)
        else None,
        "worst_date_pnl_per_share": float(daily["pnl_per_share"].min()) if len(daily) else None,
    }


def entry_result(entries: pd.DataFrame, model: str) -> dict[str, Any]:
    metrics = audit.selector_metrics(entries, model)
    metrics.update(
        {
            "avg_market_mid": float(entries["market_mid"].mean()) if len(entries) else None,
            "avg_model_probability": float(entries["model_probability"].mean())
            if len(entries)
            else None,
            "avg_edge_after_fee": float(entries["model_edge_after_fee"].mean())
            if len(entries)
            else None,
            "robustness": robustness(entries),
        }
    )
    return metrics


def two_consecutive_entries(carry: pd.DataFrame, probability_column: str) -> pd.DataFrame:
    rows: list[pd.Series] = []
    for _, group in carry.groupby(["city", "target_date"]):
        ordered = group.sort_values("decision_snapshot_dt").copy()
        positive = ordered[probability_column].gt(ordered["taker_cost"]).to_numpy(bool)
        same_bracket = ordered["current_bracket"].astype(str).eq(
            ordered["current_bracket"].astype(str).shift(1)
        ).to_numpy(bool)
        positions = np.flatnonzero(positive & np.r_[False, positive[:-1]] & same_bracket)
        if len(positions):
            row = ordered.iloc[int(positions[0])].copy()
            row["model_probability"] = row[probability_column]
            row["model_edge_after_fee"] = row[probability_column] - row["taker_cost"]
            rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else carry.iloc[0:0].copy()


def next_retained_hour(
    all_oof: pd.DataFrame, immediate: pd.DataFrame, probability_column: str
) -> pd.DataFrame:
    rows: list[pd.Series] = []
    grouped = {
        key: group.sort_values("decision_snapshot_dt")
        for key, group in all_oof.groupby(["city", "target_date"])
    }
    for _, entry in immediate.iterrows():
        group = grouped[(entry["city"], entry["target_date"])]
        later = group[
            group["decision_snapshot_dt"].gt(entry["decision_snapshot_dt"])
            & group["current_bracket"].astype(str).eq(str(entry["current_bracket"]))
        ]
        if len(later):
            row = later.iloc[0].copy()
            row["model_probability"] = row[probability_column]
            row["model_edge_after_fee"] = row[probability_column] - row["taker_cost"]
            rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else all_oof.iloc[0:0].copy()


def score_table(carry: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for name in FEATURE_SETS:
        p_col = f"p_{name}"
        row = {"model": name, **audit.score_loss(carry, p_col)}
        if name != "market_cal":
            row["vs_market_cal"] = audit.loss_delta_ci(carry, p_col, "p_market_cal")
        output.append(row)
    return output


def model_entry_table(carry: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    metrics = []
    rows = {}
    for name in FEATURE_SETS:
        entries = choose_first_positive_ev(carry, f"p_{name}")
        metrics.append(entry_result(entries, name))
        rows[name] = entries
    return metrics, rows


def forward_coverage() -> dict[str, Any]:
    if not FORWARD_LEDGER.exists():
        return {"ledger_exists": False, "path": str(FORWARD_LEDGER.relative_to(ROOT))}
    records = []
    with FORWARD_LEDGER.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    window = [row for row in records if row.get("in_research_window")]
    fields = [
        "heating_exhaustion_index_v2",
        "forecast_reheat_after_now_f",
        "solar_elevation_deg",
        "temperature_transition_risk_score",
        "regime_predictability_score",
        "taf_issue_time_utc",
        "minutes_since_last_strict_new_high",
        "current_yes_ask",
    ]
    return {
        "ledger_exists": True,
        "path": str(FORWARD_LEDGER.relative_to(ROOT)),
        "all_raw_state_rows": len(records),
        "research_window_rows": len(window),
        "research_window_dates": sorted({str(row.get("target_date")) for row in window}),
        "unique_city_snapshot_rows": len(
            {
                (row.get("city"), row.get("target_date"), row.get("decision_snapshot_ts_utc"))
                for row in window
            }
        ),
        "non_null": {
            field: sum(row.get(field) is not None for row in window) for field in fields
        },
        "direct_quote_pair_true": sum(bool(row.get("direct_quote_pair_available")) for row in window),
        "current_yes_book_status": {
            status: sum(str(row.get("current_yes_book_status")) == status for row in window)
            for status in sorted({str(row.get("current_yes_book_status")) for row in window})
        },
    }


def fmt_pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value) * 100:+.2f}%"


def fmt_num(value: Any, digits: int = 4) -> str:
    return "NA" if value is None else f"{float(value):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    scores = {row["model"]: row for row in payload["proper_scores"]}
    entries = {row["selector"]: row for row in payload["entry_results"]}
    core = entries["core"]
    core_delta = scores["core"]["vs_market_cal"]
    timing = payload["core_entry_timing"]
    live = payload["forward_coverage"]
    lines = [
        "# Current-YES Carry 连续 residual / 入场时机研究 v2",
        "",
        "Status: `exploratory_frozen_forward_challenger / taker-only / no-live-change`",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "## 真正的新结论",
        "",
        "这轮不是再拼一个天气条件漏斗，而是在相同 PIT states 上让 market 做基准，再逐组加入物理机制。"
        "结果是一个可继续 forward 的连续候选，而不是 CC/H1 的硬合并：",
        "",
        "- 候选 `core`：`market midpoint + local/forecast peak clock + dewpoint depression + wind speed + observation age`。"
        "价格只通过概率基准和 taker cost 进入，不设 `ask>=0.95`，也不设 support count。",
        "- 旧 `minutes_since_running_max`/decline/trend path 组加入后变差。根因不是“回落无用”，而是历史时钟会在"
        "相同高点再次出现时重置，不能表达 strict new high age；在语义修正前不能拿它当确认机制。",
        "- CC 的 debiased ceiling/route 再加入也没有改善，因此不合并进 challenger。",
        "- 最好的历史执行时机是首次 `OOF p_hold > fresh taker effective cost`；二次确认和等下一小时都更贵，"
        "没有提升结果。",
        "",
        "## Headline（探索性，不是 live 结论）",
        "",
        f"`core` 在 carry expression domain（market midpoint≥{CARRY_MARKET_MID_FLOOR:.2f}）首个正 EV："
        f"{core['city_days']} city-days / {core['dates']} dates / {core['cities']} cities，"
        f"win {fmt_pct(core['win_rate'])}，avg ask {fmt_num(core['avg_ask'],3)}，"
        f"fee-adjusted taker ROI {fmt_pct(core['fee_adjusted_taker_roi'])}，"
        f"date-block 95% CI [{fmt_pct(core['target_date_block_ci95'][0])}, "
        f"{fmt_pct(core['target_date_block_ci95'][1])}]。",
        f"前/后半目标日 ROI：{fmt_pct(core['robustness']['front_roi'])} / "
        f"{fmt_pct(core['robustness']['back_roi'])}；leave-one-date-out "
        f"[{fmt_pct(core['robustness']['leave_one_date_out_roi_min'])}, "
        f"{fmt_pct(core['robustness']['leave_one_date_out_roi_max'])}]。",
        f"Proper score 相对同训练过程 market calibration：Brier Δ "
        f"{fmt_num(core_delta['candidate_minus_baseline_brier'],6)} "
        f"CI[{fmt_num(core_delta['brier_delta_ci95'][0],6)}, {fmt_num(core_delta['brier_delta_ci95'][1],6)}]；"
        f"logloss Δ {fmt_num(core_delta['candidate_minus_baseline_logloss'],6)} "
        f"CI[{fmt_num(core_delta['logloss_delta_ci95'][0],6)}, {fmt_num(core_delta['logloss_delta_ci95'][1],6)}]。"
        "负值代表改善，但 CI 尚未在两项都完全小于 0。",
        "",
        "## 机制消融",
        "",
        "| model | features added after market | Brier | logloss | ΔBrier vs market-cal | Δlogloss vs market-cal | first-EV n | avg ask | taker ROI [95%CI] |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in FEATURE_SETS:
        score = scores[name]
        entry = entries[name]
        delta = score.get("vs_market_cal", {})
        feature_text = ", ".join(FEATURE_SETS[name][1:]) or "calibration only"
        ci = entry["target_date_block_ci95"]
        lines.append(
            f"| {name} | {feature_text} | {fmt_num(score['brier'])} | {fmt_num(score['logloss'])} | "
            f"{fmt_num(delta.get('candidate_minus_baseline_brier'),6) if delta else '—'} | "
            f"{fmt_num(delta.get('candidate_minus_baseline_logloss'),6) if delta else '—'} | "
            f"{entry['city_days']} | {fmt_num(entry['avg_ask'],3)} | "
            f"{fmt_pct(entry['fee_adjusted_taker_roi'])} "
            f"[{fmt_pct(ci[0])}, {fmt_pct(ci[1])}] |"
        )
    lines += [
        "",
        "单一机制组并没有各自稳定赚钱；`core` 的价值来自 market 条件下的连续交互，不能翻译成"
        "clock AND wind AND dewpoint 三道门。`core`/`no_path` 是查看消融后选出的 exploratory candidates，"
        "因此即使交易 ROI CI 为正，也必须先冻结再 forward，不能把 post-selection bootstrap 当独立显著性。",
        "",
        "## 入场时机（core，同 city-day / 同 bracket 配对）",
        "",
        "| policy | city-days | avg ask | ROI | paired n | paired ask Δ | paired ROI Δ vs first |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in timing["policies"]:
        pair = row.get("paired_vs_first", {})
        lines.append(
            f"| {row['selector']} | {row['city_days']} | {fmt_num(row['avg_ask'],3)} | "
            f"{fmt_pct(row['fee_adjusted_taker_roi'])} | {pair.get('paired_city_days', '—')} | "
            f"{fmt_num(pair.get('avg_ask_delta_delayed_minus_immediate'),4)} | "
            f"{fmt_pct(pair.get('delayed_minus_immediate_roi'))} |"
        )
    lines += [
        "",
        "这回答的是入场方向，不是分钟级最优点：历史 archive 每城每小时约一帧。当前可执行定义先冻结为"
        "“首个正 taker EV checkpoint”，不等待赔率升到 0.95，也不等待第二份确认。",
        "",
        "## 真实 taker 容量",
        "",
        "| qty | raw book match | full executable | avg VWAP | effective cost/share | slippage vs best ask | VWAP ROI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for quantity, row in payload["core_taker_vwap"].items():
        lines.append(
            f"| {quantity} | {fmt_pct(row['raw_book_match_rate'])} | "
            f"{fmt_pct(row['full_quantity_executable_rate'])} | {fmt_num(row['avg_principal_vwap'],4)} | "
            f"{fmt_num(row['avg_effective_cost_per_share'],4)} | "
            f"{fmt_num(row['avg_slippage_cents_per_share_vs_best_ask'],3)}c | "
            f"{fmt_pct(row['fee_adjusted_vwap_roi'])} |"
        )
    lines += [
        "",
        "Maker 暂不计收益：这版先用真实 ask ladder 把 taker 分母钉死，maker 只保留后续同 signal 反事实空间。",
        "",
        "## 为什么现在还不能改 live",
        "",
        f"新的 transition-aware forward ledger 研究窗口只有 {len(live.get('research_window_dates', []))} 个 target date "
        f"({', '.join(live.get('research_window_dates', [])) or 'none'})、{live.get('research_window_rows', 0)} 个 state。"
        f"其中 direct current-YES ask {live.get('non_null', {}).get('current_yes_ask', 0)}/"
        f"{live.get('research_window_rows', 0)}，strict-new-high clock "
        f"{live.get('non_null', {}).get('minutes_since_last_strict_new_high', 0)}/"
        f"{live.get('research_window_rows', 0)}。这是 evidence coverage gap，不是 signal 被策略筛掉。",
        "",
        "因此当前动作是：冻结 `core` challenger，zero-notional 连续采集完整 direct quote + strict-high clock + settlement；"
        "旧 H1/H2 live 不因本报告自动改变。待独立 forward 后同时复核 proper score、taker ROI、入场 timing 和"
        "archive/live parity，再决定是否替换。",
        "",
        "## 产物",
        "",
        f"- Script: `{payload['outputs']['script']}`",
        f"- JSON: `{payload['outputs']['json']}`",
        f"- OOF states: `{payload['outputs']['oof_states']}`",
        f"- Core entries: `{payload['outputs']['core_entries']}`",
        f"- Core taker ladder: `{payload['outputs']['core_taker_ladder']}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    universe = prepare_universe()
    oof = expanding_predictions(universe)
    carry = oof[oof["market_mid"].ge(CARRY_MARKET_MID_FLOOR)].copy()
    proper_scores = score_table(carry)
    entry_results, entry_rows = model_entry_table(carry)

    core_first = entry_rows["core"]
    core_confirm = two_consecutive_entries(carry, "p_core")
    core_next = next_retained_hour(oof, core_first, "p_core")
    timing_frames = {
        "first_positive_taker_ev": core_first,
        "two_consecutive_positive_ev": core_confirm,
        "fixed_next_retained_hour_same_bracket": core_next,
    }
    timing_metrics = []
    for name, frame in timing_frames.items():
        row = audit.selector_metrics(frame, name)
        if name != "first_positive_taker_ev":
            row["paired_vs_first"] = audit.paired_roi_delta(core_first, frame)
        timing_metrics.append(row)

    taker_vwap, taker_rows = audit.taker_vwap_audit(core_first)
    gate = audit.run_clob_fill_coverage_gate()

    oof.to_csv(OUT_DIR / "oof_state_predictions.csv", index=False)
    for name, frame in entry_rows.items():
        frame.to_csv(OUT_DIR / f"entries_{name}.csv", index=False)
    for name, frame in timing_frames.items():
        frame.to_csv(OUT_DIR / f"timing_{name}.csv", index=False)
    taker_rows.to_csv(OUT_DIR / "core_taker_ladder.csv", index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "exploratory_frozen_forward_challenger_no_live_change",
        "data_snapshot": {
            "rows": int(len(universe)),
            "city_days": int(universe.groupby(["city", "target_date"]).ngroups),
            "dates": int(universe["target_date"].nunique()),
            "date_min": str(universe["target_date"].min()),
            "date_max": str(universe["target_date"].max()),
            "oof_rows": int(len(oof)),
            "oof_dates": int(oof["target_date"].nunique()),
            "carry_rows_market_mid_ge_0p80": int(len(carry)),
            "carry_city_days": int(carry.groupby(["city", "target_date"]).ngroups),
            "forecast_lineage": "per-city fixed CITY_MODEL dual Single Runs PIT columns",
            "model_validation": "expanding target-date OOF; strictly earlier dates; each training city-day one total weight",
            "clob_fill_coverage_gate_pass": bool(gate.get("gate_pass")),
        },
        "target_metric": {
            "label": "current exact bracket remains final winning bracket",
            "expression_domain": f"market_mid >= {CARRY_MARKET_MID_FLOOR}",
            "entry": "first city-day checkpoint where OOF p_hold exceeds official-fee-adjusted direct ask",
            "execution": "taker; archived full ask ladder at decision timestamp",
        },
        "feature_sets": FEATURE_SETS,
        "proper_scores": proper_scores,
        "entry_results": entry_results,
        "core_entry_timing": {
            "policies": timing_metrics,
            "archive_cadence_limit": "one retained state per city-target_date-local-hour; not minute-level",
        },
        "core_taker_vwap": taker_vwap,
        "forward_coverage": forward_coverage(),
        "interpretation": {
            "new_candidate": "core",
            "candidate_features": FEATURE_SETS["core"],
            "path_group": "reject from challenger until strict-new-high clock semantics replace equal-high-reset minutes_since_running_max",
            "cc_group": "do not merge; no incremental historical benefit in the continuous residual ablation",
            "entry_timing": "first positive taker EV; waiting for a second confirmation or next retained hour is not supported",
            "maker": "deferred; no maker alpha credited",
            "multiplicity": "core emerged after exploratory ablation; bootstrap is descriptive post-selection evidence, not an independent promotion test",
        },
        "verdict": {
            "historical_execution": "PASS_POINT_ESTIMATE_AND_DATE_BLOCK_CI",
            "proper_score": "PARTIAL_POINT_IMPROVEMENT_BUT_CI_NOT_BOTH_STRICTLY_BELOW_ZERO",
            "independent_forward": "FAIL_ONLY_ONE_TARGET_DATE_WITH_NEW_TRANSITION_TELEMETRY",
            "production_parity": "FAIL_INCOMPLETE_DIRECT_QUOTE_AND_STRICT_HIGH_COVERAGE",
            "live_action": "none",
            "next_action": "freeze core as zero-notional challenger; complete quote/strict-high/settlement lineage and collect independent forward",
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
            "oof_states": str((OUT_DIR / "oof_state_predictions.csv").relative_to(ROOT)),
            "core_entries": str((OUT_DIR / "entries_core.csv").relative_to(ROOT)),
            "core_taker_ladder": str((OUT_DIR / "core_taker_ladder.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(payload)
    print(
        json.dumps(
            json_ready(
                {
                    "data_snapshot": payload["data_snapshot"],
                    "entry_results": entry_results,
                    "core_entry_timing": payload["core_entry_timing"],
                    "core_taker_vwap": taker_vwap,
                    "forward_coverage": payload["forward_coverage"],
                    "verdict": payload["verdict"],
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
