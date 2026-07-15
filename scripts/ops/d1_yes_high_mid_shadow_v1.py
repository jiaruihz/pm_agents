#!/usr/bin/env python3
"""Zero-notional shadow runner for the d1_yes_high_mid_v1 strategy.

Strategy (frozen v1, see docs/analysis/2026-07/2026-07-15-market-calibration-curve-v1.md):
    When the market prices "final max lands exactly one bracket above the current
    running-max bracket" (the d1 YES) at mid >= 0.80, buy d1 YES as taker at
    1 - d1_no_bid, hold to settlement.  One entry per city-date (first qualifying
    poll).  No city / hour / weather filter in v1.

This runner never submits an order.  It reads the live observation feed and the
live orderbook snapshot each cycle, reconstructs the current/d1 ladder anchoring
with the SAME semantics as the offline factory (imported below), records every
would-be entry to a journal, and backfills the settlement label from
settlement_outcomes when available.

Two accounting tracks are written per event:
  * promotion track: first qualifying poll per (city, target_date) -> promotion
    evidence, matches the backtest denominator.
  * telemetry track: every qualifying poll -> quantifies how much earlier /
    cheaper a per-poll trigger would be.  Never mixed into the promotion ROI.

Registered in the canonical strategy lineage as instance
`d1_yes_high_mid_shadow_v1` (execution_mode=zero_notional_shadow).
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse the exact bracket / running-max anchoring semantics used by the backtest
# factory so the live d1 identity cannot drift from the researched denominator.
# scripts/ is not a package, so load the factory module by file path.
import importlib.util as _ilu  # noqa: E402

_FACTORY_PATH = ROOT / "scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py"
_spec = _ilu.spec_from_file_location("reheat_feature_factory_v1", _FACTORY_PATH)
_factory = _ilu.module_from_spec(_spec)
sys.modules["reheat_feature_factory_v1"] = _factory  # dataclass introspection needs this
_spec.loader.exec_module(_factory)
Bracket = _factory.Bracket
bracket_contains = _factory.bracket_contains
parse_bracket = _factory.parse_bracket
round_half_up = _factory.round_half_up

STRATEGY_ID = "d1_yes_high_mid_shadow_v1"
RULE_ID = "d1_yes_mid_ge_0p80_first_per_city_date_taker_v1"
SOURCE_REPORT = "docs/analysis/2026-07/2026-07-15-market-calibration-curve-v1.md"

DB_DEFAULT = ROOT / "runtime/weather.db"
RUNTIME_ROOT = Path(
    os.environ.get(
        "WEATHER_DATA_FEED_RUNTIME_ROOT",
        str(Path.home() / "projects/weather_data_feed_service_runtime"),
    )
)
OBS_DEFAULT = RUNTIME_ROOT / "output/observations/latest.json"
# Prefer the dedicated full-ladder capture (all cities); fall back to the
# targeted feed (~5 cities) when the full-ladder loop is not running.  The
# runner picks the freshest snapshot file across these dirs each cycle.
ORDERBOOK_DIRS_DEFAULT = [
    RUNTIME_ROOT / "full_ladder_output/orderbook_snapshots",
    RUNTIME_ROOT / "targeted_output/orderbook_snapshots",
]
ORDERBOOK_DEFAULT = ORDERBOOK_DIRS_DEFAULT[0]

RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/d1_yes_high_mid_shadow_v1"
JOURNAL_OUT = RUNTIME_DIR / "shadow_events.jsonl"
POSITIONS_OUT = RUNTIME_DIR / "open_positions.json"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
SUMMARY_HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"

MID_THRESHOLD = 0.80
# Parity: the backtest applied NO obs-age filter (it included every trigger; the
# observed max obs age was ~60 min).  We therefore do NOT gate on the backtest
# freshness band; we only reject pathologically stale observations that could
# only happen live (a stalled obs feed), which the backtest never contained.
# obs_age is always recorded; `obs_age_in_backtest_band` flags <= 61 min.
PATHOLOGICAL_OBS_AGE_MIN = 120.0
BACKTEST_OBS_AGE_BAND_MIN = 61.0
MAX_BOOK_AGE_MIN = 60.0
FULL_LADDER_MIN_CITIES = 36
# v1.1 pre-registered secondary guards (recorded, not gating v1)
V11_MAX_ASK = 0.95
V11_MAX_REMAINING_HEAT = 1.3


def now_utc_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> str:
    return now_utc_dt().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def fee(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 6)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--observation-cache", default=str(OBS_DEFAULT))
    parser.add_argument("--orderbook-dir", action="append", default=None,
                        help="orderbook snapshot dir; repeatable. Default: full-ladder then targeted.")
    parser.add_argument("--mid-threshold", type=float, default=MID_THRESHOLD)
    parser.add_argument("--max-obs-age-min", type=float, default=PATHOLOGICAL_OBS_AGE_MIN,
                        help="reject only pathologically stale obs (live-only failure); "
                             "the backtest applied no obs-age filter")
    parser.add_argument("--max-book-age-min", type=float, default=MAX_BOOK_AGE_MIN,
                        help="reject missing/stale per-token quotes (data-validity guard)")
    parser.add_argument("--interval-seconds", type=float, default=600.0)
    parser.add_argument("--dry-run", action="store_true", help="do not write journal/positions")
    return parser.parse_args()


# --------------------------------------------------------------------------- #
# live inputs
# --------------------------------------------------------------------------- #
def load_observations(path: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Return {city: record} keyed by city for the freshest record per city."""
    if not path.exists():
        return {}, None
    doc = json.loads(path.read_text())
    generated = doc.get("generated_at_utc")
    out: dict[str, dict[str, Any]] = {}
    for rec in doc.get("records", []):
        city = rec.get("city")
        if not city or rec.get("status") in {"error"} or rec.get("error"):
            continue
        out[city] = rec
    return out, generated


