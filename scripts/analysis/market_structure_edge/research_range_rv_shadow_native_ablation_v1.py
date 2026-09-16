#!/usr/bin/env python3
"""Shadow-native ablation for forecast-bounded Range RV.

The first Range RV forward shadow days diverged from the historical replay.
This script keeps the strategy family fixed and tests only pre-registered
process mismatches: expression routing, cost bands, and first/latest/persistent
snapshot selection at the same city/event/source/model grain used by the
zero-notional shadow runner.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from scripts.analysis.forecast_quality import research_forecast_quality_base_v0 as fq_base  # noqa: E402
from scripts.analysis.forecast_quality import research_forecast_quality_source_adjusted_v0 as fq_source  # noqa: E402
from scripts.analysis.observed_max import research_settlement_source_registry_v0 as source_registry  # noqa: E402

import research_forecast_bounded_range_rv_live_standard_v1 as live_v1  # noqa: E402
import research_forecast_bounded_range_rv_source_aware_v0 as source_v0  # noqa: E402


TARGET_METRIC = "forecast_bounded_range_rv_shadow_native_ablation_v1"
STRATEGY_ID = "forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0"
CHEAPER_ALGO = "forecast_bounded_w3_cheaper"
INSIDE_ALGO = "forecast_bounded_w3_inside_yes"
EDGE_THRESHOLD = 0.02
SETTLED_YES_THRESHOLD = 0.99
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-17-range-rv-shadow-native-ablation-v1.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-17-range-rv-shadow-native-ablation-v1.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument(
        "--journal",
        default=str(ROOT / "runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl"),
    )
    parser.add_argument("--pm-history-dir", default=str(ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    parser.add_argument("--orderbook-glob", default=str(live_v1.scanner.ORDERBOOK_GLOB_DEFAULT))
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


def key5(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (*decision_key(row), str(row.get("decision_snapshot_ts_utc") or row.get("snapshot_ts_utc") or ""))


def dedup(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode not in {"first", "latest"}:
        raise ValueError(mode)
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = decision_key(row)
        ts = str(row["snapshot_ts_utc"])
        if key not in by_key:
            by_key[key] = row
            continue
        old_ts = str(by_key[key]["snapshot_ts_utc"])
        if mode == "first" and ts < old_ts:
            by_key[key] = row
        if mode == "latest" and ts > old_ts:
            by_key[key] = row
    return list(by_key.values())


def persistence_latest(rows: list[dict[str, Any]], min_triggers: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[decision_key(row)].append(row)
    out = []
    for group in grouped.values():
        snapshots = {str(row["snapshot_ts_utc"]) for row in group}
        if len(snapshots) >= min_triggers:
            out.append(max(group, key=lambda row: str(row["snapshot_ts_utc"])))
    return out


def eval_forward_row(row: dict[str, Any], pm_dir: Path, expression_mode: str) -> dict[str, Any]:
    winner, status = winner_for(pm_dir, str(row["city"]), str(row["event_date"]))
    inside = set(map(str, row.get("inside_brackets") or []))
    hit = winner in inside if status == "settled" else None
    model_mass = float(row["range_model_mass_norm"])

    if expression_mode == "current_cheaper":
        expression = str(row["expression"])
        gross_cost = sum(float(leg["best_ask"]) for leg in row.get("legs") or [] if leg.get("best_ask") is not None)
        effective_cost = float(row["effective_range_cost"])
        edge = float(row["orderbook_edge"])
        if status != "settled":
            pnl = None
        elif expression == "inside_yes":
            pnl = (1.0 if hit else 0.0) - gross_cost
        else:
            outside = {str(leg.get("bracket")) for leg in row.get("legs") or []}
            payout = (len(outside) - 1.0) if winner in outside else float(len(outside))
            pnl = payout - gross_cost
    elif expression_mode == "inside_yes_counterfactual":
        expression = "inside_yes_counterfactual"
        cost = safe_float(row.get("inside_yes_cost"))
        if cost is None:
            raise ValueError("forward row missing inside_yes_cost")
        gross_cost = cost
        effective_cost = cost
        edge = model_mass - cost
        pnl = None if status != "settled" else (1.0 if hit else 0.0) - cost
    else:
        raise ValueError(expression_mode)

    return {
        "source": "forward_shadow",
        "city": row["city"],
        "event_date": row["event_date"],
        "forecast_source": row.get("forecast_source"),
        "model_version": row["model_version"],
        "snapshot_ts_utc": row["snapshot_ts_utc"],
        "source_bucket": row.get("source_bucket"),
        "source_sensitive": row.get("source_bucket") != "default_wu",
        "forecast_quality_low": None,
        "algorithm": row["algorithm"],
        "expression": expression,
        "winner": winner,
        "settlement_status": status,
        "range_model_mass_norm": model_mass,
        "effective_range_cost": effective_cost,
        "gross_cost": gross_cost,
        "orderbook_native_edge": edge,
        "min_leg_ask_size": safe_float(row.get("min_leg_ask_size")),
        "pnl": pnl,
        "hit": hit,
        "inside_brackets": row.get("inside_brackets"),
    }


def eval_historical_row(row: dict[str, Any], split_name: str, family: str) -> dict[str, Any]:
    return {
        "source": f"historical_{split_name}",
        "family": family,
        "city": row["city"],
        "event_date": row["event_date"],
        "forecast_source": row.get("forecast_source"),
        "model_version": row["model_version"],
        "snapshot_ts_utc": row["decision_snapshot_ts_utc"],
        "source_bucket": row.get("source_bucket"),
        "source_sensitive": bool(row.get("source_sensitive")),
        "forecast_quality_low": bool(row.get("forecast_quality_low")),
        "algorithm": row["algorithm"],
        "expression": row["expression"],
        "winner": None,
        "settlement_status": "settled",
        "range_model_mass_norm": float(row["range_model_mass_norm"]),
        "effective_range_cost": float(row["orderbook_effective_range_cost"]),
        "gross_cost": float(row["orderbook_taker_cost"]),
        "orderbook_native_edge": float(row["orderbook_native_edge"]),
        "min_leg_ask_size": safe_float(row.get("min_ask_size")),
        "pnl": float(row["orderbook_taker_pnl"]),
        "hit": bool(float(row["orderbook_taker_pnl"]) > 0.0),
        "inside_brackets": row.get("components", {}).get("inside_brackets"),
    }


def build_historical(db_path: Path, orderbook_glob: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conn = fq_base.connect_ro(db_path)
    try:
        candidates = fq_base.load_candidates(conn)
        city_counts = source_registry.fact_city_counts(conn)
    finally:
        conn.close()

    registry = source_registry.add_fact_counts(source_registry.build_registry(), city_counts)
    decision_sets = fq_base.build_decision_sets(candidates)
    train_dates, holdout_dates, split = fq_base.split_dates(decision_sets)
    decision_sets = fq_base.add_historical_features(fq_base.add_cross_model_features(decision_sets))
    decision_sets, _thresholds = fq_base.add_quality_labels(decision_sets, train_dates)
    decision_sets = fq_source.attach_source_registry(decision_sets, registry)
    strategy_rows = source_v0.generate_rows(decision_sets)
    coverage = live_v1.attach_orderbook_native(strategy_rows, orderbook_glob)
    orderbook_rows = live_v1.metric_rows(strategy_rows)

    cheaper_selected = [
        row
        for row in orderbook_rows
        if row["algorithm"] == CHEAPER_ALGO
        and row.get("source_bucket") == "default_wu"
        and float(row.get("orderbook_native_edge") or -999.0) >= EDGE_THRESHOLD
    ]
    selected_keys = {key5(row) for row in cheaper_selected}
    inside_same_decision = [
        row
        for row in orderbook_rows
        if row["algorithm"] == INSIDE_ALGO and row.get("source_bucket") == "default_wu" and key5(row) in selected_keys
    ]
    inside_native = [
        row
        for row in orderbook_rows
        if row["algorithm"] == INSIDE_ALGO
        and row.get("source_bucket") == "default_wu"
        and float(row.get("orderbook_native_edge") or -999.0) >= EDGE_THRESHOLD
    ]

    out: list[dict[str, Any]] = []
    for family, rows in [
        ("current_cheaper", cheaper_selected),
        ("inside_yes_same_decisions", inside_same_decision),
        ("inside_yes_native_edge002", inside_native),
    ]:
        for row in rows:
            event_date = str(row["event_date"])
            split_name = "train" if event_date in train_dates else "holdout" if event_date in holdout_dates else "other"
            out.append(eval_historical_row(row, split_name, family))

    scope_rows = []
    for label, filt in [
        ("no_filter_all_sources", lambda r: True),
        ("exclude_forecast_quality_low", lambda r: not bool(r.get("forecast_quality_low"))),
        ("source_bucket_default_wu", lambda r: r.get("source_bucket") == "default_wu"),
    ]:
        rows = [
            row
            for row in orderbook_rows
            if row["algorithm"] == CHEAPER_ALGO
            and float(row.get("orderbook_native_edge") or -999.0) >= EDGE_THRESHOLD
            and filt(row)
        ]
        scope_rows.append({"scope": label, **agg([eval_historical_row(row, "all", "scope_check") for row in rows])})

    meta = {
        "split": split,
        "orderbook_coverage": coverage,
        "cheaper_selected_rows": len(cheaper_selected),
        "inside_same_decision_rows": len(inside_same_decision),
        "inside_native_rows": len(inside_native),
        "source_quality_scope": scope_rows,
    }
    return out, meta


def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("pnl") is not None]
    gross_cost = sum(float(row["gross_cost"]) for row in settled)
    effective_cost = sum(float(row["effective_range_cost"]) for row in settled)
    pnl = sum(float(row["pnl"]) for row in settled)
    wins = [row for row in settled if float(row["pnl"]) > 0.0]
    losses = [row for row in settled if float(row["pnl"]) <= 0.0]
    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "decision_groups": len({decision_key(row) for row in settled}),
        "event_dates": len({row["event_date"] for row in settled}),
        "cities": len({row["city"] for row in settled}),
        "gross_cost": gross_cost,
        "effective_cost": effective_cost,
        "pnl": pnl,
        "gross_roi": None if gross_cost <= 0 else pnl / gross_cost,
        "effective_roi": None if effective_cost <= 0 else pnl / effective_cost,
        "win_rate": None if not settled else len(wins) / len(settled),
        "avg_win": None if not wins else sum(float(row["pnl"]) for row in wins) / len(wins),
        "avg_loss": None if not losses else sum(float(row["pnl"]) for row in losses) / len(losses),
        "avg_effective_cost": None if not settled else float(np.mean([float(row["effective_range_cost"]) for row in settled])),
        "avg_edge": None if not settled else float(np.mean([float(row["orderbook_native_edge"]) for row in settled])),
        "avg_model_mass": None if not settled else float(np.mean([float(row["range_model_mass_norm"]) for row in settled])),
    }


def compact(row: dict[str, Any]) -> list[Any]:
    return [
        row["settled_rows"],
        row["decision_groups"],
        row["event_dates"],
        row["cities"],
        f"{row['gross_cost']:.3f}",
        f"{row['effective_cost']:.3f}",
        f"{row['pnl']:+.3f}",
        pct(row["gross_roi"]),
        pct(row["effective_roi"]),
        pct(row["win_rate"]),
        f"{row['avg_win']:+.3f}" if row["avg_win"] is not None else "NA",
        f"{row['avg_loss']:+.3f}" if row["avg_loss"] is not None else "NA",
        f"{row['avg_effective_cost']:.3f}" if row["avg_effective_cost"] is not None else "NA",
        f"{row['avg_edge']:.3f}" if row["avg_edge"] is not None else "NA",
    ]


def cost_between(lo: float | None, hi: float | None) -> Callable[[dict[str, Any]], bool]:
    def inner(row: dict[str, Any]) -> bool:
        value = float(row["effective_range_cost"])
        if lo is not None and value <= lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    return inner


def field_equals(field: str, value: str) -> Callable[[dict[str, Any]], bool]:
    return lambda row: str(row.get(field)) == value


def all_pass(_row: dict[str, Any]) -> bool:
    return True


VARIANTS: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
    ("current_all", "current_cheaper", all_pass),
    ("current_no_low_cost_gt025", "current_cheaper", cost_between(0.25, None)),
    ("current_mid_cost_050_075", "current_cheaper", cost_between(0.50, 0.75)),
    ("current_cap075_gt025", "current_cheaper", cost_between(0.25, 0.75)),
    ("current_cap080_gt025", "current_cheaper", cost_between(0.25, 0.80)),
    ("current_inside_expression_only", "current_cheaper", field_equals("expression", "inside_yes")),
    ("current_outside_expression_only_diag", "current_cheaper", field_equals("expression", "outside_no")),
    ("inside_same_decisions_all", "inside_yes_same_decisions", all_pass),
    ("inside_native_edge002", "inside_yes_native_edge002", all_pass),
    ("inside_native_edge002_mid_cost_050_075", "inside_yes_native_edge002", cost_between(0.50, 0.75)),
    ("inside_native_edge002_cap080_gt025", "inside_yes_native_edge002", cost_between(0.25, 0.80)),
]


def summarize_variants(rows: list[dict[str, Any]], cohort: str, dedup_mode: str | None = None) -> list[dict[str, Any]]:
    out = []
    for name, family, filt in VARIANTS:
        selected = [row for row in rows if row.get("family") == family and filt(row)]
        if dedup_mode == "first":
            selected = dedup(selected, "first")
        elif dedup_mode == "latest":
            selected = dedup(selected, "latest")
        elif dedup_mode == "persistence2_latest":
            selected = persistence_latest(selected, 2)
        elif dedup_mode == "persistence3_latest":
            selected = persistence_latest(selected, 3)
        elif dedup_mode is not None:
            raise ValueError(dedup_mode)
        out.append({"cohort": cohort, "dedup_mode": dedup_mode or "none", "variant": name, "family": family, **agg(selected)})
    return out


def forward_rows(journal_path: Path, pm_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = load_jsonl(journal_path)
    current = [eval_forward_row(row, pm_dir, "current_cheaper") for row in raw]
    inside_same = [eval_forward_row(row, pm_dir, "inside_yes_counterfactual") for row in raw]
    inside_native = [
        {**row, "family": "inside_yes_native_edge002"}
        for row in inside_same
        if float(row["orderbook_native_edge"]) >= EDGE_THRESHOLD
    ]
    rows = []
    rows.extend({**row, "family": "current_cheaper"} for row in current)
    rows.extend({**row, "family": "inside_yes_same_decisions"} for row in inside_same)
    rows.extend(inside_native)
    return rows, {
        "journal_rows": len(raw),
        "all_no_order_placed": all(row.get("no_order_placed") is True for row in raw),
        "all_zero_notional_shadow": all(row.get("execution_mode") == "zero_notional_shadow" for row in raw),
        "event_dates": sorted({str(row.get("event_date")) for row in raw}),
        "source_buckets": dict(Counter(str(row.get("source_bucket")) for row in raw)),
        "settlement_status": dict(Counter(row["settlement_status"] for row in current)),
        "min_snapshot_ts_utc": min((str(row.get("snapshot_ts_utc")) for row in raw), default=None),
        "max_snapshot_ts_utc": max((str(row.get("snapshot_ts_utc")) for row in raw), default=None),
    }


def render_md(payload: dict[str, Any]) -> str:
    variant_rows = [
        [row["cohort"], row["dedup_mode"], row["variant"], *compact(row)]
        for row in payload["variant_summary"]
    ]
    forward_timing_rows = [
        [row["dedup_mode"], row["variant"], *compact(row)]
        for row in payload["forward_timing_summary"]
    ]
    scope_rows = [
        [row["scope"], *compact(row)]
        for row in payload["historical_meta"]["source_quality_scope"]
    ]
    lines = [
        "# Range RV Shadow-Native Ablation v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> strategy_id: `{STRATEGY_ID}`",
        "",
        "## Data Snapshot",
        "",
        "- Data source: `runtime/weather.db` for historical decision-set replay; synced N100 zero-notional shadow journal plus `pm_history` for forward shadow telemetry.",
        "- Evidence layer: historical orderbook-native replay + shadow/counterfactual telemetry. This is not live PnL and not a live edge claim.",
        f"- journal_rows: `{payload['forward_meta']['journal_rows']}`; event_dates: `{', '.join(payload['forward_meta']['event_dates'])}`.",
        f"- shadow source_buckets: `{json.dumps(payload['forward_meta']['source_buckets'], sort_keys=True)}`.",
        f"- all_no_order_placed: `{payload['forward_meta']['all_no_order_placed']}`; all_zero_notional_shadow: `{payload['forward_meta']['all_zero_notional_shadow']}`.",
        "",
        "### Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["self_check"], indent=2, sort_keys=True),
        "```",
        "",
        "## Source/Quality Required Lines",
        "",
        "These are historical `forecast_bounded_w3_cheaper` rows with `orderbook_edge>=0.02`, shown to keep the source-aware base visible. Forward shadow currently writes only `default_wu/no_filter`, so forecast-quality-low is not available in the journal yet.",
        "",
        table(
            [
                "scope",
                "rows",
                "groups",
                "dates",
                "cities",
                "gross_cost",
                "eff_cost",
                "pnl",
                "gross_roi",
                "eff_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff",
                "avg_edge",
            ],
            scope_rows,
        ),
        "",
        "## Main Variant Summary",
        "",
        "Historical rows use the train/holdout split from the forecast-quality base. Forward rows use the latest row that would have been eligible under each fixed variant at `city + event_date + forecast_source + model_version`; only settled event dates contribute PnL.",
        "",
        table(
            [
                "cohort",
                "dedup",
                "variant",
                "rows",
                "groups",
                "dates",
                "cities",
                "gross_cost",
                "eff_cost",
                "pnl",
                "gross_roi",
                "eff_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff",
                "avg_edge",
            ],
            variant_rows,
        ),
        "",
        "## Forward Timing Sensitivity",
        "",
        table(
            [
                "dedup",
                "variant",
                "rows",
                "groups",
                "dates",
                "cities",
                "gross_cost",
                "eff_cost",
                "pnl",
                "gross_roi",
                "eff_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff",
                "avg_edge",
            ],
            forward_timing_rows,
        ),
        "",
        "## Interpretation",
        "",
        "- A simple expression fix is not enough. Historical outside-NO was weak, but the forward `inside_yes_same_decisions` and `inside_native_edge002` counterfactuals are also negative, so the miss is more likely model/timing/calibration than only the cheaper router.",
        "- Cost bands help but do not solve it. `current_mid_cost_050_075` and `current_cap075_gt025` stay positive in historical train/holdout and reduce the forward loss, yet forward remains below zero. That is a watchlist candidate, not a promotion.",
        "- Persistence is not a free quality filter in this sample; requiring 2-3 repeated triggers mostly worsens the forward result. Repeated shadow appearances can mean stale conviction, not confirmed edge.",
        "- The `inside_yes_same_decisions` rows are counterfactual when the shadow runner chose outside-NO; they use the runner's recorded inside YES cost and settlement truth, but they do not prove live executable capacity for those alternate legs.",
        "- The journal does not yet carry `forecast_quality_low`; if this branch continues, v1 shadow telemetry should write forecast-quality labels at the same decision grain so the required no-filter/exclude-low/default-WU lines can be evaluated forward too.",
        "",
        "## Current Verdict",
        "",
        "`inconclusive / keep shadow only`: there is enough evidence to instrument the next shadow candidate, not enough evidence for paper/live. The cleanest next implementation is a v1 zero-notional runner that records both current-cheaper and inside-YES counterfactual fields, plus forecast-quality labels, while pre-registering cost floor/cap diagnostics instead of city-specific filters.",
        "",
        "significance=FAIL, baseline=FAIL, forward=FAIL, conclusion=inconclusive",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    journal_path = Path(args.journal)
    pm_dir = Path(args.pm_history_dir)

    self_check = mandatory_self_check(db_path)
    historical, historical_meta = build_historical(db_path, args.orderbook_glob)
    forward, forward_meta = forward_rows(journal_path, pm_dir)
    forward_settled = [row for row in forward if row["settlement_status"] == "settled"]

    variant_summary = []
    variant_summary.extend(
        summarize_variants([row for row in historical if row["source"] == "historical_train"], "historical_train")
    )
    variant_summary.extend(
        summarize_variants([row for row in historical if row["source"] == "historical_holdout"], "historical_holdout")
    )
    variant_summary.extend(summarize_variants(forward_settled, "forward_settled", "latest"))

    forward_timing = []
    for mode in ["first", "latest", "persistence2_latest", "persistence3_latest"]:
        forward_timing.extend(summarize_variants(forward_settled, "forward_settled", mode))

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "strategy_id": STRATEGY_ID,
        "parameters": {
            "edge_threshold": EDGE_THRESHOLD,
            "settled_yes_threshold": SETTLED_YES_THRESHOLD,
            "db_path": str(db_path),
            "journal": str(journal_path),
            "pm_history_dir": str(pm_dir),
            "orderbook_glob": args.orderbook_glob,
        },
        "self_check": self_check,
        "historical_meta": historical_meta,
        "forward_meta": forward_meta,
        "variant_summary": variant_summary,
        "forward_timing_summary": forward_timing,
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    out_md.write_text(render_md(payload))
    print(
        json.dumps(
            {
                "out_json": str(out_json),
                "out_md": str(out_md),
                "forward_meta": forward_meta,
                "variant_rows": len(variant_summary),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
