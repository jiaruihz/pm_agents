from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from scripts.analysis.forecast_quality.research_wcir_stage02_stage03_rev2 import (
    CheckpointQuery,
    HashedJsonlReader,
    TokenState,
    classify_checkpoint,
    freeze_latency_inputs,
    independent_net_pnl,
    percentile,
    select_primary_oracle_action,
)
from src.platform.market_data.executable_book_truth import (
    ExecutableBookTruth,
    ExecutableSweep,
)


@dataclass
class FakeSnapshot:
    last_frame_received_at_utc: str
    best_bid: float | None
    best_ask: float | None
    book_snapshot_id: str = "book-1"
    raw_lineage_id: str = "lineage-1"
    exchange_ts_ms: int = 1

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def query() -> CheckpointQuery:
    return CheckpointQuery(
        at_ns=1_000_000_000,
        event_id="event-1",
        checkpoint="source_t0",
        checkpoint_at_utc="1970-01-01T00:00:01+00:00",
        token_id="token-1",
        condition_id="condition-1",
        outcome="no",
        bracket="20",
        roles=("prior_exact_bracket",),
    )


def truth(*, buy: bool, sell: bool) -> ExecutableBookTruth:
    sweeps = []
    for shares in (1.0, 5.0, 10.0):
        for side, feasible in (("buy", buy), ("sell", sell)):
            sweeps.append(
                ExecutableSweep(
                    shares=shares,
                    side=side,
                    gross_value_usd=shares * 0.5 if feasible else None,
                    taker_fee_usd=shares * 0.01 if feasible else None,
                    effective_value_usd=(
                        shares * (0.51 if side == "buy" else 0.49)
                        if feasible
                        else None
                    ),
                    average_price=0.5 if feasible else None,
                    fully_executable=feasible,
                )
            )
    return ExecutableBookTruth(
        book_snapshot_id="book-1",
        subscription_epoch_id="epoch-1",
        token_id="token-1",
        source="test",
        as_of_utc="1970-01-01T00:00:00.5+00:00",
        received_at_utc="1970-01-01T00:00:00.5+00:00",
        exchange_ts_ms=1,
        book_valid=True,
        gap_reason=None,
        queue_truth=False,
        maker_fill_proxy_only=True,
        sequence_status=None,
        gap_detection_status=None,
        sweeps=tuple(sweeps),
        truth_id="truth-1",
    )


def test_primary_failure_reasons_are_unique_and_invalid_separate_from_feasibility() -> None:
    missing = classify_checkpoint(
        query(), None, ever_subscribed=False, expected_condition="condition-1"
    )
    assert missing["primary_status"] == "ARCHIVE_MISSING"
    assert missing["reconstruction_invalid"] is True
    assert missing["execution_infeasible"] is False

    gap = classify_checkpoint(
        query(),
        TokenState(
            at_ns=0,
            subscription_epoch_id="epoch-1",
            condition_id="condition-1",
            status="open_gap",
            blocker_reason="best_quote_parity_mismatch",
            snapshot=None,
            truth=None,
        ),
        ever_subscribed=True,
        expected_condition="condition-1",
    )
    assert gap["primary_status"] == "OPEN_GAP"

    one_sided = classify_checkpoint(
        query(),
        TokenState(
            at_ns=0,
            subscription_epoch_id="epoch-1",
            condition_id="condition-1",
            status="valid",
            blocker_reason=None,
            snapshot=FakeSnapshot(
                last_frame_received_at_utc="1970-01-01T00:00:00.5+00:00",
                best_bid=0.4,
                best_ask=None,
            ),
            truth=truth(buy=False, sell=True),
        ),
        ever_subscribed=True,
        expected_condition="condition-1",
    )
    assert one_sided["book_valid"] is True
    assert one_sided["primary_status"] == "VALID_BUT_ONE_SIDED"
    assert one_sided["execution_infeasible"] is True
    assert one_sided["reconstruction_invalid"] is False


