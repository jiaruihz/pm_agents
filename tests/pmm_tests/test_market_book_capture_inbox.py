from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.capture_inbox import CaptureDemandInbox
from weather_data_feed_service.market_books_ws import (
    Collector,
    MarketCaptureDemandCursor,
    Selection,
    apply_market_capture_demands,
    build_parser,
)


NOW = "2026-08-09T03:00:00Z"


def _alpha_demand(*, token_id: str = "alpha-yes", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "consumer_id": "polymarket_alpha",
        "strategy_key": "polymarket_alpha.p0_offline",
        "condition_id": "alpha-condition",
        "token_id": token_id,
        "reason": "alpha_p0_sensing",
        "priority": "P1",
        "requested_at_utc": "2026-08-09T02:59:00Z",
        "expires_at_utc": "2026-08-09T03:09:00Z",
        # The existing owner fulfills an Alpha direct-token request through
        # its selective WS capture plus canonical REST context; REST-only is
        # intentionally not a valid fulfillment request for this owner.
        "desired_transport": "REST_WS",
        "trigger_event_id": "alpha-trigger",
    }
    values.update(overrides)
    return CaptureDemand.create(**values).to_dict()


def _selection() -> Selection:
    return Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )


def _write(root: Path, consumer: str, rows: list[dict[str, object] | str]) -> Path:
    path = root / consumer / "capture_demands.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(row if isinstance(row, str) else json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_alpha_inbox_reads_only_its_fixed_consumer_journal_and_validates_owner_contract(tmp_path) -> None:
    alpha = _alpha_demand()
    weather = _alpha_demand(consumer_id="weather_consumer", token_id="weather-token")
    _write(tmp_path, "polymarket_alpha", [alpha])
    _write(tmp_path, "weather_consumer", [weather])

    inbox = CaptureDemandInbox(tmp_path)
    cursor = MarketCaptureDemandCursor(inbox=inbox)
    rows = cursor.read(now_utc=datetime.fromisoformat(NOW.replace("Z", "+00:00")))

    assert [row["demand_id"] for row in rows] == [alpha["demand_id"]]
    assert rows[0]["consumer_id"] == "polymarket_alpha"
    assert inbox.last_errors == ()
    selected = apply_market_capture_demands(
        _selection(),
        market_payload={"records": []},
        demands=rows,
        allowed_shared_strategy_keys=("polymarket_alpha.p0_offline",),
    )
    assert selected.tokens == {"alpha-yes"}
    assert selected.token_rows["alpha-yes"]["capture_universe"] == "shared_direct_token"


def test_one_bad_or_truncated_line_fails_closed_for_the_whole_consumer_journal(tmp_path) -> None:
    alpha = _alpha_demand()
    journal = _write(tmp_path, "polymarket_alpha", [alpha, "not-json\n"])
    inbox = CaptureDemandInbox(tmp_path)

    assert inbox.read_lines() == ()
    assert inbox.last_errors[0].startswith("journal_invalid:polymarket_alpha:")

    journal.write_text(json.dumps(alpha) + "\n" + json.dumps(alpha)[:10], encoding="utf-8")
    assert inbox.read_lines() == ()
    assert inbox.last_errors == ("journal_truncated:polymarket_alpha",)


def test_alpha_inbox_fails_closed_for_consumer_mismatch_symlink_and_budget(tmp_path) -> None:
    mismatched = _alpha_demand(consumer_id="weather_consumer")
    journal = _write(tmp_path, "polymarket_alpha", [mismatched])
    inbox = CaptureDemandInbox(tmp_path)
    assert MarketCaptureDemandCursor(inbox=inbox).read(
        now_utc=datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    ) == []

    journal.unlink()
    target = tmp_path / "outside.jsonl"
    target.write_text(json.dumps(_alpha_demand()) + "\n", encoding="utf-8")
    journal.symlink_to(target)
    assert inbox.read_lines() == ()
    assert inbox.last_errors == ("journal_unavailable:polymarket_alpha:OSError",)

    journal.unlink()
    journal.write_text("x" * 129, encoding="utf-8")
    bounded = CaptureDemandInbox(tmp_path, max_bytes_per_consumer=128)
    assert bounded.read_lines() == ()
    assert bounded.last_errors == ("journal_byte_budget_exceeded:polymarket_alpha",)
    with pytest.raises(ValueError, match="safe identifier"):
        CaptureDemandInbox(tmp_path, consumers=("../weather",))
    with pytest.raises(ValueError, match="absolute non-traversing"):
        CaptureDemandInbox("relative-inbox")


def test_alpha_inbox_exact_retry_is_idempotent_and_conflicting_replay_cannot_rewrite(tmp_path) -> None:
    original = _alpha_demand()
    conflicting = dict(original)
    conflicting["metadata"] = {"different": "payload"}
    journal = _write(tmp_path, "polymarket_alpha", [original, original])
    cursor = MarketCaptureDemandCursor(inbox=CaptureDemandInbox(tmp_path))
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))

    first = cursor.read(now_utc=now)
    second = cursor.read(now_utc=now)
    assert first == second == [original]

    journal.write_text(json.dumps(conflicting) + "\n", encoding="utf-8")
    assert cursor.read(now_utc=now) == []
    assert cursor.inbox is not None
    assert cursor.inbox.last_errors == (
        "journal_invalid:polymarket_alpha:conflicting immutable demand replay",
    )


