"""clob_fill_sync.py -- Sync CLOB fill status for submitted Polymarket orders.

Queries the DB for all polymarket_clob orders with status='submitted', calls the
Polymarket CLOB API to check their current fill state, and inserts fill records
for any that have been matched on-chain.

Strategy
--------
1. Query DB for all ``venue='polymarket_clob'`` orders with ``status='submitted'``.
2. Extract ``orderID`` from ``exchange_response`` JSON
   (``json_extract(exchange_response, '$.place.orderID')``).
3. Attempt authenticated CLOB calls (requires ``POLYGON_WALLET_PRIVATE_KEY``):
   a. Bulk-fetch trades via ``GET /data/trades?maker_address=...``
      and index by ``maker_order_id``.
   b. For each submitted order, also call ``GET /data/order/{id}`` to get
      precise status (``MATCHED``, ``CANCELLED``, ``LIVE``, ...).
4. If a fill is found: insert a row into ``fills`` with ``status='filled'``.
5. Count cancelled / still-open / errors.
6. Return summary dict ``{checked, filled, cancelled, still_open, errors}``.

Important schema notes
----------------------
* ``orders`` has a ``BEFORE UPDATE`` trigger -- order status **cannot** be changed.
  Fill outcome is recorded in the ``fills`` table only.
* ``fills`` has a ``BEFORE UPDATE`` trigger too, so we only INSERT (idempotent
  via ``INSERT OR IGNORE``).

Fallback (no credentials)
--------------------------
If ``POLYGON_WALLET_PRIVATE_KEY`` is absent, the script falls back to the
unauthenticated ``data-api.polymarket.com/trades?maker_address=`` endpoint.
This endpoint returns public trades matched against our maker address but does
**not** include the CLOB ``orderID``, so matches are approximate
(condition_id + side + timestamp).

Usage
-----
    python -m weather_dashboard.ingest.clob_fill_sync --db-path runtime/weather.db
    python -m weather_dashboard.ingest.clob_fill_sync --db-path runtime/weather.db --dry-run
    python -m weather_dashboard.ingest.clob_fill_sync --help

Environment variables
---------------------
    POLYGON_WALLET_PRIVATE_KEY  signer private key (enables authenticated CLOB)
    PM_ADDRESS                  funder address if different from signer
    CLOB_API_KEY                CLOB API key (auto-derived if absent)
    CLOB_SECRET                 CLOB API secret
    CLOB_PASS_PHRASE            CLOB passphrase
    CLOB_BASE_URL               override CLOB host (default: https://clob.polymarket.com)
    CLOB_CHAIN_ID               Polygon chain id (default: 137)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import httpx
import requests

from weather_dashboard.ingest.clob_fill_cache import (
    DEFAULT_CACHE_PATH,
    append_cached_fill,
    import_cached_fills,
)
from weather_dashboard.ingest.clob_fill_fee_adjustments import (
    DEFAULT_FEE_ADJUSTMENT_PATH,
    import_fee_adjustments,
)
from weather_dashboard.ingest.clob_fill_price_adjustments import (
    DEFAULT_PRICE_ADJUSTMENT_PATH,
    import_price_adjustments,
)
from weather_dashboard.ingest.clob_fill_timestamp_adjustments import (
    DEFAULT_TIMESTAMP_ADJUSTMENT_PATH,
    import_timestamp_adjustments,
)
from weather_dashboard.ingest.clob_fill_validity_adjustments import (
    DEFAULT_VALIDITY_ADJUSTMENT_PATH,
    import_validity_adjustments,
)

log = logging.getLogger(__name__)
EXTERNAL_FETCH_ERRORS: list[str] = []

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOB_HOST_DEFAULT = "https://clob.polymarket.com"
DATA_API_HOST = "https://data-api.polymarket.com"
REQUEST_TIMEOUT = 10  # seconds
CLOB_CLIENT_TIMEOUT_DEFAULT = 15.0
PAGE_SIZE = 500
WEATHER_TAKER_FEE_RATE = Decimal("0.05")
FEE_QUANTUM = Decimal("0.00001")

# Default maker/signer address for the weather strategy
DEFAULT_MAKER_ADDRESS = "0x5eb81Cc2f0810B9D082328CC788Bfc7dA1Ac136f"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_fill_id(execution_id: str, order_id: str) -> str:
    """Deterministic fill_id using sha256(execution_id + order_id)."""
    raw = f"{execution_id}|clob_fill|{order_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_public_trade_fill_id(execution_id: str, public_trade_key: str) -> str:
    """Deterministic fill_id for one public activity partial fill."""
    raw = f"{execution_id}|clob_public_trade_fill|{public_trade_key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_order_delta_fill_id(
    execution_id: str,
    order_id: str,
    target_shares: float,
    target_cost: float,
) -> str:
    """Deterministic fill_id for an authenticated order-level top-up fill."""
    raw = (
        f"{execution_id}|clob_order_delta_fill|{order_id}|"
        f"{target_shares:.8f}|{target_cost:.8f}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _already_have_fill(conn: sqlite3.Connection, fill_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fills WHERE fill_id = ?", (fill_id,)
    ).fetchone()
    return row is not None


def _already_have_physical_fill(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    filled_at_utc: str | None,
    filled_shares: float,
    filled_price: float,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM fills
        WHERE order_id = ?
          AND COALESCE(filled_at_utc, '') = COALESCE(?, '')
          AND ABS(filled_shares - ?) <= 0.000001
          AND ABS(filled_price - ?) <= 0.00001
        LIMIT 1
        """,
        (order_id, filled_at_utc, filled_shares, filled_price),
    ).fetchone()
    return row is not None


def _ts_to_iso(ts_raw: Any) -> str | None:
    """Convert a Polymarket timestamp to an ISO-8601 UTC string.

    Polymarket CLOB endpoints return different timestamp formats:
    - ``updatedAt`` / ``timestamp`` from /data/order and /data/trades:
      Unix **seconds** as an integer (e.g. 1779436328 ≈ May 2026)
    - Some endpoints return Unix **milliseconds** (13-digit int)

    Heuristic: values < 1e11 are treated as seconds; >= 1e11 as milliseconds.
    This correctly handles timestamps from 1970 through ~year 5138 in both units.
    """
    if ts_raw is None:
        return None
    # Already an ISO string (pass through after sanity check)
    if isinstance(ts_raw, str) and not ts_raw.isdigit():
        try:
            from datetime import datetime as _dt
            _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
            return ts_raw  # valid ISO
        except ValueError:
            pass
    try:
        val = float(ts_raw)
        # If the float looks like seconds (9-10 digit), use directly.
        # If it looks like milliseconds (13-digit), divide by 1000.
        if val >= 1e11:  # clearly milliseconds
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ts_raw)


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _round_fee(value: Decimal) -> float:
    return float(value.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP))


def _weather_fee_estimate(shares: float, price: float) -> float:
    qty = _as_decimal(shares)
    px = _as_decimal(price)
    if qty is None or px is None or qty <= 0 or px <= 0 or px >= 1:
        return 0.0
    return _round_fee(qty * WEATHER_TAKER_FEE_RATE * px * (Decimal("1") - px))


def _public_buy_fee_details(trade: dict[str, Any]) -> dict[str, Any] | None:
    """Derive exact BUY fee from public cash less gross token cost."""
    if str(trade.get("side") or "").upper() != "BUY":
        return None
    usdc_size = _as_decimal(trade.get("usdcSize"))
    size = _as_decimal(trade.get("size"))
    price = _as_decimal(trade.get("price"))
    if usdc_size is None or size is None or price is None or size <= 0 or price <= 0:
        return None
    gross = size * price
    fee = max(usdc_size - gross, Decimal("0"))
    denominator = size * price * (Decimal("1") - price)
    effective_rate = float(fee / denominator) if fee > 0 and denominator > 0 else 0.0
    return {
        "fees_usd": _round_fee(fee),
        "fee_source": "public_activity_cash_delta_exact",
        "fee_rate": effective_rate,
        "transaction_hash": str(trade.get("transactionHash") or "") or None,
        "fee_metadata": {
            "usdc_size": float(usdc_size),
            "gross_usd": float(gross),
            "activity_size": float(size),
            "activity_price": float(price),
            "effective_fee_rate": effective_rate,
        },
    }


