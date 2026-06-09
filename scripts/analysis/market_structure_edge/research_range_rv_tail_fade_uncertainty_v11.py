#!/usr/bin/env python3
"""Tail fade / uncertainty Range RV research v1.1.

Target metric:
    forecast_tail_fade_range_rv_alpha

This is local counterfactual research only. It reads the strategy-analysis
grain from runtime/weather.db fact tables, does not use old single-leg
`eligible` as a Range RV hard gate, and evaluates executable rows only with
time-aligned orderbooks where orderbook_snapshot_ts <= decision_snapshot_ts_utc.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-tail-fade-uncertainty-v1-1.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md"

MARKET_TAIL_OVERPRICED_THRESHOLDS = (0.05, 0.10, 0.15)
MODEL_TAIL_RISK_MAX = (0.05, 0.10, 0.15)
UNCERTAINTY_FILTERS = ("none", "entropy_le_080")
MAX_LEG_SPREAD = 0.25
MIN_GATE_SELECTED_ROWS = 10
MIN_GATE_ACTIVE_DATES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))


def norm(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    total = sum(safe_float(row.get(key)) for row in items)
    if total <= 0:
        return {str(row["bracket"]): 0.0 for row in items}
    return {str(row["bracket"]): safe_float(row.get(key)) / total for row in items}


def model_entropy(items: list[dict[str, Any]]) -> float:
    probs = list(norm(items, "model_p_yes").values())
    positive = [p for p in probs if p > 0]
    if len(positive) <= 1:
        return 0.0
    return -sum(p * math.log(p) for p in positive) / math.log(len(probs))


def hours_bucket(value: Any) -> str:
    hours = safe_float(value, -1.0)
    if hours < 0:
        return "unknown"
    if hours < 22:
        return "T_lt_22"
    if hours < 24:
        return "T_22_24"
    if hours < 26:
        return "T_24_26"
    if hours < 28:
        return "T_26_28"
    return "T_ge_28"


def side_spread_ok(legs: list[dict[str, Any]]) -> bool:
    for leg in legs:
        spread = scanner.side_spread(leg, str(leg["side"]))
        if spread is not None and spread > MAX_LEG_SPREAD:
            return False
    return True


def winner_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for row in items if safe_float(row.get("final_yes")) >= 0.5)


def leg_cost(side: str, yes_price: float) -> float:
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def make_tail_candidate(
    *,
    algorithm_base: str,
    items: list[dict[str, Any]],
    tail_rows: list[dict[str, Any]],
    inner_rows: list[dict[str, Any]],
    tail_side: str,
) -> dict[str, Any] | None:
    if not tail_rows or not inner_rows:
        return None

    legs = [dict(row, side="BUY_NO") for row in tail_rows] + [dict(row, side="BUY_YES") for row in inner_rows]
    model = norm(items, "model_p_yes")
    market = norm(items, "market_yes_price")

    model_tail_mass = sum(model[str(row["bracket"])] for row in tail_rows)
    market_tail_mass = sum(market[str(row["bracket"])] for row in tail_rows)
    model_inner_mass = sum(model[str(row["bracket"])] for row in inner_rows)
    market_inner_mass = sum(market[str(row["bracket"])] for row in inner_rows)
    market_tail_overpriced = market_tail_mass - model_tail_mass
    inner_confirm_edge = model_inner_mass - market_inner_mass
    raw_model_tail_mass = sum(safe_float(row.get("model_p_yes")) for row in tail_rows)
    raw_market_tail_mass = sum(safe_float(row.get("market_yes_price")) for row in tail_rows)

    row = variants.make_candidate(
        algorithm=algorithm_base,
        decision_items=items,
        legs=legs,
        score=market_tail_overpriced,
        threshold=0.0,
        components={
            "shape": "tail_fade_no_plus_inner_yes",
            "tail_side": tail_side,
            "tail_brackets": [row["bracket"] for row in tail_rows],
            "inner_brackets": [row["bracket"] for row in inner_rows],
            "model_tail_mass": model_tail_mass,
            "market_tail_mass": market_tail_mass,
            "model_tail_minus_market_tail": model_tail_mass - market_tail_mass,
            "market_tail_overpriced": market_tail_overpriced,
            "model_inner_mass": model_inner_mass,
            "market_inner_mass": market_inner_mass,
            "inner_confirm_edge": inner_confirm_edge,
            "raw_model_tail_mass": raw_model_tail_mass,
            "raw_market_tail_mass": raw_market_tail_mass,
            "max_spread": MAX_LEG_SPREAD,
        },
    )
    row["expression"] = algorithm_base
    row["tail_side"] = tail_side
    row["model_entropy"] = model_entropy(items)
    row["model_tail_mass"] = model_tail_mass
    row["market_tail_mass"] = market_tail_mass
    row["model_tail_minus_market_tail"] = model_tail_mass - market_tail_mass
    row["market_tail_overpriced"] = market_tail_overpriced
    row["inner_confirm_edge"] = inner_confirm_edge
    row["spread_ok"] = int(side_spread_ok(legs))
    row["decision_hours_to_settle"] = safe_float(items[0].get("decision_hours_to_settle"), None)
    row["decision_hours_to_settle_bucket"] = hours_bucket(items[0].get("decision_hours_to_settle"))
    row["forecast_source"] = items[0].get("forecast_source")
    row["model_version"] = items[0].get("model_version")
    row["selected"] = 1
    return row


def generate_base_rows(decision_sets: list[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    candidate_counts = {
        "below_tail_candidate_count": 0,
        "above_tail_candidate_count": 0,
        "both_tail_candidate_count": 0,
    }
    for raw_items in decision_sets:
        items = sort_items(raw_items)
        if len(items) < 3 or winner_count(items) != 1:
            continue

        below = make_tail_candidate(
            algorithm_base="below_tail_no_inner_yes",
            items=items,
            tail_rows=[items[0]],
            inner_rows=[items[1]],
            tail_side="below",
        )
        if below:
            candidate_counts["below_tail_candidate_count"] += 1
            rows.append(below)

        above = make_tail_candidate(
            algorithm_base="above_tail_no_inner_yes",
            items=items,
            tail_rows=[items[-1]],
            inner_rows=[items[-2]],
            tail_side="above",
        )
        if above:
            candidate_counts["above_tail_candidate_count"] += 1
            rows.append(above)

        inner_pair = [items[1], items[-2]]
        inner_pair = list({str(row["bracket"]): row for row in inner_pair}.values())
        both_inner = make_tail_candidate(
            algorithm_base="both_tails_no_inner_yes",
            items=items,
            tail_rows=[items[0], items[-1]],
            inner_rows=inner_pair,
            tail_side="both",
        )
        if both_inner:
            candidate_counts["both_tail_candidate_count"] += 1
            rows.append(both_inner)

        inner = items[1:-1]
        mode = max(inner, key=lambda row: safe_float(row.get("model_p_yes"))) if inner else None
        if mode:
            both_center = make_tail_candidate(
                algorithm_base="both_tails_no_center_yes",
                items=items,
                tail_rows=[items[0], items[-1]],
                inner_rows=[mode],
                tail_side="both",
            )
            if both_center:
                candidate_counts["both_tail_candidate_count"] += 1
                rows.append(both_center)
    return rows, candidate_counts


def passes_filters(
    row: dict[str, Any],
    *,
    market_tail_overpriced_min: float,
    model_tail_risk_max: float,
    uncertainty_filter: str,
) -> tuple[bool, dict[str, int]]:
    steps: dict[str, int] = {}
    ok = True
    ok = ok and safe_float(row.get("market_tail_overpriced")) >= market_tail_overpriced_min
    steps["market_tail_overpriced"] = int(ok)
    ok = ok and safe_float(row.get("model_tail_mass")) <= model_tail_risk_max
    steps["model_tail_risk_max"] = int(ok)
    ok = ok and safe_float(row.get("inner_confirm_edge")) >= 0.0
    steps["inner_confirm_edge"] = int(ok)
    ok = ok and int(row.get("spread_ok") or 0) == 1
    steps["spread_ok"] = int(ok)
    if uncertainty_filter == "entropy_le_080":
        ok = ok and safe_float(row.get("model_entropy"), 1.0) <= 0.80
    steps["high_uncertainty_no_trade"] = int(ok)
    return ok, steps


def clone_threshold_row(
    row: dict[str, Any],
    *,
    market_tail_overpriced_min: float,
    model_tail_risk_max: float,
    uncertainty_filter: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    cloned = deepcopy(row)
    mt = int(round(market_tail_overpriced_min * 100))
    mr = int(round(model_tail_risk_max * 100))
    suffix = f"mt{mt:02d}_mr{mr:02d}_{uncertainty_filter}"
    algorithm = f"{row['expression']}_{suffix}"
    selected, steps = passes_filters(
        row,
        market_tail_overpriced_min=market_tail_overpriced_min,
        model_tail_risk_max=model_tail_risk_max,
        uncertainty_filter=uncertainty_filter,
    )
    cloned["algorithm"] = algorithm
    cloned["candidate_id"] = f"{algorithm}|{row['city']}|{row['event_date']}|{row['decision_snapshot_ts_utc']}|{','.join(cloned['sides'])}:{','.join(map(str, cloned['brackets']))}"
    cloned["selected"] = int(selected)
    cloned["score"] = row["market_tail_overpriced"]
    cloned["threshold"] = market_tail_overpriced_min
    cloned["market_tail_overpriced_min"] = market_tail_overpriced_min
    cloned["model_tail_risk_max"] = model_tail_risk_max
    cloned["uncertainty_filter"] = uncertainty_filter
    cloned["components"] = {
        **cloned.get("components", {}),
        "market_tail_overpriced_min": market_tail_overpriced_min,
        "model_tail_risk_max": model_tail_risk_max,
        "uncertainty_filter": uncertainty_filter,
        "selected": int(selected),
    }
    return cloned, steps


def expand_threshold_rows(base_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    funnel: list[dict[str, Any]] = []
    for expression in sorted({row["expression"] for row in base_rows}):
        expression_rows = [row for row in base_rows if row["expression"] == expression]
        for market_threshold in MARKET_TAIL_OVERPRICED_THRESHOLDS:
            for model_risk_max in MODEL_TAIL_RISK_MAX:
                for uncertainty_filter in UNCERTAINTY_FILTERS:
                    step_counts = {
                        "base_rows": len(expression_rows),
                        "market_tail_overpriced": 0,
                        "model_tail_risk_max": 0,
                        "inner_confirm_edge": 0,
                        "spread_ok": 0,
                        "high_uncertainty_no_trade": 0,
                    }
                    algorithm = None
                    for row in expression_rows:
                        cloned, steps = clone_threshold_row(
                            row,
                            market_tail_overpriced_min=market_threshold,
                            model_tail_risk_max=model_risk_max,
                            uncertainty_filter=uncertainty_filter,
                        )
                        algorithm = cloned["algorithm"]
                        rows.append(cloned)
                        for key, value in steps.items():
                            step_counts[key] += value
                    funnel.append(
                        {
                            "algorithm": algorithm,
                            "expression": expression,
                            "market_tail_overpriced_min": market_threshold,
                            "model_tail_risk_max": model_risk_max,
                            "uncertainty_filter": uncertainty_filter,
                            **step_counts,
                        }
                    )
    return rows, funnel


def metric_rows(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return variants.metric_rows(rows, source)


def evaluate_algorithms(
    rows: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    results = variants.evaluate_algorithms(
        rows,
        source=source,
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=iters,
        seed=seed,
    )
    for item in results:
        item["gates"] = strict_gate_result(item.get("train"), item.get("holdout"))
        item["gate_reasons"] = gate_reasons(item)
    return results


def ci_lower_positive(value: list[float | None] | None) -> bool:
    return bool(value and value[0] is not None and value[0] > 0)


def enough_sample(eval_block: dict[str, Any] | None) -> bool:
    if not eval_block:
        return False
    selected = eval_block.get("selected") or {}
    return (
        int(selected.get("rows") or 0) >= MIN_GATE_SELECTED_ROWS
        and int(selected.get("active_event_dates") or 0) >= MIN_GATE_ACTIVE_DATES
    )


def top5_stress_pass(eval_block: dict[str, Any] | None) -> bool:
    if not eval_block:
        return False
    value = (eval_block.get("selected") or {}).get("drop_top5_taker_roi")
    return value is not None and safe_float(value) > 0


def strict_gate_result(train_eval: dict[str, Any] | None, holdout_eval: dict[str, Any] | None) -> dict[str, str]:
    if not train_eval or not holdout_eval:
        return {"significance": "NA", "baseline": "NA", "forward": "NA", "verdict": "inconclusive"}
    train_sample = enough_sample(train_eval)
    holdout_sample = enough_sample(holdout_eval)
    significance = (
        "PASS"
        if train_sample and ci_lower_positive(train_eval.get("roi_ci95_cluster_by_event_date"))
        else "FAIL"
    )
    baseline = (
        "PASS"
        if train_sample and ci_lower_positive(train_eval.get("excess_roi_ci95_cluster_by_event_date"))
        else "FAIL"
    )
    forward = (
        "PASS"
        if holdout_sample
        and top5_stress_pass(holdout_eval)
        and ci_lower_positive(holdout_eval.get("roi_ci95_cluster_by_event_date"))
        and ci_lower_positive(holdout_eval.get("excess_roi_ci95_cluster_by_event_date"))
        else "FAIL"
    )
    verdict = "confirmed" if significance == "PASS" and baseline == "PASS" and forward == "PASS" else "inconclusive"
    return {"significance": significance, "baseline": baseline, "forward": forward, "verdict": verdict}


def gate_reasons(item: dict[str, Any]) -> list[str]:
    reasons = []
    train = item.get("train") or {}
    holdout = item.get("holdout") or {}
    if not enough_sample(train):
        reasons.append("train_selected_sample_below_gate")
    if not enough_sample(holdout):
        reasons.append("holdout_selected_sample_below_gate")
    if not ci_lower_positive(train.get("roi_ci95_cluster_by_event_date")):
        reasons.append("train_roi_ci_crosses_0")
    if not ci_lower_positive(train.get("excess_roi_ci95_cluster_by_event_date")):
        reasons.append("train_excess_ci_crosses_0")
    if not ci_lower_positive(holdout.get("roi_ci95_cluster_by_event_date")):
        reasons.append("holdout_roi_ci_crosses_0")
    if not ci_lower_positive(holdout.get("excess_roi_ci95_cluster_by_event_date")):
        reasons.append("holdout_excess_ci_crosses_0")
    if not top5_stress_pass(holdout):
        reasons.append("holdout_top5_removed_roi_not_positive")
    return reasons


def passed(item: dict[str, Any]) -> bool:
    gates = item["gates"]
    return gates["significance"] == "PASS" and gates["baseline"] == "PASS" and gates["forward"] == "PASS"


def rank_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
    holdout = item["holdout"]["selected"]
    return (
        safe_float(item["holdout"].get("excess_roi"), -999.0),
        safe_float(holdout.get("drop_top5_taker_roi"), -999.0),
        safe_float(holdout.get("active_event_dates"), 0.0),
        safe_float(item["train"].get("excess_roi"), -999.0),
    )


def bucket_value(value: float, cuts: tuple[float, ...], prefix: str) -> str:
    for cut in cuts:
        if value <= cut:
            return f"{prefix}_le_{cut:.2f}"
    return f"{prefix}_gt_{cuts[-1]:.2f}"


def add_uncertainty_buckets(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row["model_entropy_bucket"] = bucket_value(safe_float(row.get("model_entropy")), (0.50, 0.70, 0.85), "entropy")
        row["model_tail_mass_bucket"] = bucket_value(safe_float(row.get("model_tail_mass")), (0.05, 0.10, 0.15), "model_tail")
        row["market_tail_mass_bucket"] = bucket_value(safe_float(row.get("market_tail_mass")), (0.05, 0.10, 0.15, 0.25), "market_tail")
        row["tail_mass_delta_bucket"] = bucket_value(
            safe_float(row.get("model_tail_minus_market_tail")),
            (-0.15, -0.10, -0.05, 0.0),
            "model_minus_market",
        )


def stratified(rows: list[dict[str, Any]], keys: list[str], source: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    selected = [row for row in rows if int(row.get("selected") or 0) == 1]
    for key in keys:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in selected:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        items = []
        for name, group in sorted(grouped.items()):
            summary = scanner.summarize(group, source=source)
            summary[key] = name
            items.append(summary)
        items.sort(key=lambda row: row.get("settled_pnl") or 0.0, reverse=True)
        out[key] = items
    return out


def data_snapshot(conn: Any, db_path: Path, gate_log_path: Path) -> dict[str, Any]:
    gate_payload: dict[str, Any] | None = None
    if gate_log_path.exists():
        try:
            gate_payload = json.loads(gate_log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gate_payload = {"parse_error": True}
    return {
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "five_line_self_check": scanner.data_self_check(conn),
        "clob_gate_log": str(gate_log_path),
        "clob_gate": {
            "gate_pass": None if gate_payload is None else gate_payload.get("gate_pass"),
            "fail_reasons": [] if gate_payload is None else gate_payload.get("fail_reasons", []),
            "fact_trades_live_real": None if gate_payload is None else gate_payload.get("fact_trades_live_real"),
        },
    }


def selected_row_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    reasons = ",".join(item.get("gate_reasons") or [])
    return (
        f"| `{item['algorithm']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {item['holdout_family_rows']} | "
        f"{holdout['selected']['rows']} | {holdout['selected']['active_event_dates']} | "
        f"{pct(train['selected']['taker_roi'])} | {fmt_ci(train['roi_ci95_cluster_by_event_date'])} | "
        f"{pct(train['baseline']['taker_roi'])} | {pct(train['excess_roi'])} | "
        f"{fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['taker_roi'])} | "
        f"{fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | {pct(holdout['baseline']['taker_roi'])} | "
        f"{pct(holdout['excess_roi'])} | {fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"{money(holdout['selected']['avg_worst_case_loss_unit_notional'])} | "
        f"{pct(holdout['selected']['avg_loss_probability'])} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` | `{reasons}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    best_proxy = report.get("best_decision_proxy_by_holdout_excess")
    best_orderbook = report.get("best_orderbook_by_holdout_excess")
    lines = [
        "# Tail Fade / Uncertainty Range RV v1.1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local counterfactual research only; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` / `fact_trades`; raw orderbook snapshots only for executable price matching.",
        f"- DB last_modified: `{report['data_snapshot']['db_last_modified_utc']}`",
        f"- fact_signal_candidates built at: `{report['data_snapshot']['five_line_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- fact_trades built at: `{report['data_snapshot']['five_line_self_check']['fact_trades_max_built_at_utc']}`",
        f"- CLOB gate pass: `{report['data_snapshot']['clob_gate']['gate_pass']}`; reasons: `{report['data_snapshot']['clob_gate']['fail_reasons']}`.",
        "- 本报告不发布 live_real PnL/ROI；CLOB gate 失败时也只影响 live_real 绩效发布，不改变本地 counterfactual 机会表实验。",
        "",
        "## Target Metric",
        "",
        "`forecast_tail_fade_range_rv_alpha` = forecast low-tail-risk / market high-tail-mass 条件下，BUY_NO tail + BUY_YES adjacent inner/center basket 相对同表达未过滤 baseline 的 excess ROI。",
        "",
        "固定表达：`below_tail_no_inner_yes`、`above_tail_no_inner_yes`、`both_tails_no_inner_yes`、`both_tails_no_center_yes`。没有使用旧单腿 `eligible` 作为硬门。",
        "",
        "## Filter Funnel",
        "",
        "| step | count |",
        "|---|---:|",
    ]
    for key, value in report["filter_funnel"]["top_level"].items():
        lines.append(f"| `{key}` | {value} |")

    lines.extend(
        [
            "",
            "### Strategy Filter Grid",
            "",
            "| algorithm | base | market overpriced | model risk | inner edge | spread | after uncertainty/no-trade |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["filter_funnel"]["strategy_filters"]:
        lines.append(
            f"| `{item['algorithm']}` | {item['base_rows']} | {item['market_tail_overpriced']} | "
            f"{item['model_tail_risk_max']} | {item['inner_confirm_edge']} | {item['spread_ok']} | "
            f"{item['high_uncertainty_no_trade']} |"
        )

    lines.extend(
        [
            "",
            "## Train / Holdout",
            "",
            f"- Split field: `event_date`; split date `{report['split']['split_date']}`.",
            f"- Train: `{report['split']['train_start']}` to `{report['split']['train_end']}`, active dates `{report['split']['train_active_dates']}`, generated rows `{report['split']['train_rows']}`.",
            f"- Holdout: `{report['split']['holdout_start']}` to `{report['split']['holdout_end']}`, active dates `{report['split']['holdout_active_dates']}`, generated rows `{report['split']['holdout_rows']}`.",
            f"- Bootstrap: cluster by `event_date`, iters `{report['parameters']['bootstrap_iters']}`.",
            "",
            "## Gates",
            "",
            "| gate | status |",
            "|---|---|",
        ]
    )
    for key, value in report["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            f"Final verdict: `{report['verdict']}`. 三门未同时通过时只能 `inconclusive`，禁止 live action。",
            "",
            "## Decision Proxy Results",
            "",
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train baseline | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout baseline | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout worst loss | holdout loss prob | gates | reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["decision_proxy_results_top"]:
        lines.append(selected_row_line(item))

    lines.extend(
        [
            "",
            "## Executable Orderbook Results",
            "",
            "Executable rows require all basket legs to match the latest orderbook snapshot satisfying `orderbook_snapshot_ts <= decision_snapshot_ts_utc`.",
            "",
            f"- Fully matched base rows: `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`.",
            f"- Matched expanded rows: `{report['filter_funnel']['top_level']['orderbook_matched_generated_rows']}`.",
            "",
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train baseline | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout baseline | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout worst loss | holdout loss prob | gates | reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["orderbook_results_top"]:
        lines.append(selected_row_line(item))

    lines.extend(
        [
            "",
            "## Best Rows",
            "",
            f"- Best decision proxy by holdout excess: `{best_proxy['algorithm'] if best_proxy else None}` with gates `{best_proxy['gates'] if best_proxy else None}`.",
            f"- Best executable orderbook by holdout excess: `{best_orderbook['algorithm'] if best_orderbook else None}` with gates `{best_orderbook['gates'] if best_orderbook else None}`.",
            "",
            "## Uncertainty Stratification",
            "",
            "Stratification is descriptive only; no city/date/model post-selection is used for verdict.",
            "",
        ]
    )
    for section, payload in (
        ("Decision Proxy", report["decision_proxy_stratification"]),
        ("Executable Orderbook", report["orderbook_stratification"]),
    ):
        lines.extend([f"### {section}", ""])
        for key, items in payload.items():
            lines.extend(
                [
                    f"#### `{key}`",
                    "",
                    "| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for item in items[:12]:
                bucket = item.get(key)
                lines.append(
                    f"| `{bucket}` | {item['rows']} | {item['active_event_dates']} | {item['taker_cost']:.2f} | "
                    f"{money(item['settled_pnl'])} | {pct(item['taker_roi'])} | {pct(item['drop_top5_taker_roi'])} | "
                    f"{money(item['avg_worst_case_loss_unit_notional'])} | {pct(item['avg_loss_probability'])} |"
                )
            lines.append("")

    lines.extend(
        [
            "## Coverage Rings",
            "",
            "| ring | status | note |",
            "|---|---|---|",
            "| 1 descriptive slices | covered | fixed expression + uncertainty descriptive splits |",
            "| 2 statistical inference | covered | event_date cluster bootstrap |",
            "| 3 signal discrimination | partial | uses model-vs-market tail mass, not a full IC test |",
            "| 4 probability distribution | partial | entropy/tail-mass layers only, no calibration refit |",
            "| 5 execution microstructure | covered | time-aligned orderbook executable subset |",
            "| 6 capacity | partial | depth fields not yet used for sizing capacity |",
            "| 7 portfolio correlation | partial | event_date clustered, no city covariance model |",
            "| 8 baseline/counterfactual | covered | same-expression unfiltered baseline |",
            "",
            "## Conclusion",
            "",
            f"在 `{report['split']['train_start']}..{report['split']['holdout_end']}`，Tail fade / uncertainty Range RV 相对同表达 baseline 的三门结果为 `{report['gates']}`，结论等级 `{report['verdict']}`。",
            "",
            f"Shadow/paper suitability: `{report['shadow_paper_suitability']}`.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    db_path = Path(args.db_path)

    raw_rows = scanner.scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")
    settled_present_rows = scanner.scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND final_yes IS NOT NULL
          AND decision_snapshot_ts_utc IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    base_rows, tail_counts = generate_base_rows(decision_sets)

    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(base_rows, args.orderbook_glob)

    generated_rows, filter_grid = expand_threshold_rows(base_rows)
    add_uncertainty_buckets(generated_rows)
    all_dates = sorted({row["event_date"] for row in generated_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = metric_rows(generated_rows, "proxy")
    orderbook_rows = metric_rows(generated_rows, "orderbook")

    proxy_results = evaluate_algorithms(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = evaluate_algorithms(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )

    proxy_results_sorted = sorted(proxy_results, key=rank_key, reverse=True)
    orderbook_results_sorted = sorted(orderbook_results, key=rank_key, reverse=True)
    confirmed = {item["algorithm"] for item in proxy_results if passed(item)} & {
        item["algorithm"] for item in orderbook_results if passed(item)
    }
    if confirmed:
        gates = {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        verdict = "confirmed"
        shadow_paper = "suitable_for_shadow_or_paper_engineering_review_only_not_live_direct"
    else:
        gates = {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
        verdict = "inconclusive"
        shadow_paper = "not_suitable_yet_three_gates_not_passed"

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "forecast_tail_fade_range_rv_alpha",
        "db_path": str(db_path),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "market_tail_overpriced_thresholds": MARKET_TAIL_OVERPRICED_THRESHOLDS,
            "model_tail_risk_max": MODEL_TAIL_RISK_MAX,
            "uncertainty_filters": UNCERTAINTY_FILTERS,
            "max_leg_spread": MAX_LEG_SPREAD,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_snapshot": data_snapshot(conn, db_path, ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"),
        "filter_funnel": {
            "top_level": {
                "fact_signal_candidates_rows": raw_rows,
                "settled_decision_window_present_rows": settled_present_rows,
                "decision_sets_count": len(decision_sets),
                **tail_counts,
                "base_strategy_rows": len(base_rows),
                "generated_strategy_rows": len(generated_rows),
                "orderbook_matched_base_rows": coverage.get("fully_matched_strategy_rows"),
                "orderbook_matched_generated_rows": len(orderbook_rows),
                "train_rows": sum(1 for row in generated_rows if row["event_date"] in train_dates),
                "holdout_rows": sum(1 for row in generated_rows if row["event_date"] in holdout_dates),
                "train_active_dates": len(train_dates),
                "holdout_active_dates": len(holdout_dates),
            },
            "strategy_filters": filter_grid,
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "train_active_dates": len(train_dates),
            "holdout_active_dates": len(holdout_dates),
            "train_rows": sum(1 for row in generated_rows if row["event_date"] in train_dates),
            "holdout_rows": sum(1 for row in generated_rows if row["event_date"] in holdout_dates),
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": proxy_results,
        "orderbook_results": orderbook_results,
        "decision_proxy_results_top": proxy_results_sorted[:20],
        "orderbook_results_top": orderbook_results_sorted[:20],
        "decision_proxy_stratification": stratified(
            proxy_rows,
            [
                "model_entropy_bucket",
                "model_tail_mass_bucket",
                "market_tail_mass_bucket",
                "tail_mass_delta_bucket",
                "decision_hours_to_settle_bucket",
                "forecast_source",
                "model_version",
            ],
            "decision_market_proxy",
        ),
        "orderbook_stratification": stratified(
            orderbook_rows,
            [
                "model_entropy_bucket",
                "model_tail_mass_bucket",
                "market_tail_mass_bucket",
                "tail_mass_delta_bucket",
                "decision_hours_to_settle_bucket",
                "forecast_source",
                "model_version",
            ],
            "time_aligned_orderbook",
        ),
        "confirmed_algorithms": sorted(confirmed),
        "best_decision_proxy_by_holdout_excess": proxy_results_sorted[0] if proxy_results_sorted else None,
        "best_orderbook_by_holdout_excess": orderbook_results_sorted[0] if orderbook_results_sorted else None,
        "gates": gates,
        "verdict": verdict,
        "shadow_paper_suitability": shadow_paper,
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={verdict} confirmed_algorithms={len(confirmed)}")
    print(f"orderbook_matched={coverage.get('fully_matched_strategy_rows')}/{coverage.get('strategy_rows')}")


if __name__ == "__main__":
    main()
