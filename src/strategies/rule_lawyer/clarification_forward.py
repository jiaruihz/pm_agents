"""Zero-notional forward decisions for official Polymarket clarifications."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from src.strategies.rule_lawyer.clarification_adjudicator import (
    PROMPT_VERSION,
    build_clarification_packet,
    clarification_card_blockers,
    packet_hash,
)
from src.strategies.rule_lawyer.dispute import executable_vwap, modeled_taker_fee_per_share
from src.strategies.rule_lawyer.dispute_strategy import snapshot_fee_rate_for_token
from src.strategies.rule_lawyer.dispute_strategy import proposal_is_prospective


POLICY_ID = "official_clarification_court_taker_25share_shadow_v1"
POLICY_QUANTITY = 25.0
PAYOUT_FLOOR = 0.95
MIN_POLICY_EDGE = 0.05
MAX_UPDATE_TO_QUOTE_SECONDS = 300
MAX_BOOK_AGE_SECONDS = 15


def parse_utc_ts(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def latest_by_case(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if case_id and (
            case_id not in latest
            or str(row.get("captured_at_utc") or "")
            > str(latest[case_id].get("captured_at_utc") or "")
        ):
            latest[case_id] = row
    return sorted(latest.values(), key=lambda row: str(row.get("case_id") or ""))


def clarification_packets_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Compile each PIT onchain guidance fragment without settlement labels."""
    corpus = snapshot.get("contract_corpus") or {}
    fragments = list(corpus.get("fragments") or [])
    binding_rules = next(
        (
            str(fragment.get("text") or "")
            for fragment in fragments
            if fragment.get("legal_role") == "binding_resolution_rule"
        ),
        "",
    )
    outcomes = [str(value) for value in snapshot.get("outcomes") or []]
    proposed_binary = snapshot.get("proposal", {}).get("proposed_binary")
    proposal_outcome = (
        outcomes[1 - int(proposed_binary)]
        if proposed_binary in (0, 1) and len(outcomes) == 2
        else "Unknown"
    )
    packets: list[dict[str, Any]] = []
    for fragment in fragments:
        legal_role = str(fragment.get("legal_role") or "")
        if legal_role not in {"onchain_clarification", "contract_correction_or_refund"}:
            continue
        update_ts = parse_utc_ts(fragment.get("effective_at_utc"))
        if not update_ts:
            continue
        packet = build_clarification_packet(
            case_id=(
                f"{snapshot.get('case_id')}:clarification:"
                f"{fragment.get('fragment_id') or fragment.get('content_sha256')}"
            ),
            market_id=str(snapshot.get("market_id") or ""),
            title=str(snapshot.get("title") or ""),
            outcomes=outcomes,
            proposal_outcome=proposal_outcome,
            binding_rules=binding_rules,
            official_update=str(fragment.get("text") or ""),
            update_timestamp=update_ts,
            contract_corpus_sha256=str(corpus.get("contract_corpus_sha256") or ""),
        )
        packet["official_update_legal_role"] = legal_role
        packet["source_snapshot_case_id"] = str(snapshot.get("case_id") or "")
        packet["input_sha256"] = packet_hash(
            {key: value for key, value in packet.items() if key != "input_sha256"}
        )
        packets.append(packet)
    return sorted(packets, key=lambda row: (int(row["update_timestamp"]), row["case_id"]))


def _selected_execution(
    snapshot: dict[str, Any], determination: str, quantity: float
) -> tuple[str | None, str | None, float | None]:
    index = 0 if determination == "Outcome0" else 1 if determination == "Outcome1" else None
    outcomes = [str(value) for value in snapshot.get("outcomes") or []]
    tokens = [str(value) for value in snapshot.get("tokens") or []]
    if index not in (0, 1) or len(outcomes) != 2 or len(tokens) != 2:
        return None, None, None
    token = tokens[index]
    levels = [
        {"side": "ask", "price": entry.get("price"), "size": entry.get("size")}
        for entry in snapshot.get("books", {}).get(token, {}).get("asks", [])
    ]
    return outcomes[index], token, executable_vwap(levels, quantity)


