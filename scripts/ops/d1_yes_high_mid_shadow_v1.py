#!/usr/bin/env python3
"""Shadow/live runner for the d1_yes_high_mid_v1 strategy.

Strategy (frozen v1, see docs/analysis/2026-07/2026-07-15-market-calibration-curve-v1.md):
    When the market prices "final max lands exactly one bracket above the current
    running-max bracket" (the d1 YES) at mid >= 0.80, express the first signal
    as 5 shares taker plus a separate 5-share post-only maker child, then hold
    fills to settlement.  One entry per city-date (first qualifying poll).  No
    city / hour / weather filter in v1.

The default CLI remains zero-notional shadow.  With ``--live --confirm-live`` it
routes Taipei to shadow and submits split BUY YES taker/maker plans for other
cities only when current -> d1 is an unambiguous adjacent bounded bracket and
the directly observed YES ask has sufficient taker depth.  Open-upper ``X+``
and invalid/missing current-bracket cases always remain shadow.

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
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
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

from src.strategies.weather_edge_v1.runtime import order_runtime  # noqa: E402

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
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
LIVE_OUT = RUNTIME_DIR / "live_orders.jsonl"

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
LIVE_SHADOW_CITIES = {"Taipei"}
ACTIVE_ORDER_STATUSES = {"submitted", "simulated_open"}
MAX_YES_PARITY_GAP = 0.011


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
    parser.add_argument("--strategy-instance", default=STRATEGY_ID)
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DIR))
    parser.add_argument("--shares", type=float, default=5.0,
                        help="taker child shares (legacy flag retained for compatibility)")
    parser.add_argument("--maker-shares", type=float, default=5.0,
                        help="post-only maker child shares; set 0 to disable the maker child")
    parser.add_argument("--max-orders-per-day", type=int, default=10)
    parser.add_argument("--max-daily-cost-usd", type=float, default=50.0)
    parser.add_argument("--order-ttl-min", type=float, default=45.0)
    parser.add_argument("--live", action="store_true", help="submit eligible non-Taipei plans")
    parser.add_argument("--confirm-live", action="store_true", help="required with --live")
    parser.add_argument("--dry-run", action="store_true", help="do not write journal/positions")
    return parser.parse_args()


def configure_runtime(args: argparse.Namespace) -> None:
    global STRATEGY_ID, RUNTIME_DIR, JOURNAL_OUT, POSITIONS_OUT, SUMMARY_OUT
    global SUMMARY_HISTORY_OUT, PLAN_OUT, PAPER_OUT, LIVE_OUT
    STRATEGY_ID = str(args.strategy_instance)
    RUNTIME_DIR = Path(args.runtime_dir)
    JOURNAL_OUT = RUNTIME_DIR / "shadow_events.jsonl"
    POSITIONS_OUT = RUNTIME_DIR / "open_positions.json"
    SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
    SUMMARY_HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
    PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
    PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
    LIVE_OUT = RUNTIME_DIR / "live_orders.jsonl"


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


def coverage_note(orderbook_file: Path | None, book_cities_for_target_dates: int) -> str:
    if orderbook_file is not None and is_full_ladder_dir(orderbook_file.parent.parent):
        return (
            "full_ladder"
            if book_cities_for_target_dates >= FULL_LADDER_MIN_CITIES
            else "full_ladder_partial"
        )
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
                "market_id": row.get("market_id"),
                "event_slug": row.get("event_slug"),
                "question": row.get("question"),
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


def find_current_and_d1(
    brackets: dict[str, dict[str, Any]], running: float, unit: str
) -> tuple[str | None, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Return the rounded-running bracket and its immediate higher sibling.

    The old raw-distance implementation classified a Fahrenheit running max of
    93.92 as d1=94-95 even though settlement rounds it to 94 and therefore
    94-95 is the current bracket.  Anchor current first, then walk the actual
    market ladder.  A missing intermediate bracket fails closed instead of
    silently turning d2+ into d1.
    """
    if not math.isfinite(running):
        return None, None, None, None
    running_value = float(round_half_up(running))
    parsed: list[tuple[Bracket, str, dict[str, Any]]] = []
    for raw_bracket, sides in brackets.items():
        b = parse_bracket(raw_bracket)
        if b is None:
            continue
        parsed.append((b, raw_bracket, sides))

    current_candidates = [item for item in parsed if "yes" in item[2] and bracket_contains(item[0], running_value)]
    if not current_candidates:
        return None, None, None, None

    def width(item: tuple[Bracket, str, dict[str, Any]]) -> float:
        b = item[0]
        return (b.high - b.low) if b.low is not None and b.high is not None else math.inf

    current_b, current_bracket, _ = min(current_candidates, key=width)
    if current_b.high is None:
        return current_bracket, None, None, None
    higher = [
        item for item in parsed
        if item[0].low is not None
        and float(item[0].low) > float(current_b.high)
        and "yes" in item[2]
        and "no" in item[2]
    ]
    if not higher:
        return current_bracket, None, None, None
    d1_b, d1_bracket, d1_sides = min(higher, key=lambda item: float(item[0].low))
    if float(d1_b.low) - float(current_b.high) > 1.000001:
        return current_bracket, None, None, None
    return current_bracket, d1_bracket, d1_sides["no"], d1_sides["yes"]


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


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def live_order_files() -> list[Path]:
    root = ROOT / "runtime/weather_edge_v1"
    paths = set(root.glob("*/live_orders.jsonl"))
    paths.update(root.glob("live/*orders.jsonl"))
    paths.add(LIVE_OUT)
    return sorted(paths)


