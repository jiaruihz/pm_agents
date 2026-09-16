#!/usr/bin/env python3
"""Long-window PIT orderbook replay for HeadA max_taker_cushion.

The live runner journal only has fresh-book rows from July onward.  This script
uses historical orderbook snapshots to approximate the same question over a
longer window: after a fact candidate appears, what happens if execution sees
the next available YES book and uses different max_taker_cushion values?
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
ORDERBOOK_DEFAULT = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
OUT_DEFAULT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_cushion_long_window_v1"
REPORT_DEFAULT = ROOT / "docs/analysis/2026-07/2026-07-09-low-price-yes-cushion-long-window-v1.md"

TAKER_FEE_RATE = 0.05
MIN_ASK = 0.05
MAX_ASK = 0.20
MIN_EDGE = 0.20
MIN_FEE_EDGE = 0.15
CUSHIONS = (0.01, 0.02, 0.03, 0.05, 0.08)


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_ts(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_city(value: Any) -> str:
    return safe_str(value).strip().lower().replace(" ", "")


def first_number(value: Any) -> float:
    m = re.search(r"-?\d+(?:\.\d+)?", safe_str(value))
    return float(m.group(0)) if m else math.nan


def bracket_low_f(bracket: Any, unit: Any) -> float:
    low = first_number(bracket)
    if not math.isfinite(low):
        return math.nan
    if safe_str(unit).upper().startswith("C"):
        return low * 9.0 / 5.0 + 32.0
    return low


def bracket_width_f(unit: Any) -> float:
    return 1.8 if safe_str(unit).upper().startswith("C") else 2.0


def fee_per_share(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)


def price_tier_shares(price: float) -> float:
    if price <= 0.08:
        return 6.0
    if price <= 0.14:
        return 8.0
    return 10.0


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def load_candidates(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH base AS (
          SELECT
            candidate_id, condition_id, market_id, event_date AS target_date,
            city, bracket, side, unit, forecast_source, model_version,
            forecast_max_f, forecast_max_native, decision_snapshot_ts_utc,
            decision_hours_to_settle, model_p_yes, edge, decision_entry_price,
            settlement_status, final_yes,
            ROW_NUMBER() OVER (
              PARTITION BY event_date, city
              ORDER BY decision_snapshot_ts_utc ASC, decision_entry_price ASC, edge DESC, bracket ASC, candidate_id ASC
            ) AS city_date_rank
          FROM fact_signal_candidates
          WHERE side = 'BUY_YES'
            AND event_date BETWEEN ? AND ?
            AND settlement_status = 'settled'
            AND final_yes IS NOT NULL
            AND decision_snapshot_ts_utc IS NOT NULL
            AND decision_entry_price BETWEEN ? AND ?
            AND edge >= ?
        )
        SELECT *
        FROM base
        WHERE city_date_rank = 1
        ORDER BY target_date, decision_snapshot_ts_utc, city
        """,
        (start, end, MIN_ASK, MAX_ASK, MIN_EDGE),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        low_f = bracket_low_f(item["bracket"], item["unit"])
        width = bracket_width_f(item["unit"])
        forecast_f = to_float(item.get("forecast_max_f"), math.nan)
        dist_br = (low_f - forecast_f) / width if math.isfinite(low_f) and math.isfinite(forecast_f) else math.nan
        item["bracket_low_f"] = low_f
        item["forecast_to_bracket_low_br"] = dist_br
        item["hot_tail_boundary_v1"] = 1 if math.isfinite(dist_br) and dist_br > 0 else 0
        item["decision_dt"] = parse_ts(item["decision_snapshot_ts_utc"])
        if item["decision_dt"] is None:
            continue
        out.append(item)
    return out


