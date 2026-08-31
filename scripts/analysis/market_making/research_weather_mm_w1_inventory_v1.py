#!/usr/bin/env python3
"""Replay the five preregistered Weather-first W1 inventory arms.

The denominator is actual Core Carry BUY_YES inventory, aggregated at
condition grain.  Every arm starts at the same timestamp and position.  Public
book crossing is a diagnostic fill proxy only; the output never calls it an
actual fill and never places an order or performs a merge.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from src.strategies.weather_edge_v1.execution.economics import (  # noqa: E402
    estimate_polymarket_v2_fee,
)
from src.strategies.weather_edge_v1.execution.inventory_economics import (  # noqa: E402
    ARM_NAMES,
    InventoryArmOutcome,
    PairedInventorySummary,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_clock_contract import parse_utc, utc_text  # noqa: E402
from weather_data_feed.production_paths import historical_orderbook_roots  # noqa: E402


SCHEMA_VERSION = "weather_mm_w1_inventory_replay_v1"
DEFAULT_CONFIG = (
    ROOT
    / "src/strategies/weather_edge_v1/config/weather_mm_w1_inventory_prereg_v1.json"
)
PRODUCTION = load_production_spec()
DEFAULT_RUNTIME = (
    PRODUCTION.pm_runtime_root
    / "weather_edge_v1/current_yes_core_carry_tiny_live_v2"
)
DEFAULT_SNAPSHOT_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/strategy_snapshots"
)
DEFAULT_BOOK_ROOTS = (
    PRODUCTION.resolved_market_books_root() / "batches",
    *historical_orderbook_roots(),
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _iter_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, Mapping):
        row = dict(value)
        yield row
        for child in row.values():
            yield from _iter_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_objects(child)


def _load_actual_positions(
    connection: sqlite3.Connection,
    *,
    instance_id: str,
) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT
          condition_id, token_id, city, target_date, bracket, unit,
          SUM(fill_qty) AS shares,
          SUM(cost_usd + fees_usd) AS entry_cash_cost_usd,
          SUM(cost_usd) AS entry_principal_usd,
          SUM(fees_usd) AS entry_fees_usd,
          MIN(fill_ts_utc) AS first_fill_at_utc,
          MAX(fill_ts_utc) AS last_fill_at_utc,
          COUNT(*) AS fill_rows,
          MAX(CASE WHEN settlement_status='settled' THEN final_yes END) AS final_yes,
          MAX(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled
        FROM fact_trades
        WHERE trade_class='live_real'
          AND venue='polymarket_clob'
          AND instance_id=?
          AND side='BUY_YES'
        GROUP BY condition_id, token_id, city, target_date, bracket, unit
        ORDER BY last_fill_at_utc, condition_id
        """,
        (instance_id,),
    ).fetchall()
    positions = [dict(row) for row in rows]
    for position in positions:
        settlement = connection.execute(
            """
            SELECT final_price, available_at_utc, first_seen_at_utc, created_at_utc,
                   settlement_outcome_id
            FROM settlement_outcomes
            WHERE condition_id=? AND token_id=? AND settlement_status='settled'
            ORDER BY COALESCE(available_at_utc, first_seen_at_utc, created_at_utc)
            LIMIT 1
            """,
            (position["condition_id"], position["token_id"]),
        ).fetchone()
        if settlement is not None:
            position["final_yes"] = settlement["final_price"]
            position["settled"] = 1
            position["settlement_available_at_utc"] = (
                settlement["available_at_utc"]
                or settlement["first_seen_at_utc"]
                or settlement["created_at_utc"]
            )
            position["settlement_outcome_id"] = settlement["settlement_outcome_id"]
        else:
            position["settlement_available_at_utc"] = None
            position["settlement_outcome_id"] = None
    return positions


