#!/usr/bin/env python3
"""Model-free market-structure test for weather temperature brackets.

This is the Step 1 diagnostic for H_B:

    Does the market YES price systematically differ from realized YES frequency,
    and can a frozen model-free BUY_NO price-band rule beat an all-band BUY_NO
    baseline on later dates?

The script deliberately reads only fact_signal_candidates. It does not use raw
snapshots or raw settlement JSON, and it de-duplicates side-specific candidate
rows into one market row before estimating price-bucket behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)

DB_DEFAULT = ROOT / "runtime" / "weather.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--bucket-size", type=float, default=0.05)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--min-bin-n", type=int, default=30)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260608)
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def ci(values: list[float]) -> list[float | None]:
    return [percentile(values, 0.025), percentile(values, 0.975)]


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    alpha = 1.0 - confidence
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def price_bucket(price: float, bucket_size: float) -> str:
    low = math.floor(price / bucket_size) * bucket_size
    high = min(1.0, low + bucket_size)
    return f"{low:.2f}-{high:.2f}"


def load_market_rows(conn: sqlite3.Connection, bucket_size: float) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT
          event_date,
          city,
          COALESCE(condition_id, market_id, city || ':' || event_date || ':' || bracket) AS condition_key,
          bracket,
          AVG(market_yes_price) AS market_yes_price,
          AVG(model_p_yes) AS model_p_yes,
          MIN(decision_hours_to_settle) AS decision_hours_to_settle,
          MAX(final_yes) AS final_yes,
          COUNT(*) AS candidate_side_rows,
          MAX(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS has_buy_no_candidate,
          MAX(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS has_buy_yes_candidate
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        GROUP BY event_date, city, condition_key, bracket
        """,
    )
    out: list[dict[str, Any]] = []
    for row in data:
        price = float(row["market_yes_price"])
        final_yes = float(row["final_yes"])
        row["price_bucket"] = price_bucket(price, bucket_size)
        row["buy_no_cost"] = 1.0 - price
        row["buy_no_pnl"] = price - final_yes
        row["buy_yes_cost"] = price
        row["buy_yes_pnl"] = final_yes - price
        out.append(row)
    return out


