"""PIT feature construction for Amsterdam's market-offset probability head."""

from __future__ import annotations

import numpy as np
import pandas as pd

from weather_model_evaluation.market_offset_probability import logit
from weather_model_evaluation.probability import (
    build_state_entry_grain,
    build_transition_grain,
)


def amsterdam_path_regime(frame: pd.DataFrame) -> pd.Series:
    """Return the frozen Amsterdam remaining-heat path state."""

    conditions = [
        frame["decline_from_running_max_c"].le(0.1)
        & frame["ta_delta_30m_c"].ge(0.2),
        frame["decline_from_running_max_c"].le(0.2)
        & frame["ta_delta_30m_c"].abs().lt(0.2),
        frame["decline_from_running_max_c"].ge(0.2)
        & frame["is_rebounding_after_pullback"].eq(1),
        frame["decline_from_running_max_c"].ge(0.2)
        & frame["ta_delta_30m_c"].le(0),
    ]
    return pd.Series(
        np.select(
            conditions,
            ["fresh_high", "plateau", "pullback_rebound", "fade_pullback"],
            default="other",
        ),
        index=frame.index,
        dtype="string",
    )


def add_amsterdam_evaluation_grains(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark V5-compatible grains before any market-evidence filtering."""

    output = frame.reset_index(drop=True).copy()
    output["_evaluation_row_id"] = np.arange(len(output), dtype=int)
    output["path_regime"] = amsterdam_path_regime(output)
    transition = build_transition_grain(
        output,
        group_columns=["target_date"],
        state_columns=["current_bracket_c", "path_regime"],
        time_column="observed_at_utc",
    )
    state_entry = build_state_entry_grain(
        output,
        group_columns=["target_date"],
        state_columns=["current_bracket_c"],
        time_column="observed_at_utc",
    )
    output["is_transition"] = output["_evaluation_row_id"].isin(
        set(transition["_evaluation_row_id"].astype(int))
    )
    output["is_state_entry"] = output["_evaluation_row_id"].isin(
        set(state_entry["_evaluation_row_id"].astype(int))
    )
    return output.drop(columns="_evaluation_row_id")


def add_amsterdam_market_offset_features(
    frame: pd.DataFrame,
    required_features: list[str] | None = None,
) -> pd.DataFrame:
    """Build the exact feature contract shared by research and WCIR scoring."""

    output = frame.copy()
    required = set(required_features or ())
    build_all = required_features is None

    def needs(*names: str) -> bool:
        return build_all or bool(required.intersection(names))

    if needs(
        "weather_market_logit_disagreement",
        "weather_disagreement_x_uncertainty",
    ):
        output["weather_market_logit_disagreement"] = (
            logit(output["p_model"]) - logit(output["market_p"])
        )
    if needs("market_logit_level"):
        output["market_logit_level"] = logit(output["market_p"])
    if needs("ta_margin_current_c"):
        output["ta_margin_current_c"] = output["ta_c"] - output["current_bracket_c"]
    if needs("tx_margin_current_c"):
        output["tx_margin_current_c"] = output["tx_c"] - output["current_bracket_c"]
    if needs("market_uncertainty", "weather_disagreement_x_uncertainty"):
        output["market_uncertainty"] = 4.0 * output["market_p"] * (
            1.0 - output["market_p"]
        )
    if needs("weather_disagreement_x_uncertainty"):
        output["weather_disagreement_x_uncertainty"] = (
            output["weather_market_logit_disagreement"]
            * output["market_uncertainty"]
        )
    if needs("forecast_margin_x_remaining_solar"):
        output["forecast_margin_x_remaining_solar"] = (
            output["forecast_remaining_max_minus_d1_c"]
            * output["remaining_clear_sky_integral_h"]
        )
    if needs("trend_x_remaining_solar"):
        output["trend_x_remaining_solar"] = (
            output["ta_delta_60m_c"] * output["remaining_clear_sky_integral_h"]
        )
    if needs("peak_passed_x_drawdown"):
        output["peak_passed_x_drawdown"] = (
            output["forecast_peak_passed"] * output["knmi_drawdown_from_high_c"]
        )
    return output
