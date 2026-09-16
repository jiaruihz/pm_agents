#!/usr/bin/env python3
"""Audit deterministic RuleVerdict adapters against settled dispute labels."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.dispute_verdict import resolve_rule_verdict  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default="runtime/dispute_repricing/dispute_repricing_v1/signals.jsonl")
    parser.add_argument("--cases", default="runtime/dispute_repricing/dispute_case_panel_v1/cases.jsonl")
    parser.add_argument("--scope-label", default="all first clear non-P4 financial_timestamp_price dispute cases")
    parser.add_argument("--out-dir", default="runtime/dispute_repricing/dispute_verdict_audit_v1")
    parser.add_argument(
        "--trade-cache",
        default="runtime/dispute_repricing/dispute_repricing_v1/cache",
    )
    args = parser.parse_args()
    signals = {str(row["market_id"]): row for row in read_jsonl(Path(args.signals))}
    cases = [
        row for row in read_jsonl(Path(args.cases))
        if row.get("mechanism") == "financial_timestamp_price"
    ]
    rows: list[dict[str, Any]] = []
    for case in cases:
        signal = signals[str(case["market_id"])]
        snapshot = {
            "title": signal["title"],
            "outcomes": signal.get("outcomes") or [],
            "proposal": {"proposal_ts": signal["proposal_ts"]},
            "rules_snapshot": {
                "ancillary_text": signal["ancillary_text"],
                "description": signal["ancillary_text"],
                "resolution_source": signal.get("resolution_source"),
            },
            "rule_verdict": {"status": "unverified"},
        }
        try:
            verdict = resolve_rule_verdict(snapshot)
            error = None
        except Exception as exc:
            verdict = {"status": "resolver_error", "reason": f"{type(exc).__name__}: {exc}"}
            error = verdict["reason"]
        supports_reverse = (
            verdict.get("status") == "verified"
            and verdict.get("winning_outcome") == signal.get("reverse_outcome")
        )
        expected_reverse = bool(case["label_flip"])
        winning_entry = None
        winning_relation = None
        winning_outcome = verdict.get("winning_outcome")
        outcomes = list(signal.get("outcomes") or [])
        tokens = list(signal.get("token_ids") or [])
        if verdict.get("status") == "verified" and winning_outcome in outcomes and len(tokens) == 2:
            winning_index = outcomes.index(winning_outcome)
            winning_relation = "reverse" if winning_index == signal.get("reverse_outcome_index") else "proposal"
            trade_path = Path(args.trade_cache) / "trades" / f"{case['market_id']}.json"
            if int(signal.get("trade_rows_returned") or 0) >= 10_000:
                windows = sorted((Path(args.trade_cache) / "trades_window").glob(f"{case['market_id']}-*.json"))
                if windows:
                    trade_path = windows[-1]
            trades = json.loads(trade_path.read_text()) if trade_path.exists() else []
            post = sorted(
                (
                    trade for trade in trades
                    if str(trade.get("asset") or "") == str(tokens[winning_index])
                    and int(trade.get("timestamp") or 0) > int(signal["dispute_ts"])
                    and (
                        signal.get("final_settlement_ts") is None
                        or int(trade.get("timestamp") or 0) <= int(signal["final_settlement_ts"])
                    )
                ),
                key=lambda trade: (int(trade["timestamp"]), str(trade.get("transactionHash") or "")),
            )
            if post:
                winning_entry = {
                    "price": float(post[0]["price"]),
                    "timestamp": int(post[0]["timestamp"]),
                    "size": float(post[0].get("size") or 0),
                    "side": str(post[0].get("side") or ""),
                    "token_id": str(tokens[winning_index]),
                }
        rows.append(
            {
                "case_id": case["case_id"],
                "market_id": case["market_id"],
                "title": case["title"],
                "expected_reverse": expected_reverse,
                "resolver_supports_reverse": supports_reverse,
                "winning_relation_to_proposal": winning_relation,
                "winning_outcome_entry": winning_entry,
                "correct": supports_reverse == expected_reverse if verdict.get("status") == "verified" else None,
                "error": error,
                "verdict": verdict,
            }
        )
    verified = [row for row in rows if row["verdict"].get("status") == "verified"]
    case_by_id = {str(row["case_id"]): row for row in cases}

    def policy_metrics(threshold: float) -> dict[str, Any]:
        selected: list[tuple[dict[str, Any], float, float]] = []
        for audit in verified:
            case = case_by_id[str(audit["case_id"])]
            price = case.get("entry_price")
            if not audit["resolver_supports_reverse"] or price is None:
                continue
            price = float(price)
            fee = 0.07 * price * (1 - price)
            pnl = 1 - price - fee
            if pnl >= threshold:
                selected.append((case, price + fee, pnl))
        return {
            "markets": len(selected),
            "independent_utc_dates": len({case["dispute_utc"][:10] for case, _, _ in selected}),
            "mean_public_print_net_pnl_per_share": (
                sum(pnl for _, _, pnl in selected) / len(selected) if selected else None
            ),
            "public_print_roi_on_cost_plus_fee": (
                sum(pnl for _, _, pnl in selected) / sum(cost for _, cost, _ in selected)
                if selected else None
            ),
            "market_ids": [case["market_id"] for case, _, _ in selected],
        }

    def symmetric_policy_metrics(threshold: float) -> dict[str, Any]:
        selected: list[tuple[dict[str, Any], float, float]] = []
        for audit in verified:
            entry = audit.get("winning_outcome_entry")
            if not isinstance(entry, dict):
                continue
            price = float(entry["price"])
            fee = 0.07 * price * (1 - price)
            pnl = 1 - price - fee
            if pnl >= threshold:
                selected.append((audit, price + fee, pnl))
        return {
            "markets": len(selected),
            "independent_utc_dates": len(
                {case_by_id[str(audit["case_id"])]["dispute_utc"][:10] for audit, _, _ in selected}
            ),
            "reverse_winner_markets": sum(
                audit.get("winning_relation_to_proposal") == "reverse" for audit, _, _ in selected
            ),
            "proposal_winner_markets": sum(
                audit.get("winning_relation_to_proposal") == "proposal" for audit, _, _ in selected
            ),
            "mean_public_print_net_pnl_per_share": (
                sum(pnl for _, _, pnl in selected) / len(selected) if selected else None
            ),
            "public_print_roi_on_cost_plus_fee": (
                sum(pnl for _, _, pnl in selected) / sum(cost for _, cost, _ in selected)
                if selected else None
            ),
            "market_ids": [audit["market_id"] for audit, _, _ in selected],
        }

    summary = {
        "schema_version": "dispute_verdict_resolver_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": {
            "unit": args.scope_label,
            "cases": len(cases),
        },
        "verified": len(verified),
        "coverage": len(verified) / len(cases) if cases else None,
        "correct": sum(row["correct"] is True for row in verified),
        "accuracy": sum(row["correct"] is True for row in verified) / len(verified) if verified else None,
        "incorrect_market_ids": [row["market_id"] for row in verified if row["correct"] is False],
        "status_counts": {
            status: sum(row["verdict"].get("status") == status for row in rows)
            for status in sorted({str(row["verdict"].get("status")) for row in rows})
        },
        "deterministic_reverse_public_print_diagnostics": {
            f"net_edge_gte_{int(threshold * 100):02d}c": policy_metrics(threshold)
            for threshold in (0.01, 0.02, 0.05, 0.10)
        },
        "deterministic_verified_outcome_public_print_diagnostics": {
            f"net_edge_gte_{int(threshold * 100):02d}c": symmetric_policy_metrics(threshold)
            for threshold in (0.01, 0.02, 0.05, 0.10)
        },
        "fixed_candidate_policy": {
            "policy_id": "binance_verdict_reverse_taker_25share_v1",
            "status": "zero_notional_forward",
            "eligibility": "verified Binance spot/futures 1h RuleVerdict supports reverse; fresh 25-share executable ask; fee-adjusted deterministic edge >= 5c; non-P4",
            **policy_metrics(0.05),
            "historical_expression_limit": "uses first public print, not contemporaneous 25-share executable ask",
        },
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
