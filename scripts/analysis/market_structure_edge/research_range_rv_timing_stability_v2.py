#!/usr/bin/env python3
"""Timing and stability optimization for Range RV shadow telemetry.

This is a forward-shadow study. It does not claim live PnL and it does not use
settlement to pick an intraday timestamp inside a city/model group. Each policy
selects at most one pre-defined row per city/event/source/model group.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
TARGET_METRIC = "forecast_bounded_range_rv_timing_stability_v2"
STRATEGY_ID = "forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0"
SETTLED_YES_THRESHOLD = 0.99
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-25-range-rv-timing-stability-v2.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-25-range-rv-timing-stability-v2.md"

CITY_TZ = {
    "Ankara": "Europe/Istanbul",
    "Chengdu": "Asia/Shanghai",
    "Guangzhou": "Asia/Shanghai",
    "Jeddah": "Asia/Riyadh",
    "Karachi": "Asia/Karachi",
    "LA": "America/Los_Angeles",
    "Lucknow": "Asia/Kolkata",
    "Madrid": "Europe/Madrid",
    "Manila": "Asia/Manila",
    "Miami": "America/New_York",
    "Munich": "Europe/Berlin",
    "NYC": "America/New_York",
    "Seattle": "America/Los_Angeles",
    "Shanghai": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Tokyo": "Asia/Tokyo",
    "Warsaw": "Europe/Warsaw",
}


def default_journal_path() -> Path:
    return ROOT / "runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(default_journal_path()))
    parser.add_argument("--pm-history-dir", default=str(ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument(
        "--regime-csv",
        default=str(
            ROOT
            / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
        ),
    )
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(db_path: Path) -> dict[str, Any]:
    with connect_ro(db_path) as conn:
        return {
            "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
            "trade_class_distribution": sql_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "settlement_status_distribution": sql_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "candidate_coverage": sql_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "order_fill_coverage": sql_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_regime_rows(path: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            hour = int(float(row["decision_hour_local"]))
            key = (row["city"], row["target_date"], hour)
            out[key] = {
                "day_regime": row.get("day_regime") or None,
                "intraday_state": row.get("intraday_state") or None,
                "composite_regime": row.get("composite_regime") or None,
                "forecast_gap_to_running_native": safe_float(row.get("forecast_gap_to_running_native")),
                "remaining_heat_native": safe_float(row.get("remaining_heat_native")),
                "forecast_peak_delta_hours_local": safe_float(row.get("forecast_peak_delta_hours_local")),
            }
    return out


def winner_for(pm_history_dir: Path, city: str, event_date: str) -> tuple[str | None, str]:
    path = pm_history_dir / f"{city}_{event_date}.json"
    if not path.exists():
        return None, "missing_event"
    data = json.loads(path.read_text())
    winners = []
    for bracket in data.get("brackets", []):
        price = safe_float(bracket.get("final_price"))
        if price is not None and price >= SETTLED_YES_THRESHOLD:
            winners.append(str(bracket.get("label")))
    if len(winners) != 1:
        return None, f"winner_count_{len(winners)}"
    return winners[0], "settled"


def decision_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["city"]),
        str(row["event_date"]),
        str(row.get("forecast_source") or ""),
        str(row["model_version"]),
    )


def inside_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(map(str, row.get("inside_brackets") or []))


def parse_event_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def enrich_row(row: dict[str, Any], pm_dir: Path, regime: dict[tuple[str, str, int], dict[str, Any]]) -> dict[str, Any]:
    city = str(row["city"])
    if city not in CITY_TZ:
        raise ValueError(f"missing timezone mapping for {city}")
    tz = ZoneInfo(CITY_TZ[city])
    snapshot_utc = datetime.fromisoformat(str(row["snapshot_ts_utc"]).replace("Z", "+00:00"))
    local_dt = snapshot_utc.astimezone(tz)
    event_day = parse_event_date(str(row["event_date"]))
    day_end = datetime.combine(event_day + timedelta(days=1), time(0, 0), tzinfo=tz)
    hours_to_day_end = (day_end - local_dt).total_seconds() / 3600.0

    winner, settlement_status = winner_for(pm_dir, city, str(row["event_date"]))
    inside = set(map(str, row.get("inside_brackets") or []))
    hit = winner in inside if settlement_status == "settled" else None
    gross_cost = sum(float(leg["best_ask"]) for leg in row.get("legs") or [] if leg.get("best_ask") is not None)
    effective_cost = float(row["effective_range_cost"])
    pnl = None
    if settlement_status == "settled":
        if row["expression"] == "inside_yes":
            pnl = (1.0 if hit else 0.0) - gross_cost
        elif row["expression"] == "outside_no":
            outside = {str(leg.get("bracket")) for leg in row.get("legs") or []}
            payout = (len(outside) - 1.0) if winner in outside else float(len(outside))
            pnl = payout - gross_cost
        else:
            raise ValueError(f"unknown expression {row['expression']}")

    regime_meta = regime.get((city, str(row["event_date"]), local_dt.hour), {})
    return {
        **row,
        "snapshot_utc": snapshot_utc.isoformat(),
        "local_ts": local_dt.isoformat(),
        "local_date": local_dt.date().isoformat(),
        "local_hour": local_dt.hour,
        "local_day_offset": (local_dt.date() - event_day).days,
        "hours_to_day_end": hours_to_day_end,
        "settlement_eval_status": settlement_status,
        "winner": winner,
        "hit": hit,
        "gross_cost": gross_cost,
        "eff_cost": effective_cost,
        "pnl": pnl,
        "inside_key": "|".join(inside_key(row)),
        "model_mass": float(row["range_model_mass_norm"]),
        "edge": float(row["orderbook_edge"]),
        "day_regime": regime_meta.get("day_regime"),
        "intraday_state": regime_meta.get("intraday_state"),
        "composite_regime": regime_meta.get("composite_regime"),
        "regime_joined": bool(regime_meta),
        "forecast_gap_to_running_native": regime_meta.get("forecast_gap_to_running_native"),
        "remaining_heat_native": regime_meta.get("remaining_heat_native"),
        "forecast_peak_delta_hours_local": regime_meta.get("forecast_peak_delta_hours_local"),
    }


def add_sequence_features(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[decision_key(row)].append(row)
    out = []
    for group in grouped.values():
        group.sort(key=lambda row: row["snapshot_utc"])
        last_inside = None
        last_expr = None
        inside_run = 0
        expr_run = 0
        seen_inside: set[str] = set()
        first_ts = group[0]["snapshot_utc"]
        for idx, row in enumerate(group, 1):
            if row["inside_key"] == last_inside:
                inside_run += 1
            else:
                inside_run = 1
                last_inside = row["inside_key"]
            if row["expression"] == last_expr:
                expr_run += 1
            else:
                expr_run = 1
                last_expr = row["expression"]
            seen_inside.add(row["inside_key"])
            out.append(
                {
                    **row,
                    "trigger_index": idx,
                    "trigger_count_so_far": idx,
                    "same_inside_run": inside_run,
                    "same_expression_run": expr_run,
                    "distinct_inside_so_far": len(seen_inside),
                    "first_snapshot_ts_utc": first_ts,
                }
            )
    return out


def select_last(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    valid = [row for row in rows if predicate(row)]
    if not valid:
        return None
    return max(valid, key=lambda row: row["snapshot_utc"])


def select_first(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    valid = [row for row in rows if predicate(row)]
    if not valid:
        return None
    return min(valid, key=lambda row: row["snapshot_utc"])


def in_cost(lo: float | None, hi: float | None) -> Callable[[dict[str, Any]], bool]:
    def inner(row: dict[str, Any]) -> bool:
        cost = float(row["eff_cost"])
        if lo is not None and cost <= lo:
            return False
        if hi is not None and cost > hi:
            return False
        return True

    return inner


def all_rows(_row: dict[str, Any]) -> bool:
    return True


def and_pred(*preds: Callable[[dict[str, Any]], bool]) -> Callable[[dict[str, Any]], bool]:
    return lambda row: all(pred(row) for pred in preds)


def policy_defs() -> list[tuple[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None], str]]:
    policies: list[tuple[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None], str]] = [
        ("first_trigger", lambda rows: select_first(rows, all_rows), "baseline: first eligible trigger"),
        ("latest_trigger", lambda rows: select_last(rows, all_rows), "baseline: latest eligible trigger"),
    ]
    for hour in [8, 10, 12, 14, 16, 18, 20, 22]:
        policies.append(
            (
                f"latest_before_local_{hour:02d}",
                lambda rows, hour=hour: select_last(rows, lambda row: row["local_day_offset"] < 0 or (row["local_day_offset"] == 0 and row["local_hour"] <= hour)),
                f"prediction cutoff: last trigger no later than local {hour:02d}:59 on target date",
            )
        )
    for hours in [6, 8, 10, 12, 14, 16, 20, 24]:
        policies.append(
            (
                f"latest_min_lead_{hours:02d}h",
                lambda rows, hours=hours: select_last(rows, lambda row: row["hours_to_day_end"] >= hours),
                f"prediction cutoff: last trigger with at least {hours}h before local day end",
            )
        )
    policies.extend(
        [
            (
                "stable2_latest",
                lambda rows: select_last(rows, lambda row: row["same_inside_run"] >= 2),
                "range stability: same inside range for 2 consecutive triggers",
            ),
            (
                "stable3_latest",
                lambda rows: select_last(rows, lambda row: row["same_inside_run"] >= 3),
                "range stability: same inside range for 3 consecutive triggers",
            ),
            (
                "stable2_before_local_18",
                lambda rows: select_last(
                    rows,
                    lambda row: row["same_inside_run"] >= 2
                    and (row["local_day_offset"] < 0 or (row["local_day_offset"] == 0 and row["local_hour"] <= 18)),
                ),
                "stability plus not-too-late local 18 cutoff",
            ),
            (
                "mid_cost_latest",
                lambda rows: select_last(rows, in_cost(0.50, 0.80)),
                "risk control: mid effective cost only",
            ),
            (
                "stable2_mid_cost_latest",
                lambda rows: select_last(rows, and_pred(lambda row: row["same_inside_run"] >= 2, in_cost(0.50, 0.80))),
                "stability plus mid cost",
            ),
            (
                "stable2_mid_cost_before18",
                lambda rows: select_last(
                    rows,
                    and_pred(
                        lambda row: row["same_inside_run"] >= 2,
                        in_cost(0.50, 0.80),
                        lambda row: row["local_day_offset"] < 0 or (row["local_day_offset"] == 0 and row["local_hour"] <= 18),
                    ),
                ),
                "candidate v2: stable range, mid cost, not later than local 18",
            ),
            (
                "stable2_inside_yes_before18",
                lambda rows: select_last(
                    rows,
                    lambda row: row["same_inside_run"] >= 2
                    and row["expression"] == "inside_yes"
                    and (row["local_day_offset"] < 0 or (row["local_day_offset"] == 0 and row["local_hour"] <= 18)),
                ),
                "diagnostic: stable inside-YES only before local 18",
            ),
            (
                "stable2_mass085_before18",
                lambda rows: select_last(
                    rows,
                    lambda row: row["same_inside_run"] >= 2
                    and row["model_mass"] >= 0.85
                    and (row["local_day_offset"] < 0 or (row["local_day_offset"] == 0 and row["local_hour"] <= 18)),
                ),
                "calibration control: stable and high model mass before local 18",
            ),
        ]
    )
    return policies


def apply_policies(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[decision_key(row)].append(row)
    policies = policy_defs()
    desc = {name: description for name, _selector, description in policies}
    selected = []
    for key, group in grouped.items():
        group.sort(key=lambda row: row["snapshot_utc"])
        for name, selector, _description in policies:
            row = selector(group)
            if row is not None:
                selected.append({**row, "policy": name})
    return selected, desc


def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("pnl") is not None]
    cost = sum(float(row["eff_cost"]) for row in settled)
    gross_cost = sum(float(row["gross_cost"]) for row in settled)
    pnl = sum(float(row["pnl"]) for row in settled)
    wins = [row for row in settled if float(row["pnl"]) > 0.0]
    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "groups": len({decision_key(row) for row in settled}),
        "event_dates": len({row["event_date"] for row in settled}),
        "cities": len({row["city"] for row in settled}),
        "cost": cost,
        "gross_cost": gross_cost,
        "pnl": pnl,
        "roi": None if cost <= 0 else pnl / cost,
        "gross_roi": None if gross_cost <= 0 else pnl / gross_cost,
        "hit_rate": None if not settled else len(wins) / len(settled),
        "avg_local_hour": None if not settled else sum(float(row["local_hour"]) for row in settled) / len(settled),
        "avg_hours_to_day_end": None if not settled else sum(float(row["hours_to_day_end"]) for row in settled) / len(settled),
        "avg_cost": None if not settled else cost / len(settled),
        "avg_model_mass": None if not settled else sum(float(row["model_mass"]) for row in settled) / len(settled),
        "regime_join_rate": None if not settled else sum(1 for row in settled if row.get("regime_joined")) / len(settled),
    }


def block_bootstrap_ci(rows: list[dict[str, Any]], n: int = 20000, seed: int = 19) -> tuple[float | None, float | None]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("pnl") is not None:
            by_date[str(row["event_date"])].append(row)
    days = list(by_date)
    if len(days) < 2:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample_days = [rng.choice(days) for _ in days]
        sample = [row for day in sample_days for row in by_date[day]]
        cost = sum(float(row["eff_cost"]) for row in sample)
        pnl = sum(float(row["pnl"]) for row in sample)
        vals.append(0.0 if cost <= 0 else pnl / cost)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def policy_summary(selected: list[dict[str, Any]], descriptions: dict[str, str]) -> list[dict[str, Any]]:
    by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_policy[str(row["policy"])].append(row)
    out = []
    for policy, rows in sorted(by_policy.items()):
        item = agg(rows)
        ci_low, ci_high = block_bootstrap_ci(rows)
        out.append(
            {
                "policy": policy,
                "description": descriptions[policy],
                **item,
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
                "positive_days": sum(1 for _day, vals in group_by(rows, "event_date").items() if sum(float(r["pnl"]) for r in vals if r.get("pnl") is not None) > 0),
            }
        )
    return sorted(out, key=lambda row: (row["event_dates"], row["roi"] if row["roi"] is not None else -999), reverse=True)


def group_by(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field))].append(row)
    return grouped


def split_summary(selected: list[dict[str, Any]], policies: list[str]) -> list[dict[str, Any]]:
    out = []
    for policy in policies:
        rows = [row for row in selected if row["policy"] == policy and row.get("pnl") is not None]
        for split_name, filt in [
            ("all_settled", lambda row: True),
            ("early_shadow_0614_0620", lambda row: "2026-06-14" <= row["event_date"] <= "2026-06-20"),
            ("recent_shadow_0621_0624", lambda row: "2026-06-21" <= row["event_date"] <= "2026-06-24"),
        ]:
            vals = [row for row in rows if filt(row)]
            ci_low, ci_high = block_bootstrap_ci(vals, n=10000)
            out.append({"policy": policy, "split": split_name, **agg(vals), "roi_ci_low": ci_low, "roi_ci_high": ci_high})
    return out


def grouped_summary(selected: list[dict[str, Any]], policy: str, field: str) -> list[dict[str, Any]]:
    rows = [row for row in selected if row["policy"] == policy and row.get("pnl") is not None]
    out = []
    for key, vals in group_by(rows, field).items():
        out.append({field: key, **agg(vals)})
    return sorted(out, key=lambda row: (row["event_dates"], row["rows"]), reverse=True)


def calibration_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settled = [row for row in rows if row.get("pnl") is not None]
    buckets = [
        ("model_mass_lt075", lambda row: row["model_mass"] < 0.75),
        ("model_mass_075_085", lambda row: 0.75 <= row["model_mass"] < 0.85),
        ("model_mass_085_095", lambda row: 0.85 <= row["model_mass"] < 0.95),
        ("model_mass_ge095", lambda row: row["model_mass"] >= 0.95),
        ("cost_le050", lambda row: row["eff_cost"] <= 0.50),
        ("cost_050_080", lambda row: 0.50 < row["eff_cost"] <= 0.80),
        ("cost_gt080", lambda row: row["eff_cost"] > 0.80),
    ]
    out = []
    for name, filt in buckets:
        vals = [row for row in settled if filt(row)]
        out.append({"bucket": name, **agg(vals)})
    return out


def compact(row: dict[str, Any]) -> list[Any]:
    return [
        row["settled_rows"],
        row["event_dates"],
        row["cities"],
        f"{row['cost']:.3f}",
        f"{row['pnl']:+.3f}",
        pct(row["roi"]),
        pct(row["roi_ci_low"]),
        pct(row["roi_ci_high"]),
        pct(row["hit_rate"]),
        f"{row['avg_local_hour']:.1f}" if row["avg_local_hour"] is not None else "NA",
        f"{row['avg_hours_to_day_end']:.1f}" if row["avg_hours_to_day_end"] is not None else "NA",
        pct(row["regime_join_rate"]),
    ]


def render_md(payload: dict[str, Any]) -> str:
    top = payload["policy_summary"][:24]
    focus = payload["focus_policy_summary"]
    split_rows = [[row["policy"], row["split"], *compact(row)] for row in payload["split_summary"]]
    policy_rows = [[row["policy"], *compact(row), row["description"]] for row in top]
    cal_rows = [[row["bucket"], *compact(row)] for row in payload["calibration_summary"]]
    regime_rows = [[row["day_regime"], *compact(row)] for row in payload["regime_summary"]]
    intraday_rows = [[row["intraday_state"], *compact(row)] for row in payload["intraday_summary"]]

    lines = [
        "# Range RV Timing Stability v2",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> strategy_id: `{STRATEGY_ID}`",
        "",
        "## Data Snapshot",
        "",
        "- Evidence layer: N100 zero-notional Range RV shadow journal plus `pm_history` settlement truth.",
        "- This is not live PnL. Every journal row is shadow-only and must keep `no_order_placed=true`.",
        f"- journal_rows: `{payload['journal']['rows']}`; selected policy rows: `{payload['selected_policy_rows']}`.",
        f"- snapshot range: `{payload['journal']['min_snapshot_ts_utc']}` -> `{payload['journal']['max_snapshot_ts_utc']}`.",
        f"- event_dates: `{', '.join(payload['journal']['event_dates'])}`.",
        f"- all_no_order_placed: `{payload['journal']['all_no_order_placed']}`; source_buckets: `{json.dumps(payload['journal']['source_buckets'], sort_keys=True)}`.",
        "",
        "### Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["self_check"], indent=2, sort_keys=True),
        "```",
        "",
        "## Policy Summary",
        "",
        "Each policy selects at most one row per `city + event_date + forecast_source + model_version`. Cutoff policies are prediction-style rules; regime labels are attached only when an hourly atlas row exists.",
        "",
        table(
            [
                "policy",
                "rows",
                "dates",
                "cities",
                "cost",
                "pnl",
                "roi",
                "ci_low",
                "ci_high",
                "hit",
                "avg_hour",
                "hrs_to_end",
                "regime_join",
                "description",
            ],
            policy_rows,
        ),
        "",
        "## Focus Policies By Time Split",
        "",
        table(
            [
                "policy",
                "split",
                "rows",
                "dates",
                "cities",
                "cost",
                "pnl",
                "roi",
                "ci_low",
                "ci_high",
                "hit",
                "avg_hour",
                "hrs_to_end",
                "regime_join",
            ],
            split_rows,
        ),
        "",
        "## Calibration Buckets For Focus Policy",
        "",
        f"Focus policy: `{payload['focus_policy']}`.",
        "",
        table(
            ["bucket", "rows", "dates", "cities", "cost", "pnl", "roi", "ci_low", "ci_high", "hit", "avg_hour", "hrs_to_end", "regime_join"],
            cal_rows,
        ),
        "",
        "## Regime Overlay",
        "",
        "Regime rows are explanatory only. They are more real-time than the forecast-bounded Range RV thesis, so they should not become hard gates without a separate forward test.",
        "",
        table(
            ["day_regime", "rows", "dates", "cities", "cost", "pnl", "roi", "ci_low", "ci_high", "hit", "avg_hour", "hrs_to_end", "regime_join"],
            regime_rows,
        ),
        "",
        table(
            ["intraday_state", "rows", "dates", "cities", "cost", "pnl", "roi", "ci_low", "ci_high", "hit", "avg_hour", "hrs_to_end", "regime_join"],
            intraday_rows,
        ),
        "",
        "## Interpretation",
        "",
        "- `first_trigger` remains the wrong entry model. It is the signal-formation phase, not the confirmed forecast-relative-value phase.",
        "- The useful improvement is not a city filter. It is entry timing: use the latest forecast-bounded range before an explicit local cutoff, while preserving enough lead time for this to remain a forecast strategy.",
        "- Stability and mid-cost filters did not improve this sample. They reduce support and erase much of the latest-entry edge, so they should remain diagnostics rather than candidate gates.",
        "- METAR/regime labels explain weather state, but they are closer to real-time current-bracket logic. For Range RV they should be soft diagnostics unless future shadow data proves they add forward excess.",
        "- Cost and model-mass buckets are calibration diagnostics. If high model mass does not map to high hit rate inside a timing policy, the fix is probability calibration, not wider threshold search.",
        "",
        "## Current V2 Candidate",
        "",
        f"`{payload['focus_policy']}` is the current operational candidate to keep shadowing, not to trade live. It means one entry per city/event/source/model: take the latest valid Range RV signal no later than local 18:59 on the target date.",
        "",
        f"Focus summary: rows `{focus['settled_rows']}`, dates `{focus['event_dates']}`, ROI `{pct(focus['roi'])}`, CI `{pct(focus['roi_ci_low'])}` to `{pct(focus['roi_ci_high'])}`, hit `{pct(focus['hit_rate'])}`.",
        "",
        "significance=FAIL, baseline=FAIL, forward=FAIL, conclusion=inconclusive/shadow_only",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    journal_path = Path(args.journal)
    pm_dir = Path(args.pm_history_dir)
    regime = load_regime_rows(Path(args.regime_csv))
    raw_rows = load_jsonl(journal_path)
    enriched = [enrich_row(row, pm_dir, regime) for row in raw_rows]
    sequenced = add_sequence_features(enriched)
    selected, descriptions = apply_policies(sequenced)
    policy_rows = policy_summary(selected, descriptions)
    focus_policy = "latest_before_local_18"
    focus_rows = [row for row in selected if row["policy"] == focus_policy and row.get("pnl") is not None]
    focus_summary = next(row for row in policy_rows if row["policy"] == focus_policy)
    split_policies = list(dict.fromkeys(["first_trigger", "latest_trigger", "latest_before_local_18", focus_policy]))
    split = split_summary(selected, split_policies)
    calibration = calibration_summary(focus_rows)
    for row in calibration:
        row["roi_ci_low"], row["roi_ci_high"] = block_bootstrap_ci(
            [item for item in focus_rows if (
                (row["bucket"] == "model_mass_lt075" and item["model_mass"] < 0.75)
                or (row["bucket"] == "model_mass_075_085" and 0.75 <= item["model_mass"] < 0.85)
                or (row["bucket"] == "model_mass_085_095" and 0.85 <= item["model_mass"] < 0.95)
                or (row["bucket"] == "model_mass_ge095" and item["model_mass"] >= 0.95)
                or (row["bucket"] == "cost_le050" and item["eff_cost"] <= 0.50)
                or (row["bucket"] == "cost_050_080" and 0.50 < item["eff_cost"] <= 0.80)
                or (row["bucket"] == "cost_gt080" and item["eff_cost"] > 0.80)
            )],
            n=10000,
        )

    regime_summary = grouped_summary(selected, focus_policy, "day_regime")
    intraday_summary = grouped_summary(selected, focus_policy, "intraday_state")
    for rows in [regime_summary, intraday_summary]:
        for row in rows:
            field = "day_regime" if "day_regime" in row else "intraday_state"
            ci_low, ci_high = block_bootstrap_ci(
                [item for item in focus_rows if str(item.get(field)) == str(row[field])],
                n=10000,
            )
            row["roi_ci_low"] = ci_low
            row["roi_ci_high"] = ci_high

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "strategy_id": STRATEGY_ID,
        "parameters": {
            "journal": str(journal_path),
            "pm_history_dir": str(pm_dir),
            "regime_csv": str(args.regime_csv),
            "settled_yes_threshold": SETTLED_YES_THRESHOLD,
            "timezone_mapping": CITY_TZ,
        },
        "self_check": mandatory_self_check(Path(args.db_path)),
        "journal": {
            "rows": len(raw_rows),
            "all_no_order_placed": all(row.get("no_order_placed") is True for row in raw_rows),
            "all_zero_notional_shadow": all(row.get("execution_mode") == "zero_notional_shadow" for row in raw_rows),
            "min_snapshot_ts_utc": min((str(row.get("snapshot_ts_utc")) for row in raw_rows), default=None),
            "max_snapshot_ts_utc": max((str(row.get("snapshot_ts_utc")) for row in raw_rows), default=None),
            "event_dates": sorted({str(row.get("event_date")) for row in raw_rows}),
            "source_buckets": dict(Counter(str(row.get("source_bucket")) for row in raw_rows)),
            "settlement_status": dict(Counter(row["settlement_eval_status"] for row in enriched)),
        },
        "policy_descriptions": descriptions,
        "selected_policy_rows": len(selected),
        "candidate_policy_count": len(descriptions),
        "policy_summary": policy_rows,
        "focus_policy": focus_policy,
        "focus_policy_summary": focus_summary,
        "split_summary": split,
        "calibration_summary": calibration,
        "regime_summary": regime_summary,
        "intraday_summary": intraday_summary,
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    out_md.write_text(render_md(payload))
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "focus": focus_summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
