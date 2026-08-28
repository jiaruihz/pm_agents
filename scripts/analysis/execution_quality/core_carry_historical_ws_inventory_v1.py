#!/usr/bin/env python3
"""Build the Core Carry historical maker/WS evidence inventory.

The inventory is intentionally evidence-only.  It never estimates fills for
orders that did not exist and it never repairs a PIT candidate with a later
score or book.  One output row represents one frozen candidate/checkpoint.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_RUNTIME = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2"
)
DEFAULT_SHADOW = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_market_state_shadow_v3_forward_20260824a"
)
DEFAULT_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
MAKER_ROOT_ROLES = frozenset({"maker", "maker_staged", "maker_pullback"})
NEAR_CORE_BLOCKER = "non_positive_taker_ev"


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def utc_key(value: Any) -> str:
    parsed = parse_utc(value)
    return parsed.isoformat(timespec="microseconds") if parsed else ""


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield {**row, "_source_path": str(path), "_line_number": line_number}


def stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def score_identity(row: Mapping[str, Any]) -> str | None:
    required = {
        "checkpoint_key": row.get("checkpoint_key"),
        "created_at_utc": utc_key(row.get("created_at_utc")),
        "current_yes_token_id": row.get("current_yes_token_id"),
        "model_probability_hold": row.get("model_probability_hold"),
        "artifact_hash": row.get("artifact_hash"),
    }
    if any(value in (None, "") for value in required.values()):
        return None
    return stable_hash(required)


def candidate_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("checkpoint_key") or ""),
        utc_key(row.get("created_at_utc")),
        str(row.get("current_yes_token_id") or ""),
    )


def demand_key(row: Mapping[str, Any]) -> tuple[str, str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return (
        str(metadata.get("checkpoint_key") or ""),
        utc_key(row.get("requested_at_utc")),
    )


def decision_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("checkpoint_key") or ""),
        utc_key(row.get("event_at_utc") or row.get("signal_event_at_utc")),
    )


def order_id(row: Mapping[str, Any]) -> str:
    response = row.get("exchange_response")
    payloads: list[Mapping[str, Any]] = [row]
    if isinstance(response, Mapping):
        payloads.append(response)
        for name in ("place", "raw_response", "authoritative_order_state"):
            nested = response.get(name)
            if isinstance(nested, Mapping):
                payloads.append(nested)
    for payload in payloads:
        for name in ("venue_order_id", "order_id", "orderID", "id"):
            value = str(payload.get(name) or "").strip()
            if value:
                return value
    return ""


def bracket_value(value: Any) -> float | None:
    text = str(value or "").strip().replace("°", "")
    for suffix in ("F", "C", "+"):
        text = text.removesuffix(suffix)
    if "-" in text[1:]:
        text = text.split("-", 1)[0]
    try:
        return float(text)
    except ValueError:
        return None


def immediate_upper_token(
    score: Mapping[str, Any], demands: Sequence[Mapping[str, Any]]
) -> str | None:
    current = bracket_value(score.get("current_bracket"))
    if current is None:
        return None
    candidates: list[tuple[float, str]] = []
    for rung in score.get("full_ladder_yes_tokens") or ():
        if not isinstance(rung, Mapping):
            continue
        value = bracket_value(rung.get("bracket"))
        token = str(rung.get("token_id") or "")
        if value is not None and value > current and token:
            candidates.append((value, token))
    if not candidates:
        for demand in demands:
            metadata = (
                demand.get("metadata")
                if isinstance(demand.get("metadata"), Mapping)
                else {}
            )
            value = bracket_value(metadata.get("bracket"))
            token = str(demand.get("token_id") or "")
            if value is not None and value > current and token:
                candidates.append((value, token))
    return min(candidates)[1] if candidates else None


def raw_token_refs(decision: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for blocker in decision.get("reconstruction_blockers") or ():
        if not isinstance(blocker, Mapping) or not blocker.get("raw_frame_id"):
            continue
        token = str(blocker.get("token_id") or "")
        if token:
            tokens.add(token)
    return tokens


def current_ws_available(decision: Mapping[str, Any], token_id: str) -> bool:
    if token_id and token_id in raw_token_refs(decision):
        return True
    if decision.get("feature_book_snapshot_id"):
        return True
    for horizon in (decision.get("horizon_features") or {}).values():
        if isinstance(horizon, Mapping) and horizon.get("book_snapshot_id"):
            return True
    return False


def upper_ws_available(
    decision: Mapping[str, Any], upper_token_id: str | None
) -> bool:
    if upper_token_id and upper_token_id in raw_token_refs(decision):
        return True
    return any(decision.get(name) is not None for name in ("q_up", "q1", "alpha1"))


def public_tape_available(decision: Mapping[str, Any]) -> bool:
    return any(
        decision.get(name) is not None
        for name in ("reported_buy_volume", "reported_sell_volume")
    )


def sequence_continuity_available(decision: Mapping[str, Any]) -> bool:
    """Separate transport continuity from the stricter state-inference gate."""

    if not decision.get("reconstruction_run_id"):
        return False
    errors = decision.get("reconstruction_errors") or ()
    return not errors


def finite(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_existing_score(
    private_rows: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Resolve an existing maker root to one earlier frozen score receipt."""

    roots = [
        dict(row)
        for row in private_rows
        if str(row.get("child_order_role") or "") in MAKER_ROOT_ROLES
    ]
    if not roots:
        return None, {"status": "missing_maker_root"}
    root = min(roots, key=lambda row: utc_key(row.get("created_at_utc")))
    root_at = parse_utc(root.get("created_at_utc"))
    expected_probability = finite(root.get("model_token_probability"))
    matches: list[tuple[float, dict[str, Any]]] = []
    for source in scores:
        row = dict(source)
        score_at = parse_utc(row.get("created_at_utc"))
        probability = finite(row.get("model_probability_hold"))
        if (
            root_at is None
            or score_at is None
            or score_at > root_at
            or (root_at - score_at).total_seconds() > 120
            or str(row.get("city") or "") != str(root.get("city") or "")
            or str(row.get("target_date") or "")
            != str(root.get("target_date") or "")
            or str(row.get("current_yes_token_id") or "")
            != str(root.get("token_id") or "")
            or str(row.get("decision_status") or "") != "positive_taker_ev"
            or probability is None
            or expected_probability is None
            or abs(probability - expected_probability) > 1e-12
        ):
            continue
        matches.append(((root_at - score_at).total_seconds(), row))
    if len(matches) != 1:
        return None, {
            "status": "exact_pit_join_missing" if not matches else "exact_pit_join_ambiguous",
            "match_count": len(matches),
        }
    delta, row = matches[0]
    return row, {
        "status": "exact_one_to_one_pit_join",
        "score_to_order_seconds": delta,
        "matched_order_created_at_utc": utc_key(root.get("created_at_utc")),
    }


