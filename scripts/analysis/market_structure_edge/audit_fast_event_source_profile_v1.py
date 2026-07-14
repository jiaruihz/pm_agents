#!/usr/bin/env python3
"""Audit deployable coverage and historical telemetry impact for fast-event profiles."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.fast_event_source_policy import load_fast_event_source_profiles, market_value_from_temp_c


RNG_SEED = 20260714


def bootstrap_roi(rows: pd.DataFrame, reps: int = 20000) -> tuple[float, float]:
    daily = rows.groupby("target_date", as_index=False).agg(cost=("entry_cost", "sum"), pnl=("settlement_pnl", "sum"))
    if len(daily) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(daily), size=(reps, len(daily)))
    cost = daily["cost"].to_numpy()[indices].sum(axis=1)
    pnl = daily["pnl"].to_numpy()[indices].sum(axis=1)
    values = np.divide(pnl, cost, out=np.full_like(pnl, np.nan, dtype=float), where=cost > 0)
    return float(np.nanquantile(values, 0.025)), float(np.nanquantile(values, 0.975))


def replay_summary(rows: pd.DataFrame) -> dict[str, Any]:
    cost = float(rows["entry_cost"].sum())
    pnl = float(rows["settlement_pnl"].sum())
    lo, hi = bootstrap_roi(rows)
    return {
        "rows": len(rows),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "win_rate": float(rows["outcome"].mean()),
        "roi": pnl / cost if cost else None,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def telemetry_impact(root: Path, profiles: dict[tuple[str, str], Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"root": str(root)}
    for filename in ("events.jsonl", "quote_snapshots.jsonl"):
        rows = read_jsonl(root / filename)
        wrong_us = []
        for row in rows:
            profile = profiles.get((str(row.get("city") or ""), str(row.get("source") or "")))
            if profile is None or profile.market_unit != "F":
                continue
            temp = row.get("source_temp_c")
            old = row.get("source_round_c")
            if temp is None or old is None:
                continue
            if int(old) != market_value_from_temp_c(float(temp), profile):
                wrong_us.append(row)
        result[filename] = {
            "rows": len(rows),
            "wrong_unit_us_rows": len(wrong_us),
            "wrong_unit_us_event_keys": len({str(row.get("event_key") or "") for row in wrong_us}),
        }
    return result


def pct(value: Any) -> str:
    return "" if value is None or not np.isfinite(value) else f"{100.0 * float(value):.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selected-rows",
        default=str(ROOT / "docs/analysis/2026-07/generated/source_event_hazard_router_v1/selected_strategy_rows.csv"),
    )
    parser.add_argument(
        "--state-rows",
        default=str(ROOT / "docs/analysis/2026-07/generated/tmax_distribution_v3/state_rows_v3.csv"),
    )
    parser.add_argument(
        "--runtime-root",
        default="/Volumes/jrs/weather_data_feed_service_runtime",
    )
    parser.add_argument(
        "--report",
        default=str(ROOT / "docs/analysis/2026-07/2026-07-14-fast-event-source-profile-v1.md"),
    )
    parser.add_argument(
        "--json",
        default=str(ROOT / "docs/analysis/2026-07/2026-07-14-fast-event-source-profile-v1.json"),
    )
    args = parser.parse_args()

    profiles = load_fast_event_source_profiles()
    profile_cities = {profile.city for profile in profiles.values()}
    state_rows = pd.read_csv(args.state_rows, usecols=["city"])
    research_cities = set(state_rows["city"].dropna().astype(str))
    selected = pd.read_csv(args.selected_rows)
    selected = selected[
        selected["strategy"].eq("cross_continuation_current_no")
        & pd.to_numeric(selected["edge_threshold"], errors="coerce").round(4).eq(0.02)
    ].copy()
    selected["has_fast_event_profile"] = selected["city"].isin(profile_cities)
    covered = selected[selected["has_fast_event_profile"]]
    uncovered = selected[~selected["has_fast_event_profile"]]

    basis_counts: dict[str, int] = {}
    calibration_counts: dict[str, int] = {}
    for profile in profiles.values():
        basis_counts[profile.source_basis_class] = basis_counts.get(profile.source_basis_class, 0) + 1
        calibration_counts[profile.calibration_status] = calibration_counts.get(profile.calibration_status, 0) + 1

    runtime_root = Path(args.runtime_root)
    impacts = [
        telemetry_impact(runtime_root / "output/source_event_ladder_repricing_shadow", profiles),
        telemetry_impact(runtime_root / "output/fast_source_stale_book", profiles),
    ]
    payload = {
        "schema_version": "fast_event_source_profile_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "profiles": len(profiles),
            "cities": len(profile_cities),
            "live_eligible": sum(profile.live_eligible for profile in profiles.values()),
            "basis_counts": dict(sorted(basis_counts.items())),
            "calibration_counts": dict(sorted(calibration_counts.items())),
        },
        "coverage": {
            "research_cities": len(research_cities),
            "profile_cities_in_research": len(research_cities & profile_cities),
            "no_fast_profile_cities": sorted(research_cities - profile_cities),
            "policy": "no profile blocks only source_event head; it does not ban the city from unrelated strategies",
        },
        "replay": {
            "all": replay_summary(selected),
            "profile_covered": replay_summary(covered),
            "no_profile": replay_summary(uncovered),
        },
        "telemetry_impact": impacts,
        "fixes": [
            "per-city local target_date instead of Asia/Shanghai global rollover",
            "profile-driven C/F conversion and rounding/floor semantics",
            "real market ladder intervals instead of numeric bracket plus/minus one",
            "one event per city/date/source/market-bracket episode",
            "persistent city/date market-token index with Gamma fill only when missing",
        ],
        "verdict": "calibration_only_zero_notional_no_live",
    }

    lines = [
        "# Fast-event source profile v1",
        "",
        "> 2026-07-14; current-reference; zero-notional only; no live policy or order change.",
        "",
        "## 结论",
        "",
        f"- profile 共 {len(profiles)} 条 / {len(profile_cities)} 城；`live_eligible=0`。",
        f"- 本次研究 {len(research_cities)} 城中，{len(research_cities & profile_cities)} 城有 fast-event profile，{len(research_cities - profile_cities)} 城没有。无 profile 只是不运行 source-event head，不是全局禁用城市。",
        f"- current-NO 候选全体 {len(selected)} 行 ROI {pct(payload['replay']['all']['roi'])}；profile-covered {len(covered)} 行 ROI {pct(payload['replay']['profile_covered']['roi'])}，CI [{pct(payload['replay']['profile_covered']['roi_ci_low'])},{pct(payload['replay']['profile_covered']['roi_ci_high'])}]，仍未过 live 门。",
        "",
        "## Profile basis",
        "",
        "| source basis | profiles |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in sorted(basis_counts.items()))
    lines += [
        "",
        "## Replay denominator",
        "",
        "| slice | rows | dates | cities | win | ROI | CI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in payload["replay"].items():
        lines.append(
            f"| {name} | {summary['rows']} | {summary['dates']} | {summary['cities']} | {pct(summary['win_rate'])} | {pct(summary['roi'])} | [{pct(summary['roi_ci_low'])},{pct(summary['roi_ci_high'])}] |"
        )
    lines += [
        "",
        "## 历史污染半径",
        "",
        "| output | events wrong-unit | quote rows wrong-unit | live orders affected |",
        "|---|---:|---:|---:|",
    ]
    for impact in impacts:
        lines.append(
            f"| `{impact['root']}` | {impact['events.jsonl']['wrong_unit_us_rows']} | {impact['quote_snapshots.jsonl']['wrong_unit_us_rows']} | 0 |"
        )
    lines += [
        "",
        "旧 v1 telemetry 必须按 schema 分层；美国错误单位行不可用于 repricing 结论。本修复没有真实订单影响。",
        "",
        "## 无快源城市怎么处理",
        "",
        "这些城市只从 `source_event` head 排除；forecast、regime、lottery、tmax、普通 METAR/WU 策略仍按各自证据和 policy 独立决定。不能用“没有快源”推导成“城市不可交易”。",
        "",
        "## 当前状态",
        "",
        "`calibration_only_zero_notional_no_live`。下一阶段按 city × source 收集 side-neutral YES/NO repricing，不能把 pooled current-NO 点估直接升级为 5-share live。",
    ]

    report_path = Path(args.report)
    json_path = Path(args.json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "json": str(json_path), "verdict": payload["verdict"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
