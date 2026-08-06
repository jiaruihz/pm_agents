#!/usr/bin/env python3
"""Helper for the Core Carry maker-lineage runner's microstructure mode."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2")
SNAPSHOTS = Path("/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots")
PROFILE = "split_taker_maker_edge_capped_no_fallback_v2"
OUT_DIR = ROOT / "docs/analysis/2026-08/generated/core_carry_maker_microstructure_v1"
REPORT = ROOT / "docs/analysis/2026-08/2026-08-06-core-carry-maker-microstructure-v1.md"


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open() if line.strip()]


def terminal_state(order_rows: list[dict[str, Any]]) -> tuple[bool, float, int, str]:
    for row in order_rows:
        if row.get("child_order_role") != "core_carry_maker_terminal" or row.get("status") != "filled":
            continue
        auth = (row.get("exchange_response") or {}).get("authoritative_order_state") or {}
        return True, float(auth.get("posted_price") or 0), int(auth.get("reprice_count") or 0), str(row.get("created_at_utc") or "")
    return False, 0.0, 0, ""


def snapshot_book(root: dict[str, Any]) -> dict[str, float]:
    path = SNAPSHOTS / str(root.get("source_snapshot_path") or "")
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    token = str(root.get("token_id") or "")
    matches = [r for r in payload.get("records", []) if str(r.get("yes_token_id") or "") == token]
    if not matches:
        return {}
    rec = min(matches, key=lambda r: abs((pd.Timestamp(r.get("snapshot_ts_utc")) - pd.Timestamp(root["created_at_utc"])).total_seconds()))
    return {
        "initial_bid_size": float(rec.get("yes_bid_size") or 0),
        "initial_ask_size": float(rec.get("yes_ask_size") or 0),
        "bid_depth_5c": float(rec.get("yes_depth_bid_5c") or 0),
        "ask_depth_5c": float(rec.get("yes_depth_ask_5c") or 0),
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "intents": len(frame),
        "fills": int(frame.filled.sum()),
        "fill_rate": float(frame.filled.mean()),
        "filled_shares": float(frame.filled.sum() * 5),
        "initial_cap_bound": int(frame.initial_cap_bound.sum()),
        "initial_cap_bound_rate": float(frame.initial_cap_bound.mean()),
        "zero_reprice_intents": int(frame.reprices.eq(0).sum()),
        "intents_over_two_reprices": int(frame.reprices.gt(2).sum()),
        "total_reprices": int(frame.reprices.sum()),
        "median_initial_bid_size_filled": float(frame.loc[frame.filled, "initial_bid_size"].median()),
        "median_initial_bid_size_unfilled": float(frame.loc[~frame.filled, "initial_bid_size"].median()),
        "median_spread_filled": float(frame.loc[frame.filled, "spread"].median()),
        "median_spread_unfilled": float(frame.loc[~frame.filled, "spread"].median()),
    }


def main() -> int:
    orders = rows(RUNTIME / "live_orders.jsonl")
    decisions = rows(RUNTIME / "maker_lifecycle_decisions.jsonl")
    roots = [r for r in orders if r.get("child_order_role") == "maker" and r.get("execution_profile") == PROFILE]
    out: list[dict[str, Any]] = []
    for root in roots:
        key = (root.get("city"), root.get("target_date"), root.get("token_id"))
        children = [r for r in orders if (r.get("city"), r.get("target_date"), r.get("token_id")) == key and r.get("maker_only")]
        filled, fill_price, fill_reprices, fill_ts = terminal_state(children)
        t0 = datetime.fromisoformat(root["created_at_utc"])
        local_decisions = []
        for row in decisions:
            if (row.get("city"), row.get("target_date")) != key[:2]:
                continue
            elapsed = (datetime.fromisoformat(row["created_at_utc"]) - t0).total_seconds()
            if 0 <= elapsed <= 1200:
                local_decisions.append(row)
        bid = float(root.get("best_bid") or 0)
        ask = float(root.get("best_ask") or 0)
        post = float(root.get("posted_price") or root.get("limit_price") or 0)
        cap = float(root.get("maker_price_cap") or 0)
        record = {
            "city": key[0], "target_date": key[1], "created_at_utc": root["created_at_utc"],
            "bid": bid, "ask": ask, "post": post, "cap": cap, "spread": ask - bid,
            "ask_improvement": ask - post if post else 0,
            "initial_cap_bound": post > 0 and post >= cap - 0.0005,
            "reprices": sum(r.get("execution_action") == "core_carry_maker_reprice" for r in children),
            "reposts": sum(r.get("execution_action") == "core_carry_maker_repost" for r in children),
            "filled": filled, "fill_price": fill_price, "fill_reprices": fill_reprices,
            "fill_minutes": ((datetime.fromisoformat(fill_ts) - t0).total_seconds() / 60) if fill_ts else None,
            "decision_count": len(local_decisions),
            "decision_action_counts": json.dumps(Counter((r.get("action") or r.get("blocker") or "") for r in local_decisions), sort_keys=True),
            **snapshot_book(root),
        }
        out.append(record)
    frame = pd.DataFrame(out).sort_values(["created_at_utc", "city"])
    recent = frame[frame.created_at_utc.ge("2026-08-03")]
    result = {"profile": PROFILE, "full_profile_window": summarize(frame), "current_10_plus_5_window": summarize(recent)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "maker_intents.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    full, cur = result["full_profile_window"], result["current_10_plus_5_window"]
    REPORT.write_text(f"""# Core Carry maker 盘口与生命周期审计 v1

