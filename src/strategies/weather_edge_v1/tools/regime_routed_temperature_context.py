"""Strategy-private temperature-context sizing overlay for regime-routed NO."""

from __future__ import annotations

from typing import Any


def temperature_context_multiplier(record: dict[str, Any], *, strength: str = "light") -> float:
    """Fixed temperature-context sizing overlay for regime-routed NO shadowing.

    This is deliberately first-principles and pre-declared. It is not fit to
    realized payoff rows. The meaning of the same weather state depends on the
    route: current-bracket NO wants later pass-through, while capped d2 NO wants
    the higher bracket to remain unhit.
    """

    route = str(record.get("route_leg") or "")
    cloud = str(record.get("cloud_warming_interaction") or "")
    moisture = str(record.get("moisture_cloud_interaction") or "")
    marine = str(record.get("marine_thermal_state") or "")
    peak = str(record.get("forecast_peak_clock_state") or "")
    warming = str(record.get("warming_state") or "")

    if route == "runway_current_no":
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 1.08
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 1.03
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 0.72
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 0.50

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 1.08
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 0.82

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 0.88
        elif moisture == "dry_heat_inertia":
            value *= 1.03

        if marine == "onshore_marine_cooling_risk":
            value *= 0.86
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 1.05
    else:
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 0.82
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 0.94
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 1.04
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 1.08

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 0.86
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 1.06

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 1.04
        elif moisture == "dry_heat_inertia" and warming in {"warming", "fast_warming"}:
            value *= 0.94

        if marine == "onshore_marine_cooling_risk":
            value *= 1.05
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 0.94

    if strength == "medium":
        value = 1.0 + 1.55 * (value - 1.0)
        return max(0.35, min(1.20, float(value)))
    return max(0.55, min(1.12, float(value)))
