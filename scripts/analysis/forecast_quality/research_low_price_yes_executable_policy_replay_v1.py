#!/usr/bin/env python3
"""Replay HeadA low-price YES executable policy settings from runner journals.

This is intentionally not an opportunity-only backtest.  It replays the
forecast-tail live runner's own shadow_decisions journal in chronological order,
then varies only execution configuration such as max_taker_cushion.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_JOURNAL = (
    ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/shadow_decisions.jsonl"
)
DEFAULT_OUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_executable_policy_replay_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-09-low-price-yes-executable-policy-replay-v1.md"

TAKER_FEE_RATE = 0.05
MAX_ASK = 0.20
MIN_FEE_ADJUSTED_EDGE = 0.15
CUSHIONS = (0.01, 0.02, 0.03, 0.05, 0.08)

DIST_BLOCKERS = {
    "dist_lt0_cold_or_inside_forecast_tail_v1",
    "dist_eq0_forecast_boundary_tail_v1",
}
FRESH_REPLAYABLE_STATUSES = {"planned"}
FRESH_REPLAYABLE_BLOCKERS = {"fresh_ask_exceeds_cushion_or_band"}


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def norm_city(value: Any) -> str:
    return safe_str(value).strip().lower().replace(" ", "")


def natural_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        safe_str(row.get("target_date") or row.get("event_date")),
        norm_city(row.get("city")),
        safe_str(row.get("bracket")),
        safe_str(row.get("condition_id") or row.get("market_id")),
        "BUY_YES",
    )


def fact_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        safe_str(row.get("event_date") or row.get("target_date")),
        norm_city(row.get("city")),
        safe_str(row.get("bracket")),
        safe_str(row.get("condition_id") or row.get("market_id")),
    )


def taker_fee_per_share(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)


def price_tier_shares(price: float) -> float:
    if price <= 0:
        return 0.0
    if price <= 0.08:
        return 6.0
    if price <= 0.14:
        return 8.0
    return 10.0


def score_tier_shares(row: dict[str, Any], price: float) -> float:
    base = price_tier_shares(price)
    multiplier = to_float(row.get("score_dist_multiplier"), math.nan)
    if not math.isfinite(multiplier) or multiplier <= 0:
        tier = safe_str(row.get("score_dist_tier"))
        multiplier = {"low": 0.8, "mid": 1.2, "high": 1.5}.get(tier, 1.0)
    return max(5.0, min(15.0, base * multiplier))


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def load_facts(conn: sqlite3.Connection, start: str, end: str) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          event_date, city, bracket, condition_id, market_id, side,
          settlement_status, final_yes, model_p_yes, decision_entry_price,
          forecast_source, model_version
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND event_date BETWEEN ? AND ?
          AND settlement_status = 'settled'
        """,
        (start, end),
    ).fetchall()
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        out[fact_key(item)] = item
    return out


