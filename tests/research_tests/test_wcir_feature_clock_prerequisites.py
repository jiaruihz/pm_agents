from __future__ import annotations

import pandas as pd
import pytest

from weather_modeling.amsterdam_feature_builder_v2 import AmsterdamFeatureBuilderV2
from weather_modeling.next_print_feature_builder_v1 import NextPrintFeatureBuilderV1


BUILDERS = (NextPrintFeatureBuilderV1, AmsterdamFeatureBuilderV2)


def _path() -> pd.DataFrame:
    return pd.DataFrame({
        "observed_at": ["2026-08-20T10:00:00Z", "2026-08-20T10:10:00Z", "2026-08-20T10:20:00Z"],
        "available_at": ["2026-08-20T10:01:00Z", "2026-08-20T10:11:00Z", "2026-08-20T10:21:00Z"],
        "latest_fast_native_value": [10.0, 11.0, 12.0],
        "source_observation_id": ["a", "b", "c"],
    })


def _build(builder, frame: pd.DataFrame, **overrides):
    args = {
        "decision_vintage_id": "clock-fixture",
        "feature_cutoff_at": "2026-08-20T10:25:00Z",
        "decision_ready_at": "2026-08-20T10:25:00Z",
        "observation_cutoff_at": "2026-08-20T10:20:00Z",
        "availability_class": "CAPTURED_PIT_ARCHIVE",
        "feature_names": ["running_fast_max"],
        "source_path_complete": True,
    }
    args.update(overrides)
    return builder.build_vintage(frame, **args)


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("field,value", [
    ("feature_cutoff_at", None), ("feature_cutoff_at", pd.NaT),
    ("decision_ready_at", None), ("decision_ready_at", pd.NaT),
    ("observation_cutoff_at", None), ("observation_cutoff_at", pd.NaT),
    ("feature_cutoff_at", "2026-08-20T10:25:00"),
])
def test_pit_requires_complete_timezone_aware_build_clocks(builder, field, value) -> None:
    with pytest.raises(ValueError):
        _build(builder, _path(), **{field: value})


@pytest.mark.parametrize("builder", BUILDERS)
def test_pit_rejects_missing_nat_or_naive_source_clock(builder) -> None:
    for value in (None, pd.NaT, "2026-08-20T10:21:00"):
        frame = _path()
        frame.loc[2, "available_at"] = value
        with pytest.raises(ValueError):
            _build(builder, frame)


@pytest.mark.parametrize("builder", BUILDERS)
def test_pit_excludes_late_revision_and_never_uses_next_row(builder) -> None:
    frame = _path()
    frame.loc[2, "available_at"] = "2026-08-20T10:25:00Z"
    frame = pd.concat([frame, pd.DataFrame([{
        "observed_at": "2026-08-20T10:20:00Z", "available_at": "2026-08-20T10:30:00Z",
        "latest_fast_native_value": 99.0, "source_observation_id": "late-revision",
    }, {
        "observed_at": "2026-08-20T10:30:00Z", "available_at": "2026-08-20T10:30:00Z",
        "latest_fast_native_value": 100.0, "source_observation_id": "next-row",
    }])], ignore_index=True)
    result = _build(builder, frame)
    assert result.status == "OK"
    assert result.feature_vector["running_fast_max"] == 12.0
    assert result.max_feature_available_at == "2026-08-20T10:25:00+00:00"


def test_next_print_context_is_strict_and_contributes_to_result_max() -> None:
    frame = _path()
    result = _build(
        NextPrintFeatureBuilderV1,
        frame,
        feature_names=["running_fast_max", "last_official_native_value"],
        feature_context={"last_official_native_value": {
            "value": 11.0,
            "available_at": "2026-08-20T10:24:00Z",
            "source_observation_ids": ["official-1"],
        }},
    )
    assert result.max_feature_available_at == "2026-08-20T10:24:00+00:00"
    for value in (None, pd.NaT, "2026-08-20T10:24:00"):
        with pytest.raises(ValueError):
            _build(
                NextPrintFeatureBuilderV1,
                frame,
                feature_names=["last_official_native_value"],
                feature_context={"last_official_native_value": {"value": 11.0, "available_at": value}},
            )
    with pytest.raises(AssertionError):
        _build(
            NextPrintFeatureBuilderV1,
            frame,
            feature_names=["last_official_native_value"],
            feature_context={"last_official_native_value": {"value": 11.0, "available_at": "2026-08-20T10:26:00Z"}},
        )


@pytest.mark.parametrize("builder", BUILDERS)
def test_historical_final_keeps_legacy_naive_clock_compatibility(builder) -> None:
    historical = _build(
        builder,
        _path().assign(available_at=["2026-08-20 10:01", "2026-08-20 10:11", "2026-08-20 10:21"]),
        availability_class="HISTORICAL_FINAL_ARCHIVE",
        observation_cutoff_at=None,
    )
    assert historical.status == "OK"
    assert historical.feature_vector["running_fast_max"] == 12.0
