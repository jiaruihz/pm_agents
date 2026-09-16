#!/usr/bin/env python3
"""Restore clean Tmax features on the frozen single-snapshot denominator.

This is retrospective research only.  The 8,094-row denominator is loaded from
the lineage replay v2 artifact; this script never re-selects snapshots.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2"
STATE_PATH = SOURCE_DIR / "execution_first_eligible_candidates.csv"
RAW_GROUPS_PATH = SOURCE_DIR / "raw_decision_groups.jsonl"
FACTORY_GLOB = "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_*/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_clean_feature_restoration_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-12-tmax-clean-feature-restoration-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-12-tmax-clean-feature-restoration-v1.json"

BUCKETS = ["below", "current", "d1", "d2", "tail"]
MARKET = [f"market_p_{bucket}" for bucket in BUCKETS]
KEYS = ["city", "target_date", "decision_snapshot_ts_utc"]
MIN_TRAIN_DATES = 5
MODEL_C = 0.1
SHARES = 5.0
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE = 0.02
EPS = 1e-9
SCOPE = "retrospective_expanding_walk_forward_diagnostic"

MINIMAL_NUMERIC = MARKET + [
    "forecast_gap_to_running_f",
    "current_minus_running_f",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "forecast_peak_delta_hours_local",
    "max_age_min",
    "minutes_since_running_max",
]
QUOTE_NUMERIC = [
    f"geom_{rung}_{side}_{field}"
    for rung in ("current", "d1", "d2")
    for side in ("yes", "no")
    for field in ("bid", "ask", "spread", "bid_size", "ask_size", "depth_5c")
] + ["ladder_mark_sum", "ladder_entropy", "ladder_overround"]
SOURCE_CATEGORICAL = ["forecast_source", "unit", "forecast_gap_band", "path_state", "peak_clock_state"]
ATLAS_NUMERIC = ["atlas_dewpoint_depression_f", "atlas_relative_humidity_pct", "atlas_wind_speed_kt", "atlas_asof_within_60m"]
ATLAS_CATEGORICAL = ["atlas_sky_cover_code", "atlas_moisture_regime", "atlas_wind_regime"]
BACKFILL_NUMERIC = [
    "backfill_gfs_forecast_gap_to_running_f",
    "backfill_ecmwf_forecast_gap_to_running_f",
    "backfill_gfs_forecast_peak_delta_hours_local",
    "backfill_ecmwf_forecast_peak_delta_hours_local",
    "backfill_forecast_peak_hour_spread", "backfill_asof_within_60m",
]
BACKFILL_CATEGORICAL = ["backfill_forecast_models_agree"]

VARIANTS: dict[str, dict[str, Any]] = {
    "minimal_clean": {"numeric": MINIMAL_NUMERIC, "categorical": [], "provenance": "strict_snapshot_native"},
    "strict_quote_geometry": {"numeric": MINIMAL_NUMERIC + QUOTE_NUMERIC, "categorical": [], "provenance": "strict_same_snapshot"},
    "strict_source_context": {"numeric": MINIMAL_NUMERIC + QUOTE_NUMERIC, "categorical": SOURCE_CATEGORICAL, "provenance": "strict_contemporaneous"},
    "atlas_meteo_regime_upper_bound": {"numeric": MINIMAL_NUMERIC + QUOTE_NUMERIC + ATLAS_NUMERIC, "categorical": SOURCE_CATEGORICAL + ATLAS_CATEGORICAL, "provenance": "report_time_reconstruction_only"},
    "gfs_ecmwf_backfill_upper_bound": {"numeric": MINIMAL_NUMERIC + QUOTE_NUMERIC + BACKFILL_NUMERIC, "categorical": SOURCE_CATEGORICAL + BACKFILL_CATEGORICAL, "provenance": "research_backfill"},
}


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def official_fee(price: float) -> float:
    return 0.05 * price * (1.0 - price)


def _norm_ts(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True)


def _bracket_key(value: Any) -> str:
    return str(value).strip().replace("°C", "").replace("°F", "")


def load_frozen_states(path: Path = STATE_PATH) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if len(frame) != 8094:
        raise AssertionError(f"frozen denominator changed: {len(frame)} != 8094")
    if frame.duplicated(KEYS).any():
        raise AssertionError("frozen state keys are not unique")
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_snapshot_ts_utc"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True)
    frame["state_row_id"] = np.arange(len(frame), dtype=int)
    return frame


def _mid(record: dict[str, Any], side: str) -> float | None:
    bid, ask = finite(record.get(f"{side}_best_bid")), finite(record.get(f"{side}_best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return ask if ask is not None else bid


def quote_geometry_for_state(state: dict[str, Any], records: list[dict[str, Any]], snapshot_ts: Any) -> dict[str, Any]:
    """Build geometry only from records belonging to the state's exact snapshot."""
    if _norm_ts(snapshot_ts) != _norm_ts(state["decision_snapshot_ts_utc"]):
        raise AssertionError("quote geometry attempted cross-snapshot use")
    lookup = {_bracket_key(record.get("bracket")): record for record in records}
    out: dict[str, Any] = {"quote_geometry_snapshot_ts_utc": str(_norm_ts(snapshot_ts))}
    for rung, state_col in (("current", "current_bracket"), ("d1", "d1_bracket"), ("d2", "d2_bracket")):
        record = lookup.get(_bracket_key(state.get(state_col)), {})
        for side in ("yes", "no"):
            bid = finite(record.get(f"{side}_best_bid"))
            ask = finite(record.get(f"{side}_best_ask"))
            out[f"geom_{rung}_{side}_bid"] = bid
            out[f"geom_{rung}_{side}_ask"] = ask
            out[f"geom_{rung}_{side}_spread"] = ask - bid if ask is not None and bid is not None else None
            out[f"geom_{rung}_{side}_bid_size"] = finite(record.get(f"{side}_bid_size"))
            out[f"geom_{rung}_{side}_ask_size"] = finite(record.get(f"{side}_ask_size"))
            out[f"geom_{rung}_{side}_depth_5c"] = finite(record.get(f"{side}_depth_ask_5c"))
    marks = np.asarray([m for record in records if (m := _mid(record, "yes")) is not None], dtype=float)
    if marks.size:
        clipped = np.clip(marks, EPS, 1.0)
        total = float(clipped.sum())
        normalized = clipped / total
        out["ladder_mark_sum"] = total
        out["ladder_overround"] = total - 1.0
        out["ladder_entropy"] = float(-(normalized * np.log(normalized)).sum())
    else:
        out.update(ladder_mark_sum=None, ladder_overround=None, ladder_entropy=None)
    return out


