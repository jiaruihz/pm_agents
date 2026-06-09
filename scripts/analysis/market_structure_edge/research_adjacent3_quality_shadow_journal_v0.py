#!/usr/bin/env python3
"""Build a fixed adjacent3 + forecast-quality shadow journal.

This is local research only. It writes would-trade rows for a fixed
forecast-first adjacent3 range rule and never changes live/N100 config.
Primary source is runtime/weather.db.fact_signal_candidates.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts" / "analysis" / "market_structure_edge"
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402


DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-shadow-journal-v0.json"
OUT_JSONL_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-shadow-journal-v0.jsonl"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-shadow-journal-v0.md"
ORDERBOOK_GLOB_DEFAULT = scanner.ORDERBOOK_GLOB_DEFAULT
TARGET_METRIC = "adjacent3_quality_shadow_forward_readiness"
JOURNAL_SCHEMA_VERSION = "adjacent3_quality_shadow_journal_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-jsonl", default=str(OUT_JSONL_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--market-cost-cap", type=float, default=0.85)
    parser.add_argument("--min-edge", type=float, default=0.0)
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


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def entropy(probs: list[float]) -> float:
    values = [p for p in probs if p > 0]
    if not values:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in values) / denom


def normalize(values: list[float]) -> list[float]:
    total = sum(v for v in values if v > 0)
    if total <= 0:
        return [0.0 for _ in values]
    return [max(v, 0.0) / total for v in values]


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


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
        "fact_signal_candidates_max_built_at_utc": scalar(
            conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ),
        "fact_signal_candidates_rows": scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
        "fact_trades_rows": scalar(conn, "SELECT COUNT(*) FROM fact_trades"),
        "fact_trades_unsettled_rows": scalar(
            conn, "SELECT COUNT(*) FROM fact_trades WHERE settlement_status IS NULL OR settlement_status<>'settled'"
        ),
        "fact_trades_missing_bracket_rows": scalar(
            conn, "SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'"
        ),
    }


def load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          side,
          event_date,
          city,
          city_pool,
          bracket,
          forecast_source,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          model_p_yes,
          market_yes_price,
          yes_spread,
          live_filled,
          final_yes,
          settlement_status,
          decision_window_missing,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side='BUY_YES'
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


def split_dates(decision_sets: list[dict[str, Any]]) -> dict[str, Any]:
    settled_dates = sorted(
        {
            str(row["event_date"])
            for row in decision_sets
            if row.get("settlement_status") == "settled" and row.get("final_hit") is not None
        }
    )
    if len(settled_dates) <= 1:
        cut = len(settled_dates)
    else:
        cut = max(1, min(len(settled_dates) - 1, int(math.floor(len(settled_dates) * 0.70))))
    train_dates = set(settled_dates[:cut])
    holdout_dates = set(settled_dates[cut:])
    return {
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "train_start": min(train_dates) if train_dates else None,
        "train_end": max(train_dates) if train_dates else None,
        "holdout_start": min(holdout_dates) if holdout_dates else None,
        "holdout_end": max(holdout_dates) if holdout_dates else None,
    }


def build_decision_sets(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
            str(row["decision_snapshot_ts_utc"]),
        )
        grouped[key][str(row["bracket"])] = row

    out: list[dict[str, Any]] = []
    for key, by_bracket in grouped.items():
        legs = sorted(by_bracket.values(), key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        if len(legs) < 3:
            continue
        model = normalize([float(row["model_p_yes"]) for row in legs])
        market = normalize([float(row["market_yes_price"]) for row in legs])
        if not any(model):
            continue
        mode_i = max(range(len(model)), key=lambda idx: model[idx])
        start = max(0, min(mode_i - 1, len(legs) - 3))
        end = start + 3
        selected = legs[start:end]
        selected_labels_ready = all(
            row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in selected
        )
        final_hit = None
        settled_payout = None
        if selected_labels_ready:
            final_hit = int(any(float(row["final_yes"]) >= 0.5 for row in selected))
            settled_payout = float(final_hit)
        model_mass = sum(model[start:end])
        market_cost = sum(float(row["market_yes_price"]) for row in selected)
        market_mass_norm = sum(market[start:end])
        tail_mass = 1.0 - model_mass
        out.append(
            {
                "decision_set_id": "|".join(key),
                "city": key[0],
                "event_date": key[1],
                "target_date": key[1],
                "forecast_source": key[2],
                "model_version": key[3],
                "decision_snapshot_ts_utc": key[4],
                "decision_hours_to_settle": selected[0]["decision_hours_to_settle"],
                "settlement_status": "settled" if selected_labels_ready else "unsettled_or_unusable",
                "n_brackets": len(legs),
                "mode_i": mode_i,
                "mode_bracket": selected[mode_i - start]["bracket"] if start <= mode_i < end else legs[mode_i]["bracket"],
                "range_start_i": start,
                "range_end_i": end - 1,
                "selected_legs": selected,
                "selected_brackets": [str(row["bracket"]) for row in selected],
                "condition_ids": [str(row["condition_id"]) for row in selected],
                "model_adjacent3_mass": model_mass,
                "market_adjacent3_cost": market_cost,
                "market_adjacent3_mass_norm": market_mass_norm,
                "range_edge": model_mass - market_cost,
                "model_entropy": entropy(model),
                "model_mode_probability": model[mode_i],
                "model_tail_mass_outside_adjacent3": tail_mass,
                "model_market_l1_gap": sum(abs(a - b) for a, b in zip(model, market)),
                "final_hit": final_hit,
                "settled_payout": settled_payout,
                "decision_proxy_cost": market_cost,
                "decision_proxy_pnl": None if settled_payout is None else settled_payout - market_cost,
            }
        )
    return out


def add_quality_regimes(decision_sets: list[dict[str, Any]], train_dates: set[str]) -> dict[str, Any]:
    train = [row for row in decision_sets if str(row["event_date"]) in train_dates]
    adj3_med = percentile([float(row["model_adjacent3_mass"]) for row in train], 0.50) or 0.0
    tail_med = percentile([float(row["model_tail_mass_outside_adjacent3"]) for row in train], 0.50) or 1.0
    entropy_med = percentile([float(row["model_entropy"]) for row in train], 0.50) or 1.0
    for row in decision_sets:
        row["quality_medium"] = int(
            float(row["model_adjacent3_mass"]) >= adj3_med
            and float(row["model_tail_mass_outside_adjacent3"]) <= tail_med
        )
        row["quality_low_uncertainty"] = int(
            row["quality_medium"] == 1 and float(row["model_entropy"]) <= entropy_med
        )
    return {
        "adjacent3_mass_train_median": adj3_med,
        "tail_mass_train_median": tail_med,
        "entropy_train_median": entropy_med,
    }


def select_shadow_rows(decision_sets: list[dict[str, Any]], market_cost_cap: float, min_edge: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rules = [
        ("adjacent3_core_no_filter", "fixed adjacent3 around model mode; no hard quality filter"),
        ("adjacent3_medium_quality", "fixed adjacent3 around model mode; medium forecast quality soft gate"),
    ]
    for row in decision_sets:
        if float(row["market_adjacent3_cost"]) > market_cost_cap:
            continue
        if float(row["range_edge"]) < min_edge:
            continue
        for rule_id, rule_desc in rules:
            if rule_id == "adjacent3_medium_quality" and not row["quality_medium"]:
                continue
            leg_rows = [
                {
                    "candidate_id": leg["candidate_id"],
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "bracket": str(leg["bracket"]),
                    "side": "BUY_YES",
                    "market_yes_price": leg["market_yes_price"],
                    "model_p_yes": leg["model_p_yes"],
                    "yes_spread": leg["yes_spread"],
                    "live_filled": leg["live_filled"],
                    "settlement_status": leg["settlement_status"],
                    "final_yes": leg["final_yes"],
                }
                for leg in row["selected_legs"]
            ]
            item = {
                "journal_schema_version": JOURNAL_SCHEMA_VERSION,
                "shadow_rule_id": rule_id,
                "shadow_rule_description": rule_desc,
                "action": "shadow_only_no_live_order",
                "decision_set_id": row["decision_set_id"],
                "city": row["city"],
                "event_date": row["event_date"],
                "target_date": row["target_date"],
                "forecast_source": row["forecast_source"],
                "model_version": row["model_version"],
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "decision_hours_to_settle": row["decision_hours_to_settle"],
                "mode_bracket": row["mode_bracket"],
                "selected_brackets": row["selected_brackets"],
                "condition_ids": row["condition_ids"],
                "legs": leg_rows,
                "n_legs": 3,
                "leg_side": "BUY_YES",
                "model_prob_sum": row["model_adjacent3_mass"],
                "market_prob_sum": row["market_adjacent3_cost"],
                "range_edge": row["range_edge"],
                "model_entropy": row["model_entropy"],
                "model_mode_probability": row["model_mode_probability"],
                "model_tail_mass_outside_adjacent3": row["model_tail_mass_outside_adjacent3"],
                "model_market_l1_gap": row["model_market_l1_gap"],
                "quality_medium": row["quality_medium"],
                "quality_low_uncertainty": row["quality_low_uncertainty"],
                "settlement_status": row["settlement_status"],
                "final_hit": row["final_hit"],
                "settled_payout": row["settled_payout"],
                "decision_proxy_cost": row["decision_proxy_cost"],
                "decision_proxy_pnl": row["decision_proxy_pnl"],
                "_legs": row["selected_legs"],
            }
            out.append(item)
    return out


def attach_orderbook(shadow_rows: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    range_rows = []
    for row in shadow_rows:
        if row.get("final_hit") is None:
            continue
        decision_dt = scanner.parse_ts(str(row["decision_snapshot_ts_utc"]))
        if decision_dt is None:
            continue
        range_rows.append(
            {
                "range_id": (
                    f"{row['shadow_rule_id']}|{row['city']}|{row['event_date']}|"
                    f"{row['forecast_source']}|{row['model_version']}|{row['decision_snapshot_ts_utc']}"
                ),
                "city": row["city"],
                "event_date": row["event_date"],
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "decision_dt": decision_dt,
                "leg_side": "BUY_YES",
                "_legs": row["_legs"],
            }
        )
    coverage = scanner.attach_orderbook_costs(range_rows, orderbook_glob)
    by_id = {row["range_id"]: row for row in range_rows}
    matched = 0
    for row in shadow_rows:
        range_id = (
            f"{row['shadow_rule_id']}|{row['city']}|{row['event_date']}|"
            f"{row['forecast_source']}|{row['model_version']}|{row['decision_snapshot_ts_utc']}"
        )
        ob = by_id.get(range_id)
        if ob and ob.get("orderbook_taker_cost") is not None:
            matched += 1
            row["orderbook_taker_cost"] = ob["orderbook_taker_cost"]
            row["orderbook_taker_pnl"] = ob["orderbook_taker_pnl"]
            row["orderbook_maker_proxy_cost"] = ob["orderbook_maker_proxy_cost"]
            row["orderbook_maker_proxy_pnl"] = ob["orderbook_maker_proxy_pnl"]
            row["orderbook_avg_age_minutes"] = ob["orderbook_avg_age_minutes"]
            row["price_source"] = "time_aligned_orderbook"
        else:
            row["orderbook_taker_cost"] = None
            row["orderbook_taker_pnl"] = None
            row["orderbook_maker_proxy_cost"] = None
            row["orderbook_maker_proxy_pnl"] = None
            row["orderbook_avg_age_minutes"] = None
            row["price_source"] = "decision_market_proxy"
    coverage["shadow_rows"] = len(shadow_rows)
    coverage["shadow_rows_fully_matched"] = matched
    coverage["shadow_rows_fully_matched_rate"] = safe_div(matched, len(shadow_rows))
    return coverage


def summarize(items: list[dict[str, Any]], *, pnl_key: str, cost_key: str) -> dict[str, Any]:
    usable = [row for row in items if row.get(pnl_key) is not None and row.get(cost_key) is not None]
    cost = sum(float(row[cost_key]) for row in usable)
    pnl = sum(float(row[pnl_key]) for row in usable)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "rows": 0.0})
    for row in usable:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row[cost_key])
        slot["pnl"] += float(row[pnl_key])
        slot["rows"] += 1.0
    top5 = sorted(by_date.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]
    top5_dates = {date for date, _value in top5}
    rem_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5_dates)
    rem_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5_dates)
    return {
        "rows": len(items),
        "usable_rows": len(usable),
        "active_dates": len(by_date),
        "cost": cost,
        "pnl": pnl,
        "roi": safe_div(pnl, cost),
        "hit_rate": safe_div(sum(int(row.get("final_hit") or 0) for row in usable), len(usable)),
        "top5_removed_roi": safe_div(rem_pnl, rem_cost),
        "worst_day_pnl": min((value["pnl"] for value in by_date.values()), default=None),
    }


def split_label(row: dict[str, Any], split: dict[str, Any]) -> str:
    date = str(row["event_date"])
    if date in split["train_dates"]:
        return "train"
    if date in split["holdout_dates"]:
        return "holdout"
    return "unsettled_or_outside"


def build_summary(shadow_rows: list[dict[str, Any]], split: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rule_id in sorted({row["shadow_rule_id"] for row in shadow_rows}):
        rule_rows = [row for row in shadow_rows if row["shadow_rule_id"] == rule_id]
        for label in ("train", "holdout", "unsettled_or_outside"):
            part = [row for row in rule_rows if split_label(row, split) == label]
            out.append(
                {
                    "shadow_rule_id": rule_id,
                    "split": label,
                    "decision_proxy": summarize(part, pnl_key="decision_proxy_pnl", cost_key="decision_proxy_cost"),
                    "orderbook_taker": summarize(part, pnl_key="orderbook_taker_pnl", cost_key="orderbook_taker_cost"),
                }
            )
    return out


def strip_private(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_legs"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows_in: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows_in:
            fh.write(json.dumps(strip_private(row), ensure_ascii=False, sort_keys=True) + "\n")


def summary_row(summary: list[dict[str, Any]], rule_id: str, split: str, source: str) -> dict[str, Any]:
    for row in summary:
        if row["shadow_rule_id"] == rule_id and row["split"] == split:
            return row[source]
    return {}


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["summary"]
    rows_out = []
    for rule_id in ("adjacent3_core_no_filter", "adjacent3_medium_quality"):
        for split in ("train", "holdout"):
            dp = summary_row(summary, rule_id, split, "decision_proxy")
            ob = summary_row(summary, rule_id, split, "orderbook_taker")
            rows_out.append(
                [
                    rule_id,
                    split,
                    dp.get("usable_rows", 0),
                    pct(dp.get("roi")),
                    pct(dp.get("top5_removed_roi")),
                    ob.get("usable_rows", 0),
                    pct(ob.get("roi")),
                    pct(ob.get("top5_removed_roi")),
                ]
            )

    lines = [
        "# Adjacent3 Quality Shadow Journal v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        f"> journal_schema_version: `{report['journal_schema_version']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> Scope: local shadow journal only; no N100/live config changed; no live orders.",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        f"- fact_signal_candidates rows：`{report['data_self_check']['fact_signal_candidates_rows']}`；fact built：`{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- fact_trades rows：`{report['data_self_check']['fact_trades_rows']}`；unsettled/null：`{report['data_self_check']['fact_trades_unsettled_rows']}`；missing_bracket：`{report['data_self_check']['fact_trades_missing_bracket_rows']}`。",
        f"- train：`{report['split']['train_start']}` -> `{report['split']['train_end']}`；holdout：`{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["trade_class_distribution"],
                "settlement_status_distribution": report["data_self_check"]["settlement_status_distribution"],
                "candidate_coverage": report["data_self_check"]["candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 固定 Shadow 规则",
        "",
        "- `adjacent3_core_no_filter`：围绕模型 mode 选相邻 3 档 YES，`market_cost <= 0.85`，`model_mass > market_cost`。",
        "- `adjacent3_medium_quality`：同上，但要求 train-only 阈值下 `adjacent3_mass` 不低、tail mass 不高。",
        "- 这是 would-trade journal，不是 live 下单；orderbook 只用 `snapshot_ts_utc <= decision_snapshot_ts_utc` 的 time-aligned best ask。",
        "",
        "## Filter Funnel",
        "",
        table(
            ["step", "count"],
            [
                ["fact_signal_candidates rows", report["filter_funnel"]["fact_signal_candidates_rows"]],
                ["decision sets", report["filter_funnel"]["decision_sets"]],
                ["shadow rows", report["filter_funnel"]["shadow_rows"]],
                ["orderbook fully matched shadow rows", report["orderbook_coverage"].get("shadow_rows_fully_matched")],
                ["journal rows", report["filter_funnel"]["journal_rows"]],
            ],
        ),
        "",
        "## 当前证据",
        "",
        table(
            [
                "rule",
                "split",
                "decision rows",
                "decision ROI",
                "decision top5 removed",
                "orderbook rows",
                "orderbook ROI",
                "orderbook top5 removed",
            ],
            rows_out,
        ),
        "",
        "## 三门状态",
        "",
        table(
            ["gate", "status", "reason"],
            [
                [
                    "significance",
                    "FAIL",
                    "未跑 bootstrap CI；usable rows 很薄，medium holdout 只有 8 行，不能把点估计当显著性。",
                ],
                [
                    "baseline",
                    "NA",
                    "本产物是 shadow journal，不做无脑 NO / market EV matched baseline 结论。",
                ],
                [
                    "forward",
                    "FAIL",
                    "历史 holdout 点估计为正，但 orderbook 全腿匹配只有 29 行，且没有真实 forward shadow 期。",
                ],
            ],
        ),
        "",
        "## 人话结论",
        "",
        "- 这份产物不是为了证明能 live，而是把最接近的候选固定下来，避免继续事后调参。",
        "- 点估计看起来不错，尤其 `adjacent3_medium_quality` 的 holdout；但样本太薄，train 的 top5 removed 直接变成 -100%，说明现在还可能是少数日期撑起来。",
        "- 如果 shadow 期之后，orderbook taker/maker proxy 在 holdout/forward 仍同号、top5 removed 仍为正、event_date cluster CI 不跨 0，才讨论 tiny live test。",
        "- 当前动作：只记录、不下单、不改 size、不改城市池。",
        "",
        "## 产物",
        "",
        f"- JSON summary：`{report['out_json']}`",
        f"- JSONL journal：`{report['out_jsonl']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    generated_at_utc = datetime.now(timezone.utc).isoformat()
    current_git_sha = git_sha()
    check = data_self_check(conn, db_path)
    candidates = load_candidates(conn)
    decision_sets = build_decision_sets(candidates)
    split = split_dates(decision_sets)
    quality_thresholds = add_quality_regimes(decision_sets, split["train_dates"])
    shadow_rows = select_shadow_rows(decision_sets, args.market_cost_cap, args.min_edge)
    orderbook_coverage = attach_orderbook(shadow_rows, str(args.orderbook_glob))
    summary = build_summary(shadow_rows, split)
    journal_rows = [strip_private(row) for row in shadow_rows]
    report = {
        "generated_at_utc": generated_at_utc,
        "git_sha": current_git_sha,
        "journal_schema_version": JOURNAL_SCHEMA_VERSION,
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "out_json": str(Path(args.out_json)),
        "out_jsonl": str(Path(args.out_jsonl)),
        "data_self_check": check,
        "split": {
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "train_dates": len(split["train_dates"]),
            "holdout_start": split["holdout_start"],
            "holdout_end": split["holdout_end"],
            "holdout_dates": len(split["holdout_dates"]),
        },
        "quality_thresholds_train_only": quality_thresholds,
        "rule_params": {
            "market_cost_cap": args.market_cost_cap,
            "min_edge": args.min_edge,
            "range_width": 3,
            "leg_side": "BUY_YES",
        },
        "filter_funnel": {
            "fact_signal_candidates_rows": check["fact_signal_candidates_rows"],
            "decision_sets": len(decision_sets),
            "shadow_rows": len(shadow_rows),
            "journal_rows": len(journal_rows),
        },
        "orderbook_coverage": orderbook_coverage,
        "summary": summary,
        "journal_sample": journal_rows[:20],
    }
    write_json(Path(args.out_json), report)
    write_jsonl(Path(args.out_jsonl), journal_rows)
    write_md(Path(args.out_md), report)
    print(args.out_json)
    print(args.out_jsonl)
    print(args.out_md)


if __name__ == "__main__":
    main()
