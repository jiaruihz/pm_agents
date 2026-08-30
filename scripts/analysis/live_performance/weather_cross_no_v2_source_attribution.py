#!/usr/bin/env python3
"""Read-only, source-arm attribution for the Cross NO V2 METAR probe.

Raw journals answer funnel and execution questions.  Canonical ``fact_trades``
is joined only by raw order id for fee/settled-PnL; this program never derives
realized PnL itself.  A race is an economic cross and is attributed to one
winner only, so a cross-source race cannot duplicate realized results.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ARMS = ("metar_ws_metar", "metar_ws_hfmetar", "metar_ws_datis")
TOPICS = {
    "metar_ws_metar": "metar.obs.<icao>",
    "metar_ws_hfmetar": "metar.obs10.<icao>",
    "metar_ws_datis": "metar.atis.<icao>",
}
CHECKPOINTS = (0, 15, 30, 60, 120, 300)
POLICY_MAX_NO_ASK = 0.97


def num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def moment(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    """Read a stable byte snapshot; malformed append tails are reported, not fatal."""
    for path in paths:
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                while handle.tell() < size:
                    raw = handle.readline()
                    if not raw:
                        break
                    try:
                        item = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(item, dict):
                        yield item
        except FileNotFoundError:
            continue


def journal_paths(root: Path, name: str) -> list[Path]:
    return sorted(path for path in root.rglob(name) if path.is_file())


def source_of(row: Mapping[str, Any]) -> str | None:
    source = row.get("attribution_source_arm") or row.get("source")
    if source in ARMS:
        return str(source)
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        source = metadata.get("source_arm") or metadata.get("source")
    return str(source) if source in ARMS else None


def race_id(row: Mapping[str, Any]) -> str:
    return str(row.get("economic_cross_id") or row.get("execution_race_key") or "")


def order_id(row: Mapping[str, Any]) -> str:
    response = row.get("exchange_response")
    place = response.get("place") if isinstance(response, Mapping) else None
    return str(row.get("order_id") or (response.get("order_id") if isinstance(response, Mapping) else "") or (place.get("orderID") if isinstance(place, Mapping) else "") or "")


def event_key(row: Mapping[str, Any]) -> str:
    source = str(source_of(row) or row.get("source") or "")
    event = str(row.get("information_event_id") or "")
    if source and event:
        return f"{source}|{event}"
    return "|".join(str(row.get(k) or "") for k in ("source", "city", "target_date", "raw_report_id", "created_at_utc"))


def attribution_time(row: Mapping[str, Any]) -> tuple[int, str, str]:
    mono = num(row.get("reserved_at_monotonic_ns") or row.get("live_attempted_monotonic_ns"))
    wall = str(row.get("reserved_at_utc") or row.get("live_attempted_at_utc") or row.get("created_at_utc") or "")
    return (int(mono) if mono is not None else 2**63 - 1, wall, event_key(row))


def unique(items: Iterable[Mapping[str, Any]], key) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in items:
        row = dict(raw); value = key(row)
        if not value:
            continue
        prior = out.get(value)
        if prior is None or str(row.get("created_at_utc") or row.get("reserved_at_utc") or "") < str(prior.get("created_at_utc") or prior.get("reserved_at_utc") or ""):
            out[value] = row
    return list(out.values())


def count_sum(items: Iterable[Mapping[str, Any]], field: str) -> dict[str, float | int]:
    material = list(items)
    values = [num(row.get(field)) for row in material]
    return {"count": len(material), "sum": round(sum(value for value in values if value is not None), 6), "missing_value_rows": sum(value is None for value in values)}


def blank_arm(source: str) -> dict[str, Any]:
    return {
        "source_arm": source,
        "source_topic_template": TOPICS[source],
        "observed_source_topics": [],
        "fixed_denominator_events": 0, "crosses": 0, "candidates": 0,
        "five_share_executable": 0, "race_winner": 0, "race_later_blocked": 0,
        "attempts": 0, "orders": 0, "fills": 0,
        "actual_fill_shares": {"count": 0, "sum": 0.0, "missing_value_rows": 0},
        "actual_fill_cost_usd": {"count": 0, "sum": 0.0, "missing_value_rows": 0},
        "canonical": {"matched_fill_rows": 0, "settled_fill_rows": 0, "unsettled_fill_rows": 0, "fees_usd": 0.0, "realized_pnl_usd": 0.0, "unmatched_raw_order_ids": 0},
        "checkpoint_coverage": {str(second): {"demand_count": 0, "observed_count": 0, "coverage": None} for second in CHECKPOINTS},
        "exploratory_fee_adjusted_liquidation_markout": {
            "label": "exploratory_public_bid_liquidation_not_realized_pnl",
            "fee_basis": "canonical_entry_fee_when_available_else_recorded_expected_entry_fee_plus_weather_curve_exit_fee",
            "by_checkpoint": {
                str(second): {
                    "fill_rows": 0,
                    "marked_rows": 0,
                    "markout_usd": 0.0,
                    "missing_depth_or_cost_rows": 0,
                }
                for second in CHECKPOINTS
            },
        },
        "blockers": {},
    }


def canonical(db: Path | None, ids: set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if not db:
        return {}, {"status": "not_requested"}
    if not db.exists():
        return {}, {"status": "missing", "path": str(db)}
    if not ids:
        return {}, {"status": "no_raw_order_ids", "path": str(db)}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        for start in range(0, len(ids), 500):
            batch = sorted(ids)[start:start + 500]
            query = "SELECT order_id, fill_id, settlement_status, fees_usd, pnl_usd_at_fill, fill_qty, cost_usd, val_bid, val_snapshot_ts_utc FROM fact_trades WHERE order_id IN (%s)" % ",".join("?" * len(batch))
            for row in conn.execute(query, batch):
                result[str(row["order_id"])].append(dict(row))
        max_built = conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
        return result, {"status": "ok", "path": str(db.resolve()), "max_fact_built_at_utc": max_built}
    except sqlite3.Error as exc:
        return {}, {"status": "query_error", "path": str(db), "error": str(exc)}
    finally:
        conn.close()


def checkpoint_seconds(row: Mapping[str, Any]) -> int | None:
    value = row.get("checkpoint_seconds") or row.get("requested_checkpoint_seconds")
    if value is None and isinstance(row.get("checkpoint_ref"), Mapping):
        value = (
            row["checkpoint_ref"].get("offset_seconds")
            if row["checkpoint_ref"].get("offset_seconds") is not None
            else row["checkpoint_ref"].get("seconds")
        )
        if value is None:
            value = row["checkpoint_ref"].get("checkpoint_seconds")
    value = num(value)
    return int(value) if value is not None and int(value) in CHECKPOINTS else None


def demand_id(row: Mapping[str, Any]) -> str:
    ref = row.get("checkpoint_ref")
    return str((ref.get("demand_id") if isinstance(ref, Mapping) else "") or row.get("demand_id") or "")


def best_bid(row: Mapping[str, Any]) -> float | None:
    candidates = [row.get("best_bid")]
    book = row.get("book") or row.get("public_book")
    if isinstance(book, Mapping):
        summary = book.get("summary")
        if isinstance(summary, Mapping): candidates.append(summary.get("best_bid"))
    return next((value for value in (num(item) for item in candidates) if value is not None), None)


def five_share_sell_proceeds(row: Mapping[str, Any]) -> float | None:
    for sweep in row.get("sweeps") or ():
        if not isinstance(sweep, Mapping) or num(sweep.get("shares")) != 5.0:
            continue
        proceeds = num(sweep.get("sell_proceeds"))
        if proceeds is not None:
            return proceeds
    bid = best_bid(row)
    return None if bid is None else 5.0 * bid


def expected_weather_fee(shares: float, price: float) -> float:
    return round(float(shares) * 0.05 * float(price) * (1.0 - float(price)), 5)


def build_report(output_dir: Path, *, market_books_root: Path | None = None, canonical_db: Path | None = None) -> dict[str, Any]:
    names = ("opportunities", "intents", "execution_attempts", "orders", "fills", "capture_demands")
    raw = {name: list(rows(journal_paths(output_dir, name + ".jsonl"))) for name in names}
    arm = {source: blank_arm(source) for source in ARMS}
    opportunities = unique((r for r in raw["opportunities"] if source_of(r)), event_key)
    source_by_race: dict[str, str] = {}
    for row in opportunities:
        source = source_of(row); assert source
        current = arm[source]
        observed_topic = str(row.get("attribution_source_topic") or row.get("source_topic") or "")
        if observed_topic and observed_topic not in current["observed_source_topics"]:
            current["observed_source_topics"].append(observed_topic)
        current["fixed_denominator_events"] += 1
        blockers = row.get("blockers") or []
        if isinstance(blockers, list): current["blockers"] = dict(Counter(map(str, blockers)) + Counter(current["blockers"]))
        crossed = row.get("previous_official_bracket") not in (None, "") and row.get("new_source_bracket") not in (None, "") and str(row.get("previous_official_bracket")) != str(row.get("new_source_bracket"))
        if crossed: current["crosses"] += 1
        if row.get("status") == "candidate": current["candidates"] += 1
        worst_ask = num(row.get("worst_ask_for_five_shares"))
        executable = (
            bool(row.get("book_full_depth_valid"))
            and num(row.get("expected_five_share_vwap")) is not None
            and worst_ask is not None
            and worst_ask <= POLICY_MAX_NO_ASK
        )
        if executable: current["five_share_executable"] += 1
    # Only a durable reservation/execution claim owns actual economics.  A
    # merely executable opportunity is not a winner and never receives PnL.
    attempts = unique(
        (r for r in raw["execution_attempts"] if source_of(r)),
        lambda r: f"{source_of(r)}|{r.get('execution_attempt_id') or race_id(r) or event_key(r)}",
    )
    claims_by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        if race_id(row):
            claims_by_race[race_id(row)].append(row)
    race_conflicts: list[dict[str, Any]] = []
    for race, claims in claims_by_race.items():
        claims.sort(key=attribution_time)
        winner = source_of(claims[0])
        if winner:
            source_by_race[race] = winner
        sources = sorted({str(source_of(row)) for row in claims if source_of(row)})
        if len(sources) > 1:
            race_conflicts.append({
                "economic_cross_id": race,
                "winner_source_arm": winner,
                "claiming_source_arms": sources,
                "claim_count": len(claims),
                "status": "conflict_earliest_durable_reservation_wins_no_duplicate_pnl",
            })
    for race, source in source_by_race.items(): arm[source]["race_winner"] += 1
    for row in opportunities:
        race, source = race_id(row), source_of(row)
        if (
            race
            and source
            and source_by_race.get(race) != source
            and (
                row.get("attribution_role") == "later_race_blocked"
                or "cross_source_race_already_executed" in (row.get("blockers") or [])
            )
        ):
            arm[source]["race_later_blocked"] += 1
    for row in attempts:
        source = source_of(row)
        if source: arm[source]["attempts"] += 1
    orders = unique((r for r in raw["orders"] if source_of(r)), lambda r: order_id(r) or str(r.get("execution_attempt_id") or event_key(r)))
    fills = unique((r for r in raw["fills"] if source_of(r)), lambda r: order_id(r) or str(r.get("execution_attempt_id") or event_key(r)))
    # Some runner versions only put actual fill evidence on the order record.
    seen_fill_ids = {order_id(r) or str(r.get("execution_attempt_id") or "") for r in fills}
    fills.extend(r for r in orders if num(r.get("actual_fill_shares")) is not None and (order_id(r) or str(r.get("execution_attempt_id") or "")) not in seen_fill_ids)
    for row in orders:
        source = source_of(row)
        if source: arm[source]["orders"] += 1
    for row in fills:
        source = source_of(row)
        if source:
            arm[source]["fills"] += 1
    # recompute fill aggregates once, retaining zero/missing evidence rows.
    for source in ARMS:
        own = [r for r in fills if source_of(r) == source]
        arm[source]["actual_fill_shares"] = count_sum(own, "actual_fill_shares")
        arm[source]["actual_fill_cost_usd"] = count_sum(own, "actual_fill_cost_usd")
    facts, db_status = canonical(canonical_db, {order_id(r) for r in fills if order_id(r)})
    # Canonical economics follows the race winner, not merely a potentially
    # duplicated raw fill row's source label.
    for source in ARMS:
        own = [
            r for r in fills
            if source_by_race.get(race_id(r), source_of(r)) == source
        ]
        for row in own:
            oid = order_id(row); matched = facts.get(oid, [])
            if oid and not matched: arm[source]["canonical"]["unmatched_raw_order_ids"] += 1
            for fact in matched:
                arm[source]["canonical"]["matched_fill_rows"] += 1
                if fact.get("settlement_status") == "settled":
                    arm[source]["canonical"]["settled_fill_rows"] += 1
                    arm[source]["canonical"]["realized_pnl_usd"] += num(fact.get("pnl_usd_at_fill")) or 0.0
                else: arm[source]["canonical"]["unsettled_fill_rows"] += 1
                arm[source]["canonical"]["fees_usd"] += num(fact.get("fees_usd")) or 0.0
        arm[source]["canonical"]["fees_usd"] = round(arm[source]["canonical"]["fees_usd"], 6)
        arm[source]["canonical"]["realized_pnl_usd"] = round(arm[source]["canonical"]["realized_pnl_usd"], 6)
    demands = unique((r for r in raw["capture_demands"] if source_of(r)), lambda r: str(r.get("demand_id") or ""))
    evidence_paths = (
        [path for path in market_books_root.rglob("public_books*.jsonl") if path.is_file()]
        + [path for path in market_books_root.rglob("execution_evidence_v1.jsonl") if path.is_file()]
        if market_books_root
        else []
    )
    evidence_rows = list(rows(sorted(set(evidence_paths))))
    observed: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for evidence in evidence_rows:
        did, sec = demand_id(evidence), checkpoint_seconds(evidence)
        if did and sec is not None and (evidence.get("requested_strategy_checkpoint") is True or isinstance(evidence.get("checkpoint_ref"), Mapping)):
            observed[(did, sec)].append(evidence)
    for source in ARMS:
        own_demands = [r for r in demands if source_of(r) == source and r.get("demand_id")]
        for sec in CHECKPOINTS:
            requested = [
                str(row["demand_id"])
                for row in own_demands
                if sec in tuple(int(value) for value in row.get("requested_checkpoints_seconds") or CHECKPOINTS)
            ]
            hit = sum(bool(observed[(did, sec)]) for did in requested)
            arm[source]["checkpoint_coverage"][str(sec)] = {
                "demand_count": len(requested),
                "observed_count": hit,
                "coverage": round(hit / len(requested), 6) if requested else None,
            }
    # Markout is explicitly exploratory and is never substituted for settlement PnL.
    for source in ARMS:
        for fill in [r for r in fills if source_of(r) == source]:
            oid = order_id(fill)
            shares, cost = num(fill.get("actual_fill_shares")), num(fill.get("actual_fill_cost_usd"))
            fill_race = race_id(fill)
            fill_event = str(fill.get("information_event_id") or "")
            fill_token = str(fill.get("token_id") or "")
            related_demands = [
                demand
                for demand in demands
                if source_of(demand) == source
                and (
                    not fill_token
                    or str(demand.get("token_id") or "") == fill_token
                )
                and (
                    (fill_race and str((demand.get("metadata") or {}).get("economic_cross_id") or "") == fill_race)
                    or (fill_event and str(demand.get("trigger_event_id") or "") == fill_event)
                )
            ]
            entry_fee = sum(num(f.get("fees_usd")) or 0.0 for f in facts.get(oid, []))
            if not facts.get(oid):
                entry_fee = num(fill.get("expected_taker_fee_usd")) or 0.0
            for sec in CHECKPOINTS:
                marker = arm[source]["exploratory_fee_adjusted_liquidation_markout"]["by_checkpoint"][str(sec)]
                marker["fill_rows"] += 1
                evidence = [
                    item
                    for demand in related_demands
                    for item in observed[(str(demand.get("demand_id") or ""), sec)]
                ]
                evidence.sort(key=lambda item: str(item.get("book_observed_at_utc") or ""))
                proceeds = five_share_sell_proceeds(evidence[-1]) if evidence else None
                if shares != 5.0 or cost is None or proceeds is None:
                    marker["missing_depth_or_cost_rows"] += 1
                    continue
                exit_price = proceeds / shares
                marker["marked_rows"] += 1
                marker["markout_usd"] += proceeds - cost - entry_fee - expected_weather_fee(shares, exit_price)
        for marker in arm[source]["exploratory_fee_adjusted_liquidation_markout"]["by_checkpoint"].values():
            marker["markout_usd"] = round(marker["markout_usd"], 6)
    for source in ARMS:
        arm[source]["observed_source_topics"].sort()
    return {
        "schema_version": "weather_cross_no_v2_source_attribution_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "denominator_scope": "unique source-arm + information_event_id in supplied Cross NO V2 output journals",
        "policy_max_no_ask": POLICY_MAX_NO_ASK,
        "race_attribution_contract": "earliest durable reservation wins; opportunities never own actual PnL",
        "race_attribution_conflicts": race_conflicts,
        "arms": arm,
        "input_rows": {name: len(value) for name, value in raw.items()},
        "canonical_db": db_status,
        "coverage_blockers": {
            "market_books": "not_requested" if market_books_root is None else ("no_checkpoint_evidence" if not evidence_rows else "available"),
            "canonical": db_status["status"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Cross NO V2 source attribution JSON reporter")
    parser.add_argument("--output-dir", type=Path, required=True, help="Cross NO V2 output directory; JSONL inputs are read recursively")
    parser.add_argument("--market-books-root", type=Path, help="optional market-books root for requested checkpoint evidence")
    parser.add_argument("--canonical-db", type=Path, help="optional canonical weather.db opened read-only")
    parser.add_argument("--output", type=Path, help="optional JSON output path; default stdout")
    args = parser.parse_args()
    report = build_report(args.output_dir, market_books_root=args.market_books_root, canonical_db=args.canonical_db)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(rendered)
    else: sys.stdout.write(rendered)


if __name__ == "__main__": main()