def _extract_place_transaction_hashes(
    row: sqlite3.Row | dict[str, Any],
) -> list[str]:
    raw = row["exchange_response"] if "exchange_response" in row.keys() else None
    try:
        response = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    place = response.get("place") if isinstance(response, dict) else None
    if not isinstance(place, dict):
        return []
    hashes = (
        place.get("transactionsHashes")
        or place.get("transactionHashes")
        or place.get("transaction_hashes")
        or []
    )
    if isinstance(hashes, str):
        hashes = [hashes]
    return [str(value).lower() for value in hashes if value]


def _maker_only(row: sqlite3.Row | dict[str, Any]) -> bool:
    raw = row["exchange_response"] if "exchange_response" in row.keys() else None
    try:
        response = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    return bool(response.get("maker_only")) if isinstance(response, dict) else False


def _index_public_activity_by_tx(
    trades: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_tx: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        tx_hash = str(trade.get("transactionHash") or "").lower()
        if tx_hash:
            by_tx.setdefault(tx_hash, []).append(trade)
    return by_tx


def _exact_activity_fee_for_fill(
    *,
    transaction_hashes: list[str],
    activity_by_tx: dict[str, list[dict[str, Any]]],
    condition_id: str,
    token_id: str,
    order_side: str,
    expected_shares: float,
) -> dict[str, Any] | None:
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for tx_hash in transaction_hashes:
        for trade in activity_by_tx.get(tx_hash.lower(), []):
            if str(trade.get("conditionId") or "") != condition_id:
                continue
            if token_id and str(trade.get("asset") or "") != token_id:
                continue
            if not _side_matches_public(order_side, trade):
                continue
            details = _public_buy_fee_details(trade)
            if details is not None:
                matched.append((trade, details))
    if not matched:
        return None
    activity_shares = sum(float(trade.get("size") or 0.0) for trade, _ in matched)
    if expected_shares > 0 and abs(activity_shares - expected_shares) > 0.000001:
        return None
    fees_usd = round(sum(float(details["fees_usd"]) for _, details in matched), 5)
    tx_hashes = sorted(
        {str(trade.get("transactionHash") or "").lower() for trade, _ in matched}
    )
    weighted_denom = sum(
        float(trade.get("size") or 0.0)
        * float(trade.get("price") or 0.0)
        * (1.0 - float(trade.get("price") or 0.0))
        for trade, _ in matched
    )
    return {
        "fees_usd": fees_usd,
        "fee_source": "public_activity_tx_exact",
        "fee_rate": fees_usd / weighted_denom if fees_usd > 0 and weighted_denom > 0 else 0.0,
        "transaction_hash": tx_hashes[0] if len(tx_hashes) == 1 else None,
        "fee_metadata": {
            "transaction_hashes": tx_hashes,
            "activity_rows": len(matched),
            "activity_shares": activity_shares,
            "evidence": [details["fee_metadata"] for _, details in matched],
        },
    }


def _fallback_fee_details(
    *,
    shares: float,
    price: float,
    maker_only: bool,
) -> dict[str, Any]:
    if maker_only:
        return {
            "fees_usd": 0.0,
            "fee_source": "maker_zero",
            "fee_rate": 0.0,
            "transaction_hash": None,
            "fee_metadata": {"maker_only": True},
        }
    return {
        "fees_usd": _weather_fee_estimate(shares, price),
        "fee_source": "weather_fee_curve_estimate",
        "fee_rate": float(WEATHER_TAKER_FEE_RATE),
        "transaction_hash": None,
        "fee_metadata": {
            "fee_formula": "shares*fee_rate*price*(1-price)",
            "fee_rate": float(WEATHER_TAKER_FEE_RATE),
            "evidence_class": "estimate",
        },
    }


def _resolve_fee_details(
    *,
    authenticated_fee_usd: float,
    authenticated_fee_rate: float | None,
    authenticated_metadata: dict[str, Any] | None,
    transaction_hashes: list[str],
    activity_by_tx: dict[str, list[dict[str, Any]]],
    condition_id: str,
    token_id: str,
    order_side: str,
    shares: float,
    price: float,
    maker_only: bool,
) -> dict[str, Any]:
    if authenticated_fee_usd > 0:
        return {
            "fees_usd": authenticated_fee_usd,
            "fee_source": "authenticated_taker_fee",
            "fee_rate": authenticated_fee_rate,
            "transaction_hash": None,
            "fee_metadata": authenticated_metadata or {},
        }
    exact = _exact_activity_fee_for_fill(
        transaction_hashes=transaction_hashes,
        activity_by_tx=activity_by_tx,
        condition_id=condition_id,
        token_id=token_id,
        order_side=order_side,
        expected_shares=shares,
    )
    if exact is not None:
        return exact
    return _fallback_fee_details(shares=shares, price=price, maker_only=maker_only)


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _get_submitted_orders(
    conn: sqlite3.Connection,
    *,
    placed_after_utc: str | None = None,
) -> list[sqlite3.Row]:
    """Return all polymarket_clob orders with status='submitted'."""
    lookback_clause = (
        "AND julianday(o.placed_at_utc) >= julianday(?)"
        if placed_after_utc
        else ""
    )
    return conn.execute(
        f"""
        SELECT
            o.execution_id,
            o.order_id,
            json_extract(o.exchange_response, '$.place.orderID') AS clob_order_id,
            o.shares,
            o.limit_price,
            o.placed_at_utc,
            o.order_side,
            o.exchange_response,
            sig.condition_id,
            sig.token_id
        FROM orders o
        JOIN plans   p   ON o.plan_id   = p.plan_id
        JOIN signals sig ON p.signal_id = sig.signal_id
        LEFT JOIN order_execution_aliases alias
          ON alias.alias_execution_id = o.execution_id
        WHERE o.venue  = 'polymarket_clob'
          AND o.status = 'submitted'
          AND alias.alias_execution_id IS NULL
          {lookback_clause}
        ORDER BY o.placed_at_utc ASC
        """,
        (placed_after_utc,) if placed_after_utc else (),
    ).fetchall()


def _insert_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    execution_id: str,
    order_id: str,
    filled_shares: float,
    filled_price: float,
    fees_usd: float,
    filled_at_utc: str | None,
    dry_run: bool,
    fee_source: str = "legacy_unknown",
    fee_rate: float | None = None,
    fee_metadata: dict[str, Any] | None = None,
    transaction_hash: str | None = None,
) -> bool:
    """INSERT OR IGNORE a fill row (idempotent)."""
    now = _now_utc()
    if dry_run:
        log.info(
            "[dry-run] Would INSERT fill fill_id=%s... execution_id=%s... "
            "shares=%.6f price=%.4f fees=%.6f",
            fill_id[:12], execution_id[:12], filled_shares, filled_price, fees_usd,
        )
        return False
    effective_filled_at = filled_at_utc or now
    if _already_have_physical_fill(
        conn,
        order_id=order_id,
        filled_at_utc=effective_filled_at,
        filled_shares=filled_shares,
        filled_price=filled_price,
    ):
        return False
    before = conn.total_changes
    base_values = (
        fill_id,
        execution_id,
        order_id,
        filled_shares,
        filled_price,
        fees_usd,
        effective_filled_at,
        now,
    )
    fill_columns = {
        str(info[1]) for info in conn.execute("PRAGMA table_info(fills)").fetchall()
    }
    if "fee_source" in fill_columns:
        conn.execute(
            """
            INSERT OR IGNORE INTO fills (
                fill_id, execution_id, order_id, filled_shares, filled_price,
                fees_usd, status, filled_at_utc, created_at_utc,
                fee_source, fee_rate, fee_metadata_json, transaction_hash
            ) VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?, ?, ?, ?, ?)
            """,
            base_values
            + (
                fee_source,
                fee_rate,
                json.dumps(fee_metadata or {}, sort_keys=True),
                transaction_hash,
            ),
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO fills
                (fill_id, execution_id, order_id, filled_shares, filled_price,
                 fees_usd, status, filled_at_utc, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?)
            """,
            base_values,
        )
    conn.commit()
    inserted = conn.total_changes > before
    if inserted:
        append_cached_fill(
            {
                "fill_id": fill_id,
                "execution_id": execution_id,
                "order_id": order_id,
                "filled_shares": filled_shares,
                "filled_price": filled_price,
                "fees_usd": fees_usd,
                "fee_source": fee_source,
                "fee_rate": fee_rate,
                "fee_metadata": fee_metadata or {},
                "transaction_hash": transaction_hash,
                "filled_at_utc": effective_filled_at,
                "created_at_utc": now,
            }
        )
    return inserted


def _existing_fill_totals(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    order_id: str,
) -> dict[str, float]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS fills,
          COALESCE(SUM(filled_shares), 0.0) AS shares,
          COALESCE(SUM(filled_shares * filled_price), 0.0) AS cost,
          COALESCE(SUM(fees_usd), 0.0) AS fees
        FROM fills
        WHERE execution_id = ? AND order_id = ?
        """,
        (execution_id, order_id),
    ).fetchone()
    return {
        "fills": float(row["fills"] if row else 0.0),
        "shares": float(row["shares"] if row else 0.0),
        "cost": float(row["cost"] if row else 0.0),
        "fees": float(row["fees"] if row else 0.0),
    }


