#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _build_signal(record: Dict[str, Any], token_id: str, snapshot_path: Path) -> Dict[str, Any]:
    side = _safe_str(record.get("side")).upper()
    entry_price = _to_float(record.get("entry_price"), 0.0)
    base = {
        "source_system": "weather-predict-snapshot",
        "source_record_type": _safe_str(record.get("record_type")) or "edge_signal",
        "source_id": f"{_safe_str(record.get('condition_id'))}|{side}",
        "source_run_id": snapshot_path.stem,
        "strategy": "weather_edge_v1",
        "profile": _safe_str(record.get("forecast_source")),
        "combo": "mid_price_core_v1",
        "city": _safe_str(record.get("city")),
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
    out_path: Path,
    city_pool: str,
    min_edge: float,
    min_entry_price: float,
    max_entry_price: float,
    dry_run: bool,
) -> Dict[str, Any]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records = [x for x in payload.get("records", []) if isinstance(x, dict)]
    gamma = PolymarketGammaClient()
    market_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    signals: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = {}

    for record in records:
        record_city_pool = _safe_str(record.get("city_pool"))
        if city_pool and city_pool.lower() != "all" and record_city_pool != city_pool:
            skipped[f"city_pool_not_{city_pool}"] = skipped.get(f"city_pool_not_{city_pool}", 0) + 1
            continue
        side = _safe_str(record.get("side")).upper()
        if side not in {"BUY_YES", "BUY_NO"}:
            skipped["bad_side"] = skipped.get("bad_side", 0) + 1
            continue
        if _safe_str(record.get("time_bucket")) != "t24":
            skipped["not_t24"] = skipped.get("not_t24", 0) + 1
            continue
        edge = _to_float(record.get("abs_edge"), abs(_to_float(record.get("edge"), 0.0)))
        if edge < min_edge:
            skipped["edge_below_min"] = skipped.get("edge_below_min", 0) + 1
            continue
        entry_price = _to_float(record.get("entry_price"), 0.0)
        if entry_price < min_entry_price:
            skipped["entry_price_below_min"] = skipped.get("entry_price_below_min", 0) + 1
            continue
        if entry_price >= max_entry_price:
            skipped["entry_price_at_or_above_max"] = skipped.get("entry_price_at_or_above_max", 0) + 1
            continue

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
        signals.append(_build_signal(record, token_id, snapshot_path))

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
        "city_pool": city_pool,
        "records": len(records),
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
    parser.add_argument(
        "--city-pool",
        default="all",
        help="Only import records from this snapshot city_pool, e.g. t1_trading. Use all to disable filtering.",
    )
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--min-entry-price", type=float, default=0.25)
    parser.add_argument("--max-entry-price", type=float, default=0.75)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = _parser().parse_args()
    snapshot = Path(args.snapshot) if args.snapshot else _latest_snapshot(Path(args.snapshot_dir))
    if snapshot is None:
        raise SystemExit("no snapshot found")
    result = build_signals(
        snapshot_path=snapshot,
        out_path=Path(args.out),
        city_pool=str(args.city_pool),
        min_edge=float(args.min_edge),
        min_entry_price=float(args.min_entry_price),
        max_entry_price=float(args.max_entry_price),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
