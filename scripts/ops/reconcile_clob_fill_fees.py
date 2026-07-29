#!/usr/bin/env python3
"""Reconcile immutable CLOB fills against exact public activity cash evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.clob_fill_fee_adjustments import (  # noqa: E402
    DEFAULT_FEE_ADJUSTMENT_PATH,
    append_fee_adjustment,
    import_fee_adjustments,
)
from weather_dashboard.ingest.clob_fill_cache import DEFAULT_CACHE_PATH, iter_cached_fills  # noqa: E402
from weather_dashboard.ingest.clob_fill_sync import (  # noqa: E402
    _discover_funder,
    _exact_activity_fee_for_fill,
    _extract_place_transaction_hashes,
    _fetch_activity_public,
    _index_public_activity_by_tx,
    _weather_fee_estimate,
)


def _adjustment_id(
    fill_id: str,
    fee_source: str,
    evidence_key: str,
    fee_usd: float,
) -> str:
    raw = f"{fill_id}|{fee_source}|{evidence_key}|{fee_usd:.5f}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _load_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    has_adjustments = _table_exists(conn, "fill_fee_adjustments")
    adjustment_cte = (
        "SELECT fill_id, SUM(fee_delta_usd) fee_delta_usd, COUNT(*) adjustment_rows "
        "FROM fill_fee_adjustments GROUP BY fill_id"
        if has_adjustments
        else "SELECT NULL fill_id, 0.0 fee_delta_usd, 0 adjustment_rows WHERE 0"
    )
    has_fact = _table_exists(conn, "fact_trades")
    fact_join = "LEFT JOIN fact_trades ft ON ft.fill_id=f.fill_id" if has_fact else ""
    fact_columns = (
        "ft.strategy_key, ft.strategy_name, ft.instance_id, ft.trade_class, "
        "ft.settlement_status, ft.pnl_usd_at_fill"
        if has_fact
        else "NULL strategy_key, NULL strategy_name, NULL instance_id, NULL trade_class, NULL settlement_status, NULL pnl_usd_at_fill"
    )
    has_aliases = _table_exists(conn, "order_execution_aliases")
    alias_join = (
        "LEFT JOIN order_execution_aliases alias "
        "ON alias.alias_execution_id=o.execution_id"
        if has_aliases
        else ""
    )
    alias_filter = "AND alias.alias_execution_id IS NULL" if has_aliases else ""
    has_validity = _table_exists(conn, "fill_validity_adjustments")
    validity_join = (
        "LEFT JOIN fill_validity_adjustments validity ON validity.fill_id=f.fill_id"
        if has_validity
        else ""
    )
    validity_filter = (
        "AND COALESCE(validity.effective_status, 'valid') <> 'excluded'"
        if has_validity
        else ""
    )
    rows = conn.execute(
        f"""
        WITH fee_adj AS ({adjustment_cte})
        SELECT
          f.fill_id, f.execution_id, f.order_id, f.filled_shares, f.filled_price,
          f.fees_usd AS base_fee_usd, f.fee_source AS base_fee_source,
          f.filled_at_utc,
          COALESCE(fee_adj.fee_delta_usd, 0.0) AS existing_adjustment_usd,
          COALESCE(fee_adj.adjustment_rows, 0) AS adjustment_rows,
          o.order_side, o.exchange_response,
          COALESCE(CAST(json_extract(o.exchange_response, '$.maker_only') AS INTEGER), 0) AS maker_only,
          lower(COALESCE(json_extract(o.exchange_response, '$.place.status'), '')) AS place_status,
          sig.condition_id, sig.token_id, sig.city, sig.target_date, sig.bracket,
          {fact_columns}
        FROM fills f
        JOIN orders o ON o.execution_id=f.execution_id
        JOIN plans p ON p.plan_id=o.plan_id
        JOIN signals sig ON sig.signal_id=p.signal_id
        LEFT JOIN fee_adj ON fee_adj.fill_id=f.fill_id
        {fact_join}
        {alias_join}
        {validity_join}
        WHERE o.venue='polymarket_clob'
          AND o.status='submitted'
          AND f.status IN ('filled', 'partial')
          {alias_filter}
          {validity_filter}
        ORDER BY f.filled_at_utc, f.fill_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _is_tmax(row: dict[str, Any]) -> bool:
    haystack = "|".join(
        str(row.get(key) or "")
        for key in ("strategy_key", "strategy_name", "instance_id")
    ).lower()
    return "tmax" in haystack