def _insert_order_fill_top_up(
    conn: sqlite3.Connection,
    *,
    base_fill_id: str,
    execution_id: str,
    order_id: str,
    target_shares: float,
    target_price: float,
    fees_usd: float,
    filled_at_utc: str | None,
    dry_run: bool,
    fee_source: str = "legacy_unknown",
    fee_rate: float | None = None,
    fee_metadata: dict[str, Any] | None = None,
    transaction_hash: str | None = None,
) -> bool:
    """Insert the missing delta when auth CLOB reports a larger total fill.

    Authenticated order status is order-level, while prior cache rows may only
    contain the first partial fill. Keep the existing row and append only the
    missing shares/cost delta so fact_trades remains fill-grain and idempotent.
    """
    if target_shares <= 0 or target_price <= 0:
        return False
    existing = _existing_fill_totals(
        conn,
        execution_id=execution_id,
        order_id=order_id,
    )
    target_cost = target_shares * target_price
    existing_shares = existing["shares"]
    existing_cost = existing["cost"]
    if existing_shares <= 1e-9:
        return _insert_fill(
            conn,
            fill_id=base_fill_id,
            execution_id=execution_id,
            order_id=order_id,
            filled_shares=target_shares,
            filled_price=target_price,
            fees_usd=fees_usd,
            filled_at_utc=filled_at_utc,
            dry_run=dry_run,
            fee_source=fee_source,
            fee_rate=fee_rate,
            fee_metadata=fee_metadata,
            transaction_hash=transaction_hash,
        )
    if target_shares <= existing_shares + 1e-6:
        return False

    delta_shares = target_shares - existing_shares
    delta_cost = max(target_cost - existing_cost, 0.0)
    delta_price = delta_cost / delta_shares if delta_cost > 0 else target_price
    delta_fees = max(fees_usd - existing["fees"], 0.0)
    delta_fill_id = _make_order_delta_fill_id(
        execution_id,
        order_id,
        target_shares,
        target_cost,
    )
    return _insert_fill(
        conn,
        fill_id=delta_fill_id,
        execution_id=execution_id,
        order_id=order_id,
        filled_shares=delta_shares,
        filled_price=delta_price,
        fees_usd=delta_fees,
        filled_at_utc=filled_at_utc,
        dry_run=dry_run,
        fee_source=fee_source,
        fee_rate=fee_rate,
        fee_metadata=fee_metadata,
        transaction_hash=transaction_hash,
    )


def _cap_reported_fill_to_order(
    *,
    execution_id: str,
    order_id: str,
    reported_shares: float,
    reported_price: float,
    row_shares: float,
    row_limit_price: float,
) -> tuple[float, float]:
    """Keep recovered order-level fills inside the local submitted order cap."""
    shares = reported_shares
    price = reported_price
    if row_shares > 0 and shares > row_shares + 1e-6:
        log.warning(
            "Capping recovered fill shares for execution_id=%s... order_id=%s... "
            "reported=%.6f cap=%.6f",
            execution_id[:12],
            order_id[:12],
            shares,
            row_shares,
        )
        shares = row_shares
    if row_limit_price > 0 and price > row_limit_price + 1e-6:
        log.warning(
            "Capping recovered fill price for execution_id=%s... order_id=%s... "
            "reported=%.6f cap=%.6f",
            execution_id[:12],
            order_id[:12],
            price,
            row_limit_price,
        )
        price = row_limit_price
    return shares, price


