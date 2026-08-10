#!/usr/bin/env python3
"""Join frozen Tokyo JMA event probabilities to archived exact-bracket books.

The join uses collector-exact first-seen timestamps when a hash-verified row
exists.  Older archive-only JMA rows receive a conservative +15 minute
availability clock and remain explicitly classified as reconstructed, never
as exact first-seen.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_full_ladder_root  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


DEFAULT_PREDICTIONS = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v2"
    / "predictions.csv.gz"
)
DEFAULT_EXACT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_feature_timestamp_audit_v1"
    / "tokyo_jma_exact_enriched.csv"
)
DEFAULT_BOOKS = historical_full_ladder_root() / "orderbook_snapshots"
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_TRIGGER_EVENTS = load_production_spec().data_feed_output_root() / "fast_source_prev_no_trial/events.jsonl"
DEFAULT_CONFIRM_PREDICTIONS = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v1"
    / "predictions.csv.gz"
)
DEFAULT_PM_HISTORY = (
    ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
)
DEFAULT_HISTORY_PRICE_PROXY = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_clob_price_history_coverage_v8"
    / "historical_checkpoint_price_proxy.csv.gz"
)
DEFAULT_CONTINUOUS_FEATURES = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_continuous_ladder_probability_v1"
    / "continuous_feature_rows.csv.gz"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_market_v1"
)
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
FEE_RATE = 0.05
SHARES = 5.0
EPS = 1e-8


def parse_ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def finite(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def read_csv(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def exact_first_seen(path: Path) -> dict[str, datetime]:
    mapping: dict[str, datetime] = {}
    for row in read_csv(path):
        if str(row.get("hash_verified_against_jma_point_archive")) != "1":
            continue
        observed = parse_ts(row["observation_time_utc"]).isoformat()
        available = parse_ts(row["source_first_seen_at_utc"])
        current = mapping.get(observed)
        if current is None or available < current:
            mapping[observed] = available
    return mapping


def load_books(
    root: Path, requests: list[tuple[str, datetime]]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by_snapshot: dict[
        tuple[str, str, datetime], dict[str, dict[str, Any]]
    ] = defaultdict(dict)
    selected_paths: set[Path] = set()
    by_day: dict[str, list[datetime]] = defaultdict(list)
    for day, available in requests:
        by_day[day].append(available)
    for day, available_times in by_day.items():
        candidates: list[tuple[datetime, Path]] = []
        for path in (root / day).glob("orderbook_snapshot_*.jsonl.gz"):
            try:
                stamp = datetime.strptime(
                    path.name.removeprefix("orderbook_snapshot_").removesuffix(
                        ".jsonl.gz"
                    ),
                    "%Y%m%d_%H%M",
                ).replace(tzinfo=BEIJING).astimezone(UTC)
            except ValueError:
                continue
            candidates.append((stamp, path))
        candidates.sort()
        stamps = [stamp for stamp, _ in candidates]
        for available in available_times:
            index = bisect_left(stamps, available)
            if index < len(candidates) and stamps[index] <= available + timedelta(
                minutes=45
            ):
                selected_paths.add(candidates[index][1])
    for path in sorted(selected_paths):
        day = path.parent.name
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("city") != "Tokyo"
                    or str(row.get("event_date")) != day
                    or row.get("status") != "ok"
                ):
                    continue
                bracket = str(row.get("bracket"))
                observed = parse_ts(row["snapshot_ts_utc"])
                by_snapshot[(day, bracket, observed)][
                    str(row.get("outcome"))
                ] = row
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (day, bracket, observed), sides in by_snapshot.items():
        yes = sides.get("yes", {}).get("summary", {})
        no = sides.get("no", {}).get("summary", {})
        yes_ask = finite(yes.get("best_ask"))
        yes_bid = finite(yes.get("best_bid"))
        no_ask = finite(no.get("best_ask"))
        no_bid = finite(no.get("best_bid"))
        if yes_ask is None and no_bid is not None:
            yes_ask = 1 - no_bid
        if yes_bid is None and no_ask is not None:
            yes_bid = 1 - no_ask
        if no_ask is None and yes_bid is not None:
            no_ask = 1 - yes_bid
        if no_bid is None and yes_ask is not None:
            no_bid = 1 - yes_ask
        no_mid = (
            (no_bid + no_ask) / 2
            if no_bid is not None and no_ask is not None
            else no_ask
            if no_ask is not None
            else no_bid
        )
        no_size = finite(no.get("ask_size"))
        if no_size is None:
            no_size = finite(yes.get("bid_size"))
        output[(day, bracket)].append(
            {
                "snapshot_ts_utc": observed,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_mid": no_mid,
                "no_ask_size": no_size,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
            }
        )
    for rows in output.values():
        rows.sort(key=lambda row: row["snapshot_ts_utc"])
    return output


def load_winners(db_path: Path, start_date: str, end_date: str) -> dict[str, str]:
    connection = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2.0
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        rows = connection.execute(
            """
            SELECT target_date, bracket
            FROM settlement_outcomes
            WHERE target_date >= ? AND target_date <= ?
              AND city = 'Tokyo'
              AND settlement_status = 'settled'
              AND final_price = 1.0
            ORDER BY target_date
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        connection.close()
    return {str(target_date): str(bracket) for target_date, bracket in rows}


def quote_after(
    rows: list[dict[str, Any]], available: datetime
) -> dict[str, Any] | None:
    timestamps = [row["snapshot_ts_utc"] for row in rows]
    index = bisect_left(timestamps, available)
    if index >= len(rows):
        return None
    quote = rows[index]
    if quote["snapshot_ts_utc"] > available + timedelta(minutes=45):
        return None
    return quote


