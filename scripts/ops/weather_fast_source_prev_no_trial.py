#!/usr/bin/env python3
"""Trade the previous temperature bracket NO from a faster observation source."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_fast_source_city_policy import FastSourceCityPolicy, configured_city_policies  # noqa: E402
from scripts.ops.weather_fast_source_execution import (  # noqa: E402
    audit_order_share_caps,
    build_live_post_only_gtd_place_fn,
    exact_share_maker_intent,
    resolve_share_cap_pause,
    spent_market_shares,
    submit_post_only_gtd,
)
from scripts.ops.weather_fast_source_stale_book_observer import (  # noqa: E402
    augment_market_index_from_gamma,
    bracket_lookup,
    build_market_index,
    fetch_fresh_book,
    latest_orderbook_snapshot,
    latest_paper_snapshot,
    market_city,
    metar_running_max,
    parse_dt,
    safe_float,
    source_latest_by_city,
    target_date_for_city,
    temperature_event_slug,
)
from scripts.ops.weather_market_proxy import market_proxy_url  # noqa: E402
from weather_data_feed.fast_event_source_policy import load_fast_event_source_profiles  # noqa: E402
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/fast_source_prev_no_trial"
DEFAULT_HIGH_FREQUENCY_LATEST = RUNTIME_ROOT / "output/high_frequency_observations/latest.json"
DEFAULT_SOURCE_EVENTS_JSONL = RUNTIME_ROOT / "output/source_events/sources.jsonl"
PERSISTENT_CROSS_POLICY = "persistent_candidate_margin_v5"
SOURCE_OBSERVATION_HISTORY_SUFFIX = "__source_observations__"


def source_temp_in_market_unit(temp_c: float, market_unit: str) -> float:
    return float(temp_c) * 9.0 / 5.0 + 32.0 if market_unit == "F" else float(temp_c)


def candidate_market_is_lockable(token: Any, candidate: int) -> bool:
    parsed = parse_market_bracket(str(token.bracket), str(token.question))
    if parsed is None or parsed.top or parsed.high is None:
        return False
    return float(parsed.high) == float(candidate)


def resolve_candidate_market(
    market_index: dict[Any, Any],
    *,
    city: str,
    target_date: str,
    candidate: int,
    market_proxy: str,
) -> tuple[Any | None, dict[Any, Any], str]:
    token = bracket_lookup(market_index, city, target_date, candidate)
    if token is not None and candidate_market_is_lockable(token, candidate):
        return token, market_index, "paper_snapshot"
    augmented = augment_market_index_from_gamma(
        market_index,
        target_dates={target_date},
        cities={city},
        event_slugs={city: temperature_event_slug(city, target_date, "max")},
        market_proxy=market_proxy,
    )
    token = bracket_lookup(augmented, city, target_date, candidate)
    if token is not None and not candidate_market_is_lockable(token, candidate):
        token = None
    return token, augmented, "gamma_fallback" if token is not None else "unresolved"


def resolve_range_candidate_market(
    market_index: dict[Any, Any],
    *,
    city: str,
    target_date: str,
    metar_running_max_value: int,
    market_proxy: str,
) -> tuple[Any | None, dict[Any, Any], int | None, str]:
    """Resolve the finite 2F bracket containing the known METAR running max."""
    token = bracket_lookup(market_index, city, target_date, metar_running_max_value)
    resolution = "paper_snapshot"
    if token is None:
        market_index = augment_market_index_from_gamma(
            market_index,
            target_dates={target_date},
            cities={city},
            event_slugs={city: temperature_event_slug(city, target_date, "max")},
            market_proxy=market_proxy,
        )
        token = bracket_lookup(market_index, city, target_date, metar_running_max_value)
        resolution = "gamma_fallback" if token is not None else "unresolved"
    if token is None:
        return None, market_index, None, resolution
    parsed = parse_market_bracket(str(token.bracket), str(token.question))
    if parsed is None or parsed.top or parsed.high is None:
        return None, market_index, None, "unsupported_open_top_bracket"
    if not parsed.bottom and (
        parsed.low is None or abs((float(parsed.high) - float(parsed.low)) - 1.0) > 1e-9
    ):
        return None, market_index, None, "unsupported_non_2f_range"
    upper = int(parsed.high)
    if abs(float(parsed.high) - upper) > 1e-9:
        return None, market_index, None, "unsupported_fractional_range"
    return token, market_index, upper, resolution


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> dict[str, Any]:
    return read_json(path, {"seen_event_keys": [], "live_order_keys": []})


def source_cross_confirmation(
    *,
    city: str,
    source: str,
    target_date: str,
    source_market_temp: float,
    source_market_value: int,
    source_obs_ts_utc: str,
    metar_running_max_value: int,
    candidate_no_bracket: int,
    policy: FastSourceCityPolicy,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Apply city policy while keeping persistence state isolated by bracket."""
    if policy.confirmation_policy == "arithmetic_cross":
        confirmed = source_market_value > metar_running_max_value
        return {
            "policy": "arithmetic_round_v1",
            "basis": "metar_running_max",
            "basis_c": metar_running_max_value,
            "required_margin_c": policy.qualifying_margin,
            "required_distinct_observations": 1,
            "qualifying_distinct_observations": 1 if confirmed else 0,
            "confirmed": confirmed,
            "blocker": "" if confirmed else "source_not_above_metar_running_max",
        }
    if policy.confirmation_policy != "persistent_candidate_margin":
        raise ValueError(f"unsupported confirmation_policy={policy.confirmation_policy!r}")

    current_obs = parse_dt(source_obs_ts_utc)
    history_key = f"{city}|{target_date}|{source}|{SOURCE_OBSERVATION_HISTORY_SUFFIX}"
    history_state = dict(state.get(history_key) or {})
    if history_state.get("policy") != PERSISTENT_CROSS_POLICY:
        history_state = {}
    observations = list(history_state.get("observations") or [])
    previous_obs = parse_dt(observations[-1].get("source_obs_ts_utc")) if observations else None
    out_of_order = current_obs is not None and previous_obs is not None and current_obs < previous_obs
    if source_obs_ts_utc and not out_of_order and (
        not observations or source_obs_ts_utc != observations[-1].get("source_obs_ts_utc")
    ):
        observations.append(
            {
                "source_obs_ts_utc": source_obs_ts_utc,
                "source_market_temp": source_market_temp,
            }
        )
        observations = observations[-16:]
        state[history_key] = {
            "policy": PERSISTENT_CROSS_POLICY,
            "observations": observations,
        }

    if candidate_no_bracket < metar_running_max_value:
        return {
            "policy": PERSISTENT_CROSS_POLICY,
            "basis": "candidate_no_bracket",
            "basis_c": candidate_no_bracket,
            "required_margin_c": policy.qualifying_margin,
            "required_distinct_observations": policy.required_distinct_observations,
            "qualifying_distinct_observations": 0,
            "confirmed": False,
            "blocker": "source_not_above_metar_running_max",
        }

    qualifying_threshold = candidate_no_bracket + policy.qualifying_margin
    strong_threshold = candidate_no_bracket + policy.strong_margin
    qualifies = source_market_temp >= qualifying_threshold - 1e-9
    latest_is_strong = source_market_temp >= strong_threshold - 1e-9
    key = f"{city}|{target_date}|{source}|{candidate_no_bracket}"
    count = 0
    for observation in reversed(observations):
        if float(observation["source_market_temp"]) < qualifying_threshold - 1e-9:
            break
        count += 1
    state[key] = {
        "policy": PERSISTENT_CROSS_POLICY,
        "last_source_obs_ts_utc": source_obs_ts_utc,
        "last_observation_qualified": qualifies,
        "qualifying_distinct_observations": count,
        "source_market_temp": source_market_temp,
        "qualifying_threshold_c": qualifying_threshold,
        "strong_threshold_c": strong_threshold,
        "latest_observation_strong": latest_is_strong,
    }
    confirmed = not out_of_order and qualifies and count >= policy.required_distinct_observations and latest_is_strong
    blocker = ""
    if out_of_order:
        blocker = "source_observation_out_of_order"
    elif not qualifies:
        blocker = "source_cross_margin_not_met"
    elif count < policy.required_distinct_observations:
        blocker = "source_cross_persistence_not_met"
    elif not latest_is_strong:
        blocker = "latest_source_cross_strength_not_met"
    return {
        "policy": PERSISTENT_CROSS_POLICY,
        "basis": "candidate_no_bracket",
        "basis_c": candidate_no_bracket,
        "required_margin_c": policy.qualifying_margin,
        "threshold_c": qualifying_threshold,
        "strong_margin_c": policy.strong_margin,
        "strong_threshold_c": strong_threshold,
        "strong_observation_seen": latest_is_strong,
        "required_distinct_observations": policy.required_distinct_observations,
        "qualifying_distinct_observations": count,
        "confirmed": confirmed,
        "blocker": blocker,
    }


