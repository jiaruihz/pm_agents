#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.services.common import normalize_json_list
from src.strategies.weather_edge_v1.tools.execution_pipeline import stable_hash


DEFAULT_MARKET_DATA = ROOT / "runtime" / "weather_edge_v1" / "market_data"
DEFAULT_OUT = ROOT / "runtime" / "weather_edge_v1" / "signals" / "signals.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_csv_set(value: Any) -> set[str]:
    text = _safe_str(value)
    if not text:
        return set()
    return {part.strip() for part in text.split(",") if part.strip()}


def _parse_city_side_set(value: Any) -> set[tuple[str, str]]:
    text = _safe_str(value)
    if not text:
        return set()
    pairs: set[tuple[str, str]] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"blocked city side must be CITY:SIDE, got {part!r}")
        city, side = (piece.strip() for piece in part.split(":", 1))
        side = side.upper()
        if side in {"YES", "NO"}:
            side = f"BUY_{side}"
        if not city or side not in {"BUY_YES", "BUY_NO"}:
            raise ValueError(f"blocked city side must be CITY:BUY_YES or CITY:BUY_NO, got {part!r}")
        pairs.add((city, side))
    return pairs


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _market_top(record: Dict[str, Any]) -> Dict[str, float]:
    best_bid = _to_float(record.get("best_bid"), 0.0)
    best_ask = _to_float(record.get("best_ask"), 0.0)
    spread = _to_float(record.get("spread"), 0.0)
    if spread <= 0 and best_bid > 0 and best_ask > 0:
        spread = max(0.0, best_ask - best_bid)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
    }


