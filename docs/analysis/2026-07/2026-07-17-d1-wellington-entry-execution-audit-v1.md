# d1 Wellington 入场执行审计 v1

## 结论

Wellington 这笔没有发生“下单滑点”：策略以 fresh ask `0.93` 计划并以 `0.93` 成交。真正的执行成本是跨过 `0.87/0.93` 六个点的 spread，再加 taker fee。按 fresh midpoint `0.90` 计，5 shares 的 spread crossing 是 `$0.15`；按天气市场 `feeRate=0.05` 口径估算，fee 是 `$0.016275`，合计约 `$0.166275`，即成交成本的 `3.58%`。

但这笔也不支持直接改成 maker-only：公开逐笔显示，信号仍有效的几分钟里买盘持续在 `0.93` 成交，没有证据表明 `0.88` 的 bid+1 maker 或 `0.92` 的 ask-1 maker 能成交。更合理的待验证方案是：第一腿先短暂 maker chase，等待 30–60 秒；同一 observation、信号仍成立且 ask 未超过原 cap 时再转 taker。第二腿继续使用当前“5 taker + 5 maker、maker 管到下一次 observation”的 paired A/B。

当前证据只有 3 笔旧版 taker fill，执行优化结论为 `inconclusive`，不据此改变 live 行为。

## 口径与数据快照

- target metric：在不显著降低 signal capture rate 的前提下，降低每 share 的 `fill price + fee`。
- 分母：`d1_yes_high_mid_live_v1` raw live order ledger 中截至 2026-07-17 的 3 笔已成交旧版 taker order；Wellington 逐笔单独做盘口路径审计。
- order truth：`runtime/weather_edge_v1/d1_yes_high_mid_live_v1/live_orders.jsonl`。
- signal truth：`runtime/weather_edge_v1/d1_yes_high_mid_live_v1/shadow_events.jsonl`。
- entry book：`/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/orderbook_snapshots/2026-07-17/orderbook_snapshot_20260717_0851.jsonl.gz`，另以 executor 下单前 fresh book 为实际执行基准。
- 后续路径：Polymarket public trades；只用来判断 maker 成交可能性，不把 future price/mid touch 当作实际 fill。
- observation：AviationWeather NZWN METAR。报文 observation time 不等于本系统 first-seen time，无法据此反推精确 ingest latency。
- 本报告是 raw execution audit，不发布 canonical `live_real` PnL/ROI，因此不使用 settlement PnL，也不触发 canonical fill coverage gate。

## Wellington 单笔血缘

| 字段 | 值 |
|---|---:|
| signal cycle | `2026-07-17T01:02:53Z` |
| order created | `2026-07-17T01:02:56Z` |
| 本地时间 | Beijing `09:02:56` / Wellington 约 `13:02:56` |
| running max / d1 | `14°C / 15°C` |
| archive YES book | `0.82 / 0.93` |
| executor fresh YES book | `0.87 / 0.93` |
| top ask depth | `89.97 shares` |
| fill | `5 @ 0.93`，成本 `$4.65` |
| slippage vs fresh ask | `$0.00` |
| spread crossing vs midpoint | `(0.93 - 0.90) * 5 = $0.15` |
| estimated taker fee | `5 * 0.05 * 0.93 * (1 - 0.93) = $0.016275` |
| total drag vs midpoint | `$0.166275` |

信号读取的观测约 26 分钟旧，状态仍为 `14°C`。官方 METAR 的 `01:00Z` 报文已经打印 `15°C`，但本系统在 01:02–01:05 的 telemetry 仍消费旧状态；这里存在 observation→ingest 的 blind window，不过没有 first-seen 日志，不能断言报文当时已经可被本系统获取。

公开 trades 在本单之后仍有多笔 `0.93` 主动买入：约 01:03:35、01:04:23、01:05:04、01:06:17；约 01:06:59 又有 `0.96` 买入。信号有效窗口内没有观察到 `0.88` 或 `0.92` 的主动卖出。因此：

- `0.88` bid+1 maker：理论上每 5 shares 可比本单节省 `$0.25 + fee`，但本次大概率不会成交。
- `0.92` ask-1 maker：若成交只节省 `$0.05 + fee`，本次仍无逐笔成交证据。
- 30–60 秒 maker probe 后 capped taker：从 `0.93` ask 深度及后续成交看，有机会保留本次 capture，同时让少数愿意打 bid 的订单贡献 price improvement；这是反事实推断，不是已验证 fill。

## 现有 3 笔 taker 的描述性成本

| city | fill | notional | estimated fee | half-spread crossing |
|---|---:|---:|---:|---:|
| Ankara | `5 @ 0.84` | `$4.20` | `$0.033600` | `$0.20` |
| Warsaw | `5 @ 0.972` | `$4.86` | `$0.006804` | `$0.03` |
| Wellington | `5 @ 0.93` | `$4.65` | `$0.016275` | `$0.15` |
| total | `15 shares` | `$13.71` | `$0.056679` | `$0.38` |

三笔合计 mid-relative execution drag 约 `$0.436679`，占 notional `3.185%`。如果三笔 bid+1 maker 全部成交，理论节省上限为 `$0.711679`；这个数字没有计入 maker miss、排队和 adverse selection，不能作为可实现收益。

## 建议与验证条件

当前不改 live。新版已经在同一 signal 上生成 5-share taker 与 5-share maker 的 paired evidence，后续按以下指标比较：

1. maker fill rate、成交等待时间、相对 taker 的每 share price improvement；
2. 到下一 observation 仍未成交的比例，以及 fallback taker 的最终价格；
3. maker fill 与 miss 的 settlement/adverse-selection 差异；
4. signal capture rate 与净 PnL，而不是只比较已成交 maker 的价格。

等 paired forward 样本形成后，再评估把第一腿改成“30–60 秒 maker chase → same-signal capped taker”。正式升级仍需按同分母显著性、baseline、forward 三道 gate；当前分别为 `FAIL_LOW_SAMPLE / PARTIAL / NA`。