def live_guard_reason(event: dict[str, Any]) -> str:
    city = str(event.get("city") or "")
    target_date = str(event.get("target_date") or "")
    token_id = str(event.get("d1_yes_token_id") or "")
    condition_id = str(event.get("condition_id") or "")
    bracket = str(event.get("d1_bracket") or "")
    for path in live_order_files():
        for row in order_runtime.read_jsonl(path):
            if str(row.get("status") or "") not in ACTIVE_ORDER_STATUSES:
                continue
            if str(row.get("target_date") or "") != target_date:
                continue
            exp_city = str(row.get("city") or "")
            exp_token = str(row.get("token_id") or "")
            exp_condition = str(row.get("condition_id") or row.get("market_id") or "")
            exp_bracket = str(row.get("bracket") or "")
            exp_side = str(row.get("signal_side") or "").upper()
            if token_id and exp_token == token_id:
                return f"same_token_active:{rel(path)}"
            same_market = exp_city == city and (
                (condition_id and exp_condition == condition_id)
                or (bracket and exp_bracket == bracket)
            )
            if same_market and exp_side and exp_side != "BUY_YES":
                return f"same_market_opposite_side:{rel(path)}"
            if exp_city == city and exp_side == "BUY_NO":
                return f"city_day_existing_buy_no:{rel(path)}"
    return ""


def live_daily_usage(cycle_dt: datetime) -> tuple[int, float]:
    bj_day = cycle_dt.astimezone(timezone(timedelta(hours=8))).date()
    count = 0
    cost = 0.0
    for row in order_runtime.read_jsonl(LIVE_OUT):
        if str(row.get("status") or "") != "submitted":
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or created.astimezone(timezone(timedelta(hours=8))).date() != bj_day:
            continue
        count += 1
        cost += to_float(row.get("posted_notional"), to_float(row.get("notional"), 0.0))
    return count, cost


def successful_live_orders(path: Path | None = None) -> list[dict[str, Any]]:
    """Return durable successful submissions, including immediate matches."""
    successful: list[dict[str, Any]] = []
    for row in order_runtime.read_jsonl(path or LIVE_OUT):
        if str(row.get("status") or "") != "submitted":
            continue
        response = row.get("exchange_response") or {}
        place = response.get("place") if isinstance(response, dict) else {}
        if not isinstance(place, dict) or place.get("success") is False:
            continue
        if not str(place.get("orderID") or ""):
            continue
        successful.append(row)
    return successful


