# Core Carry event-driven lifecycle v2

## 结论与动作

**保持当前 10-share Core Carry 的 HOLD 语义，不部署止盈、减仓或天气风险退出。**

这轮同时否掉了两个看似合理的退出办法：静态 +3c 止盈在最新统一盘口回放中显著伤害赢家；独立 market-free 存活概率头在固定分母上没有打赢 Core，也没有稳定打赢 market。当前不是缺少一个更聪明的阈值，而是缺少足够多的“入场后状态变化且最终失败”的训练/验证事件。下一步只应继续采集 zero-notional lifecycle evidence，不改变真实订单。

## 研究问题和固定动作空间

- target metric：同一 Core entry 下，退出 overlay 相对 hold-to-settlement 的 10-share fee-adjusted PnL delta。
- 动作：`HOLD/NO_ADD`、首次净 bid ≥ entry+3c 时全退、首次净 bid ≥ market-free P(held wins) 时减半或全退。
- model head：仅使用 23 个天气/路径/forecast/transport 特征，不输入 market mid、bid/ask、Core p 或入场盈亏。
- 训练：按 target_date 等权、date 内 state 等权；前 8 日 warm-up，development expanding OOF；最后 8 日为预留窗口。
- 执行：只有 top bid size ≥10 才视为可执行，退出扣官方 Weather taker fee；无深度不 fallback。

## 概率门（state-entry 固定分母）

| window | rows / dates / losses | model | Brier | logloss |
|---|---:|---|---:|---:|
| development OOF | 417 / 15 / 34 | market | 0.069637 | 0.254467 |
| development OOF | 417 / 15 / 34 | Core | 0.068163 | 0.248730 |
| development OOF | 417 / 15 / 34 | market-free ridge | 0.077047 | 0.281725 |
| development OOF | 417 / 15 / 34 | market-free HGB | 0.072495 | 0.273685 |
| last-8 secondary window | 138 / 8 / 14 | market | 0.078782 | 0.291308 |
| last-8 secondary window | 138 / 8 / 14 | Core | 0.076940 | 0.275130 |
| last-8 secondary window | 138 / 8 / 14 | market-free ridge | 0.086589 | 0.341337 |
| last-8 secondary window | 138 / 8 / 14 | market-free HGB | 0.079032 | 0.296744 |

按 development 预先选择表现更好的 HGB 用于 lifecycle replay。最后 8 日 HGB−Core Brier delta `+0.002092`，date-block CI `[-0.011514, +0.015798]`；HGB−market `+0.000250`，CI `[-0.007851, +0.008517]`。负数才是改善；这里概率门没有通过。

注：最后 8 日概率分数先被查看过，随后才完成 lifecycle PnL 汇总，因此它是 secondary historical window，不冒充 pristine frozen forward。

## 历史 post-entry 执行证据

完整 ledger 有 `1349` checkpoints / `801` states / `31` dates；frozen Core selected entries `136`，其中最终 loss `6`。但能在入场后继续看到 checkpoint 的 position 只有 `82`，且这些 post-entry 路径中最终 loss 只有 `1`。

最后 8 日可执行 lifecycle 分母仅 `14` entries / `7` dates / `0` losses。market-free 全退 delta `-2.77`，CI `[-4.705300000000003, -0.8152775000000323]`；减半 delta `-1.38`，CI `[-2.4097000000000017, -0.40870000000000095]`。这个分母不足以验证“能否救错单”。

## 最新统一盘口反事实（外部验证静态止盈）

截至 `2026-08-09T07:36:10.935079+00:00`，Core raw 有 `51` signals，settled `41`；其中 `34` entries / `11` target dates 有新报文后的完整 10-share bid。hold PnL `+15.37`，+3c 全退 `+11.28`，delta `-4.09`，date-block CI `[-7.47, -1.36]`。

+3c 共退出 `25` 笔：final winners `25`、losses `0`；saved loss capital `0.00`，sacrificed winner profit `4.09`。这不是轻微噪声：当前同分母中它只卖掉赢家，没有救到一笔亏损。

## Signal / evidence funnel

- signal funnel：1,349 continuous checkpoints → 801 first city-date-bracket states → 136 frozen selected entries。
- evidence funnel：136 selected → 有 later checkpoint 的 positions → 有可卖 10-share bid 的 positions → settled losses；coverage 缺口没有伪装成策略过滤。
- 当前 raw static replay 是另一条外部时间窗，只用于检验 +3c 表达；因为历史 feature schema 与当前 raw 尚未逐字段 parity，market-free head 没有事后套到当前 live signals。

## Gate 与后续

- probability gate：FAIL；market-free head 未在 development/secondary window 稳定胜 Core/market。
- execution gate：静态 +3c FAIL；其 current unified-book delta CI 完全为负。
- lifecycle loss evidence：FAIL；post-entry selected-loss 路径太少。
- action：Core 继续 `10-share taker + existing maker` 的既有入场和 hold；不加 exit/reduce；只恢复/完善 zero-notional post-entry event ledger 时，需单独走部署确认。

## 8 环

描述性=PASS；统计推断=PASS（target-date block）；信号判别=FAIL；概率分布=FAIL；执行微结构=PASS for +3c / historical lifecycle limited；容量=PASS 到10 shares；组合=PASS；同分母 market/Core baseline=PASS；fresh frozen forward=FAIL。

## 可复现产物

- run directory：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/current_yes_core_carry_event_lifecycle_v2/market_free_hgb_20260809`
- feature ledger SHA-256：`5af298442a87e1579d1c8981c4be445fea2c9253458a8c79be00cc9668084fbb`
- `state_predictions.csv`：全部 checkpoint 的 OOF/secondary predictions。
- `historical_lifecycle_positions.csv`：selected position 的 HOLD/static/model exit 逐笔反事实。
- `result.json`：本报告全部数字。
