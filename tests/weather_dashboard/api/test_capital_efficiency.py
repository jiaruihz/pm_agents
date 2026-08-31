from datetime import datetime, timezone

import pytest

from weather_dashboard.api.capital_efficiency import build_capital_efficiency_report
from weather_dashboard.api.routers import copy_trade


WALLET = "0x83946fd2a83b4c0045a17a938ae0802dfaf58d66"


def _positions():
    return [
        {
            "asset": "near",
            "conditionId": "0x01",
            "size": 100,
            "avgPrice": 0.8,
            "initialValue": 80,
            "grossInitialValue": 81,
            "entryFeesUsdc": 1,
            "curPrice": 0.9,
            "currentValue": 90,
            "title": "Near market",
            "outcome": "Yes",
            "endDate": "2026-09-01",
            "redeemable": False,
        },
        {
            "asset": "far",
            "conditionId": "0x02",
            "size": 50,
            "avgPrice": 0.9,
            "initialValue": 45,
            "curPrice": 0.95,
            "currentValue": 47.5,
            "title": "Far market",
            "outcome": "No",
            "endDate": "2026-12-31",
            "redeemable": False,
        },
        {
            "asset": "overdue",
            "conditionId": "0x03",
            "size": 20,
            "avgPrice": 0.85,
            "initialValue": 17,
            "curPrice": 0.9,
            "currentValue": 18,
            "title": "Awaiting resolution",
            "outcome": "Yes",
            "endDate": "2026-08-26",
            "redeemable": False,
        },
        {
            "asset": "claim",
            "conditionId": "0x04",
            "size": 10,
            "avgPrice": 0.7,
            "initialValue": 7,
            "curPrice": 1,
            "currentValue": 10,
            "title": "Resolved winner",
            "outcome": "Yes",
            "endDate": "2026-08-25",
            "redeemable": True,
        },
        {
            "asset": "dust",
            "conditionId": "0x05",
            "size": 1,
            "avgPrice": 0.1,
            "initialValue": 0.1,
            "curPrice": 0.001,
            "currentValue": 0.001,
            "title": "Dust",
            "outcome": "Yes",
            "endDate": "2027-01-01",
            "redeemable": False,
        },
    ]


def _books():
    return [
        {
            "asset_id": "near",
            "bids": [{"price": "0.89", "size": "60"}, {"price": "0.88", "size": "40"}],
            "asks": [{"price": "0.91", "size": "100"}],
        },
        {
            "asset_id": "far",
            "bids": [{"price": "0.94", "size": "20"}],
            "asks": [{"price": "0.96", "size": "100"}],
        },
    ]


def test_report_separates_mark_exit_maturity_and_cash():
    report = build_capital_efficiency_report(
        wallet_address=WALLET,
        positions=_positions(),
        books=_books(),
        observed_at=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
        annual_hurdle_rate=0.20,
        available_cash_usd=100,
        reserved_cash_usd=50,
        profile={"name": "deepsleep"},
    )

    summary = report["summary"]
    assert report["display_name"] == "deepsleep"
    assert summary["active_positions_count"] == 3
    assert summary["active_mark_value_usd"] == pytest.approx(155.5)
    assert summary["active_cost_basis_usd"] == pytest.approx(143.0)
    assert summary["redeemable_value_usd"] == pytest.approx(10.0)
    assert summary["accounted_capital_usd"] == pytest.approx(315.5)
    assert summary["capital_utilization"] == pytest.approx(205.5 / 315.5)
    assert summary["below_hurdle_positions_count"] == 1
    assert summary["below_hurdle_mark_value_usd"] == pytest.approx(47.5)
    assert summary["below_hurdle_portfolio_share"] == pytest.approx(47.5 / 155.5)

    near = next(row for row in report["positions"] if row["token_id"] == "near")
    assert near["days_to_end"] == 5
    assert near["visible_exit_value"] == pytest.approx(88.6)
    assert near["visible_exit_vwap"] == pytest.approx(0.886)
    assert near["exit_coverage_ratio"] == pytest.approx(1.0)
    assert near["exit_slippage_vs_mark_usd"] == pytest.approx(1.4)
    assert near["gross_return_if_win"] == pytest.approx(10 / 90)
    assert near["annualized_simple_return_if_win"] == pytest.approx((10 / 90) * 365 / 5)
    assert near["required_confidence_for_hurdle"] == pytest.approx(0.9 * (1 + 0.2 * 5 / 365))

    far = next(row for row in report["positions"] if row["token_id"] == "far")
    assert far["exit_coverage_ratio"] == pytest.approx(0.4)
    assert far["full_exit_value_usd"] is None

    overdue = next(row for row in report["positions"] if row["token_id"] == "overdue")
    assert overdue["status"] == "pending_resolution"
    assert overdue["annualized_simple_return_if_win"] is None
    assert report["data_quality"]["pending_resolution_count"] == 1


