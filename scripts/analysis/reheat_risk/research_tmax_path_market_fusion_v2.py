#!/usr/bin/env python3
"""Absolute-ladder answerability audit for Tmax Path-Market Fusion V2.

Research only. This script intentionally does not touch any runner, config,
database, order, or live path. It refuses to relabel the existing relative
``current/d1/d2/tail`` universe as an absolute settlement ladder.

The first V2 question is data answerability: can a historical city-day state
be joined to the complete, contemporaneous settlement ladder and can that
ladder be trained with a pre-cutoff inner walk-forward?  Only after that gate
passes may the physical hazard and market-residual models be fit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from weather_data_feed.market_brackets import MarketBracket, parse_market_bracket  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402
import research_tmax_target_book_v2 as target_book  # noqa: E402


P0_SCORES = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv"
COHERENT_PREDICTIONS = ROOT / "docs/analysis/2026-07/generated/tmax_coherent_expression_calibrator_v1/coherent_predictions.csv"
DB_PATH = ROOT / "runtime/weather.db"
SNAPSHOT_ROOTS = [historical_strategy_snapshots()]
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-path-market-fusion-v2-baseline.md"
JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-path-market-fusion-v2-baseline.json"

TRAIN_CUTOFF = "2026-06-21"
MIN_INNER_TRAIN_DATES = 5
EPS = 1e-9


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return str(value)
    return value


def _snapshot_key(timestamp: str) -> str | None:
    parsed = pd.to_datetime(timestamp, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return (parsed + pd.Timedelta(hours=8)).strftime("%Y%m%d_%H%M")


def _bracket_key(label: object, question: object = "") -> tuple[float | None, float | None, bool, bool, str] | None:
    parsed = parse_market_bracket(str(label or ""), str(question or ""))
    if parsed is None:
        return None
    return (parsed.low, parsed.high, parsed.bottom, parsed.top, parsed.label)


def _bracket_sort_key(parsed: MarketBracket | None) -> tuple[float, float]:
    if parsed is None:
        return (math.inf, math.inf)
    low = -math.inf if parsed.bottom else float(parsed.low) if parsed.low is not None else math.inf
    high = math.inf if parsed.top else float(parsed.high) if parsed.high is not None else math.inf
    return (low, high)


def _quote_mid(record: dict[str, Any]) -> float | None:
    """Derive a YES midpoint from captured YES/NO top-of-book fields."""
    bids: list[float] = []
    asks: list[float] = []
    for field in ("yes_best_bid",):
        try:
            value = float(record.get(field))
            if math.isfinite(value):
                bids.append(value)
        except (TypeError, ValueError):
            pass
    for field in ("no_best_ask",):
        try:
            value = 1.0 - float(record.get(field))
            if math.isfinite(value):
                bids.append(value)
        except (TypeError, ValueError):
            pass
    for field in ("yes_best_ask",):
        try:
            value = float(record.get(field))
            if math.isfinite(value):
                asks.append(value)
        except (TypeError, ValueError):
            pass
    for field in ("no_best_bid",):
        try:
            value = 1.0 - float(record.get(field))
            if math.isfinite(value):
                asks.append(value)
        except (TypeError, ValueError):
            pass
    if bids and asks:
        return (max(bids) + min(asks)) / 2.0
    if bids:
        return max(bids)
    if asks:
        return min(asks)
    return None


def _snapshot_paths() -> tuple[dict[str, Path], dict[str, int]]:
    paths: dict[str, Path] = {}
    inventory: dict[str, int] = {}
    # Targeted Mac snapshots win when both roots have the same wall-clock key.
    for root in SNAPSHOT_ROOTS:
        files = sorted(root.glob("snapshot_*.json")) if root.exists() else []
        inventory[str(root)] = len(files)
        for path in files:
            paths[path.stem.removeprefix("snapshot_")] = path
    return paths, inventory


def _load_p0_states(snapshot_paths: dict[str, Path]) -> pd.DataFrame:
    if not P0_SCORES.exists():
        raise FileNotFoundError(P0_SCORES)
    required = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "final_winning_bracket",
        "actual_bucket",
    ]
    states = pd.read_csv(P0_SCORES, usecols=required, low_memory=False)
    states["target_date"] = states["target_date"].astype(str)
    states["snapshot_key"] = states["decision_snapshot_ts_utc"].map(_snapshot_key)
    states["snapshot_path"] = states["snapshot_key"].map(snapshot_paths)
    states["has_snapshot_path"] = states["snapshot_path"].notna()
    return states


def _settlement_inventory() -> dict[tuple[str, str], set[tuple[float | None, float | None, bool, bool, str]]]:
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = pd.read_sql_query(
            """
            SELECT city, target_date, bracket, question
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
            """,
            conn,
        )
    finally:
        conn.close()
    inventory: dict[tuple[str, str], set[tuple[float | None, float | None, bool, bool, str]]] = {}
    for row in rows.itertuples(index=False):
        parsed = _bracket_key(row.bracket, row.question)
        if parsed is not None:
            inventory.setdefault((str(row.city), str(row.target_date)), set()).add(parsed)
    return inventory


def _audit_state(
    state: pd.Series,
    snapshot: dict[str, Any],
    settlement_inventory: dict[tuple[str, str], set[tuple[float | None, float | None, bool, bool, str]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    city = str(state["city"])
    target_date = str(state["target_date"])
    records = [
        record
        for record in snapshot.get("records", [])
        if str(record.get("city") or "") == city
        and str(record.get("target_date") or record.get("event_date") or "") == target_date
    ]
    ladder: list[tuple[tuple[float | None, float | None, bool, bool, str], MarketBracket, dict[str, Any], float | None]] = []
    for record in records:
        parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
        key = _bracket_key(record.get("bracket"), record.get("question"))
        if parsed is not None and key is not None:
            ladder.append((key, parsed, record, _quote_mid(record)))
    ladder.sort(key=lambda item: _bracket_sort_key(item[1]))

    snapshot_keys = {item[0] for item in ladder}
    quoted_keys = {item[0] for item in ladder if item[3] is not None}
    expected_keys = settlement_inventory.get((city, target_date), set())
    anchors = [_bracket_key(state[column]) for column in ("current_bracket", "d1_no_bracket", "d2_no_bracket")]
    anchor_positions = [next((i for i, item in enumerate(ladder) if item[0] == key), None) for key in anchors]
    anchor_ordered = all(position is not None for position in anchor_positions) and anchor_positions == sorted(anchor_positions)
    full_identity = bool(expected_keys) and expected_keys.issubset(snapshot_keys)
    full_quote = full_identity and expected_keys.issubset(quoted_keys)
    final_key = _bracket_key(state.get("final_winning_bracket"))
    final_in_ladder = final_key in snapshot_keys if final_key is not None else False

    state_row = {
        "city": city,
        "target_date": target_date,
        "decision_hour_local": state["decision_hour_local"],
        "decision_snapshot_ts_utc": state["decision_snapshot_ts_utc"],
        "snapshot_key": state["snapshot_key"],
        "snapshot_path": str(state["snapshot_path"]),
        "actual_bucket_relative_only": state.get("actual_bucket"),
        "final_winning_bracket_outcome_only": state.get("final_winning_bracket"),
        "snapshot_record_count": len(records),
        "settlement_ladder_count_audit_only": len(expected_keys),
        "snapshot_ladder_count": len(snapshot_keys),
        "quoted_ladder_count": len(quoted_keys),
        "anchor_current_present": anchor_positions[0] is not None,
        "anchor_d1_present": anchor_positions[1] is not None,
        "anchor_d2_present": anchor_positions[2] is not None,
        "anchors_ordered": anchor_ordered,
        "complete_identity_vs_settlement_audit": full_identity,
        "complete_quoted_ladder": full_quote,
        "final_winner_in_snapshot_ladder": final_in_ladder,
        "ladder_signature": "|".join(item[0][4] for item in ladder),
    }
    expression_rows = [
        {
            "city": city,
            "target_date": target_date,
            "decision_hour_local": state["decision_hour_local"],
            "decision_snapshot_ts_utc": state["decision_snapshot_ts_utc"],
            "expression": f"YES:{key[4]}",
            "bracket": key[4],
            "market_mid": mid,
            "pit_quote_available": mid is not None,
            "complete_quoted_ladder": full_quote,
            "outcome_used_for_model": False,
        }
        for key, _parsed, _record, mid in ladder
    ]
    return state_row, expression_rows


def _pit_feature_audit() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "field_family": "captured_market_ladder",
                "source": "paper_snapshots records at decision_snapshot_ts_utc",
                "status": "allowed_if_complete_quote_ladder",
                "reason": "captured contemporaneously; the audit verifies the entire sibling set and direct quote availability",
            },
            {
                "field_family": "captured_path_observation",
                "source": "paper_snapshots metar_current_max_f/metar_latest_ts_utc",
                "status": "allowed_when_present",
                "reason": "the saved state has a decision timestamp and source observation timestamp; missing values remain missing",
            },
            {
                "field_family": "captured_forecast",
                "source": "paper_snapshots forecast_* fields",
                "status": "allowed_with_lineage_flag",
                "reason": "the value was saved at the decision snapshot, but model_init_utc_estimated is not an authentic run timestamp",
            },
            {
                "field_family": "atlas_or_p3_historical_forecast_backfill",
                "source": "intraday atlas/P3 materializations",
                "status": "excluded",
                "reason": "the V2 plan explicitly disallows historical fields reconstructed without an authentic as-of lineage",
            },
            {
                "field_family": "full_window_city_bias",
                "source": "all-date city outcomes",
                "status": "excluded",
                "reason": "out-of-fold shrinkage may be tested only after the absolute PIT denominator exists; no full-window city memory",
            },
            {
                "field_family": "final_winning_bracket_and_settlement_outcomes",
                "source": "P0 labels / settlement_outcomes",
                "status": "label_or_audit_only",
                "reason": "used only as the target or to audit collector completeness, never as a model feature",
            },
            {
                "field_family": "relative_current_d1_d2_tail",
                "source": "P0/exact-book bridge/coherent calibrator",
                "status": "excluded_as_absolute_coordinate",
                "reason": "relative buckets re-anchor during a city-day and cannot be renamed into a fixed settlement ladder",
            },
        ]
    )


def _write_report(payload: dict[str, Any]) -> None:
    funnel = pd.DataFrame(payload["funnel"])
    daily = pd.DataFrame(payload["daily"])
    comparisons = pd.DataFrame(payload["comparisons"])
    minimally_answerable = payload["sample_status"] == "minimally_answerable_but_thin"
    headline = (
        "- **当前历史数据可以形成最小 absolute-ladder PIT 分母，但样本极薄；本轮只完成可答性审计，不拟合 hazard，也不输出无统计意义的 ROI。**"
        if minimally_answerable
        else "- **当前历史数据不能形成最小 absolute-ladder PIT 分母；本轮不拟合 hazard、不输出伪 logloss/Brier/ROI，也不把 `current/d1/d2/tail` 重命名为 absolute bracket。**"
    )
    count_summary = (
        f"- 可回指 paper snapshot 的 P0 state 是 `{payload['counts']['snapshot_matched_states']}` / `{payload['counts']['p0_states']}`；完整逐档 quoted state `{payload['counts']['absolute_complete_quoted_states']}`，带标签可评估 `{payload['counts']['absolute_eligible_states']}`。pre-cutoff `{payload['counts']['pre_cutoff_absolute_rows']}` 行 / `{payload['counts']['pre_cutoff_absolute_dates']}` 天，post-cutoff `{payload['counts']['post_cutoff_absolute_rows']}` 行 / `{payload['counts']['post_cutoff_absolute_dates']}` 天；仅 `{payload['counts']['fixed_complete_ladder_city_days']}` 个 city-day 在多个 state 上持续保存完整固定梯子。"
    )
    comparison_note = (
        "四个预注册模型已有同一 absolute PIT 分母，但 47 个可评估 state 只够 smoke test，不够在 inner selection 后再给独立 forward 结论。本审计因此保留 metrics/CI/ROI 为 `NA`，后续模型实验必须继续积累 fresh complete-ladder states。"
        if minimally_answerable
        else "所有四个预注册模型都要求同一 frozen absolute PIT denominator。当前分母无法形成，因此 metrics、date-block CI 和 fee-adjusted ROI 均为 `NA`，而不是零或负数。"
    )
    next_step = (
        "现有 47 行只能验证 materializer/model plumbing。策略结论必须等待更多完整逐档 fresh states，并按 target_date 做 outer walk-forward；proper score 先选模型，fee-adjusted ROI 只能在模型冻结后作为 secondary。"
        if minimally_answerable
        else "继续前必须积累固定 sibling ladder、每档 direct quote 和 state-to-snapshot key；达到预注册日期支持后再做 target_date 外层 walk-forward。"
    )
    lines = [
        "# Tmax Path-Market Fusion V2 Baseline",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> Scope: offline data-answerability audit only. No live runner, configuration, order behavior, or database was changed.",
        "",
        "## 结论",
        "",
        headline,
        count_summary,
        "- `settlement_outcomes` 只用于事后 collector-completeness 审计；final bracket、actual bucket、outcome-derived slice 都没有进入特征。P3/atlas 的历史 forecast backfill 与全窗口 city bias 被明确排除。",
        "",
        "## Funnel",
        "",
        *_markdown_table(funnel, ["stage", "rows", "dates", "cities", "note"]),
        "",
        "## Same-Denominator Model Comparison",
        "",
        comparison_note,
        "",
        *_markdown_table(comparisons, ["model", "status", "date_equal_logloss", "date_equal_brier", "date_block_ci", "roi_fee_adjusted", "reason"]),
        "",
        "## 日期覆盖",
        "",
        *_markdown_table(daily, ["target_date", "snapshot_matched_states", "complete_identity_states", "complete_quoted_states", "cities", "pre_cutoff"]),
        "",
        "## PIT / Outcome Boundary",
        "",
        "- 可用：同一 decision snapshot 内已捕获的 full sibling quote、同一快照中的 observation/path 与 forecast 字段（forecast run timestamp 仍标 `estimated`）。",
        "- 排除：atlas/P3 historical backfill、full-window city bias、final outcome、actual overshoot/tail 等 outcome-derived slice。",
        "- Quote/settlement sibling 完整性以 canonical `settlement_outcomes` 做**事后审计**，绝不回灌到特征或训练输入。",
        "",
        "## Verdict",
        "",
        f"significance=NA; baseline=NA; forward=FAIL_THIN; conclusion=`{payload['verdict']}`。",
        "",
        next_step,
        "",
        "## Artifacts",
        "",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/funnel.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/absolute_ladder_state_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/city_day_ladder_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/expression_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/daily_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/pit_feature_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/model_comparison.csv`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _markdown_table(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    if frame.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in frame.reindex(columns=columns).to_dict("records"):
        values = []
        for value in row.values():
            if isinstance(value, float):
                values.append("NA" if not math.isfinite(value) else f"{value:.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_paths, snapshot_inventory = _snapshot_paths()
    states = _load_p0_states(snapshot_paths)
    settlement_inventory = _settlement_inventory()

    matched = states[states["has_snapshot_path"]].copy()
    state_rows: list[dict[str, Any]] = []
    expression_rows: list[dict[str, Any]] = []
    for snapshot_path, group in matched.groupby("snapshot_path", sort=False):
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        for _, state in group.iterrows():
            state_audit, expressions = _audit_state(state, snapshot, settlement_inventory)
            state_rows.append(state_audit)
            expression_rows.extend(expressions)

    state_audit = pd.DataFrame(state_rows)
    expression_audit = pd.DataFrame(expression_rows)
    if state_audit.empty:
        raise RuntimeError("No P0 state could be joined to a captured paper snapshot")
    state_audit["pre_cutoff"] = state_audit["target_date"].lt(TRAIN_CUTOFF)
    state_audit["absolute_eligible"] = (
        state_audit["complete_quoted_ladder"]
        & state_audit["anchors_ordered"]
        & state_audit["final_winner_in_snapshot_ladder"]
    )

    city_day = (
        state_audit.groupby(["city", "target_date"], as_index=False)
        .agg(
            states=("city", "size"),
            complete_identity_states=("complete_identity_vs_settlement_audit", "sum"),
            complete_quoted_states=("complete_quoted_ladder", "sum"),
            absolute_eligible_states=("absolute_eligible", "sum"),
            distinct_ladder_signatures=("ladder_signature", "nunique"),
        )
    )
    city_day["fixed_ladder_proven"] = city_day["states"].ge(2) & city_day["distinct_ladder_signatures"].eq(1)
    city_day["all_states_complete_quoted"] = city_day["complete_quoted_states"].eq(city_day["states"])
    city_day["fixed_complete_ladder_proven"] = city_day["fixed_ladder_proven"] & city_day["all_states_complete_quoted"]
    city_day["pre_cutoff"] = city_day["target_date"].lt(TRAIN_CUTOFF)

    daily = (
        state_audit.groupby("target_date", as_index=False)
        .agg(
            snapshot_matched_states=("city", "size"),
            complete_identity_states=("complete_identity_vs_settlement_audit", "sum"),
            complete_quoted_states=("complete_quoted_ladder", "sum"),
            absolute_eligible_states=("absolute_eligible", "sum"),
            cities=("city", "nunique"),
        )
    )
    daily["pre_cutoff"] = daily["target_date"].lt(TRAIN_CUTOFF)

    absolute = state_audit[state_audit["absolute_eligible"]].copy()
    pre_cutoff = absolute[absolute["target_date"].lt(TRAIN_CUTOFF)]
    post_cutoff = absolute[absolute["target_date"].ge(TRAIN_CUTOFF)]
    pre_dates = int(pre_cutoff["target_date"].nunique())
    absolute_answerable = pre_dates >= MIN_INNER_TRAIN_DATES and not post_cutoff.empty

    funnel = pd.DataFrame(
        [
            {"stage": "p0_relative_state_universe", "rows": len(states), "dates": states["target_date"].nunique(), "cities": states["city"].nunique(), "note": "relative current/d1/d2/tail; not an absolute ladder"},
            {"stage": "has_decision_snapshot_timestamp", "rows": int(states["decision_snapshot_ts_utc"].notna().sum()), "dates": states["target_date"].nunique(), "cities": states["city"].nunique(), "note": "P0 decision-state lineage"},
            {"stage": "snapshot_path_matched", "rows": len(matched), "dates": matched["target_date"].nunique(), "cities": matched["city"].nunique(), "note": "captured paper snapshot at matching Beijing wall-clock minute"},
            {"stage": "complete_identity_ladder_audit", "rows": int(state_audit["complete_identity_vs_settlement_audit"].sum()), "dates": state_audit.loc[state_audit["complete_identity_vs_settlement_audit"], "target_date"].nunique(), "cities": state_audit.loc[state_audit["complete_identity_vs_settlement_audit"], "city"].nunique(), "note": "after-the-fact settlement inventory audit only"},
            {"stage": "complete_quoted_absolute_ladder", "rows": int(state_audit["complete_quoted_ladder"].sum()), "dates": state_audit.loc[state_audit["complete_quoted_ladder"], "target_date"].nunique(), "cities": state_audit.loc[state_audit["complete_quoted_ladder"], "city"].nunique(), "note": "all audited siblings have a direct/cross-side YES quote"},
            {"stage": "absolute_eligible_with_label", "rows": len(absolute), "dates": absolute["target_date"].nunique(), "cities": absolute["city"].nunique(), "note": "complete quote + ordered anchors + final label present; label not a feature"},
            {"stage": "fixed_complete_ladder_city_days", "rows": int(city_day["fixed_complete_ladder_proven"].sum()), "dates": int(city_day.loc[city_day["fixed_complete_ladder_proven"], "target_date"].nunique()), "cities": int(city_day.loc[city_day["fixed_complete_ladder_proven"], "city"].nunique()), "note": "at least two matched states; every state complete-quoted; one identical absolute signature"},
            {"stage": "pre_cutoff_absolute_eligible", "rows": len(pre_cutoff), "dates": pre_dates, "cities": pre_cutoff["city"].nunique(), "note": f"requires at least {MIN_INNER_TRAIN_DATES} dates for inner walk-forward"},
        ]
    )

    not_run_reason = (
        f"absolute PIT denominator lacks pre-cutoff inner walk-forward support: {pre_dates} dates < {MIN_INNER_TRAIN_DATES}"
        if not absolute_answerable
        else f"minimal denominator is available but thin: pre={len(pre_cutoff)} rows/{pre_dates} dates, post={len(post_cutoff)} rows/{post_cutoff['target_date'].nunique()} dates"
    )
    comparisons = pd.DataFrame(
        [
            {
                "model": model,
                "status": "not_run_data_not_answerable" if not absolute_answerable else "not_run_audit_only_thin",
                "date_equal_logloss": math.nan,
                "date_equal_brier": math.nan,
                "date_block_ci": "NA",
                "roi_fee_adjusted": math.nan,
                "reason": not_run_reason,
            }
            for model in [
                "market_full_ladder",
                "current_coherent_base",
                "global_physical_hazard",
                "market_plus_global_hazard_residual",
            ]
        ]
    )
    feature_audit = _pit_feature_audit()

    state_audit.to_csv(output_dir / "absolute_ladder_state_audit.csv", index=False)
    city_day.to_csv(output_dir / "city_day_ladder_audit.csv", index=False)
    expression_audit.to_csv(output_dir / "expression_audit.csv", index=False)
    daily.to_csv(output_dir / "daily_audit.csv", index=False)
    funnel.to_csv(output_dir / "funnel.csv", index=False)
    comparisons.to_csv(output_dir / "model_comparison.csv", index=False)
    feature_audit.to_csv(output_dir / "pit_feature_audit.csv", index=False)

    sample_status = "minimally_answerable_but_thin" if absolute_answerable else "not_answerable"
    verdict = (
        "absolute_ladder_minimally_answerable_but_too_thin_for_strategy_claim"
        if absolute_answerable
        else "kill_current_historical_absolute_ladder_experiment_continue_forward_collection"
    )
    payload = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "scope": "offline absolute-ladder data answerability audit only",
        "sources": {
            "p0_relative_states": str(P0_SCORES.relative_to(ROOT)),
            "coherent_baseline": str(COHERENT_PREDICTIONS.relative_to(ROOT)),
            "settlement_outcomes": str(DB_PATH.relative_to(ROOT)),
            "paper_snapshot_roots": [str(path) for path in SNAPSHOT_ROOTS],
        },
        "parameters": {
            "train_cutoff": TRAIN_CUTOFF,
            "min_inner_train_dates": MIN_INNER_TRAIN_DATES,
            "fee_helper": "scripts.analysis.reheat_risk.research_tmax_target_book_v2._fee",
            "fee_formula": "0.05 * price * (1 - price)",
            "outer_fold": "target_date",
            "selection_metric": "date_equal_exact_bracket_logloss_then_brier",
        },
        "snapshot_inventory": snapshot_inventory,
        "counts": {
            "p0_states": int(len(states)),
            "snapshot_matched_states": int(len(matched)),
            "absolute_complete_quoted_states": int(state_audit["complete_quoted_ladder"].sum()),
            "absolute_eligible_states": int(len(absolute)),
            "fixed_complete_ladder_city_days": int(city_day["fixed_complete_ladder_proven"].sum()),
            "pre_cutoff_absolute_rows": int(len(pre_cutoff)),
            "pre_cutoff_absolute_dates": pre_dates,
            "post_cutoff_absolute_rows": int(len(post_cutoff)),
            "post_cutoff_absolute_dates": int(post_cutoff["target_date"].nunique()),
        },
        "funnel": funnel.to_dict("records"),
        "daily": daily.to_dict("records"),
        "comparisons": comparisons.to_dict("records"),
        "pit_feature_audit": feature_audit.to_dict("records"),
        "model_fit_performed": False,
        "roi_performed": False,
        "sample_status": sample_status,
        "verdict": verdict,
        "blocker": not_run_reason,
    }
    JSON_PATH.write_text(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_report(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    payload = run(args.out_dir)
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "counts": payload["counts"],
                "blocker": payload["blocker"],
                "report": str(REPORT_PATH.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