def completion_marker(orderbook_file: Path) -> Path | None:
    match = re.fullmatch(r"orderbook_snapshot_(\d{8}_\d{4})\.jsonl(?:\.gz)?", orderbook_file.name)
    if match is None or len(orderbook_file.parents) < 3:
        return None
    return orderbook_file.parents[2] / "paper_snapshots" / f"snapshot_{match.group(1)}.json"


def is_full_ladder_dir(orderbook_dir: Path) -> bool:
    return orderbook_dir.parent.name == "full_ladder_output"


def _latest_in_dir(orderbook_dir: Path, *, require_complete: bool = False) -> Path | None:
    if not orderbook_dir.exists():
        return None
    candidates: list[Path] = []
    for date_dir in sorted(orderbook_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in date_dir.iterdir():
            if f.name.startswith("orderbook_snapshot_") and (
                f.suffix in {".gz", ".jsonl"} or f.name.endswith(".jsonl.gz")
            ):
                if require_complete:
                    marker = completion_marker(f)
                    if marker is None or not marker.exists():
                        continue
                candidates.append(f)
        if candidates:
            break
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def latest_orderbook_file(orderbook_dirs: list[Path], prefer_max_age_sec: float = 2400.0) -> Path | None:
    """Priority-ordered pick: use the first dir's latest file if it is fresh
    (within prefer_max_age_sec, default 40 min = 2x the full-ladder cadence);
    otherwise fall through to the next dir.  This keeps the broad full-ladder
    dir authoritative when its loop is healthy, and falls back to the narrow
    targeted feed only when full-ladder capture is stale/down -- rather than
    letting a more recent narrow snapshot mask the broad one.
    """
    now = time.time()
    for d in orderbook_dirs:
        f = _latest_in_dir(d, require_complete=is_full_ladder_dir(d))
        if f is None:
            continue
        if now - f.stat().st_mtime <= prefer_max_age_sec:
            return f
    return None


def coverage_note(orderbook_file: Path | None, cities_scanned: int) -> str:
    if orderbook_file is not None and is_full_ladder_dir(orderbook_file.parent.parent):
        return "full_ladder" if cities_scanned >= FULL_LADDER_MIN_CITIES else "full_ladder_partial"
    return "narrow_targeted_coverage"


def best_level(levels: list[dict[str, Any]] | None, side: str) -> tuple[float, float]:
    """Best price/size. asks -> lowest price; bids -> highest price."""
    if not levels:
        return math.nan, math.nan
    parsed = [(to_float(l.get("price")), to_float(l.get("size"))) for l in levels]
    parsed = [(p, s) for p, s in parsed if math.isfinite(p)]
    if not parsed:
        return math.nan, math.nan
    chosen = min(parsed, key=lambda x: x[0]) if side == "asks" else max(parsed, key=lambda x: x[0])
    return chosen


def depth_within(levels: list[dict[str, Any]] | None, ref_price: float, side: str, band: float = 0.05) -> float:
    if not levels or not math.isfinite(ref_price):
        return math.nan
    total = 0.0
    for l in levels:
        p, s = to_float(l.get("price")), to_float(l.get("size"))
        if not (math.isfinite(p) and math.isfinite(s)):
            continue
        if side == "bids" and p >= ref_price - band:
            total += s
        elif side == "asks" and p <= ref_price + band:
            total += s
    return total


def load_ladder(path: Path) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Return {(city, event_date): {bracket: {'yes': quote, 'no': quote}}}."""
    opener = gzip.open if path.name.endswith(".gz") else open
    ladder: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            city = row.get("city")
            event_date = row.get("event_date") or row.get("market_local_date")
            bracket = row.get("bracket")
            outcome = str(row.get("outcome") or "").lower()
            if not (city and event_date and bracket and outcome in {"yes", "no"}):
                continue
            raw = row.get("raw") or {}
            ask_p, ask_s = best_level(raw.get("asks"), "asks")
            bid_p, bid_s = best_level(raw.get("bids"), "bids")
            quote = {
                "ask": ask_p,
                "ask_size": ask_s,
                "bid": bid_p,
                "bid_size": bid_s,
                "spread": (ask_p - bid_p) if (math.isfinite(ask_p) and math.isfinite(bid_p)) else math.nan,
                "depth_bid_5c": depth_within(raw.get("bids"), bid_p, "bids"),
                "depth_ask_5c": depth_within(raw.get("asks"), ask_p, "asks"),
                "fetched_at_utc": row.get("fetched_at_utc"),
                "condition_id": row.get("condition_id"),
                "token_id": row.get("token_id"),
            }
            key = (city, str(event_date))
            ladder.setdefault(key, {}).setdefault(bracket, {})[outcome] = quote
    return ladder


# --------------------------------------------------------------------------- #
# ladder anchoring (mirrors factory add_state_siblings / tail_distance)
# --------------------------------------------------------------------------- #
def running_native(rec: dict[str, Any]) -> tuple[float, str]:
    unit = str(rec.get("unit") or "").upper()
    rmax_c = to_float(rec.get("running_max_c"))
    if unit == "F":
        return (rmax_c * 9.0 / 5.0 + 32.0 if math.isfinite(rmax_c) else math.nan), "F"
    return rmax_c, "C"


def tail_distance(bracket_low: float, running: float, unit: str) -> int | None:
    if not (math.isfinite(bracket_low) and math.isfinite(running)):
        return None
    if bracket_low <= running:
        return None
    if unit == "F":
        return int(math.ceil((bracket_low - running) / 2.0))
    return int(round(bracket_low - running))


def find_current_and_d1(
    brackets: dict[str, dict[str, Any]], running: float, unit: str
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Return (current_bracket, d1_bracket, d1_no_quote)."""
    if not math.isfinite(running):
        return None, None, None
    running_value = float(round_half_up(running))
    current_bracket = None
    # d1 tie-break mirrors the factory: among distance==1 NO brackets, keep the
    # highest NO ask (factory sorts ask desc then drop_duplicates keeps first).
    d1_candidates: list[tuple[float, str, dict[str, Any]]] = []
    for raw_bracket, sides in brackets.items():
        b = parse_bracket(raw_bracket)
        if b is None:
            continue
        if "yes" in sides and bracket_contains(b, running_value):
            # most specific (ranged) bracket wins, matching factory specificity sort
            if current_bracket is None or (b.high is not None):
                current_bracket = raw_bracket
        if b.low is not None and "no" in sides:
            dist = tail_distance(float(b.low), running, unit)
            if dist == 1:
                no_ask = to_float(sides["no"].get("ask"))
                d1_candidates.append((no_ask if math.isfinite(no_ask) else -1.0, raw_bracket, sides["no"]))
    if not d1_candidates:
        return current_bracket, None, None
    d1_candidates.sort(key=lambda x: x[0], reverse=True)
    _, d1_bracket, d1_no_quote = d1_candidates[0]
    return current_bracket, d1_bracket, d1_no_quote


# --------------------------------------------------------------------------- #
# settlement backfill
# --------------------------------------------------------------------------- #
def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def settled_bracket(conn: sqlite3.Connection, city: str, target_date: str) -> str | None:
    rows = conn.execute(
        "SELECT bracket, final_price FROM settlement_outcomes "
        "WHERE city=? AND target_date=? AND settlement_status='settled'",
        (city, target_date),
    ).fetchall()
    winners = [r["bracket"] for r in rows if to_float(r["final_price"]) >= 0.99]
    return winners[0] if len(winners) == 1 else None


# --------------------------------------------------------------------------- #
# main cycle
# --------------------------------------------------------------------------- #
def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    cycle_dt = now_utc_dt()
    cycle_ts = cycle_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    obs_path = Path(args.observation_cache)
    ob_dirs = [Path(d) for d in (args.orderbook_dir or [str(p) for p in ORDERBOOK_DIRS_DEFAULT])]
    observations, obs_generated = load_observations(obs_path)
    ob_file = latest_orderbook_file(ob_dirs)
    ladder = load_ladder(ob_file) if ob_file else {}

    positions: dict[str, Any] = {}
    if POSITIONS_OUT.exists():
        positions = json.loads(POSITIONS_OUT.read_text())

    triggers_this_cycle = 0
    new_positions = 0
    cities_scanned = 0
    invalid_obs_age_rows = 0
    invalid_book_age_rows = 0
    events: list[dict[str, Any]] = []

    for city, rec in observations.items():
        target_date = rec.get("target_date")
        if not target_date:
            continue
        key = (city, str(target_date))
        if key not in ladder:
            continue
        cities_scanned += 1
        running, unit = running_native(rec)
        current_bracket, d1_bracket, d1_no = find_current_and_d1(ladder[key], running, unit)
        if d1_bracket is None or d1_no is None:
            continue
        no_bid = to_float(d1_no.get("bid"))
        no_ask = to_float(d1_no.get("ask"))
        if not math.isfinite(no_bid) or not math.isfinite(no_ask):
            continue
        d1_yes_ask = 1.0 - no_bid          # taker cost for YES
        d1_yes_mid = 1.0 - (no_ask + no_bid) / 2.0
        obs_age = to_float(rec.get("age_min"))
        book_ts = parse_utc(d1_no.get("fetched_at_utc"))
        book_age = (cycle_dt - book_ts).total_seconds() / 60.0 if book_ts else math.nan

        # Trigger CONDITION is identical to the backtest: d1_yes_mid >= 0.80.
        # Missing/pathologically stale observations and missing/stale quotes are
        # data-invalid live states, not strategy filters, so they fail closed.
        obs_valid = math.isfinite(obs_age) and obs_age <= args.max_obs_age_min
        book_valid = math.isfinite(book_age) and 0.0 <= book_age <= args.max_book_age_min
        if not obs_valid:
            invalid_obs_age_rows += 1
        if not book_valid:
            invalid_book_age_rows += 1
        triggered = (
            math.isfinite(d1_yes_mid)
            and d1_yes_mid >= args.mid_threshold
            and obs_valid
            and book_valid
        )
        if not triggered:
            continue
        obs_age_in_backtest_band = obs_age <= BACKTEST_OBS_AGE_BAND_MIN
        triggers_this_cycle += 1

        pos_key = f"{city}|{target_date}"
        is_first = pos_key not in positions
        event = {
            "cycle_ts_utc": cycle_ts,
            "strategy_id": STRATEGY_ID,
            "rule_id": RULE_ID,
            "track": "promotion" if is_first else "telemetry",
            "is_first_per_city_date": is_first,
            "city": city,
            "target_date": target_date,
            "unit": unit,
            "running_max_native": round(running, 3) if math.isfinite(running) else None,
            "running_value": int(round_half_up(running)) if math.isfinite(running) else None,
            "current_bracket": current_bracket,
            "d1_bracket": d1_bracket,
            "d1_yes_ask": round(d1_yes_ask, 4),
            "d1_yes_mid": round(d1_yes_mid, 4),
            "d1_no_bid": round(no_bid, 4),
            "d1_no_ask": round(no_ask, 4),
            "d1_no_bid_size": d1_no.get("bid_size"),
            "d1_no_spread": round(d1_no.get("spread"), 4) if math.isfinite(to_float(d1_no.get("spread"))) else None,
            "d1_no_depth_bid_5c": d1_no.get("depth_bid_5c"),
            "entry_cost_with_fee": round(d1_yes_ask + fee(d1_yes_ask), 6),
            "obs_age_min": round(obs_age, 2) if math.isfinite(obs_age) else None,
            "obs_age_in_backtest_band": bool(obs_age_in_backtest_band),
            "book_age_min": round(book_age, 2) if math.isfinite(book_age) else None,
            "minutes_since_running_max": rec.get("minutes_since_running_max"),
            "d_tmpf_1h": rec.get("d_tmpf_1h"),
            "d_tmpf_3h": rec.get("d_tmpf_3h"),
            "sky_code_now": rec.get("sky_code_now"),
            "wind_speed_kt": rec.get("wind_speed_kt"),
            "relative_humidity_pct": rec.get("relative_humidity_pct"),
            "orderbook_file": rel(ob_file) if ob_file else None,
            "orderbook_snapshot_complete": bool(
                ob_file
                and is_full_ladder_dir(ob_file.parent.parent)
                and completion_marker(ob_file)
                and completion_marker(ob_file).exists()
            ),
            "book_fetched_at_utc": d1_no.get("fetched_at_utc"),
            "obs_generated_at_utc": obs_generated,
            "condition_id": d1_no.get("condition_id"),
            # v1.1 pre-registered guards (recorded; remaining_heat requires forecast join, null here)
            "v11_ask_ok": bool(d1_yes_ask <= V11_MAX_ASK),
            "v11_remaining_heat_ok": None,
            "settled_bracket": None,
            "win": None,
            "pnl_at_settlement": None,
        }
        events.append(event)
        if is_first and not args.dry_run:
            positions[pos_key] = {
                "city": city,
                "target_date": target_date,
                "d1_bracket": d1_bracket,
                "d1_yes_ask": round(d1_yes_ask, 4),
                "entry_cost_with_fee": round(d1_yes_ask + fee(d1_yes_ask), 6),
                "entry_cycle_ts_utc": cycle_ts,
                "book_fetched_at_utc": d1_no.get("fetched_at_utc"),
                "book_age_min": round(book_age, 2),
                "orderbook_file": rel(ob_file) if ob_file else None,
                "settled": False,
            }
            new_positions += 1

    # settlement backfill on open positions
    settled_now = 0
    try:
        conn = connect_ro(Path(args.db))
    except sqlite3.OperationalError:
        conn = None
    if conn is not None:
        for pos_key, pos in positions.items():
            if pos.get("settled"):
                continue
            win_bracket = settled_bracket(conn, pos["city"], pos["target_date"])
            if win_bracket is None:
                continue
            win = str(win_bracket) == str(pos["d1_bracket"])
            pos["settled"] = True
            pos["settled_bracket"] = win_bracket
            pos["win"] = win
            pos["pnl_at_settlement"] = round((1.0 if win else 0.0) - pos["entry_cost_with_fee"], 6)
            settled_now += 1
            if not args.dry_run:
                append_jsonl(
                    JOURNAL_OUT,
                    {
                        "cycle_ts_utc": cycle_ts,
                        "strategy_id": STRATEGY_ID,
                        "event_type": "settlement",
                        "city": pos["city"],
                        "target_date": pos["target_date"],
                        "d1_bracket": pos["d1_bracket"],
                        "settled_bracket": win_bracket,
                        "win": win,
                        "entry_cost_with_fee": pos["entry_cost_with_fee"],
                        "pnl_at_settlement": pos["pnl_at_settlement"],
                    },
                )
        conn.close()

    if not args.dry_run:
        for event in events:
            append_jsonl(JOURNAL_OUT, event)
        write_json(POSITIONS_OUT, positions)

    settled_positions = [p for p in positions.values() if p.get("settled")]
    settled_wins = sum(1 for p in settled_positions if p.get("win"))
    settled_cost = sum(p["entry_cost_with_fee"] for p in settled_positions)
    settled_pnl = sum(p.get("pnl_at_settlement") or 0.0 for p in settled_positions)
    snapshot_age_min = (
        max(0.0, (cycle_dt.timestamp() - ob_file.stat().st_mtime) / 60.0)
        if ob_file is not None
        else None
    )
    target_dates = sorted({str(rec.get("target_date")) for rec in observations.values() if rec.get("target_date")})
    summary = {
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "source_report": SOURCE_REPORT,
        "generated_at_utc": cycle_ts,
        "cycle_ts_utc": cycle_ts,
        "obs_generated_at_utc": obs_generated,
        "orderbook_file": rel(ob_file) if ob_file else None,
        "orderbook_complete_marker": rel(completion_marker(ob_file)) if ob_file and completion_marker(ob_file) else None,
        "snapshot_age_min": round(snapshot_age_min, 2) if snapshot_age_min is not None else None,
        "target_dates": target_dates,
        "latest_target_date": max(target_dates) if target_dates else None,
        "cities_with_obs": len(observations),
        "book_city_date_pairs": len(ladder),
        "cities_scanned_with_book": cities_scanned,
        "invalid_obs_age_rows": invalid_obs_age_rows,
        "invalid_book_age_rows": invalid_book_age_rows,
        "triggers_this_cycle": triggers_this_cycle,
        "candidate_rows": triggers_this_cycle,
        "new_positions_this_cycle": new_positions,
        "selected_rows_this_cycle": new_positions,
        "rows_written_this_cycle": len(events),
        "live_requested": False,
        "live_enabled": False,
        "open_positions_total": len(positions),
        "settled_positions_total": len(settled_positions),
        "settled_wins": settled_wins,
        "settled_cost_with_fee": round(settled_cost, 6),
        "settled_pnl": round(settled_pnl, 6),
        "settled_roi": round(settled_pnl / settled_cost, 6) if settled_cost > 0 else None,
        "settled_now": settled_now,
        "coverage_note": coverage_note(ob_file, cities_scanned),
    }
    if not args.dry_run:
        write_json(SUMMARY_OUT, summary)
        append_jsonl(SUMMARY_HISTORY_OUT, summary)
    return summary


def main() -> int:
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if args.command == "run":
        summary = run_cycle(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    # loop
    while True:
        try:
            summary = run_cycle(args)
            print(
                f"[{summary['cycle_ts_utc']}] scanned={summary['cities_scanned_with_book']} "
                f"triggers={summary['triggers_this_cycle']} new_pos={summary['new_positions_this_cycle']} "
                f"open={summary['open_positions_total']} settled={summary['settled_positions_total']} "
                f"roi={summary['settled_roi']} coverage={summary['coverage_note']}",
                flush=True,
            )
        except Exception as exc:  # keep the loop alive; surface the error
            print(f"[{now_utc()}] cycle_error: {exc!r}", flush=True)
        time.sleep(max(30.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
