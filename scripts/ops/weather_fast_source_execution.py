from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

PM_CLOB_URL = "https://clob.polymarket.com"
SHARE_CAP_TOLERANCE = 1e-5
# CLOB V2 rejected GTD expirations below roughly 180 seconds on 2026-07-15,
# despite the public documentation still describing a 60-second threshold.
GTD_SECURITY_THRESHOLD_SEC = 180


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def exact_share_maker_intent(
    *,
    best_ask: float,
    tick_size: float,
    desired_shares: float,
    now: datetime,
    effective_lifetime_sec: float,
) -> dict[str, Any]:
    """Build a non-marketable GTD intent whose signed quantity is the hard cap."""
    if not (0 < tick_size < best_ask < 1):
        raise ValueError("best_ask/tick_size cannot form a resting BUY price")
    if desired_shares <= 0:
        raise ValueError("desired_shares must be positive")
    ticks_below_ask = math.floor((best_ask + 1e-12) / tick_size) - 1
    maker_price = round(ticks_below_ask * tick_size, 6)
    if maker_price <= 0 or maker_price >= best_ask - 1e-12:
        raise ValueError("maker price must be at least one tick below best ask")
    lifetime = max(1, int(math.ceil(effective_lifetime_sec)))
    return {
        "limit_price": maker_price,
        "size": float(desired_shares),
        "desired_shares": float(desired_shares),
        "submitted_notional_usd": round(float(desired_shares) * maker_price, 6),
        "limit_price_policy": "one_tick_below_best_ask_post_only_v1",
        "order_type": "GTD",
        "post_only": True,
        "expiration": int(now.timestamp()) + GTD_SECURITY_THRESHOLD_SEC + lifetime,
        "gtd_security_threshold_sec": GTD_SECURITY_THRESHOLD_SEC,
        "effective_lifetime_sec": lifetime,
        "share_cap_enforcement": "resting_post_only_signed_size_v1",
    }


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
    if not isinstance(place, dict) or str(place.get("status") or "").lower() != "matched":
        return None, None
    return safe_float(place.get("takingAmount")), safe_float(place.get("makingAmount"))


def share_cap_check(row: dict[str, Any], *, tolerance: float = SHARE_CAP_TOLERANCE) -> dict[str, Any]:
    desired = safe_float(row.get("desired_shares"))
    if desired is None:
        desired = safe_float(row.get("planned_shares"))
    if desired is None:
        desired = safe_float(row.get("size"))
    market_cap = safe_float(row.get("max_shares_per_market"))
    cap_candidates = [value for value in (desired, market_cap) if value is not None and value >= 0]
    cap = min(cap_candidates) if cap_candidates else None
    actual = safe_float(row.get("actual_fill_shares"))
    if actual is None:
        actual, _cost = matched_fill_amounts(row.get("exchange_response"))
    violation = bool(actual is not None and cap is not None and actual > cap + tolerance)
    return {
        "desired_shares": desired,
        "max_shares_per_market": market_cap,
        "effective_share_cap": cap,
        "actual_fill_shares": actual,
        "share_cap_tolerance": tolerance,
        "share_cap_excess_shares": max(0.0, actual - cap) if actual is not None and cap is not None else None,
        "share_cap_violation": violation,
    }


def audit_order_share_caps(path: Path, *, tolerance: float = SHARE_CAP_TOLERANCE) -> dict[str, Any]:
    checked = 0
    anomalies: list[dict[str, Any]] = []
    enforced_anomalies: list[dict[str, Any]] = []
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            check = share_cap_check(row, tolerance=tolerance)
            if check["actual_fill_shares"] is None:
                continue
            checked += 1
            if not check["share_cap_violation"]:
                continue
            anomaly = {
                "line_number": line_number,
                "target_date": row.get("target_date"),
                "city": row.get("city"),
                "bracket": row.get("t_minus_1_no_bracket") or row.get("t_minus_1_no_bracket_c"),
                "token_id": row.get("token_id"),
                "order_id": row.get("order_id") or extract_order_id(row.get("exchange_response")),
                "limit_price_policy": row.get("limit_price_policy"),
                "share_cap_enforcement": row.get("share_cap_enforcement"),
                **check,
            }
            anomalies.append(anomaly)
            if row.get("share_cap_enforcement") == "resting_post_only_signed_size_v1":
                enforced_anomalies.append(anomaly)
    return {
        "orders_with_actual_fill_checked": checked,
        "share_cap_anomaly_count": len(anomalies),
        "share_cap_anomalies": anomalies,
        "post_fix_share_cap_anomaly_count": len(enforced_anomalies),
        "post_fix_share_cap_anomalies": enforced_anomalies,
        "pause_required": bool(anomalies),
        "post_fix_pause_required": bool(enforced_anomalies),
        "tolerance_shares": tolerance,
    }


def resolve_share_cap_pause(
    state: dict[str, Any],
    audit: dict[str, Any],
    *,
    historical_acknowledged: bool,
) -> tuple[bool, str]:
    reason = str(state.get("share_cap_pause_reason") or "")
    stored_pause = bool(state.get("share_cap_paused"))
    if historical_acknowledged and reason.startswith("historical_actual_fill_exceeded"):
        stored_pause = False
        reason = ""
    historical_pause = bool(audit["share_cap_anomaly_count"]) and not historical_acknowledged
    paused = stored_pause or historical_pause or bool(audit["post_fix_pause_required"])
    if audit["post_fix_pause_required"]:
        reason = "post_fix_actual_fill_exceeded_desired_or_market_cap"
    elif historical_pause:
        reason = "historical_actual_fill_exceeded_desired_or_market_cap_requires_acknowledgement"
    return paused, reason


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


