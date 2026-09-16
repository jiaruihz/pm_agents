#!/usr/bin/env python3
"""Diagnose why Range RV forward shadow diverged from replay expectation."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


TARGET_METRIC = "forecast_bounded_range_rv_forward_diagnostic_v0"
STRATEGY_ID = "forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0"
ALGORITHM = "forecast_bounded_w3_cheaper"
EDGE_THRESHOLD = 0.02
SETTLED_YES_THRESHOLD = 0.99
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-17-range-rv-forward-diagnostic-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-17-range-rv-forward-diagnostic-v0.md"


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


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def winner_for(pm_dir: Path, city: str, event_date: str) -> tuple[str | None, str]:
    path = pm_dir / f"{city}_{event_date}.json"
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


def dedup(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = decision_key(row)
        if key not in by_key:
            by_key[key] = row
            continue
        if mode == "latest" and str(row["snapshot_ts_utc"]) > str(by_key[key]["snapshot_ts_utc"]):
            by_key[key] = row
        elif mode == "first" and str(row["snapshot_ts_utc"]) < str(by_key[key]["snapshot_ts_utc"]):
            by_key[key] = row
    return list(by_key.values())


def eval_forward(row: dict[str, Any], pm_dir: Path) -> dict[str, Any]:
    winner, status = winner_for(pm_dir, str(row["city"]), str(row["event_date"]))
    legs = row.get("legs") or []
    gross_cost = sum(float(leg["best_ask"]) for leg in legs if leg.get("best_ask") is not None)
    eff_cost = float(row["effective_range_cost"])
    inside = set(map(str, row.get("inside_brackets") or []))
    pnl = None
    hit = None
    if status == "settled":
        hit = winner in inside
        if row["expression"] == "inside_yes":
            pnl = (1.0 if hit else 0.0) - gross_cost
        else:
            outside = {str(leg.get("bracket")) for leg in legs}
            payout = (len(legs) - 1.0) if winner in outside else float(len(legs))
            pnl = payout - gross_cost
    return {
        "source": "forward_shadow",
        "city": row["city"],
        "event_date": row["event_date"],
        "forecast_source": row.get("forecast_source"),
        "model_version": row["model_version"],
        "snapshot_ts_utc": row["snapshot_ts_utc"],
        "algorithm": row["algorithm"],
        "expression": row["expression"],
        "range_model_mass_norm": float(row["range_model_mass_norm"]),
        "effective_range_cost": eff_cost,
        "gross_cost": gross_cost,
        "orderbook_native_edge": float(row["orderbook_edge"]),
        "pnl": pnl,
        "hit": hit,
        "winner": winner,
        "settlement_status": status,
        "min_leg_ask_size": safe_float(row.get("min_leg_ask_size")),
        "inside_brackets": row.get("inside_brackets"),
    }


def eval_historical(row: dict[str, Any], split_name: str) -> dict[str, Any]:
    return {
        "source": f"historical_{split_name}",
        "city": row["city"],
        "event_date": row["event_date"],
        "forecast_source": row.get("forecast_source"),
        "model_version": row["model_version"],
        "snapshot_ts_utc": row["decision_snapshot_ts_utc"],
        "algorithm": row["algorithm"],
        "expression": row["expression"],
        "range_model_mass_norm": float(row["range_model_mass_norm"]),
        "effective_range_cost": float(row["orderbook_effective_range_cost"]),
        "gross_cost": float(row["orderbook_taker_cost"]),
        "orderbook_native_edge": float(row["orderbook_native_edge"]),
        "pnl": float(row["orderbook_taker_pnl"]),
        "hit": bool(float(row["orderbook_taker_pnl"]) > 0.0),
        "winner": None,
        "settlement_status": "settled",
        "min_leg_ask_size": safe_float(row.get("min_ask_size")),
        "inside_brackets": row.get("components", {}).get("inside_brackets"),
    }


def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("pnl") is not None]
    cost = sum(float(row["gross_cost"]) for row in settled)
    eff = sum(float(row["effective_range_cost"]) for row in settled)
    pnl = sum(float(row["pnl"]) for row in settled)
    wins = [row for row in settled if float(row["pnl"]) > 0.0]
    losses = [row for row in settled if float(row["pnl"]) <= 0.0]
    edge_values = [float(row["orderbook_native_edge"]) for row in settled]
    cost_values = [float(row["gross_cost"]) for row in settled]
    eff_values = [float(row["effective_range_cost"]) for row in settled]
    mass_values = [float(row["range_model_mass_norm"]) for row in settled]
    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "event_dates": len({row["event_date"] for row in settled}),
        "cities": len({row["city"] for row in settled}),
        "gross_cost": cost,
        "effective_cost": eff,
        "pnl": pnl,
        "gross_roi": None if cost <= 0 else pnl / cost,
        "effective_roi": None if eff <= 0 else pnl / eff,
        "win_rate": None if not settled else len(wins) / len(settled),
        "avg_win": None if not wins else sum(float(row["pnl"]) for row in wins) / len(wins),
        "avg_loss": None if not losses else sum(float(row["pnl"]) for row in losses) / len(losses),
        "avg_gross_cost": None if not cost_values else float(np.mean(cost_values)),
        "avg_effective_cost": None if not eff_values else float(np.mean(eff_values)),
        "median_effective_cost": None if not eff_values else float(np.median(eff_values)),
        "avg_edge": None if not edge_values else float(np.mean(edge_values)),
        "median_edge": None if not edge_values else float(np.median(edge_values)),
        "avg_model_mass": None if not mass_values else float(np.mean(mass_values)),
        "median_model_mass": None if not mass_values else float(np.median(mass_values)),
    }


def group_agg(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field))].append(row)
    out = []
    for key, vals in grouped.items():
        item = agg(vals)
        item[field] = key
        out.append(item)
    return sorted(out, key=lambda item: (item["event_dates"], item["settled_rows"]), reverse=True)


def bucket(value: float, cuts: list[float]) -> str:
    prev = None
    for cut in cuts:
        if value <= cut:
            return f"<= {cut:.2f}" if prev is None else f"({prev:.2f}, {cut:.2f}]"
        prev = cut
    return f"> {cuts[-1]:.2f}"


def bucket_agg(rows: list[dict[str, Any]], field: str, cuts: list[float]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[bucket(float(row[field]), cuts)].append(row)
    out = []
    for key, vals in grouped.items():
        item = agg(vals)
        item["bucket"] = key
        out.append(item)
    return sorted(out, key=lambda item: item["bucket"])


def compact(item: dict[str, Any]) -> list[Any]:
    return [
        item["settled_rows"],
        item["event_dates"],
        item["cities"],
        f"{item['gross_cost']:.3f}",
        f"{item['pnl']:+.3f}",
        pct(item["gross_roi"]),
        pct(item["effective_roi"]),
        pct(item["win_rate"]),
        f"{item['avg_win']:+.3f}" if item["avg_win"] is not None else "NA",
        f"{item['avg_loss']:+.3f}" if item["avg_loss"] is not None else "NA",
        f"{item['avg_effective_cost']:.3f}" if item["avg_effective_cost"] is not None else "NA",
        f"{item['avg_edge']:.3f}" if item["avg_edge"] is not None else "NA",
    ]


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
    selected = [
        row
        for row in orderbook_rows
        if row["algorithm"] == ALGORITHM
        and row.get("source_bucket") == "default_wu"
        and float(row.get("orderbook_native_edge") or -999.0) >= EDGE_THRESHOLD
    ]
    strict_capacity = [row for row in selected if row.get("min_ask_size") is not None and float(row["min_ask_size"]) >= 5.0]
    out = []
    for row in selected:
        split_name = "train" if str(row["event_date"]) in train_dates else "holdout" if str(row["event_date"]) in holdout_dates else "other"
        out.append(eval_historical(row, split_name))
    out_capacity = []
    for row in strict_capacity:
        split_name = "train" if str(row["event_date"]) in train_dates else "holdout" if str(row["event_date"]) in holdout_dates else "other"
        item = eval_historical(row, split_name)
        item["source"] = f"{item['source']}_capacity5"
        out_capacity.append(item)
    meta = {
        "split": split,
        "orderbook_coverage": coverage,
        "selected_rows": len(selected),
        "capacity5_rows": len(strict_capacity),
    }
    return out + out_capacity, meta


def render_md(payload: dict[str, Any]) -> str:
    summary_rows = [
        [name, *compact(item)]
        for name, item in payload["summary"].items()
    ]
    expr_rows = [
        [row["cohort"], row["expression"], *compact(row)]
        for row in payload["by_expression"]
    ]
    cost_rows = [
        [row["cohort"], row["bucket"], *compact(row)]
        for row in payload["by_effective_cost_bucket"]
    ]
    lines = [
        "# Range RV Forward Diagnostic v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> strategy_id: `{STRATEGY_ID}`",
        "",
        "## Question",
        "",
        "Training/replay showed a positive default-WU `forecast_bounded_w3_cheaper` candidate, but the first two settled forward shadow days were negative. This report compares the historical replay rows and forward shadow rows on fixed, pre-existing dimensions instead of searching for a new winner.",
        "",
        "## Cohort Summary",
        "",
        table(
            [
                "cohort",
                "rows",
                "dates",
                "cities",
                "gross_cost",
                "pnl",
                "gross_roi",
                "effective_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff_cost",
                "avg_edge",
            ],
            summary_rows,
        ),
        "",
        "## Expression Split",
        "",
        table(
            [
                "cohort",
                "expression",
                "rows",
                "dates",
                "cities",
                "gross_cost",
                "pnl",
                "gross_roi",
                "effective_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff_cost",
                "avg_edge",
            ],
            expr_rows,
        ),
        "",
        "## Effective-Cost Buckets",
        "",
        table(
            [
                "cohort",
                "bucket",
                "rows",
                "dates",
                "cities",
                "gross_cost",
                "pnl",
                "gross_roi",
                "effective_roi",
                "win_rate",
                "avg_win",
                "avg_loss",
                "avg_eff_cost",
                "avg_edge",
            ],
            cost_rows,
        ),
        "",
        "## Diagnosis",
        "",
        "- Forward underperformance is not explained by one bad raw snapshot; it appears after deduping to `city + event_date + forecast_source + model_version`.",
        "- The first two complete forward days are below the historical holdout expectation, but still too few for a final statistical verdict.",
        "- The biggest process mismatch is expression mix. Historical holdout was mostly `inside_yes` (`31/36` rows), while forward latest is much more `outside_no` (`20/34` rows). Historical `outside_no` was already weak (`-2.7%` gross ROI in holdout), so letting `cheaper` route more forward rows into `outside_no` is a plausible failure mode.",
        "- The very-low effective-cost bucket (`<=0.25`) is not a hidden gem: it was negative in historical train/holdout and is also negative in forward. This points to model overconfidence or stale/misaligned forecast distribution when the market prices the range as very unlikely.",
        "- Historical winners depended on high model mass and high hit rate. Forward model mass is still high enough to pass edge filters, but hit rate and payoff asymmetry are worse. That is a calibration problem, not just an execution threshold problem.",
        "- The most plausible improvement areas are probability calibration, timing/persistence, cost floors/caps, and expression selection. Do not tune city-specific filters from these two days.",
        "",
        "## Fixed Next Tests",
        "",
        "1. Compare first/latest/persistence>=2 under the same dedup grain.",
        "2. Add pre-registered `effective_cost <= 0.75` and `<= 0.80` diagnostics.",
        "3. Re-estimate model range mass calibration by bucket; if high mass buckets miss too often, fix the forecast distribution before changing basket expression.",
        "4. Keep `inside_yes` and `outside_no` separate until both have enough forward dates.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    journal_path = Path(args.journal)
    pm_dir = Path(args.pm_history_dir)
    historical, hist_meta = build_historical(db_path, args.orderbook_glob)
    forward_raw = [eval_forward(row, pm_dir) for row in load_jsonl(journal_path)]
    forward_settled = [row for row in forward_raw if row["settlement_status"] == "settled"]
    forward_latest = dedup(forward_settled, "latest")
    forward_first = dedup(forward_settled, "first")

    cohorts = {
        "historical_train": [row for row in historical if row["source"] == "historical_train"],
        "historical_holdout": [row for row in historical if row["source"] == "historical_holdout"],
        "historical_train_capacity5": [row for row in historical if row["source"] == "historical_train_capacity5"],
        "historical_holdout_capacity5": [row for row in historical if row["source"] == "historical_holdout_capacity5"],
        "forward_first_settled": forward_first,
        "forward_latest_settled": forward_latest,
        "forward_latest_2026_06_15_16": [
            row for row in forward_latest if row["event_date"] in {"2026-06-15", "2026-06-16"}
        ],
    }
    summary = {name: agg(rows) for name, rows in cohorts.items()}
    by_expression = []
    for name, rows in cohorts.items():
        for item in group_agg(rows, "expression"):
            by_expression.append({"cohort": name, **item})
    by_cost = []
    for name, rows in cohorts.items():
        for item in bucket_agg(rows, "effective_range_cost", [0.25, 0.50, 0.75, 0.90, 0.95]):
            by_cost.append({"cohort": name, **item})

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "strategy_id": STRATEGY_ID,
        "parameters": {
            "algorithm": ALGORITHM,
            "edge_threshold": EDGE_THRESHOLD,
            "settled_yes_threshold": SETTLED_YES_THRESHOLD,
            "journal": str(journal_path),
            "pm_history_dir": str(pm_dir),
            "orderbook_glob": args.orderbook_glob,
        },
        "historical_meta": hist_meta,
        "forward_status": {
            "raw_rows": len(forward_raw),
            "settled_rows": len(forward_settled),
            "settled_event_dates": sorted({row["event_date"] for row in forward_settled}),
            "unsettled_status": dict(Counter(row["settlement_status"] for row in forward_raw if row["settlement_status"] != "settled")),
        },
        "summary": summary,
        "by_expression": by_expression,
        "by_effective_cost_bucket": by_cost,
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    out_md.write_text(render_md(payload))
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "forward_status": payload["forward_status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
