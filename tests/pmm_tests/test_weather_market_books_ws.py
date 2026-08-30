import json
import asyncio
from datetime import datetime, timedelta, timezone

from websockets.client import ClientProtocol
from websockets.uri import parse_uri

from src.platform.market_data.capture_demand import CaptureDemand
from weather_data_feed_service.market_books_ws import (
    Collector,
    DEFAULT_CITIES,
    HourlyWriter,
    MarketCaptureDemandCursor,
    PreTransportSafeClientConnection,
    Selection,
    SourceEventCursor,
    _rest_health,
    apply_market_capture_demands,
    build_parser,
    scheduled_report_windows,
    select_tokens,
)


NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def test_rest_health_accepts_fresh_event_contract_reuse(tmp_path) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            {
                "status": "ok_with_discovery_reuse",
                "available_at_utc": "2026-08-09T02:59:30Z",
                "batch_capture_id": "batch-reused",
            }
        )
    )

    health = _rest_health(latest, now_utc=NOW, max_age_sec=420)

    assert health["healthy"] is True
    assert health["status"] == "ok_with_discovery_reuse"


def test_proxy_reset_before_transport_initialization_closes_cleanly() -> None:
    async def exercise() -> None:
        connection = PreTransportSafeClientConnection(
            ClientProtocol(parse_uri("wss://example.com/ws"))
        )
        error = ConnectionResetError("proxy reset")
        connection.connection_lost(error)

        assert connection.connection_lost_waiter.done()
        assert connection.recv_exc is error

    asyncio.run(exercise())


def test_default_microstructure_rollout_excludes_seoul() -> None:
    assert DEFAULT_CITIES == ("Amsterdam", "Tokyo", "Helsinki", "Busan")


def test_tmax_ws_selector_ignores_tmin_rows() -> None:
    payload = _market_payload()
    for row in payload["records"]:
        row["extreme_kind"] = "min"

    selected = select_tokens(
        market_payload=payload,
        observations={
            ("Busan", "2026-08-09"): {"status": "ok", "running_max_c": 32.0}
        },
        cities=["Busan"],
        now_utc=NOW,
        active_bracket_count=2,
        research_bracket_count=3,
        event_bracket_count=3,
        post_invalidation_sec=300,
        scheduled_keys={("Busan", "2026-08-09")},
        research_keys=set(),
        burst_keys=set(),
        invalidation_state={},
    )

    assert selected.tokens == set()


def _market_payload(city: str = "Busan") -> dict:
    rows = []
    for bracket in ("30", "31", "32", "33", "34", "35", "36"):
        for outcome in ("yes", "no"):
            rows.append(
                {
                    "city": city,
                    "event_date": "2026-08-09",
                    "city_local_date_at_snapshot": "2026-08-09",
                    "bracket": bracket,
                    "outcome": outcome,
                    "token_id": f"{city}-{bracket}-{outcome}",
                }
            )
    return {"records": rows}


def _select(
    *,
    now=NOW,
    observations=None,
    scheduled=frozenset({("Busan", "2026-08-09")}),
    research=frozenset(),
    bursts=frozenset(),
    state=None,
    post_invalidation_sec=300,
):
    return select_tokens(
        market_payload=_market_payload(),
        observations=observations or {},
        cities=["Busan"],
        now_utc=now,
        active_bracket_count=2,
        research_bracket_count=3,
        event_bracket_count=3,
        post_invalidation_sec=post_invalidation_sec,
        scheduled_keys=set(scheduled),
        research_keys=set(research),
        burst_keys=set(bursts),
        invalidation_state=state or {},
    )


def test_physically_invalid_lower_brackets_expire_after_markout_grace() -> None:
    before_cross = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 30.0,
        }
    }
    baseline = _select(observations=before_cross)
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    first = _select(
        now=NOW + timedelta(seconds=1),
        observations=observations,
        state=baseline.invalidation_state,
    )
    assert len(first.tokens) == 8
    assert first.grace_brackets["Busan"] == ["30", "31"]

    after_grace = _select(
        now=NOW + timedelta(seconds=302),
        observations=observations,
        state=first.invalidation_state,
    )
    assert len(after_grace.tokens) == 4
    assert after_grace.active_brackets["Busan"] == ["32", "33"]
    assert after_grace.grace_brackets["Busan"] == []