def group_by_date(data: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        grouped[str(row["event_date"])].append(row)
    return grouped


def bootstrap_by_date(
    data: list[dict[str, Any]],
    metric: Callable[[list[dict[str, Any]]], float | None],
    *,
    iters: int,
    seed: int,
) -> list[float]:
    grouped = group_by_date(data)
    dates = sorted(grouped)
    if not dates:
        return []
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(iters):
        sample: list[dict[str, Any]] = []
        for date in dates:
            sample.extend(grouped[rng.choice(dates)])
        value = metric(sample)
        if value is not None and math.isfinite(value):
            values.append(value)
    return values


def summarize_market_rows(data: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(data)
    actual_yes = sum(float(row["final_yes"]) for row in data)
    implied_yes = sum(float(row["market_yes_price"]) for row in data)
    no_cost = sum(float(row["buy_no_cost"]) for row in data)
    no_pnl = sum(float(row["buy_no_pnl"]) for row in data)
    yes_cost = sum(float(row["buy_yes_cost"]) for row in data)
    yes_pnl = sum(float(row["buy_yes_pnl"]) for row in data)
    return {
        "n": n,
        "active_dates": len({row["event_date"] for row in data}),
        "actual_yes_rate": safe_div(actual_yes, n),
        "mean_market_yes_price": safe_div(implied_yes, n),
        "gap_actual_minus_implied": safe_div(actual_yes - implied_yes, n),
        "buy_no_cost": no_cost,
        "buy_no_pnl": no_pnl,
        "buy_no_roi": safe_div(no_pnl, no_cost),
        "buy_yes_cost": yes_cost,
        "buy_yes_pnl": yes_pnl,
        "buy_yes_roi": safe_div(yes_pnl, yes_cost),
    }


def no_roi(data: list[dict[str, Any]]) -> float | None:
    cost = sum(float(row["buy_no_cost"]) for row in data)
    pnl = sum(float(row["buy_no_pnl"]) for row in data)
    return safe_div(pnl, cost)


def no_roi_delta(selected: set[str]) -> Callable[[list[dict[str, Any]]], float | None]:
    def _metric(data: list[dict[str, Any]]) -> float | None:
        selected_rows = [row for row in data if row["price_bucket"] in selected]
        selected_roi = no_roi(selected_rows)
        baseline_roi = no_roi(data)
        if selected_roi is None or baseline_roi is None:
            return None
        return selected_roi - baseline_roi

    return _metric


def model_lift(data: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in data if row.get("model_p_yes") is not None]
    if len(usable) < 10:
        return {"n": len(usable), "delta_high_minus_low_actual_yes": None}
    median = sorted(float(row["model_p_yes"]) for row in usable)[len(usable) // 2]
    low = [row for row in usable if float(row["model_p_yes"]) <= median]
    high = [row for row in usable if float(row["model_p_yes"]) > median]
    low_rate = safe_div(sum(float(row["final_yes"]) for row in low), len(low))
    high_rate = safe_div(sum(float(row["final_yes"]) for row in high), len(high))
    delta = None if low_rate is None or high_rate is None else high_rate - low_rate
    return {
        "n": len(usable),
        "model_p_yes_median": median,
        "low_n": len(low),
        "high_n": len(high),
        "low_actual_yes_rate": low_rate,
        "high_actual_yes_rate": high_rate,
        "delta_high_minus_low_actual_yes": delta,
    }


def build_bucket_table(
    data: list[dict[str, Any]],
    *,
    bucket_size: float,
    bootstrap_iters: int,
    seed: int,
    min_bin_n: int,
) -> list[dict[str, Any]]:
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        by_bucket[row["price_bucket"]].append(row)
    k_tests = sum(1 for values in by_bucket.values() if len(values) >= min_bin_n)
    bonf_conf = 1.0 - (0.05 / k_tests) if k_tests else 0.95

    table: list[dict[str, Any]] = []
    for bucket in sorted(by_bucket):
        values = by_bucket[bucket]
        summary = summarize_market_rows(values)
        successes = int(sum(float(row["final_yes"]) for row in values))
        wilson_low, wilson_high = wilson_interval(successes, len(values), 0.95)
        bonf_low, bonf_high = wilson_interval(successes, len(values), bonf_conf)
        no_samples = bootstrap_by_date(values, no_roi, iters=bootstrap_iters, seed=seed + len(table))
        lift = model_lift(values)
        row = {
            "price_bucket": bucket,
            **summary,
            "wilson95_actual_yes": [wilson_low, wilson_high],
            "wilson_bonferroni_actual_yes": [bonf_low, bonf_high],
            "bonferroni_tests": k_tests,
            "gap_significant_bonferroni": (
                summary["mean_market_yes_price"] is not None
                and len(values) >= min_bin_n
                and (bonf_high < summary["mean_market_yes_price"] or bonf_low > summary["mean_market_yes_price"])
            ),
            "buy_no_roi_ci95_cluster_by_event_date": ci(no_samples),
            "model_lift": lift,
        }
        table.append(row)
    return table


def train_test_split_by_date(data: list[dict[str, Any]], train_frac: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    dates = sorted({str(row["event_date"]) for row in data})
    if len(dates) < 2:
        return data, [], None
    cut_idx = max(1, min(len(dates) - 1, int(len(dates) * train_frac)))
    train_dates = set(dates[:cut_idx])
    cutoff = dates[cut_idx]
    return (
        [row for row in data if str(row["event_date"]) in train_dates],
        [row for row in data if str(row["event_date"]) not in train_dates],
        cutoff,
    )


def forward_test(
    data: list[dict[str, Any]],
    *,
    train_frac: float,
    bucket_size: float,
    bootstrap_iters: int,
    seed: int,
    min_bin_n: int,
) -> dict[str, Any]:
    train, test, cutoff = train_test_split_by_date(data, train_frac)
    train_buckets = build_bucket_table(
        train,
        bucket_size=bucket_size,
        bootstrap_iters=max(100, bootstrap_iters // 4),
        seed=seed + 1000,
        min_bin_n=min_bin_n,
    )
    selected = {
        row["price_bucket"]
        for row in train_buckets
        if row["n"] >= min_bin_n
        and (row["buy_no_roi"] or 0.0) > 0.0
        and (row["gap_actual_minus_implied"] or 0.0) < 0.0
    }
    selected_test = [row for row in test if row["price_bucket"] in selected]
    selected_samples = bootstrap_by_date(selected_test, no_roi, iters=bootstrap_iters, seed=seed + 2000)
    baseline_samples = bootstrap_by_date(test, no_roi, iters=bootstrap_iters, seed=seed + 3000)
    delta_samples = bootstrap_by_date(test, no_roi_delta(selected), iters=bootstrap_iters, seed=seed + 4000)

    selected_summary = summarize_market_rows(selected_test) if selected_test else {}
    baseline_summary = summarize_market_rows(test) if test else {}
    selected_ci = ci(selected_samples)
    delta_ci = ci(delta_samples)
    significance_pass = bool(selected_test and selected_ci[0] is not None and selected_ci[0] > 0)
    baseline_pass = bool(selected_test and delta_ci[0] is not None and delta_ci[0] > 0)
    forward_pass = bool(selected_test and (selected_summary.get("buy_no_roi") or 0.0) > 0.0)
    verdict = "confirmed" if significance_pass and baseline_pass and forward_pass else "inconclusive"
    return {
        "split": {
            "train_frac": train_frac,
            "cutoff_first_test_date": cutoff,
            "train_dates": len({row["event_date"] for row in train}),
            "test_dates": len({row["event_date"] for row in test}),
            "train_rows": len(train),
            "test_rows": len(test),
        },
        "selection_rule": "train buckets with n>=min_bin_n, BUY_NO ROI>0, and actual_yes-implied_yes<0",
        "selected_price_buckets": sorted(selected),
        "selected_test": {
            **selected_summary,
            "buy_no_roi_ci95_cluster_by_event_date": selected_ci,
        },
        "all_band_buy_no_test_baseline": {
            **baseline_summary,
            "buy_no_roi_ci95_cluster_by_event_date": ci(baseline_samples),
        },
        "selected_minus_all_band_buy_no_roi": {
            "point": (
                None
                if not selected_test or selected_summary.get("buy_no_roi") is None or baseline_summary.get("buy_no_roi") is None
                else selected_summary["buy_no_roi"] - baseline_summary["buy_no_roi"]
            ),
            "ci95_cluster_by_event_date": delta_ci,
        },
        "gates": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "verdict": verdict,
        },
    }


def data_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    total = scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")
    missing = scalar(conn, "SELECT SUM(CASE WHEN decision_window_missing=1 THEN 1 ELSE 0 END) FROM fact_signal_candidates")
    eligible = scalar(conn, "SELECT SUM(eligible) FROM fact_signal_candidates")
    usable = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND market_yes_price IS NOT NULL
        """,
    )
    return {
        "fact_signal_candidates_rows": total,
        "eligible_rows": eligible,
        "decision_window_missing_rows": missing,
        "decision_window_missing_rate": safe_div(float(missing or 0), float(total or 0)),
        "usable_side_rows_before_market_dedupe": usable,
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fwd = report["forward_test"]
    lines = [
        "# Market Structural Edge Research",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: offline H_B diagnostic; source is `fact_signal_candidates`, de-duplicated to one row per market/date/bracket.",
        "",
        "## Data Quality",
        "",
        "| field | value |",
        "|---|---:|",
    ]
    for key, value in report["data_quality"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## Forward Gates",
            "",
            "| gate | status |",
            "|---|---|",
        ]
    )
    for key, value in fwd["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            f"Selected price buckets from train: `{', '.join(fwd['selected_price_buckets']) or 'none'}`.",
            "",
            "| test policy | n | dates | ROI | ROI CI | PnL | cost |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    selected = fwd["selected_test"]
    baseline = fwd["all_band_buy_no_test_baseline"]
    for name, item in [("selected BUY_NO buckets", selected), ("all-band BUY_NO baseline", baseline)]:
        lines.append(
            f"| {name} | {item.get('n', 0)} | {item.get('active_dates', 0)} | "
            f"{pct(item.get('buy_no_roi'))} | {fmt_ci(item.get('buy_no_roi_ci95_cluster_by_event_date'))} | "
            f"{(item.get('buy_no_pnl') or 0):+.2f} | {(item.get('buy_no_cost') or 0):.2f} |"
        )
    delta = fwd["selected_minus_all_band_buy_no_roi"]
    lines.extend(
        [
            "",
            f"Selected minus baseline ROI: `{pct(delta.get('point'))}`, CI `{fmt_ci(delta.get('ci95_cluster_by_event_date'))}`.",
            "",
            "## Price Buckets",
            "",
            "| bucket | n | actual YES | implied YES | gap | BUY_NO ROI | ROI CI | Bonf sig | model lift |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for row in report["price_buckets"]:
        lift = row.get("model_lift", {}).get("delta_high_minus_low_actual_yes")
        lines.append(
            f"| `{row['price_bucket']}` | {row['n']} | {pct(row.get('actual_yes_rate'))} | "
            f"{pct(row.get('mean_market_yes_price'))} | {pct(row.get('gap_actual_minus_implied'))} | "
            f"{pct(row.get('buy_no_roi'))} | {fmt_ci(row.get('buy_no_roi_ci95_cluster_by_event_date'))} | "
            f"{row.get('gap_significant_bonferroni')} | {pct(lift)} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `significance`, `baseline`, and `forward` are required before any live action. A failed gate means this script is diagnostic only.",
            "- Step 1 uses market YES price as a necessary-condition upper bound. Real executable edge still requires Step 2.",
            "- Multiple-testing risk is surfaced via Bonferroni bucket tests; the forward rule still needs independent future data before live sizing changes.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = connect(args.db_path)
    data = load_market_rows(conn, args.bucket_size)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_path": args.db_path,
        "parameters": {
            "bucket_size": args.bucket_size,
            "train_frac": args.train_frac,
            "min_bin_n": args.min_bin_n,
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
        },
        "data_quality": {
            **data_quality(conn),
            "market_rows_after_side_dedupe": len(data),
            "market_row_dates": len({row["event_date"] for row in data}),
        },
        "overall": summarize_market_rows(data),
        "price_buckets": build_bucket_table(
            data,
            bucket_size=args.bucket_size,
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed,
            min_bin_n=args.min_bin_n,
        ),
        "forward_test": forward_test(
            data,
            train_frac=args.train_frac,
            bucket_size=args.bucket_size,
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed,
            min_bin_n=args.min_bin_n,
        ),
    }
    output_dir = prepare_new_run_output(
        resolve_run_output(
            "market_structural_edge",
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    report["living_doc"] = "docs/analysis/market_structure_edge.md"
    result_json = output_dir / "result.json"
    write_json(result_json, report)
    print(f"wrote {result_json}")


if __name__ == "__main__":
    main()
