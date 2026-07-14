#!/usr/bin/env python3
"""Audit the full denominator behind the source-event strategy claim.

This script separates three evidence layers that v1 blurred together:

1. all saved city/date/hour paper-snapshot candidates;
2. generic observed running-high bracket advances in the clean PIT state set;
3. true high-frequency city/source observations covered by the new profiles.

It also replays every executable generic bracket advance without a model-edge
gate, so the final edge>=2c subset is never presented as the whole dataset.
Research-only: no order placement or live-policy mutation.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import research_source_event_hazard_router_v1 as v1  # noqa: E402
from weather_data_feed.fast_event_source_policy import load_fast_event_source_profiles  # noqa: E402


DEFAULT_STATES = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_v3/state_rows_v3.csv"
DEFAULT_SNAPSHOT_CANDIDATES = (
    ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/snapshot_candidates.csv"
)
DEFAULT_STATE_STATUS = (
    ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/state_status_summary.csv"
)
DEFAULT_V1_SUMMARY = ROOT / "docs/analysis/2026-07/generated/source_event_hazard_router_v1/strategy_summary.csv"
DEFAULT_FAST_OBS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/high_frequency_observations/"
    "high_frequency_observations.jsonl"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT_DIR = ROOT / "docs/analysis/2026-07/generated/source_event_denominator_audit_v2"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-14-source-event-denominator-audit-v2.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-source-event-denominator-audit-v2.json"

CITY_ALIASES = {"Hong Kong": "HongKong", "San Francisco": "SanFrancisco", "Tel Aviv": "TelAviv"}

EXPRESSION_SPECS = (
    ("current_yes", "current_yes_ask", "current_yes_ask_size", "y_current_yes", False),
    ("current_no", "current_no_ask_book", "current_no_ask_size_book", "y_current_yes", True),
    ("d1_yes", "d1_yes_ask_book", "d1_yes_ask_size_book", "y_d1_yes", False),
    ("d1_no", "d1_no_ask_book", "d1_no_ask_size_book", "y_d1_yes", True),
    ("previous_no", "prev_no_ask", "prev_no_ask_size", "y_prev_no", False),
    ("previous_yes", "prev_yes_ask", "prev_yes_ask_size", "y_prev_yes", False),
)


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def db_snapshot(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        fact_candidates = int(conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0])
        fact_trades = int(conn.execute("SELECT COUNT(*) FROM fact_trades").fetchone()[0])
        unsettled = int(
            conn.execute("SELECT COUNT(*) FROM fact_trades WHERE settlement_status='unsettled'").fetchone()[0]
        )
        missing = int(
            conn.execute("SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'").fetchone()[0]
        )
        settled = conn.execute(
            """
            SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date), COUNT(DISTINCT city)
            FROM settlement_outcomes WHERE settlement_status='settled'
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "db_path": str(db_path),
        "db_mtime_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "fact_signal_candidates": fact_candidates,
        "fact_trades": fact_trades,
        "unsettled_trades": unsettled,
        "missing_bracket_trades": missing,
        "settlement_min_date": settled[0],
        "settlement_max_date": settled[1],
        "settlement_dates": int(settled[2] or 0),
        "settlement_cities": int(settled[3] or 0),
    }


