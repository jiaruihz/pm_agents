from datetime import datetime, timezone
from decimal import Decimal

from src.strategies.weather_edge_v1.execution.contracts import (
    EXECUTION_SCHEMA_VERSION,
    BookLevel,
    ChildOrderPlan,
    ExecutionConstraints,
    ExecutionIntent,
    ExecutionRunContext,
    FeeSchedule,
    LifecycleContext,
    MarketBook,
    RestingOrderState,
    VenueCapabilities,
    make_execution_config_id,
    make_live_exposure_key,
    make_plan_dedupe_key,
)
from src.strategies.weather_edge_v1.runtime.execution_journal import JsonlExecutionJournal
from src.strategies.weather_edge_v1.runtime.order_runtime import LifecycleWorkItem, OrderRuntime, append_jsonl


def _book():
    return MarketBook(
        token_id="token-1",
        status="ok",
        fetched_at_utc="2026-07-26T08:00:00Z",
        venue_timestamp_utc="2026-07-26T08:00:00Z",
        book_epoch_ref="book-runtime-v1",
        tick_size="0.001",
        tick_size_source="fake-venue",
        minimum_order_shares="1",
        bids=(BookLevel("0.904", "20"),),
        asks=(BookLevel("0.910", "20"),),
    )


def _capabilities():
    return VenueCapabilities(
        venue="fake-clob",
        protocol_version="fake-v1",
        client_version="fake-client-v1",
        collateral_asset="USDC",
        supported_order_types=("GTC", "GTD"),
        post_only_order_types=("GTD",),
        price_precision=3,
        size_precision=3,
        amount_precision_by_order_type={"GTC": 3, "GTD": 3},
        gtd_security_threshold_sec=60,
        capabilities_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_schedule_ref="fake-fees-v1",
    )


def _fees():
    return FeeSchedule(
        venue="fake-clob",
        fee_schedule_ref="fake-fees-v1",
        fee_schedule_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_formula_id="fake-formula-v1",
    )


def _intent(*, profile="taker_now_v1", opportunity="opportunity-runtime-1", shares="5"):
    fixed_behavior = {"allocation_policy": "runtime-test", "tick_model_version": "fake-v1"}
    return ExecutionIntent(
        execution_schema_version=EXECUTION_SCHEMA_VERSION,
        signal_id="signal-runtime-1",
        opportunity_id=opportunity,
        comparison_group_id="comparison-runtime-1",
        strategy_id="weather-edge",
        strategy_instance="weather-edge-phase4-test",
        config_id="strategy-config-runtime-v1",
        execution_profile=profile,
        resolved_execution_profile=profile,
        execution_config_id=make_execution_config_id(resolved_execution_profile=profile, fixed_behavior=fixed_behavior),
        plan_dedupe_key=make_plan_dedupe_key(
            strategy_id="weather-edge",
            strategy_instance="weather-edge-phase4-test",
            config_id="strategy-config-runtime-v1",
            opportunity_id=opportunity,
            execution_profile=profile,
            child_role="single",
        ),
        live_exposure_key=make_live_exposure_key(
            authorized_scope="weather-edge-test",
            opportunity_id=opportunity,
            token_id="token-1",
            venue_side="BUY",
            outcome_side="YES",
        ),
        token_id="token-1",
        venue_side="BUY",
        outcome_side="YES",
        signal_side="YES",
        total_shares=shares,
        created_at_utc="2026-07-26T08:00:00Z",
        constraints=ExecutionConstraints(price_cap="0.915", minimum_shares="1"),
    )


def _child(intent, *, role="single", shares=None, maker_only=False, lifecycle="taker_now"):
    return ChildOrderPlan(
        intent=intent,
        child_role=role,
        requested_shares=shares or intent.total_shares,
        execution_policy="fake-maker" if maker_only else "fake-taker",
        order_lifecycle_policy=lifecycle,
        maker_only=maker_only,
    )


def _run_context():
    return ExecutionRunContext(
        run_id="phase4-paper-run",
        execution_mode="paper",
        run_purpose="shadow",
        dry_run=False,
        confirm_live=False,
        pause_state="paused",
        authorization_ref=None,
        runtime_owner="phase4-owner",
        code_commit="phase4-test",
        invoked_at_utc="2026-07-26T08:00:00Z",
    )


