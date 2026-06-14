#!/usr/bin/env python3
"""Zero-notional Range RV shadow runner from weather-predict snapshots.

This is an execution telemetry layer for the current Range RV research line. It
does not place orders. It reads the latest paper snapshot, rebuilds the fixed
live-standard candidate from the source-aware research base, and appends only
selected shadow plans to a local journal.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis.observed_max import research_settlement_source_registry_v0 as source_registry  # noqa: E402


STRATEGY_ID = "forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0"
WIDTH = 3
EDGE_THRESHOLD = 0.02
MIN_TOP_ASK_SIZE = 5.0

FALLBACK_DEFAULT_WU_CITIES = {
    "Amsterdam",
    "Ankara",
    "Atlanta",
    "Austin",
    "Beijing",
    "BuenosAires",
    "Busan",
    "CapeTown",
    "Chengdu",
    "Chongqing",
    "Dallas",
    "Denver",
    "Guangzhou",
    "Helsinki",
    "Houston",
    "Jeddah",
    "Karachi",
    "LA",
    "Lucknow",
    "Madrid",
    "Manila",
    "Miami",
    "Munich",
    "NYC",
    "SanFrancisco",
    "SaoPaulo",
    "Seattle",
    "Shanghai",
    "Singapore",
    "Taipei",
    "Tokyo",
    "Warsaw",
    "Wellington",
    "Wuhan",
}
FALLBACK_SOURCE_SENSITIVE_CITIES = {
    "Chicago",
    "HongKong",
    "Jakarta",
    "KualaLumpur",
    "London",
    "Milan",
    "PanamaCity",
    "Paris",
}
FALLBACK_BLOCKED_CITIES = {"Moscow", "Seoul", "Shenzhen"}
FALLBACK_DEFAULT_WATCHLIST_CITIES = {"MexicoCity"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_snapshot_glob() -> str:
    n100 = Path("/home/jiarui/projects/weather-predict/output/paper_snapshots/snapshot_*.json")
    if n100.parent.exists():
        return str(n100)
    return str(ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots/snapshot_*.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-glob", default=default_snapshot_glob())
    parser.add_argument(
        "--journal",
        default=str(ROOT / "runtime/weather_edge_v1/range_rv_shadow_v0/shadow_journal.jsonl"),
    )
    parser.add_argument(
        "--summary",
        default=str(ROOT / "runtime/weather_edge_v1/range_rv_shadow_v0/latest_summary.json"),
    )
    parser.add_argument("--city-pool", default="t1_trading", choices=["t1_trading", "t2_research", "all"])
    parser.add_argument("--edge-threshold", type=float, default=EDGE_THRESHOLD)
    parser.add_argument("--min-top-ask-size", type=float, default=MIN_TOP_ASK_SIZE)
    parser.add_argument("--write-all", action="store_true", help="Append rejected candidates too for diagnostics.")
    parser.add_argument("--max-candidates", type=int, default=0, help="Optional cap after sorting by edge desc.")
    return parser.parse_args()


def load_latest_snapshot(pattern: str) -> tuple[Path, dict[str, Any]]:
    paths = sorted(Path(p) for p in glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"no snapshot files matched {pattern}")
    path = paths[-1]
    with path.open() as f:
        data = json.load(f)
    if not isinstance(data.get("records"), list):
        raise ValueError(f"snapshot has no records list: {path}")
    return path, data


def source_bucket(settlement_source_class: str) -> str:
    if settlement_source_class == "default_wu_station_by_rules":
        return "default_wu"
    if settlement_source_class == "default_source_watchlist":
        return "default_watchlist"
    if settlement_source_class in {"special_source_confirmed", "official_station_diff_confirmed"}:
        return "source_sensitive_confirmed"
    if settlement_source_class == "blocked_unresolved_settlement_basis":
        return "blocked_unresolved"
    return "other_source_or_unknown"


def load_source_registry() -> dict[str, dict[str, Any]]:
    try:
        registry = source_registry.build_registry()
    except FileNotFoundError:
        return fallback_source_registry()
    required = {"city", "settlement_source_class", "official_station_or_feed", "mapping_rule"}
    missing = required.difference(registry.columns)
    if missing:
        raise RuntimeError(f"settlement source registry missing columns: {sorted(missing)}")

    out: dict[str, dict[str, Any]] = {}
    for row in registry.to_dict("records"):
        cls = str(row["settlement_source_class"])
        out[str(row["city"])] = {
            "settlement_source_class": cls,
            "source_bucket": source_bucket(cls),
            "official_station_or_feed": row.get("official_station_or_feed"),
            "mapping_rule": row.get("mapping_rule"),
        }
    return out


def fallback_source_registry() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for city in FALLBACK_DEFAULT_WU_CITIES:
        out[city] = {
            "settlement_source_class": "default_wu_station_by_rules",
            "source_bucket": "default_wu",
            "official_station_or_feed": None,
            "mapping_rule": "whole-degree WU station max",
        }
    for city in FALLBACK_SOURCE_SENSITIVE_CITIES:
        out[city] = {
            "settlement_source_class": "source_sensitive_confirmed_fallback",
            "source_bucket": "source_sensitive_confirmed",
            "official_station_or_feed": None,
            "mapping_rule": "runtime fallback; excluded from generic Range RV shadow",
        }
    for city in FALLBACK_BLOCKED_CITIES:
        out[city] = {
            "settlement_source_class": "blocked_unresolved_settlement_basis",
            "source_bucket": "blocked_unresolved",
            "official_station_or_feed": None,
            "mapping_rule": "runtime fallback; excluded from generic Range RV shadow",
        }
    for city in FALLBACK_DEFAULT_WATCHLIST_CITIES:
        out[city] = {
            "settlement_source_class": "default_source_watchlist",
            "source_bucket": "default_watchlist",
            "official_station_or_feed": None,
            "mapping_rule": "runtime fallback; excluded from generic Range RV shadow",
        }
    return out


def normalize(values: list[float]) -> list[float]:
    total = sum(max(0.0, x) for x in values)
    if total <= 0.0:
        return [0.0 for _ in values]
    return [max(0.0, x) / total for x in values]


def bracket_key(value: Any) -> tuple[int, float, str]:
    text = str(value)
    try:
        return (0, float(text), text)
    except ValueError:
        return (1, 0.0, text)


def centered_indices(mode_i: int, n: int, width: int) -> list[int] | None:
    if n < width:
        return None
    start = min(max(0, mode_i - width // 2), n - width)
    return list(range(start, start + width))


def finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def leg_price(row: dict[str, Any], side: str) -> float | None:
    return finite_float(row.get("yes_best_ask" if side == "BUY_YES" else "no_best_ask"))


def leg_size(row: dict[str, Any], side: str) -> float | None:
    return finite_float(row.get("yes_ask_size" if side == "BUY_YES" else "no_ask_size"))


def leg_depth(row: dict[str, Any], side: str) -> float | None:
    return finite_float(row.get("yes_depth_ask_5c" if side == "BUY_YES" else "no_depth_ask_5c"))


def leg_status(row: dict[str, Any], side: str) -> str:
    return str(row.get("yes_book_status" if side == "BUY_YES" else "no_book_status") or "")


def token_id(row: dict[str, Any], side: str) -> str | None:
    value = row.get("yes_token_id" if side == "BUY_YES" else "no_token_id")
    return None if value is None else str(value)


def make_candidate(
    *,
    group_key: tuple[str, str, str, str, str],
    rows: list[dict[str, Any]],
    source_meta: dict[str, Any],
    edge_threshold: float,
    min_top_ask_size: float,
    snapshot_ts_utc: str,
    snapshot_path: Path,
) -> dict[str, Any] | None:
    city, event_date, forecast_source, model_version, decision_snapshot_ts_utc = group_key
    items = sorted(rows, key=lambda r: bracket_key(r.get("bracket")))
    probs = [finite_float(row.get("model_prob")) for row in items]
    if any(value is None for value in probs):
        return None
    model_norm = normalize([float(value) for value in probs if value is not None])
    if not any(model_norm):
        return None

    mode_i = max(range(len(model_norm)), key=lambda i: model_norm[i])
    idxs = centered_indices(mode_i, len(items), WIDTH)
    if idxs is None:
        return None

    inside = [items[i] for i in idxs]
    inside_brackets = {str(row.get("bracket")) for row in inside}
    outside = [row for row in items if str(row.get("bracket")) not in inside_brackets]
    model_mass = sum(model_norm[i] for i in idxs)

    yes_prices = [leg_price(row, "BUY_YES") for row in inside]
    no_prices = [leg_price(row, "BUY_NO") for row in outside]
    yes_cost = sum(float(x) for x in yes_prices) if all(x is not None for x in yes_prices) else None
    no_eff_cost = (
        sum(float(x) for x in no_prices) - (len(outside) - 1)
        if outside and all(x is not None for x in no_prices)
        else None
    )
    if yes_cost is None and no_eff_cost is None:
        return None
    if no_eff_cost is not None and yes_cost is not None and no_eff_cost < yes_cost:
        expression = "outside_no"
        selected_source_rows = outside
        side = "BUY_NO"
        effective_cost = no_eff_cost
    else:
        expression = "inside_yes"
        selected_source_rows = inside
        side = "BUY_YES"
        effective_cost = yes_cost

    if effective_cost is None:
        return None
    orderbook_edge = model_mass - float(effective_cost)
    legs = []
    missing_reasons: list[str] = []
    for row in selected_source_rows:
        price = leg_price(row, side)
        size = leg_size(row, side)
        depth = leg_depth(row, side)
        status = leg_status(row, side)
        if price is None:
            missing_reasons.append(f"missing_{side.lower()}_ask")
        if size is None:
            missing_reasons.append(f"missing_{side.lower()}_ask_size")
        if status and status != "ok":
            missing_reasons.append(f"{side.lower()}_book_status_{status}")
        legs.append(
            {
                "side": side,
                "bracket": str(row.get("bracket")),
                "condition_id": row.get("condition_id"),
                "market_id": row.get("market_id"),
                "token_id": token_id(row, side),
                "best_ask": price,
                "ask_size": size,
                "depth_ask_5c": depth,
                "book_status": status,
                "book_fetched_at_utc": row.get("yes_book_fetched_at_utc" if side == "BUY_YES" else "no_book_fetched_at_utc"),
            }
        )

    min_ask_size = min((float(leg["ask_size"]) for leg in legs if leg["ask_size"] is not None), default=None)
    reject_reasons = []
    if missing_reasons:
        reject_reasons.extend(sorted(set(missing_reasons)))
    if not (0.0 < float(effective_cost) <= 0.95):
        reject_reasons.append("effective_cost_out_of_bounds")
    if orderbook_edge < edge_threshold:
        reject_reasons.append("edge_below_threshold")
    if min_ask_size is None or min_ask_size < min_top_ask_size:
        reject_reasons.append("top_ask_size_below_min")

    stem = "|".join(
        [
            STRATEGY_ID,
            decision_snapshot_ts_utc,
            city,
            event_date,
            forecast_source,
            model_version,
            expression,
            ",".join(sorted(inside_brackets, key=lambda x: bracket_key(x))),
        ]
    )
    candidate_id = hashlib.sha256(stem.encode()).hexdigest()[:24]
    selected = not reject_reasons
    return {
        "record_type": "range_rv_shadow_candidate",
        "strategy_id": STRATEGY_ID,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "candidate_id": candidate_id,
        "shadow_generated_at_utc": now_utc(),
        "snapshot_path": str(snapshot_path),
        "snapshot_ts_utc": snapshot_ts_utc,
        "city": city,
        "event_date": event_date,
        "forecast_source": forecast_source,
        "model_version": model_version,
        "decision_snapshot_ts_utc": decision_snapshot_ts_utc,
        "city_pool": rows[0].get("city_pool"),
        "source_bucket": source_meta["source_bucket"],
        "settlement_source_class": source_meta["settlement_source_class"],
        "official_station_or_feed": source_meta.get("official_station_or_feed"),
        "mapping_rule": source_meta.get("mapping_rule"),
        "algorithm": "forecast_bounded_w3_cheaper",
        "row_filter": "default_wu/no_filter",
        "expression": expression,
        "inside_brackets": [str(row.get("bracket")) for row in inside],
        "range_model_mass_norm": model_mass,
        "inside_yes_cost": yes_cost,
        "outside_no_effective_cost": no_eff_cost,
        "effective_range_cost": float(effective_cost),
        "orderbook_edge": orderbook_edge,
        "edge_threshold": edge_threshold,
        "min_top_ask_size_required": min_top_ask_size,
        "min_leg_ask_size": min_ask_size,
        "selected": selected,
        "reject_reasons": reject_reasons,
        "legs": legs,
    }


def existing_candidate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get("candidate_id")
            if value:
                ids.add(str(value))
    return ids


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    snapshot_path, snapshot = load_latest_snapshot(args.snapshot_glob)
    snapshot_ts_utc = str(snapshot.get("ts_utc") or "")
    if not snapshot_ts_utc:
        raise ValueError(f"snapshot missing ts_utc: {snapshot_path}")

    registry = load_source_registry()
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    skipped_source: dict[str, int] = {}
    skipped_pool = 0
    for row in snapshot["records"]:
        if row.get("record_type") != "edge_signal" or row.get("probability_status") != "ok":
            continue
        if args.city_pool != "all" and row.get("city_pool") != args.city_pool:
            skipped_pool += 1
            continue
        city = str(row.get("city"))
        meta = registry.get(city)
        bucket = meta["source_bucket"] if meta else "missing_registry"
        if bucket != "default_wu":
            skipped_source[bucket] = skipped_source.get(bucket, 0) + 1
            continue
        event_date = str(row.get("event_date"))
        forecast_source = str(row.get("forecast_source") or "")
        model_version = str(row.get("model") or "")
        decision_snapshot_ts_utc = str(row.get("ts_utc") or snapshot_ts_utc)
        key = (city, event_date, forecast_source, model_version, decision_snapshot_ts_utc)
        groups.setdefault(key, []).append(row)

    candidates: list[dict[str, Any]] = []
    for key, rows in groups.items():
        city = key[0]
        candidate = make_candidate(
            group_key=key,
            rows=rows,
            source_meta=registry[city],
            edge_threshold=args.edge_threshold,
            min_top_ask_size=args.min_top_ask_size,
            snapshot_ts_utc=snapshot_ts_utc,
            snapshot_path=snapshot_path,
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda row: float(row["orderbook_edge"]), reverse=True)

    selected = [row for row in candidates if row["selected"]]
    write_rows = candidates if args.write_all else selected
    if args.max_candidates > 0:
        write_rows = write_rows[: args.max_candidates]

    journal_path = Path(args.journal)
    seen = existing_candidate_ids(journal_path)
    new_rows = [row for row in write_rows if str(row["candidate_id"]) not in seen]
    append_jsonl(journal_path, new_rows)

    summary = {
        "record_type": "range_rv_shadow_summary",
        "strategy_id": STRATEGY_ID,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "generated_at_utc": now_utc(),
        "snapshot_path": str(snapshot_path),
        "snapshot_ts_utc": snapshot_ts_utc,
        "city_pool": args.city_pool,
        "edge_threshold": args.edge_threshold,
        "min_top_ask_size": args.min_top_ask_size,
        "snapshot_records": len(snapshot["records"]),
        "eligible_groups": len(groups),
        "evaluated_candidates": len(candidates),
        "selected_candidates": len(selected),
        "journal_appended_rows": len(new_rows),
        "journal_path": str(journal_path),
        "skipped_pool_records": skipped_pool,
        "skipped_source_records": skipped_source,
        "top_selected": selected[:10],
        "top_rejected": [row for row in candidates if not row["selected"]][:10],
    }
    write_summary(Path(args.summary), summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