def trade_rows(frame: pd.DataFrame, *, cross_only: bool) -> pd.DataFrame:
    base = frame[frame["cross_event"]].copy() if cross_only else frame.copy()
    parts: list[pd.DataFrame] = []
    for expression, ask_col, size_col, outcome_col, invert in EXPRESSION_SPECS:
        part = base[
            [
                "city",
                "target_date",
                "decision_ts",
                "decision_snapshot_ts_utc",
                "anchor_episode",
                "cross_event",
            ]
        ].copy()
        part["expression"] = expression
        part["ask"] = pd.to_numeric(base[ask_col], errors="coerce")
        part["ask_size"] = pd.to_numeric(base[size_col], errors="coerce")
        part["outcome"] = pd.to_numeric(base[outcome_col], errors="coerce")
        if invert:
            part["outcome"] = 1.0 - part["outcome"]
        part = part[
            part["ask"].between(0.001, 0.999, inclusive="both")
            & part["ask_size"].ge(v1.MIN_ASK_SIZE)
            & part["outcome"].notna()
        ].copy()
        part["entry_fee"] = part["ask"].map(v1.fee_per_share)
        part["entry_cost"] = part["ask"] + part["entry_fee"]
        part["settlement_pnl"] = part["outcome"] - part["entry_cost"]
        part["price_band"] = pd.cut(
            part["ask"],
            bins=[0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.99, 1.001],
            labels=["00_20", "20_40", "40_60", "60_80", "80_90", "90_95", "95_99", "99_100"],
            include_lowest=True,
        ).astype(str)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def summarize_cross(cross_rows: pd.DataFrame, all_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    daily_parts: list[pd.DataFrame] = []
    for expression in [spec[0] for spec in EXPRESSION_SPECS]:
        selected = cross_rows[cross_rows["expression"].eq(expression)].copy()
        # Baseline is the first non-cross quote in each anchor episode, limited
        # to price bands actually traded after a bracket advance.
        bands = set(selected["price_band"].dropna().astype(str))
        baseline = all_rows[
            ~all_rows["cross_event"].astype(bool)
            & all_rows["expression"].eq(expression)
            & all_rows["price_band"].astype(str).isin(bands)
        ].copy()
        baseline = baseline.sort_values("decision_ts").drop_duplicates(
            ["city", "target_date", "anchor_episode", "expression"], keep="first"
        )
        cost = float(selected["entry_cost"].sum())
        pnl = float(selected["settlement_pnl"].sum())
        lo, hi = v1._daily_bootstrap_roi(selected)
        excess, excess_lo, excess_hi = v1._date_equal_excess_ci(selected, baseline)
        recent_dates = sorted(selected["target_date"].astype(str).unique())[-4:]
        recent = selected[selected["target_date"].astype(str).isin(recent_dates)]
        recent_cost = float(recent["entry_cost"].sum())
        summaries.append(
            {
                "expression": expression,
                "rows": len(selected),
                "dates": selected["target_date"].nunique(),
                "cities": selected["city"].nunique(),
                "avg_ask": selected["ask"].mean(),
                "win_rate": selected["outcome"].mean(),
                "roi": pnl / cost if cost else math.nan,
                "roi_ci_low": lo,
                "roi_ci_high": hi,
                "same_band_initial_rows": len(baseline),
                "same_band_initial_roi": (
                    float(baseline["settlement_pnl"].sum()) / float(baseline["entry_cost"].sum())
                    if float(baseline["entry_cost"].sum()) > 0
                    else math.nan
                ),
                "date_equal_excess_roi": excess,
                "excess_ci_low": excess_lo,
                "excess_ci_high": excess_hi,
                "recent_4_dates": ",".join(recent_dates),
                "recent_roi": (
                    float(recent["settlement_pnl"].sum()) / recent_cost if recent_cost else math.nan
                ),
            }
        )
        daily_parts.append(
            selected.groupby("target_date", as_index=False)
            .agg(rows=("city", "size"), cost=("entry_cost", "sum"), pnl=("settlement_pnl", "sum"))
            .assign(expression=expression)
        )
    return pd.DataFrame(summaries), pd.concat(daily_parts, ignore_index=True)


def fast_source_history(path: Path, settlement_max_date: str | None) -> dict[str, Any]:
    profiles = load_fast_event_source_profiles()
    unique: set[tuple[str, str, str, str]] = set()
    all_dates: set[str] = set()
    all_cities: set[str] = set()
    settled_dates: set[str] = set()
    settled_city_dates: set[tuple[str, str]] = set()
    raw_profile_rows = 0
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("source_status") or "") != "ok":
                continue
            city = CITY_ALIASES.get(str(row.get("city") or ""), str(row.get("city") or ""))
            source = str(row.get("source") or "")
            if (city, source) not in profiles:
                continue
            target_date = str(row.get("target_date") or "")[:10]
            obs_ts = str(row.get("observation_time_utc") or "")
            if not target_date or not obs_ts:
                continue
            raw_profile_rows += 1
            key = (city, target_date, source, obs_ts)
            unique.add(key)
            all_dates.add(target_date)
            all_cities.add(city)
            if settlement_max_date and target_date <= settlement_max_date:
                settled_dates.add(target_date)
                settled_city_dates.add((city, target_date))
    return {
        "path": str(path),
        "raw_profile_rows_including_repolls": raw_profile_rows,
        "unique_city_date_source_observations": len(unique),
        "min_date": min(all_dates) if all_dates else None,
        "max_date": max(all_dates) if all_dates else None,
        "dates": len(all_dates),
        "cities": len(all_cities),
        "settled_overlap_dates": len(settled_dates),
        "settled_overlap_city_dates": len(settled_city_dates),
        "settled_overlap_min_date": min(settled_dates) if settled_dates else None,
        "settled_overlap_max_date": max(settled_dates) if settled_dates else None,
    }


