# Core Carry 上次校验后增量 live 复盘 v1

Status: `current-reference / tiny-live evidence / no-live-change`

## 口径

- 窗口：`2026-08-03T17:08:22Z`（上次校验 cutoff）之后，查询截至 `2026-08-06`。
- 信号 grain：`signal_id` / city-target-date-bracket；accuracy 不按 child fill 重复计数。
- 盈亏 grain：canonical `fact_trades` 的真实 `live_real` fill，含 effective fee；maker dust 单列。
- raw probability：`live_orders.jsonl.model_token_probability`。本窗口 canonical `signals.model_p_yes` 存在已确认的迁移映射污染，不能用于概率评估。
- `fact_signal_candidates` 当前最大 `event_date=2026-08-02`；本窗 8 个 signal count 以 raw `entry_attempts/live_orders` 为当前生产事实，不把 opportunity coverage gap 伪装成策略过滤。
- settlement：定向补齐 8 个 city-day pm_history 后，11/11 fill 均由 token 精确匹配到 settled outcome。
- CLOB gate：`gate_pass=true`；DB/cache/fact 均为 1,369 distinct fills，cost mismatch 为 0，fee lineage `exact=903 / maker_zero=430 / estimate=36 / unknown=0`。

## 结果

| city | target | bracket | raw p | taker fill | fee | result | taker PnL |
|---|---|---:|---:|---:|---:|---|---:|
| Miami | 2026-08-03 | 90-91 | 0.97793 | 10 @ 0.97000 | 0.01455 | win | +0.28545 |
| NYC | 2026-08-03 | 84-85 | 0.97398 | 10 @ 0.97260 | 0.01332 | win | +0.26068 |
| Manila | 2026-08-04 | 30 | 0.99191 | 10 @ 0.99000 | 0.00495 | win | +0.09505 |
| SanFrancisco | 2026-08-04 | 72-73 | 0.98629 | 10 @ 0.98000 | 0.00980 | win | +0.19020 |
| Wellington | 2026-08-05 | 9 | 0.96275 | 10 @ 0.91000 | 0.04095 | win | +0.85905 |
| Manila | 2026-08-05 | 28 | 0.97390 | 10 @ 0.96000 | 0.01920 | win | +0.38080 |
| CapeTown | 2026-08-05 | 16 | 0.94702 | 10 @ 0.91000 | 0.04095 | win | +0.85905 |
| Amsterdam | 2026-08-05 | 25 | 0.97171 | 10 @ 0.95356 | 0.02214 | win | +0.44226 |

汇总：

- 8 个有效信号，8 胜 0 负，accuracy `100%`；只有 3 个 target dates，不能据此做显著性晋升。
- 8 笔有效 taker fill，共 80 shares；fee-adjusted realized PnL `+$3.37254`，fill cost `$76.46160`，ROI `+4.41%`。
- 8 个 maker parent、10 个 maker exchange orders（Amsterdam 两次 reprice），有效成交 `0`。仅 Miami/NYC/Amsterdam 各有 `0.000005` share dust，合计 0.000015 share、PnL `+$0.00000065`；实际风险和收益均由 taker 承担。
- raw probability 均值 `0.97319`，预期胜场 `7.785/8`；按实际 fill+fee 的模型期望 PnL `+$1.22750`。实际多出的约 `$2.145` 是全胜样本的实现波动，不是新增结构性 alpha 证据。
- 同一小样本上 model Brier/logloss `0.000887 / 0.02727`，entry best-ask baseline 为 `0.002825 / 0.04649`；由于 8 行全为 winner 且经过高概率 selector，这只能记作 forward-positive telemetry，不能判模型已显著胜 market。

## Insight 与动作

1. **不调整概率模型或新增天气 gate。** 本窗没有 model failure，且 raw p 对 8/8 结果并不过度自信；样本只有 3 个日期，不能反推 overshoot 风险已经解决。
2. **maker 腿目前没有规模贡献。** 计划 40 maker shares，meaningful fill 为 0；继续把它视为 execution probe，不把 maker shares 算进实际容量。下一次容量研究应在同一批历史 book 上比较 `10 taker + 5 maker` 与 `15 taker`，使用真实 15-share ladder、fee 和 target-date block，不因本次 8/8 直接扩 live。
3. **固定 10-share taker 的资本效率差异很大。** NYC 的 net model edge 仅约 0.005c/share，Manila 30 约 0.142c/share；Wellington/CapeTown 分别约 4.87c/3.29c/share。优先研究连续 net-EV sizing，而不是再加价格 hard gate。当前组合含 fee 的 break-even accuracy 约 `95.78%`，任意一笔 10-share loss 都会把本窗 PnL 从 `+$3.37` 变成约 `-$6.63`。
4. **先修分析血缘，再评估 calibration。** `strategy_runtime_orders` 漏读 `model_token_probability`，使本窗 8/8 canonical signal 的 `model_p_yes/edge` 为 0。实例历史污染范围为 2026-07-25..2026-08-05 的 22/54 canonical signals；旧的 canonical calibration/edge 切片必须排除这些行或回到 raw。该 bug 不影响 runner 当时的下单决策、fill、fee 或 PnL。

## 数据修复状态

- 已修增量 settlement ingest 的日期/city 过滤，避免先扫全历史 signals/cache。
- 已给 fact builder 增加 `--fill-id` 增量物化，只更新已存在 fill，不删除其他事实行；本窗 11 行已 settled 重放。
- 已修未来 runtime-order migration 对 `model_token_probability` 的读取，并为 YES 生成 canonical model-market residual；定向测试通过。
- 既有 22 个 `signals` 行是 append-only，本次未获授权做全量 canonical rebuild，因此历史 `model_p_yes=0` 尚未物理改写；本报告的概率数字来自精确 raw lineage。
- 已尝试官方 candidate 增量入口：`--incremental-start-date 2026-08-03 --snapshot-lookback-days 2 --no-parquet`。它在 JRS DB 上连续 10 分钟停留于全表 read、无输出且无写入，已安全中止；因此 candidate 层仍只到 8/02，属于明确未完成的 coverage gap。
