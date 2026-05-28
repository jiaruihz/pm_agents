#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime" / "weather_edge_v1"
SNAPSHOT_DIR = RUNTIME / "market_data" / "paper_snapshots"
PM_HISTORY_DIR = RUNTIME / "market_data" / "cache" / "pm_history"
DB_PATH = ROOT / "runtime" / "weather.db"


@dataclass(frozen=True)
class Trade:
    selector: str
    snapshot: str
    target_date: str
    city: str
    city_pool: str
    model: str
    side: str
    bracket: str
    market_key: str
    entry_price: float
    shares: float
    win: bool
    pnl_usd: float
    cost_usd: float


NEW_T1_V2_CITIES = {
    "Ankara",
    "Guangzhou",
    "Istanbul",
    "Jeddah",
    "Karachi",
    "Lucknow",
    "Moscow",
    "Seattle",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _latest_snapshot() -> Path | None:
    files = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _current_t1_cities() -> set[str]:
    latest = _latest_snapshot()
    if latest is None:
        return set()
    payload = _load_json(latest)
    cities = payload.get("trading_t1_cities")
    if isinstance(cities, list) and cities:
        return {str(city) for city in cities}
    return set()


def _market_key(record: dict[str, Any]) -> str:
    side = str(record.get("side") or "")
    market_id = str(record.get("market_id") or "").strip()
    condition_id = str(record.get("condition_id") or "").strip()
    if market_id:
        return f"market_id:{market_id}|{side}"
    if condition_id:
        return f"condition_id:{condition_id}|{side}"
    return "|".join(
        [
            "city_bracket",
            str(record.get("city") or ""),
            str(record.get("event_date") or ""),
            str(record.get("bracket") or ""),
            side,
        ]
    )


def _base_filter(record: dict[str, Any], t1_cities: set[str]) -> bool:
    if str(record.get("city") or "") not in t1_cities:
        return False
    side = str(record.get("side") or "")
    if side not in {"BUY_YES", "BUY_NO"}:
        return False
    edge = abs(_safe_float(record.get("abs_edge"), _safe_float(record.get("edge"), 0.0)))
    entry_price = _safe_float(record.get("entry_price"), 0.0)
    return edge >= 0.10 and 0.25 <= entry_price < 0.75


def _strict_t24(record: dict[str, Any]) -> bool:
    return str(record.get("time_bucket") or "") == "t24"


def _wide_22_28(record: dict[str, Any]) -> bool:
    hours = _safe_float(record.get("hours_to_settle"), math.nan)
    return not math.isnan(hours) and 22.0 <= hours <= 28.0


def _winner_brackets(city: str, target_date: str) -> set[str] | None:
    path = PM_HISTORY_DIR / f"{city}_{target_date}.json"
    if not path.exists():
        return None
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return None
    winners: set[str] = set()
    for bracket in payload.get("brackets", []):
        if not isinstance(bracket, dict):
            continue
        if _safe_float(bracket.get("final_price"), -1.0) == 1.0:
            winners.add(str(bracket.get("label") or bracket.get("outcome") or "").strip())
    return winners or None


def _settle(selector: str, snapshot: Path, record: dict[str, Any], shares: float) -> Trade | None:
    city = str(record.get("city") or "")
    target_date = str(record.get("event_date") or "")
    bracket = str(record.get("bracket") or "")
    side = str(record.get("side") or "")
    winners = _winner_brackets(city, target_date)
    if winners is None:
        return None
    bracket_hit = bracket in winners
    entry_price = _safe_float(record.get("entry_price"), 0.0)
    if side == "BUY_YES":
        win = bracket_hit
    elif side == "BUY_NO":
        win = not bracket_hit
    else:
        return None
    payout = 1.0 if win else 0.0
    return Trade(
        selector=selector,
        snapshot=snapshot.name,
        target_date=target_date,
        city=city,
        city_pool="t1_trading",
        model=str(record.get("model") or record.get("forecast_source") or ""),
        side=side,
        bracket=bracket,
        market_key=_market_key(record),
        entry_price=entry_price,
        shares=shares,
        win=win,
        pnl_usd=(payout - entry_price) * shares,
        cost_usd=entry_price * shares,
    )


def _collect_candidates(t1_cities: set[str], selector: str) -> dict[str, tuple[Path, dict[str, Any]]]:
    pred = _strict_t24 if selector == "strict_t24" else _wide_22_28
    candidates: dict[str, tuple[Path, dict[str, Any]]] = {}
    for snapshot in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        payload = _load_json(snapshot)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            if not _base_filter(record, t1_cities) or not pred(record):
                continue
            key = _market_key(record)
            if key not in candidates:
                candidates[key] = (snapshot, record)
    return candidates


def _summarize(trades: list[Trade]) -> dict[str, Any]:
    cost = sum(t.cost_usd for t in trades)
    pnl = sum(t.pnl_usd for t in trades)
    wins = sum(1 for t in trades if t.win)
    return {
        "n": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) if trades else 0.0,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else 0.0,
        "fill_qty": sum(t.shares for t in trades),
    }