def score_clarification_card(
    packet: dict[str, Any],
    card: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    observed_at_utc: str,
    min_confidence: float = 0.95,
    quantity: float = POLICY_QUANTITY,
    payout_floor: float = PAYOUT_FLOOR,
    min_policy_edge: float = MIN_POLICY_EDGE,
) -> dict[str, Any]:
    """Score the first post-adjudication executable quote; never submits an order."""
    blockers = clarification_card_blockers(packet, card, min_confidence=min_confidence)
    corpus_sha = str(snapshot.get("contract_corpus", {}).get("contract_corpus_sha256") or "")
    if packet.get("contract_corpus_sha256") != corpus_sha:
        blockers.append("card_contract_corpus_is_not_current")
    if packet.get("official_update_legal_role") == "contract_correction_or_refund":
        blockers.append("official_contract_correction_or_refund")
    selected_outcome, token, ask_vwap = _selected_execution(
        snapshot, str(card.get("determination") or ""), quantity
    )
    observed_ts = parse_utc_ts(observed_at_utc)
    update_ts = int(packet.get("update_timestamp") or 0)
    latency = observed_ts - update_ts if observed_ts is not None and update_ts else None
    if latency is None or latency < 0:
        blockers.append("quote_clock_precedes_official_update")
    elif latency > MAX_UPDATE_TO_QUOTE_SECONDS:
        blockers.append("update_to_executable_quote_over_300s")
    if snapshot.get("proposal", {}).get("request_class") != "unsettled":
        blockers.append("uma_request_already_settled")
    if proposal_is_prospective(snapshot):
        blockers.append("proposal_before_objective_deadline_p4")
    if selected_outcome is None or token is None:
        blockers.append("determination_does_not_map_to_binary_outcome")
    if ask_vwap is None:
        blockers.append("missing_25share_selected_outcome_ask_depth")
    fee_rate = snapshot_fee_rate_for_token(snapshot, token or "")
    if fee_rate is None:
        blockers.append("missing_market_fee_parameters")
    book_ts_raw = snapshot.get("books", {}).get(token or "", {}).get("timestamp")
    try:
        book_ts = int(book_ts_raw)
        if book_ts > 10_000_000_000:
            book_ts //= 1000
    except (TypeError, ValueError):
        book_ts = None
    book_age = observed_ts - book_ts if observed_ts is not None and book_ts is not None else None
    if book_age is None or book_age < 0 or book_age > MAX_BOOK_AGE_SECONDS:
        blockers.append("selected_book_older_than_15s")
    fee = (
        modeled_taker_fee_per_share(float(ask_vwap), float(fee_rate))
        if ask_vwap is not None and fee_rate is not None
        else None
    )
    deterministic_edge = 1 - ask_vwap - fee if ask_vwap is not None and fee is not None else None
    policy_edge = (
        payout_floor - ask_vwap - fee if ask_vwap is not None and fee is not None else None
    )
    if policy_edge is None or policy_edge < min_policy_edge:
        blockers.append("conservative_policy_edge_below_5c")
    blockers = list(dict.fromkeys(blockers))
    identity = "|".join((POLICY_ID, str(packet.get("input_sha256")), observed_at_utc))
    book_capture = (snapshot.get("book_captures") or {}).get(token or "") or {}
    return {
        "schema_version": "clarification_forward_signal_v1",
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "policy_id": POLICY_ID,
        "prompt_version": PROMPT_VERSION,
        "case_id": packet.get("source_snapshot_case_id") or packet.get("case_id"),
        "clarification_packet_id": packet.get("case_id"),
        "input_sha256": packet.get("input_sha256"),
        "source_snapshot_case_id": packet.get("source_snapshot_case_id"),
        "market_id": packet.get("market_id"),
        "opportunity_cluster_id": snapshot.get("opportunity_cluster_id"),
        "update_cluster_id": hashlib.sha256(
            str(packet.get("official_update") or "").encode()
        ).hexdigest()[:16],
        "title": packet.get("title"),
        "official_update_timestamp": update_ts,
        "snapshot_ts_utc": observed_at_utc,
        "quote_observed_at_utc": observed_at_utc,
        "update_to_quote_seconds": latency,
        "determination": card.get("determination"),
        "adjudication_confidence": card.get("confidence"),
        "selected_outcome": selected_outcome,
        "selected_token": token,
        "execution_book_snapshot_id": book_capture.get("book_capture_id"),
        "quantity": quantity,
        "ask_vwap": ask_vwap,
        "fee_rate": fee_rate,
        "modeled_fee_per_share": fee,
        "deterministic_edge_per_share": deterministic_edge,
        "conservative_payout_floor": payout_floor,
        "conservative_policy_edge_per_share": policy_edge,
        "book_age_seconds": book_age,
        "zero_notional": True,
        "eligible_shadow": not blockers,
        "blockers": blockers,
        "card": card,
    }


def apply_update_cluster_cap(
    candidates: list[dict[str, Any]], occupied_clusters: set[str] | None = None
) -> None:
    """Allow at most one correlated market per identical official update."""
    occupied = set(occupied_clusters or set())
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row.get("eligible_shadow"):
            groups.setdefault(str(row.get("update_cluster_id") or ""), []).append(row)
    for cluster_id, rows in groups.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                -(float(row.get("conservative_policy_edge_per_share") or -1)),
                str(row.get("market_id") or ""),
            ),
        )
        keep = None if cluster_id in occupied else ordered[0]
        for row in ordered:
            if row is keep:
                continue
            row["blockers"].append("one_trade_per_official_update_cluster")
            row["eligible_shadow"] = False