def _build_live_limit_place_fn(
    proxy_url: str,
    *,
    order_type_name: str,
    post_only: bool,
    order_mode: str,
):
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
        order_type = getattr(OrderType, order_type_name)
        signed_order = client.create_order(
            order_args_cls(
                token_id=str(row["token_id"]),
                price=float(row["limit_price"]),
                size=float(row["size"]),
                side="BUY",
                expiration=int(row.get("expiration") or 0),
            )
        )
        response = (
            client.post_order(signed_order, order_type=order_type, post_only=post_only)
            if clob_v2
            else client.post_order(signed_order, orderType=order_type, post_only=post_only)
        )
        return {
            "place": response,
            "order_id": extract_order_id(response),
            "clob_client": "py_clob_client_v2" if clob_v2 else "py_clob_client",
            "order_type": order_type_name,
            "order_mode": order_mode,
            "post_only": post_only,
            "signature_type": signature_type,
            "funder": funder,
            "signer": signer_addr,
            "clob_proxy_enabled": bool(proxy_url),
        }

    return place


def build_live_fok_limit_place_fn(proxy_url: str):
    """Legacy immediate BUY path.

    FOK/FAK BUY orders are USDC-spend orders at the exchange even when signed
    through ``create_order``.  Keep this factory for callers whose risk cap is
    denominated in dollars; it must not be used for a hard share cap.
    """
    return _build_live_limit_place_fn(
        proxy_url,
        order_type_name="FOK",
        post_only=False,
        order_mode="fok_buy_usdc_spend",
    )


def build_live_post_only_gtd_place_fn(proxy_url: str):
    """Build a share-denominated maker order that can never cross on entry."""
    return _build_live_limit_place_fn(
        proxy_url,
        order_type_name="GTD",
        post_only=True,
        order_mode="post_only_gtd_buy_shares",
    )


def response_is_live_post_only(response: dict[str, Any]) -> bool:
    place = response.get("place") if isinstance(response, dict) else None
    return bool(
        isinstance(place, dict)
        and place.get("success") is True
        and str(place.get("status") or "").lower() == "live"
        and extract_order_id(place)
        and response.get("post_only") is True
        and str(response.get("order_type") or "").upper() in {"GTC", "GTD"}
    )


def submit_post_only_gtd(order_row: dict[str, Any], *, place: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Submit one exact-share maker intent, failing closed if it crosses."""
    working = dict(order_row)
    response: dict[str, Any] | None = None
    try:
        response = place(working)
        if not response_is_live_post_only(response):
            raise RuntimeError(f"post-only GTD response not live: {json.dumps(response.get('place'), sort_keys=True)}")
    except Exception as exc:  # noqa: BLE001
        actual_shares, actual_cost = matched_fill_amounts(response)
        checked_row = {
            **working,
            "exchange_response": response,
            "actual_fill_shares": actual_shares,
        }
        cap_check = share_cap_check(checked_row)
        return {
            "order_row": working,
            "exchange_response": response,
            "live_submit_status": "share_cap_violation" if cap_check["share_cap_violation"] else "submit_failed",
            "actual_fill_shares": actual_shares,
            "actual_fill_cost_usd": actual_cost,
            "share_cap_check": cap_check,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "order_row": working,
        "exchange_response": response,
        "live_submit_status": "submitted",
        "live_order_posted": True,
        "exchange_order_status": "live",
        "actual_fill_shares": None,
        "actual_fill_cost_usd": None,
        "share_cap_check": share_cap_check(working),
        "error": "",
    }


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
        cap_check = share_cap_check(
            {
                **working,
                "exchange_response": response,
                "actual_fill_shares": actual_shares,
            }
        )
        attempt.update(
            {
                "status": "share_cap_violation" if cap_check["share_cap_violation"] else "submitted",
                "order_id": response.get("order_id"),
                "actual_fill_shares": actual_shares,
                "actual_fill_cost_usd": actual_cost,
                "share_cap_check": cap_check,
            }
        )
        attempts.append(attempt)
        if cap_check["share_cap_violation"]:
            return {
                "order_row": working,
                "attempts": attempts,
                "exchange_response": response,
                "live_submit_status": "share_cap_violation",
                "actual_fill_shares": actual_shares,
                "actual_fill_cost_usd": actual_cost,
                "share_cap_check": cap_check,
                "error": "actual_fill_shares_exceeded_desired_or_market_cap",
            }
        return {
            "order_row": working,
            "attempts": attempts,
            "exchange_response": response,
            "live_submit_status": "submitted",
            "actual_fill_shares": actual_shares,
            "actual_fill_cost_usd": actual_cost,
            "share_cap_check": cap_check,
            "error": "",
        }
    return {
        "order_row": working,
        "attempts": attempts,
        "exchange_response": None,
        "live_submit_status": "submit_failed",
        "actual_fill_shares": None,
        "actual_fill_cost_usd": None,
        "share_cap_check": share_cap_check(working),
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
        if (
            row.get("target_date") != target_date
            or str(row.get("token_id") or "") != token_id
            or row.get("live_submit_status") not in {"submitted", "posted"}
        ):
            continue
        if row.get("live_submit_status") == "posted":
            total += float(row.get("desired_shares") or row.get("size") or 0.0)
            continue
        actual = safe_float(row.get("actual_fill_shares"))
        if actual is None:
            actual, _cost = matched_fill_amounts(row.get("exchange_response"))
        total += actual if actual is not None else float(row.get("size") or 0.0)
    return round(total, 6)
