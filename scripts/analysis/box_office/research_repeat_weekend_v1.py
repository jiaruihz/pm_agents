#!/usr/bin/env python3
"""Multi-year, pre-2026-locked repeat-weekend box-office study.

Model selection uses 2023 only, 2024 is an untouched internal validation and
probability-calibration year, and 2026 Polymarket events are evaluated in
chronological expanding-window order.  Every feature is available by target
Thursday 16:00 UTC and uses domestic daily observations through Wednesday.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.inspection import permutation_importance
from sklearn.preprocessing import StandardScaler

import research_repeat_weekend_v0 as v0


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v1"
V0_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v0"
YEARS = (2017, 2018, 2019, 2022, 2023, 2024)
MIN_OPENING = 5_000_000.0
PUBLIC_DAILY = DEFAULT_OUT / "revenues_per_day_2025-01-04.csv.gz"
FULL_EVENT_RE = re.compile(
    r"^[\"'“](?P<movie>.+?)[\"'”]\s+"
    r"(?P<week>2nd|second|3rd|third|4th|fourth|5th|fifth)"
    r"(?:\s+3-Day)?\s+Weekend Box Office",
    re.I,
)

FEATURES = [
    "week",
    "log_opening_weekend",
    "log_prev_weekend",
    "log_target_mw",
    "log_weekday_to_prev_weekend",
    "log_weekday_hold",
    "has_weekday_hold",
    "log_prior_hold",
    "has_prior_hold",
    "mon_hold",
    "tue_hold",
    "wed_hold",
    "target_tue_over_mon",
    "target_wed_over_tue",
    "prev_fri_share",
    "prev_sat_share",
    "prev_sun_share",
    "target_theater_ratio",
    "has_theater_ratio",
    "target_avg_rank",
    "rank_change",
    "holiday_current_week",
    "holiday_previous_week",
    "summer",
    "year_centered",
    "doy_sin",
    "doy_cos",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cached-only", action="store_true")
    parser.add_argument("--public-daily", type=Path, default=PUBLIC_DAILY)
    return parser.parse_args()


def parse_money(value: str) -> float | None:
    match = re.search(r"\$([\d,]+)", value)
    return float(match.group(1).replace(",", "")) if match else None


def parse_int(value: str) -> int | None:
    text = re.sub(r"[^0-9]", "", v0.clean_text(value))
    return int(text) if text else None


def parse_year_releases(page: str, year: int, min_opening: float = MIN_OPENING) -> list[dict[str, Any]]:
    releases = []
    for raw_row in re.findall(r"<tr>.*?</tr>", page, flags=re.S):
        match = re.search(r'href="/release/(rl\d+)/[^\"]*"[^>]*>(.*?)</a>', raw_row, flags=re.S)
        if not match:
            continue
        monies = [
            float(value.replace(",", ""))
            for value in re.findall(r'mojo-field-type-money[^>]*>\s*\$([\d,]+)', raw_row)
        ]
        if len(monies) < 2 or monies[1] < min_opening:
            continue
        releases.append(
            {
                "release_id": match.group(1),
                "movie": v0.clean_text(match.group(2)),
                "year": year,
                "year_total_gross": monies[0],
                "listed_opening_gross": monies[1],
            }
        )
    return releases


def parse_rich_daily(page: str) -> dict[date, dict[str, Any]]:
    result = {}
    for raw_row in re.findall(r"<tr>.*?</tr>", page, flags=re.S):
        date_match = re.search(r'href="/date/(\d{4}-\d{2}-\d{2})/', raw_row)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", raw_row, flags=re.S)
        if not date_match or len(cells) < 7:
            continue
        gross = parse_money(cells[3])
        if gross is None:
            continue
        day = date.fromisoformat(date_match.group(1))
        result[day] = {
            "gross": gross,
            "rank": parse_int(cells[2]),
            "theaters": parse_int(cells[6]),
        }
    if not result:
        raise ValueError("no rich daily rows parsed")
    return result


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_holidays(year: int) -> set[date]:
    return {
        date(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        last_weekday(year, 5, 0),
        date(year, 6, 19),
        date(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 10, 0, 2),
        date(year, 11, 11),
        nth_weekday(year, 11, 3, 4),
        date(year, 12, 25),
    }


def window_values(
    daily: dict[date, dict[str, Any]], days: Iterable[date], field: str
) -> list[float] | None:
    values = [daily.get(day, {}).get(field) for day in days]
    if any(value is None for value in values):
        return None
    return [float(value) for value in values]


def safe_ratio(left: float | None, right: float | None) -> float | None:
    return left / right if left is not None and right is not None and right > 0 else None


def safe_log(value: float | None) -> float:
    return math.log(max(value, 1e-9)) if value is not None else math.nan


def has_holiday(start: date, end: date) -> int:
    holidays = us_holidays(start.year) | us_holidays(end.year)
    return int(any(start <= holiday <= end for holiday in holidays))


def build_row(
    daily: dict[date, dict[str, Any]], release: dict[str, Any], week: int, *, require_target: bool = True
) -> dict[str, Any] | None:
    release_date = min(daily)
    opening_friday = v0.friday_on_or_after(release_date)
    target_friday = opening_friday + timedelta(days=7 * (week - 1))
    weekend_days = [target_friday + timedelta(days=i) for i in range(3)]
    prev_days = [target_friday - timedelta(days=7) + timedelta(days=i) for i in range(3)]
    prior_days = [target_friday - timedelta(days=14) + timedelta(days=i) for i in range(3)]
    target_mw_days = [target_friday - timedelta(days=i) for i in (4, 3, 2)]
    prev_mw_days = [day - timedelta(days=7) for day in target_mw_days]

    target_weekend_values = window_values(daily, weekend_days, "gross")
    prev_weekend_values = window_values(daily, prev_days, "gross")
    prior_weekend_values = window_values(daily, prior_days, "gross")
    target_mw_values = window_values(daily, target_mw_days, "gross")
    prev_mw_values = window_values(daily, prev_mw_days, "gross")
    opening_values = window_values(
        daily, [opening_friday + timedelta(days=i) for i in range(3)], "gross"
    )
    if (
        prev_weekend_values is None
        or target_mw_values is None
        or opening_values is None
        or (require_target and target_weekend_values is None)
    ):
        return None

    target_weekend = sum(target_weekend_values) if target_weekend_values else None
    prev_weekend = sum(prev_weekend_values)
    prior_weekend = sum(prior_weekend_values) if prior_weekend_values else None
    target_mw = sum(target_mw_values)
    prev_mw = sum(prev_mw_values) if prev_mw_values else None
    opening_weekend = sum(opening_values)
    if min(prev_weekend, target_mw, opening_weekend) <= 0:
        return None

    weekday_holds = [safe_ratio(target_mw_values[i], prev_mw_values[i]) for i in range(3)] if prev_mw_values else [None] * 3
    weekday_hold = safe_ratio(target_mw, prev_mw)
    prior_hold = safe_ratio(prev_weekend, prior_weekend)
    target_theaters = window_values(daily, target_mw_days, "theaters")
    prev_theaters = window_values(daily, prev_mw_days, "theaters")
    theater_ratio = (
        safe_ratio(float(np.mean(target_theaters)), float(np.mean(prev_theaters)))
        if target_theaters and prev_theaters
        else None
    )
    target_ranks = window_values(daily, target_mw_days, "rank")
    prev_ranks = window_values(daily, prev_mw_days, "rank")
    avg_rank = float(np.mean(target_ranks)) if target_ranks else None
    rank_change = avg_rank - float(np.mean(prev_ranks)) if avg_rank is not None and prev_ranks else None
    year = target_friday.year
    day_of_year = target_friday.timetuple().tm_yday

    row = {
        "release_id": release["release_id"],
        "movie": release["movie"],
        "source_year": year,
        "week": week,
        "release_date": release_date.isoformat(),
        "target_friday": target_friday.isoformat(),
        "actual_gross_m": target_weekend / 1_000_000 if target_weekend is not None else None,
        "prev_weekend_m": prev_weekend / 1_000_000,
        "weekend_hold": safe_ratio(target_weekend, prev_weekend),
        "opening_weekend_m": opening_weekend / 1_000_000,
        "log_opening_weekend": safe_log(opening_weekend),
        "log_prev_weekend": safe_log(prev_weekend),
        "log_target_mw": safe_log(target_mw),
        "log_weekday_to_prev_weekend": safe_log(safe_ratio(target_mw, prev_weekend)),
        "log_weekday_hold": safe_log(weekday_hold),
        "has_weekday_hold": int(weekday_hold is not None),
        "log_prior_hold": safe_log(prior_hold),
        "has_prior_hold": int(prior_hold is not None),
        "mon_hold": weekday_holds[0],
        "tue_hold": weekday_holds[1],
        "wed_hold": weekday_holds[2],
        "target_tue_over_mon": safe_ratio(target_mw_values[1], target_mw_values[0]),
        "target_wed_over_tue": safe_ratio(target_mw_values[2], target_mw_values[1]),
        "prev_fri_share": prev_weekend_values[0] / prev_weekend,
        "prev_sat_share": prev_weekend_values[1] / prev_weekend,
        "prev_sun_share": prev_weekend_values[2] / prev_weekend,
        "target_theater_ratio": theater_ratio,
        "has_theater_ratio": int(theater_ratio is not None),
        "target_avg_rank": avg_rank,
        "rank_change": rank_change,
        "holiday_current_week": has_holiday(target_friday - timedelta(days=4), target_friday + timedelta(days=2)),
        "holiday_previous_week": has_holiday(target_friday - timedelta(days=11), target_friday - timedelta(days=5)),
        "summer": int(target_friday.month in (6, 7, 8)),
        "year_centered": year - 2021,
        "doy_sin": math.sin(2 * math.pi * day_of_year / 365.25),
        "doy_cos": math.cos(2 * math.pi * day_of_year / 365.25),
    }
    return row


def discover_releases(cache_dir: Path, refresh: bool) -> list[dict[str, Any]]:
    releases = {}
    for year in YEARS:
        page = v0.fetch(
            f"{v0.BOM}/year/{year}/?grossesOption=totalGrosses",
            cache_dir,
            "bom_year",
            str(year),
            refresh=refresh,
            as_json=False,
        )
        for release in parse_year_releases(page, year):
            releases[release["release_id"]] = release
    return sorted(releases.values(), key=lambda row: (row["year"], row["release_id"]))


def collect_release_pages(
    releases: list[dict[str, Any]], cache_dir: Path, refresh: bool, workers: int,
    *, cached_only: bool = False,
) -> tuple[dict[str, dict[date, dict[str, Any]]], list[dict[str, str]]]:
    daily_by_release = {}
    failures = []
    pending = []
    for release in releases:
        primary = v0.cache_path(cache_dir, "bom_release", release["release_id"], "html")
        fallback = v0.cache_path(V0_OUT / "cache", "bom_release", release["release_id"], "html")
        if primary.exists() or fallback.exists():
            try:
                daily_by_release[release["release_id"]] = parse_rich_daily(
                    (primary if primary.exists() else fallback).read_text()
                )
            except Exception as exc:
                failures.append({"release_id": release["release_id"], "movie": release["movie"], "error": str(exc)})
        elif cached_only:
            failures.append({"release_id": release["release_id"], "movie": release["movie"], "error": "not_cached"})
        else:
            pending.append(release)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(
                v0.fetch,
                f"{v0.BOM}/release/{release['release_id']}/",
                cache_dir,
                "bom_release",
                release["release_id"],
                refresh=refresh,
                as_json=False,
            ): release
            for release in pending
        }
        for future in as_completed(jobs):
            release = jobs[future]
            try:
                daily_by_release[release["release_id"]] = parse_rich_daily(future.result())
            except Exception as exc:
                failures.append({"release_id": release["release_id"], "movie": release["movie"], "error": str(exc)})
    return daily_by_release, failures


def dataset_from_releases(
    releases: list[dict[str, Any]], daily_by_release: dict[str, dict[date, dict[str, Any]]]
) -> list[dict[str, Any]]:
    rows = []
    for release in releases:
        daily = daily_by_release.get(release["release_id"])
        if not daily:
            continue
        for week in (2, 3, 4, 5):
            row = build_row(daily, release, week)
            if row:
                rows.append(row)
    return rows


def dataset_from_public_daily(path: Path) -> tuple[list[dict[str, Any]], int]:
    columns = ["date", "title", "revenue", "theaters", "distributor"]
    frame = pd.read_csv(path, usecols=columns, parse_dates=["date"])
    frame = frame[frame.date.dt.year.isin(YEARS)].copy()
    frame["rank"] = frame.groupby("date")["revenue"].rank(method="min", ascending=False)
    rows = []
    accepted_releases = 0
    release_groups = []
    for _, title_group in frame.groupby(["title", "distributor"], dropna=False, sort=False):
        title_group = title_group.sort_values("date").copy()
        run = title_group.date.diff().dt.days.fillna(0).gt(45).cumsum()
        release_groups.extend(group for _, group in title_group.groupby(run, sort=False))
    for group in release_groups:
        group = group.sort_values("date")
        daily = {
            item.date.date(): {
                "gross": float(item.revenue),
                "rank": int(item.rank) if not pd.isna(item.rank) else None,
                "theaters": int(item.theaters) if not pd.isna(item.theaters) else None,
            }
            for item in group.itertuples()
        }
        release_date = min(daily)
        opening_friday = v0.friday_on_or_after(release_date)
        opening = window_values(
            daily, [opening_friday + timedelta(days=i) for i in range(3)], "gross"
        )
        if opening is None or sum(opening) < MIN_OPENING:
            continue
        release = {
            "release_id": f"public:{group.iloc[0].title}:{release_date.isoformat()}",
            "movie": str(group.iloc[0].title),
            "year": release_date.year,
        }
        accepted_releases += 1
        for week in (2, 3, 4, 5):
            row = build_row(daily, release, week)
            if row:
                rows.append(row)
    return rows, accepted_releases


def make_model(name: str, seed: int = 20260808):
    if name == "ridge":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=12.0))
    if name == "hist_gb":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingRegressor(
                loss="squared_error", learning_rate=0.045, max_iter=300, max_leaf_nodes=15,
                min_samples_leaf=25, l2_regularization=2.0, random_state=seed,
            ),
        )
    if name == "extra_trees":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            ExtraTreesRegressor(
                n_estimators=500, min_samples_leaf=5, max_features=0.8, n_jobs=-1, random_state=seed,
            ),
        )
    if name == "gradient_boosting":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            GradientBoostingRegressor(
                loss="huber", n_estimators=350, learning_rate=0.035, max_depth=2,
                min_samples_leaf=12, random_state=seed,
            ),
        )
    raise ValueError(name)


def fit_models(names: list[str], train: pd.DataFrame) -> dict[str, Any]:
    models = {}
    x = train[FEATURES]
    y = np.log(train["weekend_hold"].to_numpy(float))
    for name in names:
        model = make_model(name)
        model.fit(x, y)
        models[name] = model
    return models


def predict_models(models: dict[str, Any], frame: pd.DataFrame) -> dict[str, np.ndarray]:
    return {name: model.predict(frame[FEATURES]) for name, model in models.items()}


def metrics(frame: pd.DataFrame, pred_log: np.ndarray) -> dict[str, float]:
    pred_hold = np.exp(pred_log)
    pred_gross = frame["prev_weekend_m"].to_numpy(float) * pred_hold
    actual = frame["actual_gross_m"].to_numpy(float)
    return {
        "n": int(len(frame)),
        "mape": float(np.mean(np.abs(pred_gross - actual) / actual)),
        "mae_m": float(np.mean(np.abs(pred_gross - actual))),
        "log_hold_rmse": float(np.sqrt(np.mean((pred_log - np.log(frame["weekend_hold"].to_numpy(float))) ** 2))),
    }


def select_model(dataset: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    train = dataset[dataset.source_year <= 2022]
    validation = dataset[dataset.source_year == 2023]
    names = ["ridge", "hist_gb", "extra_trees", "gradient_boosting"]
    models = fit_models(names, train)
    predictions = predict_models(models, validation)
    report = {name: metrics(validation, pred) for name, pred in predictions.items()}
    ranked = sorted(names, key=lambda name: report[name]["mape"])
    ensemble_name = f"ensemble:{ranked[0]}+{ranked[1]}"
    ensemble_pred = 0.5 * predictions[ranked[0]] + 0.5 * predictions[ranked[1]]
    report[ensemble_name] = metrics(validation, ensemble_pred)
    selected = min(report, key=lambda name: report[name]["mape"])
    return selected, report


def base_names(selected: str) -> list[str]:
    return selected.split(":", 1)[1].split("+") if selected.startswith("ensemble:") else [selected]


def fit_selected(selected: str, train: pd.DataFrame) -> dict[str, Any]:
    return fit_models(base_names(selected), train)


def predict_selected(selected: str, models: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    predictions = predict_models(models, frame)
    return np.mean(np.vstack([predictions[name] for name in base_names(selected)]), axis=0)


def rolling_pre2026_predictions(dataset: pd.DataFrame, selected: str) -> pd.DataFrame:
    outputs = []
    for year in (2018, 2019, 2022, 2023, 2024):
        train = dataset[dataset.source_year < year]
        test = dataset[dataset.source_year == year].copy()
        if len(train) < 100 or test.empty:
            continue
        pred = predict_selected(selected, fit_selected(selected, train), test)
        test["pred_log_hold"] = pred
        test["predicted_gross_m"] = test.prev_weekend_m * np.exp(pred)
        test["residual_log_hold"] = np.log(test.weekend_hold) - pred
        outputs.append(test)
    return pd.concat(outputs, ignore_index=True)


def smoothed_residual_probs(
    intervals: list[tuple[float, float]], prev_weekend_m: float, pred_log_hold: float,
    residuals: pd.DataFrame, week: int,
) -> list[float]:
    local = residuals[residuals.week == week].residual_log_hold.to_numpy(float)
    if len(local) < 30:
        local = residuals.residual_log_hold.to_numpy(float)
    robust_sigma = np.median(np.abs(local - np.median(local))) * 1.4826
    bandwidth = max(0.025, 0.18 * robust_sigma)
    probs = []
    for lo, hi in intervals:
        lo_log = -math.inf if math.isinf(lo) else math.log(max(lo / prev_weekend_m, 1e-9))
        hi_log = math.inf if math.isinf(hi) else math.log(max(hi / prev_weekend_m, 1e-9))
        upper = norm.cdf((hi_log - pred_log_hold - local) / bandwidth)
        lower = norm.cdf((lo_log - pred_log_hold - local) / bandwidth)
        probs.append(float(np.mean(upper - lower)))
    total = sum(probs)
    return [value / total for value in probs]


def load_literal(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if pd.isna(value):
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return ast.literal_eval(value)


def parse_full_repeat_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Parse all observed repeat-weekend title variants on Gamma.

    The original v0 parser only accepted double quotes and omitted the
    ``Second 3-Day Weekend`` wording.  That silently dropped two older events.
    """
    match = FULL_EVENT_RE.search(event.get("title", ""))
    if not match:
        return None
    return {
        "event_id": str(event["id"]),
        "event_title": event["title"],
        "event_slug": event["slug"],
        "movie": match.group("movie"),
        "week": v0.WEEK_WORDS[match.group("week").lower()],
        "start_dt": v0.iso_dt(event["startDate"]),
        "end_dt": v0.iso_dt(event["endDate"]),
        "markets": event.get("markets", []),
        "volume": float(event.get("volume") or 0),
    }


