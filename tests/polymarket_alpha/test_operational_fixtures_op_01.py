"""OP-01 offline fixture/provider-isolation acceptance tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.polymarket_alpha.pilot.operational_fixtures import (
    FixtureReceiptCode,
    load_frozen_fixture_set,
)


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "operational_pilot"
OBSERVED = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def _copy_fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "operational_pilot"
    root.mkdir()
    for source in FIXTURE_ROOT.glob("*.json"):
        (root / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _rewrite_manifest(root: Path, mutator) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutator(manifest)
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _refresh_fixture_hashes(root: Path, *, filename: str, manifest_index: int) -> None:
    from src.polymarket_alpha.contracts import bytes_sha256, content_sha256

    path = root / filename
    raw = path.read_bytes()
    payload = json.loads(raw)

    def update(manifest) -> None:
        manifest["fixtures"][manifest_index]["raw_bytes_sha256"] = bytes_sha256(raw)
        manifest["fixtures"][manifest_index]["payload_sha256"] = content_sha256(payload)

    _rewrite_manifest(root, update)


def test_allowlisted_synthetic_set_normalizes_four_unrelated_binary_markets() -> None:
    result = load_frozen_fixture_set(FIXTURE_ROOT, source_observed_at=OBSERVED)

    assert result.fixture_set_id == "op01_synthetic_frozen_gamma_v1"
    assert len(result.fixtures) == 4
    assert {item.normalized.identity.market_id for item in result.fixtures} == {
        "op01-1001", "op01-1002", "op01-1003", "op01-1004",
    }
    assert all(item.normalized.identity.yes_token_id.endswith("yes") for item in result.fixtures)
    assert all(item.normalized.identity.no_token_id.endswith("no") for item in result.fixtures)
    assert [receipt.code for receipt in result.receipts] == [FixtureReceiptCode.ACCEPTED] * 4


def test_replay_has_stable_identity_and_fixture_set_hashes() -> None:
    first = load_frozen_fixture_set(FIXTURE_ROOT, source_observed_at=OBSERVED)
    second = load_frozen_fixture_set(FIXTURE_ROOT, source_observed_at=OBSERVED)

    assert first.fixture_set_sha256 == second.fixture_set_sha256
    assert [(item.fixture_id, item.identity_sha256) for item in first.fixtures] == [
        (item.fixture_id, item.identity_sha256) for item in second.fixtures
    ]


def test_reversed_outcome_arrays_keep_yes_no_token_semantics(tmp_path: Path) -> None:
    root = _copy_fixture_root(tmp_path)
    original = load_frozen_fixture_set(root, source_observed_at=OBSERVED)
    payload_path = root / "election_turnout_binary.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["outcomes"] = json.dumps(["Yes", "No"])
    payload["clobTokenIds"] = json.dumps(["op01-election-yes", "op01-election-no"])
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    # Refreshing an expected payload hash is an explicit fixture refresh, not
    # an identity change.  The canonical YES/NO mapping must stay identical.
    _refresh_fixture_hashes(root, filename="election_turnout_binary.json", manifest_index=1)
    reversed_order = load_frozen_fixture_set(root, source_observed_at=OBSERVED)
    original_identity = next(item.normalized.identity for item in original.fixtures if item.fixture_id == "election_turnout_binary")
    reversed_identity = next(item.normalized.identity for item in reversed_order.fixtures if item.fixture_id == "election_turnout_binary")
    assert reversed_identity == original_identity


def test_manifest_identity_mismatch_is_a_typed_receipt(tmp_path: Path) -> None:
    root = _copy_fixture_root(tmp_path)
    _rewrite_manifest(
        root,
        lambda manifest: manifest["fixtures"][0]["expected_identity"].__setitem__("yes_token_id", "wrong-token"),
    )
    result = load_frozen_fixture_set(root, source_observed_at=OBSERVED)

    mismatch = next(receipt for receipt in result.receipts if receipt.fixture_id == "storm_landfall_binary")
    assert mismatch.code == FixtureReceiptCode.IDENTITY_MISMATCH
    assert all(item.fixture_id != "storm_landfall_binary" for item in result.fixtures)


def test_schema_drift_is_a_typed_receipt_and_does_not_use_provider(tmp_path: Path) -> None:
    root = _copy_fixture_root(tmp_path)
    payload_path = root / "space_launch_binary.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["futureGammaField"] = "unrecognized"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    _refresh_fixture_hashes(root, filename="space_launch_binary.json", manifest_index=2)

    def network_provider_must_not_run() -> object:
        raise AssertionError("offline fixture loader attempted provider/network access")

    result = load_frozen_fixture_set(
        root,
        source_observed_at=OBSERVED,
        network_provider=network_provider_must_not_run,
    )
    drift = next(receipt for receipt in result.receipts if receipt.fixture_id == "space_launch_binary" and receipt.code == FixtureReceiptCode.SCHEMA_DRIFT)
    assert drift.unknown_fields == ("futureGammaField",)
    assert all(item.fixture_id != "space_launch_binary" for item in result.fixtures)


def test_payload_hash_tamper_is_typed_and_never_falls_back_to_provider(tmp_path: Path) -> None:
    root = _copy_fixture_root(tmp_path)
    _rewrite_manifest(root, lambda manifest: manifest["fixtures"][3].__setitem__("payload_sha256", "0" * 64))

    result = load_frozen_fixture_set(
        root,
        source_observed_at=OBSERVED,
        network_provider=lambda: pytest.fail("provider/network fallback is forbidden"),
    )
    receipt = next(receipt for receipt in result.receipts if receipt.fixture_id == "court_ruling_binary")
    assert receipt.code == FixtureReceiptCode.FIXTURE_INVALID
    assert "payload_sha256" in receipt.detail


def test_raw_artifact_hash_changes_when_only_json_formatting_changes(tmp_path: Path) -> None:
    root = _copy_fixture_root(tmp_path)
    original = load_frozen_fixture_set(root, source_observed_at=OBSERVED)
    path = root / "storm_landfall_binary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _refresh_fixture_hashes(root, filename=path.name, manifest_index=0)
    reformatted = load_frozen_fixture_set(root, source_observed_at=OBSERVED)
    before = next(item for item in original.fixtures if item.fixture_id == "storm_landfall_binary")
    after = next(item for item in reformatted.fixtures if item.fixture_id == "storm_landfall_binary")
    assert before.fixture_sha256 == after.fixture_sha256
    assert before.raw_bytes_sha256 != after.raw_bytes_sha256
