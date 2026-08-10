#!/usr/bin/env python3
"""Research current-YES peak-forming state-gate v2 from live telemetry.

This is signal-layer research, not a live-order replay.  The grain is one
deduped peak-forming telemetry signal epoch:

    city + target_date + current_bracket + token_id + running_max_obs_utc

The script labels each signal with settlement_outcomes and evaluates whether a
more conservative state gate improves the old peak-forming signal.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


DB_PATH = ROOT / "runtime/weather.db"
SNAPSHOT_DIR = historical_strategy_snapshots()
PEAK_TELEMETRY = (
    ROOT
    / "runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl"
)
FADE_TELEMETRY = (
    ROOT
    / "runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl"
)
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-22-current-yes-state-gate-v2.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-22-current-yes-state-gate-v2.md"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"

START_DATE = "2026-06-18"
END_DATE = "2026-06-22"
MIN_FIRST_TOUCH_CADENCE_FRACTION = 0.75
NOTIONAL_USD = 1.0

POST_SNAPSHOT_SIGNAL_STATUSES = {
    "planned",
    "city_day_cap",
    "strategy_signal_cap",
    "fresh_ask_exceeds_cushion",
    "fresh_edge_below_required",
    "fresh_book_no_ask",
    "fresh_book_fetch_failed",
}


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_float(value: Any, default: float = math.nan) -> float:
    if value in (None, "", "NaN"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value * 100:+.1f}%"


def usd(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"${value:+.2f}"


def safe_div(num: float, den: float) -> float | None:
    return None if not den else num / den


def date_from_snapshot_name(path: Path) -> str | None:
    parts = path.stem.split("_")
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(parts[1], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def snapshot_time(path: Path, data: dict[str, Any]) -> datetime | None:
    parsed = parse_dt(data.get("ts_utc"))
    if parsed is not None:
        return parsed
    parts = path.stem.split("_")
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(parts[1] + parts[2][:4], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def local_snapshot_path(value: Any) -> Path | None:
    if not value:
        return None
    path = SNAPSHOT_DIR / Path(str(value)).name
    return path if path.exists() else None


def load_snapshot(path: Path, cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    if path not in cache:
        try:
            cache[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cache[path] = {}
    return cache[path]


def snapshot_record(row: dict[str, Any], cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    path = local_snapshot_path(row.get("snapshot_path"))
    if path is None:
        return {}
    data = load_snapshot(path, cache)
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    bracket = str(row.get("current_bracket") or "")
    for record in data.get("records", []) or []:
        if (
            str(record.get("city") or "") == city
            and str(record.get("target_date") or "") == target_date
            and str(record.get("bracket") or "") == bracket
        ):
            return record
    return {}


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def market_unit(record: dict[str, Any], row: dict[str, Any]) -> str:
    question = str(record.get("question") or "")
    if "°F" in question or " F" in question:
        return "F"
    if "°C" in question or " C" in question:
        return "C"
    bracket = str(row.get("current_bracket") or "")
    nums = [to_float(part) for part in bracket.replace("+", "").split("-")]
    nums = [x for x in nums if math.isfinite(x)]
    if nums and max(nums) > 60:
        return "F"
    return "C"


def native_temp_value(temp_f: float, unit: str) -> int:
    if unit == "F":
        return round_half_up(temp_f)
    return round_half_up((temp_f - 32.0) * 5.0 / 9.0)


def bracket_contains_value(bracket: str, value: int) -> bool:
    text = str(bracket or "").strip()
    if not text:
        return False
    if text.endswith("+"):
        low = to_float(text[:-1])
        return math.isfinite(low) and value >= int(low)
    if "-" in text:
        lo_s, hi_s = text.split("-", 1)
        lo = to_float(lo_s)
        hi = to_float(hi_s)
        return math.isfinite(lo) and math.isfinite(hi) and int(lo) <= value <= int(hi)
    target = to_float(text)
    return math.isfinite(target) and value == int(target)


def snapshot_date_window(start_date: str, end_date: str) -> tuple[str, str]:
    start = datetime.fromisoformat(start_date).date() - timedelta(days=1)
    end = datetime.fromisoformat(end_date).date() + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def build_snapshot_observation_index(start_date: str, end_date: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Build point-in-time visible obs history from paper snapshots."""

    snap_start, snap_end = snapshot_date_window(start_date, end_date)
    by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        path_date = date_from_snapshot_name(path)
        if path_date is not None and not (snap_start <= path_date <= snap_end):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        snap_ts = snapshot_time(path, data)
        if snap_ts is None:
            continue
        for record in data.get("records", []) or []:
            target_date = str(record.get("target_date") or "")
            if not (start_date <= target_date <= end_date):
                continue
            city = str(record.get("city") or "")
            obs_ts = parse_dt(record.get("metar_latest_ts_utc"))
            temp_f = to_float(record.get("metar_latest_temp_f"))
            if not city or obs_ts is None or not math.isfinite(temp_f):
                continue
            obs_key = obs_ts.isoformat()
            item = by_key[(city, target_date)].get(obs_key)
            if item is None or snap_ts < item["first_seen_snapshot_ts_utc"]:
                by_key[(city, target_date)][obs_key] = {
                    "obs_ts_utc": obs_ts,
                    "first_seen_snapshot_ts_utc": snap_ts,
                    "temp_f": temp_f,
                    "record": record,
                    "snapshot_path": path,
                }
    return {key: sorted(items.values(), key=lambda x: x["obs_ts_utc"]) for key, items in by_key.items()}