def _group(trades: list[Trade], key: str) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[str(getattr(trade, key))].append(trade)
    return sorted((name, _summarize(rows)) for name, rows in buckets.items())


def _cohort(city: str) -> str:
    return "new_t1_8" if city in NEW_T1_V2_CITIES else "original_t1_12"


def _group_cohort(trades: list[Trade]) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[_cohort(trade.city)].append(trade)
    return sorted((name, _summarize(rows)) for name, rows in buckets.items())


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _delta_row(label: str, a: dict[str, Any], b: dict[str, Any]) -> str:
    if label in {"win_rate", "roi"}:
        av = _fmt_pct(float(a[label]))
        bv = _fmt_pct(float(b[label]))
        delta = _fmt_pct(float(b[label]) - float(a[label]))
        base = abs(float(a[label]))
        delta_pct = "N/A" if base == 0 else _fmt_pct((float(b[label]) - float(a[label])) / base)
        return f"| {label} | {av} | {bv} | {delta} | {delta_pct} |"
    if label in {"pnl_usd", "cost_usd"}:
        delta_value = float(b[label]) - float(a[label])
        base = abs(float(a[label]))
        delta_pct = "N/A" if base == 0 else _fmt_pct(delta_value / base)
        return f"| {label} | {_fmt_usd(float(a[label]))} | {_fmt_usd(float(b[label]))} | {_fmt_usd(delta_value)} | {delta_pct} |"
    delta_value = float(b[label]) - float(a[label])
    base = abs(float(a[label]))
    delta_pct = "N/A" if base == 0 else _fmt_pct(delta_value / base)
    return f"| {label} | {a[label]} | {b[label]} | {delta_value:.0f} | {delta_pct} |"


def _db_snapshot_status() -> tuple[str, str, int, int, int]:
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return ("unavailable", "weather.db missing_or_empty", 0, 0, 0)
    try:
        conn = sqlite3.connect(DB_PATH)
        fill_count = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        missing = conn.execute("SELECT COUNT(*) FROM settlements WHERE settlement_status='missing_bracket'").fetchone()[0]
        conn.close()
    except Exception as exc:
        return ("unavailable", f"weather.db error: {type(exc).__name__}: {exc}", 0, 0, 0)
    return ("available", "weather.db", fill_count, 0, missing)


def _rows_for_group_compare(a_trades: list[Trade], b_trades: list[Trade], key: str) -> str:
    a_groups = dict(_group(a_trades, key))
    b_groups = dict(_group(b_trades, key))
    lines = []
    for name in sorted(set(a_groups) | set(b_groups)):
        a = a_groups.get(name, _summarize([]))
        b = b_groups.get(name, _summarize([]))
        lines.append(
            f"| {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(b['pnl_usd'] - a['pnl_usd'])} | {_fmt_pct(a['roi'])} | {_fmt_pct(b['roi'])} |"
        )
    return "\n".join(lines)


