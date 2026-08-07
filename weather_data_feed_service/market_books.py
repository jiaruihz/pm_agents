"""Canonical weather-market order-book collector.

This producer owns CLOB book acquisition.  It deliberately does not fetch or
require forecast/METAR data before recording the raw market ladder.  Weather
and strategy features are joined by downstream views.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from weather_data_feed import city_scan_dates, city_local_datetime, local_settle_utc
from weather_data_feed.information_events import canonical_json_hash
from weather_data_feed.source_lineage import producer_build_id
from weather_data_feed_service.legacy_weather_predict import paper_snapshot as legacy


SCHEMA_VERSION = "weather_market_books_batch_v1"
LADDER_SCHEMA_VERSION = "weather_market_ladder_snapshot_v1"
PRODUCER = "weather_data_feed_service.market_books"
PRODUCER_BUILD_ID, PRODUCER_BUILD_ID_BASIS = producer_build_id(
    Path(__file__).resolve().parents[1]
)


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _publish_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_observation_index(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in payload.get("records") or []:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if city and target_date:
            result[(city, target_date)] = row
    return result


def _strategy_state_from_observation(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    running_max_f = row.get("running_max_f")
    if running_max_f is None and row.get("running_max_c") is not None:
        try:
            running_max_f = float(row["running_max_c"]) * 9.0 / 5.0 + 32.0
        except (TypeError, ValueError):
            running_max_f = None
    return {"metar_current_max_f": running_max_f}


def _event_slug(city: str, cfg: dict[str, Any], target_date: str) -> str:
    city_slug = cfg.get("slug", city.lower())
    parsed = datetime.strptime(target_date, "%Y-%m-%d")
    return f"highest-temperature-in-{city_slug}-on-{parsed.strftime('%B-%-d-%Y').lower()}"


def discover_market_ladders(
    *,
    now_utc: datetime,
    observation_index: dict[tuple[str, str], dict[str, Any]],
    target_date: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover open weather ladders without touching any forecast provider."""

    events: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for city, cfg in legacy.CITIES.items():
        for event_date in city_scan_dates(
            city,
            now_utc,
            explicit_target_date=target_date,
        ):
            approximate_settle = local_settle_utc(city, event_date)
            hours_to_settle = (approximate_settle - now_utc).total_seconds() / 3600.0
            if hours_to_settle < 0 or hours_to_settle > 50:
                continue
            slug = _event_slug(city, cfg, event_date)
            try:
                status_code, raw, error = legacy.curl_json_get(
                    f"{legacy.PM_GAMMA_URL}/events",
                    params={"slug": slug},
                    proxy=legacy.PROXY,
                    timeout_sec=legacy.PM_CURL_TIMEOUT_SEC,
                    connect_timeout_sec=legacy.PM_CURL_CONNECT_TIMEOUT_SEC,
                )
            except Exception as exc:  # pragma: no cover - defensive network boundary
                status_code, raw, error = 0, None, f"{type(exc).__name__}: {exc}"
            if isinstance(raw, list):
                raw = raw[0] if raw else None
            if status_code != 200 or not isinstance(raw, dict):
                failures.append(
                    {
                        "city": city,
                        "target_date": event_date,
                        "slug": slug,
                        "status_code": status_code,
                        "error": error or "event_unavailable",
                    }
                )
                continue
            _, entries = legacy.gamma_market_ladder(raw.get("markets") or [])
            if not entries:
                failures.append(
                    {
                        "city": city,
                        "target_date": event_date,
                        "slug": slug,
                        "status_code": status_code,
                        "error": "empty_market_ladder",
                    }
                )
                continue
            strategy_targets = legacy.orderbook_targets_for_strategy_live(
                raw.get("markets") or [],
                cfg["unit"],
                _strategy_state_from_observation(observation_index.get((city, event_date))),
            )
            events.append(
                {
                    "city": city,
                    "target_date": event_date,
                    "event_slug": slug,
                    "event_id": raw.get("id", ""),
                    "condition_count": len(entries),
                    "entries": entries,
                    "strategy_targets": strategy_targets,
                    "city_local_date_at_capture": city_local_datetime(city, now_utc)
                    .date()
                    .isoformat(),
                }
            )
    return events, failures


