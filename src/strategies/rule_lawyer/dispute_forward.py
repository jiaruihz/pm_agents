"""Zero-notional forward capture for Polymarket UMA disputes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from src.platform.market_data.capture_contract import (
    materialize_orderbook_capture,
    utc_now_text,
)
from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.identity import canonical_json_hash
from src.platform.market_data.market_group import binary_market_group_snapshot
from src.platform.storage.jsonl import append_jsonl_row
from src.platform.clients.uma import SUBGRAPHS, fetch_disputed_requests_since, fetch_request_rounds
from src.platform.clients.polymarket_bulletin import (
    fetch_creator_updates,
    fetch_question_and_updates,
)
from src.strategies.rule_lawyer.contract_corpus import (
    build_contract_corpus,
    build_dispute_thesis,
    bulletin_adapter_from_ancillary,
)
from src.strategies.rule_lawyer.dispute import (
    NON_EVIDENCE_DOMAINS,
    URL_RE,
    classify_dispute_case,
    executable_bid_vwap,
    executable_vwap,
    opportunity_cluster_id,
)
from src.strategies.rule_lawyer.dispute_verdict import resolve_rule_verdict


ONE = 10**18
HALF = 5 * 10**17
TOO_EARLY = -(2**255)
MARKET_ID_RE = re.compile(r"(?i)market[_ ]?id\s*[:=]\s*(\d+)")
REQUEST_QUESTION_ID_RE = re.compile(r"(0x[a-fA-F0-9]{64})$")
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
CLOB_BASE = "https://clob.polymarket.com"


def utc_iso(ts: int | float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat()


def decode_ancillary(value: str) -> str:
    try:
        return bytes.fromhex(value.removeprefix("0x")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def extract_market_id(text: str) -> str:
    match = MARKET_ID_RE.search(text)
    return match.group(1) if match else ""


def extract_title(text: str) -> str:
    for pattern in (
        r"(?is)q:\s*title:\s*(.*?)(?:,\s*description:|,\s*market_id:|;marketId:)",
        r"(?is)q:(.*?)(?:;marketId:|,\s*market_id:)",
    ):
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,")
    return ""


def extract_request_question_id(request_id: Any) -> str:
    """Return the adapter question key encoded at the end of the UMA request id."""
    match = REQUEST_QUESTION_ID_RE.search(str(request_id or ""))
    return match.group(1).lower() if match else ""


def bulletin_updates_fingerprint(updates: list[dict[str, Any]]) -> str:
    basis = [
        (
            int(update.get("timestamp") or 0),
            str(update.get("update_hex") or ""),
            str(update.get("text") or ""),
        )
        for update in updates
    ]
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def request_rounds_fingerprint(rounds: list[dict[str, Any]]) -> str:
    """Hash only oracle state fields; delivery metadata cannot create a change."""

    basis = [
        {
            key: row.get(key)
            for key in (
                "id",
                "requestTimestamp",
                "proposalTimestamp",
                "disputeTimestamp",
                "settlementTimestamp",
                "proposedPrice",
                "settlementPrice",
                "proposalHash",
                "disputeHash",
                "settlementHash",
                "proposalExpirationTimestamp",
            )
        }
        for row in rounds
    ]
    return canonical_json_hash(basis)


def request_lifecycle_fingerprint(row: dict[str, Any]) -> str:
    """Identify one proposal/dispute/settlement lifecycle for overlap-safe polling."""

    return canonical_json_hash(
        {
            key: row.get(key)
            for key in (
                "id",
                "requestTimestamp",
                "proposalTimestamp",
                "disputeTimestamp",
                "settlementTimestamp",
                "proposedPrice",
                "settlementPrice",
                "proposalHash",
                "disputeHash",
                "settlementHash",
                "proposalExpirationTimestamp",
            )
        }
    )


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def request_class(settlement: Any, proposed: Any) -> str:
    if settlement in (None, ""):
        return "unsettled"
    value = int(settlement)
    proposal = int(proposed) if proposed not in (None, "") else None
    if value == TOO_EARLY:
        return "too_early"
    if value == HALF:
        return "unknown_50_50"
    if value in (0, ONE) and proposal in (0, ONE):
        return "upheld" if value == proposal else "binary_flip"
    return "other"


def get_json(
    url: str,
    *,
    allow_404: bool = False,
    attempts: int = 4,
    retry_base_seconds: float = 0.25,
) -> Any:
    """GET JSON with bounded retry for transient upstream failures."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = requests.get(url, timeout=30)
            if allow_404 and response.status_code == 404:
                return {}
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise
            if attempt + 1 < max(1, attempts):
                time.sleep(retry_base_seconds * (2**attempt))
    assert last_error is not None
    raise last_error


def get_book(token_id: str) -> dict[str, Any]:
    payload = get_json(f"{CLOB_BASE}/book?token_id={token_id}", allow_404=True)
    return payload if isinstance(payload, dict) else {}


