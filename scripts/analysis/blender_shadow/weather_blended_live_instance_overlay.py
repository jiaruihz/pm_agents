#!/usr/bin/env python3
"""Control-variable overlay of blended probability on current live instances.

This analysis keeps the current live instance, entry band, fill price, and
settled outcome fixed. It only asks whether the same fill would still pass if
the raw model probability were replaced by the configured blended probability.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from weather_dashboard.blend import blend_probability, load_default_config

DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_MD = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-07-blended-live-instance-overlay.md"
OUT_JSON = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-07-blended-live-instance-overlay.json"
RECENT_START = "2026-06-01"
HOLDOUT_START = "2026-05-26"


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _side_edge(side: str, p_yes: float, entry_price: float) -> float:
    if side == "BUY_YES":
        return p_yes - entry_price
    return (1.0 - p_yes) - entry_price


def _instance_family(row: pd.Series) -> str | None:
    name = str(row.get("strategy_name") or "")
    policy = str(row.get("execution_policy") or "")
    if policy == "mid_price_core_v1" and "entry_0.25-0.75" in name:
        return "v1_25_75"
    if policy == "mid_price_core_v2" and "entry_0.25-0.75" in name:
        return "v2_25_75"
    if policy == "mid_price_core_v1" and ("entry_0.20-0.45" in name or "entry_0.35-0.65" in name):
        return "v1_side_band"
    return None


def _threshold(family: str, side: str) -> float:
    if family == "v1_side_band":
        return 0.20 if side == "BUY_YES" else 0.10
    return 0.10


def _load(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
            fill_id,
            strategy_id,
            strategy_name,
            execution_policy,
            entry_price_window,
            city,
            target_date,
            bracket,
            side,
            model_p_yes,
            market_price,
            fill_price,
            cost_usd,
            pnl_usd_at_fill,
            settlement_status,
            final_yes
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND model_p_yes IS NOT NULL
          AND market_price IS NOT NULL
          AND fill_price IS NOT NULL
          AND cost_usd IS NOT NULL
          AND pnl_usd_at_fill IS NOT NULL
        """,
        conn,
    )
    if df.empty:
        return df
    df["family"] = df.apply(_instance_family, axis=1)
    df = df[df["family"].notna()].copy()
    cfg = load_default_config()
    raw_edges: list[float] = []
    blended_edges: list[float] = []
    blended_ps: list[float] = []
    market_yes_prices: list[float] = []
    thresholds: list[float] = []
    pass_blended: list[bool] = []
    for row in df.itertuples():
        side = str(row.side)
        entry = float(row.fill_price)
        raw_p = float(row.model_p_yes)
        market_side_price = float(row.market_price)
        market_yes = market_side_price if side == "BUY_YES" else 1.0 - market_side_price
        blended = blend_probability(
            city=str(row.city),
            model_p_yes_raw=raw_p,
            market_implied_p_yes=market_yes,
            config=cfg,
        )
        b_edge = _side_edge(side, float(blended.p_yes_used), entry)
        threshold = _threshold(str(row.family), side)
        raw_edges.append(_side_edge(side, raw_p, entry))
        blended_edges.append(b_edge)
        blended_ps.append(float(blended.p_yes_used))
        market_yes_prices.append(market_yes)
        thresholds.append(threshold)
        pass_blended.append(b_edge >= threshold)
    df["market_yes_price"] = market_yes_prices
    df["raw_edge_at_fill"] = raw_edges
    df["blended_p_yes"] = blended_ps
    df["blended_edge_at_fill"] = blended_edges
    df["edge_threshold"] = thresholds
    df["pass_blended_overlay"] = pass_blended
    return df


