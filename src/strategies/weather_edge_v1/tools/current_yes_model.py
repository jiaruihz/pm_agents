"""Shared current-YES model scoring and entry gate helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FadeGateSpec:
    min_decline_c: float = 0.5
    min_ask: float = 0.55
    min_p_yes: float = 0.5
    min_edge: float = 0.05
    min_local_hour: int = 13
    max_local_hour: int = 15

    ask_reject_reason: str = "snapshot_rule_yes_ask_lt_0_55"
    p_reject_reason: str = "snapshot_rule_p_yes_lt_0_5"
    edge_reject_reason: str = "snapshot_rule_edge_lt_0_05"

    @property
    def training_filter_label(self) -> str:
        return f"period == train AND decline_c >= {self.min_decline_c:g} AND has_d1_no"

    @property
    def markdown_filter_label(self) -> str:
        return f"`decline_c >= {self.min_decline_c:g} AND has_d1_no`；训练只用 `period=train`。"


DEFAULT_FADE_GATE = FadeGateSpec()


def score_artifact(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = list(artifact["numeric_features"])
    categorical_features = list(artifact["categorical_features"])
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales

    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        lookup = {cat: i for i, cat in enumerate(cats)}
        for row_idx, value in enumerate(values):
            col_idx = lookup.get(str(value))
            if col_idx is not None:
                mat[row_idx, col_idx] = 1.0
        cat_parts.append(mat)

    transformed = np.concatenate([numeric, *cat_parts], axis=1) if cat_parts else numeric
    coef = np.asarray(artifact["coef"], dtype=float)
    logits = transformed @ coef + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def fade_training_population_mask(rows: pd.DataFrame, spec: FadeGateSpec = DEFAULT_FADE_GATE) -> pd.Series:
    decline = pd.to_numeric(rows["decline_c"], errors="coerce")
    if "has_d1_no" not in rows.columns:
        raise KeyError("has_d1_no")
    has_d1_no = rows["has_d1_no"]
    if has_d1_no.dtype != bool:
        has_d1_no = has_d1_no.astype(str).str.lower().isin({"true", "1", "yes"})
    return decline.ge(spec.min_decline_c) & has_d1_no.fillna(False)


def fade_live_like_mask(
    rows: pd.DataFrame,
    p_col: str,
    spec: FadeGateSpec = DEFAULT_FADE_GATE,
) -> pd.Series:
    decision_hour = pd.to_numeric(rows["decision_hour_local"], errors="coerce")
    ask = pd.to_numeric(rows["yes_current_ask"], errors="coerce")
    p_yes = pd.to_numeric(rows[p_col], errors="coerce")
    return (
        decision_hour.between(spec.min_local_hour, spec.max_local_hour)
        & ask.ge(spec.min_ask)
        & p_yes.ge(spec.min_p_yes)
        & (p_yes - ask).ge(spec.min_edge)
    )


def fade_gate_reject_reason(row: dict[str, Any], spec: FadeGateSpec = DEFAULT_FADE_GATE) -> str:
    ask = _to_float(row.get("yes_current_ask"), 0.0)
    p_yes = _to_float(row.get("p_yes_win"), 0.0)
    edge = _to_float(row.get("ev"), -999.0)
    if ask < spec.min_ask:
        return spec.ask_reject_reason
    if p_yes < spec.min_p_yes:
        return spec.p_reject_reason
    if edge < spec.min_edge:
        return spec.edge_reject_reason
    return ""


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default