结论：maker 不是完全成交不了，但当前 5 股 maker 仍不能当作可靠容量。`{PROFILE}` 全窗口 {full['fills']}/{full['intents']} intents 成交（{full['fill_rate']:.1%}）；当前 10 taker + 5 maker 窗口为 {cur['fills']}/{cur['intents']}（{cur['fill_rate']:.1%}，15 shares）。

## 盘口结构

- {full['initial_cap_bound']}/{full['intents']} 个初始报价已经到 retained-edge cap。此时即使 ask 仍高 1–2 tick，策略也不能再追；这是未成交的主要结构性约束，不是 refresh 频率不足。
- filled intents 的初始 top-bid size 中位数为 {full['median_initial_bid_size_filled']:.2f} shares，unfilled 为 {full['median_initial_bid_size_unfilled']:.2f} shares。它只能近似公开队列，不能证明真实 queue position；CLOB snapshot 没有账户级 queue-ahead 字段。
- 三笔当前配置成交中，Miami/NYC 在初始报价成交，Amsterdam 在第 2 次 reprice 后以 0.95 成交。说明保留队列有价值，但 wide-spread 场景的阶段提价也确实能补成交。

## 生命周期问题

- profile 合同当前写的是 `max_reprices=None`，不是“max 2 reprices”。全窗口共有 {full['total_reprices']} 次 reprice，{full['intents_over_two_reprices']} 个 intent 超过 2 次；Lucknow 单笔达到 21 次。
- 这会在宽 spread / best-bid 连续抬升时反复 cancel/repost，重置时间优先级。当前窗口大部分单能保持原队列，但实现仍没有硬性两次上限。
- 当前 maker lifecycle 日志只有 best bid/ask，没有逐档 queue-ahead、成交量和 5/15/30 分钟 markout，因此还不能严谨比较“排队太深”和“市场根本没有 sell flow”。这是 evidence gap，不应变成 eligibility gate。

## 推荐 challenger

保留现有 retained-edge cap 与 pre-data-update cancel，改为三个明确阶段：initial queue → 最早 5 分钟后一次 midpoint reprice → 最早 10 分钟后一次 near-ask reprice，`max_reprices=2`；只有新 observation/TTL/thesis 失效才提前撤。并在每次 quote 记录 top-level size、5c depth、public trades since post、estimated queue-ahead 与 5/15/30m markout。先做同 signal shadow A/B，不改 taker leg，也不做 maker→taker fallback。
""")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