def matching_live_order(
    *,
    city: str,
    target_date: str,
    bracket: str,
    rows: list[dict[str, Any]],
    child_roles: set[str] | None = None,
) -> dict[str, Any] | None:
    matches = [
        row
        for row in rows
        if str(row.get("city") or "") == city
        and str(row.get("target_date") or "") == target_date
        and (not bracket or str(row.get("bracket") or "") == bracket)
        and str(row.get("signal_side") or "").upper() == "BUY_YES"
        and (
            child_roles is None
            or str(row.get("child_order_role") or "single") in child_roles
        )
    ]
    return max(matches, key=lambda row: str(row.get("created_at_utc") or ""), default=None)


def apply_live_fill_basis(position: dict[str, Any], order: dict[str, Any]) -> None:
    """Scale local settlement telemetry to the actual immediate-match response.

    Canonical facts remain authoritative; the local fee is explicitly marked
    as an estimate until clob_fill_sync records exact evidence.
    """
    response = order.get("exchange_response") or {}
    place = response.get("place") if isinstance(response, dict) else {}
    place_status = str((place or {}).get("status") or "").lower()
    shares = to_float((place or {}).get("takingAmount"), 0.0)
    cost = to_float((place or {}).get("makingAmount"), 0.0)
    if place_status != "matched" or shares <= 0.0 or cost <= 0.0:
        position.update(
            {
                "position_shares": 0.0,
                "entry_cost_usd": 0.0,
                "entry_cost_with_fee": 0.0,
                "clob_order_id": str((place or {}).get("orderID") or ""),
                "pnl_basis": "canonical_fill_reconcile_required_non_immediate",
            }
        )
        return
    price = cost / shares if shares > 0 else to_float(order.get("posted_price"), 0.0)
    estimated_fee = shares * fee(price) if shares > 0 else 0.0
    position.update(
        {
            "position_shares": round(shares, 6),
            "fill_price": round(price, 6),
            "entry_cost_usd": round(cost, 6),
            "estimated_fee_usd": round(estimated_fee, 6),
            "entry_cost_with_fee": round(cost + estimated_fee, 6),
            "fee_source": "weather_fee_curve_estimate_pending_canonical",
            "clob_order_id": str((place or {}).get("orderID") or ""),
            "pnl_basis": "actual_immediate_match_response",
        }
    )


def reconcile_live_positions(
    positions: dict[str, Any], rows: list[dict[str, Any]]
) -> int:
    updated = 0
    for position in positions.values():
        if position.get("execution_mode") not in {
            "tiny_live_taker_5shares",
            "tiny_live_split_5_taker_5_maker",
        }:
            continue
        order = matching_live_order(
            city=str(position.get("city") or ""),
            target_date=str(position.get("target_date") or ""),
            bracket=str(position.get("d1_bracket") or ""),
            rows=rows,
            child_roles={"single", "taker"},
        )
        if order is None:
            continue
        before = position.get("clob_order_id")
        apply_live_fill_basis(position, order)
        updated += int(position.get("clob_order_id") != before)
    return updated


