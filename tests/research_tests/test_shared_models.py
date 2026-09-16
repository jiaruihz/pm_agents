import json

from src.models import Event, Market


def test_market_to_storage_row():
    market = Market(
        market_id="m1",
        slug="test-market",
        question="q",
        description="d",
        rules="r",
        category="cat",
        active=True,
        resolved=False,
        end_at_utc="2026-01-01T00:00:00+00:00",
        volume=1000.0,
        liquidity=500.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.4, 0.6],
        clob_token_ids=["t_yes", "t_no"],
        event_ids=["e1"],
        event_slugs=["event-1"],
        event_titles=["Event 1"],
        event_tickers=["EVT1"],
        updated_at_utc="2026-01-01T00:00:00+00:00",
        last_synced_at_utc="2026-01-01T00:00:01+00:00",
    )
    row = market.to_storage_row()
    assert row["market_id"] == "m1"
    assert row["active"] == 1
    assert row["resolved"] == 0
    assert json.loads(row["outcomes_json"]) == ["Yes", "No"]
    assert json.loads(row["clob_token_ids_json"]) == ["t_yes", "t_no"]


def test_event_to_storage_row():
    event = Event(
        event_id="e1",
        slug="event-1",
        title="Event 1",
        description="desc",
        ticker="EVT1",
        tags=["politics", "us"],
        active=True,
        closed=False,
        start_at_utc="2026-01-01T00:00:00+00:00",
        end_at_utc="2026-02-01T00:00:00+00:00",
        volume=10.0,
        liquidity=20.0,
        updated_at_utc="2026-01-02T00:00:00+00:00",
        last_synced_at_utc="2026-01-02T00:00:01+00:00",
    )
    row = event.to_storage_row()
    assert row["event_id"] == "e1"
    assert row["active"] == 1
    assert row["closed"] == 0
    assert json.loads(row["tags_json"]) == ["politics", "us"]