def test_stale_book_does_not_become_execution_infeasible() -> None:
    stale_query = CheckpointQuery(
        **{
            **query().__dict__,
            "at_ns": 200_000_000_000,
            "checkpoint_at_utc": "1970-01-01T00:03:20+00:00",
        }
    )
    row = classify_checkpoint(
        stale_query,
        TokenState(
            at_ns=0,
            subscription_epoch_id="epoch-1",
            condition_id="condition-1",
            status="valid",
            blocker_reason=None,
            snapshot=FakeSnapshot(
                last_frame_received_at_utc="1970-01-01T00:00:00+00:00",
                best_bid=0.4,
                best_ask=0.6,
            ),
            truth=truth(buy=True, sell=True),
        ),
        ever_subscribed=True,
        expected_condition="condition-1",
    )
    assert row["primary_status"] == "STALE_BOOK"
    assert row["execution_infeasible"] is False


def test_condition_identity_mismatch_fails_before_book_use() -> None:
    row = classify_checkpoint(
        query(),
        TokenState(
            at_ns=0,
            subscription_epoch_id="epoch-1",
            condition_id="wrong-condition",
            status="valid",
            blocker_reason=None,
            snapshot=FakeSnapshot(
                last_frame_received_at_utc="1970-01-01T00:00:00.5+00:00",
                best_bid=0.4,
                best_ask=0.6,
            ),
            truth=truth(buy=True, sell=True),
        ),
        ever_subscribed=True,
        expected_condition="condition-1",
    )
    assert row["primary_status"] == "IDENTITY_MISMATCH"
    assert row["reconstruction_invalid"] is True


def test_within_file_receive_clock_regression_is_explicit(tmp_path) -> None:
    path = tmp_path / "frames.jsonl"
    path.write_text(
        json.dumps({"received_at_ns": 2, "received_at_utc": "1970-01-01T00:00:00.000000002+00:00"})
        + "\n"
        + json.dumps({"received_at_ns": 1, "received_at_utc": "1970-01-01T00:00:00.000000001+00:00"})
        + "\n"
    )
    reader = HashedJsonlReader(path)
    assert reader.next().get("_within_file_receive_clock_regression") is None
    assert reader.next()["_within_file_receive_clock_regression"] is True
    assert reader.next() is None
    assert reader.identity()["within_file_receive_clock_regressions"] == 1


def test_duplicate_event_immutable_drift_fails_closed(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    base = {
        "event_key": "key-1",
        "city": "Tokyo",
        "target_date": "2026-08-09",
        "source": "jma_amedas",
        "source_detect_to_runner_sec": 1.0,
        "fresh_book_fetch_duration_sec": 0.5,
        "source_temp_c": 30.0,
    }
    path.write_text(
        json.dumps(base) + "\n" + json.dumps({**base, "source_temp_c": 31.0}) + "\n"
    )
    with pytest.raises(RuntimeError, match="immutable duplicate event payload drift"):
        freeze_latency_inputs(
            path,
            [
                {
                    "event_key": "key-1",
                    "event_id": "event-1",
                }
            ],
        )


def test_primary_oracle_action_uses_t0_cost_and_semantics_only() -> None:
    event = {"official_round_c": 22, "metar_running_max_round_c": 21}
    universe = {
        "tokens": [
            {
                "token_id": "old-no",
                "outcome": "no",
                "roles": ["prior_exact_bracket"],
            },
            {
                "token_id": "new-yes",
                "outcome": "yes",
                "roles": ["actual_next_print_bracket"],
            },
        ]
    }

    def entry(cost: float) -> dict:
        return {
            "book_valid": True,
            "sweeps": {
                "buy_5": {
                    "fully_executable": True,
                    "effective_value_usd": cost,
                }
            },
        }

    action = select_primary_oracle_action(
        event, universe, {"old-no": entry(2.0), "new-yes": entry(3.0)}
    )
    assert action["token_id"] == "old-no"
    assert "exit" not in action
    assert "horizon" not in action


def test_independent_pnl_charges_entry_and_exit_fees() -> None:
    value = independent_net_pnl(
        {"gross_value_usd": 2.0, "taker_fee_usd": 0.1},
        {"gross_value_usd": 3.0, "taker_fee_usd": 0.2},
    )
    assert value == pytest.approx(0.7)


def test_percentile_uses_conservative_nearest_rank() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
