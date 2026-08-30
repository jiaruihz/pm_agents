from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.strategies.runtime.data_products import (
    REGISTRY_FILENAME,
    load_data_product_registry,
    production_data_product_registry,
)
from src.strategies.runtime.production import load_production_spec


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_SOURCE = ROOT / "src/strategies/runtime" / REGISTRY_FILENAME


def _registry_payload() -> dict:
    return yaml.safe_load(REGISTRY_SOURCE.read_text(encoding="utf-8"))


def _load_payload(tmp_path: Path, payload: dict):
    candidate = tmp_path / REGISTRY_FILENAME
    candidate.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return load_data_product_registry(load_production_spec(), tmp_path)


def test_production_loader_is_the_registry_authority_and_exposes_audit() -> None:
    spec = load_production_spec()
    registry = production_data_product_registry(spec)

    assert registry is spec.data_product_registry
    assert registry.physical_path("market_books") == spec.resolved_market_books_root()
    assert registry.physical_path("forecast_curves") == (
        spec.resolved_forecast_output_root() / "forecast_hourly_curves"
    )
    audit = registry.audit()
    assert audit["ok"] is True
    assert audit["product_count"] == 12
    market_books = next(row for row in audit["products"] if row["product_id"] == "market_books")
    assert market_books["feature_ready"] is False
    assert "full WS book" in market_books["notes"]
    assert market_books["producer_release_sha"] == spec.release(
        market_books["producer_release_id"]
    ).expected_repo_sha
    assert len(audit["registry_build_id"]) == 64
    wcir = registry.product("wcir_decision_bundles")
    assert wcir.cursor["epoch_semantics"] == "new_epoch_never_reset_in_place"
    assert registry.cursor_state_path("wcir_decision_bundles") == (
        spec.pm_runtime_root
        / "weather_edge_v1/canonical_refresh/wcir_candidate_materialization_state.json"
    )
    assert registry.cursor_state_path("source_events") is None
    assert registry.product("source_events").cursor["state_scope"] == "per_consumer"
    assert registry.physical_path("historical_strategy_snapshots") == (
        spec.resolved_historical_paper_snapshot_root()
    )


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda payload: payload["products"].append(dict(payload["products"][0])),
            "duplicate data product product_id",
        ),
        (
            lambda payload: payload["products"][0].update(root_key="unknown_root"),
            "unknown data product root_key",
        ),
        (
            lambda payload: payload["products"][0].update(relative_path="../escape"),
            "relative_path escapes root",
        ),
        (
            lambda payload: payload["products"][0].update(owner_instance_id="not_managed"),
            "owner is not a managed runtime",
        ),
        (
            lambda payload: payload["products"][0].update(producer_release_id="data_feed"),
            "owner/release binding mismatch",
        ),
        (
            lambda payload: payload["products"][0].update(
                root_key="archive_storage_root", relative_path="current/books"
            ),
            "current data product must live under production storage",
        ),
        (
            lambda payload: payload["products"][0].update(data_class="archive"),
            "archive data product must live under archive storage",
        ),
        (
            lambda payload: payload["products"][-4]["cursor"].pop(
                "recovery_owner"
            ),
            "cursor contract incomplete",
        ),
    ],
)
def test_registry_fails_closed_for_invalid_product_contracts(
    tmp_path: Path, mutate, error: str
) -> None:
    payload = _registry_payload()
    mutate(payload)

    with pytest.raises(ValueError, match=error):
        _load_payload(tmp_path, payload)


def test_registry_rejects_multiple_mutable_writers_for_same_physical_target(tmp_path: Path) -> None:
    payload = _registry_payload()
    duplicate = dict(payload["products"][0])
    duplicate["product_id"] = "market_books_duplicate"
    payload["products"].append(duplicate)

    with pytest.raises(ValueError, match="multiple mutable data product writers"):
        _load_payload(tmp_path, payload)


def test_registry_rejects_nested_mutable_writer_targets(tmp_path: Path) -> None:
    payload = _registry_payload()
    duplicate = dict(payload["products"][0])
    duplicate["product_id"] = "market_books_nested_writer"
    duplicate["relative_path"] = "nested"
    payload["products"].append(duplicate)

    with pytest.raises(ValueError, match="overlap physical target"):
        _load_payload(tmp_path, payload)
