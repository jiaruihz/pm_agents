from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .market import (
    PolymarketBoxOfficeClient,
    basket_opportunities,
    minimum_binary_cover,
    normalized_levels,
)


SCHEMA_VERSION = "box_office_repeat_weekend_shadow_v1"
STRATEGY_KEY = "box_office.repeat_weekend.market_structure_v1"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n")


def _identity(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _norm_title(value: str) -> str:
    value = value.replace("&", " and ")
    value = re.sub(r"\bthe\b", " ", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _target_friday(event: dict[str, Any]) -> str:
    end = datetime.fromisoformat(event["end_date"].replace("Z", "+00:00")).date()
    return (end - timedelta(days=(end.weekday() - 4) % 7)).isoformat()


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_list(value: str) -> list[Any]:
    return json.loads(value) if value else []


def _empirical_probs(
    labels: list[str], forecast_mid_m: float, residuals: np.ndarray
) -> list[float]:
    from scipy.stats import norm

    from .market import parse_bracket

    robust = np.median(np.abs(residuals - np.median(residuals))) * 1.4826
    bandwidth = max(0.04, 0.18 * robust)
    values = []
    for label in labels:
        lo, hi = parse_bracket(label)
        lo_r = -math.inf if math.isinf(lo) else math.log(max(lo / forecast_mid_m, 1e-9))
        hi_r = math.inf if math.isinf(hi) else math.log(max(hi / forecast_mid_m, 1e-9))
        values.append(
            float(
                np.mean(
                    norm.cdf((hi_r - residuals) / bandwidth)
                    - norm.cdf((lo_r - residuals) / bandwidth)
                )
            )
        )
    total = sum(values)
    return [value / total for value in values]


def _directional_evidence(
    events: list[dict[str, Any]],
    books: dict[str, dict[str, Any]],
    artifact_dir: Path,
    observed_at: str,
) -> list[dict[str, Any]]:
    forecasts = _load_csv(artifact_dir / "industry_forecasts.csv")
    actuals = _load_csv(artifact_dir / "industry_forecast_actuals.csv")
    summary_path = artifact_dir / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    beta = summary.get("walk_forward_beta_last")
    residual_rows = [
        row
        for row in actuals
        if row.get("release_week") not in {None, "", "None"}
        and row.get("log_residual") not in {None, ""}
    ]
    by_key = {
        (
            row["target_friday"],
            _norm_title(row["movie"]),
            int(row["release_week"]),
        ): row
        for row in forecasts
        if row.get("release_week") not in {None, "", "None"}
    }
    outputs = []
    for event in events:
        target = _target_friday(event)
        forecast = by_key.get((target, _norm_title(event["movie"]), int(event["week"])))
        if forecast is None:
            continue
        residuals = np.asarray(
            [
                float(row["log_residual"])
                for row in residual_rows
                if row["target_friday"] < target
            ]
        )
        if len(residuals) < 8:
            continue
        labels = [market["label"] for market in event["markets"]]
        probabilities = _empirical_probs(
            labels, float(forecast["forecast_mid_m"]), residuals
        )
        yes_mids = []
        for market in event["markets"]:
            yes_book = books.get(market["yes_token_id"], {})
            no_book = books.get(market["no_token_id"], {})
            yes_bids = normalized_levels(yes_book, "bids")
            yes_asks = normalized_levels(yes_book, "asks")
            no_bids = normalized_levels(no_book, "bids")
            no_asks = normalized_levels(no_book, "asks")
            bid_candidates = ([yes_bids[0][0]] if yes_bids else []) + (
                [1.0 - no_asks[0][0]] if no_asks else []
            )
            ask_candidates = ([yes_asks[0][0]] if yes_asks else []) + (
                [1.0 - no_bids[0][0]] if no_bids else []
            )
            best_bid = max(bid_candidates) if bid_candidates else None
            best_ask = min(ask_candidates) if ask_candidates else None
            yes_mids.append(
                (best_bid + best_ask) / 2.0
                if best_bid is not None
                and best_ask is not None
                and 0 <= best_bid <= best_ask <= 1
                else None
            )
        market_probs = None
        market_p_source = None
        if all(value is not None for value in yes_mids) and sum(yes_mids) > 0:
            market_probs = [float(value) / sum(yes_mids) for value in yes_mids]
            market_p_source = "synthetic_yes_bbo_mid_normalized"
        else:
            marks = [market.get("mark_price") for market in event["markets"]]
            if all(value is not None for value in marks) and sum(marks) > 0:
                market_probs = [float(value) / sum(marks) for value in marks]
                market_p_source = "gamma_outcome_price_normalized"
        for index, market in enumerate(event["markets"]):
            outputs.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "candidate_id": _identity(
                        {
                            "observed_at": observed_at,
                            "event_id": event["event_id"],
                            "market_id": market["market_id"],
                            "kind": "directional",
                        }
                    ),
                    "candidate_kind": "directional_industry_forecast",
                    "strategy_key": STRATEGY_KEY,
                    "observed_at_utc": observed_at,
                    "event_id": event["event_id"],
                    "market_id": market["market_id"],
                    "condition_id": market["condition_id"],
                    "token_id": market["yes_token_id"],
                    "movie": event["movie"],
                    "week": event["week"],
                    "target_friday": target,
                    "bracket": market["label"],
                    "side": "YES",
                    "p_model": probabilities[index],
                    "market_p": market_probs[index] if market_probs else None,
                    "market_p_source": market_p_source,
                    "industry_forecast_mid_m": float(forecast["forecast_mid_m"]),
                    "industry_published_at_utc": forecast["published_at_utc"],
                    "calibration_rows": len(residuals),
                    "market_anchor_beta": beta,
                    "candidate_status": "shadow_only",
                    "selected": False,
                    "blocker_reason": "industry_probability_did_not_beat_market_forward",
                    "no_order_placed": True,
                }
            )
    return outputs


