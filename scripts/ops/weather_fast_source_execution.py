from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

PM_CLOB_URL = "https://clob.polymarket.com"


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def extract_order_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("order_id", "orderID", "id"):
        if payload.get(key):
            return str(payload[key])
    data = payload.get("data")
    return extract_order_id(data) if isinstance(data, dict) else ""


def matched_fill_amounts(response: dict[str, Any] | None) -> tuple[float | None, float | None]:
    place = (response or {}).get("place") if isinstance(response, dict) else None
    if not isinstance(place, dict):
        return None, None
    return safe_float(place.get("takingAmount")), safe_float(place.get("makingAmount"))


def response_is_matched(response: dict[str, Any]) -> bool:
    place = response.get("place") if isinstance(response, dict) else None
    return bool(
        isinstance(place, dict)
        and place.get("success") is True
        and str(place.get("status") or "").lower() == "matched"
        and extract_order_id(place)
    )


def is_definitive_fok_unfilled_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "couldn't be fully filled" in message and "fok" in message


def build_live_fok_limit_place_fn(proxy_url: str):
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType
        from py_clob_client_v2.constants import POLYGON
        import py_clob_client_v2.http_helpers.helpers as clob_http_helpers

        order_args_cls = OrderArgsV2
        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.constants import POLYGON
        import py_clob_client.http_helpers.helpers as clob_http_helpers

        order_args_cls = OrderArgs
        clob_v2 = False

    if proxy_url:
        clob_http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url, timeout=5.0)  # noqa: SLF001
    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or PM_CLOB_URL
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    funder = os.getenv("PM_ADDRESS", "").strip()
    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0
    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(host, chain_id=chain_id, key=private_key, creds=creds, signature_type=signature_type, funder=funder or None)
    if creds is None:
        client.set_api_creds(client.derive_api_key() if clob_v2 else client.create_or_derive_api_creds())

    def place(row: dict[str, Any]) -> dict[str, Any]:
        signed_order = client.create_order(
            order_args_cls(
                token_id=str(row["token_id"]),
                price=float(row["limit_price"]),
                size=float(row["size"]),
                side="BUY",
            )
        )
        response = (
            client.post_order(signed_order, order_type=OrderType.FOK)
            if clob_v2
            else client.post_order(signed_order, orderType=OrderType.FOK)
        )
        return {
            "place": response,
            "order_id": extract_order_id(response),
            "clob_client": "py_clob_client_v2" if clob_v2 else "py_clob_client",
            "order_type": "FOK",
            "order_mode": "limit_buy_shares",
            "signature_type": signature_type,
            "funder": funder,
            "signer": signer_addr,
            "clob_proxy_enabled": bool(proxy_url),
        }

    return place


def submit_fok_with_immediate_retries(
    order_row: dict[str, Any],
    *,
    place: Callable[[dict[str, Any]], dict[str, Any]],
    fetch_book_fn: Callable[..., dict[str, Any]],
    market_proxy: str,
    book_timeout_sec: float,
    max_no_ask: float,
    immediate_retries: int,
) -> dict[str, Any]:
    working = dict(order_row)
    attempts: list[dict[str, Any]] = []
    total_attempts = 1 + max(0, int(immediate_retries))
    last_error = ""

    for attempt_number in range(1, total_attempts + 1):
        if attempt_number > 1:
            book = fetch_book_fn(str(working["token_id"]), proxy=market_proxy, timeout_sec=float(book_timeout_sec), top_n=5)
            summary = book.get("summary") or {}
            best_ask = safe_float(summary.get("best_ask"))
            ask_size = safe_float(summary.get("ask_size"))
            blockers: list[str] = []
            if book.get("status") != "ok":
                blockers.append("fresh_book_not_ok")
            if best_ask is None:
                blockers.append("missing_best_ask")
            elif best_ask > float(max_no_ask):
                blockers.append("ask_above_max")
            if ask_size is None or ask_size < float(working["desired_shares"]):
                blockers.append("insufficient_top_ask_size")
            if blockers:
                last_error = "immediate_retry_blocked:" + ",".join(blockers)
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "attempt_ts_utc": iso(),
                        "status": "retry_blocked",
                        "best_ask": best_ask,
                        "ask_size": ask_size,
                        "blockers": blockers,
                    }
                )
                break
            working.update(
                {
                    "best_ask": best_ask,
                    "ask_size": ask_size,
                    "limit_price": best_ask,
                    "size": float(working["desired_shares"]),
                    "planned_notional_usd": round(float(working["desired_shares"]) * best_ask, 6),
                    "submitted_notional_usd": round(float(working["desired_shares"]) * best_ask, 6),
                    "fresh_book_status": book.get("status"),
                    "fresh_book_error": book.get("error", ""),
                    "fresh_book_http_status": book.get("http_status"),
                    "fresh_book_proxy_used": book.get("proxy_used", ""),
                }
            )
        attempt = {
            "attempt": attempt_number,
            "attempt_ts_utc": iso(),
            "best_ask": working.get("best_ask"),
            "ask_size": working.get("ask_size"),
            "limit_price": working.get("limit_price"),
        }
        try:
            response = place(working)
            if not response_is_matched(response):
                raise RuntimeError(f"FOK response not matched: {json.dumps(response.get('place'), sort_keys=True)}")
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            definitive = is_definitive_fok_unfilled_error(exc)
            attempt.update({"status": "submit_failed", "error": last_error, "definitive_fok_unfilled": definitive})
            attempts.append(attempt)
            if definitive and attempt_number < total_attempts:
                continue
            break
        actual_shares, actual_cost = matched_fill_amounts(response)
        attempt.update({"status": "submitted", "order_id": response.get("order_id"), "actual_fill_shares": actual_shares, "actual_fill_cost_usd": actual_cost})
        attempts.append(attempt)
        return {
            "order_row": working,
            "attempts": attempts,
            "exchange_response": response,
            "live_submit_status": "submitted",
            "actual_fill_shares": actual_shares,
            "actual_fill_cost_usd": actual_cost,
            "error": "",
        }
    return {
        "order_row": working,
        "attempts": attempts,
        "exchange_response": None,
        "live_submit_status": "submit_failed",
        "actual_fill_shares": None,
        "actual_fill_cost_usd": None,
        "error": last_error,
    }


def spent_market_shares(path: Path, *, target_date: str, token_id: str) -> float:
    total = 0.0
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("target_date") != target_date or str(row.get("token_id") or "") != token_id or row.get("live_submit_status") != "submitted":
            continue
        actual = safe_float(row.get("actual_fill_shares"))
        if actual is None:
            actual, _cost = matched_fill_amounts(row.get("exchange_response"))
        total += actual if actual is not None else float(row.get("size") or 0.0)
    return round(total, 6)
