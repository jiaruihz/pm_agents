from pathlib import Path

from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate


def _candidate(**overrides):
    legs = [
        {
            "condition_id": f"condition-{idx}",
            "bracket": str(20 + idx),
            "best_ask": price,
            "best_bid": max(price - 0.01, 0.001),
            "ask_size": 10.0,
        }
        for idx, price in enumerate([0.10, 0.15, 0.20, 0.22, 0.28])
    ]
    row = {
        "event_date": "2026-06-14",
        "city": "TestCity",
        "event_slug": "highest-temperature-in-test-city-on-june-14-2026",
        "underround": 0.05,
        "max_yes_spread": 0.01,
        "snapshot_ts_utc": "2026-06-13T18:00:00Z",
        "legs_detail": legs,
    }
    row.update(overrides)
    return row


def test_all_yes_guard_allows_complete_underround_basket(tmp_path: Path):
    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=_candidate())

    assert decision.allow
    assert decision.reason == "ok_all_leg_or_none"
    assert decision.basket_cost_usd == 4.75
    assert decision.expected_profit_usd == 0.25
    assert len(decision.leg_orders) == 5
    assert {leg["side"] for leg in decision.leg_orders} == {"BUY_YES"}


def test_all_yes_guard_rejects_missing_ask(tmp_path: Path):
    candidate = _candidate()
    candidate["legs_detail"][2]["best_ask"] = None

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "leg_2_missing_best_ask" in decision.blockers


def test_all_yes_guard_rejects_insufficient_depth(tmp_path: Path):
    candidate = _candidate()
    candidate["legs_detail"][3]["ask_size"] = 4.99

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "leg_3_depth_below_shares" in decision.blockers


def test_all_yes_guard_rejects_cost_above_cap(tmp_path: Path):
    cfg = BasketGuardConfig(max_basket_cost_usd=4.0)

    decision = check_candidate(cfg=cfg, repo_root=tmp_path, candidate=_candidate())

    assert not decision.allow
    assert "basket_cost_above_max" in decision.blockers


def test_all_yes_guard_rejects_wide_spread(tmp_path: Path):
    candidate = _candidate(max_yes_spread=0.08)

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "spread_above_max" in decision.blockers


def test_all_yes_guard_rejects_duplicate_condition_id(tmp_path: Path):
    candidate = _candidate()
    candidate["legs_detail"][4]["condition_id"] = candidate["legs_detail"][0]["condition_id"]

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "leg_4_duplicate_condition_id" in decision.blockers


def test_all_yes_guard_rejects_duplicate_bracket(tmp_path: Path):
    candidate = _candidate()
    candidate["legs_detail"][4]["bracket"] = candidate["legs_detail"][0]["bracket"]

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "leg_4_duplicate_bracket" in decision.blockers


def test_all_yes_guard_rejects_underround_mismatch(tmp_path: Path):
    candidate = _candidate(underround=0.20)

    decision = check_candidate(cfg=BasketGuardConfig(), repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "underround_mismatch" in decision.blockers


def test_all_yes_guard_rejects_kill_switch(tmp_path: Path):
    pause = tmp_path / "PAUSE"
    pause.write_text("halt", encoding="utf-8")
    cfg = BasketGuardConfig(kill_switch_path="PAUSE")

    decision = check_candidate(cfg=cfg, repo_root=tmp_path, candidate=_candidate())

    assert not decision.allow
    assert "kill_switch_active" in decision.blockers


def test_all_yes_guard_allows_fresh_snapshot_when_ttl_enabled(tmp_path: Path):
    cfg = BasketGuardConfig(max_snapshot_age_seconds=180)

    decision = check_candidate(
        cfg=cfg,
        repo_root=tmp_path,
        candidate=_candidate(),
        decision_ts_utc="2026-06-13T18:02:00Z",
    )

    assert decision.allow


def test_all_yes_guard_rejects_stale_snapshot(tmp_path: Path):
    cfg = BasketGuardConfig(max_snapshot_age_seconds=180)

    decision = check_candidate(
        cfg=cfg,
        repo_root=tmp_path,
        candidate=_candidate(),
        decision_ts_utc="2026-06-13T18:05:01Z",
    )

    assert not decision.allow
    assert "snapshot_too_old" in decision.blockers


def test_all_yes_guard_uses_oldest_orderbook_leg_time_for_ttl(tmp_path: Path):
    cfg = BasketGuardConfig(max_snapshot_age_seconds=180)
    candidate = _candidate(
        orderbook_fetched_at_utc_min="2026-06-13T18:00:00Z",
        orderbook_fetched_at_utc_max="2026-06-13T18:32:00Z",
    )

    decision = check_candidate(
        cfg=cfg,
        repo_root=tmp_path,
        candidate=candidate,
        decision_ts_utc="2026-06-13T18:33:30Z",
    )

    assert not decision.allow
    assert "snapshot_too_old" in decision.blockers


def test_all_yes_guard_rejects_missing_snapshot_when_ttl_enabled(tmp_path: Path):
    cfg = BasketGuardConfig(max_snapshot_age_seconds=180)
    candidate = _candidate()
    candidate.pop("snapshot_ts_utc")

    decision = check_candidate(cfg=cfg, repo_root=tmp_path, candidate=candidate)

    assert not decision.allow
    assert "missing_snapshot_ts" in decision.blockers


def test_all_yes_guard_rejects_future_snapshot(tmp_path: Path):
    cfg = BasketGuardConfig(max_snapshot_age_seconds=180)

    decision = check_candidate(
        cfg=cfg,
        repo_root=tmp_path,
        candidate=_candidate(),
        decision_ts_utc="2026-06-13T17:59:00Z",
    )

    assert not decision.allow
    assert "snapshot_ts_in_future" in decision.blockers
