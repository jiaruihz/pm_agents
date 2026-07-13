"""Frozen artifact + zero-notional shadow event source for tmax_distribution_v3.

The shadow events CSV feeds the existing runtime
``scripts/ops/tmax_distribution_edge_shadow_v1.py`` (no parallel data chain).
Rows are advisory zero-notional records; no order is ever placed.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (
    BUCKETS,
    MODEL_VERSION,
    TmaxHazardChainV3,
    ConstrainedMarketRecalibratorV3,
    RECALIBRATION_ROUTES,
    fuse_log_linear,
    official_fee,
    route_feature_columns,
)

from .common import FROZEN_POLICY, OUT_DIR, SCOPE_RETRO
from .execution import _eligible_expressions

SHADOW_SCHEMA_VERSION = "tmax_distribution_v3_shadow_v1"
SHADOW_CONFIG_ID = "tmax_distribution_v3_primary"
ARTIFACT_JOBLIB = OUT_DIR / "tmax_distribution_v3_frozen_primary.joblib"
ARTIFACT_JSON = OUT_DIR / "tmax_distribution_v3_frozen_primary.json"


def freeze_primary_artifact(
    hourly_labeled: pd.DataFrame, primary_route: str, freeze_date: str,
    final_alpha: float, final_coherence: float, final_c: float | None = None,
) -> dict[str, Any]:
    train = hourly_labeled[hourly_labeled["target_date"] <= freeze_date]
    if primary_route in RECALIBRATION_ROUTES:
        model = ConstrainedMarketRecalibratorV3(
            include_path=primary_route == "market_recal_path", c_value=final_c or 0.03
        ).fit(train[train["market_score_ready"]])
        artifact_kind = "frozen_primary_market_recalibrator"
    else:
        columns = route_feature_columns(primary_route)
        model = TmaxHazardChainV3(feature_columns=columns).fit(train)
        artifact_kind = "frozen_primary_hazard_chain"
    joblib.dump(model, ARTIFACT_JOBLIB)
    sha256 = hashlib.sha256(ARTIFACT_JOBLIB.read_bytes()).hexdigest()
    reloaded = joblib.load(ARTIFACT_JOBLIB)
    sample = train.head(min(256, len(train)))
    delta = float(
        np.max(
            np.abs(
                model.predict_five_bucket(sample)[[f"p_{b}" for b in BUCKETS]].to_numpy()
                - reloaded.predict_five_bucket(sample)[[f"p_{b}" for b in BUCKETS]].to_numpy()
            )
        )
    )
    assert delta <= 1e-12, f"frozen artifact reload parity failed: {delta}"
    descriptor = {
        "model_version": MODEL_VERSION,
        "artifact": artifact_kind,
        "candidate_status": "shadow_candidate",
        "promotion": "no_live",
        "primary_route": primary_route,
        "train_through": freeze_date,
        "train_rows": model.train_rows,
        "train_dates": model.train_dates,
        "fusion_alpha_frozen": final_alpha,
        "coherence_weight_frozen": final_coherence,
        "calibrator_c_frozen": final_c,
        "h_cont": model.h_cont,
        "model_description": model.describe(),
        "pipeline_sha256": sha256,
        "reload_prediction_max_abs_delta": delta,
        "zero_notional": True,
        "no_order_placed": True,
    }
    ARTIFACT_JSON.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return descriptor


def _shadow_event_id(row: dict[str, Any]) -> str:
    key = "|".join(
        str(row.get(field))
        for field in ("shadow_config_id", "city", "target_date", "decision_snapshot_ts_utc", "chosen_expression")
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def build_shadow_events(
    exec_with_probs: pd.DataFrame,
    unlabeled_states: pd.DataFrame,
    chain: TmaxHazardChainV3,
    primary_route: str,
    final_alpha: float,
    freeze_date: str,
) -> pd.DataFrame:
    """Selected/blocked shadow events: recent labeled dates (retro scope) plus
    frozen-model predictions on not-yet-settled forward dates."""
    frames = []
    recent_dates = sorted(exec_with_probs["target_date"].unique())[-3:]
    retro = exec_with_probs[exec_with_probs["target_date"].isin(recent_dates)].copy()
    retro["scope"] = SCOPE_RETRO
    retro["label_source"] = "settlement_outcomes"
    frames.append(retro)
    if not unlabeled_states.empty:
        pred = chain.predict_five_bucket(unlabeled_states)
        market = unlabeled_states[[f"market_p_{b}" for b in BUCKETS]].to_numpy(dtype=float)
        fused = fuse_log_linear(pred[[f"p_{b}" for b in BUCKETS]].to_numpy(), market, final_alpha)
        forward = unlabeled_states.copy()
        for position, bucket in enumerate(BUCKETS):
            forward[f"route_p_{bucket}"] = fused[:, position]
        forward["h_cont"] = chain.h_cont
        forward["scope"] = "forward_pending_settlement"
        forward["label_source"] = "pending"
        frames.append(forward)
    rows = []
    for frame in frames:
        ordered = frame.sort_values(["city", "target_date", "decision_snapshot_ts_utc"])
        for (city, date), group in ordered.groupby(["city", "target_date"], sort=False):
            selected_done = False
            rank = 0
            for record in group.to_dict("records"):
                candidates = _eligible_expressions(record)
                if not candidates:
                    continue
                best = candidates[0]
                rank += 1
                status = "selected" if not selected_done else "blocked"
                reason = "first_eligible_city_day" if not selected_done else "city_day_after_first_selected"
                selected_done = True
                row = {
                    "shadow_schema_version": SHADOW_SCHEMA_VERSION,
                    "shadow_config_id": SHADOW_CONFIG_ID,
                    "selection_policy": "first_eligible_city_day",
                    "zero_notional": True,
                    "no_order_placed": True,
                    "selection_status": status,
                    "selection_reason": reason,
                    "city_day_eligible_rank": rank,
                    "scope": record["scope"],
                    "city": city,
                    "target_date": date,
                    "decision_hour_local": record["decision_hour_local"],
                    "decision_snapshot_ts_utc": record["decision_snapshot_ts_utc"],
                    "method": primary_route,
                    "model_version": MODEL_VERSION,
                    "chosen_expression": best["expression"],
                    "bracket": best["bracket"],
                    "ask": best["ask"],
                    "p_win": best["p_win"],
                    "model_edge": best["edge"],
                    "fee_per_share": best["fee"],
                    "actual_bucket": record.get("actual_bucket") or "",
                    "label_source": record["label_source"],
                    "win": "" if record["label_source"] == "pending" else _shadow_win(best, record),
                    "unit_pnl": "",
                    "day_regime": "",
                    "intraday_state": "",
                    "running_max_state": "",
                    "train_through_date": freeze_date,
                }
                row["shadow_event_id"] = _shadow_event_id(row)
                rows.append(row)
    return pd.DataFrame(rows)


def _shadow_win(best: dict[str, Any], record: dict[str, Any]) -> Any:
    winner = record.get("settlement_winning_bracket_label")
    if not winner or (isinstance(winner, float) and math.isnan(winner)):
        return ""
    hit = str(best["bracket"]) == str(winner)
    return float(hit) if best["side"] == "YES" else float(not hit)
