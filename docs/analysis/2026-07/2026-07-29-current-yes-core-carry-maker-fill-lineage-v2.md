# Current-YES Core Carry maker 成交血缘审计 v2

## 数据快照

- 数据源：当前 Mac/JRS Core Carry `live_orders.jsonl`、其中持久化的 authenticated order state、authenticated CLOB trades，以及 `clob_fills.jsonl` 对照。
- 数据快照时间：runner latest summary `2026-07-29T14:13:02Z`；订单文件 131 rows。
- maker 记录：22 个初始 intent，其中 20 个成功挂出、2 个 post error；生命周期累计产生 76 个 distinct posted maker order ids。
- authenticated maker fill：11 个 intent、55 shares；这些 target dates 均已 closed，unsettled=0%，missing_bracket=0。
- production manifest 本轮被同机长时间运行的 coverage-gate/DB I/O 占用，未取得新的 manifest JSON；本报告只使用当前 raw order lineage 与 authenticated CLOB 证据，不依赖 canonical DB 计算 fill rate。

目标 grain 是一个 `(strategy instance, city, target_date, token)` maker intent。问题是：原定 5-share maker leg 真正成交多少，以及未成交是否来自正常 post-only 选择还是执行生命周期问题。

## 结论

用户观察成立：**Core Carry maker leg 不是可靠容量。** 20 个已挂出的 maker intent 只有 11 个成交，intent fill rate `55%`；真实 maker 成交 55/100 planned shares。若 taker leg 固定成交 5 shares，则一个原计划 10-share 的 split opportunity 平均只完成约 `7.75 shares`。

低成交由两个机制叠加：

1. 正常的 maker 选择：订单只挂在 bid 一侧，受 trigger midpoint / model probability cap 约束，从不主动跨 ask；只有卖方主动打进来才成交。
2. 可改进的队列损失：15 秒检查、15 分钟 TTL 下，20 个 intent 累计生成 76 个 posted order ids。reprice 会先撤旧单再挂新单，价格追上去了，但队列时间优先级不断重置。

极端例子：

- San Francisco 2026-07-26：38 个 posted child orders，最终 0 maker fill。
- Chicago 2026-07-27：10 个 posted child orders，最终 0 maker fill。
- 说明问题不只是 TTL 太短，而是频繁 cancel/repost 没有换来成交。

## 执行漏斗

```text
22 initial maker intents
  -> 20 successfully posted
  -> 76 distinct posted order ids after lifecycle replacements
  -> 11 filled intents
  -> 55 authenticated filled shares
```

成功挂单后的 intent fill rate为 `11/20 = 55%`；另有 Cape Town 2026-07-27 与 Karachi 2026-07-29 两个初始 maker post error。

## fill cache 勘误

旧 fill cache 把账户级 public activity 按 token/价格/时间回配给本地 order，曾显示：

- 17 maker fill rows；
- 72.38 maker shares；
- 13 个 maker-filled markets。

authenticated order-id 复核后，5 rows / 17.38 shares 没有对应 maker order match：

- Singapore 初始已取消且 `size_matched=0` 的订单被误配 5 shares；同一 lifecycle 的 replacement 真实成交 5 shares。
- Cape Town 两笔共 2.38 shares 没有 authenticated order match。
- Wellington 2026-07-29 两笔共 10 shares 发生在本地 maker orders 已取消数小时后，authenticated trade 中对应的是其他 order ids，不属于 Core Carry maker。

所以正确口径是 11 filled intents / 55 shares，而不是 13 markets / 72.38 shares。当前 coverage gate 能检查 fill 数量、成本、order cap 与 alias，但没有阻止这种“账户级成交被分配到错误本地 order id”的问题。

## 对收益口径的影响

剔除上述 5 笔错误 maker lineage 后，上一版 Core Carry tiny-live provisional 数字修正为：

| 版本 | 已结算 fills | 成本（含 fee） | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|
| v2 | 19 | $92.28 | -$17.28 | -18.72% |
| v3 | 14 | $59.96 | +$5.04 | +8.40% |
| 合计 | 33 | $152.24 | -$12.24 | -8.04% |

maker leg 单独为 12 physical fill rows / 11 orders、成本 `$50.40`、PnL `-$5.39`、ROI `-10.71%`。亏损仍主要来自旧 v2 Singapore/Chengdu overshoot；样本不足，不能据此判定 maker 本身负 alpha，但也不能再把它描述为已经验证的 spread improvement。

## 推荐的下一步

不直接把 maker 全改成 taker，也不只延长 TTL。应做同分母 execution shadow A/B：

1. 当前 chase：15 秒观察、允许 reprice。
2. queue-preserving：初始改善一 tick 后保持队列，只有 thesis 失效、观察更新或经济 edge 明显变化才撤。
3. maker-then-taker：到预注册 deadline 时，仅在 refreshed taker EV 仍为正才补未成交 shares；单独计算多付 spread/fee 与新增 overshoot exposure。

主指标为 authenticated intent fill rate、price improvement、5/15/30 分钟 markout 和 fee-adjusted PnL delta；future touch 不算 fill。

三门：`significance=FAIL`、`baseline=NA`、`forward=FAIL`，`conclusion=inconclusive`。当前动作是修正 fill lineage，并把 queue-preserving A/B 放入 shadow；不据此直接改 live execution。
