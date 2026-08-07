"""Stable identities and completeness manifests for exact-bracket ladders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Mapping

from weather_data_feed.information_events import canonical_json_hash
from weather_data_feed.forecast_run_contract import materialize_full_ladder_checkpoint
from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed.source_lineage import capture_batch_id


MARKET_LADDER_MANIFEST_SCHEMA_VERSION = "weather_market_ladder_manifest_v1"


def _ladder_clock_manifest(
    rows: list[Mapping[str, Any]], *, published_at_utc: str
) -> dict[str, Any]:
    """Materialize an exact clock contract without guessing legacy clocks."""

    required: list[tuple[str, str]] = [
        (side, field)
        for side in ("yes", "no")
        for field in (
            "request_started_at_utc",
            "response_received_at_utc",
            "parsed_at_utc",
        )
    ]
    blockers: list[str] = []
    values: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        for side, field in required:
            value = row.get(f"{side}_book_{field}")
            if value:
                values[(side, field)].append(str(value))
            else:
                blockers.append(f"{side}_book_{field}_missing")
        if row.get("yes_book_status") != "ok":
            blockers.append("yes_book_not_ok")
        if row.get("no_book_status") != "ok":
            blockers.append("no_book_not_ok")
    exact = not blockers and bool(rows)
    assembled_values = [
        value
        for side in ("yes", "no")
        for value in values[(side, "parsed_at_utc")]
    ]
    exchange_values = [
        str(row.get(f"{side}_book_exchange_ts_utc"))
        for row in rows
        for side in ("yes", "no")
        if row.get(f"{side}_book_exchange_ts_utc")
    ]
    return {
        "clock_lineage_status": (
            "collector_exact_full_ladder_clock"
            if exact
            else "legacy_or_incomplete_full_ladder_clock"
        ),
        "event_time_pit_scorable": exact and bool(published_at_utc),
        "clock_lineage_blockers": sorted(set(blockers)),
        "ladder_request_started_at_utc": (
            min(
                value
                for side in ("yes", "no")
                for value in values[(side, "request_started_at_utc")]
            )
            if exact
            else None
        ),
        "ladder_response_received_at_utc": (
            max(
                value
                for side in ("yes", "no")
                for value in values[(side, "response_received_at_utc")]
            )
            if exact
            else None
        ),
        "ladder_assembled_at_utc": max(assembled_values) if exact else None,
        "exchange_book_max_ts_utc": max(exchange_values) if exchange_values else None,
        # With the current snapshot-full publisher, downstream code cannot see
        # the atomic city ladder before the complete JSON is published.
        "ladder_available_at_utc": published_at_utc if exact else None,
        "published_at_utc": published_at_utc or None,
    }


def _mid(row: Mapping[str, Any]) -> float | None:
    bid, ask = row.get("yes_best_bid"), row.get("yes_best_ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        fallback = row.get("market_yes_price")
        return None if fallback is None else float(fallback)
    except (TypeError, ValueError):
        return None


def _native_complete(rows: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    parsed = [parse_market_bracket(str(row.get("bracket") or ""), str(row.get("question") or "")) for row in rows]
    reasons: list[str] = []
    if any(item is None for item in parsed):
        reasons.append("unparseable_rung")
        return False, reasons
    brackets = sorted(parsed, key=lambda item: float("-inf") if item.bottom else float(item.low))
    if sum(item.bottom for item in brackets) != 1:
        reasons.append("bottom_anchor_count_not_one")
    if sum(item.top for item in brackets) != 1:
        reasons.append("top_anchor_count_not_one")
    for left, right in zip(brackets, brackets[1:]):
        if left.high is None or right.low is None or abs(float(right.low) - float(left.high) - 1.0) > 1e-9:
            reasons.append("non_contiguous_native_lattice")
            break
    return not reasons, reasons


def annotate_market_ladder_snapshot(
    payload: dict[str, Any],
    *,
    producer: str,
    producer_build_id: str | None,
) -> list[dict[str, Any]]:
    """Attach one snapshot ID and per-event rung manifests in place."""
    records = [row for row in payload.get("records") or [] if isinstance(row, dict)]
    captured_at = str(payload.get("collection_started_at_utc") or payload.get("ts_utc") or "")
    available_at = str(payload.get("available_at_utc") or "")
    batch_id = capture_batch_id(
        producer=producer,
        captured_at_utc=captured_at,
        scope={"kind": "full_exact_bracket_ladder", "available_at_utc": available_at},
        raw_payload_hashes=(),
    )
    snapshot_capture_id = canonical_json_hash(
        {"batch_capture_id": batch_id, "record_count": len(records), "available_at_utc": available_at}
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        key = (
            str(row.get("city") or ""),
            str(row.get("event_date") or row.get("target_date") or ""),
            str(row.get("event_slug") or row.get("market_id") or row.get("condition_id") or ""),
        )
        groups[key].append(row)

    manifests: list[dict[str, Any]] = []
    for (city, target_date, event_key), rows in sorted(groups.items()):
        rung_manifest = sorted(
            [
                {
                    "bracket": str(row.get("bracket") or ""),
                    "condition_id": str(row.get("condition_id") or ""),
                    "market_id": str(row.get("market_id") or ""),
                    "yes_token_id": str(row.get("yes_token_id") or ""),
                    "no_token_id": str(row.get("no_token_id") or ""),
                }
                for row in rows
            ],
            key=lambda item: (item["bracket"], item["condition_id"]),
        )
        rung_hash = canonical_json_hash(rung_manifest)
        native_complete, blockers = _native_complete(rows)
        raw_mids = [_mid(row) for row in rows]
        market_complete = all(value is not None for value in raw_mids)
        total = sum(value for value in raw_mids if value is not None)
        normalized = (
            [round(float(value) / total, 12) for value in raw_mids]
            if market_complete and total > 0
            else None
        )
        if not market_complete:
            blockers.append("missing_market_probability")
        elif total <= 0:
            blockers.append("non_positive_market_probability_sum")
        book_snapshot_id = canonical_json_hash(
            {"snapshot_capture_id": snapshot_capture_id, "city": city, "target_date": target_date, "event_key": event_key, "rung_manifest_hash": rung_hash}
        )
        try:
            local_date = str(rows[0].get("city_local_date_at_snapshot") or target_date)
            horizon_days = (date.fromisoformat(target_date) - date.fromisoformat(local_date)).days
        except ValueError:
            horizon_days = -1
        strict_book_checkpoint: dict[str, Any] | None = None
        clock_manifest = _ladder_clock_manifest(rows, published_at_utc=available_at)
        if native_complete:
            try:
                strict_book_checkpoint = materialize_full_ladder_checkpoint(
                    [
                        {
                            **row,
                            "token_id": row.get("yes_token_id"),
                            "book_status": row.get("yes_book_status"),
                        }
                        for row in rows
                    ],
                    city=city,
                    target_date=target_date,
                    event_id=event_key,
                    checkpoint_ts_utc=available_at,
                    feature_book_snapshot_id=book_snapshot_id,
                    horizon_days=horizon_days,
                )
            except (TypeError, ValueError) as exc:
                strict_book_checkpoint = {
                    "schema_version": "weather_full_ladder_checkpoint_v2",
                    "evidence_status": "blocked",
                    "evidence_blockers": [
                        {"code": "strict_book_checkpoint_error", "error": f"{type(exc).__name__}: {exc}"}
                    ],
                }
        manifest = {
            "schema_version": MARKET_LADDER_MANIFEST_SCHEMA_VERSION,
            "producer": producer,
            "producer_build_id": producer_build_id,
            "snapshot_capture_id": snapshot_capture_id,
            "batch_capture_id": batch_id,
            "book_snapshot_id": book_snapshot_id,
            "city": city,
            "target_date": target_date,
            "event_key": event_key,
            "captured_at_utc": captured_at,
            "available_at_utc": available_at,
            "rung_count": len(rows),
            "rung_manifest": rung_manifest,
            "rung_manifest_hash": rung_hash,
            "city_target_ladder_hash": rung_hash,
            "native_lattice_complete": native_complete,
            "rung_completeness": native_complete,
            "market_distribution_complete": market_complete and total > 0,
            "two_sided_book_distribution_complete": bool(
                strict_book_checkpoint
                and strict_book_checkpoint.get("market_distribution_complete")
            ),
            "normalized_market_probability": normalized,
            "normalized_market_probability_hash": canonical_json_hash(normalized) if normalized is not None else None,
            "checkpoint_status": "scorable_probability" if native_complete and normalized is not None else "blocked",
            "checkpoint_blockers": sorted(set(blockers)),
            "strict_book_checkpoint": strict_book_checkpoint,
            **clock_manifest,
        }
        manifests.append(manifest)
        for row in rows:
            row["snapshot_capture_id"] = snapshot_capture_id
            row["batch_capture_id"] = batch_id
            row["book_snapshot_id"] = book_snapshot_id
            row["rung_manifest_hash"] = rung_hash
            row["city_target_ladder_hash"] = rung_hash
            row["native_lattice_complete"] = native_complete
            row["rung_completeness"] = native_complete
            row["market_distribution_complete"] = manifest["market_distribution_complete"]
            row["two_sided_book_distribution_complete"] = manifest["two_sided_book_distribution_complete"]
            row["ladder_checkpoint_status"] = manifest["checkpoint_status"]
            for field in (
                "clock_lineage_status",
                "event_time_pit_scorable",
                "clock_lineage_blockers",
                "ladder_request_started_at_utc",
                "ladder_response_received_at_utc",
                "ladder_assembled_at_utc",
                "exchange_book_max_ts_utc",
                "ladder_available_at_utc",
                "published_at_utc",
            ):
                row[field] = manifest[field]
    payload["producer"] = producer
    payload["producer_build_id"] = producer_build_id
    payload["snapshot_capture_id"] = snapshot_capture_id
    payload["batch_capture_id"] = batch_id
    payload["full_ladder_manifests"] = manifests
    return manifests