def build_live_plan(
    event: dict[str, Any],
    args: argparse.Namespace,
    cycle_dt: datetime,
    *,
    child_order_role: str,
) -> dict[str, Any]:
    ask = to_float(event.get("d1_yes_direct_ask"))
    bid = to_float(event.get("d1_yes_direct_bid"), 0.0)
    signal_base = {
        "strategy_instance": STRATEGY_ID,
        "city": event.get("city"),
        "target_date": event.get("target_date"),
        "bracket": event.get("d1_bracket"),
        "token_id": event.get("d1_yes_token_id"),
    }
    signal_id = "d1-yes-high-mid-" + stable_hash(signal_base)
    maker_only = child_order_role == "maker"
    shares = float(args.maker_shares if maker_only else args.shares)
    minutes_to_next_obs = to_float(event.get("minutes_to_next_obs"))
    if maker_only and math.isfinite(minutes_to_next_obs):
        ttl_min = max(1.0, min(float(args.order_ttl_min), minutes_to_next_obs - 0.5))
    elif maker_only:
        ttl_min = max(1.0, min(float(args.order_ttl_min), 5.0))
    else:
        ttl_min = float(args.order_ttl_min)
    expires_at = cycle_dt + timedelta(minutes=ttl_min)
    if maker_only:
        tick = 0.001
        limit_price = min(max(bid + tick, bid), max(bid, ask - tick)) if bid > 0 else 0.0
        execution_policy = "d1_yes_high_mid_maker_v1"
        quote_reason = "direct_yes_post_only_improve_bid_one_tick"
        quote_mode = "fresh_d1_mid_post_only_recheck"
    else:
        limit_price = ask
        execution_policy = "d1_yes_high_mid_taker_v1"
        quote_reason = "direct_yes_top_ask_taker"
        quote_mode = "top_ask_taker_live"
    spread = max(0.0, ask - bid) if bid > 0 else None
    comparison_group_id = stable_hash(
        {"signal_id": signal_id, "token_id": event.get("d1_yes_token_id")}
    )
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": "plan-" + stable_hash(
            {**signal_base, "signal_id": signal_id, "child_order_role": child_order_role}
        ),
        "signal_id": signal_id,
        "opportunity_id": signal_id,
        "comparison_group_id": comparison_group_id,
        "execution_profile": "d1_yes_split_5_taker_5_maker_v1",
        "created_at_utc": cycle_dt.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_ID,
        "source_strategy_instance": STRATEGY_ID,
        "strategy_family": "market_structure_edge.favorite_low_estimation",
        "strategy_head": "d1_yes_high_mid",
        "probability_source": "market_d1_yes_mid_ge_0p80",
        "decision_mode": "first_qualifying_city_date",
        "execution_mode": "tiny_live_split_5_taker_5_maker_taipei_shadow",
        "profile": "d1_yes_high_mid",
        "combo": RULE_ID,
        "city": str(event.get("city") or ""),
        "city_pool": "all_except_taipei_live",
        "target_date": str(event.get("target_date") or ""),
        "market_id": str(event.get("market_id") or event.get("condition_id") or ""),
        "market_slug": str(event.get("event_slug") or ""),
        "bracket": str(event.get("d1_bracket") or ""),
        "token_id": str(event.get("d1_yes_token_id") or ""),
        "condition_id": str(event.get("condition_id") or ""),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(ask, 6),
        "best_bid": round(bid, 6) if bid > 0 else 0.0,
        "best_ask": round(ask, 6),
        "spread": round(spread, 6) if spread is not None else None,
        "limit_price": round(limit_price, 6),
        "quote_status": "accepted",
        "quote_reason": quote_reason,
        "quote_edge": 0.0,
        "required_quote_edge": 0.0,
        "model_token_probability": round(to_float(event.get("d1_yes_mid"), 0.0), 6),
        "quote_best_bid": round(bid, 6) if bid > 0 else 0.0,
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(spread, 6) if spread is not None else None,
        "quote_tick_size": 0.001,
        "quote_mode": quote_mode,
        "child_order_role": child_order_role,
        "maker_only": maker_only,
        "allow_duplicate_signal_id": True,
        "execution_policy": execution_policy,
        "order_lifecycle_policy": "maker_until_data_update" if maker_only else "taker_now",
        "min_live_mid": float(args.mid_threshold),
        "tick_size": 0.001,
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": round(shares, 6),
        "max_order_shares": round(shares, 6),
        "size": round(shares, 6),
        "notional": round(shares * limit_price, 6),
        "order_notional_cap": round(shares * ask, 6),
        "paper_enabled": True,
        "live_enabled": True,
        "shadow_decision": "live_except_taipei",
        "shadow_reason": "Taipei remains zero-notional shadow",
        "model_version": "d1_yes_high_mid_v1",
        "expires_at_utc": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "order_ttl_min": round(ttl_min, 6),
        **(
            {"cancel_before_data_update_utc": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z")}
            if maker_only
            else {}
        ),
        "decision_snapshot_ts_utc": str(event.get("book_fetched_at_utc") or ""),
        "snapshot_ts_utc": str(event.get("book_fetched_at_utc") or ""),
        "source_snapshot_path": str(event.get("orderbook_file") or ""),
        "running_max_obs_utc": str(event.get("obs_generated_at_utc") or ""),
        "obs_age_min": event.get("obs_age_min"),
        "minutes_since_running_max": event.get("minutes_since_running_max"),
    }