def get_book_with_capture(
    token_id: str, *, request_batch_capture_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch one REST book with the shared exact-clock/content identity."""

    request_started_at_utc = utc_now_text()
    raw_book = get_book(token_id)
    response_received_at_utc = utc_now_text()
    parsed_at_utc = utc_now_text()
    return raw_book, materialize_orderbook_capture(
        token_id=token_id,
        raw_book=raw_book,
        request_started_at_utc=request_started_at_utc,
        response_received_at_utc=response_received_at_utc,
        parsed_at_utc=parsed_at_utc,
        request_batch_capture_id=request_batch_capture_id,
    )


def normalized_levels(book: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for side in ("bid", "ask"):
        entries = book.get(side + "s") or []
        for entry in entries:
            try:
                rows.append(
                    {
                        "side": side,
                        "price": float(entry["price"]),
                        "size": float(entry["size"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def evidence_urls(market: dict[str, Any]) -> list[str]:
    values = [
        str(market.get("resolutionSource") or ""),
        str(market.get("description") or ""),
        str(market.get("rules") or ""),
    ]
    urls: list[str] = []
    for value in values:
        for url in URL_RE.findall(value):
            cleaned = url.rstrip(".\"'")
            host = (urlparse(cleaned).hostname or "").lower().removeprefix("www.")
            if host and host not in NON_EVIDENCE_DOMAINS and cleaned not in urls:
                urls.append(cleaned)
    return urls[:1]


def fetch_evidence(url: str) -> dict[str, Any]:
    fetched_at = utc_iso()
    try:
        response = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "pm-agents-dispute-research/1.0"},
        )
        content = response.content[:250_000]
        content_type = response.headers.get("content-type", "")
        text = content.decode(response.encoding or "utf-8", "replace") if "text" in content_type or "json" in content_type else ""
        return {
            "url": url,
            "fetched_at_utc": fetched_at,
            "status_code": response.status_code,
            "content_type": content_type,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "truncated": len(response.content) > len(content),
            "text": text,
        }
    except Exception as exc:
        return {"url": url, "fetched_at_utc": fetched_at, "error": f"{type(exc).__name__}: {exc}"}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    append_jsonl_row(path, row)


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_state(
    path: Path,
    events_path: Path | None = None,
    snapshots_path: Path | None = None,
) -> dict[str, Any]:
    if not path.exists():
        state = {
            "schema_version": "dispute_forward_state_v1",
            "seen_case_ids": [],
            "tracked": {},
        }
    else:
        state = json.loads(path.read_text())
    # Raw event append precedes the mutable state write.  Always merge it back
    # so a crash in between cannot append the same first-seen event twice.
    seen = set(state.get("seen_case_ids") or ())
    tracked = dict(state.get("tracked") or {})
    retired = set(state.get("retired_case_ids") or ())
    if events_path and events_path.exists():
        for line in events_path.read_text().splitlines():
            try:
                event = json.loads(line)
                case_id = str(event["case_id"])
                seen.add(case_id)
                if case_id not in retired and isinstance(event.get("raw_request"), dict):
                    tracked.setdefault(case_id, event["raw_request"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
    state["seen_case_ids"] = sorted(seen)
    state["tracked"] = tracked
    if "last_capture_ts_by_case" not in state:
        last_capture: dict[str, int] = {}
        if snapshots_path and snapshots_path.exists():
            for line in snapshots_path.read_text().splitlines():
                try:
                    snapshot = json.loads(line)
                    captured = datetime.fromisoformat(snapshot["captured_at_utc"].replace("Z", "+00:00"))
                    last_capture[str(snapshot["case_id"])] = int(captured.timestamp())
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        state["last_capture_ts_by_case"] = last_capture
    if snapshots_path and snapshots_path.exists() and not state.get(
        "lifecycle_state_hydrated_from_snapshots"
    ):
        latest: dict[str, dict[str, Any]] = {}
        for line in snapshots_path.read_text().splitlines():
            try:
                snapshot = json.loads(line)
                case_id = str(snapshot["case_id"])
                if str(snapshot.get("captured_at_utc") or "") >= str(
                    latest.get(case_id, {}).get("captured_at_utc") or ""
                ):
                    latest[case_id] = snapshot
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        pending = set((state.get("pending_trigger_reason_by_case") or {}).keys())
        active_shadow = active_shadow_case_ids(path.parent)
        tracked = dict(state.get("tracked") or {})
        request_fingerprints = dict(
            state.get("request_state_fingerprint_by_case") or {}
        )
        last_request_polls = dict(
            state.get("last_request_state_poll_ts_by_case") or {}
        )
        terminal: set[str] = set()
        for case_id in tracked:
            snapshot = latest.get(case_id)
            if snapshot is None or case_id in pending or case_id in active_shadow:
                continue
            fingerprint = str(snapshot.get("request_rounds_fingerprint") or "")
            if fingerprint:
                request_fingerprints.setdefault(case_id, fingerprint)
                try:
                    captured = datetime.fromisoformat(
                        str(snapshot["captured_at_utc"]).replace("Z", "+00:00")
                    )
                    last_request_polls.setdefault(case_id, int(captured.timestamp()))
                except (KeyError, TypeError, ValueError):
                    pass
            request_open = (
                (snapshot.get("proposal") or {}).get("request_class") == "unsettled"
            )
            market_open = not bool((snapshot.get("market_status") or {}).get("closed"))
            if not request_open and not market_open:
                terminal.add(case_id)
        retained = set(tracked) - terminal
        state["tracked"] = {
            case_id: row for case_id, row in tracked.items() if case_id in retained
        }
        state["request_state_fingerprint_by_case"] = request_fingerprints
        state["last_request_state_poll_ts_by_case"] = last_request_polls
        for key in (
            "last_capture_ts_by_case",
            "bulletin_watches",
            "last_bulletin_poll_ts_by_case",
            "request_state_fingerprint_by_case",
            "last_request_state_poll_ts_by_case",
        ):
            values = state.get(key)
            if isinstance(values, dict):
                state[key] = {
                    case_id: value
                    for case_id, value in values.items()
                    if case_id in retained
                }
        state["lifecycle_state_hydrated_from_snapshots"] = True
        state["terminal_cases_pruned_from_tracking"] = len(terminal)
        state["retired_case_ids"] = sorted(retired | terminal)
    retired = set(state.get("retired_case_ids") or ())
    retired_fingerprints = dict(
        state.get("retired_lifecycle_fingerprint_by_case") or {}
    )
    missing_retired_fingerprints = retired - set(retired_fingerprints)
    if snapshots_path and snapshots_path.exists() and missing_retired_fingerprints:
        latest_retired: dict[str, dict[str, Any]] = {}
        for line in snapshots_path.read_text().splitlines():
            try:
                snapshot = json.loads(line)
                case_id = str(snapshot["case_id"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if case_id not in missing_retired_fingerprints:
                continue
            if str(snapshot.get("captured_at_utc") or "") >= str(
                latest_retired.get(case_id, {}).get("captured_at_utc") or ""
            ):
                latest_retired[case_id] = snapshot
        for case_id, snapshot in latest_retired.items():
            raw_request = snapshot.get("raw_request")
            if isinstance(raw_request, dict):
                retired_fingerprints[case_id] = request_lifecycle_fingerprint(
                    raw_request
                )
    state["retired_lifecycle_fingerprint_by_case"] = {
        case_id: fingerprint
        for case_id, fingerprint in retired_fingerprints.items()
        if case_id in retired
    }
    return state


def bulletin_poll_interval_seconds(dispute_ts: int, now_ts: int) -> int:
    """Adaptive reconciliation cadence for unresolved creator bulletins."""

    age = max(0, now_ts - dispute_ts)
    if age <= 6 * 3600:
        return 60
    if age <= 48 * 3600:
        return 300
    return 1800


def request_state_poll_interval_seconds(dispute_ts: int, now_ts: int) -> int:
    """Cadence for book-free UMA settlement/reproposal reconciliation."""

    age = max(0, now_ts - dispute_ts)
    if age <= 6 * 3600:
        return 300
    if age <= 48 * 3600:
        return 900
    return 1800


def capture_demands_for_snapshot(
    snapshot: dict[str, Any],
    *,
    reason: str,
    requested_at_utc: str,
    hot_seconds: int = 600,
    trigger_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Declare hot token capture without starting a second market-data writer."""

    requested = datetime.fromisoformat(requested_at_utc.replace("Z", "+00:00"))
    expires = (requested + timedelta(seconds=hot_seconds)).isoformat().replace("+00:00", "Z")
    if not trigger_event_id:
        raw_request = snapshot.get("raw_request") or {}
        bulletin = snapshot.get("bulletin_state") or {}
        trigger_event_id = canonical_json_hash(
            {
                "case_id": snapshot.get("case_id"),
                "reason": reason,
                "dispute_hash": raw_request.get("disputeHash"),
                "dispute_timestamp": raw_request.get("disputeTimestamp"),
                "bulletin_update_fingerprint": bulletin.get("update_fingerprint"),
                "contract_corpus_sha256": (snapshot.get("contract_corpus") or {}).get(
                    "contract_corpus_sha256"
                ),
            }
        )
    rows: list[dict[str, Any]] = []
    for token_id in snapshot.get("tokens") or []:
        demand = CaptureDemand.create(
            consumer_id="rule_lawyer_dispute_forward",
            strategy_key="rule_lawyer.dispute_repricing",
            condition_id=str(snapshot.get("condition_id") or ""),
            token_id=str(token_id),
            reason=reason,
            priority="P0",
            requested_at_utc=requested_at_utc,
            expires_at_utc=expires,
            desired_transport="REST_WS",
            requested_checkpoints_seconds=(0, 30, 120, 300, 900, 3600),
            trigger_event_id=trigger_event_id,
            metadata={
                "case_id": snapshot.get("case_id"),
                "market_id": snapshot.get("market_id"),
                "rest_owner": "rule_lawyer_dispute_forward",
                "ws_owner": "shared_market_books_ws",
                "market_group_snapshot_id": (
                    snapshot.get("market_group_snapshot") or {}
                ).get("group_snapshot_id"),
            },
        )
        rows.append(demand.to_dict())
    return rows


def capture_checkpoint_work(
    output_root: Path, *, now_ts: int
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Derive due REST checkpoints solely from append-only demand/receipt raw."""

    receipts = {
        (str(row.get("demand_id") or ""), int(row.get("checkpoint_seconds") or 0))
        for row in read_jsonl_rows(output_root / "capture_checkpoint_receipts.jsonl")
    }
    due: dict[str, list[dict[str, Any]]] = {}
    outstanding_cases: set[str] = set()
    for demand in read_jsonl_rows(output_root / "capture_demands.jsonl"):
        if str(demand.get("desired_transport") or "") not in {"REST", "REST_WS"}:
            continue
        metadata = demand.get("metadata") or {}
        case_id = str(metadata.get("case_id") or "")
        demand_id = str(demand.get("demand_id") or "")
        token_id = str(demand.get("token_id") or "")
        try:
            requested_at = datetime.fromisoformat(
                str(demand["requested_at_utc"]).replace("Z", "+00:00")
            )
            requested_ts = int(requested_at.timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        if not case_id or not demand_id or not token_id:
            continue
        for checkpoint in demand.get("requested_checkpoints_seconds") or ():
            checkpoint_seconds = int(checkpoint)
            key = (demand_id, checkpoint_seconds)
            if key in receipts:
                continue
            outstanding_cases.add(case_id)
            due_at_ts = requested_ts + checkpoint_seconds
            if due_at_ts <= now_ts:
                due.setdefault(case_id, []).append(
                    {
                        "demand_id": demand_id,
                        "token_id": token_id,
                        "trigger_event_id": demand.get("trigger_event_id"),
                        "checkpoint_seconds": checkpoint_seconds,
                        "due_at_ts": due_at_ts,
                    }
                )
    return due, outstanding_cases


def write_capture_checkpoint_receipts(
    output_root: Path,
    snapshot: dict[str, Any],
    work_items: list[dict[str, Any]],
    *,
    captured_at_ts: int,
) -> int:
    existing = {
        str(row.get("receipt_id") or "")
        for row in read_jsonl_rows(output_root / "capture_checkpoint_receipts.jsonl")
    }
    written = 0
    for item in work_items:
        token_id = str(item["token_id"])
        book = (snapshot.get("book_captures") or {}).get(token_id) or {}
        book_snapshot_id = str(book.get("book_capture_id") or "")
        if not book_snapshot_id:
            continue
        checkpoint = int(item["checkpoint_seconds"])
        receipt_id = canonical_json_hash(
            {
                "schema_version": "dispute_rest_checkpoint_receipt_v1",
                "demand_id": item["demand_id"],
                "checkpoint_seconds": checkpoint,
            }
        )
        if receipt_id in existing:
            continue
        lateness = max(0, captured_at_ts - int(item["due_at_ts"]))
        append_jsonl(
            output_root / "capture_checkpoint_receipts.jsonl",
            {
                "schema_version": "dispute_rest_checkpoint_receipt_v1",
                "receipt_id": receipt_id,
                "demand_id": item["demand_id"],
                "case_id": snapshot.get("case_id"),
                "token_id": token_id,
                "trigger_event_id": item.get("trigger_event_id"),
                "checkpoint_seconds": checkpoint,
                "due_at_utc": utc_iso(int(item["due_at_ts"])),
                "captured_at_utc": snapshot.get("captured_at_utc"),
                "lateness_seconds": lateness,
                "capture_status": "on_time" if lateness <= 30 else "late",
                "book_snapshot_id": book_snapshot_id,
                "owner": "rule_lawyer_dispute_forward",
            },
        )
        existing.add(receipt_id)
        written += 1
    return written


def active_shadow_case_ids(output_root: Path) -> set[str]:
    """Cases with an opened shadow position and no settlement markout yet."""
    positions_path = output_root / "shadow_positions.jsonl"
    markouts_path = output_root / "shadow_markouts.jsonl"
    if not positions_path.exists():
        return set()
    settled: set[str] = set()
    if markouts_path.exists():
        for line in markouts_path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("checkpoint") == "settlement":
                settled.add(str(row.get("position_id") or ""))
    active: set[str] = set()
    for line in positions_path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("position_id") or "") not in settled:
            active.add(str(row.get("case_id") or ""))
    return {case_id for case_id in active if case_id}


def stamp_contract_first_seen(
    snapshot: dict[str, Any], first_seen: dict[str, str]
) -> None:
    """Persist first observation of each contract fragment across recaptures."""
    case_id = str(snapshot.get("case_id") or "")
    for fragment in snapshot.get("contract_corpus", {}).get("fragments", []):
        key = "|".join(
            (
                case_id,
                str(fragment.get("origin") or ""),
                str(fragment.get("content_sha256") or ""),
                str(fragment.get("effective_at_utc") or ""),
            )
        )
        first_seen.setdefault(key, str(snapshot.get("captured_at_utc") or ""))
        fragment["first_observed_at_utc"] = first_seen[key]


def capture_case(
    raw: dict[str, Any],
    *,
    captured_at_ts: int,
    fetch_source_evidence: bool = True,
    request_rounds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ancillary_text = decode_ancillary(str(raw.get("ancillaryData") or ""))
    market_id = extract_market_id(ancillary_text)
    rounds = request_rounds or fetch_request_rounds(
        str(raw["subgraph"]), str(raw["ancillaryData"])
    )
    current = next((row for row in rounds if row.get("id") == raw.get("id")), raw)
    market = get_json(GAMMA_MARKET_URL.format(market_id=market_id), allow_404=True) if market_id else {}
    market = market if isinstance(market, dict) else {}
    tokens = [str(value) for value in parse_json_list(market.get("clobTokenIds"))]
    outcomes = [str(value) for value in parse_json_list(market.get("outcomes"))]
    outcome_prices = [float(value) for value in parse_json_list(market.get("outcomePrices"))]
    condition_id = str(market.get("conditionId") or "")
    request_batch_capture_id = canonical_json_hash(
        {
            "collector": "rule_lawyer_dispute_forward",
            "market_id": market_id,
            "condition_id": condition_id,
            "captured_at_ts": captured_at_ts,
            "tokens": tokens,
        }
    )
    book_pairs = {
        token: get_book_with_capture(
            token, request_batch_capture_id=request_batch_capture_id
        )
        for token in tokens
    }
    books = {token: pair[0] for token, pair in book_pairs.items()}
    book_captures = {token: pair[1] for token, pair in book_pairs.items()}
    clob_market = get_json(f"{CLOB_BASE}/clob-markets/{condition_id}", allow_404=True) if condition_id else {}
    fee_rates = {
        token: get_json(f"{CLOB_BASE}/fee-rate/{token}", allow_404=True)
        for token in tokens
    }
    proposed = int(current["proposedPrice"]) if current.get("proposedPrice") not in (None, "") else None
    proposed_binary = proposed // ONE if proposed in (0, ONE) else None
    reverse_index = proposed_binary if proposed_binary in (0, 1) and len(tokens) == 2 else None
    reverse_token = tokens[reverse_index] if reverse_index is not None else ""
    reverse_levels = normalized_levels(books.get(reverse_token, {})) if reverse_token else []
    market_theme = "sports_esports" if str(market.get("sportsMarketType") or "") else ""
    issue_row = {
        "title": str(market.get("question") or extract_title(ancillary_text)),
        "ancillary_text": ancillary_text,
        "rules": str(market.get("description") or market.get("rules") or ""),
        "resolution_source": str(market.get("resolutionSource") or ""),
        "market_theme_v1": market_theme,
    }
    issue = classify_dispute_case(issue_row).to_dict()
    cluster_id = opportunity_cluster_id(
        {
            "market_id": market_id,
            "slug": market.get("slug"),
            "market_theme_v1": market_theme,
        }
    )
    captured_at = utc_iso(captured_at_ts)
    dispute_ts = int(current.get("disputeTimestamp") or raw.get("disputeTimestamp") or 0)
    proposal_ts = int(current.get("proposalTimestamp") or 0)
    game_start_raw = market.get("gameStartTime") or market.get("eventStartTime")
    try:
        game_start_ts = int(datetime.fromisoformat(str(game_start_raw).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        game_start_ts = 0
    rule_verdict = {
        "status": "prospective_too_early" if proposal_ts and game_start_ts and proposal_ts < game_start_ts else "unverified",
        "winning_outcome": None,
        "source_url": None,
        "observed_at_utc": captured_at,
        "reason": (
            "proposal predates scheduled sports event start"
            if proposal_ts and game_start_ts and proposal_ts < game_start_ts
            else "no deterministic source resolver has verified the market outcome"
        ),
    }
    bulletin = None
    bulletin_error = None
    bulletin_adapter = bulletin_adapter_from_ancillary(ancillary_text)
    gamma_question_id = str(market.get("questionID") or "")
    question_id = extract_request_question_id(current.get("id") or raw.get("id")) or gamma_question_id
    if bulletin_adapter:
        if not question_id:
            bulletin_error = "Gamma market did not expose questionID"
        else:
            try:
                bulletin = fetch_question_and_updates(
                    str(current.get("requester") or bulletin_adapter),
                    question_id,
                )
            except Exception as exc:
                bulletin_error = f"{type(exc).__name__}: {exc}"
    contract_corpus = build_contract_corpus(
        ancillary_text=ancillary_text,
        market=market,
        observed_at_utc=captured_at,
        observed_at_ts=captured_at_ts,
        bulletin=bulletin,
        bulletin_error=bulletin_error,
    )
    snapshot = {
        "schema_version": "dispute_forward_snapshot_v1",
        "case_id": f"{raw['subgraph']}:{raw['id']}",
        "captured_at_utc": captured_at,
        "capture_delay_seconds": captured_at_ts - dispute_ts if dispute_ts else None,
        "subgraph": raw["subgraph"],
        "request_id": raw["id"],
        "request_rounds_fingerprint": request_rounds_fingerprint(rounds),
        "market_id": market_id,
        "market_slug": market.get("slug"),
        "group_item_title": market.get("groupItemTitle"),
        "sports_market_type": market.get("sportsMarketType"),
        "opportunity_cluster_id": cluster_id,
        "condition_id": condition_id,
        "question_id": question_id,
        "gamma_question_id": gamma_question_id,
        "bulletin_state": {
            "enabled": bool(bulletin_adapter),
            "adapter": str(bulletin.get("adapter") or bulletin_adapter or "").lower()
            if bulletin
            else str(bulletin_adapter or "").lower(),
            "question_id": str(bulletin.get("question_id") or question_id or "").lower()
            if bulletin
            else str(question_id or "").lower(),
            "creator": str(bulletin.get("creator") or "").lower() if bulletin else "",
            "update_count": len(bulletin.get("updates") or []) if bulletin else None,
            "update_fingerprint": bulletin_updates_fingerprint(bulletin.get("updates") or [])
            if bulletin
            else None,
            "fetch_error": bulletin_error,
        },
        "title": issue_row["title"],
        "proposal": {
            "proposed_binary": proposed_binary,
            "proposal_ts": proposal_ts,
            "dispute_ts": dispute_ts,
            "settlement_price": current.get("settlementPrice"),
            "request_class": request_class(current.get("settlementPrice"), current.get("proposedPrice")),
            "round_count_observed": len(rounds),
            "bond_raw": current.get("bond"),
            "proposer": current.get("proposer"),
            "disputer": current.get("disputer"),
        },
        "market_status": {
            "active": market.get("active"),
            "closed": market.get("closed"),
            "uma_resolution_status": market.get("umaResolutionStatus"),
            "game_start_time": market.get("gameStartTime") or market.get("eventStartTime"),
            "end_date": market.get("endDate"),
        },
        "outcomes": outcomes,
        "outcome_prices": outcome_prices,
        "tokens": tokens,
        "reverse_index": reverse_index,
        "reverse_token": reverse_token,
        "issue_features": issue,
        "rule_verdict": rule_verdict,
        "contract_corpus": contract_corpus,
        "rules_snapshot": {
            "ancillary_text": ancillary_text,
            "description": market.get("description"),
            "rules": market.get("rules"),
            "resolution_source": market.get("resolutionSource"),
        },
        "clob_market": clob_market,
        "fee_rates": fee_rates,
        "books": books,
        "book_captures": book_captures,
        "request_batch_capture_id": request_batch_capture_id,
        "reverse_executable_vwap": {
            str(quantity): executable_vwap(reverse_levels, quantity)
            for quantity in (5, 25, 100)
        },
        "reverse_executable_bid_vwap": {
            str(quantity): executable_bid_vwap(reverse_levels, quantity)
            for quantity in (5, 25, 100)
        },
        "evidence": (
            [fetch_evidence(url) for url in evidence_urls(market)]
            if fetch_source_evidence else []
        ),
        "raw_request": current,
    }
    if market_id and condition_id and len(outcomes) == 2 and len(tokens) == 2:
        available_at = max(
            (
                str(row.get("available_at_utc") or captured_at)
                for row in book_captures.values()
            ),
            default=captured_at,
        )
        snapshot["market_group_snapshot"] = binary_market_group_snapshot(
            group_id=f"condition:{condition_id}",
            market_id=market_id,
            condition_id=condition_id,
            outcomes=outcomes,
            token_ids=tokens,
            captured_at_utc=captured_at,
            available_at_utc=available_at,
            book_snapshot_ids={
                token: book_captures[token].get("book_capture_id") for token in tokens
            },
            capture_batch_id=request_batch_capture_id,
            metadata={
                "case_id": snapshot["case_id"],
                "opportunity_cluster_id": cluster_id,
                "market_slug": snapshot["market_slug"],
            },
        ).to_dict()
    snapshot["rule_verdict"] = resolve_rule_verdict(snapshot)
    snapshot["dispute_thesis"] = build_dispute_thesis(snapshot)
    return snapshot


def run_capture_once(
    output_root: Path,
    *,
    lookback_hours: float = 72,
    now_ts: int | None = None,
    fetch_source_evidence: bool = True,
    workers: int = 6,
    force_recapture: bool = False,
    max_bootstrap_reconciliations_per_run: int = 8,
) -> dict[str, Any]:
    captured_at_ts = int(now_ts or time.time())
    state_path = output_root / "state.json"
    events_path = output_root / "events.jsonl"
    initial_bootstrap = not state_path.exists() and not events_path.exists()
    state = load_state(
        state_path,
        events_path,
        output_root / "snapshots.jsonl",
    )
    since = int(state.get("last_dispute_ts") or (captured_at_ts - lookback_hours * 3600))
    fetched: list[dict[str, Any]] = []
    for subgraph in SUBGRAPHS:
        fetched.extend(fetch_disputed_requests_since(subgraph, max(0, since - 2), until_ts=captured_at_ts))
    tracked: dict[str, dict[str, Any]] = dict(state.get("tracked") or {})
    seen = set(state.get("seen_case_ids") or [])
    pending_trigger_reason: dict[str, str] = {
        str(case_id): str(reason)
        for case_id, reason in (state.get("pending_trigger_reason_by_case") or {}).items()
        if reason
    }
    retired_case_ids = set(state.get("retired_case_ids") or ())
    retired_lifecycle_fingerprints = dict(
        state.get("retired_lifecycle_fingerprint_by_case") or {}
    )
    existing_demand_ids = {
        str(row.get("demand_id") or "")
        for row in read_jsonl_rows(output_root / "capture_demands.jsonl")
    }
    bootstrap_historical_events = 0
    for row in fetched:
        ancillary = decode_ancillary(str(row.get("ancillaryData") or ""))
        if not extract_market_id(ancillary):
            continue
        case_id = f"{row['subgraph']}:{row['id']}"
        if (
            case_id in retired_case_ids
            and retired_lifecycle_fingerprints.get(case_id)
            == request_lifecycle_fingerprint(row)
        ):
            continue
        retired_case_ids.discard(case_id)
        retired_lifecycle_fingerprints.pop(case_id, None)
        tracked[case_id] = row
        if case_id not in seen:
            dispute_ts = int(row.get("disputeTimestamp") or 0)
            historical_bootstrap = initial_bootstrap and (
                not dispute_ts or captured_at_ts - dispute_ts > 300
            )
            append_jsonl(events_path, {
                "schema_version": "dispute_forward_event_v1",
                "first_observed_at_utc": utc_iso(captured_at_ts),
                "observation_kind": (
                    "bootstrap_historical"
                    if historical_bootstrap
                    else "realtime_first_seen"
                ),
                "dispute_age_seconds_at_first_observation": (
                    captured_at_ts - dispute_ts if dispute_ts else None
                ),
                "case_id": case_id,
                "raw_request": row,
            })
            seen.add(case_id)
            if historical_bootstrap:
                bootstrap_historical_events += 1
            else:
                pending_trigger_reason[case_id] = "dispute_first_seen"

    last_capture = {
        str(case_id): int(ts)
        for case_id, ts in (state.get("last_capture_ts_by_case") or {}).items()
    }
    contract_first_seen = {
        str(key): str(value)
        for key, value in (state.get("contract_fragment_first_seen_at") or {}).items()
    }
    bulletin_watches: dict[str, dict[str, Any]] = dict(state.get("bulletin_watches") or {})
    last_bulletin_poll = {
        str(case_id): int(ts)
        for case_id, ts in (state.get("last_bulletin_poll_ts_by_case") or {}).items()
    }
    bulletin_changed_cases: set[str] = set()
    bulletin_poll_errors: list[dict[str, str]] = []
    poll_due = {
        case_id: watch
        for case_id, watch in bulletin_watches.items()
        if case_id in tracked
        and captured_at_ts - int(last_bulletin_poll.get(case_id, 0))
        >= bulletin_poll_interval_seconds(
            int((tracked.get(case_id) or {}).get("disputeTimestamp") or 0),
            captured_at_ts,
        )
        and watch.get("adapter")
        and watch.get("question_id")
        and watch.get("creator")
    }
    with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as pool:
        futures = {
            pool.submit(
                fetch_creator_updates,
                str(watch["adapter"]),
                str(watch["question_id"]),
                str(watch["creator"]),
            ): case_id
            for case_id, watch in sorted(poll_due.items())
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                updates = future.result()
                last_bulletin_poll[case_id] = captured_at_ts
                if bulletin_updates_fingerprint(updates) != bulletin_watches[case_id].get(
                    "update_fingerprint"
                ):
                    bulletin_changed_cases.add(case_id)
                    pending_trigger_reason[case_id] = "official_update"
            except Exception as exc:
                bulletin_poll_errors.append(
                    {"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"}
                )
    request_state_fingerprints: dict[str, str] = {
        str(case_id): str(value)
        for case_id, value in (state.get("request_state_fingerprint_by_case") or {}).items()
        if value
    }
    last_request_state_poll: dict[str, int] = {
        str(case_id): int(value)
        for case_id, value in (state.get("last_request_state_poll_ts_by_case") or {}).items()
    }
    request_state_changed_cases: set[str] = set()
    request_state_rounds: dict[str, list[dict[str, Any]]] = {}
    pending_request_reconciliations: set[str] = set(
        state.get("pending_request_reconciliations") or ()
    )
    request_state_poll_errors: list[dict[str, str]] = []
    state_poll_due = {
        case_id: row
        for case_id, row in tracked.items()
        if case_id not in pending_trigger_reason
        and captured_at_ts - int(last_request_state_poll.get(case_id, 0))
        >= request_state_poll_interval_seconds(
            int(row.get("disputeTimestamp") or 0), captured_at_ts
        )
    }
    with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as pool:
        futures = {
            pool.submit(
                fetch_request_rounds,
                str(row["subgraph"]),
                str(row["ancillaryData"]),
            ): case_id
            for case_id, row in sorted(state_poll_due.items())
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                rounds = future.result()
                request_state_rounds[case_id] = rounds
                fingerprint = request_rounds_fingerprint(rounds)
                previous = request_state_fingerprints.get(case_id)
                request_state_fingerprints[case_id] = fingerprint
                last_request_state_poll[case_id] = captured_at_ts
                if previous is None:
                    pending_request_reconciliations.add(case_id)
                elif fingerprint != previous:
                    request_state_changed_cases.add(case_id)
            except Exception as exc:
                request_state_poll_errors.append(
                    {"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"}
                )
    priority_cases = active_shadow_case_ids(output_root)
    checkpoint_due_by_case, outstanding_checkpoint_cases = capture_checkpoint_work(
        output_root, now_ts=captured_at_ts
    )
    bootstrap_reconciliation_due = set(
        sorted(pending_request_reconciliations)[
            : max(0, max_bootstrap_reconciliations_per_run)
        ]
    )
    due: dict[str, dict[str, Any]] = {}
    for case_id, row in tracked.items():
        if (
            force_recapture
            or case_id in bulletin_changed_cases
            or case_id in pending_trigger_reason
            or case_id in request_state_changed_cases
            or case_id in bootstrap_reconciliation_due
            or case_id in checkpoint_due_by_case
            or (
                case_id in priority_cases
                and captured_at_ts - int(last_capture.get(case_id, 0)) >= 60
            )
        ):
            due[case_id] = row

    snapshots: list[dict[str, Any]] = []
    capture_demands_written = 0
    capture_checkpoint_receipts_written = 0
    source_evidence_cases = {
        case_id
        for case_id in due
        if fetch_source_evidence
        and (
            force_recapture
            or case_id in pending_trigger_reason
            or case_id in bulletin_changed_cases
        )
    }
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                capture_case,
                row,
                captured_at_ts=captured_at_ts,
                fetch_source_evidence=case_id in source_evidence_cases,
                request_rounds=request_state_rounds.get(case_id),
            ): case_id
            for case_id, row in sorted(due.items())
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                snapshot = future.result()
                stamp_contract_first_seen(snapshot, contract_first_seen)
                append_jsonl(output_root / "snapshots.jsonl", snapshot)
                snapshots.append(snapshot)
                trigger_reason = pending_trigger_reason.get(case_id)
                if trigger_reason:
                    trigger_demands: list[dict[str, Any]] = []
                    for demand in capture_demands_for_snapshot(
                        snapshot,
                        reason=trigger_reason,
                        requested_at_utc=str(snapshot["captured_at_utc"]),
                    ):
                        trigger_demands.append(demand)
                        if str(demand["demand_id"]) not in existing_demand_ids:
                            append_jsonl(output_root / "capture_demands.jsonl", demand)
                            existing_demand_ids.add(str(demand["demand_id"]))
                            capture_demands_written += 1
                    capture_checkpoint_receipts_written += write_capture_checkpoint_receipts(
                        output_root,
                        snapshot,
                        [
                            {
                                "demand_id": demand["demand_id"],
                                "token_id": demand["token_id"],
                                "trigger_event_id": demand.get("trigger_event_id"),
                                "checkpoint_seconds": 0,
                                "due_at_ts": captured_at_ts,
                            }
                            for demand in trigger_demands
                        ],
                        captured_at_ts=captured_at_ts,
                    )
                    if any(
                        int(value) > 0
                        for demand in trigger_demands
                        for value in demand.get("requested_checkpoints_seconds") or ()
                    ):
                        outstanding_checkpoint_cases.add(case_id)
                    pending_trigger_reason.pop(case_id, None)
                capture_checkpoint_receipts_written += write_capture_checkpoint_receipts(
                    output_root,
                    snapshot,
                    checkpoint_due_by_case.get(case_id, []),
                    captured_at_ts=captured_at_ts,
                )
                last_capture[case_id] = captured_at_ts
                if snapshot.get("request_rounds_fingerprint"):
                    request_state_fingerprints[case_id] = str(
                        snapshot["request_rounds_fingerprint"]
                    )
                    last_request_state_poll[case_id] = captured_at_ts
                pending_request_reconciliations.discard(case_id)
                bulletin_state = snapshot.get("bulletin_state") or {}
                if (
                    bulletin_state.get("adapter")
                    and bulletin_state.get("question_id")
                    and bulletin_state.get("creator")
                    and not bulletin_state.get("fetch_error")
                ):
                    bulletin_watches[case_id] = {
                        key: bulletin_state[key]
                        for key in (
                            "adapter",
                            "question_id",
                            "creator",
                            "update_count",
                            "update_fingerprint",
                        )
                    }
                    last_bulletin_poll[case_id] = captured_at_ts
            except Exception as exc:
                errors.append({"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})

    # Receipts written in this cycle may have completed the final checkpoint.
    # Re-derive from append-only truth before deciding whether a terminal case
    # remains tracked.
    _, outstanding_checkpoint_cases = capture_checkpoint_work(
        output_root, now_ts=captured_at_ts
    )

    # Settled and closed markets no longer need frequent book snapshots. Keep
    # requests that are unsettled or whose market remains open for a new round.
    next_tracked: dict[str, dict[str, Any]] = {}
    snapshot_by_case = {item["case_id"]: item for item in snapshots}
    for case_id, row in tracked.items():
        snapshot = snapshot_by_case.get(case_id)
        if snapshot is None:
            next_tracked[case_id] = row
            continue
        request_open = snapshot["proposal"]["request_class"] == "unsettled"
        market_open = not bool(snapshot["market_status"].get("closed"))
        if (
            request_open
            or market_open
            or case_id in pending_trigger_reason
            or case_id in outstanding_checkpoint_cases
        ):
            next_tracked[case_id] = row
        else:
            retired_case_ids.add(case_id)
            raw_request = snapshot.get("raw_request")
            if isinstance(raw_request, dict):
                retired_lifecycle_fingerprints[case_id] = (
                    request_lifecycle_fingerprint(raw_request)
                )

    latest_ts = max([int(row.get("disputeTimestamp") or 0) for row in fetched] + [since])
    state = {
        "schema_version": "dispute_forward_state_v1",
        "updated_at_utc": utc_iso(captured_at_ts),
        "last_dispute_ts": latest_ts,
        "seen_case_ids": sorted(seen),
        "tracked": next_tracked,
        "retired_case_ids": sorted(retired_case_ids),
        "retired_lifecycle_fingerprint_by_case": {
            case_id: fingerprint
            for case_id, fingerprint in retired_lifecycle_fingerprints.items()
            if case_id in retired_case_ids
        },
        "last_capture_ts_by_case": {
            case_id: ts for case_id, ts in last_capture.items() if case_id in next_tracked
        },
        "contract_fragment_first_seen_at": contract_first_seen,
        "bulletin_watches": {
            case_id: watch
            for case_id, watch in bulletin_watches.items()
            if case_id in next_tracked
        },
        "last_bulletin_poll_ts_by_case": {
            case_id: ts
            for case_id, ts in last_bulletin_poll.items()
            if case_id in next_tracked
        },
        "pending_trigger_reason_by_case": {
            case_id: reason
            for case_id, reason in pending_trigger_reason.items()
            if case_id in next_tracked
        },
        "request_state_fingerprint_by_case": {
            case_id: value
            for case_id, value in request_state_fingerprints.items()
            if case_id in next_tracked
        },
        "last_request_state_poll_ts_by_case": {
            case_id: value
            for case_id, value in last_request_state_poll.items()
            if case_id in next_tracked
        },
        "pending_request_reconciliations": sorted(
            case_id
            for case_id in pending_request_reconciliations
            if case_id in next_tracked
        ),
        "lifecycle_state_hydrated_from_snapshots": bool(
            state.get("lifecycle_state_hydrated_from_snapshots")
        ),
        "terminal_cases_pruned_from_tracking": int(
            state.get("terminal_cases_pruned_from_tracking") or 0
        ),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    summary = {
        "schema_version": "dispute_forward_run_v1",
        "captured_at_utc": utc_iso(captured_at_ts),
        "since_dispute_ts": since,
        "fetched_events": len(fetched),
        "tracked_cases_before_capture": len(tracked),
        "tracked_cases_after_capture": len(next_tracked),
        "cases_due": len(due),
        "active_shadow_cases": len(priority_cases),
        "bulletin_polls": len(poll_due),
        "bulletin_change_triggers": len(bulletin_changed_cases),
        "bulletin_poll_errors": bulletin_poll_errors,
        "request_state_polls": len(state_poll_due),
        "request_state_change_triggers": len(request_state_changed_cases),
        "bootstrap_reconciliations_pending": len(pending_request_reconciliations),
        "bootstrap_reconciliations_due": len(bootstrap_reconciliation_due),
        "request_state_poll_errors": request_state_poll_errors,
        "source_evidence_enabled": fetch_source_evidence,
        "source_evidence_cases": len(source_evidence_cases),
        "force_recapture": force_recapture,
        "snapshots_written": len(snapshots),
        "capture_demands_written": capture_demands_written,
        "bootstrap_historical_events_without_trigger_demand": (
            bootstrap_historical_events
        ),
        "rest_checkpoint_cases_due": len(checkpoint_due_by_case),
        "rest_checkpoint_items_due": sum(map(len, checkpoint_due_by_case.values())),
        "rest_checkpoint_receipts_written": capture_checkpoint_receipts_written,
        "rest_checkpoint_cases_outstanding": len(outstanding_checkpoint_cases),
        "fresh_within_5m": sum((item.get("capture_delay_seconds") or 10**9) <= 300 for item in snapshots),
        "reverse_book_covered": sum(bool(item.get("books", {}).get(item.get("reverse_token"))) for item in snapshots),
        "reverse_25_share_covered": sum(item["reverse_executable_vwap"].get("25") is not None for item in snapshots),
        "errors": errors,
    }
    (output_root / "latest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    append_jsonl(output_root / "runs.jsonl", summary)
    return summary