def metar_report_clocks(
    path: Path,
    target_dates_by_city: dict[str, str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Infer routine METAR cadence per city and local target date; SPECI is excluded."""
    routine_reports: dict[tuple[str, str], set[datetime]] = {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            report_dt = parse_dt(row.get("source_report_ts_utc"))
            if report_dt is None or not str(row.get("raw_metar") or "").strip().upper().startswith("METAR "):
                continue
            city = market_city(str(row.get("city") or ""))
            target_date = str(row.get("target_date") or "")
            if target_dates_by_city.get(city) != target_date:
                continue
            routine_reports.setdefault((city, target_date), set()).add(report_dt)

    clocks: dict[tuple[str, str], dict[str, Any]] = {}
    for key, report_set in routine_reports.items():
        reports = sorted(report_set)
        gaps = [
            (current - previous).total_seconds() / 60.0
            for previous, current in zip(reports, reports[1:])
            if 15.0 <= (current - previous).total_seconds() / 60.0 <= 90.0
        ]
        if len(reports) < 3 or len(gaps) < 2:
            continue
        cadence_min = float(median(gaps[-8:]))
        latest_report = reports[-1]
        clocks[key] = {
            "routine_metar_cadence_min": round(cadence_min, 3),
            "latest_routine_metar_report_ts_utc": latest_report.isoformat(),
            "next_expected_metar_report_ts_utc": (latest_report + timedelta(minutes=cadence_min)).isoformat(),
            "routine_metar_report_count": len(reports),
        }
    return clocks


def next_metar_window_status(clock: dict[str, Any] | None, now: datetime, *, window_min: float) -> dict[str, Any]:
    next_report = parse_dt((clock or {}).get("next_expected_metar_report_ts_utc"))
    if next_report is None:
        return {
            **(clock or {}),
            "next_metar_window_min": float(window_min),
            "next_metar_window_eligible": False,
            "next_metar_window_blocker": "metar_report_clock_missing",
        }
    minutes_to_next = (next_report - now).total_seconds() / 60.0
    eligible = abs(minutes_to_next) <= float(window_min) + 1e-9
    return {
        **(clock or {}),
        "minutes_to_next_expected_metar": round(minutes_to_next, 3),
        "next_metar_window_distance_min": round(abs(minutes_to_next), 3),
        "next_metar_window_min": float(window_min),
        "next_metar_window_eligible": eligible,
        "next_metar_window_blocker": "" if eligible else "outside_next_metar_execution_window",
    }


def next_metar_burst_cities(opportunity_rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row["city"]) for row in opportunity_rows if row.get("next_metar_window_eligible") and row.get("city")})


def _load_runtime_inputs(args: argparse.Namespace, now: datetime) -> dict[str, Any]:
    policies = configured_city_policies(live_cities=args.live_cities, shadow_cities=args.shadow_cities)
    target_dates = {city: target_date_for_city(city, now, args.target_date) for city in policies}
    profiles = load_fast_event_source_profiles()
    city_profiles: dict[str, Any] = {}
    for city, policy in policies.items():
        profile = profiles.get((city, policy.source))
        if profile is None or not profile.collector_enabled:
            raise RuntimeError(f"missing enabled fast-source profile for {city}/{policy.source}")
        supported_handler = (
            (policy.signal_handler == "metar_prev_no_exact" and profile.market_unit == "C")
            or (policy.signal_handler == "metar_prev_no_range_2f" and profile.market_unit == "F")
        )
        if not supported_handler:
            raise RuntimeError(
                f"unsupported fast-source handler/unit for {city}/{policy.source}: "
                f"{policy.signal_handler}/{profile.market_unit}"
            )
        if policy.default_mode == "live_trial" and not profile.live_eligible and not policy.source_profile_override_reason:
            raise RuntimeError(f"live-trial source override reason missing for {city}/{policy.source}")
        city_profiles[city] = profile
    sources = set(args.sources or [policy.source for policy in policies.values()])
    return {
        "policies": policies,
        "target_dates": target_dates,
        "profiles": profiles,
        "city_profiles": city_profiles,
        "sources": sources,
    }


def run_once(args: argparse.Namespace, live_place_cache: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out_dir = Path(args.output_dir)
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    orders_path = out_dir / "orders.jsonl"
    share_cap_audit = audit_order_share_caps(orders_path)
    historical_acknowledged = bool(args.acknowledge_historical_share_cap_incidents)
    share_cap_paused, share_cap_pause_reason = resolve_share_cap_pause(
        state,
        share_cap_audit,
        historical_acknowledged=historical_acknowledged,
    )
    seen = set(state.get("seen_event_keys") or [])
    live_order_keys = set(state.get("live_order_keys") or [])
    confirmation_state = dict(state.get("source_cross_confirmation") or {})
    runtime = _load_runtime_inputs(args, now)
    policies: dict[str, FastSourceCityPolicy] = runtime["policies"]
    target_dates: dict[str, str] = runtime["target_dates"]
    profiles = runtime["profiles"]
    city_profiles = runtime["city_profiles"]
    sources = runtime["sources"]
    market_proxy = market_proxy_url(args.market_proxy or None)

    source_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for city, policy in policies.items():
        policy_rows = source_latest_by_city(
            Path(args.high_frequency_latest),
            args.target_date,
            {policy.source},
            profiles,
            now,
        )
        key = (city, target_dates[city])
        if key in policy_rows:
            source_rows[key] = policy_rows[key]
    metar_rows = metar_running_max(Path(args.source_events_jsonl), args.target_date, city_profiles, now)
    clocks = metar_report_clocks(Path(args.source_events_jsonl), target_dates)
    paper_path = latest_paper_snapshot()
    orderbook_path = latest_orderbook_snapshot()
    market_index = build_market_index(paper_path, set(target_dates.values()))
    event_rows: list[dict[str, Any]] = []
    opportunity_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []

    for city, policy in sorted(policies.items()):
        target_date = target_dates[city]
        profile = city_profiles[city]
        src = source_rows.get((city, target_date))
        metar = metar_rows.get((city, target_date))
        live_city = policy.default_mode == "live_trial"
        base = {
            "schema_version": "fast_source_prev_no_trial_v2",
            "ts_utc": iso(now),
            "city": city,
            "target_date": target_date,
            "mode": "live" if args.live and live_city else "shadow",
            "strategy_city_policy": policy.__dict__,
            "fast_source_profile": profile.__dict__,
            "source_profile_live_eligible": profile.live_eligible,
            "source_profile_override_reason": policy.source_profile_override_reason,
            "high_frequency_latest": str(args.high_frequency_latest),
            "source_events_jsonl": str(args.source_events_jsonl),
            "paper_snapshot_path": str(paper_path) if paper_path else "",
            "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        }
        if not src:
            opportunity_rows.append({**base, "status": "source_missing"})
            continue
        if not metar:
            opportunity_rows.append({**base, "status": "metar_missing", "source": src.get("source")})
            continue

        source_obs_dt = parse_dt(src.get("source_obs_ts_utc"))
        source_detect_dt = parse_dt(src.get("source_detect_ts_utc"))
        latest_metar_dt = parse_dt(metar.get("latest_report_ts_utc"))
        source_age = (now - source_obs_dt).total_seconds() / 60.0 if source_obs_dt else None
        detect_age = (now - source_detect_dt).total_seconds() / 60.0 if source_detect_dt else None
        observation_lag = (source_detect_dt - source_obs_dt).total_seconds() / 60.0 if source_obs_dt and source_detect_dt else None
        source_value = int(src["source_market_value"])
        source_temp_c = float(src["temp_c"])
        source_market_temp = source_temp_in_market_unit(source_temp_c, profile.market_unit)
        metar_max = int(metar["metar_running_max_market_value"])
        candidate = source_value - 1
        resolved_token = None
        market_resolution = ""
        if policy.signal_handler == "metar_prev_no_range_2f":
            resolved_token, market_index, range_upper, market_resolution = resolve_range_candidate_market(
                market_index,
                city=city,
                target_date=target_date,
                metar_running_max_value=metar_max,
                market_proxy=market_proxy,
            )
            if range_upper is None:
                opportunity_rows.append(
                    {
                        **base,
                        "status": "missing_t_minus_1_market",
                        "market_resolution": market_resolution,
                        "source": policy.source,
                        "source_market_unit": profile.market_unit,
                        "source_market_temp": round(source_market_temp, 3),
                        "source_market_value": source_value,
                        "metar_running_max_market_value": metar_max,
                    }
                )
                continue
            candidate = range_upper
        window = next_metar_window_status(clocks.get((city, target_date)), now, window_min=args.next_metar_window_min)
        confirmation = source_cross_confirmation(
            city=city,
            source=policy.source,
            target_date=target_date,
            source_market_temp=source_market_temp,
            source_market_value=source_value,
            source_obs_ts_utc=str(src.get("source_obs_ts_utc") or ""),
            metar_running_max_value=metar_max,
            candidate_no_bracket=candidate,
            policy=policy,
            state=confirmation_state,
        )
        common = {
            **base,
            "source": policy.source,
            "station": src.get("station"),
            "source_kind": src.get("source_kind"),
            "source_runway": src.get("runway"),
            "source_primary_runway": src.get("primary_runway"),
            "source_preferred_temperature_runway": src.get("preferred_temperature_runway"),
            "source_is_preferred_temperature_runway": src.get("is_preferred_temperature_runway"),
            "source_obs_ts_utc": src.get("source_obs_ts_utc"),
            "source_detect_ts_utc": src.get("source_detect_ts_utc"),
            "source_age_min": round(source_age, 3) if source_age is not None else None,
            "source_detect_age_min": round(detect_age, 3) if detect_age is not None else None,
            "source_obs_lag_min": round(observation_lag, 3) if observation_lag is not None else None,
            "source_temp_c": src.get("temp_c"),
            "source_market_unit": profile.market_unit,
            "source_market_temp": round(source_market_temp, 3),
            "source_market_value": source_value,
            "source_round_c": source_value,
            "latest_metar_report_ts_utc": metar.get("latest_report_ts_utc"),
            "latest_metar_detect_ts_utc": metar.get("latest_detect_ts_utc"),
            "latest_metar_temp_c": metar.get("latest_metar_temp_c"),
            "latest_metar_market_value": metar.get("latest_metar_round_c"),
            "latest_metar_round_c": metar.get("latest_metar_round_c"),
            "metar_running_max_market_value": metar_max,
            "metar_running_max_round_c": metar_max,
            "metar_running_max_temp_c": metar.get("metar_running_max_temp_c"),
            "t_minus_1_no_bracket": candidate,
            "t_minus_1_no_bracket_c": candidate,
            "t_minus_1_no_market_bracket": str(resolved_token.bracket) if resolved_token is not None else str(candidate),
            **window,
            "source_cross_policy": confirmation["policy"],
            "source_cross_confirmation_basis": confirmation.get("basis"),
            "source_cross_confirmation_basis_c": confirmation.get("basis_c"),
            "source_cross_required_margin_c": confirmation.get("required_margin_c"),
            "source_cross_threshold_c": confirmation.get("threshold_c"),
            "source_cross_strong_margin_c": confirmation.get("strong_margin_c"),
            "source_cross_strong_threshold_c": confirmation.get("strong_threshold_c"),
            "source_cross_strong_observation_seen": confirmation.get("strong_observation_seen"),
            "source_cross_required_distinct_observations": confirmation.get("required_distinct_observations"),
            "source_cross_qualifying_distinct_observations": confirmation.get("qualifying_distinct_observations"),
            "source_cross_confirmed": confirmation["confirmed"],
        }
        blockers: list[str] = []
        if confirmation["blocker"]:
            blockers.append(confirmation["blocker"])
        max_source_age_min = float(policy.max_source_age_min or args.max_source_age_min)
        max_source_observation_lag_min = float(
            policy.max_source_observation_lag_min or args.max_source_observation_lag_min
        )
        common["max_source_age_min"] = max_source_age_min
        common["max_source_observation_lag_min"] = max_source_observation_lag_min
        if source_age is None or source_age < -1.0 or source_age > max_source_age_min:
            blockers.append("source_observation_too_old")
        if detect_age is None or detect_age < -1.0 or detect_age > args.max_source_detect_age_min:
            blockers.append("source_detection_too_old")
        if observation_lag is None or observation_lag < -1.0 or observation_lag > max_source_observation_lag_min:
            blockers.append("source_observation_lag_too_high")
        if source_obs_dt and latest_metar_dt and source_obs_dt <= latest_metar_dt:
            blockers.append("source_not_after_latest_metar")
        if window["next_metar_window_blocker"]:
            blockers.append(str(window["next_metar_window_blocker"]))
        if source_obs_dt and latest_metar_dt:
            common["source_obs_after_latest_metar_report_sec"] = round((source_obs_dt - latest_metar_dt).total_seconds(), 3)
        if blockers:
            opportunity_rows.append({**common, "status": "blocked", "blockers": blockers})
            continue

        event_key = "|".join([city, target_date, policy.source, str(src.get("source_obs_ts_utc")), str(source_value), str(metar_max), str(candidate)])
        token = resolved_token
        if token is None:
            token, market_index, market_resolution = resolve_candidate_market(
                market_index,
                city=city,
                target_date=target_date,
                candidate=candidate,
                market_proxy=market_proxy,
            )
        if token is None:
            opportunity_rows.append(
                {
                    **common,
                    "status": "missing_t_minus_1_market",
                    "event_key": event_key,
                    "market_resolution": market_resolution,
                }
            )
            continue
        book = fetch_fresh_book(token.no_token_id, proxy=market_proxy, timeout_sec=args.book_timeout_sec, top_n=5)
        summary = book.get("summary") or {}
        best_ask = safe_float(summary.get("best_ask"))
        ask_size = safe_float(summary.get("ask_size"))
        tick_size = safe_float(summary.get("tick_size"))
        desired_shares = float(policy.shares_per_trade)
        market_cap = float(policy.max_shares_per_market)
        market_spent = spent_market_shares(orders_path, target_date=target_date, token_id=token.no_token_id)
        live_blockers: list[str] = []
        if book.get("status") != "ok":
            live_blockers.append("fresh_book_not_ok")
        if best_ask is None:
            live_blockers.append("missing_best_ask")
        elif best_ask > policy.max_no_ask:
            live_blockers.append("ask_above_max")
        if tick_size is None:
            live_blockers.append("missing_tick_size")
        if live_city and (ask_size is None or ask_size < desired_shares):
            live_blockers.append("insufficient_top_ask_size")
        if live_city and market_spent + desired_shares > market_cap + 1e-9:
            live_blockers.append("market_share_cap")
        if args.live and live_city and not args.confirm_live:
            live_blockers.append("confirm_live_missing")
        if args.live and live_city and share_cap_paused:
            live_blockers.append("share_cap_paused")
        maker_intent: dict[str, Any] | None = None
        if best_ask is not None and tick_size is not None and desired_shares > 0:
            try:
                maker_intent = exact_share_maker_intent(
                    best_ask=best_ask,
                    tick_size=tick_size,
                    desired_shares=desired_shares,
                    now=now,
                    effective_lifetime_sec=args.maker_effective_lifetime_sec,
                )
            except ValueError:
                live_blockers.append("exact_share_maker_intent_invalid")
        opportunity = {
            **common,
            "status": "cross_candidate",
            "event_key": event_key,
            "market_resolution": market_resolution,
            "question": token.question,
            "t_minus_1_no_market_bracket": token.bracket,
            "market_id": token.market_id,
            "condition_id": token.condition_id,
            "token_id": token.no_token_id,
            "best_ask": best_ask,
            "ask_size": ask_size,
            "tick_size": tick_size,
            "fresh_book_status": book.get("status"),
            "fresh_book_error": book.get("error", ""),
            "fresh_book_http_status": book.get("http_status"),
            "fresh_book_proxy_used": book.get("proxy_used", ""),
            "max_no_ask": policy.max_no_ask,
            "planned_shares": desired_shares,
            "market_spent_shares": market_spent,
            "max_shares_per_market": market_cap,
            "planned_notional_usd": round(desired_shares * float(best_ask or 0.0), 6),
            "live_requested": bool(args.live and live_city),
            "live_enabled": bool(args.live and args.confirm_live and live_city),
            "live_blockers": live_blockers,
            "share_cap_execution_mode": "post_only_gtd_buy_shares",
        }
        opportunity_rows.append(opportunity)
        if event_key not in seen:
            append_jsonl(out_dir / "events.jsonl", opportunity)
            seen.add(event_key)
            event_rows.append(opportunity)
        live_key = "|".join([city, target_date, str(candidate), token.no_token_id, str(src.get("source_obs_ts_utc"))])
        if args.live and args.confirm_live and live_city and not live_blockers and maker_intent is not None and live_key not in live_order_keys:
            order_row = {
                **opportunity,
                "order_side": "BUY",
                **maker_intent,
                "live_attempted": True,
                "live_attempt_ts_utc": iso(),
            }
            if "place" not in live_place_cache:
                live_place_cache["place"] = build_live_post_only_gtd_place_fn(market_proxy)
            result = submit_post_only_gtd(
                order_row,
                place=live_place_cache["place"],
                fetch_book_fn=fetch_fresh_book,
                market_proxy=market_proxy,
                book_timeout_sec=args.book_timeout_sec,
                max_no_ask=policy.max_no_ask,
                immediate_reprices=args.post_only_immediate_reprices,
            )
            order_row = result["order_row"]
            order_row.update(
                {
                    "live_submit_status": result["live_submit_status"],
                    "actual_fill_shares": result.get("actual_fill_shares"),
                    "actual_fill_cost_usd": result.get("actual_fill_cost_usd"),
                    "live_order_posted": bool(result.get("live_order_posted")),
                    "exchange_order_status": result.get("exchange_order_status"),
                    "share_cap_check": result.get("share_cap_check"),
                }
            )
            if result["exchange_response"] is not None:
                response = result["exchange_response"]
                order_row["exchange_response"] = response
                order_row["order_id"] = response.get("order_id")
                live_order_keys.add(live_key)
            if result.get("attempts"):
                order_row["attempts"] = result["attempts"]
            if result["live_submit_status"] == "share_cap_violation":
                share_cap_paused = True
                share_cap_pause_reason = "post_fix_actual_fill_exceeded_desired_or_market_cap"
            if result["error"]:
                order_row["error"] = result["error"]
            append_jsonl(out_dir / "orders.jsonl", order_row)
            order_rows.append(order_row)

    for row in opportunity_rows:
        append_jsonl(out_dir / "opportunities.jsonl", row)
    active_dates = set(target_dates.values())
    confirmation_state = {
        key: value
        for key, value in confirmation_state.items()
        if any(f"|{target_date}|" in key for target_date in active_dates)
    }
    write_json(
        state_path,
        {
            "updated_at_utc": iso(),
            "target_dates_by_city": target_dates,
            "seen_event_keys": sorted(seen)[-5000:],
            "live_order_keys": sorted(live_order_keys)[-5000:],
            "source_cross_confirmation": confirmation_state,
            "share_cap_paused": share_cap_paused,
            "share_cap_pause_reason": share_cap_pause_reason,
        },
    )
    burst_cities = next_metar_burst_cities(opportunity_rows)
    effective_interval = args.burst_interval_sec if burst_cities else args.interval_sec
    latest = {
        "status": "ok",
        "schema_version": "fast_source_prev_no_trial_latest_v2",
        "generated_at_utc": iso(),
        "strategy_id": "fast_source_prev_no_trial_v1",
        "strategy_instance": "fast_source_prev_no_trial_v1",
        "target_dates_by_city": target_dates,
        "sources": sorted(sources),
        "live_cities": sorted(city for city, policy in policies.items() if policy.default_mode == "live_trial"),
        "shadow_cities": sorted(city for city, policy in policies.items() if policy.default_mode == "shadow"),
        "city_policies": {city: policy.__dict__ for city, policy in policies.items()},
        "live_enabled": bool(args.live and args.confirm_live and any(policy.default_mode == "live_trial" for policy in policies.values())),
        "caps": {
            "by_city": {
                city: {
                    "shares_per_trade": policy.shares_per_trade,
                    "max_shares_per_market": policy.max_shares_per_market,
                    "max_no_ask": policy.max_no_ask,
                    "max_source_age_min": float(policy.max_source_age_min or args.max_source_age_min),
                    "max_source_observation_lag_min": float(
                        policy.max_source_observation_lag_min or args.max_source_observation_lag_min
                    ),
                }
                for city, policy in policies.items()
            },
            "max_source_age_min": args.max_source_age_min,
            "max_source_detect_age_min": args.max_source_detect_age_min,
            "max_source_observation_lag_min": args.max_source_observation_lag_min,
            "next_metar_window_min": args.next_metar_window_min,
            "maker_effective_lifetime_sec": args.maker_effective_lifetime_sec,
            "share_cap_enforcement": "resting_post_only_signed_size_v1",
        },
        "share_cap_health": {
            **share_cap_audit,
            "paused": share_cap_paused,
            "pause_reason": share_cap_pause_reason,
            "historical_incidents_acknowledged": historical_acknowledged,
        },
        "source_cities": sorted({city for city, _target_date in source_rows}),
        "metar_cities": sorted({city for city, _target_date in metar_rows}),
        "events": len(event_rows),
        "opportunities": len(opportunity_rows),
        "execution_eligible": len(order_rows),
        "live_orders_attempted": len(order_rows),
        "live_orders_submitted": sum(row.get("live_submit_status") == "submitted" for row in order_rows),
        "live_orders_posted": sum(bool(row.get("live_order_posted")) for row in order_rows),
        "polling": {
            "base_interval_sec": args.interval_sec,
            "burst_interval_sec": args.burst_interval_sec,
            "effective_interval_sec": effective_interval,
            "burst_active": bool(burst_cities),
            "burst_cities": burst_cities,
        },
        "paper_snapshot_path": str(paper_path) if paper_path else "",
        "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        "latest_opportunities": opportunity_rows[-20:],
    }
    write_json(out_dir / "latest.json", latest)
    write_json(out_dir / "latest_summary.json", latest)
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--high-frequency-latest", default=str(DEFAULT_HIGH_FREQUENCY_LATEST))
    parser.add_argument("--source-events-jsonl", default=str(DEFAULT_SOURCE_EVENTS_JSONL))
    parser.add_argument("--sources", nargs="*", default=None)
    parser.add_argument("--live-cities", nargs="*", default=None)
    parser.add_argument("--shadow-cities", nargs="*", default=None)
    parser.add_argument("--max-source-age-min", type=float, default=15.0)
    parser.add_argument("--max-source-detect-age-min", type=float, default=5.0)
    parser.add_argument("--max-source-observation-lag-min", type=float, default=15.0)
    parser.add_argument("--next-metar-window-min", type=float, default=20.0)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument(
        "--post-only-immediate-reprices",
        "--fok-immediate-retries",
        dest="post_only_immediate_reprices",
        type=int,
        default=2,
        help="Immediate fresh-book maker reprices after a post-only crossing rejection.",
    )
    parser.add_argument("--maker-effective-lifetime-sec", type=float, default=45.0)
    parser.add_argument("--acknowledge-historical-share-cap-incidents", action="store_true")
    parser.add_argument("--market-proxy", default=market_proxy_url(None))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--prebuild-live-client", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--burst-interval-sec", type=float, default=10.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    live_place_cache: dict[str, Any] = {}
    if args.live and args.confirm_live and args.prebuild_live_client:
        live_place_cache["place"] = build_live_post_only_gtd_place_fn(market_proxy_url(args.market_proxy or None))
    while True:
        started = time.monotonic()
        latest = run_once(args, live_place_cache)
        print(json.dumps({key: value for key, value in latest.items() if key != "latest_opportunities"}, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        interval = float((latest.get("polling") or {}).get("effective_interval_sec") or args.interval_sec)
        time.sleep(max(5.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