def test_utilization_is_not_published_without_private_cash_inputs():
    report = build_capital_efficiency_report(
        wallet_address=WALLET,
        positions=_positions(),
        observed_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert report["cash_inputs_complete"] is False
    assert report["summary"]["accounted_capital_usd"] is None
    assert report["summary"]["capital_utilization"] is None


def test_today_unknown_end_and_missing_book_are_explicit():
    report = build_capital_efficiency_report(
        wallet_address=WALLET,
        positions=[
            {
                "asset": "today",
                "size": 10,
                "avgPrice": 0.8,
                "curPrice": 0.9,
                "currentValue": 9,
                "endDate": "2026-08-27",
                "redeemable": False,
            },
            {
                "asset": "unknown",
                "size": 10,
                "avgPrice": 0.8,
                "curPrice": 0.9,
                "currentValue": 9,
                "redeemable": False,
            },
        ],
        observed_at=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
    )
    today = next(row for row in report["positions"] if row["token_id"] == "today")
    unknown = next(row for row in report["positions"] if row["token_id"] == "unknown")
    assert today["days_to_end"] == 1
    assert today["annualized_simple_return_if_win"] is not None
    assert today["exit_coverage_ratio"] == 0
    assert unknown["status"] == "unknown_end"
    assert unknown["days_to_end"] is None
    assert unknown["annualized_simple_return_if_win"] is None
    assert report["data_quality"]["unknown_end_date_count"] == 1


def test_positions_api_paginates_past_500(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(*args, **kwargs):
        offset = kwargs["params"]["offset"]
        calls.append(offset)
        if offset == 0:
            return FakeResponse([{"asset": f"asset-{idx}", "size": 1} for idx in range(500)])
        return FakeResponse([{"asset": "asset-500", "size": 1}])

    monkeypatch.setattr(copy_trade.requests, "get", fake_get)
    positions, meta = copy_trade._get_positions_strict(WALLET)
    assert len(positions) == 501
    assert calls == [0, 500]
    assert meta == {"pages_fetched": 2, "truncated": False, "max_supported_positions": 10_500}


def test_clob_batch_failure_is_reported(monkeypatch):
    def fail_post(*args, **kwargs):
        raise TimeoutError("book timeout")

    monkeypatch.setattr(copy_trade.requests, "post", fail_post)
    books, errors = copy_trade._get_clob_books(["asset-1", "asset-2"])
    assert books == []
    assert errors == ["batch_1:TimeoutError"]


def test_capital_efficiency_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        copy_trade,
        "_get_positions_strict",
        lambda wallet: (_positions() + [{"asset": "bad", "size": "not-a-number"}], {"pages_fetched": 1, "truncated": False}),
    )
    monkeypatch.setattr(copy_trade, "_get_clob_books", lambda token_ids: (_books(), []))
    monkeypatch.setattr(copy_trade, "_get_public_profile", lambda wallet: {"name": "deepsleep"})

    response = client.get(
        "/api/copy-trade/capital-efficiency",
        params={
            "wallet_address": WALLET,
            "annual_hurdle_rate": 0.08,
            "available_cash_usd": 100,
            "reserved_cash_usd": 0,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "deepsleep"
    assert payload["annual_hurdle_rate"] == pytest.approx(0.08)
    assert payload["sources"]["books_requested"] == 4
    assert payload["sources"]["books_received"] == 2
    assert payload["sources"]["position_pages_fetched"] == 1
    assert payload["data_quality"]["positions_truncated"] is False
    assert payload["data_quality"]["order_book_fetch_complete"] is False


def test_capital_efficiency_endpoint_rejects_invalid_wallet(client):
    response = client.get(
        "/api/copy-trade/capital-efficiency",
        params={"wallet_address": "not-a-wallet"},
    )
    assert response.status_code == 422