def _order(*, status="live", matched="4", remaining="6", owner="phase4-owner", profile="d1_taker_plus_maker_chase_to_mid_v1", policy="d1_yes_high_mid_maker_v1", lifecycle="maker_until_data_update", cancel_confirmed=False):
    return RestingOrderState(
        order_id="venue-order-1",
        client_order_id="client-order-1",
        expected_venue_order_id="venue-order-1",
        root_order_id="root-order-1",
        source_order_id="source-order-1",
        plan_id="plan-runtime-1",
        token_id="token-1",
        venue_side="BUY",
        outcome_side="YES",
        requested_shares="10",
        matched_shares=matched,
        remaining_shares=remaining,
        posted_price="0.900",
        status=status,
        created_at_utc="2026-07-26T07:50:00Z",
        maker_only=True,
        execution_profile=profile,
        execution_policy=policy,
        order_lifecycle_policy=lifecycle,
        reprice_count=0,
        data_epoch_ref="metar:old",
        authoritative_state_version="authoritative-v1",
        lifecycle_owner=owner,
        cancel_confirmed=cancel_confirmed,
    )


class FakeVenue:
    def __init__(self, *, current_order=None, final_order=None, place_outcomes=None, cancel_outcomes=None, reconciliations=None):
        self.book = _book()
        self.current_order = current_order
        self.final_order = final_order
        self.place_outcomes = list(place_outcomes or [{"status": "submitted", "venue_order_id": "new-order-1"}])
        self.cancel_outcomes = list(cancel_outcomes or [{"status": "cancelled"}])
        self.reconciliations = dict(reconciliations or {})
        self.place_calls = []
        self.cancel_calls = []
        self.reconcile_calls = []
        self.fetch_counts = {"book": 0, "capabilities": 0, "fees": 0, "order": 0}

    def fetch_market_book(self, token_id):
        assert token_id == "token-1"
        self.fetch_counts["book"] += 1
        return self.book

    def fetch_capabilities(self):
        self.fetch_counts["capabilities"] += 1
        return _capabilities()

    def fetch_fee_schedule(self):
        self.fetch_counts["fees"] += 1
        return _fees()

    def place(self, **kwargs):
        self.place_calls.append(kwargs)
        return self.place_outcomes.pop(0) if self.place_outcomes else {"status": "submitted"}

    def cancel(self, order_state):
        self.cancel_calls.append(order_state)
        if self.final_order is not None:
            self.current_order = self.final_order
        return self.cancel_outcomes.pop(0) if self.cancel_outcomes else {"status": "cancelled"}

    def fetch_order_state(self, order_id, client_order_id):
        self.fetch_counts["order"] += 1
        return self.current_order

    def reconcile_unknown(self, *, kind, identity_key):
        self.reconcile_calls.append((kind, identity_key))
        return self.reconciliations.get(kind)


class FakeRisk:
    def __init__(self, *, rejected_stages=()):
        self.rejected_stages = set(rejected_stages)
        self.calls = []

    def check(self, *, stage, payload):
        self.calls.append((stage, payload))
        return stage not in self.rejected_stages


def _runtime(tmp_path, *, venue, risk=None, planner=None, replacement_planner=None, writer="writer-a"):
    return OrderRuntime(
        venue=venue,
        risk=risk or FakeRisk(),
        journal=JsonlExecutionJournal(tmp_path / "execution.jsonl", writer_id=writer),
        planner=planner or (lambda intent, profile, book, capabilities, fees, now: [_child(intent)]),
        replacement_planner=replacement_planner,
    )


def test_submit_intent_uses_gate_claims_and_fresh_injected_snapshots(tmp_path):
    venue = FakeVenue()
    risk = FakeRisk()
    runtime = _runtime(tmp_path, venue=venue, risk=risk)

    result = runtime.submit_intent(_intent(), _run_context())

    assert result.status == "ok"
    assert [action.status for action in result.actions] == ["submitted"]
    assert [stage for stage, _ in risk.calls] == ["initial_aggregate", "initial_child"]
    assert venue.fetch_counts["capabilities"] == venue.fetch_counts["fees"] == venue.fetch_counts["book"] == 2
    assert len(venue.place_calls) == 1
    rows = runtime.journal.read_rows()
    assert [row["event_type"] for row in rows] == ["plan_claimed", "live_exposure_reserved", "attempt_before_side_effect", "outcome"]
    assert all("http" not in str(row).lower() for row in rows)


