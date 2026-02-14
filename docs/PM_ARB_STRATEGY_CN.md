# PM-Arb Bot 策略与监控指南（中文）

本文解释三件事：

1. PM-Arb 的策略设计（它在做什么，为什么可能赚钱）。
2. 模拟命令输出字段的含义。
3. 实盘/回测中应该重点监控哪些指标。

代码目录：`src/domains/arb/`。

## 标识模型（Event vs Market vs Token）

Polymarket 有多层“标识/对象”。PM-Arb 的交易单位是 **market/outcome**，不是 event。

| 层级 | 含义 | 常见字段 | 数量关系 |
|---|---|---|---|
| Event（话题/归类） | 一组相关 markets 的主题聚合 | `event.ticker`, `event.slug`, `event.title` | 1 event -> N markets |
| Market（可交易合约） | 一个具体问题/合约 | `market.id`, `market.slug`, `market.question`, `condition_id` | 1 market -> K outcomes |
| Outcome（结果） | market 内的某个 outcome（YES/NO/...） | `outcomes[i]` | 1 outcome -> 1 token_id |
| Token（CLOB 资产） | 订阅盘口/下单用的资产标识 | `token_id`（CLOB） | 1 token_id -> 1 outcome |

在本项目中：

- PM-Arb 配置的 **pair** 是交易单位：一个二元 market 用 `(yes_token_id, no_token_id, condition_id, partition)` 表达。
- `pair.name` 只是人类可读标签。它可以写成 event 维度，但推荐写成 market 维度（例如带上 market id/slug），避免一个 event 下有多个 market 时撞名。

## 1) 策略设计（核心思路）

Polymarket 的二元市场（YES/NO）基于 CTF 抵押品，理论上满足近似无套利恒等式：

- 理想情况下：`Price(YES) + Price(NO) ~= 1.0`（忽略手续费与执行摩擦）

PM-Arb 监控某个 YES/NO 对应的盘口最优价（top-of-book），寻找两类机会：`merge_arb` 与 `split_arb`。

### Merge Arb（先买入双腿，再合并）

触发条件：

- `ask_yes + ask_no + fee_buffer < 1.0`

直觉解释：

- 用 `ask_yes` 买 1 份 YES，用 `ask_no` 买 1 份 NO。
- 然后把 `(YES, NO)` 合并（merge）回大约 1 USDC 抵押品。
- 若总成本加 buffer 小于 1.0，差值就是套利空间（edge）。

动作顺序（逻辑上）：

1. YES：按 `ask_yes` 下 BUY
2. NO：按 `ask_no` 下 BUY
3. 调用 `mergePositions`（tool service：`POST /ctf/merge`）

### Split Arb（先拆分抵押品，再卖出双腿）

触发条件：

- `bid_yes + bid_no > 1.0 + fee_buffer`

直觉解释：

- 如果你能按 `bid_yes` 卖 YES、按 `bid_no` 卖 NO，合计收入超过 1.0（再加 buffer）。
- 你可以先把 1 USDC 拆分（split）成 1 YES + 1 NO，然后把两份都卖掉。

动作顺序（逻辑上）：

1. 调用 `splitPosition`（tool service：`POST /ctf/split`）
2. YES：按 `bid_yes` 下 SELL
3. NO：按 `bid_no` 下 SELL

### `partition: [1,2]` 是什么？

`partition` 是 CTF 合约 `splitPosition/mergePositions` 的参数，表示“如何把 outcome space 切分/合并”。

- 对二元 YES/NO，`partition=[1,2]` 是最常见、最标准的表达方式，表示两个互补 outcome 的 index-set。
- PM-Arb 会把 `partition` 和 `condition_id` 一起传入 `/ctf/split`、`/ctf/merge`。

### 决策、收益与规模（如何选交易）

PM-Arb 关键变量：

- `edge_merge = 1.0 - (ask_yes + ask_no + fee_buffer)`
- `edge_split = (bid_yes + bid_no) - 1.0 - fee_buffer`
- `notional_merge = size * (ask_yes + ask_no)`
- `notional_split = size * 1.0`（每一对 YES+NO 对应约 1 USDC 抵押品）
- `expected_profit_usdc = max(0, edge) * notional - gas_estimate_usdc`

交易规模 `size` 的上限来自两部分：

- 盘口约束：双腿都要能吃到的 size（`min(yes_size, no_size)`）
- 名义规模上限：`PM_ARB_MAX_NOTIONAL_USDC_PER_TRADE`

风控门槛：

