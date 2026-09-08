import json
import pytest
from datetime import datetime, timezone

from scripts.ops import weather_core_carry_order_runtime as shared
from scripts.ops import weather_current_yes_core_carry_tiny_live_v2 as runner
from src.strategies.weather_edge_v1.execution.contracts import RestingOrderState


class FakeTransport:
    def __init__(self, *, post_responses=None):
        self.posts = []
        self.cancels = []
        self.post_responses = list(post_responses or [])

    def fetch_capabilities(self):
        return {
            "venue": "polymarket_clob",
            "protocol_version": "test",
            "client_version": "test",
            "collateral_asset": "USDC",
            "supported_order_types": ("GTC",),
            "post_only_order_types": ("GTC",),
            "price_precision": 3,
            "size_precision": 3,
            "amount_precision_by_order_type": {"GTC": 8},
            "gtd_security_threshold_sec": 60,
            "capabilities_fetched_at_utc": "2026-07-28T08:00:00Z",
            "fee_schedule_ref": "fees",
        }

    def fetch_fee_schedule(self):
        return {
            "venue": "polymarket_clob",
            "fee_schedule_ref": "fees",
            "fee_schedule_fetched_at_utc": "2026-07-28T08:00:00Z",
            "fee_formula_id": "test",
            "taker_fee_parameters": {"rate": "0"},
            "maker_fee_parameters": {"rate": "0", "rebate_rate": "0"},
        }

    def fetch_fee_schedule_for_token(self, _token_id):
        return self.fetch_fee_schedule()

    def fetch_instrument(self, _token_id):
        return {
            "tick_size": "0.01",
            "minimum_order_shares": "5",
            "tick_size_source": "test",
            "instrument_version": "test",
        }

    def fetch_market_book(self, _token_id):
        return {
            "status": "ok",
            "fetched_at_utc": "2026-07-28T08:00:00Z",
            "sequence": "book-1",
            "bids": [{"price": "0.80", "size": "20"}],
            "asks": [{"price": "0.84", "size": "20"}],
        }

    def create_order(self, payload):
        return {
            "expected_venue_order_id": f"expected-{len(self.posts)}",
            **dict(payload),
        }

    def post_order(self, signed, *, order_type, post_only):
        order_id = f"order-{len(self.posts) + 1}"
        self.posts.append((dict(signed), order_type, post_only))
        if self.post_responses:
            return self.post_responses.pop(0)
        return {"status": "live", "orderID": order_id}

    def cancel_order(self, _order_id):
        self.cancels.append(_order_id)
        return {"status": "cancelled"}

    def fetch_order(self, _order_id, _client_order_id):
        return None

    def reconcile_unknown(self, *, kind, identity_key):
        return None


def _score():
    return {
        "city": "Busan",
        "target_date": "2026-07-28",
        "token_id": "token-1",
        "condition_id": "condition-1",
        "current_bracket": "30",
        "current_yes_bid": 0.80,
        "current_yes_ask": 0.84,
        "current_yes_tick_size": 0.01,
        "model_probability_hold": 0.91,
        "checkpoint_key": "Busan|2026-07-28|13",
        "decision_snapshot_ts_utc": "2026-07-28T04:30:00Z",
        "source_report_ts_utc": "2026-07-28T04:20:00Z",
        "observation_cadence_min": 30.0,
        "artifact_hash": "hash",
    }


def test_core_carry_live_submits_each_child_once_through_shared_runtime(
    tmp_path,
    monkeypatch,
):
    plans = runner.build_entry_plans(
        _score(),
        live_enabled=True,
        now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    transport = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (transport, {"mode": "test"}),
    )

    result = shared.execute_core_carry_plans(
        plans=plans,
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )

    assert result["execution_runtime"] == "OrderRuntime"
    assert result["venue_adapter"] == "PolymarketVenueAdapter"
    assert result["live_written"] == 2
    assert result["live_errors"] == 0
    assert len(transport.posts) == 2
    assert [post_only for _signed, _kind, post_only in transport.posts] == [
        False,
        True,
    ]
    live_rows = [
        json.loads(line)
        for line in (tmp_path / "live_orders.jsonl").read_text().splitlines()
    ]
    assert len(live_rows) == 2
    assert all(row["execution_schema_version"] for row in live_rows)
    assert all(row["client_order_id"] for row in live_rows)
    journal_rows = [
        json.loads(line)
        for line in (tmp_path / "execution_journal.jsonl").read_text().splitlines()
    ]
    assert [row["event_type"] for row in journal_rows].count(
        "attempt_before_side_effect"
    ) == 2