def test_already_invalid_on_start_is_not_subscribed() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(observations=observations)

    assert len(selected.tokens) == 4
    assert selected.active_brackets["Busan"] == ["32", "33"]
    assert selected.grace_brackets["Busan"] == []


def test_recent_source_event_opens_only_the_hot_strip() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(
        now=NOW + timedelta(seconds=301),
        observations=observations,
        scheduled=frozenset(),
        bursts={("Busan", "2026-08-09")},
        state={
            "Busan|2026-08-09|30": NOW.timestamp(),
            "Busan|2026-08-09|31": NOW.timestamp(),
        },
    )
    assert len(selected.tokens) == 6
    assert selected.burst_cities == ["Busan"]
    assert selected.active_brackets["Busan"] == ["32", "33", "34"]
    assert selected.grace_brackets["Busan"] == []


def test_sampled_research_window_adds_only_the_third_bracket() -> None:
    observations = {
        ("Busan", "2026-08-09"): {"status": "ok", "running_max_c": 32.0}
    }
    selected = _select(
        observations=observations,
        research={("Busan", "2026-08-09")},
    )

    assert len(selected.tokens) == 6
    assert selected.research_cities == ["Busan"]
    assert selected.active_brackets["Busan"] == ["32", "33", "34"]


def test_missing_settlement_facing_observation_fails_closed() -> None:
    selected = _select()
    assert len(selected.tokens) == 0
    assert selected.missing_observation_cities == ["Busan"]


def test_socket_is_idle_outside_scheduled_or_event_windows() -> None:
    observations = {
        ("Busan", "2026-08-09"): {"status": "ok", "running_max_c": 32.0}
    }
    selected = _select(observations=observations, scheduled=frozenset())

    assert selected.tokens == set()
    assert selected.active_brackets["Busan"] == []


def test_scheduled_report_window_uses_observation_cadence() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "last_obs_utc": "2026-08-09T02:30:00Z",
            "estimated_cadence_min": 30,
        }
    }
    active, research, next_reports = scheduled_report_windows(
        observations,
        now_utc=NOW - timedelta(seconds=30),
        before_sec=45,
        after_sec=120,
        extended_before_sec=125,
        research_sample_modulus=0,
    )

    assert active == {("Busan", "2026-08-09")}
    assert research == set()
    assert next_reports == {"Busan": "2026-08-09T03:00:00.000Z"}


def test_sampled_report_window_is_marked_for_three_brackets() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "last_obs_utc": "2026-08-09T02:30:00Z",
            "estimated_cadence_min": 30,
        }
    }
    active, research, _ = scheduled_report_windows(
        observations,
        now_utc=NOW - timedelta(seconds=100),
        before_sec=45,
        after_sec=120,
        extended_before_sec=125,
        research_sample_modulus=1,
    )

    assert active == {("Busan", "2026-08-09")}
    assert research == {("Busan", "2026-08-09")}


