import json

from src.domains.research.clients.gamma import (
    normalize_event,
    normalize_event_model,
    normalize_market,
    normalize_market_model,
)


def test_normalize_market_model_and_storage_row():
    raw = {
        "id": 123,
        "slug": "market-abc",
        "question": "Will X happen?",
        "description": "desc",
        "rules": "rules",
        "category": "politics",
        "active": True,
        "resolved": False,
        "endDate": "2026-12-31T00:00:00Z",
        "volume": 1000,
        "liquidity": 500,
        "outcomes": '["Yes","No"]',
        "outcomePrices": "[0.44,0.56]",
        "clobTokenIds": '["t1","t2"]',
        "events": [{"id": 88, "slug": "ev", "title": "Event", "ticker": "EV"}],
        "updatedAt": "2026-01-01T00:00:00Z",
    }

    model = normalize_market_model(raw)
    assert model.market_id == "123"
    assert model.clob_token_ids == ["t1", "t2"]
    assert model.event_ids == ["88"]
    assert model.event_tickers == ["EV"]

    row = normalize_market(raw)
    assert row["market_id"] == "123"
    assert row["active"] == 1
    assert row["resolved"] == 0
    assert json.loads(row["outcomes_json"]) == ["Yes", "No"]
    assert json.loads(row["event_ids_json"]) == ["88"]


def test_normalize_event_model_and_storage_row():
    raw = {
        "eventId": 42,
        "slug": "event-x",
        "title": "Event X",
        "description": "desc",
        "ticker": "EX",
        "tags": [{"slug": "Politics"}, {"label": "US"}],
        "active": 1,
        "closed": 0,
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-02-01T00:00:00Z",
        "updatedAt": "2026-01-03T00:00:00Z",
    }

    model = normalize_event_model(raw)
    assert model.event_id == "42"
    assert model.tags == ["politics", "us"]

    row = normalize_event(raw)
    assert row["event_id"] == "42"
    assert row["active"] == 1
    assert row["closed"] == 0
    assert json.loads(row["tags_json"]) == ["politics", "us"]