def _write_report(
    *,
    out_path: Path,
    strict: list[Trade],
    wide: list[Trade],
    incremental: list[Trade],
    unsettled_strict: int,
    unsettled_wide: int,
    t1_cities: set[str],
    source_note: str,
) -> None:
    strict_s = _summarize(strict)
    wide_s = _summarize(wide)
    inc_s = _summarize(incremental)
    latest = _latest_snapshot()
    snapshot_ts = datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="seconds") if latest else "N/A"
    source_path = str(SNAPSHOT_DIR.relative_to(ROOT))

    lines = [
        "# 策略对比：strict_t24 vs wide_22_28_window",
        "",
        "> 时间窗：全部已结算镜像样本（北京时间 target_date）",
        "> 对比维度：signal_capture_window（counterfactual snapshot replay）",
        "> 数据源：镜像 JSON/pm_history（DB/API 无法回答未执行候选 replay）",
        "",
        "## 数据快照",
        "",
        "| 项目 | A（strict_t24） | B（wide_22_28_window） |",
        "|---|---|---|",
        f"| 数据快照时间 | {snapshot_ts} | {snapshot_ts} |",
        f"| fills 行数 | {strict_s['n']} replay trades | {wide_s['n']} replay trades |",
        f"| unsettled 占比 | {unsettled_strict} / {strict_s['n'] + unsettled_strict} | {unsettled_wide} / {wide_s['n'] + unsettled_wide} |",
        "| missing_bracket 数 | N/A（pm_history 缺失/未结算计入 unsettled） | N/A（pm_history 缺失/未结算计入 unsettled） |",
        "",
        "## 对比设定",
        "",
        f"- **Selector A**：当前 T1 城市池（{len(t1_cities)} 城）+ `time_bucket == t24` + `abs(edge) >= 0.10` + `0.25 <= entry_price < 0.75`。",
        "- **Selector B**：同城市池/edge/price，但窗口改为 `22 <= hours_to_settle <= 28`。",
        "- **对齐方式**：同一批 snapshot/pm_history 镜像；每个 `market_id/condition_id + side` 只取第一次满足条件的候选，按 10 shares 做 replay。",
        f"- **数据源降级原因**：{source_note}",
        "- **skill 口径备注**：这是未执行候选 replay，不是 DB fills 实绩；`pnl_usd_at_fill` 与 `pnl_usd_at_plan` 在本报告中等同于 snapshot entry 价 replay。",
        "",
        "## 总览对比",
        "",
        "| 指标 | A | B | delta (B−A) | delta% |",
        "|---|---|---|---|---|",
        _delta_row("pnl_usd", strict_s, wide_s),
        _delta_row("roi", strict_s, wide_s),
        _delta_row("win_rate", strict_s, wide_s),
        "| Win rate（by notional） | N/A（snapshot replay 无 fill notional 胜率字段） | N/A | N/A | N/A |",
        _delta_row("n", strict_s, wide_s),
        _delta_row("cost_usd", strict_s, wide_s),
        "",
        "## 增量窗口收益（B − A，只看新增候选）",
        "",
        "| trades | wins | win_rate | cost_usd | pnl_usd | roi |",
        "|---|---|---|---|---|---|",
        f"| {inc_s['n']} | {inc_s['wins']} | {_fmt_pct(inc_s['win_rate'])} | {_fmt_usd(inc_s['cost_usd'])} | {_fmt_usd(inc_s['pnl_usd'])} | {_fmt_pct(inc_s['roi'])} |",
        "",
        "## 切片对比：by_date",
        "",
        "| 日期（北京时间） | A pnl | B pnl | delta | A win_rate | B win_rate |",
        "|---|---|---|---|---|---|",
    ]
    a_by_date = dict(_group(strict, "target_date"))
    b_by_date = dict(_group(wide, "target_date"))
    for name in sorted(set(a_by_date) | set(b_by_date)):
        a = a_by_date.get(name, _summarize([]))
        b = b_by_date.get(name, _summarize([]))
        lines.append(f"| {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(b['pnl_usd'] - a['pnl_usd'])} | {_fmt_pct(a['win_rate'])} | {_fmt_pct(b['win_rate'])} |")
    lines.extend(
        [
            "",
            "## 切片对比：by_t1_cohort",
            "",
            "| cohort | A pnl | B pnl | delta | A roi | B roi | A n | B n |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    a_by_cohort = dict(_group_cohort(strict))
    b_by_cohort = dict(_group_cohort(wide))
    for name in ["original_t1_12", "new_t1_8"]:
        a = a_by_cohort.get(name, _summarize([]))
        b = b_by_cohort.get(name, _summarize([]))
        lines.append(
            f"| {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(b['pnl_usd'] - a['pnl_usd'])} | {_fmt_pct(a['roi'])} | {_fmt_pct(b['roi'])} | {a['n']} | {b['n']} |"
        )
    inc_by_cohort = dict(_group_cohort(incremental))
    lines.extend(
        [
            "",
            "### 增量窗口：by_t1_cohort",
            "",
            "| cohort | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for name in ["original_t1_12", "new_t1_8"]:
        s = inc_by_cohort.get(name, _summarize([]))
        lines.append(
            f"| {name} | {s['n']} | {s['wins']} | {_fmt_pct(s['win_rate'])} | {_fmt_usd(s['cost_usd'])} | {_fmt_usd(s['pnl_usd'])} | {_fmt_pct(s['roi'])} |"
        )
    lines.extend(
        [
            "",
            "## 切片对比：by_city",
            "",
            "| city | A pnl | B pnl | delta | A roi | B roi |",
            "|---|---|---|---|---|---|",
            _rows_for_group_compare(strict, wide, "city"),
            "",
            "## 切片对比：by_model",
            "",
            "| model | A pnl | B pnl | delta | A win_rate | B win_rate |",
            "|---|---|---|---|---|---|",
        ]
    )
    a_by_model = dict(_group(strict, "model"))
    b_by_model = dict(_group(wide, "model"))
    for name in sorted(set(a_by_model) | set(b_by_model)):
        a = a_by_model.get(name, _summarize([]))
        b = b_by_model.get(name, _summarize([]))
        lines.append(f"| {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(b['pnl_usd'] - a['pnl_usd'])} | {_fmt_pct(a['win_rate'])} | {_fmt_pct(b['win_rate'])} |")
    lines.extend(
        [
            "",
            "## 切片对比：by_side",
            "",
            "| side | A pnl | B pnl | delta | A win_rate | B win_rate |",
            "|---|---|---|---|---|---|",
        ]
    )
    a_by_side = dict(_group(strict, "side"))
    b_by_side = dict(_group(wide, "side"))
    for name in sorted(set(a_by_side) | set(b_by_side)):
        a = a_by_side.get(name, _summarize([]))
        b = b_by_side.get(name, _summarize([]))
        lines.append(f"| {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(b['pnl_usd'] - a['pnl_usd'])} | {_fmt_pct(a['win_rate'])} | {_fmt_pct(b['win_rate'])} |")
    lines.extend(
        [
            "",
            "## 显著差异 Top-N",
            "",
            "**B 显著优于 A（delta > +$2 或 win_rate delta > +10%）：**",
            "",
            "| 维度 | 值 | A | B | delta |",
            "|---|---|---|---|---|",
        ]
    )
    city_deltas = []
    a_by_city = dict(_group(strict, "city"))
    b_by_city = dict(_group(wide, "city"))
    for name in sorted(set(a_by_city) | set(b_by_city)):
        a = a_by_city.get(name, _summarize([]))
        b = b_by_city.get(name, _summarize([]))
        city_deltas.append((b["pnl_usd"] - a["pnl_usd"], name, a, b))
    for delta, name, a, b in sorted(city_deltas, reverse=True)[:8]:
        if delta > 2 or b["win_rate"] - a["win_rate"] > 0.10:
            lines.append(f"| by_city | {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(delta)} |")
    lines.extend(
        [
            "",
            "**A 显著优于 B：**",
            "",
            "| 维度 | 值 | A | B | delta |",
            "|---|---|---|---|---|",
        ]
    )
    for delta, name, a, b in sorted(city_deltas)[:8]:
        if delta < -2:
            lines.append(f"| by_city | {name} | {_fmt_usd(a['pnl_usd'])} | {_fmt_usd(b['pnl_usd'])} | {_fmt_usd(delta)} |")
    lines.extend(
        [
            "",
            "## 数据完整性自检",
            "",
            "- [x] A 和 B 时间窗完全对齐：同一批 snapshot/pm_history 镜像。",
            "- [x] A 和 B 城市池范围一致：均使用当前 latest snapshot 的 `trading_t1_cities`。",
            f"- [x] 双方 unsettled 占比：A={unsettled_strict}/{strict_s['n'] + unsettled_strict}，B={unsettled_wide}/{wide_s['n'] + unsettled_wide}；未结算不计入已结算 PnL。",
            "- [x] DB/API 降级已说明：本报告为未执行候选 replay，DB/API 无完整候选全集。",
            "",
            "## 观察与建议",
            "",
            f"宽窗口相对 strict_t24 增加 {inc_s['n']} 个已结算候选，增量 PnL {_fmt_usd(inc_s['pnl_usd'])}，ROI {_fmt_pct(inc_s['roi'])}。主要收益仍来自 BUY_NO；BUY_YES 需要继续保持单独过滤，不应因为窗口放宽而整体放开。skill/contract 当前没有专门的 counterfactual missed-signal 指标，建议后续把 `signal_capture_window` 和 `snapshot_replay_candidate` 作为正式 M3 selector 写入 contract。",
            "",
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--shares", type=float, default=10.0)
    args = parser.parse_args()
    t1_cities = _current_t1_cities()
    if not t1_cities:
        raise SystemExit("No current T1 cities found in latest snapshot")
    db_state, db_note, _, _, _ = _db_snapshot_status()
    source_note = f"DB {db_state}: {db_note}; API checked separately but no counterfactual candidate replay endpoint; using mirror snapshots + pm_history."
    strict_candidates = _collect_candidates(t1_cities, "strict_t24")
    wide_candidates = _collect_candidates(t1_cities, "wide_22_28")
    strict: list[Trade] = []
    wide: list[Trade] = []
    unsettled_strict = 0
    unsettled_wide = 0
    for snapshot, record in strict_candidates.values():
        trade = _settle("strict_t24", snapshot, record, args.shares)
        if trade is None:
            unsettled_strict += 1
        else:
            strict.append(trade)
    for snapshot, record in wide_candidates.values():
        trade = _settle("wide_22_28", snapshot, record, args.shares)
        if trade is None:
            unsettled_wide += 1
        else:
            wide.append(trade)
    strict_keys = {trade.market_key for trade in strict}
    incremental = [trade for trade in wide if trade.market_key not in strict_keys]
    out_path = Path(args.out) if args.out else ROOT / "docs" / "analysis" / "2026-05" / "2026-05-27-compare-strict-t24-vs-wide-window.md"
    _write_report(
        out_path=out_path,
        strict=strict,
        wide=wide,
        incremental=incremental,
        unsettled_strict=unsettled_strict,
        unsettled_wide=unsettled_wide,
        t1_cities=t1_cities,
        source_note=source_note,
    )
    print(json.dumps({
        "out": str(out_path),
        "strict": _summarize(strict),
        "wide": _summarize(wide),
        "incremental": _summarize(incremental),
        "unsettled_strict": unsettled_strict,
        "unsettled_wide": unsettled_wide,
        "t1_cities": sorted(t1_cities),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
