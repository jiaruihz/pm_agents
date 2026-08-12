#!/usr/bin/env python3
"""H-C conditional city x model edge research.

H-C asks whether a conditional sub-pool, especially city x GFS x side, has
model and trading value even if global H-A fails.

Source of truth:
  runtime/weather.db.fact_signal_candidates

Method:
  1. Use only eligible, settled, decision-window-present candidate rows.
  2. Split by event_date into train and holdout.
  3. On train, select city x model_version x side keys with:
       - enough rows and active dates,
       - model Brier better than market Brier,
       - counterfactual ROI better than a matched side+price-bucket baseline.
  4. Freeze selected keys, evaluate only on holdout.
  5. Report significance / baseline / forward gates. No live action is allowed
     unless gates pass; this script is an offline diagnostic.
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
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
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
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--min-train-rows", type=int, default=10)
    parser.add_argument("--min-train-days", type=int, default=4)
    parser.add_argument("--min-holdout-rows", type=int, default=5)
    parser.add_argument("--bucket-size", type=float, default=0.10)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260608)
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


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


def price_bucket(price: float, bucket_size: float) -> str:
    low = math.floor(price / bucket_size) * bucket_size
    high = min(1.0, low + bucket_size)
    return f"{low:.2f}-{high:.2f}"


def load_candidates(conn: sqlite3.Connection, bucket_size: float) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT
          candidate_id,
          event_date,
          city,
          side,
          model_version,
          forecast_source,
          market_yes_price,
          model_p_yes,
          decision_entry_price,
          final_yes,
          counterfactual_pnl,
          live_filled,
          paper_ordered
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND market_yes_price IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND counterfactual_pnl IS NOT NULL
          AND side IN ('BUY_YES', 'BUY_NO')
        """,
    )
    out: list[dict[str, Any]] = []
    for row in data:
        market_p = float(row["market_yes_price"])
        model_p = float(row["model_p_yes"])
        final_yes = float(row["final_yes"])
        row["key"] = f"{row['city']}|{row['model_version']}|{row['side']}"
        row["side_bucket"] = f"{row['side']}|{price_bucket(market_p, bucket_size)}"
        row["model_brier"] = (model_p - final_yes) ** 2
        row["market_brier"] = (market_p - final_yes) ** 2
        row["brier_delta_market_minus_model"] = row["market_brier"] - row["model_brier"]
        row["cost_usd"] = float(row["decision_entry_price"])
        row["pnl_usd"] = float(row["counterfactual_pnl"])
        out.append(row)
    return out