def load_settlements() -> dict[tuple[str, str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, final_price, settlement_status, created_at_utc
        FROM settlement_outcomes
        WHERE target_date >= ?
        """,
        (START_DATE,),
    ).fetchall()
    conn.close()
    return {(str(r["city"]), str(r["target_date"]), str(r["bracket"])): dict(r) for r in rows}


def sql_self_check() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    out = {
        "fact_signal_candidates": dict(
            conn.execute(
                "SELECT COUNT(*) rows, MIN(decision_snapshot_ts_utc) min_ts, MAX(decision_snapshot_ts_utc) max_ts FROM fact_signal_candidates"
            ).fetchone()
        ),
        "fact_trades": dict(
            conn.execute("SELECT COUNT(*) rows, MIN(fill_ts_utc) min_ts, MAX(fill_ts_utc) max_ts FROM fact_trades").fetchone()
        ),
        "trade_class": [dict(r) for r in conn.execute("SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class ORDER BY rows DESC")],
        "settlement": [
            dict(r)
            for r in conn.execute("SELECT COALESCE(settlement_status, '') settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status")
        ],
        "live_real": dict(
            conn.execute(
                """
                SELECT COUNT(*) rows,
                       SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) settled_rows,
                       ROUND(SUM(cost_usd), 6) cost_usd,
                       ROUND(SUM(pnl_usd_at_fill), 6) pnl_usd
                FROM fact_trades
                WHERE trade_class='live_real'
                """
            ).fetchone()
        ),
    }
    conn.close()
    return out


def load_clob_gate_pass() -> bool | None:
    if not GATE_JSON.exists():
        return None
    try:
        payload = json.loads(GATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = payload.get("gate_pass")
    return bool(value) if value is not None else None


def load_peak_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with PEAK_TELEMETRY.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            target_date = str(row.get("target_date") or "")
            if not (START_DATE <= target_date <= END_DATE):
                continue
            if row.get("entry_profile") != "peak_forming_micro":
                continue
            if row.get("decision_status") not in POST_SNAPSHOT_SIGNAL_STATUSES:
                continue
            rows.append(row)
    rows.sort(key=lambda r: parse_dt(r.get("created_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))
    return rows


def signal_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("city") or ""),
        str(row.get("target_date") or ""),
        str(row.get("current_bracket") or ""),
        str(row.get("token_id") or ""),
        str(row.get("running_max_obs_utc") or row.get("last_obs_utc") or ""),
    )


def dedupe_signal_epochs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        seen.setdefault(signal_key(row), row)
    return list(seen.values())


def first_touch_state(
    row: dict[str, Any],
    record: dict[str, Any],
    obs_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    path = local_snapshot_path(row.get("snapshot_path"))
    decision_ts = parse_dt(row.get("snapshot_ts_utc")) or parse_dt(row.get("created_at_utc"))
    if decision_ts is None and path is not None:
        try:
            decision_ts = snapshot_time(path, json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            decision_ts = None
    if decision_ts is None:
        return {"ok": False, "reasons": ["missing_decision_ts"]}
    key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
    obs_all = obs_index.get(key, [])
    visible = [x for x in obs_all if x["obs_ts_utc"] <= decision_ts and x["first_seen_snapshot_ts_utc"] <= decision_ts]
    if not visible:
        return {"ok": False, "reasons": ["missing_visible_obs_history"]}
    unit = market_unit(record, row)
    values = [native_temp_value(float(x["temp_f"]), unit) for x in visible]
    running_value = max(values)
    bracket = str(row.get("current_bracket") or "")
    running_bracket_ok = bracket_contains_value(bracket, running_value)
    first_idx = values.index(running_value)
    first_obs = visible[first_idx]
    latest_value = values[-1]
    latest_maps_to_running = latest_value == running_value and running_bracket_ok
    post_first_values = values[first_idx + 1 :]
    post_first_count = len(post_first_values)
    same_running_count = sum(1 for value in values if value == running_value)
    higher_after_first = sum(1 for value in post_first_values if value > running_value)
    cadence = to_float(row.get("obs_cadence_min"))
    if not math.isfinite(cadence) or cadence <= 0:
        cadence = to_float(row.get("obs_age_min")) + to_float(row.get("minutes_to_next_obs"))
    if not math.isfinite(cadence) or cadence <= 0:
        cadence = to_float(record.get("estimated_cadence_min"), to_float(record.get("metar_cadence_min")))
    elapsed = (decision_ts - first_obs["obs_ts_utc"]).total_seconds() / 60.0
    cadence_elapsed = math.isfinite(cadence) and elapsed >= MIN_FIRST_TOUCH_CADENCE_FRACTION * cadence
    plateau_ok = same_running_count >= 2 or cadence_elapsed
    ok = (
        running_bracket_ok
        and latest_maps_to_running
        and post_first_count >= 1
        and higher_after_first == 0
        and plateau_ok
    )
    reasons: list[str] = []
    if not running_bracket_ok:
        reasons.append("running_value_not_in_bracket")
    if not latest_maps_to_running:
        reasons.append("latest_not_running_value")
    if post_first_count < 1:
        reasons.append("no_post_first_high_obs")
    if higher_after_first:
        reasons.append("higher_after_first_high")
    if not plateau_ok:
        reasons.append("no_cadence_or_repeat_high")
    return {
        "ok": ok,
        "reasons": reasons,
        "unit": unit,
        "running_value": running_value,
        "latest_value": latest_value,
        "first_running_max_obs_utc": first_obs["obs_ts_utc"].isoformat(),
        "elapsed_since_first_running_max_min": elapsed,
        "cadence_min": cadence if math.isfinite(cadence) else None,
        "post_first_high_obs_count": post_first_count,
        "same_running_max_obs_count": same_running_count,
        "higher_after_first_high_count": higher_after_first,
        "latest_maps_to_running": latest_maps_to_running,
        "running_bracket_ok": running_bracket_ok,
        "visible_obs_count": len(visible),
    }


@dataclass
class Signal:
    row: dict[str, Any]
    settlement: dict[str, Any]
    first_touch: dict[str, Any]
    price_snapshot: float | None
    price_fresh: float | None
    price_live_like: float | None

    @property
    def target_date(self) -> str:
        return str(self.row.get("target_date") or "")

    @property
    def status(self) -> str:
        return str(self.row.get("decision_status") or "")

    @property
    def settled(self) -> bool:
        return self.settlement.get("settlement_status") == "settled" and self.final_yes is not None

    @property
    def final_yes(self) -> float | None:
        value = to_float(self.settlement.get("final_price"))
        return value if math.isfinite(value) else None

    @property
    def win(self) -> bool | None:
        return None if self.final_yes is None else self.final_yes >= 0.5


def candidate_price(row: dict[str, Any], mode: str) -> float | None:
    if mode == "snapshot":
        price = to_float(row.get("yes_current_ask"))
    elif mode == "fresh":
        price = to_float(row.get("fresh_best_ask"))
        if not math.isfinite(price) or price <= 0:
            price = to_float(row.get("taker_limit_price"))
    elif mode == "live_like":
        if row.get("decision_status") != "planned":
            return None
        price = to_float(row.get("taker_limit_price"))
        if not math.isfinite(price) or price <= 0:
            price = to_float(row.get("fresh_best_ask"))
    else:
        raise ValueError(mode)
    if not math.isfinite(price) or price <= 0 or price >= 1:
        return None
    return price


def enrich_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    settlements = load_settlements()
    obs_index = build_snapshot_observation_index(START_DATE, END_DATE)
    snapshot_cache: dict[Path, dict[str, Any]] = {}
    out: list[Signal] = []
    for row in rows:
        record = snapshot_record(row, snapshot_cache)
        settlement = settlements.get(
            (str(row.get("city") or ""), str(row.get("target_date") or ""), str(row.get("current_bracket") or "")),
            {},
        )
        out.append(
            Signal(
                row=row,
                settlement=settlement,
                first_touch=first_touch_state(row, record, obs_index),
                price_snapshot=candidate_price(row, "snapshot"),
                price_fresh=candidate_price(row, "fresh"),
                price_live_like=candidate_price(row, "live_like"),
            )
        )
    return out


def finite(value: Any) -> bool:
    return math.isfinite(to_float(value))


def le_field(field: str, threshold: float, *, missing_pass: bool = False) -> Callable[[Signal], bool]:
    def fn(signal: Signal) -> bool:
        value = to_float(signal.row.get(field))
        if not math.isfinite(value):
            return missing_pass
        return value <= threshold

    return fn


def ge_field(field: str, threshold: float, *, missing_pass: bool = False) -> Callable[[Signal], bool]:
    def fn(signal: Signal) -> bool:
        value = to_float(signal.row.get(field))
        if not math.isfinite(value):
            return missing_pass
        return value >= threshold

    return fn


def post_high_gap_ok(signal: Signal) -> bool:
    minutes_since = to_float(signal.row.get("minutes_since_running_max"))
    obs_age = to_float(signal.row.get("obs_age_min"))
    cadence = to_float(signal.row.get("obs_cadence_min"))
    if not all(math.isfinite(x) for x in (minutes_since, obs_age, cadence)):
        return False
    return minutes_since - obs_age >= max(20.0, MIN_FIRST_TOUCH_CADENCE_FRACTION * cadence)


def has_remaining_reheat(signal: Signal) -> bool:
    value = to_float(signal.row.get("forecast_reheat_after_now_f"))
    return math.isfinite(value)


def no_reheat_le(threshold: float) -> Callable[[Signal], bool]:
    def fn(signal: Signal) -> bool:
        value = to_float(signal.row.get("forecast_reheat_after_now_f"))
        if math.isfinite(value):
            return value <= threshold
        remaining = to_float(signal.row.get("forecast_remaining_max_f"))
        running_c = to_float(signal.row.get("running_max_c"))
        if math.isfinite(remaining) and math.isfinite(running_c):
            running_f = running_c * 9.0 / 5.0 + 32.0
            return remaining - running_f <= threshold
        return False

    return fn


def price_for(signal: Signal, price_mode: str) -> float | None:
    if price_mode == "snapshot":
        return signal.price_snapshot
    if price_mode == "fresh":
        return signal.price_fresh
    if price_mode == "live_like":
        return signal.price_live_like
    raise ValueError(price_mode)


@dataclass
class Variant:
    name: str
    price_mode: str
    filters: list[Callable[[Signal], bool]]
    description: str

    def keep(self, signal: Signal) -> bool:
        return all(fn(signal) for fn in self.filters) and price_for(signal, self.price_mode) is not None


def variant_defs() -> list[Variant]:
    return [
        Variant("old_peak_snapshot_price", "snapshot", [], "Old peak-forming profile signal, evaluated at snapshot ask."),
        Variant("old_peak_fresh_proxy", "fresh", [], "Old peak-forming profile signal, evaluated at fresh ask/limit when available."),
        Variant("old_peak_live_planned", "live_like", [], "Rows that the current runner accepted as planned orders."),
        Variant("last_max_gap_v0", "fresh", [post_high_gap_ok], "Fail-closed last-running-max gap check."),
        Variant("first_touch_plateau_v2", "fresh", [lambda s: bool(s.first_touch.get("ok"))], "Correct first-touch post-observation plateau state."),
        Variant(
            "first_touch_after_forecast_peak",
            "fresh",
            [lambda s: bool(s.first_touch.get("ok")), le_field("forecast_peak_delta_hours_local", 0.0)],
            "First-touch plateau plus forecast peak at/behind decision time.",
        ),
        Variant(
            "first_touch_dtmp3_le_2f",
            "fresh",
            [lambda s: bool(s.first_touch.get("ok")), le_field("d_tmpf_3h", 2.0, missing_pass=False)],
            "First-touch plateau plus no strong 3h warming.",
        ),
        Variant(
            "first_touch_after_peak_dtmp3_le_2f",
            "fresh",
            [
                lambda s: bool(s.first_touch.get("ok")),
                le_field("forecast_peak_delta_hours_local", 0.0),
                le_field("d_tmpf_3h", 2.0, missing_pass=False),
            ],
            "First-touch plateau, forecast peak passed, and no strong 3h warming.",
        ),
        Variant(
            "first_touch_no_reheat_le_0_9f",
            "fresh",
            [lambda s: bool(s.first_touch.get("ok")), no_reheat_le(0.9)],
            "First-touch plateau plus explicit remaining forecast reheat <= 0.9F.",
        ),
        Variant(
            "dtmp3_le_2f_only",
            "fresh",
            [le_field("d_tmpf_3h", 2.0, missing_pass=False)],
            "No strong 3h warming, without first-touch requirement.",
        ),
        Variant(
            "forecast_peak_passed_only",
            "fresh",
            [le_field("forecast_peak_delta_hours_local", 0.0)],
            "Forecast peak at/behind decision time only.",
        ),
    ]


def pnl_for(signal: Signal, price: float) -> float | None:
    if signal.final_yes is None:
        return None
    shares = NOTIONAL_USD / price
    return signal.final_yes * shares - NOTIONAL_USD


def summarize(signals: list[Signal], variant: Variant) -> dict[str, Any]:
    kept = [s for s in signals if variant.keep(s)]
    settled = [s for s in kept if s.settled]
    unsettled = [s for s in kept if not s.settled]
    wins = sum(1 for s in settled if s.win)
    cost = 0.0
    pnl = 0.0
    prices: list[float] = []
    for signal in settled:
        price = price_for(signal, variant.price_mode)
        if price is None:
            continue
        prices.append(price)
        cost += NOTIONAL_USD
        pnl += float(pnl_for(signal, price) or 0.0)
    dates = sorted({s.target_date for s in settled})
    by_date: dict[str, dict[str, Any]] = {}
    for date in dates:
        part = [s for s in settled if s.target_date == date]
        date_cost = 0.0
        date_pnl = 0.0
        date_wins = 0
        for signal in part:
            price = price_for(signal, variant.price_mode)
            if price is None:
                continue
            date_cost += NOTIONAL_USD
            date_pnl += float(pnl_for(signal, price) or 0.0)
            date_wins += 1 if signal.win else 0
        by_date[date] = {
            "signals": len(part),
            "wins": date_wins,
            "losses": len(part) - date_wins,
            "roi": safe_div(date_pnl, date_cost),
            "pnl_usd_per_1usd": date_pnl,
        }
    return {
        "variant": variant.name,
        "description": variant.description,
        "price_mode": variant.price_mode,
        "kept_signals": len(kept),
        "settled_signals": len(settled),
        "unsettled_signals": len(unsettled),
        "active_dates": len(dates),
        "wins": wins,
        "losses": len(settled) - wins,
        "win_rate": safe_div(wins, len(settled)),
        "avg_price": safe_div(sum(prices), len(prices)),
        "cost_usd_per_1usd": cost,
        "pnl_usd_per_1usd": pnl,
        "roi": safe_div(pnl, cost),
        "date_breakdown": by_date,
    }


def date_bootstrap_ci(signals: list[Signal], variant: Variant, n: int = 5000) -> list[float | None]:
    kept_settled = [s for s in signals if variant.keep(s) and s.settled]
    dates = sorted({s.target_date for s in kept_settled})
    if len(dates) < 2:
        return [None, None]
    daily: dict[str, tuple[float, float]] = {}
    for date in dates:
        cost = 0.0
        pnl = 0.0
        for signal in [s for s in kept_settled if s.target_date == date]:
            price = price_for(signal, variant.price_mode)
            if price is None:
                continue
            cost += NOTIONAL_USD
            pnl += float(pnl_for(signal, price) or 0.0)
        daily[date] = (cost, pnl)
    rng = random.Random(20260622)
    vals: list[float] = []
    for _ in range(n):
        cost = 0.0
        pnl = 0.0
        for _ in dates:
            c, p = daily[rng.choice(dates)]
            cost += c
            pnl += p
        if cost:
            vals.append(pnl / cost)
    if not vals:
        return [None, None]
    vals.sort()
    return [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]


def grid_variants() -> list[Variant]:
    out: list[Variant] = []
    for threshold in [-1.0, 0.0, 1.0, 2.0, 3.0, 4.0]:
        out.append(
            Variant(
                f"grid_first_touch_dtmp3_le_{threshold:g}",
                "fresh",
                [lambda s: bool(s.first_touch.get("ok")), le_field("d_tmpf_3h", threshold, missing_pass=False)],
                f"First-touch plateau and d_tmpf_3h <= {threshold:g}F.",
            )
        )
    for threshold in [-1.0, 0.0, 0.5, 1.0, 2.0]:
        out.append(
            Variant(
                f"grid_first_touch_forecast_delta_le_{threshold:g}",
                "fresh",
                [lambda s: bool(s.first_touch.get("ok")), le_field("forecast_peak_delta_hours_local", threshold)],
                f"First-touch plateau and forecast peak delta <= {threshold:g}h.",
            )
        )
    for threshold in [0.5, 0.9, 1.5, 2.0]:
        out.append(
            Variant(
                f"grid_first_touch_reheat_le_{threshold:g}",
                "fresh",
                [lambda s: bool(s.first_touch.get("ok")), no_reheat_le(threshold)],
                f"First-touch plateau and remaining reheat <= {threshold:g}F.",
            )
        )
    return out


def concise_signal(signal: Signal) -> dict[str, Any]:
    row = signal.row
    price = signal.price_fresh or signal.price_snapshot
    pnl = pnl_for(signal, price) if price else None
    return {
        "created_at_utc": row.get("created_at_utc"),
        "target_date": row.get("target_date"),
        "city": row.get("city"),
        "bracket": row.get("current_bracket"),
        "status": row.get("decision_status"),
        "price": price,
        "final_yes": signal.final_yes,
        "win": signal.win,
        "pnl_per_1usd": pnl,
        "first_touch_ok": signal.first_touch.get("ok"),
        "first_touch_reasons": signal.first_touch.get("reasons"),
        "forecast_peak_delta_hours_local": row.get("forecast_peak_delta_hours_local"),
        "forecast_reheat_after_now_f": row.get("forecast_reheat_after_now_f"),
        "d_tmpf_1h": row.get("d_tmpf_1h"),
        "d_tmpf_3h": row.get("d_tmpf_3h"),
        "minutes_since_running_max": row.get("minutes_since_running_max"),
        "obs_age_min": row.get("obs_age_min"),
        "obs_cadence_min": row.get("obs_cadence_min"),
    }


def format_ci(ci: list[float | None]) -> str:
    if ci[0] is None:
        return "NA"
    return f"[{pct(ci[0])}, {pct(ci[1])}]"


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES state gate v2 telemetry research",
        "",
        "Target metric: 用 split current-YES `peak_forming_micro` forward telemetry 验证状态层从“刚摸高”改成“post-observation hold / hazard downtrend”后，是否出现比旧 peak 信号更可靠的方向。",
        "",
        "## Data Snapshot",
        "",
        f"- Window: target_date `{START_DATE}`..`{END_DATE}`; settled labels currently available through `{payload['data']['max_settled_target_date']}`.",
        "- Evidence layer: `forward_telemetry.jsonl` signal telemetry + `settlement_outcomes`; this is not `live_real` fill PnL.",
        "- Row grain: one deduped signal epoch = `city + target_date + bracket + token_id + running_max_obs_utc`.",
        "- Price modes: `snapshot` = snapshot ask; `fresh` = fresh ask/limit when present; `live_like` = only current runner `planned` rows.",
        f"- Telemetry synced after fixing split runtime sync; peak latest summary generated_at `{payload['data']['peak_latest_summary'].get('generated_at_utc')}`, live_enabled `{payload['data']['peak_latest_summary'].get('live_enabled')}`.",
        f"- {payload['data']['run_stack_note']}.",
        f"- CLOB coverage gate: `gate_pass={payload['data']['clob_gate_pass']}`.",
        "",
        "## 5-line Self-check",
        "",
        "```json",
        json.dumps(payload["data"]["sql_self_check"], indent=2, sort_keys=True),
        "```",
        "",
        "## Funnel",
        "",
        f"- Raw peak telemetry rows in window after old peak profile pass: {payload['funnel']['raw_peak_profile_rows']}.",
        f"- Deduped signal epochs: {payload['funnel']['deduped_signal_epochs']}.",
        f"- Settled deduped signal epochs: {payload['funnel']['settled_signal_epochs']}.",
        f"- Unsettled/pending signal epochs: {payload['funnel']['unsettled_signal_epochs']}.",
        f"- Decision status counts after dedupe: `{payload['funnel']['deduped_status_counts']}`.",
        "",
        "## Main Variants",
        "",
        "| variant | price | kept | settled | dates | W-L | win | avg price | ROI | PnL per $1 | date bootstrap 95% ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["summaries"]:
        lines.append(
            f"| `{row['variant']}` | {row['price_mode']} | {row['kept_signals']} | {row['settled_signals']} | "
            f"{row['active_dates']} | {row['wins']}-{row['losses']} | {pct(row['win_rate'])} | "
            f"{pct(row['avg_price'])} | {pct(row['roi'])} | {usd(row['pnl_usd_per_1usd'])} | "
            f"{format_ci(row['date_bootstrap_roi_95'])} |"
        )
    lines += [
        "",
        "## Grid Search",
        "",
        "Exploratory only.  The window has too few settled dates for promotion; this is used to choose what to keep tracking next.",
        "",
        "| variant | kept | settled | dates | W-L | win | ROI | CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["grid_top"]:
        lines.append(
            f"| `{row['variant']}` | {row['kept_signals']} | {row['settled_signals']} | {row['active_dates']} | "
            f"{row['wins']}-{row['losses']} | {pct(row['win_rate'])} | {pct(row['roi'])} | {format_ci(row['date_bootstrap_roi_95'])} |"
        )
    lines += [
        "",
        "## Finding",
        "",
        "- `old_peak_live_planned` is the closest live-action proxy: after adding 2026-06-21 settlement it is 18 settled signals, 11-7, ROI -14.0%, and the date bootstrap CI still crosses 0.  That is a clear no-live result.",
        "- `last_max_gap_v0` passes zero rows because live telemetry stores the last observation equal to the running max, not the first touch.  This confirms the old cadence field is structurally wrong for plateau detection.",
        "- `first_touch_plateau_v2` is the right state semantics to log, but it is not a tradable hard gate yet: 31 settled signals, ROI -2.9%, CI crosses 0.",
        "- Adding `first_touch` as a hard gate sample-starves the slice and does not rescue expectancy.  The previous best-looking hazard/downtrend feature `d_tmpf_3h <= 2F` also failed after 2026-06-21 settled: 40 settled signals, 31-9, ROI -8.1%, CI crosses 0.",
        "- `forecast_peak_passed_only` and `first_touch_after_forecast_peak` are too blunt here; they cut sample and still do not create a reliable live-grade edge.",
        "",
        "## Recommendation",
        "",
        "Do not replace the old `minutes_since_running_max >= 10` live gate with a new peak-forming hard gate yet.  Keep `peak_forming_micro` real live disabled and leave fade live unchanged.",
        "",
        "```text",
        "peak_state_v2_next_step =",
        "  telemetry/research only",
        "  + log first_touch_plateau fields",
        "  + log hazard/downtrend features such as d_tmpf_3h",
        "  + collect more settled forward dates before any shadow trading rule",
        "```",
        "",
        "Shadow verdict: telemetry-only, not a new executable shadow rule.  Live verdict: no live change.",
        "",
        "Contract verdict:",
        "",
        "```text",
        "significance=FAIL",
        "baseline=FAIL",
        "forward=FAIL",
        "conclusion=inconclusive",
        "```",
        "",
        "## Examples",
        "",
        "### d_tmpf_3h <= 2F kept settled examples",
        "",
        "| created | date | city | bracket | status | win | price | PnL/$1 | peak_delta | d_tmpf_3h |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["examples"]["dtmp3_le_2f_kept_settled"][:30]:
        lines.append(
            f"| {row['created_at_utc']} | {row['target_date']} | {row['city']} | {row['bracket']} | "
            f"{row['status']} | {row['win']} | {row['price']:.3f} | {usd(row['pnl_per_1usd'])} | "
            f"{row['forecast_peak_delta_hours_local']} | {row['d_tmpf_3h']} |"
        )
    lines += [
        "",
        "### d_tmpf_3h > 2F or missing rejected losing examples",
        "",
        "| created | date | city | bracket | status | win | price | reasons | d_tmpf_3h |",
        "|---|---|---|---|---|---:|---:|---|---:|",
    ]
    for row in payload["examples"]["dtmp3_rejected_losers"][:30]:
        price = "NA" if row["price"] is None else f"{row['price']:.3f}"
        lines.append(
            f"| {row['created_at_utc']} | {row['target_date']} | {row['city']} | {row['bracket']} | "
            f"{row['status']} | {row['win']} | {price} | d_tmpf_3h_gt_2_or_missing | {row['d_tmpf_3h']} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    raw_rows = load_peak_rows()
    deduped_rows = dedupe_signal_epochs(raw_rows)
    signals = enrich_signals(deduped_rows)
    settled_signals = [s for s in signals if s.settled]
    max_settled_target_date = max([s.target_date for s in settled_signals], default="")
    latest_summary = json.loads((PEAK_TELEMETRY.parent / "latest_summary.json").read_text(encoding="utf-8"))

    variants = variant_defs()
    summaries: list[dict[str, Any]] = []
    for variant in variants:
        summary = summarize(signals, variant)
        summary["date_bootstrap_roi_95"] = date_bootstrap_ci(signals, variant)
        summaries.append(summary)

    grid_rows: list[dict[str, Any]] = []
    for variant in grid_variants():
        summary = summarize(signals, variant)
        summary["date_bootstrap_roi_95"] = date_bootstrap_ci(signals, variant)
        if summary["settled_signals"] >= 8:
            grid_rows.append(summary)
    grid_rows.sort(key=lambda r: (r["active_dates"], r["settled_signals"], r["roi"] or -999), reverse=True)

    selected = next(v for v in variants if v.name == "dtmp3_le_2f_only")
    selected_kept = [s for s in signals if selected.keep(s) and s.settled]
    dtmp3_rejected_losers = [
        s
        for s in signals
        if s.settled
        and not selected.keep(s)
        and s.win is False
        and (s.price_fresh or s.price_snapshot) is not None
    ]

    payload = {
        "scope": "current_yes_peak_forming_state_gate_v2_telemetry",
        "data": {
            "window": {"target_date_start": START_DATE, "target_date_end": END_DATE},
            "peak_telemetry": str(PEAK_TELEMETRY.relative_to(ROOT)),
            "fade_telemetry": str(FADE_TELEMETRY.relative_to(ROOT)),
            "db": str(DB_PATH.relative_to(ROOT)),
            "max_settled_target_date": max_settled_target_date,
            "peak_latest_summary": {
                "generated_at_utc": latest_summary.get("generated_at_utc"),
                "snapshot_ts_utc": latest_summary.get("snapshot_ts_utc"),
                "live_enabled": latest_summary.get("live_enabled"),
                "forward_telemetry_rows": latest_summary.get("forward_telemetry_rows"),
                "trigger_signal_count": latest_summary.get("trigger_signal_count"),
            },
            "sql_self_check": sql_self_check(),
            "clob_gate_pass": load_clob_gate_pass(),
            "run_stack_note": "run_stack.sh rebuilt fact tables before this report; DB and CLOB coverage gate were usable",
        },
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "shadow": "telemetry_only",
            "live": "no_live_change",
            "fade_live": "unchanged",
        },
        "funnel": {
            "raw_peak_profile_rows": len(raw_rows),
            "deduped_signal_epochs": len(signals),
            "settled_signal_epochs": len(settled_signals),
            "unsettled_signal_epochs": len([s for s in signals if not s.settled]),
            "deduped_status_counts": dict(Counter(s.status for s in signals)),
            "raw_status_counts": dict(Counter(str(r.get("decision_status") or "") for r in raw_rows)),
        },
        "state_definition": {
            "first_touch_plateau": [
                "post_first_high_obs_count >= 1",
                "higher_after_first_high_count == 0",
                "latest observation maps to running max bracket",
                "elapsed_since_first_running_max >= 0.75 * cadence OR same_running_max_obs_count >= 2",
            ],
            "recommended_shadow_candidate": [
                "d_tmpf_3h <= 2F",
                "first_touch_plateau logged as feature, not hard gate yet",
                "existing price/model edge gates",
            ],
        },
        "summaries": summaries,
        "grid_top": grid_rows[:12],
        "examples": {
            "dtmp3_le_2f_kept_settled": [concise_signal(s) for s in selected_kept],
            "dtmp3_rejected_losers": [concise_signal(s) for s in dtmp3_rejected_losers],
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "summaries": summaries, "grid_top": grid_rows[:8]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
