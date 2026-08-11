#!/usr/bin/env python3
"""Authenticated py-clob-client-v2 transport for the shared weather order runtime.

This module owns client construction and raw CLOB normalization.  Strategy
quote, sizing, lifecycle and risk decisions remain outside the transport.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from scripts.ops.weather_market_proxy import production_market_proxy_url


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
MARKET_PROXY_ENV_KEYS = (
    "WEATHER_EXECUTOR_MARKET_PROXY",
    "WEATHER_DATA_FEED_MARKET_PROXY",
    "WEATHER_PREDICT_MARKET_PROXY",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "dict"):
        result = value.dict()
        return dict(result) if isinstance(result, Mapping) else {}
    fields = {}
    for name in (
        "asset_id",
        "asks",
        "bids",
        "hash",
        "last_trade_price",
        "market",
        "min_order_size",
        "neg_risk",
        "price",
        "size",
        "tick_size",
        "timestamp",
    ):
        item = getattr(value, name, None)
        if item is not None:
            fields[name] = item
    return fields


def _levels(values: Any, *, reverse: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for value in values or ():
        item = _mapping(value)
        price = str(item.get("price") or "").strip()
        size = str(item.get("size") or "").strip()
        if price and size:
            rows.append({"price": price, "size": size})
    return sorted(rows, key=lambda row: Decimal(row["price"]), reverse=reverse)


def _clean_proxy(value: str) -> str:
    result = str(value or "").strip().strip('"').strip("'")
    return "" if result.lower() in {"", "direct", "none", "off", "0"} else result


def configure_market_proxy_env(explicit_proxy: str | None) -> dict[str, Any]:
    source = ""
    proxy = _clean_proxy(explicit_proxy or "")
    if explicit_proxy is not None:
        source = "--market-proxy"
    else:
        for key in MARKET_PROXY_ENV_KEYS:
            if os.getenv(key, "").strip():
                source = key
                proxy = _clean_proxy(os.getenv(key, ""))
                break
    if source:
        for key in PROXY_ENV_KEYS:
            if proxy:
                os.environ[key] = proxy
            else:
                os.environ.pop(key, None)
    return {
        "mode": "explicit_market_proxy" if proxy else "explicit_direct",
        "source": source,
        "proxy": proxy,
        "http_proxy_set": bool(proxy),
    }


def resolve_live_market_proxy(explicit_proxy: str | None) -> str:
    """Resolve the route owned by the irreversible execution handoff."""

    if explicit_proxy is not None:
        return _clean_proxy(explicit_proxy)
    return production_market_proxy_url(route_key="stable")


def _order_id(payload: Mapping[str, Any]) -> str:
    for key in ("order_id", "orderID", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "").upper().strip()


class LivePolymarketTransport:
    """Small injected transport used by ``PolymarketVenueAdapter``."""

    def __init__(
        self,
        *,
        client: Any,
        order_args_cls: Any,
        order_payload_cls: Any,
        order_type_cls: Any,
        metadata_by_order_id: Mapping[str, Mapping[str, Any]] | None = None,
        client_version: str = "py_clob_client_v2",
    ) -> None:
        self.client = client
        self.order_args_cls = order_args_cls
        self.order_payload_cls = order_payload_cls
        self.order_type_cls = order_type_cls
        self.client_version = client_version
        self.metadata_by_order_id = {
            str(key): dict(value) for key, value in dict(metadata_by_order_id or {}).items()
        }
        self.metadata_by_client_id: dict[str, dict[str, Any]] = {}
        self.order_id_by_client_id: dict[str, str] = {}
        self.unknown_submit_by_identity: dict[str, dict[str, Any]] = {}

    def fetch_capabilities(self) -> Mapping[str, Any]:
        return {
            "venue": "polymarket_clob",
            "protocol_version": "clob-v2",
            "client_version": self.client_version,
            "collateral_asset": "USDC",
            "supported_order_types": ("GTC", "GTD", "FAK", "FOK"),
            "post_only_order_types": ("GTC", "GTD"),
            "price_precision": 4,
            "size_precision": 4,
            "amount_precision_by_order_type": {
                "GTC": 8,
                "GTD": 8,
                "FAK": 8,
                "FOK": 8,
            },
            "gtd_security_threshold_sec": 60,
            "capabilities_fetched_at_utc": _utc_now(),
            "fee_schedule_ref": "polymarket-token-fee-bps-v1",
        }

    def fetch_fee_schedule(self) -> Mapping[str, Any]:
        return {
            "venue": "polymarket_clob",
            "fee_schedule_ref": "polymarket-token-fee-bps-v1",
            "fee_schedule_fetched_at_utc": _utc_now(),
            "fee_formula_id": "polymarket_dynamic_token_fee_bps_v1",
            "taker_fee_parameters": {"rate": "0"},
            "maker_fee_parameters": {"rate": "0", "rebate_rate": "0"},
            "maker_rebate_program": None,
        }

    def fetch_fee_schedule_for_token(self, token_id: str) -> Mapping[str, Any]:
        fee_bps = int(self.client.get_fee_rate_bps(str(token_id)))
        return {
            **dict(self.fetch_fee_schedule()),
            "taker_fee_parameters": {
                "rate": format(Decimal(fee_bps) / Decimal("10000"), "f"),
                "fee_rate_bps": fee_bps,
            },
            "maker_fee_parameters": {
                "rate": "0",
                "rebate_rate": "0",
                "fee_rate_bps": fee_bps,
            },
        }

    def _book(self, token_id: str) -> dict[str, Any]:
        return _mapping(self.client.get_order_book(str(token_id)))

    def fetch_instrument(self, token_id: str) -> Mapping[str, Any]:
        book = self._book(token_id)
        tick = str(self.client.get_tick_size(str(token_id)))
        minimum = str(book.get("min_order_size") or "5")
        return {
            "tick_size": tick,
            "minimum_order_shares": minimum,
            "tick_size_source": "py_clob_client_v2.get_tick_size",
            "instrument_version": f"{tick}:{minimum}",
        }

    def fetch_market_book(self, token_id: str) -> Mapping[str, Any]:
        book = self._book(token_id)
        return {
            "status": "ok",
            "fetched_at_utc": _utc_now(),
            "venue_timestamp_utc": None,
            "sequence": str(book.get("hash") or book.get("timestamp") or ""),
            "tick_size": str(book.get("tick_size") or self.client.get_tick_size(str(token_id))),
            "minimum_order_shares": str(book.get("min_order_size") or "5"),
            "tick_size_source": "py_clob_client_v2.order_book",
            "bids": _levels(book.get("bids"), reverse=True),
            "asks": _levels(book.get("asks"), reverse=False),
        }

    def create_order(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        expiration = 0
        expiration_utc = str(payload.get("expiration_utc") or "").strip()
        if expiration_utc:
            expiration = int(
                datetime.fromisoformat(expiration_utc.replace("Z", "+00:00")).timestamp()
            )
        signed = self.client.create_order(
            self.order_args_cls(
                token_id=str(payload["token_id"]),
                price=float(payload["price"]),
                size=float(payload["shares"]),
                side=str(payload["side"]),
                expiration=expiration,
            )
        )
        client_order_id = str(payload.get("client_order_id") or "")
        self.metadata_by_client_id[client_order_id] = dict(payload)
        expected = str(
            getattr(signed, "order_id", "")
            or getattr(signed, "hash", "")
            or ""
        )
        return {
            "signed_order": signed,
            "client_order_id": client_order_id,
            "expected_venue_order_id": expected or None,
        }

    def post_order(
        self,
        signed_order: Mapping[str, Any],
        *,
        order_type: str,
        post_only: bool,
    ) -> Mapping[str, Any]:
        client_order_id = str(signed_order.get("client_order_id") or "")
        metadata = dict(self.metadata_by_client_id.get(client_order_id, {}))
        try:
            raw = self.client.post_order(
                signed_order["signed_order"],
                order_type=getattr(
                    self.order_type_cls,
                    str(order_type),
                    str(order_type),
                ),
                post_only=bool(post_only),
            )
        except Exception:
            identity_key = str(metadata.get("identity_key") or "")
            if identity_key:
                self.unknown_submit_by_identity[identity_key] = {
                    "client_order_id": client_order_id,
                    "order_id": signed_order.get("expected_venue_order_id"),
                }
            raise
        response = _mapping(raw)
        venue_order_id = _order_id(response)
        if venue_order_id:
            self.order_id_by_client_id[client_order_id] = venue_order_id
            self.metadata_by_order_id[venue_order_id] = metadata
        return response

    def cancel_order(self, order_id: str) -> Mapping[str, Any]:
        response = _mapping(
            self.client.cancel_order(self.order_payload_cls(orderID=str(order_id)))
        )
        canceled = response.get("canceled") or response.get("cancelled") or ()
        not_canceled = response.get("not_canceled") or {}
        if str(order_id) in {str(value) for value in canceled}:
            status = "cancelled"
        elif str(order_id) in {str(value) for value in not_canceled}:
            status = "unknown"
        else:
            status = str(response.get("status") or "unknown")
        return {"status": status, **response}

    def fetch_order(
        self,
        order_id: str | None,
        client_order_id: str,
    ) -> Mapping[str, Any] | None:
        resolved_order_id = str(
            order_id or self.order_id_by_client_id.get(str(client_order_id)) or ""
        )
        if not resolved_order_id:
            return None
        try:
            raw = _mapping(self.client.get_order(resolved_order_id))
        except Exception:
            return None
        if not raw:
            return None
        metadata = dict(
            self.metadata_by_order_id.get(resolved_order_id)
            or self.metadata_by_client_id.get(str(client_order_id))
            or {}
        )
        requested = Decimal(
            str(raw.get("original_size") or metadata.get("shares") or "0")
        )
        matched = Decimal(str(raw.get("size_matched") or "0"))
        remaining = max(Decimal("0"), requested - matched)
        raw_status = _status(raw)
        root_order_id = str(
            metadata.get("root_order_id")
            or metadata.get("replacement_root_order_id")
            or resolved_order_id
        )
        return {
            "order_id": resolved_order_id,
            "expected_venue_order_id": metadata.get("expected_venue_order_id"),
            "root_order_id": root_order_id,
            "source_order_id": metadata.get("source_order_id") or resolved_order_id,
            "plan_id": metadata.get("plan_id") or metadata.get("legacy_plan_id"),
            "token_id": raw.get("asset_id") or metadata.get("token_id"),
            "venue_side": raw.get("side") or metadata.get("side"),
            "outcome_side": metadata.get("outcome_side") or "YES",
            "requested_shares": format(requested, "f"),
            "matched_shares": format(matched, "f"),
            "remaining_shares": format(remaining, "f"),
            "posted_price": raw.get("price") or metadata.get("price"),
            "status": raw_status,
            "created_at_utc": metadata.get("created_at_utc") or _utc_now(),
            "maker_only": bool(metadata.get("post_only")),
            "execution_profile": metadata.get("execution_profile"),
            "execution_policy": metadata.get("execution_policy"),
            "order_lifecycle_policy": metadata.get("order_lifecycle_policy"),
            "reprice_count": int(metadata.get("reprice_count") or 0),
            "data_epoch_ref": metadata.get("data_epoch_ref"),
            "authoritative_state_version": (
                f"{raw_status}:{raw.get('size_matched')}:{raw.get('price')}"
            ),
            "lifecycle_owner": metadata.get("lifecycle_owner"),
        }

    def reconcile_unknown(
        self,
        *,
        kind: str,
        identity_key: str,
    ) -> Mapping[str, Any] | None:
        if kind != "submit":
            return None
        prior = self.unknown_submit_by_identity.get(str(identity_key))
        if not prior:
            return None
        order = self.fetch_order(
            str(prior.get("order_id") or "") or None,
            str(prior.get("client_order_id") or ""),
        )
        return None if order is None else {"status": "submitted", "order": dict(order)}


def build_live_transport(
    *,
    market_proxy: str | None,
    metadata_by_order_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[LivePolymarketTransport, dict[str, Any]]:
    proxy = configure_market_proxy_env(resolve_live_market_proxy(market_proxy))
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderPayload, OrderType
    from py_clob_client_v2.constants import POLYGON

    host = (
        os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or "https://clob.polymarket.com"
    )
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    private_key = (
        os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip()
        or os.getenv("PM", "").strip()
    )
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")
    funder = os.getenv("PM_ADDRESS", "").strip()
    signature_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    if signature_type < 0:
        signer = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
        signature_type = 1 if funder and signer and funder.lower() != signer.lower() else 0
    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = (
        ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_pass,
        )
        if api_key and api_secret and api_pass
        else None
    )
    client = ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=signature_type,
        funder=funder or None,
    )
    if creds is None:
        client.set_api_creds(client.derive_api_key())
    return (
        LivePolymarketTransport(
            client=client,
            order_args_cls=OrderArgsV2,
            order_payload_cls=OrderPayload,
            order_type_cls=OrderType,
            metadata_by_order_id=metadata_by_order_id,
        ),
        proxy,
    )
