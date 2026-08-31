from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from scripts.ops.weather_market_proxy import production_market_proxy_url

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


def exact_share_taker_intent(*, best_ask: float, desired_shares: float) -> dict[str, Any]:
    """Build a marketable GTC BUY whose signed quantity is an exact share cap.

    FOK/FAK BUY quantities are interpreted as USDC spend by the CLOB.  A
    marketable GTC created with ``create_order`` preserves share-denominated
    sizing; if the top ask moves, any remainder can rest without exceeding the
    signed share cap.
    """
    if not (0 < best_ask < 1):
        raise ValueError("best_ask must be between zero and one")
    if desired_shares <= 0:
        raise ValueError("desired_shares must be positive")
    return {
        "limit_price": round(float(best_ask), 6),
        "size": float(desired_shares),
        "desired_shares": float(desired_shares),
        "submitted_notional_usd": round(float(desired_shares) * float(best_ask), 6),
        "limit_price_policy": "fresh_best_ask_marketable_gtc_v1",
        "order_type": "GTC",
        "post_only": False,
        "expiration": 0,
        "share_cap_enforcement": "marketable_gtc_signed_size_v1",
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
            if row.get("share_cap_enforcement") in {
                "resting_post_only_signed_size_v1",
                "marketable_gtc_signed_size_v1",
            }:
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


def is_post_only_crossing_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid post-only order" in message or "order crosses book" in message


def _build_live_limit_place_fn(
    proxy_url: str,
    *,
    order_type_name: str,
    post_only: bool,
    order_mode: str,
):
    # All live placement factories share one named stable route. The caller's
    # proxy remains the book-read route; execution routing is owned here.
    proxy_url = production_market_proxy_url(route_key="stable")
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderPayload, OrderType
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
        OrderPayload = None  # type: ignore[assignment]
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

    def cancel(order_id: str) -> dict[str, Any]:
        """Cancel a share-denominated GTC remainder with order-state evidence."""
        result: dict[str, Any] = {}
        try:
            result["order_before_cancel"] = client.get_order(order_id)
        except Exception as exc:  # noqa: BLE001
            result["order_before_cancel_error"] = f"{type(exc).__name__}: {exc}"
        before_state = _order_state(result.get("order_before_cancel"))
        original = _state_float(before_state, "original_size", "originalSize", "size")
        matched = _state_float(
            before_state,
            "size_matched",
            "sizeMatched",
            "matched_size",
            "matchedSize",
        )
        if (
            original is not None
            and original > 0
            and matched is not None
            and matched >= original - SHARE_CAP_TOLERANCE
        ):
            result["terminal_before_cancel"] = "fully_matched"
            return result
        if clob_v2:
            result["cancel"] = client.cancel_order(OrderPayload(orderID=order_id))
        else:
            result["cancel"] = client.cancel(order_id)
        try:
            result["order_after_cancel"] = client.get_order(order_id)
        except Exception as exc:  # noqa: BLE001
            result["order_after_cancel_error"] = f"{type(exc).__name__}: {exc}"
        return result

    place.cancel = cancel  # type: ignore[attr-defined]

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


def build_live_taker_gtc_place_fn(proxy_url: str):
    """Build an exact-share marketable BUY path with no post-only flag."""
    return _build_live_limit_place_fn(
        proxy_url,
        order_type_name="GTC",
        post_only=False,
        order_mode="marketable_gtc_buy_shares",
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


def response_is_accepted_taker(response: dict[str, Any]) -> bool:
    place = response.get("place") if isinstance(response, dict) else None
    return bool(
        isinstance(place, dict)
        and place.get("success") is True
        and str(place.get("status") or "").lower() in {"live", "matched"}
        and extract_order_id(place)
        and response.get("post_only") is False
        and str(response.get("order_type") or "").upper() == "GTC"
    )


def _order_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get("order")
    return nested if isinstance(nested, dict) else value


def _state_float(state: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = safe_float(state.get(key))
        if value is not None:
            return value
    return None


def canceled_remainder_fill_amounts(
    cancel_response: dict[str, Any] | None,
    *,
    fallback_price: float,
) -> tuple[float | None, float | None]:
    """Read matched shares from authenticated state captured around cancel."""
    if not isinstance(cancel_response, dict):
        return None, None
    for key in ("order_after_cancel", "order_before_cancel"):
        state = _order_state(cancel_response.get(key))
        matched = _state_float(
            state,
            "size_matched",
            "sizeMatched",
            "matched_size",
            "matchedSize",
        )
        if matched is None:
            continue
        price = _state_float(state, "price") or float(fallback_price)
        return matched, round(matched * price, 6)
    return None, None


def cancel_response_confirmed(
    response: dict[str, Any] | None,
    order_id: str,
) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("terminal_before_cancel") == "fully_matched":
        return True
    payload = response.get("cancel") if isinstance(response.get("cancel"), dict) else response
    if not isinstance(payload, dict):
        return False
    canceled = payload.get("canceled")
    if isinstance(canceled, list) and order_id in {str(value) for value in canceled}:
        return True
    return str(payload.get("cancelled") or payload.get("canceled") or "") == order_id or bool(
        payload.get("cancelled") is True or payload.get("canceled") is True
    )


def submit_marketable_gtc(
    order_row: dict[str, Any],
    *,
    place: Callable[[dict[str, Any]], dict[str, Any]],
    cancel: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Submit one exact-share marketable GTC and cancel any resting remainder."""
    working = dict(order_row)
    attempt = {
        "attempt": 1,
        "attempt_ts_utc": iso(),
        "best_ask": working.get("best_ask"),
        "ask_size": working.get("ask_size"),
        "limit_price": working.get("limit_price"),
    }
    try:
        response = place(working)
        if not response_is_accepted_taker(response):
            raise RuntimeError(f"marketable GTC response not accepted: {json.dumps(response.get('place'), sort_keys=True)}")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        attempt.update({"status": "submit_failed", "error": error})
        return {
            "order_row": working,
            "attempts": [attempt],
            "exchange_response": None,
            "live_submit_status": "submit_failed",
            "live_order_posted": False,
            "exchange_order_status": None,
            "actual_fill_shares": None,
            "actual_fill_cost_usd": None,
            "share_cap_check": share_cap_check(working),
            "error": error,
        }

    actual_shares, actual_cost = matched_fill_amounts(response)
    place_status = str((response.get("place") or {}).get("status") or "").lower()
    immediate_cancel_response: dict[str, Any] | None = None
    immediate_cancel_error = ""
    immediate_cancel_confirmed = False
    if place_status == "live":
        cancel_fn = cancel or getattr(place, "cancel", None)
        order_id = str(response.get("order_id") or "")
        if cancel_fn is None or not order_id:
            immediate_cancel_error = "marketable_gtc_live_remainder_missing_cancel_path"
        else:
            try:
                immediate_cancel_response = cancel_fn(order_id)
                immediate_cancel_confirmed = cancel_response_confirmed(
                    immediate_cancel_response,
                    order_id,
                )
                canceled_shares, canceled_cost = canceled_remainder_fill_amounts(
                    immediate_cancel_response,
                    fallback_price=float(working.get("limit_price") or 0.0),
                )
                if canceled_shares is not None:
                    actual_shares, actual_cost = canceled_shares, canceled_cost
                if not immediate_cancel_confirmed:
                    immediate_cancel_error = (
                        "marketable_gtc_live_remainder_cancel_not_confirmed"
                    )
            except Exception as exc:  # noqa: BLE001
                immediate_cancel_error = (
                    "marketable_gtc_live_remainder_cancel_failed:"
                    f"{type(exc).__name__}:{exc}"
                )
    checked = {**working, "exchange_response": response, "actual_fill_shares": actual_shares}
    cap_check = share_cap_check(checked)
    fully_matched_after_live = bool(
        place_status == "live"
        and actual_shares is not None
        and actual_shares
        >= float(working.get("desired_shares") or working.get("size") or 0.0)
        - SHARE_CAP_TOLERANCE
    )
    lifecycle_status = (
        "matched"
        if fully_matched_after_live
        else ("canceled" if immediate_cancel_confirmed else place_status)
    )
    submit_status = (
        "share_cap_violation" if cap_check["share_cap_violation"] else "submitted"
    )
    if immediate_cancel_error and submit_status == "submitted":
        submit_status = "submitted_residual_cancel_failed"
    attempt.update(
        {
            "status": submit_status,
            "order_id": response.get("order_id"),
            "actual_fill_shares": actual_shares,
            "actual_fill_cost_usd": actual_cost,
            "share_cap_check": cap_check,
        }
    )
    return {
        "order_row": working,
        "attempts": [attempt],
        "exchange_response": response,
        "live_submit_status": submit_status,
        "live_order_posted": True,
        "exchange_order_status": lifecycle_status,
        "actual_fill_shares": actual_shares,
        "actual_fill_cost_usd": actual_cost,
        "share_cap_check": cap_check,
        "immediate_cancel_response": immediate_cancel_response,
        "immediate_cancel_confirmed": immediate_cancel_confirmed,
        "error": (
            "actual_fill_shares_exceeded_desired_or_market_cap"
            if cap_check["share_cap_violation"]
            else immediate_cancel_error
        ),
    }


def submit_post_only_gtd(
    order_row: dict[str, Any],
    *,
    place: Callable[[dict[str, Any]], dict[str, Any]],
    fetch_book_fn: Callable[..., dict[str, Any]] | None = None,
    market_proxy: str = "",
    book_timeout_sec: float = 5.0,
    max_no_ask: float = 1.0,
    immediate_reprices: int = 0,
) -> dict[str, Any]:
    """Submit an exact-share maker intent and immediately reprice crossing rejects."""
    working = dict(order_row)
    response: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = []
    total_attempts = 1 + max(0, int(immediate_reprices))
    last_error = ""

    for attempt_number in range(1, total_attempts + 1):
        attempt = {
            "attempt": attempt_number,
            "attempt_ts_utc": iso(),
            "best_ask": working.get("best_ask"),
            "ask_size": working.get("ask_size"),
            "limit_price": working.get("limit_price"),
        }
        try:
            response = place(working)
            if not response_is_live_post_only(response):
                raise RuntimeError(f"post-only GTD response not live: {json.dumps(response.get('place'), sort_keys=True)}")
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            crossing = is_post_only_crossing_error(exc)
            attempt.update({"status": "submit_failed", "error": last_error, "post_only_crossing": crossing})
            attempts.append(attempt)
            if not (crossing and attempt_number < total_attempts and fetch_book_fn is not None):
                break

            try:
                book = fetch_book_fn(
                    str(working["token_id"]),
                    proxy=market_proxy,
                    timeout_sec=float(book_timeout_sec),
                    top_n=5,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = f"immediate_reprice_book_failed:{type(exc).__name__}:{exc}"
                attempts.append(
                    {
                        "attempt": attempt_number + 1,
                        "attempt_ts_utc": iso(),
                        "status": "reprice_blocked",
                        "blockers": ["fresh_book_fetch_failed"],
                        "error": last_error,
                    }
                )
                break
            summary = book.get("summary") or {}
            best_ask = safe_float(summary.get("best_ask"))
            ask_size = safe_float(summary.get("ask_size"))
            tick_size = safe_float(summary.get("tick_size")) or safe_float(working.get("tick_size"))
            desired_shares = safe_float(working.get("desired_shares")) or safe_float(working.get("size"))
            blockers: list[str] = []
            if book.get("status") != "ok":
                blockers.append("fresh_book_not_ok")
            if best_ask is None:
                blockers.append("missing_best_ask")
            elif best_ask > float(max_no_ask):
                blockers.append("ask_above_max")
            if tick_size is None:
                blockers.append("missing_tick_size")
            if desired_shares is None or desired_shares <= 0:
                blockers.append("invalid_desired_shares")
            elif ask_size is None or ask_size < desired_shares:
                blockers.append("insufficient_top_ask_size")
            if blockers:
                last_error = "immediate_reprice_blocked:" + ",".join(blockers)
                attempts.append(
                    {
                        "attempt": attempt_number + 1,
                        "attempt_ts_utc": iso(),
                        "status": "reprice_blocked",
                        "best_ask": best_ask,
                        "ask_size": ask_size,
                        "blockers": blockers,
                    }
                )
                break
            try:
                maker_intent = exact_share_maker_intent(
                    best_ask=float(best_ask),
                    tick_size=float(tick_size),
                    desired_shares=float(desired_shares),
                    now=datetime.now(timezone.utc),
                    effective_lifetime_sec=float(working.get("effective_lifetime_sec") or 45.0),
                )
            except ValueError as exc:
                last_error = f"immediate_reprice_invalid:{exc}"
                attempts.append(
                    {
                        "attempt": attempt_number + 1,
                        "attempt_ts_utc": iso(),
                        "status": "reprice_blocked",
                        "best_ask": best_ask,
                        "ask_size": ask_size,
                        "blockers": ["exact_share_maker_intent_invalid"],
                    }
                )
                break
            working.update(
                {
                    "best_ask": best_ask,
                    "ask_size": ask_size,
                    "tick_size": tick_size,
                    "planned_notional_usd": round(float(desired_shares) * float(best_ask), 6),
                    "fresh_book_status": book.get("status"),
                    "fresh_book_error": book.get("error", ""),
                    "fresh_book_http_status": book.get("http_status"),
                    "fresh_book_proxy_used": book.get("proxy_used", ""),
                    **maker_intent,
                }
            )
            continue

        attempts.append({**attempt, "status": "submitted", "order_id": response.get("order_id")})
        return {
            "order_row": working,
            "attempts": attempts,
            "exchange_response": response,
            "live_submit_status": "submitted",
            "live_order_posted": True,
            "exchange_order_status": "live",
            "actual_fill_shares": None,
            "actual_fill_cost_usd": None,
            "share_cap_check": share_cap_check(working),
            "error": "",
        }

    actual_shares, actual_cost = matched_fill_amounts(response)
    checked_row = {**working, "exchange_response": response, "actual_fill_shares": actual_shares}
    cap_check = share_cap_check(checked_row)
    return {
        "order_row": working,
        "attempts": attempts,
        "exchange_response": response,
        "live_submit_status": "share_cap_violation" if cap_check["share_cap_violation"] else "submit_failed",
        "actual_fill_shares": actual_shares,
        "actual_fill_cost_usd": actual_cost,
        "share_cap_check": cap_check,
        "error": last_error,
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
