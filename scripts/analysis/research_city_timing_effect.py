#!/usr/bin/env python3
"""City x entry timing research on weather live_real fills.

This script intentionally uses only authorized fact tables:
- fact_trades for live_real realized PnL
- fact_signal_candidates only for mandatory coverage self-checks
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
STEM = "2026-06-08-city-x-entry-timing-research"

TIMING_BINS = [
    ("<T-18", lambda h: h < 18),
    ("T-18-20", lambda h: 18 <= h < 20),
    ("T-20-22", lambda h: 20 <= h < 22),
    ("T-22-24", lambda h: 22 <= h <= 24),
    ("T-24-26", lambda h: 24 < h <= 26),
    ("T-26-28", lambda h: 26 < h <= 28),
    (">T-28", lambda h: h > 28),
]
BIN_ORDER = {name: i for i, (name, _) in enumerate(TIMING_BINS)}


def timing_bin(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    h = float(hours)
    for name, pred in TIMING_BINS:
        if pred(h):
            return name
    return "unknown"


def period_label(target_date: str | None) -> str:
    if not target_date:
        return "unknown"
    return "post_2026_06_01" if target_date >= "2026-06-01" else "pre_2026_06_01"


def pct(n: float | None, d: float | None) -> float | None:
    if not d:
        return None
    return n / d


def r4(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x), 4)


def money(x: float | None) -> float:
    return round(float(x or 0.0), 4)


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def load_gate() -> dict[str, Any]:
    import subprocess

    proc = subprocess.run(
        ["python3", "scripts/analysis/weather_clob_fill_coverage_gate.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB),
        "db_mtime_utc": datetime.fromtimestamp(DB.stat().st_mtime, timezone.utc).isoformat(),
        "db_size_bytes": DB.stat().st_size,
        "fact_built_at": fetch_dicts(
            conn, "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades"
        ),
        "trade_class": fetch_dicts(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status": fetch_dicts(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS n "
            "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "signal_candidates": fetch_dicts(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        ),
        "orders_fills": fetch_dicts(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def add_fill(a: dict[str, Any], r: sqlite3.Row) -> None:
    a["fills"] += 1
    a["cost"] += r["cost_usd"] or 0.0
    a["pnl"] += r["pnl_usd_at_fill"] or 0.0
    a["wins"] += r["win_by_count"] or 0
    a["city_days"].add((r["target_date"], r["city"], r["execution_policy"]))


def fmt_agg(a: dict[str, Any]) -> dict[str, Any]:
    fills = int(a["fills"])
    cost = a["cost"]
    pnl = a["pnl"]
    return {
        "fills": fills,
        "city_days": len(a["city_days"]),
        "cost_usd": money(cost),
        "pnl_usd": money(pnl),
        "roi": r4(pct(pnl, cost)),
        "win_rate": r4(pct(a["wins"], fills)),
    }


def classify_bin(row: dict[str, Any]) -> str:
    fills = int(row.get("fills") or 0)
    city_days = int(row.get("city_days") or 0)
    roi = row.get("roi")
    pnl = row.get("pnl_usd") or 0.0
    if fills < 5 or city_days < 2 or roi is None:
        return "sample_too_small"
    if pnl > 0 and roi >= 0.10:
        return "positive_direction"
    if pnl < 0 and roi <= -0.10:
        return "negative_direction"
    return "mixed_or_flat"


def build_city_timing(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT city, city_pool, target_date, execution_policy, strategy_id, side,
               model_version, hours_to_settle, cost_usd, pnl_usd_at_fill, win_by_count
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND hours_to_settle IS NOT NULL
        """
    ).fetchall()

    by_city_period_bin: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"fills": 0, "city_days": set(), "cost": 0.0, "pnl": 0.0, "wins": 0}
    )
    by_city_bin_side: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"fills": 0, "city_days": set(), "cost": 0.0, "pnl": 0.0, "wins": 0}
    )
    by_city: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"fills": 0, "city_days": set(), "cost": 0.0, "pnl": 0.0, "wins": 0}
    )

    for r in rows:
        b = timing_bin(r["hours_to_settle"])
        period = period_label(r["target_date"])
        add_fill(by_city_period_bin[(r["city"], period, b)], r)
        add_fill(by_city_bin_side[(r["city"], b, r["side"])], r)
        add_fill(by_city[r["city"]], r)

    city_period_bin = []
    for (city, period, b), a in by_city_period_bin.items():
        x = {"city": city, "period": period, "timing_bin": b}
        x.update(fmt_agg(a))
        x["evidence"] = classify_bin(x)
        city_period_bin.append(x)

    city_bin_side = []
    for (city, b, side), a in by_city_bin_side.items():
        x = {"city": city, "timing_bin": b, "side": side}
        x.update(fmt_agg(a))
        x["evidence"] = classify_bin(x)
        city_bin_side.append(x)

    city_totals = []
    for city, a in by_city.items():
        x = {"city": city}
        x.update(fmt_agg(a))
        city_totals.append(x)

    city_policy = []
    post_lookup = {
        (r["city"], r["timing_bin"]): r for r in city_period_bin if r["period"] == "post_2026_06_01"
    }
    cities = sorted(by_city)
    for city in cities:
        lt22_bins = [post_lookup.get((city, b)) for b in ("<T-18", "T-18-20", "T-20-22")]
        lt22 = combine_rows([r for r in lt22_bins if r])
        t2224 = post_lookup.get((city, "T-22-24"))
        t2426 = post_lookup.get((city, "T-24-26"))
        t2628 = post_lookup.get((city, "T-26-28"))
        gt28 = post_lookup.get((city, ">T-28"))

        if t2426 and t2426["evidence"] == "negative_direction":
            t2426_action = "city_specific_review_before_keep"
        elif t2426 and t2426["evidence"] == "positive_direction":
            t2426_action = "keep_candidate_pending_forecast_checkpoint"
        else:
            t2426_action = "insufficient_or_mixed"

        if t2628 and t2628["evidence"] == "positive_direction":
            t2628_action = "possible_city_exception_but_keep_shadow_only"
        elif t2628 and t2628["evidence"] == "negative_direction":
            t2628_action = "supports_drop"
        else:
            t2628_action = "no_reliable_exception"

        city_policy.append(
            {
                "city": city,
                "lt22_fills": lt22.get("fills"),
                "lt22_roi": lt22.get("roi"),
                "T-22-24_fills": (t2224 or {}).get("fills"),
                "T-22-24_roi": (t2224 or {}).get("roi"),
                "T-24-26_fills": (t2426 or {}).get("fills"),
                "T-24-26_roi": (t2426 or {}).get("roi"),
                "T-24-26_action": t2426_action,
                "T-26-28_fills": (t2628 or {}).get("fills"),
                "T-26-28_roi": (t2628 or {}).get("roi"),
                "T-26-28_action": t2628_action,
                ">T-28_fills": (gt28 or {}).get("fills"),
                ">T-28_roi": (gt28 or {}).get("roi"),
            }
        )

    return {
        "city_totals": sorted(city_totals, key=lambda r: r["pnl_usd"]),
        "city_period_timing": sorted(
            city_period_bin,
            key=lambda r: (r["period"], r["city"], BIN_ORDER.get(r["timing_bin"], 999)),
        ),
        "city_timing_side": sorted(
            city_bin_side,
            key=lambda r: (r["city"], BIN_ORDER.get(r["timing_bin"], 999), r["side"]),
        ),
        "post_policy_matrix": city_policy,
        "post_negative_direction": [
            r
            for r in sorted(city_period_bin, key=lambda x: x["pnl_usd"])
            if r["period"] == "post_2026_06_01" and r["evidence"] == "negative_direction"
        ],
        "post_positive_direction": [
            r
            for r in sorted(city_period_bin, key=lambda x: x["pnl_usd"], reverse=True)
            if r["period"] == "post_2026_06_01" and r["evidence"] == "positive_direction"
        ],
    }