def _score_metadata(runtime: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(runtime / "pre_live_scores.jsonl"):
        condition = str(row.get("current_condition_id") or "")
        if not condition:
            continue
        prior = result.get(condition)
        # Prefer the actual selected row, then the earliest evidence row.
        if prior is None or (
            row.get("would_submit_after_family_dedupe") is True
            and prior.get("would_submit_after_family_dedupe") is not True
        ):
            result[condition] = row
    return result


def _snapshot_index(root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = defaultdict(list)
    if root.exists():
        for path in root.rglob("*.json"):
            result[path.name].append(path)
    return result


def _instrument_mapping(
    positions: Sequence[Mapping[str, Any]],
    score_rows: Mapping[str, Mapping[str, Any]],
    snapshot_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    index = _snapshot_index(snapshot_root)
    mapping: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for position in positions:
        condition = str(position["condition_id"])
        score = score_rows.get(condition, {})
        snapshot_file = str(score.get("snapshot_file") or "")
        candidates = index.get(snapshot_file, []) if snapshot_file else []
        found = None
        source_path = None
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for row in _iter_objects(payload):
                if str(row.get("condition_id") or "") != condition:
                    continue
                yes_token = str(row.get("yes_token_id") or "")
                no_token = str(row.get("no_token_id") or "")
                if yes_token == str(position["token_id"]) and no_token:
                    found = {
                        "condition_id": condition,
                        "yes_token_id": yes_token,
                        "no_token_id": no_token,
                        "market_id": str(row.get("market_id") or ""),
                        "tick_size": str(score.get("current_yes_tick_size") or "0.01"),
                        "topology": "same_condition_binary_yes_no",
                    }
                    source_path = path
                    break
            if found:
                break
        if found:
            mapping[condition] = found
        evidence.append(
            {
                "condition_id": condition,
                "yes_token_id": position["token_id"],
                "no_token_id": None if found is None else found["no_token_id"],
                "snapshot_file": snapshot_file,
                "source_path": None if source_path is None else str(source_path),
                "topology_verified": found is not None,
                "reason": "ok" if found else "same_condition_no_token_mapping_missing",
            }
        )
    return mapping, evidence


def _book_files(roots: Sequence[Path], date_min: str, date_max: str) -> list[Path]:
    result: list[Path] = []
    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not root.exists():
            continue
        for day in root.iterdir():
            if not day.is_dir() or not date_min <= day.name <= date_max:
                continue
            for pattern in ("market_books_*.jsonl*", "orderbook_snapshot_*.jsonl*"):
                for path in day.glob(pattern):
                    stat = path.stat()
                    identity = (stat.st_dev, stat.st_ino)
                    if identity not in seen:
                        seen.add(identity)
                        result.append(path)
    return sorted(result)


def _load_books(
    roots: Sequence[Path],
    token_ids: set[str],
    date_min: str,
    date_max: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    files = _book_files(roots, date_min, date_max)
    for path in files:
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        raw_row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token_id = str(raw_row.get("token_id") or "")
                    if token_id not in token_ids or raw_row.get("status") != "ok":
                        continue
                    timestamp = str(
                        raw_row.get("available_at_utc")
                        or raw_row.get("fetched_at_utc")
                        or raw_row.get("snapshot_ts_utc")
                        or ""
                    )
                    try:
                        parsed = parse_utc(timestamp, field="book_available_at_utc")
                    except ValueError:
                        continue
                    if parsed is None:
                        continue
                    summary = raw_row.get("summary") if isinstance(raw_row.get("summary"), Mapping) else {}
                    raw_book = raw_row.get("raw") if isinstance(raw_row.get("raw"), Mapping) else {}
                    candidate = {
                        "token_id": token_id,
                        "available_at_utc": utc_text(parsed, field="book_available_at_utc"),
                        "snapshot_ts_utc": raw_row.get("snapshot_ts_utc"),
                        "bids": summary.get("bids") or raw_book.get("bids") or [],
                        "asks": summary.get("asks") or raw_book.get("asks") or [],
                        "source_path": str(path),
                    }
                    key = candidate["available_at_utc"]
                    prior = rows[token_id].get(key)
                    depth = sum(
                        _decimal(level.get("size")) or Decimal("0")
                        for side in (candidate["bids"], candidate["asks"])
                        for level in side
                        if isinstance(level, Mapping)
                    )
                    prior_depth = sum(
                        _decimal(level.get("size")) or Decimal("0")
                        for side in ((prior or {}).get("bids", []), (prior or {}).get("asks", []))
                        for level in side
                        if isinstance(level, Mapping)
                    )
                    if prior is None or depth > prior_depth:
                        rows[token_id][key] = candidate
        except (EOFError, OSError):
            continue
    ordered = {
        token: sorted(items.values(), key=lambda row: parse_utc(row["available_at_utc"], field="book_available_at_utc"))
        for token, items in rows.items()
    }
    return ordered, {
        "files_scanned": len(files),
        "tokens_requested": len(token_ids),
        "tokens_with_books": len(ordered),
        "book_rows": sum(len(items) for items in ordered.values()),
        "roots": [str(root) for root in roots],
    }


def _book_after(
    rows: Sequence[Mapping[str, Any]],
    at: datetime,
    max_lag_minutes: int,
) -> Mapping[str, Any] | None:
    deadline = at + timedelta(minutes=max_lag_minutes)
    return next(
        (
            row
            for row in rows
            if at
            <= parse_utc(row["available_at_utc"], field="book_available_at_utc")
            <= deadline
        ),
        None,
    )


def _levels(levels: Sequence[Any], *, reverse: bool) -> list[tuple[Decimal, Decimal]]:
    parsed: list[tuple[Decimal, Decimal]] = []
    for level in levels:
        if not isinstance(level, Mapping):
            continue
        price = _decimal(level.get("price"))
        size = _decimal(level.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            parsed.append((price, size))
    return sorted(parsed, key=lambda item: item[0], reverse=reverse)


def _walk(
    levels: Sequence[Any],
    shares: Decimal,
    *,
    sell: bool,
) -> dict[str, Any] | None:
    remaining = shares
    principal = Decimal("0")
    fee = Decimal("0")
    used: list[tuple[Decimal, Decimal]] = []
    for price, available in _levels(levels, reverse=sell):
        take = min(remaining, available)
        if take <= 0:
            continue
        principal += take * price
        fee += estimate_polymarket_v2_fee(
            shares=take,
            rate="0.05",
            price=price,
            exponent="1",
        )
        used.append((price, take))
        remaining -= take
        if remaining == 0:
            break
    if remaining > 0:
        return None
    cash = principal - fee if sell else principal + fee
    return {
        "principal": principal,
        "fee": fee,
        "cash": cash,
        "vwap": principal / shares,
        "worst_price": used[-1][0],
    }


def _depth_at_or_better(
    levels: Sequence[Any],
    quote: Decimal,
) -> Decimal:
    return sum(
        (size for price, size in _levels(levels, reverse=True) if price >= quote),
        Decimal("0"),
    )


def _hours_between(start: datetime, end: datetime) -> Decimal:
    delta = max(end - start, timedelta(0))
    seconds = (
        Decimal(delta.days * 86400 + delta.seconds)
        + Decimal(delta.microseconds) / Decimal("1000000")
    )
    return seconds / Decimal("3600")


def _financing_cost(capital_time: Decimal, annual_rate: Decimal) -> Decimal:
    return capital_time * annual_rate / Decimal("8760")


def _arm_row(
    episode: Mapping[str, Any],
    arm: str,
    *,
    terminal_at: datetime | None,
    accounting_pnl: Decimal | None,
    evidence: str,
    actual_episode: bool = False,
) -> dict[str, Any]:
    decision_at = parse_utc(episode["decision_at_utc"], field="decision_at_utc")
    assert decision_at is not None
    entry_cost = Decimal(str(episode["entry_cash_cost_usd"]))
    capital_time = (
        None
        if terminal_at is None
        else entry_cost * _hours_between(decision_at, terminal_at)
    )
    annual_rate = Decimal(str(episode["annual_financing_rate"]))
    financing = None if capital_time is None else _financing_cost(capital_time, annual_rate)
    operating = Decimal(str(episode["incremental_operating_cost_usd"]))
    economic = (
        None
        if accounting_pnl is None or financing is None
        else accounting_pnl - financing - operating
    )
    return {
        "episode_id": episode["episode_id"],
        "condition_id": episode["condition_id"],
        "city": episode["city"],
        "target_date": episode["target_date"],
        "shares": episode["shares"],
        "arm": arm,
        # W1's primary metric is capital-adjusted economic profit.  A known
        # terminal payoff without a PIT terminal timestamp is useful
        # accounting evidence, but it is not complete evidence for that
        # primary metric and must not enter the economic summary.
        "accounting_evidence_complete": accounting_pnl is not None,
        "evidence_complete": economic is not None,
        "evidence": evidence,
        "actual_exit_or_merge_episode": actual_episode,
        "terminal_at_utc": None if terminal_at is None else utc_text(terminal_at, field="terminal_at_utc"),
        "accounting_pnl_usd": None if accounting_pnl is None else str(accounting_pnl),
        "capital_time_usd_hours": None if capital_time is None else str(capital_time),
        "financing_cost_usd": None if financing is None else str(financing),
        "incremental_operating_cost_usd": str(operating),
        "economic_profit_usd": None if economic is None else str(economic),
        "economic_profit_per_share_usd": (
            None if economic is None else str(economic / Decimal(str(episode["shares"])))
        ),
    }


def _replay_episode(
    position: Mapping[str, Any],
    instrument: Mapping[str, Any] | None,
    books: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    execution = config["execution"]
    economics = config["economics"]
    shares = Decimal(str(position["shares"]))
    entry_cost = Decimal(str(position["entry_cash_cost_usd"]))
    decision_at = parse_utc(position["last_fill_at_utc"], field="last_fill_at_utc")
    assert decision_at is not None
    episode_id = "inventory:" + hashlib.sha256(
        f"{position['condition_id']}|{position['token_id']}|{position['last_fill_at_utc']}".encode()
    ).hexdigest()
    episode = {
        **dict(position),
        "episode_id": episode_id,
        "decision_at_utc": utc_text(decision_at, field="decision_at_utc"),
        "annual_financing_rate": economics["annual_financing_rate"],
        "incremental_operating_cost_usd": execution["incremental_operating_cost_usd"],
        "instrument_topology_verified": instrument is not None,
        "no_token_id": None if instrument is None else instrument["no_token_id"],
    }
    initial_lag = int(execution["initial_book_max_lag_minutes"])
    yes_rows = books.get(str(position["token_id"]), [])
    initial_yes = _book_after(yes_rows, decision_at, initial_lag)
    immediate = None if initial_yes is None else _walk(initial_yes["bids"], shares, sell=True)
    immediate_at = (
        None
        if initial_yes is None
        else parse_utc(initial_yes["available_at_utc"], field="book_available_at_utc")
    )
    arms = [
        _arm_row(
            episode,
            "immediate_taker_exit",
            terminal_at=immediate_at if immediate else None,
            accounting_pnl=None if immediate is None else immediate["cash"] - entry_cost,
            evidence="full_yes_bid_ladder" if immediate else "initial_full_yes_bid_ladder_missing",
        )
    ]

    passive_pnl = None
    passive_at = None
    passive_evidence = "initial_full_yes_book_missing"
    passive_quote = None
    passive_proxy_filled = False
    if initial_yes is not None:
        bids = _levels(initial_yes["bids"], reverse=True)
        asks = _levels(initial_yes["asks"], reverse=False)
        if bids and asks:
            tick = Decimal(str((instrument or {}).get("tick_size") or "0.01"))
            quote = max(bids[0][0] + tick, asks[0][0] - tick)
            if bids[0][0] < quote < Decimal("1"):
                passive_quote = quote
                deadline = decision_at + timedelta(minutes=int(execution["passive_ttl_minutes"]))
                cross = next(
                    (
                        row
                        for row in yes_rows
                        if decision_at
                        < parse_utc(row["available_at_utc"], field="book_available_at_utc")
                        <= deadline
                        and _depth_at_or_better(row["bids"], quote) >= shares
                    ),
                    None,
                )
                if cross is not None:
                    passive_proxy_filled = True
                    passive_at = parse_utc(cross["available_at_utc"], field="book_available_at_utc")
                    passive_pnl = shares * quote - entry_cost
                    passive_evidence = "book_cross_depth_fill_proxy_not_actual"
                else:
                    fallback_book = _book_after(
                        yes_rows,
                        deadline,
                        int(execution["fallback_book_max_lag_minutes"]),
                    )
                    fallback = None if fallback_book is None else _walk(fallback_book["bids"], shares, sell=True)
                    if fallback is not None:
                        passive_at = parse_utc(fallback_book["available_at_utc"], field="book_available_at_utc")
                        passive_pnl = fallback["cash"] - entry_cost
                        passive_evidence = "no_proxy_fill_then_full_bid_fallback"
                    else:
                        passive_evidence = "fallback_full_bid_ladder_missing"
            else:
                passive_evidence = "post_only_sell_quote_unavailable"
    arms.append(
        _arm_row(
            episode,
            "passive_then_fallback",
            terminal_at=passive_at,
            accounting_pnl=passive_pnl,
            evidence=passive_evidence,
        )
    )

    complement = None
    complement_at = None
    complement_evidence = "same_condition_no_token_mapping_missing"
    if instrument is not None:
        no_book = _book_after(
            books.get(str(instrument["no_token_id"]), []), decision_at, initial_lag
        )
        complement = None if no_book is None else _walk(no_book["asks"], shares, sell=False)
        if complement is not None:
            complement_at = parse_utc(no_book["available_at_utc"], field="book_available_at_utc")
            complement_evidence = "same_condition_no_full_ask_then_merge_counterfactual"
        else:
            complement_evidence = "same_condition_no_full_ask_missing"
    merge_cost = Decimal(str(execution["merge_operation_cost_usd"]))
    arms.append(
        _arm_row(
            episode,
            "complement_then_merge",
            terminal_at=complement_at,
            accounting_pnl=(
                None
                if complement is None
                else shares - entry_cost - complement["cash"] - merge_cost
            ),
            evidence=complement_evidence,
        )
    )

    final_yes = _decimal(position.get("final_yes"))
    settlement_at = None
    if position.get("settlement_available_at_utc"):
        settlement_at = parse_utc(
            position["settlement_available_at_utc"], field="settlement_available_at_utc"
        )
    hold_pnl = None if final_yes is None else shares * final_yes - entry_cost
    for arm in ("hold_to_settlement", "no_action"):
        arms.append(
            _arm_row(
                episode,
                arm,
                terminal_at=settlement_at if hold_pnl is not None else None,
                accounting_pnl=hold_pnl,
                evidence=(
                    "canonical_settlement"
                    if hold_pnl is not None and settlement_at is not None
                    else "canonical_settlement_missing"
                ),
            )
        )

    episode.update(
        {
            "initial_yes_book_at_utc": None if initial_yes is None else initial_yes["available_at_utc"],
            "immediate_exit_executable": immediate is not None,
            "passive_quote": None if passive_quote is None else str(passive_quote),
            "passive_proxy_filled": passive_proxy_filled,
            "complement_executable": complement is not None,
            "settlement_complete": hold_pnl is not None,
            "paired_complete": all(row["evidence_complete"] for row in arms),
        }
    )
    return episode, arms


def _block_values(
    rows: Sequence[Mapping[str, Any]],
    arm: str,
    metric: str,
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        if row["arm"] == arm and row.get(metric) is not None:
            result[str(row["target_date"])] += Decimal(str(row[metric]))
    return dict(result)


def _expected_shortfall(values: Sequence[Decimal], fraction: Decimal) -> Decimal | None:
    if not values:
        return None
    count = max(1, math.ceil(len(values) * float(fraction)))
    worst = sorted(values)[:count]
    return sum(worst, Decimal("0")) / Decimal(count)


def _bootstrap_paired_per_share(
    rows: Sequence[Mapping[str, Any]],
    arm: str,
    *,
    repetitions: int,
    seed: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, int, int]:
    by_episode = {
        (str(row["episode_id"]), str(row["arm"])): row
        for row in rows
        if row.get("economic_profit_usd") is not None
    }
    daily_delta: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    daily_shares: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    episodes = 0
    for key, candidate in by_episode.items():
        if key[1] != arm:
            continue
        baseline = by_episode.get((key[0], "no_action"))
        if baseline is None:
            continue
        date = str(candidate["target_date"])
        daily_delta[date] += Decimal(str(candidate["economic_profit_usd"])) - Decimal(str(baseline["economic_profit_usd"]))
        daily_shares[date] += Decimal(str(candidate["shares"]))
        episodes += 1
    dates = sorted(date for date in daily_delta if daily_shares[date] > 0)
    if not dates:
        return None, None, None, 0, 0
    exact_values = [daily_delta[date] / daily_shares[date] for date in dates]
    point = sum(exact_values, Decimal("0")) / Decimal(len(exact_values))
    values = np.array([float(value) for value in exact_values])
    if len(dates) == 1:
        return point, None, None, episodes, 1
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(repetitions, len(values)))
    draws = values[indices].mean(axis=1)
    return (
        point,
        Decimal(str(float(np.quantile(draws, 0.025)))),
        Decimal(str(float(np.quantile(draws, 0.975)))),
        episodes,
        len(dates),
    )


def _summaries(
    rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> dict[str, Any]:
    tail_fraction = Decimal(str(config["economics"]["expected_shortfall_tail_fraction"]))
    mere = Decimal(str(config["economics"]["mere_incremental_economic_profit_per_share_usd"]))
    repetitions = int(config["inference"]["bootstrap_repetitions"])
    seed = int(config["inference"]["seed"])
    summaries: dict[str, Any] = {}
    for index, arm in enumerate(ARM_NAMES):
        arm_rows = [row for row in rows if row["arm"] == arm]
        complete = [row for row in arm_rows if row["evidence_complete"]]
        economic_blocks = _block_values(complete, arm, "economic_profit_usd")
        point, lower, upper, paired_episodes, paired_dates = _bootstrap_paired_per_share(
            rows, arm, repetitions=repetitions, seed=seed + index
        )
        summaries[arm] = {
            "signal_denominator_episodes": len(arm_rows),
            "evidence_complete_episodes": len(complete),
            "evidence_complete_target_dates": len({row["target_date"] for row in complete}),
            "accounting_pnl_usd": str(sum((Decimal(str(row["accounting_pnl_usd"])) for row in complete), Decimal("0"))),
            "economic_profit_usd": str(sum((Decimal(str(row["economic_profit_usd"])) for row in complete), Decimal("0"))),
            "capital_time_usd_hours": str(sum((Decimal(str(row["capital_time_usd_hours"])) for row in complete), Decimal("0"))),
            "target_date_expected_shortfall_usd": (
                None
                if not economic_blocks
                else str(_expected_shortfall(list(economic_blocks.values()), tail_fraction))
            ),
            "target_date_worst_case_usd": None if not economic_blocks else str(min(economic_blocks.values())),
            "paired_vs_no_action": {
                "episodes": paired_episodes,
                "target_dates": paired_dates,
                "mean_economic_delta_per_share_usd": None if point is None else str(point),
                "date_block_bootstrap_ci95": [
                    None if lower is None else str(lower),
                    None if upper is None else str(upper),
                ],
                "mere_per_share_usd": str(mere),
                "economic_gate": (
                    "PASS"
                    if lower is not None and lower > mere
                    else "FUTILITY"
                    if upper is not None and upper < mere
                    else "INCONCLUSIVE"
                ),
            },
        }
    return summaries


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = Path(args.runtime)
    db_path = Path(args.db)
    output = prepare_new_run_output(
        resolve_run_output(
            "weather_mm_w1_inventory_v1",
            run_id=args.run_id,
            explicit_output=Path(args.output_dir) if args.output_dir else None,
        )
    )
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        positions = _load_actual_positions(
            connection,
            instance_id=config["universe"]["strategy_instance"],
        )
        fact_built_at = connection.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
    finally:
        connection.close()
    score_rows = _score_metadata(runtime)
    instruments, instrument_evidence = _instrument_mapping(
        positions, score_rows, Path(args.snapshot_root)
    )
    dates = sorted(str(position["target_date"]) for position in positions)
    token_ids = {str(position["token_id"]) for position in positions}
    token_ids.update(instrument["no_token_id"] for instrument in instruments.values())
    if dates:
        date_min = (
            datetime.fromisoformat(dates[0]) - timedelta(days=1)
        ).date().isoformat()
        date_max = (
            datetime.fromisoformat(dates[-1]) + timedelta(days=2)
        ).date().isoformat()
        books, book_coverage = _load_books(
            tuple(Path(root) for root in args.book_root), token_ids, date_min, date_max
        )
    else:
        books = {}
        book_coverage = {
            "files_scanned": 0,
            "tokens_requested": 0,
            "tokens_with_books": 0,
            "book_rows": 0,
            "roots": [str(root) for root in args.book_root],
            "reason": "empty_actual_inventory_denominator",
        }
    episodes: list[dict[str, Any]] = []
    arm_rows: list[dict[str, Any]] = []
    for position in positions:
        episode, arms = _replay_episode(
            position,
            instruments.get(str(position["condition_id"])),
            books,
            config,
        )
        episodes.append(episode)
        arm_rows.extend({**row, "target_date": position["target_date"]} for row in arms)
    summaries = _summaries(arm_rows, config)
    complete_episodes = [row for row in episodes if row["paired_complete"]]
    floors = config["inference"]["first_look_floor"]
    actual_exit_or_merge = sum(row["actual_exit_or_merge_episode"] for row in arm_rows)
    measurement_gate = {
        "decision_floor": len(positions) >= int(floors["decisions"]),
        "target_date_floor": len(set(dates)) >= int(floors["target_dates"]),
        "actual_exit_or_merge_floor": actual_exit_or_merge >= int(floors["actual_exit_or_merge_episodes"]),
        "actual_exit_or_merge_episodes": actual_exit_or_merge,
        "status": "FAIL",
    }
    if all(value for key, value in measurement_gate.items() if key.endswith("_floor")):
        measurement_gate["status"] = "PASS"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": "historical_counterfactual_effect_estimate_not_live_evidence",
        "config": {
            "path": str(config_path),
            "sha256": _sha256(config_path),
            "payload": config,
        },
        "inputs": {
            "db": str(db_path),
            "db_device": db_path.stat().st_dev,
            "db_inode": db_path.stat().st_ino,
            "fact_trades_built_at_utc": fact_built_at,
            "runtime": str(runtime),
            "pre_live_scores_sha256": _sha256(runtime / "pre_live_scores.jsonl"),
        },
        "denominator": {
            "actual_inventory_episodes": len(positions),
            "target_dates": len(set(dates)),
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "settled_episodes": sum(bool(row.get("settled")) for row in positions),
            "topology_verified_episodes": len(instruments),
            "paired_complete_episodes": len(complete_episodes),
            "paired_complete_target_dates": len({row["target_date"] for row in complete_episodes}),
        },
        "book_coverage": book_coverage,
        "arm_summaries": summaries,
        "gates": {
            "correctness": {
                "nonempty_actual_inventory_denominator": bool(positions),
                "same_actual_position_and_decision_time": True,
                "fixed_five_arms": list(ARM_NAMES),
                "no_estimated_incentive_in_realized_pnl": True,
                "status": "PASS" if positions else "FAIL",
            },
            "measurement": measurement_gate,
            "economic": {
                arm: summary["paired_vs_no_action"]["economic_gate"]
                for arm, summary in summaries.items()
            },
            "promotion": "FAIL_CLOSED",
        },
        "decision": {
            "live_change": "none",
            "default_inventory_policy": "no_action_hold_to_settlement",
            "candidate_for_zero_notional_shadow": (
                "passive_then_fallback"
                if positions
                and summaries["passive_then_fallback"]["paired_vs_no_action"]["economic_gate"] != "FUTILITY"
                else "none"
            ),
            "reason": "actual exit/merge measurement floor is not met and public book crossing is not actual fill evidence",
        },
        "limitations": [
            "Passive fill credit is a public-book cross/depth proxy, not an own-order fill.",
            "Complement-and-merge is a same-condition topology counterfactual; no split, merge, redeem, or order was executed.",
            "No counterfactual rebate or reward is counted without a realized payout identity.",
            "Missing full ladders remain in the signal denominator and out of the evidence funnel.",
        ],
    }
    _write_csv(output / "inventory_episodes.csv", episodes)
    _write_csv(output / "inventory_arm_outcomes.csv", arm_rows)
    _write_csv(output / "instrument_topology_evidence.csv", instrument_evidence)
    payload["outputs"] = {
        "report_json": str(output / "report.json"),
        "inventory_episodes_csv": str(output / "inventory_episodes.csv"),
        "inventory_arm_outcomes_csv": str(output / "inventory_arm_outcomes.csv"),
        "instrument_topology_evidence_csv": str(output / "instrument_topology_evidence.csv"),
    }
    (output / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser()
    out.add_argument("--config", default=str(DEFAULT_CONFIG))
    out.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    out.add_argument("--db", default=str(PRODUCTION.canonical_db_path))
    out.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT))
    out.add_argument("--book-root", action="append", default=None)
    out.add_argument("--run-id")
    out.add_argument("--output-dir")
    return out


def main() -> int:
    args = parser().parse_args()
    if args.book_root is None:
        args.book_root = [str(path) for path in DEFAULT_BOOK_ROOTS]
    payload = run(args)
    print(
        json.dumps(
            {
                "denominator": payload["denominator"],
                "gates": payload["gates"],
                "decision": payload["decision"],
                "outputs": payload["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
