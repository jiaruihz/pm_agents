#!/usr/bin/env python3
"""Test one fixed +60..+72 minute taker exit on two Tmax entry families.

The policy is intentionally not tuned here.  It is migrated from the Tmin
previous-NO repricing challenger: buy at an executable ask, then sell the same
quantity at the first fresh executable bid from minute 60 through minute 72.
Both legs pay the official Weather taker fee.  Research only; no orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_core_carry_post_entry_capture_v1 import (  # noqa: E402
    load_books,
    walk_sell_ladder,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402


SCHEMA_VERSION = "tmax_fixed_60m_taker_exit_v1"
POLICY_ID = "tmax_fixed_first_executable_exit60m_v1"
FEE_RATE = 0.05
FAST_SOURCE_SHARES = 5.0
CORE_ALLOWED_QUANTITIES = {5.0, 10.0}
EXIT_START_MINUTES = 60
EXIT_END_MINUTES = 72
MAX_QUOTE_AGE_SECONDS = 300.0
EARLY_END_TARGET_DATE = "2026-08-12"
LATE_START_TARGET_DATE = "2026-08-13"
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260828
VALID_FAST_EVENT_SCHEMAS = {
    "fast_source_stale_book_event_v2",
    "fast_source_stale_book_event_v3",
}


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def weather_fee_per_share(price: float) -> float:
    if not math.isfinite(price) or not 0.0 <= price <= 1.0:
        raise ValueError(f"price must be within [0, 1], got {price!r}")
    return round(FEE_RATE * price * (1.0 - price), 5)


def snapshot_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_size = path.stat().st_size
    with path.open("rb") as handle:
        payload = handle.read(source_size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    for raw in complete.splitlines():
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parse_errors += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, {
        "path": str(path),
        "source_size_at_read_bytes": source_size,
        "complete_size_bytes": complete_size,
        "complete_sha256": hashlib.sha256(complete).hexdigest(),
        "rows": len(rows),
        "parse_errors": parse_errors,
    }


@dataclass(frozen=True)
class SettlementIndex:
    by_condition: Mapping[str, float]
    by_city_date_bracket: Mapping[tuple[str, str, str], float]
    snapshot: Mapping[str, Any]

    def payoff(
        self,
        *,
        condition_id: str,
        city: str,
        target_date: str,
        bracket: str,
        side: str,
    ) -> float | None:
        yes = self.by_condition.get(condition_id) if condition_id else None
        if yes is None:
            yes = self.by_city_date_bracket.get((city, target_date, bracket))
        if yes is None or float(yes) not in {0.0, 1.0}:
            return None
        return float(yes if side == "yes" else 1.0 - yes)


def load_tmax_settlements(db_path: Path) -> SettlementIndex:
    stat = db_path.stat()
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    rows = connection.execute(
        """
        SELECT city, target_date, bracket, condition_id, final_price,
               created_at_utc, source_path
        FROM settlement_outcomes
        WHERE settlement_status='settled'
          AND (
            lower(COALESCE(question, '')) LIKE '%highest temperature%'
            OR (
              COALESCE(source_path, '') LIKE '%/pm_history/%'
              AND COALESCE(source_path, '') NOT LIKE '%pm_history_lowest%'
            )
          )
        ORDER BY created_at_utc
        """
    ).fetchall()
    fact = connection.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*) FROM fact_trades"
    ).fetchone()
    connection.close()
    by_condition: dict[str, float] = {}
    by_key: dict[tuple[str, str, str], float] = {}
    for city, target_date, bracket, condition_id, final_price, _created, _source in rows:
        value = finite(final_price)
        if value not in {0.0, 1.0}:
            continue
        key = (str(city), str(target_date), str(bracket))
        by_key[key] = value
        if condition_id:
            by_condition[str(condition_id)] = value
    return SettlementIndex(
        by_condition=by_condition,
        by_city_date_bracket=by_key,
        snapshot={
            "path": str(db_path.resolve()),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "tmax_rows": len(rows),
            "tmax_city_date_brackets": len(by_key),
            "tmax_condition_ids": len(by_condition),
            "target_date_min": min((key[1] for key in by_key), default=None),
            "target_date_max": max((key[1] for key in by_key), default=None),
            "fact_trades_built_at_utc": fact[0],
            "fact_trades_rows": int(fact[1] or 0),
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )


def nested_quote(row: Mapping[str, Any], expression: str, side: str) -> Mapping[str, Any]:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), Mapping) else {}
    expression_row = quotes.get(expression) if isinstance(quotes, Mapping) else {}
    if not isinstance(expression_row, Mapping):
        return {}
    output = expression_row.get(side)
    return output if isinstance(output, Mapping) else {}


def fresh_top(
    row: Mapping[str, Any], *, expression: str, side: str, action: str, min_shares: float
) -> dict[str, Any] | None:
    quote = nested_quote(row, expression, side)
    row_ts = parse_utc(row.get("ts_utc"))
    fetched = parse_utc(quote.get("fresh_fetched_at_utc"))
    price_key = "fresh_best_ask" if action == "buy" else "fresh_best_bid"
    size_key = "fresh_ask_size" if action == "buy" else "fresh_bid_size"
    price = finite(quote.get(price_key))
    size = finite(quote.get(size_key))
    if row_ts is None or fetched is None or str(quote.get("fresh_status") or "") != "ok":
        return None
    age = (row_ts - fetched).total_seconds()
    if (
        price is None
        or not 0.0 < price < 1.0
        or size is None
        or size < min_shares
        or not 0.0 <= age <= MAX_QUOTE_AGE_SECONDS
    ):
        return None
    return {
        "ts": row_ts,
        "fetched_at": fetched,
        "age_seconds": age,
        "price": price,
        "size": size,
        "token_id": str(quote.get("token_id") or ""),
        "condition_id": str(quote.get("condition_id") or ""),
        "bracket": str(quote.get("bracket") or ""),
    }


def evaluate_fast_source(
    events: Sequence[Mapping[str, Any]],
    quotes: Sequence[Mapping[str, Any]],
    settlements: SettlementIndex,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    quote_tape: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for quote in quotes:
        event_key = str(quote.get("event_key") or "")
        if event_key and parse_utc(quote.get("ts_utc")) is not None:
            quote_tape[event_key].append(quote)
    for tape in quote_tape.values():
        tape.sort(key=lambda row: parse_utc(row.get("ts_utc")) or datetime.max.replace(tzinfo=timezone.utc))

    candidate_rows: list[dict[str, Any]] = []
    schema_counts: dict[str, int] = defaultdict(int)
    for event in events:
        schema = str(event.get("schema_version") or "")
        schema_counts[schema] += 1
        if schema not in VALID_FAST_EVENT_SCHEMAS or str(event.get("extreme_kind") or "") != "max":
            continue
        created = parse_utc(event.get("created_at_utc"))
        event_key = str(event.get("event_key") or "")
        previous = str(
            event.get("previous_market_bracket")
            or event.get("t_minus_1_no_bracket_c")
            or event.get("previous_no_bracket_c")
            or ""
        )
        if created is None or not event_key or not previous:
            continue
        entry = next(
            (
                top
                for row in quote_tape.get(event_key, [])
                if (row_ts := parse_utc(row.get("ts_utc"))) is not None
                and row_ts >= created
                and (top := fresh_top(
                    row,
                    expression="t_minus_1",
                    side="no",
                    action="buy",
                    min_shares=FAST_SOURCE_SHARES,
                ))
                is not None
                and top["token_id"]
                and (not top["bracket"] or top["bracket"] == previous)
            ),
            None,
        )
        if entry is None:
            continue
        candidate_rows.append(
            {
                "family": "fast_source_previous_no",
                "event_key": event_key,
                "event_schema": schema,
                "city": str(event.get("city") or ""),
                "target_date": str(event.get("target_date") or ""),
                "source": str(event.get("source") or ""),
                "source_basis_class": str(event.get("source_basis_class") or ""),
                "bracket": previous,
                "condition_id": entry["condition_id"],
                "token_id": entry["token_id"],
                "quantity": FAST_SOURCE_SHARES,
                "entry_ts": entry["ts"],
                "entry_price": entry["price"],
                "entry_top_size": entry["size"],
                "entry_quote_age_seconds": entry["age_seconds"],
            }
        )
    candidates = pd.DataFrame(candidate_rows)
    if candidates.empty:
        return candidates, {
            "raw_events": len(events),
            "raw_quotes": len(quotes),
            "schema_counts": dict(schema_counts),
            "valid_schema_events": sum(schema_counts.get(schema, 0) for schema in VALID_FAST_EVENT_SCHEMAS),
            "entry_executable_events": 0,
            "selected_city_dates": 0,
        }
    candidates.sort_values(["entry_ts", "event_key"], inplace=True)
    selected = candidates.drop_duplicates(["city", "target_date"], keep="first").copy()
    output: list[dict[str, Any]] = []
    for row in selected.to_dict("records"):
        start = row["entry_ts"] + timedelta(minutes=EXIT_START_MINUTES)
        end = row["entry_ts"] + timedelta(minutes=EXIT_END_MINUTES)
        exit_quote = next(
            (
                top
                for quote in quote_tape.get(row["event_key"], [])
                if (quote_ts := parse_utc(quote.get("ts_utc"))) is not None
                and start <= quote_ts <= end
                and (top := fresh_top(
                    quote,
                    expression="t_minus_1",
                    side="no",
                    action="sell",
                    min_shares=float(row["quantity"]),
                ))
                is not None
                and top["token_id"] == row["token_id"]
            ),
            None,
        )
        quantity = float(row["quantity"])
        entry_price = float(row["entry_price"])
        entry_fee = quantity * weather_fee_per_share(entry_price)
        entry_principal = quantity * entry_price
        entry_cost = entry_principal + entry_fee
        payoff = settlements.payoff(
            condition_id=str(row["condition_id"]),
            city=str(row["city"]),
            target_date=str(row["target_date"]),
            bracket=str(row["bracket"]),
            side="no",
        )
        result = {
            **row,
            "entry_principal_usd": entry_principal,
            "entry_fee_usd": entry_fee,
            "entry_cost_usd": entry_cost,
            "settlement_payoff": payoff,
            "hold_pnl_usd": None if payoff is None else quantity * payoff - entry_cost,
            "exit_covered": exit_quote is not None,
            "exit_ts": None if exit_quote is None else exit_quote["ts"],
            "exit_price": None if exit_quote is None else exit_quote["price"],
            "exit_top_size": None if exit_quote is None else exit_quote["size"],
            "exit_quote_age_seconds": None if exit_quote is None else exit_quote["age_seconds"],
            "exit_principal_usd": None,
            "exit_fee_usd": None,
            "exit_net_proceeds_usd": None,
            "round_trip_pnl_usd": None,
            "delta_vs_hold_usd": None,
        }
        if exit_quote is not None:
            exit_price = float(exit_quote["price"])
            exit_principal = quantity * exit_price
            exit_fee = quantity * weather_fee_per_share(exit_price)
            net = exit_principal - exit_fee
            result.update(
                {
                    "exit_principal_usd": exit_principal,
                    "exit_fee_usd": exit_fee,
                    "exit_net_proceeds_usd": net,
                    "round_trip_pnl_usd": net - entry_cost,
                    "delta_vs_hold_usd": (
                        None if payoff is None else net - quantity * payoff
                    ),
                }
            )
        output.append(result)
    return pd.DataFrame(output), {
        "raw_events": len(events),
        "raw_quotes": len(quotes),
        "schema_counts": dict(schema_counts),
        "valid_schema_events": sum(schema_counts.get(schema, 0) for schema in VALID_FAST_EVENT_SCHEMAS),
        "entry_executable_events": len(candidates),
        "selected_city_dates": len(selected),
    }


def evaluate_core_entries(
    entries: Sequence[Mapping[str, Any]],
    books: Mapping[str, Sequence[Mapping[str, Any]]],
    settlements: SettlementIndex,
) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    seen_city_dates: set[tuple[str, str]] = set()
    ordered = sorted(
        entries,
        key=lambda row: parse_utc(
            row.get("current_yes_book_fetched_at_utc") or row.get("created_at_utc")
        )
        or datetime.max.replace(tzinfo=timezone.utc),
    )
    for entry in ordered:
        if entry.get("would_submit_after_family_dedupe") is not True:
            continue
        city = str(entry.get("city") or "")
        target_date = str(entry.get("target_date") or "")
        city_date = (city, target_date)
        if not city or not target_date or city_date in seen_city_dates:
            continue
        ladder = entry.get("taker_ladder") if isinstance(entry.get("taker_ladder"), Mapping) else {}
        quantity = finite(ladder.get("quantity"))
        entry_cost_per_share = finite(ladder.get("effective_cost_per_share"))
        entry_principal = finite(ladder.get("principal"))
        entry_fee = finite(ladder.get("fee"))
        entry_ts = parse_utc(
            entry.get("current_yes_book_fetched_at_utc") or entry.get("created_at_utc")
        )
        token_id = str(entry.get("current_yes_token_id") or "")
        if (
            ladder.get("executable") is not True
            or quantity is None
            or quantity not in CORE_ALLOWED_QUANTITIES
            or entry_cost_per_share is None
            or entry_ts is None
            or not token_id
        ):
            continue
        seen_city_dates.add(city_date)
        entry_cost = quantity * entry_cost_per_share
        start = entry_ts + timedelta(minutes=EXIT_START_MINUTES)
        end = entry_ts + timedelta(minutes=EXIT_END_MINUTES)
        chosen: dict[str, Any] | None = None
        for book in books.get(token_id, []):
            available = parse_utc(book.get("available_at_utc") or book.get("snapshot_ts_utc"))
            snapshot = parse_utc(book.get("snapshot_ts_utc"))
            if available is None or snapshot is None or not start <= available <= end:
                continue
            age = (available - snapshot).total_seconds()
            if not 0.0 <= age <= MAX_QUOTE_AGE_SECONDS:
                continue
            sell = walk_sell_ladder(book.get("bids") or [], quantity)
            if sell["executable"]:
                chosen = {"book": book, "sell": sell, "available": available, "age": age}
                break
        condition_id = str(entry.get("current_condition_id") or "")
        bracket = str(entry.get("current_bracket") or "")
        payoff = settlements.payoff(
            condition_id=condition_id,
            city=city,
            target_date=target_date,
            bracket=bracket,
            side="yes",
        )
        net = None if chosen is None else finite(chosen["sell"].get("principal"))
        exit_fee = None if chosen is None else finite(chosen["sell"].get("fee"))
        if net is not None and exit_fee is not None:
            net -= exit_fee
        output.append(
            {
                "family": "normal_core_carry_current_yes",
                "event_key": str(entry.get("shadow_decision_id") or entry.get("checkpoint_key") or ""),
                "event_schema": str(entry.get("feature_schema_version") or ""),
                "city": city,
                "target_date": target_date,
                "source": str(entry.get("obs_source") or ""),
                "source_basis_class": "official_or_normal_core_input",
                "bracket": bracket,
                "condition_id": condition_id,
                "token_id": token_id,
                "quantity": quantity,
                "entry_ts": entry_ts,
                "entry_price": finite(ladder.get("principal_vwap")),
                "entry_top_size": finite(ladder.get("available_shares")),
                "entry_quote_age_seconds": (
                    None
                    if parse_utc(entry.get("decision_snapshot_ts_utc")) is None
                    else (entry_ts - parse_utc(entry.get("decision_snapshot_ts_utc"))).total_seconds()
                ),
                "entry_principal_usd": entry_principal,
                "entry_fee_usd": entry_fee,
                "entry_cost_usd": entry_cost,
                "settlement_payoff": payoff,
                "hold_pnl_usd": None if payoff is None else quantity * payoff - entry_cost,
                "exit_covered": chosen is not None,
                "exit_ts": None if chosen is None else chosen["available"],
                "exit_price": None if chosen is None else chosen["sell"].get("principal_vwap"),
                "exit_top_size": None if chosen is None else chosen["sell"].get("quantity"),
                "exit_quote_age_seconds": None if chosen is None else chosen["age"],
                "exit_principal_usd": None if chosen is None else chosen["sell"].get("principal"),
                "exit_fee_usd": exit_fee,
                "exit_net_proceeds_usd": net,
                "round_trip_pnl_usd": None if net is None else net - entry_cost,
                "delta_vs_hold_usd": (
                    None if net is None or payoff is None else net - quantity * payoff
                ),
                "exit_source_path": None if chosen is None else chosen["book"].get("source_path"),
            }
        )
    return pd.DataFrame(output)


def bootstrap_ratio(
    frame: pd.DataFrame, *, numerator: str, denominator: str, seed_offset: int = 0
) -> dict[str, Any]:
    usable = frame[frame[numerator].notna() & frame[denominator].notna()].copy()
    if usable.empty or float(usable[denominator].sum()) <= 0:
        return {"ratio": None, "ci_low": None, "ci_high": None, "target_dates": 0}
    daily = usable.groupby("target_date", sort=True)[[numerator, denominator]].sum()
    rng = np.random.default_rng(BOOTSTRAP_SEED + seed_offset)
    count = len(daily)
    indices = rng.integers(0, count, size=(BOOTSTRAP_DRAWS, count))
    num = daily[numerator].to_numpy()[indices].sum(axis=1)
    den = daily[denominator].to_numpy()[indices].sum(axis=1)
    draws = np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0)
    low, high = np.nanquantile(draws, [0.025, 0.975])
    return {
        "ratio": float(usable[numerator].sum() / usable[denominator].sum()),
        "ci_low": float(low),
        "ci_high": float(high),
        "target_dates": count,
        "draws": BOOTSTRAP_DRAWS,
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "signals": 0,
            "signal_target_dates": 0,
            "exit_covered": 0,
            "exit_target_dates": 0,
        }
    covered = frame[frame["exit_covered"] & frame["round_trip_pnl_usd"].notna()].copy()
    settled = covered[covered["settlement_payoff"].notna()].copy()
    round_trip = bootstrap_ratio(
        covered, numerator="round_trip_pnl_usd", denominator="entry_cost_usd"
    )
    settled_round_trip = bootstrap_ratio(
        settled,
        numerator="round_trip_pnl_usd",
        denominator="entry_cost_usd",
        seed_offset=3,
    )
    hold = bootstrap_ratio(settled, numerator="hold_pnl_usd", denominator="entry_cost_usd", seed_offset=1)
    delta = bootstrap_ratio(
        settled, numerator="delta_vs_hold_usd", denominator="entry_cost_usd", seed_offset=2
    )
    top_share = None
    leave_top_roi = None
    if not covered.empty:
        top_index = covered["round_trip_pnl_usd"].abs().idxmax()
        total = float(covered["round_trip_pnl_usd"].sum())
        top_value = float(covered.loc[top_index, "round_trip_pnl_usd"])
        top_share = None if abs(total) < 1e-12 else top_value / total
        without = covered.drop(index=top_index)
        if not without.empty and float(without["entry_cost_usd"].sum()) > 0:
            leave_top_roi = float(without["round_trip_pnl_usd"].sum() / without["entry_cost_usd"].sum())
    return {
        "signals": int(len(frame)),
        "signal_target_dates": int(frame["target_date"].nunique()),
        "signal_city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0]),
        "exit_covered": int(len(covered)),
        "exit_target_dates": int(covered["target_date"].nunique()),
        "exit_coverage_rate": float(len(covered) / len(frame)),
        "settled_exit_rows": int(len(settled)),
        "positive_round_trips": int(covered["round_trip_pnl_usd"].gt(0).sum()),
        "negative_round_trips": int(covered["round_trip_pnl_usd"].lt(0).sum()),
        "entry_principal_usd": float(covered["entry_principal_usd"].sum()),
        "entry_fee_usd": float(covered["entry_fee_usd"].sum()),
        "entry_cash_cost_usd": float(covered["entry_cost_usd"].sum()),
        "exit_principal_usd": float(covered["exit_principal_usd"].sum()),
        "exit_fee_usd": float(covered["exit_fee_usd"].sum()),
        "exit_net_proceeds_usd": float(covered["exit_net_proceeds_usd"].sum()),
        "gross_two_leg_quote_notional_usd": float(
            covered["entry_principal_usd"].sum() + covered["exit_principal_usd"].sum()
        ),
        "round_trip_pnl_usd": float(covered["round_trip_pnl_usd"].sum()),
        "round_trip_roi": round_trip,
        "round_trip_pnl_usd_same_settled_rows": float(
            settled["round_trip_pnl_usd"].sum()
        ),
        "round_trip_roi_same_settled_rows": settled_round_trip,
        "hold_pnl_usd_same_rows": float(settled["hold_pnl_usd"].sum()),
        "hold_roi_same_rows": hold,
        "fixed_exit_minus_hold_pnl_usd": float(settled["delta_vs_hold_usd"].sum()),
        "fixed_exit_minus_hold_roi": delta,
        "largest_abs_trade_contribution_share_of_total": top_share,
        "leave_largest_abs_trade_out_roi": leave_top_roi,
    }


def split_summaries(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "all_historical": summarize(frame),
        "early_historical_development": summarize(
            frame[frame["target_date"].astype(str).le(EARLY_END_TARGET_DATE)]
        ),
        "late_secondary_historical": summarize(
            frame[frame["target_date"].astype(str).ge(LATE_START_TARGET_DATE)]
        ),
    }


def file_manifest(paths: Iterable[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw_path in sorted({path for path in paths if path}):
        path = Path(raw_path)
        if not path.exists():
            continue
        stat = path.stat()
        output.append(
            {
                "path": raw_path,
                "size_bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return output


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def markdown(payload: Mapping[str, Any]) -> str:
    fast = payload["results"]["fast_source_previous_no"]
    core = payload["results"]["normal_core_carry_current_yes"]
    lines = [
        "# Tmax fixed 60-minute taker exit v1",
        "",
        "## 结论",
        "",
        f"- research status: `{payload['research_status']}`; live change: `none`.",
        "- 固定策略：首个可执行 entry 后，从 +60:00 到 +72:00 取首个可执行 bid；entry/exit 都是 taker，缺 quote/depth 不补价。",
        "- 这轮没有重新搜索 10/30/120/240 分钟；60 分钟是从 Tmin 迁移来的唯一主检验。",
        "",
        "## 结果",
        "",
        "| family / window | signals | exits / settled | entry cash | fixed PnL, all exits | fixed PnL, same settled | same-settled ROI [95% CI] | HOLD PnL, same settled | fixed-HOLD delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family, result in (("fast source previous NO", fast), ("normal Core Carry YES", core)):
        for window, summary in result["windows"].items():
            settled_roi = summary.get("round_trip_roi_same_settled_rows", {})
            lines.append(
                f"| {family} / {window} | {summary.get('signals', 0)} | "
                f"{summary.get('exit_covered', 0)} / {summary.get('settled_exit_rows', 0)} | "
                f"${summary.get('entry_cash_cost_usd', 0):.2f} | ${summary.get('round_trip_pnl_usd', 0):+.2f} | "
                f"${summary.get('round_trip_pnl_usd_same_settled_rows', 0):+.2f} | "
                f"{settled_roi.get('ratio') if settled_roi.get('ratio') is not None else 'NA'} "
                f"[{settled_roi.get('ci_low') if settled_roi.get('ci_low') is not None else 'NA'}, "
                f"{settled_roi.get('ci_high') if settled_roi.get('ci_high') is not None else 'NA'}] | "
                f"${summary.get('hold_pnl_usd_same_rows', 0):+.2f} | "
                f"${summary.get('fixed_exit_minus_hold_pnl_usd', 0):+.2f} |"
            )
    lines.extend(
        [
            "",
            "## 数据与限制",
            "",
            f"- fast-source raw: {payload['inputs']['fast_events']['rows']} events / {payload['inputs']['fast_quotes']['rows']} quotes.",
            f"- Core selected entry raw: {payload['inputs']['core_entries']['rows']} rows; market-book matched files are hashed in result.json.",
            "- early/late 都是重复利用的历史窗口；late 只叫 secondary historical，不冒充 pristine frozen forward。",
            "- 所有交易都是 counterfactual executable replay；actual fills=0。",
            "",
            "## Gate",
            "",
            f"- significance={payload['gates']['significance']}; baseline={payload['gates']['baseline']}; forward={payload['gates']['forward']}; conclusion={payload['gates']['conclusion']}.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    production = load_production_spec()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast-events",
        type=Path,
        default=production.data_feed_runtime_root / "output/source_event_ladder_repricing_shadow/events.jsonl",
    )
    parser.add_argument(
        "--fast-quotes",
        type=Path,
        default=production.data_feed_runtime_root / "output/source_event_ladder_repricing_shadow/quote_snapshots.jsonl",
    )
    parser.add_argument(
        "--core-entries",
        type=Path,
        default=production.pm_runtime_root / "weather_edge_v1/current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl",
    )
    parser.add_argument(
        "--market-books-root",
        type=Path,
        default=production.resolved_market_books_root() / "batches",
    )
    parser.add_argument("--db", type=Path, default=production.canonical_db_path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit(f"output directory must be absent or empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    events, event_snapshot = snapshot_jsonl(args.fast_events)
    quotes, quote_snapshot = snapshot_jsonl(args.fast_quotes)
    core_entries, core_snapshot = snapshot_jsonl(args.core_entries)
    settlements = load_tmax_settlements(args.db)

    fast_frame, fast_funnel = evaluate_fast_source(events, quotes, settlements)
    selected_core = [row for row in core_entries if row.get("would_submit_after_family_dedupe") is True]
    if not selected_core:
        raise SystemExit("core entry snapshot has no would-submit rows")
    tokens = {str(row.get("current_yes_token_id") or "") for row in selected_core}
    tokens.discard("")
    date_min = min(str(row.get("target_date")) for row in selected_core)
    date_max = max(str(row.get("target_date")) for row in selected_core)
    core_books, book_funnel = load_books([args.market_books_root], tokens, date_min, date_max)
    core_frame = evaluate_core_entries(selected_core, core_books, settlements)

    fast_positions_path = args.output_dir / "fast_source_positions.csv"
    core_positions_path = args.output_dir / "normal_core_positions.csv"
    fast_frame.to_csv(fast_positions_path, index=False)
    core_frame.to_csv(core_positions_path, index=False)

    fast_windows = split_summaries(fast_frame)
    core_windows = split_summaries(core_frame)
    late_fast = fast_windows["late_secondary_historical"]
    late_core = core_windows["late_secondary_historical"]
    both_positive_ci = all(
        summary.get("round_trip_roi", {}).get("ci_low") is not None
        and summary["round_trip_roi"]["ci_low"] > 0
        for summary in (late_fast, late_core)
    )
    both_beat_hold = all(
        summary.get("fixed_exit_minus_hold_roi", {}).get("ci_low") is not None
        and summary["fixed_exit_minus_hold_roi"]["ci_low"] > 0
        for summary in (late_fast, late_core)
    )
    complete_exit_coverage = all(
        summary.get("signals", 0) == summary.get("exit_covered", -1)
        for summary in (late_fast, late_core)
    )
    passed = both_positive_ci and both_beat_hold and complete_exit_coverage
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": (
            "qualified_for_prospective_zero_notional_forward"
            if passed
            else "inconclusive_do_not_promote_general_fixed_taker_exit_keep_existing_hold_semantics"
        ),
        "policy_contract": {
            "entry": "family-specific fixed executable ask; first fast-source city-day event or frozen Core selected entry",
            "exit": f"first fresh executable bid in +{EXIT_START_MINUTES}..+{EXIT_END_MINUTES} minutes",
            "fees": "official Weather taker fee on entry and exit; feeRate=0.05, per-share rounded to 5 decimals",
            "fast_source_quantity": FAST_SOURCE_SHARES,
            "core_quantity": "original frozen taker_ladder quantity (5 or 10 shares)",
            "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
            "horizons_tested_here": [60],
            "actual_orders_or_fills": 0,
        },
        "split_contract": {
            "early_historical_development_end": EARLY_END_TARGET_DATE,
            "late_secondary_historical_start": LATE_START_TARGET_DATE,
            "late_is_pristine_forward": False,
        },
        "inputs": {
            "fast_events": event_snapshot,
            "fast_quotes": quote_snapshot,
            "core_entries": core_snapshot,
            "settlements": dict(settlements.snapshot),
            "market_books": book_funnel,
            "matched_exit_book_files": file_manifest(
                core_frame.get("exit_source_path", pd.Series(dtype=str)).dropna().astype(str)
            ),
        },
        "reproducibility": {
            "git_head": git_head(),
            "evaluator_path": str(Path(__file__).resolve()),
            "evaluator_sha256": sha256_file(Path(__file__).resolve()),
            "core_book_helper_path": str(
                ROOT
                / "scripts/analysis/reheat_risk/research_core_carry_post_entry_capture_v1.py"
            ),
            "core_book_helper_sha256": sha256_file(
                ROOT
                / "scripts/analysis/reheat_risk/research_core_carry_post_entry_capture_v1.py"
            ),
            "resolved_cli_inputs": {
                "fast_events": str(args.fast_events.resolve()),
                "fast_quotes": str(args.fast_quotes.resolve()),
                "core_entries": str(args.core_entries.resolve()),
                "market_books_root": str(args.market_books_root.resolve()),
                "db": str(args.db.resolve()),
                "output_dir": str(args.output_dir.resolve()),
            },
            "position_outputs": {
                "fast_source_positions.csv": sha256_file(fast_positions_path),
                "normal_core_positions.csv": sha256_file(core_positions_path),
            },
        },
        "results": {
            "fast_source_previous_no": {"funnel": fast_funnel, "windows": fast_windows},
            "normal_core_carry_current_yes": {
                "funnel": {
                    "raw_core_rows": len(core_entries),
                    "selected_entries": len(selected_core),
                    "selected_entries_invalid_quantity": sum(
                        finite(
                            (
                                row.get("taker_ladder")
                                if isinstance(row.get("taker_ladder"), Mapping)
                                else {}
                            ).get("quantity")
                        )
                        not in CORE_ALLOWED_QUANTITIES
                        for row in selected_core
                    ),
                    "tokens": len(tokens),
                    "materialized_entries": len(core_frame),
                },
                "windows": core_windows,
            },
        },
        "gates": {
            "late_both_families_positive_round_trip_ci": both_positive_ci,
            "late_both_families_beat_hold_ci": both_beat_hold,
            "late_complete_exit_coverage": complete_exit_coverage,
            "significance": "PASS" if both_positive_ci else "FAIL",
            "baseline": "PASS" if both_beat_hold else "FAIL",
            "forward": "FAIL_not_pristine_forward",
            "conclusion": "shadow_candidate" if passed else "inconclusive",
            "live_change": "none",
        },
        "limitations": [
            "The late window is reused historical data, not pristine frozen forward.",
            "Fast-source journal top depth proves quoted capacity only; it is not an actual fill.",
            "Core entries are frozen would-submit rows, not a claim that every counterfactual entry filled live.",
            "Missing executable exits remain in the evidence funnel and are never silently filtered from the signal denominator.",
        ],
    }
    (args.output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "results": payload["results"], "gates": payload["gates"]}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
