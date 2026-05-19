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
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOB_HOST_DEFAULT = "https://clob.polymarket.com"
DATA_API_HOST = "https://data-api.polymarket.com"
REQUEST_TIMEOUT = 10  # seconds
PAGE_SIZE = 500

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


def _already_have_fill(conn: sqlite3.Connection, fill_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fills WHERE fill_id = ?", (fill_id,)
    ).fetchone()
    return row is not None


def _ts_to_iso(ts_ms: Any) -> str | None:
    """Convert a millisecond timestamp (int or str) to an ISO-8601 UTC string."""
    if ts_ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ts_ms)


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _get_submitted_orders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all polymarket_clob orders with status='submitted'."""
    return conn.execute(
        """
        SELECT
            o.execution_id,
            o.order_id,
            json_extract(o.exchange_response, '$.place.orderID') AS clob_order_id,
            o.shares,
            o.limit_price,
            o.placed_at_utc,
            o.order_side,
            sig.condition_id
        FROM orders o
        JOIN plans   p   ON o.plan_id   = p.plan_id
        JOIN signals sig ON p.signal_id = sig.signal_id
        WHERE o.venue  = 'polymarket_clob'
          AND o.status = 'submitted'
        ORDER BY o.placed_at_utc ASC
        """
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
) -> None:
    """INSERT OR IGNORE a fill row (idempotent)."""
    now = _now_utc()
    if dry_run:
        log.info(
            "[dry-run] Would INSERT fill fill_id=%s... execution_id=%s... "
            "shares=%.6f price=%.4f fees=%.6f",
            fill_id[:12], execution_id[:12], filled_shares, filled_price, fees_usd,
        )
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO fills
            (fill_id, execution_id, order_id, filled_shares, filled_price,
             fees_usd, status, filled_at_utc, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?)
        """,
        (
            fill_id,
            execution_id,
            order_id,
            filled_shares,
            filled_price,
            fees_usd,
            filled_at_utc or now,
            now,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Authenticated CLOB client
# ---------------------------------------------------------------------------

def _build_clob_client() -> Any | None:
    """
    Build a py_clob_client_v2 ClobClient using env-var credentials.
    Returns None if the private key is absent (graceful fallback).
    """
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.constants import POLYGON
    except ImportError:
        try:
            from py_clob_client.client import ClobClient  # type: ignore[assignment]
            from py_clob_client.clob_types import ApiCreds  # type: ignore[assignment]
            from py_clob_client.constants import POLYGON  # type: ignore[assignment]
        except ImportError:
            log.warning("Neither py_clob_client_v2 nor py_clob_client installed.")
            return None

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
            client.set_api_creds(client.derive_api_key())
        except Exception as exc:
            log.warning("derive_api_key failed: %s -- trying create_or_derive_api_creds", exc)
            try:
                client.set_api_creds(client.create_or_derive_api_creds())
            except Exception as exc2:
                log.warning(
                    "create_or_derive_api_creds failed: %s -- no auth available", exc2
                )
                return None
    return client


def _fetch_order_status_clob(client: Any, order_id: str) -> dict[str, Any] | None:
    """Call GET /data/order/{order_id}. Returns dict or None on error."""
    try:
        result = client.get_order(order_id)
        return result if isinstance(result, dict) else None
    except Exception as exc:
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
        log.warning("CLOB get_trades failed: %s", exc)
        return []


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
        log.warning("Public trades fetch failed: %s", exc)
    return all_trades


def _fetch_activity_public(funder: str) -> list[dict[str, Any]]:
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


def _parse_clob_order_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a /data/order/{id} response.

    Typical fields:
        status        "LIVE" | "MATCHED" | "CANCELLED" | "EXPIRED"
        sizeMatched   filled qty in smallest token units (/ 1e6 = shares)
        price         limit price (str)
        takerFee      fee in smallest USDC units (/ 1e6 = USD)
        updatedAt     timestamp ms (str)
    """
    status = (data.get("status") or "").upper()

    size_matched_raw = data.get("sizeMatched") or data.get("size_matched") or "0"
    try:
        size_matched = float(size_matched_raw) / 1_000_000
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

    return {
        "clob_status": status,
        "size_matched": size_matched,
        "price": price,
        "fees_usd": fees_usd,
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

    for t in trades:
        sz_raw = t.get("size") or t.get("matched_amount") or t.get("makerAmount") or "0"
        try:
            sz = float(sz_raw) / 1_000_000
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

        if fill_time is None:
            fill_time = _ts_to_iso(t.get("timestamp") or t.get("matchTime"))

    avg_price = total_cost / total_shares if total_shares > 0 else row_limit_price
    if total_shares <= 0:
        total_shares = row_shares
    return {
        "size_matched": total_shares,
        "price": avg_price,
        "fees_usd": total_fees,
        "filled_at": fill_time,
    }


def _side_matches_public(order_side: str, trade: dict[str, Any]) -> bool:
    """
    Heuristic match between our order_side and a public data-api trade record.
    Public trade fields: side='BUY'/'SELL', outcome='Yes'/'No'.
    """
    side = (trade.get("side") or "").upper()
    outcome = (trade.get("outcome") or "").lower()
    if order_side == "BUY_YES":
        return side == "BUY" and outcome in ("yes", "")
    if order_side == "BUY_NO":
        return side == "BUY" and outcome == "no"
    return False


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_clob_fills(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    maker_address: str | None = None,
) -> dict[str, Any]:
    """
    Sync fill status for all submitted polymarket_clob orders.

    Parameters
    ----------
    conn:           Open SQLite connection (row_factory = sqlite3.Row recommended).
    dry_run:        If True, log what would be done without writing to DB.
    maker_address:  Maker/signer address (defaults to DEFAULT_MAKER_ADDRESS).

    Returns
    -------
    dict with keys: checked, filled, cancelled, still_open, errors, dry_run
    """
    if maker_address is None:
        maker_address = DEFAULT_MAKER_ADDRESS

    summary: dict[str, Any] = {
        "checked": 0,
        "filled": 0,
        "cancelled": 0,
        "still_open": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    submitted = _get_submitted_orders(conn)
    if not submitted:
        log.info("No submitted polymarket_clob orders found -- nothing to sync.")
        return summary

    summary["checked"] = len(submitted)
    log.info("Found %d submitted polymarket_clob orders.", len(submitted))

    # --- Build auth CLOB client (optional) ---
    client = _build_clob_client()

    # --- Bulk fetch trades (authenticated path) ---
    trades_by_order_id: dict[str, list[dict[str, Any]]] = {}
    if client is not None:
        log.info(
            "Fetching all CLOB trades for maker_address=%s...", maker_address
        )
        all_clob_trades = _fetch_trades_clob(client, maker_address)
        log.info("CLOB returned %d trade records.", len(all_clob_trades))
        for t in all_clob_trades:
            oid = (
                t.get("maker_order_id")
                or t.get("makerOrderId")
                or t.get("order_id")
                or t.get("orderId")
                or ""
            )
            if oid:
                trades_by_order_id.setdefault(oid, []).append(t)

    # --- Unauthenticated public fallback: activity API (preferred) or trades API ---
    public_trades: list[dict[str, Any]] = []
    if client is None:
        # Prefer activity API with the funder address (on-chain events with conditionId).
        # The signer/maker address often shows 0 activity; the funder wallet does.
        funder = _discover_funder(conn)
        log.info("Fetching activity for funder=%s (preferred unauthenticated path)...", funder)
        public_trades = _fetch_activity_public(funder)
        log.info("Activity API returned %d TRADE events.", len(public_trades))
        if not public_trades:
            log.info(
                "Falling back to trades endpoint for maker_address=%s...", maker_address
            )
            public_trades = _fetch_trades_public(maker_address)
        log.info(
            "Total unauthenticated trade records: %d", len(public_trades)
        )

    # --- Process each submitted order ---
    for row in submitted:
        execution_id = row["execution_id"]
        clob_order_id = row["clob_order_id"] or row["order_id"] or ""
        try:
            row_shares = float(row["shares"] or 0)
        except (TypeError, ValueError):
            row_shares = 0.0
        try:
            row_limit_price = float(row["limit_price"] or 0)
        except (TypeError, ValueError):
            row_limit_price = 0.0

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
        if _already_have_fill(conn, fill_id):
            log.debug(
                "Fill already recorded for execution_id=%s...; skipping.",
                execution_id[:12],
            )
            summary["still_open"] += 1
            continue

        # ------------------------------------------------------------------
        # Authenticated path: per-order status + bulk trades
        # ------------------------------------------------------------------
        if client is not None:
            order_data = _fetch_order_status_clob(client, clob_order_id)
            matched_trades = trades_by_order_id.get(clob_order_id, [])

            if order_data is not None:
                parsed = _parse_clob_order_response(order_data)
                clob_status = parsed["clob_status"]

                if clob_status == "MATCHED":
                    filled_shares = parsed["size_matched"]
                    filled_price = parsed["price"]
                    fees_usd = parsed["fees_usd"]
                    filled_at = parsed["filled_at"]

                    # Supplement from trade records if size_matched is zero
                    if filled_shares <= 0 and matched_trades:
                        agg = _extract_trade_fill(
                            matched_trades,
                            row_shares=row_shares,
                            row_limit_price=row_limit_price,
                        )
                        filled_shares = agg["size_matched"]
                        filled_price = agg["price"] or filled_price
                        fees_usd = agg["fees_usd"]
                        filled_at = agg["filled_at"] or filled_at

                    if filled_shares <= 0:
                        filled_shares = row_shares
                    if filled_price <= 0:
                        filled_price = row_limit_price

                    _insert_fill(
                        conn,
                        fill_id=fill_id,
                        execution_id=execution_id,
                        order_id=clob_order_id,
                        filled_shares=filled_shares,
                        filled_price=filled_price,
                        fees_usd=fees_usd,
                        filled_at_utc=filled_at,
                        dry_run=dry_run,
                    )
                    log.info(
                        "Recorded fill for execution_id=%s... shares=%.6f price=%.4f",
                        execution_id[:12], filled_shares, filled_price,
                    )
                    summary["filled"] += 1
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
                    # LIVE, OPEN, or unknown
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
                _insert_fill(
                    conn,
                    fill_id=fill_id,
                    execution_id=execution_id,
                    order_id=clob_order_id,
                    filled_shares=agg["size_matched"],
                    filled_price=agg["price"],
                    fees_usd=agg["fees_usd"],
                    filled_at_utc=agg["filled_at"],
                    dry_run=dry_run,
                )
                log.info(
                    "Recorded fill (from trades) execution_id=%s... "
                    "shares=%.6f avg_price=%.4f",
                    execution_id[:12], agg["size_matched"], agg["price"],
                )
                summary["filled"] += 1

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

            matching_public = [
                t for t in public_trades
                if t.get("conditionId") == cid
                and _side_matches_public(order_side, t)
                and int(t.get("timestamp", 0)) >= placed_ts
            ]

            if matching_public:
                # Pick the earliest trade after order placement
                best = min(
                    matching_public, key=lambda t: int(t.get("timestamp", 0))
                )
                try:
                    filled_shares = float(best.get("size") or row_shares)
                except (TypeError, ValueError):
                    filled_shares = row_shares
                try:
                    filled_price = float(best.get("price") or row_limit_price)
                except (TypeError, ValueError):
                    filled_price = row_limit_price
                filled_at = _ts_to_iso(best.get("timestamp"))

                _insert_fill(
                    conn,
                    fill_id=fill_id,
                    execution_id=execution_id,
                    order_id=clob_order_id,
                    filled_shares=filled_shares,
                    filled_price=filled_price,
                    fees_usd=0.0,
                    filled_at_utc=filled_at,
                    dry_run=dry_run,
                )
                log.info(
                    "Recorded fill (public fallback) execution_id=%s... "
                    "shares=%.6f price=%.4f",
                    execution_id[:12], filled_shares, filled_price,
                )
                summary["filled"] += 1
            else:
                log.debug(
                    "No public trade match for execution_id=%s... -- still open.",
                    execution_id[:12],
                )
                summary["still_open"] += 1

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
        )
        print(json.dumps(result, indent=2))
    finally:
        db_conn.close()
