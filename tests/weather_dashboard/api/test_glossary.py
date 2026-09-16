"""Contract tests for the /api/glossary field dictionary."""


def test_glossary_returns_known_fields(client):
    r = client.get("/api/glossary")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body
    # core canonical fields the UI hovers must be present
    for key in ("open_cost", "posted_notional", "fill_date_bj", "heartbeat_age_min", "snapshot_age_min"):
        assert key in body["fields"], f"missing glossary entry: {key}"
        entry = body["fields"][key]
        assert entry["zh"] and entry["definition"]
