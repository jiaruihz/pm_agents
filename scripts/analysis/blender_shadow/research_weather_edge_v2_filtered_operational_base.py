"""
research_weather_edge_v2_filtered_operational_base.py

Filtered operational-base rerun for weather_edge_v2 research.

This is an offline research orchestrator. It keeps the current live operational
base as the first-layer universe filter:

  - exclude Ankara, BuenosAires, Jeddah, Karachi, Moscow, Munich
  - decision_hours_to_settle <= 28

Then it reruns the raw/blend/side-band/basket comparisons, distribution sanity,
and walk-forward checks on the same filtered base.

No N100/live order behavior is changed.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.city_selection.compare_city_day_basket_vs_legacy_baselines import (  # noqa: E402
    PROFILES,
    _decide_legacy_profile,
    _evaluate_profile,
    _entry_band_universe,
    _legacy_trigger_universe,
    _load_rows as _load_compare_rows,
)
from scripts.analysis.city_selection.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    Leg,
    _attribution,
    _summarize,
)
from scripts.analysis.city_selection.research_city_day_basket_optimizer import (  # noqa: E402
    ComboLeg,
    _candidate_legs_for_group,
    _cvar20,
    _decide_combo_optimizer,
    _evaluate_slice as _evaluate_optimizer_slice,
    _expected_value,
    _leave_best_out_ev,
    _payoff_by_temp,
    _probability_distribution,
    _to_eval_leg,
    _valid_combo,
)
from scripts.analysis.city_selection.research_city_day_basket_walkforward import (  # noqa: E402
    _aggregate_folds,
    _walkforward,
)
from scripts.analysis.city_selection.research_city_day_distribution_quality import _evaluate as _evaluate_distribution  # noqa: E402


REMOVED_CITIES = {"Ankara", "BuenosAires", "Jeddah", "Karachi", "Moscow", "Munich"}
MAX_DECISION_HOURS_TO_SETTLE = 28.0
REPORT_STEM = "weather-edge-v2-filtered-operational-base-research"


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _self_checks(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        checks = {
            "fact_built_at": _rows(
                conn,
                "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades",
            ),
            "trade_class_distribution": _rows(
                conn,
                """
                SELECT trade_class, COUNT(*) AS rows
                FROM fact_trades
                GROUP BY trade_class
                ORDER BY trade_class
                """,
            ),
            "settlement_status_distribution": _rows(
                conn,
                """
                SELECT settlement_status, COUNT(*) AS rows
                FROM fact_trades
                GROUP BY settlement_status
                ORDER BY settlement_status
                """,
            ),
            "signal_candidate_coverage": _rows(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       SUM(eligible) AS eligible,
                       SUM(paper_ordered) AS paper_ordered,
                       SUM(live_filled) AS live_filled
                FROM fact_signal_candidates
                """,
            ),
            "clob_order_fill_status": _rows(
                conn,
                """
                SELECT o.status,
                       COUNT(*) AS orders,
                       SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
                FROM orders o
                LEFT JOIN fills f USING(execution_id)
                WHERE o.venue='polymarket_clob'
                GROUP BY o.status
                ORDER BY o.status
                """,
            ),
            "fact_rows": _rows(
                conn,
                """
                SELECT COUNT(*) AS fact_rows,
                       SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
                       SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows
                FROM fact_trades
                """,
            ),
        }
    checks["db_mtime_bj"] = dt.datetime.fromtimestamp(
        os.path.getmtime(db_path),
        dt.timezone(dt.timedelta(hours=8)),
    ).isoformat(timespec="seconds")
    return checks


