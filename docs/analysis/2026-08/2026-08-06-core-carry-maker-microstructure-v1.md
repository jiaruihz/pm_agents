# Core Carry maker 盘口与生命周期审计 v1

结论：maker 不是完全成交不了，但当前 5 股 maker 仍不能当作可靠容量。`split_taker_maker_edge_capped_no_fallback_v2` 全窗口 3/14 intents 成交（21.4%）；当前 10 taker + 5 maker 窗口为 3/8（37.5%，15 shares）。

## 盘口结构

- 11/14 个初始报价已经到 retained-edge cap。此时即使 ask 仍高 1–2 tick，策略也不能再追；这是未成交的主要结构性约束，不是 refresh 频率不足。
- filled intents 的初始 top-bid size 中位数为 8.00 shares，unfilled 为 15.00 shares。它只能近似公开队列，不能证明真实 queue position；CLOB snapshot 没有账户级 queue-ahead 字段。
- 三笔当前配置成交中，Miami/NYC 在初始报价成交，Amsterdam 在第 2 次 reprice 后以 0.95 成交。说明保留队列有价值，但 wide-spread 场景的阶段提价也确实能补成交。

## 生命周期问题

- profile 合同当前写的是 `max_reprices=None`，不是“max 2 reprices”。全窗口共有 27 次 reprice，2 个 intent 超过 2 次；Lucknow 单笔达到 21 次。
- 这会在宽 spread / best-bid 连续抬升时反复 cancel/repost，重置时间优先级。当前窗口大部分单能保持原队列，但实现仍没有硬性两次上限。
- 当前 maker lifecycle 日志只有 best bid/ask，没有逐档 queue-ahead、成交量和 5/15/30 分钟 markout，因此还不能严谨比较“排队太深”和“市场根本没有 sell flow”。这是 evidence gap，不应变成 eligibility gate。

## 推荐 challenger

保留现有 retained-edge cap 与 pre-data-update cancel，改为三个明确阶段：initial queue → 最早 5 分钟后一次 midpoint reprice → 最早 10 分钟后一次 near-ask reprice，`max_reprices=2`；只有新 observation/TTL/thesis 失效才提前撤。并在每次 quote 记录 top-level size、5c depth、public trades since post、estimated queue-ahead 与 5/15/30m markout。先做同 signal shadow A/B，不改 taker leg，也不做 maker→taker fallback。

复现：`.venv/bin/python scripts/analysis/execution_quality/audit_core_carry_maker_fill_lineage_v1.py --microstructure`
