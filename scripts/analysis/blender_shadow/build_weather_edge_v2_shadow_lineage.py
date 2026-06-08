"""
build_weather_edge_v2_shadow_lineage.py

Build per city-day shadow lineage for weather_edge_v2 research rules.

This is offline analysis only. It reads runtime/weather.db fact tables and
writes committed research artifacts under docs/analysis/YYYY-MM/.

No N100/live order behavior is changed.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.compare_city_day_basket_vs_legacy_baselines import (  # noqa: E402
    PROFILES,
    _decide_legacy_profile,
    _entry_band_universe,
    _legacy_trigger_universe,
    _load_rows,
)
from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    Leg,
    _attribution,
    _summarize,
)
from scripts.analysis.research_city_day_basket_optimizer import (  # noqa: E402
    ComboLeg,
    _candidate_legs_for_group,
    _cvar20,
    _decide_combo_optimizer,
    _expected_value,
    _leave_best_out_ev,
    _payoff_by_temp,
    _probability_distribution,
)
from scripts.analysis.research_weather_edge_v2_filtered_operational_base import (  # noqa: E402
    MAX_DECISION_HOURS_TO_SETTLE,
    REMOVED_CITIES,
    _actual_live_operational_base,
    _apply_operational_base,
    _decide_custom_basket_variant,
    _fmt_money,
    _fmt_roi,
    _run_clob_gate,
    _self_checks,
)

REPORT_STEM = "weather-edge-v2-shadow-lineage"

RULE_IDS = (
    "legacy_side_band_raw",
    "combo_market_risk_entry",
    "combo_market_tail_entry",
    "robust_single_best_entry",
    "tail_balanced_combo_entry",
    "no_hit_guard_combo_entry",
    "compact_diversified_combo_entry",
    "live_trigger_tail_combo",
    "live_trigger_no_hit_guard_combo",
)

RULE_META: dict[str, dict[str, str]] = {
    "legacy_side_band_raw": {
        "distribution_source": "raw_side_band",
        "universe": "legacy_side_band_entry",
    },
    "combo_market_risk_entry": {
        "distribution_source": "market_norm",
        "universe": "legacy_side_band_entry",
    },
    "combo_market_tail_entry": {
        "distribution_source": "market_norm_leave_best_out",
        "universe": "legacy_side_band_entry",
    },
    "robust_single_best_entry": {
        "distribution_source": "market_norm_single_leg_tail_floor",
        "universe": "legacy_side_band_entry",
    },
    "tail_balanced_combo_entry": {
        "distribution_source": "market_norm_leave_best_out_cvar20",
        "universe": "legacy_side_band_entry",
    },
    "no_hit_guard_combo_entry": {
        "distribution_source": "market_norm_no_hit_guard",
        "universe": "legacy_side_band_entry",
    },
    "compact_diversified_combo_entry": {
        "distribution_source": "market_norm_compact_diversified",
        "universe": "legacy_side_band_entry",
    },
    "live_trigger_tail_combo": {
        "distribution_source": "market_norm_leave_best_out_cvar20",
        "universe": "legacy_raw_trigger",
    },
    "live_trigger_no_hit_guard_combo": {
        "distribution_source": "market_norm_no_hit_guard",
        "universe": "legacy_raw_trigger",
    },
}


def _side_profile():
    return next(p for p in PROFILES if p.profile_id == "legacy_side_band")


def _leg_key(leg: Leg | ComboLeg) -> tuple[str, str, str, str]:
    return (str(leg.city), str(leg.target_date), str(leg.bracket), str(leg.side))


def _side_edge(side: str, p_yes: float, entry_price: float) -> float:
    return p_yes - entry_price if side == "BUY_YES" else (1.0 - p_yes) - entry_price


def _leg_dict(leg: Leg) -> dict[str, Any]:
    return {
        "city": leg.city,
        "target_date": leg.target_date,
        "bracket": leg.bracket,
        "side": leg.side,
        "entry_price": leg.entry_price,
        "notional": leg.notional,
        "p_yes_raw": leg.p_yes_raw,
        "p_yes_used": leg.p_yes_used,
        "edge": _side_edge(leg.side, leg.p_yes_used, leg.entry_price),
        "final_yes": leg.final_yes,
        "actual_pnl": leg.pnl,
        "is_win": leg.is_win,
    }


def _candidate_dict(leg: ComboLeg) -> dict[str, Any]:
    return {
        "city": leg.city,
        "target_date": leg.target_date,
        "bracket": leg.bracket,
        "side": leg.side,
        "entry_price": leg.entry_price,
        "notional": leg.notional,
        "p_yes_raw": leg.p_yes_raw,
        "p_yes_used": leg.p_yes_used,
        "edge": leg.edge,
    }


def _market_distribution(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {}
    by_bracket: dict[str, float] = {}
    for row in df.itertuples():
        by_bracket[str(row.bracket)] = float(row.market_yes_price)
    return _probability_distribution(by_bracket)


def _candidate_legs(df: pd.DataFrame) -> list[ComboLeg]:
    if df.empty:
        return []
    candidates, _prob_inputs = _candidate_legs_for_group(
        df,
        edge_threshold=0.03,
        small_notional=3.0,
        normal_notional=8.0,
        normal_threshold=0.06,
        max_win_multiple=10.0,
    )
    return candidates


def _portfolio_metrics(legs: list[Leg], probs_market: dict[str, float]) -> dict[str, Any]:
    actual_pnl = sum(leg.pnl for leg in legs)
    cost = sum(leg.notional for leg in legs)
    if not legs or not probs_market:
        return {
            "cost_usd": cost,
            "actual_pnl_usd": actual_pnl,
            "actual_roi": actual_pnl / cost if cost else 0.0,
            "expected_value": 0.0,
            "cvar20": 0.0,
            "leave_best_out_ev": 0.0,
            "worst_case": 0.0,
            "best_case": 0.0,
            "market_top_bracket": None,
        }

    combo_like = tuple(
        ComboLeg(
            city=leg.city,
            target_date=leg.target_date,
            bracket=leg.bracket,
            side=leg.side,
            entry_price=leg.entry_price,
            notional=leg.notional,
            final_yes=leg.final_yes,
            p_yes_raw=leg.p_yes_raw,
            p_yes_used=leg.p_yes_used,
            edge=_side_edge(leg.side, leg.p_yes_used, leg.entry_price),
        )
        for leg in legs
    )
    payoff = _payoff_by_temp(combo_like, list(probs_market))
    return {
        "cost_usd": cost,
        "actual_pnl_usd": actual_pnl,
        "actual_roi": actual_pnl / cost if cost else 0.0,
        "expected_value": _expected_value(payoff, probs_market),
        "cvar20": _cvar20(payoff, probs_market),
        "leave_best_out_ev": _leave_best_out_ev(payoff, probs_market),
        "worst_case": min(payoff.values()) if payoff else 0.0,
        "best_case": max(payoff.values()) if payoff else 0.0,
        "market_top_bracket": max(probs_market, key=probs_market.get) if probs_market else None,
    }


def _select_rule_legs(
    rule_id: str,
    group_df: pd.DataFrame,
    entry_universe: pd.DataFrame,
    legacy_trigger_universe: pd.DataFrame,
    legacy_legs: list[Leg],
) -> tuple[pd.DataFrame, list[Leg]]:
    if rule_id == "legacy_side_band_raw":
        return entry_universe, legacy_legs
    if rule_id == "combo_market_risk_entry":
        return entry_universe, _decide_combo_optimizer(entry_universe, mode="market_risk")
    if rule_id == "combo_market_tail_entry":
        return entry_universe, _decide_combo_optimizer(entry_universe, mode="market_tail")
    if rule_id == "robust_single_best_entry":
        return entry_universe, _decide_custom_basket_variant(entry_universe, "robust_single_best")
    if rule_id == "tail_balanced_combo_entry":
        return entry_universe, _decide_custom_basket_variant(entry_universe, "tail_balanced_combo")
    if rule_id == "no_hit_guard_combo_entry":
        return entry_universe, _decide_custom_basket_variant(entry_universe, "no_hit_guard_combo")
    if rule_id == "compact_diversified_combo_entry":
        return entry_universe, _decide_custom_basket_variant(
            entry_universe,
            "compact_diversified_combo",
        )
    if rule_id == "live_trigger_tail_combo":
        return legacy_trigger_universe, _decide_custom_basket_variant(
            legacy_trigger_universe,
            "tail_balanced_combo",
        )
    if rule_id == "live_trigger_no_hit_guard_combo":
        return legacy_trigger_universe, _decide_custom_basket_variant(
            legacy_trigger_universe,
            "no_hit_guard_combo",
        )
    raise ValueError(f"unknown rule_id: {rule_id}")


def _lineage_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    profile = _side_profile()
    for (city, event_date), group_df in df.groupby(["city", "event_date"]):
        group_df = group_df.reset_index(drop=True)
        entry_universe = _entry_band_universe(group_df, profile)
        legacy_legs = _decide_legacy_profile(group_df, profile, "raw")
        legacy_trigger_universe = _legacy_trigger_universe(group_df, legacy_legs)
        probs_market = _market_distribution(group_df)
        baseline_summary = asdict(_summarize("legacy_side_band_raw", legacy_legs))

        for rule_id in RULE_IDS:
            universe, selected = _select_rule_legs(
                rule_id,
                group_df,
                entry_universe,
                legacy_trigger_universe,
                legacy_legs,
            )
            candidates = _candidate_legs(universe)
            selected_keys = {_leg_key(leg) for leg in selected}
            rejected = [
                candidate
                for candidate in candidates
                if _leg_key(candidate) not in selected_keys
            ]
            summary = asdict(_summarize(rule_id, selected))
            attr = None if rule_id == "legacy_side_band_raw" else _attribution(selected, legacy_legs)
            records.append(
                {
                    "lineage_id": f"{city}:{event_date}:{rule_id}",
                    "city": str(city),
                    "target_date": str(event_date),
                    "rule_id": rule_id,
                    "operational_base": {
                        "pass": True,
                        "removed_cities": sorted(REMOVED_CITIES),
                        "max_decision_hours_to_settle": MAX_DECISION_HOURS_TO_SETTLE,
                    },
                    "distribution_source": RULE_META[rule_id]["distribution_source"],
                    "candidate_universe": RULE_META[rule_id]["universe"],
                    "market_distribution": probs_market,
                    "selected_legs": [_leg_dict(leg) for leg in selected],
                    "rejected_legs": [_candidate_dict(leg) for leg in rejected],
                    "selected_count": len(selected),
                    "rejected_count": len(rejected),
                    "metrics": _portfolio_metrics(selected, probs_market),
                    "summary": summary,
                    "baseline_summary": baseline_summary,
                    "vs_legacy_side_band_raw": attr,
                }
            )
    return records


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rule_id in RULE_IDS:
        selected: list[Leg] = []
        attr_totals = {
            "missed_profit_usd": 0.0,
            "avoided_loss_usd": 0.0,
            "shared_profit_usd": 0.0,
            "shared_loss_usd": 0.0,
            "net_basket_vs_baseline_usd": 0.0,
        }
        active_city_days = 0
        for record in records:
            if record["rule_id"] != rule_id:
                continue
            if record["selected_count"] > 0:
                active_city_days += 1
            for leg in record["selected_legs"]:
                selected.append(
                    Leg(
                        city=leg["city"],
                        target_date=leg["target_date"],
                        bracket=leg["bracket"],
                        side=leg["side"],
                        entry_price=float(leg["entry_price"]),
                        notional=float(leg["notional"]),
                        final_yes=float(leg["final_yes"]),
                        p_yes_raw=float(leg["p_yes_raw"]),
                        p_yes_used=float(leg["p_yes_used"]),
                    )
                )
            if record["vs_legacy_side_band_raw"]:
                for key in attr_totals:
                    attr_totals[key] += float(record["vs_legacy_side_band_raw"].get(key) or 0.0)
        out[rule_id] = {
            "active_city_days": active_city_days,
            "summary": asdict(_summarize(rule_id, selected)),
            "vs_legacy_side_band_raw": attr_totals if rule_id != "legacy_side_band_raw" else None,
        }
    return out


def _write_markdown(report: dict[str, Any], out_path: Path) -> None:
    gate = report["clob_gate"]
    live_recent = report["actual_live_operational_base"]["recent_from_2026_06_01"]
    lines = [
        "# Weather Edge V2 Shadow Lineage - 2026-06-08",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades` for live reference)",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- DB mtime BJ: `{report['self_checks']['db_mtime_bj']}`",
        f"- MAX fact_built_at_utc: `{report['self_checks']['fact_built_at'][0]['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; live_real fill_id DB `{gate.get('fact_trades_live_real', {}).get('fill_ids')}`, raw CLOB `{gate.get('db_fills', {}).get('distinct_fill_ids')}`",
        f"- operational base: exclude `{', '.join(sorted(REMOVED_CITIES))}`; `decision_hours_to_settle <= {MAX_DECISION_HOURS_TO_SETTLE:.0f}`",
        f"- lineage records: `{report['lineage_record_count']}`; city-days: `{report['city_day_count']}`",
        "",
        "## Target Metric",
        "",
        "`weather_edge_v2_shadow_lineage` = 对每个 operational-base city-day 输出每个候选 rule 的 selected/rejected legs、market distribution、EV、CVaR20、leave-best-out EV、worst-case payoff、actual settled PnL、以及相对 current-like `legacy_side_band_raw` 的 missed/avoided attribution。",
        "",
        "这不是 live 下单记录；它是用于 forward settled shadow 的 lineage artifact。",
        "",
        "## Current Live Reference",
        "",
        f"- recent live_real operational-base ROI: `{_fmt_roi(live_recent['roi'])}`; top5 ROI `{_fmt_roi(live_recent['roi_excl_top5'])}`; fills `{live_recent['fills']}`。",
        "",
        "## Rule Summary",
        "",
        "| rule | active city-days | n | pnl | ROI | top5 ROI | missed | avoided |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rule_id in RULE_IDS:
        block = report["aggregate"][rule_id]
        summary = block["summary"]
        attr = block["vs_legacy_side_band_raw"] or {}
        lines.append(
            f"| {rule_id} | {block['active_city_days']} | {summary['n_legs']} | "
            f"{_fmt_money(summary['total_pnl_usd'])} | {_fmt_roi(summary['roi'])} | "
            f"{_fmt_roi(summary['roi_excl_top5'])} | "
            f"{_fmt_money(attr.get('missed_profit_usd'))} | "
            f"{_fmt_money(attr.get('avoided_loss_usd'))} |"
        )
    lines.extend([
        "",
        "## Highest-Risk City-Day Records",
        "",
        "| city | date | rule | n | actual pnl | EV | CVaR20 | leave-best EV | worst |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    risky = sorted(
        [r for r in report["sample_records"] if r["selected_count"] > 0],
        key=lambda r: (r["metrics"]["cvar20"], r["metrics"]["worst_case"]),
    )[:20]
    for record in risky:
        metrics = record["metrics"]
        lines.append(
            f"| {record['city']} | {record['target_date']} | {record['rule_id']} | "
            f"{record['selected_count']} | {_fmt_money(metrics['actual_pnl_usd'])} | "
            f"{_fmt_money(metrics['expected_value'])} | {_fmt_money(metrics['cvar20'])} | "
            f"{_fmt_money(metrics['leave_best_out_ev'])} | {_fmt_money(metrics['worst_case'])} |"
        )
    lines.extend([
        "",
        "## 使用方式",
        "",
        "- 人看摘要: 本 Markdown。",
        f"- 程序/后续 forward settled: `{report['jsonl_path']}`。",
        "- 看板展示应挂在 Weather Research，而不是 Strategies/Live 绩效卡；当前仍是 shadow/offline evidence。",
        "",
        "## 结论",
        "",
        "- 可以开始记录 shadow lineage；不要 canary。",
        "- 下一步 forward settled 时，把新增 settled city-day append 到同一 lineage schema，再看 recent/holdout/top5-removed 是否同时改善。",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    db_path = DB_DEFAULT
    checks = _self_checks(db_path)
    clob_gate = _run_clob_gate(db_path)
    if not clob_gate.get("gate_pass"):
        raise SystemExit(f"CLOB gate failed; refusing to publish lineage: {clob_gate}")

    source_df = _load_rows(db_path)
    filtered_df = _apply_operational_base(source_df)
    records = _lineage_records(filtered_df)
    aggregate = _aggregate(records)
    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = out_dir / f"{today.isoformat()}-{REPORT_STEM}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    risky_records = sorted(
        [r for r in records if r["selected_count"] > 0],
        key=lambda r: (r["metrics"]["cvar20"], r["metrics"]["worst_case"]),
    )[:100]

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "self_checks": checks,
        "clob_gate": clob_gate,
        "operational_base": {
            "removed_cities": sorted(REMOVED_CITIES),
            "max_decision_hours_to_settle": MAX_DECISION_HOURS_TO_SETTLE,
            "source_rows": int(len(source_df)),
            "filtered_rows": int(len(filtered_df)),
            "date_range": [
                str(filtered_df["event_date"].min()) if len(filtered_df) else None,
                str(filtered_df["event_date"].max()) if len(filtered_df) else None,
            ],
        },
        "actual_live_operational_base": _actual_live_operational_base(db_path),
        "lineage_record_count": len(records),
        "city_day_count": len({(r["city"], r["target_date"]) for r in records}),
        "rule_ids": list(RULE_IDS),
        "aggregate": aggregate,
        "jsonl_path": str(jsonl_path),
        "sample_records": risky_records,
    }

    json_path = out_dir / f"{today.isoformat()}-{REPORT_STEM}.json"
    md_path = out_dir / f"{today.isoformat()}-{REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_markdown(report, md_path)

    print(f"lineage records: {len(records)}")
    print(f"city-days: {report['city_day_count']}")
    for rule_id, block in aggregate.items():
        summary = block["summary"]
        print(
            f"{rule_id:36s} n={summary['n_legs']:4d} "
            f"pnl={summary['total_pnl_usd']:+8.2f} "
            f"roi={summary['roi']*100:+7.2f}% "
            f"top5={summary['roi_excl_top5']*100:+7.2f}%"
        )
    print(f"JSONL written to {jsonl_path}")
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
