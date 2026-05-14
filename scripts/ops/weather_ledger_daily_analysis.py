#!/usr/bin/env python3
"""Reusable daily analysis for weather paper ledger trades."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


DEFAULT_ROOT = Path("runtime/weather_edge_v1/market_data")
DEFAULT_TRADES = DEFAULT_ROOT / "research" / "t24_paper_ledger_trades.csv"
DEFAULT_REPLAY = DEFAULT_ROOT / "research" / "t24_paper_snapshot_replay_trades.csv"
DEFAULT_OUT_DIR = DEFAULT_ROOT / "research" / "daily_ledger_analysis"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze weather paper ledger trades with a fixed daily template.")
    parser.add_argument("--event-date", help="Event date to analyze, e.g. 2026-05-12.")
    parser.add_argument("--all-settled", action="store_true", help="Analyze all settled rows instead of one event date.")
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--snapshot-replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args()


def as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"missing trades file: {path}")
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def edge_bucket(row: dict[str, Any]) -> str:
    edge = abs(as_float(row.get("abs_edge") or row.get("edge")))
    if edge < 0.15:
        return "10-15%"
    if edge < 0.25:
        return "15-25%"
    if edge < 0.30:
        return "25-30%"
    if edge < 0.40:
        return "30-40%"
    return "40%+"


def edge_30_bucket(row: dict[str, Any]) -> str:
    return ">=30%" if abs(as_float(row.get("abs_edge") or row.get("edge"))) >= 0.30 else "<30%"


def entry_bucket(row: dict[str, Any]) -> str:
    price = as_float(row.get("entry_price"))
    if price < 0.10:
        return "<10c"
    if price < 0.25:
        return "10-25c"
    if price < 0.50:
        return "25-50c"
    if price < 0.75:
        return "50-75c"
    return "75c+"


def enrich(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for row in rows:
        item = dict(row)
        item["edge_bucket"] = edge_bucket(item)
        item["edge_30_bucket"] = edge_30_bucket(item)
        item["entry_bucket"] = entry_bucket(item)
        item["side_edge_bucket"] = f"{item.get('side') or 'unknown'}_{item['edge_bucket']}"
        out.append(item)
    return out


def select_rows(rows: list[dict[str, str]], event_date: str | None, all_settled: bool) -> list[dict[str, str]]:
    if all_settled:
        return [r for r in rows if r.get("settlement_status") == "settled"]
    if not event_date:
        raise SystemExit("provide --event-date or --all-settled")
    return [r for r in rows if r.get("event_date") == event_date]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if r.get("settlement_status") == "settled"]
    missing = [r for r in rows if r.get("settlement_status") != "settled"]
    wins = sum(1 for r in settled if as_bool(r.get("won")))
    total_cost = sum(as_float(r.get("cost_usd")) for r in rows)
    settled_cost = sum(as_float(r.get("cost_usd")) for r in settled)
    pnl = sum(as_float(r.get("pnl_usd")) for r in settled)
    return {
        "orders": len(rows),
        "settled": len(settled),
        "missing": len(missing),
        "wins": wins,
        "win_rate": wins / len(settled) if settled else None,
        "total_cost": total_cost,
        "settled_cost": settled_cost,
        "pnl": pnl,
        "roi": pnl / settled_cost if settled_cost else None,
        "avg_abs_edge": sum(abs(as_float(r.get("abs_edge") or r.get("edge"))) for r in rows) / len(rows) if rows else None,
        "avg_entry_price": sum(as_float(r.get("entry_price")) for r in rows) / len(rows) if rows else None,
    }


def grouped(rows: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_cost = sum(as_float(r.get("cost_usd")) for r in rows)
    for row in rows:
        buckets[key_fn(row)].append(row)
    result = []
    for key, group_rows in buckets.items():
        stats = summarize(group_rows)
        stats["key"] = key
        stats["cost_pct"] = stats["total_cost"] / total_cost if total_cost else None
        result.append(stats)
    return sorted(result, key=lambda x: (-as_float(x.get("pnl")), -int(x.get("orders") or 0), str(x.get("key"))))


def top_trades(rows: list[dict[str, Any]], reverse: bool, limit: int = 8) -> list[dict[str, Any]]:
    settled = [r for r in rows if r.get("settlement_status") == "settled"]
    ordered = sorted(settled, key=lambda r: as_float(r.get("pnl_usd")), reverse=reverse)
    keys = ["city", "side", "bracket", "model", "entry_price", "cost_usd", "edge", "pnl_usd", "won"]
    return [{k: r.get(k) for k in keys} for r in ordered[:limit]]


def fmt_money(value: Any) -> str:
    return "" if value is None else f"${as_float(value):+.2f}"


def fmt_pct(value: Any) -> str:
    return "" if value is None else f"{as_float(value) * 100:+.1f}%"


def fmt_num(value: Any, digits: int = 3) -> str:
    return "" if value is None else f"{as_float(value):.{digits}f}"


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], limit: int | None = None) -> str:
    rows = rows[:limit] if limit else rows
    lines = [
        "| " + " | ".join(title for title, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key)
            if key in {"pnl", "total_cost", "settled_cost"}:
                values.append(fmt_money(value))
            elif key in {"roi", "win_rate", "cost_pct"}:
                values.append(fmt_pct(value))
            elif key in {"avg_abs_edge", "avg_entry_price"}:
                values.append(fmt_num(value))
            else:
                values.append("" if value is None else str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def build_report(label: str, rows: list[dict[str, Any]], replay_rows: list[dict[str, Any]] | None) -> tuple[str, dict[str, Any]]:
    overall = summarize(rows)
    by_pool = grouped(rows, lambda r: r.get("city_pool") or "unknown")
    by_city = grouped(rows, lambda r: r.get("city") or "unknown")
    by_side = grouped(rows, lambda r: r.get("side") or "unknown")
    by_model = grouped(rows, lambda r: r.get("model") or "unknown")
    by_edge = grouped(rows, lambda r: r.get("edge_bucket") or "unknown")
    by_edge_30 = grouped(rows, lambda r: r.get("edge_30_bucket") or "unknown")
    by_side_edge = grouped(rows, lambda r: r.get("side_edge_bucket") or "unknown")
    by_entry = grouped(rows, lambda r: r.get("entry_bucket") or "unknown")
    missing = [r for r in rows if r.get("settlement_status") != "settled"]

    replay_summary = summarize(replay_rows or []) if replay_rows is not None else None
    data = {
        "label": label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall": overall,
        "by_pool": by_pool,
        "by_city": by_city,
        "by_side": by_side,
        "by_model": by_model,
        "by_edge_bucket": by_edge,
        "by_edge_30_bucket": by_edge_30,
        "by_side_edge_bucket": by_side_edge,
        "by_entry_bucket": by_entry,
        "top_losses": top_trades(rows, reverse=False),
        "top_gains": top_trades(rows, reverse=True),
        "missing": missing,
        "snapshot_replay_overall": replay_summary,
    }

    cols = [
        ("key", "key"), ("orders", "orders"), ("settled", "settled"), ("missing", "missing"),
        ("cost", "total_cost"), ("cost_pct", "cost_pct"), ("pnl", "pnl"), ("roi", "roi"),
        ("wr", "win_rate"), ("avg_edge", "avg_abs_edge"), ("avg_entry", "avg_entry_price"),
    ]
    simple_cols = [
        ("key", "key"), ("orders", "orders"), ("settled", "settled"), ("missing", "missing"),
        ("cost", "total_cost"), ("pnl", "pnl"), ("roi", "roi"), ("wr", "win_rate"),
    ]
    trade_cols = [
        ("city", "city"), ("side", "side"), ("bracket", "bracket"), ("model", "model"),
        ("entry", "entry_price"), ("cost", "cost_usd"), ("edge", "edge"), ("pnl", "pnl_usd"), ("won", "won"),
    ]
    lines = [
        f"# Weather Paper Ledger Analysis - {label}",
        "",
        "## Overall",
        markdown_table([{"key": "overall", **overall}], simple_cols),
        "",
        "## Edge >=30% Check",
        markdown_table(by_edge_30, simple_cols),
        "",
        "## By City",
        markdown_table(by_city, cols),
        "",
        "## By Side",
        markdown_table(by_side, cols),
        "",
        "## By Model",
        markdown_table(by_model, cols),
        "",
        "## By Edge Bucket",
        markdown_table(by_edge, simple_cols),
        "",
        "## By Side x Edge Bucket",
        markdown_table(by_side_edge, simple_cols),
        "",
        "## By Entry Price Bucket",
        markdown_table(by_entry, simple_cols),
        "",
        "## Top Losses",
        markdown_table(data["top_losses"], trade_cols),
        "",
        "## Top Gains",
        markdown_table(data["top_gains"], trade_cols),
        "",
        "## Missing",
        f"- missing rows: {len(missing)}",
    ]
    if replay_summary is not None:
        lines += [
            "",
            "## Snapshot Replay Overall",
            markdown_table([{"key": "snapshot_replay", **replay_summary}], simple_cols),
        ]
    return "\n".join(lines) + "\n", data


def safe_label(event_date: str | None, all_settled: bool) -> str:
    return "all_settled" if all_settled else str(event_date)


def main() -> int:
    args = parse_args()
    rows = enrich(load_rows(args.trades))
    selected = select_rows(rows, args.event_date, args.all_settled)

    replay_selected = None
    if args.snapshot_replay.exists():
        replay_rows = enrich(load_rows(args.snapshot_replay))
        replay_selected = select_rows(replay_rows, args.event_date, args.all_settled)

    label = safe_label(args.event_date, args.all_settled)
    report, data = build_report(label, selected, replay_selected)
    print(report)

    if not args.no_write:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        md_path = args.out_dir / f"weather_ledger_analysis_{label}.md"
        json_path = args.out_dir / f"weather_ledger_analysis_{label}.json"
        md_path.write_text(report)
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"wrote {md_path}")
        print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