def split_by_date(data: list[dict[str, Any]], train_frac: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    dates = sorted({row["event_date"] for row in data})
    if len(dates) < 2:
        return data, [], {"dates": dates, "cutoff_first_holdout_date": None}
    cut_idx = max(1, min(len(dates) - 1, int(len(dates) * train_frac)))
    train_dates = set(dates[:cut_idx])
    train = [row for row in data if row["event_date"] in train_dates]
    holdout = [row for row in data if row["event_date"] not in train_dates]
    return train, holdout, {
        "train_frac": train_frac,
        "train_dates": len(train_dates),
        "holdout_dates": len(dates) - len(train_dates),
        "cutoff_first_holdout_date": dates[cut_idx],
        "min_date": dates[0],
        "max_date": dates[-1],
    }


def summarize(data: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(data)
    cost = sum(float(row["cost_usd"]) for row in data)
    pnl = sum(float(row["pnl_usd"]) for row in data)
    brier_delta = safe_div(sum(float(row["brier_delta_market_minus_model"]) for row in data), n)
    return {
        "n": n,
        "active_dates": len({row["event_date"] for row in data}),
        "cities": len({row["city"] for row in data}),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": safe_div(pnl, cost),
        "win_rate": safe_div(sum(1 for row in data if float(row["pnl_usd"]) > 0), n),
        "model_brier": safe_div(sum(float(row["model_brier"]) for row in data), n),
        "market_brier": safe_div(sum(float(row["market_brier"]) for row in data), n),
        "brier_delta_market_minus_model": brier_delta,
        "live_fill_rate": safe_div(sum(int(row["live_filled"] or 0) for row in data), n),
        "paper_order_rate": safe_div(sum(int(row["paper_ordered"] or 0) for row in data), n),
    }


def roi(data: list[dict[str, Any]]) -> float | None:
    return summarize(data)["roi"]


def brier_delta(data: list[dict[str, Any]]) -> float | None:
    return summarize(data)["brier_delta_market_minus_model"]


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
    out: list[float] = []
    for _ in range(iters):
        sample: list[dict[str, Any]] = []
        for _date in dates:
            sample.extend(grouped[rng.choice(dates)])
        value = metric(sample)
        if value is not None and math.isfinite(value):
            out.append(value)
    return out


def matched_baseline(rows_: list[dict[str, Any]], universe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = {row["side_bucket"] for row in rows_}
    return [row for row in universe if row["side_bucket"] in buckets]


def excess_roi_metric(selected_keys: set[str], allowed_buckets: set[str]) -> Callable[[list[dict[str, Any]]], float | None]:
    def _metric(sample: list[dict[str, Any]]) -> float | None:
        selected = [row for row in sample if row["key"] in selected_keys]
        baseline = [row for row in sample if row["side_bucket"] in allowed_buckets]
        selected_roi = roi(selected)
        baseline_roi = roi(baseline)
        if selected_roi is None or baseline_roi is None:
            return None
        return selected_roi - baseline_roi

    return _metric


def add_ci(summary: dict[str, Any], data: list[dict[str, Any]], *, iters: int, seed: int) -> dict[str, Any]:
    out = dict(summary)
    out["roi_ci95_cluster_by_event_date"] = ci(bootstrap_by_date(data, roi, iters=iters, seed=seed))
    out["brier_delta_ci95_cluster_by_event_date"] = ci(bootstrap_by_date(data, brier_delta, iters=iters, seed=seed + 1))
    return out


def candidate_table(
    train: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
    *,
    min_train_rows: int,
    min_train_days: int,
    min_holdout_rows: int,
    bootstrap_iters: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_key_train: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_key_holdout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train:
        by_key_train[row["key"]].append(row)
    for row in holdout:
        by_key_holdout[row["key"]].append(row)

    out: list[dict[str, Any]] = []
    selected_keys: list[str] = []
    for idx, key in enumerate(sorted(by_key_train)):
        train_rows = by_key_train[key]
        holdout_rows = by_key_holdout.get(key, [])
        train_base = matched_baseline(train_rows, train)
        train_summary = summarize(train_rows)
        train_base_summary = summarize(train_base)
        train_excess = None
        if train_summary["roi"] is not None and train_base_summary["roi"] is not None:
            train_excess = train_summary["roi"] - train_base_summary["roi"]
        selected = (
            train_summary["n"] >= min_train_rows
            and train_summary["active_dates"] >= min_train_days
            and (train_summary["brier_delta_market_minus_model"] or 0.0) > 0.0
            and (train_excess or 0.0) > 0.0
        )
        if selected:
            selected_keys.append(key)

        holdout_base = matched_baseline(holdout_rows, holdout)
        holdout_summary = summarize(holdout_rows) if holdout_rows else {"n": 0, "active_dates": 0}
        holdout_base_summary = summarize(holdout_base) if holdout_base else {"n": 0, "active_dates": 0}
        holdout_excess = None
        if holdout_rows and holdout_summary.get("roi") is not None and holdout_base_summary.get("roi") is not None:
            holdout_excess = holdout_summary["roi"] - holdout_base_summary["roi"]
        low_sample = len(holdout_rows) < min_holdout_rows
        out.append({
            "key": key,
            "city": train_rows[0]["city"],
            "model_version": train_rows[0]["model_version"],
            "side": train_rows[0]["side"],
            "selected_on_train": selected,
            "low_holdout_sample": low_sample,
            "train": {
                **add_ci(train_summary, train_rows, iters=bootstrap_iters, seed=seed + idx * 10),
                "matched_baseline_roi": train_base_summary.get("roi"),
                "excess_roi_vs_matched_baseline": train_excess,
            },
            "holdout": {
                **(add_ci(holdout_summary, holdout_rows, iters=bootstrap_iters, seed=seed + 5000 + idx * 10) if holdout_rows else holdout_summary),
                "matched_baseline_roi": holdout_base_summary.get("roi"),
                "excess_roi_vs_matched_baseline": holdout_excess,
            },
        })
    out.sort(
        key=lambda row: (
            not row["selected_on_train"],
            -(row["holdout"].get("excess_roi_vs_matched_baseline") or -999.0),
            row["key"],
        )
    )
    return out, selected_keys


def aggregate_selected(
    selected_keys: list[str],
    train: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
    *,
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    key_set = set(selected_keys)
    train_selected = [row for row in train if row["key"] in key_set]
    holdout_selected = [row for row in holdout if row["key"] in key_set]
    train_buckets = {row["side_bucket"] for row in train_selected}
    holdout_baseline = [row for row in holdout if row["side_bucket"] in train_buckets]
    train_baseline = [row for row in train if row["side_bucket"] in train_buckets]

    train_summary = summarize(train_selected) if train_selected else {"n": 0, "active_dates": 0}
    holdout_summary = summarize(holdout_selected) if holdout_selected else {"n": 0, "active_dates": 0}
    train_base_summary = summarize(train_baseline) if train_baseline else {"n": 0, "active_dates": 0}
    holdout_base_summary = summarize(holdout_baseline) if holdout_baseline else {"n": 0, "active_dates": 0}

    if holdout_selected:
        holdout_summary = add_ci(holdout_summary, holdout_selected, iters=bootstrap_iters, seed=seed)
    excess_samples = bootstrap_by_date(
        holdout,
        excess_roi_metric(key_set, train_buckets),
        iters=bootstrap_iters,
        seed=seed + 1000,
    )
    brier_ci = holdout_summary.get("brier_delta_ci95_cluster_by_event_date") or [None, None]
    excess_point = None
    if holdout_summary.get("roi") is not None and holdout_base_summary.get("roi") is not None:
        excess_point = holdout_summary["roi"] - holdout_base_summary["roi"]
    excess_ci = ci(excess_samples)

    significance_pass = bool(
        holdout_summary.get("roi_ci95_cluster_by_event_date")
        and holdout_summary["roi_ci95_cluster_by_event_date"][0] is not None
        and holdout_summary["roi_ci95_cluster_by_event_date"][0] > 0
    )
    baseline_pass = bool(excess_ci[0] is not None and excess_ci[0] > 0)
    forward_pass = bool(
        holdout_selected
        and (holdout_summary.get("brier_delta_market_minus_model") or 0.0) > 0.0
        and (excess_point or 0.0) > 0.0
    )
    verdict = "confirmed" if significance_pass and baseline_pass and forward_pass else "inconclusive"
    return {
        "selected_keys": selected_keys,
        "selected_key_count": len(selected_keys),
        "selection_side_buckets": sorted(train_buckets),
        "train_selected": train_summary,
        "train_matched_baseline": train_base_summary,
        "holdout_selected": holdout_summary,
        "holdout_matched_baseline": holdout_base_summary,
        "holdout_excess_roi_vs_matched_baseline": {
            "point": excess_point,
            "ci95_cluster_by_event_date": excess_ci,
        },
        "holdout_brier_delta_ci95_cluster_by_event_date": brier_ci,
        "gates": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "verdict": verdict,
        },
    }


def grouped_overview(data: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        grouped[str(row[key])].append(row)
    out = []
    for name, items in sorted(grouped.items()):
        item = summarize(items)
        item[key] = name
        out.append(item)
    out.sort(key=lambda row: row.get("brier_delta_market_minus_model") or -999, reverse=True)
    return out


def data_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_trades_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "max_fact_signal_candidates_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
        "fact_signal_candidates_rows": scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
        "eligible_rows": scalar(conn, "SELECT SUM(eligible) FROM fact_signal_candidates"),
        "decision_window_missing_rows": scalar(conn, "SELECT SUM(CASE WHEN decision_window_missing=1 THEN 1 ELSE 0 END) FROM fact_signal_candidates"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:+.2f}"


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    agg = report["aggregate_selected"]
    lines = [
        "# City x Model Conditional Edge Research",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: H-C offline diagnostic. Source is `fact_signal_candidates`; no N100/live behavior changed.",
        "",
        "## Data Snapshot",
        "",
        "| field | value |",
        "|---|---:|",
    ]
    for key, value in report["data_snapshot"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend([
        "",
        "## Split",
        "",
        "| field | value |",
        "|---|---:|",
    ])
    for key, value in report["split"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend([
        "",
        "## Gates",
        "",
        "| gate | status |",
        "|---|---|",
    ])
    for key, value in agg["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    holdout = agg["holdout_selected"]
    baseline = agg["holdout_matched_baseline"]
    excess = agg["holdout_excess_roi_vs_matched_baseline"]
    lines.extend([
        "",
        f"Train-selected keys: `{', '.join(agg['selected_keys']) or 'none'}`.",
        "",
        "| holdout policy | n | dates | cost | pnl | ROI | ROI CI | Brier delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| selected H-C pool | {holdout.get('n', 0)} | {holdout.get('active_dates', 0)} | "
        f"{holdout.get('cost_usd', 0):.2f} | {fmt_money(holdout.get('pnl_usd'))} | "
        f"{pct(holdout.get('roi'))} | {fmt_ci(holdout.get('roi_ci95_cluster_by_event_date'))} | "
        f"{holdout.get('brier_delta_market_minus_model')} |",
        f"| matched side+price baseline | {baseline.get('n', 0)} | {baseline.get('active_dates', 0)} | "
        f"{baseline.get('cost_usd', 0):.2f} | {fmt_money(baseline.get('pnl_usd'))} | "
        f"{pct(baseline.get('roi'))} | NA | {baseline.get('brier_delta_market_minus_model')} |",
        "",
        f"Holdout excess ROI vs matched baseline: `{pct(excess.get('point'))}`, CI `{fmt_ci(excess.get('ci95_cluster_by_event_date'))}`.",
        "",
        "## Selected Key Detail",
        "",
        "| key | selected | train n | train excess ROI | holdout n | holdout ROI | holdout excess ROI | holdout Brier delta | low sample |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    selected_or_top = [row for row in report["candidate_table"] if row["selected_on_train"]][:30]
    if not selected_or_top:
        selected_or_top = report["candidate_table"][:20]
    for row in selected_or_top:
        lines.append(
            f"| `{row['key']}` | {row['selected_on_train']} | {row['train'].get('n', 0)} | "
            f"{pct(row['train'].get('excess_roi_vs_matched_baseline'))} | {row['holdout'].get('n', 0)} | "
            f"{pct(row['holdout'].get('roi'))} | {pct(row['holdout'].get('excess_roi_vs_matched_baseline'))} | "
            f"{row['holdout'].get('brier_delta_market_minus_model')} | {row['low_holdout_sample']} |"
        )
    lines.extend([
        "",
        "## Model Overview",
        "",
        "| model | n | ROI | Brier delta | live fill rate |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in report["overview_by_model"]:
        lines.append(
            f"| `{row['model_version']}` | {row['n']} | {pct(row.get('roi'))} | "
            f"{row.get('brier_delta_market_minus_model')} | {pct(row.get('live_fill_rate'))} |"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- Positive `brier_delta_market_minus_model` means the model probability has lower Brier loss than market YES price.",
        "- The baseline is all holdout candidates with the same selected `side+price_bucket`; this avoids mistaking BUY_NO base-rate or price-band structure for city-model alpha.",
        "- `decision_window_missing` rows are excluded by contract; the data snapshot reports their scale.",
        "- A failed gate means no live keep/cut/size action.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = prepare_new_run_output(
        resolve_run_output(
            "city_model_conditional_edge",
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    conn = connect(args.db_path)
    data = load_candidates(conn, args.bucket_size)
    train, holdout, split = split_by_date(data, args.train_frac)
    candidates, selected_keys = candidate_table(
        train,
        holdout,
        min_train_rows=args.min_train_rows,
        min_train_days=args.min_train_days,
        min_holdout_rows=args.min_holdout_rows,
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed,
    )
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_path": args.db_path,
        "parameters": {
            "train_frac": args.train_frac,
            "min_train_rows": args.min_train_rows,
            "min_train_days": args.min_train_days,
            "min_holdout_rows": args.min_holdout_rows,
            "bucket_size": args.bucket_size,
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
        },
        "data_snapshot": {
            **data_snapshot(conn),
            "usable_rows": len(data),
            "usable_dates": len({row["event_date"] for row in data}),
        },
        "split": {
            **split,
            "train_rows": len(train),
            "holdout_rows": len(holdout),
        },
        "overview_by_model": grouped_overview(data, "model_version"),
        "overview_by_side": grouped_overview(data, "side"),
        "candidate_table": candidates,
        "aggregate_selected": aggregate_selected(
            selected_keys,
            train,
            holdout,
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed + 10000,
        ),
    }
    report["living_doc"] = "docs/analysis/city_selection.md"
    output_json = output_dir / "result.json"
    write_json(output_json, report)
    print(f"wrote {output_json}")


if __name__ == "__main__":
    main()
