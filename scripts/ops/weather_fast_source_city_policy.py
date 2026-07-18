from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_NO_ASK = 0.97


@dataclass(frozen=True)
class FastSourceCityPolicy:
    city: str
    source: str
    default_mode: str
    signal_handler: str
    confirmation_policy: str
    required_distinct_observations: int
    qualifying_margin: float
    strong_margin: float
    shares_per_trade: float
    max_shares_per_market: float
    max_no_ask: float
    max_source_age_min: float | None = None
    max_source_observation_lag_min: float | None = None
    source_profile_override_reason: str = ""


# Strategy policy belongs here, not in the data-source registry. The source registry
# still owns station, unit, rounding, settlement basis, and calibration status.
CITY_POLICIES: dict[tuple[str, str], FastSourceCityPolicy] = {
    ("Atlanta", "noaa_madis_hfmetar"): FastSourceCityPolicy(
        city="Atlanta",
        source="noaa_madis_hfmetar",
        default_mode="shadow",
        signal_handler="metar_prev_no_range_2f",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
        max_source_age_min=30.0,
        max_source_observation_lag_min=30.0,
    ),
    ("Busan", "amos_runway"): FastSourceCityPolicy(
        city="Busan",
        source="amos_runway",
        default_mode="live_trial",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=15.0,
        max_shares_per_market=15.0,
        max_no_ask=MAX_NO_ASK,
        source_profile_override_reason="user_approved_tiny_live_trial_pending_source_alignment",
    ),
    ("Helsinki", "fmi"): FastSourceCityPolicy(
        city="Helsinki",
        source="fmi",
        default_mode="live_trial",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=15.0,
        max_shares_per_market=15.0,
        max_no_ask=MAX_NO_ASK,
        source_profile_override_reason="user_approved_tiny_live_trial_pending_source_alignment",
    ),
    ("Miami", "noaa_madis_hfmetar"): FastSourceCityPolicy(
        city="Miami",
        source="noaa_madis_hfmetar",
        default_mode="shadow",
        signal_handler="metar_prev_no_range_2f",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
        max_source_age_min=30.0,
        max_source_observation_lag_min=30.0,
    ),
    ("SanFrancisco", "noaa_madis_hfmetar"): FastSourceCityPolicy(
        city="SanFrancisco",
        source="noaa_madis_hfmetar",
        default_mode="shadow",
        signal_handler="metar_prev_no_range_2f",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
        max_source_age_min=30.0,
        max_source_observation_lag_min=30.0,
    ),
    ("Singapore", "singapore_mss"): FastSourceCityPolicy(
        city="Singapore",
        source="singapore_mss",
        default_mode="live_trial",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=15.0,
        max_shares_per_market=15.0,
        max_no_ask=MAX_NO_ASK,
        source_profile_override_reason="user_approved_tiny_live_trial_cross_station_basis_pending",
    ),
    ("Tokyo", "jma_amedas"): FastSourceCityPolicy(
        city="Tokyo",
        source="jma_amedas",
        default_mode="live_trial",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="arithmetic_cross",
        required_distinct_observations=1,
        qualifying_margin=0.5,
        strong_margin=0.5,
        shares_per_trade=15.0,
        max_shares_per_market=15.0,
        max_no_ask=MAX_NO_ASK,
        source_profile_override_reason="user_approved_tiny_live_trial_pending_source_alignment",
    ),
    ("Seoul", "amos_runway"): FastSourceCityPolicy(
        city="Seoul",
        source="amos_runway",
        default_mode="shadow",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="persistent_candidate_margin",
        required_distinct_observations=2,
        qualifying_margin=0.5,
        strong_margin=0.7,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
    ),
    ("Ankara", "mgm"): FastSourceCityPolicy(
        city="Ankara",
        source="mgm",
        default_mode="shadow",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="arithmetic_cross",
        required_distinct_observations=1,
        qualifying_margin=0.5,
        strong_margin=0.5,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
    ),
    ("Istanbul", "mgm"): FastSourceCityPolicy(
        city="Istanbul",
        source="mgm",
        default_mode="shadow",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="arithmetic_cross",
        required_distinct_observations=1,
        qualifying_margin=0.5,
        strong_margin=0.5,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
    ),
    ("TelAviv", "ims_lod"): FastSourceCityPolicy(
        city="TelAviv",
        source="ims_lod",
        default_mode="shadow",
        signal_handler="metar_prev_no_exact",
        confirmation_policy="arithmetic_cross",
        required_distinct_observations=1,
        qualifying_margin=0.5,
        strong_margin=0.5,
        shares_per_trade=0.0,
        max_shares_per_market=0.0,
        max_no_ask=MAX_NO_ASK,
    ),
}


def configured_city_policies(
    *,
    live_cities: Iterable[str] | None = None,
    shadow_cities: Iterable[str] | None = None,
) -> dict[str, FastSourceCityPolicy]:
    by_city = {policy.city: policy for policy in CITY_POLICIES.values()}
    live_override = set(live_cities or [])
    shadow_override = set(shadow_cities or [])
    if live_cities is None and shadow_cities is None:
        return by_city
    selected = live_override | shadow_override
    missing = selected - set(by_city)
    if missing:
        raise ValueError(f"unsupported generic fast-source cities: {sorted(missing)}")
    out: dict[str, FastSourceCityPolicy] = {}
    for city in sorted(selected):
        policy = by_city[city]
        mode = "live_trial" if city in live_override else "shadow"
        out[city] = FastSourceCityPolicy(**{**policy.__dict__, "default_mode": mode})
    return out