def reconcile(
    conn: sqlite3.Connection,
    activity_rows: list[dict[str, Any]],
    cache_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    activity_by_tx = _index_public_activity_by_tx(activity_rows)
    cache_tx_by_fill = {
        str(row.get("fill_id") or ""): str(
            row.get("transaction_hash") or row.get("transactionHash") or ""
        ).lower()
        for row in cache_rows
        if row.get("fill_id")
        and (row.get("transaction_hash") or row.get("transactionHash"))
    }
    rows = _load_rows(conn)
    now = datetime.now(timezone.utc).isoformat()
    details: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []
    for row in rows:
        tx_hashes = _extract_place_transaction_hashes(row)
        cache_tx_hash = cache_tx_by_fill.get(str(row["fill_id"]))
        if cache_tx_hash and cache_tx_hash not in tx_hashes:
            tx_hashes.append(cache_tx_hash)
        exact = _exact_activity_fee_for_fill(
            transaction_hashes=tx_hashes,
            activity_by_tx=activity_by_tx,
            condition_id=str(row.get("condition_id") or ""),
            token_id=str(row.get("token_id") or ""),
            order_side=str(row.get("order_side") or ""),
            expected_shares=float(row.get("filled_shares") or 0.0),
        )
        effective_fee = float(row.get("base_fee_usd") or 0.0) + float(
            row.get("existing_adjustment_usd") or 0.0
        )
        base_fee_source = str(row.get("base_fee_source") or "").strip().lower()
        needs_lineage = base_fee_source in {"", "legacy_unknown", "unknown"}
        detail = {
            "fill_id": row["fill_id"],
            "execution_id": row["execution_id"],
            "order_id": row["order_id"],
            "filled_at_utc": row["filled_at_utc"],
            "city": row["city"],
            "target_date": row["target_date"],
            "bracket": row["bracket"],
            "side": row["order_side"],
            "filled_shares": row["filled_shares"],
            "filled_price": row["filled_price"],
            "base_fee_usd": row["base_fee_usd"],
            "base_fee_source": row.get("base_fee_source"),
            "existing_adjustment_usd": row["existing_adjustment_usd"],
            "transaction_hashes": tx_hashes,
            "exact_match": exact is not None,
            "exact_fee_usd": exact["fees_usd"] if exact else None,
            "proposed_fee_delta_usd": None,
            "strategy_key": row.get("strategy_key"),
            "instance_id": row.get("instance_id"),
            "settlement_status": row.get("settlement_status"),
            "is_tmax": _is_tmax(row),
            "matched_response": bool(_extract_place_transaction_hashes(row)),
            "maker_only": bool(row.get("maker_only")),
            "place_status": row.get("place_status"),
            "proposed_fee_source": None,
            "proposed_evidence_class": None,
        }
        proposal: dict[str, Any] | None = None
        if (
            int(row.get("adjustment_rows") or 0) == 0
            and exact is not None
            and (
                needs_lineage
                or abs(float(exact["fees_usd"]) - effective_fee) > 0.00001
            )
        ):
            evidence_rows = []
            for tx_hash in tx_hashes:
                for activity in activity_by_tx.get(tx_hash, []):
                    if (
                        str(activity.get("conditionId") or "") == str(row.get("condition_id") or "")
                        and str(activity.get("asset") or "") == str(row.get("token_id") or "")
                        and str(activity.get("side") or "").upper() == "BUY"
                    ):
                        evidence_rows.append(
                            {
                                key: activity.get(key)
                                for key in (
                                    "transactionHash", "conditionId", "asset", "side",
                                    "size", "price", "usdcSize", "timestamp", "outcome",
                                )
                            }
                        )
            proposal = {
                "fee_delta_usd": round(float(exact["fees_usd"]) - effective_fee, 5),
                "fee_source": "public_activity_tx_exact",
                "fee_evidence_class": "exact",
                "transaction_hash": exact.get("transaction_hash"),
                "fee_rate": exact.get("fee_rate"),
                "market_fee_metadata": exact.get("fee_metadata") or {},
                "evidence_key": ",".join(sorted(tx_hashes)),
                "evidence": {
                    "order_transaction_hashes": tx_hashes,
                    "activity_rows": evidence_rows,
                    "base_fee_usd": row["base_fee_usd"],
                    "effective_fee_before_usd": effective_fee,
                    "exact_fee_usd": exact["fees_usd"],
                },
            }
        elif (
            int(row.get("adjustment_rows") or 0) == 0
            and needs_lineage
            and bool(row.get("maker_only"))
            and row.get("place_status") == "live"
        ):
            proposal = {
                "fee_delta_usd": 0.0,
                "fee_source": "maker_zero",
                "fee_evidence_class": "exact",
                "transaction_hash": None,
                "fee_rate": 0.0,
                "market_fee_metadata": {
                    "maker_fee_rate": 0.0,
                    "evidence_basis": "order_semantics",
                },
                "evidence_key": "maker_only=1|place_status=live",
                "evidence": {
                    "maker_only": True,
                    "place_status": "live",
                    "reason": "maker order semantics imply zero maker fee",
                    "base_fee_usd": row["base_fee_usd"],
                },
            }
        elif (
            int(row.get("adjustment_rows") or 0) == 0
            and needs_lineage
            and not bool(row.get("maker_only"))
            and row.get("place_status") == "live"
        ):
            estimated_fee = _weather_fee_estimate(
                float(row.get("filled_shares") or 0.0),
                float(row.get("filled_price") or 0.0),
            )
            proposal = {
                "fee_delta_usd": round(estimated_fee - effective_fee, 5),
                "fee_source": "weather_fee_curve_estimate",
                "fee_evidence_class": "estimate",
                "transaction_hash": None,
                "fee_rate": 0.05,
                "market_fee_metadata": {
                    "category": "weather",
                    "taker_fee_rate": 0.05,
                    "fee_formula": "shares*fee_rate*price*(1-price)",
                    "fee_precision_decimals": 5,
                },
                "evidence_key": "maker_only=0|place_status=live|no_exact_activity",
                "evidence": {
                    "maker_only": False,
                    "place_status": "live",
                    "reason": "no exact activity tx evidence; weather taker curve last resort",
                    "filled_shares": row["filled_shares"],
                    "filled_price": row["filled_price"],
                    "base_fee_usd": row["base_fee_usd"],
                },
            }
        if proposal is not None:
            detail["proposed_fee_delta_usd"] = proposal["fee_delta_usd"]
            detail["proposed_fee_source"] = proposal["fee_source"]
            detail["proposed_evidence_class"] = proposal["fee_evidence_class"]
            adjustments.append(
                {
                    "adjustment_id": _adjustment_id(
                        row["fill_id"],
                        proposal["fee_source"],
                        proposal.pop("evidence_key"),
                        float(proposal["fee_delta_usd"]),
                    ),
                    "fill_id": row["fill_id"],
                    **proposal,
                    "created_at_utc": now,
                }
            )
        details.append(detail)

    proposed_by_fill = {row["fill_id"]: row for row in adjustments}
    settled_delta = -sum(
        float(proposed_by_fill[row["fill_id"]]["fee_delta_usd"])
        for row in rows
        if row["fill_id"] in proposed_by_fill and row.get("settlement_status") == "settled"
    )
    tmax_settled_delta = -sum(
        float(proposed_by_fill[row["fill_id"]]["fee_delta_usd"])
        for row in rows
        if row["fill_id"] in proposed_by_fill
        and row.get("settlement_status") == "settled"
        and _is_tmax(row)
    )
    matched_details = [row for row in details if row["exact_match"]]
    exact_adjustments = [
        row for row in adjustments if row["fee_evidence_class"] == "exact"
    ]
    estimated_adjustments = [
        row for row in adjustments if row["fee_evidence_class"] == "estimate"
    ]
    summary = {
        "generated_at_utc": now,
        "scope": "all_submitted_clob_fill_fee_lineage",
        "rows_scanned": len(rows),
        "time_window": {
            "start": min((str(row["filled_at_utc"]) for row in rows), default=None),
            "end": max((str(row["filled_at_utc"]) for row in rows), default=None),
        },
        "exact_tx_activity_matches": len(matched_details),
        "exact_tx_activity_unmatched": len(rows) - len(matched_details),
        "proposed_adjustments": len(adjustments),
        "proposed_exact_adjustments": len(exact_adjustments),
        "proposed_exact_fee_usd": round(
            sum(float(row["fee_delta_usd"]) for row in exact_adjustments), 5
        ),
        "proposed_estimated_adjustments": len(estimated_adjustments),
        "proposed_estimated_fee_usd": round(
            sum(float(row["fee_delta_usd"]) for row in estimated_adjustments), 5
        ),
        "proposed_by_source": {
            source: {
                "rows": sum(1 for row in adjustments if row["fee_source"] == source),
                "fee_usd": round(
                    sum(
                        float(row["fee_delta_usd"])
                        for row in adjustments
                        if row["fee_source"] == source
                    ),
                    5,
                ),
            }
            for source in sorted({row["fee_source"] for row in adjustments})
        },
        "all_weather_settled_pnl_delta_usd": round(settled_delta, 5),
        "tmax_settled_pnl_delta_usd": round(tmax_settled_delta, 5),
        "details": details,
    }
    return summary, adjustments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument("--wallet")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--journal", type=Path, default=DEFAULT_FEE_ADJUSTMENT_PATH)
    parser.add_argument("--apply-exact", action="store_true")
    parser.add_argument("--apply-lineage", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        wallet = args.wallet or _discover_funder(conn)
        activity = _fetch_activity_public(wallet)
        cache_rows = list(iter_cached_fills(args.cache))
        summary, adjustments = reconcile(conn, activity, cache_rows)
        summary["wallet"] = wallet
        summary["activity_trade_rows"] = len(activity)
        summary["cache_rows"] = len(cache_rows)
        summary["apply_exact"] = args.apply_exact
        summary["apply_lineage"] = args.apply_lineage
        rows_to_apply = (
            adjustments
            if args.apply_lineage
            else [row for row in adjustments if row["fee_evidence_class"] == "exact"]
            if args.apply_exact
            else []
        )
        if args.apply_lineage or args.apply_exact:
            appended = sum(1 for row in rows_to_apply if append_fee_adjustment(row, args.journal))
            imported = import_fee_adjustments(conn, args.journal)
            summary["journal"] = str(args.journal)
            summary["journal_rows_appended"] = appended
            summary["db_adjustments_imported"] = imported
        text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(text + "\n", encoding="utf-8")
        print(text)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