def fetch_full_repeat_events(
    cache_dir: Path, *, refresh: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Union tag discovery with Gamma full-text discovery.

    Tag 51 is nearly complete but misses older repeat-weekend events.  Public
    search supplies those; event id is the stable de-duplication key.
    """
    raw_by_id: dict[str, dict[str, Any]] = {}
    for closed in (True, False):
        for event in v0.fetch_events(V0_OUT / "cache", closed=closed, refresh=refresh):
            raw_by_id[str(event["id"])] = event

    for page_number in range(1, 100):
        payload = v0.fetch(
            f"{v0.GAMMA}/public-search?q=weekend%20box%20office&page={page_number}&limit_per_type=50",
            cache_dir,
            "gamma_public_search",
            f"weekend_box_office_{page_number}",
            refresh=refresh,
            as_json=True,
        )
        for event in payload.get("events", []):
            if parse_full_repeat_event(event):
                raw_by_id[str(event["id"])] = event
        if not payload.get("pagination", {}).get("hasMore"):
            break

    parsed = [parse_full_repeat_event(event) for event in raw_by_id.values()]
    repeat = [event for event in parsed if event is not None]
    closed_events = sorted(
        [event for event in repeat if raw_by_id[event["event_id"]].get("closed")],
        key=lambda event: (event["end_dt"], event["event_id"]),
    )
    open_events = sorted(
        [event for event in repeat if not raw_by_id[event["event_id"]].get("closed")],
        key=lambda event: (event["end_dt"], event["event_id"]),
    )
    return closed_events, open_events


def fetch_external_daily(
    v0_dataset: pd.DataFrame, cache_dir: Path, refresh: bool, workers: int,
    *, cached_only: bool = False,
) -> tuple[dict[str, dict[date, dict[str, Any]]], list[dict[str, str]]]:
    releases = [
        {"release_id": release_id, "movie": group.iloc[0].movie, "year": 2026}
        for release_id, group in v0_dataset.groupby("release_id")
    ]
    return collect_release_pages(releases, cache_dir, refresh, workers, cached_only=cached_only)


def build_external_rows(
    v0_dataset: pd.DataFrame, daily: dict[str, dict[date, dict[str, Any]]]
) -> pd.DataFrame:
    rows = []
    for _, source in v0_dataset.iterrows():
        release = {"release_id": source.release_id, "movie": source.movie, "year": 2026}
        row = build_row(daily[source.release_id], release, int(source.week))
        if row:
            rows.append(row)
    return pd.DataFrame(rows).drop_duplicates(["movie", "week", "target_friday"])


def expanding_external_predictions(
    historical: pd.DataFrame, external: pd.DataFrame, rolling: pd.DataFrame, selected: str,
    prior_external: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions = []
    prior_external = prior_external if prior_external is not None else pd.DataFrame()
    residual_pool = pd.concat([rolling, prior_external], ignore_index=True)
    for target_friday in sorted(external.target_friday.unique()):
        test = external[external.target_friday == target_friday].copy()
        earlier = external[external.target_friday < target_friday]
        train = pd.concat([historical, prior_external, earlier], ignore_index=True)
        pred = predict_selected(selected, fit_selected(selected, train), test)
        test["pred_log_hold"] = pred
        test["predicted_gross_m"] = test.prev_weekend_m * np.exp(pred)
        test["residual_log_hold"] = np.log(test.weekend_hold) - pred
        predictions.append(test)
        residual_pool = pd.concat([residual_pool, test], ignore_index=True)
    return pd.concat(predictions, ignore_index=True), residual_pool


def build_avatar_2025_seed(
    v0_dataset: pd.DataFrame,
    external_daily: dict[str, dict[date, dict[str, Any]]],
    historical: pd.DataFrame,
    selected: str,
) -> pd.DataFrame:
    """Create the missing 2025 Avatar week-2 row before scoring 2026.

    Gamma public search contains its repeat-weekend market, while the old tag
    path first observed the movie at week 3.  Its realized week-2 row is also
    legitimate prior information for every 2026 prediction.
    """
    matches = v0_dataset[v0_dataset.movie == "Avatar: Fire and Ash"]
    if matches.empty:
        return pd.DataFrame()
    source = matches.iloc[0]
    release = {
        "release_id": source.release_id,
        "movie": source.movie,
        "year": 2025,
    }
    row = build_row(external_daily[source.release_id], release, 2)
    if row is None:
        return pd.DataFrame()
    frame = pd.DataFrame([row])
    pred = predict_selected(selected, fit_selected(selected, historical), frame)
    frame["pred_log_hold"] = pred
    frame["predicted_gross_m"] = frame.prev_weekend_m * np.exp(pred)
    frame["residual_log_hold"] = np.log(frame.weekend_hold) - pred
    return frame


def prediction_rows_by_event(
    events: list[dict[str, Any]], predictions: pd.DataFrame
) -> dict[tuple[str, int], dict[str, Any]]:
    by_normalized = {
        (v0.norm_title(row.movie), int(row.week)): row._asdict()
        for row in predictions.itertuples(index=False)
    }
    result = {}
    for event in events:
        row = by_normalized.get((v0.norm_title(event["movie"]), int(event["week"])))
        if row is not None:
            result[(event["movie"], int(event["week"]))] = row
    return result


def market_audit_from_partitions(partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for part in partitions:
        prices = [bracket.get("market_price") for bracket in part["brackets"]]
        eligible = (
            part["is_exhaustive"]
            and part["settlement_match"]
            and part["resolved_winner_n"] == 1
            and all(price is not None for price in prices)
        )
        row = part.get("row", {})
        record = {
            "event_id": part["event_id"],
            "event_title": part["event_title"],
            "movie": part["movie"],
            "week": part["week"],
            "target_friday": row.get("target_friday"),
            "actual_gross_m": row.get("actual_gross_m"),
            "is_exhaustive": part["is_exhaustive"],
            "settlement_match": part["settlement_match"],
            "resolved_winner_n": part["resolved_winner_n"],
            "price_complete": all(price is not None for price in prices),
            "price_status": part.get("price_status"),
            "market_cutoff": part.get("market_cutoff"),
            "market_lag_hours": part.get("market_lag_hours"),
            "eligible": eligible,
        }
        if eligible:
            resolved = [bracket["resolved_yes"] for bracket in part["brackets"]]
            record.update(
                {
                    "winner": resolved.index(True),
                    "brackets": [bracket["label"] for bracket in part["brackets"]],
                    "market_raw": prices,
                }
            )
        rows.append(record)
    return rows


def score_market(
    market: pd.DataFrame, predictions: pd.DataFrame, rolling_residuals: pd.DataFrame
) -> list[dict[str, Any]]:
    pred_by_key = {(row.movie, int(row.week)): row for row in predictions.itertuples()}
    scored = []
    for row in market.itertuples():
        if str(row.eligible).lower() != "true":
            continue
        pred = pred_by_key.get((row.movie, int(row.week)))
        if pred is None:
            continue
        # Only residuals that existed before this target weekend are available.
        residuals = rolling_residuals[rolling_residuals.target_friday < pred.target_friday]
        labels = load_literal(row.brackets)
        intervals = [v0.parse_bracket(label) for label in labels]
        probs = smoothed_residual_probs(intervals, pred.prev_weekend_m, pred.pred_log_hold, residuals, int(row.week))
        market_raw = [float(value) for value in load_literal(row.market_raw)]
        market_probs = (np.array(market_raw) / sum(market_raw)).tolist()
        winner = int(row.winner)
        scored.append(
            {
                "event_id": str(row.event_id),
                "event_title": row.event_title,
                "movie": row.movie,
                "week": int(row.week),
                "target_friday": pred.target_friday,
                "actual_gross_m": pred.actual_gross_m,
                "predicted_gross_m": pred.predicted_gross_m,
                "brackets": labels,
                "winner": winner,
                "model_probs": probs,
                "market_raw": market_raw,
                "market_probs": market_probs,
                "model_log_loss": -math.log(max(probs[winner], 1e-12)),
                "market_log_loss": -math.log(max(market_probs[winner], 1e-12)),
                "model_brier": sum((p - float(i == winner)) ** 2 for i, p in enumerate(probs)),
                "market_brier": sum((p - float(i == winner)) ** 2 for i, p in enumerate(market_probs)),
                "model_top_correct": int(np.argmax(probs) == winner),
                "market_top_correct": int(np.argmax(market_probs) == winner),
            }
        )
    return scored


def trade_grid(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for edge_cutoff in (0.05, 0.08, 0.10, 0.15):
        for spread in (0.0, 0.02, 0.05, 0.10, 0.15):
            ledger = trade_ledger(scored, edge_cutoff, spread)
            capital = sum(row["cost"] for row in ledger)
            pnl = sum(row["pnl"] for row in ledger)
            result.append(
                {
                    "edge_cutoff": edge_cutoff,
                    "spread_stress": spread,
                    "trades": len(ledger),
                    "wins": sum(row["won"] for row in ledger),
                    "capital": capital,
                    "pnl": pnl,
                    "roi": pnl / capital if capital else None,
                    "roi_bootstrap_target_weekend": bootstrap_trade_roi(ledger, "target_friday"),
                    "roi_bootstrap_movie": bootstrap_trade_roi(ledger, "movie"),
                }
            )
    return result


def trade_ledger(
    scored: list[dict[str, Any]], edge_cutoff: float, spread: float
) -> list[dict[str, Any]]:
    candidates = []
    for row in scored:
        edges = np.array(row["model_probs"]) - np.array(row["market_raw"])
        pick = int(np.argmax(edges))
        if edges[pick] < edge_cutoff:
            continue
        proxy = float(row["market_raw"][pick])
        fee = 0.05 * proxy * (1 - proxy)
        cost = min(proxy + fee + spread, 0.999)
        won = int(pick == row["winner"])
        candidates.append(
            {
                "event_id": row["event_id"],
                "movie": row["movie"],
                "week": row["week"],
                "target_friday": row["target_friday"],
                "bracket": row["brackets"][pick],
                "model_probability": row["model_probs"][pick],
                "historical_price_proxy": proxy,
                "model_edge": float(edges[pick]),
                "fee": fee,
                "spread_stress": spread,
                "cost": cost,
                "won": won,
                "pnl": won - cost,
            }
        )
    best = {}
    for row in candidates:
        key = (row["movie"], row["week"], row["target_friday"])
        if key not in best or row["model_edge"] > best[key]["model_edge"]:
            best[key] = row
    return list(best.values())


def bootstrap_trade_roi(
    ledger: list[dict[str, Any]], block_field: str, draws: int = 10_000
) -> dict[str, Any]:
    if not ledger:
        return {"blocks": 0, "ci_low": None, "median": None, "ci_high": None}
    blocks = {}
    for row in ledger:
        blocks.setdefault(str(row[block_field]), []).append(row)
    values = list(blocks.values())
    rng = np.random.default_rng(20260808 + len(values))
    samples = []
    for _ in range(draws):
        sampled = [values[index] for index in rng.integers(0, len(values), len(values))]
        capital = sum(row["cost"] for block in sampled for row in block)
        pnl = sum(row["pnl"] for block in sampled for row in block)
        samples.append(pnl / capital)
    return {
        "blocks": len(values),
        "ci_low": float(np.quantile(samples, 0.025)),
        "median": float(np.quantile(samples, 0.5)),
        "ci_high": float(np.quantile(samples, 0.975)),
    }


def interval_calibration(rolling: pd.DataFrame, test_year: int) -> dict[str, Any]:
    test = rolling[rolling.source_year == test_year]
    prior = rolling[rolling.source_year < test_year]
    result = {"n": len(test)}
    for level in (0.5, 0.8, 0.9):
        covered = []
        relative_widths = []
        alpha = (1 - level) / 2
        for row in test.itertuples():
            residual = prior[prior.week == row.week].residual_log_hold.to_numpy(float)
            if len(residual) < 30:
                residual = prior.residual_log_hold.to_numpy(float)
            lo, hi = np.quantile(residual, [alpha, 1 - alpha])
            actual_log = math.log(row.weekend_hold)
            covered.append(row.pred_log_hold + lo <= actual_log <= row.pred_log_hold + hi)
            gross_lo = row.prev_weekend_m * math.exp(row.pred_log_hold + lo)
            gross_hi = row.prev_weekend_m * math.exp(row.pred_log_hold + hi)
            relative_widths.append((gross_hi - gross_lo) / row.actual_gross_m)
        result[str(level)] = {
            "coverage": float(np.mean(covered)),
            "mean_relative_width": float(np.mean(relative_widths)),
        }
    return result


def diagnostics_2024(historical: pd.DataFrame, selected: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train = historical[historical.source_year < 2024]
    test = historical[historical.source_year == 2024]
    feature_sets = {
        "full": FEATURES,
        "no_weekday_comparisons": [
            feature for feature in FEATURES
            if feature not in {
                "log_target_mw", "log_weekday_to_prev_weekend", "log_weekday_hold",
                "has_weekday_hold", "mon_hold", "tue_hold", "wed_hold",
                "target_tue_over_mon", "target_wed_over_tue",
            }
        ],
        "no_theater_or_rank": [
            feature for feature in FEATURES
            if feature not in {"target_theater_ratio", "has_theater_ratio", "target_avg_rank", "rank_change"}
        ],
        "no_calendar": [
            feature for feature in FEATURES
            if feature not in {
                "holiday_current_week", "holiday_previous_week", "summer", "year_centered", "doy_sin", "doy_cos"
            }
        ],
        "core_only": [
            "week", "log_opening_weekend", "log_prev_weekend", "log_target_mw",
            "log_weekday_to_prev_weekend", "log_weekday_hold", "has_weekday_hold",
            "log_prior_hold", "has_prior_hold",
        ],
    }
    ablations = {}
    for label, features in feature_sets.items():
        model = make_model(base_names(selected)[0])
        model.fit(train[features], np.log(train.weekend_hold.to_numpy(float)))
        pred = model.predict(test[features])
        ablations[label] = metrics(test, pred)

    model = make_model(base_names(selected)[0])
    target = np.log(train.weekend_hold.to_numpy(float))
    model.fit(train[FEATURES], target)
    importance = permutation_importance(
        model,
        test[FEATURES],
        np.log(test.weekend_hold.to_numpy(float)),
        scoring="neg_mean_absolute_error",
        n_repeats=10,
        random_state=20260808,
        n_jobs=-1,
    )
    importance_rows = sorted(
        [
            {
                "feature": feature,
                "importance_mean": float(mean_value),
                "importance_std": float(std_value),
            }
            for feature, mean_value, std_value in zip(
                FEATURES, importance.importances_mean, importance.importances_std
            )
        ],
        key=lambda row: row["importance_mean"],
        reverse=True,
    )
    return ablations, importance_rows


def paired_v1_v0(external: pd.DataFrame, v0_dataset: pd.DataFrame) -> dict[str, Any]:
    old = v0_dataset[v0_dataset.predicted_gross_m.notna()][
        ["movie", "week", "target_friday", "predicted_gross_m"]
    ].rename(columns={"predicted_gross_m": "v0_predicted_gross_m"})
    paired = external.merge(old, on=["movie", "week", "target_friday"])
    paired["v1_ape"] = abs(paired.predicted_gross_m - paired.actual_gross_m) / paired.actual_gross_m
    paired["v0_ape"] = abs(paired.v0_predicted_gross_m - paired.actual_gross_m) / paired.actual_gross_m
    blocks = [group for _, group in paired.groupby("target_friday")]
    rng = np.random.default_rng(20260808)
    differences = []
    for _ in range(10_000):
        sampled = [blocks[index] for index in rng.integers(0, len(blocks), len(blocks))]
        differences.append(
            float(np.mean([value for group in sampled for value in (group.v1_ape - group.v0_ape)]))
        )
    return {
        "common_rows": len(paired),
        "target_weekend_blocks": len(blocks),
        "v1_mape": float(paired.v1_ape.mean()),
        "v0_mape": float(paired.v0_ape.mean()),
        "v1_better_fraction": float((paired.v1_ape < paired.v0_ape).mean()),
        "mape_difference_v1_minus_v0_ci": [
            float(np.quantile(differences, 0.025)),
            float(np.quantile(differences, 0.975)),
        ],
    }


def current_predictions(
    historical: pd.DataFrame, external_pred: pd.DataFrame, rolling_residuals: pd.DataFrame,
    external_daily: dict[str, dict[date, dict[str, Any]]], selected: str,
) -> list[dict[str, Any]]:
    current_path = V0_OUT / "current_signals.csv"
    if not current_path.exists() or current_path.stat().st_size == 0:
        return []
    current = pd.read_csv(current_path)
    open_rows = []
    for (movie, week), group in current.groupby(["movie", "week"]):
        release_id = None
        # Reuse exact 2026 title resolution from the v0 closed data where possible.
        matches = external_pred[external_pred.movie == movie]
        if not matches.empty:
            release_id = matches.iloc[0].release_id
        else:
            # The open title may not yet have a closed row; resolve through BOM.
            info = v0.resolve_movie(movie, 2026, V0_OUT / "cache", refresh=False)
            release_id = info["release_id"]
            if release_id not in external_daily:
                page = v0.fetch(
                    info["release_url"], V0_OUT / "cache", "bom_release", release_id,
                    refresh=False, as_json=False,
                )
                external_daily[release_id] = parse_rich_daily(page)
        release = {"release_id": release_id, "movie": movie, "year": 2026}
        feature = build_row(external_daily[release_id], release, int(week), require_target=False)
        if feature:
            open_rows.append(feature)
    if not open_rows:
        return []
    open_frame = pd.DataFrame(open_rows)
    outputs = []
    for feature in open_frame.itertuples():
        train_external = external_pred[external_pred.target_friday < feature.target_friday]
        train = pd.concat([historical, train_external[historical.columns.intersection(train_external.columns)]], ignore_index=True)
        one = pd.DataFrame([feature._asdict()])
        pred_log = float(predict_selected(selected, fit_selected(selected, train), one)[0])
        residuals = rolling_residuals[rolling_residuals.target_friday < feature.target_friday]
        all_market_rows = current[(current.movie == feature.movie) & (current.week == feature.week)]
        for _, market_rows in all_market_rows.groupby("event_id", sort=False):
            intervals = [v0.parse_bracket(label) for label in market_rows.bracket]
            probs = smoothed_residual_probs(
                intervals, feature.prev_weekend_m, pred_log, residuals, int(feature.week)
            )
            for market_row, probability in zip(market_rows.itertuples(), probs):
                ask = float(market_row.best_ask) if not pd.isna(market_row.best_ask) else None
                fee = 0.05 * ask * (1 - ask) if ask is not None else None
                outputs.append(
                    {
                        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
                        "event_id": str(market_row.event_id),
                        "event_title": market_row.event_title,
                        "movie": feature.movie,
                        "week": int(feature.week),
                        "target_friday": feature.target_friday,
                        "predicted_gross_m": feature.prev_weekend_m * math.exp(pred_log),
                        "bracket": market_row.bracket,
                        "model_probability": probability,
                        "best_bid": market_row.best_bid,
                        "best_ask": ask,
                        "all_in_ask": ask + fee if ask is not None else None,
                        "ask_edge": probability - ask - fee if ask is not None else None,
                    }
                )
    return outputs


def bootstrap_diff(scored: list[dict[str, Any]], model: str, market: str) -> dict[str, Any]:
    return v0.block_bootstrap_market_diff(scored, model, market)


def mean_field(rows: list[dict[str, Any]], field: str) -> float | None:
    return float(np.mean([row[field] for row in rows])) if rows else None


def write_csv(path: Path, rows: list[dict[str, Any]] | pd.DataFrame) -> None:
    if isinstance(rows, pd.DataFrame):
        rows.to_csv(path, index=False)
    else:
        v0.write_csv(path, rows)


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    cache_dir = out / "cache"
    out.mkdir(parents=True, exist_ok=True)

    historical_rows, release_count = dataset_from_public_daily(args.public_daily.resolve())
    failures: list[dict[str, str]] = []
    historical = pd.DataFrame(historical_rows)
    selected, selection_report = select_model(historical)
    rolling = rolling_pre2026_predictions(historical, selected)

    internal_2024 = rolling[rolling.source_year == 2024]
    internal_metrics = metrics(internal_2024, internal_2024.pred_log_hold.to_numpy(float))
    internal_by_week = {
        str(week): metrics(group, group.pred_log_hold.to_numpy(float))
        for week, group in internal_2024.groupby("week")
    }

    v0_dataset = pd.read_csv(V0_OUT / "dataset.csv")
    v0_closed = v0_dataset[v0_dataset.actual_gross_m.notna()]
    external_daily, external_failures = fetch_external_daily(
        v0_closed, cache_dir, args.refresh, args.workers, cached_only=args.cached_only
    )
    external = build_external_rows(v0_closed, external_daily)
    avatar_2025 = build_avatar_2025_seed(
        v0_dataset, external_daily, historical, selected
    )
    external_pred, residual_pool = expanding_external_predictions(
        historical, external, rolling, selected, prior_external=avatar_2025
    )
    external_metrics = metrics(external_pred, external_pred.pred_log_hold.to_numpy(float))
    external_by_week = {
        str(week): metrics(group, group.pred_log_hold.to_numpy(float))
        for week, group in external_pred.groupby("week")
    }

    closed_events, open_events = fetch_full_repeat_events(cache_dir, refresh=args.refresh)
    all_predictions = pd.concat([rolling, avatar_2025, external_pred], ignore_index=True)
    row_by_key = prediction_rows_by_event(closed_events, all_predictions)
    partitions = v0.historical_partitions(
        closed_events,
        row_by_key,
        cache_dir,
        refresh=args.refresh,
        workers=args.workers,
    )
    market_audit = market_audit_from_partitions(partitions)
    market = pd.DataFrame(market_audit)
    scored = score_market(market, all_predictions, residual_pool)
    trades = trade_grid(scored)
    primary_ledger = trade_ledger(scored, 0.10, 0.02)
    current = current_predictions(historical, external_pred, residual_pool, external_daily, selected)
    ablations, importance_rows = diagnostics_2024(historical, selected)
    paired_improvement = paired_v1_v0(external_pred, v0_dataset)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "years": YEARS,
        "minimum_opening_gross": MIN_OPENING,
        "public_daily_source": str(args.public_daily.resolve()),
        "release_count": release_count,
        "release_fetch_failures": failures,
        "historical_movie_week_rows": len(historical),
        "selected_model_locked_on_2023": selected,
        "model_selection_2023": selection_report,
        "internal_2024": {
            **internal_metrics,
            "by_week": internal_by_week,
            "interval_calibration": interval_calibration(rolling, 2024),
        },
        "external_2026": {**external_metrics, "by_week": external_by_week, "rows": len(external_pred)},
        "v1_vs_v0_common_external": paired_improvement,
        "diagnostics_2024": {
            "ablations": ablations,
            "top_permutation_importance": importance_rows[:12],
        },
        "external_fetch_failures": external_failures,
        "market_universe": {
            "closed_event_partitions_discovered": len(closed_events),
            "closed_binary_contracts_discovered": sum(len(event["markets"]) for event in closed_events),
            "closed_unique_movie_week": len(
                {(v0.norm_title(event["movie"]), event["week"]) for event in closed_events}
            ),
            "open_event_partitions_discovered": len(open_events),
            "open_binary_contracts_discovered": sum(len(event["markets"]) for event in open_events),
            "events_with_physical_prediction": len(row_by_key),
            "partitions_with_market_history_attempted": len(partitions),
            "eligible_partitions": len(scored),
            "first_closed_end_utc": min(event["end_dt"] for event in closed_events).isoformat(),
            "last_closed_end_utc": max(event["end_dt"] for event in closed_events).isoformat(),
        },
        "market_full_history": {
            "partitions": len(scored),
            "model_log_loss": mean_field(scored, "model_log_loss"),
            "market_log_loss": mean_field(scored, "market_log_loss"),
            "model_brier": mean_field(scored, "model_brier"),
            "market_brier": mean_field(scored, "market_brier"),
            "model_top_accuracy": mean_field(scored, "model_top_correct"),
            "market_top_accuracy": mean_field(scored, "market_top_correct"),
            "log_loss_bootstrap_model_minus_market": bootstrap_diff(scored, "model_log_loss", "market_log_loss"),
            "brier_bootstrap_model_minus_market": bootstrap_diff(scored, "model_brier", "market_brier"),
        },
        "trade_grid": trades,
        "current_signal_rows": len(current),
    }

    write_csv(out / "historical_dataset.csv", historical)
    write_csv(out / "rolling_pre2026_predictions.csv", rolling)
    write_csv(out / "external_2026_predictions.csv", external_pred)
    write_csv(out / "market_full_audit.csv", market_audit)
    write_csv(out / "market_full_comparison.csv", scored)
    # Retain the old filename as a compatibility pointer, but its contents now
    # use the corrected full-history universe rather than v0's gated subset.
    write_csv(out / "market_2026_comparison.csv", scored)
    write_csv(
        out / "market_event_inventory.csv",
        [
            {
                "status": "closed",
                "event_id": event["event_id"],
                "event_title": event["event_title"],
                "movie": event["movie"],
                "week": event["week"],
                "start_utc": event["start_dt"].isoformat(),
                "end_utc": event["end_dt"].isoformat(),
                "binary_contracts": len(event["markets"]),
                "volume": event["volume"],
            }
            for event in closed_events
        ]
        + [
            {
                "status": "open",
                "event_id": event["event_id"],
                "event_title": event["event_title"],
                "movie": event["movie"],
                "week": event["week"],
                "start_utc": event["start_dt"].isoformat(),
                "end_utc": event["end_dt"].isoformat(),
                "binary_contracts": len(event["markets"]),
                "volume": event["volume"],
            }
            for event in open_events
        ],
    )
    write_csv(out / "current_signals.csv", current)
    write_csv(out / "primary_trade_ledger.csv", primary_ledger)
    write_csv(out / "permutation_importance_2024.csv", importance_rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