def snapshot_file_ts(path: Path) -> datetime | None:
    m = re.search(r"orderbook_snapshot_(\d{8})_(\d{4})", path.name)
    if not m:
        return None
    try:
        return datetime.strptime("".join(m.groups()), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def orderbook_files(root: Path, start_dt: datetime, end_dt: datetime) -> list[Path]:
    files = list(root.rglob("orderbook_snapshot_*.jsonl.gz")) + list(root.rglob("orderbook_snapshot_*.jsonl"))
    out: list[Path] = []
    for path in files:
        ts = snapshot_file_ts(path)
        if ts is None:
            continue
        if start_dt - timedelta(hours=1) <= ts <= end_dt + timedelta(hours=6):
            out.append(path)
    return sorted(out, key=lambda p: snapshot_file_ts(p) or datetime.min.replace(tzinfo=timezone.utc))


def load_next_book_matches(
    candidates: list[dict[str, Any]],
    root: Path,
    *,
    execution_delay_minutes: float,
    max_lag_minutes: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates:
        return [], {"status": "no_candidates"}
    min_dt = min(c["decision_dt"] for c in candidates)
    max_dt = max(c["decision_dt"] for c in candidates)
    by_condition: dict[str, list[int]] = defaultdict(list)
    for idx, row in enumerate(candidates):
        by_condition[safe_str(row["condition_id"])].append(idx)
    remaining = set(range(len(candidates)))
    matches: dict[int, dict[str, Any]] = {}
    files = orderbook_files(root, min_dt, max_dt)
    scanned_rows = 0
    relevant_rows = 0
    min_delay = timedelta(minutes=execution_delay_minutes)
    max_wait = timedelta(minutes=max_lag_minutes)

    for path in files:
        file_ts = snapshot_file_ts(path)
        if file_ts is not None and file_ts > max_dt + min_delay + max_wait:
            break
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    scanned_rows += 1
                    try:
                        book = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if safe_str(book.get("outcome")).lower() != "yes":
                        continue
                    idxs = by_condition.get(safe_str(book.get("condition_id")))
                    if not idxs:
                        continue
                    book_dt = parse_ts(book.get("snapshot_ts_utc")) or parse_ts(book.get("fetched_at_utc"))
                    if book_dt is None:
                        continue
                    summary = book.get("summary") or {}
                    best_ask = to_float(summary.get("best_ask"), math.nan)
                    if not math.isfinite(best_ask):
                        asks = ((book.get("raw") or {}).get("asks") or [])
                        if asks:
                            best_ask = min(to_float(a.get("price"), math.nan) for a in asks)
                    if not math.isfinite(best_ask):
                        continue
                    for idx in idxs:
                        if idx not in remaining:
                            continue
                        dt = candidates[idx]["decision_dt"]
                        target_dt = dt + min_delay
                        if target_dt <= book_dt <= target_dt + max_wait:
                            relevant_rows += 1
                            matches[idx] = {
                                "execution_book_ts_utc": book_dt.isoformat(),
                                "execution_lag_minutes": (book_dt - dt).total_seconds() / 60.0,
                                "execution_delay_target_minutes": execution_delay_minutes,
                                "execution_wait_after_target_minutes": (book_dt - target_dt).total_seconds() / 60.0,
                                "fresh_best_ask": best_ask,
                                "fresh_best_bid": to_float(summary.get("best_bid"), math.nan),
                                "fresh_best_ask_size": to_float(summary.get("ask_size"), math.nan),
                                "orderbook_path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                            }
                            remaining.remove(idx)
        except OSError:
            continue

    out: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates):
        match = matches.get(idx)
        if not match:
            continue
        row = dict(candidate)
        row.pop("decision_dt", None)
        row.update(match)
        out.append(row)
    return out, {
        "status": "ok",
        "orderbook_root": str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
        "orderbook_files": len(files),
        "candidate_rows": len(candidates),
        "matched_rows": len(out),
        "matched_rate": len(out) / len(candidates) if candidates else math.nan,
        "scanned_rows": scanned_rows,
        "relevant_book_rows": relevant_rows,
        "execution_delay_minutes": execution_delay_minutes,
        "max_lag_minutes": max_lag_minutes,
        "min_decision_ts_utc": min_dt.isoformat(),
        "max_decision_ts_utc": max_dt.isoformat(),
    }


def select_rows(rows: list[dict[str, Any]], cushion: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("hot_tail_boundary_v1") or 0) != 1:
            continue
        snapshot_ask = to_float(row.get("decision_entry_price"), math.nan)
        fresh_ask = to_float(row.get("fresh_best_ask"), math.nan)
        p_yes = to_float(row.get("model_p_yes"), math.nan)
        if not (math.isfinite(snapshot_ask) and math.isfinite(fresh_ask) and math.isfinite(p_yes)):
            continue
        max_taker = min(MAX_ASK, snapshot_ask + cushion)
        fee = fee_per_share(fresh_ask)
        fee_edge = p_yes - fresh_ask - fee
        if fresh_ask > max_taker + 1e-9 or fee_edge < MIN_FEE_EDGE - 1e-9:
            continue
        final_yes = to_float(row.get("final_yes"), 0.0)
        per_share_cost = fresh_ask + fee
        per_share_pnl = final_yes - per_share_cost
        shares = price_tier_shares(fresh_ask)
        selected = {
            **{k: row.get(k) for k in [
                "target_date", "city", "bracket", "condition_id", "forecast_source", "model_version",
                "decision_snapshot_ts_utc", "execution_book_ts_utc", "execution_lag_minutes",
                "decision_entry_price", "fresh_best_ask", "fresh_best_bid", "fresh_best_ask_size",
                "model_p_yes", "edge", "forecast_to_bracket_low_br", "final_yes",
            ]},
            "cushion": cushion,
            "max_taker_price": max_taker,
            "taker_fee_per_share": fee,
            "fee_adjusted_edge": fee_edge,
            "win": 1 if final_yes >= 0.5 else 0,
            "per_share_cost": per_share_cost,
            "per_share_pnl": per_share_pnl,
            "price_tier_shares": shares,
            "price_tier_cost": per_share_cost * shares,
            "price_tier_pnl": per_share_pnl * shares,
        }
        out.append(selected)
    return out


def summarize(items: list[dict[str, Any]], *, mode: str, window: str, cushion: float) -> dict[str, Any]:
    cost_key = "per_share_cost" if mode == "per_share" else "price_tier_cost"
    pnl_key = "per_share_pnl" if mode == "per_share" else "price_tier_pnl"
    cost = sum(to_float(r.get(cost_key), 0.0) for r in items)
    pnl = sum(to_float(r.get(pnl_key), 0.0) for r in items)
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0})
    for r in items:
        d = safe_str(r.get("target_date"))
        daily[d]["cost"] += to_float(r.get(cost_key), 0.0)
        daily[d]["pnl"] += to_float(r.get(pnl_key), 0.0)
    losing = [v for v in daily.values() if v["pnl"] < 0]
    le50 = [v for v in daily.values() if v["cost"] > 0 and v["pnl"] / v["cost"] <= -0.5]
    return {
        "window": window,
        "cushion": cushion,
        "mode": mode,
        "rows": len(items),
        "dates": len({r.get("target_date") for r in items}),
        "cities": len({norm_city(r.get("city")) for r in items}),
        "wins": sum(int(r.get("win") or 0) for r in items),
        "win_rate": (sum(int(r.get("win") or 0) for r in items) / len(items)) if items else math.nan,
        "avg_snapshot_ask": (sum(to_float(r.get("decision_entry_price"), 0.0) for r in items) / len(items)) if items else math.nan,
        "avg_fresh_ask": (sum(to_float(r.get("fresh_best_ask"), 0.0) for r in items) / len(items)) if items else math.nan,
        "avg_lag_min": (sum(to_float(r.get("execution_lag_minutes"), 0.0) for r in items) / len(items)) if items else math.nan,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "losing_days": len(losing),
        "le_minus_50pct_days": len(le50),
        "max_daily_loss": min((v["pnl"] for v in daily.values()), default=math.nan),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(x: Any) -> str:
    v = to_float(x, math.nan)
    return "NA" if not math.isfinite(v) else f"{v * 100:.1f}%"


def fmt_num(x: Any, n: int = 3) -> str:
    v = to_float(x, math.nan)
    return "NA" if not math.isfinite(v) else f"{v:.{n}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--orderbook-root", type=Path, default=ORDERBOOK_DEFAULT)
    parser.add_argument("--start", default="2026-05-29")
    parser.add_argument("--end", default="2026-07-07")
    parser.add_argument("--execution-delay-minutes", type=float, default=0.0)
    parser.add_argument("--max-lag-minutes", type=float, default=60.0)
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--report", type=Path, default=REPORT_DEFAULT)
    args = parser.parse_args()

    conn = connect(args.db)
    candidates = load_candidates(conn, args.start, args.end)
    matched, match_meta = load_next_book_matches(
        candidates,
        args.orderbook_root,
        execution_delay_minutes=args.execution_delay_minutes,
        max_lag_minutes=args.max_lag_minutes,
    )
    hot_rows = [r for r in matched if int(r.get("hot_tail_boundary_v1") or 0) == 1]

    selected_all: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    windows = {
        "pre_choice_2026-05-29_2026-06-30": lambda r: safe_str(r.get("target_date")) <= "2026-06-30",
        "post_choice_2026-07-01_2026-07-07": lambda r: safe_str(r.get("target_date")) >= "2026-07-01",
        "full_2026-05-29_2026-07-07": lambda r: True,
    }
    for cushion in CUSHIONS:
        selected = select_rows(matched, cushion)
        selected_all.extend(selected)
        for window, pred in windows.items():
            subset = [r for r in selected if pred(r)]
            summaries.append(summarize(subset, mode="per_share", window=window, cushion=cushion))
            summaries.append(summarize(subset, mode="price_tier", window=window, cushion=cushion))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "candidate_rows.csv", candidates)
    write_csv(args.out_dir / "execution_lag_matched_rows.csv", matched)
    write_csv(args.out_dir / "selected_rows_by_cushion.csv", selected_all)
    write_csv(args.out_dir / "cushion_sweep_summary.csv", summaries)
    summary_json = {
        "start": args.start,
        "end": args.end,
        "execution_delay_minutes": args.execution_delay_minutes,
        "max_lag_minutes": args.max_lag_minutes,
        "candidate_rows": len(candidates),
        "matched_rows": len(matched),
        "hot_matched_rows": len(hot_rows),
        "match_meta": match_meta,
        "summaries": summaries,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary_json, indent=2, sort_keys=True), encoding="utf-8")

    pre = [s for s in summaries if s["mode"] == "per_share" and s["window"].startswith("pre_choice")]
    post = [s for s in summaries if s["mode"] == "per_share" and s["window"].startswith("post_choice")]
    full = [s for s in summaries if s["mode"] == "per_share" and s["window"].startswith("full")]
    lines = [
        "# Low-Price YES Cushion Long Window Replay v1",
        "",
        "Status: snapshot",
        "Date: 2026-07-09",
        "Strategy family: `forecast_tail_low_price_yes` / HeadA",
        "",
        "## 结论",
        "",
        "这次是专门回应 `0.05` 是否因 Tel Aviv / Shanghai 两个已知 winner 后验选出来的问题。长窗 PIT execution-lag replay 不支持把 5c 当成稳健最优阈值：在阈值选择前的 5/29-6/30，5c 没有稳定优于更窄的 1c/3c；5c 的优势主要来自 7/1-7/7 后验窗口。",
        "",
        "因此 5c 只能解释为“避免过紧”的临时执行探针，不是 confirmed 配置。更干净的折中是 `0.03`：比 1c 少卡合理重定价，比 5c 少吃后验窗口外的 loser。",
        "",
        "## 数据与口径",
        "",
        f"- target_date window: `{args.start}`..`{args.end}`",
        f"- orderbook root: `{args.orderbook_root.relative_to(ROOT) if args.orderbook_root.is_relative_to(ROOT) else args.orderbook_root}`",
        f"- execution-lag book: first YES orderbook row after `decision_snapshot_ts_utc + {args.execution_delay_minutes:.0f}m`, max wait `{args.max_lag_minutes:.0f}` minutes",
        f"- candidate rows: `{len(candidates)}`, matched rows: `{len(matched)}`, hot-tail matched rows: `{len(hot_rows)}`",
        "- selector approximation: HeadA base low-price YES (`ask 5-20c`, `edge>=20c`, first city-date row) + current `dist>0` hot-tail boundary",
        "- fee: official Weather taker fee `0.05 * price * (1-price)`",
        "",
        "## Pre-Choice Window",
        "",
        "| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in pre:
        lines.append(
            f"| {s['cushion']:.2f} | {s['rows']} | {s['dates']} | {s['cities']} | {s['wins']} | {fmt_pct(s['win_rate'])} | {fmt_num(s['avg_snapshot_ask'])} | {fmt_num(s['avg_fresh_ask'])} | {fmt_pct(s['roi'])} | {fmt_num(s['pnl'])} | {s['losing_days']} |"
        )
    lines.extend([
        "",
        "## Post-Choice / Known-Winner Window",
        "",
        "| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for s in post:
        lines.append(
            f"| {s['cushion']:.2f} | {s['rows']} | {s['dates']} | {s['cities']} | {s['wins']} | {fmt_pct(s['win_rate'])} | {fmt_num(s['avg_snapshot_ask'])} | {fmt_num(s['avg_fresh_ask'])} | {fmt_pct(s['roi'])} | {fmt_num(s['pnl'])} | {s['losing_days']} |"
        )
    lines.extend([
        "",
        "## Full Window",
        "",
        "| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for s in full:
        lines.append(
            f"| {s['cushion']:.2f} | {s['rows']} | {s['dates']} | {s['cities']} | {s['wins']} | {fmt_pct(s['win_rate'])} | {fmt_num(s['avg_snapshot_ask'])} | {fmt_num(s['avg_fresh_ask'])} | {fmt_pct(s['roi'])} | {fmt_num(s['pnl'])} | {s['losing_days']} |"
        )
    lines.extend([
        "",
        "## 动作判断",
        "",
        "```text",
        "0.05 cushion:",
        "  status=execution_probe_not_confirmed",
        "  reason=post-choice uplift not confirmed in pre-choice historical replay",
        "  live_action=do not keep as default; downgrade to 3c unless future forward proves 5c fill uplift",
        "",
        "0.03 cushion:",
        "  status=cleaner_pre_choice_candidate",
        "  reason=less obviously selected from known 7/1-7/7 winners and keeps most execution flexibility",
        "  action=use as current tiny-live execution cushion; monitor forward, do not size up",
        "```",
        "",
        "Caveat: this is execution-lag PIT replay, not exact live runner fresh-book replay. It uses the first orderbook snapshot after the decision timestamp within 60 minutes; actual live timing depends on fact-refresh and loop cadence.",
    ])
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary_json, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
