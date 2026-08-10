#!/usr/bin/env python3
"""Historical PIT replay of LMVM single-rung forecast innovation.

At each first-seen forecast-state change, select exactly one YES rung by
``(P_model_after-P_model_before) - (P_market_after-P_market_before)``. Entry is
the same-snapshot executable ask; exits are future executable bids with both
Weather taker fees. Static residual, model mode, and market favorite remain
same-row controls. This script is research-only and has no production writes.

Lineage warning: ``paper_snapshot.model_prob`` is the legacy empirical-error
probability from ``compute_bracket_probs``. It is not a current market-anchored
probability artifact. Results from this script are therefore a legacy-input
baseline unless the probability source is replaced explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import research_lmvm_single_yes_repricing_v1 as base  # noqa: E402
from scripts.ops.weather_lmvm_forecast_repricing_shadow_v1 import (  # noqa: E402
    parse_clock_exact_snapshot_file,
)
from weather_data_feed.production_paths import (  # noqa: E402
    current_strategy_snapshots,
    historical_full_ladder_root,
    historical_targeted_root,
)


DEFAULT_OUTPUT = ROOT / "docs/analysis/2026-08/generated/lmvm_forecast_innovation_v2"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-04-research-lmvm-forecast-innovation-v2.md"
SEED = 20260804
DEFAULT_END_TARGET_DATE = (
    datetime.now(base.ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--snapshot-dir", type=Path, default=base.DEFAULT_SNAPSHOTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--draws", type=int, default=5000)
    parser.add_argument("--max-files", type=int)
    parser.add_argument(
        "--end-target-date",
        default=DEFAULT_END_TARGET_DATE,
        help="inclusive target-date cutoff (default: Asia/Shanghai T-1)",
    )
    parser.add_argument(
        "--current-snapshot-dir",
        type=Path,
        default=current_strategy_snapshots(),
    )
    parser.add_argument(
        "--historical-full-ladder-root",
        type=Path,
        default=historical_full_ladder_root(),
    )
    parser.add_argument(
        "--historical-targeted-root",
        type=Path,
        default=historical_targeted_root(),
    )
    parser.add_argument(
        "--legacy-snapshot-only",
        action="store_true",
        help="diagnostic negative control; do not join migrated book sources",
    )
    return parser.parse_args()


_STAMP = re.compile(r"snapshot_(\d{8})_(\d{4})\.json$")


def _companion_book(root: Path, snapshot: Path) -> Path | None:
    match = _STAMP.match(snapshot.name)
    if match is None:
        return None
    day = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
    candidate = (
        root
        / "orderbook_snapshots"
        / day
        / snapshot.name.replace("snapshot_", "orderbook_snapshot_", 1).replace(".json", ".jsonl.gz")
    )
    return candidate if candidate.exists() else None


def discover_snapshot_inputs(
    snapshot_dir: Path,
    current_dir: Path,
    full_ladder_root: Path,
    targeted_root: Path,
    *,
    legacy_only: bool,
) -> list[tuple[str, str | None]]:
    """Resolve one best snapshot per capture stamp across schema generations."""

    selected: dict[str, tuple[int, Path, Path | None]] = {}

    def add(root: Path, rank: int, book_root: Path | None = None) -> None:
        if not root.exists():
            return
        for snapshot in root.glob("snapshot_*.json"):
            book = _companion_book(book_root, snapshot) if book_root is not None else None
            if book is None and not legacy_only:
                book = _companion_book(full_ladder_root, snapshot) or _companion_book(
                    targeted_root, snapshot
                )
            previous = selected.get(snapshot.name)
            if previous is None or rank > previous[0]:
                selected[snapshot.name] = (rank, snapshot, book)

    add(snapshot_dir, 0)
    if not legacy_only:
        add(current_dir, 0)
        add(targeted_root / "paper_snapshots", 1, targeted_root)
        add(full_ladder_root / "paper_snapshots", 2, full_ladder_root)
    return [
        (str(snapshot), str(book) if book is not None else None)
        for _, snapshot, book in (selected[key] for key in sorted(selected))
    ]


def _normalize_current_state(row: dict[str, Any]) -> dict[str, Any] | None:
    if int(row.get("lead_days") or -1) not in {1, 2}:
        return None
    rungs = [dict(rung) for rung in row.get("rungs") or []]
    model_mass = sum(float(rung["model_prob"]) for rung in rungs)
    market_mass = sum(float(rung["market_prob"]) for rung in rungs)
    if not rungs or not 0.80 <= model_mass <= 1.20 or not 0.50 <= market_mass <= 1.50:
        return None
    for rung in rungs:
        rung["model_prob"] = float(rung["model_prob"]) / model_mass
        rung["model_prob_raw"] = float(rung["model_prob"])
        rung["market_mid"] = float(rung["market_prob"])
        rung["market_prob"] = float(rung["market_prob"]) / market_mass
    decision_epoch = float(row["decision_epoch"])
    local = datetime.fromtimestamp(decision_epoch, timezone.utc).astimezone(
        base.ZoneInfo(str(row["market_timezone"]))
    )
    return {
        **row,
        "snapshot_epoch": decision_epoch,
        "snapshot_ts_utc": str(row.get("decision_ts_utc") or row.get("snapshot_ts_utc")),
        "source_snapshot_ts_utc": row.get("snapshot_ts_utc"),
        "book_available_at_utc": row.get("decision_ts_utc"),
        "decision_local": local.isoformat(),
        "decision_hour_local": local.hour + local.minute / 60.0,
        "model_probability_sum_raw": model_mass,
        "market_mid_sum_raw": market_mass,
        "rungs": rungs,
    }


def parse_repricing_snapshot(
    item: tuple[str, str | None],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    path = Path(item[0])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {"files_read": 1, "files_invalid": 1}
    if isinstance(payload.get("canonical_orderbook_source"), dict):
        rows, counts = parse_clock_exact_snapshot_file(path)
        normalized = [state for row in rows if (state := _normalize_current_state(row)) is not None]
        counts = dict(counts)
        counts["current_canonical_states"] = len(normalized)
        counts["current_canonical_mass_blocked"] = len(rows) - len(normalized)
        return normalized, counts
    return base.parse_snapshot_with_orderbook(item)


def paired_rungs(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = {str(row["condition_id"]): row for row in previous["rungs"]}
    paired: list[dict[str, Any]] = []
    for row in current["rungs"]:
        prior = before.get(str(row["condition_id"]))
        if prior is None:
            continue
        model_delta = float(row["model_prob"]) - float(prior["model_prob"])
        market_delta = float(row["market_prob"]) - float(prior["market_prob"])
        paired.append(
            {
                **row,
                "model_probability_before": float(prior["model_prob"]),
                "model_probability_after": float(row["model_prob"]),
                "market_probability_before": float(prior["market_prob"]),
                "market_probability_after": float(row["market_prob"]),
                "model_probability_delta": model_delta,
                "market_probability_delta": market_delta,
                "forecast_innovation_score": model_delta - market_delta,
            }
        )
    return paired


def forecast_update_pairs(states: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], Counter[str]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        grouped[base._stream_key(state)].append(state)
    output: list[tuple[dict[str, Any], dict[str, Any]]] = []
    counts: Counter[str] = Counter()
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["snapshot_epoch"], row["snapshot_id"]))
        previous: dict[str, Any] | None = None
        for current in rows:
            if previous is None:
                counts["left_censored_streams"] += 1
            elif previous["forecast_state_key"] != current["forecast_state_key"]:
                counts["forecast_update_events"] += 1
                paired = paired_rungs(previous, current)
                if len(paired) == len(current["rungs"]):
                    output.append((previous, current))
                else:
                    counts["blocked_unpaired_ladder"] += 1
            previous = current
    counts["paired_update_events"] = len(output)
    return output, counts


def _candidate_base(state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "snapshot_id", "source_path", "snapshot_ts_utc", "snapshot_epoch",
        "decision_local", "decision_hour_local", "lead_days", "city", "target_date",
        "event_slug", "market_timezone", "forecast_source", "forecast_model",
        "model_version", "forecast_state_key", "forecast_state_basis",
        "model_init_utc_estimated", "forecast_max_f", "rung_count",
        "model_probability_sum_raw", "market_mid_sum_raw",
    )
    output = {key: state[key] for key in keys}
    for key in (
        "source_snapshot_ts_utc",
        "clock_lineage_status",
        "book_source_path",
        "book_available_at_utc",
    ):
        output[key] = state.get(key)
    return output


def build_innovation_candidates(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for previous, current in pairs:
        paired = paired_rungs(previous, current)
        selected = max(
            paired,
            key=lambda row: (
                float(row["forecast_innovation_score"]),
                float(row["model_probability_delta"]),
                float(row["model_probability_after"]),
                -float(row["yes_ask"]),
            ),
        )
        ask = float(selected["yes_ask"])
        entry_fee = base.weather_fee_per_share(ask)
        rows.append(
            {
                **_candidate_base(current),
                "forecast_state_before": previous["forecast_state_key"],
                "policy": "forecast_innovation_argmax",
                "condition_id": selected["condition_id"],
                "bracket": selected["bracket"],
                "question": selected["question"],
                "model_prob": selected["model_probability_after"],
                "market_prob": selected["market_probability_after"],
                "model_probability_before": selected["model_probability_before"],
                "market_probability_before": selected["market_probability_before"],
                "model_probability_delta": selected["model_probability_delta"],
                "market_probability_delta": selected["market_probability_delta"],
                "forecast_innovation_score": selected["forecast_innovation_score"],
                "entry_bid": selected["yes_bid"],
                "entry_ask": ask,
                "entry_bid_size": selected["yes_bid_size"],
                "entry_ask_size": selected["yes_ask_size"],
                "entry_fee_per_share": entry_fee,
                "model_edge_after_entry_fee": float(selected["model_prob"]) - ask - entry_fee,
                "policy_eligible": True,
            }
        )
    return pd.DataFrame(rows)


def build_full_ladder_panel(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> pd.DataFrame:
    """Materialize every selected and unselected rung at each forecast event.

    The event denominator is fixed before any execution policy is applied.  Old
    paper snapshots do not expose an upstream provider-availability clock, so
    that clock remains explicitly missing rather than being aliased to the
    collector's earliest observed snapshot.
    """

    rows: list[dict[str, Any]] = []
    for previous, current in pairs:
        paired = paired_rungs(previous, current)
        selected = max(
            paired,
            key=lambda row: (
                float(row["forecast_innovation_score"]),
                float(row["model_probability_delta"]),
                float(row["model_probability_after"]),
                -float(row["yes_ask"]),
            ),
        )
        event_id = hashlib.sha256(
            (
                f"{current['snapshot_id']}|{previous['forecast_state_key']}|"
                f"{current['forecast_state_key']}"
            ).encode("utf-8")
        ).hexdigest()
        market_rank = {
            str(row["condition_id"]): rank
            for rank, row in enumerate(
                sorted(paired, key=lambda item: float(item["market_probability_after"]), reverse=True),
                start=1,
            )
        }
        model_rank = {
            str(row["condition_id"]): rank
            for rank, row in enumerate(
                sorted(paired, key=lambda item: float(item["model_probability_after"]), reverse=True),
                start=1,
            )
        }
        for rung in paired:
            ask = float(rung["yes_ask"])
            entry_fee = base.weather_fee_per_share(ask)
            condition_id = str(rung["condition_id"])
            rows.append(
                {
                    **_candidate_base(current),
                    "forecast_event_id": event_id,
                    "forecast_state_before": previous["forecast_state_key"],
                    "condition_id": condition_id,
                    "bracket": rung["bracket"],
                    "question": rung["question"],
                    "selected_by_innovation": condition_id == str(selected["condition_id"]),
                    "model_prob": rung["model_probability_after"],
                    "market_prob": rung["market_probability_after"],
                    "model_probability_before": rung["model_probability_before"],
                    "model_probability_after": rung["model_probability_after"],
                    "market_probability_before": rung["market_probability_before"],
                    "market_probability_after": rung["market_probability_after"],
                    "model_probability_delta": rung["model_probability_delta"],
                    "market_probability_delta": rung["market_probability_delta"],
                    "forecast_innovation_score": rung["forecast_innovation_score"],
                    "market_probability_rank": market_rank[condition_id],
                    "model_probability_rank": model_rank[condition_id],
                    "entry_bid": rung["yes_bid"],
                    "entry_ask": ask,
                    "entry_bid_size": rung["yes_bid_size"],
                    "entry_ask_size": rung["yes_ask_size"],
                    "entry_spread": ask - float(rung["yes_bid"]),
                    "entry_fee_per_share": entry_fee,
                    "model_edge_after_entry_fee": float(rung["model_probability_after"])
                    - ask
                    - entry_fee,
                    "forecast_issue_time_utc": current.get("model_init_utc_estimated"),
                    "provider_first_seen_at_utc": None,
                    "provider_first_seen_status": "unavailable_in_reconstructed_archive",
                    "collector_first_seen_at_utc": current["snapshot_ts_utc"],
                    "collector_first_seen_status": (
                        "strategy_snapshot_first_observed_not_forecast_collector_event"
                        if current.get("clock_lineage_status")
                        == "collector_exact_joined_full_ladder_v1"
                        else "legacy_earliest_observed_not_collector_exact"
                    ),
                    "book_snapshot_time_utc": current.get("book_available_at_utc")
                    or current["snapshot_ts_utc"],
                    "execution_time_utc": current["snapshot_ts_utc"],
                    "execution_time_status": current.get("clock_lineage_status")
                    or "same_snapshot_replay_assumption",
                    "candidate_selected": condition_id == str(selected["condition_id"]),
                }
            )
    return pd.DataFrame(rows)


def assign_period(rows: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    result = rows.copy()
    dates = sorted(str(value) for value in result["target_date"].dropna().unique())
    if len(dates) < 3:
        result["period"] = "all_history"
        return result, None
    cutoff = dates[max(1, int(len(dates) * 2 / 3))]
    result["period"] = np.where(result["target_date"].astype(str) < cutoff, "development", "late_holdout")
    return result, cutoff


def _roi(rows: pd.DataFrame, horizon: int) -> float:
    prefix = f"h{horizon}"
    usable = rows[rows[f"{prefix}_net_pnl_usd"].notna()].copy()
    cost = (usable["entry_ask"] + usable["entry_fee_per_share"]) * usable[f"{prefix}_executable_shares"]
    return float(usable[f"{prefix}_net_pnl_usd"].sum() / cost.sum()) if float(cost.sum()) else math.nan


def markout_summary(rows: pd.DataFrame, draws: int) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    scopes = [("all_history", rows)] + [(name, group) for name, group in rows.groupby("period")]
    for scope, scoped in scopes:
        for (policy, lead_days), group in scoped.groupby(["policy", "lead_days"]):
            eligible = group[group["policy_eligible"]].copy()
            for horizon in base.HORIZONS_MIN:
                prefix = f"h{horizon}"
                usable = eligible[eligible[f"{prefix}_net_pnl_usd"].notna()].copy()
                usable["entry_cost_usd"] = (
                    (usable["entry_ask"] + usable["entry_fee_per_share"])
                    * usable[f"{prefix}_executable_shares"]
                )
                point, low, high, date_count = base.block_bootstrap_ratio(
                    usable,
                    f"{prefix}_net_pnl_usd",
                    "entry_cost_usd",
                    draws,
                    SEED + horizon + int(lead_days),
                )
                output.append(
                    {
                        "period": scope,
                        "policy": policy,
                        "lead_days": int(lead_days),
                        "horizon_min": horizon,
                        "signals": len(eligible),
                        "covered": len(usable),
                        "target_dates": date_count,
                        "positive_rate": float((usable[f"{prefix}_net_pnl_usd"] > 0).mean()) if len(usable) else math.nan,
                        "turnover_roi": point,
                        "ci_low": low,
                        "ci_high": high,
                    }
                )
    return pd.DataFrame(output)


def paired_policy_deltas(rows: pd.DataFrame, draws: int) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    controls = ("residual_argmax", "forecast_mode", "market_favorite")
    scopes = [("all_history", rows)] + [(name, group) for name, group in rows.groupby("period")]
    rng = np.random.default_rng(SEED + 707)
    for scope, scoped in scopes:
        for lead_days in (1, 2):
            lead = scoped[scoped["lead_days"].eq(lead_days)]
            for horizon in base.HORIZONS_MIN:
                pnl = f"h{horizon}_net_pnl_usd"
                shares = f"h{horizon}_executable_shares"
                for control in controls:
                    primary = lead[lead["policy"].eq("forecast_innovation_argmax")][
                        ["snapshot_id", "target_date", "entry_ask", "entry_fee_per_share", shares, pnl]
                    ].dropna()
                    comparator = lead[(lead["policy"].eq(control)) & lead["policy_eligible"]][
                        ["snapshot_id", "entry_ask", "entry_fee_per_share", shares, pnl]
                    ].dropna()
                    paired = primary.merge(comparator, on="snapshot_id", suffixes=("_primary", "_control"))
                    if paired.empty:
                        continue
                    paired["cost_primary"] = (
                        (paired["entry_ask_primary"] + paired["entry_fee_per_share_primary"])
                        * paired[f"{shares}_primary"]
                    )
                    paired["cost_control"] = (
                        (paired["entry_ask_control"] + paired["entry_fee_per_share_control"])
                        * paired[f"{shares}_control"]
                    )
                    by_date = paired.groupby("target_date", as_index=False).agg(
                        pnl_primary=(f"{pnl}_primary", "sum"),
                        cost_primary=("cost_primary", "sum"),
                        pnl_control=(f"{pnl}_control", "sum"),
                        cost_control=("cost_control", "sum"),
                    )
                    point = (
                        by_date["pnl_primary"].sum() / by_date["cost_primary"].sum()
                        - by_date["pnl_control"].sum() / by_date["cost_control"].sum()
                    )
                    boot: list[float] = []
                    values = by_date[["pnl_primary", "cost_primary", "pnl_control", "cost_control"]].to_numpy(float)
                    if len(values) >= 3:
                        for _ in range(draws):
                            sample = values[rng.integers(0, len(values), size=len(values))]
                            if sample[:, 1].sum() and sample[:, 3].sum():
                                boot.append(float(sample[:, 0].sum() / sample[:, 1].sum() - sample[:, 2].sum() / sample[:, 3].sum()))
                    output.append(
                        {
                            "period": scope,
                            "lead_days": lead_days,
                            "horizon_min": horizon,
                            "control": control,
                            "paired_events": len(paired),
                            "target_dates": len(by_date),
                            "roi_delta_primary_minus_control": float(point),
                            "ci_low": float(np.percentile(boot, 2.5)) if boot else math.nan,
                            "ci_high": float(np.percentile(boot, 97.5)) if boot else math.nan,
                        }
                    )
    return pd.DataFrame(output)


def execution_diagnostics(rows: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    selected = rows[
        rows["policy"].eq("forecast_innovation_argmax") & rows["lead_days"].eq(1)
    ].copy()
    development = selected[selected["period"].eq("development")]
    holdout = selected[selected["period"].eq("late_holdout")].copy()
    _, edges = pd.qcut(
        development["forecast_innovation_score"], 10, retbins=True, duplicates="drop"
    )
    edges[0], edges[-1] = -np.inf, np.inf
    holdout["innovation_decile"] = (
        pd.cut(holdout["forecast_innovation_score"], edges, labels=False, include_lowest=True) + 1
    )
    rows_out: list[dict[str, Any]] = []
    for decile, group in holdout.groupby("innovation_decile", observed=True):
        for horizon in (30, 60, 120):
            usable = group[group[f"h{horizon}_bid"].notna()].copy()
            shares = usable[f"h{horizon}_executable_shares"]
            bid_cost = usable["entry_bid"] * shares
            no_fee = (usable[f"h{horizon}_bid"] - usable["entry_bid"]) * shares
            exit_fee = (
                usable[f"h{horizon}_bid"]
                - usable[f"h{horizon}_exit_fee_per_share"]
                - usable["entry_bid"]
            ) * shares
            rows_out.append(
                {
                    "innovation_decile": int(decile),
                    "horizon_min": horizon,
                    "signals": len(group),
                    "covered": len(usable),
                    "score_min": float(group["forecast_innovation_score"].min()),
                    "score_median": float(group["forecast_innovation_score"].median()),
                    "score_max": float(group["forecast_innovation_score"].max()),
                    "median_spread": float((group["entry_ask"] - group["entry_bid"]).median()),
                    "bid_above_entry_ask_rate": float((usable[f"h{horizon}_bid"] > usable["entry_ask"]).mean()),
                    "conditional_maker_bid_to_bid_roi_no_fee": float(no_fee.sum() / bid_cost.sum()),
                    "conditional_maker_entry_exit_taker_roi": float(exit_fee.sum() / bid_cost.sum()),
                }
            )
    deciles = pd.DataFrame(rows_out)
    usable = holdout[holdout["h60_bid"].notna()].copy()
    shares = usable["h60_executable_shares"]
    ask_cost = usable["entry_ask"] * shares
    bid_cost = usable["entry_bid"] * shares
    top = deciles[(deciles["innovation_decile"].eq(deciles["innovation_decile"].max())) & deciles["horizon_min"].eq(60)].iloc[0]
    diagnostics = {
        "d1_late_holdout_median_spread": float((holdout["entry_ask"] - holdout["entry_bid"]).median()),
        "d1_late_holdout_h60_bid_above_entry_ask_rate": float((usable["h60_bid"] > usable["entry_ask"]).mean()),
        "d1_late_holdout_h60_ask_to_bid_roi_no_fee": float(((usable["h60_bid"] - usable["entry_ask"]) * shares).sum() / ask_cost.sum()),
        "d1_late_holdout_h60_bid_to_bid_roi_no_fee_assuming_fill": float(((usable["h60_bid"] - usable["entry_bid"]) * shares).sum() / bid_cost.sum()),
        "top_decile_score_min": float(top["score_min"]),
        "top_decile_h60_covered": int(top["covered"]),
        "top_decile_h60_bid_to_bid_roi_no_fee_assuming_fill": float(top["conditional_maker_bid_to_bid_roi_no_fee"]),
        "top_decile_h60_entry_maker_exit_taker_roi_assuming_fill": float(top["conditional_maker_entry_exit_taker_roi"]),
    }
    return diagnostics, deciles


def pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NA"
    return "NA" if not math.isfinite(number) else f"{number * 100:+.2f}%"


def write_report(
    path: Path,
    *,
    generated_at: str,
    file_count: int,
    counts: Counter[str],
    cutoff: str | None,
    probabilities: pd.DataFrame,
    summary: pd.DataFrame,
    paired: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> None:
    probability = base.probability_summary(probabilities)
    focus = summary[
        summary["policy"].eq("forecast_innovation_argmax")
        & summary["horizon_min"].isin([20, 30, 60, 120])
    ].copy()
    if focus.empty:
        focus = summary[
            summary["policy"].eq("forecast_innovation_argmax")
            & summary["horizon_min"].isin([30, 60, 120])
        ].copy()
    rows = [
        f"| {row.period} | D-{row.lead_days} | {row.horizon_min}m | {row.covered}/{row.signals} | {pct(row.turnover_roi)} | [{pct(row.ci_low)}, {pct(row.ci_high)}] |"
        for row in focus.itertuples()
    ]
    delta_focus = paired[
        paired["period"].eq("late_holdout")
        & paired["horizon_min"].isin([30, 60, 120])
    ]
    delta_rows = [
        f"| D-{row.lead_days} | {row.horizon_min}m | {row.control} | {row.paired_events} | {pct(row.roi_delta_primary_minus_control)} | [{pct(row.ci_low)}, {pct(row.ci_high)}] |"
        for row in delta_focus.itertuples()
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# LMVM forecast innovation 历史回测 v2

> generated_at_utc: `{generated_at}`  
> research-only；没有 production instance、plan、order 或 fill 写入。

## 固定问题

每次 D-2/D-1 forecast first-seen 改版，在完整 exact-bracket ladder 中只买一档 YES：
`argmax[(ΔPmodel)−(ΔPmarket)]`。同 snapshot ask 入场，固定 horizon 的首个可用 bid 退出，
entry/exit 都扣官方 Weather taker fee。静态 residual、forecast mode、market favorite 是同 rows 对照。

- snapshot files: {file_count:,}
- forecast update events: {counts['forecast_update_events']:,}
- complete paired ladders: {counts['paired_update_events']:,}
- blocked unpaired ladders: {counts['blocked_unpaired_ladder']:,}
- chronological cutoff: `{cutoff}`（前 2/3 development，后 1/3 late holdout；不是事前盲测）

## 旧 forecast probability 本身

| metric | model | same-row market | model-market | target-date 95% CI |
|---|---:|---:|---:|---:|
| Brier | {probability.get('model_brier', math.nan):.6f} | {probability.get('market_brier', math.nan):.6f} | {probability.get('brier_delta_model_minus_market', math.nan):+.6f} | [{probability.get('brier_delta_ci_low', math.nan):+.6f}, {probability.get('brier_delta_ci_high', math.nan):+.6f}] |
| logloss | {probability.get('model_logloss', math.nan):.6f} | {probability.get('market_logloss', math.nan):.6f} | {probability.get('logloss_delta_model_minus_market', math.nan):+.6f} | [{probability.get('logloss_delta_ci_low', math.nan):+.6f}, {probability.get('logloss_delta_ci_high', math.nan):+.6f}] |

## `ΔPmodel−ΔPmarket` 可执行 markout

| period | lead | horizon | covered/signals | turnover ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

## late holdout 相对同 rows 对照

| lead | horizon | control | paired events | ROI delta | target-date 95% CI |
|---|---:|---|---:|---:|---:|
{chr(10).join(delta_rows)}

## 边界

- 该 archive 足够回测 forecast innovation 与 taker ask→future bid repricing。
- 它不含我们自己的真实 maker queue position；`bid touched` 不能当 maker fill，因此 maker 成交率仍需独立 forward paper/quote ledger。
- `model_prob` 是 capture-time telemetry，能做 PIT replay；但旧概率体系的某些历史研究是 market-anchored bucket model，不能与这里的 raw full-ladder telemetry 混称同一个模型。

## maker 条件诊断

- D-1 late holdout median spread：{diagnostics['d1_late_holdout_median_spread']:.3f}；60m future bid 高于 entry ask：{diagnostics['d1_late_holdout_h60_bid_above_entry_ask_rate']:.2%}。
- ask→future bid 不计 fee：{diagnostics['d1_late_holdout_h60_ask_to_bid_roi_no_fee']:.2%}。
- 假设每笔都在 entry best bid maker fill，bid→future bid 不计 fee：{diagnostics['d1_late_holdout_h60_bid_to_bid_roi_no_fee_assuming_fill']:.2%}。
- development 冻结的最高 innovation decile（score>={diagnostics['top_decile_score_min']:.6f}）late holdout covered={diagnostics['top_decile_h60_covered']}：无 fee bid→bid {diagnostics['top_decile_h60_bid_to_bid_roi_no_fee_assuming_fill']:.2%}；entry maker + exit taker fee {diagnostics['top_decile_h60_entry_maker_exit_taker_roi_assuming_fill']:.2%}。

这些只是 conditional quote return；没有 queue/fill evidence 就不是可实现 maker ROI。D-2 late holdout 为零，不能外推。
""",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    inputs = discover_snapshot_inputs(
        args.snapshot_dir,
        args.current_snapshot_dir,
        args.historical_full_ladder_root,
        args.historical_targeted_root,
        legacy_only=args.legacy_snapshot_only,
    )
    if args.max_files is not None:
        inputs = inputs[: args.max_files]
    if not inputs:
        raise FileNotFoundError(f"no snapshots under {args.snapshot_dir}")
    states: list[dict[str, Any]] = []
    parse_counts: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for parsed, local_counts in executor.map(parse_repricing_snapshot, inputs, chunksize=8):
            states.extend(parsed)
            parse_counts.update(local_counts)
    if args.end_target_date:
        before = len(states)
        states = [row for row in states if str(row.get("target_date") or "") <= args.end_target_date]
        parse_counts["states_after_end_target_date"] = len(states)
        parse_counts["states_beyond_end_target_date_blocked"] = before - len(states)

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    evidence_rank = {
        "legacy_inline_snapshot_v1": 0,
        "historical_companion_orderbook_same_capture_v1": 1,
        "collector_exact_joined_full_ladder_v1": 2,
    }
    for row in states:
        key = (
            row.get("city"),
            row.get("target_date"),
            row.get("event_slug"),
            row.get("forecast_source"),
            row.get("forecast_model"),
            round(float(row.get("snapshot_epoch") or 0.0)),
        )
        previous = deduped.get(key)
        if previous is None or evidence_rank.get(str(row.get("clock_lineage_status")), -1) > evidence_rank.get(
            str(previous.get("clock_lineage_status")), -1
        ):
            deduped[key] = row
    parse_counts["duplicate_states_removed"] = len(states) - len(deduped)
    states = list(deduped.values())

    annotated = base.annotate_forecast_updates(states)
    static_candidates, probability_rows = base.build_candidates(annotated)
    pairs, update_counts = forecast_update_pairs(states)
    innovation = build_innovation_candidates(pairs)
    full_ladder = build_full_ladder_panel(pairs)
    candidates = pd.concat([static_candidates, innovation], ignore_index=True, sort=False)
    quote_history = base.quote_history(states)
    candidates = base.attach_markouts(candidates, quote_history)
    full_ladder = base.attach_markouts(full_ladder, quote_history)
    candidates, cutoff = assign_period(candidates)
    probabilities = base.score_probabilities(probability_rows, base.load_winners(args.db))
    summary = markout_summary(candidates, args.draws)
    paired = paired_policy_deltas(candidates, args.draws)
    diagnostics, deciles = execution_diagnostics(candidates)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(args.output_dir / "candidate_markouts.csv", index=False)
    full_ladder.to_csv(args.output_dir / "forecast_event_rungs.csv", index=False)
    probabilities.to_csv(args.output_dir / "probability_rows.csv", index=False)
    summary.to_csv(args.output_dir / "markout_summary.csv", index=False)
    paired.to_csv(args.output_dir / "paired_policy_deltas.csv", index=False)
    deciles.to_csv(args.output_dir / "innovation_score_deciles.csv", index=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at_utc": generated_at,
        "target_metric": "lmvm_forecast_innovation_v2",
        "snapshot_files": len(inputs),
        "end_target_date": args.end_target_date,
        "input_contract": {
            "legacy_snapshot_root": str(args.snapshot_dir),
            "current_snapshot_root": str(args.current_snapshot_dir),
            "historical_full_ladder_root": str(args.historical_full_ladder_root),
            "historical_targeted_root": str(args.historical_targeted_root),
            "legacy_snapshot_only": bool(args.legacy_snapshot_only),
        },
        "state_target_date_min": min((str(row["target_date"]) for row in states), default=None),
        "state_target_date_max": max((str(row["target_date"]) for row in states), default=None),
        "state_clock_lineage": dict(Counter(str(row.get("clock_lineage_status")) for row in states)),
        "parse_counts": dict(parse_counts),
        "update_counts": dict(update_counts),
        "full_ladder_event_rows": len(full_ladder),
        "full_ladder_events": int(full_ladder["forecast_event_id"].nunique()) if not full_ladder.empty else 0,
        "full_ladder_selected_rows": int(full_ladder["selected_by_innovation"].sum()) if not full_ladder.empty else 0,
        "chronological_cutoff": cutoff,
        "probability": base.probability_summary(probabilities),
        "markout_summary": summary.to_dict("records"),
        "paired_policy_deltas": paired.to_dict("records"),
        "execution_diagnostics": diagnostics,
        "probability_input": "legacy_paper_snapshot_compute_bracket_probs",
        "model_input_verdict": "invalid_for_mature_probability_strategy_evaluation",
        "status": "invalid_model_input_baseline",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        generated_at=generated_at,
        file_count=len(inputs),
        counts=update_counts,
        cutoff=cutoff,
        probabilities=probabilities,
        summary=summary,
        paired=paired,
        diagnostics=diagnostics,
    )
    print(json.dumps({"report": str(args.report), "output_dir": str(args.output_dir), **payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
