# Tokyo market-anchor 历史覆盖与训练量敏感性 v7

## 结论

`2026-07-16..30` 不能作为 v6 full-stack 的完整 frozen holdout 重跑。严格早于
`7/16` 的 Tokyo 同刻盘口只有 `7/15` 一天；可连接行仅 4 条，全部是
`archive_reconstructed_plus_15m`，`collector_exact=0`。这不足以训练带 6 个 weather/path
特征和截距的 market-offset 层，也不能冒充 PIT 历史。

盘口训练量会影响模型概率。固定 v6 `physical ridge=1` 结构、固定相同的
`7/27..29` 测试集后，累计训练日期从 3 增加到 5/7/9 天，checkpoint Brier 从
`0.02046` 降到 `0.01388 / 0.01245 / 0.01152`；同分母 market 为 `0.01946`。
这是“继续积累后可以迭代”的正向证据，但测试只有 3 个 target dates，CI 均不足，且
state-entry 仍差于 market。因此动作是继续 exact collector + zero-notional 取证，不恢复
旧 15 天为 holdout、不改 live。

## Target 与固定口径

目标：在相同未来 PIT checkpoint/label/book rows 上，检验固定 v6 market-offset 规格的
训练 target-date 数量是否影响 Brier/logloss、预测稳定性和 zero-notional entry。

- unit：Tokyo JMA 10-minute checkpoint；相关性单位为 `target_date`；
- target：最终 exact maximum 是否停在 current bracket；
- candidate：`logit(P_market) + physical weather/path correction`；
- market：同一 checkpoint current-exact midpoint；交易成本使用同刻 side ask + 官方 fee；
- 固定模型：v6 `physical` features、ridge `1.0`，本轮 `K=1`，不重新挑结构；
- common test：`2026-07-27..29`，61 checkpoints / 3 dates，其中 exact 40 / 3；
- train size：3/5/7/9 个更早 target dates；所有 fit 都满足 `train_end < 2026-07-27`；
- 本轮属于 post-v6 development diagnostic，不是 untouched forward。

## 历史盘口覆盖审计

| evidence layer | rows | dates | window | `7/16` 前 | 判断 |
|---|---:|---:|---|---:|---|
| raw full-ladder books | — | 17 | `7/15..31` | 1 date | 原始采集从 `7/15` 开始 |
| v1 pre-holdout join | 4 | 1 | `7/15` | 1 date | 全是 archive +15m |
| pre-holdout collector exact | 0 | 0 | — | 0 | 不存在 strict PIT 训练日 |
| v6 settled prepared market | 238 | 12 | `7/16..29` | 0 | 已属于原 15 日窗口 |
| v6 collector exact | 129 | 8 | `7/22..29` | 0 | 可作 development/后续训练 |

`7/17、7/18` 没有进入当前 weather-to-book settled join，`7/30` 在本次 artifact 冻结时
没有 settlement。这些是 evidence coverage gap，不是信号筛除。

### `/prices-history` 补充审计（只补 price proxy，不改上表 exact-book 口径）

后续 v8 实测确认，CLOB `/prices-history` 可显著扩展历史 market-anchor，但它不是历史
orderbook。`2026-04-01..07-15` 的 Tokyo 06:00–18:00 JST 天气分母共 7,632 checkpoints /
106 dates；其中 6,691 rows / 104 dates 能映射 exact-current YES token，6,661 rows /
103 dates 在假定 `observation+15m` 后 15 分钟内取得 price point。缺口是 5/17–18 无 event、
5/19 market 在目标日结束后才创建。

与现有 238 条 collector/archive book midpoint 重合校验中，220 条能取得 2 分钟内 prior
history point；median absolute difference 0.1c，但 MAE 2.57c、p90 8.1c（快速重定价与
proxy 语义共同影响）。因此它只能作为 `midpoint_proxy` 训练 market-offset correction、
做同 rows probability baseline 或 repricing path；不能补 best bid/ask、spread、size/depth，
不能计算 executable ROI，也不能把 `archive_reconstructed_plus_15m` 改写成 exact first-seen。
完整审计与可复现脚本见
[`generated/tokyo_clob_price_history_coverage_v8/report.md`](generated/tokyo_clob_price_history_coverage_v8/report.md)。

相关 collector 当前仍在积累：`live_cross_observations` 中 Tokyo `jma_amedas`
在 `2026-07-31T12:56:54Z` 记录了 observation `12:50Z` 的 exact
`source_first_seen_at_utc` 和 raw hash；生产 orderbook 在本次检查时最新为
`14:18:28Z`、约 7.7 分钟。总 health 为 `warn`，原因是另一份旧
`output/high_frequency_observations/state.json` 陈旧；当前 live-cross JMA 与 orderbook
链本身在更新，不能把旧 state 的 stale 状态误判成 Tokyo collector 停止。

## 训练量 learning curve

### 累计训练：自然模拟“多积累几天再升级”

所有行使用相同 61-row / 3-date common test：