def run_shadow(
    output_dir: Path,
    *,
    shares_grid: tuple[float, ...] = (5.0, 10.0, 25.0),
    client: PolymarketBoxOfficeClient | None = None,
    industry_artifact_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = client or PolymarketBoxOfficeClient()
    events = client.discover_open_repeat_events()
    tokens = sorted(
        {
            market[f"{side}_token_id"]
            for event in events
            for market in event["markets"]
            for side in ("yes", "no")
        }
    )
    observed_at, books = client.fetch_books(tokens)
    missing_tokens = sorted(set(tokens) - set(books))

    snapshot_rows = []
    for event in events:
        for market in event["markets"]:
            for side in ("YES", "NO"):
                token = market[f"{side.lower()}_token_id"]
                book = books.get(token, {})
                bids = normalized_levels(book, "bids")
                asks = normalized_levels(book, "asks")
                snapshot_rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "observed_at_utc": observed_at,
                        "book_timestamp": book.get("timestamp"),
                        "book_hash": book.get("hash"),
                        "event_id": event["event_id"],
                        "event_title": event["event_title"],
                        "movie": event["movie"],
                        "week": event["week"],
                        "target_friday": _target_friday(event),
                        "market_id": market["market_id"],
                        "condition_id": market["condition_id"],
                        "bracket": market["label"],
                        "side": side,
                        "token_id": token,
                        "fee_rate": market["fee_rate"],
                        "best_bid": bids[0][0] if bids else None,
                        "best_ask": asks[0][0] if asks else None,
                        "bid_depth": sum(size for _, size in bids),
                        "ask_depth": sum(size for _, size in asks),
                        "book_available": token in books,
                    }
                )

    basket_rows = []
    for event in events:
        fee_rates = {
            market[f"{side}_token_id"]: market["fee_rate"]
            for market in event["markets"]
            for side in ("yes", "no")
        }
        for shares in shares_grid:
            for row in basket_opportunities(
                event,
                books,
                shares=shares,
                fee_rates_by_token=fee_rates,
            ):
                basket_rows.append(
                    {
                        **row,
                        "schema_version": SCHEMA_VERSION,
                        "observed_at_utc": observed_at,
                    }
                )

    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for event in events:
        key = (_norm_title(event["movie"]), event["week"], _target_friday(event))
        grouped.setdefault(key, []).append(event)
    cover_rows = []
    for group in grouped.values():
        for shares in shares_grid:
            cover = minimum_binary_cover(group, books, shares=shares)
            if cover is not None:
                cover_rows.append(
                    {
                        **cover,
                        "schema_version": SCHEMA_VERSION,
                        "observed_at_utc": observed_at,
                        "candidate_id": _identity(
                            {
                                "observed_at": observed_at,
                                "event_ids": cover["event_ids"],
                                "shares": shares,
                            }
                        ),
                        "candidate_kind": "model_free_binary_cover",
                        "candidate_status": (
                            "snapshot_positive_edge"
                            if cover["locked_profit"] > 0
                            else "no_edge"
                        ),
                        "selected": bool(cover["locked_profit"] > 0),
                        "blocker_reason": (
                            "multi_leg_taker_execution_is_not_atomic"
                            if cover["locked_profit"] > 0
                            else "all_in_cost_not_below_payout_floor"
                        ),
                        "no_order_placed": True,
                    }
                )

    directional_rows = (
        _directional_evidence(events, books, industry_artifact_dir, observed_at)
        if industry_artifact_dir is not None
        else []
    )
    signal_candidates = cover_rows + directional_rows
    summary = {
        "schema_version": SCHEMA_VERSION,
        "strategy_key": STRATEGY_KEY,
        "run_id": _identity({"observed_at": observed_at, "tokens": tokens}),
        "observed_at_utc": observed_at,
        "mode": "zero_notional_shadow",
        "signal_funnel": {
            "open_repeat_event_partitions": len(events),
            "underlying_movie_week_groups": len(grouped),
            "binary_contracts": sum(len(event["markets"]) for event in events),
            "size_points": len(shares_grid),
            "minimum_cover_candidates": len(cover_rows),
            "positive_snapshot_cover_edges": sum(
                row["locked_profit"] > 0 for row in cover_rows
            ),
            "directional_shadow_rows": len(directional_rows),
            "directional_selected": 0,
        },
        "evidence_funnel": {
            "tokens_expected": len(tokens),
            "books_received": len(books),
            "tokens_missing": missing_tokens,
            "complete_basket_depth_rows": sum(
                bool(row["complete_depth"]) for row in basket_rows
            ),
            "industry_artifact_available": bool(
                industry_artifact_dir and (industry_artifact_dir / "summary.json").exists()
            ),
        },
        "orders_submitted": 0,
        "venue_write_calls": 0,
        "no_order_placed": True,
    }
    raw = {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": observed_at,
        "events": events,
        "books_by_token": books,
    }
    (output_dir / "raw_snapshot.json").write_text(
        json.dumps(_jsonable(raw), ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    _write_jsonl(output_dir / "market_snapshots.jsonl", snapshot_rows)
    _write_jsonl(output_dir / "basket_economics.jsonl", basket_rows)
    _write_jsonl(output_dir / "minimum_cover_candidates.jsonl", cover_rows)
    _write_jsonl(output_dir / "directional_shadow_candidates.jsonl", directional_rows)
    _write_jsonl(output_dir / "signal_candidates.jsonl", signal_candidates)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
