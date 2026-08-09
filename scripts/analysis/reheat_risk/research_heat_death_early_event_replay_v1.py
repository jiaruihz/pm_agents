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
import gzip
import json
import math
import random
import re
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
from weather_data_feed.production_paths import (  # noqa: E402
    historical_strategy_snapshots,
    historical_targeted_root,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_feature_layer.market import parse_bracket, settlement_interval  # noqa: E402


RUNTIME_ROOT = load_production_spec().data_feed_runtime_root
SOURCE_EVENTS_DIR = load_production_spec().source_events_root()
SNAPSHOT_DIR = historical_strategy_snapshots()
ORDERBOOK_DIR = historical_targeted_root() / "orderbook_snapshots"
DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/heat_death_early_event_replay_v1"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-heat-death-early-event-replay-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-14-heat-death-early-event-replay-v1.md"
PRICE_HISTORY_PROXY_CACHE = OUT_DIR / "clob_price_history_proxy.csv"
CLOB_PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"

FEE_RATE = 0.05
WINDOW_START = 13.0
WINDOW_END = 17.0
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
                    "in_research_window": WINDOW_START <= hour <= WINDOW_END,
                    "decline_gate": decline >= 0.5,
                    "mature_high_gate": mins_since_max is not None and mins_since_max >= 60.0,
                    "path_not_warming_gate": warm in {"flat", "cooling"},
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


def load_book_quotes(
    orderbook_dir: Path,
    start: str,
    end: str,
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    """Index every archived direct ask; archive coverage is not a signal gate."""

    out: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(orderbook_dir.glob("20*/orderbook_snapshot_*.jsonl.gz")):
        try:
            handle = gzip.open(path, "rt", encoding="utf-8")
            with handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    target_date = str(row.get("event_date") or row.get("market_local_date") or "")
                    if not (start <= target_date <= end) or row.get("status") != "ok":
                        continue
                    city = str(row.get("city") or "")
                    bracket = str(row.get("bracket") or "")
                    outcome = str(row.get("outcome") or "").lower()
                    fetched = parse_ts(row.get("fetched_at_utc") or row.get("snapshot_ts_utc"))
                    best_ask = finite((row.get("summary") or {}).get("best_ask"))
                    if not city or not bracket or outcome not in {"yes", "no"} or fetched is None or best_ask is None:
                        continue
                    out[(city, target_date, bracket, outcome)].append(
                        {
                            "ask": best_ask,
                            "fetched_dt": fetched,
                            "quote_ts": iso(fetched),
                            "ask_size": finite((row.get("summary") or {}).get("ask_size")),
                            "path": str(path),
                        }
                    )
        except (OSError, EOFError, json.JSONDecodeError):
            continue
    for rows in out.values():
        rows.sort(key=lambda row: row["fetched_dt"])
    return out


def first_archived_book_after(
    books: Mapping[tuple[str, str, str, str], list[dict[str, Any]]],
    *,
    city: str,
    target_date: str,
    bracket: str,
    outcome: str,
    detect: datetime,
) -> dict[str, Any]:
    for row in books.get((city, target_date, bracket, outcome), []):
        delay = (row["fetched_dt"] - detect).total_seconds() / 60.0
        if 0 <= delay <= MAX_QUOTE_DELAY_MIN:
            return {**row, "quote_delay_min": delay, "valid": True, "source": "orderbook_archive_first_after_signal"}
        if delay > MAX_QUOTE_DELAY_MIN:
            break
    return {"ask": None, "quote_ts": None, "quote_delay_min": None, "valid": False, "source": "missing_within_30m"}


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


def attach_market(
    states: list[dict[str, Any]],
    snapshot_dir: Path,
    books: Mapping[tuple[str, str, str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
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
        # The actionable signal does not exist until this paper snapshot has
        # supplied the forecast-peak clock, so quotes before snapshot_ts are
        # not eligible even if the source report was already visible.
        snapshot_current_yes = quote(current, "yes", snapshot_ts, snapshot_ts)
        snapshot_d1_no = quote(d1, "no", snapshot_ts, snapshot_ts)
        current_yes = first_archived_book_after(
            books,
            city=state["city"],
            target_date=state["target_date"],
            bracket=str(current.get("bracket") or ""),
            outcome="yes",
            detect=snapshot_ts,
        )
        d1_no = first_archived_book_after(
            books,
            city=state["city"],
            target_date=state["target_date"],
            bracket=str(d1.get("bracket") or "") if d1 else "",
            outcome="no",
            detect=snapshot_ts,
        )
        if not current_yes["valid"] and snapshot_current_yes["valid"]:
            current_yes = {**snapshot_current_yes, "source": "paper_snapshot_direct"}
        if not d1_no["valid"] and snapshot_d1_no["valid"]:
            d1_no = {**snapshot_d1_no, "source": "paper_snapshot_direct"}
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
                "current_yes_quote_source": current_yes.get("source"),
                "current_yes_snapshot_raw_ask": snapshot_current_yes["ask"],
                "current_yes_snapshot_book_status": current.get("yes_book_status"),
                "current_yes_token_id": str(current.get("yes_token_id") or ""),
                "current_yes_indicative": finite(current.get("market_yes_price")),
                "d1_no_ask": d1_no["ask"] if d1_no["valid"] else None,
                "d1_no_quote_ts_utc": d1_no["quote_ts"],
                "d1_no_quote_delay_min": d1_no["quote_delay_min"],
                "d1_no_quote_source": d1_no.get("source"),
                "d1_no_snapshot_raw_ask": snapshot_d1_no["ask"],
                "d1_no_snapshot_book_status": None if d1 is None else d1.get("no_book_status"),
                "d1_no_token_id": "" if d1 is None else str(d1.get("no_token_id") or ""),
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
            if not bracket or ask is None or final_yes is None or not (0 < ask < 1):
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
                    "price_source": "direct_executable_ask",
                }
            )
    return out


def indicative_expression_rows(
    selected: list[dict[str, Any]],
    settlements: Mapping[tuple[str, str, str], float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in selected:
        for expression, bracket_field, price_field, invert in (
            ("current_yes", "current_bracket", "current_yes_indicative", False),
            ("d1_no", "d1_bracket", "d1_no_indicative", True),
        ):
            bracket = str(row.get(bracket_field) or "")
            price = finite(row.get(price_field))
            final_yes = settlements.get((row["target_date"], row["city"], bracket))
            if not bracket or price is None or final_yes is None or not (0 < price < 1):
                continue
            win = 1.0 - final_yes if invert else final_yes
            effective_cost = price + fee(price)
            out.append(
                {
                    **row,
                    "expression": expression,
                    "entry_ask": price,
                    "fee_per_share": fee(price),
                    "effective_cost_per_share": effective_cost,
                    "win": win,
                    "pnl_per_share": win - effective_cost,
                    "price_source": "indicative_market_price_not_executable",
                }
            )
    return out


def _price_history_request(row: Mapping[str, Any], expression: str) -> dict[str, Any]:
    token_field = "current_yes_token_id" if expression == "current_yes" else "d1_no_token_id"
    token_id = str(row.get(token_field) or "")
    decision = parse_ts(row.get("decision_snapshot_ts_utc"))
    base = {
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "expression": expression,
        "token_id": token_id,
        "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
        "history_point_ts_utc": None,
        "history_price": None,
        "history_delay_min": None,
        "status": "missing_token_or_decision",
    }
    if not token_id or decision is None:
        return base
    params = urlencode(
        {
            "market": token_id,
            "startTs": int((decision - timedelta(minutes=2)).timestamp()),
            "endTs": int((decision + timedelta(minutes=31)).timestamp()),
            "fidelity": 1,
        }
    )
    try:
        request = Request(
            f"{CLOB_PRICES_HISTORY_URL}?{params}",
            headers={"User-Agent": "pm-agents-weather-research/1.0"},
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except Exception as exc:  # network/API errors are retained in the audit artifact
        return {**base, "status": f"error:{type(exc).__name__}"}
    points: list[tuple[datetime, float]] = []
    for point in payload.get("history") or []:
        try:
            point_dt = datetime.fromtimestamp(int(point["t"]), tz=timezone.utc)
            price = float(point["p"])
        except (KeyError, TypeError, ValueError, OSError):
            continue
        delay = (point_dt - decision).total_seconds() / 60.0
        if 0 <= delay <= MAX_QUOTE_DELAY_MIN and 0 < price < 1:
            points.append((point_dt, price))
    if not points:
        return {**base, "status": "no_point_within_30m"}
    point_dt, price = min(points, key=lambda item: item[0])
    return {
        **base,
        "history_point_ts_utc": iso(point_dt),
        "history_price": price,
        "history_delay_min": (point_dt - decision).total_seconds() / 60.0,
        "status": "ok",
    }


def proxy_row_key(row: Mapping[str, Any], expression: str | None = None) -> tuple[str, str, str, str]:
    return (
        str(row.get("city")),
        str(row.get("target_date")),
        expression if expression is not None else str(row.get("expression")),
        str(row.get("decision_snapshot_ts_utc") or ""),
    )


def load_or_fetch_price_history_proxy(
    cohorts: list[list[dict[str, Any]]],
    cache_path: Path,
    *,
    refresh: bool,
) -> list[dict[str, Any]]:
    # Each cohort's first signal per city-day can occur at a different decision
    # snapshot, so requests are keyed by decision ts, not just city-day.
    requests: dict[tuple[str, str, str, str], tuple[dict[str, Any], str]] = {}
    for cohort in cohorts:
        for row in cohort:
            for expression in ("current_yes", "d1_no"):
                requests.setdefault(proxy_row_key(row, expression), (row, expression))
    cached: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if cache_path.exists() and not refresh:
        with cache_path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                cached[proxy_row_key(row)] = row
    missing = [requests[key] for key in requests if key not in cached]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_price_history_request, row, expression) for row, expression in missing]
            for future in as_completed(futures):
                fetched = future.result()
                cached[proxy_row_key(fetched)] = fetched
    out = [row for key, row in cached.items() if key in requests]
    out.sort(key=lambda row: (str(row.get("target_date")), str(row.get("city")), str(row.get("expression"))))
    write_csv(cache_path, out)
    return out


def proxy_expression_rows(
    selected: list[dict[str, Any]],
    settlements: Mapping[tuple[str, str, str], float],
    proxy_rows: list[dict[str, Any]],
    *,
    slippage_add: float = 0.0,
) -> list[dict[str, Any]]:
    proxy = {
        proxy_row_key(row): row
        for row in proxy_rows
        if str(row.get("status")) == "ok"
    }
    out: list[dict[str, Any]] = []
    for row in selected:
        for expression, bracket_field, invert in (
            ("current_yes", "current_bracket", False),
            ("d1_no", "d1_bracket", True),
        ):
            bracket = str(row.get(bracket_field) or "")
            proxy_row = proxy.get(proxy_row_key(row, expression))
            raw_price = None if proxy_row is None else finite(proxy_row.get("history_price"))
            final_yes = settlements.get((row["target_date"], row["city"], bracket))
            if not bracket or raw_price is None or final_yes is None or not (0 < raw_price < 1):
                continue
            price = min(0.999, raw_price + slippage_add)
            win = 1.0 - final_yes if invert else final_yes
            effective_cost = price + fee(price)
            out.append(
                {
                    **row,
                    "expression": expression,
                    "entry_ask": price,
                    "raw_history_price": raw_price,
                    "history_point_ts_utc": proxy_row.get("history_point_ts_utc"),
                    "history_delay_min": finite(proxy_row.get("history_delay_min")),
                    "assumed_slippage_add": slippage_add,
                    "fee_per_share": fee(price),
                    "effective_cost_per_share": effective_cost,
                    "win": win,
                    "pnl_per_share": win - effective_cost,
                    "price_source": "clob_prices_history_proxy_not_executable",
                }
            )
    return out


def proxy_direct_overlap(selected: list[dict[str, Any]], proxy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proxy = {
        proxy_row_key(row): finite(row.get("history_price"))
        for row in proxy_rows
        if str(row.get("status")) == "ok"
    }
    out: list[dict[str, Any]] = []
    for expression, direct_field in (("current_yes", "current_yes_ask"), ("d1_no", "d1_no_ask")):
        deltas: list[float] = []
        for row in selected:
            direct = finite(row.get(direct_field))
            history = proxy.get(proxy_row_key(row, expression))
            if direct is not None and history is not None:
                deltas.append(direct - history)
        ordered = sorted(deltas)
        out.append(
            {
                "expression": expression,
                "overlap_rows": len(ordered),
                "mean_direct_ask_minus_history": None if not ordered else sum(ordered) / len(ordered),
                "median_direct_ask_minus_history": None if not ordered else ordered[len(ordered) // 2],
                "p90_direct_ask_minus_history": None if not ordered else ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)],
            }
        )
    return out


def same_snapshot_ask_premium(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for expression, ask_field, price_field in (
        ("current_yes", "current_yes_snapshot_raw_ask", "current_yes_indicative"),
        ("d1_no", "d1_no_snapshot_raw_ask", "d1_no_indicative"),
    ):
        deltas = sorted(
            ask - price
            for row in selected
            if (ask := finite(row.get(ask_field))) is not None
            and (price := finite(row.get(price_field))) is not None
        )
        out.append(
            {
                "expression": expression,
                "overlap_rows": len(deltas),
                "mean_ask_minus_indicative": None if not deltas else sum(deltas) / len(deltas),
                "median_ask_minus_indicative": None if not deltas else deltas[len(deltas) // 2],
                "p90_ask_minus_indicative": None if not deltas else deltas[min(len(deltas) - 1, math.ceil(0.9 * len(deltas)) - 1)],
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


def expression_summaries(rows: list[dict[str, Any]], *, cohort: str, price_layer: str) -> list[dict[str, Any]]:
    return [
        {"cohort": cohort, "price_layer": price_layer, **summary(rows, expression)}
        for expression in ("current_yes", "d1_no")
    ]


def paired_expression_economics(
    rows: list[dict[str, Any]],
    *,
    cohort: str,
    price_layer: str,
    seed: int = 20260715,
    reps: int = 5000,
) -> dict[str, Any]:
    """Compare both expressions only where the same signal has both prices."""

    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[
            (
                str(row.get("city")),
                str(row.get("target_date")),
                str(row.get("decision_snapshot_ts_utc") or ""),
            )
        ][str(row.get("expression"))] = row
    pairs: list[dict[str, Any]] = []
    for (city, target_date, decision_ts), legs in grouped.items():
        current = legs.get("current_yes")
        d1 = legs.get("d1_no")
        if current is None or d1 is None:
            continue
        current_win = float(current["win"])
        d1_win = float(d1["win"])
        if current_win == 1.0 and d1_win == 1.0:
            bucket = "both_win_stop_current"
        elif current_win == 0.0 and d1_win == 0.0:
            bucket = "both_lose_exact_d1"
        elif current_win == 0.0 and d1_win == 1.0:
            bucket = "d1_only_win_d2_plus"
        else:
            bucket = "current_only_win_inconsistent"
        pairs.append(
            {
                "city": city,
                "target_date": target_date,
                "decision_snapshot_ts_utc": decision_ts,
                "current_yes_ask": float(current["entry_ask"]),
                "d1_no_ask": float(d1["entry_ask"]),
                "current_yes_cost": float(current["effective_cost_per_share"]),
                "d1_no_cost": float(d1["effective_cost_per_share"]),
                "current_yes_pnl": float(current["pnl_per_share"]),
                "d1_no_pnl": float(d1["pnl_per_share"]),
                "current_yes_win": current_win,
                "d1_no_win": d1_win,
                "outcome_bucket": bucket,
            }
        )

    def paired_delta(sample: list[dict[str, Any]]) -> float | None:
        current_cost = sum(row["current_yes_cost"] for row in sample)
        d1_cost = sum(row["d1_no_cost"] for row in sample)
        if current_cost <= 0 or d1_cost <= 0:
            return None
        current_roi = sum(row["current_yes_pnl"] for row in sample) / current_cost
        d1_roi = sum(row["d1_no_pnl"] for row in sample) / d1_cost
        return current_roi - d1_roi

    if not pairs:
        return {"cohort": cohort, "price_layer": price_layer, "rows": 0, "active_dates": 0}
    current_cost = sum(row["current_yes_cost"] for row in pairs)
    d1_cost = sum(row["d1_no_cost"] for row in pairs)
    current_roi = sum(row["current_yes_pnl"] for row in pairs) / current_cost
    d1_roi = sum(row["d1_no_pnl"] for row in pairs) / d1_cost
    dates = sorted({row["target_date"] for row in pairs})
    ci: list[float] | None = None
    if len(dates) >= 2:
        by_date = {date: [row for row in pairs if row["target_date"] == date] for date in dates}
        rng = random.Random(seed)
        values: list[float] = []
        for _ in range(reps):
            sample = [row for date in rng.choices(dates, k=len(dates)) for row in by_date[date]]
            value = paired_delta(sample)
            if value is not None:
                values.append(value)
        values.sort()
        if values:
            ci = [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]
    buckets: dict[str, int] = defaultdict(int)
    for row in pairs:
        buckets[row["outcome_bucket"]] += 1
    return {
        "cohort": cohort,
        "price_layer": price_layer,
        "rows": len(pairs),
        "active_dates": len(dates),
        "cities": len({row["city"] for row in pairs}),
        "current_yes_win_rate": sum(row["current_yes_win"] for row in pairs) / len(pairs),
        "d1_no_win_rate": sum(row["d1_no_win"] for row in pairs) / len(pairs),
        "d1_extra_win_rate": sum(row["d1_no_win"] - row["current_yes_win"] for row in pairs) / len(pairs),
        "avg_d1_minus_current_ask": sum(row["d1_no_ask"] - row["current_yes_ask"] for row in pairs) / len(pairs),
        "current_yes_roi": current_roi,
        "d1_no_roi": d1_roi,
        "current_yes_minus_d1_no_roi": current_roi - d1_roi,
        "roi_delta_ci95": ci,
        "outcome_counts": {
            "both_win_stop_current": buckets["both_win_stop_current"],
            "both_lose_exact_d1": buckets["both_lose_exact_d1"],
            "d1_only_win_d2_plus": buckets["d1_only_win_d2_plus"],
            "current_only_win_inconsistent": buckets["current_only_win_inconsistent"],
        },
    }


def support_bucket(value: Any) -> str:
    count = int(value or 0)
    return "3+" if count >= 3 else str(count)


def support_slice_summaries(rows: list[dict[str, Any]], *, price_layer: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bucket in ("0", "1", "2", "3+"):
        subset = [row for row in rows if support_bucket(row.get("physical_support_count_partial")) == bucket]
        for expression in ("current_yes", "d1_no"):
            out.append(
                {
                    "support_bucket": bucket,
                    "price_layer": price_layer,
                    **summary(subset, expression),
                }
            )
    return out


def quote_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "city_days": len(rows),
        "current_yes_direct_ask": sum(finite(row.get("current_yes_ask")) is not None for row in rows),
        "d1_no_direct_ask": sum(finite(row.get("d1_no_ask")) is not None for row in rows),
    }


def evidence_coverage_audit(
    selected: list[dict[str, Any]],
    settlements: Mapping[tuple[str, str, str], float],
    *,
    cohort: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for expression, bracket_field, direct_field, indicative_field, raw_field, status_field in (
        (
            "current_yes", "current_bracket", "current_yes_ask", "current_yes_indicative",
            "current_yes_snapshot_raw_ask", "current_yes_snapshot_book_status",
        ),
        (
            "d1_no", "d1_bracket", "d1_no_ask", "d1_no_indicative",
            "d1_no_snapshot_raw_ask", "d1_no_snapshot_book_status",
        ),
    ):
        status_counts: dict[str, int] = defaultdict(int)
        source_counts: dict[str, int] = defaultdict(int)
        direct_dates: set[str] = set()
        settled_dates: set[str] = set()
        counts: dict[str, int] = defaultdict(int)
        for row in selected:
            counts["signal_city_days"] += 1
            bracket = str(row.get(bracket_field) or "")
            indicative = finite(row.get(indicative_field))
            raw_ask = finite(row.get(raw_field))
            direct = finite(row.get(direct_field))
            settled = bool(bracket) and (row["target_date"], row["city"], bracket) in settlements
            status = str(row.get(status_field) or "missing_status")
            status_counts[status] += 1
            if bracket:
                counts["leg_present"] += 1
            if indicative is not None:
                counts["indicative_price_present"] += 1
            if raw_ask is not None:
                counts["first_snapshot_direct_ask"] += 1
            if direct is not None:
                counts["direct_ask_within_30m"] += 1
                direct_dates.add(str(row["target_date"]))
                source_counts[str(row.get(f"{expression}_quote_source") or "unknown")] += 1
            elif indicative is not None:
                counts["indicative_but_no_direct_ask"] += 1
            if settled:
                counts["settlement_present"] += 1
                settled_dates.add(str(row["target_date"]))
            if direct is not None and settled:
                counts["direct_and_settled"] += 1
            if indicative is not None and settled:
                counts["indicative_and_settled"] += 1
        out.append(
            {
                "cohort": cohort,
                "expression": expression,
                **counts,
                "direct_active_dates": len(direct_dates),
                "settled_active_dates": len(settled_dates),
                "first_snapshot_book_status_counts": dict(sorted(status_counts.items())),
                "direct_quote_source_counts": dict(sorted(source_counts.items())),
            }
        )
    return out


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


def find_summary(items: list[dict[str, Any]], cohort: str, expression: str) -> dict[str, Any]:
    return next(item for item in items if item["cohort"] == cohort and item["expression"] == expression)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-07-07")
    ap.add_argument("--end", default="2026-07-13")
    ap.add_argument("--source-events-dir", default=str(SOURCE_EVENTS_DIR))
    ap.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR))
    ap.add_argument("--orderbook-dir", default=str(ORDERBOOK_DIR))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--sanity-date", default="2026-07-14")
    ap.add_argument("--sanity-city", default="Busan")
    ap.add_argument("--price-history-proxy-cache", default=str(PRICE_HISTORY_PROXY_CACHE))
    ap.add_argument("--refresh-price-history-proxy", action="store_true")
    args = ap.parse_args()

    events = load_source_events(Path(args.source_events_dir), args.start, args.end)
    states = derive_event_states(events)
    prebase = [row for row in states if row["prebase_without_forecast_clock"]]
    books = load_book_quotes(Path(args.orderbook_dir), args.start, args.end)
    market_rows = attach_market(states, Path(args.snapshot_dir), books)
    base_first = select_first(market_rows, "physical_confirmation_base")
    strong_first = select_first(market_rows, "physical_confirmation_strong_partial")
    # The Busan anchor trade defined this strategy's target morphology, so its
    # city-day is in-sample by construction and never enters evidence layers.
    anchor_key = (args.sanity_city, args.sanity_date)
    anchor_in_window = bool(args.sanity_date) and args.start <= args.sanity_date <= args.end
    base_first_signal_count = len(base_first)
    strong_first_signal_count = len(strong_first)
    if anchor_in_window:
        base_first = [row for row in base_first if (row["city"], row["target_date"]) != anchor_key]
        strong_first = [row for row in strong_first if (row["city"], row["target_date"]) != anchor_key]
    settlements = settlement_map(Path(args.db), args.start, args.end)
    settled_target_dates = sorted({key[0] for key in settlements})
    base_expr = expression_rows(base_first, settlements)
    strong_expr = expression_rows(strong_first, settlements)
    base_indicative_expr = indicative_expression_rows(base_first, settlements)
    strong_indicative_expr = indicative_expression_rows(strong_first, settlements)
    price_history_proxy = load_or_fetch_price_history_proxy(
        [base_first, strong_first],
        Path(args.price_history_proxy_cache),
        refresh=args.refresh_price_history_proxy,
    )
    proxy_layers: dict[float, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for slippage_add in (0.0, 0.01, 0.02, 0.03):
        proxy_layers[slippage_add] = (
            proxy_expression_rows(base_first, settlements, price_history_proxy, slippage_add=slippage_add),
            proxy_expression_rows(strong_first, settlements, price_history_proxy, slippage_add=slippage_add),
        )
    base_proxy_expr, strong_proxy_expr = proxy_layers[0.0]
    executable_summaries = [
        *expression_summaries(base_expr, cohort="base", price_layer="direct_executable_ask"),
        *expression_summaries(strong_expr, cohort="strong_partial", price_layer="direct_executable_ask"),
    ]
    indicative_summaries = [
        *expression_summaries(base_indicative_expr, cohort="base", price_layer="indicative_not_executable"),
        *expression_summaries(strong_indicative_expr, cohort="strong_partial", price_layer="indicative_not_executable"),
    ]
    proxy_summaries = [
        *expression_summaries(base_proxy_expr, cohort="base", price_layer="clob_price_history_proxy"),
        *expression_summaries(strong_proxy_expr, cohort="strong_partial", price_layer="clob_price_history_proxy"),
    ]
    paired_economics = [
        paired_expression_economics(base_expr, cohort="base", price_layer="direct_executable_ask"),
        paired_expression_economics(strong_expr, cohort="strong_partial", price_layer="direct_executable_ask"),
        paired_expression_economics(base_indicative_expr, cohort="base", price_layer="indicative_not_executable"),
        paired_expression_economics(strong_indicative_expr, cohort="strong_partial", price_layer="indicative_not_executable"),
        paired_expression_economics(base_proxy_expr, cohort="base", price_layer="clob_price_history_proxy"),
        paired_expression_economics(strong_proxy_expr, cohort="strong_partial", price_layer="clob_price_history_proxy"),
    ]
    proxy_slippage_sensitivity: list[dict[str, Any]] = []
    for slippage_add, (base_rows, strong_rows) in proxy_layers.items():
        for item in (
            *expression_summaries(base_rows, cohort="base", price_layer="clob_price_history_proxy"),
            *expression_summaries(strong_rows, cohort="strong_partial", price_layer="clob_price_history_proxy"),
        ):
            proxy_slippage_sensitivity.append({"slippage_add": slippage_add, **item})
    base_anchor_band_proxy_rows = [row for row in base_proxy_expr if 0.80 <= float(row["raw_history_price"]) <= 0.90]
    strong_anchor_band_proxy_rows = [row for row in strong_proxy_expr if 0.80 <= float(row["raw_history_price"]) <= 0.90]
    anchor_band_summaries = [
        *expression_summaries(
            base_anchor_band_proxy_rows,
            cohort="base_anchor_price_band_0.80_0.90",
            price_layer="clob_price_history_proxy",
        ),
        *expression_summaries(
            strong_anchor_band_proxy_rows,
            cohort="strong_anchor_price_band_0.80_0.90",
            price_layer="clob_price_history_proxy",
        ),
    ]
    proxy_overlap = proxy_direct_overlap(base_first, price_history_proxy)
    snapshot_premium = same_snapshot_ask_premium(base_first)
    support_slices = support_slice_summaries(base_indicative_expr, price_layer="indicative_not_executable")
    evidence_audit = [
        *evidence_coverage_audit(base_first, settlements, cohort="base"),
        *evidence_coverage_audit(strong_first, settlements, cohort="strong_partial"),
    ]
    strong_executable_summaries = [row for row in executable_summaries if row["cohort"] == "strong_partial"]
    sample_gate_pass = all(
        item["rows"] >= 30 and item["active_dates"] >= 10
        for item in strong_executable_summaries
    )

    in_window = [row for row in states if row["in_research_window"]]
    after_decline = [row for row in in_window if row["decline_gate"]]
    after_mature_high = [row for row in after_decline if row["mature_high_gate"]]
    after_path = [row for row in after_mature_high if row["path_not_warming_gate"]]

    sanity_case: dict[str, Any] | None = None
    if args.sanity_date:
        sanity_events = load_source_events(Path(args.source_events_dir), args.sanity_date, args.sanity_date)
        sanity_books = load_book_quotes(Path(args.orderbook_dir), args.sanity_date, args.sanity_date)
        sanity_market = attach_market(derive_event_states(sanity_events), Path(args.snapshot_dir), sanity_books)
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

    busan_anchor_alignment: dict[str, Any] | None = None
    if sanity_case is not None:
        busan_anchor_alignment = {
            "role": "user-reported executed anchor case that defines the intended early-dislocation morphology",
            "selector_match": True,
            "first_strong_ts_utc": sanity_case.get("decision_snapshot_ts_utc"),
            "current_yes_ask": sanity_case.get("current_yes_ask"),
            "d1_no_ask": sanity_case.get("d1_no_ask"),
            "included_in_settled_roi_window": False,
            "why_not_in_settled_roi": (
                "excluded as defining anchor: the morphology was specified from this executed trade, so it is in-sample by construction"
                if anchor_in_window
                else f"target_date {args.sanity_date} is outside the settled replay window ending {args.end}"
            ),
            "actual_backtest_gap": "historical first-signal direct executable quote coverage is sparse and skewed toward already-repriced late books",
            "not_a_backtest_gap": [
                "the forward runner did not exist yet",
                "the personal fill is not in strategy canonical lineage",
            ],
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "event_states_prebase.csv", prebase)
    write_csv(OUT_DIR / "base_first_candidates.csv", base_first)
    write_csv(OUT_DIR / "strong_first_candidates.csv", strong_first)
    write_csv(OUT_DIR / "base_expression_rows.csv", base_expr)
    write_csv(OUT_DIR / "strong_expression_rows.csv", strong_expr)
    write_csv(OUT_DIR / "base_indicative_expression_rows.csv", base_indicative_expr)
    write_csv(OUT_DIR / "strong_indicative_expression_rows.csv", strong_indicative_expr)
    write_csv(OUT_DIR / "base_price_history_proxy_expression_rows.csv", base_proxy_expr)
    write_csv(OUT_DIR / "strong_price_history_proxy_expression_rows.csv", strong_proxy_expr)
    write_csv(OUT_DIR / "price_history_proxy_slippage_sensitivity.csv", proxy_slippage_sensitivity)
    write_csv(OUT_DIR / "price_history_proxy_direct_overlap.csv", proxy_overlap)
    write_csv(OUT_DIR / "same_snapshot_ask_premium.csv", snapshot_premium)
    write_csv(OUT_DIR / "support_slice_summary.csv", support_slices)
    write_csv(OUT_DIR / "evidence_coverage_audit.csv", evidence_audit)
    write_csv(OUT_DIR / "paired_expression_economics.csv", paired_economics)

    payload = {
        "generated_at_utc": iso(datetime.now(timezone.utc)),
        "strategy_head": "heat_death_early_dislocation_v1",
        "decision_clock": "new METAR/SPECI detect -> first later paper snapshot -> first archived direct quote after signal",
        "window": {"start": args.start, "end": args.end},
        "date_coverage": {
            "input_target_dates": sorted({row["target_date"] for row in base_first}),
            "settled_target_dates": settled_target_dates,
            "unsettled_base_signal_city_days": sum(
                not any((row["target_date"], row["city"], str(row.get(field) or "")) in settlements for field in ("current_bracket", "d1_bracket"))
                for row in base_first
            ),
            "unsettled_strong_signal_city_days": sum(
                not any((row["target_date"], row["city"], str(row.get(field) or "")) in settlements for field in ("current_bracket", "d1_bracket"))
                for row in strong_first
            ),
        },
        "rule": {
            "local_hour": [WINDOW_START, WINDOW_END],
            "decline_native_gte": 0.5,
            "minutes_since_running_max_gte": 60,
            "warming_state": ["flat", "cooling"],
            "forecast_peak_passed_hours_gte": 0.25,
            "strong_partial_support_count_gte": 2,
            "quote_delay_minutes_lte": MAX_QUOTE_DELAY_MIN,
            "price_role": "continuous EV input; never an eligibility gate",
        },
        "signal_funnel": {
            "unit_note": "event rows until market alignment; then first signal per city-day",
            "unique_source_reports": len(events),
            "local_hour_13_17_event_rows": len(in_window),
            "plus_decline_gte_0_5_event_rows": len(after_decline),
            "plus_high_age_gte_60m_event_rows": len(after_mature_high),
            "plus_path_not_warming_event_rows": len(after_path),
            "market_aligned_event_rows": len(market_rows),
            "first_base_signal_city_days": base_first_signal_count,
            "first_strong_partial_signal_city_days": strong_first_signal_count,
            "anchor_city_day_excluded_from_evidence": anchor_in_window,
        },
        "evidence_funnel": {
            "unit_note": "quote and settlement availability are evidence coverage, not strategy filters",
            "base_quote_coverage": quote_coverage(base_first),
            "strong_quote_coverage": quote_coverage(strong_first),
            "base_settled_executable_expression_rows": len(base_expr),
            "strong_settled_executable_expression_rows": len(strong_expr),
            "base_settled_indicative_expression_rows": len(base_indicative_expr),
            "strong_settled_indicative_expression_rows": len(strong_indicative_expr),
            "base_settled_price_history_proxy_expression_rows": len(base_proxy_expr),
            "strong_settled_price_history_proxy_expression_rows": len(strong_proxy_expr),
        },
        "evidence_coverage_audit": evidence_audit,
        "historical_collection_diagnosis": {
            "root_cause": "bounded strategy-scoped orderbook enrichment, not missing weather signals",
            "snapshot_interval_seconds_default": 600,
            "orderbook_scope_default": "strategy_live",
            "orderbook_budget_seconds_default": 60,
            "orderbook_workers_default": 1,
            "effect": "most paper records retain indicative market prices but lack a contemporaneous executable ask",
            "recoverable_layer": "CLOB /prices-history minute price is still available for most selected tokens",
            "irrecoverable_layer": "historical best ask, size, depth, spread and guaranteed taker fill where orderbooks were not archived",
        },
        "feature_coverage": {
            "available_pit": ["METAR/SPECI precipitation", "cloud layers", "wind direction/speed", "temperature path", "forecast peak clock", "direct quote"],
            "missing_in_archive": ["forecast remaining-3h precipitation/cloud/wind", "coordinate-backed solar geometry"],
            "strong_name": "strong_partial because two newly added feature families are unavailable historically",
        },
        "executable_summary": executable_summaries,
        "indicative_summary": indicative_summaries,
        "price_history_proxy_summary": proxy_summaries,
        "paired_expression_economics": paired_economics,
        "price_history_proxy_slippage_sensitivity": proxy_slippage_sensitivity,
        "price_history_proxy_direct_overlap": proxy_overlap,
        "same_snapshot_ask_premium": snapshot_premium,
        "anchor_price_band_0_80_0_90_diagnostic": anchor_band_summaries,
        "support_slice_summary": support_slices,
        "overfit_audit": {
            "previous_five_rows_was_signal_count": False,
            "previous_five_rows_cause": "first-snapshot direct-ask archive gap plus an unjustified 0.20-0.97 price hard filter",
            "price_hard_filter_removed": True,
            "support_gte_2_status": "diagnostic cohort only; not an approved strategy gate",
            "shadow_runner_scope": "all physical_confirmation_base rows; quote refresh capped operationally; zero notional",
            "tiny_live_probe_scope": "physical_confirmation_strong rows in H2 ask 0.50-0.93; fixed 10 shares; no size-up approval",
        },
        "busan_executed_anchor_not_in_settled_roi": sanity_case,
        "busan_anchor_backtest_alignment": busan_anchor_alignment,
        "sample_gate": {
            "required": {"settled_rows": 30, "active_dates": 10},
            "pass": sample_gate_pass,
        },
        "three_gates": {
            "significance": "FAIL_LOW_SAMPLE" if not sample_gate_pass else "PASS",
            "baseline": "NA_short_event_archive",
            "forward": "FAIL_THIN",
            "conclusion": "inconclusive_tiny_live_probe_only",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def table_lines(items: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in items:
            ci = item["roi_ci95"]
            ci_text = "NA" if ci is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
            avg_ask = "NA" if item["avg_ask"] is None else f"{item['avg_ask']:.3f}"
            lines.append(
                f"| {item['cohort']} | {item['expression']} | {item['rows']} | {item['active_dates']} | {item['cities']} | {pct(item['win_rate'])} | {avg_ask} | {pct(item['roi'])} | {ci_text} |"
            )
        return lines

    executable_lines = table_lines(executable_summaries)
    indicative_lines = table_lines(indicative_summaries)
    proxy_lines = table_lines(proxy_summaries)
    anchor_band_lines = table_lines(anchor_band_summaries)
    paired_lines: list[str] = []
    for item in paired_economics:
        ci = item.get("roi_delta_ci95")
        ci_text = "NA" if ci is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
        paired_lines.append(
            f"| {item['price_layer']} | {item['cohort']} | {item['rows']} | {item['active_dates']} | "
            f"{pct(item.get('current_yes_win_rate'))} | {pct(item.get('d1_no_win_rate'))} | "
            f"{item.get('avg_d1_minus_current_ask', 0):+.3f} | {pct(item.get('current_yes_roi'))} | "
            f"{pct(item.get('d1_no_roi'))} | {pct(item.get('current_yes_minus_d1_no_roi'))} | {ci_text} |"
        )
    slippage_lines: list[str] = []
    for item in proxy_slippage_sensitivity:
        avg_proxy = "NA" if item["avg_ask"] is None else f"{item['avg_ask']:.3f}"
        slippage_lines.append(
            f"| {item['slippage_add']:+.2f} | {item['cohort']} | {item['expression']} | {item['rows']} | {avg_proxy} | {pct(item['roi'])} |"
        )
    overlap_lines = [
        (
            f"| {item['expression']} | {item['overlap_rows']} | "
            f"{item['mean_direct_ask_minus_history']:+.3f} | {item['median_direct_ask_minus_history']:+.3f} | "
            f"{item['p90_direct_ask_minus_history']:+.3f} |"
        )
        for item in proxy_overlap
        if item["overlap_rows"]
    ]
    snapshot_premium_lines = [
        (
            f"| {item['expression']} | {item['overlap_rows']} | "
            f"{item['mean_ask_minus_indicative']:+.3f} | {item['median_ask_minus_indicative']:+.3f} | "
            f"{item['p90_ask_minus_indicative']:+.3f} |"
        )
        for item in snapshot_premium
        if item["overlap_rows"]
    ]
    support_lines: list[str] = []
    for item in support_slices:
        ci = item["roi_ci95"]
        ci_text = "NA" if ci is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
        avg_ask = "NA" if item["avg_ask"] is None else f"{item['avg_ask']:.3f}"
        support_lines.append(
            f"| {item['support_bucket']} | {item['expression']} | {item['rows']} | {item['active_dates']} | {pct(item['win_rate'])} | {avg_ask} | {pct(item['roi'])} | {ci_text} |"
        )
    coverage_lines = [
        (
            f"| {item['cohort']} | {item['expression']} | {item['signal_city_days']} | "
            f"{item.get('indicative_price_present', 0)} | {item.get('first_snapshot_direct_ask', 0)} | "
            f"{item.get('direct_ask_within_30m', 0)} | {item.get('settlement_present', 0)} | "
            f"{item.get('direct_and_settled', 0)} | {item.get('indicative_but_no_direct_ask', 0)} | "
            f"`{json.dumps(item['first_snapshot_book_status_counts'], ensure_ascii=False, sort_keys=True)}` |"
        )
        for item in evidence_audit
    ]
    sanity_line = (
        "未找到指定 anchor case。"
        if sanity_case is None
        else (
            f"{sanity_case['city']} {sanity_case['target_date']} 在 {sanity_case['decision_snapshot_ts_utc']} "
            f"首次 strong-partial：{sanity_case['current_bracket']} YES ask={sanity_case['current_yes_ask']}，"
            f"{sanity_case['d1_bracket']} NO ask={sanity_case['d1_no_ask']}。该日未纳入上面的已结算 ROI。"
        )
    )
    lineage_lines = ["未找到 anchor alignment。"]
    if busan_anchor_alignment is not None:
        lineage_lines = [
            "用户确认这是实际人工成交的 anchor trade；它定义了本研究要寻找的 early-dislocation 形态，不是普通 sanity case。",
            "",
            f"- selector 对齐：replay 在 {busan_anchor_alignment['first_strong_ts_utc']} 首次选中 strong，价格正是 `{sanity_case['current_bracket']} YES={sanity_case['current_yes_ask']} / {sanity_case['d1_bracket']} NO={sanity_case['d1_no_ask']}`。",
            "- 当时 runner 尚未开发、人工成交未进 canonical，都不是回测缺陷；回测本来就是事后重建。",
            "- 真正缺口是历史 first-signal direct ask 覆盖稀疏，能进入 executable 统计的行偏向市场已经 repriced 的晚期高价盘口。",
            "- 分钟 price proxy 已把同分母历史方向补回，但结果没有显示稳定正 edge；由于缺当时 ask/depth，`0.84/0.89` 这类早期错价交易头的 executable ROI 仍未充分验证。",
        ]
    OUT_MD.write_text(
        "\n".join(
            [
                "# Heat-Death Early Event Replay v1",
                "",
                "Status: current-reference",
                "Verdict: `inconclusive_tiny_live_probe_only`",
                "",
                "## 结论",
                "",
                "**上一版“最终只有 5 笔”的说法作废。5 是盘口档案缺口再叠加任意价格带后的可计算行数，不是策略信号数。**",
                "这次审计把 signal funnel 与 quote/settlement evidence funnel 分开，价格只作为连续 EV 输入，不再作为 eligibility hard gate。",
                (
                    f"CLOB 分钟 price history 补回 {find_summary(proxy_summaries, 'base', 'current_yes')['rows']}/"
                    f"{find_summary(indicative_summaries, 'base', 'current_yes')['rows']} 个已结算 leg："
                    f"base current YES fee ROI {pct(find_summary(proxy_summaries, 'base', 'current_yes')['roi'])}，"
                    f"加 2c ask premium 后 {pct(next(i for i in proxy_slippage_sensitivity if i['slippage_add'] == 0.02 and i['cohort'] == 'base' and i['expression'] == 'current_yes')['roi'])}；"
                    f"base d1 NO 原价即 {pct(find_summary(proxy_summaries, 'base', 'd1_no')['roi'])}。"
                    f"support>=2 两边分别 {pct(find_summary(proxy_summaries, 'strong_partial', 'current_yes')['roi'])} / "
                    f"{pct(find_summary(proxy_summaries, 'strong_partial', 'd1_no')['roi'])}，没有显示更强 edge。"
                ),
                (
                    f"Busan-like 0.80-0.90 只是事后诊断切片：base current YES {find_summary(anchor_band_summaries, 'base_anchor_price_band_0.80_0.90', 'current_yes')['rows']} 行 "
                    f"ROI {pct(find_summary(anchor_band_summaries, 'base_anchor_price_band_0.80_0.90', 'current_yes')['roi'])}，"
                    f"d1 NO {find_summary(anchor_band_summaries, 'base_anchor_price_band_0.80_0.90', 'd1_no')['rows']} 行 "
                    f"{pct(find_summary(anchor_band_summaries, 'base_anchor_price_band_0.80_0.90', 'd1_no')['roi'])}；"
                    f"support>=2 各只有 {find_summary(anchor_band_summaries, 'strong_anchor_price_band_0.80_0.90', 'current_yes')['rows']} 行，不能据此定策略阈值。"
                ),
                f"事件输入已覆盖 {args.start}..{args.end}，但 ROI 只使用 {len(settled_target_dates)} 个已结算日（截至 {settled_target_dates[-1] if settled_target_dates else 'NA'}）；"
                "H2 只维持 fixed-10-share tiny-live probe，不具备 size-up 证据。"
                + ("Busan {} anchor city-day 已按预注册原则从全部证据层剔除（定义形态的 in-sample 交易）。".format(args.sanity_date) if anchor_in_window else ""),
                "",
                "## Signal funnel（这里才是策略漏斗）",
                "",
                f"- unique source reports: {len(events)}",
                f"- local 13:00-17:00 event rows: {len(in_window)}",
                f"- + decline >= 0.5: {len(after_decline)}",
                f"- + running high age >= 60m: {len(after_mature_high)}",
                f"- + flat/cooling path: {len(after_path)}",
                f"- market-aligned event rows: {len(market_rows)}",
                f"- first base signal city-days: {base_first_signal_count}",
                f"- first support>=2 diagnostic city-days: {strong_first_signal_count}",
                *(
                    [f"- anchor 剔除：Busan {args.sanity_date} 从证据层移除（定义形态的 in-sample 交易），证据分母为 base {len(base_first)} / strong {len(strong_first)}"]
                    if anchor_in_window
                    else []
                ),
                "",
                "## Evidence coverage（不是策略筛选）",
                "",
                f"- base direct quote coverage: current YES {sum(finite(row.get('current_yes_ask')) is not None for row in base_first)}/{len(base_first)}; d1 NO {sum(finite(row.get('d1_no_ask')) is not None for row in base_first)}/{len(base_first)}",
                f"- support>=2 direct quote coverage: current YES {sum(finite(row.get('current_yes_ask')) is not None for row in strong_first)}/{len(strong_first)}; d1 NO {sum(finite(row.get('d1_no_ask')) is not None for row in strong_first)}/{len(strong_first)}",
                f"- settled executable rows: base {len(base_expr)}; support>=2 {len(strong_expr)}",
                f"- settled indicative rows (not executable): base {len(base_indicative_expr)}; support>=2 {len(strong_indicative_expr)}",
                "",
                "## 为什么 executable coverage 会塌缩",
                "",
                "| Cohort | Expression | Signals | Indicative price | First-snapshot ask | Ask within 30m | Settled | Ask+settled | Indicative but no ask | First-snapshot book status |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
                *coverage_lines,
                "",
                "历史 paper snapshot 不是全量盘口录制：默认每 10 分钟生成一次 snapshot，但 orderbook enrichment 使用 `strategy_live` 紧凑 scope、60 秒总预算和单 worker。天气状态与 indicative market price 大多保留，真实 YES/NO ask 则大量标为 `orderbook_budget_exhausted` 或 `orderbook_scope_skipped`。因此缺的是可执行价格证据，不是物理 signal 或 settlement 全部缺失。",
                "",
                "这批数据仍有意义：indicative+settled 层可检验 selector 的方向、胜率和粗略定价残差；只有 direct ask+settled 层才能声称 executable ROI。前者不能冒充后者。",
                "",
                "## CLOB minute price PIT proxy（同分母补回）",
                "",
                "`/prices-history` 仍可取回 closed market 的分钟价格，因此可以在首次识别后取第一个 PIT price。它恢复了价格路径分母，但不含当时 ask、spread、size/depth；下面是 price proxy ROI，不是 guaranteed fill ROI。",
                "",
                "| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
                *proxy_lines,
                "",
                "### 固定加价敏感性（代理 ask = history price + 1/2/3c）",
                "",
                "| Add-on | Cohort | Expression | Rows | Avg assumed ask | Fee ROI |",
                "|---:|---|---|---:|---:|---:|",
                *slippage_lines,
                "",
                "### 与已留存 direct ask 的重合校验",
                "",
                "| Expression | Overlap | Mean ask-history | Median | P90 |",
                "|---|---:|---:|---:|---:|",
                *overlap_lines,
                "",
                "上表混合了 signal 后到 direct book 出现前的价格移动，不能纯解释为 spread。第一张 snapshot 同时有 ask 和 indicative price 的 16 个 leg 校验如下：",
                "",
                "| Expression | Same-snapshot overlap | Mean ask-indicative | Median | P90 |",
                "|---|---:|---:|---:|---:|",
                *snapshot_premium_lines,
                "",
                "### Busan-like 0.80-0.90 价格形态（仅诊断，不作为门槛）",
                "",
                "| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
                *anchor_band_lines,
                "",
                "## current YES vs d1 NO（严格同一 signal 分母）",
                "",
                "current YES 只有最终最高温正好停在当前档才赢；d1 NO 只要求最终最高温不是下一档。当前档已经打印后，停在当前档时两者都赢，只升一档时两者都输，只有升两档及以上时 d1 NO 额外赢。因此 d1 NO 是 `d2+ overshoot` 保险，是否值得取决于额外胜率能否覆盖价格溢价。",
                "",
                "| Price layer | Cohort | Paired rows | Dates | Current YES win | d1 NO win | d1-current ask | Current YES ROI | d1 NO ROI | YES-d1 ROI | 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                *paired_lines,
                "",
                "## Direct executable ask result",
                "",
                "| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
                *executable_lines,
                "",
                "## Broad indicative-price diagnostic（不可当成成交回测）",
                "",
                "| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
                *indicative_lines,
                "",
                "## support count diagnostic（base cohort，非门槛）",
                "",
                "| Support | Expression | Rows | Dates | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
                *support_lines,
                "",
                "## Busan executed anchor case",
                "",
                sanity_line,
                "",
                *lineage_lines,
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
                "conclusion=inconclusive_tiny_live_probe_only",
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
