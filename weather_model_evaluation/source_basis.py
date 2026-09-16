"""Contract for predictive weather sources that differ from settlement truth."""

from __future__ import annotations

from typing import Any


def build_inference_source_basis(
    *,
    inference_source: str,
    settlement_source: str,
    raw_value: float,
    raw_unit: str,
    model_artifact_id: str,
    basis_calibration_artifact_id: str | None = None,
    calibrated_value: float | None = None,
) -> dict[str, Any]:
    """Describe a source as a predictive feature, never as an outcome label.

    Source mismatch is expected and is not a contract error.  It becomes a
    research-only blocker only when no frozen model/basis artifact explains
    how the source is mapped into the settlement-native target.
    """

    inference = str(inference_source or "").strip()
    settlement = str(settlement_source or "").strip()
    model = str(model_artifact_id or "").strip()
    unit = str(raw_unit or "").strip()
    if not inference or not settlement or not model or not unit:
        raise ValueError(
            "inference_source, settlement_source, model_artifact_id, and raw_unit are required"
        )
    value = float(raw_value)
    calibrated = float(calibrated_value) if calibrated_value is not None else None
    same_source = inference == settlement
    calibration_id = str(basis_calibration_artifact_id or "").strip() or None
    if same_source:
        relation = "same_source_identity_or_model_mapping"
        status = "model_mapped"
    elif calibration_id:
        relation = "cross_source_calibrated_inference"
        status = "model_and_basis_mapped"
    else:
        relation = "cross_source_model_inference_basis_not_separately_identified"
        status = "model_mapped_basis_audit_required"
    return {
        "inference_role": "predictive_observation_not_settlement_label",
        "inference_source": inference,
        "settlement_label_source": settlement,
        "source_matches_settlement": same_source,
        "source_mismatch_is_error": False,
        "basis_relation": relation,
        "basis_status": status,
        "raw_value": value,
        "raw_unit": unit,
        "calibrated_value": calibrated,
        "model_artifact_id": model,
        "basis_calibration_artifact_id": calibration_id,
    }