def settlement_index(db_path: Path, token_ids: set[str]) -> dict[str, dict[str, Any]]:
    tokens = sorted(token_ids - {""})
    if not tokens:
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    connection.row_factory = sqlite3.Row
    result: dict[str, dict[str, Any]] = {}
    try:
        for offset in range(0, len(tokens), 500):
            batch = tokens[offset : offset + 500]
            marks = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT token_id, final_price, settlement_status, city,
                       target_date, bracket, created_at_utc
                FROM settlement_outcomes
                WHERE token_id IN ({marks})
                ORDER BY created_at_utc
                """,
                batch,
            ).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for raw in rows:
                grouped[str(raw["token_id"] or "")].append(dict(raw))
            for token, values in grouped.items():
                finals = {row.get("final_price") for row in values}
                result[token] = (
                    values[-1]
                    if len(finals) == 1
                    else {
                        "token_id": token,
                        "settlement_status": "ambiguous_conflicting_labels",
                        "final_price": None,
                    }
                )
    finally:
        connection.close()
    return result


def classify(row: Mapping[str, Any]) -> str:
    exact = bool(row.get("exact_frozen_score_identity_available"))
    settled = bool(row.get("settlement_available"))
    ws_replay = all(
        bool(row.get(name))
        for name in (
            "current_token_raw_incremental_ws_available",
            "public_trade_tape_available",
            "sequence_reconnect_continuity_available",
        )
    )
    private = bool(row.get("private_order_lifecycle_available"))
    if exact and settled and ws_replay and private:
        return "GOLD"
    if exact and settled and ws_replay and not private:
        return "SILVER"
    if exact and settled:
        return "BRONZE"
    return "UNUSABLE"


def _base_evidence(
    *,
    score: Mapping[str, Any],
    source_sleeve: str,
    decision: Mapping[str, Any] | None,
    demands: Sequence[Mapping[str, Any]],
    private_rows: Sequence[Mapping[str, Any]],
    settlement: Mapping[str, Any] | None,
    score_join: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    decision = dict(decision or {})
    token_id = str(score.get("current_yes_token_id") or "")
    upper_token = immediate_upper_token(score, demands)
    identity = score_identity(score)
    venue_ids = sorted({order_id(row) for row in private_rows} - {""})
    roles = sorted(
        {
            str(row.get("child_order_role") or "")
            for row in private_rows
            if row.get("child_order_role")
        }
    )
    row = {
        "candidate_id": stable_hash(
            {
                "source_sleeve": source_sleeve,
                "checkpoint_key": score.get("checkpoint_key"),
                "created_at_utc": utc_key(score.get("created_at_utc")),
                "token_id": token_id,
            }
        ),
        "source_sleeve": source_sleeve,
        "target_date": score.get("target_date"),
        "city": score.get("city"),
        "checkpoint_key": score.get("checkpoint_key"),
        "candidate_at_utc": utc_key(score.get("created_at_utc")),
        "signal_id": score.get("signal_id"),
        "current_token_id": token_id or None,
        "immediate_upper_token_id": upper_token,
        "candidate_checkpoint_receipt_available": True,
        "exact_frozen_score_identity_available": identity is not None,
        "score_identity": identity,
        "sole_blocker_reconstructable_pit": (
            list(score.get("reasons") or ()) == [NEAR_CORE_BLOCKER]
            if source_sleeve == "CORE_CARRY_NEAR_CORE_MAKER_PROBE_5S"
            else None
        ),
        "current_token_raw_incremental_ws_available": current_ws_available(
            decision, token_id
        ),
        "upper_rung_raw_incremental_ws_available": upper_ws_available(
            decision, upper_token
        ),
        "public_trade_tape_available": public_tape_available(decision),
        "sequence_reconnect_continuity_available": sequence_continuity_available(
            decision
        ),
        "private_order_lifecycle_available": bool(venue_ids),
        "private_venue_order_ids": venue_ids,
        "private_child_order_roles": roles,
        "settlement_available": bool(
            settlement and settlement.get("final_price") is not None
        ),
        "settlement_status": (
            settlement.get("settlement_status") if settlement else "missing"
        ),
        "settlement_final_price": (
            settlement.get("final_price") if settlement else None
        ),
        "ws_decision_event_id": decision.get("event_id"),
        "ws_evidence_status": decision.get("evidence_status"),
        "score_join": dict(score_join or {"status": "native_score_receipt"}),
        "ws_reconstruction_blockers": decision.get("reconstruction_blockers") or [],
        "capture_demand_count": len(demands),
        "public_trade_buy_volume": decision.get("reported_buy_volume"),
        "public_trade_sell_volume": decision.get("reported_sell_volume"),
    }
    row["classification"] = classify(row)
    row["full_ladder_lifecycle_research_ready"] = (
        row["classification"] == "GOLD"
        and row["upper_rung_raw_incremental_ws_available"]
    )
    row["deterministic_policy_replay_ready"] = row["classification"] in {
        "GOLD",
        "SILVER",
    }
    return row


def build_inventory(
    *,
    scores: Sequence[Mapping[str, Any]],
    capture_demands: Sequence[Mapping[str, Any]],
    pretrigger_demands: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    live_orders: Sequence[Mapping[str, Any]],
    settlements: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    score_rows: list[dict[str, Any]] = []
    seen_scores: set[tuple[str, str, str]] = set()
    for source in scores:
        row = dict(source)
        key = candidate_key(row)
        if not all(key) or key in seen_scores:
            continue
        seen_scores.add(key)
        score_rows.append(row)

    demands_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in (*capture_demands, *pretrigger_demands):
        row = dict(source)
        key = demand_key(row)
        if all(key):
            demands_by_key[key].append(row)

    near_decisions: dict[tuple[str, str], dict[str, Any]] = {}
    signal_decisions: dict[str, dict[str, Any]] = {}
    for source in decisions:
        row = dict(source)
        kind = str(row.get("event_kind") or "")
        if kind in {"candidate", "near_core_candidate"}:
            near_decisions[decision_key(row)] = row
        elif kind == "first_positive" and row.get("signal_id"):
            signal_decisions[str(row["signal_id"])] = row

    private_by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    maker_signal_ids: set[str] = set()
    for source in live_orders:
        row = dict(source)
        signal_id = str(row.get("signal_id") or "")
        if not signal_id:
            continue
        private_by_signal[signal_id].append(row)
        if str(row.get("child_order_role") or "") in MAKER_ROOT_ROLES:
            maker_signal_ids.add(signal_id)

    output: list[dict[str, Any]] = []
    for score in score_rows:
        if list(score.get("reasons") or ()) != [NEAR_CORE_BLOCKER]:
            continue
        key = (
            str(score.get("checkpoint_key") or ""),
            utc_key(score.get("created_at_utc")),
        )
        output.append(
            _base_evidence(
                score=score,
                source_sleeve="CORE_CARRY_NEAR_CORE_MAKER_PROBE_5S",
                decision=near_decisions.get(key),
                demands=demands_by_key.get(key, ()),
                private_rows=(),
                settlement=settlements.get(str(score.get("current_yes_token_id") or "")),
                score_join={"status": "native_near_core_score_receipt"},
            )
        )

    for signal_id in sorted(maker_signal_ids):
        score, score_join = resolve_existing_score(
            private_by_signal[signal_id], score_rows
        )
        if score is None:
            roots = [
                row
                for row in private_by_signal[signal_id]
                if str(row.get("child_order_role") or "") in MAKER_ROOT_ROLES
            ]
            first = roots[0]
            score = {
                "city": first.get("city"),
                "target_date": first.get("target_date"),
                "checkpoint_key": first.get("checkpoint_key"),
                "created_at_utc": first.get("created_at_utc"),
                "current_yes_token_id": first.get("token_id"),
                "signal_id": signal_id,
            }
        else:
            score = {**score, "signal_id": signal_id}
        key = (
            str(score.get("checkpoint_key") or ""),
            utc_key(score.get("created_at_utc")),
        )
        output.append(
            _base_evidence(
                score=score,
                source_sleeve="CORE_CARRY_EXISTING_MAKER",
                decision=signal_decisions.get(signal_id),
                demands=demands_by_key.get(key, ()),
                private_rows=private_by_signal[signal_id],
                settlement=settlements.get(str(score.get("current_yes_token_id") or "")),
                score_join=score_join,
            )
        )
    return sorted(
        output,
        key=lambda row: (
            str(row.get("target_date") or ""),
            str(row.get("city") or ""),
            str(row.get("candidate_at_utc") or ""),
            str(row.get("source_sleeve") or ""),
        ),
    )


def input_stat(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classifications = Counter(str(row["classification"]) for row in rows)
    sleeves = Counter(str(row["source_sleeve"]) for row in rows)
    dates = {str(row.get("target_date") or "") for row in rows} - {""}
    cities = {str(row.get("city") or "") for row in rows} - {""}
    return {
        "row_count": len(rows),
        "target_date_count": len(dates),
        "target_date_min": min(dates) if dates else None,
        "target_date_max": max(dates) if dates else None,
        "city_count": len(cities),
        "classification_counts": dict(sorted(classifications.items())),
        "source_sleeve_counts": dict(sorted(sleeves.items())),
        "full_ladder_lifecycle_research_ready": sum(
            bool(row.get("full_ladder_lifecycle_research_ready")) for row in rows
        ),
        "deterministic_policy_replay_ready": sum(
            bool(row.get("deterministic_policy_replay_ready")) for row in rows
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--shadow-dir", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--canonical-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    paths = {
        "scores": args.runtime_dir / "pre_live_scores.jsonl",
        "capture_demands": args.runtime_dir / "capture_demands.jsonl",
        "pretrigger_demands": args.shadow_dir / "pretrigger_capture_demands.jsonl",
        "decisions": args.shadow_dir / "market_state_decisions.jsonl",
        "live_orders": args.runtime_dir / "live_orders.jsonl",
        "canonical_db": args.canonical_db,
    }
    scores = list(iter_jsonl(paths["scores"]))
    capture_demands = list(iter_jsonl(paths["capture_demands"]))
    pretrigger_demands = list(iter_jsonl(paths["pretrigger_demands"]))
    decisions = list(iter_jsonl(paths["decisions"]))
    live_orders = list(iter_jsonl(paths["live_orders"]))
    token_ids = {
        str(row.get("current_yes_token_id") or "") for row in scores
    } | {str(row.get("token_id") or "") for row in live_orders}
    settlements = settlement_index(paths["canonical_db"], token_ids)
    rows = build_inventory(
        scores=scores,
        capture_demands=capture_demands,
        pretrigger_demands=pretrigger_demands,
        decisions=decisions,
        live_orders=live_orders,
        settlements=settlements,
    )
    payload = {
        "schema_version": "core_carry_historical_ws_inventory_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "grain": "source_sleeve_x_frozen_candidate_checkpoint",
        "classification_contract": {
            "GOLD": "exact score + settlement + replayable current-token WS/trade tape + continuity + actual private order lifecycle",
            "SILVER": "exact score + settlement + replayable current-token WS/trade tape + continuity; no actual private order lifecycle",
            "BRONZE": "exact score + settlement, without replayable lifecycle evidence",
            "UNUSABLE": "exact score identity or settlement unavailable",
        },
        "inputs": {name: input_stat(path) for name, path in paths.items()},
        "summary": summarize(rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
