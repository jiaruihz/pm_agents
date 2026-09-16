#!/usr/bin/env python3
"""Point-in-time-ish v0 study for Polymarket repeat-weekend box office.

The signal only uses Monday-Wednesday domestic grosses before the target
weekend.  Outcomes are walked forward by target weekend; rows sharing the
same target weekend never train one another.  Historical CLOB observations
are last recorded prices, not reconstructable executable asks, so trading
results are explicitly stressed for spread and Culture-category fees.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote_plus

import numpy as np
import requests
from scipy.stats import norm


GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BOM = "https://www.boxofficemojo.com"
UA = "Mozilla/5.0 (compatible; pm-agents-box-office-research/0.1)"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v0"
WEEK_WORDS = {
    "2nd": 2,
    "second": 2,
    "3rd": 3,
    "third": 3,
    "4th": 4,
    "fourth": 4,
    "5th": 5,
    "fifth": 5,
}
EVENT_RE = re.compile(
    r'^"(?P<movie>.+?)"\s+(?P<week>2nd|second|3rd|third|4th|fourth|5th|fifth)\s+Weekend Box Office',
    re.I,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=10)
    return parser.parse_args()


def jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", value).strip()


def norm_title(value: str) -> str:
    value = re.sub(r"\(\d{4}\)", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def cache_path(cache_dir: Path, namespace: str, key: str, suffix: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)[:180]
    path = cache_dir / namespace / f"{safe}.{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fetch(
    url: str,
    cache_dir: Path,
    namespace: str,
    key: str,
    *,
    refresh: bool,
    as_json: bool,
) -> Any:
    path = cache_path(cache_dir, namespace, key, "json" if as_json else "html")
    if path.exists() and not refresh:
        return json.loads(path.read_text()) if as_json else path.read_text()
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            response.raise_for_status()
            result = response.json() if as_json else response.text
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) if as_json else result
            )
            return result
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def fetch_events(cache_dir: Path, *, closed: bool, refresh: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for offset in range(0, 1000, 100):
        params = (
            f"tag_id=51&closed={'true' if closed else 'false'}&limit=100&offset={offset}"
            "&order=endDate&ascending=true"
        )
        page = fetch(
            f"{GAMMA}/events?{params}",
            cache_dir,
            "gamma_events",
            f"closed_{closed}_{offset}",
            refresh=refresh,
            as_json=True,
        )
        result.extend(page)
        if len(page) < 100:
            break
    return result


def parse_repeat_event(event: dict[str, Any]) -> dict[str, Any] | None:
    match = EVENT_RE.search(event.get("title", ""))
    if not match:
        return None
    week_raw = match.group("week").lower()
    return {
        "event_id": str(event["id"]),
        "event_title": event["title"],
        "event_slug": event["slug"],
        "movie": match.group("movie"),
        "week": WEEK_WORDS[week_raw],
        "start_dt": iso_dt(event["startDate"]),
        "end_dt": iso_dt(event["endDate"]),
        "markets": event.get("markets", []),
        "volume": float(event.get("volume") or 0),
    }


def parse_search_results(page: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'href="/title/(?P<id>tt\d+)/[^\"]*"[^>]*>(?P<title>.*?)</a>'
        r'<span class="a-color-secondary">\s*\((?P<year>\d{4})\)</span>',
        re.S,
    )
    return [
        {"title_id": m.group("id"), "title": clean_text(m.group("title")), "year": int(m.group("year"))}
        for m in pattern.finditer(page)
    ]


def select_search_result(results: list[dict[str, Any]], movie: str, event_year: int) -> dict[str, Any]:
    target = norm_title(movie)
    if not results:
        raise ValueError(f"no Box Office Mojo search results for {movie}")
    scored = []
    for item in results:
        similarity = SequenceMatcher(None, target, norm_title(item["title"])).ratio()
        year_distance = min(abs(item["year"] - event_year), abs(item["year"] - (event_year - 1)))
        score = similarity - 0.08 * year_distance
        scored.append((score, similarity, item))
    score, similarity, selected = max(scored, key=lambda value: value[:2])
    if similarity < 0.72:
        raise ValueError(f"weak Box Office Mojo title match for {movie}: {selected}")
    return {**selected, "match_score": score}


def parse_domestic_release_id(page: str) -> str:
    match = re.search(r'href="/release/(rl\d+)/[^\"]*"[^>]*>Domestic</a>', page)
    if not match:
        raise ValueError("Domestic release link not found")
    return match.group(1)


def parse_daily_rows(page: str) -> dict[date, float]:
    result: dict[date, float] = {}
    for row in re.findall(r"<tr>.*?</tr>", page, flags=re.S):
        date_match = re.search(r'href="/date/(\d{4}-\d{2}-\d{2})/', row)
        gross_match = re.search(
            r'mojo-field-type-money[^>]*>\s*\$([\d,]+)', row
        )
        if date_match and gross_match:
            result[date.fromisoformat(date_match.group(1))] = float(gross_match.group(1).replace(",", ""))
    if not result:
        raise ValueError("no daily box-office rows parsed")
    return result


def resolve_movie(
    movie: str,
    event_year: int,
    cache_dir: Path,
    *,
    refresh: bool,
) -> dict[str, Any]:
    search_title = re.sub(r"\s*\(\d{4}\)\s*$", "", movie).strip()
    search_page = fetch(
        f"{BOM}/search/?q={quote_plus(search_title)}",
        cache_dir,
        "bom_search",
        search_title,
        refresh=refresh,
        as_json=False,
    )
    selected = select_search_result(parse_search_results(search_page), movie, event_year)
    title_page = fetch(
        f"{BOM}/title/{selected['title_id']}/",
        cache_dir,
        "bom_title",
        selected["title_id"],
        refresh=refresh,
        as_json=False,
    )
    release_id = parse_domestic_release_id(title_page)
    daily_page = fetch(
        f"{BOM}/release/{release_id}/",
        cache_dir,
        "bom_release",
        release_id,
        refresh=refresh,
        as_json=False,
    )
    daily = parse_daily_rows(daily_page)
    return {
        **selected,
        "release_id": release_id,
        "release_url": f"{BOM}/release/{release_id}/",
        "daily": daily,
    }


def friday_on_or_after(value: date) -> date:
    return value + timedelta(days=(4 - value.weekday()) % 7)


def sum_days(daily: dict[date, float], days: Iterable[date]) -> float | None:
    values = [daily.get(day) for day in days]
    if any(value is None for value in values):
        return None
    return float(sum(value for value in values if value is not None))


def feature_row(
    movie_info: dict[str, Any], movie: str, week: int, *, require_outcome: bool = True
) -> dict[str, Any] | None:
    daily = movie_info["daily"]
    release_date = min(daily)
    opening_friday = friday_on_or_after(release_date)
    target_friday = opening_friday + timedelta(days=7 * (week - 1))
    target_weekend = sum_days(daily, (target_friday + timedelta(days=i) for i in range(3)))
    prev_friday = target_friday - timedelta(days=7)
    prev_weekend = sum_days(daily, (prev_friday + timedelta(days=i) for i in range(3)))
    target_mw = sum_days(daily, (target_friday - timedelta(days=i) for i in (4, 3, 2)))
    prev_mw = sum_days(daily, (target_friday - timedelta(days=i) for i in (11, 10, 9)))
    prior_weekend = sum_days(
        daily,
        (target_friday - timedelta(days=14) + timedelta(days=i) for i in range(3)),
    )
    if (
        (require_outcome and target_weekend is None)
        or prev_weekend is None
        or target_mw is None
        or prev_weekend <= 0
    ):
        return None
    weekday_hold = target_mw / prev_mw if prev_mw and prev_mw > 0 else None
    prior_hold = prev_weekend / prior_weekend if prior_weekend and prior_weekend > 0 else None
    return {
        "movie": movie,
        "week": week,
        "release_id": movie_info["release_id"],
        "release_url": movie_info["release_url"],
        "release_date": release_date.isoformat(),
        "target_friday": target_friday.isoformat(),
        "actual_gross_m": target_weekend / 1_000_000 if target_weekend is not None else None,
        "prev_weekend_m": prev_weekend / 1_000_000,
        "target_mw_m": target_mw / 1_000_000,
        "prev_mw_m": prev_mw / 1_000_000 if prev_mw else None,
        "weekday_to_prev_weekend": target_mw / prev_weekend,
        "weekday_hold": weekday_hold,
        "prior_hold": prior_hold,
        "weekend_hold": target_weekend / prev_weekend if target_weekend is not None else None,
    }


def model_vector(row: dict[str, Any]) -> np.ndarray:
    weekday_hold = row.get("weekday_hold")
    prior_hold = row.get("prior_hold")
    return np.array(
        [
            math.log(max(row["weekday_to_prev_weekend"], 1e-6)),
            math.log(max(weekday_hold, 1e-6)) if weekday_hold else 0.0,
            1.0 if weekday_hold else 0.0,
            math.log(max(prior_hold, 1e-6)) if prior_hold else 0.0,
            1.0 if prior_hold else 0.0,
            1.0 if row["week"] == 2 else 0.0,
            1.0 if row["week"] == 3 else 0.0,
            1.0 if row["week"] == 4 else 0.0,
            1.0 if row["week"] == 5 else 0.0,
        ],
        dtype=float,
    )


@dataclass
class RidgeDistribution:
    mean: np.ndarray
    scale: np.ndarray
    beta: np.ndarray
    sigma: float

    def predict_log_hold(self, row: dict[str, Any]) -> float:
        z = (model_vector(row) - self.mean) / self.scale
        return float(np.r_[1.0, z] @ self.beta)


def fit_model(rows: list[dict[str, Any]], alpha: float = 4.0) -> RidgeDistribution:
    x = np.vstack([model_vector(row) for row in rows])
    y = np.log(np.array([row["weekend_hold"] for row in rows], dtype=float))
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    z = (x - mean) / scale
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    residual = y - design @ beta
    sigma = max(float(np.sqrt(np.sum(residual**2) / max(len(y) - 3, 1))), 0.10)
    return RidgeDistribution(mean=mean, scale=scale, beta=beta, sigma=sigma)


def walk_forward(rows: list[dict[str, Any]], min_train: int = 10) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (row["target_friday"], row["movie"], row["week"]))
    result: list[dict[str, Any]] = []
    for row in ordered:
        train = [candidate for candidate in ordered if candidate["target_friday"] < row["target_friday"]]
        output = dict(row)
        output["train_n"] = len(train)
        if len(train) >= min_train:
            model = fit_model(train)
            pred_log = model.predict_log_hold(row)
            predicted_hold = math.exp(pred_log)
            output.update(
                {
                    "predicted_hold": predicted_hold,
                    "predicted_gross_m": row["prev_weekend_m"] * predicted_hold,
                    "pred_log_hold": pred_log,
                    "pred_sigma": model.sigma,
                }
            )
            same_week = [x["weekend_hold"] for x in train if x["week"] == row["week"]]
            baseline_hold = float(np.median(same_week or [x["weekend_hold"] for x in train]))
            output["baseline_gross_m"] = row["prev_weekend_m"] * baseline_hold
        result.append(output)
    return result


def parse_bracket(value: str) -> tuple[float, float]:
    text = value.lower().replace("$", "").replace(" ", "").replace("m", "")
    if text.startswith("<"):
        return -math.inf, float(text[1:])
    if text.startswith(">"):
        return float(text[1:]), math.inf
    if text.endswith("+"):
        return float(text[:-1]), math.inf
    match = re.fullmatch(r"([0-9.]+)-([0-9.]+)", text)
    if not match:
        raise ValueError(f"unrecognized bracket: {value}")
    return float(match.group(1)), float(match.group(2))


def bracket_contains(interval: tuple[float, float], value: float) -> bool:
    lo, hi = interval
    return value >= lo and value < hi


def exhaustive(intervals: list[tuple[float, float]]) -> bool:
    ordered = sorted(intervals)
    if not ordered or not math.isinf(ordered[0][0]) or not math.isinf(ordered[-1][1]):
        return False
    return all(abs(left[1] - right[0]) < 1e-9 for left, right in zip(ordered, ordered[1:]))


def bracket_probs(
    intervals: list[tuple[float, float]], prev_weekend_m: float, pred_log_hold: float, sigma: float
) -> list[float]:
    result = []
    for lo, hi in intervals:
        lo_z = -math.inf if math.isinf(lo) else (math.log(max(lo / prev_weekend_m, 1e-9)) - pred_log_hold) / sigma
        hi_z = math.inf if math.isinf(hi) else (math.log(max(hi / prev_weekend_m, 1e-9)) - pred_log_hold) / sigma
        result.append(float(norm.cdf(hi_z) - norm.cdf(lo_z)))
    total = sum(result)
    return [value / total for value in result]


def get_yes_price(
    market: dict[str, Any], start_dt: datetime, cutoff: datetime, cache_dir: Path, refresh: bool
) -> float | None:
    tokens = jsonish(market.get("clobTokenIds")) or []
    if not tokens:
        return None
    token = str(tokens[0])
    start_ts = int(start_dt.timestamp()) - 60
    end_ts = int(cutoff.timestamp())
    data = fetch(
        f"{CLOB}/prices-history?market={token}&startTs={start_ts}&endTs={end_ts}&fidelity=30",
        cache_dir,
        "clob_history",
        f"{market['id']}_{end_ts}",
        refresh=refresh,
        as_json=True,
    )
    points = [point for point in data.get("history", []) if int(point["t"]) <= end_ts]
    return float(points[-1]["p"]) if points else None


def current_book(market: dict[str, Any], cache_dir: Path, refresh: bool) -> dict[str, float | None]:
    tokens = jsonish(market.get("clobTokenIds")) or []
    if not tokens:
        return {"best_bid": None, "best_ask": None}
    data = fetch(
        f"{CLOB}/book?token_id={tokens[0]}",
        cache_dir,
        "clob_book",
        str(market["id"]),
        refresh=refresh,
        as_json=True,
    )
    bids = [float(level["price"]) for level in data.get("bids", [])]
    asks = [float(level["price"]) for level in data.get("asks", [])]
    return {"best_bid": max(bids) if bids else None, "best_ask": min(asks) if asks else None}


def partition_skeleton(event: dict[str, Any], actual_gross_m: float | None) -> dict[str, Any]:
    brackets = []
    for market in event["markets"]:
        label = market.get("groupItemTitle") or market.get("question", "")
        interval = parse_bracket(label)
        outcome_prices = jsonish(market.get("outcomePrices")) or []
        resolved_yes = bool(outcome_prices and float(outcome_prices[0]) > 0.99)
        brackets.append(
            {
                "market_id": str(market["id"]),
                "label": label,
                "interval": interval,
                "resolved_yes": resolved_yes,
                "market": market,
            }
        )
    intervals = [item["interval"] for item in brackets]
    calculated = [bracket_contains(item["interval"], actual_gross_m) for item in brackets] if actual_gross_m is not None else []
    resolved = [item["resolved_yes"] for item in brackets]
    return {
        "event_id": event["event_id"],
        "event_title": event["event_title"],
        "movie": event["movie"],
        "week": event["week"],
        "start_dt": event["start_dt"],
        "end_dt": event["end_dt"],
        "brackets": brackets,
        "is_exhaustive": exhaustive(intervals),
        "settlement_match": calculated == resolved if calculated else None,
        "calculated_winner_n": sum(calculated),
        "resolved_winner_n": sum(resolved),
    }


def historical_partitions(
    events: list[dict[str, Any]],
    row_by_key: dict[tuple[str, int], dict[str, Any]],
    cache_dir: Path,
    *,
    refresh: bool,
    workers: int,
) -> list[dict[str, Any]]:
    partitions = []
    jobs = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for event in events:
            row = row_by_key.get((event["movie"], event["week"]))
            if not row or "pred_log_hold" not in row:
                continue
            partition = partition_skeleton(event, row["actual_gross_m"])
            target_friday = date.fromisoformat(row["target_friday"])
            fixed_cutoff = datetime.combine(target_friday - timedelta(days=1), dt_time(16), tzinfo=timezone.utc)
            cutoff = max(fixed_cutoff, event["start_dt"] + timedelta(hours=1))
            if cutoff > datetime.combine(target_friday, dt_time(20), tzinfo=timezone.utc):
                partition["price_status"] = "event_started_too_late"
                partitions.append(partition)
                continue
            partition["fixed_cutoff"] = fixed_cutoff.isoformat()
            partition["market_cutoff"] = cutoff.isoformat()
            partition["market_lag_hours"] = (cutoff - fixed_cutoff).total_seconds() / 3600
            partition["row"] = row
            partitions.append(partition)
            for bracket in partition["brackets"]:
                future = pool.submit(
                    get_yes_price,
                    bracket["market"],
                    event["start_dt"],
                    cutoff,
                    cache_dir,
                    refresh,
                )
                jobs[future] = bracket
        for future in as_completed(jobs):
            bracket = jobs[future]
            try:
                bracket["market_price"] = future.result()
            except Exception as exc:  # retain the sample audit trail
                bracket["market_price"] = None
                bracket["price_error"] = str(exc)
    return partitions


def score_partitions(partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for part in partitions:
        row = part.get("row")
        if not row:
            continue
        prices = [bracket.get("market_price") for bracket in part["brackets"]]
        eligible = (
            part["is_exhaustive"]
            and part["settlement_match"]
            and part["resolved_winner_n"] == 1
            and all(price is not None for price in prices)
        )
        record = {
            "event_id": part["event_id"],
            "event_title": part["event_title"],
            "movie": part["movie"],
            "week": part["week"],
            "target_friday": row["target_friday"],
            "actual_gross_m": row["actual_gross_m"],
            "model_gross_m": row["predicted_gross_m"],
            "baseline_gross_m": row["baseline_gross_m"],
            "is_exhaustive": part["is_exhaustive"],
            "settlement_match": part["settlement_match"],
            "price_complete": all(price is not None for price in prices),
            "eligible": eligible,
            "market_cutoff": part.get("market_cutoff"),
            "market_lag_hours": part.get("market_lag_hours"),
        }
        if eligible:
            intervals = [bracket["interval"] for bracket in part["brackets"]]
            model_probs = bracket_probs(
                intervals, row["prev_weekend_m"], row["pred_log_hold"], row["pred_sigma"]
            )
            market_raw = np.array(prices, dtype=float)
            market_probs = (market_raw / market_raw.sum()).tolist()
            actual = [bracket["resolved_yes"] for bracket in part["brackets"]]
            winner = actual.index(True)
            record.update(
                {
                    "winner": winner,
                    "brackets": [bracket["label"] for bracket in part["brackets"]],
                    "model_probs": model_probs,
                    "market_raw": market_raw.tolist(),
                    "market_probs": market_probs,
                    "model_log_loss": -math.log(max(model_probs[winner], 1e-9)),
                    "market_log_loss": -math.log(max(market_probs[winner], 1e-9)),
                    "model_brier": sum((p - float(i == winner)) ** 2 for i, p in enumerate(model_probs)),
                    "market_brier": sum((p - float(i == winner)) ** 2 for i, p in enumerate(market_probs)),
                    "model_top_correct": int(np.argmax(model_probs) == winner),
                    "market_top_correct": int(np.argmax(market_probs) == winner),
                }
            )
        scored.append(record)
    return scored


def trade_grid(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [row for row in scored if row["eligible"]]
    result = []
    for edge_cutoff in (0.05, 0.08, 0.10, 0.15):
        for spread in (0.0, 0.02, 0.05):
            candidates = []
            for row in eligible:
                edges = [m - p for m, p in zip(row["model_probs"], row["market_raw"])]
                pick = int(np.argmax(edges))
                if edges[pick] < edge_cutoff:
                    continue
                proxy = row["market_raw"][pick]
                fee = 0.05 * proxy * (1 - proxy)
                cost = min(proxy + fee + spread, 0.999)
                pnl = float(pick == row["winner"]) - cost
                candidates.append(
                    {
                        "key": (row["movie"], row["week"], row["target_friday"]),
                        "edge": edges[pick],
                        "cost": cost,
                        "pnl": pnl,
                        "won": int(pick == row["winner"]),
                    }
                )
            # Multiple Polymarket events can partition the same movie/weekend with
            # different strikes.  Keep one highest-edge contract per underlying.
            best_by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
            for candidate in candidates:
                key = candidate["key"]
                if key not in best_by_key or candidate["edge"] > best_by_key[key]["edge"]:
                    best_by_key[key] = candidate
            trades = list(best_by_key.values())
            capital = sum(trade["cost"] for trade in trades)
            pnl = sum(trade["pnl"] for trade in trades)
            result.append(
                {
                    "edge_cutoff": edge_cutoff,
                    "spread_stress": spread,
                    "trades": len(trades),
                    "wins": sum(trade["won"] for trade in trades),
                    "capital": capital,
                    "pnl": pnl,
                    "roi": pnl / capital if capital else None,
                }
            )
    return result


def block_bootstrap_market_diff(
    rows: list[dict[str, Any]], model_field: str, market_field: str, draws: int = 10_000
) -> dict[str, float | None]:
    if not rows:
        return {"mean": None, "ci_low": None, "ci_high": None}
    blocks: dict[str, list[float]] = {}
    for row in rows:
        blocks.setdefault(row["target_friday"], []).append(
            float(row[model_field]) - float(row[market_field])
        )
    block_values = list(blocks.values())
    rng = np.random.default_rng(20260808)
    samples = []
    for _ in range(draws):
        sampled = [block_values[index] for index in rng.integers(0, len(block_values), len(block_values))]
        samples.append(float(np.mean([value for block in sampled for value in block])))
    observed = float(np.mean([value for block in block_values for value in block]))
    return {
        "mean": observed,
        "ci_low": float(np.quantile(samples, 0.025)),
        "ci_high": float(np.quantile(samples, 0.975)),
        "blocks": len(block_values),
    }


def current_signals(
    open_events: list[dict[str, Any]],
    movie_infos: dict[str, dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    cache_dir: Path,
    *,
    refresh: bool,
) -> list[dict[str, Any]]:
    if len(closed_rows) < 10:
        return []
    model = fit_model(closed_rows)
    result = []
    for event in open_events:
        info = movie_infos.get(event["movie"])
        if not info:
            continue
        row = feature_row(info, event["movie"], event["week"], require_outcome=False)
        if not row:
            continue
        target_friday = date.fromisoformat(row["target_friday"])
        # Never let already-arrived target-weekend data leak into the feature contract.
        pred_log = model.predict_log_hold(row)
        partition = partition_skeleton(event, None)
        intervals = [bracket["interval"] for bracket in partition["brackets"]]
        probs = bracket_probs(intervals, row["prev_weekend_m"], pred_log, model.sigma)
        for bracket, probability in zip(partition["brackets"], probs):
            book = current_book(bracket["market"], cache_dir, refresh)
            ask = book["best_ask"]
            fee = 0.05 * ask * (1 - ask) if ask is not None else None
            result.append(
                {
                    "snapshot_utc": datetime.now(timezone.utc).isoformat(),
                    "event_id": event["event_id"],
                    "event_title": event["event_title"],
                    "movie": event["movie"],
                    "week": event["week"],
                    "target_friday": target_friday.isoformat(),
                    "predicted_gross_m": row["prev_weekend_m"] * math.exp(pred_log),
                    "pred_sigma_log": model.sigma,
                    "bracket": bracket["label"],
                    "model_probability": probability,
                    "best_bid": book["best_bid"],
                    "best_ask": ask,
                    "all_in_ask": ask + fee if ask is not None else None,
                    "ask_edge": probability - ask - fee if ask is not None else None,
                    "feature_cutoff_contract": "target-week Monday-Wednesday only",
                }
            )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def mean(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return float(np.mean(values)) if values else None


def summarize(
    all_rows: list[dict[str, Any]], scored: list[dict[str, Any]], trades: list[dict[str, Any]], current: list[dict[str, Any]],
    event_count: int, failures: list[dict[str, str]],
) -> dict[str, Any]:
    evaluated = [row for row in all_rows if row.get("predicted_gross_m") is not None]
    eligible = [row for row in scored if row["eligible"]]
    mape = mean(
        [
            {"v": abs(row["predicted_gross_m"] - row["actual_gross_m"]) / row["actual_gross_m"]}
            for row in evaluated
        ],
        "v",
    )
    baseline_mape = mean(
        [
            {"v": abs(row["baseline_gross_m"] - row["actual_gross_m"]) / row["actual_gross_m"]}
            for row in evaluated
        ],
        "v",
    )
    by_week = {}
    for week in (2, 3, 4, 5):
        subset = [row for row in evaluated if row["week"] == week]
        by_week[str(week)] = {
            "n": len(subset),
            "model_mape": mean(
                [{"v": abs(row["predicted_gross_m"] - row["actual_gross_m"]) / row["actual_gross_m"]} for row in subset],
                "v",
            ),
            "baseline_mape": mean(
                [{"v": abs(row["baseline_gross_m"] - row["actual_gross_m"]) / row["actual_gross_m"]} for row in subset],
                "v",
            ),
        }
    later = [row for row in evaluated if row["week"] >= 3]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "closed_repeat_event_partitions": event_count,
        "unique_movie_weekend_rows": len(all_rows),
        "walk_forward_evaluated_rows": len(evaluated),
        "movie_resolution_failures": failures,
        "point_forecast": {
            "model_mape": mape,
            "historical_week_median_baseline_mape": baseline_mape,
            "model_mae_m": mean(
                [{"v": abs(row["predicted_gross_m"] - row["actual_gross_m"])} for row in evaluated], "v"
            ),
            "by_week": by_week,
            "weeks_3_to_5_n": len(later),
            "weeks_3_to_5_model_mape": mean(
                [{"v": abs(row["predicted_gross_m"] - row["actual_gross_m"]) / row["actual_gross_m"]} for row in later],
                "v",
            ),
        },
        "market_comparison": {
            "eligible_partitions": len(eligible),
            "model_log_loss": mean(eligible, "model_log_loss"),
            "market_log_loss": mean(eligible, "market_log_loss"),
            "model_brier": mean(eligible, "model_brier"),
            "market_brier": mean(eligible, "market_brier"),
            "model_top_accuracy": mean(eligible, "model_top_correct"),
            "market_top_accuracy": mean(eligible, "market_top_correct"),
            "model_minus_market_log_loss_block_bootstrap": block_bootstrap_market_diff(
                eligible, "model_log_loss", "market_log_loss"
            ),
            "model_minus_market_brier_block_bootstrap": block_bootstrap_market_diff(
                eligible, "model_brier", "market_brier"
            ),
        },
        "trade_grid": trades,
        "current_signal_rows": len(current),
    }


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    cache_dir = out / "cache"
    out.mkdir(parents=True, exist_ok=True)

    raw_closed = fetch_events(cache_dir, closed=True, refresh=args.refresh)
    raw_open = fetch_events(cache_dir, closed=False, refresh=args.refresh)
    closed_events = [parsed for event in raw_closed if (parsed := parse_repeat_event(event))]
    open_events = [parsed for event in raw_open if (parsed := parse_repeat_event(event))]
    unique_keys = sorted(
        {(event["movie"], event["week"], event["end_dt"].year) for event in closed_events + open_events}
    )
    movie_infos: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for movie, _, event_year in unique_keys:
        if movie in movie_infos:
            continue
        try:
            movie_infos[movie] = resolve_movie(movie, event_year, cache_dir, refresh=args.refresh)
        except Exception as exc:
            failures.append({"movie": movie, "error": str(exc)})

    rows = []
    for movie, week, _ in unique_keys:
        info = movie_infos.get(movie)
        if not info:
            continue
        row = feature_row(info, movie, week)
        if row:
            rows.append(row)
    # Only closed outcomes belong in the historical walk-forward panel.
    closed_keys = {(event["movie"], event["week"]) for event in closed_events}
    closed_rows = [row for row in rows if (row["movie"], row["week"]) in closed_keys]
    walked = walk_forward(closed_rows)
    row_by_key = {(row["movie"], row["week"]): row for row in walked}

    partitions = historical_partitions(
        closed_events,
        row_by_key,
        cache_dir,
        refresh=args.refresh,
        workers=args.workers,
    )
    scored = score_partitions(partitions)
    trades = trade_grid(scored)
    current = current_signals(
        open_events,
        movie_infos,
        closed_rows,
        cache_dir,
        refresh=args.refresh,
    )
    summary = summarize(walked, scored, trades, current, len(closed_events), failures)

    write_csv(out / "dataset.csv", walked)
    write_csv(out / "market_comparison.csv", scored)
    write_csv(out / "current_signals.csv", current)
    (out / "trade_grid.json").write_text(json.dumps(trades, ensure_ascii=False, indent=2))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