def date_bootstrap_brier_delta(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    daily: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        y = int(row["settlement_no_wins"])
        daily[str(row["target_date"])].append(
            (float(row["p_model_no"]) - y) ** 2
            - (float(row["p_market_no"]) - y) ** 2
        )
    values = np.asarray([np.mean(group) for group in daily.values()])
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(np.mean(rng.choice(values, len(values), replace=True)))
            for _ in range(5000)
        ]
    )
    return (
        float(np.mean(values)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def date_equal_brier(rows: list[dict[str, Any]], field: str) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        y = int(row["settlement_no_wins"])
        daily[str(row["target_date"])].append((float(row[field]) - y) ** 2)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def roi_bootstrap(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not rows:
        return math.nan, math.nan, math.nan
    daily_accumulator: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        values = daily_accumulator[str(row["target_date"])]
        values[0] += float(row["fee_adjusted_pnl_usd"])
        values[1] += float(row["entry_cost_usd"])
    daily = {
        target_date: (values[0], values[1])
        for target_date, values in daily_accumulator.items()
    }
    dates = list(daily)
    pnl = sum(value[0] for value in daily.values())
    cost = sum(value[1] for value in daily.values())
    rng = np.random.default_rng(20260731)
    draws = []
    for _ in range(5000):
        selected = rng.choice(dates, len(dates), replace=True)
        draw_pnl = sum(daily[str(day)][0] for day in selected)
        draw_cost = sum(daily[str(day)][1] for day in selected)
        draws.append(draw_pnl / draw_cost if draw_cost else math.nan)
    return (
        pnl / cost if cost else math.nan,
        float(np.nanquantile(draws, 0.025)),
        float(np.nanquantile(draws, 0.975)),
    )


def load_tokyo_winners_from_pm_history(
    root: Path, target_dates: set[str]
) -> dict[str, str]:
    """Load only explicitly requested Tokyo day files; never scan pm_history."""
    output: dict[str, str] = {}
    for target_date in sorted(target_dates):
        path = root / f"Tokyo_{target_date}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        winners = [
            str(row.get("label"))
            for row in payload.get("brackets", [])
            if finite(row.get("final_price")) is not None
            and float(row["final_price"]) >= 0.99
        ]
        if len(winners) == 1:
            output[target_date] = winners[0]
    return output


def load_confirmation_predictions(path: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in read_csv(path):
        if (
            row.get("target_id")
            != "next_routine_metar_confirms_jma_lattice_30m"
            or row.get("model_id") != "jma_metar_hgb_v1"
        ):
            continue
        output[parse_ts(row["decision_ts_utc"]).isoformat()] = row
    return output


def load_final_settlement_predictions(
    path: Path, model_id: str = "event_safe_selector_v2"
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Load the weather-only final-break head at its original event grain."""
    output: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in read_csv(path):
        if row.get("target_id") != "final_break" or row.get("model_id") != model_id:
            continue
        key = (
            str(row["target_date"]),
            parse_ts(row["decision_ts_utc"]).isoformat(),
            str(int(float(row["prior_bracket"]))),
        )
        output[key] = row
    return output


def first_margin_events(
    rows: list[dict[str, Any]], margin: float
) -> list[dict[str, Any]]:
    """First qualifying event per target-date/exact-bracket policy state."""
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: parse_ts(item["ts_utc"])):
        if float(row["actual_source_margin_c"]) < margin - EPS:
            continue
        selected.setdefault(
            (str(row["target_date"]), str(row["market_bracket"])), row
        )
    return list(selected.values())


def phase_policy_comparison(
    current_rows: list[dict[str, Any]],
    prior_join_paths: list[Path],
    *,
    target_shares: float,
    min_shares: float,
    max_ask: float,
    consensus_min_ask: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate fixed T-13/T-3 source-event policies across append-only runs.

    The phase is defined by the observation clock, not by outcome or price.
    Prior artifacts extend the date denominator without rewriting their raw
    evidence; current rows take precedence if the same event was replayed.
    """
    combined: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in prior_join_paths:
        for row in read_csv(path):
            key = (
                str(row.get("target_date")),
                str(row.get("market_bracket")),
                str(row.get("ts_utc")),
            )
            combined.setdefault(key, dict(row))
    for row in current_rows:
        key = (
            str(row.get("target_date")),
            str(row.get("market_bracket")),
            str(row.get("ts_utc")),
        )
        combined[key] = dict(row)

    eligible: list[dict[str, Any]] = []
    for row in combined.values():
        margin = finite(row.get("actual_source_margin_c"))
        if margin is None or margin < 0.7 - EPS:
            continue
        if row.get("scheduled_phase") not in {"T13", "T3"}:
            continue
        record = dict(row)
        record.update(
            {
                "replay_target_shares": target_shares,
                "replay_min_shares": min_shares,
                "replay_max_ask": max_ask,
            }
        )
        eligible.append(record)

    first_rows = first_margin_events(eligible, 0.7)
    summaries: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for phase in ("T13", "T3"):
        phase_rows = [row for row in first_rows if row["scheduled_phase"] == phase]
        settled = [
            row for row in phase_rows
            if finite(row.get("settlement_no_wins")) is not None
        ]
        summary, selected = policy_summary(
            settled, f"first_margin_ge_0p7_{phase.lower()}", lambda row: True
        )
        summary.update(
            {
                "phase": phase,
                "signal_events": len(phase_rows),
                "settled_signal_events": len(settled),
                "settled_signal_wins": sum(
                    int(float(row["settlement_no_wins"])) for row in settled
                ),
                "source_join_artifacts": len(prior_join_paths) + 1,
            }
        )
        summaries.append(summary)
        trades.extend(selected)
    settled_all = [
        row for row in first_rows if finite(row.get("settlement_no_wins")) is not None
    ]
    consensus_rows = [
        row
        for row in settled_all
        if finite(row.get("best_ask")) is not None
        and float(row["best_ask"]) >= consensus_min_ask
    ]
    consensus_summary, consensus_trades = policy_summary(
        consensus_rows,
        "first_margin_ge_0p7_market_consensus",
        lambda row: True,
    )
    consensus_summary.update(
        {
            "phase": "ALL",
            "mechanism": (
                "JMA .7C first-seen trigger plus post-event previous-NO ask "
                "market consensus; no weather residual threshold"
            ),
            "consensus_min_no_ask": consensus_min_ask,
            "signal_events": len(first_rows),
            "settled_signal_events": len(settled_all),
            "settled_signal_wins": sum(
                int(float(row["settlement_no_wins"])) for row in settled_all
            ),
            "source_join_artifacts": len(prior_join_paths) + 1,
        }
    )
    summaries.append(consensus_summary)
    trades.extend(consensus_trades)
    return summaries, trades


def attach_trigger_evidence(
    rows: list[dict[str, Any]],
    *,
    winners: dict[str, str],
    confirmation_predictions: dict[str, dict[str, Any]],
    final_predictions: dict[tuple[str, str, str], dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        decision_ts = parse_ts(row["source_obs_ts_utc"]).isoformat()
        confirmation = confirmation_predictions.get(decision_ts)
        final_prediction = final_predictions.get(
            (
                str(row["target_date"]),
                decision_ts,
                str(int(float(row["market_bracket"]))),
            )
        )
        winner = winners.get(str(row["target_date"]))
        bid = finite(row.get("best_bid"))
        ask = finite(row.get("best_ask"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        record.update(
            {
                "winning_bracket": winner,
                "settlement_no_wins": (
                    int(str(winner) != str(row["market_bracket"]))
                    if winner is not None
                    else None
                ),
                "p_market_no": mid,
                "p_confirm_next_metar": (
                    finite(confirmation.get("p_model")) if confirmation else None
                ),
                "next_metar_confirmation_label": (
                    int(confirmation["label"]) if confirmation else None
                ),
                "p_final_settlement_no": (
                    finite(final_prediction.get("p_model"))
                    if final_prediction
                    else None
                ),
                "final_model_split": (
                    str(final_prediction.get("split")) if final_prediction else None
                ),
                "final_model_pit_provenance": (
                    str(final_prediction.get("pit_provenance"))
                    if final_prediction
                    else None
                ),
                "replay_target_shares": args.target_shares,
                "replay_min_shares": args.min_shares,
                "replay_max_ask": args.max_ask,
            }
        )
        output.append(record)
    return output


def source_phase(row: dict[str, Any]) -> tuple[str | None, float | None]:
    observed = parse_ts(row["source_obs_ts_utc"])
    minute = observed.minute
    if minute in {10, 40}:
        return "T13", 13.0
    if minute in {20, 50}:
        return "T3", 3.0
    return None, None


def scheduled_report_ts(row: dict[str, Any]) -> str | None:
    explicit = row.get("next_expected_metar_report_ts_utc")
    if explicit:
        return parse_ts(explicit).isoformat()
    observed = parse_ts(row["source_obs_ts_utc"])
    if observed.minute in {10, 20}:
        report = observed.replace(minute=30, second=0, microsecond=0)
    elif observed.minute in {40, 50}:
        report = observed.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        return None
    return report.isoformat()


def load_trigger_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("city") != "Tokyo" or row.get("status") != "cross_candidate":
                continue
            basis = finite(
                row.get("source_cross_confirmation_basis_c")
                if row.get("source_cross_confirmation_basis_c") is not None
                else row.get("t_minus_1_no_bracket_c")
            )
            source_temp = finite(row.get("source_temp_c"))
            bracket = row.get("t_minus_1_no_market_bracket")
            if bracket is None:
                bracket = row.get("t_minus_1_no_bracket_c")
            if basis is None or source_temp is None or bracket is None:
                continue
            phase, nominal_lead = source_phase(row)
            record = dict(row)
            record.update(
                {
                    "market_bracket": str(bracket),
                    "actual_source_margin_c": source_temp - basis,
                    "scheduled_phase": phase,
                    "nominal_lead_min": nominal_lead,
                    "scheduled_report_ts_utc": scheduled_report_ts(row),
                }
            )
            rows.append(record)
    return rows


def max_drawdown(trades: list[dict[str, Any]]) -> float:
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for row in sorted(trades, key=lambda item: parse_ts(item["ts_utc"])):
        cumulative += float(row["fee_adjusted_pnl_usd"])
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return drawdown


def wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    rate = wins / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return center - half, center + half


def policy_summary(
    rows: list[dict[str, Any]], name: str, selected: Callable[[dict[str, Any]], bool]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    for row in rows:
        if not selected(row):
            continue
        ask = finite(row.get("best_ask"))
        ask_size = finite(row.get("ask_size"))
        if (
            ask is None
            or ask_size is None
            or ask > float(row["replay_max_ask"])
            or ask_size < float(row["replay_min_shares"])
        ):
            continue
        shares = min(float(row["replay_target_shares"]), ask_size)
        fee_per_share = FEE_RATE * ask * (1 - ask)
        cost = shares * (ask + fee_per_share)
        payout = shares * int(row["settlement_no_wins"])
        trade = dict(row)
        trade.update(
            {
                "policy": name,
                "shares": shares,
                "entry_cost_usd": cost,
                "fees_usd": shares * fee_per_share,
                "fee_adjusted_pnl_usd": payout - cost,
                "trade_result": "correct" if payout else "wrong",
            }
        )
        trades.append(trade)
    pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in trades)
    cost = sum(float(row["entry_cost_usd"]) for row in trades)
    roi, low, high = roi_bootstrap(trades)
    wins = sum(int(row["trade_result"] == "correct") for row in trades)
    win_low, win_high = wilson_interval(wins, len(trades))
    total_shares = sum(float(row["shares"]) for row in trades)
    summary = {
        "policy": name,
        "trades": len(trades),
        "target_dates": len({str(row["target_date"]) for row in trades}),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_wilson_95_ci": [win_low, win_high],
        "shares": total_shares,
        "entry_cost_usd": cost,
        "fees_usd": sum(float(row["fees_usd"]) for row in trades),
        "fee_adjusted_pnl_usd": pnl,
        "fee_adjusted_roi": pnl / cost if cost else None,
        "date_bootstrap_roi_ci": [low, high],
        "pnl_per_trade_usd": pnl / len(trades) if trades else None,
        "average_ask": (
            sum(float(row["best_ask"]) * float(row["shares"]) for row in trades)
            / sum(float(row["shares"]) for row in trades)
            if trades
            else None
        ),
        "average_fee_adjusted_breakeven_probability": (
            cost / total_shares if total_shares else None
        ),
        "max_drawdown_usd": max_drawdown(trades),
    }
    return summary, trades


def calibration_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.000001))
    output: list[dict[str, Any]] = []
    for lower, upper in bins:
        selected = [
            row
            for row in rows
            if finite(row.get("p_market_no")) is not None
            and lower <= float(row["p_market_no"]) < upper
        ]
        if not selected:
            continue
        weights = np.asarray([1.0 / sum(str(other["target_date"]) == str(row["target_date"]) for other in selected) for row in selected])
        probabilities = np.asarray([float(row["p_market_no"]) for row in selected])
        outcomes = np.asarray([int(row["settlement_no_wins"]) for row in selected])
        asks = np.asarray([float(row["best_ask"]) for row in selected])
        breakeven = asks + FEE_RATE * asks * (1 - asks)
        output.append(
            {
                "bin": f"{lower:.1f}-{min(upper, 1.0):.1f}",
                "events": len(selected),
                "target_dates": len({str(row["target_date"]) for row in selected}),
                "mean_market_mid": float(np.average(probabilities, weights=weights)),
                "observed_no_rate": float(np.average(outcomes, weights=weights)),
                "calibration_gap_observed_minus_mid": float(np.average(outcomes - probabilities, weights=weights)),
                "mean_taker_breakeven": float(np.average(breakeven, weights=weights)),
                "observed_minus_taker_breakeven": float(np.average(outcomes - breakeven, weights=weights)),
            }
        )
    return output


def broad_market_calibration(
    price_proxy_path: Path, feature_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    labels: dict[tuple[str, str, str], int] = {}
    for row in read_csv(feature_path):
        labels[(
            str(row["target_date"]),
            parse_ts(row["decision_ts_utc"]).isoformat(),
            str(row["current_bracket"]),
        )] = int(str(row["final_bracket"]) == str(row["current_bracket"]))
    rows: list[dict[str, Any]] = []
    for row in read_csv(price_proxy_path):
        probability = finite(row.get("before_price"))
        delay = finite(row.get("before_delay_min"))
        key = (
            str(row["target_date"]),
            parse_ts(row["decision_ts_utc"]).isoformat(),
            str(row["current_bracket"]),
        )
        if probability is None or delay is None or abs(delay) > 15 or key not in labels:
            continue
        rows.append(
            {
                "target_date": key[0],
                "market_probability": probability,
                "outcome": labels[key],
            }
        )
    bins = [
        ("45-55 focused", 0.45, 0.55),
        ("75-85 focused", 0.75, 0.85),
        *[(f"{index * 10}-{(index + 1) * 10}", index / 10, (index + 1) / 10 + EPS) for index in range(10)],
    ]
    output: list[dict[str, Any]] = []
    rng = np.random.default_rng(20260805)
    for name, lower, upper in bins:
        selected = [
            row for row in rows if lower <= float(row["market_probability"]) < upper
        ]
        if not selected:
            continue
        daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            daily[str(row["target_date"])].append(row)
        daily_p = np.asarray(
            [np.mean([float(row["market_probability"]) for row in group]) for group in daily.values()]
        )
        daily_y = np.asarray(
            [np.mean([int(row["outcome"]) for row in group]) for group in daily.values()]
        )
        daily_gap = daily_y - daily_p
        draws = np.asarray(
            [
                float(np.mean(rng.choice(daily_gap, len(daily_gap), replace=True)))
                for _ in range(5000)
            ]
        )
        output.append(
            {
                "bin": name,
                "events": len(selected),
                "target_dates": len(daily),
                "date_equal_mean_market_probability": float(np.mean(daily_p)),
                "date_equal_observed_rate": float(np.mean(daily_y)),
                "date_equal_calibration_gap": float(np.mean(daily_gap)),
                "date_bootstrap_gap_ci_low": float(np.quantile(draws, 0.025)),
                "date_bootstrap_gap_ci_high": float(np.quantile(draws, 0.975)),
                "row_weighted_mean_market_probability": float(
                    np.mean([float(row["market_probability"]) for row in selected])
                ),
                "row_weighted_observed_rate": float(
                    np.mean([int(row["outcome"]) for row in selected])
                ),
            }
        )
    daily_brier: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        daily_brier[str(row["target_date"])].append(
            (float(row["market_probability"]) - int(row["outcome"])) ** 2
        )
    summary = {
        "rows": len(rows),
        "target_dates": len(daily_brier),
        "date_equal_brier": float(
            np.mean([np.mean(values) for values in daily_brier.values()])
        ),
        "price_semantics": "CLOB prices-history midpoint-like proxy immediately before observation+15m assumed availability",
        "execution_semantics": "not an orderbook: no ask, spread, depth, or taker VWAP",
    }
    return output, summary


def paired_policy_delta(
    baseline: list[dict[str, Any]],
    challenger: list[dict[str, Any]],
    denominator_dates: list[str] | None = None,
) -> dict[str, Any]:
    baseline_daily: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    challenger_daily: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in baseline:
        values = baseline_daily[str(row["target_date"])]
        values[0] += float(row["fee_adjusted_pnl_usd"])
        values[1] += float(row["entry_cost_usd"])
    for row in challenger:
        values = challenger_daily[str(row["target_date"])]
        values[0] += float(row["fee_adjusted_pnl_usd"])
        values[1] += float(row["entry_cost_usd"])
    dates = sorted(
        set(denominator_dates or []) | set(baseline_daily) | set(challenger_daily)
    )
    if not dates:
        return {
            "denominator_dates": 0,
            "fee_adjusted_pnl_delta_usd": 0.0,
            "pnl_delta_date_bootstrap_ci": [0.0, 0.0],
            "fee_adjusted_roi_delta": None,
            "roi_delta_date_bootstrap_ci": [None, None],
        }
    rng = np.random.default_rng(20260805)
    pnl_draws: list[float] = []
    roi_draws: list[float] = []
    for _ in range(5000):
        selected = rng.choice(dates, len(dates), replace=True)
        baseline_pnl = baseline_cost = challenger_pnl = challenger_cost = 0.0
        for target_date in selected:
            baseline_values = baseline_daily.get(str(target_date), [0.0, 0.0])
            challenger_values = challenger_daily.get(str(target_date), [0.0, 0.0])
            baseline_pnl += baseline_values[0]
            baseline_cost += baseline_values[1]
            challenger_pnl += challenger_values[0]
            challenger_cost += challenger_values[1]
        pnl_draws.append(challenger_pnl - baseline_pnl)
        if challenger_cost and baseline_cost:
            roi_draws.append(
                challenger_pnl / challenger_cost - baseline_pnl / baseline_cost
            )
    baseline_pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in baseline)
    baseline_cost = sum(float(row["entry_cost_usd"]) for row in baseline)
    challenger_pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in challenger)
    challenger_cost = sum(float(row["entry_cost_usd"]) for row in challenger)
    return {
        "denominator_dates": len(dates),
        "fee_adjusted_pnl_delta_usd": challenger_pnl - baseline_pnl,
        "pnl_delta_date_bootstrap_ci": [
            float(np.quantile(pnl_draws, 0.025)),
            float(np.quantile(pnl_draws, 0.975)),
        ],
        "fee_adjusted_roi_delta": (
            challenger_pnl / challenger_cost - baseline_pnl / baseline_cost
            if challenger_cost and baseline_cost
            else None
        ),
        "roi_delta_date_bootstrap_ci": [
            float(np.quantile(roi_draws, 0.025)) if roi_draws else None,
            float(np.quantile(roi_draws, 0.975)) if roi_draws else None,
        ],
    }


def run_trigger_ab(args: argparse.Namespace) -> int:
    raw = load_trigger_events(args.trigger_events)
    in_window = [
        row
        for row in raw
        if args.start_date <= str(row["target_date"]) <= args.end_date
    ]
    scheduled_all = [row for row in in_window if row["scheduled_phase"] is not None]
    margin_05 = [
        row for row in scheduled_all
        if float(row["actual_source_margin_c"]) >= 0.5 - EPS
    ]
    margin = [
        row for row in scheduled_all
        if float(row["actual_source_margin_c"]) >= 0.7 - EPS
    ]
    scheduled = margin
    first_05_rows = first_margin_events(scheduled_all, 0.5)
    trigger_rows = first_margin_events(scheduled_all, 0.7)
    predictions = load_confirmation_predictions(args.confirm_predictions)
    final_predictions = load_final_settlement_predictions(
        args.predictions, args.final_model_id
    )
    winners = load_tokyo_winners_from_pm_history(
        args.pm_history_dir,
        {
            str(row["target_date"])
            for row in first_05_rows + trigger_rows + scheduled_all
        },
    )
    joined = attach_trigger_evidence(
        trigger_rows,
        winners=winners,
        confirmation_predictions=predictions,
        final_predictions=final_predictions,
        args=args,
    )
    joined_05 = attach_trigger_evidence(
        first_05_rows,
        winners=winners,
        confirmation_predictions=predictions,
        final_predictions=final_predictions,
        args=args,
    )
    joined_model_scan = attach_trigger_evidence(
        margin_05,
        winners=winners,
        confirmation_predictions=predictions,
        final_predictions=final_predictions,
        args=args,
    )
    settled = [row for row in joined if row["settlement_no_wins"] is not None]
    model_scored = [
        row
        for row in settled
        if row["p_confirm_next_metar"] is not None
    ]
    market_scored = [
        row
        for row in settled
        if row["p_market_no"] is not None
    ]
    executable = [
        row
        for row in model_scored
        if finite(row.get("best_ask")) is not None
        and finite(row.get("ask_size")) is not None
        and float(row["best_ask"]) <= args.max_ask
        and float(row["ask_size"]) >= args.min_shares
    ]
    baseline_summary, baseline_trades = policy_summary(
        executable, "direct_taker_no_model", lambda row: True
    )
    gated_summary, gated_trades = policy_summary(
        executable,
        f"confirm_model_ge_{args.model_threshold:g}",
        lambda row: float(row["p_confirm_next_metar"]) >= args.model_threshold,
    )
    threshold_sweep: list[dict[str, Any]] = []
    for threshold in (0.5, 0.6, 0.7, 0.8):
        threshold_summary, threshold_trades = policy_summary(
            executable,
            f"confirm_model_ge_{threshold:g}",
            lambda row, value=threshold: float(row["p_confirm_next_metar"]) >= value,
        )
        kept = {(row["target_date"], row["market_bracket"]) for row in threshold_trades}
        rejected = [
            row
            for row in baseline_trades
            if (row["target_date"], row["market_bracket"]) not in kept
        ]
        threshold_summary.update(
            {
                "threshold": threshold,
                "wrong_baseline_trades_avoided": sum(
                    int(row["trade_result"] == "wrong") for row in rejected
                ),
                "correct_baseline_trades_missed": sum(
                    int(row["trade_result"] == "correct") for row in rejected
                ),
            }
        )
        threshold_sweep.append(threshold_summary)

    # Three-way policy comparison requested for the same raw Tokyo path:
    # first .5 cross, first .7 cross, and the first .5+ event whose frozen
    # final-settlement probability clears the actual ask, fee, and edge.
    settled_05 = [row for row in joined_05 if row["settlement_no_wins"] is not None]
    direct_05_summary, direct_05_trades = policy_summary(
        settled_05, "first_margin_ge_0p5", lambda row: True
    )
    direct_07_summary, direct_07_trades = policy_summary(
        settled, "first_margin_ge_0p7", lambda row: True
    )
    model_scan_settled = [
        row
        for row in joined_model_scan
        if row["settlement_no_wins"] is not None
        and row["p_final_settlement_no"] is not None
    ]
    def model_route(edge: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        eligible_rows: list[dict[str, Any]] = []
        for row in model_scan_settled:
            ask = finite(row.get("best_ask"))
            ask_size = finite(row.get("ask_size"))
            if (
                ask is None
                or ask_size is None
                or ask > args.max_ask
                or ask_size < args.min_shares
            ):
                continue
            fee_per_share = FEE_RATE * ask * (1 - ask)
            model_edge = float(row["p_final_settlement_no"]) - ask - fee_per_share
            if model_edge < edge - EPS:
                continue
            record = dict(row)
            record["final_model_edge_at_entry"] = model_edge
            eligible_rows.append(record)
        first_by_state: dict[tuple[str, str], dict[str, Any]] = {}
        for row in sorted(eligible_rows, key=lambda item: parse_ts(item["ts_utc"])):
            first_by_state.setdefault(
                (str(row["target_date"]), str(row["market_bracket"])), row
            )
        summary, trades = policy_summary(
            list(first_by_state.values()),
            f"final_settlement_positive_ev_ge_{edge:g}",
            lambda row: True,
        )
        summary.update({
            "model_scored_path_events": len(model_scan_settled),
            "positive_edge_path_events": len(eligible_rows),
            "model_id": args.final_model_id,
            "minimum_edge": edge,
        })
        return summary, trades

    edge_runs = {
        edge: model_route(edge)
        for edge in sorted({0.0, 0.02, 0.05, float(args.final_model_edge)})
    }
    model_summary, model_trades = edge_runs[float(args.final_model_edge)]
    comparison_dates = sorted({str(row["target_date"]) for row in settled_05})
    direct_07_minus_05 = paired_policy_delta(
        direct_05_trades, direct_07_trades, comparison_dates
    )
    model_minus_05 = paired_policy_delta(
        direct_05_trades, model_trades, comparison_dates
    )
    model_minus_07 = paired_policy_delta(
        direct_07_trades, model_trades, comparison_dates
    )
    model_edge_sweep: list[dict[str, Any]] = []
    for edge, (edge_summary, edge_trades) in edge_runs.items():
        edge_trade_keys = {
            (str(row["target_date"]), str(row["market_bracket"]))
            for row in edge_trades
        }
        rejected = [
            row
            for row in direct_05_trades
            if (str(row["target_date"]), str(row["market_bracket"]))
            not in edge_trade_keys
        ]
        edge_summary = dict(edge_summary)
        edge_summary.update({
            "wrong_0p5_trades_avoided": sum(
                int(row["trade_result"] == "wrong") for row in rejected
            ),
            "correct_0p5_trades_missed": sum(
                int(row["trade_result"] == "correct") for row in rejected
            ),
            "minus_0p5": paired_policy_delta(
                direct_05_trades, edge_trades, comparison_dates
            ),
        })
        model_edge_sweep.append(edge_summary)

    direct_05_map = {
        (str(row["target_date"]), str(row["market_bracket"])): row
        for row in direct_05_trades
    }
    direct_07_map = {
        (str(row["target_date"]), str(row["market_bracket"])): row
        for row in direct_07_trades
    }
    paired_05_07: list[dict[str, Any]] = []
    for key in sorted(set(direct_05_map) & set(direct_07_map)):
        early = direct_05_map[key]
        late = direct_07_map[key]
        paired_05_07.append({
            "target_date": key[0],
            "market_bracket": key[1],
            "margin_05_ts_utc": early["ts_utc"],
            "margin_07_ts_utc": late["ts_utc"],
            "wait_minutes": (
                parse_ts(late["ts_utc"]) - parse_ts(early["ts_utc"])
            ).total_seconds() / 60.0,
            "margin_05_ask": early["best_ask"],
            "margin_07_ask": late["best_ask"],
            "ask_change_07_minus_05": (
                float(late["best_ask"]) - float(early["best_ask"])
            ),
            "margin_05_ask_size": early["ask_size"],
            "margin_07_ask_size": late["ask_size"],
            "settlement_no_wins": early["settlement_no_wins"],
        })
    model_trade_keys = {
        (str(row["target_date"]), str(row["market_bracket"])) for row in model_trades
    }
    model_rejected_05_trades = [
        row
        for row in direct_05_trades
        if (str(row["target_date"]), str(row["market_bracket"]))
        not in model_trade_keys
    ]
    phase_summaries, phase_trades = phase_policy_comparison(
        joined,
        args.prior_trigger_join,
        target_shares=args.target_shares,
        min_shares=args.min_shares,
        max_ask=args.max_ask,
        consensus_min_ask=args.consensus_min_ask,
    )
    three_way = {
        "universe": "Tokyo raw scheduled JMA cross-candidate path; first date-bracket at .5, first at .7, or first .5+ event with final-settlement positive executable edge",
        "first_05_signal_events": len(first_05_rows),
        "first_07_signal_events": len(trigger_rows),
        "first_05_settled_events": len(settled_05),
        "first_07_settled_events": len(settled),
        "model_scored_path_events": len(model_scan_settled),
        "policies": [direct_05_summary, direct_07_summary, model_summary],
        "model_edge_sweep": model_edge_sweep,
        "first_07_minus_first_05": direct_07_minus_05,
        "model_minus_first_05": model_minus_05,
        "model_minus_first_07": model_minus_07,
        "paired_05_07_both_executable": len(paired_05_07),
        "paired_05_07_mean_wait_minutes": (
            float(np.mean([row["wait_minutes"] for row in paired_05_07]))
            if paired_05_07 else None
        ),
        "paired_05_07_median_wait_minutes": (
            float(np.median([row["wait_minutes"] for row in paired_05_07]))
            if paired_05_07 else None
        ),
        "paired_05_07_mean_ask_change": (
            float(np.mean([row["ask_change_07_minus_05"] for row in paired_05_07]))
            if paired_05_07 else None
        ),
        "paired_05_07_median_ask_change": (
            float(np.median([row["ask_change_07_minus_05"] for row in paired_05_07]))
            if paired_05_07 else None
        ),
        "model_wrong_05_trades_avoided": sum(
            int(row["trade_result"] == "wrong") for row in model_rejected_05_trades
        ),
        "model_correct_05_trades_missed": sum(
            int(row["trade_result"] == "correct") for row in model_rejected_05_trades
        ),
        "model_training_clock": "historical_non_pit_observation_clock; post-audit replay is not untouched forward",
        "classification": "research_same-clock_counterfactual_no_live_change",
    }
    selected_ids = {(row["target_date"], row["market_bracket"]) for row in gated_trades}
    avoided = [
        row
        for row in baseline_trades
        if (row["target_date"], row["market_bracket"]) not in selected_ids
    ]
    policy_rows = [baseline_summary, gated_summary]
    policy_delta = paired_policy_delta(baseline_trades, gated_trades)
    all_trades = baseline_trades + gated_trades
    calibration = calibration_rows(market_scored)
    broad_calibration: list[dict[str, Any]] = []
    broad_calibration_summary: dict[str, Any] | None = None
    if args.history_price_proxy.exists() and args.continuous_features.exists():
        broad_calibration, broad_calibration_summary = broad_market_calibration(
            args.history_price_proxy, args.continuous_features
        )
    phase_metrics: list[dict[str, Any]] = []
    for phase in ("T13", "T3"):
        phase_rows = [row for row in model_scored if row["scheduled_phase"] == phase]
        if not phase_rows:
            continue
        y = np.asarray([int(row["next_metar_confirmation_label"]) for row in phase_rows])
        p = np.asarray([float(row["p_confirm_next_metar"]) for row in phase_rows])
        phase_metrics.append(
            {
                "phase": phase,
                "events": len(phase_rows),
                "target_dates": len({str(row["target_date"]) for row in phase_rows}),
                "confirmation_rate": float(np.mean(y)),
                "mean_model_probability": float(np.mean(p)),
                "model_brier": float(np.mean((p - y) ** 2)),
                "model_accuracy_at_0p5": float(np.mean((p >= 0.5) == y)),
                "settlement_no_win_rate": float(np.mean([int(row["settlement_no_wins"]) for row in phase_rows])),
            }
        )
    paired_rows: list[dict[str, Any]] = []
    by_key_phase: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in scheduled:
        key = (
            str(row["target_date"]),
            str(row["market_bracket"]),
            str(row["scheduled_report_ts_utc"]),
            str(row["scheduled_phase"]),
        )
        current = by_key_phase.get(key)
        if current is None or parse_ts(row["ts_utc"]) < parse_ts(current["ts_utc"]):
            by_key_phase[key] = row
    for target_date, bracket, report_ts in sorted(
        {(key[0], key[1], key[2]) for key in by_key_phase}
    ):
        early = by_key_phase.get((target_date, bracket, report_ts, "T13"))
        late = by_key_phase.get((target_date, bracket, report_ts, "T3"))
        if early is None or late is None:
            continue
        early_ask = finite(early.get("best_ask"))
        late_ask = finite(late.get("best_ask"))
        if early_ask is None or late_ask is None:
            continue
        paired_rows.append(
            {
                "target_date": target_date,
                "market_bracket": bracket,
                "scheduled_report_ts_utc": report_ts,
                "t13_ts_utc": early["ts_utc"],
                "t3_ts_utc": late["ts_utc"],
                "t13_ask": early_ask,
                "t3_ask": late_ask,
                "wait_price_change": late_ask - early_ask,
                "t13_ask_size": early.get("ask_size"),
                "t3_ask_size": late.get("ask_size"),
            }
        )
    wait_changes = [float(row["wait_price_change"]) for row in paired_rows]
    for row in paired_rows:
        row["t13_executable"] = int(
            float(row["t13_ask"]) <= args.max_ask
            and finite(row.get("t13_ask_size")) is not None
            and float(row["t13_ask_size"]) >= args.min_shares
        )
        row["t3_executable"] = int(
            float(row["t3_ask"]) <= args.max_ask
            and finite(row.get("t3_ask_size")) is not None
            and float(row["t3_ask_size"]) >= args.min_shares
        )
    two_sided_spreads = [
        float(row["best_ask"]) - float(row["best_bid"])
        for row in market_scored
        if finite(row.get("best_ask")) is not None
        and finite(row.get("best_bid")) is not None
    ]
    funnel = [
        {"funnel": "signal", "stage": "tokyo_cross_candidate", "unit": "event", "count": len(raw)},
        {"funnel": "signal", "stage": "window_scheduled_margin_ge_0p5", "unit": "event", "count": len(margin_05)},
        {"funnel": "signal", "stage": "first_0p5_target_date_bracket", "unit": "date-bracket", "count": len(first_05_rows)},
        {"funnel": "signal", "stage": "actual_margin_ge_0p7", "unit": "event", "count": len(margin)},
        {"funnel": "signal", "stage": "scheduled_T13_or_T3", "unit": "event", "count": len(scheduled)},
        {"funnel": "signal", "stage": "first_target_date_bracket", "unit": "date-bracket", "count": len(trigger_rows)},
        {"funnel": "evidence", "stage": "settled", "unit": "date-bracket", "count": len(settled)},
        {"funnel": "evidence", "stage": "confirmation_model", "unit": "date-bracket", "count": len(model_scored)},
        {"funnel": "evidence", "stage": "two_sided_market_mid", "unit": "date-bracket", "count": len(market_scored)},
        {"funnel": "evidence", "stage": "taker_executable", "unit": "date-bracket", "count": len(executable)},
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "trigger_join_rows.csv", joined)
    write_csv(args.out / "trigger_join_rows_0p5.csv", joined_05)
    write_csv(args.out / "policy_summaries.csv", policy_rows)
    write_csv(args.out / "threshold_sweep.csv", threshold_sweep)
    write_csv(args.out / "policy_trades.csv", all_trades)
    write_csv(args.out / "market_calibration.csv", calibration)
    write_csv(args.out / "broad_market_calibration.csv", broad_calibration)
    write_csv(args.out / "phase_metrics.csv", phase_metrics)
    write_csv(args.out / "t13_t3_wait_pairs.csv", paired_rows)
    write_csv(args.out / "funnel.csv", funnel)
    write_csv(args.out / "model_rejected_baseline_trades.csv", avoided)
    write_csv(
        args.out / "three_way_policy_summaries.csv",
        [direct_05_summary, direct_07_summary, model_summary],
    )
    write_csv(args.out / "three_way_model_edge_sweep.csv", model_edge_sweep)
    write_csv(
        args.out / "three_way_policy_trades.csv",
        direct_05_trades + direct_07_trades + model_trades,
    )
    write_csv(args.out / "three_way_0p5_0p7_pairs.csv", paired_05_07)
    write_csv(
        args.out / "three_way_model_rejected_0p5_trades.csv",
        model_rejected_05_trades,
    )
    write_csv(args.out / "phase_policy_summaries.csv", phase_summaries)
    write_csv(args.out / "phase_policy_trades.csv", phase_trades)
    summary = {
        "schema_version": "tokyo_jma_multivariate_market_v1_trigger_ab",
        "window": {"start": args.start_date, "end": args.end_date},
        "universe": "first Tokyo date-bracket actual JMA margin >=0.7 at source phases :10/:20/:40/:50",
        "events": len(trigger_rows),
        "settled_events": len(settled),
        "confirmation_model_scored_events": len(model_scored),
        "two_sided_market_mid_events": len(market_scored),
        "executable_events": len(executable),
        "baseline": baseline_summary,
        "model_gate": gated_summary,
        "model_gate_minus_direct": policy_delta,
        "three_way_policy_comparison": three_way,
        "phase_policy_comparison": phase_summaries,
        "threshold_sweep": threshold_sweep,
        "wrong_baseline_trades_avoided": sum(int(row["trade_result"] == "wrong") for row in avoided),
        "correct_baseline_trades_missed": sum(int(row["trade_result"] == "correct") for row in avoided),
        "avoided_trade_pnl_usd": sum(float(row["fee_adjusted_pnl_usd"]) for row in avoided),
        "phase_metrics": phase_metrics,
        "t13_t3_pairs": len(paired_rows),
        "mean_wait_price_change": (
            float(np.mean(wait_changes)) if wait_changes else None
        ),
        "median_wait_price_change": (
            float(np.median(wait_changes)) if wait_changes else None
        ),
        "wait_price_change_range": (
            [min(wait_changes), max(wait_changes)] if wait_changes else None
        ),
        "wait_both_executable_pairs": sum(
            int(row["t13_executable"] and row["t3_executable"])
            for row in paired_rows
        ),
        "wait_gained_executability_pairs": sum(
            int(not row["t13_executable"] and row["t3_executable"])
            for row in paired_rows
        ),
        "wait_lost_executability_pairs": sum(
            int(row["t13_executable"] and not row["t3_executable"])
            for row in paired_rows
        ),
        "two_sided_spread_events": len(two_sided_spreads),
        "mean_two_sided_spread": (
            float(np.mean(two_sided_spreads)) if two_sided_spreads else None
        ),
        "median_two_sided_spread": (
            float(np.median(two_sided_spreads)) if two_sided_spreads else None
        ),
        "market_calibration": calibration,
        "broad_market_calibration_summary": broad_calibration_summary,
        "fee_formula": "shares * 0.05 * ask * (1-ask)",
        "depth_policy": "min(target_shares, top_ask_size), require min_shares; no unobserved deeper VWAP assumed",
        "model_role": "next scheduled routine METAR confirmation diagnostic; not final settlement probability",
        "live_behavior_changed": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--exact-first-seen", type=Path, default=DEFAULT_EXACT)
    parser.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-07-30")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--trigger-events",
        type=Path,
        help="Use exact live cross events and run same-clock trigger A/B mode.",
    )
    parser.add_argument(
        "--prior-trigger-join",
        type=Path,
        action="append",
        default=[],
        help=(
            "Prior trigger_join_rows.csv artifact used only to extend the "
            "fixed phase-policy date denominator; repeat as needed."
        ),
    )
    parser.add_argument(
        "--confirm-predictions", type=Path, default=DEFAULT_CONFIRM_PREDICTIONS
    )
    parser.add_argument("--pm-history-dir", type=Path, default=DEFAULT_PM_HISTORY)
    parser.add_argument(
        "--history-price-proxy", type=Path, default=DEFAULT_HISTORY_PRICE_PROXY
    )
    parser.add_argument(
        "--continuous-features", type=Path, default=DEFAULT_CONTINUOUS_FEATURES
    )
    parser.add_argument("--target-shares", type=float, default=15.0)
    parser.add_argument("--min-shares", type=float, default=5.0)
    parser.add_argument("--max-ask", type=float, default=0.97)
    parser.add_argument(
        "--consensus-min-ask",
        type=float,
        default=0.80,
        help=(
            "Pre-registered market-consensus boundary for the .7 previous-NO "
            "source-event policy; evaluated as a structural arm, not swept by PnL."
        ),
    )
    parser.add_argument("--model-threshold", type=float, default=0.5)
    parser.add_argument(
        "--final-model-id", default="event_safe_selector_v2"
    )
    parser.add_argument(
        "--final-model-edge",
        type=float,
        default=0.0,
        help="Minimum p_final_no - actual NO ask - official fee for model route.",
    )
    args = parser.parse_args()
    if args.trigger_events is not None:
        return run_trigger_ab(args)
    args.out.mkdir(parents=True, exist_ok=True)

    raw_predictions = [
        row
        for row in read_csv(args.predictions)
        if row.get("target_id") == "final_break"
        and row.get("split") == "post_audit_replay"
        and args.start_date <= str(row["target_date"]) <= args.end_date
    ]
    exact = exact_first_seen(args.exact_first_seen)
    availability_requests: list[tuple[str, datetime]] = []
    for row in raw_predictions:
        observed = parse_ts(row["decision_ts_utc"])
        availability_requests.append(
            (
                str(row["target_date"]),
                exact.get(observed.isoformat(), observed + timedelta(minutes=15)),
            )
        )
    books = load_books(args.books, availability_requests)
    winners = load_winners(args.db, args.start_date, args.end_date)

    joined: list[dict[str, Any]] = []
    seen_exact = 0
    for row in raw_predictions:
        observed = parse_ts(row["decision_ts_utc"])
        exact_available = exact.get(observed.isoformat())
        if exact_available is not None:
            available = exact_available
            clock_class = "collector_exact_hash_verified"
            seen_exact += 1
        else:
            available = observed + timedelta(minutes=15)
            clock_class = "archive_reconstructed_plus_15m"
        bracket = str(int(float(row["prior_bracket"])))
        quote = quote_after(books.get((str(row["target_date"]), bracket), []), available)
        winner = winners.get(str(row["target_date"]))
        record = dict(row)
        record.update(
            {
                "availability_ts_utc": available.isoformat(),
                "availability_clock_class": clock_class,
                "market_bracket": bracket,
                "winning_bracket": winner,
                "settlement_no_wins": (
                    int(winner != bracket) if winner is not None else None
                ),
                "book_covered": int(quote is not None),
            }
        )
        if quote is not None:
            record.update(
                {
                    "snapshot_ts_utc": quote["snapshot_ts_utc"].isoformat(),
                    "availability_to_book_min": (
                        quote["snapshot_ts_utc"] - available
                    ).total_seconds()
                    / 60,
                    "p_model_no": float(row["p_model"]),
                    "p_market_no": quote["no_mid"],
                    "no_bid": quote["no_bid"],
                    "no_ask": quote["no_ask"],
                    "no_ask_size": quote["no_ask_size"],
                    "fee_per_share": (
                        FEE_RATE * quote["no_ask"] * (1 - quote["no_ask"])
                        if quote["no_ask"] is not None
                        else None
                    ),
                }
            )
            if quote["no_ask"] is not None:
                fee = FEE_RATE * quote["no_ask"] * (1 - quote["no_ask"])
                record["fee_adjusted_edge"] = (
                    float(row["p_model"]) - quote["no_ask"] - fee
                )
                record["five_share_executable"] = int(
                    quote["no_ask_size"] is not None
                    and quote["no_ask_size"] >= SHARES
                )
        joined.append(record)

    scored = [
        row
        for row in joined
        if row.get("settlement_no_wins") is not None
        and finite(row.get("p_market_no")) is not None
    ]
    delta, delta_low, delta_high = (
        date_bootstrap_brier_delta(scored)
        if scored
        else (math.nan, math.nan, math.nan)
    )
    model_brier = date_equal_brier(scored, "p_model_no") if scored else math.nan
    market_brier = date_equal_brier(scored, "p_market_no") if scored else math.nan
    score_slices: list[dict[str, Any]] = []
    for slice_name, selected in (
        ("all", scored),
        (
            "collector_exact_hash_verified",
            [
                row
                for row in scored
                if row["availability_clock_class"]
                == "collector_exact_hash_verified"
            ],
        ),
        (
            "archive_reconstructed_plus_15m",
            [
                row
                for row in scored
                if row["availability_clock_class"]
                == "archive_reconstructed_plus_15m"
            ],
        ),
    ):
        if not selected:
            continue
        slice_delta, slice_low, slice_high = date_bootstrap_brier_delta(selected)
        score_slices.append(
            {
                "slice": slice_name,
                "events": len(selected),
                "target_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "model_brier": date_equal_brier(selected, "p_model_no"),
                "market_brier": date_equal_brier(selected, "p_market_no"),
                "model_minus_market_brier": slice_delta,
                "date_bootstrap_ci_low": slice_low,
                "date_bootstrap_ci_high": slice_high,
            }
        )

    candidates = [
        row
        for row in joined
        if row.get("settlement_no_wins") is not None
        and row.get("five_share_executable") == 1
        and finite(row.get("fee_adjusted_edge")) is not None
        and float(row["fee_adjusted_edge"]) > 0
    ]
    first_by_date: dict[str, dict[str, Any]] = {}
    for row in sorted(candidates, key=lambda item: str(item["availability_ts_utc"])):
        first_by_date.setdefault(str(row["target_date"]), row)
    trades: list[dict[str, Any]] = []
    for row in first_by_date.values():
        trade = dict(row)
        ask = float(row["no_ask"])
        fee = FEE_RATE * ask * (1 - ask)
        payout = SHARES * int(row["settlement_no_wins"])
        cost = SHARES * (ask + fee)
        trade.update(
            {
                "shares": SHARES,
                "entry_cost_usd": cost,
                "gross_payout_usd": payout,
                "fee_adjusted_pnl_usd": payout - cost,
                "trade_result": "correct" if payout > 0 else "wrong",
                "replay_class": "counterfactual_frozen_zero_notional",
            }
        )
        trades.append(trade)
    roi, roi_low, roi_high = roi_bootstrap(trades)

    cases = sorted(
        trades,
        key=lambda row: (
            int(row["trade_result"] == "wrong"),
            abs(float(row["fee_adjusted_pnl_usd"])),
        ),
        reverse=True,
    )
    if len(cases) < 8:
        extra = sorted(
            scored,
            key=lambda row: abs(
                float(row["p_model_no"]) - int(row["settlement_no_wins"])
            ),
            reverse=True,
        )
        used = {str(row["event_id"]) for row in cases}
        cases.extend(row for row in extra if str(row["event_id"]) not in used)
    cases = cases[:12]

    funnel = [
        {"funnel": "signal", "stage": "first_cross_events", "unit": "event", "count": len(raw_predictions)},
        {"funnel": "signal", "stage": "positive_fee_adjusted_edge", "unit": "event", "count": len(candidates)},
        {"funnel": "signal", "stage": "first_city_day_trade", "unit": "city-day", "count": len(trades)},
        {"funnel": "evidence", "stage": "collector_exact_clock", "unit": "event", "count": seen_exact},
        {"funnel": "evidence", "stage": "book_within_45m", "unit": "event", "count": sum(int(row["book_covered"]) for row in joined)},
        {"funnel": "evidence", "stage": "same_row_settled_score", "unit": "event", "count": len(scored)},
        {"funnel": "evidence", "stage": "settled_executable_trade", "unit": "city-day", "count": len(trades)},
    ]
    write_csv(args.out / "market_join_rows.csv", joined)
    write_csv(args.out / "counterfactual_trades.csv", trades)
    write_csv(args.out / "casebook.csv", cases)
    write_csv(args.out / "funnel.csv", funnel)
    write_csv(args.out / "score_slices.csv", score_slices)
    summary = {
        "schema_version": "tokyo_jma_multivariate_market_v1",
        "window": {"start": args.start_date, "end": args.end_date},
        "clock_policy": {
            "exact": "collector exact + hash verified where available",
            "archive": "observation clock + 15 minutes; never exact first-seen",
        },
        "events": len(raw_predictions),
        "exact_clock_events": seen_exact,
        "book_covered_events": sum(int(row["book_covered"]) for row in joined),
        "settled_same_row_scores": len(scored),
        "settled_target_dates": len(
            {str(row["target_date"]) for row in scored}
        ),
        "model_brier": model_brier,
        "market_brier": market_brier,
        "model_minus_market_brier": delta,
        "model_minus_market_brier_ci": [delta_low, delta_high],
        "counterfactual_trades": len(trades),
        "trade_wins": sum(int(row["trade_result"] == "correct") for row in trades),
        "fee_adjusted_pnl_usd": sum(
            float(row["fee_adjusted_pnl_usd"]) for row in trades
        ),
        "fee_adjusted_roi": roi,
        "fee_adjusted_roi_ci": [roi_low, roi_high],
        "fee_formula": "shares * 0.05 * ask * (1-ask)",
        "live_behavior_changed": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