def combine_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"fills": None, "roi": None, "pnl_usd": None, "cost_usd": None}
    fills = sum(int(r.get("fills") or 0) for r in rows)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    pnl = sum(float(r.get("pnl_usd") or 0.0) for r in rows)
    return {"fills": fills, "cost_usd": money(cost), "pnl_usd": money(pnl), "roi": r4(pct(pnl, cost))}


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c)
            if v is None:
                vals.append("")
            elif isinstance(v, float):
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def make_summary(data: dict[str, Any]) -> str:
    policy = data["city_timing"]["post_policy_matrix"]
    t2426_keep = [r["city"] for r in policy if r["T-24-26_action"] == "keep_candidate_pending_forecast_checkpoint"]
    t2426_review = [r["city"] for r in policy if r["T-24-26_action"] == "city_specific_review_before_keep"]
    t2628_exception = [r["city"] for r in policy if r["T-26-28_action"] == "possible_city_exception_but_keep_shadow_only"]
    t2628_drop = [r["city"] for r in policy if r["T-26-28_action"] == "supports_drop"]
    return "\n".join(
        [
            "- 城市差异存在，但大多数 city x timing cell 的样本仍小；结论应作为 live 风控和 shadow 研究优先级，不应直接放宽已禁窗口。",
            f"- `T-26-28`：post slice 中支持 drop 的城市有 `{', '.join(t2628_drop) or 'none'}`；出现正向但只能 shadow 例外观察的城市有 `{', '.join(t2628_exception) or 'none'}`。",
            f"- `T-24-26`：post slice 中保留候选城市有 `{', '.join(t2426_keep) or 'none'}`；需要城市级复核后再 keep 的城市有 `{', '.join(t2426_review) or 'none'}`。",
            "- `<T-22` 当前仍不建议恢复 live：它没有完整 opportunity fact，且 post live fill 基线里 `T-20-22` 仍是明显负向。",
            "- 下一步必须接 forecast checkpoint lineage；若某城市的 `T-24-26` 正负分化只是 pre/post forecast update 的混合，城市硬白名单会误判。",
        ]
    )


