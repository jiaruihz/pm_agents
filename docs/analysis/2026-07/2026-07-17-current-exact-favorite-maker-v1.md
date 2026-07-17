# Current Exact Favorite Maker v1

- Status: `research_snapshot`
- Verdict: `inconclusive_shadow_research_only`
- Script: `scripts/analysis/market_structure_edge/research_current_exact_favorite_maker_v1.py`
- Artifact: `docs/analysis/2026-07/generated/current_exact_favorite_maker_v1/summary.json`

## 数据快照

- 主分析输入：`intraday_weather_regime_state_rows.csv`，mtime `2026-07-09 11:37 CST`，14,368 个 city-date-hour PIT state、36 城、日期 `2026-05-19..2026-07-08`；本策略可用结算信号止于 `2026-07-07`。
- canonical 新鲜度核对：`fact_signal_candidates.decision_snapshot_ts_utc` 到 `2026-07-16 13:58Z`，`fact_trades.fill_ts_utc` 到 `2026-07-17 06:46Z`，`settlement_outcomes.target_date` 到 `2026-07-15`。
- Mac fresh full-ladder collector 已有 71 个 complete snapshot。最新 `2026-07-17 07:12Z` 一轮有 738 rungs、47 城、91 city-date，全部 YES/NO book status=`ok`，718 rungs 有双侧 direct bid/ask，fallback=0；但对应 `7/17..7/18` 尚未结算，因此只能证明未来可采集完整执行分母，不能补本报告的 alpha 证据。
- 本轮不以“截至今天的最新收益”下结论，也不发布 live PnL；使用冻结的历史校准分母复算一个新 hypothesis，因此没有执行 fact rebuild 或全量重算。

## 直接结论

**除 `d1 YES mid>0.80` 外，目前仍没有第二条已确认 alpha。** 最干净、值得冻结规则继续收证据的是：

> `current_exact_favorite_maker_v1`：当 current exact bracket YES 的同刻 mid 首次高于 0.98，只挂一次短生命周期 post-only BUY YES，赚 favorite 低估与 half-spread；不吃 ask，不跨下一次数据更新。

它与 d1 策略独立：d1 买的是“最终正好再升一档”，本策略买的是“最终最高温停在已经打印的当前档”。它也不是天气 hard-filter 策略；物理/路径字段只记 telemetry，不作 eligibility gate。

当前可确认的是**概率层存在约 0.30c/share 的 favorite 低估**；不可确认的是**被动成交后还剩多少 edge**。因此动作是 queue-aware zero-notional shadow，不是 tiny-live。

## 目标、分母与表达

Target metric：

```text
P(current exact bracket wins | same-snapshot PIT market state)
  - executable price
```

并把它拆成两个不能混淆的层：

1. 概率层：settlement frequency - same-snapshot mid。
2. 执行层：fill-conditioned settlement PnL - queue adverse selection。

冻结规则：

- grain：每个 `(city, target_date)` 第一次 `current YES mid > 0.98`；后续重复状态不重复下意图。
- mid：`(direct current YES ask + 1 - direct current NO ask) / 2`。
- quote：BUY current YES，post-only；能改善时挂 `best_bid + 1 actual tick`，否则 join best bid，绝不 lock/cross。
- lifecycle：`maker_until_data_update`，在下一次 registered official/source/model data epoch 前撤单；无 taker fallback。
- size：shadow 统一记 5 planned shares；maker fee=0，rebate 不计入 baseline。
- fill 后持有到 settlement；不加城市、时段、天气、forecast、support-count 或其他事后 filter。

## 双漏斗

Signal funnel：

| stage | unit | n |
|---|---|---:|
| atlas PIT states | city-date-hour state | 14,368 |
| current exact quote + settlement 有效 | state | 11,246 |
| current YES mid > 0.98 | qualifying state | 3,682 |
| 每 city-date 首次触发 | signal | 1,295 |

Evidence funnel：

| stage | unit | n |
|---|---|---:|
| 首次触发有 PIT quote + settlement | signal | 1,295 |
| 同刻可形成 post-only price | planned maker order | 1,295 |
| 有 queue/tape 可验证的历史 maker fill | fill | **0** |
| fill-linked 1/5/15m markout + settlement | fill | **0** |

盘口/成交缺失记作 evidence gap，不当作策略筛除。

## 复算结果

CI 均为 target-date block bootstrap 95%。maker 列是假设每个计划单都按同刻挂价成交、且没有逆选的 **zero-adverse-selection benchmark**，不是可执行 ROI。

| slice | rows / dates | win | mid bias | maker benchmark | same-trigger taker after fee |
|---|---:|---:|---:|---:|---:|
| 全部 qualifying states（概率诊断） | 3,682 / 47 | 99.810% | +0.295c `[+0.131,+0.430]` | +0.472c `[+0.306,+0.604]` | +0.032c `[-0.140,+0.166]` |
| **首次触发（诚实策略分母）** | **1,295 / 47** | **99.537%** | **+0.320c `[-0.058,+0.643]`** | **+0.623c `[+0.227,+0.963]`** | **-0.091c `[-0.467,+0.224]`** |
| train `<2026-06-21` | 974 / 33 | 99.487% | +0.265c `[-0.205,+0.678]` | +0.547c `[+0.087,+0.960]` | -0.127c `[-0.569,+0.267]` |
| forward `>=2026-06-21` | 321 / 14 | 99.688% | +0.488c `[-0.169,+0.858]` | +0.856c `[+0.195,+1.247]` | +0.015c `[-0.643,+0.367]` |

