# Current-YES Maker-then-Taker 执行计划 v0

Status: design-draft
Date: 2026-06-20
血缘层: [4] 执行（不改 [1] 信号/概率层；信号门沿用现有 current-YES runner）

## 为什么（盈利逻辑，不是追胜率）

current-YES 现在是**纯 taker**：在 ask 0.85–0.95 穿价买。三个结构性漏点：
1. 付掉 spread + fee，而残差 edge 只有几个 cent，大半被成本吃掉；
2. 成交被**逆向选择**——你能在 ask 成交，往往是 ask 刚朝对你不利方向动过（陈报价/有人知情）；
3. 高 ask 裸吃单赔率差，一个 adverse day 抹掉一串 win。

maker-then-taker 同时修这三点：
- **挂被动单赚 spread 而非付 spread**（成本项变号）；
- **成交选择反过来**——别人来砸你才成交，而不是你追价；
- **挂不上时不无脑追**：只有"穿价仍 ≥ 要求 edge 且盘口新鲜"才转 taker；ask 已跑掉的 toxic 单**跳过**而非追。
  → 这条是核心：maker 收良性单、taker 兜底只兜还划算的、toxic 的丢掉。

> 注意：maker 自身也有逆选（你正好在你错的时候被砸）。所以**必须 shadow 先量"maker 成交单的
> realized 胜率 vs 模型 p"**，确认成交集不是 toxic，再上 live。这是 gate，不是可选。

## 复用现有基建（别重造）

`scripts/ops/weather_order_executor.py` 已具备：

| 能力 | 入口 | 复用方式 |
|---|---|---|
| post-only 挂单定价 | `_maker_only_price()` | BUY 挂 `min(bid+1tick, edge上限)`，会穿价则拒 |
| maker 报价引擎（band/shade/逆选） | `build_execution_quote(s)` + `ExecutionPolicyConfig` | 用 `execution_policy="maker_queue_v2"`（单腿）；高 band 可用 `mid_price_core_v2` 的 high_band 逻辑 |
| maker/taker 拆单 | `mid_price_core_v2` 的 `split_enabled + taker_fraction` | 一部分挂、一部分吃 |
| 撤单/改价 | `_build_live_cancel_fn` | 重挂 / 兜底前撤 |
| 错误分类 | `_classify_live_error` | post_only_crosses_book / maker_only_no_resting_price |

**结论：不复活 mid_price_core/maker_queue 当策略（它们因实盘亏损 shelved），只复用其报价引擎；
信号/风控仍是 current-YES runner 的门。** maker_queue_v1 已退役，统一走 v2。

## 核心机制：maker-then-taker 状态机

一个合格信号触发后，进入一个有界的执行窗口（不是 15min 信号周期，是窗口内 30–60s 子轮询）：

```text
信号合格(过现有 current-YES 门)
        │
        ▼
[1] 计算 maker 价 = min(best_bid + improve_ticks, p_yes - required_edge)
        │  若 maker 价 ≤ 0 或 ≥ best_ask(会穿) 或 < bid(无队列) → 记 skip_no_maker_price
        ▼
[2] post-only 挂单(maker_only=True)，进窗口 T_maker(如 3–5min)，每 30–60s 轮询 fill
        │
        ├─ 全部成交 → done(maker 成交，赚 spread，记 filled_as=maker)
        ├─ 部分成交 → 记已成 size，剩余进 [3]
        ├─ best_bid 被人超过 → 至多 N 次 cancel+重挂在新 bid+1tick(仍 ≤ edge 上限)；超 N 次停重挂
        └─ 窗口到期仍(部分)未成交 → [3]
        ▼
[3] 兜底判定(剩余 size)：撤掉残单，调用现有 fresh_taker_quote
        │
        ├─ 仍 accepted(edge_at_limit ≥ required_edge、fresh ask 在 cushion 内、深度够) → 穿价吃掉剩余(filled_as=taker_fallback)
        └─ rejected(ask 跑掉/edge 不够/不新鲜) → 跳过剩余(记 skip_toxic，不追价) ★关键纪律
```

### 兜底触发条件（精确）
不是"没成交就吃"，而是 **"没成交 且 穿价仍 ≥ required_edge 且盘口新鲜"**。
ask 跑掉的 toxic 场景 → `skip_toxic`，不追。这正是纯 taker 现在亏的地方。

### 窗口与新鲜度耦合
- `T_maker` 不跨 METAR 更新窗口：临近 pre-METAR blackout（现有 6min）前**强制结算**（吃或撤），
  不持挂单穿越更新——更新会动 running max / 概率，挂单语义失效。