def _submitted_order_price_cap(row: sqlite3.Row | dict[str, Any]) -> float:
    """Return the actual exchange-posted cap, not the pre-tick request.

    The executor can round a requested maker quote to the venue tick (for
    example 0.791 -> 0.80).  Authenticated order/trade evidence is therefore
    allowed up to ``posted_price``; using ``limit_price`` here silently clips
    the real fill price and understates cost.
    """
    keys = set(row.keys())
    for key in ("posted_price", "limit_price"):
        if key not in keys:
            continue
        try:
            value = float(row[key] or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _extract_immediate_place_fill(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any] | None:
    """Extract exact immediate-match fill details from order exchange_response.

    For successful BUY placements with ``place.status=matched``, Polymarket
    returns USDC ``makingAmount`` and token ``takingAmount`` in the local order
    response. This is more precise than later public account activity, which is
    account-level and does not expose CLOB order IDs.
    """
    raw = row["exchange_response"] if "exchange_response" in row.keys() else None
    if not raw:
        return None
    try:
        response = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(response, dict):
        return None
    place = response.get("place") or {}
    if not isinstance(place, dict):
        return None
    if str(place.get("status") or "").lower() != "matched":
        return None
    if place.get("success") is False:
        return None
    order_side = str(row["order_side"] if "order_side" in row.keys() else "").upper()
    if not order_side.startswith("BUY"):
        return None
    try:
        cost = float(place.get("makingAmount") or 0.0)
        shares = float(place.get("takingAmount") or 0.0)
    except (TypeError, ValueError):
        return None
    if shares <= 0 or cost <= 0:
        return None
    price = cost / shares
    fee = _fallback_fee_details(
        shares=shares,
        price=price,
        maker_only=_maker_only(row),
    )
    return {
        "filled_shares": shares,
        "filled_price": price,
        **fee,
        "transaction_hashes": _extract_place_transaction_hashes(row),
        "filled_at_utc": row["placed_at_utc"] if "placed_at_utc" in row.keys() else None,
    }


# ---------------------------------------------------------------------------
# Authenticated CLOB client
# ---------------------------------------------------------------------------

def _configure_v2_clob_http_client(clob_http_helpers: Any) -> dict[str, Any]:
    """Bind py_clob_client_v2 to the production proxy with a resilient timeout.

    py_clob_client_v2 owns a module-global httpx client created with a five
    second default timeout.  The local proxy can be healthy while an upstream
    TLS handshake takes slightly longer than that, which previously made the
    bounded canonical refresh fail all three attempts.  Replace the singleton
    before any authenticated request and use the explicit production proxy
    environment populated by the canonical launcher.
    """
    proxy = (
        os.getenv("WEATHER_DATA_FEED_MARKET_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("ALL_PROXY", "").strip()
    )
    raw_timeout = os.getenv("WEATHER_CLOB_HTTP_TIMEOUT_SEC", "").strip()
    timeout = float(raw_timeout) if raw_timeout else CLOB_CLIENT_TIMEOUT_DEFAULT
    if timeout <= 0:
        raise ValueError("WEATHER_CLOB_HTTP_TIMEOUT_SEC must be positive")

    previous = getattr(clob_http_helpers, "_http_client", None)
    clob_http_helpers._http_client = httpx.Client(  # noqa: SLF001
        http2=True,
        proxy=proxy or None,
        timeout=timeout,
        trust_env=not bool(proxy),
    )
    if previous is not None and previous is not clob_http_helpers._http_client:
        close = getattr(previous, "close", None)
        if callable(close):
            close()
    return {
        "proxy_configured": bool(proxy),
        "timeout_sec": timeout,
    }


def _build_clob_client() -> Any | None:
    """
    Build a py_clob_client_v2 ClobClient using env-var credentials.
    Returns None if the private key is absent (graceful fallback).
    """
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.constants import POLYGON
        import py_clob_client_v2.http_helpers.helpers as clob_http_helpers

        clob_v2 = True
    except ImportError:
        try:
            from py_clob_client.client import ClobClient  # type: ignore[assignment]
            from py_clob_client.clob_types import ApiCreds  # type: ignore[assignment]
            from py_clob_client.constants import POLYGON  # type: ignore[assignment]

            clob_v2 = False
        except ImportError:
            log.warning("Neither py_clob_client_v2 nor py_clob_client installed.")
            return None

    if clob_v2:
        _configure_v2_clob_http_client(clob_http_helpers)

    host = (
        os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or CLOB_HOST_DEFAULT
    )
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    private_key = (
        os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip()
        or os.getenv("PM", "").strip()
    )
    if not private_key:
        log.warning(
            "POLYGON_WALLET_PRIVATE_KEY not set; authenticated CLOB calls unavailable. "
            "Will fall back to public data-api."
        )
        return None

    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = (
        ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass)
        if api_key and api_secret and api_pass
        else None
    )

    funder = os.getenv("PM_ADDRESS", "").strip() or None
    sig_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))

    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""

    sig_type = sig_type_raw
    if sig_type < 0:
        sig_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0

    client = ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=sig_type,
        funder=funder,
    )
    if creds is None:
        try:
            derived = (
                client.derive_api_key()
                if clob_v2
                else client.create_or_derive_api_creds()
            )
            client.set_api_creds(derived)
        except Exception as exc:
            method = "derive_api_key" if clob_v2 else "create_or_derive_api_creds"
            log.warning("%s failed: %s -- no auth available", method, exc)
            return None
    return client


def _fetch_order_status_clob(client: Any, order_id: str) -> dict[str, Any] | None:
    """Call GET /data/order/{order_id}. Returns dict or None on error."""
    try:
        result = client.get_order(order_id)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        EXTERNAL_FETCH_ERRORS.append(f"clob_order_status:{exc}")
        log.debug("get_order(%s...): %s", order_id[:12], exc)
        return None


def _fetch_trades_clob(client: Any, maker_address: str) -> list[dict[str, Any]]:
    """Call GET /data/trades?maker_address=... with pagination. Returns flat list."""
    try:
        from py_clob_client_v2.clob_types import TradeParams  # type: ignore[import]
    except ImportError:
        try:
            from py_clob_client.clob_types import TradeParams  # type: ignore[import,assignment]
        except ImportError:
            return []
    try:
        params = TradeParams(maker_address=maker_address)
        result = client.get_trades(params=params)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("data", [])
        return []
    except Exception as exc:
        EXTERNAL_FETCH_ERRORS.append(f"clob_trades:{exc}")
        log.warning("CLOB get_trades failed: %s", exc)
        return []