def test_source_event_cursor_ignores_revisions(tmp_path) -> None:
    path = tmp_path / "sources.jsonl"
    base = {
        "city": "Busan",
        "target_date": "2026-08-09",
        "information_event_status": "material",
        "material_state_change": True,
        "first_seen_at_utc": "2026-08-09T02:59:30Z",
    }
    rows = [
        {**base, "event_role": "revision", "information_event_id": "revision"},
        {**base, "event_role": "new_content", "information_event_id": "new"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    active = SourceEventCursor(path).read(
        cities={"Busan"}, now_utc=NOW, burst_sec=120
    )

    assert active == {("Busan", "2026-08-09")}


def test_source_event_cursor_follows_dated_shard_rollover(tmp_path) -> None:
    root = tmp_path / "source_events"
    first = root / "2026-08-09" / "sources.jsonl"
    second = root / "2026-08-10" / "sources.jsonl"
    first.parent.mkdir(parents=True)
    first.write_text(json.dumps({
        "city": "Busan",
        "target_date": "2026-08-09",
        "event_role": "new_content",
        "information_event_status": "material",
        "material_state_change": True,
        "first_seen_at_utc": "2026-08-09T02:59:30Z",
    }) + "\n")
    cursor = SourceEventCursor(root)
    assert cursor.read(cities={"Busan"}, now_utc=NOW, burst_sec=120) == {
        ("Busan", "2026-08-09")
    }

    second.parent.mkdir(parents=True)
    second.write_text(json.dumps({
        "city": "Tokyo",
        "target_date": "2026-08-10",
        "event_role": "new_content",
        "information_event_status": "material",
        "material_state_change": True,
        "first_seen_at_utc": "2026-08-09T03:01:30Z",
    }) + "\n")

    assert cursor.read(
        cities={"Tokyo"},
        now_utc=NOW + timedelta(minutes=2),
        burst_sec=120,
    ) == {
        ("Tokyo", "2026-08-10")
    }


def test_hourly_writer_uses_restart_safe_stream_file(tmp_path) -> None:
    writer = HourlyWriter(tmp_path)
    path = writer.write({"message": "one"}, NOW)
    writer.close()

    assert writer.stream_id in path.name
    assert path.suffix == ".jsonl"
    assert path.read_text(encoding="utf-8").strip() == '{"message":"one"}'


def test_d1_capture_demand_adds_revision_path_and_neighbors(tmp_path) -> None:
    path = tmp_path / "capture_demands.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "weather_market_capture_demand_v1",
                "capture_request_id": "request-1",
                "producer": "weather_data_feed_service.forecast_run_capture",
                "reason": "d1_provider_run_first_seen",
                "city": "Busan",
                "target_date": "2026-08-10",
                "requested_at_utc": "2026-08-09T02:59:00Z",
                "expires_at_utc": "2026-08-09T04:59:00Z",
                "max_token_count": 12,
                "ladder_scope": "revision_path_plus_one_neighbor_yes_no",
                "consensus_before_native": 32.0,
                "consensus_after_native": 33.0,
            }
        )
        + "\n"
    )
    demand = MarketCaptureDemandCursor(path).read(now_utc=NOW)
    selection = Selection(
        tokens=set(),
        token_rows={},
        city_token_counts={},
        active_brackets={},
        grace_brackets={},
        scheduled_cities=[],
        research_cities=[],
        burst_cities=[],
        missing_observation_cities=[],
        invalidation_state={},
    )
    future = _market_payload()
    for row in future["records"]:
        row["event_date"] = "2026-08-10"
    selected = apply_market_capture_demands(
        selection,
        market_payload=future,
        demands=demand,
        allowed_cities={"Busan"},
    )
    assert len(selected.tokens) == 8
    assert selected.city_token_counts == {"Busan": 8}
    assert selected.capture_demands[0]["resolution_status"] == "resolved_revision_strip"
    assert selected.capture_demands[0]["resolved_token_count"] == 8
    assert selected.capture_demands[0]["resolved_brackets"] == ["31", "32", "33", "34"]


def test_shared_direct_token_demand_uses_same_ws_owner(tmp_path) -> None:
    demand = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="dispute-token",
        reason="dispute_first_seen",
        priority="P0",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:09:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-1",
    )
    weather_path = tmp_path / "weather.jsonl"
    shared_path = tmp_path / "dispute.jsonl"
    weather_path.write_text("")
    shared_path.write_text(json.dumps(demand.to_dict()) + "\n")
    rows = MarketCaptureDemandCursor([weather_path, shared_path]).read(now_utc=NOW)
    selection = Selection(
        tokens=set(),
        token_rows={},
        city_token_counts={},
        active_brackets={},
        grace_brackets={},
        scheduled_cities=[],
        research_cities=[],
        burst_cities=[],
        missing_observation_cities=[],
        invalidation_state={},
    )
    selected = apply_market_capture_demands(
        selection,
        market_payload={"records": []},
        demands=rows,
    )
    assert selected.tokens == {"dispute-token"}
    assert selected.capture_demands[0]["resolution_status"] == "resolved_direct_token"
    assert selected.token_rows["dispute-token"]["capture_universe"] == "shared_direct_token"


