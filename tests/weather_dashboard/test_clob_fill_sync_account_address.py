from __future__ import annotations

import sqlite3

import pytest

from weather_dashboard.ingest import clob_fill_sync


class _CapturedAddress(RuntimeError):
    pass


def test_default_authenticated_trade_lookup_uses_funder(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(":memory:")
    monkeypatch.setenv("PM_ADDRESS", "0xfunder")
    monkeypatch.setattr(clob_fill_sync, "_get_submitted_orders", lambda _conn: [object()])
    monkeypatch.setattr(clob_fill_sync, "_build_clob_client", lambda: object())

    seen: list[str] = []

    def capture_address(_client: object, address: str) -> list[dict[str, object]]:
        seen.append(address)
        raise _CapturedAddress

    monkeypatch.setattr(clob_fill_sync, "_fetch_trades_clob", capture_address)

    with pytest.raises(_CapturedAddress):
        clob_fill_sync.sync_clob_fills(conn, dry_run=True)

    assert seen == ["0xfunder"]


def test_v2_clob_share_units_are_not_divided_by_one_million() -> None:
    order = clob_fill_sync._parse_clob_order_response(
        {"status": "MATCHED", "size_matched": "5", "price": "0.973"}
    )
    trade = clob_fill_sync._extract_trade_fill(
        [{"size": "5", "price": "0.973"}],
        row_shares=5.0,
        row_limit_price=0.973,
    )

    assert order["size_matched"] == 5.0
    assert trade["size_matched"] == 5.0