def _summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "fills": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "kept_fills": 0,
            "kept_cost_usd": 0.0,
            "kept_pnl_usd": 0.0,
            "kept_roi": None,
            "filtered_fills": 0,
            "filtered_cost_usd": 0.0,
            "filtered_pnl_usd": 0.0,
            "delta_pnl_if_filter": 0.0,
            "avoided_loss_usd": 0.0,
            "missed_profit_usd": 0.0,
            "filtered_winner_fills": 0,
            "filtered_loser_fills": 0,
            "kept_winner_fills": 0,
            "kept_loser_fills": 0,
            "net_filter_value_usd": 0.0,
            "pnl_ex_top5_wins_usd": 0.0,
            "kept_pnl_ex_top5_wins_usd": 0.0,
            "filtered_pnl_ex_top5_wins_usd": 0.0,
            "raw_edge_mean": None,
            "blended_edge_mean": None,
        }
    kept = df[df["pass_blended_overlay"]].copy()
    filtered = df[~df["pass_blended_overlay"]].copy()

    def block(x: pd.DataFrame) -> tuple[int, float, float, float | None]:
        cost = float(x["cost_usd"].sum()) if not x.empty else 0.0
        pnl = float(x["pnl_usd_at_fill"].sum()) if not x.empty else 0.0
        roi = pnl / cost if cost else None
        return int(len(x)), round(cost, 6), round(pnl, 6), None if roi is None else round(roi, 6)

    fills, cost, pnl, roi = block(df)
    kept_fills, kept_cost, kept_pnl, kept_roi = block(kept)
    filt_fills, filt_cost, filt_pnl, _ = block(filtered)
    filtered_winners = filtered[filtered["pnl_usd_at_fill"] > 0].copy()
    filtered_losers = filtered[filtered["pnl_usd_at_fill"] < 0].copy()
    kept_winners = kept[kept["pnl_usd_at_fill"] > 0].copy()
    kept_losers = kept[kept["pnl_usd_at_fill"] < 0].copy()
    avoided_loss = -float(filtered_losers["pnl_usd_at_fill"].sum()) if not filtered_losers.empty else 0.0
    missed_profit = float(filtered_winners["pnl_usd_at_fill"].sum()) if not filtered_winners.empty else 0.0

    def pnl_ex_top_wins(x: pd.DataFrame, n: int = 5) -> float:
        if x.empty:
            return 0.0
        positives = x[x["pnl_usd_at_fill"] > 0]["pnl_usd_at_fill"].sort_values(ascending=False)
        return float(x["pnl_usd_at_fill"].sum()) - float(positives.head(n).sum())

    def mean_or_none(x: pd.Series) -> float | None:
        return None if x.empty else round(float(x.mean()), 6)

    return {
        "fills": fills,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": roi,
        "kept_fills": kept_fills,
        "kept_cost_usd": kept_cost,
        "kept_pnl_usd": kept_pnl,
        "kept_roi": kept_roi,
        "filtered_fills": filt_fills,
        "filtered_cost_usd": filt_cost,
        "filtered_pnl_usd": filt_pnl,
        "delta_pnl_if_filter": round(-filt_pnl, 6),
        "avoided_loss_usd": round(avoided_loss, 6),
        "missed_profit_usd": round(missed_profit, 6),
        "filtered_winner_fills": int(len(filtered_winners)),
        "filtered_loser_fills": int(len(filtered_losers)),
        "kept_winner_fills": int(len(kept_winners)),
        "kept_loser_fills": int(len(kept_losers)),
        "net_filter_value_usd": round(avoided_loss - missed_profit, 6),
        "pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(df), 6),
        "kept_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(kept), 6),
        "filtered_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(filtered), 6),
        "raw_edge_mean": mean_or_none(df["raw_edge_at_fill"]),
        "blended_edge_mean": mean_or_none(df["blended_edge_at_fill"]),
        "kept_blended_edge_mean": mean_or_none(kept["blended_edge_at_fill"]),
        "filtered_blended_edge_mean": mean_or_none(filtered["blended_edge_at_fill"]),
    }


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df
    if name == "pre_holdout_before_2026_05_26":
        return df[df["target_date"] < HOLDOUT_START].copy()
    if name == "holdout_from_2026_05_26":
        return df[df["target_date"] >= HOLDOUT_START].copy()
    if name == "pre_recent_before_2026_06_01":
        return df[df["target_date"] < RECENT_START].copy()
    if name == "recent_from_2026_06_01":
        return df[df["target_date"] >= RECENT_START].copy()
    raise ValueError(name)


def _fmt_money(x: float) -> str:
    return f"${x:+.2f}"


