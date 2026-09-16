#!/usr/bin/env python3
"""Append-only repair for Core Carry maker fills parsed as token micro-units."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.clob_fill_cache import append_cached_fill, iter_cached_fills  # noqa: E402
from weather_dashboard.ingest.clob_fill_fee_adjustments import (  # noqa: E402
    append_fee_adjustment,
    iter_fee_adjustments,
)
from weather_dashboard.ingest.clob_fill_validity_adjustments import append_validity_adjustment  # noqa: E402

ORDERS = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/live_orders.jsonl")
CACHE = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"
VALIDITY = ROOT / "runtime/weather_edge_v1/clob_fill_validity_adjustments.jsonl"
FEE_ADJUSTMENTS = ROOT / "runtime/weather_edge_v1/clob_fill_fee_adjustments.jsonl"


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(body.encode()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open() if line.strip()]


def corrections() -> list[dict[str, Any]]:
    cached_by_order: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_cached_fills(CACHE):
        cached_by_order[str(row.get("order_id") or "")].append(row)
    result: list[dict[str, Any]] = []
    for terminal in load_jsonl(ORDERS):
        if terminal.get("child_order_role") != "core_carry_maker_terminal" or terminal.get("status") != "filled":
            continue
        auth = (terminal.get("exchange_response") or {}).get("authoritative_order_state") or {}
        order_id = str(auth.get("order_id") or "")
        matched = float(auth.get("matched_shares") or 0)
        raw_rows = cached_by_order.get(order_id, [])
        cached = sum(float(row.get("filled_shares") or 0) for row in raw_rows)
        if not order_id or matched <= 0 or not raw_rows or cached >= matched - 1e-6:
            continue
        # The known incident signature is exactly a 1e6 undercount.  Refuse to
        # turn other partial-fill discrepancies into automatic corrections.
        if abs(cached * 1_000_000 - matched) > 1e-6:
            continue
        result.append({"terminal": terminal, "auth": auth, "raw_rows": raw_rows, "matched": matched})
    return result


def missing_maker_fee_adjustments(
    cache_path: Path = CACHE,
    fee_adjustment_path: Path = FEE_ADJUSTMENTS,
) -> list[dict[str, Any]]:
    covered_fill_ids = {
        str(row.get("fill_id") or "")
        for row in iter_fee_adjustments(fee_adjustment_path)
        if str(row.get("fee_source") or "") == "maker_zero"
    }
    repairs: list[dict[str, Any]] = []
    for row in iter_cached_fills(cache_path):
        fill_id = str(row.get("fill_id") or "")
        if (
            not fill_id
            or fill_id in covered_fill_ids
            or str(row.get("source") or "")
            != "authenticated_order_state_share_unit_correction_v1"
        ):
            continue
        metadata = row.get("fee_metadata") if isinstance(
            row.get("fee_metadata"), dict
        ) else {}
        if not bool(metadata.get("maker_only")):
            continue
        repairs.append(
            {
                "adjustment_id": stable_id(
                    "fill-fee-maker-zero-",
                    {"fill_id": fill_id, "reason": "authenticated_maker_order"},
                ),
                "fill_id": fill_id,
                "fee_delta_usd": 0.0,
                "fee_source": "maker_zero",
                "fee_evidence_class": "exact",
                "transaction_hash": row.get("transaction_hash"),
                "fee_rate": 0.0,
                "market_fee_metadata": {
                    "evidence_basis": "authenticated_maker_order_state",
                    "maker_fee_rate": 0.0,
                },
                "evidence": {
                    "reason": "maker order semantics imply zero maker fee",
                    "fill_id": fill_id,
                    "order_id": row.get("order_id"),
                    "correction": metadata.get("correction") or {},
                },
            }
        )
    return repairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    found = corrections()
    applied = 0
    now = datetime.now(timezone.utc).isoformat()
    output: list[dict[str, Any]] = []
    for item in found:
        auth, terminal, raw_rows = item["auth"], item["terminal"], item["raw_rows"]
        evidence = {
            "reason": "v2_camel_case_sizeMatched_was_already_in_shares",
            "authoritative_state_version": auth.get("authoritative_state_version"),
            "order_id": auth.get("order_id"),
            "matched_shares": item["matched"],
            "city": terminal.get("city"),
            "target_date": terminal.get("target_date"),
            "source": str(ORDERS),
        }
        for raw in raw_rows:
            adjustment = {
                "adjustment_id": stable_id("fill-validity-", {"fill_id": raw["fill_id"], **evidence}),
                "fill_id": raw["fill_id"],
                "effective_status": "excluded",
                "reason": "clob_sizeMatched_unit_parse_1e6_undercount",
                "evidence": evidence,
                "source_path": str(ORDERS),
                "created_at_utc": now,
            }
            if args.apply:
                applied += int(append_validity_adjustment(adjustment, VALIDITY))
        raw = raw_rows[0]
        corrected = {
            "fill_id": stable_id("fill-corrected-", evidence),
            "execution_id": raw["execution_id"],
            "order_id": auth["order_id"],
            "filled_shares": item["matched"],
            "filled_price": float(auth.get("posted_price") or raw["filled_price"]),
            "fees_usd": 0.0,
            "fee_source": "maker_zero",
            "fee_rate": 0.0,
            "fee_metadata": {"maker_only": True, "correction": evidence},
            "transaction_hash": raw.get("transaction_hash"),
            "filled_at_utc": raw["filled_at_utc"],
            "created_at_utc": now,
            "source": "authenticated_order_state_share_unit_correction_v1",
        }
        if args.apply:
            append_cached_fill(corrected, CACHE)
            applied += 1
        output.append({"order_id": auth["order_id"], "old_shares": sum(float(r["filled_shares"]) for r in raw_rows), "new_shares": item["matched"], "corrected_fill_id": corrected["fill_id"]})
    fee_repairs = missing_maker_fee_adjustments()
    for repair in fee_repairs:
        repair["created_at_utc"] = now
        if args.apply:
            applied += int(append_fee_adjustment(repair, FEE_ADJUSTMENTS))
    print(
        json.dumps(
            {
                "apply": args.apply,
                "corrections": output,
                "maker_fee_lineage_repairs": [
                    {
                        "fill_id": row["fill_id"],
                        "adjustment_id": row["adjustment_id"],
                    }
                    for row in fee_repairs
                ],
                "append_actions": applied,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
