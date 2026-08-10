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
from concurrent.futures import ThreadPoolExecutor
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
EVENT_CONTRACT_CACHE_SCHEMA_VERSION = "weather_market_event_contract_cache_v1"
PRODUCER = "weather_data_feed_service.market_books"
PRODUCER_BUILD_ID, PRODUCER_BUILD_ID_BASIS = producer_build_id(
    Path(__file__).resolve().parents[1]
)
DEFAULT_DISCOVERY_WORKERS = int(
    os.environ.get("WEATHER_MARKET_BOOKS_DISCOVERY_WORKERS", "8")
)
DEFAULT_DISCOVERY_RETRIES = int(
    os.environ.get("WEATHER_MARKET_BOOKS_DISCOVERY_RETRIES", "1")
)
DISCOVERY_RETRY_BACKOFF_SEC = float(
    os.environ.get("WEATHER_MARKET_BOOKS_DISCOVERY_RETRY_BACKOFF_SEC", "0.1")
)
EVENT_CONTRACT_MAX_AGE_SEC = float(
    os.environ.get("WEATHER_MARKET_EVENT_CONTRACT_MAX_AGE_SEC", "86400")
)
EVENT_CONTRACT_BOOTSTRAP_FILES = int(
    os.environ.get("WEATHER_MARKET_EVENT_CONTRACT_BOOTSTRAP_FILES", "72")
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


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strategy_targets_for_entries(
    entries: list[dict[str, Any]],
    *,
    unit: str,
    observation: dict[str, Any] | None,
) -> set[tuple[str, str]]:
    metar_max_f = _strategy_state_from_observation(observation).get(
        "metar_current_max_f"
    )
    if metar_max_f is None:
        return set()
    if unit == "C":
        running_native = (float(metar_max_f) - 32.0) * 5.0 / 9.0
        running_compare_f = (
            legacy.round_half_up_float(running_native) * 9.0 / 5.0 + 32.0
        )
    else:
        running_compare_f = legacy.round_half_up_float(float(metar_max_f))
    parsed: list[tuple[str, float, float]] = []
    for entry in entries:
        label = str(entry.get("label") or "")
        lo_f, hi_f = legacy.parse_bracket_bounds(label, unit)
        if lo_f is not None and hi_f is not None:
            parsed.append((label, lo_f, hi_f))
    targets: set[tuple[str, str]] = set()
    current = [
        (label, hi_f)
        for label, lo_f, hi_f in parsed
        if lo_f <= running_compare_f <= hi_f
    ]
    if current:
        current_label, _ = sorted(current, key=lambda item: item[1])[0]
        targets.add((current_label, "yes"))
        targets.add((current_label, "no"))
    higher = [(label, lo_f) for label, lo_f, _ in parsed if lo_f > running_compare_f]
    for label, _ in sorted(higher, key=lambda item: item[1])[:2]:
        targets.add((label, "no"))
    return targets


def _contract_row_from_ladder(
    row: dict[str, Any], *, discovered_at_utc: str
) -> dict[str, Any] | None:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    event_slug = str(row.get("event_slug") or "")
    entries = []
    for rung in row.get("rungs") or []:
        if not isinstance(rung, dict):
            continue
        label = str(rung.get("bracket") or "")
        yes_token_id = str(rung.get("yes_token_id") or "")
        no_token_id = str(rung.get("no_token_id") or "")
        if not label or not yes_token_id or not no_token_id:
            continue
        entries.append(
            {
                "label": label,
                "market_id": str(rung.get("market_id") or ""),
                "condition_id": str(rung.get("condition_id") or ""),
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
            }
        )
    if not city or not target_date or not event_slug or not entries:
        return None
    return {
        "city": city,
        "target_date": target_date,
        "event_slug": event_slug,
        "event_id": str(row.get("event_id") or ""),
        "entries": entries,
        "last_discovered_at_utc": discovered_at_utc,
    }


def _load_event_contracts(
    ladder_root: Path, *, now_utc: datetime
) -> dict[tuple[str, str], dict[str, Any]]:
    cache_path = ladder_root / "event_contract_cache.json"
    candidates: list[Path] = []
    if cache_path.exists():
        candidates.append(cache_path)
    archived = sorted(
        ladder_root.rglob("market_ladder_snapshot_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: max(1, EVENT_CONTRACT_BOOTSTRAP_FILES)]
    candidates.extend(archived)
    contracts: dict[tuple[str, str], dict[str, Any]] = {}
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        available_at = str(payload.get("available_at_utc") or "")
        for row in payload.get("records") or []:
            if not isinstance(row, dict):
                continue
            if path == cache_path:
                contract = dict(row)
            else:
                contract = _contract_row_from_ladder(
                    row, discovered_at_utc=available_at
                )
            if not contract:
                continue
            key = (
                str(contract.get("city") or ""),
                str(contract.get("target_date") or ""),
            )
            discovered_at = _parse_utc(contract.get("last_discovered_at_utc"))
            if (
                not all(key)
                or key in contracts
                or discovered_at is None
                or (now_utc - discovered_at).total_seconds()
                > EVENT_CONTRACT_MAX_AGE_SEC
            ):
                continue
            contracts[key] = contract
    return contracts


def _recover_discovery_contracts(
    *,
    events: list[dict[str, Any]],
    discovery_failures: list[dict[str, Any]],
    contracts: dict[tuple[str, str], dict[str, Any]],
    observation_index: dict[tuple[str, str], dict[str, Any]],
    now_utc: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    recovered = 0
    event_keys = {(str(row["city"]), str(row["target_date"])) for row in events}
    for failure in discovery_failures:
        if failure.get("discovery_failure_class") != "operational_failure":
            continue
        key = (str(failure.get("city") or ""), str(failure.get("target_date") or ""))
        contract = contracts.get(key)
        if contract is None or key in event_keys:
            continue
        if str(contract.get("event_slug") or "") != str(failure.get("slug") or ""):
            continue
        cfg = legacy.CITIES.get(key[0])
        entries = contract.get("entries") or []
        if not isinstance(cfg, dict) or not entries:
            continue
        events.append(
            {
                "city": key[0],
                "target_date": key[1],
                "event_slug": contract["event_slug"],
                "event_id": contract.get("event_id", ""),
                "condition_count": len(entries),
                "entries": entries,
                "strategy_targets": _strategy_targets_for_entries(
                    entries,
                    unit=str(cfg["unit"]),
                    observation=observation_index.get(key),
                ),
                "discovery_attempt_count": failure.get("discovery_attempt_count", 1),
                "market_discovery_source": "cached_event_contract",
                "market_contract_last_discovered_at_utc": contract.get(
                    "last_discovered_at_utc"
                ),
                "city_local_date_at_capture": city_local_datetime(key[0], now_utc)
                .date()
                .isoformat(),
            }
        )
        event_keys.add(key)
        failure["recovered_by_event_contract"] = True
        failure["event_contract_last_discovered_at_utc"] = contract.get(
            "last_discovered_at_utc"
        )
        recovered += 1
    events.sort(key=lambda row: (str(row.get("city")), str(row.get("target_date"))))
    return events, discovery_failures, recovered


def _publish_event_contracts(
    path: Path,
    *,
    contracts: dict[tuple[str, str], dict[str, Any]],
    events: list[dict[str, Any]],
    available_at_utc: str,
) -> None:
    for event in events:
        if event.get("market_discovery_source") == "cached_event_contract":
            continue
        key = (str(event.get("city") or ""), str(event.get("target_date") or ""))
        if not all(key):
            continue
        contracts[key] = {
            "city": key[0],
            "target_date": key[1],
            "event_slug": str(event.get("event_slug") or ""),
            "event_id": str(event.get("event_id") or ""),
            "entries": list(event.get("entries") or []),
            "last_discovered_at_utc": available_at_utc,
        }
    _publish_json_atomic(
        path,
        {
            "schema_version": EVENT_CONTRACT_CACHE_SCHEMA_VERSION,
            "producer": PRODUCER,
            "producer_build_id": PRODUCER_BUILD_ID,
            "available_at_utc": available_at_utc,
            "records": [contracts[key] for key in sorted(contracts)],
        },
    )


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
    max_workers: int = DEFAULT_DISCOVERY_WORKERS,
    retries: int = DEFAULT_DISCOVERY_RETRIES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover open weather ladders without touching any forecast provider."""

    requests: list[tuple[str, dict[str, Any], str, str]] = []
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
            requests.append((city, cfg, event_date, slug))

    def discover_one(
        request: tuple[str, dict[str, Any], str, str]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        city, cfg, event_date, slug = request
        status_code = 0
        raw: Any = None
        error = "event_unavailable"
        attempt_count = 0
        max_attempts = max(1, int(retries) + 1)
        for attempt_count in range(1, max_attempts + 1):
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
            if status_code == 200 and isinstance(raw, dict):
                break
            retryable = status_code == 0 or status_code in {408, 425, 429} or status_code >= 500
            if not retryable or attempt_count >= max_attempts:
                break
            if DISCOVERY_RETRY_BACKOFF_SEC > 0:
                time.sleep(DISCOVERY_RETRY_BACKOFF_SEC * (2 ** (attempt_count - 1)))

        if status_code != 200 or not isinstance(raw, dict):
            failure_class = (
                "expected_unavailable"
                if status_code == 200 and raw is None
                else "operational_failure"
            )
            return None, {
                "city": city,
                "target_date": event_date,
                "slug": slug,
                "status_code": status_code,
                "error": error or "event_unavailable",
                "discovery_failure_class": failure_class,
                "discovery_attempt_count": attempt_count,
            }
        _, entries = legacy.gamma_market_ladder(raw.get("markets") or [])
        if not entries:
            return None, {
                "city": city,
                "target_date": event_date,
                "slug": slug,
                "status_code": status_code,
                "error": "empty_market_ladder",
                "discovery_failure_class": "expected_unavailable",
                "discovery_attempt_count": attempt_count,
            }
        strategy_targets = legacy.orderbook_targets_for_strategy_live(
            raw.get("markets") or [],
            cfg["unit"],
            _strategy_state_from_observation(observation_index.get((city, event_date))),
        )
        return {
            "city": city,
            "target_date": event_date,
            "event_slug": slug,
            "event_id": raw.get("id", ""),
            "condition_count": len(entries),
            "entries": entries,
            "strategy_targets": strategy_targets,
            "discovery_attempt_count": attempt_count,
            "city_local_date_at_capture": city_local_datetime(city, now_utc)
            .date()
            .isoformat(),
        }, None

    events: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        discovered = executor.map(discover_one, requests)
        for event, failure in discovered:
            if event is not None:
                events.append(event)
            if failure is not None:
                failures.append(failure)
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
                    "market_discovery_source": event.get(
                        "market_discovery_source", "gamma_live"
                    ),
                    "market_contract_last_discovered_at_utc": event.get(
                        "market_contract_last_discovered_at_utc"
                    ),
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
    ladder_root = Path(args.market_ladder_root)
    event_contracts = _load_event_contracts(ladder_root, now_utc=now_utc)
    events, discovery_failures = discover_market_ladders(
        now_utc=now_utc,
        observation_index=observations,
        target_date=args.target_date,
        max_workers=getattr(args, "market_discovery_workers", DEFAULT_DISCOVERY_WORKERS),
        retries=getattr(args, "market_discovery_retries", DEFAULT_DISCOVERY_RETRIES),
    )
    events, discovery_failures, recovered_discovery_count = (
        _recover_discovery_contracts(
            events=events,
            discovery_failures=discovery_failures,
            contracts=event_contracts,
            observation_index=observations,
            now_utc=now_utc,
        )
    )
    contract_cache_path = ladder_root / "event_contract_cache.json"
    _publish_event_contracts(
        contract_cache_path,
        contracts=event_contracts,
        events=events,
        available_at_utc=capture_started_at_utc,
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
    hot_records = [row for row in records if row.get("capture_priority") == "hot"]
    cold_records = [row for row in records if row.get("capture_priority") == "cold"]
    operational_discovery_failures = [
        row
        for row in discovery_failures
        if row.get("discovery_failure_class") == "operational_failure"
    ]
    unrecovered_operational_discovery_failures = [
        row
        for row in operational_discovery_failures
        if not row.get("recovered_by_event_contract")
    ]
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

    books_complete = (
        bool(records)
        and len(records) == len(request_rows)
        and all(row.get("status") == "ok" for row in records)
    )
    if books_complete and not unrecovered_operational_discovery_failures:
        batch_status = (
            "ok_with_discovery_reuse" if recovered_discovery_count else "ok"
        )
    else:
        batch_status = "degraded"
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
            "hot_ok_books": sum(row.get("status") == "ok" for row in hot_records),
            "hot_failed_books": sum(row.get("status") != "ok" for row in hot_records),
            "cold_ok_books": sum(row.get("status") == "ok" for row in cold_records),
            "cold_failed_books": sum(row.get("status") != "ok" for row in cold_records),
            "ok_books": sum(row.get("status") == "ok" for row in records),
            "failed_books": sum(row.get("status") != "ok" for row in records),
            "discovery_failures": len(discovery_failures),
            "discovery_operational_failures": len(operational_discovery_failures),
            "discovery_operational_recovered": recovered_discovery_count,
            "discovery_operational_unrecovered": len(
                unrecovered_operational_discovery_failures
            ),
            "discovery_expected_unavailable": len(discovery_failures)
            - len(operational_discovery_failures),
            "discovery_retries_used": sum(
                max(0, int(row.get("discovery_attempt_count") or 1) - 1)
                for row in [*events, *discovery_failures]
            ),
            "forecast_dependency": False,
            "event_contract_cache_path": str(contract_cache_path),
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
        ladder_root
        / day
        / f"market_ladder_snapshot_{stamp}.json"
    )
    _publish_json_atomic(ladder_path, ladder_payload)
    _publish_json_atomic(ladder_root / "latest.json", ladder_payload)

    result = {
        "status": batch_status,
        "schema_version": SCHEMA_VERSION,
        "batch_capture_id": batch_capture_id,
        "latest_path": str(output_root / "latest.json"),
        "archive_path": str(archive_path),
        "market_ladder_path": str(ladder_path),
        **latest_payload["summary"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--market-ladder-root", required=True)
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--orderbook-top-n", type=int, default=20)
    parser.add_argument("--orderbook-budget-sec", type=float, default=240.0)
    parser.add_argument(
        "--market-discovery-workers", type=int, default=DEFAULT_DISCOVERY_WORKERS
    )
    parser.add_argument(
        "--market-discovery-retries", type=int, default=DEFAULT_DISCOVERY_RETRIES
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    collect(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