def load_live_fills(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          target_date, city, bracket, condition_id, side, execution_policy,
          COUNT(*) AS fill_rows,
          SUM(cost_usd) AS cost_usd,
          SUM(CASE WHEN settlement_status = 'settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl_usd,
          MAX(final_yes) AS final_yes,
          MIN(fill_ts_utc) AS first_fill_ts_utc,
          MAX(fill_ts_utc) AS last_fill_ts_utc
        FROM fact_trades
        WHERE target_date BETWEEN ? AND ?
          AND side = 'BUY_YES'
          AND execution_policy LIKE 'low_price_yes_lottery%'
        GROUP BY target_date, city, bracket, condition_id, side, execution_policy
        ORDER BY target_date, city, bracket, execution_policy
        """,
        (start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def iter_journal(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if safe_str(row.get("record_type")) != "low_price_yes_lottery_tiny_live_decision":
                continue
            rows.append(row)
    rows.sort(key=lambda r: safe_str(r.get("created_at_utc")))
    return rows


def is_replayable_book_row(row: dict[str, Any]) -> bool:
    status = safe_str(row.get("decision_status"))
    blocker = safe_str(row.get("blocker"))
    ask = to_float(row.get("fresh_best_ask"), math.nan)
    snapshot_ask = to_float(row.get("snapshot_ask") or row.get("decision_entry_price"), math.nan)
    return (
        (status in FRESH_REPLAYABLE_STATUSES or blocker in FRESH_REPLAYABLE_BLOCKERS)
        and math.isfinite(ask)
        and math.isfinite(snapshot_ask)
    )


def would_select(row: dict[str, Any], cushion: float) -> tuple[bool, dict[str, float]]:
    snapshot_ask = to_float(row.get("snapshot_ask") or row.get("decision_entry_price"), math.nan)
    fresh_ask = to_float(row.get("fresh_best_ask"), math.nan)
    p_yes = to_float(row.get("model_p_yes"), math.nan)
    if not (math.isfinite(snapshot_ask) and math.isfinite(fresh_ask) and math.isfinite(p_yes)):
        return False, {}
    max_taker_price = min(MAX_ASK, snapshot_ask + cushion)
    fee = taker_fee_per_share(fresh_ask)
    fee_adjusted_edge = p_yes - fresh_ask - fee
    ok = fresh_ask <= max_taker_price + 1e-9 and fee_adjusted_edge >= MIN_FEE_ADJUSTED_EDGE - 1e-9
    return ok, {
        "snapshot_ask": snapshot_ask,
        "fresh_best_ask": fresh_ask,
        "max_taker_price": max_taker_price,
        "p_yes": p_yes,
        "taker_fee_per_share": fee,
        "fee_adjusted_edge": fee_adjusted_edge,
    }


@dataclass
class ReplayResult:
    cushion: float
    scanned_rows: int
    matched_settled_rows: int
    fresh_book_rows: int
    selected: list[dict[str, Any]]
    blocker_counts: Counter[str]


def replay_policy(
    rows: list[dict[str, Any]],
    facts: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    start: str,
    end: str,
    cushion: float,
) -> ReplayResult:
    submitted: set[tuple[str, str, str, str, str]] = set()
    selected: list[dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    scanned = 0
    matched = 0
    fresh_book_rows = 0

    for row in rows:
        target_date = safe_str(row.get("target_date") or row.get("event_date"))
        if target_date < start or target_date > end:
            continue
        if safe_str(row.get("side")) != "BUY_YES":
            continue
        scanned += 1
        fkey = fact_key(row)
        fact = facts.get(fkey)
        if not fact:
            blocker_counts["no_settled_fact_match"] += 1
            continue
        matched += 1
        nkey = natural_key(row)
        if nkey in submitted:
            blocker_counts["sim_duplicate_after_selected"] += 1
            continue

        if not is_replayable_book_row(row):
            blocker = safe_str(row.get("blocker")) or safe_str(row.get("decision_status")) or "not_replayable_no_fresh_book"
            blocker_counts[blocker] += 1
            continue

        fresh_book_rows += 1
        ok, metrics = would_select(row, cushion)
        if not ok:
            blocker_counts["fresh_ask_exceeds_replayed_cushion_or_fee_edge"] += 1
            continue

        final_yes = to_float(fact.get("final_yes"), 0.0)
        price = metrics["fresh_best_ask"]
        fee = metrics["taker_fee_per_share"]
        per_share_cost = price + fee
        per_share_pnl = final_yes - per_share_cost
        pt_shares = price_tier_shares(price)
        st_shares = score_tier_shares(row, price)
        out = {
            "cushion": cushion,
            "created_at_utc": safe_str(row.get("created_at_utc")),
            "target_date": target_date,
            "city": safe_str(row.get("city")),
            "bracket": safe_str(row.get("bracket")),
            "condition_id": safe_str(row.get("condition_id") or row.get("market_id")),
            "original_status": safe_str(row.get("decision_status")),
            "original_blocker": safe_str(row.get("blocker")),
            "snapshot_ask": metrics["snapshot_ask"],
            "fresh_best_ask": price,
            "fresh_best_bid": to_float(row.get("fresh_best_bid"), math.nan),
            "fresh_best_ask_size": to_float(row.get("fresh_best_ask_size"), math.nan),
            "max_taker_price": metrics["max_taker_price"],
            "model_p_yes": metrics["p_yes"],
            "fee_adjusted_edge": metrics["fee_adjusted_edge"],
            "taker_fee_per_share": fee,
            "final_yes": final_yes,
            "win": 1 if final_yes >= 0.5 else 0,
            "per_share_cost": per_share_cost,
            "per_share_pnl": per_share_pnl,
            "price_tier_shares": pt_shares,
            "price_tier_cost": per_share_cost * pt_shares,
            "price_tier_pnl": per_share_pnl * pt_shares,
            "score_tier": safe_str(row.get("score_dist_tier")),
            "score_tier_multiplier": to_float(row.get("score_dist_multiplier"), math.nan),
            "score_tier_shares": st_shares,
            "score_tier_cost": per_share_cost * st_shares,
            "score_tier_pnl": per_share_pnl * st_shares,
            "forecast_to_bracket_low_native": to_float(row.get("forecast_to_bracket_low_native"), math.nan),
            "forecast_source": safe_str(row.get("forecast_source")),
            "model_version": safe_str(row.get("model_version")),
        }
        selected.append(out)
        submitted.add(nkey)
        blocker_counts["selected"] += 1

    return ReplayResult(
        cushion=cushion,
        scanned_rows=scanned,
        matched_settled_rows=matched,
        fresh_book_rows=fresh_book_rows,
        selected=selected,
        blocker_counts=blocker_counts,
    )


def summarize_selected(result: ReplayResult, *, mode: str) -> dict[str, Any]:
    rows = result.selected
    cost_key = "per_share_cost" if mode == "per_share" else f"{mode}_cost"
    pnl_key = "per_share_pnl" if mode == "per_share" else f"{mode}_pnl"
    cost = sum(to_float(r.get(cost_key), 0.0) for r in rows)
    pnl = sum(to_float(r.get(pnl_key), 0.0) for r in rows)
    daily: dict[str, float] = defaultdict(float)
    for row in rows:
        daily[safe_str(row["target_date"])] += to_float(row.get(pnl_key), 0.0)
    losses = [v for v in daily.values() if v < 0]
    return {
        "cushion": result.cushion,
        "mode": mode,
        "journal_rows_scanned": result.scanned_rows,
        "settled_fact_matched_rows": result.matched_settled_rows,
        "fresh_book_replayable_rows_seen_before_dedupe": result.fresh_book_rows,
        "selected_rows": len(rows),
        "dates": len({r["target_date"] for r in rows}),
        "cities": len({norm_city(r["city"]) for r in rows}),
        "wins": sum(int(r["win"]) for r in rows),
        "win_rate": (sum(int(r["win"]) for r in rows) / len(rows)) if rows else math.nan,
        "avg_fresh_ask": (sum(float(r["fresh_best_ask"]) for r in rows) / len(rows)) if rows else math.nan,
        "cost": cost,
        "pnl": pnl,
        "roi": (pnl / cost) if cost > 0 else math.nan,
        "active_days": len(daily),
        "losing_days": len(losses),
        "max_daily_loss": min(daily.values()) if daily else math.nan,
        "le_minus_50pct_days_per_share": sum(
            1
            for day, pnl_v in daily.items()
            if sum(float(r["per_share_cost"]) for r in rows if r["target_date"] == day) > 0
            and pnl_v / sum(float(r["per_share_cost"]) for r in rows if r["target_date"] == day) <= -0.5
        ),
    }


def dist_snapshot_replay(
    rows: list[dict[str, Any]],
    facts: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        target_date = safe_str(row.get("target_date") or row.get("event_date"))
        if target_date < start or target_date > end:
            continue
        blocker = safe_str(row.get("blocker"))
        if blocker not in DIST_BLOCKERS:
            continue
        nkey = natural_key(row)
        if nkey in seen:
            continue
        fact = facts.get(fact_key(row))
        if not fact:
            continue
        seen.add(nkey)
        price = to_float(row.get("snapshot_ask") or row.get("decision_entry_price"), math.nan)
        if not math.isfinite(price):
            continue
        fee = taker_fee_per_share(price)
        final_yes = to_float(fact.get("final_yes"), 0.0)
        out.append(
            {
                "target_date": target_date,
                "city": safe_str(row.get("city")),
                "bracket": safe_str(row.get("bracket")),
                "blocker": blocker,
                "created_at_utc": safe_str(row.get("created_at_utc")),
                "snapshot_ask": price,
                "taker_fee_per_share": fee,
                "final_yes": final_yes,
                "win": 1 if final_yes >= 0.5 else 0,
                "per_share_cost": price + fee,
                "per_share_pnl": final_yes - price - fee,
                "forecast_to_bracket_low_native": to_float(row.get("forecast_to_bracket_low_native"), math.nan),
                "execution_replayable": 0,
                "note": "dist blocker fires before token/book fetch in live runner; snapshot-only diagnostic",
            }
        )
    return out


def summarize_rows(rows: list[dict[str, Any]], *, group_key: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if group_key is None:
        groups["all"].extend(rows)
    else:
        for row in rows:
            groups[safe_str(row.get(group_key))].append(row)
    out: list[dict[str, Any]] = []
    for key, items in groups.items():
        cost = sum(to_float(r.get("per_share_cost"), 0.0) for r in items)
        pnl = sum(to_float(r.get("per_share_pnl"), 0.0) for r in items)
        out.append(
            {
                "group": key,
                "rows": len(items),
                "dates": len({r["target_date"] for r in items}),
                "cities": len({norm_city(r["city"]) for r in items}),
                "wins": sum(int(r["win"]) for r in items),
                "win_rate": (sum(int(r["win"]) for r in items) / len(items)) if items else math.nan,
                "avg_ask": (sum(float(r["snapshot_ask"]) for r in items) / len(items)) if items else math.nan,
                "cost": cost,
                "pnl": pnl,
                "roi": (pnl / cost) if cost else math.nan,
            }
        )
    return sorted(out, key=lambda r: safe_str(r["group"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    return f"{value * 100:.1f}%"


def fmt_money(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    return f"{value:.3f}"


def make_report(
    *,
    report_path: Path,
    start: str,
    end: str,
    journal_path: Path,
    db_path: Path,
    sweep: list[dict[str, Any]],
    weighted_sweep: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    dist_summary: list[dict[str, Any]],
    live_fills: list[dict[str, Any]],
    blocker_counts: dict[str, int],
) -> None:
    by_cushion_rows = [r for r in sweep if r["mode"] == "per_share"]
    rows = [
        "# Low-Price YES Executable Policy Replay v1",
        "",
        "Status: snapshot",
        "Date: 2026-07-09",
        "Strategy family: `forecast_tail_low_price_yes` / HeadA",
        "",
        "## 结论",
        "",
        (
            "`max_taker_cushion=0.01` 在 7/1-7/7 的真实 runner journal 上过紧；"
            "把它放到 `0.05` 在 fresh-book 可重放分母里没有拆坏现有链路，"
            "并且会吃到 Tel Aviv 7/06 与 Shanghai 7/07 这类 1c cushion 卡掉的 winner。"
            "2026-07-09 已把 runner/default launcher/Mac stack 默认值统一改为 `0.05`。"
        ),
        "",
        "这不是新的 alpha 证明，只是执行配置 A/B：分母只有 runner 实际 fetch 到 fresh book 的状态行；`dist<=0` 分支由于 live runner 在 book fetch 前就 block，不能用同一链路反事实成交，仍只能 shadow 采集。",
        "",
        "## 数据与链路",
        "",
        f"- window: `{start}`..`{end}` target_date，settled fact rows only",
        f"- runner journal: `{journal_path.relative_to(ROOT)}`",
        f"- settlement/fill DB: `{db_path.relative_to(ROOT)}`",
        "- replay semantics: chronological journal scan; once a city-date-bracket-condition is selected under a cushion, later same-key rows are simulated duplicates",
        "- executable price: `fresh_best_ask` from runner-fetched book, not stale opportunity ask",
        "- fee: Weather official taker fee `0.05 * price * (1-price)` per share",
        "- entry condition replayed: `fresh_best_ask <= min(0.20, snapshot_ask + cushion)` and `model_p_yes - fresh_ask - fee >= 0.15`",
        "",
        "## Max Taker Cushion Sweep",
        "",
        "| cushion | selected | dates | cities | wins | win rate | avg ask | per-share ROI | per-share pnl | score-tier ROI |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    weighted_by_cushion = {r["cushion"]: r for r in weighted_sweep if r["mode"] == "score_tier"}
    for r in by_cushion_rows:
        w = weighted_by_cushion.get(r["cushion"], {})
        rows.append(
            "| "
            + " | ".join(
                [
                    f"{r['cushion']:.2f}",
                    str(r["selected_rows"]),
                    str(r["dates"]),
                    str(r["cities"]),
                    str(r["wins"]),
                    fmt_pct(float(r["win_rate"])),
                    f"{float(r['avg_fresh_ask']):.3f}" if math.isfinite(float(r["avg_fresh_ask"])) else "NA",
                    fmt_pct(float(r["roi"])),
                    fmt_money(float(r["pnl"])),
                    fmt_pct(float(w.get("roi", math.nan))),
                ]
            )
            + " |"
        )
    rows.extend(
        [
            "",
            "解读：`0.05` 是这组 replay 里的干净候选；`0.08` 开始额外吃进 loser，点估回落。样本太小，不能说 5c 是最优参数，只能说 1c 在当前 live 微结构下明显过紧，5c 更符合“snapshot ask 之后 book 小幅重定价仍允许成交”的执行意图。",
            "",
            "## 0.05 相对 0.01 的新增行",
            "",
            "| target_date | city | bracket | original blocker | snapshot ask | fresh ask | final | pnl/share |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    selected_by_key_001 = {
        (r["target_date"], norm_city(r["city"]), r["bracket"], r["condition_id"])
        for r in selected_rows
        if abs(float(r["cushion"]) - 0.01) < 1e-9
    }
    additions_005 = [
        r
        for r in selected_rows
        if abs(float(r["cushion"]) - 0.05) < 1e-9
        and (r["target_date"], norm_city(r["city"]), r["bracket"], r["condition_id"]) not in selected_by_key_001
    ]
    for r in additions_005:
        rows.append(
            "| "
            + " | ".join(
                [
                    safe_str(r["target_date"]),
                    safe_str(r["city"]),
                    safe_str(r["bracket"]),
                    safe_str(r["original_blocker"] or r["original_status"]),
                    f"{float(r['snapshot_ask']):.3f}",
                    f"{float(r['fresh_best_ask']):.3f}",
                    str(int(float(r["final_yes"]))),
                    fmt_money(float(r["per_share_pnl"])),
                ]
            )
            + " |"
        )
    if not additions_005:
        rows.append("| _none_ | | | | | | | |")
    rows.extend(
        [
            "",
            "## Dist Blocker Boundary",
            "",
            "`dist<=0` 不能和 max_taker 一样做 executable replay：当前 runner 在 dist blocker 处直接 return，历史 journal 没有 token/book/fresh ask。下面只保留 snapshot-price 诊断，不能作为恢复 live 的证据。",
            "",
            "| blocker | rows | dates | cities | wins | win rate | avg ask | snapshot ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for r in dist_summary:
        rows.append(
            "| "
            + " | ".join(
                [
                    safe_str(r["group"]),
                    str(r["rows"]),
                    str(r["dates"]),
                    str(r["cities"]),
                    str(r["wins"]),
                    fmt_pct(float(r["win_rate"])),
                    f"{float(r['avg_ask']):.3f}" if math.isfinite(float(r["avg_ask"])) else "NA",
                    fmt_pct(float(r["roi"])),
                ]
            )
            + " |"
        )
    rows.extend(
        [
            "",
            "动作：dist 不是马上放开；下一步应该改 runner telemetry，让 dist-blocked 行也在 shadow-only 路径 fetch/log fresh book，但仍不下单。这样以后才能把 dist 的机会层表现和可执行表现放到同一个 replay 链路里。",
            "",
            "## Live Fill 对照",
            "",
            f"- low-price YES fact_trades rows in window: `{len(live_fills)}` grouped ticket/policy rows",
            "- live fill 只作对账，不反推 selector；selector replay 以上面的 runner journal 为准。",
            "",
            "## Blocker Funnel Snapshot",
            "",
            "| blocker | journal count |",
            "|---|---:|",
        ]
    )
    for key, value in sorted(blocker_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        rows.append(f"| {key} | {value} |")
    rows.extend(
        [
            "",
            "## Verdict",
            "",
            "```text",
            "max_taker_cushion_0p05: shadow/live-config candidate",
            "  significance=NA (execution A/B micro-window, not alpha backtest)",
            "  baseline=PASS for same-journal comparison vs 0.01 in 7/1..7/7 fresh-book rows",
            "  forward=NA until post-change fills accumulate",
            "  action=runner/default launcher/Mac stack default switched from 0.01 to 0.05 on 2026-07-09; monitor as execution config, not alpha",
            "",
            "dist_le0_restore: not approved",
            "  reason=current runner did not fetch book before blocking; executable replay missing",
            "  action=add shadow fresh-book logging before any live restore",
            "```",
        ]
    )
    report_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-07-07")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    conn = connect(args.db)
    facts = load_facts(conn, args.start, args.end)
    live_fills = load_live_fills(conn, args.start, args.end)
    rows = iter_journal(args.journal)

    results = [
        replay_policy(rows, facts, start=args.start, end=args.end, cushion=cushion)
        for cushion in CUSHIONS
    ]
    sweep: list[dict[str, Any]] = []
    weighted_sweep: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for result in results:
        sweep.append(summarize_selected(result, mode="per_share"))
        weighted_sweep.append(summarize_selected(result, mode="price_tier"))
        weighted_sweep.append(summarize_selected(result, mode="score_tier"))
        selected_rows.extend(result.selected)

    blocker_counts = Counter()
    for result in results:
        if abs(result.cushion - 0.05) < 1e-9:
            blocker_counts.update(result.blocker_counts)

    dist_rows = dist_snapshot_replay(rows, facts, start=args.start, end=args.end)
    dist_summary = summarize_rows(dist_rows, group_key="blocker")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "max_taker_cushion_sweep_per_share.csv", sweep)
    write_csv(args.out_dir / "max_taker_cushion_sweep_weighted.csv", weighted_sweep)
    write_csv(args.out_dir / "selected_rows_by_cushion.csv", selected_rows)
    write_csv(args.out_dir / "dist_blocker_snapshot_replay.csv", dist_rows)
    write_csv(args.out_dir / "dist_blocker_snapshot_summary.csv", dist_summary)
    write_csv(args.out_dir / "live_low_price_yes_fill_groups.csv", live_fills)
    write_csv(
        args.out_dir / "blocker_funnel_cushion_0p05.csv",
        [{"blocker": k, "count": v} for k, v in sorted(blocker_counts.items())],
    )

    summary = {
        "start": args.start,
        "end": args.end,
        "journal": str(args.journal.relative_to(ROOT)),
        "db": str(args.db.relative_to(ROOT)),
        "sweep_per_share": sweep,
        "sweep_weighted": weighted_sweep,
        "dist_summary": dist_summary,
        "live_fill_group_rows": len(live_fills),
        "blocker_counts_cushion_0p05": dict(blocker_counts),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    make_report(
        report_path=args.report,
        start=args.start,
        end=args.end,
        journal_path=args.journal,
        db_path=args.db,
        sweep=sweep,
        weighted_sweep=weighted_sweep,
        selected_rows=selected_rows,
        dist_summary=dist_summary,
        live_fills=live_fills,
        blocker_counts=dict(blocker_counts),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
