#!/usr/bin/env python3
"""Executable-edge diagnostics for weather strategy fills and candidates.

Step 2 is intentionally split:

2A. Settled real-fill audit from fact_trades.
2B. Decision-entry proxy from fact_signal_candidates.

Raw orderbook replay is fail-closed in this script until a time-aligned
ts <= decision_snapshot_ts_utc implementation exists. That avoids the known
"latest orderbook" lookahead bug from the external audit draft.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-08-executable-edge.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-08-executable-edge.md"

INSTANCE_EXPR = """
CASE
  WHEN run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  WHEN producer_run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN producer_run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN producer_run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65') THEN 'mid_price_core_v1_side_band'
  ELSE COALESCE(strategy_id, execution_policy, 'unknown')
END
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--trade-class", default="live_real")
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--market-structure-json", default=None, help="Optional Step 1 JSON; selected buckets are reused for the candidate proxy.")
    parser.add_argument("--orderbook-glob", default=None, help="Accepted for compatibility; raw orderbook replay is fail-closed.")
    parser.add_argument("--token-map", default=None, help="Accepted for compatibility; raw orderbook replay is fail-closed.")
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260608)
    parser.add_argument("--skip-live-gate", action="store_true", help="Do not run weather_clob_fill_coverage_gate.py for live_real.")
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


