from __future__ import annotations

from weather_model_evaluation import build_inference_source_basis


def test_faster_inference_source_may_differ_from_settlement_source() -> None:
    evidence = build_inference_source_basis(
        inference_source="amos_runway_10m_RKSI",
        settlement_source="weather_com_hourly_RKSI",
        raw_value=23.4,
        raw_unit="C",
        calibrated_value=24.0,
        model_artifact_id="tmin-probability-v1",
        basis_calibration_artifact_id="rksi-amos-to-wu-v1",
    )

    assert evidence["source_matches_settlement"] is False
    assert evidence["source_mismatch_is_error"] is False
    assert evidence["inference_role"] == "predictive_observation_not_settlement_label"
    assert evidence["basis_status"] == "model_and_basis_mapped"


def test_cross_source_without_separate_basis_is_visible_not_relabelled() -> None:
    evidence = build_inference_source_basis(
        inference_source="fast_source",
        settlement_source="official_source",
        raw_value=20.0,
        raw_unit="C",
        model_artifact_id="model-v1",
    )

    assert evidence["basis_status"] == "model_mapped_basis_audit_required"
    assert evidence["settlement_label_source"] == "official_source"