def build_live_plans(
    event: dict[str, Any], args: argparse.Namespace, cycle_dt: datetime
) -> list[dict[str, Any]]:
    plans = [build_live_plan(event, args, cycle_dt, child_order_role="taker")]
    if float(args.maker_shares) > 0:
        plans.append(build_live_plan(event, args, cycle_dt, child_order_role="maker"))
    return plans


# --------------------------------------------------------------------------- #
# main cycle
# --------------------------------------------------------------------------- #
def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if float(args.shares) <= 0 or float(args.maker_shares) < 0:
        raise ValueError("--shares must be positive and --maker-shares must be non-negative")
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
    live_rows = successful_live_orders()
    reconciled_live_positions = reconcile_live_positions(positions, live_rows)

    triggers_this_cycle = 0
    new_positions = 0
    cities_scanned = 0
    cities_with_usable_d1_quote = 0
    invalid_obs_age_rows = 0
    invalid_book_age_rows = 0
    events: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    pending_live_positions: dict[str, dict[str, Any]] = {}
    live_planned = 0
    shadow_first = 0
    daily_order_count, daily_cost = live_daily_usage(cycle_dt)

    for city, rec in observations.items():
        target_date = rec.get("target_date")
        if not target_date:
            continue
        key = (city, str(target_date))
        if key not in ladder:
            continue
        cities_scanned += 1
        running, unit = running_native(rec)
        current_bracket, d1_bracket, d1_no, d1_yes = find_current_and_d1(ladder[key], running, unit)
        if d1_bracket is None or d1_no is None or d1_yes is None:
            continue
        no_bid = to_float(d1_no.get("bid"))
        no_ask = to_float(d1_no.get("ask"))
        if not math.isfinite(no_bid) or not math.isfinite(no_ask):
            continue
        cities_with_usable_d1_quote += 1
        d1_yes_ask = 1.0 - no_bid          # taker cost for YES
        d1_yes_mid = 1.0 - (no_ask + no_bid) / 2.0
        direct_yes_ask = to_float(d1_yes.get("ask"))
        direct_yes_bid = to_float(d1_yes.get("bid"))
        parity_gap = abs(direct_yes_ask - d1_yes_ask) if math.isfinite(direct_yes_ask) else math.nan
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
        # The durable live ledger is also part of first-signal state.  This
        # prevents a crash between exchange submit and positions.json update
        # from replaying both split children on the next loop.
        prior_live_order = matching_live_order(
            city=city,
            target_date=target_date,
            bracket="",
            rows=live_rows,
        )
        is_first = pos_key not in positions and prior_live_order is None
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
            "d1_yes_direct_ask": round(direct_yes_ask, 4) if math.isfinite(direct_yes_ask) else None,
            "d1_yes_direct_bid": round(direct_yes_bid, 4) if math.isfinite(direct_yes_bid) else None,
            "d1_yes_parity_gap": round(parity_gap, 6) if math.isfinite(parity_gap) else None,
            "d1_yes_ask_size": d1_yes.get("ask_size"),
            "d1_yes_depth_ask_5c": d1_yes.get("depth_ask_5c"),
            "d1_yes_token_id": d1_yes.get("token_id"),
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
            "minutes_to_next_obs": rec.get("minutes_to_next_obs"),
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
            "market_id": d1_yes.get("market_id"),
            "event_slug": d1_yes.get("event_slug"),
            # v1.1 pre-registered guards (recorded; remaining_heat requires forecast join, null here)
            "v11_ask_ok": bool(d1_yes_ask <= V11_MAX_ASK),
            "v11_remaining_heat_ok": None,
            "settled_bracket": None,
            "win": None,
            "pnl_at_settlement": None,
            "execution_mode": "telemetry" if not is_first else "shadow",
            "live_blocker": None,
        }

        if is_first:
            blocker = ""
            d1_parsed = parse_bracket(d1_bracket)
            if city in LIVE_SHADOW_CITIES:
                blocker = "city_policy_taipei_shadow"
            elif d1_parsed is None or d1_parsed.high is None:
                blocker = "open_upper_or_unparsed_d1_shadow"
            elif current_bracket == d1_bracket:
                blocker = "invalid_same_current_and_d1"
            elif not str(d1_yes.get("token_id") or ""):
                blocker = "missing_yes_token"
            elif not math.isfinite(direct_yes_ask):
                blocker = "missing_direct_yes_ask"
            elif not math.isfinite(parity_gap) or parity_gap > MAX_YES_PARITY_GAP:
                blocker = "yes_no_quote_parity_mismatch"
            elif to_float(d1_yes.get("ask_size"), 0.0) < float(args.shares):
                blocker = "insufficient_top_ask_depth"
            elif to_float(d1_yes.get("depth_ask_5c"), 0.0) < float(args.shares):
                blocker = "insufficient_ask_depth_5c"
            else:
                blocker = live_guard_reason(event)

            event_plans = build_live_plans(event, args, cycle_dt) if not blocker else []
            order_cost = sum(to_float(plan.get("order_notional_cap"), 0.0) for plan in event_plans)
            if not blocker and daily_order_count + live_planned + len(event_plans) > int(args.max_orders_per_day):
                blocker = "strategy_daily_order_cap"
            if not blocker and daily_cost + sum(to_float(p.get("order_notional_cap"), 0.0) for p in plans) + order_cost > float(args.max_daily_cost_usd) + 1e-9:
                blocker = "strategy_daily_cost_cap"
            if not blocker and not (args.live and args.confirm_live):
                blocker = "live_not_requested"

            if blocker:
                event["execution_mode"] = "zero_notional_shadow"
                event["live_blocker"] = blocker
                shadow_first += 1
            else:
                event["execution_mode"] = "tiny_live_split_5_taker_5_maker"
                plans.extend(event_plans)
                live_planned += len(event_plans)
        events.append(event)
        if is_first and not args.dry_run:
            candidate_position = {
                "city": city,
                "target_date": target_date,
                "d1_bracket": d1_bracket,
                "d1_yes_ask": round(d1_yes_ask, 4),
                "entry_cost_with_fee": round(d1_yes_ask + fee(d1_yes_ask), 6),
                "entry_cycle_ts_utc": cycle_ts,
                "book_fetched_at_utc": d1_no.get("fetched_at_utc"),
                "book_age_min": round(book_age, 2),
                "orderbook_file": rel(ob_file) if ob_file else None,
                "execution_mode": event["execution_mode"],
                "live_blocker": event["live_blocker"],
                "planned_taker_shares": float(args.shares),
                "planned_maker_shares": float(args.maker_shares),
                "planned_total_shares": float(args.shares) + float(args.maker_shares),
                "settled": False,
            }
            if event["execution_mode"] == "tiny_live_split_5_taker_5_maker":
                # A failed submit must not consume the city-date.  Promote the
                # position only after the durable live ledger proves success.
                pending_live_positions[pos_key] = candidate_position
            else:
                positions[pos_key] = candidate_position
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
            shares = to_float(pos.get("position_shares"), 1.0)
            pos["pnl_at_settlement"] = round(
                (shares if win else 0.0) - pos["entry_cost_with_fee"], 6
            )
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

    executor_result = None
    if not args.dry_run:
        order_runtime.write_jsonl(PLAN_OUT, plans)
        if args.live:
            executor_result = order_runtime.run_weather_order_executor(
                root=ROOT,
                plans_path=PLAN_OUT,
                paper_out=PAPER_OUT,
                live_out=LIVE_OUT,
                live=True,
                confirm_live=bool(args.confirm_live),
                allow_taker=True,
                cancel_expired=True,
                no_telegram=True,
                timeout_sec=180.0,
            )
            live_rows = successful_live_orders()
            for pos_key, position in pending_live_positions.items():
                submitted_order = matching_live_order(
                    city=str(position.get("city") or ""),
                    target_date=str(position.get("target_date") or ""),
                    bracket=str(position.get("d1_bracket") or ""),
                    rows=live_rows,
                    child_roles={"single", "taker", "maker"},
                )
                if submitted_order is None:
                    continue
                taker_order = matching_live_order(
                    city=str(position.get("city") or ""),
                    target_date=str(position.get("target_date") or ""),
                    bracket=str(position.get("d1_bracket") or ""),
                    rows=live_rows,
                    child_roles={"single", "taker"},
                )
                if taker_order is not None:
                    apply_live_fill_basis(position, taker_order)
                else:
                    position.update(
                        {
                            "position_shares": 0.0,
                            "entry_cost_usd": 0.0,
                            "entry_cost_with_fee": 0.0,
                            "clob_order_id": str(
                                ((submitted_order.get("exchange_response") or {}).get("place") or {}).get("orderID")
                                or ""
                            ),
                            "pnl_basis": "canonical_fill_reconcile_required_for_maker_child",
                        }
                    )
                positions[pos_key] = position
                new_positions += 1
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
    book_cities_for_target_dates = len(
        {city for city, target_date in ladder if target_date in set(target_dates)}
    )
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
        "book_cities_for_target_dates": book_cities_for_target_dates,
        "cities_scanned_with_book": cities_scanned,
        "cities_with_usable_d1_quote": cities_with_usable_d1_quote,
        "invalid_obs_age_rows": invalid_obs_age_rows,
        "invalid_book_age_rows": invalid_book_age_rows,
        "triggers_this_cycle": triggers_this_cycle,
        "candidate_rows": triggers_this_cycle,
        "new_positions_this_cycle": new_positions,
        "selected_rows_this_cycle": new_positions,
        "rows_written_this_cycle": len(events),
        "live_requested": bool(args.live),
        "live_enabled": bool(args.live and args.confirm_live),
        "live_policy": {"Taipei": "zero_notional_shadow", "other_cities": "live_if_exact_and_executable"},
        "taker_shares": float(args.shares),
        "maker_shares": float(args.maker_shares),
        "target_total_shares_per_signal": float(args.shares) + float(args.maker_shares),
        "max_orders_per_day": int(args.max_orders_per_day),
        "max_daily_cost_usd": float(args.max_daily_cost_usd),
        "live_orders_before_cycle_today": daily_order_count,
        "live_cost_before_cycle_today": round(daily_cost, 6),
        "live_plans_this_cycle": live_planned,
        "live_positions_reconciled_this_cycle": reconciled_live_positions,
        "shadow_first_signals_this_cycle": shadow_first,
        "executor_result": executor_result,
        "open_positions_total": len(positions),
        "settled_positions_total": len(settled_positions),
        "settled_wins": settled_wins,
        "settled_cost_with_fee": round(settled_cost, 6),
        "settled_pnl": round(settled_pnl, 6),
        "settled_roi": round(settled_pnl / settled_cost, 6) if settled_cost > 0 else None,
        "settled_now": settled_now,
        "coverage_note": coverage_note(ob_file, book_cities_for_target_dates),
    }
    if not args.dry_run:
        write_json(SUMMARY_OUT, summary)
        append_jsonl(SUMMARY_HISTORY_OUT, summary)
    return summary


def main() -> int:
    args = parse_args()
    configure_runtime(args)
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
                f"live_plans={summary['live_plans_this_cycle']} "
                f"open={summary['open_positions_total']} settled={summary['settled_positions_total']} "
                f"roi={summary['settled_roi']} coverage={summary['coverage_note']}",
                flush=True,
            )
        except Exception as exc:  # keep the loop alive; surface the error
            print(f"[{now_utc()}] cycle_error: {exc!r}", flush=True)
        time.sleep(max(30.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