def group_by_date(data: list[dict[str, Any]], date_key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        grouped[str(row[date_key])].append(row)
    return grouped


def bootstrap_by_date(
    data: list[dict[str, Any]],
    date_key: str,
    metric: Callable[[list[dict[str, Any]]], float | None],
    *,
    iters: int,
    seed: int,
) -> list[float]:
    grouped = group_by_date(data, date_key)
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


def roi_from_rows(data: list[dict[str, Any]]) -> float | None:
    cost = sum(float(row.get("cost_usd") or 0.0) for row in data)
    pnl = sum(float(row.get("pnl_usd_at_fill") or 0.0) for row in data)
    return safe_div(pnl, cost)


def cf_roi_from_rows(data: list[dict[str, Any]]) -> float | None:
    cost = sum(float(row.get("decision_cost_usd") or 0.0) for row in data)
    pnl = sum(float(row.get("counterfactual_pnl") or 0.0) for row in data)
    return safe_div(pnl, cost)


def run_live_gate(trade_class: str, skip: bool) -> dict[str, Any]:
    if trade_class != "live_real":
        return {"required": False, "status": "not_live_real"}
    if skip:
        return {"required": True, "status": "skipped_by_flag", "gate_pass": None}
    script = ROOT / "scripts" / "analysis" / "weather_clob_fill_coverage_gate.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = proc.stdout
    gate_pass = False
    try:
        parsed = json.loads(output)
        gate_pass = bool(parsed.get("gate_pass"))
    except json.JSONDecodeError:
        gate_pass = "gate_pass=true" in output or '"gate_pass": true' in output
    return {
        "required": True,
        "status": "pass" if gate_pass else "fail",
        "gate_pass": gate_pass,
        "returncode": proc.returncode,
        "output_tail": output.splitlines()[-20:],
    }


def load_trades(conn: sqlite3.Connection, trade_class: str) -> list[dict[str, Any]]:
    return rows(
        conn,
        f"""
        SELECT
          *,
          {INSTANCE_EXPR} AS strategy_instance
        FROM fact_trades
        WHERE trade_class=?
        """,
        (trade_class,),
    )


def summarize_trades(data: list[dict[str, Any]], *, bootstrap_iters: int, seed: int) -> dict[str, Any]:
    settled = [row for row in data if row.get("settlement_status") == "settled"]
    open_rows = [row for row in data if row.get("settlement_status") != "settled"]
    cost = sum(float(row.get("cost_usd") or 0.0) for row in settled)
    pnl = sum(float(row.get("pnl_usd_at_fill") or 0.0) for row in settled)
    samples = bootstrap_by_date(settled, "target_date", roi_from_rows, iters=bootstrap_iters, seed=seed)
    pnl_values = sorted((float(row.get("pnl_usd_at_fill") or 0.0) for row in settled), reverse=True)
    drop_top1 = pnl - sum(pnl_values[:1])
    drop_top5 = pnl - sum(pnl_values[:5])
    return {
        "fills_total": len(data),
        "settled_fills": len(settled),
        "open_or_unsettled_fills": len(open_rows),
        "active_target_dates": len({row.get("target_date") for row in settled}),
        "settled_cost_usd": cost,
        "settled_pnl_usd": pnl,
        "settled_roi": safe_div(pnl, cost),
        "settled_roi_ci95_cluster_by_target_date": ci(samples),
        "win_rate": safe_div(sum(1 for row in settled if float(row.get("pnl_usd_at_fill") or 0.0) > 0), len(settled)),
        "open_cost_usd": sum(float(row.get("cost_usd") or 0.0) for row in open_rows),
        "drop_top1_fill_pnl_usd": drop_top1,
        "drop_top5_fill_pnl_usd": drop_top5,
    }


def grouped_trade_summaries(
    data: list[dict[str, Any]],
    key: str,
    *,
    bootstrap_iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        grouped[str(row.get(key) or "unknown")].append(row)
    out = []
    for idx, name in enumerate(sorted(grouped)):
        item = summarize_trades(grouped[name], bootstrap_iters=bootstrap_iters, seed=seed + idx)
        item[key] = name
        out.append(item)
    out.sort(key=lambda row: row.get("settled_pnl_usd") or 0.0, reverse=True)
    return out


def edge_predictiveness(data: list[dict[str, Any]], *, bootstrap_iters: int, seed: int) -> dict[str, Any]:
    settled = [
        row for row in data
        if row.get("settlement_status") == "settled"
        and row.get("edge") is not None
        and row.get("pnl_usd_at_fill") is not None
    ]
    if len(settled) < 10:
        return {"n": len(settled), "status": "insufficient"}
    edges = [float(row["edge"]) for row in settled]
    pnls = [float(row["pnl_usd_at_fill"]) for row in settled]
    mean_edge = sum(edges) / len(edges)
    mean_pnl = sum(pnls) / len(pnls)
    cov = sum((edge - mean_edge) * (pnl - mean_pnl) for edge, pnl in zip(edges, pnls))
    edge_var = sum((edge - mean_edge) ** 2 for edge in edges)
    pnl_var = sum((pnl - mean_pnl) ** 2 for pnl in pnls)
    corr = cov / math.sqrt(edge_var * pnl_var) if edge_var and pnl_var else None

    median = sorted(edges)[len(edges) // 2]
    high = [row for row in settled if float(row["edge"]) > median]
    low = [row for row in settled if float(row["edge"]) <= median]

    def delta_metric(sample: list[dict[str, Any]]) -> float | None:
        high_sample = [row for row in sample if float(row["edge"]) > median]
        low_sample = [row for row in sample if float(row["edge"]) <= median]
        high_roi = roi_from_rows(high_sample)
        low_roi = roi_from_rows(low_sample)
        if high_roi is None or low_roi is None:
            return None
        return high_roi - low_roi

    delta_samples = bootstrap_by_date(settled, "target_date", delta_metric, iters=bootstrap_iters, seed=seed)
    return {
        "n": len(settled),
        "edge_pnl_pearson_by_fill": corr,
        "edge_median": median,
        "high_edge_roi": roi_from_rows(high),
        "low_edge_roi": roi_from_rows(low),
        "high_minus_low_edge_roi": None if roi_from_rows(high) is None or roi_from_rows(low) is None else roi_from_rows(high) - roi_from_rows(low),
        "high_minus_low_edge_roi_ci95_cluster_by_target_date": ci(delta_samples),
    }


def selected_bins_from_step1(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return set(payload.get("forward_test", {}).get("selected_price_buckets", []))


def bucket_for_price(price: float, bucket_size: float = 0.05) -> str:
    low = math.floor(price / bucket_size) * bucket_size
    high = min(1.0, low + bucket_size)
    return f"{low:.2f}-{high:.2f}"


def candidate_proxy(
    conn: sqlite3.Connection,
    *,
    selected_bins: set[str],
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    data = rows(
        conn,
        """
        SELECT
          event_date,
          city,
          side,
          market_yes_price,
          decision_entry_price,
          yes_spread,
          no_spread,
          live_filled,
          final_yes,
          counterfactual_pnl,
          decision_entry_price AS decision_cost_usd
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND decision_entry_price IS NOT NULL
          AND counterfactual_pnl IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )
    for row in data:
        row["price_bucket"] = bucket_for_price(float(row["market_yes_price"]))
    filtered = [row for row in data if not selected_bins or row["price_bucket"] in selected_bins]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filtered:
        grouped[str(row["side"])].append(row)

    def summarize(items: list[dict[str, Any]], idx: int) -> dict[str, Any]:
        cost = sum(float(row.get("decision_cost_usd") or 0.0) for row in items)
        pnl = sum(float(row.get("counterfactual_pnl") or 0.0) for row in items)
        samples = bootstrap_by_date(items, "event_date", cf_roi_from_rows, iters=bootstrap_iters, seed=seed + idx)
        return {
            "n": len(items),
            "active_dates": len({row["event_date"] for row in items}),
            "decision_cost_usd": cost,
            "counterfactual_pnl": pnl,
            "counterfactual_roi": safe_div(pnl, cost),
            "counterfactual_roi_ci95_cluster_by_event_date": ci(samples),
            "live_fill_rate": safe_div(sum(int(row.get("live_filled") or 0) for row in items), len(items)),
            "avg_yes_spread": safe_div(sum(float(row.get("yes_spread") or 0.0) for row in items), len(items)),
            "avg_no_spread": safe_div(sum(float(row.get("no_spread") or 0.0) for row in items), len(items)),
        }

    return {
        "source": "fact_signal_candidates.decision_entry_price",
        "scope": "selected Step1 buckets" if selected_bins else "all usable eligible candidates",
        "selected_price_buckets": sorted(selected_bins),
        "overall": summarize(filtered, 0),
        "by_side": {side: summarize(items, idx + 1) for idx, (side, items) in enumerate(sorted(grouped.items()))},
    }


def orderbook_status(orderbook_glob: str | None, token_map: str | None) -> dict[str, Any]:
    if not orderbook_glob and not token_map:
        return {"status": "not_requested"}
    return {
        "status": "skipped_fail_closed",
        "reason": "raw orderbook replay requires a time-aligned ts<=decision_snapshot_ts_utc implementation; latest-snapshot replay is refused to avoid lookahead",
        "orderbook_glob": orderbook_glob,
        "token_map": token_map,
    }


def gate_status(overall: dict[str, Any], edge: dict[str, Any]) -> dict[str, str]:
    roi_ci = overall.get("settled_roi_ci95_cluster_by_target_date") or [None, None]
    edge_ci = edge.get("high_minus_low_edge_roi_ci95_cluster_by_target_date") or [None, None]
    significance = "PASS" if roi_ci[0] is not None and roi_ci[0] > 0 else "FAIL"
    baseline = "PASS" if edge_ci[0] is not None and edge_ci[0] > 0 else "FAIL"
    forward = "NA"
    verdict = "shadow_candidate" if significance == "PASS" and baseline == "PASS" else "inconclusive"
    return {
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "verdict": verdict,
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
    overall = report["fill_audit_2a"]["overall"]
    edge = report["fill_audit_2a"]["edge_predictiveness"]
    lines = [
        "# Executable Edge Research",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        f"> trade_class: `{report['trade_class']}`",
        "> Scope: offline Step 2 diagnostic; no N100/live behavior changed.",
        "",
        "## Gates",
        "",
        "| gate | status |",
        "|---|---|",
    ]
    for key, value in report["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## 2A Fill Audit",
            "",
            "| fills | settled | open | cost | pnl | ROI | ROI CI | win rate |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {overall['fills_total']} | {overall['settled_fills']} | {overall['open_or_unsettled_fills']} | "
            f"{overall['settled_cost_usd']:.2f} | {overall['settled_pnl_usd']:+.2f} | {pct(overall['settled_roi'])} | "
            f"{fmt_ci(overall['settled_roi_ci95_cluster_by_target_date'])} | {pct(overall['win_rate'])} |",
            "",
            f"Drop top1 fill PnL: `{overall['drop_top1_fill_pnl_usd']:+.2f}`; "
            f"drop top5 fill PnL: `{overall['drop_top5_fill_pnl_usd']:+.2f}`.",
            "",
            "## Edge Predictiveness",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| edge-pnl Pearson by fill | `{edge.get('edge_pnl_pearson_by_fill')}` |",
            f"| high edge ROI | `{pct(edge.get('high_edge_roi'))}` |",
            f"| low edge ROI | `{pct(edge.get('low_edge_roi'))}` |",
            f"| high-low ROI delta | `{pct(edge.get('high_minus_low_edge_roi'))}` |",
            f"| high-low delta CI | `{fmt_ci(edge.get('high_minus_low_edge_roi_ci95_cluster_by_target_date'))}` |",
            "",
            "## 2B Decision-Entry Proxy",
            "",
            "| side | n | dates | cf ROI | ROI CI | live fill rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    proxy = report["decision_entry_proxy_2b"]
    for side, item in proxy["by_side"].items():
        lines.append(
            f"| `{side}` | {item['n']} | {item['active_dates']} | {pct(item['counterfactual_roi'])} | "
            f"{fmt_ci(item['counterfactual_roi_ci95_cluster_by_event_date'])} | {pct(item['live_fill_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Raw Orderbook Replay",
            "",
            f"`{report['raw_orderbook_2b']['status']}`: {report['raw_orderbook_2b'].get('reason', '')}",
            "",
            "## Notes",
            "",
            "- 2A uses settled `fact_trades` only and surfaces open/unsettled rows separately.",
            "- 2B here is a decision-entry proxy from `fact_signal_candidates`; it is not a raw orderbook replay.",
            "- Raw orderbook replay is intentionally disabled until it enforces `snapshot_ts <= decision_snapshot_ts_utc` and de-duplicates market rows before aggregation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    gate = run_live_gate(args.trade_class, args.skip_live_gate)
    if gate.get("required") and gate.get("gate_pass") is False:
        raise SystemExit("live_real coverage gate failed; refusing to publish live_real executable-edge metrics")

    conn = connect(args.db_path)
    trades = load_trades(conn, args.trade_class)
    overall = summarize_trades(trades, bootstrap_iters=args.bootstrap_iters, seed=args.seed)
    edge = edge_predictiveness(trades, bootstrap_iters=args.bootstrap_iters, seed=args.seed + 5000)
    selected_bins = selected_bins_from_step1(args.market_structure_json)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_path": args.db_path,
        "trade_class": args.trade_class,
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "market_structure_json": args.market_structure_json,
        },
        "data_freshness": {
            "max_fact_trades_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
            "max_fact_signal_candidates_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
        },
        "live_real_coverage_gate": gate,
        "fill_audit_2a": {
            "overall": overall,
            "by_side": grouped_trade_summaries(trades, "side", bootstrap_iters=args.bootstrap_iters, seed=args.seed + 100),
            "by_strategy_instance": grouped_trade_summaries(trades, "strategy_instance", bootstrap_iters=args.bootstrap_iters, seed=args.seed + 200),
            "edge_predictiveness": edge,
        },
        "decision_entry_proxy_2b": candidate_proxy(
            conn,
            selected_bins=selected_bins,
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed + 6000,
        ),
        "raw_orderbook_2b": orderbook_status(args.orderbook_glob, args.token_map),
    }
    report["gates"] = gate_status(overall, edge)
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