def test_amsterdam_wcir_direct_token_demand_uses_same_ws_owner() -> None:
    demand = CaptureDemand.create(
        consumer_id="weather_amsterdam_wcir_frozen_v2",
        strategy_key="weather_amsterdam_wcir_frozen_v2",
        condition_id="condition-amsterdam-22",
        token_id="amsterdam-no-22",
        reason="next_print_previous_running_max",
        priority="P1",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T04:59:00Z",
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 5, 15, 30, 60, 120),
        trigger_event_id="amsterdam-decision-vintage-1",
        metadata={
            "city": "Amsterdam",
            "target_date": "2026-08-09",
            "bracket": "22",
            "side": "NO",
        },
    ).to_dict()
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )

    selected = apply_market_capture_demands(
        selection,
        market_payload={"records": []},
        demands=[demand],
    )

    assert selected.tokens == {"amsterdam-no-22"}
    assert selected.capture_demands[0]["resolution_status"] == "resolved_direct_token"
    assert selected.token_rows["amsterdam-no-22"]["condition_id"] == "condition-amsterdam-22"
    assert selected.token_rows["amsterdam-no-22"]["capture_universe"] == "shared_direct_token"


def test_weather_first_selective_maker_direct_token_demand_is_allowlisted() -> None:
    demand = CaptureDemand.create(
        consumer_id="weather_first_selective_maker_v2_1",
        strategy_key="weather_first_selective_maker_v2_1",
        condition_id="condition-amsterdam-22",
        token_id="amsterdam-yes-22",
        reason="v2_1_pit_route_clock_capture",
        priority="P1",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:09:00Z",
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 10, 30, 60, 120, 300),
        trigger_event_id="first-positive-1",
    ).to_dict()
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )

    selected = apply_market_capture_demands(
        selection,
        market_payload={"records": []},
        demands=[demand],
    )

    assert selected.tokens == {"amsterdam-yes-22"}
    assert selected.capture_demands[0]["resolution_status"] == "resolved_direct_token"
    assert selected.token_rows["amsterdam-yes-22"]["capture_demand_ids"] == [
        demand["demand_id"]
    ]


def test_metar_event_direct_token_demand_keeps_us_market_lineage() -> None:
    demand = CaptureDemand.create(
        consumer_id="weather_metar_ws_research",
        strategy_key="weather.metar_ws_event_repricing",
        condition_id="condition-miami-90",
        token_id="Miami-90-no",
        reason="metar_first_seen_market_repricing",
        priority="P0",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:04:30Z",
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 15, 30, 60, 120, 300),
        trigger_event_id="metar-event-1",
        metadata={
            "city": "Miami",
            "target_date": "2026-08-09",
            "bracket": "90",
            "side": "NO",
        },
    ).to_dict()
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )

    selected = apply_market_capture_demands(
        selection,
        market_payload=_market_payload(city="Miami"),
        demands=[demand],
    )

    assert selected.tokens == {"Miami-90-no"}
    row = selected.token_rows["Miami-90-no"]
    assert row["city"] == "Miami"
    assert row["event_date"] == "2026-08-09"
    assert row["bracket"] == "90"
    assert row["outcome"] == "no"
    assert row["strategy_key"] == "weather.metar_ws_event_repricing"


def test_core_carry_full_ladder_demand_is_allowed_by_shared_ws_owner() -> None:
    demand = CaptureDemand.create(
        consumer_id="current_yes_core_carry_tiny_live_v2",
        strategy_key="reheat_risk.current_yes",
        condition_id="condition-30",
        token_id="yes-30",
        reason="core_carry_first_positive_full_ladder_tape",
        priority="P0",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:29:00Z",
        desired_transport="WS",
        trigger_event_id="decision-packet-1",
    ).to_dict()
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )

    selected = apply_market_capture_demands(
        selection, market_payload={"records": []}, demands=[demand]
    )

    assert selected.tokens == {"yes-30"}
    assert selected.capture_demands[0]["resolution_status"] == "resolved_direct_token"
    assert selected.token_rows["yes-30"]["capture_universe"] == "shared_direct_token"


