#!/usr/bin/env python3
"""Scan first-dispute markets for creator-authored onchain clarifications."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any

import requests

from src.platform.clients.polymarket_bulletin import (
    fetch_creator_updates,
    is_question_initialized,
)
from src.strategies.rule_lawyer.contract_corpus import (
    bulletin_adapter_from_ancillary,
    classify_official_update_text,
)


QUESTION_ID_RE = re.compile(r"(0x[a-fA-F0-9]{64})$")
INITIALIZER_RE = re.compile(
    r"(?i)initializer\s*[:=]\s*(?:0x)?([0-9a-f]{40})"
)
GAMMA_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
def classify_update_text(text: str) -> str:
    """Backward-compatible artifact label backed by the live corpus classifier."""
    return classify_official_update_text(text)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def first_dispute_by_market(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_id = str(row.get("market_id") or "")
        if not market_id:
            continue
        if market_id not in first or int(row.get("dispute_ts") or 0) < int(
            first[market_id].get("dispute_ts") or 0
        ):
            first[market_id] = row
    return sorted(first.values(), key=lambda row: int(row.get("dispute_ts") or 0))


def retry(call, attempts: int = 4):
    for attempt in range(attempts):
        try:
            return call()
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.25 * (2**attempt))


def gamma_question_id(market_id: str) -> str:
    response = requests.get(GAMMA_URL.format(market_id=market_id), timeout=20)
    response.raise_for_status()
    payload = response.json()
    return str(payload.get("questionID") or payload.get("questionId") or "").lower()


def scan_case(row: dict[str, Any]) -> dict[str, Any]:
    ancillary = str(row.get("ancillary_text") or "")
    adapter = bulletin_adapter_from_ancillary(ancillary)
    initializer = INITIALIZER_RE.search(ancillary)
    request_qid = QUESTION_ID_RE.search(str(row.get("signal_id") or ""))
    base = {
        "schema_version": "dispute_official_clarification_case_v1",
        "market_id": str(row.get("market_id") or ""),
        "title": row.get("title"),
        "market_theme_v1": row.get("market_theme_v1"),
        "proposal_ts": int(row.get("proposal_ts") or 0),
        "dispute_ts": int(row.get("dispute_ts") or 0),
        "final_settlement_ts": int(row.get("final_settlement_ts") or 0),
        "request_settlement_class": row.get("request_settlement_class"),
        "reversed": row.get("reversed"),
        "adapter": adapter,
    }
    if not adapter or not initializer or not request_qid:
        return {**base, "status": "unsupported_contract_identity", "updates": []}
    creator = "0x" + initializer.group(1).lower()
    question_id = request_qid.group(1).lower()
    initialized = retry(lambda: is_question_initialized(adapter, question_id, timeout=20))
    question_id_source = "uma_request_id_suffix"
    if not initialized:
        question_id = retry(lambda: gamma_question_id(base["market_id"]))
        question_id_source = "gamma_fallback"
        initialized = bool(question_id) and retry(
            lambda: is_question_initialized(adapter, question_id, timeout=20)
        )
    if not initialized:
        return {
            **base,
            "status": "question_not_initialized",
            "creator": creator,
            "question_id": question_id,
            "question_id_source": question_id_source,
            "updates": [],
        }
    updates = retry(
        lambda: fetch_creator_updates(
            adapter, question_id, creator, timeout=20
        )
    )
    classified = []
    for update in updates:
        timestamp = int(update.get("timestamp") or 0)
        phase = (
            "before_proposal"
            if timestamp <= base["proposal_ts"]
            else "proposal_to_dispute"
            if timestamp <= base["dispute_ts"]
            else "after_dispute_before_settlement"
            if not base["final_settlement_ts"] or timestamp <= base["final_settlement_ts"]
            else "after_settlement"
        )
        classified.append(
            {
                **update,
                "phase": phase,
                "update_text_class_v1": classify_update_text(str(update.get("text") or "")),
            }
        )
    return {
        **base,
        "status": "ok",
        "creator": creator,
        "question_id": question_id,
        "question_id_source": question_id_source,
        "updates": classified,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path("runtime/dispute_repricing/dispute_repricing_v1/signals.jsonl"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runtime/dispute_repricing/official_clarifications_v1"),
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    rows = first_dispute_by_market(read_jsonl(args.signals))
    args.output_root.mkdir(parents=True, exist_ok=True)
    cases_path = args.output_root / "cases.jsonl"
    completed = {
        str(row.get("market_id") or "")
        for row in read_jsonl(cases_path)
        if row.get("status") in {"ok", "unsupported_contract_identity", "question_not_initialized"}
    } if cases_path.exists() else set()
    pending = [row for row in rows if str(row.get("market_id") or "") not in completed]
    with cases_path.open("a") as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_case, row): row for row in pending}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "schema_version": "dispute_official_clarification_case_v1",
                    "market_id": str(source.get("market_id") or ""),
                    "title": source.get("title"),
                    "market_theme_v1": source.get("market_theme_v1"),
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "updates": [],
                }
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()

    cases = read_jsonl(cases_path)
    latest = {str(row.get("market_id") or ""): row for row in cases}
    cases = list(latest.values())
    update_cases = [row for row in cases if row.get("updates")]
    verified_cases = [row for row in cases if row.get("status") == "ok"]
    summary = {
        "schema_version": "dispute_official_clarification_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": "first observed dispute per unique market in signals artifact",
        "signals_path": str(args.signals),
        "unique_markets": len(rows),
        "cases_scanned": len(cases),
        "status_counts": dict(Counter(str(row.get("status")) for row in cases)),
        "question_id_source_counts": dict(
            Counter(str(row.get("question_id_source")) for row in cases if row.get("question_id_source"))
        ),
        "verified_contract_cases": len(verified_cases),
        "markets_with_creator_update": len(update_cases),
        "creator_update_market_rate": (
            len(update_cases) / len(verified_cases) if verified_cases else None
        ),
        "creator_updates": sum(len(row.get("updates") or []) for row in cases),
        "update_phase_counts": dict(
            Counter(
                str(update.get("phase"))
                for row in update_cases
                for update in row.get("updates") or []
            )
        ),
        "update_text_class_counts_v1": dict(
            Counter(
                str(
                    update.get("update_text_class_v1")
                    or classify_update_text(str(update.get("text") or ""))
                )
                for row in update_cases
                for update in row.get("updates") or []
            )
        ),
        "update_market_text_class_counts_v1": {
            kind: len(
                {
                    str(row.get("market_id"))
                    for row in update_cases
                    if any(
                        str(
                            update.get("update_text_class_v1")
                            or classify_update_text(str(update.get("text") or ""))
                        )
                        == kind
                        for update in row.get("updates") or []
                    )
                }
            )
            for kind in (
                "adjudication_guidance",
                "operational_notice",
                "contract_correction_or_refund",
            )
        },
        "update_theme_counts": dict(
            Counter(str(row.get("market_theme_v1") or "Other") for row in update_cases)
        ),
        "update_market_ids": sorted(str(row.get("market_id")) for row in update_cases),
    }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