def test_submit_preplanned_uses_shared_claim_risk_and_attempt_order(tmp_path):
    venue = FakeVenue()
    risk = FakeRisk()
    runtime = _runtime(tmp_path, venue=venue, risk=risk)
    intent = _intent()
    child = _child(intent)

    first = runtime.submit_preplanned(intent, child, _run_context())
    second = runtime.submit_preplanned(intent, child, _run_context())

    assert [action.status for action in first.actions] == ["submitted"]
    assert [stage for stage, _ in risk.calls] == [
        "initial_aggregate",
        "initial_child",
        "initial_aggregate",
    ]
    assert second.actions[0].reason == "plan_dedupe_claim_not_acquired"
    assert len(venue.place_calls) == 1


def test_run_context_gate_happens_before_any_fetch_or_claim(tmp_path):
    class BlockedContext:
        def validate_submission(self):
            raise ValueError("paused")

    venue = FakeVenue()
    runtime = _runtime(tmp_path, venue=venue)
    result = runtime.submit_intent(_intent(), BlockedContext())

    assert result.status == "blocked"
    assert result.actions[0].reason == "run_context_gate:paused"
    assert venue.fetch_counts == {"book": 0, "capabilities": 0, "fees": 0, "order": 0}
    assert runtime.journal.read_rows() == []


def test_aggregate_risk_sees_children_and_prior_open_or_reserved_root(tmp_path):
    venue = FakeVenue()
    risk = FakeRisk(rejected_stages={"initial_aggregate"})
    intent = _intent()
    runtime = _runtime(
        tmp_path,
        venue=venue,
        risk=risk,
        planner=lambda *_: [_child(intent, role="first", shares="2"), _child(intent, role="second", shares="3")],
    )
    runtime.journal.reserve_live_exposure("existing-root", "prior-owner", {"root_order_id": "root-open-1"})

    result = runtime.submit_intent(intent, _run_context())

    assert result.status == "blocked"
    aggregate = risk.calls[0][1]
    assert len(aggregate["children"]) == 2
    assert aggregate["open_or_reserved_exposure"][0]["payload"]["root_order_id"] == "root-open-1"
    assert venue.place_calls == []


def test_unknown_submit_reconciles_before_one_explicit_retry_claim(tmp_path):
    venue = FakeVenue(
        place_outcomes=[{"status": "unknown"}, {"status": "submitted", "venue_order_id": "retry-order"}],
        reconciliations={"submit": {"status": "not_found"}},
    )
    runtime = _runtime(tmp_path, venue=venue)
    first = runtime.submit_intent(_intent(), _run_context())
    second = runtime.submit_intent(_intent(), _run_context())
    third = runtime.submit_intent(_intent(), _run_context())

    assert first.actions[0].reason == "unknown_submit_reconciled_retry_permitted"
    assert second.actions[0].status == "submitted"
    assert third.actions[0].reason == "plan_dedupe_claim_not_acquired"
    assert len(venue.place_calls) == 2
    assert len(venue.reconcile_calls) == 1
    assert any(row["event_type"] == "plan_claimed_retry_claimed" for row in runtime.journal.read_rows())


