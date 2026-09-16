from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_rev2 import (
    DEFAULT_OUTPUT,
    _exact_record,
    _sweep,
    add_path_features,
    dependency_weights,
    verify_manifest,
)


def test_shared_path_builder_uses_complete_ordered_path() -> None:
    rows = pd.DataFrame({
        "path": ["historical"] * 4 + ["captured"] * 4,
        "observed_at": pd.to_datetime([
            "2026-01-01T10:00Z", "2026-01-01T10:10Z", "2026-01-01T10:20Z", "2026-01-01T10:30Z",
        ] * 2, utc=True),
        "latest_fast_native_value": [10.0, 11.0, 10.5, 12.0] * 2,
    })
    result = add_path_features(rows, group_column="path")
    left = result.loc[result["path"].eq("historical")].reset_index(drop=True)
    right = result.loc[result["path"].eq("captured")].reset_index(drop=True)
    for column in ("recent_slope", "recent_acceleration", "path_volatility", "running_fast_max", "pullback_depth", "reheat_strength", "time_since_high_minutes"):
        assert np.allclose(left[column], right[column], equal_nan=True)


def test_dependency_weights_equalize_dates_and_print_groups() -> None:
    frame = pd.DataFrame({
        "target_date": ["a", "a", "a", "b"],
        "official_print_group_id": ["g1", "g1", "g2", "g3"],
    })
    weights = dependency_weights(frame)
    weighted = frame.assign(weight=weights)
    date_weight = weighted.groupby("target_date")["weight"].sum()
    assert np.isclose(date_weight["a"], date_weight["b"])
    groups = weighted.groupby(["target_date", "official_print_group_id"])["weight"].sum()
    assert np.isclose(groups[("a", "g1")], groups[("a", "g2")])


def test_direct_sweep_never_synthesizes_missing_side() -> None:
    assert _sweep([], 1.0, "buy")["fully_executable"] is False
    result = _sweep([{"price": 0.4, "size": 5.0}], 5.0, "buy")
    assert result["fully_executable"] is True
    assert result["effective_value_usd"] > 2.0


def test_exact_market_identity_requires_market_condition_and_token() -> None:
    snapshot = {"records": [{"market_id": "m", "condition_id": "c", "yes_token_id": "y", "no_token_id": "n"}]}
    record, reason = _exact_record(snapshot, {"market_id": "m", "condition_id": "c", "token_id": "n"})
    assert record is not None and reason is None
    record, reason = _exact_record(snapshot, {"market_id": "m", "condition_id": "wrong", "token_id": "n"})
    assert record is None and reason == "condition_id_not_matched"


def test_generated_rev2_is_zero_notional_and_row_complete() -> None:
    parity = json.loads((DEFAULT_OUTPUT / "FEATURE_PARITY_AUDIT.json").read_text())
    zero = json.loads((DEFAULT_OUTPUT / "ZERO_NOTIONAL_AUDIT.json").read_text())
    market = json.loads((DEFAULT_OUTPUT / "MARKET_ARCHIVE_RECONCILIATION.json").read_text())
    predictions = pd.read_parquet(DEFAULT_OUTPUT / "CAPTURED_PIT_PREDICTIONS.parquet")
    assert parity["feature_rows"] == parity["latest_value_exact_match"] == 87
    assert parity["failures"] == {}
    assert predictions["decision_vintage_id"].nunique() == 87
    assert set(predictions["model_id"].unique()) == {
        "B2_latest_fast_rounded", "ams_next_print_m1_ordinal_logit", "ams_next_print_m2_monotonic_additive"
    }
    assert market["rest_exact_identity_t0"] == 87
    assert zero["orders"] == zero["fills"] == zero["notional"] == 0


def test_manifest_rejects_extra_file(tmp_path: Path) -> None:
    source = DEFAULT_OUTPUT
    manifest = json.loads((source / "EVIDENCE_MANIFEST.json").read_text())
    destination = tmp_path / "evidence"
    destination.mkdir()
    (destination / "EVIDENCE_MANIFEST.json").write_text(json.dumps(manifest))
    for row in manifest["entries"]:
        target = destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / row["path"]).read_bytes())
    verify_manifest(destination)
    (destination / "extra").write_text("drift")
    with pytest.raises(RuntimeError, match="entry-set drift"):
        verify_manifest(destination)
