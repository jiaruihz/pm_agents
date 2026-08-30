from __future__ import annotations

from weather_data_feed.cutover import audit_cutover, audit_product


def _evidence() -> dict:
    return {"registry_build_id": "a" * 64, "producer_release_sha": "b" * 40, "new_writer_single_owner": True, "consumer_routes_verified": True, "evidence_window_start_utc": "2026-08-29T00:00:00Z", "evidence_window_end_utc": "2026-08-30T00:00:00Z", "same_input_snapshot": True, "row_parity": True, "identity_parity": True, "key_parity": True, "watermark_parity": True, "target_date_blocks": 2, "quarantine_clear": True, "rollovers": 1, "old_writer_cycles": [0, 0], "accounting_deltas": {field: 0 for field in ("orders", "fills", "notional", "cost", "fee", "pnl", "coverage")}, "rollback_contract": {"documented": True, "old_data_retain_read_only": True}, "old_path_read_only_tombstone": True}


def test_cutover_only_goes_when_every_contract_is_satisfied() -> None:
    result = audit_product("observations", _evidence())
    assert result.decision == "GO" and result.retain_read_only is True
    bad = _evidence()
    bad["accounting_deltas"]["fee"] = "0.01"
    bad["old_writer_cycles"] = [0, 1]
    result = audit_product("observations", bad)
    assert result.decision == "NO_GO"
    assert "accounting_delta_fee" in result.failures
    assert "old_writer_not_zero_for_two_cycles" in result.failures


def test_audit_is_per_product_and_does_not_offer_operational_actions() -> None:
    results = audit_cutover({"good": _evidence(), "bad": {}})
    assert [result.decision for result in results] == ["GO", "NO_GO"]


def test_cutover_evidence_is_bound_to_the_loaded_registry_and_release() -> None:
    result = audit_product(
        "observations",
        _evidence(),
        expected_registry_build_id="c" * 64,
        expected_producer_release_sha="d" * 40,
    )
    assert result.decision == "NO_GO"
    assert "registry_build_id_mismatch" in result.failures
    assert "producer_release_sha_mismatch" in result.failures