| train dates | train window end | checkpoint Brier | logloss | Brier Δ vs market |
|---:|---|---:|---:|---:|
| market | — | 0.01946 | 0.06921 | — |
| 3 | `7/20` | 0.02046 | 0.06831 | +0.00101 |
| 5 | `7/22` | 0.01388 | 0.05175 | -0.00558 |
| 7 | `7/24` | 0.01245 | 0.04837 | -0.00700 |
| 9 | `7/26` | **0.01152** | **0.04417** | **-0.00794** |

点估从 5 天开始优于 market，并随累计日期增加继续改善；但 3-date block bootstrap
CI 全跨 0，不能称为统计确认。

collector-exact checkpoint 的 Brier 为：market `0.01830`，3/5/7/9-day model
分别 `0.01880 / 0.00740 / 0.00771 / 0.00492`。方向一致，但 exact test 仍只有 3 天。

### 固定训练终点：旧数据是否继续有帮助

都训练到 `7/26`，只改变向前包含的日期数：

| trailing train dates | checkpoint Brier | logloss |
|---:|---:|---:|
| 3 | 0.01322 | 0.04634 |
| 5 | 0.01458 | 0.04964 |
| 7 | 0.01206 | 0.04540 |
| 9 | **0.01152** | **0.04417** |

并非每增加一天都单调改善：5 天反而比 3 天略差，说明 regime/date composition 也重要；
但 7–9 天整体最好，较老数据当前没有明显拖累。

### 参数与信号稳定性

累计 3→5 天时，同一测试 row 的概率平均绝对变化 `2.88pp`，p95 `23.06pp`；5→7 天
降到 `0.56pp`，7→9 天为 `0.88pp`，但 p95 仍有 `6.16pp`。因为策略 entry edge
阈值只有 2%，尾部概率变化仍足以改变交易方向。

实际 selected entry 也会变化：3-day prefix 在 `7/29 current=34` 选择 NO，5/7/9-day
版本改为 YES；所以盘口训练量不仅影响“准确率”，会直接改变 side、触发数量和 ROI。
本轮 common test 的可执行交易最多只有 3 笔，不能用 ROI 选择训练天数。

### 关键未修复项：state-entry

同一 common test 的 state-entry 只有 5 states / 2 dates：market Brier `0.00077`，
3/5/7/9-day 累计模型分别 `0.00082 / 0.00352 / 0.00293 / 0.00359`。更多普通
checkpoint 数据没有自动解决“刚进入新档第一刻”的概率质量。这也是为什么不能因
checkpoint learning curve 变好就立即把模型接到交易。

## 下一次迭代方案

1. 保持当前 v6 artifact 不动，从 `2026-08-01` 起持续写完整 zero-notional score；每个
   checkpoint 都记录 model、market、side ask、edge 和后续 settlement，而不是只存触发单。
2. 以 `collector_exact + settled + PIT book` 的独立 target dates 计数。达到 **20 个训练日**
   时只做第一次候选 refit；至少另留 **10 个完全未读日期**作 frozen holdout。也就是首个
   有意义的升级评审分母为 30 个 exact settled dates，而不是 checkpoint 行数。
3. 评审必须同时看 checkpoint、transition、state-entry；优先指标为相对同 rows market 的
   date-equal Brier/logloss。state-entry 未改善时，即使普通 checkpoint 更好也不晋级。
4. 若 30 日评审仍因 CI 或 regime 不稳定，继续积累到约 **40 train + 20 holdout** 再评；
   不在等待期间滚动调特征/阈值污染 forward。
5. 每次升级产生新 model id，并保留旧版同期 A/B zero-notional prediction。训练窗扩大后
   旧 forward 会转成 development，下一段日期才是新版本的 untouched forward。

这组 20+10 是研究/迭代门槛，不是 live 晋级门。真实部署仍需 significance、同分母
market baseline 和 untouched forward 三门共同通过。

## 双漏斗与 8 环

```text
signal funnel (common test):
61 checkpoints / 3 dates
  -> 8–9 eligible states（多数 5+ day variants）
  -> 3 first date×bracket signals / 2 active dates
  -> 2–3 five-share executable counterfactuals（variant dependent）

evidence funnel:
17 raw-book dates
  -> 12 settled prepared dates
  -> 8 collector-exact dates
  -> 3 common-test dates / 40 exact checkpoints
  -> actual order/fill = 0/0
```

- 已覆盖：描述性概率/交易诊断、date-block CI、同分母 market、PIT book、fee/depth
  counterfactual、训练量 A/B；
- 未覆盖：足量独立日期、稳定 state-entry、untouched forward、真实 fill/queue、容量、组合；
- gates：`significance=FAIL`、`baseline=FAIL`、`forward=NA`；
- conclusion：`inconclusive_continue_collection`；deployment：`zero-notional_only`。

## 复现

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_market_anchor_training_curve_v7.py
```

主要产物：

- `generated/tokyo_market_anchor_training_curve_v7/coverage_audit.csv`
- `generated/tokyo_market_anchor_training_curve_v7/probability_learning_curve.csv`
- `generated/tokyo_market_anchor_training_curve_v7/prediction_stability.csv`
- `generated/tokyo_market_anchor_training_curve_v7/strategy_learning_curve.csv`
- `generated/tokyo_market_anchor_training_curve_v7/summary.json`
