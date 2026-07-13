"""PIT as-of enrichment layers for tmax_distribution_v3.

Every joined feature uses feature timestamp <= decision timestamp.  Direct
``city/date/hour`` joins are forbidden; this module also re-measures the two
numbers the work order flagged (dual-source same-time coverage and the
direct-join future-leakage minutes).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from .common import ATLAS_CSV, CURVE_ROOTS, ROOT, bracket_mid_native, city_family, parse_utc, to_f_delta

MECH_FIELDS = ("relative_humidity_pct", "dewpoint_depression_f", "wind_speed_kt", "sky_cover_num")
RESTORATION_STATES = ROOT / "docs/analysis/2026-07/generated/tmax_clean_feature_restoration_v1/enriched_clean_states.csv"
RESTORATION_KEYS = ["city", "target_date", "decision_snapshot_ts_utc"]


# ---------------------------------------------------------------------------
# Forecast hourly curves (strict PIT: capture snapshot_ts_utc as availability)
# ---------------------------------------------------------------------------

def load_curves() -> dict[tuple[str, str], list[dict[str, Any]]]:
    seen: set[tuple[str, str, str, str]] = set()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for root in CURVE_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                city = str(raw.get("city") or "")
                target_date = str(raw.get("target_date") or "")
                ts = parse_utc(raw.get("snapshot_ts_utc"))
                curve = raw.get("hourly_curve")
                if not city or not target_date or ts is None or not isinstance(curve, list):
                    continue
                key = (city, target_date, str(raw.get("snapshot_ts_utc")), str(raw.get("forecast_values_hash")))
                if key in seen:
                    continue
                seen.add(key)
                points = []
                for point in curve:
                    temp = point.get("temperature_f")
                    time_local = str(point.get("time_local") or "")
                    if temp is None or len(time_local) < 13:
                        continue
                    points.append((time_local, float(temp)))
                if not points:
                    continue
                grouped[(city, target_date)].append(
                    {
                        "available_ts": ts,
                        "source": str(raw.get("forecast_source") or ""),
                        "offset_seconds": int(raw.get("forecast_utc_offset_seconds") or 0),
                        "points": points,
                    }
                )
    for key in grouped:
        grouped[key].sort(key=lambda item: item["available_ts"])
    return grouped


def curve_features(state: dict[str, Any], curves: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, float]:
    out = {
        "remaining_heat_integral_f": math.nan,
        "forecast_ceiling_margin_f": math.nan,
        "tracking_residual_f": math.nan,
        "forecast_curve_available": 0.0,
    }
    candidates = curves.get((str(state["city"]), str(state["target_date"])))
    if not candidates:
        return out
    decision = parse_utc(state["decision_snapshot_ts_utc"])
    eligible = [c for c in candidates if c["available_ts"] <= decision]
    if not eligible:
        return out
    preferred = [c for c in eligible if c["source"] == str(state.get("forecast_source") or "")]
    chosen = (preferred or eligible)[-1]
    local_decision = decision + pd.Timedelta(seconds=chosen["offset_seconds"])
    local_iso = local_decision.strftime("%Y-%m-%dT%H:%M")
    current_f = float(state["current_temp_f"])
    running_f = float(state["running_max_f"])
    future = [(t, v) for t, v in chosen["points"] if t > local_iso and t[:10] == str(state["target_date"])]
    past = [(t, v) for t, v in chosen["points"] if t <= local_iso]
    out["forecast_curve_available"] = 1.0
    if future:
        out["remaining_heat_integral_f"] = float(sum(max(0.0, v - current_f) for _, v in future))
        out["forecast_ceiling_margin_f"] = float(max(v for _, v in future) - running_f)
    else:
        out["remaining_heat_integral_f"] = 0.0
        out["forecast_ceiling_margin_f"] = float(-(running_f - max(v for _, v in chosen["points"])))
    if past:
        out["tracking_residual_f"] = float(current_f - past[-1][1])
    return out


# ---------------------------------------------------------------------------
# Atlas mechanism context (archive-reconstructed; upper-bound layer only)
# ---------------------------------------------------------------------------

def load_atlas() -> tuple[dict[tuple[str, str], pd.DataFrame], pd.DataFrame]:
    atlas = pd.read_csv(ATLAS_CSV, low_memory=False)
    atlas["feature_ts"] = pd.to_datetime(atlas["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    atlas = atlas.dropna(subset=["feature_ts"]).copy()
    atlas["sky_cover_num"] = pd.to_numeric(atlas["sky_cover_code"], errors="coerce")
    atlas["dewpoint_depression_f"] = pd.to_numeric(atlas["dewpoint_depression_f"], errors="coerce")
    grouped = {
        (str(city), str(date)): group.sort_values("feature_ts")
        for (city, date), group in atlas.groupby(["city", "target_date"])
    }
    return grouped, atlas


def load_restoration_mechanism() -> pd.DataFrame:
    """Load the already-audited <=60m feature-factory as-of reconstruction.

    This is the only source used by the archive weather upper-bound. The atlas
    state table is retained solely for the direct-hour leakage diagnostic.
    """
    if not RESTORATION_STATES.exists():
        raise RuntimeError(f"restoration enrichment missing: {RESTORATION_STATES}")
    use = RESTORATION_KEYS + [
        "factory_feature_snapshot_ts_utc", "factory_lag_min", "factory_match_type",
        "atlas_asof_within_60m", "atlas_relative_humidity_pct",
        "atlas_dewpoint_depression_f", "atlas_wind_speed_kt", "atlas_sky_cover_code",
        "backfill_gfs_forecast_peak_delta_hours_local",
        "backfill_ecmwf_forecast_peak_delta_hours_local",
    ]
    frame = pd.read_csv(RESTORATION_STATES, usecols=use, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_snapshot_ts_utc"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True)
    frame["factory_feature_snapshot_ts_utc"] = pd.to_datetime(frame["factory_feature_snapshot_ts_utc"], utc=True, errors="coerce")
    matched = frame["factory_feature_snapshot_ts_utc"].notna()
    if not (frame.loc[matched, "factory_feature_snapshot_ts_utc"] <= frame.loc[matched, "decision_snapshot_ts_utc"]).all():
        raise AssertionError("restoration mechanism cache contains a future feature timestamp")
    accepted = frame["factory_match_type"].isin(["exact", "asof_prior"])
    if not (pd.to_numeric(frame.loc[accepted, "factory_lag_min"], errors="coerce") <= 60.0).all():
        raise AssertionError("restoration mechanism cache exceeds the 60-minute PIT window")
    sky_map = {"CLR": 0.0, "SKC": 0.0, "FEW": 1.0, "SCT": 2.0, "BKN": 3.0, "OVC": 4.0, "VV": 4.0}
    sky = frame["atlas_sky_cover_code"].astype(str).str.upper().map(sky_map)
    sky = sky.fillna(pd.to_numeric(frame["atlas_sky_cover_code"], errors="coerce"))
    out = frame[RESTORATION_KEYS].copy()
    out["relative_humidity_pct"] = pd.to_numeric(frame["atlas_relative_humidity_pct"], errors="coerce")
    out["dewpoint_depression_f"] = pd.to_numeric(frame["atlas_dewpoint_depression_f"], errors="coerce")
    out["wind_speed_kt"] = pd.to_numeric(frame["atlas_wind_speed_kt"], errors="coerce")
    out["sky_cover_num"] = sky
    out["mech_available"] = accepted.astype(float)
    out["atlas_gfs_available"] = frame["backfill_gfs_forecast_peak_delta_hours_local"].notna().astype(float)
    out["atlas_ecmwf_available"] = frame["backfill_ecmwf_forecast_peak_delta_hours_local"].notna().astype(float)
    out["mechanism_feature_ts_utc"] = frame["factory_feature_snapshot_ts_utc"]
    out["mechanism_lag_min"] = pd.to_numeric(frame["factory_lag_min"], errors="coerce")
    out["mechanism_lineage"] = "report_time_reconstruction_only_lag_le_60m"
    return out


def atlas_asof_features(state: dict[str, Any], atlas_groups: dict[tuple[str, str], pd.DataFrame]) -> dict[str, float]:
    out = {field: math.nan for field in MECH_FIELDS}
    out.update({"mech_available": 0.0, "atlas_gfs_available": 0.0, "atlas_ecmwf_available": 0.0})
    group = atlas_groups.get((str(state["city"]), str(state["target_date"])))
    if group is None:
        return out
    decision = parse_utc(state["decision_snapshot_ts_utc"])
    eligible = group[group["feature_ts"] <= decision]
    if eligible.empty:
        return out
    row = eligible.iloc[-1]
    for field in MECH_FIELDS:
        value = row.get(field)
        out[field] = float(value) if value is not None and np.isfinite(float(value or np.nan)) else math.nan
    out["mech_available"] = 1.0
    out["atlas_gfs_available"] = float(np.isfinite(float(row.get("gfs_forecast_max_native") or np.nan)))
    out["atlas_ecmwf_available"] = float(np.isfinite(float(row.get("ecmwf_forecast_max_native") or np.nan)))
    return out


def mech_interactions(state: dict[str, Any]) -> dict[str, float]:
    peak_delta = state.get("forecast_peak_delta_hours_local")
    peak_delta = float(peak_delta) if peak_delta is not None and np.isfinite(float(peak_delta or np.nan)) else math.nan
    rh = state.get("relative_humidity_pct", math.nan)
    sky = state.get("sky_cover_num", math.nan)
    wind = state.get("wind_speed_kt", math.nan)
    coastal = 1.0 if city_family(str(state["city"])) == "southern_or_maritime" else 0.0
    return {
        "rh_x_hours_to_peak": (rh / 100.0) * peak_delta if np.isfinite(rh) and np.isfinite(peak_delta) else math.nan,
        "cloud_x_hours_to_peak": (sky / 4.0) * peak_delta if np.isfinite(sky) and np.isfinite(peak_delta) else math.nan,
        "wind_x_coastal": wind * coastal if np.isfinite(wind) else math.nan,
        "solar_noon_distance_h": abs(float(state["decision_hour_local"]) - 13.0),
    }


def remeasure_flagged_numbers(states: pd.DataFrame, atlas_flat: pd.DataFrame) -> dict[str, Any]:
    """Re-measure the two numbers flagged in the work order on current data."""
    decision_ts = pd.to_datetime(states["decision_snapshot_ts_utc"], utc=True)
    keyed = states[["city", "target_date", "decision_hour_local"]].copy()
    keyed["state_ts"] = decision_ts
    atlas_keyed = atlas_flat[["city", "target_date", "decision_hour_local", "feature_ts"]].copy()
    atlas_keyed["target_date"] = atlas_keyed["target_date"].astype(str)
    joined = keyed.merge(atlas_keyed, on=["city", "target_date", "decision_hour_local"], how="inner")
    leak_minutes = (joined["feature_ts"] - joined["state_ts"]).dt.total_seconds() / 60.0
    positive = leak_minutes[leak_minutes > 0]
    dual = states[["atlas_gfs_available", "atlas_ecmwf_available"]].fillna(0.0)
    both = (dual["atlas_gfs_available"] > 0) & (dual["atlas_ecmwf_available"] > 0)
    by_date = states.assign(dual=both).groupby("target_date")["dual"].mean()
    dual_counts = states.assign(dual=both).groupby("target_date")["dual"].sum().astype(int)
    mech = pd.to_numeric(states.get("mech_available", 0.0), errors="coerce").fillna(0.0).gt(0)
    mech_counts = states.assign(mech=mech).groupby("target_date")["mech"].sum().astype(int)
    return {
        "direct_join_rows": int(len(joined)),
        "direct_join_future_leakage_rows": int((leak_minutes > 0).sum()),
        "direct_join_future_leakage_share": float((leak_minutes > 0).mean()) if len(joined) else math.nan,
        "direct_join_median_positive_leakage_minutes": float(positive.median()) if len(positive) else math.nan,
        "direct_join_p90_positive_leakage_minutes": float(positive.quantile(0.9)) if len(positive) else math.nan,
        "dual_source_asof_state_share": float(both.mean()),
        "dual_source_dates_nonzero": {str(k): round(float(v), 4) for k, v in by_date[by_date > 0].items()},
        "dual_source_asof_rows_by_date": {str(k): int(v) for k, v in dual_counts[dual_counts > 0].items()},
        "dual_source_verdict": "not_answerable_no_2026_06_21_plus_coverage",
        "archive_meteo_asof_60m_rows": int(mech.sum()),
        "archive_meteo_asof_60m_share": float(mech.mean()),
        "archive_meteo_asof_60m_rows_by_date": {str(k): int(v) for k, v in mech_counts.items()},
        "archive_meteo_lineage": "report_time_reconstruction_only_upper_bound",
    }


# ---------------------------------------------------------------------------
# Source calibration (expanding by date; hierarchical shrinkage, no city memory)
# ---------------------------------------------------------------------------

def build_source_calibration(states: pd.DataFrame) -> pd.DataFrame:
    labeled = states[states["labeled"] & states["forecast_max_native"].notna()].copy()
    labeled["actual_native"] = labeled["settlement_winning_bracket_label"].map(bracket_mid_native)
    day = (
        labeled.dropna(subset=["actual_native"])
        .groupby(["city", "target_date"], as_index=False)
        .agg(
            forecast_max_native=("forecast_max_native", "median"),
            actual_native=("actual_native", "first"),
            unit=("unit", "first"),
            forecast_source=("forecast_source", "first"),
        )
    )
    day["family"] = day["city"].map(city_family)
    day["error_f"] = (day["forecast_max_native"] - day["actual_native"]) * day["unit"].map(to_f_delta)
    rows = []
    for date in sorted(states["target_date"].astype(str).unique()):
        train = day[day["target_date"] < date]
        if train.empty:
            continue
        g_bias, g_mae = float(train["error_f"].mean()), float(train["error_f"].abs().mean())
        src = train.groupby("forecast_source")["error_f"].agg(["sum", "count", lambda s: s.abs().sum()])
        src.columns = ["sum", "count", "abs_sum"]
        fam = train.groupby(["family", "forecast_source"])["error_f"].agg(["sum", "count"])
        for source in states["forecast_source"].dropna().unique():
            s = src.loc[source] if source in src.index else None
            n = float(s["count"]) if s is not None else 0.0
            bias = ((s["sum"] if s is not None else 0.0) + 10.0 * g_bias) / (n + 10.0)
            mae = ((s["abs_sum"] if s is not None else 0.0) + 10.0 * g_mae) / (n + 10.0)
            for family in day["family"].unique():
                f = fam.loc[(family, source)] if (family, source) in fam.index else None
                fn = float(f["count"]) if f is not None else 0.0
                f_bias = ((f["sum"] if f is not None else 0.0) + 5.0 * bias) / (fn + 5.0)
                rows.append(
                    {
                        "target_date": date,
                        "forecast_source": source,
                        "family": family,
                        "source_bias_shrunk_f": float(bias),
                        "source_mae_shrunk_f": float(mae),
                        "family_source_bias_shrunk_f": float(f_bias),
                    }
                )
    return pd.DataFrame(rows)


def apply_enrichment(states: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    curves = load_curves()
    _, atlas_flat = load_atlas()
    mechanism = load_restoration_mechanism()
    curve_rows = []
    for record in states.to_dict("records"):
        curve_rows.append(curve_features(record, curves))
    out = pd.concat([states.reset_index(drop=True), pd.DataFrame(curve_rows)], axis=1)
    out["decision_snapshot_ts_utc"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True)
    out["target_date"] = out["target_date"].astype(str)
    before = len(out)
    out = out.merge(mechanism, on=RESTORATION_KEYS, how="left", validate="one_to_one")
    if len(out) != before:
        raise AssertionError("restoration mechanism join changed the clean denominator")
    for field in (*MECH_FIELDS, "mech_available", "atlas_gfs_available", "atlas_ecmwf_available"):
        out[field] = pd.to_numeric(out[field], errors="coerce")
    out["mech_available"] = out["mech_available"].fillna(0.0)
    out["atlas_gfs_available"] = out["atlas_gfs_available"].fillna(0.0)
    out["atlas_ecmwf_available"] = out["atlas_ecmwf_available"].fillna(0.0)
    inter = pd.DataFrame([mech_interactions(record) for record in out.to_dict("records")])
    out = pd.concat([out, inter], axis=1)
    calibration = build_source_calibration(out)
    out["family"] = out["city"].map(city_family)
    out["target_date"] = out["target_date"].astype(str)
    out = out.merge(
        calibration,
        left_on=["target_date", "forecast_source", "family"],
        right_on=["target_date", "forecast_source", "family"],
        how="left",
    )
    out["unit_is_c"] = out["unit"].astype(str).str.upper().eq("C").astype(float)
    out["source_is_ecmwf"] = out["forecast_source"].astype(str).str.contains("ecmwf").astype(float)
    remeasured = remeasure_flagged_numbers(out, atlas_flat)
    return out, remeasured