def _fmt_roi(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _fmt_num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _group_filter_value(df: pd.DataFrame, group_cols: list[str], limit: int = 12) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for key, gdf in df.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        s = _summarize(gdf)
        row = {col: val for col, val in zip(group_cols, key)}
        row.update(
            {
                "fills": s["fills"],
                "pnl_usd": s["pnl_usd"],
                "kept_pnl_usd": s["kept_pnl_usd"],
                "filtered_pnl_usd": s["filtered_pnl_usd"],
                "delta_pnl_if_filter": s["delta_pnl_if_filter"],
                "avoided_loss_usd": s["avoided_loss_usd"],
                "missed_profit_usd": s["missed_profit_usd"],
            }
        )
        rows.append(row)
    rows.sort(key=lambda r: float(r["delta_pnl_if_filter"]))
    deltas = [float(r["delta_pnl_if_filter"]) for r in rows]
    return {
        "groups": len(rows),
        "positive_delta_groups": sum(1 for d in deltas if d > 0),
        "negative_delta_groups": sum(1 for d in deltas if d < 0),
        "zero_delta_groups": sum(1 for d in deltas if d == 0),
        "median_delta_usd": None if not deltas else round(float(pd.Series(deltas).median()), 6),
        "bottom": rows[:limit],
        "top": list(reversed(rows[-limit:])),
    }


def _edge_buckets(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    bins = [-999.0, -0.05, 0.0, 0.05, 0.10, 0.15, 999.0]
    labels = ["<-0.05", "-0.05~0", "0~0.05", "0.05~0.10", "0.10~0.15", ">=0.15"]
    x = df.copy()
    x["blended_edge_bucket"] = pd.cut(x["blended_edge_at_fill"], bins=bins, labels=labels, right=False)
    rows = []
    for bucket, bdf in x.groupby("blended_edge_bucket", observed=False):
        s = _summarize(bdf)
        rows.append(
            {
                "bucket": str(bucket),
                "fills": s["fills"],
                "pnl_usd": s["pnl_usd"],
                "kept_fills": s["kept_fills"],
                "filtered_fills": s["filtered_fills"],
                "delta_pnl_if_filter": s["delta_pnl_if_filter"],
            }
        )
    return rows


def main() -> int:
    conn = sqlite3.connect(DB_DEFAULT)
    self_check = {
        "fact_trades_freshness": _fetchall(conn, "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades"),
        "fact_trades_by_class": _fetchall(conn, "SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "fact_trades_by_settlement": _fetchall(conn, "SELECT settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"),
        "fact_signal_candidates_coverage": _fetchall(conn, "SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled FROM fact_signal_candidates"),
        "clob_orders_with_fills": _fetchall(
            conn,
            """
            SELECT o.status, COUNT(*) orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
            FROM orders o
            LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
    }
    df = _load(conn)
    report: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db": str(DB_DEFAULT),
        "self_check": self_check,
        "slices": {},
    }
    for slice_name in [
        "full",
        "pre_holdout_before_2026_05_26",
        "holdout_from_2026_05_26",
        "pre_recent_before_2026_06_01",
        "recent_from_2026_06_01",
    ]:
        part = _slice(df, slice_name)
        families = {}
        for family in ["v1_25_75", "v2_25_75", "v1_side_band"]:
            fdf = part[part["family"] == family].copy()
            families[family] = _summarize(fdf)
            by_side = {}
            for side in ["BUY_YES", "BUY_NO"]:
                by_side[side] = _summarize(fdf[fdf["side"] == side].copy())
            families[family]["by_side"] = by_side
            families[family]["edge_buckets"] = _edge_buckets(fdf)
        report["slices"][slice_name] = families
    v1 = df[df["family"] == "v1_25_75"].copy()
    report["v1_25_75_diagnostics"] = {
        "by_city": _group_filter_value(v1, ["city"], limit=15),
        "by_target_date": _group_filter_value(v1, ["target_date"], limit=20),
        "by_city_side": _group_filter_value(v1, ["city", "side"], limit=20),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    v1_full = report["slices"]["full"]["v1_25_75"]
    v1_pre_recent = report["slices"]["pre_recent_before_2026_06_01"]["v1_25_75"]
    v1_recent = report["slices"]["recent_from_2026_06_01"]["v1_25_75"]
    lines = [
        "# Blended Gate On Live Fills: Attribution",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{DB_DEFAULT}`",
        "",
        "## 结论",
        "",
        "- 当前证据支持把 blender 作为 `v1_25_75` 的二级 filter/paper shadow，而不是直接 live。",
        f"- `v1_25_75` 全窗口 delta 只有 `{_fmt_money(v1_full['delta_pnl_if_filter'])}`，因为 6 月前 gate 会误杀一批盈利；"
        f"6 月以来 delta 是 `{_fmt_money(v1_recent['delta_pnl_if_filter'])}`，pre-recent 是 `{_fmt_money(v1_pre_recent['delta_pnl_if_filter'])}`。",
        f"- 收益来源不是纯粹少下单：全窗口 avoided_loss `{_fmt_money(v1_full['avoided_loss_usd'])}`，missed_profit `{_fmt_money(v1_full['missed_profit_usd'])}`；"
        f"recent avoided_loss `{_fmt_money(v1_recent['avoided_loss_usd'])}`，missed_profit `{_fmt_money(v1_recent['missed_profit_usd'])}`。",
        "- 解释上，0.30/0.70 blend 会把 raw 概率向 market 收缩；能通过 0.10 blended edge 的单，通常需要 raw 与 market 的分歧足够大。它过滤的是“raw 模型单边很激进、但市场不确认”的 fill。",
        "- 主要风险：它在历史上确实拦过赚钱单，尤其 BUY_NO；所以不能只因最近亏损就直接 live，需要 paper/shadow 继续验证 city/date/side 稳定性。",
        "",
        "## 口径",
        "",
        "这是对真实 settled `live_real` fill 的 control-variable overlay：保留原 live instance、fill price、size、结算结果不变，只重算同一笔 fill 如果加上 blended edge gate 是否会通过。",
        "",
        "它只能评估“原策略已成交的单，如果被 blended gate 过滤会怎样”，不能评估 standalone blended 新机会，也不包含未结算 open fill。",
        "",
        "## Summary By Period",
        "",
        "| slice | family | fills | cost | pnl | ROI | kept fills | kept pnl | filtered fills | filtered pnl | avoided loss | missed profit | delta | pnl ex top5 wins |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name, families in report["slices"].items():
        for family, s in families.items():
            lines.append(
                f"| {slice_name} | {family} | {s['fills']} | ${s['cost_usd']:.2f} | {_fmt_money(s['pnl_usd'])} | {_fmt_roi(s['roi'])} | "
                f"{s['kept_fills']} | {_fmt_money(s['kept_pnl_usd'])} | {s['filtered_fills']} | {_fmt_money(s['filtered_pnl_usd'])} | "
                f"{_fmt_money(s['avoided_loss_usd'])} | {_fmt_money(s['missed_profit_usd'])} | {_fmt_money(s['delta_pnl_if_filter'])} | "
                f"{_fmt_money(s['pnl_ex_top5_wins_usd'])} |"
            )
    lines.extend(["", "## V1_25_75 收益归因", ""])
    for slice_name in ["full", "pre_recent_before_2026_06_01", "recent_from_2026_06_01"]:
        s = report["slices"][slice_name]["v1_25_75"]
        lines.extend(
            [
                f"### {slice_name}",
                "",
                f"- 原始 PnL `{_fmt_money(s['pnl_usd'])}`；加 gate 后保留部分 PnL `{_fmt_money(s['kept_pnl_usd'])}`；理论变化 `{_fmt_money(s['delta_pnl_if_filter'])}`。",
                f"- 被拦截 winner `{s['filtered_winner_fills']}` 笔，missed profit `{_fmt_money(s['missed_profit_usd'])}`；被拦截 loser `{s['filtered_loser_fills']}` 笔，avoided loss `{_fmt_money(s['avoided_loss_usd'])}`。",
                f"- 保留下来的 loser `{s['kept_loser_fills']}` 笔，说明 gate 不是止损器，只是概率一致性过滤。",
                f"- raw edge mean `{_fmt_num(s['raw_edge_mean'])}`，blended edge mean `{_fmt_num(s['blended_edge_mean'])}`，filtered blended edge mean `{_fmt_num(s['filtered_blended_edge_mean'])}`。",
                "",
            ]
        )
    lines.extend(["", "## By Side", ""])
    for slice_name, families in report["slices"].items():
        lines.extend([
            f"### {slice_name}",
            "",
            "| family | side | fills | pnl | kept fills | kept pnl | filtered fills | filtered pnl | delta if filtered |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for family, s in families.items():
            for side, ss in s["by_side"].items():
                lines.append(
                    f"| {family} | {side} | {ss['fills']} | {_fmt_money(ss['pnl_usd'])} | {ss['kept_fills']} | "
                    f"{_fmt_money(ss['kept_pnl_usd'])} | {ss['filtered_fills']} | {_fmt_money(ss['filtered_pnl_usd'])} | "
                    f"{_fmt_money(ss['delta_pnl_if_filter'])} |"
                )
            lines.append("")
    lines.extend(["", "## V1_25_75 Blended Edge Buckets", ""])
    lines.extend(
        [
            "| slice | blended edge bucket | fills | pnl | kept fills | filtered fills | delta if filtered |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for slice_name in ["full", "pre_recent_before_2026_06_01", "recent_from_2026_06_01"]:
        for row in report["slices"][slice_name]["v1_25_75"]["edge_buckets"]:
            lines.append(
                f"| {slice_name} | {row['bucket']} | {row['fills']} | {_fmt_money(row['pnl_usd'])} | "
                f"{row['kept_fills']} | {row['filtered_fills']} | {_fmt_money(row['delta_pnl_if_filter'])} |"
            )
    lines.extend(["", "## V1_25_75 City/Day Robustness", ""])
    for title, key in [("By City", "by_city"), ("By Target Date", "by_target_date"), ("By City Side", "by_city_side")]:
        diag = report["v1_25_75_diagnostics"][key]
        lines.extend(
            [
                f"### {title}",
                "",
                f"- groups: `{diag['groups']}`; positive delta: `{diag['positive_delta_groups']}`; negative delta: `{diag['negative_delta_groups']}`; median delta: `{_fmt_money(diag['median_delta_usd'] or 0.0)}`.",
                "",
                "| rank | key | fills | pnl | kept pnl | filtered pnl | avoided loss | missed profit | delta |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for rank, row in enumerate(diag["top"][:8], start=1):
            key_text = " / ".join(str(row.get(c, "")) for c in row.keys() if c in {"city", "target_date", "side"})
            lines.append(
                f"| top {rank} | {key_text} | {row['fills']} | {_fmt_money(row['pnl_usd'])} | {_fmt_money(row['kept_pnl_usd'])} | "
                f"{_fmt_money(row['filtered_pnl_usd'])} | {_fmt_money(row['avoided_loss_usd'])} | {_fmt_money(row['missed_profit_usd'])} | {_fmt_money(row['delta_pnl_if_filter'])} |"
            )
        for rank, row in enumerate(diag["bottom"][:8], start=1):
            key_text = " / ".join(str(row.get(c, "")) for c in row.keys() if c in {"city", "target_date", "side"})
            lines.append(
                f"| bottom {rank} | {key_text} | {row['fills']} | {_fmt_money(row['pnl_usd'])} | {_fmt_money(row['kept_pnl_usd'])} | "
                f"{_fmt_money(row['filtered_pnl_usd'])} | {_fmt_money(row['avoided_loss_usd'])} | {_fmt_money(row['missed_profit_usd'])} | {_fmt_money(row['delta_pnl_if_filter'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 交易解释",
            "",
            "这个 gate 的收益如果存在，来源应是：当 raw 模型给出很高 edge，但市场价格并不支持时，blend 把概率拉回市场，导致 blended edge 低于阈值，从而过滤掉高 raw edge 但低市场确认的单。",
            "",
            "因此它不是“更聪明地预测天气”，而是在做 model-vs-market disagreement control。它可能提升亏损期表现，也可能在 raw 模型真的有独立 alpha 的时期误杀盈利。是否应该 live，必须看 filtered loser 的 avoided loss 是否稳定大于 filtered winner 的 missed profit，并且这个关系不能只出现在少数日期或少数城市。",
            "",
            "当前建议：只跑 paper/shadow；不要直接替换 live。下一步应把这份 overlay 与未来真实 paper fills 做前瞻对照，至少按 city、side、target_date 和 edge bucket 连续观察。",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD)
    print(OUT_JSON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