- `PM_ARB_MIN_EXPECTED_PROFIT_USDC`
- `PM_ARB_GAS_MULTIPLIER_GUARD * PM_ARB_GAS_ESTIMATE_USDC`

执行安全边界（非常重要）：

- 当前 tool-service API 不支持“YES+NO 双腿严格原子化的 FOK 一次性成交”。
- 当 `PM_ARB_STRICT_FOK_REQUIRED=1` 且 `PM_ARB_ALLOW_DEGRADED_EXECUTION=0` 时，实盘会被设计为阻断（避免 legging 风险）。

## 2) PM-Arb 和 PMM 怎么一起用

PMM 与 PM-Arb 解决的是不同问题，可以并行运行：

- PMM（`src/domains/pmm/`）：常态做市，持续挂单，靠价差吃流量，并做库存管理/风控。
- PM-Arb（`src/domains/arb/`）：只有当 YES/NO 恒等式偏离足够大时才出手，目标是“更接近无风险”的套利。

推荐的协同方式（后续可实现）：

- 共享同一个 WS orderbook 缓存（避免两边各开一个 WS，看到的世界不一致）。
- 当 PM-Arb 触发时，让 PMM 暂停该 pair 的做市（或取消某一侧挂单），避免自相残杀/资金占用冲突。

## 3) 模拟命令输出字段含义

你会看到类似：

- `[PM-ARB][tick=0] pairs=3 actions=1 top_pair=... top_action=... top_edge=... top_expected_profit=... top_size=...`

含义：

- `tick`：循环计数
- `pairs`：本 tick 评估了多少个 pair
- `actions`：本 tick 实际尝试执行了多少个动作
- `top_*`：把所有 pair 按 `expected_profit` 排序后，取第一名（当你配置了多个 pair 时，用于快速判断“当前最强机会”）

每个动作会打印一个字典（dry-run 时尤其清晰）：

- `pair`：pair 名称
- `action`：`merge_arb` 或 `split_arb`
- `size`：本次交易规模（shares；在脚手架里近似等同于 USDC 抵押品数量）
- `expected_profit`：扣掉 `gas_estimate_usdc` 后的期望收益

`merge_arb` 会包含：

- `buy_resp`：YES/NO 两腿 BUY 的意图（dry-run 为本地结构，实盘为接口返回）
- `merge_resp`：merge 的意图或交易回执（dry-run/实盘）

`split_arb` 会包含：

- `split_resp`：split 的意图或交易回执（dry-run/实盘）
- `sell_resp`：YES/NO 两腿 SELL 的意图/返回

若由于安全开关被阻断，会看到：

- `status: "blocked"` 与 `reason`

## 4) 监控建议（重点看什么）

最小监控集（每 tick / 每 pair）：

- `edge` 与 `expected_profit`
如果 edge 经常为正但几乎不执行，说明阈值太严或行情数据不新鲜。
如果执行很多但收益很小，说明 `fee_buffer`/`gas_estimate_usdc` 太乐观。
- `size`
如果 size 长期很小，说明盘口太薄或你的 notional cap 太小。
- 动作类型频率（merge vs split）
长期偏向某一边，可能是数据源偏差或 buffer 参数不合理。
- `blocked/error` 比例
blocked 多往往是还在 dry-run 或严格 FOK 安全模式（预期行为）。
error 多通常是 API/网络/字段结构问题。

实盘还应补充（当前脚手架未完全实现）：

- legging 风险统计（双腿顺序执行造成的暴露）
- 每条腿的 placed vs filled 比例与滑点
- split/merge 的确认时间（时间越长，资金占用越高）
- paired inventory 漂移（没及时 merge 回来会“卡资金”）

## 5) Mock 模拟（不依赖网络）

PM-Arb 支持 mock 数据源，用于在网络不可用时验证逻辑链路：

- `PM_ARB_MARKET_DATA_SOURCE=mock`
- `PM_ARB_MOCK_SCENARIO=toggle|merge_arb|split_arb|neutral`

示例：

```bash
PM_ARB_MARKET_DATA_SOURCE=mock \\
PM_ARB_MOCK_SCENARIO=toggle \\
PM_ARB_MAX_TICKS=6 \\
PM_ARB_TICK_INTERVAL_SEC=0.2 \\
PM_ARB_DRY_RUN=1 \\
PM_ARB_PAIRS_JSON='[{\"name\":\"pair\",\"yes_token_id\":\"YES\",\"no_token_id\":\"NO\",\"condition_id\":\"0x..\",\"partition\":[1,2]}]' \\
python3 -m src.domains.arb.main
```
