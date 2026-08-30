"""Offline METAR.ws-to-public-market reaction parser.

This is a research parser, not a collector or execution component.  It joins
append-only lab events, capture demands, subscription epochs, and already
captured market WebSocket frames using the host-local monotonic receive clock.
Consequently all reported deltas are exploratory ordering measurements, never
formal wall-clock latency.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from us_fast_weather_lab.market_reaction_cohort import MarketReactionCohort, load_market_reaction_cohort, load_named_market_reaction_cohort

COHORT = load_market_reaction_cohort()
TARGET_CITIES = COHORT.reaction_cities
TARGET_SOURCES = frozenset({"metar_ws_metar", "metar_ws_hfmetar", "metar_ws_datis"})
CHECKPOINTS_SECONDS = (0, 15, 30, 60, 120, 300)
EXECUTABLE_SIZES = (1, 5, 10)
WEATHER_TAKER_FEE_RATE = 0.05
MIN_PRICE_DETERIORATION = 0.001
MAX_LIVE_SOURCE_EVENT_AGE_SECONDS = 1800
STRATEGY_KEY = "weather.metar_ws_event_repricing"
LEFT_CENSORED = "LEFT_CENSORED_NO_PRE_EVENT_BASELINE"


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return ()
    def rows() -> Iterable[dict[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, Mapping):
                    yield dict(value)
    return rows()


def _source_events(root: Path) -> Iterable[dict[str, Any]]:
    def rows() -> Iterable[dict[str, Any]]:
        for path in sorted(root.glob("*/sources.jsonl")):
            yield from _jsonl(path)
    return rows() if root.exists() else ()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ns(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _token_id(message: Mapping[str, Any]) -> str:
    return str(message.get("asset_id") or message.get("token_id") or "")


def _levels(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for level in raw:
        value = level.get("price") if isinstance(level, Mapping) else (level[0] if isinstance(level, list | tuple) and level else None)
        price = _number(value)
        if price is not None:
            values.append(price)
    return values


def _level_map(raw: Any) -> dict[float, float]:
    """Parse a CLOB ladder without discarding displayed depth."""
    if not isinstance(raw, list):
        return {}
    output: dict[float, float] = {}
    for level in raw:
        if isinstance(level, Mapping):
            price, size = _number(level.get("price")), _number(level.get("size"))
        elif isinstance(level, list | tuple) and len(level) >= 2:
            price, size = _number(level[0]), _number(level[1])
        else:
            continue
        if price is None or size is None or not 0 <= price <= 1 or size <= 0:
            continue
        output[price] = size
    return output


def _copy_book(levels: Mapping[str, Mapping[float, float]]) -> dict[str, dict[float, float]]:
    return {"bids": dict(levels["bids"]), "asks": dict(levels["asks"])}


def _sweep(
    levels: Mapping[str, Mapping[float, float]], *, size: int, side: str,
) -> dict[str, float | bool | None]:
    """Return a deterministic taker sweep for one frozen reconstructed book."""
    ladder_name = "asks" if side == "buy" else "bids"
    ladder = levels[ladder_name]
    ordered = sorted(ladder.items(), reverse=side == "sell")
    remaining = float(size)
    gross = fee = 0.0
    for price, available in ordered:
        take = min(remaining, available)
        if take <= 0:
            continue
        gross += take * price
        fee += take * WEATHER_TAKER_FEE_RATE * price * (1 - price)
        remaining -= take
        if remaining <= 1e-12:
            effective = gross + fee if side == "buy" else gross - fee
            return {
                "fully_executable": True,
                "gross_value": gross,
                "fee": fee,
                "vwap": gross / size,
                "effective_value": effective,
                "effective_price": effective / size,
            }
    return {
        "fully_executable": False,
        "gross_value": None,
        "fee": None,
        "vwap": None,
        "effective_value": None,
        "effective_price": None,
    }


def _write_sweep_fields(
    row: dict[str, Any], prefix: str, book: Mapping[str, Mapping[float, float]] | None,
) -> None:
    for size in EXECUTABLE_SIZES:
        for side in ("buy", "sell"):
            sweep = _sweep(book, size=size, side=side) if book is not None else {
                "fully_executable": False,
                "gross_value": None,
                "fee": None,
                "vwap": None,
                "effective_value": None,
                "effective_price": None,
            }
            for name, value in sweep.items():
                row[f"{prefix}_{side}_{size}share_{name}"] = value


def _book_top(message: Mapping[str, Any]) -> tuple[float, float]:
    bids, asks = _levels(message.get("bids")), _levels(message.get("asks"))
    return (max(bids) if bids else 0.0, min(asks) if asks else 1.0)


def _direct_top(message: Mapping[str, Any]) -> tuple[float, float] | None:
    bid = _number(message.get("best_bid", message.get("bestBid")))
    ask = _number(message.get("best_ask", message.get("bestAsk")))
    # The feed can emit partial administrative updates.  A single side is not
    # a complete executable top and must not be normalized into a fake 0/1
    # market state.
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return (bid, ask)


def _messages(envelope: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    payload = envelope.get("message")
    values = payload if isinstance(payload, list) else [payload]
    for value in values:
        if isinstance(value, Mapping):
            # price_change can contain several independent changes.
            changes = value.get("price_changes", value.get("changes"))
            if str(value.get("event_type") or value.get("type") or "") == "price_change" and isinstance(changes, list):
                for change in changes:
                    if isinstance(change, Mapping):
                        merged = dict(value)
                        merged.pop("changes", None)
                        merged.pop("price_changes", None)
                        merged.update(change)
                        yield merged
            else:
                yield value


def _apply_change(levels: dict[str, dict[float, float]], message: Mapping[str, Any]) -> bool:
    price, size = _number(message.get("price")), _number(message.get("size"))
    side = str(message.get("side") or "").upper()
    if price is None or size is None or side not in {"BUY", "SELL"}:
        return False
    target = levels["bids" if side == "BUY" else "asks"]
    if size > 0:
        target[price] = size
    else:
        target.pop(price, None)
    return True


def _epoch_tokens(epoch: Mapping[str, Any]) -> set[str]:
    values = set(map(str, epoch.get("token_ids") or ()))
    token_rows = epoch.get("token_rows")
    if isinstance(token_rows, Mapping):
        values.update(map(str, token_rows.keys()))
        for key, row in token_rows.items():
            if isinstance(row, Mapping):
                values.add(str(row.get("token_id") or row.get("asset_id") or key))
    elif isinstance(token_rows, list):
        for row in token_rows:
            if isinstance(row, Mapping):
                values.add(_token_id(row))
    values.discard("")
    return values


def _read_frames(paths: Sequence[Path]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for path in paths:
        frames.extend(_jsonl(path))
    def sort_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return (
            int(row["transport_received_monotonic_ns"]),
            str(row.get("raw_frame_id") or canonical),
            canonical,
        )

    return sorted(
        (row for row in frames if _ns(row.get("transport_received_monotonic_ns")) is not None),
        key=sort_key,
    )


def _top(levels: Mapping[str, Mapping[float, float]]) -> tuple[float, float]:
    return (max(levels["bids"], default=0.0), min(levels["asks"], default=1.0))


def _validate_demand_cohort_metadata(
    event: Mapping[str, Any], demand: Mapping[str, Any], cohort: MarketReactionCohort
) -> None:
    target = cohort.target_for_event(event)
    if target is None:
        raise ValueError("demand references an event outside the fixed reaction cohort")
    raw_metadata = demand.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    expected = {
        "city": target.city,
        "cohort_id": cohort.cohort_id,
        "cohort_role": target.role,
        "basis_status": target.basis_status,
        "cohort_source_station": target.source_station,
        "cohort_market_station": target.market_station,
        "region": target.region,
        "comparison_class": target.comparison_class,
        "market_unit": target.market_unit,
    }
    for key, expected_value in expected.items():
        if cohort.cohort_id != COHORT.cohort_id and key not in metadata:
            raise ValueError(f"global cohort demand metadata missing required field: {key}")
        if key in metadata and metadata.get(key) != expected_value:
            raise ValueError(f"demand metadata conflicts with fixed cohort field: {key}")


def _event_demands(events: Iterable[Mapping[str, Any]], demands: Iterable[Mapping[str, Any]], cohort: MarketReactionCohort) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    event_by_id = {str(row.get("information_event_id")): dict(row) for row in events
                   if cohort.target_for_event(row) is not None and str(row.get("source")) in TARGET_SOURCES}
    output: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for demand in demands:
        if demand.get("schema_version") != "polymarket_capture_demand_v1" or demand.get("strategy_key") != STRATEGY_KEY:
            continue
        event_id, token_id = str(demand.get("trigger_event_id") or ""), str(demand.get("token_id") or "")
        if event_id in event_by_id and token_id and (event_id, token_id) not in seen:
            _validate_demand_cohort_metadata(event_by_id[event_id], demand, cohort)
            output.append((event_by_id[event_id], dict(demand))); seen.add((event_id, token_id))
    return output


def _bracket_lower(value: Any) -> float | None:
    """Return the lower bound of the existing exact/range display label."""
    text = str(value or "").replace("°F", "").replace("F", "").strip()
    try:
        return float(text.split("-")[0].replace("+", "").strip())
    except ValueError:
        return None


def _event_running_max(events: Iterable[Mapping[str, Any]], cohort: MarketReactionCohort) -> dict[str, dict[str, Any]]:
    """Compare each vendor event with the PIT prior official-METAR maximum.

    This is deliberately a source-cross proxy.  U.S. Polymarket temperature
    markets settle to Weather Underground, so neither METAR nor D-ATIS is
    allowed to turn this label into a logical settlement invalidation.
    """
    values = list(events)
    official: list[tuple[int, str, str, str, datetime, float]] = []
    for event in values:
        received = _ns(event.get("transport_received_monotonic_ns"))
        temp = _number(event.get("temp_c"))
        report_time = _datetime(event.get("source_report_ts_utc"))
        if (
            received is not None
            and temp is not None
            and report_time is not None
            and str(event.get("source")) in {"metar_ws_metar", "aviationweather_metar"}
        ):
            official.append((
                received,
                str(event.get("station_id") or event.get("station") or ""),
                str(event.get("city") or ""),
                str(event.get("target_date") or ""),
                report_time,
                temp,
            ))
    accepted = []
    for event in values:
        event_id = str(event.get("information_event_id") or "")
        received = _ns(event.get("transport_received_monotonic_ns"))
        temp = _number(event.get("temp_c"))
        report_time = _datetime(event.get("source_report_ts_utc"))
        source = str(event.get("source"))
        if (
            event_id
            and received is not None
            and temp is not None
            and report_time is not None
            and cohort.target_for_event(event) is not None
            and source in TARGET_SOURCES
        ):
            accepted.append((received, event_id, dict(event), temp, report_time))
    output: dict[str, dict[str, Any]] = {}
    for received, event_id, event, temp, report_time in sorted(accepted, key=lambda value: (value[0], value[1])):
        target = cohort.target_for_event(event)
        assert target is not None
        station = str(event.get("station_id") or event.get("station") or "")
        city = str(event.get("city") or "")
        target_date = str(event.get("target_date") or "")
        prior = [
            value
            for prior_received, prior_station, prior_city, prior_date, prior_time, value in official
            if prior_received <= received
            and prior_time < report_time
            and prior_date == target_date
            and ((station and prior_station == station) or (not station and prior_city == city))
        ]
        before = max(prior) if prior else None
        after = max(before, temp) if before is not None else temp
        received_at = _datetime(event.get("transport_received_at_utc"))
        event_age_sec = (received_at - report_time).total_seconds() if received_at is not None else None
        live_eligible = bool(
            event_age_sec is not None
            and -1 <= event_age_sec <= MAX_LIVE_SOURCE_EVENT_AGE_SECONDS
        )
        output[event_id] = {
            "running_max_market_before": None if before is None else (before if target.market_unit == "C" else before * 9 / 5 + 32),
            "running_max_market_after": after if target.market_unit == "C" else after * 9 / 5 + 32,
            "running_max_market_unit": target.market_unit,
            # Legacy aliases remain only for the Fahrenheit US cohort.
            "running_max_f_before": None if target.market_unit != "F" or before is None else before * 9 / 5 + 32,
            "running_max_f_after": None if target.market_unit != "F" else after * 9 / 5 + 32,
            "running_max_before_known": before is not None,
            "is_new_running_max": bool(before is not None and temp > before and live_eligible),
            "source_cross_proxy_eligible": live_eligible and before is not None,
            "source_event_age_sec": event_age_sec,
            "running_max_basis": "pit_prior_official_metar_not_settlement_truth",
        }
    return output


def analyze_metar_market_reaction(
    source_events_root: Path, demand_jsonl: Path, subscription_epochs_jsonl: Path,
    market_raw_jsonl: Sequence[Path],
    *, cohort: MarketReactionCohort | None = None,
) -> dict[str, Any]:
    """Return complete event×token rows plus a compact exploratory summary."""
    cohort = cohort or COHORT
    source_events = list(_source_events(source_events_root))
    demands = list(_jsonl(demand_jsonl))
    running_max = _event_running_max(source_events, cohort)
    epochs = [row for row in _jsonl(subscription_epochs_jsonl)
              if row.get("schema_version") == "weather_market_books_ws_subscription_epoch_v2"]
    epoch_tokens = {
        str(epoch.get("subscription_epoch_id") or ""): _epoch_tokens(epoch)
        for epoch in epochs
        if str(epoch.get("subscription_epoch_id") or "")
    }
    frames = _read_frames(market_raw_jsonl)
    rows: list[dict[str, Any]] = []
    for event, demand in _event_demands(source_events, demands, cohort):
        event_ns = _ns(event.get("transport_received_monotonic_ns"))
        if event_ns is None:
            continue
        token = str(demand["token_id"]); metadata = demand.get("metadata") if isinstance(demand.get("metadata"), Mapping) else {}
        cohort_target = cohort.target_for_event(event)
        assert cohort_target is not None
        subscribed_epochs = [e for e in epochs if token in _epoch_tokens(e)]
        pre_epochs = {str(e.get("subscription_epoch_id")) for e in subscribed_epochs if (_ns(e.get("started_monotonic_ns")) or 10**30) <= event_ns}
        state: dict[str, dict[str, dict[float, float]]] = {}
        full_book_valid: dict[str, bool] = {}
        top_by_epoch: dict[str, tuple[float, float]] = {}
        baseline: tuple[float, float] | None = None
        baseline_book: dict[str, dict[float, float]] | None = None
        baseline_epoch: str | None = None
        first_depth = first_top = first_trade = None
        first_buy_deterioration: dict[int, float | None] = {size: None for size in EXECUTABLE_SIZES}
        checkpoints: dict[int, tuple[float, float] | None] = {point: None for point in CHECKPOINTS_SECONDS}
        checkpoint_books: dict[int, dict[str, dict[float, float]] | None] = {
            point: None for point in CHECKPOINTS_SECONDS
        }
        checkpoint_depth_seen: dict[int, bool] = {point: False for point in CHECKPOINTS_SECONDS}
        for frame in frames:
            received_ns = int(frame["transport_received_monotonic_ns"])
            epoch_id = str(frame.get("subscription_epoch_id") or "")
            if received_ns > event_ns + max(CHECKPOINTS_SECONDS) * 1_000_000_000:
                break
            for message in _messages(frame):
                if _token_id(message) != token:
                    continue
                if epoch_id not in epoch_tokens or token not in epoch_tokens[epoch_id]:
                    continue
                kind = str(message.get("event_type") or message.get("type") or "")
                books = state.setdefault(epoch_id, {"bids": {}, "asks": {}})
                previous = top_by_epoch.get(epoch_id)
                if kind == "book":
                    books["bids"] = _level_map(message.get("bids"))
                    books["asks"] = _level_map(message.get("asks"))
                    full_book_valid[epoch_id] = True
                    current = _top(books)
                elif kind == "price_change":
                    if not _apply_change(books, message):
                        continue
                    # Real CLOB price_change payloads carry an authoritative
                    # best bid/ask alongside the changed depth level.  Prefer
                    # it to a locally partial book after reconnect.
                    local_top = _top(books)
                    direct_top = _direct_top(message)
                    if direct_top is not None and full_book_valid.get(epoch_id) and direct_top != local_top:
                        full_book_valid[epoch_id] = False
                    current = direct_top or local_top
                elif kind == "best_bid_ask":
                    current = _direct_top(message)
                    if current is None:
                        continue
                    if full_book_valid.get(epoch_id) and current != _top(books):
                        full_book_valid[epoch_id] = False
                elif kind == "last_trade_price":
                    current = None
                else:
                    continue
                if current is not None:
                    top_by_epoch[epoch_id] = current
                if received_ns <= event_ns:
                    if kind in {"book", "price_change", "best_bid_ask"} and epoch_id in pre_epochs:
                        baseline, baseline_epoch = current, epoch_id
                        baseline_book = _copy_book(books) if full_book_valid.get(epoch_id) else None
                    continue
                delta = (received_ns - event_ns) / 1_000_000_000
                # A reconnect book is a snapshot, not an incremental update.
                if kind in {"price_change", "best_bid_ask"} and first_depth is None:
                    first_depth = delta
                if kind == "last_trade_price" and first_trade is None:
                    first_trade = delta
                # A reconnect book is a fresh epoch baseline/snapshot, never evidence of a change.
                if (
                    baseline is not None
                    and kind in {"price_change", "best_bid_ask"}
                    and previous is not None
                    and current != previous
                    and first_top is None
                ):
                        first_top = delta
                if (
                    baseline_book is not None
                    and full_book_valid.get(epoch_id)
                    and kind == "price_change"
                    and epoch_id == baseline_epoch
                ):
                    for size in EXECUTABLE_SIZES:
                        if first_buy_deterioration[size] is not None:
                            continue
                        before = _sweep(baseline_book, size=size, side="buy")
                        after = _sweep(books, size=size, side="buy")
                        before_price = before["effective_price"]
                        after_price = after["effective_price"]
                        if (
                            before["fully_executable"]
                            and after["fully_executable"]
                            and isinstance(before_price, float)
                            and isinstance(after_price, float)
                            and after_price >= before_price + MIN_PRICE_DETERIORATION - 1e-12
                        ):
                            first_buy_deterioration[size] = delta
                for point in CHECKPOINTS_SECONDS:
                    if received_ns <= event_ns + point * 1_000_000_000 and current is not None:
                        checkpoints[point] = current
                        checkpoint_depth_seen[point] = True
                        checkpoint_books[point] = (
                            _copy_book(books) if full_book_valid.get(epoch_id) else None
                        )
        censored = baseline is None
        # At t=0 the observable state is the pre-event baseline; later empty
        # checkpoints retain it unless an observed state update replaced it.
        if baseline is not None:
            for point, top in checkpoints.items():
                if top is None:
                    checkpoints[point] = baseline
                if not checkpoint_depth_seen[point] and baseline_book is not None:
                    checkpoint_books[point] = _copy_book(baseline_book)
        event_max = running_max.get(str(event.get("information_event_id")), {})
        bracket_lower = _bracket_lower(metadata.get("bracket"))
        before_f, after_f = event_max.get("running_max_market_before"), event_max.get("running_max_market_after")
        bracket_transition = bool(
            event_max.get("source_cross_proxy_eligible")
            and bracket_lower is not None
            and before_f is not None
            and after_f is not None
            and before_f < bracket_lower <= after_f
        )
        row: dict[str, Any] = {
            "schema_version": "metar_market_reaction_v1", "information_event_id": event.get("information_event_id"),
            "demand_id": demand.get("demand_id"), "token_id": token, "condition_id": demand.get("condition_id"),
            "city": cohort_target.city, "bracket": metadata.get("bracket"), "outcome": metadata.get("outcome"),
            **cohort_target.metadata(cohort_id=cohort.cohort_id),
            "source": event.get("source"), "station_id": event.get("station_id", event.get("station")),
            "source_report_ts_utc": event.get("source_report_ts_utc"), "temp_c": event.get("temp_c"),
            "event_transport_received_at_utc": event.get("transport_received_at_utc"), "event_transport_received_monotonic_ns": event_ns,
            "running_max_market_before": before_f, "running_max_market_after": after_f,
            "running_max_market_unit": cohort_target.market_unit,
            "running_max_f_before": before_f if cohort_target.market_unit == "F" else None,
            "running_max_f_after": after_f if cohort_target.market_unit == "F" else None,
            "is_new_running_max": event_max.get("is_new_running_max"), "bracket_transition": bracket_transition,
            "running_max_before_known": event_max.get("running_max_before_known"),
            "source_cross_proxy_eligible": event_max.get("source_cross_proxy_eligible"),
            "source_event_age_sec": event_max.get("source_event_age_sec"),
            "running_max_basis": event_max.get("running_max_basis"),
            "market_impact_semantics": "source_cross_proxy_not_settlement_hard_invalidation",
            "ordering_clock": "same_host_transport_received_monotonic_ns_exploratory", "formal_latency": None,
            "event_clock_valid": bool(event.get("clock_valid")),
            "event_pit_eligible": bool(event.get("pit_eligible")),
            "formal_latency_eligible": False,
            "pre_event_subscription": bool(pre_epochs), "baseline_subscription_epoch_id": baseline_epoch,
            "baseline_bid": baseline[0] if baseline else None, "baseline_ask": baseline[1] if baseline else None,
            "baseline_full_depth_valid": baseline_book is not None,
            "status": LEFT_CENSORED if censored else "OBSERVED", "first_depth_update_delta_sec": first_depth,
            "first_top_change_delta_sec": first_top, "first_public_trade_print_delta_sec": first_trade,
            "public_trade_print_semantics": "public trade print != own fill",
            "fee_model": "polymarket_weather_taker_p_times_one_minus_p_v1",
            "fee_rate": WEATHER_TAKER_FEE_RATE,
        }
        _write_sweep_fields(row, "baseline", baseline_book)
        for size, delta in first_buy_deterioration.items():
            row[f"first_buy_{size}share_effective_deterioration_delta_sec"] = delta
        for point, top in checkpoints.items():
            row[f"checkpoint_{point}s_bid"] = top[0] if top else None
            row[f"checkpoint_{point}s_ask"] = top[1] if top else None
            row[f"checkpoint_{point}s_snapshot_differs_from_baseline"] = (
                None if censored or top is None else top != baseline
            )
            row[f"checkpoint_{point}s_top_changed"] = (
                None if censored else first_top is not None and first_top <= point
            )
            row[f"checkpoint_{point}s_full_depth_valid"] = checkpoint_books[point] is not None
            _write_sweep_fields(row, f"checkpoint_{point}s", checkpoint_books[point])
        rows.append(row)
    observed = [row for row in rows if row["status"] == "OBSERVED"]
    deltas = sorted(row["first_top_change_delta_sec"] for row in observed if row["first_top_change_delta_sec"] is not None)
    def quantile(q: float) -> float | None:
        if not deltas: return None
        return deltas[round((len(deltas) - 1) * q)]
    all_event_ids = set(running_max)
    material_event_ids = {row["information_event_id"] for row in rows if row["is_new_running_max"] or row["bracket_transition"]}
    def funnel(event_ids: set[str]) -> dict[str, Any]:
        scoped = [row for row in rows if row["information_event_id"] in event_ids]
        scoped_observed = [row for row in scoped if row["status"] == "OBSERVED"]
        return {"event_count": len(event_ids), "event_token_denominator": len(scoped),
                "pre_event_baseline_coverage": len(scoped_observed) / len(scoped) if scoped else None}
    primary_rows = [row for row in rows if row["cohort_role"] == "primary"]
    control_rows = [row for row in rows if row["cohort_role"] != "primary"]
    summary = {
        "event_count": len({row["information_event_id"] for row in rows}), "event_token_denominator": len(rows),
        "pre_event_baseline_coverage": len(observed) / len(rows) if rows else None,
        "pre_event_full_depth_coverage": (
            sum(bool(row["baseline_full_depth_valid"]) for row in rows) / len(rows) if rows else None
        ),
        "baseline_executable_coverage_by_size": {
            str(size): {
                side: (
                    sum(bool(row[f"baseline_{side}_{size}share_fully_executable"]) for row in rows) / len(rows)
                    if rows else None
                )
                for side in ("buy", "sell")
            }
            for size in EXECUTABLE_SIZES
        },
        "buy_effective_deterioration_rate_by_size": {
            str(size): (
                sum(row[f"first_buy_{size}share_effective_deterioration_delta_sec"] is not None for row in observed)
                / len(observed)
                if observed else None
            )
            for size in EXECUTABLE_SIZES
        },
        "top_change_rate_by_checkpoint": {str(point): (sum(bool(row[f"checkpoint_{point}s_top_changed"]) for row in observed) / len(observed) if observed else None) for point in CHECKPOINTS_SECONDS},
        "first_top_change_delta_sec_quantiles": {"p50": quantile(.5), "p90": quantile(.9), "p99": quantile(.99)},
        "public_trade_print_coverage": sum(row["first_public_trade_print_delta_sec"] is not None for row in observed) / len(observed) if observed else None,
        "public_trade_print_semantics": "public trade print != own fill",
        "event_funnels": {"all_source_events": funnel(all_event_ids),
                          "material_running_max_or_bracket_transition_events": funnel(material_event_ids)},
        "cohort": {
            "cohort_id": cohort.cohort_id,
            "primary_event_token_denominator": len(primary_rows),
            "basis_mismatch_control_event_token_denominator": len(control_rows),
            "primary_event_count": len({row["information_event_id"] for row in primary_rows}),
            "basis_mismatch_control_event_count": len({row["information_event_id"] for row in control_rows}),
        },
    }
    return {"schema_version": "metar_market_reaction_report_v1", "summary": summary, "rows": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-events-root", required=True, type=Path)
    parser.add_argument("--market-capture-demands", required=True, type=Path)
    parser.add_argument("--subscription-epochs", required=True, type=Path)
    parser.add_argument("--market-raw", required=True, type=Path, nargs="+")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--market-reaction-cohort", choices=("us_temperature_markets_wide_v1", "europe_asia_core_v1"), default="us_temperature_markets_wide_v1")
    args = parser.parse_args(argv)
    report = analyze_metar_market_reaction(args.source_events_root, args.market_capture_demands, args.subscription_epochs, args.market_raw, cohort=load_named_market_reaction_cohort(args.market_reaction_cohort))
    args.output_json.parent.mkdir(parents=True, exist_ok=True); args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in report["rows"] for key in row})
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(report["rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