def test_core_carry_full_ladder_group_is_rejected_atomically_when_over_budget() -> None:
    demands = [
        CaptureDemand.create(
            consumer_id="current_yes_core_carry_tiny_live_v2",
            strategy_key="reheat_risk.current_yes",
            condition_id=f"condition-{index}",
            token_id=f"yes-{index}",
            reason="core_carry_first_positive_full_ladder_tape",
            priority="P0",
            requested_at_utc="2026-08-09T02:59:00Z",
            expires_at_utc="2026-08-09T03:29:00Z",
            desired_transport="WS",
            trigger_event_id="decision-packet-1",
        ).to_dict()
        for index in range(3)
    ]
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )

    selected = apply_market_capture_demands(
        selection,
        market_payload={"records": []},
        demands=demands,
        max_active_tokens=2,
    )

    assert selected.tokens == set()
    assert {row["resolution_status"] for row in selected.capture_demands} == {
        "global_active_token_budget_exceeded_atomic_group"
    }


def test_multiple_direct_demands_for_same_token_keep_all_lineage() -> None:
    base = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="dispute-token",
        reason="dispute_first_seen",
        priority="P0",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:09:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-1",
    ).to_dict()
    second = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="dispute-token",
        reason="official_update",
        priority="P0",
        requested_at_utc="2026-08-09T03:00:00Z",
        expires_at_utc="2026-08-09T03:10:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-2",
    ).to_dict()
    selection = Selection(
        tokens=set(), token_rows={}, city_token_counts={}, active_brackets={},
        grace_brackets={}, scheduled_cities=[], research_cities=[], burst_cities=[],
        missing_observation_cities=[], invalidation_state={},
    )
    selected = apply_market_capture_demands(
        selection, market_payload={"records": []}, demands=[base, second]
    )
    assert all(
        row["resolution_status"] == "resolved_direct_token"
        for row in selected.capture_demands
    )
    assert selected.token_rows["dispute-token"]["capture_demand_ids"] == sorted(
        [base["demand_id"], second["demand_id"]]
    )


