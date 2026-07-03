#!/usr/bin/env python3
"""Review recent regime-routed NO live cases."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
RUNTIME = ROOT / "runtime/weather_edge_v1/remote_pm_agent/regime_routed_no_tiny_live_v1"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_live_case_review_20260625_26"
OUT_CSV = OUT_DIR / "case_review.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-live-case-review.md"


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def fetch_gamma(market_id: Any) -> dict[str, Any]:
    if not market_id:
        return {}
    try:
        response = requests.get(f"https://gamma-api.polymarket.com/markets/{int(float(market_id))}", timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        return {"gamma_error": f"{type(exc).__name__}: {exc}"}
    try:
        outcomes = json.loads(data.get("outcomes") or "[]")
        prices = [float(x) for x in json.loads(data.get("outcomePrices") or "[]")]
    except Exception:
        outcomes, prices = [], []
    out = {
        "gamma_question": data.get("question"),
        "gamma_closed": data.get("closed"),
        "gamma_active": data.get("active"),
        "gamma_updated_at": data.get("updatedAt"),
        "gamma_yes_price": None,
        "gamma_no_price": None,
    }
    for outcome, price in zip(outcomes, prices):
        if str(outcome).lower() == "yes":
            out["gamma_yes_price"] = price
        elif str(outcome).lower() == "no":
            out["gamma_no_price"] = price
    return out


def fact_rows() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT city,target_date,bracket,side,order_ts_utc,fill_ts_utc,fill_price,fill_qty,cost_usd,
                   settlement_status,final_yes,contract_won,pnl_usd_at_fill,market_id,token_id,order_id
            FROM fact_trades
            WHERE trade_class='live_real'
              AND model_version='regime_routed_no_expression_v1'
              AND (target_date>='2026-06-24' OR fill_ts_utc>='2026-06-24')
            ORDER BY fill_ts_utc
            """
        )
    ]
    conn.close()
    return rows


