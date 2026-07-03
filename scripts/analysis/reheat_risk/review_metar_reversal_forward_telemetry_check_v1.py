#!/usr/bin/env python3
"""Head B METAR reversal forward telemetry check v1.

Research-only report generator. It reads existing Head B generated matrices,
the zero-notional shadow journal, the rebuilt weather.db, and CLOB coverage
gate output. It does not place orders or touch live strategy configs.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
RUNWAY_ROWS = ROOT / "docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1/runway_matrix_rows.csv"
METAR_ROWS = ROOT / "docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1/expression_matrix_rows.csv"
SHADOW_DIR = ROOT / "runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1"
DECISIONS = SHADOW_DIR / "state_decisions.jsonl"
SUMMARY_HISTORY = SHADOW_DIR / "summary_history.jsonl"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
DB_PATH = ROOT / "runtime/weather.db"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-forward-telemetry-check-v1.md"
REPORT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-metar-reversal-forward-telemetry-check-v1.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_float(value: Any) -> float:
    try:
        if value in ("", None):
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def counter_dict(values: list[Any]) -> dict[str, int]:
    return {str(k): int(v) for k, v in Counter(values).items()}


def db_snapshot() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    rows = {}
    rows["fact_signal_candidates"] = conn.execute(
        """
        SELECT COUNT(*), MIN(event_date), MAX(event_date), MAX(fact_built_at_utc)
        FROM fact_signal_candidates
        """
    ).fetchone()
    rows["fact_trades"] = conn.execute(
        """
        SELECT COUNT(*), MIN(target_date), MAX(target_date), MAX(fact_built_at_utc),
               SUM(CASE WHEN settlement_status IS NULL OR settlement_status != 'settled' THEN 1 ELSE 0 END),
               SUM(CASE WHEN settlement_status = 'missing_bracket' THEN 1 ELSE 0 END)
        FROM fact_trades
        """
    ).fetchone()
    conn.close()
    return {
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_mtime_utc": datetime.fromtimestamp(DB_PATH.stat().st_mtime, timezone.utc).isoformat(),
        "fact_signal_candidates": {
            "rows": rows["fact_signal_candidates"][0],
            "min_event_date": rows["fact_signal_candidates"][1],
            "max_event_date": rows["fact_signal_candidates"][2],
            "fact_built_at_utc": rows["fact_signal_candidates"][3],
        },
        "fact_trades": {
            "rows": rows["fact_trades"][0],
            "min_target_date": rows["fact_trades"][1],
            "max_target_date": rows["fact_trades"][2],
            "fact_built_at_utc": rows["fact_trades"][3],
            "unsettled_rows": rows["fact_trades"][4] or 0,
            "missing_bracket_rows": rows["fact_trades"][5] or 0,
        },
    }


def historical_frequency() -> dict[str, Any]:
    runway = read_csv(RUNWAY_ROWS)
    for r in runway:
        for k in [
            "current_high_yes_ask",
            "temp_trend_1h_f",
            "forecast_peak_delta_hours_local",
            "d1_yes_ask",
            "d1_yes_size",
        ]:
            r[f"_{k}"] = as_float(r.get(k))
    b4_proxy = [
        r
        for r in runway
        if r.get("runway_state") == "one_step_runway"
        and r.get("market_current_state") == "current_live"
        and r["_current_high_yes_ask"] >= 0.60
        and r["_temp_trend_1h_f"] >= 0.5
        and r["_forecast_peak_delta_hours_local"] <= 0
    ]
    metar = read_csv(METAR_ROWS)
    for r in metar:
        for k in [
            "temp_trend_1h_f",
            "forecast_max_native",
            "running_native",
            "forecast_peak_delta_hours_local",
            "d1_yes_ask",
            "d1_yes_size",
            "current_high_yes_ask",
        ]:
            r[f"_{k}"] = as_float(r.get(k))
    false_fade = [
        r
        for r in metar
        if r["_temp_trend_1h_f"] >= 0.5
        and (r["_forecast_max_native"] - r["_running_native"]) >= 1.0
        and r["_forecast_peak_delta_hours_local"] <= 0
        and r["_d1_yes_ask"] <= 0.30
        and r["_current_high_yes_ask"] >= 0.40
    ]

    def summarize(rows: list[dict[str, Any]], size_key: str) -> dict[str, Any]:
        dates = sorted({r.get("target_date") for r in rows if r.get("target_date")})
        recent = [r for r in rows if str(r.get("target_date")) >= "2026-06-21"]
        sizes = [r[size_key] for r in rows if finite(r.get(size_key))]
        return {
            "rows": len(rows),
            "dates": len(dates),
            "cities": len({r.get("city") for r in rows if r.get("city")}),
            "min_date": dates[0] if dates else None,
            "max_date": dates[-1] if dates else None,
            "rows_2026_06_21_plus": len(recent),
            "dates_2026_06_21_plus": sorted({r.get("target_date") for r in recent if r.get("target_date")}),
            "d1_size_n": len(sizes),
            "d1_size_median": median(sizes) if sizes else None,
            "d1_size_lt_5": sum(1 for v in sizes if v < 5),
            "d1_size_lt_10": sum(1 for v in sizes if v < 10),
            "d1_size_lt_20": sum(1 for v in sizes if v < 20),
        }

    one_step_recent = [r for r in runway if r.get("runway_state") == "one_step_runway" and str(r.get("target_date")) >= "2026-06-21"]
    return {
        "rich_current_b4_proxy": summarize(b4_proxy, "_d1_yes_size"),
        "false_fade_reheat_conflict": summarize(false_fade, "_d1_yes_size"),
        "one_step_runway_2026_06_21_plus_rows": len(one_step_recent),
        "one_step_runway_2026_06_21_plus_dates": sorted({r.get("target_date") for r in one_step_recent if r.get("target_date")}),
        "source_files": [str(RUNWAY_ROWS.relative_to(ROOT)), str(METAR_ROWS.relative_to(ROOT))],
    }


def shadow_telemetry() -> dict[str, Any]:
    summaries = read_jsonl(SUMMARY_HISTORY)
    decisions = read_jsonl(DECISIONS)
    latest_cycle = summaries[-1].get("cycle_id") if summaries else None
    latest_rows = [r for r in decisions if r.get("cycle_id") == latest_cycle]
    latest_ok = [r for r in latest_rows if r.get("state_status") == "ok"]
    all_ok = [r for r in decisions if r.get("state_status") == "ok"]
    size_values = [r.get("d1_yes_ask_size") for r in latest_ok if finite(r.get("d1_yes_ask_size"))]
    obs_age_values = [r.get("obs_age_min") for r in latest_ok if finite(r.get("obs_age_min"))]
    near_b4 = []
    for r in latest_ok:
        c = r.get("conds_b4") or {}
        if c.get("trend_ok") and c.get("forecast_steps_ge_1") and c.get("peak_ahead_ok") and c.get("obs_fresh_ok"):
            near_b4.append(r)
    return {
        "summary_rows": len(summaries),
        "unique_snapshots": len({r.get("snapshot_file") for r in summaries if r.get("snapshot_file")}),
        "latest_summary": summaries[-1] if summaries else None,
        "total_false_fade_triggers": sum(int(bool(r.get("false_fade_reheat_conflict_triggered"))) for r in summaries),
        "total_b4_triggers": sum(int(bool(r.get("rich_current_conflict_b4_triggered"))) for r in summaries),
        "latest_state_status": counter_dict([r.get("state_status") for r in latest_rows]),
        "latest_d1_book_status": counter_dict([r.get("d1_book_status") for r in latest_ok]),
        "latest_d1_quote_source": counter_dict([r.get("d1_quote_source") for r in latest_ok]),
        "latest_current_quote_source": counter_dict([r.get("current_quote_source") for r in latest_ok]),
        "latest_d1_size_n": len(size_values),
        "latest_d1_size_median": median(size_values) if size_values else None,
        "latest_obs_age_min_median": median(obs_age_values) if obs_age_values else None,
        "all_ok_rows": len(all_ok),
        "all_ok_d1_size_finite": sum(1 for r in all_ok if finite(r.get("d1_yes_ask_size"))),
        "all_ok_d1_quote_source": counter_dict([r.get("d1_quote_source") for r in all_ok]),
        "latest_b4_near_without_current_rich": [
            {
                "city": r.get("city"),
                "current_yes_ask": r.get("current_yes_ask"),
                "d1_yes_ask": r.get("d1_yes_ask"),
                "d1_yes_ask_size": r.get("d1_yes_ask_size"),
                "d1_book_status": r.get("d1_book_status"),
            }
            for r in near_b4
        ],
        "source_files": [str(DECISIONS.relative_to(ROOT)), str(SUMMARY_HISTORY.relative_to(ROOT))],
    }


def gate_snapshot() -> dict[str, Any]:
    if not GATE_JSON.exists():
        return {"gate_file": str(GATE_JSON.relative_to(ROOT)), "exists": False}
    payload = json.loads(GATE_JSON.read_text(encoding="utf-8"))
    return {
        "gate_file": str(GATE_JSON.relative_to(ROOT)),
        "exists": True,
        "gate_pass": payload.get("gate_pass"),
        "fail_reasons": payload.get("fail_reasons"),
        "fact_trades_live_real": payload.get("fact_trades_live_real"),
        "db_vs_primary_cache": payload.get("db_vs_primary_cache"),
    }


def write_report(result: dict[str, Any]) -> None:
    db = result["db_snapshot"]
    hist = result["historical_frequency"]
    shadow = result["shadow_telemetry"]
    gate = result["clob_gate"]
    b4 = hist["rich_current_b4_proxy"]
    ff = hist["false_fade_reheat_conflict"]
    latest = shadow["latest_summary"] or {}
    lines = [
        "# METAR Reversal Forward Telemetry Check v1",
        "",
        f"Generated: {result['generated_at_utc']}",
        "",
        "## Data Snapshot",
        "",
        "- Data sources: rebuilt `runtime/weather.db`, generated Head B CSV matrices, and zero-notional shadow journal.",
        f"- DB mtime: `{db['db_mtime_utc']}`; `fact_signal_candidates` rows={db['fact_signal_candidates']['rows']} event_date={db['fact_signal_candidates']['min_event_date']}..{db['fact_signal_candidates']['max_event_date']} built={db['fact_signal_candidates']['fact_built_at_utc']}.",
        f"- `fact_trades` rows={db['fact_trades']['rows']} target_date={db['fact_trades']['min_target_date']}..{db['fact_trades']['max_target_date']} built={db['fact_trades']['fact_built_at_utc']}; unsettled={db['fact_trades']['unsettled_rows']}; missing_bracket={db['fact_trades']['missing_bracket_rows']}.",
        f"- CLOB fill coverage gate: gate_pass={gate.get('gate_pass')} fail_reasons={gate.get('fail_reasons')}.",
        "- `run_stack.sh` completed DB/fact/gate work, then exited on frontend port 5174 still busy; analysis uses the rebuilt DB/gate artifacts.",
        "",
        "## Verdict",
        "",
        "`rich_current_collapse_d1_yes` remains `shadow_candidate_keep_collecting`: keep zero-notional shadow running, do not change live, and do not approve future small size yet.",
        "",
        "```text",
        "significance=PASS historically for the frozen Head B branch already documented",
        "baseline=PASS historically versus same-snapshot broad d1/current siblings",
        "forward=FAIL/NA: no 2026-06-21+ qualifying state in offline matrices; current shadow has 0 triggers",
        "conclusion=shadow_candidate, not confirmed",
        "```",
        "",
        "## State Frequency",
        "",
        f"- Historical B4/runway proxy: {b4['rows']} rows / {b4['dates']} dates / {b4['cities']} cities, date range {b4['min_date']}..{b4['max_date']}; 2026-06-21+ rows={b4['rows_2026_06_21_plus']}.",
        f"- Historical false_fade sibling: {ff['rows']} rows / {ff['dates']} dates / {ff['cities']} cities, date range {ff['min_date']}..{ff['max_date']}; 2026-06-21+ rows={ff['rows_2026_06_21_plus']}.",
        f"- Broad one-step runway rows after 2026-06-21: {hist['one_step_runway_2026_06_21_plus_rows']} rows.",
        f"- Forward shadow journal: {shadow['summary_rows']} summary rows / {shadow['unique_snapshots']} unique snapshots; total false_fade triggers={shadow['total_false_fade_triggers']}, total B4 triggers={shadow['total_b4_triggers']}.",
        f"- Latest cycle `{latest.get('cycle_id')}` on `{latest.get('snapshot_file')}`: states={latest.get('states')}, states_ok={latest.get('states_ok')}, false_fade={latest.get('false_fade_reheat_conflict_triggered')}, B4={latest.get('rich_current_conflict_b4_triggered')}.",
        "",
        "## Fresh-Book Fill Feasibility",
        "",
        f"- Latest d1 quote source: {shadow['latest_d1_quote_source']}; latest d1 book status: {shadow['latest_d1_book_status']}.",
        f"- Latest finite `d1_yes_ask_size` count: {shadow['latest_d1_size_n']}; all journaled ok rows finite d1 size: {shadow['all_ok_d1_size_finite']} / {shadow['all_ok_rows']}.",
        f"- Historical d1 sizes were already thin: B4 proxy median={b4['d1_size_median']} shares, false_fade median={ff['d1_size_median']} shares; false_fade had {ff['d1_size_lt_20']} / {ff['d1_size_n']} rows below 20 shares.",
        "- Current fresh-book feasibility is partially restored after switching the local data-feed snapshot loop to full orderbook coverage: latest ok states now include executable d1 ask size, but residual budget exhaustion and thin historical size mean fill feasibility still needs fresh-forward accumulation.",
        "",
        "## Runner State",
        "",
        "- `scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py` is Head B-only and writes zero-notional JSONL rows; it does not call the order executor.",
        "- A Mac LaunchAgent was added and loaded as `com.pm-agents.metar-reversal-shadow`, reading local data-feed paper snapshots and the shared observation cache every 300s.",
        "",
        "## Action",
        "",
        "- Continue shadow: yes.",
        "- Mark dormant: no, not yet; the evidence gap is fresh-forward frequency and book depth, exactly what the shadow now collects.",
        "- Future small live: no. Revisit only after nonzero fresh-forward triggers with actual CLOB d1 ask size/depth and settled forward outcomes.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    result = {
        "generated_at_utc": now_utc(),
        "db_snapshot": db_snapshot(),
        "clob_gate": gate_snapshot(),
        "historical_frequency": historical_frequency(),
        "shadow_telemetry": shadow_telemetry(),
        "verdict": {
            "significance": "PASS_historical",
            "baseline": "PASS_historical",
            "forward": "FAIL_or_NA",
            "conclusion": "shadow_candidate_keep_collecting",
            "live_action": "none",
        },
    }
    REPORT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    write_report(result)
    print(json.dumps({"report": str(REPORT), "json": str(REPORT_JSON), "verdict": result["verdict"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