def _index_authenticated_trades_by_order_id(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Index CLOB trades by our exact order, including aggregated maker legs.

    The authenticated endpoint may return one aggregate match with the actual
    maker legs nested in ``maker_orders``.  Its top-level size and price cover
    every maker in the match, so copying them onto our order would overstate a
    fill.  Project each nested leg into an order-specific record instead.
    """
    indexed: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        top_level_order_ids = {
            str(value)
            for value in (
                trade.get("maker_order_id"),
                trade.get("makerOrderId"),
                trade.get("taker_order_id"),
                trade.get("takerOrderId"),
                trade.get("order_id"),
                trade.get("orderId"),
            )
            if value
        }
        for order_id in top_level_order_ids:
            indexed.setdefault(order_id, []).append(trade)

        for maker_order in trade.get("maker_orders") or []:
            if not isinstance(maker_order, dict):
                continue
            order_id = str(maker_order.get("order_id") or maker_order.get("orderId") or "")
            if not order_id:
                continue
            indexed.setdefault(order_id, []).append(
                {
                    **trade,
                    "order_id": order_id,
                    "size": maker_order.get("matched_amount") or maker_order.get("size"),
                    "price": maker_order.get("price") or trade.get("price"),
                    "fee_rate_bps": maker_order.get("fee_rate_bps") or trade.get("fee_rate_bps"),
                }
            )
    return indexed


# ---------------------------------------------------------------------------
# Unauthenticated public fallback
# ---------------------------------------------------------------------------

def _fetch_trades_public(maker_address: str) -> list[dict[str, Any]]:
    """
    Public endpoint: data-api.polymarket.com/trades?maker_address=...
    Returns trades but WITHOUT the CLOB orderID, so correlation is approximate.
    """
    url = f"{DATA_API_HOST}/trades"
    params: dict[str, Any] = {"maker_address": maker_address, "limit": PAGE_SIZE}
    all_trades: list[dict[str, Any]] = []
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        batch = resp.json()
        if isinstance(batch, list):
            all_trades.extend(batch)
            log.debug("Public trades: fetched %d records.", len(all_trades))
    except requests.RequestException as exc:
        EXTERNAL_FETCH_ERRORS.append(f"public_trades:{exc}")
        log.warning("Public trades fetch failed: %s", exc)
    return all_trades


def _fetch_activity_public(
    funder: str,
    *,
    record_errors: bool = True,
) -> list[dict[str, Any]]:
    """
    Public activity endpoint: data-api.polymarket.com/activity?user={funder}
    Returns TRADE and REDEEM events including conditionId. Paginated.
    This is the preferred unauthenticated fallback because the funder wallet
    appears in on-chain activity events whereas the signer/maker address may not.
    """
    url = f"{DATA_API_HOST}/activity"
    all_items: list[dict[str, Any]] = []
    offset = 0
    while True:
        try:
            resp = requests.get(
                url,
                params={"user": funder, "limit": PAGE_SIZE, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json()
        except requests.RequestException as exc:
            if record_errors:
                EXTERNAL_FETCH_ERRORS.append(f"activity:{exc}")
            log.warning("Activity fetch failed at offset=%d: %s", offset, exc)
            break
        if not isinstance(batch, list) or not batch:
            break
        all_items.extend(batch)
        log.debug("Activity: fetched %d items (offset=%d)", len(batch), offset)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return [a for a in all_items if a.get("type") == "TRADE"]


# Fallback funder address for the weather strategy (used when not in exchange_response)
KNOWN_WEATHER_FUNDER = "0x76c7ad96e789e3995046af3bb13218f5f1a2860e"


def _discover_funder(conn: sqlite3.Connection) -> str:
    """
    Return the funder address for the weather strategy.
    Tries to extract from exchange_response (balance_preflight.funder),
    falls back to KNOWN_WEATHER_FUNDER constant.
    """
    env_funder = os.getenv("PM_ADDRESS", "").strip()
    if env_funder:
        return env_funder

    row = conn.execute(
        """
        SELECT json_extract(exchange_response, '$.balance_preflight.funder') AS funder
        FROM orders
        WHERE venue = 'polymarket_clob'
          AND exchange_response IS NOT NULL
          AND json_extract(exchange_response, '$.balance_preflight.funder') IS NOT NULL
        LIMIT 1
        """
    ).fetchone()
    if row and row[0]:
        return row[0]
    return KNOWN_WEATHER_FUNDER


def _parse_clob_order_response(
    data: dict[str, Any], *, expected_shares: float | None = None
) -> dict[str, Any]:
    """
    Normalise a /data/order/{id} response.

    Typical fields:
        status        "LIVE" | "MATCHED" | "CANCELLED" | "EXPIRED"
        sizeMatched   filled qty; current v2 responses use shares, while some
                      legacy responses used smallest token units
        price         limit price (str)
        takerFee      fee in smallest USDC units (/ 1e6 = USD)
        updatedAt     timestamp ms (str)
    """
    status = (data.get("status") or "").upper()

    size_matched_raw = data.get("size_matched") or data.get("sizeMatched") or "0"
    try:
        size_matched = float(size_matched_raw)
        # Do not infer units from camelCase.  Current authenticated responses
        # return e.g. ``sizeMatched='5'`` for a five-share order.  Only treat a
        # value as micro-units when it is implausibly large relative to the
        # submitted order (or clearly >= one token-micro unit without that
        # context).
        micro_threshold = (
            max(100_000.0, float(expected_shares) * 1_000.0)
            if expected_shares is not None and expected_shares > 0
            else 1_000_000.0
        )
        if size_matched >= micro_threshold:
            size_matched /= 1_000_000
    except (ValueError, TypeError):
        size_matched = 0.0

    price_raw = data.get("price") or "0"
    try:
        price = float(price_raw)
    except (ValueError, TypeError):
        price = 0.0

    fee_raw = data.get("takerFee") or data.get("taker_fee") or "0"
    try:
        fees_usd = float(fee_raw) / 1_000_000
    except (ValueError, TypeError):
        fees_usd = 0.0

    filled_at = _ts_to_iso(data.get("updatedAt") or data.get("updated_at"))
    fee_rate_raw = (
        data.get("feeRate")
        or data.get("fee_rate")
        or data.get("feeRateBps")
        or data.get("fee_rate_bps")
    )
    try:
        fee_rate = float(fee_rate_raw) if fee_rate_raw is not None else None
        if fee_rate is not None and fee_rate > 1:
            fee_rate /= 10_000
    except (ValueError, TypeError):
        fee_rate = None

    return {
        "clob_status": status,
        "size_matched": size_matched,
        "price": price,
        "fees_usd": fees_usd,
        "fee_rate": fee_rate,
        "fee_metadata": {
            key: data[key]
            for key in ("takerFee", "taker_fee", "feeRate", "fee_rate", "feeRateBps", "fee_rate_bps")
            if key in data
        },
        "filled_at": filled_at,
    }


def _extract_trade_fill(
    trades: list[dict[str, Any]],
    *,
    row_shares: float,
    row_limit_price: float,
) -> dict[str, Any]:
    """
    Aggregate fill details from a list of CLOB trade records for one order.
    CLOB trade size fields use smallest token units (/ 1e6 = shares).
    """
    total_shares = 0.0
    total_cost = 0.0
    total_fees = 0.0
    fill_time: str | None = None
    transaction_hashes: list[str] = []

    for t in trades:
        sz_raw = t.get("size") or t.get("matched_amount") or t.get("makerAmount") or "0"
        try:
            sz = float(sz_raw)
            if "size" not in t and "matched_amount" not in t:
                sz /= 1_000_000
        except (ValueError, TypeError):
            sz = 0.0

        px_raw = t.get("price") or "0"
        try:
            px = float(px_raw)
        except (ValueError, TypeError):
            px = 0.0

        fee_raw = t.get("fee") or t.get("takerFee") or t.get("makerFee") or "0"
        try:
            fee = float(fee_raw) / 1_000_000
        except (ValueError, TypeError):
            fee = 0.0

        total_shares += sz
        total_cost += sz * px
        total_fees += fee
        tx_hash = (
            t.get("transactionHash")
            or t.get("transaction_hash")
            or t.get("match_tx_hash")
        )
        if tx_hash:
            transaction_hashes.append(str(tx_hash).lower())

        if fill_time is None:
            fill_time = _ts_to_iso(
                t.get("timestamp") or t.get("matchTime") or t.get("match_time")
            )

    avg_price = total_cost / total_shares if total_shares > 0 else row_limit_price
    if total_shares <= 0:
        total_shares = row_shares
    return {
        "size_matched": total_shares,
        "price": avg_price,
        "fees_usd": total_fees,
        "transaction_hashes": sorted(set(transaction_hashes)),
        "filled_at": fill_time,
    }


def _side_matches_public(order_side: str, trade: dict[str, Any]) -> bool:
    """
    Heuristic match between our order_side and a public data-api trade record.
    Public trade fields: side='BUY'/'SELL', outcome='Yes'/'No'.
    """
    side = (trade.get("side") or "").upper()
    outcome = (trade.get("outcome") or "").lower()
    if order_side == "BUY":
        return side == "BUY"
    if order_side == "BUY_YES":
        return side == "BUY" and outcome in ("yes", "")
    if order_side == "BUY_NO":
        return side == "BUY" and outcome == "no"
    if order_side == "SELL_YES":
        return side == "SELL" and outcome in ("yes", "")
    if order_side == "SELL_NO":
        return side == "SELL" and outcome == "no"
    return False


def _public_trade_key(trade: dict[str, Any]) -> str:
    """Best-effort stable key for one public activity trade row."""
    return "|".join(
        str(trade.get(k) or "")
        for k in ("transactionHash", "asset", "timestamp", "side", "outcome", "size", "price")
    )


def _public_trade_matches_order(
    trade: dict[str, Any],
    *,
    condition_id: str,
    token_id: str,
    order_side: str,
    limit_price: float,
    placed_ts: int,
) -> bool:
    """Strict public fallback match.

    Public activity rows do not include the CLOB order id. To avoid inventing
    fills, require the exact condition, exact asset token, compatible side, a
    trade after placement, and an executable price for our buy limit.
    """
    if trade.get("conditionId") != condition_id:
        return False
    if token_id and str(trade.get("asset") or "") != token_id:
        return False
    if not _side_matches_public(order_side, trade):
        return False
    try:
        trade_ts = int(trade.get("timestamp", 0))
    except (TypeError, ValueError):
        return False
    if trade_ts < placed_ts:
        return False
    try:
        trade_price = float(trade.get("price") or 0)
    except (TypeError, ValueError):
        return False
    return trade_price <= limit_price + 1e-6


def _select_public_partial_fills(
    public_trades: list[dict[str, Any]],
    *,
    used_public_trade_keys: set[str],
    condition_id: str,
    token_id: str,
    order_side: str,
    limit_price: float,
    row_shares: float,
    placed_ts: int,
) -> list[dict[str, Any]]:
    """Return public activity rows that fill one submitted order.

    The activity API exposes partial trades, but not CLOB order IDs. Match rows
    by exact condition/token, compatible buy side, executable price, and time
    after placement, then allocate earliest rows up to the submitted size.
    """
    candidates = [
        t for t in public_trades
        if _public_trade_key(t) not in used_public_trade_keys
        and _public_trade_matches_order(
            t,
            condition_id=condition_id,
            token_id=token_id,
            order_side=order_side,
            limit_price=limit_price,
            placed_ts=placed_ts,
        )
    ]
    candidates.sort(key=lambda t: int(t.get("timestamp", 0) or 0))

    selected: list[dict[str, Any]] = []
    selected_shares = 0.0
    selected_cost = 0.0
    max_cost = row_shares * limit_price if row_shares > 0 and limit_price > 0 else 0.0
    for trade in candidates:
        try:
            size = float(trade.get("size") or 0.0)
        except (TypeError, ValueError):
            size = 0.0
        try:
            price = float(trade.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if size <= 0 or price <= 0:
            continue
        if row_shares > 0 and selected_shares + size > row_shares + 1e-6:
            continue
        if max_cost > 0 and selected_cost + size * price > max_cost + 0.02:
            continue
        selected.append(trade)
        selected_shares += size
        selected_cost += size * price
        if row_shares > 0 and selected_shares >= row_shares - 1e-6:
            break
    return selected


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_clob_fills(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    maker_address: str | None = None,
    cache_only: bool = False,
    require_authenticated: bool = False,
    lookback_hours: float | None = None,
) -> dict[str, Any]:
    """
    Sync fill status for all submitted polymarket_clob orders.

    Parameters
    ----------
    conn:           Open SQLite connection (row_factory = sqlite3.Row recommended).
    dry_run:        If True, log what would be done without writing to DB.
    maker_address:  Maker/signer address (defaults to DEFAULT_MAKER_ADDRESS).
    cache_only:     If True, replay the audited persistent cache and skip all
                    external CLOB/public fallback fetches.

    Returns
    -------
    dict with keys: checked, filled, cancelled, still_open, errors, dry_run
    """
    summary: dict[str, Any] = {
        "checked": 0,
        "filled": 0,
        "cancelled": 0,
        "still_open": 0,
        "errors": 0,
        "external_fetch_errors": 0,
        "data_incomplete": False,
        "cached_imported": 0,
        "fee_adjustments_imported": 0,
        "price_adjustments_imported": 0,
        "timestamp_adjustments_imported": 0,
        "validity_adjustments_imported": 0,
        "dry_run": dry_run,
        "cache_only": cache_only,
        "require_authenticated": require_authenticated,
        "lookback_hours": lookback_hours,
    }
    EXTERNAL_FETCH_ERRORS.clear()

    if not dry_run:
        summary["cached_imported"] = import_cached_fills(conn, DEFAULT_CACHE_PATH)
        if summary["cached_imported"]:
            log.info("Imported %d cached CLOB fill(s).", summary["cached_imported"])
        summary["fee_adjustments_imported"] = import_fee_adjustments(
            conn, DEFAULT_FEE_ADJUSTMENT_PATH
        )
        if summary["fee_adjustments_imported"]:
            log.info(
                "Imported %d cached CLOB fee adjustment(s).",
                summary["fee_adjustments_imported"],
            )
        summary["price_adjustments_imported"] = import_price_adjustments(
            conn, DEFAULT_PRICE_ADJUSTMENT_PATH
        )
        if summary["price_adjustments_imported"]:
            log.info(
                "Imported %d cached CLOB price adjustment(s).",
                summary["price_adjustments_imported"],
            )
        summary["timestamp_adjustments_imported"] = import_timestamp_adjustments(
            conn, DEFAULT_TIMESTAMP_ADJUSTMENT_PATH
        )
        if summary["timestamp_adjustments_imported"]:
            log.info(
                "Imported %d cached CLOB timestamp adjustment(s).",
                summary["timestamp_adjustments_imported"],
            )
        summary["validity_adjustments_imported"] = import_validity_adjustments(
            conn, DEFAULT_VALIDITY_ADJUSTMENT_PATH
        )
    if cache_only:
        log.info("Cache-only CLOB fill sync requested; skipping external CLOB/public fallback fetches.")
        return summary

    placed_after_utc = (
        (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        if lookback_hours is not None
        else None
    )
    submitted = _get_submitted_orders(
        conn,
        placed_after_utc=placed_after_utc,
    )
    if not submitted:
        log.info("No submitted polymarket_clob orders found -- nothing to sync.")
        return summary

    summary["checked"] = len(submitted)
    log.info("Found %d submitted polymarket_clob orders.", len(submitted))

    if maker_address is None:
        # CLOB TradeParams.maker_address is the proxy/funder recorded on the
        # order, not the EOA signer. Using the signer returns an empty trade
        # page for signature_type=1 accounts and incorrectly forces the lossy
        # public-activity fallback.
        maker_address = _discover_funder(conn)

    # --- Build auth CLOB client (optional) ---
    client = _build_clob_client()
    if require_authenticated and client is None:
        EXTERNAL_FETCH_ERRORS.append("authenticated_clob_client_unavailable")
        summary["errors"] += 1
        summary["external_fetch_errors"] = len(EXTERNAL_FETCH_ERRORS)
        summary["data_incomplete"] = True
        log.error(
            "Authenticated CLOB evidence is required; refusing public activity "
            "fallback and fill writes."
        )
        return summary

    # --- Bulk fetch trades (authenticated path) ---
    trades_by_order_id: dict[str, list[dict[str, Any]]] = {}
    if client is not None:
        log.info(
            "Fetching all CLOB trades for maker_address=%s...", maker_address
        )
        all_clob_trades = _fetch_trades_clob(client, maker_address)
        log.info("CLOB returned %d trade records.", len(all_clob_trades))
        if not all_clob_trades:
            log.info(
                "Authenticated bulk trades returned no maker rows; retaining "
                "authenticated per-order status. Public activity remains fee "
                "evidence only."
            )
        trades_by_order_id = _index_authenticated_trades_by_order_id(all_clob_trades)

    # Public activity is also the exact cash evidence for transaction hashes in
    # immediate matched responses, even when authenticated CLOB data is present.
    funder = _discover_funder(conn)
    log.info("Fetching activity for funder=%s (fee evidence and fallback)...", funder)
    activity_trades = _fetch_activity_public(funder, record_errors=client is None)
    activity_by_tx = _index_public_activity_by_tx(activity_trades)
    log.info("Activity API returned %d TRADE events.", len(activity_trades))

    # --- Unauthenticated public fallback: activity API (preferred) or trades API ---
    public_trades: list[dict[str, Any]] = []
    if client is None:
        public_trades = activity_trades
        if not public_trades:
            log.info(
                "Falling back to trades endpoint for maker_address=%s...", maker_address
            )
            public_trades = _fetch_trades_public(maker_address)
        log.info(
            "Total unauthenticated trade records: %d", len(public_trades)
        )

    if EXTERNAL_FETCH_ERRORS:
        summary["external_fetch_errors"] = len(EXTERNAL_FETCH_ERRORS)
        summary["data_incomplete"] = True
        log.error(
            "External CLOB fill source failed %d time(s); DB live fill status is incomplete.",
            len(EXTERNAL_FETCH_ERRORS),
        )
    used_public_trade_keys: set[str] = set()

    # --- Process each submitted order ---
    for row in submitted:
        execution_id = row["execution_id"]
        clob_order_id = row["clob_order_id"] or row["order_id"] or ""
        try:
            row_shares = float(row["shares"] or 0)
        except (TypeError, ValueError):
            row_shares = 0.0
        row_limit_price = _submitted_order_price_cap(row)

        if not clob_order_id:
            log.warning(
                "execution_id=%s... has no CLOB orderID in exchange_response; skipping.",
                execution_id[:12],
            )
            summary["errors"] += 1
            continue

        log.debug(
            "Checking execution_id=%s... order_id=%s...",
            execution_id[:12], clob_order_id[:12],
        )

        fill_id = _make_fill_id(execution_id, clob_order_id)
        immediate_fill = _extract_immediate_place_fill(row)
        if immediate_fill is not None:
            fee_details: dict[str, Any] | None = None
            if client is not None:
                authenticated_order = _fetch_order_status_clob(client, clob_order_id)
                if authenticated_order is not None:
                    authenticated = _parse_clob_order_response(
                        authenticated_order, expected_shares=row_shares
                    )
                    if authenticated["fees_usd"] > 0:
                        fee_details = {
                            "fees_usd": authenticated["fees_usd"],
                            "fee_source": "authenticated_taker_fee",
                            "fee_rate": authenticated["fee_rate"],
                            "transaction_hash": None,
                            "fee_metadata": authenticated["fee_metadata"],
                        }
            if fee_details is None:
                fee_details = _exact_activity_fee_for_fill(
                    transaction_hashes=immediate_fill["transaction_hashes"],
                    activity_by_tx=activity_by_tx,
                    condition_id=str(row["condition_id"] or ""),
                    token_id=str(row["token_id"] or ""),
                    order_side=str(row["order_side"] or ""),
                    expected_shares=immediate_fill["filled_shares"],
                )
            if fee_details is None:
                fee_details = {
                    key: immediate_fill[key]
                    for key in (
                        "fees_usd",
                        "fee_source",
                        "fee_rate",
                        "transaction_hash",
                        "fee_metadata",
                    )
                }
            inserted = _insert_order_fill_top_up(
                conn,
                base_fill_id=fill_id,
                execution_id=execution_id,
                order_id=clob_order_id,
                target_shares=immediate_fill["filled_shares"],
                target_price=immediate_fill["filled_price"],
                fees_usd=fee_details["fees_usd"],
                filled_at_utc=immediate_fill["filled_at_utc"],
                dry_run=dry_run,
                fee_source=fee_details["fee_source"],
                fee_rate=fee_details["fee_rate"],
                fee_metadata=fee_details["fee_metadata"],
                transaction_hash=fee_details["transaction_hash"],
            )
            if inserted:
                log.info(
                    "Recorded immediate matched fill for execution_id=%s... "
                    "shares=%.6f price=%.4f fee=%.5f source=%s",
                    execution_id[:12],
                    immediate_fill["filled_shares"],
                    immediate_fill["filled_price"],
                    fee_details["fees_usd"],
                    fee_details["fee_source"],
                )
                summary["filled"] += 1
            else:
                summary["still_open"] += 1

        # ------------------------------------------------------------------
        # Authenticated path: per-order status + bulk trades
        # ------------------------------------------------------------------
        if client is not None:
            order_data = _fetch_order_status_clob(client, clob_order_id)
            matched_trades = trades_by_order_id.get(clob_order_id, [])

            if order_data is not None:
                parsed = _parse_clob_order_response(
                    order_data, expected_shares=row_shares
                )
                clob_status = parsed["clob_status"]

                if clob_status == "MATCHED":
                    filled_shares = parsed["size_matched"]
                    filled_price = parsed["price"]
                    fees_usd = parsed["fees_usd"]
                    filled_at = parsed["filled_at"]
                    trade_agg = (
                        _extract_trade_fill(
                            matched_trades,
                            row_shares=row_shares,
                            row_limit_price=row_limit_price,
                        )
                        if matched_trades
                        else None
                    )

                    # Supplement from trade records if size_matched is zero
                    if filled_shares <= 0 and trade_agg:
                        filled_shares = trade_agg["size_matched"]
                        filled_price = trade_agg["price"] or filled_price
                        filled_at = trade_agg["filled_at"] or filled_at
                    if fees_usd <= 0 and trade_agg and trade_agg["fees_usd"] > 0:
                        fees_usd = trade_agg["fees_usd"]

                    if filled_shares <= 0:
                        filled_shares = row_shares
                    if filled_price <= 0:
                        filled_price = row_limit_price
                    filled_shares, filled_price = _cap_reported_fill_to_order(
                        execution_id=execution_id,
                        order_id=clob_order_id,
                        reported_shares=filled_shares,
                        reported_price=filled_price,
                        row_shares=row_shares,
                        row_limit_price=row_limit_price,
                    )
                    fee_details = _resolve_fee_details(
                        authenticated_fee_usd=fees_usd,
                        authenticated_fee_rate=parsed["fee_rate"],
                        authenticated_metadata=parsed["fee_metadata"],
                        transaction_hashes=(trade_agg or {}).get("transaction_hashes", []),
                        activity_by_tx=activity_by_tx,
                        condition_id=str(row["condition_id"] or ""),
                        token_id=str(row["token_id"] or ""),
                        order_side=str(row["order_side"] or ""),
                        shares=filled_shares,
                        price=filled_price,
                        maker_only=_maker_only(row),
                    )

                    inserted = _insert_order_fill_top_up(
                        conn,
                        base_fill_id=fill_id,
                        execution_id=execution_id,
                        order_id=clob_order_id,
                        target_shares=filled_shares,
                        target_price=filled_price,
                        fees_usd=fee_details["fees_usd"],
                        filled_at_utc=filled_at,
                        dry_run=dry_run,
                        fee_source=fee_details["fee_source"],
                        fee_rate=fee_details["fee_rate"],
                        fee_metadata=fee_details["fee_metadata"],
                        transaction_hash=fee_details["transaction_hash"],
                    )
                    if inserted:
                        log.info(
                            "Recorded fill for execution_id=%s... shares=%.6f price=%.4f",
                            execution_id[:12], filled_shares, filled_price,
                        )
                        summary["filled"] += 1
                    else:
                        summary["still_open"] += 1
                    # NOTE: orders is append-only (BEFORE UPDATE trigger prevents
                    # changing orders.status). The fill record in fills table is
                    # the authoritative source of truth for fill status.

                elif clob_status in ("CANCELLED", "CANCELED", "EXPIRED"):
                    log.info(
                        "Order execution_id=%s... is %s on CLOB (no fill).",
                        execution_id[:12], clob_status,
                    )
                    summary["cancelled"] += 1

                else:
                    # LIVE/OPEN orders may still be partially filled.
                    filled_shares = parsed["size_matched"]
                    if filled_shares > 0:
                        filled_price = parsed["price"] or row_limit_price
                        filled_shares, filled_price = _cap_reported_fill_to_order(
                            execution_id=execution_id,
                            order_id=clob_order_id,
                            reported_shares=filled_shares,
                            reported_price=filled_price,
                            row_shares=row_shares,
                            row_limit_price=row_limit_price,
                        )
                        fee_details = _resolve_fee_details(
                            authenticated_fee_usd=parsed["fees_usd"],
                            authenticated_fee_rate=parsed["fee_rate"],
                            authenticated_metadata=parsed["fee_metadata"],
                            transaction_hashes=[],
                            activity_by_tx=activity_by_tx,
                            condition_id=str(row["condition_id"] or ""),
                            token_id=str(row["token_id"] or ""),
                            order_side=str(row["order_side"] or ""),
                            shares=filled_shares,
                            price=filled_price,
                            maker_only=_maker_only(row),
                        )
                        inserted = _insert_order_fill_top_up(
                            conn,
                            base_fill_id=fill_id,
                            execution_id=execution_id,
                            order_id=clob_order_id,
                            target_shares=filled_shares,
                            target_price=filled_price,
                            fees_usd=fee_details["fees_usd"],
                            filled_at_utc=parsed["filled_at"],
                            dry_run=dry_run,
                            fee_source=fee_details["fee_source"],
                            fee_rate=fee_details["fee_rate"],
                            fee_metadata=fee_details["fee_metadata"],
                            transaction_hash=fee_details["transaction_hash"],
                        )
                        if inserted:
                            log.info(
                                "Recorded partial fill for execution_id=%s... "
                                "status=%s shares=%.6f price=%.4f",
                                execution_id[:12], clob_status or "UNKNOWN",
                                filled_shares, filled_price,
                            )
                            summary["filled"] += 1
                        else:
                            summary["still_open"] += 1
                    else:
                        log.debug(
                            "Order execution_id=%s... status=%s -- still open.",
                            execution_id[:12], clob_status or "UNKNOWN",
                        )
                        summary["still_open"] += 1

            elif matched_trades:
                # Order endpoint returned nothing but we have matching trade records
                agg = _extract_trade_fill(
                    matched_trades,
                    row_shares=row_shares,
                    row_limit_price=row_limit_price,
                )
                filled_shares, filled_price = _cap_reported_fill_to_order(
                    execution_id=execution_id,
                    order_id=clob_order_id,
                    reported_shares=agg["size_matched"],
                    reported_price=agg["price"],
                    row_shares=row_shares,
                    row_limit_price=row_limit_price,
                )
                fee_details = _resolve_fee_details(
                    authenticated_fee_usd=agg["fees_usd"],
                    authenticated_fee_rate=None,
                    authenticated_metadata={"source": "authenticated_trade"},
                    transaction_hashes=agg["transaction_hashes"],
                    activity_by_tx=activity_by_tx,
                    condition_id=str(row["condition_id"] or ""),
                    token_id=str(row["token_id"] or ""),
                    order_side=str(row["order_side"] or ""),
                    shares=filled_shares,
                    price=filled_price,
                    maker_only=_maker_only(row),
                )
                inserted = _insert_order_fill_top_up(
                    conn,
                    base_fill_id=fill_id,
                    execution_id=execution_id,
                    order_id=clob_order_id,
                    target_shares=filled_shares,
                    target_price=filled_price,
                    fees_usd=fee_details["fees_usd"],
                    filled_at_utc=agg["filled_at"],
                    dry_run=dry_run,
                    fee_source=fee_details["fee_source"],
                    fee_rate=fee_details["fee_rate"],
                    fee_metadata=fee_details["fee_metadata"],
                    transaction_hash=fee_details["transaction_hash"],
                )
                if inserted:
                    log.info(
                        "Recorded fill (from trades) execution_id=%s... "
                        "shares=%.6f avg_price=%.4f",
                        execution_id[:12], agg["size_matched"], agg["price"],
                    )
                    summary["filled"] += 1
                else:
                    summary["still_open"] += 1

            else:
                log.debug(
                    "No CLOB data for execution_id=%s... -- still open.",
                    execution_id[:12],
                )
                summary["still_open"] += 1

        # ------------------------------------------------------------------
        # Unauthenticated fallback path
        # ------------------------------------------------------------------
        else:
            cid = row["condition_id"] or ""
            token_id = str(row["token_id"] or "")
            order_side = row["order_side"] or ""
            placed_ts = 0
            placed_raw = row["placed_at_utc"]
            if placed_raw:
                try:
                    placed_ts = int(
                        datetime.fromisoformat(
                            str(placed_raw).replace("Z", "+00:00")
                        ).timestamp()
                    )
                except (ValueError, TypeError):
                    pass

            matching_public = _select_public_partial_fills(
                public_trades,
                used_public_trade_keys=used_public_trade_keys,
                condition_id=cid,
                token_id=token_id,
                order_side=order_side,
                limit_price=row_limit_price,
                row_shares=row_shares,
                placed_ts=placed_ts,
            )

            if matching_public:
                inserted_count = 0
                for trade_idx, trade in enumerate(matching_public):
                    public_key = _public_trade_key(trade)
                    # Preserve backward compatibility with the old cache: the
                    # first public fill for an order uses the legacy per-order
                    # fill_id, while later partial fills get trade-grain ids.
                    public_fill_id = (
                        fill_id
                        if trade_idx == 0
                        else _make_public_trade_fill_id(execution_id, public_key)
                    )
                    if _already_have_fill(conn, public_fill_id):
                        used_public_trade_keys.add(public_key)
                        continue
                    try:
                        filled_shares = float(trade.get("size") or row_shares)
                    except (TypeError, ValueError):
                        filled_shares = row_shares
                    try:
                        filled_price = float(trade.get("price") or row_limit_price)
                    except (TypeError, ValueError):
                        filled_price = row_limit_price
                    filled_at = _ts_to_iso(trade.get("timestamp"))
                    fee_details = _public_buy_fee_details(trade) or _fallback_fee_details(
                        shares=filled_shares,
                        price=filled_price,
                        maker_only=_maker_only(row),
                    )
                    existing = _existing_fill_totals(
                        conn,
                        execution_id=execution_id,
                        order_id=clob_order_id,
                    )
                    max_cost = row_shares * row_limit_price if row_shares > 0 and row_limit_price > 0 else 0.0
                    would_shares = existing["shares"] + filled_shares
                    would_cost = existing["cost"] + filled_shares * filled_price
                    if row_shares > 0 and would_shares > row_shares + 1e-6:
                        log.warning(
                            "Skipping public fallback fill over share cap for execution_id=%s... "
                            "existing=%.6f candidate=%.6f cap=%.6f",
                            execution_id[:12],
                            existing["shares"],
                            filled_shares,
                            row_shares,
                        )
                        used_public_trade_keys.add(public_key)
                        continue
                    if max_cost > 0 and would_cost > max_cost + 0.02:
                        log.warning(
                            "Skipping public fallback fill over cost cap for execution_id=%s... "
                            "existing_cost=%.6f candidate_cost=%.6f cap=%.6f",
                            execution_id[:12],
                            existing["cost"],
                            filled_shares * filled_price,
                            max_cost,
                        )
                        used_public_trade_keys.add(public_key)
                        continue

                    if _insert_fill(
                        conn,
                        fill_id=public_fill_id,
                        execution_id=execution_id,
                        order_id=clob_order_id,
                        filled_shares=filled_shares,
                        filled_price=filled_price,
                        fees_usd=fee_details["fees_usd"],
                        filled_at_utc=filled_at,
                        dry_run=dry_run,
                        fee_source=fee_details["fee_source"],
                        fee_rate=fee_details["fee_rate"],
                        fee_metadata=fee_details["fee_metadata"],
                        transaction_hash=fee_details["transaction_hash"],
                    ):
                        inserted_count += 1
                    used_public_trade_keys.add(public_key)
                    log.info(
                        "Recorded partial fill (public fallback) execution_id=%s... "
                        "shares=%.6f price=%.4f",
                        execution_id[:12], filled_shares, filled_price,
                    )
                summary["filled"] += inserted_count
                if inserted_count == 0:
                    summary["still_open"] += 1
            else:
                log.debug(
                    "No public trade match for execution_id=%s... -- still open.",
                    execution_id[:12],
                )
                summary["still_open"] += 1

    if EXTERNAL_FETCH_ERRORS:
        summary["external_fetch_errors"] = len(EXTERNAL_FETCH_ERRORS)
        summary["data_incomplete"] = True

    log.info("Sync complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Sync CLOB fill status for submitted Polymarket orders."
    )
    parser.add_argument(
        "--db-path",
        default="runtime/weather.db",
        help="Path to SQLite weather.db (default: runtime/weather.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be done without writing to the DB.",
    )
    parser.add_argument(
        "--maker-address",
        default=None,
        help="Override the maker/signer address (default: DEFAULT_MAKER_ADDRESS).",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Only replay the persistent CLOB fill cache into the DB; skip external CLOB/public fallback fetches.",
    )
    parser.add_argument(
        "--require-authenticated",
        action="store_true",
        help=(
            "Fail closed when authenticated CLOB setup is unavailable; "
            "never allocate account-level public activity to orders."
        ),
    )
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=None,
        help="Only query exchange/public fallback for orders placed within this many hours.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load .env if present (search from db-path directory up to project root)
    env_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(args.db_path)), "..", ".env"),
        os.path.join(os.path.dirname(os.path.abspath(args.db_path)), ".env"),
        os.path.abspath(".env"),
    ]
    for env_path in env_candidates:
        env_path = os.path.normpath(env_path)
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
            log.info("Loaded env from %s", env_path)
            break

    db_conn = sqlite3.connect(args.db_path)
    db_conn.row_factory = sqlite3.Row

    try:
        result = sync_clob_fills(
            db_conn,
            dry_run=args.dry_run,
            maker_address=args.maker_address,
            cache_only=args.cache_only,
            require_authenticated=args.require_authenticated,
            lookback_hours=args.lookback_hours,
        )
        print(json.dumps(result, indent=2))
        if result.get("data_incomplete") and not args.dry_run:
            sys.exit(1)
    finally:
        db_conn.close()
