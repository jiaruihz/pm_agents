#!/usr/bin/env python3
"""Counterfactual replay for current-YES cadence-aware state gates.

This intentionally replays raw split current-YES live order rows because the
newer split-run fills are not yet present in fact_trades on this local mirror.
Matched CLOB responses contain the executed cost/shares needed for settled PnL.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = ROOT / "runtime/weather_edge_v1/remote_pm_agent/live"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
DB_PATH = ROOT / "runtime/weather.db"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-21-current-yes-cadence-state-live-counterfactual-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-21-current-yes-cadence-state-live-counterfactual-v0.md"

ORDER_FILES = [
    LIVE_DIR / "theta_current_yes_peak_forming_micro_tiny_live_v1_orders.jsonl",
    LIVE_DIR / "theta_current_yes_fade_confirmed_tiny_live_v1_orders.jsonl",
]

START_DATE = "2026-06-18"
END_DATE = "2026-06-21"
MIN_POST_HIGH_CYCLE_FRACTION = 0.75
MIN_POST_HIGH_GAP_MIN = 20.0
FORECAST_REMAINING_BUFFER_F = 0.9


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
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


def safe_div(num: float, den: float) -> float | None:
    return None if not den else num / den


def local_snapshot_path(source_path: Any) -> Path | None:
    if not source_path:
        return None
    name = Path(str(source_path)).name
    if not name:
        return None
    path = SNAPSHOT_DIR / name
    return path if path.exists() else None


def snapshot_time(path: Path, data: dict[str, Any]) -> datetime | None:
    parsed = parse_dt(data.get("ts_utc"))
    if parsed is not None:
        return parsed
    # Filename fallback: snapshot_YYYYMMDD_HHMM.json
    stem = path.stem
    try:
        date_part, time_part = stem.split("_", 2)[1:3]
        return datetime.strptime(date_part + time_part[:4], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_snapshot_record(order: dict[str, Any], cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    path = local_snapshot_path(order.get("source_snapshot_path"))
    if path is None:
        return {}
    if path not in cache:
        try:
            cache[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cache[path] = {}
    for record in cache[path].get("records", []) or []:
        if (
            str(record.get("city")) == str(order.get("city"))
            and str(record.get("target_date")) == str(order.get("target_date"))
            and str(record.get("bracket")) == str(order.get("bracket"))
        ):
            return record
    return {}


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def market_unit(record: dict[str, Any], order: dict[str, Any]) -> str:
    question = str(record.get("question") or "")
    if "°F" in question or " F" in question:
        return "F"
    if "°C" in question or " C" in question:
        return "C"
    bracket = str(order.get("bracket") or record.get("bracket") or "")
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


def build_snapshot_observation_index() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Build point-in-time obs history from paper snapshots.

    Each snapshot repeats city/date market records.  For a given city/date, the
    pair `(metar_latest_ts_utc, metar_latest_temp_f)` is the observation visible
    at that snapshot.  We keep the earliest snapshot that exposed each obs.
    """
    by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        snap_ts = snapshot_time(path, data)
        if snap_ts is None:
            continue
        for record in data.get("records", []) or []:
            target_date = str(record.get("target_date") or "")
            if not (START_DATE <= target_date <= END_DATE):
                continue
            city = str(record.get("city") or "")
            obs_ts = parse_dt(record.get("metar_latest_ts_utc"))
            temp_f = to_float(record.get("metar_latest_temp_f"))
            if not city or obs_ts is None or not math.isfinite(temp_f):
                continue
            key = (city, target_date)
            obs_key = obs_ts.isoformat()
            item = by_key.setdefault(key, {}).get(obs_key)
            if item is None or snap_ts < item["first_seen_snapshot_ts_utc"]:
                by_key.setdefault(key, {})[obs_key] = {
                    "obs_ts_utc": obs_ts,
                    "temp_f": temp_f,
                    "first_seen_snapshot_ts_utc": snap_ts,
                    "record": record,
                    "snapshot_path": path,
                }
    return {key: sorted(items.values(), key=lambda x: x["obs_ts_utc"]) for key, items in by_key.items()}


