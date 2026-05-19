from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DATA_ROOT = Path("runtime/weather_edge_v1/market_data")
DEFAULT_OUT_ROOT = Path("runtime/weather_low_price_gamma_v1/research")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _snapshot_files(data_root: Path) -> list[Path]:
    return sorted((data_root / "paper_snapshots").glob("snapshot_*.json"))


def _iter_snapshot_records(data_root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in _snapshot_files(data_root):
        try:
            payload = _json_load(path)
        except Exception:
            continue
        for record in payload.get("records", []) or []:
            if isinstance(record, dict):
                yield path, record


def _market_key(record: dict[str, Any]) -> str:
    condition_id = str(record.get("condition_id") or "").strip()
    side = str(record.get("side") or "").strip().upper()
    if condition_id:
        return f"{condition_id}|{side}"
    return "|".join(
        [
            str(record.get("city") or ""),
            str(record.get("event_date") or ""),
            str(record.get("bracket") or ""),
            str(record.get("unit") or ""),
            side,
        ]
    )


def _is_low_yes_candidate(
    record: dict[str, Any],
    *,
    min_price: float,
    max_price: float,
    min_prob_to_price: float,
    min_abs_edge: float,
) -> bool:
    if str(record.get("side") or "").upper() != "BUY_YES":
        return False
    entry = _float(record.get("entry_price") or record.get("market_yes_price"))
    prob = _float(record.get("model_prob"))
    edge = prob - entry
    if entry < min_price or entry > max_price:
        return False
    if entry <= 0 or prob / entry < min_prob_to_price:
        return False
    return edge >= min_abs_edge


def _price(record: dict[str, Any]) -> float:
    return _float(record.get("market_yes_price") or record.get("entry_price") or record.get("last_trade_price"))


def _load_resolution_map(data_root: Path) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for path in (data_root / "cache" / "pm_history").glob("*_20*.json"):
        try:
            payload = _json_load(path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        city = str(payload.get("city") or path.stem.rsplit("_", 1)[0])
        target_date = str(payload.get("date") or path.stem.rsplit("_", 1)[-1])
        for bracket in payload.get("brackets", []) or []:
            if not isinstance(bracket, dict):
                continue
            label = str(bracket.get("label") or "")
            final_price = bracket.get("final_price")
            if label and final_price is not None:
                out[(city, target_date, label)] = _float(final_price)
    return out


def _settlement(record: dict[str, Any], resolutions: dict[tuple[str, str, str], float]) -> float | None:
    key = (
        str(record.get("city") or ""),
        str(record.get("event_date") or ""),
        str(record.get("bracket") or ""),
    )
    return resolutions.get(key)


@dataclass(frozen=True)
class RuleResult:
    pnl: float | None
    exit_price: float | None
    exit_reason: str


def _rule_results(entry: float, final: float | None, max_4h: float | None) -> dict[str, RuleResult]:
    target_50 = entry * 1.5
    target_100 = entry * 2.0
    touched_50 = max_4h is not None and max_4h >= target_50
    touched_100 = max_4h is not None and max_4h >= target_100
    settlement_pnl = None if final is None else final - entry
    out = {
        "hold": RuleResult(settlement_pnl, final, "settlement" if final is not None else "missing_resolution"),
        "tp50_full": RuleResult(
            target_50 - entry if touched_50 else settlement_pnl,
            target_50 if touched_50 else final,
            "tp50" if touched_50 else ("settlement" if final is not None else "missing_resolution"),
        ),
        "tp100_full": RuleResult(
            target_100 - entry if touched_100 else settlement_pnl,
            target_100 if touched_100 else final,
            "tp100" if touched_100 else ("settlement" if final is not None else "missing_resolution"),
        ),
    }
    if touched_100:
        pnl = 0.5 * (target_50 - entry) + 0.5 * (target_100 - entry)
        out["tp50_half_tp100_clear"] = RuleResult(pnl, target_100, "tp50_then_tp100")
        pnl_tail = 0.5 * (target_50 - entry) + 0.3 * (target_100 - entry)
        pnl_tail = None if final is None else pnl_tail + 0.2 * (final - entry)
        out["tp50_half_tp100_30pct_tail20"] = RuleResult(
            pnl_tail,
            final if final is not None else target_100,
            "tp50_then_tp100_tail" if final is not None else "missing_resolution",
        )
    elif touched_50:
        pnl = None if final is None else 0.5 * (target_50 - entry) + 0.5 * (final - entry)
        out["tp50_half_tp100_clear"] = RuleResult(
            pnl,
            final if final is not None else target_50,
            "tp50_then_settlement" if final is not None else "missing_resolution",
        )
        out["tp50_half_tp100_30pct_tail20"] = RuleResult(
            pnl,
            final if final is not None else target_50,
            "tp50_then_settlement" if final is not None else "missing_resolution",
        )
    else:
        out["tp50_half_tp100_clear"] = RuleResult(settlement_pnl, final, "settlement" if final is not None else "missing_resolution")
        out["tp50_half_tp100_30pct_tail20"] = RuleResult(settlement_pnl, final, "settlement" if final is not None else "missing_resolution")
    return out


def audit(args: argparse.Namespace) -> int:
    data_root = Path(args.data_root)
    out_root = Path(args.out_root)
    snapshot_paths = _snapshot_files(data_root)
    snapshot_records = 0
    snapshot_dates: Counter[str] = Counter()
    snapshot_cities: Counter[str] = Counter()
    low_yes = 0
    for _, record in _iter_snapshot_records(data_root):
        snapshot_records += 1
        snapshot_dates[str(record.get("event_date") or "")] += 1
        snapshot_cities[str(record.get("city") or "")] += 1
        if _is_low_yes_candidate(
            record,
            min_price=args.min_price,
            max_price=args.max_price,
            min_prob_to_price=args.min_prob_to_price,
            min_abs_edge=args.min_abs_edge,
        ):
            low_yes += 1

    history_files = sorted((data_root / "clob_price_history").glob("*/*/*.json"))
    history_dates: Counter[str] = Counter()
    history_cities: Counter[str] = Counter()
    history_points: list[int] = []
    for path in history_files:
        try:
            history_dates[path.parts[-3]] += 1
            history_cities[path.parts[-2]] += 1
            history_points.append(len((_json_load(path).get("history") or [])))
        except Exception:
            pass

    live_files = sorted((data_root / "live_orderbook").glob("*/*.jsonl.gz"))
    live_rows = 0
    live_token_obs = 0
    live_nonzero_obs = 0
    for path in live_files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    live_rows += 1
                    payload = json.loads(line)
                    for orderbook in (payload.get("orderbooks") or {}).values():
                        live_token_obs += 1
                        if _float(orderbook.get("best_bid")) > 0 or _float(orderbook.get("best_ask")) > 0:
                            live_nonzero_obs += 1
        except Exception:
            continue

    summary = {
        "generated_at_utc": _now_utc(),
        "data_root": str(data_root),
        "filters": {
            "side": "BUY_YES",
            "min_price": args.min_price,
            "max_price": args.max_price,
            "min_prob_to_price": args.min_prob_to_price,
            "min_abs_edge": args.min_abs_edge,
        },
        "paper_snapshots": {
            "files": len(snapshot_paths),
            "records": snapshot_records,
            "date_count": len([k for k in snapshot_dates if k]),
            "first_date": min([k for k in snapshot_dates if k], default=""),
            "last_date": max([k for k in snapshot_dates if k], default=""),
            "top_cities": snapshot_cities.most_common(30),
            "low_price_yes_candidates": low_yes,
        },
        "clob_price_history": {
            "files": len(history_files),
            "date_count": len(history_dates),
            "first_date": min(history_dates, default=""),
            "last_date": max(history_dates, default=""),
            "top_cities": history_cities.most_common(30),
            "points_p50": statistics.median(history_points) if history_points else 0,
            "points_max": max(history_points) if history_points else 0,
        },
        "live_orderbook": {
            "files": len(live_files),
            "rows": live_rows,
            "token_observations": live_token_obs,
            "nonzero_token_observations": live_nonzero_obs,
            "nonzero_rate": round(live_nonzero_obs / live_token_obs, 4) if live_token_obs else 0.0,
        },
        "research_readiness": {
            "weak_snapshot_gamma": snapshot_records > 0 and low_yes > 0,
            "price_history_gamma": len(history_files) > 0,
            "executable_orderbook_gamma": live_nonzero_obs > 0,
            "warning": "Snapshot and price-history studies are path proxies. Real executable gamma requires bid/ask orderbook coverage.",
        },
    }
    json_path = out_root / "research_data_audit.json"
    md_path = out_root / "research_data_audit.md"
    _write_json(json_path, summary)
    md_path.write_text(
        "\n".join(
            [
                "# Weather Low Price Gamma Data Audit",
                "",
                f"Generated: `{summary['generated_at_utc']}`",
                "",
                f"- Snapshot files: `{summary['paper_snapshots']['files']}`",
                f"- Snapshot records: `{summary['paper_snapshots']['records']}`",
                f"- Low-price YES candidates: `{low_yes}`",
                f"- CLOB price history files: `{len(history_files)}`",
                f"- Live orderbook files: `{len(live_files)}`",
                f"- Live nonzero orderbook observation rate: `{summary['live_orderbook']['nonzero_rate']}`",
                "",
                "Readiness:",
                "",
                f"- Weak snapshot gamma: `{summary['research_readiness']['weak_snapshot_gamma']}`",
                f"- Price-history gamma: `{summary['research_readiness']['price_history_gamma']}`",
                f"- Executable orderbook gamma: `{summary['research_readiness']['executable_orderbook_gamma']}`",
                "",
                "Warning: snapshot and price-history studies are proxy studies, not executable bid/ask backtests.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "low_price_yes_candidates": low_yes}, indent=2))
    return 0


def weak_backtest(args: argparse.Namespace) -> int:
    data_root = Path(args.data_root)
    out_root = Path(args.out_root)
    resolutions = _load_resolution_map(data_root)
    series: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    candidates: list[tuple[Path, dict[str, Any]]] = []

    for path, record in _iter_snapshot_records(data_root):
        ts = _dt(record.get("ts_utc"))
        px = _price(record)
        if ts is not None and px > 0:
            series[_market_key(record)].append((ts, px))
        if _is_low_yes_candidate(
            record,
            min_price=args.min_price,
            max_price=args.max_price,
            min_prob_to_price=args.min_prob_to_price,
            min_abs_edge=args.min_abs_edge,
        ):
            candidates.append((path, record))

    for points in series.values():
        points.sort(key=lambda item: item[0])

    windows = {"max_price_30m": 0.5, "max_price_1h": 1.0, "max_price_2h": 2.0, "max_price_4h": 4.0}
    rows: list[dict[str, Any]] = []
    rule_pnls: dict[str, list[float]] = defaultdict(list)
    touch_50 = touch_100 = settled = 0

    for path, record in candidates:
        entry_ts = _dt(record.get("ts_utc"))
        entry = _float(record.get("entry_price") or record.get("market_yes_price"))
        if entry_ts is None or entry <= 0:
            continue
        points = series.get(_market_key(record), [])
        max_by_window: dict[str, float | None] = {}
        for label, hours in windows.items():
            end_seconds = hours * 3600
            values = [
                price
                for ts, price in points
                if ts >= entry_ts and (ts - entry_ts).total_seconds() <= end_seconds
            ]
            max_by_window[label] = max(values) if values else None
        max_4h = max_by_window["max_price_4h"]
        if max_4h is not None and max_4h >= entry * 1.5:
            touch_50 += 1
        if max_4h is not None and max_4h >= entry * 2.0:
            touch_100 += 1
        final = _settlement(record, resolutions)
        if final is not None:
            settled += 1
        rule_results = _rule_results(entry, final, max_4h)
        row = {
            "snapshot_file": path.name,
            "city": record.get("city"),
            "city_pool": record.get("city_pool"),
            "icao": record.get("icao"),
            "event_date": record.get("event_date"),
            "bracket": record.get("bracket"),
            "unit": record.get("unit"),
            "model": record.get("model"),
            "entry_time_utc": record.get("ts_utc"),
            "entry_price_proxy": round(entry, 6),
            "model_prob": round(_float(record.get("model_prob")), 6),
            "edge": round(_float(record.get("model_prob")) - entry, 6),
            "prob_to_price": round(_float(record.get("model_prob")) / entry, 6),
            "hours_to_settle": record.get("hours_to_settle"),
            "metar_current_max_f": record.get("metar_current_max_f"),
            "metar_latest_temp_f": record.get("metar_latest_temp_f"),
            **{k: (round(v, 6) if v is not None else "") for k, v in max_by_window.items()},
            "touch_50_4h": bool(max_4h is not None and max_4h >= entry * 1.5),
            "touch_100_4h": bool(max_4h is not None and max_4h >= entry * 2.0),
            "final_resolution": "" if final is None else final,
        }
        for name, result in rule_results.items():
            row[f"{name}_pnl_proxy"] = "" if result.pnl is None else round(result.pnl, 6)
            row[f"{name}_exit_reason"] = result.exit_reason
            if result.pnl is not None:
                rule_pnls[name].append(result.pnl)
        rows.append(row)

    def summarize(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"n": 0, "pnl": 0.0, "avg_pnl": 0.0, "roi_per_dollar": 0.0}
        return {
            "n": len(values),
            "pnl": round(sum(values), 6),
            "avg_pnl": round(sum(values) / len(values), 6),
        }

    summary = {
        "generated_at_utc": _now_utc(),
        "method": "weak_snapshot_price_proxy",
        "warning": "Uses snapshot market_yes_price as path proxy. It does not use executable bid/ask.",
        "filters": {
            "side": "BUY_YES",
            "min_price": args.min_price,
            "max_price": args.max_price,
            "min_prob_to_price": args.min_prob_to_price,
            "min_abs_edge": args.min_abs_edge,
        },
        "candidates": len(rows),
        "settled_candidates": settled,
        "touch_50_4h": touch_50,
        "touch_100_4h": touch_100,
        "touch_50_4h_rate": round(touch_50 / len(rows), 4) if rows else 0.0,
        "touch_100_4h_rate": round(touch_100 / len(rows), 4) if rows else 0.0,
        "rules": {name: summarize(values) for name, values in sorted(rule_pnls.items())},
        "by_city": {},
    }
    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_city[str(row.get("city") or "")].append(row)
    for city, city_rows in by_city.items():
        summary["by_city"][city] = {
            "n": len(city_rows),
            "touch_50_4h_rate": round(sum(1 for r in city_rows if r["touch_50_4h"]) / len(city_rows), 4),
            "touch_100_4h_rate": round(sum(1 for r in city_rows if r["touch_100_4h"]) / len(city_rows), 4),
        }

    csv_path = out_root / "weak_gamma_candidates.csv"
    json_path = out_root / "weak_gamma_summary.json"
    _write_csv(csv_path, rows)
    _write_json(json_path, summary)
    print(json.dumps({"summary": str(json_path), "candidates_csv": str(csv_path), "candidates": len(rows)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather low-price YES gamma research tools.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--min-price", type=float, default=0.05)
    parser.add_argument("--max-price", type=float, default=0.20)
    parser.add_argument("--min-prob-to-price", type=float, default=1.5)
    parser.add_argument("--min-abs-edge", type=float, default=0.03)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="Audit local mirror coverage for this research.")
    sub.add_parser("weak-backtest", help="Run snapshot-price proxy gamma research.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "audit":
        return audit(args)
    if args.command == "weak-backtest":
        return weak_backtest(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
