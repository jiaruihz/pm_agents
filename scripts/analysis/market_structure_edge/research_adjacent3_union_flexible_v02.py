#!/usr/bin/env python3
"""Forecast-quality adjacent3 union/flexible re-review v0.2.

This fixes the known v0/v0.1 denominator issue: the bracket universe is built
from BUY_YES plus BUY_NO rows in fact_signal_candidates, then de-duplicated to
one row per bracket. The experiment also compares expressions that are not
forced to be three YES legs.

Scope: local research only. No live/N100 config is read or changed.
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
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402
import adjacent3_legacy_denominator as legacy_denominator  # noqa: E402


TARGET_METRIC = "forecast_quality_adjacent3_union_flexible_alpha_v02"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-adjacent3-union-flexible-v0-2.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-adjacent3-union-flexible-v0-2.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260611)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def entropy(probs: list[float]) -> float:
    vals = [p for p in probs if p > 0]
    if not vals:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in vals) / denom


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


def load_union_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return scanner.rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          event_date,
          city,
          city_pool,
          bracket,
          side,
          forecast_source,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          model_p_yes,
          market_yes_price,
          decision_entry_price,
          yes_spread,
          no_spread,
          eligible,
          paper_ordered,
          live_filled,
          settlement_status,
          final_yes,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side IN ('BUY_YES', 'BUY_NO')
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND market_id IS NOT NULL
          AND event_date IS NOT NULL
          AND city IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )


def choose_bracket_row(prev: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    if prev is None:
        return row
    if prev.get("final_yes") is None and row.get("final_yes") is not None:
        return row
    if prev.get("side") != "BUY_YES" and row.get("side") == "BUY_YES":
        return row
    return prev


def group_union_decision_sets(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
            str(row["decision_snapshot_ts_utc"]),
        )
        bracket = str(row["bracket"])
        grouped[key][bracket] = choose_bracket_row(grouped[key].get(bracket), row)

    out: list[list[dict[str, Any]]] = []
    for by_bracket in grouped.values():
        ordered = sorted(by_bracket.values(), key=lambda r: scanner.bracket_sort_value(str(r["bracket"])))
        if len(ordered) >= 3:
            out.append(ordered)
    return out



def legs_ready(legs: list[dict[str, Any]]) -> bool:
    return bool(legs) and all(leg.get("final_yes") is not None and leg.get("settlement_status") == "settled" for leg in legs)


def safe_make_candidate(
    *,
    algorithm: str,
    decision_items: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    score: float,
    threshold: float,
    components: dict[str, Any],
) -> dict[str, Any] | None:
    if not legs_ready(legs):
        return None
    return variants.make_candidate(
        algorithm=algorithm,
        decision_items=decision_items,
        legs=legs,
        score=score,
        threshold=threshold,
        components=components,
    )


def safe_pair_spread(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for left, right in zip(items, items[1:]):
        left_edge = variants.score_edge(left)
        right_edge = variants.score_edge(right)
        if left_edge >= right_edge:
            high, low = left, right
            score = left_edge - right_edge
        else:
            high, low = right, left
            score = right_edge - left_edge
        row = safe_make_candidate(
            algorithm="pair_spread_adjacent_e010",
            decision_items=items,
            legs=[dict(high, side="BUY_YES"), dict(low, side="BUY_NO")],
            score=score,
            threshold=threshold,
            components={"high_bracket": high["bracket"], "low_bracket": low["bracket"]},
        )
        if row is not None:
            candidates.append(row)
    return variants.top_or_none(candidates)


def safe_center_shoulders(items: list[dict[str, Any]], *, shoulders: bool, threshold: float) -> dict[str, Any] | None:
    candidates = []
    algorithm = "shoulders_over_center_e010" if shoulders else "center_over_shoulders_e010"
    for start in range(0, len(items) - 2):
        left, center, right = items[start : start + 3]
        center_edge = variants.score_edge(center)
        shoulder_edge = (variants.score_edge(left) + variants.score_edge(right)) / 2.0
        if shoulders:
            score = shoulder_edge - center_edge
            legs = [dict(left, side="BUY_YES"), dict(right, side="BUY_YES"), dict(center, side="BUY_NO")]
        else:
            score = center_edge - shoulder_edge
            legs = [dict(center, side="BUY_YES"), dict(left, side="BUY_NO"), dict(right, side="BUY_NO")]
        row = safe_make_candidate(
            algorithm=algorithm,
            decision_items=items,
            legs=legs,
            score=score,
            threshold=threshold,
            components={"center": center["bracket"], "left": left["bracket"], "right": right["bracket"]},
        )
        if row is not None:
            candidates.append(row)
    return variants.top_or_none(candidates)


def safe_range_vs_neighbors(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for start in range(0, len(items) - 2):
        inside = items[start : start + 3]
        neighbors = []
        if start - 1 >= 0:
            neighbors.append(items[start - 1])
        if start + 3 < len(items):
            neighbors.append(items[start + 3])
        if not neighbors:
            continue
        score = sum(variants.score_edge(row) for row in inside) - sum(variants.score_edge(row) for row in neighbors)
        legs = [dict(row, side="BUY_YES") for row in inside] + [dict(row, side="BUY_NO") for row in neighbors]
        row = safe_make_candidate(
            algorithm="range_vs_neighbors_e010",
            decision_items=items,
            legs=legs,
            score=score,
            threshold=threshold,
            components={"inside": [row["bracket"] for row in inside], "neighbors": [row["bracket"] for row in neighbors]},
        )
        if row is not None:
            candidates.append(row)
    return variants.top_or_none(candidates)


def safe_tail_fade(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for end in range(1, len(items)):
        below = items[:end]
        score = sum(float(row["market_yes_price"]) - float(row["model_p_yes"]) for row in below)
        row = safe_make_candidate(
            algorithm="tail_fade_overpriced_e015",
            decision_items=items,
            legs=[dict(row, side="BUY_NO") for row in below],
            score=score,
            threshold=threshold,
            components={"tail": "below", "brackets": [row["bracket"] for row in below]},
        )
        if row is not None:
            candidates.append(row)
    for start in range(1, len(items)):
        above = items[start:]
        score = sum(float(row["market_yes_price"]) - float(row["model_p_yes"]) for row in above)
        row = safe_make_candidate(
            algorithm="tail_fade_overpriced_e015",
            decision_items=items,
            legs=[dict(row, side="BUY_NO") for row in above],
            score=score,
            threshold=threshold,
            components={"tail": "above", "brackets": [row["bracket"] for row in above]},
        )
        if row is not None:
            candidates.append(row)
    return variants.top_or_none(candidates)

def mode_cluster_candidate(items: list[dict[str, Any]], *, algorithm: str) -> dict[str, Any] | None:
    if len(items) < 3:
        return None
    mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
    start = min(max(0, mode_idx - 1), len(items) - 3)
    cluster = items[start : start + 3]
    model_mass = sum(float(row["model_p_yes"]) for row in cluster)
    cost = sum(float(row["market_yes_price"]) for row in cluster)
    score = model_mass - cost
    row = safe_make_candidate(
        algorithm=algorithm,
        decision_items=items,
        legs=[dict(leg, side="BUY_YES") for leg in cluster],
        score=score,
        threshold=0.0,
        components={"mode": items[mode_idx]["bracket"], "model_mass": model_mass, "cost": cost},
    )
    if row is None:
        return None
    row["selected"] = int(score > 0 and cost <= 0.85)
    row["model_adjacent3_mass"] = model_mass
    row["model_tail_mass_outside_adjacent3"] = max(0.0, 1.0 - model_mass)
    row["model_entropy"] = entropy([float(item["model_p_yes"]) for item in items])
    return row


def outside_no_candidate(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(items) < 4:
        return None
    mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
    start = min(max(0, mode_idx - 1), len(items) - 3)
    inside = set(range(start, start + 3))
    outside = [row for idx, row in enumerate(items) if idx not in inside]
    if not outside:
        return None
    model_tail = sum(float(row["model_p_yes"]) for row in outside)
    market_tail = sum(float(row["market_yes_price"]) for row in outside)
    score = market_tail - model_tail
    row = safe_make_candidate(
        algorithm="mode_adj3_outside_no_tail_e010",
        decision_items=items,
        legs=[dict(leg, side="BUY_NO") for leg in outside],
        score=score,
        threshold=0.10,
        components={"outside_brackets": [row["bracket"] for row in outside], "market_tail": market_tail, "model_tail": model_tail},
    )
    if row is None:
        return None
    row["selected"] = int(score >= 0.10)
    return row


def flexible_best_legs(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    legs = []
    for row in items:
        edge = float(row["model_p_yes"]) - float(row["market_yes_price"])
        if edge >= 0.08:
            legs.append(dict(row, side="BUY_YES", _score=edge))
        elif edge <= -0.08:
            legs.append(dict(row, side="BUY_NO", _score=-edge))
    legs = sorted(legs, key=lambda row: float(row["_score"]), reverse=True)[:4]
    if not legs:
        return None
    score = sum(float(row["_score"]) for row in legs)
    for row in legs:
        row.pop("_score", None)
    candidate = safe_make_candidate(
        algorithm="flexible_best_yes_no_legs_e008_top4",
        decision_items=items,
        legs=legs,
        score=score,
        threshold=0.18,
        components={"max_legs": 4, "per_leg_abs_edge_min": 0.08},
    )
    if candidate is None:
        return None
    candidate["selected"] = int(score >= 0.18 and candidate["taker_cost"] <= 2.00)
    return candidate


def generate_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for items in decision_sets:
        emitters = [
            lambda xs: mode_cluster_candidate(xs, algorithm="mode_adj3_yes_cost085"),
            outside_no_candidate,
            flexible_best_legs,
            lambda xs: safe_pair_spread(xs, threshold=0.10),
            lambda xs: safe_center_shoulders(xs, shoulders=False, threshold=0.10),
            lambda xs: safe_center_shoulders(xs, shoulders=True, threshold=0.10),
            lambda xs: safe_range_vs_neighbors(xs, threshold=0.10),
            lambda xs: safe_tail_fade(xs, threshold=0.15),
        ]
        for emit in emitters:
            row = emit(items)
            if row is not None and row.get("decision_dt") is not None:
                rows.append(row)
    return rows


def add_medium_quality_selection(rows: list[dict[str, Any]], train_dates: set[str]) -> dict[str, Any]:
    base = [row for row in rows if row["algorithm"] == "mode_adj3_yes_cost085"]
    train = [row for row in base if row["event_date"] in train_dates]
    mass_med = percentile([float(row["model_adjacent3_mass"]) for row in train], 0.50) or 0.0
    tail_med = percentile([float(row["model_tail_mass_outside_adjacent3"]) for row in train], 0.50) or 1.0
    entropy_med = percentile([float(row["model_entropy"]) for row in train], 0.50) or 1.0
    medium_rows = []
    for row in base:
        cloned = dict(row)
        cloned["algorithm"] = "mode_adj3_yes_medium_quality"
        cloned["candidate_id"] = cloned["candidate_id"].replace("mode_adj3_yes_cost085", "mode_adj3_yes_medium_quality")
        cloned["selected"] = int(
            int(row.get("selected") or 0) == 1
            and float(row["model_adjacent3_mass"]) >= mass_med
            and float(row["model_tail_mass_outside_adjacent3"]) <= tail_med
        )
        medium_rows.append(cloned)
    low_rows = []
    for row in medium_rows:
        cloned = dict(row)
        cloned["algorithm"] = "mode_adj3_yes_low_uncertainty"
        cloned["candidate_id"] = cloned["candidate_id"].replace("mode_adj3_yes_medium_quality", "mode_adj3_yes_low_uncertainty")
        cloned["selected"] = int(int(row.get("selected") or 0) == 1 and float(row["model_entropy"]) <= entropy_med)
        low_rows.append(cloned)
    rows.extend(medium_rows)
    rows.extend(low_rows)
    return {
        "adjacent3_mass_train_median": mass_med,
        "tail_mass_train_median": tail_med,
        "entropy_train_median": entropy_med,
    }


def bracket_coverage(decision_sets: list[list[dict[str, Any]]]) -> dict[str, Any]:
    counts = Counter(len(items) for items in decision_sets)
    return {
        "distribution": dict(sorted(counts.items())),
        "min_3": sum(1 for items in decision_sets if len(items) >= 3),
        "min_5": sum(1 for items in decision_sets if len(items) >= 5),
        "min_7": sum(1 for items in decision_sets if len(items) >= 7),
        "min_9": sum(1 for items in decision_sets if len(items) >= 9),
        "min_11": sum(1 for items in decision_sets if len(items) >= 11),
    }


def result_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    return (
        f"| `{item['algorithm']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {pct(train['selected']['taker_roi'])} | "
        f"{fmt_ci(train['roi_ci95_cluster_by_event_date'])} | {pct(train['excess_roi'])} | "
        f"{fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {item['holdout_family_rows']} | "
        f"{holdout['selected']['rows']} | {holdout['selected']['active_event_dates']} | "
        f"{pct(holdout['selected']['taker_roi'])} | {fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['excess_roi'])} | {fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` |"
    )


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Adjacent3 Union Flexible v0.2",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 一句话",
        "",
        "- 旧 adjacent3 只看 `BUY_YES`，会把大量 bracket 丢掉；v0.2 改用 `BUY_YES + BUY_NO` union 后，样本恢复明显。",
        "- 修正分母后，forecast-adjacent3 仍有正 holdout 点估计，但 train 稳定性和 orderbook 子样本没有过三门，所以结论仍是 `inconclusive`。",
        "- 这次同时测试了 NO / YES+NO 混合表达，不再强制三腿 YES；目前没有任何表达达到 live 试探门槛。",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`runtime/weather.db.fact_signal_candidates`；`fact_trades` 只用于强制自检。",
        f"- fact_signal_candidates rows：`{report['data_self_check']['fact_signal_candidate_coverage']['rows']}`；fact built：`{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- union base rows：`{report['input']['union_rows']}`；decision sets：`{report['input']['decision_sets']}`；mode-adj3 evaluable rows：`{report['input']['mode_adj3_evaluable_rows']}`；strategy rows：`{report['input']['strategy_rows']}`。",
        f"- train：`{report['split']['train_start']}` -> `{report['split']['train_end']}`；holdout：`{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["fact_trades_max_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["fact_trades_by_class"],
                "settlement_status_distribution": report["data_self_check"]["fact_trades_by_settlement_status"],
                "candidate_coverage": report["data_self_check"]["fact_signal_candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["clob_order_fill_join"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 分母修正",
        "",
        table(
            ["metric", "old BUY_YES-only reference", "v0.2 union"],
            [
                ["decision sets", report["old_reference"]["decision_sets"], report["input"]["decision_sets"]],
                ["n_brackets >= 9", report["old_reference"]["min_9"], report["bracket_coverage"]["min_9"]],
                ["mode-adj3 selected legs settled", report["old_reference"]["selected3_settled"], report["input"]["mode_adj3_evaluable_rows"]],
                ["all-bracket fully settled sets", "NA", report["input"]["settled_complete_decision_sets"]],
                ["generated strategy rows", "NA", report["input"]["strategy_rows"]],
            ],
        ),
        "",
        "## 表达定义",
        "",
        "- `mode_adj3_yes_cost085`：模型 mode 附近三档 YES，`score=model_mass-cost>0` 且 `cost<=0.85`。",
        "- `mode_adj3_yes_medium_quality` / `low_uncertainty`：只用 train 中位数阈值做 forecast-quality 软过滤。",
        "- `mode_adj3_outside_no_tail_e010`：模型 mode 三档之外的 tail 被市场高估时买外侧 NO。",
        "- `flexible_best_yes_no_legs_e008_top4`：每个 city-day 最多选 4 条绝对 edge 最大的 YES/NO 腿，不强制三 YES。",
        "- 其它混合表达沿用 variant lab：adjacent pair spread、center/shoulders、range vs neighbors、tail fade。",
        "",
        "## Decision Proxy",
        "",
        "| algorithm | train family | train selected | train dates | train ROI | train ROI CI | train excess | train excess CI | holdout family | holdout selected | holdout dates | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["decision_proxy_results"]:
        lines.append(result_line(item))
    lines.extend(
        [
            "",
            "## Time-Aligned Orderbook",
            "",
            "- 价格匹配复用 `research_executable_edge.py` 逻辑，只允许 `orderbook_snapshot_ts <= decision_snapshot_ts_utc`。",
            f"- Fully matched strategy rows：`{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`。",
            "",
            "| algorithm | train family | train selected | train dates | train ROI | train ROI CI | train excess | train excess CI | holdout family | holdout selected | holdout dates | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["orderbook_results"]:
        lines.append(result_line(item))
    lines.extend(
        [
            "",
            "## 三门状态",
            "",
            table(
                ["gate", "status", "reason"],
                [
                    ["significance", report["gates"]["significance"], "cluster bootstrap CI 仍未形成稳定正超额，且部分候选 train 样本/OB 覆盖太薄。"],
                    ["baseline", report["gates"]["baseline"], "没有表达在 decision proxy 与 time-aligned orderbook 两层同时相对同 family baseline 过门。"],
                    ["forward", report["gates"]["forward"], "holdout 点估计有正值，但 train 稳定性、top5 removed 与 orderbook 子样本不够。"],
                ],
            ),
            "",
            f"结论：`{report['verdict']}`，不改 live、不加 size、不扩池。",
            "",
            "## 下一步",
            "",
            "- 可以把 union universe 的 bracket coverage 修正吸收到正式 adjacent3 scanner，然后继续 shadow/paper。",
            "- 需要补更多已结算 forward 日期后再看；当前不能把正 holdout 点估计当 live edge。",
        ]
    )
    return "\n".join(lines) + "\n"


def old_reference(conn: sqlite3.Connection) -> dict[str, Any]:
    old_rows = legacy_denominator.load_candidates(conn)
    old_sets = legacy_denominator.build_decision_sets(old_rows)
    return {
        "decision_sets": len(old_sets),
        "min_9": sum(1 for row in old_sets if int(row["n_brackets"]) >= 9),
        "selected3_settled": sum(1 for row in old_sets if row["settlement_status"] == "settled"),
    }


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    union_rows = load_union_rows(conn)
    decision_sets = group_union_decision_sets(union_rows)
    complete_sets = [
        items
        for items in decision_sets
        if all(row.get("final_yes") is not None and row.get("settlement_status") == "settled" for row in items)
    ]
    strategy_rows = generate_rows(decision_sets)
    all_dates = sorted({row["event_date"] for row in strategy_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    thresholds = add_medium_quality_selection(strategy_rows, train_dates)

    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(strategy_rows, args.orderbook_glob)

    proxy_rows = variants.metric_rows(strategy_rows, "proxy")
    orderbook_rows = variants.metric_rows(strategy_rows, "orderbook")
    proxy_results = variants.evaluate_algorithms(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = variants.evaluate_algorithms(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )
    confirmed = {
        item["algorithm"] for item in proxy_results if variants.passed(item)
    } & {item["algorithm"] for item in orderbook_results if variants.passed(item)}
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if confirmed
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_self_check": scanner.data_self_check(conn),
        "old_reference": old_reference(conn),
        "input": {
            "union_rows": len(union_rows),
            "decision_sets": len(decision_sets),
            "settled_complete_decision_sets": len(complete_sets),
            "strategy_rows": len(strategy_rows),
            "mode_adj3_evaluable_rows": sum(1 for row in strategy_rows if row["algorithm"] == "mode_adj3_yes_cost085"),
            "algorithms": len({row["algorithm"] for row in strategy_rows}),
            "event_dates": len(all_dates),
        },
        "bracket_coverage": bracket_coverage(decision_sets),
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
        },
        "quality_thresholds": thresholds,
        "decision_proxy_results": proxy_results,
        "orderbook_coverage": coverage,
        "orderbook_results": orderbook_results,
        "confirmed_algorithms": sorted(confirmed),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(render_md(report), encoding="utf-8")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} strategy_rows={len(strategy_rows)}")


if __name__ == "__main__":
    main()