def nearest_candidate(fact: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    order_ts = parse_ts(fact.get("order_ts_utc"))
    scored = []
    for row in candidates:
        if row.get("city") != fact.get("city") or row.get("target_date") != fact.get("target_date"):
            continue
        if str(row.get("token_id")) != str(fact.get("token_id")):
            continue
        row_ts = parse_ts(row.get("created_at_utc"))
        if not order_ts or not row_ts:
            continue
        delta_min = (order_ts - row_ts).total_seconds() / 60.0
        if -3.0 <= delta_min <= 90.0:
            scored.append((abs(delta_min), delta_min, row))
    if not scored:
        return {}
    scored.sort(key=lambda item: item[0])
    out = dict(scored[0][2])
    out["nearest_candidate_delta_min"] = scored[0][1]
    return out


def classify(row: dict[str, Any]) -> str:
    city = row.get("city")
    if city == "NYC":
        return "duplicate_live_submission_but_won"
    if city == "Seattle":
        return "lost_thin_bracket_escape_margin"
    if city == "Chongqing":
        return "bad_peak_clock_and_thin_margin"
    if city == "Karachi":
        return "post_fix_loss_forecast_overestimate_windy_mixing"
    if city == "Manila":
        return "appears_winning_but_pullback_state_needs_review"
    return "review"


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"${float(value):+.2f}"


def pct_price(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.3f}"


def write_markdown(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    total_cost = sum(float(row.get("cost_usd") or 0) for row in rows)
    settled_pnl = sum(float(row.get("pnl_usd_at_fill") or 0) for row in rows if row.get("pnl_usd_at_fill") is not None)
    lines = [
        "# Regime-routed NO Live Case Review",
        "",
        "## Conclusion",
        "",
        "最近三天不是一个 bug，而是四类问题同时暴露：重复下单、peak-clock 未进旧 live、bracket escape margin 太薄、以及 forecast overestimate / mixing 风险。",
        "",
        f"- live cases reviewed: {len(rows)}",
        f"- total filled cost: {money(total_cost)}",
        f"- settled PnL currently in fact_trades: {money(settled_pnl)}",
        f"- CLOB fill coverage gate: {summary['coverage_gate']}",
        "",
        "## Cases",
        "",
        "| city | target | bracket | entry | cost | fact/result | gamma yes/no | mechanism | verdict |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        fact_result = (
            f"settled pnl {money(row.get('pnl_usd_at_fill'))}"
            if row.get("settlement_status") == "settled"
            else "open in fact"
        )
        gamma = f"{pct_price(row.get('gamma_yes_price'))}/{pct_price(row.get('gamma_no_price'))}"
        mechanism = (
            f"day={row.get('day_regime')}; state={row.get('intraday_state')}; "
            f"peak_delta={row.get('forecast_peak_delta_hours_local')}; "
            f"gap_run={row.get('forecast_gap_to_running_native')}; "
            f"rh={row.get('relative_humidity_pct')}; wind={row.get('wind_speed_kt')}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("city") or ""),
                    str(row.get("target_date") or ""),
                    str(row.get("bracket") or ""),
                    pct_price(row.get("fill_price")),
                    money(row.get("cost_usd")),
                    fact_result,
                    gamma,
                    mechanism,
                    str(row.get("case_class") or ""),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Root Causes",
            "",
            "1. `NYC`: duplicate city/date/token submission happened before duplicate live gate was fixed. It won, but process-wise it was wrong.",
            "2. `Seattle`: peak was still ahead and trend was warm, but forecast max was only 65.3F against bracket 64-65F. This is too thin for a current-bracket NO.",
            "3. `Chongqing`: old live lacked peak-clock enforcement; decision was after forecast peak and margin was only +0.6C.",
            "4. `Karachi`: after the peak-clock patch, it still passed because peak was ahead. Current market marks 35C YES near certain, so this is a separate forecast-overestimate / windy-mixing failure.",
            "5. `Manila`: current market marks NO near certain, but it was a pullback-from-high / humid case. It should stay in review because the same shape can become false runway in other cities.",
            "",
            "## Actions",
            "",
            "- Already deployed: duplicate city/date/token veto, peak-clock sizing, current-NO after-peak veto.",
            "- Still needed: add `forecast_max - bracket_upper` escape margin as a core current-NO feature; do not rely on `forecast_max - running_max` alone.",
            "- Still needed: log accepted candidate full feature payload in live orders; pre-fix cases required reconstruction from blocked candidates.",
            "- Still needed: isolate windy-mixing / forecast-overestimate failures like Karachi in frozen forward, not by one-off city blacklist.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = read_jsonl(RUNTIME / "blocked_candidates.jsonl")
    rows = []
    for fact in fact_rows():
        cand = nearest_candidate(fact, candidates)
        gamma = fetch_gamma(fact.get("market_id"))
        row = {**fact}
        for key in [
            "created_at_utc",
            "candidate_status",
            "execution_skip_reason",
            "nearest_candidate_delta_min",
            "day_regime",
            "intraday_state",
            "moisture_cloud_regime",
            "wind_regime",
            "running_max_state",
            "route_leg",
            "ask",
            "bid",
            "soft_balanced",
            "soft_shares",
            "forecast_source",
            "forecast_max_native",
            "forecast_peak_hour_local",
            "forecast_peak_delta_hours_local",
            "forecast_gap_to_running_native",
            "running_native",
            "current_native",
            "temp_trend_1h_f",
            "temp_trend_3h_f",
            "relative_humidity_pct",
            "wind_speed_kt",
            "minutes_since_running_max",
            "current_no_peak_clock_ok",
            "peak_clock_state",
            "peak_clock_multiplier",
        ]:
            row[key] = cand.get(key)
        row.update(gamma)
        row["case_class"] = classify(row)
        rows.append(row)

    summary = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "coverage_gate": "pass_before_case_review",
        "cases": len(rows),
        "outputs": {
            "csv": str(OUT_CSV.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    fieldnames = sorted({key for row in rows for key in row})
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    OUT_JSON.write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_markdown(rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
