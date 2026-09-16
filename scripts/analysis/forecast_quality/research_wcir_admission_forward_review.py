#!/usr/bin/env python3
"""Review the active WCIR admission-forward profiles on an exact market identity.

This is a read-only, zero-notional evaluation. Candidate and selection evidence
comes from the authoritative WCIR decision journal. Outcomes are fetched by
Polymarket market id and verified against each candidate condition id; the
evaluator never joins settlements on (city, target_date, bracket).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


UTC = timezone.utc
DEFAULT_RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_OUTPUT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
    "weather_city_intraday_probability/admission_forward_review/2026-08-23"
)
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
LOCAL_TZ = {
    "Amsterdam": ZoneInfo("Europe/Amsterdam"),
    "Busan": ZoneInfo("Asia/Seoul"),
    "Helsinki": ZoneInfo("Europe/Helsinki"),
    "Tokyo": ZoneInfo("Asia/Tokyo"),
}

# The set is intentionally explicit: superseded pre-boundary profiles are not
# silently mixed into the current admission-forward denominator.
PROFILES = (
    {
        "name": "Amsterdam V9 remaining heat",
        "model_id": "amsterdam_knmi_remaining_heat_v9_ecmwf_day1",
        "city": "Amsterdam",
        "forward_start_utc": "2026-08-11T22:00:00Z",
        "position_scope": "city_date_bracket_model",
    },
    {
        "name": "Amsterdam cross-.7 survival",
        "model_id": "amsterdam_knmi_cross_survival_v1",
        "city": "Amsterdam",
        "forward_start_utc": "2026-08-12T03:00:00Z",
        "position_scope": "city_date_bracket_model",
    },
    {
        "name": "Amsterdam market-offset V3",
        "model_id": "amsterdam_knmi_market_offset_probability_v3",
        "city": "Amsterdam",
        "forward_start_utc": "2026-08-12T08:24:21.919654Z",
        "position_scope": "city_date_bracket_model",
    },
    {
        "name": "Busan online market-prior",
        "model_id": "busan_intraday_exact_no_online_market_prior_residual",
        "city": "Busan",
        "forward_start_utc": "2026-08-12T02:00:00Z",
        "position_scope": "city_date_bracket_side_model",
    },
    {
        "name": "Helsinki bounded residual V2",
        "model_id": "helsinki_regime_calibrated_bounded_residual_c015_v2",
        "city": "Helsinki",
        "forward_start_utc": "2026-08-12T08:31:24Z",
        "position_scope": "city_date_bracket_model",
    },
    {
        "name": "Tokyo pre-cross V2",
        "model_id": "weather.city_intraday_probability.tokyo_pre_cross_market_sharpening",
        "city": "Tokyo",
        "forward_start_utc": "2026-08-12T00:00:00Z",
        "position_scope": "city_date_bracket_model",
    },
    {
        "name": "Tokyo state-entry V7",
        "model_id": "tokyo_state_entry_routed_market_residual_v7",
        "city": "Tokyo",
        "forward_start_utc": "2026-07-31T15:00:00Z",
        "position_scope": "city_date_model",
    },
    {
        "name": "Tokyo overshoot V2",
        "model_id": "tokyo_overshoot_market_residual_v2",
        "city": "Tokyo",
        "forward_start_utc": "2026-08-02T00:00:00Z",
        "position_scope": "city_date_bracket_model",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument("--gamma-workers", type=int, default=12)
    parser.add_argument(
        "--as-of-utc",
        help="Optional inclusive decision cutoff, e.g. 2026-08-23T15:43:41.173029Z",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * probability)
    return ordered[max(0, min(len(ordered) - 1, index))]


def describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "max": max(values),
    }


def probability_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "brier": None, "logloss": None}
    epsilon = 1e-12
    probabilities = [float(row["probability"]) for row in rows]
    labels = [int(row["label"]) for row in rows]
    return {
        "rows": len(rows),
        "positive_rate": statistics.mean(labels),
        "mean_probability": statistics.mean(probabilities),
        "brier": statistics.mean((p - y) ** 2 for p, y in zip(probabilities, labels)),
        "logloss": statistics.mean(
            -(y * math.log(max(p, epsilon)) + (1 - y) * math.log(max(1 - p, epsilon)))
            for p, y in zip(probabilities, labels)
        ),
    }


def bootstrap_ratio(
    rows: list[dict[str, Any]], *, samples: int, seed: int
) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if not dates:
        return {"target_dates": 0, "estimate": None, "ci95": [None, None]}
    estimate = sum(float(row["pnl"]) for row in rows) / sum(
        float(row["cost"]) for row in rows
    )
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(samples):
        sampled = [rng.choice(dates) for _ in dates]
        pnl = sum(float(row["pnl"]) for date in sampled for row in by_date[date])
        cost = sum(float(row["cost"]) for date in sampled for row in by_date[date])
        if cost:
            draws.append(pnl / cost)
    return {
        "target_dates": len(dates),
        "estimate": estimate,
        "ci95": [quantile(draws, 0.025), quantile(draws, 0.975)],
        "bootstrap_unit": "target_date",
    }


def bootstrap_score_delta(
    rows: list[dict[str, Any]], *, samples: int, seed: int
) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if not dates:
        return {"target_dates": 0, "brier_delta": None, "brier_delta_ci95": [None, None]}

    def delta(sampled_dates: list[str]) -> float:
        sampled = [row for date in sampled_dates for row in by_date[date]]
        return statistics.mean(
            (float(row["model_probability"]) - int(row["label"])) ** 2
            - (float(row["market_probability"]) - int(row["label"])) ** 2
            for row in sampled
        )

    estimate = delta(dates)
    rng = random.Random(seed)
    draws = [delta([rng.choice(dates) for _ in dates]) for _ in range(samples)]
    return {
        "target_dates": len(dates),
        "brier_delta": estimate,
        "brier_delta_ci95": [quantile(draws, 0.025), quantile(draws, 0.975)],
        "bootstrap_unit": "target_date",
    }


def fetch_market(market_id: str) -> dict[str, Any]:
    request = Request(
        GAMMA_MARKET_URL.format(market_id=market_id),
        headers={"User-Agent": "pm-agents-wcir-review/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, dict):
                raise ValueError("Gamma market response is not an object")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Gamma fetch failed for market {market_id}: {last_error}")


def load_markets(
    market_ids: set[str], cache_path: Path, workers: int
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    cache: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        payload = json.loads(cache_path.read_text())
        cache = {
            str(key): value
            for key, value in (payload.get("markets") or {}).items()
            if isinstance(value, dict)
        }
    missing = sorted(market_ids - set(cache))
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_market, market_id): market_id for market_id in missing}
        for future in as_completed(futures):
            market_id = futures[future]
            try:
                cache[market_id] = future.result()
            except Exception as exc:  # keep the evidence funnel explicit
                errors[market_id] = str(exc)
    cache_path.write_text(
        json.dumps(
            {
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
                "source": "https://gamma-api.polymarket.com/markets/{market_id}",
                "markets": cache,
                "errors": errors,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return cache, errors


def market_winner(market: dict[str, Any], condition_id: str) -> tuple[str | None, str]:
    if str(market.get("conditionId") or "") != condition_id:
        return None, "condition_id_mismatch"
    if not bool(market.get("closed")):
        return None, "market_not_closed"
    try:
        outcomes = json.loads(market.get("outcomes") or "[]")
        prices = [float(value) for value in json.loads(market.get("outcomePrices") or "[]")]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, "invalid_outcome_payload"
    winners = [str(outcome).upper() for outcome, price in zip(outcomes, prices) if price >= 0.999]
    if len(winners) != 1 or winners[0] not in {"YES", "NO"}:
        return None, "no_unique_binary_winner"
    return winners[0], "settled_exact_market_identity"


def dedupe_candidates(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_id: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for bundle in read_jsonl(path):
        candidate = bundle.get("signal_candidate") or {}
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            counts["missing_candidate_id"] += 1
            continue
        counts["journal_rows"] += 1
        if candidate_id in by_id:
            counts["duplicate_candidate_rows"] += 1
            # A selected record is stronger evidence than an earlier unselected
            # serialization of the same immutable candidate.
            if candidate.get("selected") and not (
                by_id[candidate_id].get("signal_candidate") or {}
            ).get("selected"):
                by_id[candidate_id] = bundle
            continue
        by_id[candidate_id] = bundle
    return list(by_id.values()), dict(counts)


def profile_rows(
    bundles: list[dict[str, Any]], profile: dict[str, Any], as_of: datetime | None
) -> list[dict[str, Any]]:
    start = parse_ts(str(profile["forward_start_utc"]))
    rows = []
    for bundle in bundles:
        candidate = bundle.get("signal_candidate") or {}
        if candidate.get("model_id") != profile["model_id"]:
            continue
        if candidate.get("city") != profile["city"]:
            continue
        decision_ts = str(candidate.get("decision_ts_utc") or "")
        if not decision_ts or parse_ts(decision_ts) < start:
            continue
        if as_of is not None and parse_ts(decision_ts) > as_of:
            continue
        rows.append(bundle)
    return rows


def selected_replay(
    selected: list[dict[str, Any]], profile: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if profile["position_scope"] != "city_date_bracket_model":
        return selected, []
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    occupied: set[tuple[str, str, str, str]] = set()
    for bundle in sorted(
        selected,
        key=lambda row: str((row.get("signal_candidate") or {}).get("decision_ts_utc") or ""),
    ):
        candidate = bundle.get("signal_candidate") or {}
        key = (
            str(candidate.get("city") or ""),
            str(candidate.get("target_date") or ""),
            str(candidate.get("bracket") or ""),
            str(candidate.get("model_id") or ""),
        )
        if key in occupied:
            suppressed.append(bundle)
        else:
            occupied.add(key)
            kept.append(bundle)
    return kept, suppressed


def evaluate_selected(
    selected: list[dict[str, Any]], markets: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], Counter]:
    settled: list[dict[str, Any]] = []
    exclusions = Counter()
    for bundle in selected:
        candidate = bundle.get("signal_candidate") or {}
        market_id = str(candidate.get("market_id") or "")
        condition_id = str(candidate.get("condition_id") or "")
        if not market_id or market_id not in markets:
            exclusions["market_evidence_missing"] += 1
            continue
        winner, status = market_winner(markets[market_id], condition_id)
        if winner is None:
            exclusions[status] += 1
            continue
        try:
            cost = float(candidate["executable_cost"])
        except (KeyError, TypeError, ValueError):
            exclusions["executable_cost_missing"] += 1
            continue
        side = str(candidate.get("side") or "").upper()
        won = side == winner
        settled.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "city": candidate.get("city"),
                "target_date": candidate.get("target_date"),
                "bracket": candidate.get("bracket"),
                "side": side,
                "decision_ts_utc": candidate.get("decision_ts_utc"),
                "market_id": market_id,
                "condition_id": condition_id,
                "winner": winner,
                "won": won,
                "cost": cost,
                "payout": float(won),
                "pnl": float(won) - cost,
                "model_probability": candidate.get("p_model"),
                "market_probability": candidate.get("market_p"),
                "edge_after_fee": (candidate.get("metadata") or {}).get("edge_after_fee"),
                "settlement_status": status,
            }
        )
    return settled, exclusions


def score_model_vs_market(
    bundles: list[dict[str, Any]], markets: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], Counter]:
    rows: list[dict[str, Any]] = []
    exclusions = Counter()
    for bundle in bundles:
        candidate = bundle.get("signal_candidate") or {}
        if candidate.get("candidate_status") != "scored":
            exclusions["candidate_not_scored"] += 1
            continue
        market_id = str(candidate.get("market_id") or "")
        condition_id = str(candidate.get("condition_id") or "")
        market = markets.get(market_id)
        if market is None:
            exclusions["market_evidence_missing"] += 1
            continue
        winner, status = market_winner(market, condition_id)
        if winner is None:
            exclusions[status] += 1
            continue
        try:
            model_probability = float(candidate["p_model"])
            market_probability = float(candidate["market_p"])
        except (KeyError, TypeError, ValueError):
            exclusions["probability_missing"] += 1
            continue
        rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "target_date": candidate.get("target_date"),
                "label": int(str(candidate.get("side") or "").upper() == winner),
                "model_probability": model_probability,
                "market_probability": market_probability,
                "settlement_status": status,
            }
        )
    return rows, exclusions


def local_hour(bundle: dict[str, Any]) -> float:
    candidate = bundle.get("signal_candidate") or {}
    city = str(candidate.get("city") or "")
    local = parse_ts(str(candidate["decision_ts_utc"])).astimezone(LOCAL_TZ[city])
    return local.hour + local.minute / 60 + local.second / 3600


def compact_selection(bundle: dict[str, Any]) -> dict[str, Any]:
    candidate = bundle.get("signal_candidate") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "city": candidate.get("city"),
        "target_date": candidate.get("target_date"),
        "bracket": candidate.get("bracket"),
        "side": candidate.get("side"),
        "decision_ts_utc": candidate.get("decision_ts_utc"),
        "market_id": candidate.get("market_id"),
        "condition_id": candidate.get("condition_id"),
        "model_probability": candidate.get("p_model"),
        "market_probability": candidate.get("market_p"),
        "effective_entry_cost": candidate.get("executable_cost"),
        "edge_after_fee": (candidate.get("metadata") or {}).get("edge_after_fee"),
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    journal = args.runtime_root / "output/city_probability_runtime_v3/decision_bundles.jsonl"
    intent_journal = args.runtime_root / "output/city_probability_runtime_v3/trade_intents.jsonl"
    bundles, journal_counts = dedupe_candidates(journal)
    selected_intent_ids = {
        str(row.get("candidate_id") or "") for row in read_jsonl(intent_journal)
    }
    as_of = parse_ts(args.as_of_utc) if args.as_of_utc else None

    scoped: dict[str, list[dict[str, Any]]] = {
        str(profile["model_id"]): profile_rows(bundles, profile, as_of)
        for profile in PROFILES
    }
    market_ids = {
        str((bundle.get("signal_candidate") or {}).get("market_id"))
        for rows in scoped.values()
        for bundle in rows
        if (bundle.get("signal_candidate") or {}).get("market_id")
    }
    cache_path = args.output_dir / "gamma_markets.json"
    markets, gamma_errors = load_markets(market_ids, cache_path, args.gamma_workers)

    profile_results: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    all_selected_settled: list[dict[str, Any]] = []
    all_replay_settled: list[dict[str, Any]] = []
    all_score_rows: list[dict[str, Any]] = []
    all_suppressed: list[dict[str, Any]] = []
    for index, profile in enumerate(PROFILES):
        rows = scoped[str(profile["model_id"])]
        selected = [
            bundle
            for bundle in rows
            if bool((bundle.get("signal_candidate") or {}).get("selected"))
        ]
        replay_kept, replay_suppressed = selected_replay(selected, profile)
        settled, settlement_exclusions = evaluate_selected(selected, markets)
        replay_settled, replay_exclusions = evaluate_selected(replay_kept, markets)
        score_rows, score_exclusions = score_model_vs_market(rows, markets)
        model_metrics = probability_metrics(
            [
                {"probability": row["model_probability"], "label": row["label"]}
                for row in score_rows
            ]
        )
        market_metrics = probability_metrics(
            [
                {"probability": row["market_probability"], "label": row["label"]}
                for row in score_rows
            ]
        )
        source_events = {
            str((bundle.get("signal_candidate") or {}).get("trigger_event_id") or "")
            for bundle in rows
        }
        selected_events = {
            str((bundle.get("signal_candidate") or {}).get("trigger_event_id") or "")
            for bundle in selected
        }
        candidate_statuses = Counter(
            str((bundle.get("signal_candidate") or {}).get("candidate_status") or "missing")
            for bundle in rows
        )
        candidate_blockers = Counter(
            str((bundle.get("signal_candidate") or {}).get("blocker_reason") or "none")
            for bundle in rows
        )
        result = {
            **profile,
            "candidate_rows": len(rows),
            "candidate_statuses": dict(candidate_statuses),
            "candidate_blockers": dict(candidate_blockers),
            "source_trigger_events": len(source_events),
            "selected_source_events": len(selected_events),
            "trigger_selection_frequency": (
                len(selected_events) / len(source_events) if source_events else None
            ),
            "selected_intents": len(selected),
            "selected_intents_present_in_intent_journal": sum(
                str((bundle.get("signal_candidate") or {}).get("candidate_id") or "")
                in selected_intent_ids
                for bundle in selected
            ),
            "selected_target_dates": len(
                {str((bundle.get("signal_candidate") or {}).get("target_date")) for bundle in selected}
            ),
            "selected_per_target_date": (
                len(selected)
                / len(
                    {
                        str((bundle.get("signal_candidate") or {}).get("target_date"))
                        for bundle in selected
                    }
                )
                if selected
                else 0.0
            ),
            "entry_local_hour": describe([local_hour(bundle) for bundle in selected]),
            "entry_effective_cost": describe(
                [float((bundle.get("signal_candidate") or {})["executable_cost"]) for bundle in selected]
            ),
            "entry_model_probability": describe(
                [float((bundle.get("signal_candidate") or {})["p_model"]) for bundle in selected]
            ),
            "entry_market_probability": describe(
                [float((bundle.get("signal_candidate") or {})["market_p"]) for bundle in selected]
            ),
            "entry_edge_after_fee": describe(
                [
                    float(((bundle.get("signal_candidate") or {}).get("metadata") or {})["edge_after_fee"])
                    for bundle in selected
                ]
            ),
            "selected_settlement": {
                "settled": len(settled),
                "wins": sum(int(row["won"]) for row in settled),
                "losses": sum(int(not row["won"]) for row in settled),
                "cost_per_one_share": sum(float(row["cost"]) for row in settled),
                "pnl_per_one_share": sum(float(row["pnl"]) for row in settled),
                "roi": (
                    sum(float(row["pnl"]) for row in settled)
                    / sum(float(row["cost"]) for row in settled)
                    if settled
                    else None
                ),
                "roi_block_bootstrap": bootstrap_ratio(
                    settled, samples=args.bootstrap_samples, seed=args.seed + index
                ),
                "exclusions": dict(settlement_exclusions),
            },
            "current_bracket_policy_replay": {
                "kept_intents": len(replay_kept),
                "suppressed_later_same_bracket": len(replay_suppressed),
                "settled": len(replay_settled),
                "wins": sum(int(row["won"]) for row in replay_settled),
                "cost_per_one_share": sum(float(row["cost"]) for row in replay_settled),
                "pnl_per_one_share": sum(float(row["pnl"]) for row in replay_settled),
                "roi": (
                    sum(float(row["pnl"]) for row in replay_settled)
                    / sum(float(row["cost"]) for row in replay_settled)
                    if replay_settled
                    else None
                ),
                "roi_block_bootstrap": bootstrap_ratio(
                    replay_settled,
                    samples=args.bootstrap_samples,
                    seed=args.seed + 100 + index,
                ),
                "exclusions": dict(replay_exclusions),
            },
            "model_vs_market_same_rows": {
                "model": model_metrics,
                "market": market_metrics,
                "brier_delta_model_minus_market": (
                    model_metrics["brier"] - market_metrics["brier"]
                    if score_rows
                    else None
                ),
                "logloss_delta_model_minus_market": (
                    model_metrics["logloss"] - market_metrics["logloss"]
                    if score_rows
                    else None
                ),
                "brier_delta_block_bootstrap": bootstrap_score_delta(
                    score_rows,
                    samples=args.bootstrap_samples,
                    seed=args.seed + 200 + index,
                ),
                "exclusions": dict(score_exclusions),
            },
        }
        profile_results.append(result)
        all_selected_settled.extend(settled)
        all_replay_settled.extend(replay_settled)
        all_score_rows.extend(score_rows)
        all_suppressed.extend(compact_selection(bundle) for bundle in replay_suppressed)
        for bundle in selected:
            compact = compact_selection(bundle)
            match = next(
                (row for row in settled if row["candidate_id"] == compact["candidate_id"]),
                None,
            )
            compact.update(
                {
                    "profile_name": profile["name"],
                    "settled": match is not None,
                    "winner": match.get("winner") if match else None,
                    "won": match.get("won") if match else None,
                    "unit_pnl": match.get("pnl") if match else None,
                }
            )
            selection_rows.append(compact)

    combined_model = probability_metrics(
        [
            {"probability": row["model_probability"], "label": row["label"]}
            for row in all_score_rows
        ]
    )
    combined_market = probability_metrics(
        [
            {"probability": row["market_probability"], "label": row["label"]}
            for row in all_score_rows
        ]
    )
    now = datetime.now(UTC)
    output = {
        "schema_version": "wcir_admission_forward_review_v1",
        "generated_at_utc": now.isoformat(),
        "observation_cutoff_utc": max(
            str((bundle.get("signal_candidate") or {}).get("decision_ts_utc") or "")
            for rows in scoped.values()
            for bundle in rows
        ),
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "settlement_identity": "Gamma market_id with candidate condition_id equality",
        "journal": str(journal),
        "intent_journal": str(intent_journal),
        "journal_counts": journal_counts,
        "gamma_market_ids_requested": len(market_ids),
        "gamma_market_ids_loaded": len(markets),
        "gamma_errors": gamma_errors,
        "profiles": profile_results,
        "combined": {
            "selected_intents": len(selection_rows),
            "selected_settled": len(all_selected_settled),
            "selected_wins": sum(int(row["won"]) for row in all_selected_settled),
            "selected_cost_per_one_share": sum(float(row["cost"]) for row in all_selected_settled),
            "selected_pnl_per_one_share": sum(float(row["pnl"]) for row in all_selected_settled),
            "selected_roi": (
                sum(float(row["pnl"]) for row in all_selected_settled)
                / sum(float(row["cost"]) for row in all_selected_settled)
                if all_selected_settled
                else None
            ),
            "selected_roi_block_bootstrap": bootstrap_ratio(
                all_selected_settled,
                samples=args.bootstrap_samples,
                seed=args.seed + 999,
            ),
            "model_vs_market_same_rows": {
                "model": combined_model,
                "market": combined_market,
                "brier_delta_model_minus_market": (
                    combined_model["brier"] - combined_market["brier"]
                    if all_score_rows
                    else None
                ),
                "logloss_delta_model_minus_market": (
                    combined_model["logloss"] - combined_market["logloss"]
                    if all_score_rows
                    else None
                ),
            },
            "current_bracket_policy_suppressed_historical_intents": len(all_suppressed),
            "current_bracket_policy_replay": {
                "settled": len(all_replay_settled),
                "wins": sum(int(row["won"]) for row in all_replay_settled),
                "cost_per_one_share": sum(float(row["cost"]) for row in all_replay_settled),
                "pnl_per_one_share": sum(float(row["pnl"]) for row in all_replay_settled),
                "roi": (
                    sum(float(row["pnl"]) for row in all_replay_settled)
                    / sum(float(row["cost"]) for row in all_replay_settled)
                    if all_replay_settled
                    else None
                ),
                "roi_block_bootstrap": bootstrap_ratio(
                    all_replay_settled,
                    samples=args.bootstrap_samples,
                    seed=args.seed + 1000,
                ),
            },
        },
        "selection_rows": sorted(
            selection_rows, key=lambda row: (str(row["decision_ts_utc"]), str(row["candidate_id"]))
        ),
        "current_bracket_policy_suppressed_rows": all_suppressed,
    }
    (args.output_dir / "review.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(output["combined"], indent=2, sort_keys=True))
    print(f"output={args.output_dir / 'review.json'}")


if __name__ == "__main__":
    main()
