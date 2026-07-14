#!/usr/bin/env python3
"""PIT replay for the first heat-death confirmation after a METAR event.

This is intentionally separate from the hourly-last late-carry replay.  The
decision clock is:

    first detection of a new METAR/SPECI report
      -> first subsequently published paper snapshot
      -> first direct executable quote captured by that snapshot

The historical source-event archive starts on 2026-07-06, so the default
settled window is deliberately short and the result cannot qualify for live.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import random
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import city_local_datetime  # noqa: E402
from weather_data_feed.physical_features import metar_physical_features  # noqa: E402
from weather_data_feed.weather_context import (  # noqa: E402
    cloud_warming_interaction,
    moisture_cloud_interaction,
    warming_state,
    wind_thermal_interaction,
)
from weather_feature_layer.market import parse_bracket, settlement_interval  # noqa: E402


RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
SOURCE_EVENTS_DIR = RUNTIME_ROOT / "output" / "source_events"
SNAPSHOT_DIR = RUNTIME_ROOT / "targeted_output" / "paper_snapshots"
DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/heat_death_early_event_replay_v1"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-heat-death-early-event-replay-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-14-heat-death-early-event-replay-v1.md"

FEE_RATE = 0.05
WINDOW_START = 13.0
WINDOW_END = 17.0
PRICE_MIN = 0.20
PRICE_MAX = 0.97
MAX_QUOTE_DELAY_MIN = 30.0
TEMP_RE = re.compile(r"\b(M?\d{2})/(M?\d{2}|//)\b")
SKY_RE = re.compile(r"\b(CLR|SKC|CAVOK|FEW|SCT|BKN|OVC|VV)(?:\d{3}|///)?\b")


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def native_temp(temp_c: float, unit: str) -> float:
    return temp_c * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else temp_c


def fee(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            if isinstance(row, dict):
                yield row


def load_source_events(source_dir: Path, start: str, end: str) -> list[dict[str, Any]]:
    """Keep the earliest detection for each city/report timestamp."""

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc) - timedelta(days=1)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=2)
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(source_dir.glob("20*/sources.jsonl")):
        for row in iter_jsonl(path):
            if row.get("status") != "ok" or not row.get("raw_metar"):
                continue
            city = str(row.get("city") or "")
            report_ts = parse_ts(row.get("source_report_ts_utc"))
            detect_ts = parse_ts(row.get("local_detect_ts_utc") or row.get("ts_utc"))
            temp_c = finite(row.get("temp_c"))
            if not city or report_ts is None or detect_ts is None or temp_c is None:
                continue
            if not (start_dt <= report_ts <= end_dt):
                continue
            target_date = city_local_datetime(city, report_ts).date().isoformat()
            if not (start <= target_date <= end):
                continue
            key = (city, iso(report_ts) or "")
            candidate = {
                **row,
                "city": city,
                "target_date": target_date,
                "report_dt": report_ts,
                "detect_dt": detect_ts,
                "temp_c": temp_c,
                "unit": str(row.get("unit") or "C").upper(),
            }
            current = best.get(key)
            if current is None or detect_ts < current["detect_dt"]:
                best[key] = candidate
    return sorted(best.values(), key=lambda row: (row["city"], row["target_date"], row["report_dt"]))


def dewpoint_c(raw_metar: str) -> float | None:
    match = TEMP_RE.search(raw_metar.upper())
    if not match or match.group(2) == "//":
        return None
    token = match.group(2)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def relative_humidity(temp_c: float, dew_c: float | None) -> float | None:
    if dew_c is None:
        return None
    a, b = 17.625, 243.04
    return 100.0 * math.exp(a * dew_c / (b + dew_c)) / math.exp(a * temp_c / (b + temp_c))


def sky_code(raw_metar: str) -> int | None:
    severity = {"CLR": 0, "SKC": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
    codes = [severity[token] for token in SKY_RE.findall(raw_metar.upper())]
    return max(codes) if codes else None


def closest_prior(events: list[dict[str, Any]], index: int, minutes: float) -> dict[str, Any] | None:
    current = events[index]["report_dt"]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for prior in events[:index]:
        gap = (current - prior["report_dt"]).total_seconds() / 60.0
        if 30.0 <= gap <= 90.0:
            candidates.append((abs(gap - minutes), prior))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def derive_event_states(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        grouped[(row["city"], row["target_date"])].append(row)
    out: list[dict[str, Any]] = []
    for (city, target_date), rows in grouped.items():
        rows.sort(key=lambda row: row["report_dt"])
        max_c = -math.inf
        max_dt: datetime | None = None
        day_has_speci = any(str(row.get("raw_metar") or "").upper().startswith("SPECI") for row in rows)
        for idx, row in enumerate(rows):
            temp_c = float(row["temp_c"])
            if temp_c >= max_c:
                max_c = temp_c
                max_dt = row["report_dt"]
            prior = closest_prior(rows, idx, 60.0)
            trend_f = None if prior is None else (temp_c - float(prior["temp_c"])) * 9.0 / 5.0
            unit = row["unit"]
            current_native = native_temp(temp_c, unit)
            running_native = native_temp(max_c, unit)
            decline = running_native - current_native
            mins_since_max = None if max_dt is None else (row["detect_dt"] - max_dt).total_seconds() / 60.0
            local = city_local_datetime(city, row["detect_dt"])
            hour = local.hour + local.minute / 60.0 + local.second / 3600.0
            raw = str(row.get("raw_metar") or "")
            physical = metar_physical_features(raw)
            sky = sky_code(raw)
            dew_c = dewpoint_c(raw)
            rh = relative_humidity(temp_c, dew_c)
            dew_dep_f = None if dew_c is None else (temp_c - dew_c) * 9.0 / 5.0
            warm = warming_state(trend_f, None)
            cloud = cloud_warming_interaction(sky, trend_f, None)
            moisture = moisture_cloud_interaction(rh, dew_dep_f, sky)
            wind = wind_thermal_interaction(city, physical.get("metar_wind_speed_kt"), physical.get("metar_wind_dir_deg"))
            support: list[str] = []
            if physical.get("precip_observed"):
                support.append("observed_precipitation")
            if cloud == "cloud_limited_flat_or_cooling":
                support.append("cloud_limited_flat_or_cooling")
            if moisture in {"humid_cloud_suppression", "cloud_suppression"}:
                support.append("moisture_cloud_suppression")
            if wind.get("marine_thermal_state") == "onshore_marine_cooling_risk":
                support.append("onshore_marine_cooling")
            prebase = (
                WINDOW_START <= hour <= WINDOW_END
                and decline >= 0.5
                and mins_since_max is not None
                and mins_since_max >= 60.0
                and warm in {"flat", "cooling"}
            )
            out.append(
                {
                    **{key: value for key, value in row.items() if key not in {"report_dt", "detect_dt"}},
                    "source_report_ts_utc": iso(row["report_dt"]),
                    "source_detect_ts_utc": iso(row["detect_dt"]),
                    "decision_hour_local_at_detect": hour,
                    "current_temp_c": temp_c,
                    "running_max_c": max_c,
                    "current_native": current_native,
                    "running_native": running_native,
                    "decline_native": decline,
                    "minutes_since_running_max_at_detect": mins_since_max,
                    "temp_trend_1h_f": trend_f,
                    "warming_state": warm,
                    "relative_humidity_pct": rh,
                    "sky_cover_code": sky,
                    "precip_state": physical.get("precip_state"),
                    "physical_support_reasons_partial": support,
                    "physical_support_count_partial": len(support),
                    "prebase_without_forecast_clock": prebase,
                    "report_kind": "SPECI" if raw.upper().startswith("SPECI") else "METAR",
                    "source_lane": "metar_speci_capable" if day_has_speci else "routine_metar_only",
                }
            )
    return sorted(out, key=lambda row: row["source_detect_ts_utc"] or "")


def snapshot_nominal_utc(path: Path) -> datetime | None:
    match = re.search(r"snapshot_(\d{8})_(\d{4})", path.name)
    if not match:
        return None
    bj = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M").replace(tzinfo=timezone(timedelta(hours=8)))
    return bj.astimezone(timezone.utc)


def snapshot_index(snapshot_dir: Path) -> tuple[list[datetime], list[Path]]:
    pairs = [(snapshot_nominal_utc(path), path) for path in snapshot_dir.glob("snapshot_*.json")]
    ordered = sorted((dt, path) for dt, path in pairs if dt is not None)
    return [dt for dt, _path in ordered], [path for _dt, path in ordered]


@lru_cache(maxsize=16)
def read_snapshot(path_text: str) -> dict[str, Any]:
    return json.loads(Path(path_text).read_text(encoding="utf-8"))


def first_snapshot_after(
    detect: datetime,
    nominal_times: list[datetime],
    paths: list[Path],
) -> tuple[Path, dict[str, Any], datetime] | None:
    idx = max(0, bisect.bisect_left(nominal_times, detect) - 1)
    for path in paths[idx : idx + 5]:
        payload = read_snapshot(str(path))
        snapshot_ts = parse_ts(payload.get("ts_utc"))
        if snapshot_ts is not None and snapshot_ts >= detect:
            return path, payload, snapshot_ts
    return None


def interval(record: Mapping[str, Any]) -> tuple[float, float] | None:
    bracket = parse_bracket(str(record.get("bracket") or ""))
    if bracket is None or bracket.low is None:
        return None
    question = str(record.get("question") or "").lower()
    if "or below" in question or "or lower" in question or "or less" in question:
        return -math.inf, bracket.low + 0.5
    if "or above" in question or "or higher" in question or bracket.high is None:
        return bracket.low - 0.5, math.inf
    return settlement_interval(str(record.get("bracket") or ""))


def ladder_for(payload: Mapping[str, Any], city: str, target_date: str) -> list[dict[str, Any]]:
    records = [
        dict(row)
        for row in payload.get("records") or []
        if isinstance(row, Mapping)
        and str(row.get("city") or "") == city
        and str(row.get("target_date") or row.get("event_date") or "") == target_date
        and str(row.get("city_local_date_at_snapshot") or target_date) == target_date
        and interval(row) is not None
    ]
    return sorted(records, key=lambda row: (interval(row) or (math.inf, math.inf), str(row.get("bracket") or "")))


def quote(record: Mapping[str, Any] | None, side: str, detect: datetime, snapshot_ts: datetime) -> dict[str, Any]:
    if record is None:
        return {"ask": None, "quote_ts": None, "quote_delay_min": None, "valid": False}
    prefix = side.lower()
    ask = finite(record.get(f"{prefix}_best_ask"))
    book_ts = parse_ts(record.get(f"{prefix}_book_fetched_at_utc"))
    quote_ts = max(snapshot_ts, book_ts) if book_ts is not None else snapshot_ts
    delay = (quote_ts - detect).total_seconds() / 60.0
    valid = ask is not None and 0 <= delay <= MAX_QUOTE_DELAY_MIN
    return {"ask": ask, "quote_ts": iso(quote_ts), "quote_delay_min": delay, "valid": valid}


def attach_market(states: list[dict[str, Any]], snapshot_dir: Path) -> list[dict[str, Any]]:
    nominal_times, paths = snapshot_index(snapshot_dir)
    out: list[dict[str, Any]] = []
    for state in states:
        if not state["prebase_without_forecast_clock"]:
            continue
        detect = parse_ts(state["source_detect_ts_utc"])
        if detect is None:
            continue
        selected = first_snapshot_after(detect, nominal_times, paths)
        if selected is None:
            continue
        path, payload, snapshot_ts = selected
        ladder = ladder_for(payload, state["city"], state["target_date"])
        current_idx = next(
            (
                idx
                for idx, record in enumerate(ladder)
                if (interval(record) or (math.inf, -math.inf))[0] <= state["running_native"] < (interval(record) or (math.inf, -math.inf))[1]
            ),
            None,
        )
        if current_idx is None:
            continue
        current = ladder[current_idx]
        current_interval = interval(current)
        d1 = next(
            (
                record
                for record in ladder[current_idx + 1 :]
                if current_interval is not None and interval(record) is not None and interval(record)[0] >= current_interval[1] - 1e-9
            ),
            None,
        )
        current_yes = quote(current, "yes", detect, snapshot_ts)
        d1_no = quote(d1, "no", detect, snapshot_ts)
        peak_delta = finite(current.get("forecast_peak_delta_hours_local"))
        base = peak_delta is not None and peak_delta >= 0.25
        strong = base and int(state["physical_support_count_partial"]) >= 2
        out.append(
            {
                **state,
                "snapshot_path": str(path),
                "decision_snapshot_ts_utc": iso(snapshot_ts),
                "forecast_peak_delta_hours_local": peak_delta,
                "forecast_max_native": finite(current.get("forecast_max_native")),
                "physical_confirmation_base": base,
                "physical_confirmation_strong_partial": strong,
                "current_bracket": str(current.get("bracket") or ""),
                "d1_bracket": str(d1.get("bracket") or "") if d1 else "",
                "current_yes_ask": current_yes["ask"] if current_yes["valid"] else None,
                "current_yes_quote_ts_utc": current_yes["quote_ts"],
                "current_yes_quote_delay_min": current_yes["quote_delay_min"],
                "current_yes_indicative": finite(current.get("market_yes_price")),
                "d1_no_ask": d1_no["ask"] if d1_no["valid"] else None,
                "d1_no_quote_ts_utc": d1_no["quote_ts"],
                "d1_no_quote_delay_min": d1_no["quote_delay_min"],
                "d1_no_indicative": None if d1 is None or finite(d1.get("market_yes_price")) is None else 1.0 - finite(d1.get("market_yes_price")),
            }
        )
    return out


def select_first(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: item["source_detect_ts_utc"] or ""):
        if not row.get(field):
            continue
        key = (row["city"], row["target_date"])
        selected.setdefault(key, row)
    return list(selected.values())


def settlement_map(db_path: Path, start: str, end: str) -> dict[tuple[str, str, str], float]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    rows = conn.execute(
        """
        SELECT event_date, city, bracket, MAX(final_yes)
        FROM fact_signal_candidates
        WHERE event_date BETWEEN ? AND ? AND settlement_status='settled' AND final_yes IS NOT NULL
        GROUP BY event_date, city, bracket
        """,
        (start, end),
    ).fetchall()
    conn.close()
    return {(str(date), str(city), str(bracket)): float(final_yes) for date, city, bracket, final_yes in rows}


def expression_rows(selected: list[dict[str, Any]], settlements: Mapping[tuple[str, str, str], float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in selected:
        for expression, bracket_field, ask_field, invert in (
            ("current_yes", "current_bracket", "current_yes_ask", False),
            ("d1_no", "d1_bracket", "d1_no_ask", True),
        ):
            bracket = str(row.get(bracket_field) or "")
            ask = finite(row.get(ask_field))
            final_yes = settlements.get((row["target_date"], row["city"], bracket))
            if not bracket or ask is None or final_yes is None or not (PRICE_MIN <= ask <= PRICE_MAX):
                continue
            win = 1.0 - final_yes if invert else final_yes
            effective_cost = ask + fee(ask)
            out.append(
                {
                    **row,
                    "expression": expression,
                    "entry_ask": ask,
                    "fee_per_share": fee(ask),
                    "effective_cost_per_share": effective_cost,
                    "win": win,
                    "pnl_per_share": win - effective_cost,
                }
            )
    return out


def roi(rows: list[dict[str, Any]]) -> float | None:
    cost = sum(float(row["effective_cost_per_share"]) for row in rows)
    return None if cost <= 0 else sum(float(row["pnl_per_share"]) for row in rows) / cost


def cluster_ci(rows: list[dict[str, Any]], *, seed: int = 20260714, reps: int = 5000) -> list[float] | None:
    dates = sorted({str(row["target_date"]) for row in rows})
    if len(dates) < 2:
        return None
    by_date = {date: [row for row in rows if str(row["target_date"]) == date] for date in dates}
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(reps):
        sample: list[dict[str, Any]] = []
        for date in rng.choices(dates, k=len(dates)):
            sample.extend(by_date[date])
        value = roi(sample)
        if value is not None:
            values.append(value)
    values.sort()
    if not values:
        return None
    return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]


def summary(rows: list[dict[str, Any]], expression: str) -> dict[str, Any]:
    subset = [row for row in rows if row["expression"] == expression]
    return {
        "expression": expression,
        "rows": len(subset),
        "active_dates": len({row["target_date"] for row in subset}),
        "cities": len({row["city"] for row in subset}),
        "wins": sum(float(row["win"]) for row in subset),
        "win_rate": None if not subset else sum(float(row["win"]) for row in subset) / len(subset),
        "avg_ask": None if not subset else sum(float(row["entry_ask"]) for row in subset) / len(subset),
        "cost": sum(float(row["effective_cost_per_share"]) for row in subset),
        "pnl": sum(float(row["pnl_per_share"]) for row in subset),
        "roi": roi(subset),
        "roi_ci95": cluster_ci(subset),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{100 * value:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-07-07")
    ap.add_argument("--end", default="2026-07-13")
    ap.add_argument("--source-events-dir", default=str(SOURCE_EVENTS_DIR))
    ap.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--sanity-date", default="2026-07-14")
    ap.add_argument("--sanity-city", default="Busan")
    args = ap.parse_args()

    events = load_source_events(Path(args.source_events_dir), args.start, args.end)
    states = derive_event_states(events)
    prebase = [row for row in states if row["prebase_without_forecast_clock"]]
    market_rows = attach_market(states, Path(args.snapshot_dir))
    base_first = select_first(market_rows, "physical_confirmation_base")
    strong_first = select_first(market_rows, "physical_confirmation_strong_partial")
    settlements = settlement_map(Path(args.db), args.start, args.end)
    base_expr = expression_rows(base_first, settlements)
    strong_expr = expression_rows(strong_first, settlements)
    summaries = [summary(strong_expr, "current_yes"), summary(strong_expr, "d1_no")]
    sample_gate_pass = all(item["rows"] >= 30 and item["active_dates"] >= 10 for item in summaries)

    sanity_case: dict[str, Any] | None = None
    if args.sanity_date:
        sanity_events = load_source_events(Path(args.source_events_dir), args.sanity_date, args.sanity_date)
        sanity_market = attach_market(derive_event_states(sanity_events), Path(args.snapshot_dir))
        sanity_rows = select_first(sanity_market, "physical_confirmation_strong_partial")
        sanity_raw = next(
            (row for row in sanity_rows if row["city"] == args.sanity_city and row["target_date"] == args.sanity_date),
            None,
        )
        if sanity_raw:
            sanity_fields = [
                "city", "target_date", "source_report_ts_utc", "source_detect_ts_utc",
                "decision_snapshot_ts_utc", "report_kind", "source_lane", "current_bracket",
                "current_yes_ask", "current_yes_indicative", "d1_bracket", "d1_no_ask",
                "d1_no_indicative", "decline_native", "minutes_since_running_max_at_detect",
                "temp_trend_1h_f", "forecast_peak_delta_hours_local", "physical_support_reasons_partial",
            ]
            sanity_case = {field: sanity_raw.get(field) for field in sanity_fields}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "event_states_prebase.csv", prebase)
    write_csv(OUT_DIR / "base_first_candidates.csv", base_first)
    write_csv(OUT_DIR / "strong_first_candidates.csv", strong_first)
    write_csv(OUT_DIR / "strong_expression_rows.csv", strong_expr)

    payload = {
        "generated_at_utc": iso(datetime.now(timezone.utc)),
        "strategy_head": "heat_death_early_dislocation_v1",
        "decision_clock": "new METAR/SPECI detect -> first later paper snapshot -> first direct quote",
        "window": {"start": args.start, "end": args.end},
        "rule": {
            "local_hour": [WINDOW_START, WINDOW_END],
            "decline_native_gte": 0.5,
            "minutes_since_running_max_gte": 60,
            "warming_state": ["flat", "cooling"],
            "forecast_peak_passed_hours_gte": 0.25,
            "strong_partial_support_count_gte": 2,
            "entry_price_band": [PRICE_MIN, PRICE_MAX],
            "quote_delay_minutes_lte": MAX_QUOTE_DELAY_MIN,
        },
        "funnel": {
            "unique_source_reports": len(events),
            "event_states": len(states),
            "prebase_without_forecast_clock": len(prebase),
            "market_aligned_prebase": len(market_rows),
            "first_base_city_days": len(base_first),
            "first_strong_partial_city_days": len(strong_first),
            "strong_executable_expression_rows": len(strong_expr),
        },
        "quote_coverage": {
            "strong_city_days": len(strong_first),
            "current_yes_direct_ask": sum(finite(row.get("current_yes_ask")) is not None for row in strong_first),
            "d1_no_direct_ask": sum(finite(row.get("d1_no_ask")) is not None for row in strong_first),
            "current_yes_in_entry_band": sum(
                finite(row.get("current_yes_ask")) is not None
                and PRICE_MIN <= float(row["current_yes_ask"]) <= PRICE_MAX
                for row in strong_first
            ),
            "d1_no_in_entry_band": sum(
                finite(row.get("d1_no_ask")) is not None
                and PRICE_MIN <= float(row["d1_no_ask"]) <= PRICE_MAX
                for row in strong_first
            ),
        },
        "feature_coverage": {
            "available_pit": ["METAR/SPECI precipitation", "cloud layers", "wind direction/speed", "temperature path", "forecast peak clock", "direct quote"],
            "missing_in_archive": ["forecast remaining-3h precipitation/cloud/wind", "coordinate-backed solar geometry"],
            "strong_name": "strong_partial because two newly added feature families are unavailable historically",
        },
        "summary": summaries,
        "busan_current_day_sanity_not_in_settled_roi": sanity_case,
        "sample_gate": {
            "required": {"settled_rows": 30, "active_dates": 10},
            "pass": sample_gate_pass,
        },
        "three_gates": {
            "significance": "FAIL_LOW_SAMPLE" if not sample_gate_pass else "PASS",
            "baseline": "NA_short_event_archive",
            "forward": "FAIL_THIN",
            "conclusion": "inconclusive_zero_notional_only",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_lines = []
    for item in summaries:
        ci = item["roi_ci95"]
        ci_text = "NA" if ci is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
        summary_lines.append(
            f"| {item['expression']} | {item['rows']} | {item['active_dates']} | {item['cities']} | {pct(item['win_rate'])} | {item['avg_ask']:.3f} | {pct(item['roi'])} | {ci_text} |"
        )
    sanity_line = (
        "未找到指定 sanity case。"
        if sanity_case is None
        else (
            f"{sanity_case['city']} {sanity_case['target_date']} 在 {sanity_case['decision_snapshot_ts_utc']} "
            f"首次 strong-partial：{sanity_case['current_bracket']} YES ask={sanity_case['current_yes_ask']}，"
            f"{sanity_case['d1_bracket']} NO ask={sanity_case['d1_no_ask']}。该日未纳入上面的已结算 ROI。"
        )
    )
    OUT_MD.write_text(
        "\n".join(
            [
                "# Heat-Death Early Event Replay v1",
                "",
                "Status: current-reference",
                "Verdict: `inconclusive_zero_notional_only`",
                "",
                "## 结论",
                "",
                "这是一版真正按 `source event detect -> 首个后续 snapshot/quote` 对齐的早期错价重放，和 hourly-last 晚期 carry 分开。",
                f"历史事件档案只覆盖 {args.start}..{args.end} 的已结算日，因此无论点估如何都达不到 10 active dates / 30 settled rows 的确认门槛。",
                "",
                "## Funnel",
                "",
                f"- unique source reports: {len(events)}",
                f"- prebase without forecast clock: {len(prebase)}",
                f"- market-aligned prebase: {len(market_rows)}",
                f"- first base city-days: {len(base_first)}",
                f"- first strong-partial city-days: {len(strong_first)}",
                f"- executable settled expression rows: {len(strong_expr)}",
                f"- direct quote coverage: current YES {sum(finite(row.get('current_yes_ask')) is not None for row in strong_first)}/{len(strong_first)}; d1 NO {sum(finite(row.get('d1_no_ask')) is not None for row in strong_first)}/{len(strong_first)}",
                "",
                "## Early expression result",
                "",
                "| Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
                *summary_lines,
                "",
                "## Busan current-day sanity check",
                "",
                sanity_line,
                "",
                "## Feature coverage boundary",
                "",
                "可 PIT 重建：METAR/SPECI 雨、云层、风向/风速、温度路径、forecast peak clock、首个后续直接盘口。历史仍缺 remaining-3h forecast weather 与带坐标 solar geometry，因此这里叫 `strong_partial`，不能假装是完整 weather_state_v2 回测。",
                "",
                "## Three gates",
                "",
                "```text",
                "significance=FAIL_LOW_SAMPLE",
                "baseline=NA_short_event_archive",
                "forward=FAIL_THIN",
                "conclusion=inconclusive_zero_notional_only",
                "```",
                "",
                "完整逐事件行见 `generated/heat_death_early_event_replay_v1/`。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