def first_touch_state(
    order: dict[str, Any],
    record: dict[str, Any],
    obs_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    path = local_snapshot_path(order.get("source_snapshot_path"))
    decision_ts = None
    if path is not None and path.exists():
        try:
            decision_ts = snapshot_time(path, json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            decision_ts = None
    decision_ts = decision_ts or parse_dt(order.get("snapshot_ts_utc")) or parse_dt(order.get("created_at_utc"))
    key = (str(order.get("city") or ""), str(order.get("target_date") or ""))
    if decision_ts is None or not record:
        return {"ok": False, "reasons": ["missing_first_touch_snapshot_context"]}
    obs_all = obs_index.get(key, [])
    visible = [
        x for x in obs_all
        if x["obs_ts_utc"] <= decision_ts and x["first_seen_snapshot_ts_utc"] <= decision_ts
    ]
    if not visible:
        return {"ok": False, "reasons": ["missing_first_touch_obs_history"]}
    unit = market_unit(record, order)
    values = [native_temp_value(float(x["temp_f"]), unit) for x in visible]
    running_value = max(values)
    bracket = str(order.get("bracket") or record.get("bracket") or "")
    running_bracket_ok = bracket_contains_value(bracket, running_value)
    first_idx = values.index(running_value)
    first_obs = visible[first_idx]
    latest_value = values[-1]
    latest_maps_to_running = latest_value == running_value and running_bracket_ok
    post_first = visible[first_idx + 1:]
    post_first_count = len(post_first)
    same_count = sum(1 for value in values if value == running_value)
    higher_after_first = sum(1 for value in values[first_idx + 1:] if value > running_value)
    cadence = to_float(order.get("obs_age_min")) + to_float(order.get("minutes_to_next_obs"))
    if not math.isfinite(cadence) or cadence <= 0:
        cadence = to_float(record.get("estimated_cadence_min"), to_float(record.get("metar_cadence_min")))
    elapsed = (decision_ts - first_obs["obs_ts_utc"]).total_seconds() / 60.0
    one_cadence_elapsed = math.isfinite(cadence) and elapsed >= MIN_POST_HIGH_CYCLE_FRACTION * cadence
    plateau_ok = same_count >= 2 or one_cadence_elapsed
    ok = (
        running_bracket_ok
        and latest_maps_to_running
        and post_first_count >= 1
        and higher_after_first == 0
        and plateau_ok
    )
    reasons: list[str] = []
    if not running_bracket_ok:
        reasons.append("running_value_not_in_order_bracket")
    if not latest_maps_to_running:
        reasons.append("latest_not_running_value")
    if post_first_count < 1:
        reasons.append("no_post_first_high_obs")
    if higher_after_first:
        reasons.append("higher_after_first_high")
    if not plateau_ok:
        reasons.append("no_full_cadence_or_repeat_high")
    return {
        "ok": ok,
        "reasons": reasons,
        "unit": unit,
        "running_value": running_value,
        "latest_value": latest_value,
        "running_bracket_ok": running_bracket_ok,
        "latest_maps_to_running": latest_maps_to_running,
        "first_running_max_obs_utc": first_obs["obs_ts_utc"].isoformat(),
        "elapsed_since_first_running_max_min": elapsed,
        "post_first_high_obs_count": post_first_count,
        "same_running_max_obs_count": same_count,
        "higher_after_first_high_count": higher_after_first,
        "visible_obs_count": len(visible),
        "cadence_min": cadence if math.isfinite(cadence) else None,
    }


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
        """
        ,
        (START_DATE,),
    ).fetchall()
    conn.close()
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["city"]), str(row["target_date"]), str(row["bracket"]))
        out[key] = dict(row)
    return out


def profile_for_order(order: dict[str, Any], source_name: str) -> str:
    explicit = str(order.get("entry_profile") or "")
    if explicit:
        return explicit
    if "peak_forming" in source_name or "peak_forming" in str(order.get("shadow_decision")):
        return "peak_forming_micro"
    if "fade_confirmed" in source_name or "fade_confirmed" in str(order.get("shadow_decision")):
        return "fade_confirmed"
    return "unknown"


def status_for_order(order: dict[str, Any]) -> str:
    place = (order.get("exchange_response") or {}).get("place") or {}
    return str(place.get("status") or order.get("status") or "")


def cost_shares(order: dict[str, Any]) -> tuple[float, float]:
    place = (order.get("exchange_response") or {}).get("place") or {}
    cost = to_float(place.get("makingAmount"))
    shares = to_float(place.get("takingAmount"))
    if not math.isfinite(cost):
        cost = to_float(order.get("posted_notional"), to_float(order.get("notional"), 0.0))
    if not math.isfinite(shares):
        price = to_float(order.get("posted_price"), to_float(order.get("limit_price")))
        shares = cost / price if price and math.isfinite(price) else 0.0
    return cost, shares


@dataclass
class EnrichedOrder:
    order: dict[str, Any]
    source_file: str
    profile: str
    order_status: str
    matched: bool
    settled: bool
    final_yes: float | None
    cost: float
    shares: float
    pnl: float | None
    win: bool | None
    telemetry_ok: bool
    cadence_min: float | None
    post_high_gap_min: float | None
    post_high_cycle_required_min: float | None
    post_high_cycle_ok: bool
    forecast_peak_delta_hours_local: float | None
    forecast_max_above_metar_max_f: float | None
    latest_same_bracket: bool | None
    running_bracket_ok: bool | None
    reject_reasons: list[str]
    first_touch_state: dict[str, Any]


def enrich_order(
    order: dict[str, Any],
    source_file: str,
    settlements: dict[tuple[str, str, str], dict[str, Any]],
    snapshot_cache: dict[Path, dict[str, Any]],
    obs_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> EnrichedOrder:
    profile = profile_for_order(order, source_file)
    order_status = status_for_order(order)
    matched = order_status == "matched"
    settlement = settlements.get((str(order.get("city")), str(order.get("target_date")), str(order.get("bracket"))), {})
    final_price = to_float(settlement.get("final_price"))
    settled = str(settlement.get("settlement_status")) == "settled" and math.isfinite(final_price)
    cost, shares = cost_shares(order)
    pnl = final_price * shares - cost if matched and settled else None
    win = bool(final_price >= 0.5) if matched and settled else None

    record = load_snapshot_record(order, snapshot_cache)
    obs_age = to_float(order.get("obs_age_min"))
    minutes_to_next = to_float(order.get("minutes_to_next_obs"))
    minutes_since_max = to_float(order.get("minutes_since_running_max"))
    cadence = obs_age + minutes_to_next if math.isfinite(obs_age) and math.isfinite(minutes_to_next) else math.nan
    if not math.isfinite(cadence) or cadence <= 0:
        cadence = math.nan
    post_high_gap = minutes_since_max - obs_age if math.isfinite(minutes_since_max) and math.isfinite(obs_age) else math.nan
    required_gap = max(MIN_POST_HIGH_GAP_MIN, MIN_POST_HIGH_CYCLE_FRACTION * cadence) if math.isfinite(cadence) else math.nan
    telemetry_ok = all(math.isfinite(x) for x in (obs_age, minutes_to_next, minutes_since_max, cadence, post_high_gap, required_gap))
    post_high_cycle_ok = bool(telemetry_ok and post_high_gap >= required_gap)

    forecast_peak_delta = to_float(order.get("forecast_peak_delta_hours_local"))
    if not math.isfinite(forecast_peak_delta):
        forecast_peak_delta = math.nan
    forecast_above = to_float(record.get("forecast_max_above_metar_max_f"))
    if not math.isfinite(forecast_above):
        forecast_above = math.nan

    running_bracket_ok: bool | None = None
    latest_same_bracket: bool | None = None
    # The live runner already selected the current running max bracket.  When the
    # source snapshot is available, keep a lightweight sanity check using exact
    # bracket text only for integer single-bracket C markets.
    if record:
        running = to_float(record.get("metar_current_max_f"))
        latest = to_float(record.get("metar_latest_temp_f"))
        bracket = str(order.get("bracket") or "")
        if math.isfinite(running) and math.isfinite(latest):
            # For C markets metar_*_f is still Fahrenheit; convert when the
            # bracket is a plausible Celsius single integer.
            try:
                b = int(bracket)
                if b <= 60:
                    running_native = round((running - 32.0) * 5.0 / 9.0)
                    latest_native = round((latest - 32.0) * 5.0 / 9.0)
                else:
                    running_native = round(running)
                    latest_native = round(latest)
                running_bracket_ok = running_native == b
                latest_same_bracket = latest_native == b
            except ValueError:
                running_bracket_ok = None
                latest_same_bracket = None

    reject_reasons: list[str] = []
    if not telemetry_ok:
        reject_reasons.append("missing_cadence_telemetry")
    elif not post_high_cycle_ok:
        reject_reasons.append("no_full_post_high_obs_cycle")
    if math.isfinite(forecast_peak_delta) and forecast_peak_delta > 0:
        reject_reasons.append("forecast_peak_still_future")
    if math.isfinite(forecast_above) and forecast_above > FORECAST_REMAINING_BUFFER_F:
        reject_reasons.append("forecast_remaining_above_current_max")
    if profile == "peak_forming_micro" and latest_same_bracket is False:
        reject_reasons.append("latest_not_same_running_bracket")
    first_state = first_touch_state(order, record, obs_index)

    return EnrichedOrder(
        order=order,
        source_file=source_file,
        profile=profile,
        order_status=order_status,
        matched=matched,
        settled=settled,
        final_yes=final_price if settled else None,
        cost=cost,
        shares=shares,
        pnl=pnl,
        win=win,
        telemetry_ok=telemetry_ok,
        cadence_min=cadence if math.isfinite(cadence) else None,
        post_high_gap_min=post_high_gap if math.isfinite(post_high_gap) else None,
        post_high_cycle_required_min=required_gap if math.isfinite(required_gap) else None,
        post_high_cycle_ok=post_high_cycle_ok,
        forecast_peak_delta_hours_local=forecast_peak_delta if math.isfinite(forecast_peak_delta) else None,
        forecast_max_above_metar_max_f=forecast_above if math.isfinite(forecast_above) else None,
        latest_same_bracket=latest_same_bracket,
        running_bracket_ok=running_bracket_ok,
        reject_reasons=reject_reasons,
        first_touch_state=first_state,
    )


def load_orders() -> list[EnrichedOrder]:
    settlements = load_settlements()
    snapshot_cache: dict[Path, dict[str, Any]] = {}
    obs_index = build_snapshot_observation_index()
    rows: list[EnrichedOrder] = []
    for path in ORDER_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            order = json.loads(line)
            target_date = str(order.get("target_date") or "")
            if not (START_DATE <= target_date <= END_DATE):
                continue
            rows.append(enrich_order(order, path.name, settlements, snapshot_cache, obs_index))
    return rows


def variant_keep(row: EnrichedOrder, variant: str) -> bool:
    if variant == "actual_matched":
        return True
    if variant == "fade_only_stop_peak_forming":
        return row.profile != "peak_forming_micro"
    if variant == "cadence_only_last_max_v0":
        if not row.post_high_cycle_ok:
            return False
        if row.profile == "peak_forming_micro" and row.latest_same_bracket is False:
            return False
        return True
    if variant == "cadence_only_first_touch_v1":
        if row.profile == "peak_forming_micro":
            return bool(row.first_touch_state.get("ok"))
        return row.post_high_cycle_ok
    if variant == "cadence_first_touch_after_forecast_peak":
        return variant_keep(row, "cadence_only_first_touch_v1") and (
            row.forecast_peak_delta_hours_local is not None and row.forecast_peak_delta_hours_local <= 0
        )
    if variant == "cadence_first_touch_no_reheat_strict":
        return (
            variant_keep(row, "cadence_first_touch_after_forecast_peak")
            and row.forecast_max_above_metar_max_f is not None
            and row.forecast_max_above_metar_max_f <= FORECAST_REMAINING_BUFFER_F
        )
    raise ValueError(variant)


def variant_reasons(row: EnrichedOrder, variant: str) -> list[str]:
    if variant in {"cadence_only_first_touch_v1", "cadence_first_touch_after_forecast_peak", "cadence_first_touch_no_reheat_strict"}:
        reasons = list(row.first_touch_state.get("reasons") or []) if row.profile == "peak_forming_micro" else list(row.reject_reasons)
        if variant in {"cadence_first_touch_after_forecast_peak", "cadence_first_touch_no_reheat_strict"}:
            if row.forecast_peak_delta_hours_local is None or row.forecast_peak_delta_hours_local > 0:
                reasons.append("forecast_peak_still_future")
        if variant == "cadence_first_touch_no_reheat_strict":
            if row.forecast_max_above_metar_max_f is None or row.forecast_max_above_metar_max_f > FORECAST_REMAINING_BUFFER_F:
                reasons.append("forecast_remaining_above_current_max")
        return list(dict.fromkeys(reasons))
    return list(row.reject_reasons)


def summarize(rows: list[EnrichedOrder], variant: str) -> dict[str, Any]:
    matched = [r for r in rows if r.matched]
    settled = [r for r in matched if r.settled]
    kept_matched = [r for r in matched if variant_keep(r, variant)]
    kept_settled = [r for r in kept_matched if r.settled]
    blocked_settled = [r for r in settled if not variant_keep(r, variant)]
    cost = sum(r.cost for r in kept_settled)
    pnl = sum(float(r.pnl or 0.0) for r in kept_settled)
    wins = sum(1 for r in kept_settled if r.win)
    blocked_losses = sum(1 for r in blocked_settled if r.pnl is not None and r.pnl < 0)
    blocked_winners = sum(1 for r in blocked_settled if r.pnl is not None and r.pnl >= 0)
    return {
        "variant": variant,
        "raw_orders": len(rows),
        "matched_orders": len(matched),
        "settled_matched_orders": len(settled),
        "kept_matched_orders": len(kept_matched),
        "kept_settled_orders": len(kept_settled),
        "pending_kept_matched_orders": len([r for r in kept_matched if not r.settled]),
        "blocked_settled_orders": len(blocked_settled),
        "blocked_losing_orders": blocked_losses,
        "blocked_winning_orders": blocked_winners,
        "wins": wins,
        "losses": len(kept_settled) - wins,
        "win_rate": safe_div(wins, len(kept_settled)),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": safe_div(pnl, cost),
        "blocked_pnl_usd": sum(float(r.pnl or 0.0) for r in blocked_settled),
    }


def summarize_by_profile(rows: list[EnrichedOrder], variants: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for profile in sorted({r.profile for r in rows}):
        part = [r for r in rows if r.profile == profile]
        for variant in variants:
            s = summarize(part, variant)
            out.append(
                {
                    "profile": profile,
                    "variant": variant,
                    "settled_matched_orders": s["settled_matched_orders"],
                    "kept_settled_orders": s["kept_settled_orders"],
                    "wins": s["wins"],
                    "losses": s["losses"],
                    "win_rate": s["win_rate"],
                    "cost_usd": s["cost_usd"],
                    "pnl_usd": s["pnl_usd"],
                    "roi": s["roi"],
                }
            )
    return out


def by_date_roi_ci(rows: list[EnrichedOrder], variant: str, n: int = 5000) -> list[float | None]:
    settled = [r for r in rows if r.matched and r.settled and variant_keep(r, variant)]
    dates = sorted({str(r.order.get("target_date")) for r in settled})
    if len(dates) < 2:
        return [None, None]
    daily: dict[str, tuple[float, float]] = {}
    for date in dates:
        part = [r for r in settled if str(r.order.get("target_date")) == date]
        daily[date] = (sum(r.cost for r in part), sum(float(r.pnl or 0.0) for r in part))
    rng = random.Random(20260621)
    vals = []
    for _ in range(n):
        cost = 0.0
        pnl = 0.0
        for _ in dates:
            c, p = daily[rng.choice(dates)]
            cost += c
            pnl += p
        if cost:
            vals.append(pnl / cost)
    vals.sort()
    return [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]


def order_row(row: EnrichedOrder) -> dict[str, Any]:
    order = row.order
    return {
        "created_at_utc": order.get("created_at_utc"),
        "target_date": order.get("target_date"),
        "city": order.get("city"),
        "bracket": order.get("bracket"),
        "profile": row.profile,
        "status": row.order_status,
        "settled": row.settled,
        "win": row.win,
        "cost_usd": row.cost,
        "pnl_usd": row.pnl,
        "cadence_min": row.cadence_min,
        "post_high_gap_min": row.post_high_gap_min,
        "post_high_cycle_required_min": row.post_high_cycle_required_min,
        "forecast_peak_delta_hours_local": row.forecast_peak_delta_hours_local,
        "forecast_max_above_metar_max_f": row.forecast_max_above_metar_max_f,
        "latest_same_bracket": row.latest_same_bracket,
        "reject_reasons": row.reject_reasons,
        "first_touch_state": row.first_touch_state,
    }


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def usd(value: float | None) -> str:
    return "NA" if value is None else f"${value:+.2f}"


def main() -> None:
    rows = load_orders()
    variants = [
        "actual_matched",
        "fade_only_stop_peak_forming",
        "cadence_only_last_max_v0",
        "cadence_only_first_touch_v1",
        "cadence_first_touch_after_forecast_peak",
        "cadence_first_touch_no_reheat_strict",
    ]
    summaries = []
    for variant in variants:
        s = summarize(rows, variant)
        s["date_bootstrap_roi_95"] = by_date_roi_ci(rows, variant)
        summaries.append(s)

    raw_status = {}
    for row in rows:
        raw_status[row.order_status] = raw_status.get(row.order_status, 0) + 1
    profile_status = {}
    for row in rows:
        key = (row.profile, row.order_status)
        profile_status[key] = profile_status.get(key, 0) + 1
    blocked_examples = {
        variant: [
            {**order_row(r), "reject_reasons": variant_reasons(r, variant)}
            for r in rows
            if r.matched and r.settled and not variant_keep(r, variant)
        ]
        for variant in variants
        if variant != "actual_matched"
    }
    pending = [order_row(r) for r in rows if r.matched and not r.settled]
    payload = {
        "scope": "current_yes_split_live_raw_matched_counterfactual",
        "window": {"target_date_start": START_DATE, "target_date_end": END_DATE},
        "data": {
            "order_files": [str(p.relative_to(ROOT)) for p in ORDER_FILES],
            "db": str(DB_PATH.relative_to(ROOT)),
            "settlement_source": "settlement_outcomes city/target_date/bracket",
            "pnl_source": "raw CLOB matched place.makingAmount/takingAmount plus settlement_outcomes final_price",
            "fact_trades_note": "local fact_trades live_real currently maxes at 2026-06-11, so split current-YES fills are not used for PnL",
            "run_stack_note": "run_stack rebuilt fact tables but exited non-zero after FE port 5174 stayed busy",
            "cadence_v0_limitation": "cadence_only uses last-running-max telemetry. Since live running_max_obs_utc is the last obs equal to the max, equal-high plateau refreshes the timestamp and cannot pass this v0 gate.",
        },
        "correct_state_gate": {
            "required_fields": [
                "first_running_max_obs_utc",
                "last_running_max_obs_utc",
                "post_first_high_obs_count",
                "same_running_max_obs_count",
                "higher_after_first_high_count",
                "latest_obs_maps_to_running_max_bracket",
            ],
            "stalled_high_candidate": [
                "post_first_high_obs_count >= 1",
                "higher_after_first_high_count == 0",
                "latest_obs_maps_to_running_max_bracket == true",
                "elapsed_since_first_running_max >= one expected cadence or same_running_max_obs_count >= 2",
            ],
            "corrected_variant": "cadence_only_first_touch_v1",
            "legacy_fail_closed_variant": "cadence_only_last_max_v0",
        },
        "parameters": {
            "min_post_high_cycle_fraction": MIN_POST_HIGH_CYCLE_FRACTION,
            "min_post_high_gap_min": MIN_POST_HIGH_GAP_MIN,
            "forecast_remaining_buffer_f": FORECAST_REMAINING_BUFFER_F,
        },
        "raw_status_counts": raw_status,
        "profile_status_counts": [
            {"profile": profile, "status": status, "orders": count}
            for (profile, status), count in sorted(profile_status.items())
        ],
        "summaries": summaries,
        "profile_summaries": summarize_by_profile(rows, variants),
        "pending_matched_unsettled": pending,
        "blocked_examples": blocked_examples,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Current-YES cadence state live counterfactual v0",
        "",
        "Target metric: 对 split current-YES live raw matched orders 做反事实过滤：如果把旧 `peak_forming`/fade 时机换成 cadence-aware 状态层，最近几天成绩会变成什么。",
        "",
        "## Data Snapshot",
        "",
        f"- Window: target_date `{START_DATE}`..`{END_DATE}`.",
        f"- Order files: {', '.join(p.name for p in ORDER_FILES)}.",
        "- PnL grain: raw matched order; cost/shares use CLOB immediate `place.makingAmount/takingAmount`.",
        "- Settlement: `settlement_outcomes` by city/target_date/bracket.",
        "- `fact_trades` note: local canonical live_real fills currently stop at 2026-06-11, so this report does not use `fact_trades` for the split current-YES PnL.",
        "- `run_stack.sh` note: fact tables rebuilt, then script exited non-zero only because FE port 5174 stayed busy.",
        "- Important limitation: `cadence_only` is a fail-closed lower-bound filter using last-running-max telemetry; it cannot detect equal-high plateau because live `running_max_obs_utc` is the last observation equal to the max, not the first touch.",
        "",
        "## Funnel",
        "",
        f"- Raw orders: {len(rows)}.",
        f"- Status counts: {raw_status}.",
        f"- Matched but unsettled: {len(pending)}; these are excluded from ROI and listed separately.",
        "",
        "## Variants",
        "",
        "| variant | matched | settled | kept settled | wins-losses | win | ROI | PnL | blocked settled | blocked L/W | date bootstrap 95% ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        ci = s["date_bootstrap_roi_95"]
        ci_text = "NA" if ci[0] is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
        lines.append(
            f"| `{s['variant']}` | {s['matched_orders']} | {s['settled_matched_orders']} | "
            f"{s['kept_settled_orders']} | {s['wins']}-{s['losses']} | {pct(s['win_rate'])} | "
            f"{pct(s['roi'])} | {usd(s['pnl_usd'])} | {s['blocked_settled_orders']} | "
            f"{s['blocked_losing_orders']}/{s['blocked_winning_orders']} | {ci_text} |"
        )
    lines += [
        "",
        "## By Profile",
        "",
        "| profile | variant | settled | kept settled | wins-losses | win | ROI | PnL |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in payload["profile_summaries"]:
        lines.append(
            f"| `{s['profile']}` | `{s['variant']}` | {s['settled_matched_orders']} | "
            f"{s['kept_settled_orders']} | {s['wins']}-{s['losses']} | {pct(s['win_rate'])} | "
            f"{pct(s['roi'])} | {usd(s['pnl_usd'])} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- `fade_only_stop_peak_forming` answers the blunt stop-loss question: what happens if peak-forming real orders are disabled and fade remains.",
        "- `cadence_only_last_max_v0` is the old fail-closed lower-bound check: `minutes_since_running_max - obs_age_min >= max(20m, 0.75 * cadence_min)`.",
        "- `cadence_only_first_touch_v1` is the corrected state gate. It reconstructs visible observations from prior paper snapshots and requires first-touch plateau evidence.",
        "- `cadence_first_touch_after_forecast_peak` additionally requires the decision to be at/after the forecast peak.",
        f"- `cadence_first_touch_no_reheat_strict` also requires forecast remaining max not to exceed current METAR max by more than {FORECAST_REMAINING_BUFFER_F}F.",
        "- Observation history is reconstructed point-in-time from snapshots already written before each order's source snapshot.",
        "",
        "## Corrected Cadence Semantics",
        "",
        "The v0 `cadence_only` result should not be read as the final state-machine backtest. It answers a narrower fail-closed question: if we only trust orders where the latest observation is after the last max timestamp, what remains?",
        "",
        "That is too strict for plateau detection. If a station reports the same high twice, live `running_max_obs_utc` is refreshed to the second high, so `minutes_since_running_max - obs_age_min` stays near zero even though a full cadence may have passed since the first high.",
        "",
        "The correct state gate needs these fields:",
        "",
        "- `first_running_max_obs_utc`",
        "- `last_running_max_obs_utc`",
        "- `post_first_high_obs_count`",
        "- `same_running_max_obs_count` / `plateau_obs_count`",
        "- `higher_after_first_high_count`",
        "- `latest_obs_maps_to_running_max_bracket`",
        "",
        "A real `stalled_high_candidate` should mean:",
        "",
        "```text",
        "post_first_high_obs_count >= 1",
        "+ higher_after_first_high_count == 0",
        "+ latest_obs_maps_to_running_max_bracket",
        "+ (elapsed_since_first_running_max >= one cadence OR same_running_max_obs_count >= 2)",
        "```",
        "",
        "The corrected v1 replay reconstructs these fields from historical snapshots for this report. The live runner should still log them directly so future telemetry does not depend on replay reconstruction.",
        "",
        "## Blocked Settled Orders",
        "",
    ]
    for variant in variants:
        if variant == "actual_matched":
            continue
        examples = blocked_examples.get(variant, [])
        lines.append(f"### {variant}")
        if not examples:
            lines.append("")
            lines.append("- None.")
            lines.append("")
            continue
        lines.append("")
        lines.append("| created | date | city | bracket | profile | win | PnL | reasons |")
        lines.append("|---|---|---|---|---|---:|---:|---|")
        for r in examples:
            lines.append(
                f"| {r['created_at_utc']} | {r['target_date']} | {r['city']} | {r['bracket']} | "
                f"{r['profile']} | {r['win']} | {usd(r['pnl_usd'])} | {', '.join(r['reject_reasons'])} |"
            )
        lines.append("")
    if pending:
        lines += [
            "## Pending Matched Orders",
            "",
            "| created | date | city | bracket | profile | kept by cadence_no_reheat_strict | reasons |",
            "|---|---|---|---|---|---:|---|",
        ]
        strict_variant = "cadence_first_touch_no_reheat_strict"
        for r, e in [(order_row(x), x) for x in rows if x.matched and not x.settled]:
            lines.append(
                f"| {r['created_at_utc']} | {r['target_date']} | {r['city']} | {r['bracket']} | {r['profile']} | "
                f"{variant_keep(e, strict_variant)} | {', '.join(variant_reasons(e, strict_variant))} |"
            )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "summaries": summaries}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