def test_capture_demand_cursor_does_not_consume_partial_json_line(tmp_path) -> None:
    demand = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="token-1",
        reason="dispute_first_seen",
        priority="P0",
        requested_at_utc="2026-08-09T02:59:00Z",
        expires_at_utc="2026-08-09T03:09:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-1",
    ).to_dict()
    encoded = json.dumps(demand)
    path = tmp_path / "demands.jsonl"
    path.write_text(encoded[: len(encoded) // 2])
    cursor = MarketCaptureDemandCursor(path)
    assert cursor.read(now_utc=NOW) == []
    with path.open("a") as handle:
        handle.write(encoded[len(encoded) // 2 :] + "\n")
    assert [row["demand_id"] for row in cursor.read(now_utc=NOW)] == [demand["demand_id"]]


def test_capture_demand_rejects_untrusted_or_over_budget_request() -> None:
    base = {
        "schema_version": "weather_market_capture_demand_v1",
        "capture_request_id": "request-1",
        "producer": "weather_data_feed_service.forecast_run_capture",
        "reason": "d1_provider_run_first_seen",
        "city": "Busan",
        "target_date": "2026-08-09",
        "requested_at_utc": "2026-08-09T02:59:00Z",
        "expires_at_utc": "2026-08-09T04:59:00Z",
        "max_token_count": 12,
        "ladder_scope": "revision_path_plus_one_neighbor_yes_no",
        "consensus_before_native": 32.0,
        "consensus_after_native": 33.0,
    }
    selection = Selection(
        tokens=set(),
        token_rows={},
        city_token_counts={},
        active_brackets={},
        grace_brackets={},
        scheduled_cities=[],
        research_cities=[],
        burst_cities=[],
        missing_observation_cities=[],
        invalidation_state={},
    )
    rejected = apply_market_capture_demands(
        selection,
        market_payload=_market_payload(),
        demands=[{**base, "producer": "unknown"}],
        allowed_cities={"Busan"},
    )
    assert rejected.capture_demands[0]["resolution_status"] == "invalid_capture_demand_contract"
    capped = apply_market_capture_demands(
        selection,
        market_payload=_market_payload(),
        demands=[base],
        allowed_cities={"Busan"},
        max_active_tokens=6,
    )
    assert capped.capture_demands[0]["resolution_status"] == "global_active_token_budget_exceeded"


def test_collector_publishes_append_only_subscription_epoch_lineage(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--market-books-latest",
            str(tmp_path / "latest.json"),
            "--observation-cache",
            str(tmp_path / "observations.json"),
            "--source-events-jsonl",
            str(tmp_path / "sources.jsonl"),
            "--output-root",
            str(tmp_path / "ws"),
            "--health-path",
            str(tmp_path / "health.json"),
        ]
    )
    collector = Collector(args)
    collector.selection = Selection(
        tokens={"yes-token"},
        token_rows={
            "yes-token": {
                "city": "Helsinki",
                "event_date": "2026-08-09",
                "bracket": "22",
                "outcome": "yes",
                "condition_id": "condition-22",
            }
        },
        city_token_counts={"Helsinki": 1},
        active_brackets={"Helsinki": ["22"]},
        grace_brackets={"Helsinki": []},
        scheduled_cities=["Helsinki"],
        research_cities=[],
        burst_cities=[],
        missing_observation_cities=[],
        invalidation_state={},
    )
    first = collector.publish_subscription_epoch(
        {"yes-token"}, NOW, reason="connect"
    )
    second = collector.publish_subscription_epoch(
        {"yes-token"}, NOW + timedelta(seconds=5), reason="selector_reconcile"
    )

    path = tmp_path / "ws" / "subscription_epochs" / "subscription_epochs_2026-08-09.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [first, second]
    assert first["schema_version"] == "weather_market_books_ws_subscription_epoch_v2"
    assert isinstance(first["started_wall_ns"], int)
    assert isinstance(first["started_monotonic_ns"], int)
    assert first["token_map_id"]
    assert first["capture_policy_id"]
    assert first["subscription_set_id"]
    assert second["previous_subscription_epoch_id"] == first["subscription_epoch_id"]
    assert first["capture_policy"]["execution_evidence"]["enabled"] is True
    assert collector.execution_evidence.engine.epoch_id == second["subscription_epoch_id"]
    collector.execution_evidence.close()


def test_collector_health_states_public_market_evidence_semantics(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--market-books-latest", str(tmp_path / "latest.json"),
            "--observation-cache", str(tmp_path / "observations.json"),
            "--source-events-jsonl", str(tmp_path / "sources.jsonl"),
            "--output-root", str(tmp_path / "ws"),
            "--health-path", str(tmp_path / "health.json"),
        ]
    )
    collector = Collector(args)

    collector.publish_health(NOW)

    health = json.loads((tmp_path / "health.json").read_text())
    assert health["execution_evidence"]["semantics"] == {
        "public_trade_print": "exchange match not own fill",
        "public_book": "quote state not queue or execution",
        "execution_evidence": "requires decision plus private order lifecycle join",
    }
    collector.execution_evidence.close()


def test_derived_evidence_fault_does_not_rollback_raw_epoch(
    tmp_path, monkeypatch
) -> None:
    args = build_parser().parse_args(
        [
            "--market-books-latest", str(tmp_path / "latest.json"),
            "--observation-cache", str(tmp_path / "observations.json"),
            "--source-events-jsonl", str(tmp_path / "sources.jsonl"),
            "--output-root", str(tmp_path / "ws"),
            "--health-path", str(tmp_path / "health.json"),
        ]
    )
    collector = Collector(args)
    collector.selection = Selection(
        tokens={"yes-token"},
        token_rows={"yes-token": {"city": "Helsinki"}},
        city_token_counts={"Helsinki": 1},
        active_brackets={"Helsinki": ["22"]},
        grace_brackets={"Helsinki": []},
        scheduled_cities=["Helsinki"],
        research_cities=[],
        burst_cities=[],
        missing_observation_cities=[],
        invalidation_state={},
    )

    def fail_derived(_payload) -> None:
        raise RuntimeError("derived writer unavailable")

    monkeypatch.setattr(collector.execution_evidence, "activate_epoch", fail_derived)
    payload = collector.publish_subscription_epoch(
        {"yes-token"}, NOW, reason="connect"
    )

    epoch_path = (
        tmp_path
        / "ws"
        / "subscription_epochs"
        / "subscription_epochs_2026-08-09.jsonl"
    )
    assert json.loads(epoch_path.read_text()) == payload
    assert collector.subscription_epoch_id == payload["subscription_epoch_id"]
    assert collector.execution_evidence.integration_errors == 1
    assert "derived writer unavailable" in (
        collector.execution_evidence.last_integration_error or ""
    )
    collector.execution_evidence.close()