def test_lifecycle_replacement_rechecks_risk_and_uses_authoritative_remaining(tmp_path):
    initial = _order()
    final = _order(status="cancelled", cancel_confirmed=True)
    venue = FakeVenue(current_order=initial, final_order=final)
    replacement_intent = _intent(profile="d1_taker_plus_maker_chase_to_mid_v1", opportunity="replacement-opportunity", shares="6")
    runtime = _runtime(
        tmp_path,
        venue=venue,
        replacement_planner=lambda order, decision, remaining: (
            replacement_intent,
            _child(replacement_intent, role="maker", shares=remaining, maker_only=True, lifecycle="maker_until_data_update"),
        ),
    )
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        thesis_valid=True,
        token_unchanged=True,
        book_fresh=True,
        price_cap_valid=True,
        depth_valid=True,
        fee_adjusted_edge_valid=True,
        maker_price_cap="0.908",
    )

    result = runtime.manage_active_orders("phase4-owner", [LifecycleWorkItem(initial, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))

    assert result.actions[0].action == "REPRICE_MAKER"
    assert result.actions[0].status == "submitted"
    assert len(venue.cancel_calls) == len(venue.place_calls) == 1
    assert venue.place_calls[0]["child"].requested_shares == Decimal("6")
    assert venue.place_calls[0]["replacement_of"].status == "cancelled"
    assert any(stage == "replacement" for stage, _ in runtime.risk.calls)


def test_reprice_blocks_failed_or_rejected_cancel_before_planning_or_submit(tmp_path):
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        thesis_valid=True,
        token_unchanged=True,
        book_fresh=True,
        price_cap_valid=True,
        maker_price_cap="0.908",
    )
    for status in ("failed", "rejected"):
        initial = _order()
        venue = FakeVenue(current_order=initial, cancel_outcomes=[{"status": status}])
        planner_calls = []
        def replacement_planner(*args):
            planner_calls.append(args)
            return _intent(), _child(_intent())
        runtime = _runtime(
            tmp_path / status,
            venue=venue,
            replacement_planner=replacement_planner,
        )

        result = runtime.manage_active_orders("phase4-owner", [LifecycleWorkItem(initial, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))

        assert result.actions[0].reason == "cancel_before_replacement_not_final"
        assert len(venue.cancel_calls) == 1
        assert venue.place_calls == []
        assert venue.fetch_counts["order"] == 1
        assert planner_calls == []


def test_reprice_blocks_cancel_response_when_authoritative_state_is_still_live(tmp_path):
    initial = _order()
    venue = FakeVenue(current_order=initial, cancel_outcomes=[{"status": "cancelled"}])
    planner_calls = []
    def replacement_planner(*args):
        planner_calls.append(args)
        return _intent(), _child(_intent())
    runtime = _runtime(
        tmp_path,
        venue=venue,
        replacement_planner=replacement_planner,
    )
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        thesis_valid=True,
        token_unchanged=True,
        book_fresh=True,
        price_cap_valid=True,
        maker_price_cap="0.908",
    )

    result = runtime.manage_active_orders("phase4-owner", [LifecycleWorkItem(initial, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))

    assert result.actions[0].reason == "replacement_requires_authoritative_final_cancel"
    assert len(venue.cancel_calls) == 1
    assert venue.fetch_counts["order"] == 2
    assert venue.place_calls == []
    assert planner_calls == []


def test_cancel_final_taker_fallback_rechecks_risk_before_injected_submit(tmp_path):
    final = _order(
        status="cancelled",
        cancel_confirmed=True,
        profile="split_taker_maker_chase_v1",
        policy="current_yes_heat_death_maker_probe_v1",
        lifecycle="maker_chase_then_taker_fallback_v1",
    )
    venue = FakeVenue(current_order=final)
    fallback_intent = _intent(profile="split_taker_maker_chase_v1", opportunity="fallback-opportunity", shares="6")
    risk = FakeRisk()
    runtime = _runtime(
        tmp_path,
        venue=venue,
        risk=risk,
        replacement_planner=lambda order, decision, remaining: (
            fallback_intent,
            _child(fallback_intent, role="fallback", shares=remaining, lifecycle="taker_now"),
        ),
    )
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        deadline_utc="2026-07-26T07:59:00Z",
        thesis_valid=True,
        token_unchanged=True,
        book_fresh=True,
        price_cap_valid=True,
        depth_valid=True,
        fee_adjusted_edge_valid=True,
        taker_price_cap="0.915",
    )

    result = runtime.manage_active_orders("phase4-owner", [LifecycleWorkItem(final, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))

    assert result.actions[0].action == "TAKER_FALLBACK"
    assert result.actions[0].status == "submitted"
    assert venue.cancel_calls == []
    assert len(venue.place_calls) == 1
    assert any(stage == "replacement" for stage, _ in risk.calls)


def test_lifecycle_owner_mismatch_and_action_claim_race_prevent_side_effects(tmp_path):
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        thesis_valid=True,
        token_unchanged=True,
        book_fresh=True,
        price_cap_valid=True,
        maker_price_cap="0.908",
    )
    mismatched_venue = FakeVenue(current_order=_order(owner="other-owner"))
    mismatched = _runtime(tmp_path, venue=mismatched_venue).manage_active_orders("phase4-owner", [LifecycleWorkItem(_order(owner="other-owner"), context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))
    assert mismatched.actions[0].reason == "lifecycle_owner_mismatch"
    assert mismatched_venue.cancel_calls == mismatched_venue.place_calls == []

    initial = _order()
    final = _order(status="cancelled", cancel_confirmed=True)
    venue_a = FakeVenue(current_order=initial, final_order=final)
    replacement_intent = _intent(profile="d1_taker_plus_maker_chase_to_mid_v1", opportunity="race-replacement", shares="6")
    replacement_planner = lambda order, decision, remaining: (replacement_intent, _child(replacement_intent, role="maker", shares=remaining, maker_only=True, lifecycle="maker_until_data_update"))
    first = _runtime(tmp_path, venue=venue_a, replacement_planner=replacement_planner, writer="writer-a")
    first.manage_active_orders("phase4-owner", [LifecycleWorkItem(initial, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))
    venue_b = FakeVenue(current_order=initial, final_order=final)
    second = _runtime(tmp_path, venue=venue_b, replacement_planner=replacement_planner, writer="writer-b")
    replay = second.manage_active_orders("phase4-owner", [LifecycleWorkItem(initial, context)], _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))
    assert replay.actions[0].reason == "lifecycle_action_claim_not_acquired"
    assert venue_b.cancel_calls == venue_b.place_calls == []


def test_unknown_cancel_requires_reconcile_and_allows_one_claimed_retry(tmp_path):
    initial = _order(profile="d1_taker_plus_maker_static_v1", policy="d1_yes_high_mid_maker_v1")
    venue = FakeVenue(
        current_order=initial,
        cancel_outcomes=[{"status": "unknown"}, {"status": "cancelled"}],
        reconciliations={"cancel": {"status": "not_found"}},
    )
    runtime = _runtime(tmp_path, venue=venue)
    context = LifecycleContext(
        now_utc="2026-07-26T08:00:00Z",
        data_epoch_ref="metar:old",
        lifecycle_owner="phase4-owner",
        deadline_utc="2026-07-26T07:59:00Z",
    )
    work = [LifecycleWorkItem(initial, context)]
    first = runtime.manage_active_orders("phase4-owner", work, _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))
    second = runtime.manage_active_orders("phase4-owner", work, _run_context(), datetime(2026, 7, 26, 8, tzinfo=timezone.utc))

    assert first.actions[0].reason == "unknown_cancel_reconciled_retry_permitted"
    assert second.actions[0].status == "cancelled"
    assert len(venue.cancel_calls) == 2
    assert len(venue.reconcile_calls) == 1


def test_journal_reads_legacy_rows_and_locks_claims_across_restart(tmp_path):
    path = tmp_path / "execution.jsonl"
    append_jsonl(path, {"root_order_id": "legacy-root", "source_order_id": "legacy-source", "status": "legacy"})
    first = JsonlExecutionJournal(path, writer_id="writer-a")
    second = JsonlExecutionJournal(path, writer_id="writer-b")

    assert first.claim_plan("plan-1", "writer-a") is True
    assert second.claim_plan("plan-1", "writer-b") is False
    assert first.reserve_live_exposure("exposure-1", "writer-a", {"root_order_id": "root-1"}) is True
    first.record_outcome({"identity_key": "new-row", "root_order_id": "new-root", "source_order_id": "new-source", "status": "submitted"})
    assert {row["event_type"] for row in first.lookup_order_chain(root_order_id="legacy-root")} == {"legacy"}
    assert len(first.lookup_order_chain(source_order_id="legacy-source")) == 1
    assert len(first.lookup_order_chain(root_order_id="new-root")) == 1
    assert len(first.lookup_order_chain(source_order_id="new-source")) == 1
    assert first.open_or_reserved_exposure()[0]["claim_key"] == "exposure-1"