诚实读法：

- 全状态的 +0.30c favorite bias 是统计上可见的市场结构；但首次触发去重后，单独的 mid bias CI 跨 0。
- 首次触发平均模拟挂价 0.98913，zero-adverse-selection benchmark 约 0.623c/share；5 planned shares 只有约 **$0.031** 期望空间。
- fill-conditioned 胜率必须高于约 **98.913%** 才 break even。平均 5-share 单次 loss 约 **$4.95**；逆选只要吃掉 0.623 个百分点，edge 就归零。
- 同分母 taker after fee 不正，因此这不是“换成吃单也能跑”的策略。

## 为什么它比其他候选干净

本轮不是继续扫任意阈值。`>0.98` 来自已经观察到的全市场 favorite-longshot calibration bucket，候选动作只改变 execution expression；没有新增城市/天气/时段组合。

| family | 当前证据 | 处理 |
|---|---|---|
| current-exact favorite maker | 47 个独立 target dates 的概率层弯曲；quote rule/lifecycle 明确 | **唯一进入 queue-aware shadow 的新 hypothesis** |
| H1 late carry / heat-death | holdout 7 行/6 日，absolute +5.8%；相对同价 base-fade 仅 +0.4pp，CI `[-4.3,+6.5]` | 物理 telemetry，不作新 gate |
| H2 early dislocation | proxy 151 行/8 日，ROI +0.3%，CI `[-2.8,+3.1]`；ask +2c 后 -1.1% | 不成立 |
| fast-source previous NO | actual 11 fills/5 日；source/settlement basis 与执行污染未解 | collector-only |
| coherent calibrator / target-book | clean 8,094 states/22 日；最小模型 logloss 比 market 差 +0.0078，CI `[+0.0016,+0.0172]` | 不能当 alpha |

另外出现过一个看似更强的 full-ladder normalized YES 切片：11 行/8 日、9 win、ROI +40.6%。逐 event 还原后，入选状态只 join 到 6/11 或 8/11 rungs，**0 条有完整 ladder**；归一化概率被缺 rung 人为抬高。该结果按 coverage artifact 丢弃，不进入候选榜。

## 最大反证：maker 历史逆选

旧 `2026-06-13-maker-backtest-v0` 已证明“挂单=免费收 spread”不成立：白名单 420 个 order、30.7% fill、filled ROI -12.6%；60 个真正 passive fill 胜率仅 53.3%、ROI -31.7%。愿意砸到 resting bid 的交易者往往拿到了更新的温度信息。

本 hypothesis 与旧回测的差别只有两个：高 favorite current YES 分母、订单不跨 data epoch。它们足以支持重新做 shadow，**不足以推翻 maker 逆选先验**。

## 八环与 three gates

| 环 | 状态 |
|---|---|
| 1 label scope | PASS：current exact settlement 语义明确 |
| 2 PIT feature | PASS：只用同 snapshot direct quote；无事后天气特征 |
| 3 probability | MARGINAL：全状态 bias CI>0，首次触发 CI 跨 0 |
| 4 action mapping | PASS：单边 current YES、一次触发、无 filter stack |
| 5 executable price | FAIL：可形成 resting price，但真实 queue fill=0 |
| 6 liquidity/capacity | FAIL：无 queue-ahead、partial fill、planned-notional capacity |
| 7 uncertainty | PASS：按 target_date block bootstrap |
| 8 representation | PARTIAL：36 城/47 日，但只覆盖 5–7 月市场阶段；fresh complete ladder 尚无足够 settlement |

```text
significance=FAIL_EXECUTABLE_POLICY
baseline=PASS_PROBABILITY_LAYER_ONLY
forward=FAIL_NO_QUEUE_AWARE_FILLS
conclusion=inconclusive_shadow_research_only
```

## 下一步冻结实验

只做 zero-notional/paper observer，不启动真实订单：

1. 每个候选同时记录 `maker / taker-now / no-trade`，未成交也留在 planned denominator。
2. 记录 actual tick、queue ahead、top-of-book/depth、partial fill、cancel reason、source/official/model epoch，以及 1/5/15m markout。
3. 任何订单不得跨 data epoch；不做 chase，不 fallback 到 taker。
4. 至少取得 **30 个可信 queue fills + 12 个独立 target dates**；planned-denominator 净 edge CI 下界 >0、fill-conditioned settlement edge CI >0、markout 不显著为负，才讨论 tiny-live。

在这些条件满足前，动作是：**保留并采集，不 live、不加 size、不用事后 filter 美化。**

## 证据入口

- [Market calibration curve v1](2026-07-15-market-calibration-curve-v1.md)
- [Current YES heat-death physical backtest v1](2026-07-14-current-yes-heat-death-physical-backtest-v1.md)
- [Strategy search reset v1](2026-07-14-strategy-search-reset-v1.md)
- [Tmax clean denominator feature restoration v1](2026-07-12-tmax-clean-feature-restoration-v1.md)
- [Maker backtest v0](../2026-06/2026-06-13-maker-backtest-v0.md)
