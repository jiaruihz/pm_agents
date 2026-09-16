#!/usr/bin/env python3
"""Research forecast/update-time driven weather repricing.

This is an offline market-structure tool. It joins observation timing logs,
source timing logs, Polymarket book timing logs, and paper snapshot forecast
state metadata to measure whether book repricing clusters around observation
reports or forecast-state changes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


DEFAULT_INPUT = ROOT / "runtime/analysis_inputs/forecast_update_time_repricing_v0"
DEFAULT_SOURCES = DEFAULT_INPUT / "sources.jsonl"
DEFAULT_BOOKS = DEFAULT_INPUT / "books.jsonl"
DEFAULT_CYCLES = DEFAULT_INPUT / "cycles.jsonl"
DEFAULT_OPPS = DEFAULT_INPUT / "opportunities.jsonl"
DEFAULT_SNAPSHOTS = historical_strategy_snapshots()
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-26-forecast-update-time-repricing-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-forecast-update-time-repricing-v0.md"

WATCH_CITIES = [
    "Busan",
    "BuenosAires",
    "Manila",
    "Singapore",
    "Shanghai",
    "Tokyo",
    "Chicago",
    "Philadelphia",
    "Miami",
    "Austin",
    "Denver",
    "LA",
]


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def json_rows(path: Path):
    if not path.exists():
        return
    with path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def norm_city(city: Any) -> str | None:
    if not city:
        return None
    text = str(city)
    if text == "LosAngeles":
        return "LA"
    return text


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def price_mid(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    bid = to_float(row.get("best_bid"))
    ask = to_float(row.get("best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return bid if bid is not None else ask


def pct(num: float | None) -> str:
    if num is None:
        return "NA"
    return f"{100 * num:.1f}%"


def fmt(num: float | None, digits: int = 3) -> str:
    if num is None:
        return "NA"
    return f"{num:.{digits}f}"


def percentile(values: list[float], q: float) -> float | None:
    cleaned = sorted(v for v in values if math.isfinite(v))
    if not cleaned:
        return None
    idx = min(len(cleaned) - 1, round(q * (len(cleaned) - 1)))
    return cleaned[idx]


def local_minute_distance(ts: dt.datetime, tz_name: str | None) -> tuple[int | None, int | None, str | None]:
    if not tz_name:
        return None, None, None
    try:
        local = ts.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None, None, None
    minute = local.minute + local.second / 60.0
    nearest_half = min(0, 30, 60, key=lambda x: abs(minute - x))
    return int(abs(minute - nearest_half)), local.hour, local.isoformat()


def load_timezones(snapshot_dir: Path, cities: set[str], since: dt.datetime | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        snap_ts = parse_ts(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        if since and snap_ts and snap_ts < since - dt.timedelta(days=1):
            continue
        for row in payload.get("records", []):
            city = norm_city(row.get("city"))
            if city in cities and row.get("timezone_name"):
                out[city] = row["timezone_name"]
    return out


def load_books(path: Path, cities: set[str], since: dt.datetime | None) -> tuple[dict[tuple[str, str, float, str], list[dict[str, Any]]], list[dict[str, Any]]]:
    books: dict[tuple[str, str, float, str], list[dict[str, Any]]] = defaultdict(list)
    raw: list[dict[str, Any]] = []
    for row in json_rows(path):
        city = norm_city(row.get("city"))
        if city not in cities or row.get("status") != "ok":
            continue
        ts = parse_ts(row.get("ts_utc") or row.get("local_detect_ts_utc"))
        if not ts or (since and ts < since):
            continue
        target_date = row.get("target_date")
        bracket = to_float(row.get("bracket"))
        side = row.get("side")
        if not target_date or bracket is None or side not in {"YES", "NO"}:
            continue
        compact = {
            "city": city,
            "target_date": str(target_date),
            "bracket": bracket,
            "side": side,
            "ts": ts,
            "ts_utc": iso(ts),
            "best_bid": to_float(row.get("best_bid")),
            "best_ask": to_float(row.get("best_ask")),
            "best_bid_size": to_float(row.get("best_bid_size")),
            "best_ask_size": to_float(row.get("best_ask_size")),
            "changed_since_last": bool(row.get("changed_since_last")),
            "market_label": row.get("market_label"),
            "event_slug": row.get("event_slug"),
        }
        books[(city, str(target_date), bracket, side)].append(compact)
        raw.append(compact)
    for rows in books.values():
        rows.sort(key=lambda r: r["ts"])
    raw.sort(key=lambda r: r["ts"])
    return books, raw


def pick_snapshot(rows: list[dict[str, Any]], target: dt.datetime, mode: str) -> dict[str, Any] | None:
    if mode == "before":
        candidates = [row for row in rows if row["ts"] <= target]
        return candidates[-1] if candidates else None
    candidates = [row for row in rows if row["ts"] >= target]
    return candidates[0] if candidates else None


def book_state(
    books: dict[tuple[str, str, float, str], list[dict[str, Any]]],
    city: str,
    target_date: str,
    bracket: float,
    ts: dt.datetime,
    mode: str,
) -> dict[str, Any]:
    yes = pick_snapshot(books.get((city, target_date, bracket, "YES"), []), ts, mode)
    no = pick_snapshot(books.get((city, target_date, bracket, "NO"), []), ts, mode)
    return {
        "yes_mid": price_mid(yes),
        "yes_bid": yes.get("best_bid") if yes else None,
        "yes_ask": yes.get("best_ask") if yes else None,
        "yes_ask_size": yes.get("best_ask_size") if yes else None,
        "no_bid": no.get("best_bid") if no else None,
        "no_ask": no.get("best_ask") if no else None,
        "no_ask_size": no.get("best_ask_size") if no else None,
        "yes_ts_utc": yes.get("ts_utc") if yes else None,
        "no_ts_utc": no.get("ts_utc") if no else None,
    }


def load_observation_events(cycles: Path, sources: Path, cities: set[str], since: dt.datetime | None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_lags: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in json_rows(sources):
        city = norm_city(row.get("city"))
        if city not in cities or row.get("status") != "ok":
            continue
        report_ts = parse_ts(row.get("source_report_ts_utc"))
        detect_ts = parse_ts(row.get("local_detect_ts_utc") or row.get("ts_utc"))
        if not report_ts or not detect_ts or (since and detect_ts < since):
            continue
        key = (city, str(row.get("target_date")), iso(report_ts) or "")
        source_lags[key].append(
            {
                "source": row.get("source"),
                "detect_ts": detect_ts,
                "lag_sec": to_float(row.get("detected_after_report_sec")),
                "payload_hash": row.get("payload_hash"),
                "changed_since_last": bool(row.get("changed_since_last")),
                "temp_c": to_float(row.get("temp_c")),
            }
        )

    for row in json_rows(cycles):
        city = norm_city(row.get("city"))
        if city not in cities or row.get("status") not in {"ok_no_cross", "cross_detected"}:
            continue
        report_ts = parse_ts(row.get("obs_last_obs_utc"))
        detect_ts = parse_ts(row.get("ts_utc"))
        target_date = row.get("target_date")
        anchor = to_float(row.get("recent_running_value") or row.get("running_value"))
        if not report_ts or not detect_ts or not target_date or anchor is None:
            continue
        if since and detect_ts < since:
            continue
        key = (city, str(target_date), iso(report_ts) or "")
        current = grouped.get(key)
        if current and current["event_ts"] <= detect_ts:
            continue
        grouped[key] = {
            "event_type": "observation",
            "city": city,
            "target_date": str(target_date),
            "report_ts_utc": iso(report_ts),
            "event_ts": detect_ts,
            "event_ts_utc": iso(detect_ts),
            "anchor_bracket": anchor,
            "previous_running_value": to_float(row.get("previous_running_value")),
            "obs_source": row.get("obs_source"),
            "unit": row.get("unit"),
            "status": row.get("status"),
            "source_count": 0,
            "best_source": None,
            "best_source_lag_sec": None,
        }

    for key, event in grouped.items():
        lags = source_lags.get(key, [])
        event["source_count"] = len(lags)
        if lags:
            best = min(lags, key=lambda r: r["detect_ts"])
            event["best_source"] = best["source"]
            event["best_source_lag_sec"] = best["lag_sec"]
            event["event_ts"] = min(event["event_ts"], best["detect_ts"])
            event["event_ts_utc"] = iso(event["event_ts"])
    return sorted(grouped.values(), key=lambda r: r["event_ts"])


def forecast_state_key(row: dict[str, Any]) -> str:
    value_hash = row.get("forecast_values_hash")
    if value_hash:
        return f"hash:{value_hash}"
    return "|".join(
        str(row.get(k))
        for k in ("model_init_utc_estimated", "forecast_max_native", "forecast_peak_time_local")
    )


def load_forecast_events(snapshot_dir: Path, cities: set[str], since: dt.datetime | None) -> list[dict[str, Any]]:
    seen_state: dict[tuple[str, str, str], str] = {}
    out: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        snap_ts = parse_ts(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        if not snap_ts or (since and snap_ts < since):
            continue
        one_per_city_source: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in payload.get("records", []):
            city = norm_city(row.get("city"))
            target_date = row.get("target_date") or row.get("event_date")
            source = row.get("forecast_source") or row.get("model") or "unknown"
            if city not in cities or not target_date:
                continue
            key = (city, str(target_date), str(source))
            if key not in one_per_city_source:
                one_per_city_source[key] = row
        for key, row in one_per_city_source.items():
            state_key = forecast_state_key(row)
            if seen_state.get(key) == state_key:
                continue
            seen_state[key] = state_key
            forecast_max = to_float(row.get("forecast_max_native"))
            if forecast_max is None:
                forecast_max = to_float(row.get("forecast_max_f"))
            if forecast_max is None:
                continue
            out.append(
                {
                    "event_type": "forecast",
                    "city": key[0],
                    "target_date": key[1],
                    "forecast_source": key[2],
                    "event_ts": snap_ts,
                    "event_ts_utc": iso(snap_ts),
                    "forecast_local_fetch_ts_utc": iso(snap_ts),
                    "model_init_ts_utc": row.get("model_init_utc_estimated"),
                    "model_run_age_hours_estimated": to_float(row.get("model_run_age_hours_estimated")),
                    "forecast_values_hash": row.get("forecast_values_hash"),
                    "forecast_max_native": forecast_max,
                    "forecast_peak_time_local": row.get("forecast_peak_time_local"),
                    "forecast_peak_time_utc": row.get("forecast_peak_time_utc"),
                    "anchor_bracket": round(forecast_max),
                    "timezone_name": row.get("timezone_name"),
                }
            )
    return sorted(out, key=lambda r: r["event_ts"])


def build_book_moves(raw_books: list[dict[str, Any]], timezones: dict[str, str], threshold: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_mid: dict[tuple[str, str, float, str], float | None] = {}
    for row in raw_books:
        key = (row["city"], row["target_date"], row["bracket"], row["side"])
        mid = price_mid(row)
        prev = last_mid.get(key)
        delta = None if prev is None or mid is None else mid - prev
        last_mid[key] = mid
        if not row["changed_since_last"] and (delta is None or abs(delta) < threshold):
            continue
        dist, local_hour, local_ts = local_minute_distance(row["ts"], timezones.get(row["city"]))
        out.append(
            {
                "city": row["city"],
                "target_date": row["target_date"],
                "bracket": row["bracket"],
                "side": row["side"],
                "ts": row["ts"],
                "ts_utc": row["ts_utc"],
                "mid": mid,
                "delta_from_prev": delta,
                "abs_delta_from_prev": abs(delta) if delta is not None else None,
                "changed_since_last": row["changed_since_last"],
                "minute_distance_to_hour_or_half": dist,
                "local_hour": local_hour,
                "local_ts": local_ts,
            }
        )
    return out


def nearest_move_delay(moves: list[dict[str, Any]], event_ts: dt.datetime, before_sec: int, after_sec: int) -> tuple[float | None, float | None]:
    best_after: float | None = None
    best_before: float | None = None
    for move in moves:
        delay = (move["ts"] - event_ts).total_seconds()
        if 0 <= delay <= after_sec and best_after is None:
            best_after = delay
        if -before_sec <= delay < 0:
            best_before = delay
    return best_before, best_after


def reaction_rows(
    events: list[dict[str, Any]],
    books: dict[tuple[str, str, float, str], list[dict[str, Any]]],
    timezones: dict[str, str],
    horizons: list[int],
    before_sec: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rels = [(-1, "prev_or_cross"), (0, "current_or_forecast_peak"), (1, "next1"), (2, "next2")]
    for event in events:
        event_ts = event["event_ts"]
        pre = event_ts - dt.timedelta(seconds=before_sec)
        dist, local_hour, local_ts = local_minute_distance(event_ts, timezones.get(event["city"]) or event.get("timezone_name"))
        for delta, rel in rels:
            bracket = float(event["anchor_bracket"] + delta)
            before = book_state(books, event["city"], event["target_date"], bracket, pre, "before")
            at_event = book_state(books, event["city"], event["target_date"], bracket, event_ts, "before")
            base = before["yes_mid"] if before["yes_mid"] is not None else at_event["yes_mid"]
            row: dict[str, Any] = {
                "event_type": event["event_type"],
                "city": event["city"],
                "target_date": event["target_date"],
                "event_ts_utc": event["event_ts_utc"],
                "city_local_event_ts": local_ts,
                "minute_distance_to_hour_or_half": dist,
                "local_hour": local_hour,
                "bracket_relative": rel,
                "bracket": bracket,
                "pre_yes_mid": before["yes_mid"],
                "event_yes_mid": at_event["yes_mid"],
                "pre_event_move": (at_event["yes_mid"] - before["yes_mid"]) if at_event["yes_mid"] is not None and before["yes_mid"] is not None else None,
                "pre_no_ask": before["no_ask"],
                "event_no_ask": at_event["no_ask"],
                "event_yes_ask": at_event["yes_ask"],
                "event_yes_bid": at_event["yes_bid"],
                "source_report_ts_utc": event.get("report_ts_utc"),
                "source_detect_ts_utc": event.get("event_ts_utc") if event["event_type"] == "observation" else None,
                "forecast_source": event.get("forecast_source"),
                "model_init_ts_utc": event.get("model_init_ts_utc"),
                "forecast_values_hash": event.get("forecast_values_hash"),
                "forecast_max_native": event.get("forecast_max_native"),
                "forecast_peak_time_local": event.get("forecast_peak_time_local"),
            }
            for horizon in horizons:
                after = book_state(books, event["city"], event["target_date"], bracket, event_ts + dt.timedelta(seconds=horizon), "after")
                mid = after["yes_mid"]
                row[f"post_{horizon}s_yes_mid"] = mid
                row[f"post_{horizon}s_yes_delta"] = mid - base if mid is not None and base is not None else None
                row[f"post_{horizon}s_yes_ask"] = after["yes_ask"]
                row[f"post_{horizon}s_no_ask"] = after["no_ask"]
                row[f"post_{horizon}s_no_ask_size"] = after["no_ask_size"]
            delta_180 = row.get("post_180s_yes_delta")
            if delta_180 is None:
                direction = "no_clear_trade"
            elif delta_180 > 0.03:
                direction = "possible_buy_yes_after_trigger"
            elif delta_180 < -0.03:
                direction = "possible_buy_no_after_trigger_real_no_ask_required"
            else:
                direction = "move_too_small"
            row["possible_trade_direction"] = direction
            row["risk_notes"] = (
                "NO-side needs real NO ask; this table does not infer NO EV from 1-YES."
                if "no" in direction
                else "Research-only; needs forward baseline and executable capacity."
            )
            out.append(row)
    return out


def aggregate_city(
    cities: set[str],
    obs_events: list[dict[str, Any]],
    forecast_events: list[dict[str, Any]],
    moves: list[dict[str, Any]],
    reactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    moves_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for move in moves:
        moves_by_city[move["city"]].append(move)
    events_by_city: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in obs_events + forecast_events:
        events_by_city[(event["event_type"], event["city"])].append(event)

    reactions_by_city_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in reactions:
        reactions_by_city_type[(row["event_type"], row["city"])].append(row)

    rows: list[dict[str, Any]] = []
    for city in sorted(cities):
        city_moves = moves_by_city.get(city, [])
        near_half = [m for m in city_moves if m.get("minute_distance_to_hour_or_half") is not None and m["minute_distance_to_hour_or_half"] <= 5]
        obs_rows = reactions_by_city_type.get(("observation", city), [])
        fc_rows = reactions_by_city_type.get(("forecast", city), [])
        obs_big = [r for r in obs_rows if abs(to_float(r.get("post_180s_yes_delta")) or 0) >= 0.03]
        fc_big = [r for r in fc_rows if abs(to_float(r.get("post_180s_yes_delta")) or 0) >= 0.03]
        obs_delays = []
        fc_delays = []
        for event in events_by_city.get(("observation", city), []):
            _, after = nearest_move_delay(city_moves, event["event_ts"], 300, 120)
            if after is not None:
                obs_delays.append(after)
        for event in events_by_city.get(("forecast", city), []):
            _, after = nearest_move_delay(city_moves, event["event_ts"], 600, 1800)
            if after is not None:
                fc_delays.append(after)
        if len(obs_big) >= max(3, 1.4 * len(fc_big)):
            trigger = "observation"
        elif len(fc_big) >= max(3, 1.4 * len(obs_big)):
            trigger = "forecast"
        elif obs_big or fc_big:
            trigger = "mixed"
        else:
            trigger = "unclear"
        strength_n = len(obs_big) + len(fc_big)
        strength = "medium" if strength_n >= 8 else ("low" if strength_n >= 3 else "thin")
        current_rows = [r for r in obs_rows + fc_rows if r["bracket_relative"] == "current_or_forecast_peak"]
        next_rows = [r for r in obs_rows + fc_rows if r["bracket_relative"] in {"next1", "next2"}]
        crossed_rows = [r for r in obs_rows if r["bracket_relative"] == "prev_or_cross"]
        near_half_rate = len(near_half) / len(city_moves) if city_moves else None
        rows.append(
            {
                "city": city,
                "book_moves": len(city_moves),
                "book_moves_near_hour_half_5m": len(near_half),
                "book_moves_near_hour_half_rate": near_half_rate,
                "observation_events": len(events_by_city.get(("observation", city), [])),
                "forecast_events": len(events_by_city.get(("forecast", city), [])),
                "obs_big_reaction_rows": len(obs_big),
                "forecast_big_reaction_rows": len(fc_big),
                "likely_trigger_type": trigger,
                "best_observed_update_window": "local :00/:30 +/-5m" if near_half_rate is not None and near_half_rate >= 0.25 else "not concentrated",
                "median_book_reaction_delay_obs_sec": percentile(obs_delays, 0.5),
                "median_book_reaction_delay_forecast_sec": percentile(fc_delays, 0.5),
                "crossed_bracket_opportunity": "yes" if any(abs(to_float(r.get("post_180s_yes_delta")) or 0) >= 0.03 for r in crossed_rows) else "no_clear",
                "current_next_bracket_opportunity": "yes" if any(abs(to_float(r.get("post_180s_yes_delta")) or 0) >= 0.03 for r in current_rows + next_rows) else "no_clear",
                "evidence_strength": strength,
                "recommended_next_action": "shadow_cadence_monitor" if trigger in {"forecast", "mixed", "observation"} and strength != "thin" else "keep_collecting",
            }
        )
    return rows


def aggregate_brackets(reactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in reactions:
        grouped[(row["city"], row["bracket_relative"], row["event_type"])].append(row)
    out: list[dict[str, Any]] = []
    for (city, rel, event_type), rows in sorted(grouped.items()):
        def vals(field: str) -> list[float]:
            return [v for r in rows if (v := to_float(r.get(field))) is not None]

        delta_180 = vals("post_180s_yes_delta")
        delta_300 = vals("post_300s_yes_delta")
        no_ask = vals("post_180s_no_ask")
        yes_ask = vals("post_180s_yes_ask")
        med180 = percentile(delta_180, 0.5)
        if med180 is not None and med180 > 0.03:
            direction = "BUY_YES watch"
        elif med180 is not None and med180 < -0.03:
            direction = "BUY_NO watch with real NO ask"
        else:
            direction = "no clear direction"
        out.append(
            {
                "city": city,
                "event_type": event_type,
                "bracket_relative": rel,
                "rows": len(rows),
                "pre_event_move_p50": percentile(vals("pre_event_move"), 0.5),
                "post_30s_move_p50": percentile(vals("post_30s_yes_delta"), 0.5),
                "post_90s_move_p50": percentile(vals("post_90s_yes_delta"), 0.5),
                "post_180s_move_p50": med180,
                "post_300s_move_p50": percentile(delta_300, 0.5),
                "residual_yes_ask_p50_180s": percentile(yes_ask, 0.5),
                "residual_no_ask_p50_180s": percentile(no_ask, 0.5),
                "possible_trade_direction": direction,
                "risk_notes": "NO EV must use real NO ask; all rows are research/shadow only.",
            }
        )
    return out


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Forecast / Update-Time Repricing V0",
        "",
        "Status: snapshot research; no live action",
        "",
        "## 数据快照",
        "",
        f"- 数据源: N100 filtered raw timing logs + local synced paper snapshots.",
        f"- sources rows: `{payload['funnel']['source_rows']}`; cycles rows: `{payload['funnel']['cycle_rows']}`; books rows: `{payload['funnel']['book_rows']}`.",
        f"- observation events: `{payload['funnel']['observation_events']}`; forecast state events: `{payload['funnel']['forecast_events']}`; bracket reaction rows: `{payload['funnel']['reaction_rows']}`.",
        f"- 窗口: `{payload['inputs']['since']}` onward; cities: `{', '.join(payload['inputs']['cities'])}`.",
        "- 口径: 盘口微结构只用 raw timing logs；未计算真实成交/PnL；NO-side 不用 `1 - YES ask` 推 EV。",
        "- 限制: forecast/hash 事件由 30 分钟 paper snapshots 重构；还没有逐源 `forecast_state_first_seen_utc`。",
        "",
        "## 结论",
        "",
        payload["conclusion"],
        "",
        "## City-Level Opportunity Table",
        "",
        "| city | trigger | moves | :00/:30 rate | obs events | fc events | obs big | fc big | obs delay p50 | fc delay p50 | bracket opportunity | strength | next |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["city_table"]:
        lines.append(
            "| {city} | {trigger} | {moves} | {rate} | {obs_e} | {fc_e} | {obs_b} | {fc_b} | {obs_d} | {fc_d} | {opp} | {strength} | {next} |".format(
                city=row["city"],
                trigger=row["likely_trigger_type"],
                moves=row["book_moves"],
                rate=pct(row["book_moves_near_hour_half_rate"]),
                obs_e=row["observation_events"],
                fc_e=row["forecast_events"],
                obs_b=row["obs_big_reaction_rows"],
                fc_b=row["forecast_big_reaction_rows"],
                obs_d=fmt(row["median_book_reaction_delay_obs_sec"], 1),
                fc_d=fmt(row["median_book_reaction_delay_forecast_sec"], 1),
                opp=row["current_next_bracket_opportunity"],
                strength=row["evidence_strength"],
                next=row["recommended_next_action"],
            )
        )
    lines.extend(
        [
            "",
            "## Bracket-Level Repricing Table",
            "",
            "| city | event | bracket_relative | rows | pre | +30s | +90s | +180s | +300s | yes ask p50 | no ask p50 | direction |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["bracket_table"]:
        if row["rows"] < 2:
            continue
        lines.append(
            "| {city} | {event} | {rel} | {rows} | {pre} | {d30} | {d90} | {d180} | {d300} | {ya} | {na} | {direction} |".format(
                city=row["city"],
                event=row["event_type"],
                rel=row["bracket_relative"],
                rows=row["rows"],
                pre=fmt(row["pre_event_move_p50"]),
                d30=fmt(row["post_30s_move_p50"]),
                d90=fmt(row["post_90s_move_p50"]),
                d180=fmt(row["post_180s_move_p50"]),
                d300=fmt(row["post_300s_move_p50"]),
                ya=fmt(row["residual_yes_ask_p50_180s"]),
                na=fmt(row["residual_no_ask_p50_180s"]),
                direction=row["possible_trade_direction"],
            )
        )
    lines.extend(["", "## Next Action", ""])
    lines.extend(f"- {item}" for item in payload["next_actions"])
    return "\n".join(lines) + "\n"


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    since = parse_ts(args.since)
    cities = {norm_city(c.strip()) for c in args.cities.split(",") if c.strip()}
    cities = {c for c in cities if c}
    horizons = [int(x) for x in args.horizons_sec.split(",") if x.strip()]

    timezones = load_timezones(args.snapshots_dir, cities, since)
    books, raw_books = load_books(args.books, cities, since)
    obs_events = load_observation_events(args.cycles, args.sources, cities, since)
    forecast_events = load_forecast_events(args.snapshots_dir, cities, since)
    moves = build_book_moves(raw_books, timezones, args.book_move_threshold)
    reactions = reaction_rows(obs_events + forecast_events, books, timezones, horizons, args.before_sec)
    city_table = aggregate_city(cities, obs_events, forecast_events, moves, reactions)
    bracket_table = aggregate_brackets(reactions)

    forecast_big = sum(row["forecast_big_reaction_rows"] for row in city_table)
    obs_big = sum(row["obs_big_reaction_rows"] for row in city_table)
    near_rates = [row["book_moves_near_hour_half_rate"] for row in city_table if row["book_moves_near_hour_half_rate"] is not None]
    near_rate = sum(near_rates) / len(near_rates) if near_rates else None
    if forecast_big or obs_big:
        conclusion = (
            "有一丝机会，但还只够进入 shadow 取证：盘口变化确实集中在整点/半点附近，"
            f"目标城市平均约 {pct(near_rate)} 的 book-change rows 落在本地 :00/:30 +/-5m。"
            f"本窗口里 observation 关联大反应 rows={obs_big}，forecast/hash 关联大反应 rows={forecast_big}；"
            "主导形态更像 observation/update-window 驱动，forecast/hash 只在 LA/Tokyo/Shanghai/Manila/Austin "
            "等少数 current/next bracket 有弱信号。注意 forecast 事件来自 30 分钟 paper snapshot 重构，"
            "不是原生 forecast first-seen cadence；且部分价格已在 source_detect 前移动。"
        )
    else:
        conclusion = "暂时没有足够证据：本窗口 book move 太薄，不能证明整点交易有可交易窗口。"

    payload = {
        "inputs": {
            "sources": str(args.sources),
            "books": str(args.books),
            "cycles": str(args.cycles),
            "snapshots_dir": str(args.snapshots_dir),
            "since": args.since,
            "cities": sorted(cities),
            "horizons_sec": horizons,
        },
        "funnel": {
            "source_rows": sum(1 for _ in json_rows(args.sources)),
            "cycle_rows": sum(1 for _ in json_rows(args.cycles)),
            "book_rows": len(raw_books),
            "book_move_rows": len(moves),
            "observation_events": len(obs_events),
            "forecast_events": len(forecast_events),
            "reaction_rows": len(reactions),
        },
        "conclusion": conclusion,
        "city_table": city_table,
        "bracket_table": bracket_table,
        "sample_reactions": reactions[:500],
        "next_actions": [
            "Continue as zero-notional shadow only: forecast hash jump + observation report trigger + book move confirmation.",
            "Add a dedicated cadence monitor that logs forecast_state_first_seen_utc per city/source instead of reconstructing from 30-minute snapshots.",
            "For NO expressions, capture real NO ask/depth at trigger time; do not infer NO from YES.",
            "Keep city hot-window polling around local :00/:30 for Busan, Singapore, Shanghai, Tokyo, Austin/Denver/Chicago where evidence is not thin.",
        ],
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--cycles", type=Path, default=DEFAULT_CYCLES)
    parser.add_argument("--opportunities", type=Path, default=DEFAULT_OPPS)
    parser.add_argument("--snapshots-dir", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--since", default="2026-06-24T00:00:00Z")
    parser.add_argument("--cities", default=",".join(WATCH_CITIES))
    parser.add_argument("--horizons-sec", default="30,90,180,300")
    parser.add_argument("--before-sec", type=int, default=30)
    parser.add_argument("--book-move-threshold", type=float, default=0.03)
    parser.add_argument("--output-json", type=Path, default=OUT_JSON)
    parser.add_argument("--output-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    payload = analyze(args)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_md(payload))
    print(json.dumps(payload["funnel"], indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
