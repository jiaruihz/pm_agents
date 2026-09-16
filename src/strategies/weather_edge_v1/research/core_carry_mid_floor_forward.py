#!/usr/bin/env python3
"""Evaluate whether Core Carry's market-mid >= 0.80 live gate is justified.

The replay keeps the deployed v3 probability, exact-bracket contract, first
positive full-ladder taker EV selector, and all data/support checks fixed.  It
changes only the lower market-mid boundary.  Probability scoring and execution
scoring use separate, explicitly reported denominators.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[4]
RESEARCH_ID = "current_yes_core_carry_mid_floor_forward_v1"
DEFAULT_SCORES = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_ARTIFACT = Path(
    "/Users/deepsleep/projects/pm_agents_market_books_prod/"
    "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json"
)
HISTORICAL_SUMMARY = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-30-current-yes-core-carry-post-rebracket-event-ab-v1.json"
)
OUT_JSON = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-08-current-yes-core-carry-mid-floor-forward-v1.json"
)
OUT_MD = OUT_JSON.with_suffix(".md")
DEFAULT_LEDGER_DIR = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
    "current_yes_core_carry_mid_floor_forward_v1"
)

FLOORS = (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90)
PROBABILITY_BANDS = (
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.90),
    (0.90, 0.9895),
)
BOOTSTRAP_REPS = 10_000
SEED = 20260808


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield row


def exact_bounded(frame: pd.DataFrame) -> pd.Series:
    bracket = frame["current_bracket"].fillna("").astype(str).str.lower()
    question = frame["current_question"].fillna("").astype(str).str.lower()
    return ~(
        bracket.str.contains(r"\+", regex=True)
        | question.str.contains(
            r"or above|or higher|or below|or lower|or less", regex=True
        )
    )


def load_first_scored_checkpoints(
    path: Path,
    *,
    artifact_hash: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    raw = pd.DataFrame(
        row
        for row in iter_jsonl(path)
        if str(row.get("artifact_hash") or "") == artifact_hash
        and start_date <= str(row.get("target_date") or "") <= end_date
    )
    if raw.empty:
        raise RuntimeError("no score rows match artifact/date window")
    counts = {"stable_artifact_raw_rows": int(len(raw))}
    raw["decision_snapshot_dt"] = pd.to_datetime(
        raw["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    raw["created_dt"] = pd.to_datetime(raw["created_at_utc"], utc=True, errors="coerce")
    # The production contract is the first *successfully scored* checkpoint in
    # each local hour.  A restart may append the same checkpoint again.
    scored = raw[
        raw["checkpoint_key"].notna()
        & raw["decision_snapshot_dt"].notna()
        & raw["model_probability_hold"].notna()
    ].copy()
    counts["successfully_scored_raw_rows"] = int(len(scored))
    first = (
        scored.sort_values(["decision_snapshot_dt", "created_dt"])
        .drop_duplicates("checkpoint_key", keep="first")
        .reset_index(drop=True)
    )
    counts["first_scored_unique_checkpoints"] = int(len(first))
    counts["restart_duplicate_rows_removed"] = int(len(scored) - len(first))
    return first, counts


def load_settlements(
    db_path: Path, *, start_date: str, end_date: str
) -> pd.DataFrame:
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        frame = pd.read_sql_query(
            """
            SELECT city, target_date, bracket, final_price, settlement_status,
                   source_path
            FROM settlement_outcomes
            WHERE source_system = 'pm_history'
              AND target_date BETWEEN ? AND ?
            """,
            conn,
            params=(start_date, end_date),
        )
    if frame.duplicated(["city", "target_date", "bracket"]).any():
        raise RuntimeError("duplicate pm_history city/date/bracket settlements")
    frame["label"] = pd.to_numeric(frame["final_price"], errors="coerce")
    frame.loc[frame["label"].ge(0.999), "label"] = 1.0
    frame.loc[frame["label"].le(0.001), "label"] = 0.0
    return frame


def build_denominators(
    checkpoints: pd.DataFrame,
    settlements: pd.DataFrame,
    *,
    market_mid_ceiling: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    work = checkpoints.copy()
    work["current_bracket"] = work["current_bracket"].astype(str)
    work["market_mid"] = pd.to_numeric(work["market_mid"], errors="coerce")
    work["model_probability_hold"] = pd.to_numeric(
        work["model_probability_hold"], errors="coerce"
    )
    work["current_yes_effective_cost"] = pd.to_numeric(
        work["current_yes_effective_cost"], errors="coerce"
    )
    common = (
        work["checkpoint_eligible"].eq(True)  # noqa: E712
        & work["observation_freshness_valid"].eq(True)  # noqa: E712
        & work["model_input_support_status"].eq("within_training_support")
        & work["current_yes_book_status"].eq("ok")
        & work["market_mid"].between(0.0, market_mid_ceiling, inclusive="both")
        & work["model_probability_hold"].between(0.0, 1.0, inclusive="both")
        & exact_bounded(work)
    )
    probability_pre_settlement = work[common].copy()
    joined = probability_pre_settlement.merge(
        settlements[
            ["city", "target_date", "bracket", "label", "settlement_status", "source_path"]
        ],
        left_on=["city", "target_date", "current_bracket"],
        right_on=["city", "target_date", "bracket"],
        how="left",
        validate="many_to_one",
    )
    joined["settled_binary"] = (
        joined["settlement_status"].eq("settled") & joined["label"].isin([0.0, 1.0])
    )
    coverage = (
        joined.groupby("target_date", as_index=False)
        .agg(
            probability_rows=("checkpoint_key", "size"),
            settled_rows=("settled_binary", "sum"),
        )
        .sort_values("target_date")
    )
    coverage["coverage"] = coverage["settled_rows"] / coverage["probability_rows"]
    incomplete_rows = joined[~joined["settled_binary"]].copy()
    probability = joined[joined["settled_binary"]].copy()
    ladder_exec = probability["taker_ladder"].map(
        lambda value: isinstance(value, dict) and bool(value.get("executable"))
    )
    execution = probability[
        ladder_exec & probability["current_yes_effective_cost"].notna()
    ].copy()
    execution["edge_after_cost"] = (
        execution["model_probability_hold"]
        - execution["current_yes_effective_cost"]
    )
    funnel = {
        "common_probability_pre_settlement_rows": int(len(probability_pre_settlement)),
        "settled_probability_rows": int(len(probability)),
        "settled_probability_city_days": int(
            probability.groupby(["city", "target_date"]).ngroups
        ),
        "executable_rows": int(len(execution)),
        "executable_city_days": int(execution.groupby(["city", "target_date"]).ngroups),
        "settlement_coverage_by_date": coverage.to_dict(orient="records"),
        "unsettled_probability_rows": int(len(incomplete_rows)),
        "unsettled_probability_cases": incomplete_rows[
            [
                "city",
                "target_date",
                "current_bracket",
                "market_mid",
                "model_probability_hold",
                "current_yes_effective_cost",
                "settlement_status",
                "label",
            ]
        ].to_dict(orient="records"),
    }
    return probability, execution, funnel


def select_first_positive(
    execution: pd.DataFrame, *, floor: float, ceiling: float
) -> pd.DataFrame:
    selected = execution[
        execution["market_mid"].between(floor, ceiling, inclusive="both")
        & execution["edge_after_cost"].gt(0.0)
    ].copy()
    return (
        selected.sort_values(["target_date", "city", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .reset_index(drop=True)
    )


def roi_bootstrap(entries: pd.DataFrame) -> list[float]:
    if entries.empty:
        return []
    daily = (
        entries.assign(
            _pnl=entries["label"] - entries["current_yes_effective_cost"],
            _cost=entries["current_yes_effective_cost"],
        )
        .groupby("target_date")[["_pnl", "_cost"]]
        .sum()
        .to_numpy(float)
    )
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sample = daily[rng.integers(0, len(daily), len(daily))]
        cost = float(sample[:, 1].sum())
        if cost > 0:
            draws.append(float(sample[:, 0].sum() / cost))
    return draws


def entry_metrics(entries: pd.DataFrame, *, shares: float) -> dict[str, Any]:
    if entries.empty:
        return {"entries": 0}
    cost = entries["current_yes_effective_cost"].astype(float)
    pnl_per_share = entries["label"].astype(float) - cost
    draws = roi_bootstrap(entries)
    return {
        "entries": int(len(entries)),
        "dates": int(entries["target_date"].nunique()),
        "cities": int(entries["city"].nunique()),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "win_rate": float(entries["label"].mean()),
        "avg_market_mid": float(entries["market_mid"].mean()),
        "avg_model_probability": float(entries["model_probability_hold"].mean()),
        "avg_effective_cost_per_share": float(cost.mean()),
        "avg_edge_after_cost": float(entries["edge_after_cost"].mean()),
        "submitted_notional_at_fixed_shares": float(shares * cost.sum()),
        "realized_pnl_at_fixed_shares": float(shares * pnl_per_share.sum()),
        "fee_adjusted_roi": float(pnl_per_share.sum() / cost.sum()),
        "target_date_block_roi_ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
    }


def paired_pnl_delta_bootstrap(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    shares: float,
    all_dates: list[str],
) -> dict[str, Any]:
    def daily_pnl(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(0.0, index=all_dates)
        pnl = shares * (frame["label"] - frame["current_yes_effective_cost"])
        return pnl.groupby(frame["target_date"]).sum().reindex(all_dates, fill_value=0.0)

    delta = (daily_pnl(candidate) - daily_pnl(baseline)).to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws = np.empty(BOOTSTRAP_REPS, dtype=float)
    for index in range(BOOTSTRAP_REPS):
        draws[index] = delta[rng.integers(0, len(delta), len(delta))].sum()
    return {
        "paired_pnl_delta_usd": float(delta.sum()),
        "target_date_block_ci95_usd": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "dates_candidate_better": int((delta > 0).sum()),
        "dates_equal": int((delta == 0).sum()),
        "dates_candidate_worse": int((delta < 0).sum()),
    }


def daily_pnl(frame: pd.DataFrame, *, shares: float, all_dates: list[str]) -> pd.Series:
    if frame.empty:
        return pd.Series(0.0, index=all_dates)
    pnl = shares * (frame["label"] - frame["current_yes_effective_cost"])
    return pnl.groupby(frame["target_date"]).sum().reindex(all_dates, fill_value=0.0)


def threshold_familywise_sign_flip(
    policy_frames: dict[float, pd.DataFrame],
    *,
    baseline_floor: float,
    shares: float,
    all_dates: list[str],
) -> dict[str, Any]:
    """Exact paired test that accounts for selecting the best tested floor."""

    baseline = daily_pnl(
        policy_frames[baseline_floor], shares=shares, all_dates=all_dates
    ).to_numpy(float)
    candidate_floors = [floor for floor in policy_frames if floor != baseline_floor]
    deltas = np.column_stack(
        [
            daily_pnl(policy_frames[floor], shares=shares, all_dates=all_dates).to_numpy(float)
            - baseline
            for floor in candidate_floors
        ]
    )
    totals = deltas.sum(axis=0)
    best_index = int(np.argmax(totals))
    observed_max = float(totals[best_index])
    null_maxima: list[float] = []
    for mask in range(1 << len(all_dates)):
        signs = np.array(
            [1.0 if mask & (1 << index) else -1.0 for index in range(len(all_dates))]
        )
        null_maxima.append(float(np.max((deltas * signs[:, None]).sum(axis=0))))
    p_value = float(np.mean(np.asarray(null_maxima) >= observed_max - 1e-12))
    per_floor = []
    for index, floor in enumerate(candidate_floors):
        delta = deltas[:, index]
        observed = float(delta.sum())
        null = []
        for mask in range(1 << len(all_dates)):
            signs = np.array(
                [1.0 if mask & (1 << row) else -1.0 for row in range(len(all_dates))]
            )
            null.append(float((delta * signs).sum()))
        per_floor.append(
            {
                "market_mid_floor": floor,
                "observed_pnl_delta_usd": observed,
                "exact_one_sided_sign_flip_p": float(
                    np.mean(np.asarray(null) >= observed - 1e-12)
                ),
            }
        )
    return {
        "method": "exact target-date paired sign flip; statistic=max PnL improvement across tested floors",
        "tested_candidate_floors": candidate_floors,
        "best_post_hoc_floor": candidate_floors[best_index],
        "observed_best_pnl_delta_usd": observed_max,
        "familywise_one_sided_p": p_value,
        "per_floor_unadjusted": per_floor,
    }


def probability_metrics(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    if frame.empty:
        return {"band": name, "rows": 0}
    counts = frame.groupby(["city", "target_date"])["label"].transform("size")
    weights = 1.0 / counts.clip(lower=1).to_numpy(float)
    y = frame["label"].to_numpy(float)
    model = frame["model_probability_hold"].clip(1e-6, 1 - 1e-6).to_numpy(float)
    market = frame["market_mid"].clip(1e-6, 1 - 1e-6).to_numpy(float)
    model_brier = float(np.average((model - y) ** 2, weights=weights))
    market_brier = float(np.average((market - y) ** 2, weights=weights))
    model_ll = float(
        np.average(-(y * np.log(model) + (1 - y) * np.log(1 - model)), weights=weights)
    )
    market_ll = float(
        np.average(
            -(y * np.log(market) + (1 - y) * np.log(1 - market)), weights=weights
        )
    )
    diagnostics = pd.DataFrame(
        {
            "target_date": frame["target_date"].astype(str).to_numpy(),
            "weight": weights,
            "brier_delta_weighted": weights * ((model - y) ** 2 - (market - y) ** 2),
            "logloss_delta_weighted": weights
            * (
                -(y * np.log(model) + (1 - y) * np.log(1 - model))
                + (y * np.log(market) + (1 - y) * np.log(1 - market))
            ),
        }
    )
    daily = diagnostics.groupby("target_date").sum(numeric_only=True).to_numpy(float)
    rng = np.random.default_rng(SEED)
    brier_draws: list[float] = []
    logloss_draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sample = daily[rng.integers(0, len(daily), len(daily))]
        denominator = float(sample[:, 0].sum())
        if denominator > 0:
            brier_draws.append(float(sample[:, 1].sum() / denominator))
            logloss_draws.append(float(sample[:, 2].sum() / denominator))
    return {
        "band": name,
        "rows": int(len(frame)),
        "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
        "dates": int(frame["target_date"].nunique()),
        "observed_hold_rate": float(np.average(y, weights=weights)),
        "avg_market_mid": float(np.average(market, weights=weights)),
        "avg_model_probability": float(np.average(model, weights=weights)),
        "model_minus_market_brier": model_brier - market_brier,
        "model_minus_market_brier_target_date_ci95": [
            float(np.quantile(brier_draws, 0.025)),
            float(np.quantile(brier_draws, 0.975)),
        ],
        "model_minus_market_logloss": model_ll - market_ll,
        "model_minus_market_logloss_target_date_ci95": [
            float(np.quantile(logloss_draws, 0.025)),
            float(np.quantile(logloss_draws, 0.975)),
        ],
    }


def group_diagnostics(frame: pd.DataFrame, column: str, *, shares: float) -> list[dict[str, Any]]:
    if frame.empty or column not in frame:
        return []
    rows: list[dict[str, Any]] = []
    for value, group in frame.groupby(column, dropna=False):
        rows.append({"value": str(value), **entry_metrics(group, shares=shares)})
    return sorted(rows, key=lambda row: (-int(row.get("entries", 0)), row["value"]))


def compact_entry_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "market_mid",
        "model_probability_hold",
        "current_yes_effective_cost",
        "edge_after_cost",
        "label",
        "day_regime",
        "intraday_state",
        "heating_done_bucket_v1",
        "composite_regime",
    ]
    return frame[[column for column in columns if column in frame]].to_dict(orient="records")


def frame_sha256(frame: pd.DataFrame, columns: list[str]) -> str:
    ordered = frame[columns].sort_values(columns[:4]).reset_index(drop=True)
    payload = ordered.to_json(
        orient="records", date_format="iso", date_unit="us", double_precision=15
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value):+.2%}"


def render_report(payload: dict[str, Any]) -> str:
    policy_lines = [
        "| floor | entries | W-L | avg mid | avg cost | PnL (10sh) | ROI | date-block 95% CI | vs 0.80 PnL |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["floor_replay"]:
        metric = row["metrics"]
        paired = row["paired_vs_080"]
        ci = metric.get("target_date_block_roi_ci95")
        policy_lines.append(
            f"| {row['market_mid_floor']:.2f} | {metric.get('entries', 0)} | "
            f"{metric.get('wins', 0)}-{metric.get('losses', 0)} | "
            f"{pct(metric.get('avg_market_mid'))} | {pct(metric.get('avg_effective_cost_per_share'))} | "
            f"${metric.get('realized_pnl_at_fixed_shares', 0):+.2f} | "
            f"{pct(metric.get('fee_adjusted_roi'))} | "
            f"{'NA' if not ci else f'[{pct(ci[0])}, {pct(ci[1])}]'} | "
            f"${paired['paired_pnl_delta_usd']:+.2f} |"
        )
    probability_lines = [
        "| mid band | rows / city-days | actual | model p | market | model-market Brier | model-market logloss |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["forward_probability_by_band"]:
        probability_lines.append(
            f"| {row['band']} | {row.get('rows', 0)} / {row.get('city_days', 0)} | "
            f"{pct(row.get('observed_hold_rate'))} | {pct(row.get('avg_model_probability'))} | "
            f"{pct(row.get('avg_market_mid'))} | "
            f"{row.get('model_minus_market_brier', float('nan')):+.5f} | "
            f"{row.get('model_minus_market_logloss', float('nan')):+.5f} |"
        )
    coverage_lines = [
        f"- `{row['target_date']}`: {row['settled_rows']}/{row['probability_rows']} ({row['coverage']:.0%})"
        for row in payload["evidence_funnel"]["settlement_coverage_by_date"]
    ]
    floor_050 = next(row for row in payload["floor_replay"] if row["market_mid_floor"] == 0.5)
    floor_080 = next(row for row in payload["floor_replay"] if row["market_mid_floor"] == 0.8)
    low = payload["below_080_positive_ev"]
    historical = payload["historical_reference"]
    multiplicity = payload["threshold_multiplicity_test"]
    return "\n".join(
        [
            "# Current-YES Core Carry：0.80 market-mid gate forward 复核 v1",
            "",
            "Status: `keep live floor / floor is not model boundary / lower band remains shadow-only`",
            "",
            "## 结论",
            "",
            "`market mid >= 0.80` **不是模型有效性的自然卡点，也不是训练边界**；它是 live 资金域。"
            "本轮最新 8 个 target dates（entry label 全覆盖，probability 分母仅 1 row 缺口）没有证明把它降到 "
            "0.50/0.60/0.70/0.75 会改善收益，"
            "所以当前 live 仍保留 0.80。但 0.80 以下不是模型不能算：应继续完整评分并作为独立 shadow domain，"
            "而不是在模型层删除。",
            "",
            f"最宽的 floor 0.50 得到 `{floor_050['metrics']['entries']}` 笔，10 股 PnL "
            f"`${floor_050['metrics']['realized_pnl_at_fixed_shares']:+.2f}`、ROI "
            f"`{pct(floor_050['metrics']['fee_adjusted_roi'])}`；当前 floor 0.80 得到 "
            f"`{floor_080['metrics']['entries']}` 笔，PnL "
            f"`${floor_080['metrics']['realized_pnl_at_fixed_shares']:+.2f}`、ROI "
            f"`{pct(floor_080['metrics']['fee_adjusted_roi'])}`。两者 paired PnL 差 "
            f"`${floor_050['paired_vs_080']['paired_pnl_delta_usd']:+.2f}`，"
            f"95% target-date block CI "
            f"`[${floor_050['paired_vs_080']['target_date_block_ci95_usd'][0]:+.2f}, "
            f"${floor_050['paired_vs_080']['target_date_block_ci95_usd'][1]:+.2f}]`。",
            "",
            "## 只改变 floor 的 forward 回放",
            "",
            *policy_lines,
            "",
            "所有策略均固定：deployed v3、首次成功小时 checkpoint、fresh observation、"
            "训练 support、exact bracket、10-share full ask ladder + 官方 fee、首个正 net-EV 锁 city-day。",
            "这些是 signal-level 全部成交反事实，不是实际 live fill PnL。",
            "",
            f"事后最好的 floor 是 `{multiplicity['best_post_hoc_floor']:.2f}`，相对 0.80 多 "
            f"`${multiplicity['observed_best_pnl_delta_usd']:+.2f}`；但同时试了 6 个 floor 后，"
            f"exact target-date sign-flip family-wise p=`{multiplicity['familywise_one_sided_p']:.3f}`，"
            "不能把这 8 天的赢家阈值当新规则。floor 0.70 的改善只来自 2 个新增 winner 和 "
            "Ankara/Wellington 两个同档更早、更便宜的 winner，没有形成独立样本规模。",
            "",
            "## 概率层：0.80 以下是否属于域外",
            "",
            *probability_lines,
            "",
            "负的 proper-score delta 才表示模型优于同 rows 的 market mid。forward 只有 8 个 date blocks，"
            "六个 band 的 Brier/logloss target-date CI 均跨 0，因此只把方向当新增证据，不据此重新选阈值。",
            "",
            "历史 OOF（32 dates）同样说明 0.80 不是能力边界：",
            f"`0.50–0.80` 有 {historical['mid_050_080']['states']} states，model-market Brier "
            f"`{historical['mid_050_080']['model_minus_market_brier']:+.5f}`，首个正 taker-EV ROI "
            f"`{pct(historical['mid_050_080']['first_positive_taker_ev']['fee_adjusted_roi'])}`，"
            f"但 CI `{historical['mid_050_080']['first_positive_taker_ev']['target_date_block_ci95']}` 跨 0。"
            f"反而 `0.90–0.9895` 历史 ROI 为 "
            f"`{pct(historical['mid_090_09895']['first_positive_taker_ev']['fee_adjusted_roi'])}`。",
            "",
            "## 被 0.80 排除的正 net-EV 机会",
            "",
            f"本窗 `<0.80` 有 `{low['metrics']['entries']}` 个首个正 net-EV city-day，"
            f"`{low['metrics'].get('wins', 0)}` 胜 `{low['metrics'].get('losses', 0)}` 负，"
            f"10 股 PnL `${low['metrics'].get('realized_pnl_at_fixed_shares', 0):+.2f}`、"
            f"ROI `{pct(low['metrics'].get('fee_adjusted_roi'))}`。其中相对当前 0.80 policy："
            f"新增 city-day `{low['relationship_to_080']['incremental_city_days']}`，"
            f"同 city-day 提前/换档 `{low['relationship_to_080']['overlap_city_days']}`。",
            "",
            "这些是研究候选，不应再叠天气模式 hard gate。城市/天气 regime 只作为诊断，完整逐条 ledger 在归档产物中。",
            "",
            "## 双漏斗与数据修复",
            "",
            f"- signal funnel：stable raw `{payload['signal_funnel']['stable_artifact_raw_rows']}` → "
            f"成功 score `{payload['signal_funnel']['successfully_scored_raw_rows']}` → "
            f"去 restart 重复后 `{payload['signal_funnel']['first_scored_unique_checkpoints']}` → "
            f"同支持域 probability rows `{payload['evidence_funnel']['settled_probability_rows']}` → "
            f"10-share executable `{payload['evidence_funnel']['executable_rows']}`。",
            "- evidence funnel：settlement 在研究前 8/2–8/7 只覆盖 2–7 城；本轮按 canonical bounded "
            "backfill 补到每日 47 城，新增 2,783 条 `settlement_outcomes`。另有 San Francisco 8/1 "
            "一条旧 append-only row 仍是 0.9825/missing_bracket；当前 Gamma 已刷新为 1.0。该 row "
            "只从 probability score 分母显式排除，且其 model p 低于 10-share cost，不影响任何 floor 的 entry/PnL。",
            *coverage_lines,
            "",
            f"数据快照：artifact `{payload['denominator_scope']['artifact_hash']}`；"
            f"probability denominator SHA `{payload['denominator_scope']['evaluation_denominator_sha256']}`；"
            f"canonical DB inode `{payload['sources']['canonical_db_identity']['inode']}`。",
            "",
            "## 决策",
            "",
            "- live：保持 `market_mid_floor=0.80`，不因本轮改生产。",
            "- 语义：把 0.80 明确称为 `live_authorized_market_mid_floor`；不要称为 model support floor。",
            "- research/shadow：继续记录 `0.50 <= mid < 0.80 AND p_model > 10-share taker cost`；"
            "累计至少 30 个新 settled target dates 后，按同一预注册 selector 做一次 frozen review。",
            "- 不新增 price bucket/weather regime gate；最终候选应是连续 net-EV expression，floor 只负责 live 风险授权。",
            "",
            "## Reproduce",
            "",
            "```bash",
            ".venv/bin/python scripts/analysis/reheat_risk/"
            "research_current_yes_core_carry_europe_mid_replay_v1.py "
            "--study global-live-forward",
            "```",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--historical-summary", type=Path, default=HISTORICAL_SUMMARY)
    parser.add_argument("--start-date", default="2026-07-31")
    parser.add_argument("--end-date", default="2026-08-07")
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    args = parser.parse_args(argv)

    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    artifact_hash = str(artifact["artifact_hash"])
    entry_policy = artifact["entry_policy"]
    ceiling = float(entry_policy["market_mid_ceiling"])
    shares = float(entry_policy["taker_shares"])
    checkpoints, signal_funnel = load_first_scored_checkpoints(
        args.scores,
        artifact_hash=artifact_hash,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    settlements = load_settlements(
        args.db_path, start_date=args.start_date, end_date=args.end_date
    )
    probability, execution, evidence_funnel = build_denominators(
        checkpoints, settlements, market_mid_ceiling=ceiling
    )
    all_dates = sorted(probability["target_date"].unique().tolist())
    if all_dates != pd.date_range(args.start_date, args.end_date).strftime("%Y-%m-%d").tolist():
        raise RuntimeError(f"non-contiguous forward target dates: {all_dates}")

    policy_frames = {
        floor: select_first_positive(execution, floor=floor, ceiling=ceiling)
        for floor in FLOORS
    }
    baseline = policy_frames[0.80]
    raw_positive = checkpoints[
        checkpoints["decision_status"].eq("positive_taker_ev")
        & checkpoints["target_date"].between(args.start_date, args.end_date)
    ]
    parity = {
        "raw_positive_checkpoint_keys": sorted(raw_positive["checkpoint_key"].unique().tolist()),
        "replayed_floor_080_checkpoint_keys": sorted(baseline["checkpoint_key"].unique().tolist()),
    }
    parity["missing_from_replay"] = sorted(
        set(parity["raw_positive_checkpoint_keys"])
        - set(parity["replayed_floor_080_checkpoint_keys"])
    )
    parity["unexpected_in_replay"] = sorted(
        set(parity["replayed_floor_080_checkpoint_keys"])
        - set(parity["raw_positive_checkpoint_keys"])
    )
    if parity["missing_from_replay"] or parity["unexpected_in_replay"]:
        raise RuntimeError(f"0.80 production parity failed: {parity}")

    floor_rows: list[dict[str, Any]] = []
    for floor, frame in policy_frames.items():
        floor_rows.append(
            {
                "market_mid_floor": floor,
                "metrics": entry_metrics(frame, shares=shares),
                "paired_vs_080": paired_pnl_delta_bootstrap(
                    frame, baseline, shares=shares, all_dates=all_dates
                ),
            }
        )

    multiplicity = threshold_familywise_sign_flip(
        policy_frames,
        baseline_floor=0.80,
        shares=shares,
        all_dates=all_dates,
    )

    probability_rows = []
    for lower, upper in PROBABILITY_BANDS:
        inclusive_upper = upper == ceiling
        mask = probability["market_mid"].ge(lower) & (
            probability["market_mid"].le(upper)
            if inclusive_upper
            else probability["market_mid"].lt(upper)
        )
        probability_rows.append(
            probability_metrics(
                probability[mask],
                f"[{lower:.2f},{upper:.4f}{']' if inclusive_upper else ')'}",
            )
        )

    below_080 = select_first_positive(execution, floor=0.50, ceiling=0.80 - 1e-12)
    low_keys = set(zip(below_080["city"], below_080["target_date"]))
    baseline_keys = set(zip(baseline["city"], baseline["target_date"]))
    historical = json.loads(args.historical_summary.read_text(encoding="utf-8"))
    historical_bands = {
        row["band"]: row
        for row in historical["historical_probability_band_validation"]["bands"]
    }

    args.ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "checkpoint_key",
        "current_bracket",
        "market_mid",
        "model_probability_hold",
        "current_yes_effective_cost",
        "edge_after_cost",
        "label",
        "day_regime",
        "intraday_state",
        "heating_done_bucket_v1",
        "composite_regime",
    ]
    pd.concat(
        [frame.assign(market_mid_floor=floor) for floor, frame in policy_frames.items()],
        ignore_index=True,
    )[["market_mid_floor", *ledger_columns]].to_csv(
        args.ledger_dir / "floor_entries.csv", index=False
    )
    below_080[ledger_columns].to_csv(
        args.ledger_dir / "below_080_positive_ev_entries.csv", index=False
    )

    db_stat = args.db_path.resolve().stat()
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": generated_at,
        "observed_at_utc": generated_at,
        "status": "keep_live_floor_lower_band_shadow_only",
        "target_metric": (
            "same-checkpoint model-vs-market proper score and first-positive "
            "10-share fee-adjusted taker PnL when only market_mid_floor changes"
        ),
        "denominator_scope": {
            "target_dates": [args.start_date, args.end_date],
            "dates": all_dates,
            "artifact_hash": artifact_hash,
            "build_id": artifact_hash,
            "artifact_version": artifact["artifact_version"],
            "training_mid_support": artifact.get("training_snapshot", {}).get(
                "market_mid_support"
            ),
            "fixed_expression": "current exact bracket BUY_YES",
            "selector": "first positive 10-share full-ladder net EV per city-day",
            "candidate_grain_version": "first_scored_city_date_local_hour_v1",
            "evaluation_denominator_sha256": frame_sha256(
                probability,
                [
                    "city",
                    "target_date",
                    "current_bracket",
                    "decision_snapshot_ts_utc",
                    "market_mid",
                    "model_probability_hold",
                    "label",
                ],
            ),
        },
        "sources": {
            "scores": str(args.scores),
            "scores_sha256_at_run": file_sha256(args.scores),
            "canonical_db": str(args.db_path.resolve()),
            "canonical_db_identity": {
                "device": int(db_stat.st_dev),
                "inode": int(db_stat.st_ino),
                "size_bytes": int(db_stat.st_size),
            },
            "artifact": str(args.artifact),
            "artifact_sha256": file_sha256(args.artifact),
            "historical_summary": str(args.historical_summary),
            "historical_summary_sha256": file_sha256(args.historical_summary),
            "ledger_dir": str(args.ledger_dir),
        },
        "signal_funnel": signal_funnel,
        "evidence_funnel": evidence_funnel,
        "production_parity_floor_080": parity,
        "floor_replay": floor_rows,
        "threshold_multiplicity_test": multiplicity,
        "forward_probability_by_band": probability_rows,
        "below_080_positive_ev": {
            "metrics": entry_metrics(below_080, shares=shares),
            "relationship_to_080": {
                "incremental_city_days": len(low_keys - baseline_keys),
                "overlap_city_days": len(low_keys & baseline_keys),
            },
            "by_city": group_diagnostics(below_080, "city", shares=shares),
            "by_day_regime": group_diagnostics(below_080, "day_regime", shares=shares),
            "by_heating_done_bucket": group_diagnostics(
                below_080, "heating_done_bucket_v1", shares=shares
            ),
            "entries": compact_entry_rows(below_080),
        },
        "historical_reference": {
            "warning": "historical 5-share OOF is development evidence, not new forward",
            "training_mid_support": historical["historical_probability_band_validation"][
                "model_training_mid_support"
            ],
            "mid_050_080": historical_bands["0.50_to_0.80"],
            "mid_080_090": historical_bands["0.80_to_0.90"],
            "mid_090_09895": historical_bands["0.90_to_0.9895"],
        },
        "decision": {
            "live": "keep market_mid_floor=0.80",
            "model_semantics": "0.80 is live authorization, not model support",
            "shadow": "retain continuous 0.50<=mid<0.80 positive-10-share-net-EV scoring",
            "next_review": "after at least 30 new settled target dates on the frozen selector",
            "new_weather_or_price_gates": "none",
        },
    }
    payload = json_ready(payload)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OUT_MD.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