def pct(value: Any) -> str:
    number = safe_float(value)
    return "NA" if number is None else f"{number:.1%}"


def markdown_table(frame: pd.DataFrame, percent_cols: set[str] | None = None) -> str:
    if frame.empty:
        return "_no rows_"
    percent_cols = percent_cols or set()
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for record in frame.to_dict("records"):
        cells = []
        for col in cols:
            value = record[col]
            if col in percent_cols:
                cells.append(pct(value))
            elif isinstance(value, float):
                cells.append("NA" if not math.isfinite(value) else f"{value:.4f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_report(payload: dict[str, Any], summary: pd.DataFrame, status: pd.DataFrame) -> str:
    db = payload["data_snapshot"]
    funnel = payload["funnel"]
    fast = payload["true_fast_source_history"]
    current_no = summary[summary["expression"].eq("current_no")].iloc[0]
    old = payload["old_edge_selected_current_no"]
    return "\n".join(
        [
            "# Source-Event Denominator Audit v2",
            "",
            "> 2026-07-14; research-only; zero notional; supersedes treating v1's 42 rows as the full fast-source denominator.",
            "> Superseded for current/d1 performance by `source-event-expression-denominator-v3`: v2 inherited a full-ladder current+d1+d2 state gate that is not valid for expression-specific trading.",
            "",
            "## 数据快照",
            "",
            f"- canonical DB: `{db['db_path']}`; mtime `{db['db_mtime_utc']}`.",
            f"- fact_signal_candidates `{db['fact_signal_candidates']}`; fact_trades `{db['fact_trades']}`; unsettled `{db['unsettled_trades']}`; missing_bracket `{db['missing_bracket_trades']}`.",
            f"- settlement_outcomes: `{db['settlement_min_date']}..{db['settlement_max_date']}` / `{db['settlement_dates']}` dates / `{db['settlement_cities']}` cities.",
            "- strategy grain is PIT saved ladder state / first quote after a generic running-high bracket advance; it is not fill-grain realized PnL.",
            "",
            "## 结论先行",
            "",
            "用户对分母的质疑成立。v1 的 `42 rows` 是最终模型筛选结果，不是全部日期城市。",
            f"完整 raw hourly-last inventory 有 `{funnel['raw_city_date_hour_candidates']}` city-date-hours / `{funnel['raw_dates']}` dates / `{funnel['raw_cities']}` cities。"
            f"但它不能直接除以 `{funnel['labeled_pit_states']}`：后者是另一条 v3 reconstructed-PIT lineage，"
            f"包含 `{funnel['state_dates']}` 个 settled dates 上的每个 decision snapshot，同一小时可有多行。",
            f"同窗 raw hourly-last 是 `{funnel['raw_same_window_city_date_hour_candidates']}`；v3 clean state 去重到同一 city/date/hour 后是 "
            f"`{funnel['labeled_unique_city_date_hours']}`。策略实际使用未按小时去重的 `{funnel['labeled_pit_states']}` decision snapshots，"
            f"其中 generic running-high bracket advances 为 `{funnel['generic_bracket_advances']}`。",
            "",
            "更关键的是：v1 的 `cross_event` 只由 `previous_current_key != current_key` 定义，代码没有 join JMA/AMOS/HKO/MSS/MADIS，也没有使用 city×source profile。"
            "所以 v1 实际评估的是 generic observed running-high 跨档，不是快源领先策略。",
            "",
            f"把全部 `{int(current_no['rows'])}` 个有真实 ask/depth 的 generic cross current-NO 都交易，fee-adjusted ROI `{pct(current_no['roi'])}`，"
            f"date bootstrap CI `[{pct(current_no['roi_ci_low'])},{pct(current_no['roi_ci_high'])}]`；"
            f"相对同价带 initial-anchor baseline 的 date-equal excess `{pct(current_no['date_equal_excess_roi'])}`，"
            f"CI `[{pct(current_no['excess_ci_low'])},{pct(current_no['excess_ci_high'])}]`。没有全体事件 alpha。",
            "",
            f"旧 edge>=2c current-NO 只有 `{old.get('rows')}` rows，ROI `{pct(old.get('roi'))}`、CI `[{pct(old.get('roi_ci_low'))},{pct(old.get('roi_ci_high'))}]`；"
            "这是一个事后由 expanding model 选出的稀疏子集，显著性和 baseline 均未通过，不能代表全部 source-event。",
            "",
            "## 分母漏斗",
            "",
            markdown_table(pd.DataFrame(payload["funnel_rows"])),
            "",
            "### 独立的 raw saved-field hourly-last 审计排除原因",
            "",
            markdown_table(status),
            "",
            "## 全部 generic cross 的双边交易结果",
            "",
            markdown_table(
                summary,
                {
                    "win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "same_band_initial_roi",
                    "date_equal_excess_roi",
                    "excess_ci_low",
                    "excess_ci_high",
                    "recent_roi",
                },
            ),
            "",
            "解释：current YES 和 d1 YES 在完整 cross 分母上显著为负；current NO 接近打平但 CI 跨 0，且相对同价带 baseline 没有正 excess；"
            "d1 NO 也是负收益。previous NO 约 0.1% 只是市场已把已跨过档位买到接近 1，并非可扩张 alpha。",
            "",
            "## 真正快源历史覆盖",
            "",
            f"- profile-matched fast observations: raw repolls `{fast.get('raw_profile_rows_including_repolls')}`，unique observations `{fast.get('unique_city_date_source_observations')}`.",
            f"- window `{fast.get('min_date')}..{fast.get('max_date')}` / `{fast.get('dates')}` dates / `{fast.get('cities')}` cities.",
            f"- 与当前已结算 label 重叠只有 `{fast.get('settled_overlap_dates')}` dates / `{fast.get('settled_overlap_city_dates')}` city-dates（`{fast.get('settled_overlap_min_date')}..{fast.get('settled_overlap_max_date')}`）。",
            "",
            "因此两个月盘口历史并不等于两个月 fast-source 历史。5/05 起确实有盘口，但 profile-matched 高频文件从 7/08 UTC 才开始持续保存；"
            "按城市本地 target_date 归属后最早可落到 7/07。"
            "不能用 generic METAR/WU 跨档冒充 fast-source alpha，也不能把缺失的早期快源事后补造成 PIT 数据。",
            "",
            "## Three Gates",
            "",
            "- significance=FAIL：全量 current-NO CI 跨 0；其余主要表达为负。",
            "- baseline=FAIL：current-NO date-equal excess 点估为负且 CI 跨 0。",
            "- forward=FAIL：真正 profile-matched fast-source 只有少量已结算日期，尚无独立 forward 窗。",
            "- conclusion=`inconclusive`；保留 zero-notional side-neutral collector，不批准 5-share live。",
            "",
            "## 8 环覆盖",
            "",
            "- covered: 描述性双边结果、date bootstrap、真实 ask/depth、official taker fee、同价带 baseline、日期/城市漏斗。",
            "- partial: capacity 只验证 top ask size>=5；组合相关性只按 target_date block。",
            "- missing: 两个月 profile-matched fast-source PIT 历史、独立 settled forward、真实 fill/latency/queue。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--snapshot-candidates", type=Path, default=DEFAULT_SNAPSHOT_CANDIDATES)
    parser.add_argument("--state-status", type=Path, default=DEFAULT_STATE_STATUS)
    parser.add_argument("--v1-summary", type=Path, default=DEFAULT_V1_SUMMARY)
    parser.add_argument("--fast-observations", type=Path, default=DEFAULT_FAST_OBS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    db = db_snapshot(args.db)
    snapshot_candidates = pd.read_csv(args.snapshot_candidates, low_memory=False)
    raw_states = pd.read_csv(args.states, low_memory=False)
    frame = v1.enrich_state_rows(args.states)
    status = pd.read_csv(args.state_status, low_memory=False)
    all_rows = trade_rows(frame, cross_only=False)
    cross_rows = trade_rows(frame, cross_only=True)
    summary, daily = summarize_cross(cross_rows, all_rows)

    v1_summary = pd.read_csv(args.v1_summary, low_memory=False)
    old_match = v1_summary[
        v1_summary["strategy"].eq("cross_continuation_current_no")
        & pd.to_numeric(v1_summary["edge_threshold"], errors="coerce").eq(0.02)
    ]
    old = old_match.iloc[0].to_dict() if not old_match.empty else {}

    labeled = raw_states[raw_states["labeled"].fillna(False).astype(bool)]
    generic_cross = frame[frame["cross_event"]].copy()
    fast = fast_source_history(args.fast_observations, db.get("settlement_max_date"))
    predicted = int(
        (
            frame.get("p_current_physics", pd.Series(index=frame.index, dtype=float)).notna()
            & frame.get("p_d1_physics", pd.Series(index=frame.index, dtype=float)).notna()
        ).sum()
    )
    # The current source script computes predictions in-memory; the persisted
    # scored v1 rows carry the actual expanding coverage.
    scored_path = args.v1_summary.parent / "scored_state_rows.csv"
    if scored_path.exists():
        scored = pd.read_csv(scored_path, low_memory=False)
        predicted = int((scored["p_current_physics"].notna() & scored["p_d1_physics"].notna()).sum())

    funnel = {
        "raw_city_date_hour_candidates": len(snapshot_candidates),
        "raw_dates": snapshot_candidates["target_date"].nunique(),
        "raw_cities": snapshot_candidates["city"].nunique(),
        "raw_min_date": str(snapshot_candidates["target_date"].min()),
        "raw_max_date": str(snapshot_candidates["target_date"].max()),
        "labeled_pit_states": len(labeled),
        "state_dates": labeled["target_date"].nunique(),
        "state_cities": labeled["city"].nunique(),
        "state_min_date": str(labeled["target_date"].min()),
        "state_max_date": str(labeled["target_date"].max()),
        "generic_bracket_advances": len(generic_cross),
        "generic_cross_dates": generic_cross["target_date"].nunique(),
        "generic_cross_cities": generic_cross["city"].nunique(),
        "expanding_predicted_states": predicted,
        "old_edge_selected_current_no_rows": int(old.get("rows") or 0),
    }
    same_window_candidates = snapshot_candidates[
        snapshot_candidates["target_date"].astype(str).between(
            funnel["state_min_date"], funnel["state_max_date"]
        )
    ]
    labeled_unique_hours = labeled.drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )
    funnel.update(
        {
            "raw_same_window_city_date_hour_candidates": len(same_window_candidates),
            "labeled_unique_city_date_hours": len(labeled_unique_hours),
        }
    )
    funnel_rows = [
        {
            "lineage": "raw saved-field audit",
            "stage": "all-window hourly-last inventory",
            "rows": funnel["raw_city_date_hour_candidates"],
            "dates": funnel["raw_dates"],
            "cities": funnel["raw_cities"],
            "grain": "last saved snapshot per city/date/local-hour",
        },
        {
            "lineage": "raw saved-field audit",
            "stage": "same-window hourly-last inventory",
            "rows": funnel["raw_same_window_city_date_hour_candidates"],
            "dates": funnel["state_dates"],
            "cities": funnel["state_cities"],
            "grain": "hourly-last inventory restricted to the v3 labeled date window",
        },
        {
            "lineage": "v3 reconstructed PIT",
            "stage": "clean labeled decision snapshots",
            "rows": funnel["labeled_pit_states"],
            "dates": funnel["state_dates"],
            "cities": funnel["state_cities"],
            "grain": "every reconstructable decision snapshot; multiple rows per local hour",
        },
        {
            "lineage": "v3 reconstructed PIT",
            "stage": "clean labeled unique city-date-hours",
            "rows": funnel["labeled_unique_city_date_hours"],
            "dates": funnel["state_dates"],
            "cities": funnel["state_cities"],
            "grain": "v3 states deduplicated to city/date/local-hour for comparability",
        },
        {
            "lineage": "v3 reconstructed PIT",
            "stage": "generic running-high bracket advances",
            "rows": funnel["generic_bracket_advances"],
            "dates": funnel["generic_cross_dates"],
            "cities": funnel["generic_cross_cities"],
            "grain": "current bracket changed from previous state; not source-specific",
        },
        {
            "lineage": "v3 reconstructed PIT",
            "stage": "generic cross current-NO executable",
            "rows": int(summary.loc[summary["expression"].eq("current_no"), "rows"].iloc[0]),
            "dates": int(summary.loc[summary["expression"].eq("current_no"), "dates"].iloc[0]),
            "cities": int(summary.loc[summary["expression"].eq("current_no"), "cities"].iloc[0]),
            "grain": "all cross rows with ask 0.001..0.999 and ask_size>=5",
        },
        {
            "lineage": "v3 reconstructed PIT",
            "stage": "v1 model edge>=2c current-NO",
            "rows": funnel["old_edge_selected_current_no_rows"],
            "dates": int(old.get("dates") or 0),
            "cities": int(old.get("cities") or 0),
            "grain": "final model-selected subset",
        },
    ]
    payload = {
        "schema_version": "source_event_denominator_audit_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_zero_notional",
        "data_snapshot": db,
        "funnel": funnel,
        "funnel_rows": funnel_rows,
        "old_edge_selected_current_no": old,
        "unconditional_cross_summary": summary.replace({np.nan: None}).to_dict("records"),
        "true_fast_source_history": fast,
        "research_semantics_correction": (
            "v1 cross_event is a generic current-bracket change and contains no high-frequency source join"
        ),
        "gates": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out_dir / "unconditional_cross_summary.csv", index=False)
    daily.to_csv(args.out_dir / "unconditional_cross_daily.csv", index=False)
    pd.DataFrame(funnel_rows).to_csv(args.out_dir / "funnel.csv", index=False)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(render_report(payload, summary, status), encoding="utf-8")
    print(json.dumps(json_ready({"funnel": funnel, "gates": payload["gates"]}), ensure_ascii=False))


if __name__ == "__main__":
    main()