- 单日 city-day cap（$5）按 **maker 已成 + taker 兜底**合计计，剩余空间不足则兜底缩量。
- 这也顺带解决旧痛点：现在"$5 单层全深度才下"会整单拒；maker 部分成 + taker 兜底剩余 = 允许分批。

## 参数（首版默认，shadow 期标定）

| 参数 | 默认 | 说明 |
|---|---|---|
| `improve_ticks` | 1 | 挂在 bid 上方 1 tick 抢队列前 |
| `required_edge`(maker) | 同现有 taker 口径(fade 0.05−cushion；peak 0.02) | maker 价必须 ≤ p_yes − required_edge |
| `T_maker` | 3–5 min | maker 等待窗；不跨 blackout |
| `poll_interval` | 30–60 s | 窗口内查 fill |
| `max_repeg` | 2 | 被超价后最多重挂次数 |
| `taker_fraction` | 0（首版纯 maker，到期才兜底） | 后续可试开市即拆一部分吃 |
| `fallback_enabled` | true | 关掉=纯 maker（不成就 skip），用于对照 |

## 分阶段落地（shadow-first / git-first，硬边界）

| 阶段 | 做什么 | gate（过了才进下一阶段） |
|---|---|---|
| **P0 离线 fill 可行性** | 用 `orderbook_snapshots` 回放：若当时挂 bid+1tick，后续 book 有没有 trade-through 把我成交？估 maker 命中率 | maker 命中率 > 0 且非微不足道；否则 maker 在这些薄市根本不成交，停 |
| **P1 zero-notional shadow** | 在 runner 加 maker-then-taker 控制器，跑零 notional：记 maker命中/taker兜底/skip_toxic 分布、**maker 成交单 realized 胜率 vs 模型 p** | shadow maker 成交集不 toxic（realized ≈ 模型 p）；有效价 < 现纯 taker 基准 |
| **P2 tiny-live $5** | maker-first + taker 兜底真钱，与现纯 taker 同窗对照 | 有效成交价、滑点、fill 率优于纯 taker；无新增不可控风险 |
| **P3 评估/决策** | 复盘按 STRATEGY_REVIEW_PIPELINE | 保留/扩 size/回滚 |

任何 live 参数/policy 变更走 **weather-strategy-deploy 的 git-first 流程，不许 scp/rsync 直推**；
保留 notional 上限、暂停开关、可追溯日志。

## 遥测（新增字段，喂评估）

`forward_telemetry.jsonl` / live orders 增加：`exec_mode`(maker_then_taker)、`order_role`
(maker/taker_fallback/skip)、`maker_posted_price`、`time_to_fill_s`、`filled_as`、`repeg_count`、
`fallback_reason`、`effective_fill_price`、`skip_toxic` flag。

核心评估指标：
- **有效成交价分布**（maker 应低于现 taker）；
- **fill 率**（maker+兜底 应 ≥ 现 taker，或被 skip 的恰是 toxic 那批）；
- **maker 成交单 realized 胜率 vs 模型 p**（逆选判定，P1 hard gate）；
- skip_toxic 占比（量化"避免追价"省下多少）。

## 硬边界 & 风险

- 涉及真实 CLOB 挂单/撤单/资金：保留显式 `--confirm-live`、city-day/单笔 notional 上限、可追溯日志。
- 挂单期价格风险：持仓未成时市场可逆向移动；用 `T_maker` 有界 + 不跨 blackout 限制敞口。
- maker 逆选：P1 shadow 必须证明成交集不 toxic 才进 P2。
- 撤单竞态：兜底前撤单失败要处理（已成部分按实际，避免重复下单）；复用 `_build_live_cancel_fn` 的返回判定。

## 关联

- 现执行/taker 路径：`scripts/ops/weather_theta_current_yes_tiny_live.py`（`fresh_taker_quote`）、
  `scripts/ops/weather_order_executor.py`（maker 基建）。
- 旧实践：mid_price_core/maker_queue（`WEATHER_STRATEGY_REGISTRY.md` 执行层，shelved，仅复用报价引擎）。
- 复盘流程：[WEATHER_STRATEGY_REVIEW_PIPELINE.md](../../WEATHER_STRATEGY_REVIEW_PIPELINE.md)。
- 盈利方向背景：[reheat-tail-mechanism-feature-gap-v1](2026-06-19-reheat-tail-mechanism-feature-gap-v1.md)
  （模型到顶→盈利杠杆在执行端而非堆特征）。
