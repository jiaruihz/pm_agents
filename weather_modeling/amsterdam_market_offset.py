"""PIT feature construction for Amsterdam's market-offset probability head."""

from __future__ import annotations

import numpy as np
import pandas as pd

from weather_model_evaluation.market_offset_probability import logit


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
