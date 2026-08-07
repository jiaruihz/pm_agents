#!/usr/bin/env python3
"""Materialize market-only tail telemetry from canonical ladder captures.

This runner is deliberately zero-notional. It emits no signal candidate,
trade intent, order, or fill. The output is a PIT feature journal used to
settle and evaluate market-implied tail residual hypotheses later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import city_local_datetime, city_timezone_name


SCHEMA_VERSION = "market_implied_tail_residual_shadow_v1"
DEFAULT_RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("once", "loop"), nargs="?", default="once")
    parser.add_argument(
        "--market-books",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT / "market_books/latest.json",
    )
    parser.add_argument(
        "--ladder-snapshot",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT / "market_ladder_snapshots/latest.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT / "output/market_implied_tail_residual_shadow_v1",
    )
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def repo_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def bracket_order(value: Any) -> float:
    raw = str(value or "").strip().replace("°", "")
    digits = "".join(character for character in raw if character.isdigit() or character in ".-")
    try:
        number = float(digits)
    except ValueError:
        return math.inf
    if raw.startswith("<"):
        return number - 0.5
    if raw.endswith("+") or raw.startswith(">"):
        return number + 0.5
    return number


def lifecycle(local_hours: float) -> str:
    if local_hours < -24:
        return "D-2_or_earlier"
    if local_hours < -12:
        return "D-1_early"
    if local_hours < 0:
        return "D-1_late"
    if local_hours < 6:
        return "D0_00_06"
    if local_hours < 10:
        return "D0_06_10"
    if local_hours < 14:
        return "D0_10_14"
    if local_hours < 18:
        return "D0_14_18"
    if local_hours < 24:
        return "D0_18_24"
    return "post_D0"


def ask_bucket(ask: float | None) -> str | None:
    if ask is None:
        return None
    for upper, label in ((0.03, "0-3c"), (0.05, "3-5c"), (0.10, "5-10c"), (0.20, "10-20c"), (0.40, "20-40c"), (0.70, "40-70c"), (1.01, "70c+")):
        if ask < upper:
            return label
    return None


def summary(record: dict[str, Any] | None) -> dict[str, Any]:
    value = (record or {}).get("summary")
    return value if isinstance(value, dict) else {}


def direct_quote(record: dict[str, Any] | None) -> dict[str, float | None]:
    values = summary(record)
    bid = finite(values.get("best_bid"))
    ask = finite(values.get("best_ask"))
    return {
        "bid": bid,
        "ask": ask,
        "bid_size": finite(values.get("bid_size")),
        "ask_size": finite(values.get("ask_size")),
        "depth_ask_5c": finite(values.get("depth_ask_5c")),
        "mid": (bid + ask) / 2.0 if bid is not None and ask is not None and ask >= bid else None,
        "spread": ask - bid if bid is not None and ask is not None and ask >= bid else None,
    }


def effective_yes_quote(yes: dict[str, Any] | None, no: dict[str, Any] | None) -> dict[str, float | str | None]:
    yes_quote = direct_quote(yes)
    no_quote = direct_quote(no)
    bids = [("direct_yes", yes_quote["bid"], yes_quote["bid_size"])]
    asks = [("direct_yes", yes_quote["ask"], yes_quote["ask_size"])]
    if no_quote["ask"] is not None:
        bids.append(("complement_no", 1.0 - float(no_quote["ask"]), no_quote["ask_size"]))
    if no_quote["bid"] is not None:
        asks.append(("complement_no", 1.0 - float(no_quote["bid"]), no_quote["bid_size"]))
    valid_bids = [item for item in bids if item[1] is not None]
    valid_asks = [item for item in asks if item[1] is not None]
    best_bid = max(valid_bids, key=lambda item: float(item[1])) if valid_bids else None
    best_ask = min(valid_asks, key=lambda item: float(item[1])) if valid_asks else None
    bid = float(best_bid[1]) if best_bid else None
    ask = float(best_ask[1]) if best_ask else None
    valid = bid is not None and ask is not None and ask >= bid
    return {
        "bid": bid,
        "ask": ask,
        "bid_size": best_bid[2] if best_bid else None,
        "ask_size": best_ask[2] if best_ask else None,
        "bid_source": best_bid[0] if best_bid else None,
        "ask_source": best_ask[0] if best_ask else None,
        "mid": (bid + ask) / 2.0 if valid else None,
        "spread": ask - bid if valid else None,
    }


def mode_distance(rank: int, mode_rank: int) -> str:
    distance = rank - mode_rank
    if distance == 0:
        return "mode"
    side = "hot" if distance > 0 else "cold"
    magnitude = abs(distance)
    return f"{side}_{magnitude}" if magnitude <= 2 else f"{side}_3plus"


def build_rows(
    books: dict[str, Any],
    ladders: dict[str, Any],
    state: dict[str, Any],
    *,
    ingested_at: datetime,
    build_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    books_batch = str(books.get("batch_capture_id") or "")
    ladders_batch = str(ladders.get("batch_capture_id") or "")
    if not books_batch or books_batch != ladders_batch:
        raise ValueError(f"batch mismatch market_books={books_batch!r} ladder={ladders_batch!r}")
    observed_at = parse_utc(ladders.get("available_at_utc") or books.get("available_at_utc"))
    book_index = {
        str(record.get("book_capture_id")): record
        for record in books.get("records", [])
        if isinstance(record, dict) and record.get("book_capture_id")
    }
    seen = set(state.get("seen_checkpoint_keys") or [])
    prior = dict(state.get("prior_mid_by_rung") or {})
    output: list[dict[str, Any]] = []
    counters = {"events_seen": 0, "events_new": 0, "rungs_emitted": 0, "events_missing_timezone": 0}

    for event in ladders.get("records", []):
        if not isinstance(event, dict):
            continue
        counters["events_seen"] += 1
        city = str(event.get("city") or "")
        target_date = str(event.get("target_date") or "")
        timezone_name = city_timezone_name(city)
        if not timezone_name:
            counters["events_missing_timezone"] += 1
            continue
        local_dt = city_local_datetime(city, observed_at, timezone_name)
        target_midnight = datetime.fromisoformat(target_date).replace(tzinfo=local_dt.tzinfo)
        local_hours = (local_dt - target_midnight).total_seconds() / 3600.0
        checkpoint_index = math.floor(local_hours / 2.0)
        checkpoint_key = f"{city}|{target_date}|{checkpoint_index}"
        if checkpoint_key in seen:
            continue
        seen.add(checkpoint_key)
        counters["events_new"] += 1

        rung_rows: list[dict[str, Any]] = []
        for rung in sorted(event.get("rungs", []), key=lambda item: bracket_order(item.get("bracket"))):
            yes = book_index.get(str(rung.get("yes_book_capture_id") or ""))
            no = book_index.get(str(rung.get("no_book_capture_id") or ""))
            direct = direct_quote(yes)
            effective = effective_yes_quote(yes, no)
            rung_rows.append({"rung": rung, "yes": yes, "no": no, "direct": direct, "effective": effective})

        direct_mids = [float(item["direct"]["mid"]) for item in rung_rows if item["direct"]["mid"] is not None]
        direct_mid_mass = sum(direct_mids)
        direct_two_sided = len(direct_mids)
        rung_count = len(rung_rows)
        quote_fraction = direct_two_sided / rung_count if rung_count else 0.0
        mode_rank = None
        if direct_mids:
            mode_rank = max(
                range(len(rung_rows)),
                key=lambda index: float(rung_rows[index]["direct"]["mid"] or -1.0),
            )
        normalized = [
            (float(item["direct"]["mid"]) / direct_mid_mass if item["direct"]["mid"] is not None and direct_mid_mass > 0 else None)
            for item in rung_rows
        ]
        tail_mass = sum(value for index, value in enumerate(normalized) if value is not None and mode_rank is not None and index > mode_rank)

        for rank, (item, normalized_mid) in enumerate(zip(rung_rows, normalized, strict=True)):
            rung = item["rung"]
            direct = item["direct"]
            effective = item["effective"]
            rung_key = f"{city}|{target_date}|{rung.get('bracket')}"
            previous = prior.get(rung_key) if isinstance(prior.get(rung_key), dict) else {}
            prior_mid = finite(previous.get("direct_yes_mid"))
            direct_mid = finite(direct.get("mid"))
            delta = direct_mid - prior_mid if direct_mid is not None and prior_mid is not None else None
            path = None
            if delta is not None:
                path = "rising_2c_plus" if delta >= 0.02 else "falling_2c_plus" if delta <= -0.02 else "stable"
            record_id = hashlib.sha256(f"{books_batch}|{checkpoint_key}|{rung.get('market_id')}".encode()).hexdigest()
            output.append({
                "schema_version": SCHEMA_VERSION,
                "record_id": record_id,
                "strategy_family": "market_implied_tail_residual",
                "strategy_instance_id": "market_implied_tail_residual_shadow_v1",
                "execution_mode": "shadow_zero_notional",
                "notional_usd": 0.0,
                "signal_status": "telemetry_only_model_unfitted",
                "city": city,
                "target_date": target_date,
                "event_id": event.get("event_id"),
                "event_slug": event.get("event_slug"),
                "market_id": rung.get("market_id"),
                "condition_id": rung.get("condition_id"),
                "bracket": rung.get("bracket"),
                "bracket_rank": rank,
                "mode_rank": mode_rank,
                "mode_distance": mode_distance(rank, mode_rank) if mode_rank is not None else None,
                "timezone_name": timezone_name,
                "decision_ts_utc": iso_utc(observed_at),
                "decision_ts_local": local_dt.isoformat(timespec="seconds"),
                "local_hours_from_target_midnight": round(local_hours, 6),
                "lifecycle": lifecycle(local_hours),
                "checkpoint_index_2h": checkpoint_index,
                "checkpoint_key": checkpoint_key,
                "market_batch_capture_id": books_batch,
                "ladder_distribution_complete": bool(event.get("market_distribution_complete")),
                "two_sided_book_distribution_complete": bool(event.get("two_sided_book_distribution_complete")),
                "rung_count": rung_count,
                "direct_two_sided_rungs": direct_two_sided,
                "direct_quote_fraction": quote_fraction,
                "direct_yes_bid": direct.get("bid"),
                "direct_yes_ask": direct.get("ask"),
                "direct_yes_bid_size": direct.get("bid_size"),
                "direct_yes_ask_size": direct.get("ask_size"),
                "direct_yes_depth_ask_5c": direct.get("depth_ask_5c"),
                "direct_yes_mid": direct_mid,
                "direct_yes_spread": direct.get("spread"),
                "effective_yes_bid": effective.get("bid"),
                "effective_yes_ask": effective.get("ask"),
                "effective_yes_bid_source": effective.get("bid_source"),
                "effective_yes_ask_source": effective.get("ask_source"),
                "effective_yes_mid": effective.get("mid"),
                "normalized_direct_mid": normalized_mid,
                "market_implied_hotter_tail_mass": tail_mass,
                "ask_bucket": ask_bucket(finite(direct.get("ask"))),
                "prior_checkpoint_direct_yes_mid": prior_mid,
                "direct_mid_delta_from_prior_checkpoint": delta,
                "price_path": path,
                "yes_book_status": (item["yes"] or {}).get("status"),
                "no_book_status": (item["no"] or {}).get("status"),
                "yes_token_id": rung.get("yes_token_id"),
                "no_token_id": rung.get("no_token_id"),
                "observed_at_utc": iso_utc(observed_at),
                "ingested_at_utc": iso_utc(ingested_at),
                "producer_build_id": build_id,
                "source_producer_build_id": books.get("producer_build_id"),
            })
            prior[rung_key] = {"direct_yes_mid": direct_mid, "checkpoint_key": checkpoint_key, "observed_at_utc": iso_utc(observed_at)}

    counters["rungs_emitted"] = len(output)
    new_state = {
        "schema_version": SCHEMA_VERSION,
        "updated_at_utc": iso_utc(ingested_at),
        "seen_checkpoint_keys": sorted(seen),
        "prior_mid_by_rung": prior,
    }
    return output, new_state, counters


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    started = utc_now()
    books = read_json(args.market_books)
    ladders = read_json(args.ladder_snapshot)
    state_path = args.output_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {}
    build_id = repo_sha()
    rows, new_state, counters = build_rows(books, ladders, state, ingested_at=started, build_id=build_id)
    append_jsonl(args.output_dir / "checkpoints.jsonl", rows)
    atomic_json(state_path, new_state)
    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "execution_mode": "shadow_zero_notional",
        "orders_enabled": False,
        "notional_usd": 0.0,
        "signal_status": "telemetry_only_model_unfitted",
        "generated_at_utc": iso_utc(utc_now()),
        "source_available_at_utc": ladders.get("available_at_utc"),
        "source_batch_capture_id": ladders.get("batch_capture_id"),
        "source_market_books": str(args.market_books),
        "source_ladder_snapshot": str(args.ladder_snapshot),
        "checkpoint_journal": str(args.output_dir / "checkpoints.jsonl"),
        "producer_build_id": build_id,
        **counters,
    }
    atomic_json(args.output_dir / "latest_summary.json", summary_payload)
    return summary_payload


def main() -> int:
    args = parse_args()
    if args.command == "once":
        print(json.dumps(run_once(args), sort_keys=True))
        return 0
    while True:
        try:
            print(json.dumps(run_once(args), sort_keys=True), flush=True)
        except Exception as exc:  # health becomes stale when the source contract breaks
            print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}", "generated_at_utc": iso_utc(utc_now())}, sort_keys=True), flush=True)
        time.sleep(max(5.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