def _token_requests(
    events: list[dict[str, Any]], capture_started_at_utc: str
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    rows: dict[str, dict[str, Any]] = {}
    hot: list[str] = []
    cold: list[str] = []
    for event in events:
        targets = event["strategy_targets"]
        for entry in event["entries"]:
            for outcome, token_id in (
                ("yes", entry.get("yes_token_id")),
                ("no", entry.get("no_token_id")),
            ):
                token_id = str(token_id or "")
                if not token_id or token_id in rows:
                    continue
                is_hot = (entry["label"], outcome) in targets
                rows[token_id] = {
                    "type": "weather_market_book",
                    "capture_reason": "strategy_hot" if is_hot else "full_market_ladder",
                    "capture_priority": "hot" if is_hot else "cold",
                    "snapshot_ts_utc": capture_started_at_utc,
                    "city": event["city"],
                    "event_date": event["target_date"],
                    "market_local_date": event["target_date"],
                    "city_local_date_at_snapshot": event["city_local_date_at_capture"],
                    "event_slug": event["event_slug"],
                    "event_id": event["event_id"],
                    "market_id": entry.get("market_id", ""),
                    "condition_id": entry.get("condition_id", ""),
                    "bracket": entry["label"],
                    "outcome": outcome,
                    "token_id": token_id,
                }
                (hot if is_hot else cold).append(token_id)
    return rows, hot, cold


def _fetch_priority_group(
    client: httpx.Client,
    request_rows: dict[str, dict[str, Any]],
    token_ids: list[str],
    *,
    top_n: int,
    deadline_monotonic: float | None,
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    selected = {token_id: request_rows[token_id] for token_id in token_ids}
    return legacy.fetch_token_orderbook_batch(
        client,
        selected,
        top_n=top_n,
        max_workers=1,
        deadline_monotonic=deadline_monotonic,
    )


def _archive_row(metadata: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    row = {**metadata, **book}
    raw_payload_hash = canonical_json_hash(row.get("raw") or {})
    row.setdefault("schema_version", "weather_orderbook_capture_v3")
    row.setdefault("producer", PRODUCER)
    row.setdefault("producer_build_id", PRODUCER_BUILD_ID)
    row.setdefault("producer_build_id_basis", PRODUCER_BUILD_ID_BASIS)
    row.setdefault("raw_payload_hash", raw_payload_hash)
    row.setdefault(
        "book_capture_id",
        canonical_json_hash(
            {
                "token_id": row.get("token_id"),
                "fetched_at_utc": row.get("fetched_at_utc"),
                "raw_payload_hash": raw_payload_hash,
            }
        ),
    )
    row.setdefault("detected_at_utc", row.get("response_received_at_utc"))
    row.setdefault("first_seen_at_utc", row.get("response_received_at_utc"))
    row.setdefault("available_at_utc", row.get("response_received_at_utc"))
    row.setdefault("source_lineage_status", "collector_exact_orderbook_response_v3")
    return row


def _ladder_payload(
    events: list[dict[str, Any]],
    records_by_token: dict[str, dict[str, Any]],
    *,
    batch_capture_id: str,
    capture_started_at_utc: str,
    available_at_utc: str,
) -> dict[str, Any]:
    ladder_records: list[dict[str, Any]] = []
    for event in events:
        rungs = []
        for entry in event["entries"]:
            yes = records_by_token.get(str(entry.get("yes_token_id") or ""), {})
            no = records_by_token.get(str(entry.get("no_token_id") or ""), {})
            rungs.append(
                {
                    "bracket": entry["label"],
                    "market_id": entry.get("market_id", ""),
                    "condition_id": entry.get("condition_id", ""),
                    "yes_token_id": entry.get("yes_token_id", ""),
                    "no_token_id": entry.get("no_token_id", ""),
                    "yes_book_capture_id": yes.get("book_capture_id"),
                    "no_book_capture_id": no.get("book_capture_id"),
                    "yes_book_status": yes.get("status", "missing"),
                    "no_book_status": no.get("status", "missing"),
                }
            )
        complete = bool(rungs) and all(
            rung["yes_book_status"] == "ok" and rung["no_book_status"] == "ok"
            for rung in rungs
        )
        ladder_records.append(
            {
                "city": event["city"],
                "target_date": event["target_date"],
                "event_slug": event["event_slug"],
                "event_id": event["event_id"],
                "batch_capture_id": batch_capture_id,
                "market_distribution_complete": complete,
                "two_sided_book_distribution_complete": complete,
                "rung_count": len(rungs),
                "rungs": rungs,
            }
        )
    return {
        "schema_version": LADDER_SCHEMA_VERSION,
        "producer": PRODUCER,
        "producer_build_id": PRODUCER_BUILD_ID,
        "batch_capture_id": batch_capture_id,
        "collection_started_at_utc": capture_started_at_utc,
        "available_at_utc": available_at_utc,
        "records": ladder_records,
        "summary": {
            "events": len(ladder_records),
            "complete_events": sum(
                bool(row["market_distribution_complete"]) for row in ladder_records
            ),
        },
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = legacy.parse_now_utc(args.now_utc)
    capture_started_at_utc = _utc_text(now_utc)
    output_root = Path(args.output_root)
    observation_path = Path(args.observation_cache) if args.observation_cache else None
    observations = _load_observation_index(observation_path)
    events, discovery_failures = discover_market_ladders(
        now_utc=now_utc,
        observation_index=observations,
        target_date=args.target_date,
    )
    request_rows, hot_tokens, cold_tokens = _token_requests(events, capture_started_at_utc)
    budget = float(args.orderbook_budget_sec)
    deadline = None if budget < 0 else time.monotonic() + budget
    client = httpx.Client(
        proxy=legacy.PROXY,
        timeout=legacy.PM_HTTP_TIMEOUT,
        limits=legacy.PM_HTTP_LIMITS,
        follow_redirects=True,
        trust_env=False,
    )
    try:
        fetched = _fetch_priority_group(
            client,
            request_rows,
            hot_tokens,
            top_n=args.orderbook_top_n,
            deadline_monotonic=deadline,
        )
        fetched.update(
            _fetch_priority_group(
                client,
                request_rows,
                cold_tokens,
                top_n=args.orderbook_top_n,
                deadline_monotonic=deadline,
            )
        )
    finally:
        client.close()

    records = [
        _archive_row(metadata, book)
        for metadata, book in (fetched[token_id] for token_id in request_rows if token_id in fetched)
    ]
    records_by_token = {str(row.get("token_id") or ""): row for row in records}
    batch_capture_id = canonical_json_hash(
        {
            "producer": PRODUCER,
            "collection_started_at_utc": capture_started_at_utc,
            "token_ids": sorted(request_rows),
        }
    )
    available_at_utc = _utc_text()
    bj = now_utc.astimezone(timezone.utc).timestamp() + 8 * 3600
    bj_dt = datetime.fromtimestamp(bj, timezone.utc)
    day = bj_dt.strftime("%Y-%m-%d")
    stamp = bj_dt.strftime("%Y%m%d_%H%M%S")
    archive_path = output_root / "batches" / day / f"market_books_{stamp}.jsonl.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    batch_status = (
        "ok"
        if records and len(records) == len(request_rows) and all(row.get("status") == "ok" for row in records)
        else "degraded"
    )
    latest_payload = {
        "schema_version": SCHEMA_VERSION,
        "status": batch_status,
        "producer": PRODUCER,
        "producer_build_id": PRODUCER_BUILD_ID,
        "producer_build_id_basis": PRODUCER_BUILD_ID_BASIS,
        "batch_capture_id": batch_capture_id,
        "collection_started_at_utc": capture_started_at_utc,
        "available_at_utc": available_at_utc,
        "archive_path": str(archive_path),
        "records": records,
        "summary": {
            "events": len(events),
            "tokens": len(request_rows),
            "hot_tokens": len(hot_tokens),
            "cold_tokens": len(cold_tokens),
            "ok_books": sum(row.get("status") == "ok" for row in records),
            "failed_books": sum(row.get("status") != "ok" for row in records),
            "discovery_failures": len(discovery_failures),
            "forecast_dependency": False,
        },
        "discovery_failures": discovery_failures,
    }
    _publish_json_atomic(output_root / "latest.json", latest_payload)

    ladder_payload = _ladder_payload(
        events,
        records_by_token,
        batch_capture_id=batch_capture_id,
        capture_started_at_utc=capture_started_at_utc,
        available_at_utc=available_at_utc,
    )
    ladder_path = (
        Path(args.market_ladder_root)
        / day
        / f"market_ladder_snapshot_{stamp}.json"
    )
    _publish_json_atomic(ladder_path, ladder_payload)
    _publish_json_atomic(Path(args.market_ladder_root) / "latest.json", ladder_payload)

    legacy_path = None
    if args.legacy_full_orderbook_root:
        legacy_path = (
            Path(args.legacy_full_orderbook_root)
            / day
            / f"orderbook_snapshot_{bj_dt.strftime('%Y%m%d_%H%M')}.jsonl.gz"
        )
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        if legacy_path.exists():
            legacy_path.unlink()
        try:
            os.link(archive_path, legacy_path)
        except OSError:
            with gzip.open(legacy_path, "wt", encoding="utf-8") as handle:
                for row in records:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    result = {
        "status": batch_status,
        "schema_version": SCHEMA_VERSION,
        "batch_capture_id": batch_capture_id,
        "latest_path": str(output_root / "latest.json"),
        "archive_path": str(archive_path),
        "market_ladder_path": str(ladder_path),
        "legacy_orderbook_path": str(legacy_path) if legacy_path else None,
        **latest_payload["summary"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--market-ladder-root", required=True)
    parser.add_argument("--legacy-full-orderbook-root", default="")
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--orderbook-top-n", type=int, default=20)
    parser.add_argument("--orderbook-budget-sec", type=float, default=240.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    collect(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