def enrich_quote_geometry(states: pd.DataFrame, cache_path: Path = RAW_GROUPS_PATH) -> tuple[pd.DataFrame, dict[str, Any]]:
    wanted = {(row.city, str(row.target_date), str(row.decision_snapshot_ts_utc)): row._asdict() for row in states.itertuples(index=False)}
    rows: list[dict[str, Any]] = []
    with cache_path.open() as handle:
        for line in handle:
            group = json.loads(line)
            key = (group["city"], str(group["target_date"]), str(_norm_ts(group["snapshot_ts_utc"])))
            state = wanted.get(key)
            if state is None:
                continue
            rows.append({"state_row_id": state["state_row_id"], **quote_geometry_for_state(state, group["records"], group["snapshot_ts_utc"])})
    geometry = pd.DataFrame(rows).drop_duplicates("state_row_id", keep="last") if rows else pd.DataFrame(columns=["state_row_id"])
    out = states.merge(geometry, on="state_row_id", how="left", validate="one_to_one")
    if len(out) != len(states):
        raise AssertionError("quote enrichment changed denominator")
    matched = int(out["quote_geometry_snapshot_ts_utc"].notna().sum())
    return out, {"matched": matched, "missing": len(out) - matched, "coverage": matched / len(out)}


def load_feature_factory_rows(paths: Iterable[Path] | None = None) -> pd.DataFrame:
    paths = list(paths or sorted(ROOT.glob(FACTORY_GLOB)))
    keep = [
        "city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc",
        "dewpoint_depression_f", "relative_humidity_pct", "wind_speed_kt", "sky_cover_code",
        "gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native",
        "gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread", "forecast_peak_models_agree_le_1h", "unit",
    ]
    frames = []
    for path in paths:
        header = pd.read_csv(path, nrows=0).columns
        use = [column for column in keep if column in header]
        item = pd.read_csv(path, usecols=use)
        for column in keep:
            if column not in item:
                item[column] = np.nan
        frames.append(item[keep].assign(feature_factory_path=str(path.relative_to(ROOT))))
    if not frames:
        return pd.DataFrame(columns=keep + ["feature_factory_path"])
    rows = pd.concat(frames, ignore_index=True)
    rows["target_date"] = rows["target_date"].astype(str)
    rows["decision_hour_local"] = pd.to_numeric(rows["decision_hour_local"], errors="coerce")
    rows["feature_snapshot_ts_utc"] = pd.to_datetime(rows["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    # One feature state is repeated across bracket rows. Keep the richest copy.
    value_cols = [column for column in keep if column not in {"city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"}]
    rows["richness"] = rows[value_cols].notna().sum(axis=1)
    return rows.sort_values("richness").drop_duplicates(
        ["city", "target_date", "decision_hour_local", "feature_snapshot_ts_utc"], keep="last"
    )


def strict_asof_enrich(states: pd.DataFrame, features: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Nearest prior factory row within city/date/hour; never use a later row."""
    groups: dict[tuple[str, str], tuple[list[int], list[dict[str, Any]]]] = {}
    valid = features.dropna(subset=["feature_snapshot_ts_utc", "decision_hour_local"])
    for key, group in valid.groupby(["city", "target_date"], sort=False):
        ordered = group.sort_values("feature_snapshot_ts_utc")
        groups[(str(key[0]), str(key[1]))] = (
            ordered["feature_snapshot_ts_utc"].astype("int64").tolist(), ordered.to_dict("records")
        )
    selected: list[dict[str, Any]] = []
    for row in states.itertuples(index=False):
        # Timestamp is authoritative. A valid <=60m prior state can sit in the
        # preceding local-hour partition when the clean decision crossed :00.
        key = (str(row.city), str(row.target_date))
        times, records = groups.get(key, ([], []))
        decision_ns = int(pd.Timestamp(row.decision_snapshot_ts_utc).value)
        index = bisect.bisect_right(times, decision_ns) - 1
        if index < 0:
            selected.append({"state_row_id": row.state_row_id, "factory_match_type": "missing"})
            continue
        record = records[index]
        feature_ts = pd.Timestamp(record["feature_snapshot_ts_utc"])
        lag = (pd.Timestamp(row.decision_snapshot_ts_utc) - feature_ts).total_seconds() / 60.0
        if lag < -1e-9:
            raise AssertionError("future feature-factory row selected")
        within_60m = lag <= 60.0
        selected.append({
            "state_row_id": row.state_row_id,
            "factory_feature_snapshot_ts_utc": feature_ts,
            "factory_lag_min": lag,
            "factory_match_type": "exact" if abs(lag) < 1e-9 else "asof_prior" if within_60m else "stale_over_60m",
            "factory_path": record.get("feature_factory_path"),
            "factory_decision_hour_local": record.get("decision_hour_local"),
            "factory_hour_matches_clean": bool(int(record.get("decision_hour_local")) == int(row.decision_hour_local)),
            "atlas_asof_within_60m": float(within_60m),
            "backfill_asof_within_60m": float(within_60m),
            "atlas_dewpoint_depression_f": record.get("dewpoint_depression_f") if within_60m else None,
            "atlas_relative_humidity_pct": record.get("relative_humidity_pct") if within_60m else None,
            "atlas_wind_speed_kt": record.get("wind_speed_kt") if within_60m else None,
            "atlas_sky_cover_code": record.get("sky_cover_code") if within_60m else None,
            "factory_unit": record.get("unit"),
            "raw_gfs_gap": record.get("gfs_forecast_gap_to_running_native") if within_60m else None,
            "raw_ecmwf_gap": record.get("ecmwf_forecast_gap_to_running_native") if within_60m else None,
            "backfill_gfs_forecast_peak_delta_hours_local": record.get("gfs_forecast_peak_delta_hours_local") if within_60m else None,
            "backfill_ecmwf_forecast_peak_delta_hours_local": record.get("ecmwf_forecast_peak_delta_hours_local") if within_60m else None,
            "backfill_forecast_peak_hour_spread": record.get("forecast_peak_hour_spread") if within_60m else None,
            "backfill_forecast_models_agree": record.get("forecast_peak_models_agree_le_1h") if within_60m else None,
        })
    enrichment = pd.DataFrame(selected)
    out = states.merge(enrichment, on="state_row_id", how="left", validate="one_to_one")
    if len(out) != len(states):
        raise AssertionError("as-of enrichment changed denominator")
    if out["factory_feature_snapshot_ts_utc"].notna().any():
        assert (out.loc[out.factory_feature_snapshot_ts_utc.notna(), "factory_feature_snapshot_ts_utc"] <= out.loc[out.factory_feature_snapshot_ts_utc.notna(), "decision_snapshot_ts_utc"]).all()
    factor = np.where(out["unit"].eq("C"), 1.8, 1.0)
    out["backfill_gfs_forecast_gap_to_running_f"] = pd.to_numeric(out["raw_gfs_gap"], errors="coerce") * factor
    out["backfill_ecmwf_forecast_gap_to_running_f"] = pd.to_numeric(out["raw_ecmwf_gap"], errors="coerce") * factor
    out["atlas_asof_within_60m"] = out["atlas_asof_within_60m"].fillna(0.0)
    out["backfill_asof_within_60m"] = out["backfill_asof_within_60m"].fillna(0.0)
    counts = out["factory_match_type"].fillna("missing").value_counts().to_dict()
    lag = out.loc[out.factory_match_type.isin(["exact", "asof_prior"]), "factory_lag_min"]
    meta = {
        "exact": int(counts.get("exact", 0)), "asof_prior": int(counts.get("asof_prior", 0)), "stale_over_60m": int(counts.get("stale_over_60m", 0)), "missing": int(counts.get("missing", 0)),
        "coverage": float(out.factory_match_type.isin(["exact", "asof_prior"]).mean()),
        "lag_min_median": float(lag.median()) if len(lag) else None,
        "lag_min_p95": float(lag.quantile(0.95)) if len(lag) else None,
        "gfs_non_null": int(out["backfill_gfs_forecast_gap_to_running_f"].notna().sum()),
        "ecmwf_non_null": int(out["backfill_ecmwf_forecast_gap_to_running_f"].notna().sum()),
    }
    return out, meta


def add_context_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["forecast_gap_band"] = pd.cut(pd.to_numeric(out.forecast_gap_to_running_f, errors="coerce"), [-np.inf, -1, 1, 3, np.inf], labels=["busted", "near", "marginal", "runway"]).astype(object).fillna("unknown")
    trend = pd.to_numeric(out.temp_trend_3h_f, errors="coerce")
    age = pd.to_numeric(out.minutes_since_running_max, errors="coerce")
    out["path_state"] = np.select([trend.gt(0.5), trend.lt(-0.5) & age.gt(30), age.le(30)], ["warming", "fade", "fresh_high"], default="plateau_or_unknown")
    peak = pd.to_numeric(out.forecast_peak_delta_hours_local, errors="coerce")
    out["peak_clock_state"] = np.select([peak.gt(1), peak.lt(-1), peak.notna()], ["pre_peak", "post_peak", "near_peak"], default="unknown")
    rh = pd.to_numeric(out.get("atlas_relative_humidity_pct"), errors="coerce")
    dew = pd.to_numeric(out.get("atlas_dewpoint_depression_f"), errors="coerce")
    wind = pd.to_numeric(out.get("atlas_wind_speed_kt"), errors="coerce")
    out["atlas_moisture_regime"] = np.select([rh.ge(75), dew.ge(18), rh.notna()], ["humid", "dry", "mixed"], default="unknown")
    out["atlas_wind_regime"] = np.select([wind.ge(15), wind.ge(7), wind.notna()], ["windy", "breezy", "light"], default="unknown")
    categorical = sorted({column for spec in VARIANTS.values() for column in spec["categorical"]})
    for column in categorical:
        out[column] = out[column].fillna("unknown").astype(str)
    return out


def make_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(("num", Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)), ("scale", StandardScaler())]), numeric))
    if categorical:
        transformers.append(("cat", Pipeline([("imputer", SimpleImputer(strategy="constant", fill_value="unknown")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical))
    return Pipeline([("features", ColumnTransformer(transformers, remainder="drop")), ("clf", LogisticRegression(C=MODEL_C, max_iter=3000))])


def _probability_frame(test: pd.DataFrame, probabilities: np.ndarray, classes: list[str], variant: str, route: str) -> pd.DataFrame:
    out = test[["state_row_id", *KEYS, "decision_hour_local", "actual_bucket", "unit"]].copy()
    expanded = np.full((len(test), len(BUCKETS)), EPS, dtype=float)
    for i, bucket in enumerate(BUCKETS):
        if bucket in classes:
            expanded[:, i] = probabilities[:, classes.index(bucket)]
    expanded /= expanded.sum(axis=1, keepdims=True)
    for i, bucket in enumerate(BUCKETS):
        out[f"p_{bucket}"] = expanded[:, i]
    out["variant"] = variant
    out["route"] = route
    out["scope"] = SCOPE
    return out


def expanding_predictions(frame: pd.DataFrame, variant: str, spec: dict[str, Any]) -> pd.DataFrame:
    pieces = []
    columns = spec["numeric"] + spec["categorical"]
    for date in sorted(frame.target_date.unique()):
        train, test = frame[frame.target_date < date], frame[frame.target_date == date]
        if train.target_date.nunique() < MIN_TRAIN_DATES or test.empty or train.actual_bucket.nunique() < 2:
            continue
        model = make_pipeline(spec["numeric"], spec["categorical"])
        model.fit(train[columns], train.actual_bucket.astype(str))
        probs = model.predict_proba(test[columns])
        pieces.append(_probability_frame(test, probs, list(model.named_steps["clf"].classes_), variant, "raw"))
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def market_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame[frame[MARKET].notna().all(axis=1)].copy()
    probs = valid[MARKET].to_numpy(float)
    probs = np.clip(probs, EPS, None); probs /= probs.sum(axis=1, keepdims=True)
    return _probability_frame(valid, probs, BUCKETS, "market_only", "market")


def blend_predictions(raw: pd.DataFrame, market: pd.DataFrame, alpha: float) -> pd.DataFrame:
    joined = raw.merge(market[["state_row_id"] + [f"p_{b}" for b in BUCKETS]], on="state_row_id", suffixes=("_raw", "_market"), validate="one_to_one")
    out = raw.loc[raw.state_row_id.isin(joined.state_row_id)].set_index("state_row_id").loc[joined.state_row_id].reset_index()
    values = np.column_stack([(1 - alpha) * joined[f"p_{b}_market"] + alpha * joined[f"p_{b}_raw"] for b in BUCKETS])
    values /= values.sum(axis=1, keepdims=True)
    for i, bucket in enumerate(BUCKETS): out[f"p_{bucket}"] = values[:, i]
    out["route"] = f"market_blend_alpha_{alpha:g}"
    return out


def coherent_oof_calibration(raw: pd.DataFrame, frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Second stage sees only earlier-date first-stage OOF predictions."""
    source = raw.merge(frame[["state_row_id"] + MARKET + ["ladder_overround", "geom_current_yes_spread", "geom_d1_yes_spread", "geom_d2_yes_spread"]], on="state_row_id", how="left", validate="one_to_one")
    features = [f"log_raw_{b}" for b in BUCKETS] + MARKET + ["ladder_overround", "geom_current_yes_spread", "geom_d1_yes_spread", "geom_d2_yes_spread"]
    for bucket in BUCKETS: source[f"log_raw_{bucket}"] = np.log(source[f"p_{bucket}"].clip(EPS, 1))
    pieces = []
    for date in sorted(source.target_date.unique()):
        train, test = source[source.target_date < date], source[source.target_date == date]
        if train.target_date.nunique() < MIN_TRAIN_DATES or test.empty or train.actual_bucket.nunique() < 2:
            identity = test.copy()
            identity["route"] = "coherent_identity_insufficient_past_oof"
            pieces.append(identity[raw.columns])
            continue
        model = make_pipeline(features, [])
        model.fit(train[features], train.actual_bucket.astype(str))
        pred = _probability_frame(test, model.predict_proba(test[features]), list(model.named_steps["clf"].classes_), variant, "coherent_past_oof")
        pieces.append(pred)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def score_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    actual_index = predictions.actual_bucket.map({b: i for i, b in enumerate(BUCKETS)}).to_numpy(int)
    probs = predictions[[f"p_{b}" for b in BUCKETS]].to_numpy(float)
    out = predictions[["state_row_id", *KEYS, "actual_bucket", "unit", "variant", "route", "scope"]].copy()
    out["logloss"] = -np.log(np.clip(probs[np.arange(len(out)), actual_index], EPS, 1))
    out["brier"] = ((probs - np.eye(len(BUCKETS))[actual_index]) ** 2).sum(axis=1)
    return out


def paired_date_ci(joined: pd.DataFrame, column: str, seed: int = 20260712) -> tuple[float, float, float]:
    daily = joined.groupby("target_date")[column].mean().to_numpy(float)
    point = float(joined[column].mean())
    if len(daily) < 3: return point, math.nan, math.nan
    rng = np.random.default_rng(seed); samples = []
    for _ in range(5000): samples.append(float(rng.choice(daily, size=len(daily), replace=True).mean()))
    return point, float(np.quantile(samples, .025)), float(np.quantile(samples, .975))


def score_summary(scores: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    market = scores[(scores.variant == "market_only") & (scores.route == "market")][["state_row_id", "logloss", "brier"]].rename(columns={"logloss": "market_logloss", "brier": "market_brier"})
    rows, paired_rows = [], []
    for (variant, route), group in scores.groupby(["variant", "route"]):
        paired = group.merge(market, on="state_row_id", how="inner")
        paired["delta_logloss"] = paired.logloss - paired.market_logloss
        paired["delta_brier"] = paired.brier - paired.market_brier
        dl, dll, dlh = paired_date_ci(paired, "delta_logloss") if len(paired) else (math.nan,) * 3
        db, dbl, dbh = paired_date_ci(paired, "delta_brier") if len(paired) else (math.nan,) * 3
        rows.append({"variant": variant, "route": route, "rows": len(group), "dates": group.target_date.nunique(), "paired_market_rows": len(paired), "logloss": group.logloss.mean(), "brier": group.brier.mean(), "delta_logloss": dl, "delta_logloss_ci_low": dll, "delta_logloss_ci_high": dlh, "delta_brier": db, "delta_brier_ci_low": dbl, "delta_brier_ci_high": dbh})
        if len(paired): paired_rows.append(paired.assign(variant=variant, route=route))
    return pd.DataFrame(rows), pd.concat(paired_rows, ignore_index=True) if paired_rows else pd.DataFrame()


def execution_policy(frame: pd.DataFrame, predictions: pd.DataFrame, variant: str, route: str) -> pd.DataFrame:
    """First eligible snapshot independently within one variant/route arm."""
    joined = frame.merge(predictions[["state_row_id"] + [f"p_{b}" for b in BUCKETS]], on="state_row_id", how="inner", validate="one_to_one")
    candidates = []
    expressions = {
        "current_no": ("current", "current_no_ask", "current_no_ask_size", "NO"),
        "d1_no": ("d1", "d1_no_ask", "d1_no_ask_size", "NO"),
        "d2_no": ("d2", "d2_no_ask", "d2_no_ask_size", "NO"),
        "d1_yes": ("d1", "d1_yes_direct_ask", "d1_yes_direct_ask_size", "YES"),
        "d2_yes": ("d2", "d2_yes_direct_ask", "d2_yes_direct_ask_size", "YES"),
    }
    for row in joined.to_dict("records"):
        for expression, (bucket, ask_col, size_col, side) in expressions.items():
            ask, size = finite(row.get(ask_col)), finite(row.get(size_col))
            if ask is None or size is None or size < SHARES or not ASK_FLOOR <= ask <= ASK_CEILING: continue
            pwin = float(row[f"p_{bucket}"]); pwin = 1.0 - pwin if side == "NO" else pwin
            fee = official_fee(ask); edge = pwin - ask - fee
            if edge < EDGE: continue
            win = float(row["actual_bucket"] != bucket) if side == "NO" else float(row["actual_bucket"] == bucket)
            candidates.append({"variant": variant, "route": route, "state_row_id": row["state_row_id"], "city": row["city"], "target_date": row["target_date"], "unit": row["unit"], "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"], "expression": expression, "side": side, "ask": ask, "ask_size": size, "shares": SHARES, "p_win": pwin, "edge": edge, "fee_per_share": fee, "cost": SHARES * (ask + fee), "win": win, "pnl": SHARES * (win - ask - fee)})
    eligible = pd.DataFrame(candidates)
    if eligible.empty: return eligible
    first_snapshot = eligible.sort_values(["city", "target_date", "decision_snapshot_ts_utc", "edge"], ascending=[True, True, True, False]).groupby(["city", "target_date"], as_index=False).head(1)
    assert not first_snapshot.duplicated(["city", "target_date"]).any()
    assert first_snapshot.ask_size.ge(SHARES).all()
    return first_snapshot


def roi_ci(group: pd.DataFrame, seed: int = 20260712) -> tuple[float, float, float]:
    if group.empty: return math.nan, math.nan, math.nan
    daily = group.groupby("target_date")[["pnl", "cost"]].sum().to_numpy(float)
    point = float(group.pnl.sum() / group.cost.sum())
    if len(daily) < 3: return point, math.nan, math.nan
    rng = np.random.default_rng(seed); values = []
    for _ in range(5000):
        sample = daily[rng.integers(0, len(daily), len(daily))]
        values.append(float(sample[:, 0].sum() / sample[:, 1].sum()))
    return point, float(np.quantile(values, .025)), float(np.quantile(values, .975))


def execution_summary(executions: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in executions.groupby(by, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,); roi, low, high = roi_ci(group)
        rows.append({**dict(zip(by, keys)), "rows": len(group), "dates": group.target_date.nunique(), "wins": group.win.sum(), "cost": group.cost.sum(), "pnl": group.pnl.sum(), "roi": roi, "roi_ci_low": low, "roi_ci_high": high})
    return pd.DataFrame(rows)


def transition_tables(executions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = executions[(executions.variant == "minimal_clean") & (executions.route == "raw")].copy()
    rows, removed = [], []
    for (variant, route), group in executions.groupby(["variant", "route"]):
        if variant == "minimal_clean" and route == "raw": continue
        merged = base.merge(group, on=["city", "target_date"], how="outer", suffixes=("_minimal", "_candidate"), indicator=True)
        merged["transition"] = np.where(merged._merge == "left_only", "cancelled", np.where(merged._merge == "right_only", "added", np.where(merged.expression_minimal == merged.expression_candidate, "same", "switched_expression")))
        rows.extend(merged.groupby("transition", dropna=False).agg(rows=("city", "size"), minimal_pnl=("pnl_minimal", "sum"), candidate_pnl=("pnl_candidate", "sum")).reset_index().assign(variant=variant, route=route).to_dict("records"))
        gone = merged[merged.transition == "cancelled"].copy(); gone["removed_pnl"] = gone["pnl_minimal"]
        removed.append(gone.assign(variant=variant, route=route))
    top = pd.concat(removed, ignore_index=True).sort_values("removed_pnl").head(10) if removed else pd.DataFrame()
    return pd.DataFrame(rows), top


def feature_coverage(frame: pd.DataFrame, asof_meta: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for variant, spec in VARIANTS.items():
        for column in spec["numeric"] + spec["categorical"]:
            rows.append({"variant": variant, "feature": column, "kind": "numeric" if column in spec["numeric"] else "categorical", "provenance": spec["provenance"], "non_null_rows": int(frame[column].notna().sum()), "coverage": float(frame[column].notna().mean())})
    rows.append({"variant": "atlas_meteo_regime_upper_bound", "feature": "factory_asof_match", "kind": "lineage", "provenance": "report_time_reconstruction_only", "non_null_rows": asof_meta["exact"] + asof_meta["asof_prior"], "coverage": asof_meta["coverage"]})
    return pd.DataFrame(rows)


def _md_table(frame: pd.DataFrame, columns: list[str], digits: int = 4) -> str:
    if frame.empty: return "_No rows._"
    view = frame[columns].copy()
    for column in view.select_dtypes(include=["float"]).columns: view[column] = view[column].map(lambda x: "" if pd.isna(x) else f"{x:.{digits}f}")
    labels = [str(column) for column in view.columns]
    body = ["| " + " | ".join(labels) + " |", "|" + "|".join(["---"] * len(labels)) + "|"]
    for values in view.astype(object).where(view.notna(), "").itertuples(index=False, name=None):
        body.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    return "\n".join(body)


def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    enriched_path = OUT_DIR / "enriched_clean_states.csv"
    if enriched_path.exists():
        states = pd.read_csv(enriched_path)
        if len(states) != 8094 or "geom_current_yes_ask" not in states or any(column.endswith(("_x", "_y")) for column in states.columns):
            raise AssertionError("existing enriched cache is incompatible; remove it and rerun")
        states["decision_snapshot_ts_utc"] = pd.to_datetime(states["decision_snapshot_ts_utc"], utc=True)
        states["factory_feature_snapshot_ts_utc"] = pd.to_datetime(states["factory_feature_snapshot_ts_utc"], utc=True, errors="coerce")
        quote_matched = int(states.quote_geometry_snapshot_ts_utc.notna().sum())
        quote_meta = {"matched": quote_matched, "missing": len(states)-quote_matched, "coverage": quote_matched/len(states)}
        counts = states.factory_match_type.fillna("missing").value_counts().to_dict()
        lag = pd.to_numeric(states.loc[states.factory_match_type.isin(["exact", "asof_prior"]), "factory_lag_min"], errors="coerce").dropna()
        asof_meta = {
            "exact": int(counts.get("exact", 0)), "asof_prior": int(counts.get("asof_prior", 0)),
            "stale_over_60m": int(counts.get("stale_over_60m", 0)), "missing": int(counts.get("missing", 0)),
            "coverage": float(states.factory_match_type.isin(["exact", "asof_prior"]).mean()),
            "lag_min_median": float(lag.median()) if len(lag) else None, "lag_min_p95": float(lag.quantile(.95)) if len(lag) else None,
            "gfs_non_null": int(states.backfill_gfs_forecast_gap_to_running_f.notna().sum()),
            "ecmwf_non_null": int(states.backfill_ecmwf_forecast_gap_to_running_f.notna().sum()),
        }
    else:
        states = load_frozen_states()
        states, quote_meta = enrich_quote_geometry(states)
        factory = load_feature_factory_rows()
        states, asof_meta = strict_asof_enrich(states, factory)
    states = add_context_features(states)
    states.to_csv(enriched_path, index=False)
    coverage_daily = states.groupby("target_date", as_index=False).agg(
        clean_rows=("state_row_id", "size"),
        meteo60_rows=("atlas_relative_humidity_pct", lambda x: int(x.notna().sum())),
        gfs60_rows=("backfill_gfs_forecast_peak_delta_hours_local", lambda x: int(x.notna().sum())),
        ecmwf60_rows=("backfill_ecmwf_forecast_peak_delta_hours_local", lambda x: int(x.notna().sum())),
    )
    coverage_daily["dual_model60_rows"] = np.minimum(coverage_daily.gfs60_rows, coverage_daily.ecmwf60_rows)
    coverage_daily.to_csv(OUT_DIR / "asof_60m_coverage_daily.csv", index=False)

    market = market_predictions(states)
    prediction_frames = [market]
    raw_by_variant: dict[str, pd.DataFrame] = {}
    for variant, spec in VARIANTS.items():
        if variant == "gfs_ecmwf_backfill_upper_bound":
            continue
        raw = expanding_predictions(states, variant, spec); raw_by_variant[variant] = raw; prediction_frames.append(raw)
        prediction_frames.append(blend_predictions(raw, market, .25))
        prediction_frames.append(blend_predictions(raw, market, .5))
        prediction_frames.append(coherent_oof_calibration(raw, states, variant))
    predictions = pd.concat([x for x in prediction_frames if not x.empty], ignore_index=True)
    predictions.to_csv(OUT_DIR / "expanding_predictions.csv", index=False)
    scores = score_rows(predictions); scores.to_csv(OUT_DIR / "state_score_rows.csv", index=False)
    summary, paired = score_summary(scores); summary.to_csv(OUT_DIR / "proper_score_summary.csv", index=False); paired.to_csv(OUT_DIR / "paired_score_rows.csv", index=False)
    scores.groupby(["variant", "route", "target_date"], as_index=False).agg(rows=("state_row_id", "size"), logloss=("logloss", "mean"), brier=("brier", "mean")).to_csv(OUT_DIR / "proper_score_daily.csv", index=False)
    scores.groupby(["variant", "route", "unit"], as_index=False).agg(rows=("state_row_id", "size"), logloss=("logloss", "mean"), brier=("brier", "mean")).to_csv(OUT_DIR / "proper_score_unit.csv", index=False)

    executions = []
    for (variant, route), pred in predictions.groupby(["variant", "route"]):
        executions.append(execution_policy(states, pred, variant, route))
    execution = pd.concat([x for x in executions if not x.empty], ignore_index=True) if any(not x.empty for x in executions) else pd.DataFrame()
    execution.to_csv(OUT_DIR / "execution_first_lock_rows.csv", index=False)
    total = execution_summary(execution, ["variant", "route"]); total.to_csv(OUT_DIR / "execution_summary.csv", index=False)
    execution_summary(execution, ["variant", "route", "expression"]).to_csv(OUT_DIR / "execution_expression.csv", index=False)
    execution_summary(execution, ["variant", "route", "side"]).to_csv(OUT_DIR / "execution_yes_no.csv", index=False)
    execution_summary(execution, ["variant", "route", "unit"]).to_csv(OUT_DIR / "execution_unit.csv", index=False)
    execution.groupby(["variant", "route", "target_date"], as_index=False).agg(rows=("city", "size"), cost=("cost", "sum"), pnl=("pnl", "sum")).assign(roi=lambda x: x.pnl/x.cost).to_csv(OUT_DIR / "execution_daily.csv", index=False)
    transitions, removed = transition_tables(execution); transitions.to_csv(OUT_DIR / "overlap_transitions_vs_minimal.csv", index=False); removed.to_csv(OUT_DIR / "top10_removed_vs_minimal.csv", index=False)
    coverage = feature_coverage(states, asof_meta); coverage.to_csv(OUT_DIR / "feature_coverage_provenance.csv", index=False)

    not_answerable = pd.DataFrame([{
        "variant": "gfs_ecmwf_backfill_upper_bound", "route": "not_answerable",
        "rows": 0, "dates": 0, "paired_market_rows": 0, "logloss": np.nan, "brier": np.nan,
        "delta_logloss": np.nan, "delta_logloss_ci_low": np.nan, "delta_logloss_ci_high": np.nan,
        "delta_brier": np.nan, "delta_brier_ci_low": np.nan, "delta_brier_ci_high": np.nan,
    }])
    summary = pd.concat([summary, not_answerable], ignore_index=True)
    summary.to_csv(OUT_DIR / "proper_score_summary.csv", index=False)
    gates = summary.merge(total, on=["variant", "route"], how="left")
    gates["statistical_gate"] = np.where(gates.delta_logloss_ci_high < 0, "PASS", "FAIL")
    gates["economics_gate"] = np.where(gates.roi_ci_low > 0, "PASS", "FAIL")
    gates["fresh_forward_gate"] = "FAIL"
    gates["fresh_forward_dates"] = 0
    gates["promotion"] = "FAIL_SHADOW_ONLY"
    gates.to_csv(OUT_DIR / "three_gates.csv", index=False)

    state_hash = hashlib.sha256(STATE_PATH.read_bytes()).hexdigest()
    d2 = execution[(execution.expression == "d2_no")].groupby(["variant", "route"], as_index=False).agg(rows=("city", "size"), pnl=("pnl", "sum"), cost=("cost", "sum")); d2["roi"] = d2.pnl/d2.cost
    payload = {
        "title": "Tmax clean denominator feature restoration v1",
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "scope": SCOPE,
        "frozen_denominator": {"rows": len(states), "dates": states.target_date.nunique(), "min_date": states.target_date.min(), "max_date": states.target_date.max(), "source_sha256": state_hash, "market_complete_rows": int(states[MARKET].notna().all(axis=1).sum())},
        "quote_geometry": quote_meta,
        "feature_factory_strict_asof": asof_meta,
        "asof_60m_coverage_by_date": coverage_daily.to_dict("records"),
        "model": {"logistic_c": MODEL_C, "min_prior_dates": MIN_TRAIN_DATES, "selection": "none", "fresh_forward_dates": 0},
        "provenance": {variant: spec["provenance"] for variant, spec in VARIANTS.items()},
        "d2_no_reference": {"prior_minimal_rows": 174, "current": d2.to_dict("records")},
        "verdict": {"strict_primary": "diagnostic_only_no_fresh_forward", "atlas_upper_bound": "research_only_not_live_parity", "gfs_ecmwf_upper_bound": "not_answerable_no_6_21_plus_pit_coverage", "live_change": False},
        "artifacts": sorted(path.name for path in OUT_DIR.iterdir()),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    strict = summary[summary.variant.isin(["minimal_clean", "strict_quote_geometry", "strict_source_context"])]
    research = summary[summary.variant.isin(["atlas_meteo_regime_upper_bound", "gfs_ecmwf_backfill_upper_bound"])]
    focus = summary.set_index(["variant", "route"])
    minimal25 = focus.loc[("minimal_clean", "market_blend_alpha_0.25")]
    quote25 = focus.loc[("strict_quote_geometry", "market_blend_alpha_0.25")]
    source25 = focus.loc[("strict_source_context", "market_blend_alpha_0.25")]
    atlas25 = focus.loc[("atlas_meteo_regime_upper_bound", "market_blend_alpha_0.25")]
    report = f"""# Tmax Clean Denominator Feature Restoration v1

## 结论

- 本轮固定复用 lineage replay v2 的 **8,094** 行 clean states（{states.target_date.min()}..{states.target_date.max()}，{states.target_date.nunique()} dates），没有重选 snapshot 或分母。
- 所有模型结果统一标为 `{SCOPE}`；fresh forward = 0，所有 artifact 仅 shadow research，**不改 live**。
- strict primary 是 B/C/D；E 是 `report_time_reconstruction_only`，F 是 `research_backfill`，两者只作 research upper-bound，不能与 strict 候选混称。
- 这是 `tmax_distribution_v3` work order 的 feature availability / fixed ablation 前置证据，不是另一个模型任务。结论是：恢复 strict quote/source 后仍未改善 market proper score；archive meteo upper-bound 也没有显示增量，F 则不可答。
- paired alpha=.25 logloss delta vs market：minimal {minimal25.delta_logloss:+.4f}（CI [{minimal25.delta_logloss_ci_low:+.4f}, {minimal25.delta_logloss_ci_high:+.4f}]）、strict quote {quote25.delta_logloss:+.4f}、strict source {source25.delta_logloss:+.4f}、archive meteo {atlas25.delta_logloss:+.4f}；正值均代表比 market 更差。不能把 execution ROI 点估倒推成 feature selection。

## PIT Enrichment

| enrichment | exact | asof prior | missing | coverage | median lag min | p95 lag min |
|---|---:|---:|---:|---:|---:|---:|
| feature factory city/date + timestamp as-of, lag<=60m | {asof_meta['exact']} | {asof_meta['asof_prior']} | {asof_meta['missing'] + asof_meta['stale_over_60m']} | {asof_meta['coverage']:.1%} | {asof_meta['lag_min_median'] if asof_meta['lag_min_median'] is not None else 'NA'} | {asof_meta['lag_min_p95'] if asof_meta['lag_min_p95'] is not None else 'NA'} |

- as-of 以 timestamp 为权威，在同 city/date 内只取 `feature_snapshot_ts <= clean decision_snapshot_ts` 且 lag<=60m 的最近一条；允许整点边界命中前一 local-hour，并另存 hour-match 审计字段。后到的 atlas snapshot 一律不使用。无法恢复的行继续保留，由 imputer/unknown 处理。
- GFS gap strict-asof且lag<=60m non-null={asof_meta['gfs_non_null']}/8094；ECMWF={asof_meta['ecmwf_non_null']}/8094。双模型只出现在 6/19..6/20，6/21+ 为 0，因此 F **not_answerable**，不训练、不输出 imputation 伪结果。meteo 只作 upper-bound，并显式加入 missing indicator；逐日 coverage 见 `asof_60m_coverage_daily.csv`。
- quote geometry 从 raw decision-group cache 的 exact `decision_snapshot_ts` 构造：matched={quote_meta['matched']}，missing={quote_meta['missing']}；current/d1/d2 与完整 ladder 均来自同 snapshot。

## Proper Score

同一 route 的 delta 只在 market probability 完整的 paired rows 上计算，CI 为 date-block bootstrap。模型自身可在 8,094 corpus 上经 median/unknown 处理；market baseline 可评分分母为 {payload['frozen_denominator']['market_complete_rows']}。

### Strict Primary

{_md_table(strict, ['variant','route','rows','paired_market_rows','logloss','brier','delta_logloss','delta_logloss_ci_low','delta_logloss_ci_high'])}

### Research Upper-bound

{_md_table(research, ['variant','route','rows','paired_market_rows','logloss','brier','delta_logloss','delta_logloss_ci_low','delta_logloss_ci_high'])}

## Execution First-lock

- 每个 `variant + route` arm 独立按 city-day 的 snapshot 顺序扫描，首次有任一固定表达满足 ask/fee/edge 即锁定；arm 之间不抢第一笔。
- 5 shares；YES 只用 direct YES ask；ask size 必须 finite 且 >=5；official fee=`0.05*p*(1-p)`；ask 0.40..0.99；edge >=0.02。

{_md_table(total, ['variant','route','rows','dates','pnl','roi','roi_ci_low','roi_ci_high'])}

## d2_no

旧 minimal reference 是 174 笔；本轮各固定 variant/route 如下。改善与否只描述，不用于选择 spec/alpha/threshold。

{_md_table(d2, ['variant','route','rows','pnl','cost','roi'])}

## 三道门与裁决

- statistical gate：paired date-block logloss delta CI upper < 0。
- economics gate：5-share fee-adjusted ROI date-block CI lower > 0。
- fresh-forward gate：固定 FAIL（fresh dates=0）。因此 promotion 全部 FAIL，artifact 只 shadow。
- coherent second-stage 只使用更早日期的 expanding OOF prediction；不足 5 个既有 OOF dates 时 identity/skip，绝不读取测试日 label。

完整逐日、表达、YES/NO、C/F、transition、top10 removed、coverage/provenance 与三道门见 `generated/tmax_clean_feature_restoration_v1/`。
"""
    OUT_MD.write_text(report)
    return payload


def main() -> int:
    payload = run()
    print(json.dumps({"rows": payload["frozen_denominator"]["rows"], "dates": payload["frozen_denominator"]["dates"], "asof": payload["feature_factory_strict_asof"], "report": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