def _latest_snapshot(snapshot_dir: Path) -> Optional[Path]:
    files = sorted(snapshot_dir.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _recent_snapshots(snapshot_dir: Path, lookback_minutes: float) -> List[Path]:
    files = sorted(snapshot_dir.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return []
    if lookback_minutes <= 0:
        return [files[-1]]
    newest_mtime = files[-1].stat().st_mtime
    cutoff = newest_mtime - lookback_minutes * 60.0
    return [p for p in files if p.stat().st_mtime >= cutoff]


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_to_settle_now(record: Dict[str, Any], now_utc: datetime) -> Optional[float]:
    settle_utc = _parse_utc(record.get("settle_utc"))
    if settle_utc is not None:
        return (settle_utc - now_utc).total_seconds() / 3600.0
    try:
        return float(record.get("hours_to_settle"))
    except (TypeError, ValueError):
        return None


def _snapshot_hours_to_settle(record: Dict[str, Any]) -> Optional[float]:
    try:
        return float(record.get("hours_to_settle"))
    except (TypeError, ValueError):
        return None


def _record_key(record: Dict[str, Any], side: str) -> Tuple[str, str, str, str, str]:
    market_id = _safe_str(record.get("market_id"))
    condition_id = _safe_str(record.get("condition_id"))
    if market_id:
        return ("market_id", market_id, "", "", side)
    if condition_id:
        return ("condition_id", condition_id, "", "", side)
    return (
        "city_bracket",
        _safe_str(record.get("city")),
        _safe_str(record.get("event_date")),
        _safe_str(record.get("bracket")),
        side,
    )


def _token_for_side(market: Dict[str, Any], side: str) -> str:
    outcomes = [str(x).strip().lower() for x in normalize_json_list(market.get("outcomes"))]
    token_ids = [str(x).strip() for x in normalize_json_list(market.get("clobTokenIds"))]
    if not token_ids:
        return ""
    wanted = "yes" if side == "BUY_YES" else "no"
    for idx, outcome in enumerate(outcomes):
        if outcome == wanted and idx < len(token_ids):
            return token_ids[idx]
    if len(token_ids) >= 2:
        return token_ids[0] if wanted == "yes" else token_ids[1]
    return token_ids[0]


def _build_signal(record: Dict[str, Any], token_id: str, snapshot_path: Path, *, strategy_instance: str) -> Dict[str, Any]:
    side = _safe_str(record.get("side")).upper()
    entry_price = _to_float(record.get("entry_price"), 0.0)
    base = {
        "source_system": "weather-predict-snapshot",
        "source_record_type": _safe_str(record.get("record_type")) or "edge_signal",
        "source_id": f"{_safe_str(record.get('condition_id'))}|{side}",
        "source_run_id": snapshot_path.stem,
        "strategy": "weather_edge_v1",
        "strategy_instance": strategy_instance,
        "profile": _safe_str(record.get("forecast_source")),
        "combo": "mid_price_core_v1",
        "city": _safe_str(record.get("city")),
        "city_pool": _safe_str(record.get("city_pool")),
        "target_date": _safe_str(record.get("event_date")),
        "unit": _safe_str(record.get("unit")),
        "event_slug": _safe_str(record.get("event_slug")),
        "market_id": _safe_str(record.get("market_id")),
        "market_slug": "",
        "question": _safe_str(record.get("question")),
        "bracket": _safe_str(record.get("bracket")),
        "token_id": token_id,
        "signal_side": side,
        "order_side": "BUY",
        "model_probability_yes": _to_float(record.get("model_prob"), 0.0),
        "market_price": entry_price,
        **_market_top(record),
        "edge": _to_float(record.get("abs_edge"), abs(_to_float(record.get("edge"), 0.0))),
        "min_edge": 0.10,
        "price_source": "weather_snapshot_entry_price",
        "obs_source": _safe_str(record.get("obs_source")) or _safe_str(record.get("obs_source_version")),
        "model_version": _safe_str(record.get("model")),
        "forecast_source": _safe_str(record.get("forecast_source")),
        "forecast_max_f": record.get("forecast_max_f"),
        "forecast_max_native": record.get("forecast_max_native"),
        "forecast_peak_hour_local": record.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": record.get("forecast_peak_time_local"),
        "forecast_peak_hour_utc": record.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": record.get("forecast_peak_time_utc"),
        "forecast_hourly_count": record.get("forecast_hourly_count"),
        "forecast_values_hash": _safe_str(record.get("forecast_values_hash")),
        "forecast_peak_source": _safe_str(record.get("forecast_peak_source")),
        "forecast_timezone": _safe_str(record.get("forecast_timezone")),
        "forecast_utc_offset_seconds": record.get("forecast_utc_offset_seconds"),
        "forecast_peak_delta_hours_local": record.get("forecast_peak_delta_hours_local"),
        "forecast_max_in_bracket": record.get("forecast_max_in_bracket"),
        "forecast_max_above_bracket_f": record.get("forecast_max_above_bracket_f"),
        "forecast_max_below_bracket_f": record.get("forecast_max_below_bracket_f"),
        "forecast_max_above_metar_max_f": record.get("forecast_max_above_metar_max_f"),
        "snapshot_fetched_at_utc": _safe_str(record.get("ts_utc")) or _safe_str(record.get("snapshot_ts_utc")),
        "execution_policy": "mid_price_core_v1",
    }
    return {
        "record_type": "weather_edge_signal",
        "signal_id": stable_hash(base),
        "imported_at_utc": _now_utc(),
        "status": "new",
        **base,
    }


def build_signals(
    *,
    snapshot_path: Path,
    snapshot_paths: Optional[Iterable[Path]] = None,
    out_path: Path,
    city_pool: str,
    min_edge: float,
    min_entry_price: float,
    max_entry_price: float,
    yes_min_entry_price: Optional[float] = None,
    yes_max_entry_price: Optional[float] = None,
    yes_min_edge: Optional[float] = None,
    no_min_entry_price: Optional[float] = None,
    no_max_entry_price: Optional[float] = None,
    no_min_edge: Optional[float] = None,
    strategy_instance: str = "",
    allowed_cities: Optional[set[str]] = None,
    blocked_city_sides: Optional[set[tuple[str, str]]] = None,
    min_hours_to_settle: Optional[float] = None,
    max_hours_to_settle: Optional[float] = None,
    dry_run: bool,
) -> Dict[str, Any]:
    def _band_for_side(s: str) -> Tuple[float, float, float]:
        s = (s or "").upper()
        if s == "BUY_YES":
            return (
                yes_min_entry_price if yes_min_entry_price is not None else min_entry_price,
                yes_max_entry_price if yes_max_entry_price is not None else max_entry_price,
                yes_min_edge if yes_min_edge is not None else min_edge,
            )
        if s == "BUY_NO":
            return (
                no_min_entry_price if no_min_entry_price is not None else min_entry_price,
                no_max_entry_price if no_max_entry_price is not None else max_entry_price,
                no_min_edge if no_min_edge is not None else min_edge,
            )
        return (min_entry_price, max_entry_price, min_edge)

    paths = list(snapshot_paths) if snapshot_paths is not None else [snapshot_path]
    now_utc = datetime.now(timezone.utc)
    records_with_source: List[Tuple[Dict[str, Any], Path]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records_with_source.extend((x, path) for x in payload.get("records", []) if isinstance(x, dict))
    gamma = PolymarketGammaClient()
    market_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    signals: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = {}
    candidates: Dict[Tuple[str, str, str, str, str], Tuple[Dict[str, Any], Path]] = {}

    for record, source_path in records_with_source:
        record_city_pool = _safe_str(record.get("city_pool"))
        if city_pool and city_pool.lower() != "all" and record_city_pool != city_pool:
            skipped[f"city_pool_not_{city_pool}"] = skipped.get(f"city_pool_not_{city_pool}", 0) + 1
            continue
        city = _safe_str(record.get("city"))
        if allowed_cities and city not in allowed_cities:
            skipped["city_not_allowed"] = skipped.get("city_not_allowed", 0) + 1
            continue
        side = _safe_str(record.get("side")).upper()
        if side not in {"BUY_YES", "BUY_NO"}:
            skipped["bad_side"] = skipped.get("bad_side", 0) + 1
            continue
        if blocked_city_sides and (city, side) in blocked_city_sides:
            skipped["city_side_blocked"] = skipped.get("city_side_blocked", 0) + 1
            continue
        if min_hours_to_settle is None and max_hours_to_settle is None:
            if _safe_str(record.get("time_bucket")) != "t24":
                skipped["not_t24"] = skipped.get("not_t24", 0) + 1
                continue
        else:
            snapshot_hours_to_settle = _snapshot_hours_to_settle(record)
            if (
                max_hours_to_settle is not None
                and snapshot_hours_to_settle is not None
                and snapshot_hours_to_settle > max_hours_to_settle
            ):
                skipped["snapshot_hours_to_settle_above_max"] = skipped.get(
                    "snapshot_hours_to_settle_above_max", 0
                ) + 1
                continue
            hours_to_settle = _hours_to_settle_now(record, now_utc)
            if hours_to_settle is None:
                skipped["missing_hours_to_settle"] = skipped.get("missing_hours_to_settle", 0) + 1
                continue
            if min_hours_to_settle is not None and hours_to_settle < min_hours_to_settle:
                skipped["hours_to_settle_below_min"] = skipped.get("hours_to_settle_below_min", 0) + 1
                continue
            if max_hours_to_settle is not None and hours_to_settle > max_hours_to_settle:
                skipped["hours_to_settle_above_max"] = skipped.get("hours_to_settle_above_max", 0) + 1
                continue
        side_min_entry, side_max_entry, side_min_edge = _band_for_side(side)
        edge = _to_float(record.get("abs_edge"), abs(_to_float(record.get("edge"), 0.0)))
        if edge < side_min_edge:
            skipped["edge_below_min"] = skipped.get("edge_below_min", 0) + 1
            continue
        entry_price = _to_float(record.get("entry_price"), 0.0)
        if entry_price < side_min_entry:
            skipped["entry_price_below_min"] = skipped.get("entry_price_below_min", 0) + 1
            continue
        if entry_price >= side_max_entry:
            skipped["entry_price_at_or_above_max"] = skipped.get("entry_price_at_or_above_max", 0) + 1
            continue
        # De-duplicate by market, not by market+side. With a lookback window the
        # same market can flip from BUY_NO to BUY_YES across snapshots; keeping
        # both creates self-hedged live positions. Paths are processed oldest ->
        # newest, so the latest eligible snapshot wins.
        key = _record_key(record, "")
        if key in candidates:
            skipped["older_duplicate_candidate"] = skipped.get("older_duplicate_candidate", 0) + 1
        candidates[key] = (record, source_path)

    for record, source_path in candidates.values():
        side = _safe_str(record.get("side")).upper()
        market_id = _safe_str(record.get("market_id"))
        condition_id = _safe_str(record.get("condition_id"))
        cache_key = market_id or condition_id
        if cache_key not in market_cache:
            market_cache[cache_key] = (
                gamma.fetch_market_by_id_or_slug(market_id=market_id)
                if market_id
                else gamma.fetch_market_by_condition_id(condition_id)
            )
        market = market_cache.get(cache_key)
        if not market:
            skipped["market_not_found"] = skipped.get("market_not_found", 0) + 1
            continue
        token_id = _token_for_side(market, side)
        if not token_id:
            skipped["token_not_found"] = skipped.get("token_not_found", 0) + 1
            continue
        signals.append(_build_signal(record, token_id, source_path, strategy_instance=strategy_instance))

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    existing.add(json.loads(line).get("signal_id"))
                except Exception:
                    continue
        with out_path.open("a", encoding="utf-8") as fh:
            for signal in signals:
                if signal["signal_id"] in existing:
                    skipped["duplicate"] = skipped.get("duplicate", 0) + 1
                    continue
                fh.write(json.dumps(signal, ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(signal["signal_id"])

    return {
        "snapshot": str(snapshot_path),
        "snapshots": [str(p) for p in paths],
        "city_pool": city_pool,
        "allowed_cities": sorted(allowed_cities or []),
        "blocked_city_sides": [f"{city}:{side}" for city, side in sorted(blocked_city_sides or [])],
        "records": len(records_with_source),
        "candidate_signals": len(candidates),
        "signals": len(signals),
        "out": str(out_path),
        "dry_run": dry_run,
        "skipped": dict(sorted(skipped.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build executable weather signals from a paper snapshot.")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_MARKET_DATA / "paper_snapshots"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--strategy-instance", default=os.getenv("WEATHER_STRATEGY_INSTANCE", ""))
    parser.add_argument(
        "--allowed-cities",
        default=os.getenv("WEATHER_LIVE_ALLOWED_CITIES", ""),
        help="Comma-separated city allowlist after city_pool filtering. Empty means all cities in the pool.",
    )
    parser.add_argument(
        "--blocked-city-sides",
        default=os.getenv("WEATHER_LIVE_BLOCKED_CITY_SIDES", ""),
        help="Comma-separated CITY:SIDE entries, e.g. NYC:BUY_YES,Ankara:NO.",
    )
    parser.add_argument(
        "--city-pool",
        default="all",
        help="Only import records from this snapshot city_pool, e.g. t1_trading. Use all to disable filtering.",
    )
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--min-entry-price", type=float, default=0.25)
    parser.add_argument("--max-entry-price", type=float, default=0.75)
    # Per-side entry band / edge overrides (None -> use global above).
    parser.add_argument("--yes-min-entry-price", type=float, default=None)
    parser.add_argument("--yes-max-entry-price", type=float, default=None)
    parser.add_argument("--yes-min-edge", type=float, default=None)
    parser.add_argument("--no-min-entry-price", type=float, default=None)
    parser.add_argument("--no-max-entry-price", type=float, default=None)
    parser.add_argument("--no-min-edge", type=float, default=None)
    parser.add_argument(
        "--snapshot-lookback-minutes",
        type=float,
        default=0.0,
        help="Scan all snapshots whose mtime is within this many minutes of the newest snapshot. Default preserves latest-only behavior.",
    )
    parser.add_argument("--min-hours-to-settle", type=float, default=None)
    parser.add_argument("--max-hours-to-settle", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = _parser().parse_args()
    if args.snapshot:
        snapshots = [Path(args.snapshot)]
    else:
        snapshots = _recent_snapshots(Path(args.snapshot_dir), float(args.snapshot_lookback_minutes))
    snapshot = snapshots[-1] if snapshots else None
    if snapshot is None:
        raise SystemExit("no snapshot found")
    result = build_signals(
        snapshot_path=snapshot,
        snapshot_paths=snapshots,
        out_path=Path(args.out),
        city_pool=str(args.city_pool),
        min_edge=float(args.min_edge),
        min_entry_price=float(args.min_entry_price),
        max_entry_price=float(args.max_entry_price),
        yes_min_entry_price=(float(args.yes_min_entry_price) if args.yes_min_entry_price is not None else None),
        yes_max_entry_price=(float(args.yes_max_entry_price) if args.yes_max_entry_price is not None else None),
        yes_min_edge=(float(args.yes_min_edge) if args.yes_min_edge is not None else None),
        no_min_entry_price=(float(args.no_min_entry_price) if args.no_min_entry_price is not None else None),
        no_max_entry_price=(float(args.no_max_entry_price) if args.no_max_entry_price is not None else None),
        no_min_edge=(float(args.no_min_edge) if args.no_min_edge is not None else None),
        strategy_instance=str(args.strategy_instance),
        allowed_cities=_parse_csv_set(args.allowed_cities),
        blocked_city_sides=_parse_city_side_set(args.blocked_city_sides),
        min_hours_to_settle=args.min_hours_to_settle,
        max_hours_to_settle=args.max_hours_to_settle,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