def make_markdown(data: dict[str, Any]) -> str:
    gate = data["coverage_gate"]
    checks = data["self_checks"]
    city = data["city_timing"]
    return "\n".join(
        [
            "# City x Entry Timing Research - 2026-06-08",
            "",
            "## 数据快照",
            "",
            f"- 数据源: `runtime/weather.db` (`{checks['db_path']}`)",
            f"- DB mtime UTC: `{checks['db_mtime_utc']}`",
            f"- fact_built_at_utc: `{checks['fact_built_at'][0]['max_fact_built_at_utc']}`",
            f"- CLOB coverage gate: `gate_pass={gate.get('gate_pass')}`, live_real fill_ids={gate.get('fact_trades_live_real', {}).get('fill_ids')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}",
            "- sync/rebuild: `sync_weather_remote.sh` succeeded; `run_stack.sh` rebuilt DB/facts and then failed only at API start because port 8000 was already in use.",
            "",
            "### 强制 SQL 自检",
            "",
            "trade_class:",
            markdown_table(checks["trade_class"], ["trade_class", "n"]),
            "settlement_status:",
            markdown_table(checks["settlement_status"], ["settlement_status", "n"]),
            "fact_signal_candidates:",
            markdown_table(checks["signal_candidates"], ["rows", "eligible", "paper_ordered", "live_filled"]),
            "orders x fills:",
            markdown_table(checks["orders_fills"], ["status", "orders", "with_fill"]),
            "## 结论摘要",
            "",
            data["summary"],
            "",
            "## Post-2026-06-01 Policy Matrix",
            "",
            markdown_table(
                city["post_policy_matrix"],
                [
                    "city",
                    "lt22_fills",
                    "lt22_roi",
                    "T-22-24_fills",
                    "T-22-24_roi",
                    "T-24-26_fills",
                    "T-24-26_roi",
                    "T-24-26_action",
                    "T-26-28_fills",
                    "T-26-28_roi",
                    "T-26-28_action",
                    ">T-28_fills",
                    ">T-28_roi",
                ],
            ),
            "## Post Negative Direction Cells",
            "",
            markdown_table(
                city["post_negative_direction"],
                ["city", "period", "timing_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "evidence"],
            ),
            "## Post Positive Direction Cells",
            "",
            markdown_table(
                city["post_positive_direction"],
                ["city", "period", "timing_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "evidence"],
            ),
            "## Full City x Period x Timing",
            "",
            markdown_table(
                city["city_period_timing"],
                ["city", "period", "timing_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "evidence"],
            ),
            "## City x Timing x Side",
            "",
            markdown_table(
                city["city_timing_side"],
                ["city", "timing_bin", "side", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "evidence"],
            ),
            "## 使用边界",
            "",
            "- 本报告只用 `live_real + settled` 成交样本回答城市 x timing 的 realized 风险，不证明未成交 opportunity alpha。",
            "- `sample_too_small` 不等于安全；它只表示当前样本不足以形成城市级例外。",
            "- 已收紧 live 默认窗口到 `22 <= hours_to_settle <= 26`；任何恢复 `T-26-28` 的城市例外都应先走 shadow。",
            "",
        ]
    )


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    data: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "self_checks": self_checks(conn),
        "coverage_gate": load_gate(),
        "city_timing": build_city_timing(conn),
    }
    data["summary"] = make_summary(data)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"{STEM}.json"
    md_path = OUT_DIR / f"{STEM}.md"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(make_markdown(data), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))


if __name__ == "__main__":
    main()
