from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json

from scripts.ops import weather_knmi_first_seen_ladder_shadow_v1 as subject


def _market_record(bracket: str, condition: str) -> dict:
    return {
        "city": "Amsterdam",
        "target_date": "2026-07-30",
        "event_date": "2026-07-30",
        "event_slug": "highest-temperature-in-amsterdam-on-july-30-2026",
        "question": f"Will the highest temperature in Amsterdam be {bracket}°C on July 30?",
        "bracket": bracket,
        "market_id": f"market-{condition}",
        "condition_id": condition,
        "yes_token_id": f"yes-{condition}",
        "no_token_id": f"no-{condition}",
        "snapshot_ts_utc": "2026-07-30T01:33:00Z",
        "model_prob": 0.5,
    }


def test_capture_snapshot_fetches_both_sides_for_every_ladder_market(
    monkeypatch,
    tmp_path,
) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "snapshot_20260730_0133.json").write_text(
        json.dumps(
            {
                "ts_utc": "2026-07-30T01:33:00Z",
                "records": [
                    _market_record("24", "c24"),
                    _market_record("25", "c25"),
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subject,
        "augment_market_index_from_gamma",
        lambda index, **_kwargs: index,
    )
    calls = []

    def fake_fetch(token_id, **_kwargs):
        calls.append(token_id)
        return {
            "status": "ok",
            "fetched_at_utc": "2026-07-30T01:34:00Z",
            "summary": {
                "best_bid": 0.40,
                "best_ask": 0.42,
                "bid_size": 10.0,
                "ask_size": 12.0,
                "spread": 0.02,
                "bids": [{"price": 0.40, "size": 10.0}],
                "asks": [{"price": 0.42, "size": 12.0}],
            },
        }

    payload = subject.capture_snapshot(
        {
            "information_event_id": "event-1",
            "capture_anchor_utc": "2026-07-30T01:33:58Z",
            "first_seen_at_utc": "2026-07-30T01:33:58Z",
            "available_at_utc": "2026-07-30T01:33:59Z",
            "source_event_ts_utc": "2026-07-30T01:30:00Z",
        },
        0,
        paper_snapshot_root=paper,
        market_proxy="direct",
        max_workers=4,
        top_n=50,
        book_fetcher=fake_fetch,
        now=datetime(2026, 7, 30, 1, 34, tzinfo=timezone.utc),
    )

    assert set(calls) == {"yes-c24", "no-c24", "yes-c25", "no-c25"}
    assert payload["ladder_market_count"] == 2
    assert payload["book_ok_count"] == 4
    assert payload["capture_status"] == "complete"
    assert payload["zero_notional"] and payload["no_order_placed"]
    assert all(row["yes_book_asks"] and row["no_book_bids"] for row in payload["records"])


def test_capture_snapshot_retries_failed_book_fetch(monkeypatch, tmp_path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "snapshot_20260730_0133.json").write_text(
        json.dumps(
            {
                "ts_utc": "2026-07-30T01:33:00Z",
                "records": [_market_record("24", "c24")],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subject,
        "augment_market_index_from_gamma",
        lambda index, **_kwargs: index,
    )
    attempts = {}

    def flaky_fetch(token_id, **_kwargs):
        attempts[token_id] = attempts.get(token_id, 0) + 1
        status = "error" if token_id == "no-c24" and attempts[token_id] == 1 else "ok"
        return {
            "status": status,
            "fetched_at_utc": "2026-07-30T01:34:00Z",
            "summary": {},
            "error": "transient" if status == "error" else None,
        }

    payload = subject.capture_snapshot(
        {
            "information_event_id": "event-1",
            "capture_anchor_utc": "2026-07-30T01:33:58Z",
        },
        0,
        paper_snapshot_root=paper,
        market_proxy="direct",
        max_workers=2,
        top_n=50,
        book_fetcher=flaky_fetch,
        now=datetime(2026, 7, 30, 1, 34, tzinfo=timezone.utc),
    )

    assert payload["capture_status"] == "complete"
    assert attempts == {"yes-c24": 1, "no-c24": 2}
    assert payload["records"][0]["no_book_fetch_attempt_count"] == 2
    assert payload["records"][0]["no_book_fetch_attempt_statuses"] == ["error", "ok"]


def test_read_new_events_bootstraps_then_reads_only_material_initial(tmp_path) -> None:
    source = tmp_path / "knmi.jsonl"
    source.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
    state = {}
    now = datetime(2026, 7, 30, 1, 34, tzinfo=timezone.utc)

    assert subject.read_new_events(
        source,
        state,
        bootstrap_at_end=True,
        now=now,
        max_event_age_seconds=90,
    ) == []
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "source": "knmi",
                    "city": "Amsterdam",
                    "station": "06240",
                    "target_date": "2026-07-30",
                    "observation_time_utc": "2026-07-30T01:30:00Z",
                    "knmi_first_seen_at_utc": "2026-07-30T01:33:58Z",
                    "fetched_at_utc": "2026-07-30T01:33:59Z",
                    "knmi_revision_kind": "initial",
                    "temp_c": 18.4,
                }
            )
            + "\n"
        )

    events = subject.read_new_events(
        source,
        state,
        bootstrap_at_end=False,
        now=now,
        max_event_age_seconds=90,
    )
    assert len(events) == 1
    assert events[0]["source"] == "knmi"
    assert events[0]["capture_anchor_utc"] == "2026-07-30T01:33:58.000000Z"


def test_pre_event_archive_skips_newer_partial_ladder(tmp_path) -> None:
    root = tmp_path / "books"
    root.mkdir()

    def write_archive(name: str, timestamp: str, *, partial: bool) -> None:
        rows = []
        for condition in ("c24", "c25"):
            for outcome in ("yes", "no"):
                rows.append(
                    {
                        "city": "Amsterdam",
                        "target_date": "2026-07-30",
                        "condition_id": condition,
                        "outcome": outcome,
                        "status": (
                            "error"
                            if partial and condition == "c25" and outcome == "no"
                            else "ok"
                        ),
                        "snapshot_ts_utc": timestamp,
                    }
                )
        with gzip.open(root / name, "wt", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    write_archive(
        "orderbook_snapshot_20260730_013300.jsonl.gz",
        "2026-07-30T01:33:00Z",
        partial=False,
    )
    write_archive(
        "orderbook_snapshot_20260730_013350.jsonl.gz",
        "2026-07-30T01:33:50Z",
        partial=True,
    )

    path, rows = subject.latest_orderbook_archive_at_or_before(
        root,
        datetime(2026, 7, 30, 1, 33, 58, tzinfo=timezone.utc),
        "2026-07-30",
        expected_condition_ids={"c24", "c25"},
    )

    assert path is not None
    assert path.name == "orderbook_snapshot_20260730_013300.jsonl.gz"
    assert len(rows) == 4
    assert all(row["status"] == "ok" for row in rows)
