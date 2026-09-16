"""Zero-notional scoring for the frozen mechanical dispute candidate."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import joblib

from src.platform.storage.jsonl import append_jsonl_row
from src.strategies.rule_lawyer.dispute import (
    build_model_feature_row,
    executable_bid_vwap,
    executable_vwap,
    modeled_taker_fee_per_share,
    model_theme_for_issue,
)


POLICY_ID = "mechanical_reverse_taker_25share_v1"
POLICY_EDGE_THRESHOLD = 0.10
POLICY_QUANTITY = 25.0
MAX_CAPTURE_DELAY_SECONDS = 300
BINANCE_POLICY_ID = "binance_verdict_reverse_taker_25share_v1"
BINANCE_POLICY_EDGE_THRESHOLD = 0.05
SEMANTIC_POLICY_ID = "semantic_verified_reverse_shadow_v1"
SEMANTIC_POLICY_EDGE_THRESHOLD = 0.05
SPORTS_VERDICT_POLICY_ID = "sports_verified_outcome_taker_25share_v1"
SPORTS_VERDICT_POLICY_EDGE_THRESHOLD = 0.05
MAX_BOOK_AGE_SECONDS = 120


def parse_utc_ts(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def verdict_revision_id(verdict: Any) -> str:
    payload = verdict if isinstance(verdict, dict) else {}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def review_available_for_snapshot(review: dict[str, Any] | None, snapshot: dict[str, Any]) -> bool:
    if not isinstance(review, dict):
        return False
    reviewed_ts = parse_utc_ts(review.get("reviewed_at_utc") or review.get("observed_at_utc"))
    snapshot_ts = parse_utc_ts(snapshot.get("captured_at_utc"))
    return reviewed_ts is not None and snapshot_ts is not None and reviewed_ts <= snapshot_ts


def contract_verdict_blockers(snapshot: dict[str, Any], verdict: dict[str, Any]) -> list[str]:
    """Require a verdict to explicitly account for the exact PIT contract corpus."""
    corpus = snapshot.get("contract_corpus")
    if not isinstance(corpus, dict):
        # Backward compatibility for historical fixtures/snapshots. New forward
        # snapshots always carry a corpus and therefore take the strict path.
        return []
    binding = corpus.get("binding_map") if isinstance(corpus.get("binding_map"), dict) else {}
    raw_blockers = list(binding.get("adjudication_blockers") or [])
    if not raw_blockers:
        return []
    if verdict.get("contract_corpus_sha256") != corpus.get("contract_corpus_sha256"):
        return ["verdict_does_not_reference_current_contract_corpus"]
    blockers: list[str] = []
    if "official_clarification_requires_fundamental_intent_review" in raw_blockers:
        if verdict.get("clarification_assessment") not in {
            "consistent_with_fundamental_intent",
            "not_material_to_verdict",
        }:
            blockers.append("official_clarification_fundamental_intent_not_assessed")
    if "official_contract_correction_or_refund_requires_precedence_review" in raw_blockers:
        if verdict.get("contract_correction_assessment") != "consistent_with_fundamental_intent":
            blockers.append("official_contract_correction_or_refund_not_trade_safe")
    hard_conflicts = {
        "bulletin_updates_unavailable",
        "subgraph_ancillary_differs_from_adapter_question",
        "request_bulletin_adapter_mismatch",
        "gamma_additional_context_not_verified_onchain",
    }
    if hard_conflicts.intersection(raw_blockers):
        blockers.append("contract_snapshot_not_authoritatively_verified")
    if "gamma_description_differs_from_onchain_request" in raw_blockers:
        if verdict.get("contract_precedence_assessment") != "onchain_request_and_valid_bulletin_control":
            blockers.append("gamma_onchain_contract_precedence_not_assessed")
    return blockers


def proposal_is_prospective(snapshot: dict[str, Any]) -> bool:
    """True when a proposal predates an objective market/event deadline (P4)."""
    proposal_ts = int(snapshot.get("proposal", {}).get("proposal_ts") or 0)
    start_ts = parse_utc_ts(snapshot.get("market_status", {}).get("game_start_time"))
    end_ts = parse_utc_ts(snapshot.get("market_status", {}).get("end_date"))
    objective_deadlines = [value for value in (start_ts, end_ts) if value]
    return bool(proposal_ts and objective_deadlines and proposal_ts < max(objective_deadlines))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def latest_by_case(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row["case_id"])
        if case_id not in latest or str(row["captured_at_utc"]) > str(latest[case_id]["captured_at_utc"]):
            latest[case_id] = row
    return sorted(latest.values(), key=lambda row: (row["captured_at_utc"], row["case_id"]))


def snapshot_case_features(snapshot: dict[str, Any]) -> dict[str, Any]:
    issue = dict(snapshot["issue_features"])
    proposal = snapshot["proposal"]
    proposal_ts = int(proposal.get("proposal_ts") or 0)
    dispute_ts = int(proposal.get("dispute_ts") or 0)
    latency_minutes = max(0.0, (dispute_ts - proposal_ts) / 60) if proposal_ts and dispute_ts else 0.0
    domains = list(issue.get("source_domains") or [])
    proposed_binary = proposal.get("proposed_binary")
    return {
        **issue,
        "market_theme_v1": model_theme_for_issue(issue, str(snapshot.get("title") or "")),
        "proposed_side": "YES" if proposed_binary == 1 else "NO",
        "top_source_domain": domains[0] if domains else "none",
        "log_dispute_latency": math.log1p(latency_minutes),
    }


def snapshot_fee_rate(snapshot: dict[str, Any]) -> float | None:
    return snapshot_fee_rate_for_token(snapshot, str(snapshot.get("reverse_token") or ""))


def snapshot_book_id_for_token(snapshot: dict[str, Any], token_id: str) -> str | None:
    group = snapshot.get("market_group_snapshot") or {}
    value = (group.get("book_snapshot_ids") or {}).get(token_id)
    if value:
        return str(value)
    capture = (snapshot.get("book_captures") or {}).get(token_id) or {}
    return str(capture.get("book_capture_id") or "") or None


def snapshot_fee_rate_for_token(snapshot: dict[str, Any], token_id: str) -> float | None:
    details = snapshot.get("clob_market", {}).get("fd")
    if isinstance(details, dict) and details.get("r") is not None:
        try:
            rate = float(details["r"])
        except (TypeError, ValueError):
            return None
        return rate if rate >= 0 else None
    # The token endpoint is definitive for fee-free markets.  A positive
    # base_fee is not converted here because its integer units require the
    # market's exponent/rate tuple; fail closed when ``fd`` is absent.
    token_fee = snapshot.get("fee_rates", {}).get(token_id, {})
    try:
        if float(token_fee.get("base_fee")) == 0:
            return 0.0
    except (TypeError, ValueError):
        pass
    return None


def outcome_execution(snapshot: dict[str, Any], outcome: str, quantity: float) -> tuple[str | None, float | None]:
    outcomes = list(snapshot.get("outcomes") or [])
    tokens = list(snapshot.get("tokens") or [])
    try:
        index = outcomes.index(outcome)
        token = str(tokens[index])
    except (ValueError, IndexError):
        return None, None
    levels: list[dict[str, Any]] = []
    for entry in snapshot.get("books", {}).get(token, {}).get("asks", []):
        try:
            levels.append({"side": "ask", "price": float(entry["price"]), "size": float(entry["size"])})
        except (KeyError, TypeError, ValueError):
            continue
    return token, executable_vwap(levels, quantity)


def token_bid_execution(snapshot: dict[str, Any], token: str, quantity: float) -> float | None:
    levels: list[dict[str, Any]] = []
    for entry in snapshot.get("books", {}).get(token, {}).get("bids", []):
        try:
            levels.append({"side": "bid", "price": float(entry["price"]), "size": float(entry["size"])})
        except (KeyError, TypeError, ValueError):
            continue
    return executable_bid_vwap(levels, quantity)


def token_book_age_seconds(
    snapshot: dict[str, Any], token: str, *, now: datetime
) -> int | None:
    raw = snapshot.get("books", {}).get(token, {}).get("timestamp")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    timestamp_seconds = value / 1000 if value > 10_000_000_000 else value
    return max(0, int(now.timestamp() - timestamp_seconds))


def selected_market_settlement_payout(snapshot: dict[str, Any], outcome: str) -> float | None:
    """Return final token payout only after the Gamma market itself is closed."""
    if not bool(snapshot.get("market_status", {}).get("closed")):
        return None
    outcomes = list(snapshot.get("outcomes") or [])
    prices = list(snapshot.get("outcome_prices") or [])
    try:
        value = float(prices[outcomes.index(outcome)])
    except (ValueError, IndexError, TypeError):
        return None
    if value >= 0.999:
        return 1.0
    if value <= 0.001:
        return 0.0
    if abs(value - 0.5) <= 0.001:
        return 0.5
    return None


def score_snapshot(
    snapshot: dict[str, Any],
    model: Any,
    *,
    edge_threshold: float = POLICY_EDGE_THRESHOLD,
) -> dict[str, Any]:
    price = snapshot.get("reverse_executable_vwap", {}).get(str(int(POLICY_QUANTITY)))
    fee_rate = snapshot_fee_rate(snapshot)
    features = snapshot_case_features(snapshot)
    probability = None
    fee = None
    edge = None
    blockers: list[str] = []
    if price is None:
        blockers.append("missing_25share_reverse_ask_depth")
    if fee_rate is None:
        blockers.append("missing_market_fee_parameters")
    if price is not None:
        probability = float(
            model.predict_proba([build_model_feature_row(features, market_price=float(price))])[0, 1]
        )
    if price is not None and fee_rate is not None and probability is not None:
        fee = modeled_taker_fee_per_share(float(price), fee_rate)
        edge = probability - float(price) - fee
    if features["track"] != "mechanical":
        blockers.append("non_mechanical_track")
    if proposal_is_prospective(snapshot):
        blockers.append("proposal_before_objective_deadline_p4")
    verdict = snapshot.get("rule_verdict")
    if not isinstance(verdict, dict) or verdict.get("status") != "verified":
        blockers.append("rule_verdict_not_verified")
    elif verdict.get("winning_outcome") != (
        snapshot.get("outcomes", [])[snapshot["reverse_index"]]
        if snapshot.get("reverse_index") in (0, 1) and len(snapshot.get("outcomes") or []) == 2
        else None
    ):
        blockers.append("rule_verdict_does_not_support_reverse")
    if isinstance(verdict, dict):
        blockers.extend(contract_verdict_blockers(snapshot, verdict))
    capture_delay = snapshot.get("capture_delay_seconds")
    if capture_delay is None or int(capture_delay) > MAX_CAPTURE_DELAY_SECONDS:
        blockers.append("book_not_captured_within_5m")
    if edge is None or edge < edge_threshold:
        blockers.append("model_edge_below_10c")
    identity = "|".join(
        (
            POLICY_ID,
            str(snapshot["case_id"]),
            str(snapshot["captured_at_utc"]),
            verdict_revision_id(verdict),
        )
    )
    return {
        "schema_version": "dispute_signal_candidate_v1",
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "policy_id": POLICY_ID,
        "case_id": snapshot["case_id"],
        "market_id": snapshot.get("market_id"),
        "opportunity_cluster_id": snapshot.get("opportunity_cluster_id") or f"market:{snapshot.get('market_id')}",
        "title": snapshot.get("title"),
        "snapshot_ts_utc": snapshot["captured_at_utc"],
        "capture_delay_seconds": capture_delay,
        "track": features["track"],
        "mechanism": features["mechanism"],
        "reverse_token": snapshot.get("reverse_token"),
        "execution_book_snapshot_id": snapshot_book_id_for_token(
            snapshot, str(snapshot.get("reverse_token") or "")
        ),
        "reverse_outcome": (
            snapshot.get("outcomes", [])[snapshot["reverse_index"]]
            if snapshot.get("reverse_index") in (0, 1) and len(snapshot.get("outcomes") or []) == 2
            else None
        ),
        "rule_verdict": verdict,
        "quantity": POLICY_QUANTITY,
        "reverse_ask_vwap": price,
        "fee_rate": fee_rate,
        "modeled_fee_per_share": fee,
        "p_reverse": probability,
        "net_edge_per_share": edge,
        "zero_notional": True,
        "eligible_shadow": not blockers,
        "blockers": blockers,
        "model_features": features,
    }


def score_binance_verdict_snapshot(
    snapshot: dict[str, Any], *, scored_at: datetime | None = None
) -> dict[str, Any]:
    price = snapshot.get("reverse_executable_vwap", {}).get(str(int(POLICY_QUANTITY)))
    fee_rate = snapshot_fee_rate(snapshot)
    verdict = snapshot.get("rule_verdict") if isinstance(snapshot.get("rule_verdict"), dict) else {}
    reverse_outcome = (
        snapshot.get("outcomes", [])[snapshot["reverse_index"]]
        if snapshot.get("reverse_index") in (0, 1) and len(snapshot.get("outcomes") or []) == 2
        else None
    )
    fee = modeled_taker_fee_per_share(float(price), fee_rate) if price is not None and fee_rate is not None else None
    edge = 1.0 - float(price) - float(fee) if price is not None and fee is not None else None
    blockers: list[str] = []
    if price is None:
        blockers.append("missing_25share_reverse_ask_depth")
    if fee_rate is None:
        blockers.append("missing_market_fee_parameters")
    resolver_id = str(verdict.get("resolver_id") or "")
    if verdict.get("status") != "verified" or not resolver_id.startswith("binance_"):
        blockers.append("binance_rule_verdict_not_verified")
    elif verdict.get("winning_outcome") != reverse_outcome:
        blockers.append("binance_verdict_does_not_support_reverse")
    blockers.extend(contract_verdict_blockers(snapshot, verdict))
    if proposal_is_prospective(snapshot):
        blockers.append("proposal_before_objective_deadline_p4")
    capture_delay = snapshot.get("capture_delay_seconds")
    now = scored_at or datetime.now(timezone.utc)
    book_age = token_book_age_seconds(snapshot, str(snapshot.get("reverse_token") or ""), now=now)
    if book_age is None or book_age > MAX_BOOK_AGE_SECONDS:
        blockers.append("book_snapshot_older_than_120s")
    if edge is None or edge < BINANCE_POLICY_EDGE_THRESHOLD:
        blockers.append("deterministic_net_edge_below_5c")
    identity = "|".join((BINANCE_POLICY_ID, str(snapshot["case_id"]), str(snapshot["captured_at_utc"]), verdict_revision_id(verdict)))
    return {
        "schema_version": "dispute_signal_candidate_v1",
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "policy_id": BINANCE_POLICY_ID,
        "case_id": snapshot["case_id"],
        "market_id": snapshot.get("market_id"),
        "opportunity_cluster_id": snapshot.get("opportunity_cluster_id") or f"market:{snapshot.get('market_id')}",
        "title": snapshot.get("title"),
        "snapshot_ts_utc": snapshot["captured_at_utc"],
        "book_age_seconds": book_age,
        "capture_delay_seconds": capture_delay,
        "track": "mechanical",
        "mechanism": "financial_timestamp_price",
        "reverse_token": snapshot.get("reverse_token"),
        "execution_book_snapshot_id": snapshot_book_id_for_token(
            snapshot, str(snapshot.get("reverse_token") or "")
        ),
        "reverse_outcome": reverse_outcome,
        "quantity": POLICY_QUANTITY,
        "reverse_ask_vwap": price,
        "fee_rate": fee_rate,
        "modeled_fee_per_share": fee,
        "p_reverse": 1.0 if verdict.get("status") == "verified" and verdict.get("winning_outcome") == reverse_outcome else None,
        "net_edge_per_share": edge,
        "rule_verdict": verdict,
        "zero_notional": True,
        "eligible_shadow": not blockers,
        "blockers": blockers,
        "model_features": None,
    }


def score_semantic_review_snapshot(
    snapshot: dict[str, Any], *, scored_at: datetime | None = None
) -> dict[str, Any]:
    price = snapshot.get("reverse_executable_vwap", {}).get(str(int(POLICY_QUANTITY)))
    fee_rate = snapshot_fee_rate(snapshot)
    verdict = snapshot.get("rule_verdict") if isinstance(snapshot.get("rule_verdict"), dict) else {}
    reverse_outcome = (
        snapshot.get("outcomes", [])[snapshot["reverse_index"]]
        if snapshot.get("reverse_index") in (0, 1) and len(snapshot.get("outcomes") or []) == 2
        else None
    )
    fee = modeled_taker_fee_per_share(float(price), fee_rate) if price is not None and fee_rate is not None else None
    confidence = float(verdict.get("confidence") or 0)
    edge = confidence - float(price) - float(fee) if price is not None and fee is not None else None
    blockers: list[str] = []
    if price is None:
        blockers.append("missing_25share_reverse_ask_depth")
    if fee_rate is None:
        blockers.append("missing_market_fee_parameters")
    if (
        verdict.get("status") != "verified"
        or not str(verdict.get("resolver_id") or "").startswith("semantic_")
        or float(verdict.get("confidence") or 0) < 0.95
    ):
        blockers.append("semantic_review_not_verified_at_95pct")
    elif verdict.get("winning_outcome") != reverse_outcome:
        blockers.append("semantic_review_does_not_support_reverse")
    blockers.extend(contract_verdict_blockers(snapshot, verdict))
    if proposal_is_prospective(snapshot):
        blockers.append("proposal_before_objective_deadline_p4")
    now = scored_at or datetime.now(timezone.utc)
    book_age = token_book_age_seconds(snapshot, str(snapshot.get("reverse_token") or ""), now=now)
    if book_age is None or book_age > MAX_BOOK_AGE_SECONDS:
        blockers.append("book_snapshot_older_than_120s")
    if edge is None or edge < SEMANTIC_POLICY_EDGE_THRESHOLD:
        blockers.append("deterministic_net_edge_below_5c")
    identity = "|".join((SEMANTIC_POLICY_ID, str(snapshot["case_id"]), str(snapshot["captured_at_utc"]), verdict_revision_id(verdict)))
    return {
        "schema_version": "dispute_signal_candidate_v1",
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "policy_id": SEMANTIC_POLICY_ID,
        "case_id": snapshot["case_id"],
        "market_id": snapshot.get("market_id"),
        "opportunity_cluster_id": snapshot.get("opportunity_cluster_id") or f"market:{snapshot.get('market_id')}",
        "title": snapshot.get("title"),
        "snapshot_ts_utc": snapshot["captured_at_utc"],
        "book_age_seconds": book_age,
        "capture_delay_seconds": snapshot.get("capture_delay_seconds"),
        "track": "semantic",
        "mechanism": snapshot.get("issue_features", {}).get("mechanism"),
        "reverse_token": snapshot.get("reverse_token"),
        "execution_book_snapshot_id": snapshot_book_id_for_token(
            snapshot, str(snapshot.get("reverse_token") or "")
        ),
        "reverse_outcome": reverse_outcome,
        "quantity": POLICY_QUANTITY,
        "reverse_ask_vwap": price,
        "fee_rate": fee_rate,
        "modeled_fee_per_share": fee,
        "p_reverse": confidence if verdict.get("status") == "verified" else None,
        "net_edge_per_share": edge,
        "rule_verdict": verdict,
        "zero_notional": True,
        "eligible_shadow": not blockers,
        "blockers": blockers,
        "model_features": None,
    }


def score_sports_verdict_snapshot(
    snapshot: dict[str, Any], *, scored_at: datetime | None = None
) -> dict[str, Any]:
    verdict = snapshot.get("rule_verdict") if isinstance(snapshot.get("rule_verdict"), dict) else {}
    winning_outcome = str(verdict.get("winning_outcome") or "")
    token, price = outcome_execution(snapshot, winning_outcome, POLICY_QUANTITY)
    fee_rate = snapshot_fee_rate_for_token(snapshot, token or "")
    confidence = float(verdict.get("confidence") or 0)
    fee = modeled_taker_fee_per_share(float(price), fee_rate) if price is not None and fee_rate is not None else None
    edge = confidence - float(price) - float(fee) if price is not None and fee is not None else None
    blockers: list[str] = []
    if price is None:
        blockers.append("missing_25share_winning_outcome_ask_depth")
    if fee_rate is None:
        blockers.append("missing_market_fee_parameters")
    if (
        verdict.get("status") != "verified"
        or not str(verdict.get("resolver_id") or "").startswith("sports_")
        or confidence < 0.95
    ):
        blockers.append("sports_rule_verdict_not_verified_at_95pct")
    if winning_outcome not in list(snapshot.get("outcomes") or []):
        blockers.append("sports_rule_verdict_outcome_not_in_market")
    blockers.extend(contract_verdict_blockers(snapshot, verdict))
    if proposal_is_prospective(snapshot):
        blockers.append("proposal_before_objective_deadline_p4")
    now = scored_at or datetime.now(timezone.utc)
    book_age = token_book_age_seconds(snapshot, token or "", now=now)
    if book_age is None or book_age > MAX_BOOK_AGE_SECONDS:
        blockers.append("book_snapshot_older_than_120s")
    if edge is None or edge < SPORTS_VERDICT_POLICY_EDGE_THRESHOLD:
        blockers.append("verified_outcome_net_edge_below_5c")
    relation = None
    outcomes = list(snapshot.get("outcomes") or [])
    reverse_index = snapshot.get("reverse_index")
    if winning_outcome in outcomes and reverse_index in (0, 1):
        relation = "reverse" if outcomes.index(winning_outcome) == reverse_index else "proposal"
    identity = "|".join((SPORTS_VERDICT_POLICY_ID, str(snapshot["case_id"]), str(snapshot["captured_at_utc"]), verdict_revision_id(verdict)))
    return {
        "schema_version": "dispute_signal_candidate_v1",
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "policy_id": SPORTS_VERDICT_POLICY_ID,
        "case_id": snapshot["case_id"],
        "market_id": snapshot.get("market_id"),
        "opportunity_cluster_id": snapshot.get("opportunity_cluster_id") or f"market:{snapshot.get('market_id')}",
        "title": snapshot.get("title"),
        "snapshot_ts_utc": snapshot["captured_at_utc"],
        "book_age_seconds": book_age,
        "capture_delay_seconds": snapshot.get("capture_delay_seconds"),
        "track": "mechanical",
        "mechanism": snapshot.get("issue_features", {}).get("mechanism"),
        "selected_token": token,
        "execution_book_snapshot_id": snapshot_book_id_for_token(snapshot, token or ""),
        "selected_outcome": winning_outcome or None,
        "verdict_relation_to_proposal": relation,
        "quantity": POLICY_QUANTITY,
        "selected_ask_vwap": price,
        "fee_rate": fee_rate,
        "modeled_fee_per_share": fee,
        "p_selected_outcome": confidence if verdict.get("status") == "verified" else None,
        "net_edge_per_share": edge,
        "rule_verdict": verdict,
        "zero_notional": True,
        "eligible_shadow": not blockers,
        "blockers": blockers,
        "model_features": None,
    }


def build_rule_review_queue(
    snapshots: list[dict[str, Any]], *, scored_at: datetime | None = None
) -> dict[str, Any]:
    """Rank unresolved cases where a deterministic verdict could still clear 5c.

    This is an upper-bound screen, not a signal: it assumes the researched
    outcome is certain, then subtracts executable ask and official taker fee.
    Cases that cannot clear the frozen edge threshold even at p=1 do not spend
    scarce RuleVerdict/LLM research time.
    """
    now = scored_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        verdict = snapshot.get("rule_verdict") if isinstance(snapshot.get("rule_verdict"), dict) else {}
        if verdict.get("status") in {"verified", "prospective_too_early"}:
            continue
        if bool(snapshot.get("market_status", {}).get("closed")):
            continue
        if str(snapshot.get("proposal", {}).get("request_class") or "") != "unsettled":
            continue
        if proposal_is_prospective(snapshot):
            continue
        choices: list[dict[str, Any]] = []
        outcomes = [str(value) for value in snapshot.get("outcomes") or []]
        tokens = [str(value) for value in snapshot.get("tokens") or []]
        reverse_index = snapshot.get("reverse_index")
        for index, outcome in enumerate(outcomes):
            if index >= len(tokens):
                continue
            token = tokens[index]
            book_age = token_book_age_seconds(snapshot, token, now=now)
            if book_age is None or book_age > MAX_BOOK_AGE_SECONDS:
                continue
            _, ask_vwap = outcome_execution(snapshot, outcome, POLICY_QUANTITY)
            fee_rate = snapshot_fee_rate_for_token(snapshot, token)
            if ask_vwap is None or fee_rate is None:
                continue
            fee = modeled_taker_fee_per_share(float(ask_vwap), float(fee_rate))
            upper_bound = 1.0 - float(ask_vwap) - fee
            if upper_bound + 1e-12 < SPORTS_VERDICT_POLICY_EDGE_THRESHOLD:
                continue
            relation = (
                "reverse" if reverse_index in (0, 1) and index == reverse_index
                else "proposal" if reverse_index in (0, 1)
                else None
            )
            choices.append(
                {
                    "outcome": outcome,
                    "token_id": token,
                    "relation_to_proposal": relation,
                    "ask_vwap_25": ask_vwap,
                    "fee_rate": fee_rate,
                    "modeled_fee_per_share": fee,
                    "book_age_seconds": book_age,
                    "max_deterministic_edge_per_share": upper_bound,
                }
            )
        if not choices:
            continue
        choices.sort(key=lambda row: (-float(row["max_deterministic_edge_per_share"]), str(row["outcome"])))
        issue = snapshot.get("issue_features", {})
        proposal_ts = int(snapshot.get("proposal", {}).get("proposal_ts") or 0)
        dispute_ts = int(snapshot.get("proposal", {}).get("dispute_ts") or 0)
        rows.append(
            {
                "case_id": snapshot.get("case_id"),
                "market_id": snapshot.get("market_id"),
                "opportunity_cluster_id": snapshot.get("opportunity_cluster_id"),
                "title": snapshot.get("title"),
                "snapshot_ts_utc": snapshot.get("captured_at_utc"),
                "book_age_seconds": min(int(choice["book_age_seconds"]) for choice in choices),
                "track": issue.get("track"),
                "mechanism": issue.get("mechanism"),
                "source_domains": issue.get("source_domains") or [],
                "dispute_latency_seconds": dispute_ts - proposal_ts if dispute_ts and proposal_ts else None,
                "verdict_status": verdict.get("status") or "unverified",
                "research_choices": choices,
                "best_max_deterministic_edge_per_share": choices[0]["max_deterministic_edge_per_share"],
                "zero_notional_only": True,
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["best_max_deterministic_edge_per_share"]),
            str(row.get("snapshot_ts_utc") or ""),
            str(row.get("case_id") or ""),
        )
    )
    return {
        "schema_version": "dispute_rule_review_queue_v1",
        "generated_at_utc": now.isoformat(),
        "quantity": POLICY_QUANTITY,
        "frozen_min_edge_per_share": SPORTS_VERDICT_POLICY_EDGE_THRESHOLD,
        "interpretation": "upper-bound research queue only; no RuleVerdict and no trade authorization",
        "candidates": len(rows),
        "rows": rows,
    }


def apply_cluster_cap(candidates: list[dict[str, Any]]) -> None:
    """Allow at most one otherwise-eligible expression per event risk cluster."""
    eligible_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row["eligible_shadow"]:
            eligible_by_cluster.setdefault(str(row["opportunity_cluster_id"]), []).append(row)
    for rows in eligible_by_cluster.values():
        winner = max(
            rows,
            key=lambda row: (float(row["net_edge_per_share"]), str(row["candidate_id"])),
        )
        for row in rows:
            if row is winner:
                continue
            row["blockers"].append("lower_edge_sibling_in_opportunity_cluster")
            row["eligible_shadow"] = False


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    append_jsonl_row(path, row)


def update_shadow_ledger(output_root: Path, *, updated_at: datetime | None = None) -> dict[str, Any]:
    """Open zero-notional positions once and append executable bid/settlement markouts."""
    now = updated_at or datetime.now(timezone.utc)
    from src.strategies.rule_lawyer.trade_intents import read_signal_candidates

    signals = read_signal_candidates(output_root)
    positions_path = output_root / "shadow_positions.jsonl"
    markouts_path = output_root / "shadow_markouts.jsonl"
    positions = read_jsonl(positions_path)
    paper_fills_by_candidate = {
        str(row.get("candidate_id") or ""): row
        for row in read_jsonl(output_root / "paper_fills.jsonl")
    }
    position_keys = {str(row.get("position_key") or "") for row in positions}
    opened = 0
    for signal in sorted(signals, key=lambda row: (str(row.get("snapshot_ts_utc") or ""), str(row.get("candidate_id") or ""))):
        if not signal.get("eligible_shadow") or not signal.get("zero_notional"):
            continue
        position_key = "|".join(
            (str(signal.get("policy_id") or ""), str(signal.get("opportunity_cluster_id") or ""))
        )
        if position_key in position_keys:
            continue
        identity = f"shadow|{position_key}"
        quantity = float(signal["quantity"])
        entry_price_raw = signal.get("selected_ask_vwap")
        if entry_price_raw is None:
            entry_price_raw = signal.get("reverse_ask_vwap")
        if entry_price_raw is None:
            entry_price_raw = signal.get("ask_vwap")
        entry_price = float(entry_price_raw)
        entry_fee = float(signal.get("modeled_fee_per_share") or 0)
        paper_fill = paper_fills_by_candidate.get(str(signal.get("candidate_id") or ""))
        if paper_fill is None:
            continue
        position = {
            "schema_version": "dispute_shadow_position_v1",
            "position_id": hashlib.sha256(identity.encode()).hexdigest(),
            "position_key": position_key,
            "opened_at_utc": (
                signal.get("snapshot_ts_utc") or signal.get("quote_observed_at_utc")
            ),
            "signal_id": signal["candidate_id"],
            "policy_id": signal["policy_id"],
            "case_id": signal["case_id"],
            "market_id": signal.get("market_id"),
            "opportunity_cluster_id": signal.get("opportunity_cluster_id"),
            "title": signal.get("title"),
            "outcome": signal.get("selected_outcome") or signal.get("reverse_outcome"),
            "verdict_relation_to_proposal": signal.get("verdict_relation_to_proposal") or "reverse",
            "token_id": signal.get("selected_token") or signal.get("reverse_token"),
            "quantity": quantity,
            "entry_ask_vwap": entry_price,
            "entry_fee_per_share": entry_fee,
            "entry_cost": quantity * (entry_price + entry_fee),
            "max_payout": quantity,
            "expected_edge_per_share": (
                signal.get("net_edge_per_share")
                if signal.get("net_edge_per_share") is not None
                else signal.get("conservative_policy_edge_per_share")
            ),
            "rule_verdict": signal.get("rule_verdict"),
            "zero_notional": True,
            "paper_fill_id": paper_fill["fill_id"],
            "paper_order_id": paper_fill["order_id"],
            "trade_intent_id": paper_fill["intent_id"],
        }
        _append_jsonl(positions_path, position)
        positions.append(position)
        position_keys.add(position_key)
        opened += 1

    snapshots = read_jsonl(output_root / "snapshots.jsonl")
    latest_snapshot_by_case = {
        str(row["case_id"]): row for row in latest_by_case(snapshots)
    }
    existing_markouts = read_jsonl(markouts_path)
    emitted = {
        (str(row.get("position_id") or ""), str(row.get("checkpoint") or ""))
        for row in existing_markouts
    }
    checkpoint_targets = (("entry", 0), ("5m", 300), ("15m", 900), ("1h", 3600), ("6h", 21_600), ("24h", 86_400))
    markouts_written = 0
    settled_positions: set[str] = {
        str(row.get("position_id") or "")
        for row in existing_markouts
        if row.get("checkpoint") == "settlement"
    }
    for position in positions:
        position_id = str(position["position_id"])
        snapshot = latest_snapshot_by_case.get(str(position["case_id"]))
        if snapshot is None:
            continue
        opened_ts = parse_utc_ts(position.get("opened_at_utc"))
        snapshot_ts = parse_utc_ts(snapshot.get("captured_at_utc"))
        if opened_ts is None or snapshot_ts is None or snapshot_ts < opened_ts:
            continue
        elapsed = snapshot_ts - opened_ts
        request_class = str(snapshot.get("proposal", {}).get("request_class") or "")
        payout = selected_market_settlement_payout(snapshot, str(position.get("outcome") or ""))
        checkpoints: list[tuple[str, int]] = []
        if payout is not None:
            checkpoints.append(("settlement", elapsed))
        else:
            checkpoints.extend((name, target) for name, target in checkpoint_targets if elapsed >= target)
        for checkpoint, target_seconds in checkpoints:
            if (position_id, checkpoint) in emitted:
                continue
            quantity = float(position["quantity"])
            entry_price = float(position["entry_ask_vwap"])
            entry_fee = float(position.get("entry_fee_per_share") or 0)
            bid = token_bid_execution(snapshot, str(position.get("token_id") or ""), quantity)
            fee_rate = snapshot_fee_rate_for_token(snapshot, str(position.get("token_id") or ""))
            if payout is None and (bid is None or fee_rate is None):
                continue
            exit_fee = 0.0 if payout is not None else modeled_taker_fee_per_share(float(bid), float(fee_rate))
            mark_price = float(payout) if payout is not None else float(bid)
            pnl = quantity * (mark_price - entry_price - entry_fee - exit_fee)
            row = {
                "schema_version": "dispute_shadow_markout_v1",
                "position_id": position_id,
                "position_key": position["position_key"],
                "checkpoint": checkpoint,
                "checkpoint_target_seconds": target_seconds,
                "observed_at_utc": snapshot["captured_at_utc"],
                "elapsed_seconds": elapsed,
                "request_class": request_class,
                "quantity": quantity,
                "entry_ask_vwap": entry_price,
                "exit_bid_vwap": None if payout is not None else float(bid),
                "settlement_payout": payout,
                "exit_fee_per_share": exit_fee,
                "net_pnl": pnl,
                "return_on_entry_cost": pnl / float(position["entry_cost"]),
                "zero_notional": True,
            }
            _append_jsonl(markouts_path, row)
            emitted.add((position_id, checkpoint))
            markouts_written += 1
            if checkpoint == "settlement":
                settled_positions.add(position_id)
    return {
        "schema_version": "dispute_shadow_ledger_summary_v1",
        "generated_at_utc": now.isoformat(),
        "positions": len(positions),
        "positions_opened": opened,
        "open_positions": len(positions) - len(settled_positions),
        "settled_positions": len(settled_positions),
        "markouts_written": markouts_written,
    }


def score_forward_snapshots(
    snapshots_path: Path,
    model_path: Path,
    signals_path: Path,
) -> dict[str, Any]:
    snapshots = latest_by_case(read_jsonl(snapshots_path))
    reviews_path = snapshots_path.parent / "semantic_reviews.jsonl"
    latest_review_by_market: dict[str, dict[str, Any]] = {}
    for review in read_jsonl(reviews_path):
        market_id = str(review.get("market_id") or "")
        if market_id and (
            market_id not in latest_review_by_market
            or str(review.get("reviewed_at_utc") or "") > str(latest_review_by_market[market_id].get("reviewed_at_utc") or "")
        ):
            latest_review_by_market[market_id] = review
    snapshots = [
        {
            **snapshot,
            "rule_verdict": (
                latest_review_by_market[str(snapshot.get("market_id"))]
                if review_available_for_snapshot(
                    latest_review_by_market.get(str(snapshot.get("market_id"))), snapshot
                )
                else snapshot.get("rule_verdict")
            ),
        }
        for snapshot in snapshots
    ]
    model = joblib.load(model_path)
    existing_ids = {row.get("candidate_id") for row in read_jsonl(signals_path)}
    primary_candidates = [score_snapshot(snapshot, model) for snapshot in snapshots]
    scored_at = datetime.now(timezone.utc)
    binance_candidates = [
        score_binance_verdict_snapshot(snapshot, scored_at=scored_at) for snapshot in snapshots
    ]
    semantic_candidates = [score_semantic_review_snapshot(snapshot, scored_at=scored_at) for snapshot in snapshots]
    sports_verdict_candidates = [score_sports_verdict_snapshot(snapshot, scored_at=scored_at) for snapshot in snapshots]
    candidates = primary_candidates + binance_candidates + semantic_candidates + sports_verdict_candidates
    apply_cluster_cap(candidates)
    review_queue = build_rule_review_queue(snapshots, scored_at=scored_at)
    (signals_path.parent / "review_queue.json").write_text(
        json.dumps(review_queue, indent=2, sort_keys=True) + "\n"
    )
    new = [row for row in candidates if row["candidate_id"] not in existing_ids]
    if new:
        for row in new:
            append_jsonl_row(signals_path, row)
    return {
        "schema_version": "dispute_forward_scorecard_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_id": POLICY_ID,
        "unique_latest_cases": len(snapshots),
        "opportunity_clusters": len({row["opportunity_cluster_id"] for row in candidates}),
        "new_candidates_written": len(new),
        "fresh_within_5m": sum(
            snapshot.get("capture_delay_seconds") is not None
            and int(snapshot["capture_delay_seconds"]) <= MAX_CAPTURE_DELAY_SECONDS
            for snapshot in snapshots
        ),
        "reverse_25share_depth": sum(row["reverse_ask_vwap"] is not None for row in primary_candidates),
        "mechanical_cases": sum(row["track"] == "mechanical" for row in primary_candidates),
        "model_edge_gte_10c": sum(
            row["net_edge_per_share"] is not None
            and float(row["net_edge_per_share"]) >= POLICY_EDGE_THRESHOLD
            for row in primary_candidates
        ),
        "eligible_shadow": sum(row["eligible_shadow"] for row in candidates),
        "rule_review_queue_candidates": review_queue["candidates"],
        "by_policy": {
            policy_id: {
                "candidates": sum(row["policy_id"] == policy_id for row in candidates),
                "eligible_shadow": sum(row["policy_id"] == policy_id and row["eligible_shadow"] for row in candidates),
            }
            for policy_id in (POLICY_ID, BINANCE_POLICY_ID, SEMANTIC_POLICY_ID, SPORTS_VERDICT_POLICY_ID)
        },
    }