def _run_clob_gate(db_path: Path) -> dict[str, Any]:
    out_path = _ROOT / "runtime" / "_dashboard_logs" / "edge_v2_filtered_operational_base_clob_gate.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(
            _ROOT
            / "scripts"
            / "analysis"
            / "execution_quality"
            / "weather_clob_fill_coverage_gate.py"
        ),
        "--db",
        str(db_path),
        "--json-out",
        str(out_path),
    ]
    proc = subprocess.run(cmd, cwd=str(_ROOT), text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return {
            "gate_pass": False,
            "error": proc.stderr.strip() or proc.stdout.strip(),
            "json_path": str(out_path),
        }
    return json.loads(out_path.read_text(encoding="utf-8"))


def _apply_operational_base(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["decision_hours_to_settle"] = pd.to_numeric(
        out["decision_hours_to_settle"], errors="coerce"
    )
    return out[
        (~out["city"].isin(REMOVED_CITIES))
        & (out["decision_hours_to_settle"] <= MAX_DECISION_HOURS_TO_SETTLE)
    ].reset_index(drop=True)


def _top_removed_roi_from_rows(rows: list[dict[str, Any]], remove_n: int) -> float:
    if len(rows) <= remove_n:
        return 0.0
    ranked = sorted(
        rows,
        key=lambda r: float(r.get("pnl_usd_at_fill") or 0.0),
        reverse=True,
    )
    kept = ranked[remove_n:]
    cost = sum(float(r.get("cost_usd") or 0.0) for r in kept)
    pnl = sum(float(r.get("pnl_usd_at_fill") or 0.0) for r in kept)
    return pnl / cost if cost > 0 else 0.0


def _actual_live_operational_base(db_path: Path) -> dict[str, Any]:
    city_placeholders = ",".join("?" for _ in REMOVED_CITIES)
    params: list[Any] = [*sorted(REMOVED_CITIES), MAX_DECISION_HOURS_TO_SETTLE]
    sql = f"""
        SELECT
          ft.strategy_id,
          COALESCE(ft.strategy_id, ft.strategy_name, ft.execution_policy, 'unknown') AS strategy_instance,
          ft.city,
          ft.target_date,
          ft.side,
          ft.cost_usd,
          ft.pnl_usd_at_fill,
          ft.win_by_count,
          fsc.decision_hours_to_settle
        FROM fact_trades ft
        JOIN fact_signal_candidates fsc
          ON ft.condition_id = fsc.condition_id
         AND ft.side = fsc.side
         AND ft.target_date = fsc.event_date
        WHERE ft.trade_class = 'live_real'
          AND ft.settlement_status = 'settled'
          AND ft.city NOT IN ({city_placeholders})
          AND fsc.decision_hours_to_settle <= ?
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    def summarize(sub_rows: list[dict[str, Any]]) -> dict[str, Any]:
        cost = sum(float(r.get("cost_usd") or 0.0) for r in sub_rows)
        pnl = sum(float(r.get("pnl_usd_at_fill") or 0.0) for r in sub_rows)
        wins = sum(1 for r in sub_rows if int(r.get("win_by_count") or 0) == 1)
        return {
            "fills": len(sub_rows),
            "cost_usd": cost,
            "pnl_usd": pnl,
            "roi": pnl / cost if cost > 0 else 0.0,
            "win_rate": wins / len(sub_rows) if sub_rows else 0.0,
            "roi_excl_top5": _top_removed_roi_from_rows(sub_rows, 5),
            "date_range": [
                min((str(r["target_date"]) for r in sub_rows), default=None),
                max((str(r["target_date"]) for r in sub_rows), default=None),
            ],
        }

    by_instance = []
    for instance in sorted({str(r["strategy_instance"]) for r in rows}):
        sub = [r for r in rows if str(r["strategy_instance"]) == instance]
        by_instance.append({"strategy_instance": instance, **summarize(sub)})

    recent_rows = [r for r in rows if str(r["target_date"]) >= "2026-06-01"]
    return {
        "definition": (
            "fact_trades live_real settled joined to fact_signal_candidates by "
            "(condition_id, side, target_date/event_date), filtered by operational base"
        ),
        "overall": summarize(rows),
        "recent_from_2026_06_01": summarize(recent_rows),
        "by_instance": by_instance,
    }


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df.reset_index(drop=True)
    if name == "pre_2026_06_01":
        return df[df["event_date"] < "2026-06-01"].reset_index(drop=True)
    if name in ("post_2026_06_01", "recent_from_2026_06_01"):
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _distribution_input(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["city", "event_date", "bracket", "model_p_yes", "market_yes_price", "final_yes"]
    base = df[cols + ["live_filled"]].copy()
    grouped = (
        base.groupby(["city", "event_date", "bracket"], as_index=False)
        .agg(
            model_p_yes=("model_p_yes", "first"),
            market_yes_price=("market_yes_price", "first"),
            final_yes=("final_yes", "first"),
            live_filled=("live_filled", "max"),
        )
    )
    return grouped.reset_index(drop=True)


def _combo_score_for_variant(
    combo: tuple[ComboLeg, ...],
    probs_market: dict[str, float],
    variant: str,
) -> float | None:
    payoff = _payoff_by_temp(combo, list(probs_market))
    if not payoff:
        return None
    cost = sum(leg.notional for leg in combo)
    ev = _expected_value(payoff, probs_market)
    cvar = _cvar20(payoff, probs_market)
    leave_best = _leave_best_out_ev(payoff, probs_market)
    worst = min(payoff.values())
    sides = [leg.side for leg in combo]
    top_market_bracket = max(probs_market, key=probs_market.get)

    if ev <= 0.0:
        return None

    if variant == "robust_single_best":
        if len(combo) != 1:
            return None
        if worst < -0.70 * cost:
            return None
        return ev + 0.25 * cvar

    if variant == "tail_balanced_combo":
        if leave_best <= 0.0:
            return None
        if cvar < -0.65 * cost:
            return None
        if worst < -1.00 * cost:
            return None
        return leave_best + 0.35 * cvar + 0.15 * ev

    if variant == "no_hit_guard_combo":
        if sides.count("BUY_NO") > 2:
            return None
        if any(leg.side == "BUY_NO" and leg.bracket == top_market_bracket for leg in combo):
            return None
        if leave_best <= -0.10 * cost:
            return None
        if cvar < -0.70 * cost:
            return None
        return ev + 0.50 * leave_best + 0.25 * cvar

    if variant == "compact_diversified_combo":
        if len(combo) > 2:
            return None
        if sides.count("BUY_YES") > 1:
            return None
        if sides.count("BUY_NO") > 2:
            return None
        if worst < -0.90 * cost:
            return None
        if leave_best <= -0.05 * cost:
            return None
        return 0.60 * ev + 0.40 * leave_best + 0.20 * cvar

    raise ValueError(f"unknown variant: {variant}")


def _decide_custom_basket_variant(df: pd.DataFrame, variant: str) -> list[Leg]:
    out: list[Leg] = []
    max_legs = 1 if variant == "robust_single_best" else 4
    if variant == "compact_diversified_combo":
        max_legs = 2
    for (_city, _event_date), grp in df.groupby(["city", "event_date"]):
        candidates, prob_inputs = _candidate_legs_for_group(
            grp,
            edge_threshold=0.03,
            small_notional=3.0,
            normal_notional=8.0,
            normal_threshold=0.06,
            max_win_multiple=10.0 if variant != "robust_single_best" else 8.0,
        )
        probs_market = _probability_distribution(prob_inputs["market"])
        if not candidates or not probs_market:
            continue
        pool = sorted(candidates, key=lambda leg: leg.edge, reverse=True)[:12]
        best_combo: tuple[ComboLeg, ...] | None = None
        best_score: float | None = None
        for k in range(1, min(max_legs, len(pool)) + 1):
            for combo in itertools.combinations(pool, k):
                if not _valid_combo(combo):
                    continue
                score = _combo_score_for_variant(combo, probs_market, variant)
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_combo = combo
                    best_score = score
        if best_combo:
            out.extend(_to_eval_leg(leg) for leg in best_combo)
    return out


def _side_roi(summary: dict[str, Any], side: str) -> float:
    side_block = summary.get("by_side", {}).get(side)
    if not side_block:
        return 0.0
    cost = side_block.get("total_cost_usd") or 0.0
    pnl = side_block.get("total_pnl_usd") or 0.0
    return pnl / cost if cost else 0.0


def _research_direction_gates(
    summary: dict[str, Any],
    attr: dict[str, float] | None,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "roi_beats_legacy_side_band": summary["roi"] > baseline["roi"],
        "top5_roi_not_worse_than_legacy": summary["roi_excl_top5"] >= baseline["roi_excl_top5"],
        "missed_profit_lte_avoided_loss": (
            (attr or {}).get("missed_profit_usd", 0.0)
            <= (attr or {}).get("avoided_loss_usd", 0.0)
        ),
        "buy_no_roi_nonnegative": _side_roi(summary, "BUY_NO") >= 0.0,
    }
    return {**checks, "gate_count": sum(checks.values())}


def _evaluate_research_directions(df: pd.DataFrame) -> dict[str, Any]:
    side_profile = next(p for p in PROFILES if p.profile_id == "legacy_side_band")
    legacy_side = _decide_legacy_profile(df, side_profile, "raw")
    entry_universe = _entry_band_universe(df, side_profile)
    raw_trigger_universe = _legacy_trigger_universe(df, legacy_side)
    rule_legs: dict[str, list[Leg]] = {
        "legacy_side_band_raw": legacy_side,
        "combo_market_risk_entry": _decide_combo_optimizer(entry_universe, mode="market_risk"),
        "combo_market_tail_entry": _decide_combo_optimizer(entry_universe, mode="market_tail"),
        "robust_single_best_entry": _decide_custom_basket_variant(
            entry_universe, "robust_single_best"
        ),
        "tail_balanced_combo_entry": _decide_custom_basket_variant(
            entry_universe, "tail_balanced_combo"
        ),
        "no_hit_guard_combo_entry": _decide_custom_basket_variant(
            entry_universe, "no_hit_guard_combo"
        ),
        "compact_diversified_combo_entry": _decide_custom_basket_variant(
            entry_universe, "compact_diversified_combo"
        ),
        "live_trigger_tail_combo": _decide_custom_basket_variant(
            raw_trigger_universe, "tail_balanced_combo"
        ),
        "live_trigger_no_hit_guard_combo": _decide_custom_basket_variant(
            raw_trigger_universe, "no_hit_guard_combo"
        ),
    }
    summaries = {
        name: asdict(_summarize(name, legs))
        for name, legs in rule_legs.items()
    }
    baseline = summaries["legacy_side_band_raw"]
    attrs = {
        name: None if name == "legacy_side_band_raw" else _attribution(legs, legacy_side)
        for name, legs in rule_legs.items()
    }
    gates = {
        name: _research_direction_gates(summaries[name], attrs[name], baseline)
        for name in rule_legs
        if name != "legacy_side_band_raw"
    }
    return {
        "n_rows": int(len(df)),
        "entry_band_rows": int(len(entry_universe)),
        "legacy_raw_trigger_rows": int(len(raw_trigger_universe)),
        "rules": summaries,
        "attr_vs_legacy_side_band": attrs,
        "gates": gates,
    }


def _compact_rule(block: dict[str, Any]) -> dict[str, Any]:
    return block["summary"] if "summary" in block else block


def _fmt_money(v: float | int | None) -> str:
    return f"${float(v or 0.0):+.2f}"


def _fmt_roi(v: float | int | None) -> str:
    return f"{float(v or 0.0) * 100:+.2f}%"


def _write_markdown(report: dict[str, Any], out_path: Path) -> None:
    gate = report["clob_gate"]
    checks = report["self_checks"]
    base = report["operational_base"]
    fact_rows = checks["fact_rows"][0]
    total_fact_rows = fact_rows["fact_rows"] or 0
    settled_rows = fact_rows["settled_rows"] or 0
    unsettled_or_null_rows = total_fact_rows - settled_rows
    unsettled_ratio = unsettled_or_null_rows / total_fact_rows if total_fact_rows else 0.0
    lines = [
        "# Weather Edge V2 Filtered Operational-Base Research - 2026-06-08",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db` (`fact_trades`, `fact_signal_candidates`)",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- DB mtime BJ: `{checks['db_mtime_bj']}`",
        f"- MAX fact_built_at_utc: `{checks['fact_built_at'][0]['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; "
        f"db/cache diff `{gate.get('db_vs_primary_cache', {}).get('db_not_in_cache')}/"
        f"{gate.get('db_vs_primary_cache', {}).get('cache_not_in_db')}`; "
        f"missing_order_rows `{gate.get('db_fills', {}).get('missing_order_rows')}`; "
        f"over_order_keys `{gate.get('db_fills', {}).get('over_order_keys')}`; "
        f"db_fill_cost_minus_fact_cost `{gate.get('db_fill_cost_minus_fact_cost')}`",
        f"- live_real fill_id reconciliation: DB `{gate.get('fact_trades_live_real', {}).get('fill_ids')}`, "
        f"raw CLOB `{gate.get('db_fills', {}).get('distinct_fill_ids')}`",
        f"- fact_trades rows: `{total_fact_rows}`; settled `{settled_rows}`; "
        f"unsettled/null `{unsettled_or_null_rows}` (`{unsettled_ratio*100:.1f}%`); "
        f"missing_bracket `{fact_rows['missing_bracket_rows'] or 0}`",
        "",
        "## 目标指标与分母",
        "",
        "- target metric: `weather_edge_v2_filtered_operational_base` = 在新 operational base 上比较 raw/blend/side-band/basket 规则的 settled opportunity PnL、ROI、top5-removed ROI、missed/avoided attribution、walk-forward 稳定性。",
        "- 分母: `fact_signal_candidates` 中 `settlement_status='settled'`、`decision_window_missing=0`、模型/市场/entry/final 字段齐全的机会行。",
        "- 本报告中的策略 PnL 是 offline opportunity / shadow-notional PnL；不是钱包 cashflow，也不是直接用来解释账户余额的 live_real realized PnL。",
        f"- operational base: exclude `{', '.join(sorted(REMOVED_CITIES))}`; `decision_hours_to_settle <= {MAX_DECISION_HOURS_TO_SETTLE:.0f}`。",
        f"- filtered rows: `{base['filtered_rows']}` / `{base['source_rows']}`; date range `{base['date_range'][0]} -> {base['date_range'][1]}`。",
        "",
        "## 数据完整性自检",
        "",
        "```json",
        json.dumps(checks, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Current Live Actual Reference",
        "",
        "这段只看 `fact_trades.trade_class='live_real'` 的已结算真实成交，并 join `fact_signal_candidates` 做 operational-base 过滤。它回答“现在符合 live 条件的真实成交实际表现”，不等同于 basket 反事实。",
        "",
        "| scope | fills | date range | cost | pnl | ROI | top5 ROI | win_rate |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    live_actual = report["actual_live_operational_base"]
    for scope, row in (
        ("overall", live_actual["overall"]),
        ("recent_from_2026_06_01", live_actual["recent_from_2026_06_01"]),
    ):
        lines.append(
            f"| {scope} | {row['fills']} | {row['date_range'][0]} -> {row['date_range'][1]} | "
            f"{_fmt_money(row['cost_usd'])} | {_fmt_money(row['pnl_usd'])} | "
            f"{_fmt_roi(row['roi'])} | {_fmt_roi(row['roi_excl_top5'])} | "
            f"{row['win_rate']*100:.1f}% |"
        )
    lines.extend([
        "",
        "| instance | fills | date range | cost | pnl | ROI | top5 ROI | win_rate |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ])
    for row in live_actual["by_instance"]:
        lines.append(
            f"| {row['strategy_instance']} | {row['fills']} | "
            f"{row['date_range'][0]} -> {row['date_range'][1]} | "
            f"{_fmt_money(row['cost_usd'])} | {_fmt_money(row['pnl_usd'])} | "
            f"{_fmt_roi(row['roi'])} | {_fmt_roi(row['roi_excl_top5'])} | "
            f"{row['win_rate']*100:.1f}% |"
        )
    lines.extend([
        "",
        "## Basket Research Directions",
        "",
        "新增方向都只做 shadow/offline 比较，baseline 固定为 current-like `legacy_side_band_raw`。`entry` 表示同 side-band 入场价 universe；`live_trigger` 表示只在 legacy raw 已触发 rows 上重组 basket。",
        "",
        "| slice | rule | n | pnl | ROI | top5 ROI | missed | avoided | gates |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    research_rules = (
        "legacy_side_band_raw",
        "combo_market_risk_entry",
        "combo_market_tail_entry",
        "robust_single_best_entry",
        "tail_balanced_combo_entry",
        "no_hit_guard_combo_entry",
        "compact_diversified_combo_entry",
        "live_trigger_tail_combo",
        "live_trigger_no_hit_guard_combo",
    )
    for slice_name, data in report["research_directions"].items():
        for rule in research_rules:
            s = data["rules"][rule]
            attr = data["attr_vs_legacy_side_band"].get(rule) or {}
            gates = data["gates"].get(rule, {})
            lines.append(
                f"| {slice_name} | {rule} | {s['n_legs']} | {_fmt_money(s['total_pnl_usd'])} | "
                f"{_fmt_roi(s['roi'])} | {_fmt_roi(s['roi_excl_top5'])} | "
                f"{_fmt_money(attr.get('missed_profit_usd'))} | {_fmt_money(attr.get('avoided_loss_usd'))} | "
                f"{gates.get('gate_count', 0)}/4 |"
            )
    lines.extend([
        "",
        "### 方向定义",
        "",
        "- `combo_market_risk_entry`: 既有 market-normalized risk objective，作为 combo 旧方向对照。",
        "- `combo_market_tail_entry`: 要求移除单个最佳温度结果后仍有正 EV，直接压制 top outcome 依赖。",
        "- `robust_single_best_entry`: 每 city-day 最多一腿，用 market EV + tail floor 做保守单腿化。",
        "- `tail_balanced_combo_entry`: 组合枚举，但要求 leave-best-out EV > 0、CVaR20 不吞掉过多成本。",
        "- `no_hit_guard_combo_entry`: 限制 BUY_NO 数量，并禁止对 market top bracket 买 NO，针对 “买 NO 命中被买 bracket” 的坏场景。",
        "- `compact_diversified_combo_entry`: 每 city-day 最多两腿，限制 YES/NO 结构，减少多腿尾部集中。",
        "- `live_trigger_tail_combo` / `live_trigger_no_hit_guard_combo`: 不扩大触发机会，只重组当前 side-band raw 已会碰到的 rows。",
        "",
        "## Baseline / Basket 对比",
        "",
        "| slice | profile | rule | n | pnl | ROI | top5 ROI | missed | avoided |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for slice_name, slice_data in report["baseline_vs_basket"].items():
        for profile_id, profile_data in slice_data.items():
            for rule, block in profile_data["rules"].items():
                s = block["summary"]
                attr = block.get("vs_legacy_raw") or {}
                if rule not in (
                    "legacy_raw_independent",
                    "legacy_blended_independent",
                    "basket_pr2b_same_entry_band",
                    "combo_market_risk_same_entry_band",
                    "combo_market_risk_legacy_raw_triggers",
                ):
                    continue
                lines.append(
                    f"| {slice_name} | {profile_id} | {rule} | {s['n_legs']} | "
                    f"{_fmt_money(s['total_pnl_usd'])} | {_fmt_roi(s['roi'])} | "
                    f"{_fmt_roi(s['roi_excl_top5'])} | {_fmt_money(attr.get('missed_profit_usd'))} | "
                    f"{_fmt_money(attr.get('avoided_loss_usd'))} |"
                )
    lines.extend([
        "",
        "## Optimizer Slice",
        "",
        "| slice | rule | n | pnl | ROI | top5 ROI | gates |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for slice_name, data in report["optimizer"].items():
        for rule in ("raw_single", "blended_single", "heuristic_pr2b", "combo_market_risk", "combo_market_tail"):
            s = data["rules"][rule]
            gates = data["gates"].get(rule, {})
            lines.append(
                f"| {slice_name} | {rule} | {s['n_legs']} | {_fmt_money(s['total_pnl_usd'])} | "
                f"{_fmt_roi(s['roi'])} | {_fmt_roi(s['roi_excl_top5'])} | {gates.get('gate_count', 0)}/4 |"
            )
    lines.extend([
        "",
        "## Distribution Sanity",
        "",
        "| slice | best logloss | market_norm logloss | blend_norm logloss | raw_norm logloss |",
        "|---|---|---:|---:|---:|",
    ])
    for slice_name, dists in report["distribution"].items():
        best = min(dists, key=lambda k: dists[k]["log_loss"])
        lines.append(
            f"| {slice_name} | {best} | {dists['market_norm']['log_loss']:.4f} | "
            f"{dists['blend_norm']['log_loss']:.4f} | {dists['raw_norm']['log_loss']:.4f} |"
        )
    lines.extend([
        "",
        "## Walk-Forward",
        "",
        "| policy | folds | n | pnl | ROI | weighted top5 ROI | positive folds |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    agg = report["walkforward"]["aggregate"]
    selected = agg["selected_by_train_score"]
    lines.append(
        f"| selected_by_train_score | {selected['folds']} | {selected['n_legs']} | "
        f"{_fmt_money(selected['total_pnl_usd'])} | {_fmt_roi(selected['roi'])} | "
        f"{_fmt_roi(selected['weighted_top5_removed_roi'])} | {selected['positive_fold_rate']*100:.1f}% |"
    )
    for rule, vals in agg["always_rules"].items():
        lines.append(
            f"| always_{rule} | {vals['folds']} | {vals['n_legs']} | "
            f"{_fmt_money(vals['total_pnl_usd'])} | {_fmt_roi(vals['roi'])} | "
            f"{_fmt_roi(vals['weighted_top5_removed_roi'])} | {vals['positive_fold_rate']*100:.1f}% |"
        )
    lines.extend([
        "",
        "## 结论",
        "",
    ])
    lines.extend(report["conclusion"])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    db_path = DB_DEFAULT
    self_checks = _self_checks(db_path)
    clob_gate = _run_clob_gate(db_path)
    if not clob_gate.get("gate_pass"):
        raise SystemExit(f"CLOB gate failed; refusing to publish live-related research: {clob_gate}")

    source_df = _load_compare_rows(db_path)
    filtered_df = _apply_operational_base(source_df)
    slice_names = [
        "full",
        "pre_2026_06_01",
        "post_2026_06_01",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]

    baseline_vs_basket = {
        name: {
            profile.profile_id: _evaluate_profile(_slice(filtered_df, name), profile)
            for profile in PROFILES
        }
        for name in slice_names
    }
    optimizer = {
        name: _evaluate_optimizer_slice(_slice(filtered_df, name))
        for name in slice_names
    }
    research_direction_slices = [
        "full",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]
    research_directions = {
        name: _evaluate_research_directions(_slice(filtered_df, name))
        for name in research_direction_slices
    }
    dist_df = _distribution_input(filtered_df)
    distribution = {
        name: _evaluate_distribution(_slice(dist_df, name))
        for name in slice_names
    }
    folds = _walkforward(filtered_df)
    walkforward = {
        "folds": folds,
        "aggregate": _aggregate_folds(folds),
    }

    side_recent = baseline_vs_basket["post_2026_06_01"]["legacy_side_band"]["rules"]
    side_legacy = side_recent["legacy_raw_independent"]["summary"]
    side_combo = side_recent["combo_market_risk_same_entry_band"]["summary"]
    live_actual = _actual_live_operational_base(db_path)
    live_actual_recent = live_actual["recent_from_2026_06_01"]
    live_filled_research = research_directions["live_filled_only"]["rules"]
    live_filled_best_rule = max(
        (name for name in live_filled_research if name != "legacy_side_band_raw"),
        key=lambda name: live_filled_research[name]["roi"],
    )
    wf_selected = walkforward["aggregate"]["selected_by_train_score"]
    conclusion = [
        f"- 交易动作: `shadow`, 不 canary。当前 operational-base live_real settled recent ROI `{_fmt_roi(live_actual_recent['roi'])}`、top5 ROI `{_fmt_roi(live_actual_recent['roi_excl_top5'])}`；这说明要看 current-live 分母，但也不能把它当作 basket 已验证。",
        f"- filtered base 的 offline opportunity 结果里，`legacy_side_band` post-2026-06-01 ROI `{_fmt_roi(side_legacy['roi'])}`，`combo_market_risk_same_entry_band` post ROI `{_fmt_roi(side_combo['roi'])}`，walk-forward selected weighted top5 ROI `{_fmt_roi(wf_selected['weighted_top5_removed_roi'])}`，旧 combo 仍不能直接替代。",
        f"- 新增 basket 方向里，`live_filled_only` 上 headline ROI 最高的是 `{live_filled_best_rule}` = `{_fmt_roi(live_filled_research[live_filled_best_rule]['roi'])}`，但仍要看 holdout/recent top5 ROI、missed/avoided 和 forward settled，不允许只按 live_filled headline 选 canary。",
        "- v2 方向: raw/blend 只能先作为候选过滤或确认信号；city-day objective 继续 market-normalized，并且必须带 live-trigger constrained、leave-best-out、CVaR20、NO-hit guard、missed-vs-avoided 约束。",
        "- shadow lineage 下一步: 对每个 city-day 输出 rule_id、operational-base pass/fail、distribution source、selected/rejected legs、EV、CVaR20、leave-best-out EV、worst-case payoff、missed/avoided attribution。",
        "- canary gate 维持原条件: CLOB gate 和 fill_id reconciliation 通过、filtered basket 在 holdout/recent  beats side_band、top5-removed ROI 不劣化、walk-forward positive fold >=60%、再加一周 forward settled shadow 证据。",
    ]

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "self_checks": self_checks,
        "clob_gate": clob_gate,
        "operational_base": {
            "removed_cities": sorted(REMOVED_CITIES),
            "max_decision_hours_to_settle": MAX_DECISION_HOURS_TO_SETTLE,
            "source_rows": int(len(source_df)),
            "filtered_rows": int(len(filtered_df)),
            "date_range": [
                str(filtered_df["event_date"].min()) if len(filtered_df) else None,
                str(filtered_df["event_date"].max()) if len(filtered_df) else None,
            ],
        },
        "actual_live_operational_base": live_actual,
        "baseline_vs_basket": baseline_vs_basket,
        "optimizer": optimizer,
        "research_directions": research_directions,
        "distribution": distribution,
        "walkforward": walkforward,
        "conclusion": conclusion,
    }

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-{REPORT_STEM}.json"
    md_path = out_dir / f"{today.isoformat()}-{REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)

    print(f"filtered rows: {len(filtered_df)} / {len(source_df)}")
    print("CLOB gate:", clob_gate.get("gate_pass"))
    print("post side_band legacy_raw:", side_legacy)
    print("post combo_market_risk_same_entry_band:", side_combo)
    print("actual live operational-base recent:", live_actual_recent)
    print(
        "live_filled best new direction:",
        live_filled_best_rule,
        live_filled_research[live_filled_best_rule],
    )
    print("walkforward selected:", wf_selected)
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