def test_journaled_submit_is_projected_after_restart_without_resubmission(
    tmp_path,
    monkeypatch,
):
    plan = runner.build_entry_plans(
        _score(),
        live_enabled=True,
        now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )[0]
    identity_key = f"{plan['plan_dedupe_key']}:taker"
    journal = shared.JsonlExecutionJournal(
        tmp_path / "execution_journal.jsonl",
        writer_id=shared.RUNTIME_OWNER,
    )
    journal.record_attempt(
        {"identity_key": identity_key, "kind": "submit", "child_role": "taker"}
    )
    journal.record_outcome(
        {
            "identity_key": identity_key,
            "live_exposure_key": f"{plan['live_exposure_key']}:taker",
            "kind": "submit",
            "status": "submitted",
            "venue_order_id": "recovered-order",
            "posted_price": "0.84",
            "requested_price": "0.84",
            "requested_shares": "5",
            "raw_response": {
                "status": "matched",
                "orderID": "recovered-order",
                "success": True,
            },
        }
    )
    transport = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (transport, {"mode": "test"}),
    )

    result = shared.execute_core_carry_plans(
        plans=[plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )

    assert result["live_errors"] == 0
    assert result["live_written"] == 1
    assert transport.posts == []
    row = json.loads((tmp_path / "live_orders.jsonl").read_text().splitlines()[-1])
    assert row["venue_order_id"] == "recovered-order"


def test_attempt_without_outcome_requires_reconciliation_and_never_resubmits(
    tmp_path,
    monkeypatch,
):
    plan = runner.build_entry_plans(
        _score(),
        live_enabled=True,
        now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )[0]
    journal = shared.JsonlExecutionJournal(
        tmp_path / "execution_journal.jsonl",
        writer_id=shared.RUNTIME_OWNER,
    )
    journal.record_attempt(
        {
            "identity_key": f"{plan['plan_dedupe_key']}:taker",
            "kind": "submit",
            "child_role": "taker",
        }
    )
    transport = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (transport, {"mode": "test"}),
    )

    result = shared.execute_core_carry_plans(
        plans=[plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )

    assert result["live_errors"] == 1
    assert result["reconciliation_required"] == 1
    assert result["live_written"] == 0
    assert transport.posts == []


def test_known_post_only_rejection_can_repost_without_a_retry_cap(
    tmp_path,
    monkeypatch,
):
    maker_plan = next(
        plan
        for plan in runner.build_entry_plans(
            _score(),
            live_enabled=True,
            now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
            taker_shares=5,
            maker_shares=5,
            order_ttl_min=15,
        )
        if plan["child_order_role"] == "maker"
    )
    rejected = FakeTransport(
        post_responses=[
            {
                "status": "error",
                "error": "invalid post-only order crosses book",
            }
        ]
    )
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (rejected, {"mode": "test"}),
    )
    first = shared.execute_core_carry_plans(
        plans=[maker_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )
    failed_row = json.loads(
        (tmp_path / "live_orders.jsonl").read_text().splitlines()[-1]
    )
    assert first["live_errors"] == 1
    assert (
        failed_row["exchange_response"]["error_classification"]
        == "post_only_crosses_book"
    )

    repost_plan = runner.build_maker_lifecycle_plan(
        failed_row,
        action="core_carry_maker_repost",
        limit_price=0.81,
        cancel_only=False,
        cancel_source_order=False,
        now=datetime(2026, 7, 28, 4, 32, tzinfo=timezone.utc),
        live_enabled=True,
    )
    accepted = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (accepted, {"mode": "test"}),
    )
    second = shared.execute_core_carry_plans(
        plans=[repost_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )

    assert second["live_errors"] == 0
    assert second["live_written"] == 1
    assert len(accepted.posts) == 1


def test_terminal_maker_is_projected_once_and_removed_from_lifecycle_heads(
    tmp_path,
    monkeypatch,
):
    maker_plan = next(
        plan
        for plan in runner.build_entry_plans(
            _score(),
            live_enabled=True,
            now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
            taker_shares=5,
            maker_shares=5,
            order_ttl_min=15,
        )
        if plan["child_order_role"] == "maker"
    )
    transport = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (transport, {"mode": "test"}),
    )
    shared.execute_core_carry_plans(
        plans=[maker_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )
    submitted = json.loads(
        (tmp_path / "live_orders.jsonl").read_text().splitlines()[-1]
    )
    lifecycle_plan = runner.build_maker_lifecycle_plan(
        submitted,
        action="core_carry_maker_cancel_ttl",
        limit_price=0,
        cancel_only=True,
        cancel_source_order=True,
        now=datetime(2026, 7, 28, 4, 47, tzinfo=timezone.utc),
        live_enabled=True,
    )
    terminal = RestingOrderState(
        order_id=runner.live_order_id(submitted),
        client_order_id=str(submitted["client_order_id"]),
        expected_venue_order_id=None,
        root_order_id=runner.live_order_id(submitted),
        source_order_id=runner.live_order_id(submitted),
        plan_id=str(submitted["plan_id"]),
        token_id=str(submitted["token_id"]),
        venue_side="BUY",
        outcome_side="YES",
        requested_shares="5",
        matched_shares="5",
        remaining_shares="0",
        posted_price=str(submitted["posted_price"]),
        status="filled",
        created_at_utc=str(submitted["created_at_utc"]),
        maker_only=True,
        execution_profile=str(submitted["resolved_execution_profile"]),
        execution_policy=str(submitted["execution_policy"]),
        order_lifecycle_policy=str(submitted["order_lifecycle_policy"]),
        reprice_count=0,
        data_epoch_ref=str(submitted["source_report_ts_utc"]),
        authoritative_state_version="MATCHED:5",
        lifecycle_owner=shared.RUNTIME_OWNER,
        raw_venue_status="MATCHED",
        order_state_provenance="test",
    )
    monkeypatch.setattr(
        shared.PolymarketVenueAdapter,
        "fetch_order_state",
        lambda *_args, **_kwargs: terminal,
    )

    result = shared.execute_core_carry_plans(
        plans=[lifecycle_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=100,
        code_commit="test-sha",
    )

    assert result["live_errors"] == 0
    assert result["live_written"] == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "live_orders.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["status"] == "filled"
    assert rows[-1]["child_order_role"] == "core_carry_maker_terminal"
    assert runner.maker_lifecycle_heads(tmp_path / "live_orders.jsonl")[-1]["status"] == "filled"


def test_near_core_cancel_is_confirmed_before_core_supersession(
    tmp_path, monkeypatch
) -> None:
    maker_plan = runner.build_near_core_entry_plan(
        {
            **_score(),
            "eligible": False,
            "reasons": ["non_positive_taker_ev"],
            "rescore_mode": "continuous_market_reprice",
        },
        live_enabled=True,
        now=datetime(2026, 7, 28, 4, 31, tzinfo=timezone.utc),
        order_ttl_min=15,
    )
    assert maker_plan is not None
    transport = FakeTransport()
    monkeypatch.setattr(
        shared,
        "build_live_transport",
        lambda **_kwargs: (transport, {"mode": "test"}),
    )
    shared.execute_core_carry_plans(
        plans=[maker_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=5,
        code_commit="test-sha",
    )
    submitted = json.loads(
        (tmp_path / "live_orders.jsonl").read_text().splitlines()[-1]
    )
    lifecycle_plan = runner.build_maker_lifecycle_plan(
        submitted,
        action="near_core_maker_cancel_core_supersession",
        limit_price=0,
        cancel_only=True,
        cancel_source_order=True,
        now=datetime(2026, 7, 28, 4, 32, tzinfo=timezone.utc),
        live_enabled=True,
    )
    live_state = RestingOrderState(
        order_id=runner.live_order_id(submitted),
        client_order_id=str(submitted["client_order_id"]),
        expected_venue_order_id=None,
        root_order_id=runner.live_order_id(submitted),
        source_order_id=runner.live_order_id(submitted),
        plan_id=str(submitted["plan_id"]),
        token_id=str(submitted["token_id"]),
        venue_side="BUY",
        outcome_side="YES",
        requested_shares="5",
        matched_shares="0",
        remaining_shares="5",
        posted_price=str(submitted["posted_price"]),
        status="live",
        created_at_utc=str(submitted["created_at_utc"]),
        maker_only=True,
        execution_profile=str(submitted["resolved_execution_profile"]),
        execution_policy=str(submitted["execution_policy"]),
        order_lifecycle_policy=str(submitted["order_lifecycle_policy"]),
        reprice_count=0,
        data_epoch_ref=str(submitted["data_epoch_ref"]),
        authoritative_state_version="OPEN:0:5",
        lifecycle_owner=shared.RUNTIME_OWNER,
        raw_venue_status="LIVE",
        order_state_provenance="test",
    )
    monkeypatch.setattr(
        shared.PolymarketVenueAdapter,
        "fetch_order_state",
        lambda *_args, **_kwargs: live_state,
    )

    result = shared.execute_core_carry_plans(
        plans=[lifecycle_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=5,
        code_commit="test-sha",
    )

    assert result["live_errors"] == 0, result
    assert transport.cancels == [runner.live_order_id(submitted)]
    rows = [
        json.loads(line)
        for line in (tmp_path / "live_orders.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["status"] == "cancelled"
    assert runner.near_core_maker_lifecycle_heads(
        tmp_path / "live_orders.jsonl"
    )[-1]["status"] == "cancelled"

    repeated = shared.execute_core_carry_plans(
        plans=[lifecycle_plan],
        output_dir=tmp_path,
        live=True,
        market_proxy=None,
        max_child_shares=5,
        max_batch_cost_usd=5,
        code_commit="test-sha",
    )
    repeated_rows = [
        json.loads(line)
        for line in (tmp_path / "live_orders.jsonl").read_text().splitlines()
    ]
    assert repeated["live_errors"] == 0
    assert repeated["live_written"] == 0
    assert transport.cancels == [runner.live_order_id(submitted)]
    assert repeated_rows == rows


def test_near_midpoint_is_used_at_actual_fresh_submit(tmp_path,monkeypatch):
    plan=runner.build_near_core_entry_plan(_score(),live_enabled=True,now=datetime(2026,7,28,4,31,tzinfo=timezone.utc),order_ttl_min=15)
    assert plan is not None
    transport=FakeTransport()
    monkeypatch.setattr(shared,"build_live_transport",lambda **kw:(transport,{"mode":"test"}))
    result=shared.execute_core_carry_plans(plans=[plan],output_dir=tmp_path,live=True,market_proxy=None,max_child_shares=5,max_batch_cost_usd=100,code_commit="test")
    assert result["live_errors"]==0
    assert len(transport.posts)==1
    payload,_,post_only=transport.posts[0]
    assert post_only is True
    assert float(payload["price"])==.82


def test_private_terminal_requires_exact_token_quantity_and_terminal(tmp_path):
    path=tmp_path/'private.jsonl'
    meta={'token_id':'token','shares':'5','side':'BUY'}
    raw={'event_type':'order','id':'order','asset_id':'token','side':'BUY','status':'CANCELED','original_size':'5','size_matched':'2'}
    for changes in [{'asset_id':'wrong'},{'size_matched':None},{'original_size':'6'},{'size_matched':'6'},{'status':'LIVE'}]:
        path.write_text(json.dumps({'payload':{**raw,**changes}})+'\n')
        assert shared.private_terminal_order_lookup(path,'order',meta) is None
    path.write_text(json.dumps({'payload':raw})+'\n')
    assert shared.private_terminal_order_lookup(path,'order',meta)['size_matched']=='2'


@pytest.mark.parametrize("final_matched", [0, 1, 5])
def test_replacement_can_rest_below_best_bid_and_respects_partial_minimum(tmp_path,monkeypatch,final_matched):
    from datetime import timedelta
    from dataclasses import replace
    now=datetime.now(timezone.utc)
    plan=runner.build_entry_plans(_score(),live_enabled=True,now=datetime(2026,7,28,4,31,tzinfo=timezone.utc),taker_shares=5,maker_shares=5,order_ttl_min=15)[1]
    plan.update(maker_price_cap=.83,expires_at_utc=(now+timedelta(minutes=10)).isoformat(),maker_lifecycle_deadline_utc=(now+timedelta(minutes=10)).isoformat())
    transport=FakeTransport()
    monkeypatch.setattr(shared,'build_live_transport',lambda **kw:(transport,{'mode':'test'}))
    kw=dict(output_dir=tmp_path,live=True,market_proxy=None,max_child_shares=5,max_batch_cost_usd=100,code_commit='test')
    shared.execute_core_carry_plans(plans=[plan],**kw)
    source=json.loads((tmp_path/'live_orders.jsonl').read_text().splitlines()[-1])
    action=runner.build_maker_lifecycle_plan(source,action='core_carry_maker_reprice',limit_price=.83,cancel_only=False,cancel_source_order=True,now=now,live_enabled=True,reprice_stage='midpoint')
    state=replace(shared._stub_order_state(action,source),authoritative_state_version='LIVE:0:.81',order_state_provenance='test')
    def lookup(*a,**k):
        return replace(state,status='cancelled',cancel_confirmed=True,matched_shares=str(final_matched),remaining_shares=str(5-final_matched),authoritative_state_version=f'CANCELED:{final_matched}:.81') if transport.cancels else state
    monkeypatch.setattr(shared.PolymarketVenueAdapter,'fetch_order_state',lookup)
    transport.fetch_market_book=lambda token: {'status':'ok','fetched_at_utc':now.isoformat(),'sequence':'book-new','bids':[{'price':'.85','size':'20'}],'asks':[{'price':'.86','size':'20'}]}
    result=shared.execute_core_carry_plans(plans=[action],**kw)
    expected_posts = 2 if final_matched == 0 else 1
    assert len(transport.posts)==expected_posts
    if final_matched == 0:
        assert result['live_errors']==0
        assert float(transport.posts[-1][0]['price'])==.83
    # Replaying the same action cannot create a third replacement.
    shared.execute_core_carry_plans(plans=[action],**kw)
    assert len(transport.posts)==expected_posts