def test_inbox_line_limit_and_unconfigured_cli_keep_default_behavior(tmp_path) -> None:
    _write(tmp_path, "polymarket_alpha", [_alpha_demand(token_id=f"token-{index}") for index in range(2)])
    inbox = CaptureDemandInbox(tmp_path, max_lines_per_consumer=1)
    assert inbox.read_lines() == ()
    assert inbox.last_errors == ("journal_line_budget_exceeded:polymarket_alpha",)

    args = build_parser().parse_args(
        [
            "--market-books-latest", str(tmp_path / "latest.json"),
            "--observation-cache", str(tmp_path / "observations.json"),
            "--source-events-jsonl", str(tmp_path / "sources.jsonl"),
            "--output-root", str(tmp_path / "output"),
            "--health-path", str(tmp_path / "health.json"),
        ]
    )
    assert args.shared_capture_demand_inbox_root == ""


def test_collector_enables_alpha_strategy_only_when_explicit_inbox_root_is_configured(tmp_path) -> None:
    _write(tmp_path / "inbox", "polymarket_alpha", [_alpha_demand()])
    base = [
        "--market-books-latest", str(tmp_path / "latest.json"),
        "--observation-cache", str(tmp_path / "observations.json"),
        "--source-events-jsonl", str(tmp_path / "sources.jsonl"),
        "--output-root", str(tmp_path / "output"),
        "--health-path", str(tmp_path / "health.json"),
    ]
    disabled = Collector(build_parser().parse_args(base))
    assert disabled.refresh_selection(datetime.fromisoformat(NOW.replace("Z", "+00:00"))).tokens == set()
    enabled = Collector(
        build_parser().parse_args(
            [*base, "--shared-capture-demand-inbox-root", str(tmp_path / "inbox")]
        )
    )
    assert enabled.refresh_selection(datetime.fromisoformat(NOW.replace("Z", "+00:00"))).tokens == {"alpha-yes"}


def test_enabling_inbox_does_not_allow_alpha_injection_from_legacy_shared_path(tmp_path) -> None:
    inbox_root = tmp_path / "inbox"
    (inbox_root / "polymarket_alpha").mkdir(parents=True)
    legacy = _write(tmp_path / "legacy", "polymarket_alpha", [_alpha_demand()])
    cursor = MarketCaptureDemandCursor(
        legacy,
        inbox=CaptureDemandInbox(inbox_root),
    )

    assert cursor.read(now_utc=datetime.fromisoformat(NOW.replace("Z", "+00:00"))) == []


def test_rest_only_alpha_demand_is_valid_schema_but_not_owner_fulfillable(tmp_path) -> None:
    rest_only = _alpha_demand(desired_transport="REST")
    _write(tmp_path, "polymarket_alpha", [rest_only])
    rows = MarketCaptureDemandCursor(inbox=CaptureDemandInbox(tmp_path)).read(
        now_utc=datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    )
    selection = apply_market_capture_demands(
        _selection(),
        market_payload={"records": []},
        demands=rows,
        allowed_shared_strategy_keys=("polymarket_alpha.p0_offline",),
    )

    assert selection.tokens == set()
    assert selection.capture_demands[0]["resolution_status"] == (
        "invalid_shared_capture_demand_contract"
    )


def test_collector_health_records_inbox_fail_closed_reason(tmp_path) -> None:
    inbox_root = tmp_path / "inbox"
    journal = _write(inbox_root, "polymarket_alpha", [_alpha_demand()])
    journal.unlink()
    journal.symlink_to(tmp_path / "outside.jsonl")
    args = build_parser().parse_args(
        [
            "--market-books-latest", str(tmp_path / "latest.json"),
            "--observation-cache", str(tmp_path / "observations.json"),
            "--source-events-jsonl", str(tmp_path / "sources.jsonl"),
            "--output-root", str(tmp_path / "output"),
            "--health-path", str(tmp_path / "health.json"),
            "--shared-capture-demand-inbox-root", str(inbox_root),
        ]
    )
    collector = Collector(args)
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    collector.refresh_selection(now)
    collector.publish_health(now)
    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["shared_capture_demand_inbox_errors"][0].startswith(
        "journal_unavailable:polymarket_alpha:"
    )


def test_inbox_does_not_follow_consumer_directory_symlink(tmp_path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "polymarket_alpha", [_alpha_demand()])
    os.symlink(outside / "polymarket_alpha", tmp_path / "polymarket_alpha")
    inbox = CaptureDemandInbox(tmp_path)

    assert inbox.read_lines() == ()
    assert inbox.last_errors[0].startswith("consumer_unavailable:polymarket_alpha:")


def test_inbox_does_not_follow_root_symlink(tmp_path) -> None:
    real_root = tmp_path / "real-inbox"
    _write(real_root, "polymarket_alpha", [_alpha_demand()])
    linked_root = tmp_path / "linked-inbox"
    linked_root.symlink_to(real_root, target_is_directory=True)

    inbox = CaptureDemandInbox(linked_root)
    assert inbox.read_lines() == ()
    assert inbox.last_errors[0].startswith("inbox_root_unavailable:")


def test_inbox_does_not_follow_intermediate_symlink_or_block_on_fifo(tmp_path) -> None:
    real_parent = tmp_path / "real-parent"
    inbox_root = real_parent / "inbox"
    _write(inbox_root, "polymarket_alpha", [_alpha_demand()])
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    linked = CaptureDemandInbox(alias / "inbox")
    assert linked.read_lines() == ()
    assert linked.last_errors[0].startswith("inbox_root_unavailable:")

    journal = inbox_root / "polymarket_alpha" / "capture_demands.jsonl"
    journal.unlink()
    os.mkfifo(journal)
    fifo = CaptureDemandInbox(inbox_root)
    assert fifo.read_lines() == ()
    assert fifo.last_errors == ("journal_not_regular:polymarket_alpha",)
