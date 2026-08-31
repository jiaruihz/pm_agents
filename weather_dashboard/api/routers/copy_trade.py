"""Copy-trade wallet research endpoints."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

from weather_dashboard.api.capital_efficiency import build_capital_efficiency_report


router = APIRouter(prefix="/copy-trade", tags=["copy-trade"])

RESEARCH_DB_PATH = os.environ.get("COPY_TRADE_RESEARCH_DB_PATH", "runtime/db/research.db")
DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(RESEARCH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _json_loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _get_data_api(path: str, params: dict[str, Any], timeout_sec: float = 25.0) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            f"{DATA_API_BASE}{path}",
            params={k: v for k, v in params.items() if v is not None and str(v) != ""},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-dashboard/1.0"},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _compact_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": row.get("conditionId") or row.get("condition_id"),
        "token_id": row.get("asset") or row.get("tokenId") or row.get("token_id"),
        "title": row.get("title") or row.get("question"),
        "slug": row.get("slug"),
        "outcome": row.get("outcome"),
        "size": row.get("size"),
        "avg_price": row.get("avgPrice") or row.get("avg_price"),
        "cur_price": row.get("curPrice") or row.get("cur_price"),
        "initial_value": row.get("initialValue"),
        "current_value": row.get("currentValue"),
        "cash_pnl": row.get("cashPnl") or row.get("realizedPnl"),
        "percent_pnl": row.get("percentPnl"),
        "raw": row,
    }


def _compact_closed_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": row.get("conditionId") or row.get("condition_id"),
        "title": row.get("title") or row.get("question"),
        "slug": row.get("slug"),
        "outcome": row.get("outcome"),
        "size": row.get("size"),
        "avg_price": row.get("avgPrice") or row.get("avg_price"),
        "realized_pnl": row.get("realizedPnl") or row.get("cashPnl") or row.get("pnl"),
        "cur_price": row.get("curPrice") or row.get("cur_price"),
        "raw": row,
    }


def _get_public_profile(wallet: str, timeout_sec: float = 10.0) -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/public-profile",
            params={"address": wallet},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-capital-efficiency/1.0"},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _get_positions_strict(
    wallet: str,
    timeout_sec: float = 25.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page_limit = 500
    max_offset = 10_000
    positions: list[dict[str, Any]] = []
    pages_fetched = 0
    truncated = False
    for offset in range(0, max_offset + 1, page_limit):
        try:
            resp = requests.get(
                f"{DATA_API_BASE}/positions",
                params={
                    "user": wallet,
                    "sizeThreshold": 0,
                    "limit": page_limit,
                    "offset": offset,
                    "sortBy": "CURRENT",
                    "sortDirection": "DESC",
                },
                headers={"Accept": "application/json", "User-Agent": "pm-agent-capital-efficiency/1.0"},
                timeout=timeout_sec,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Polymarket positions API unavailable: {exc}") from exc
        if not isinstance(payload, list):
            raise HTTPException(status_code=502, detail="Polymarket positions API returned an invalid payload")
        page = [item for item in payload if isinstance(item, dict)]
        positions.extend(page)
        pages_fetched += 1
        if len(payload) < page_limit:
            break
        if offset == max_offset:
            truncated = True

    return positions, {
        "pages_fetched": pages_fetched,
        "truncated": truncated,
        "max_supported_positions": max_offset + page_limit,
    }


def _get_clob_books(
    token_ids: list[str],
    timeout_sec: float = 25.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    books: list[dict[str, Any]] = []
    errors: list[str] = []
    for start in range(0, len(token_ids), 100):
        batch = token_ids[start : start + 100]
        try:
            resp = requests.post(
                f"{CLOB_API_BASE}/books",
                json=[{"token_id": token_id} for token_id in batch],
                headers={"Accept": "application/json", "User-Agent": "pm-agent-capital-efficiency/1.0"},
                timeout=timeout_sec,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            errors.append(f"batch_{start // 100 + 1}:{type(exc).__name__}")
            continue
        if isinstance(payload, list):
            books.extend(item for item in payload if isinstance(item, dict))
        else:
            errors.append(f"batch_{start // 100 + 1}:invalid_payload")
    return books, errors


def _positive_size(row: dict[str, Any]) -> bool:
    try:
        size = float(row.get("size") or 0)
    except (TypeError, ValueError):
        return False
    return size > 0


def _latest_reviews(conn: sqlite3.Connection, scan_mode: Optional[str] = None) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if scan_mode:
        where = "WHERE r.metrics_json LIKE ?"
        params.append(f'%"scan_mode": "{scan_mode}"%')

    rows = conn.execute(
        f"""
        SELECT
            r.review_id,
            r.wallet_address,
            r.reviewed_at,
            r.score,
            r.verdict,
            r.metrics_json,
            r.reasons_json,
            r.summary,
            w.display_name,
            w.status,
            w.tags_json,
            w.traits_json
        FROM copy_trade_wallet_reviews r
        LEFT JOIN copy_trade_wallets w ON w.wallet_address = r.wallet_address
        {where}
        ORDER BY r.reviewed_at DESC
        """,
        params,
    ).fetchall()

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        wallet = row["wallet_address"]
        if wallet in latest:
            continue
        metrics = _json_loads(row["metrics_json"], {})
        reasons = _json_loads(row["reasons_json"], [])
        latest[wallet] = {
            "wallet_address": wallet,
            "display_name": row["display_name"] or "",
            "status": row["status"] or "",
            "reviewed_at": row["reviewed_at"],
            "score": row["score"],
            "verdict": row["verdict"],
            "summary": row["summary"],
            "reasons": reasons,
            "metrics": metrics,
            "tags": _json_loads(row["tags_json"], []),
        }
    return list(latest.values())


def _wallet_row(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item["metrics"]
    return {
        "wallet_address": item["wallet_address"],
        "display_name": item["display_name"],
        "status": item["status"],
        "reviewed_at": item["reviewed_at"],
        "score": item["score"],
        "verdict": item["verdict"],
        "summary": item["summary"],
        "reasons": item["reasons"],
        "closed_positions_count": metrics.get("closed_positions_count", 0),
        "open_positions_count": metrics.get("open_positions_count", 0),
        "net_pnl": metrics.get("net_pnl", 0.0),
        "non_election_pnl": metrics.get("non_election_pnl", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "sports_ratio": metrics.get("sports_ratio", 0.0),
        "arbitrage_like_ratio": metrics.get("arbitrage_like_ratio", 0.0),
        "election_dependency": metrics.get("election_dependency", 0.0),
        "single_event_dependency": metrics.get("single_event_dependency", 0.0),
        "positive_single_event_dependency": metrics.get("positive_single_event_dependency", 0.0),
        "avg_entry_price": metrics.get("avg_entry_price", 0.0),
        "market_diversity_ratio": metrics.get("market_diversity_ratio", 0.0),
        "history_truncated": bool(metrics.get("history_truncated")),
        "scan_mode": metrics.get("scan_mode", ""),
        "topic_counts": metrics.get("topic_counts", {}),
    }


@router.get("/capital-efficiency")
def get_capital_efficiency(
    wallet_address: str = Query(..., description="Polymarket profile/proxy wallet address"),
    annual_hurdle_rate: float = Query(0.10, ge=0.0, le=10.0),
    available_cash_usd: Optional[float] = Query(None, ge=0.0),
    reserved_cash_usd: Optional[float] = Query(None, ge=0.0),
):
    wallet = wallet_address.strip().lower()
    if not WALLET_RE.fullmatch(wallet):
        raise HTTPException(status_code=422, detail="wallet_address must be a 0x-prefixed 40-hex address")

    positions, positions_meta = _get_positions_strict(wallet)
    token_ids = sorted(
        {
            str(row["asset"])
            for row in positions
            if row.get("asset") is not None
            and not bool(row.get("redeemable"))
            and _positive_size(row)
        }
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        books_future = pool.submit(_get_clob_books, token_ids)
        profile_future = pool.submit(_get_public_profile, wallet)
        books, book_errors = books_future.result()
        profile = profile_future.result()

    report = build_capital_efficiency_report(
        wallet_address=wallet,
        positions=positions,
        books=books,
        observed_at=datetime.now(timezone.utc),
        annual_hurdle_rate=annual_hurdle_rate,
        available_cash_usd=available_cash_usd,
        reserved_cash_usd=reserved_cash_usd,
        profile=profile,
    )
    report["sources"] = {
        "positions": f"{DATA_API_BASE}/positions",
        "order_books": f"{CLOB_API_BASE}/books",
        "profile": f"{GAMMA_API_BASE}/public-profile",
        "books_requested": len(token_ids),
        "books_received": len(books),
        "book_fetch_errors": book_errors,
        "position_pages_fetched": positions_meta["pages_fetched"],
    }
    report["data_quality"]["positions_truncated"] = positions_meta["truncated"]
    report["data_quality"]["order_book_fetch_complete"] = (
        not book_errors and len(books) == len(token_ids)
    )
    return report


@router.get("/summary")
def get_copy_trade_summary():
    with _connect() as conn:
        wallets_count = conn.execute("SELECT COUNT(*) AS n FROM copy_trade_wallets").fetchone()["n"]
        discoveries_count = conn.execute("SELECT COUNT(*) AS n FROM copy_trade_wallet_discoveries").fetchone()["n"]
        reviews_count = conn.execute("SELECT COUNT(*) AS n FROM copy_trade_wallet_reviews").fetchone()["n"]
        full_scan_items = [_wallet_row(item) for item in _latest_reviews(conn, scan_mode="full_scan")]
        all_latest = [_wallet_row(item) for item in _latest_reviews(conn)]

    verdict_counts = Counter(item["verdict"] for item in all_latest)
    full_scan_counts = Counter(item["verdict"] for item in full_scan_items)
    paper_candidates = [item for item in full_scan_items if item["verdict"] == "paper_candidate"]
    core_candidates = [
        item
        for item in paper_candidates
        if item["profit_factor"] >= 1.8
        and item["sports_ratio"] < 0.2
        and item["election_dependency"] < 0.25
        and item["positive_single_event_dependency"] < 0.35
    ]

    return {
        "db_path": RESEARCH_DB_PATH,
        "wallets_count": wallets_count,
        "discoveries_count": discoveries_count,
        "reviews_count": reviews_count,
        "latest_review_counts": dict(verdict_counts),
        "full_scan_counts": dict(full_scan_counts),
        "paper_candidates": len(paper_candidates),
        "core_candidates": len(core_candidates),
    }


@router.get("/wallets")
def list_copy_trade_wallets(
    verdict: Optional[str] = Query(None),
    scan_mode: str = Query("full_scan"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with _connect() as conn:
        items = [_wallet_row(item) for item in _latest_reviews(conn, scan_mode=scan_mode or None)]

    if verdict:
        items = [item for item in items if item["verdict"] == verdict]

    def sort_key(item: dict[str, Any]) -> tuple[float, float]:
        non_election_pnl = float(item.get("non_election_pnl") or 0.0)
        return (float(item.get("score") or 0.0), non_election_pnl)

    items.sort(key=sort_key, reverse=True)
    total = len(items)
    return {"total": total, "items": items[offset : offset + limit]}


@router.get("/wallets/{wallet_address}")
def get_copy_trade_wallet(wallet_address: str):
    wallet = str(wallet_address).lower()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.review_id,
                r.wallet_address,
                r.reviewed_at,
                r.score,
                r.verdict,
                r.metrics_json,
                r.reasons_json,
                r.summary,
                w.display_name,
                w.status
            FROM copy_trade_wallet_reviews r
            LEFT JOIN copy_trade_wallets w ON w.wallet_address = r.wallet_address
            WHERE lower(r.wallet_address) = ?
            ORDER BY r.reviewed_at DESC
            LIMIT 20
            """,
            (wallet,),
        ).fetchall()
        discoveries = conn.execute(
            """
            SELECT method, source_ref, strength, tags_json, discovered_at
            FROM copy_trade_wallet_discoveries
            WHERE lower(wallet_address) = ?
            ORDER BY discovered_at DESC
            LIMIT 100
            """,
            (wallet,),
        ).fetchall()

    open_positions = [_compact_position(row) for row in _get_data_api("/positions", {"user": wallet, "limit": 200})]
    closed_positions = [
        _compact_closed_position(row)
        for row in _get_data_api("/closed-positions", {"user": wallet, "limit": 50, "offset": 0})
    ]

    return {
        "wallet_address": wallet,
        "reviews": [
            {
                "reviewed_at": row["reviewed_at"],
                "score": row["score"],
                "verdict": row["verdict"],
                "summary": row["summary"],
                "reasons": _json_loads(row["reasons_json"], []),
                "metrics": _json_loads(row["metrics_json"], {}),
                "display_name": row["display_name"] or "",
                "status": row["status"] or "",
            }
            for row in rows
        ],
        "discoveries": [
            {
                "method": row["method"],
                "source_ref": row["source_ref"],
                "strength": row["strength"],
                "tags": _json_loads(row["tags_json"], []),
                "discovered_at": row["discovered_at"],
            }
            for row in discoveries
        ],
        "open_positions": open_positions,
        "recent_closed_positions": closed_positions,
    }
