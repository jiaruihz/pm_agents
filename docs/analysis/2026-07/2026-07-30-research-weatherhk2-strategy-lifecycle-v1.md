# WeatherHK2：区域天气 ladder 库存与路径换档研究 v1

## Data Snapshot

- source：Polymarket public activity / positions / Gamma metadata 的本地完整快照，wallet `0xdadbf9e1df1b8d7a184a0d6ab9c83b2337b61870`
- snapshot：`20260730T135201Z`；分析生成于 `2026-07-30T14:35Z`
- grain：主口径为 `city × target_date` 完整互斥 ladder；机制诊断另用 condition 和 public fill transaction
- rows：8,099 条 weather activity；227 个 event portfolio，其中 221 个已结算且 public cashflow complete
- coverage：69 个独立 target_date，`2026-04-25` 至 `2026-07-29`
- unsettled / incomplete：6/227 = 2.64%，不进入 PnL；metadata incomplete = 0，missing bracket metadata = 0
- 生产 preflight：`weather_production_manifest.py --strict` 通过，canonical DB 路由为同一 inode；本报告没有改写 canonical facts
- 产物：[summary.json](generated/weatherhk2_strategy_v1/summary.json)、[slice_performance.csv](generated/weatherhk2_strategy_v1/slice_performance.csv)、[condition_lifecycle.csv](generated/weatherhk2_strategy_v1/condition_lifecycle.csv)、[case_timelines.json](generated/weatherhk2_strategy_v1/case_timelines.json)

## 结论与动作

**WeatherHK2 值得参考，但不能照抄它的成交。** 可复制的核心不是“买香港某个温度”，而是：

1. 专注香港、深圳、广州的完整 exact-bracket ladder；
2. D-1 建立便宜的候选库存；
3. D0 随温度路径在 `current YES / next YES / previous-or-current NO` 间换档；
4. 主动 SELL 无效腿、MERGE/REDEEM 回收资金，而不是全部持有到结算。

暂不复制它的 size、低价挂单成交率或 lifetime underround。先做一个 **zero-notional shadow router**，同时记录 signal、PIT book、source first-seen、理论 taker fill 和 passive maker fill；至少积累 30 个新 target_date 后再判断。

当前状态：`inconclusive / research-only`。它的 public wallet 赚钱真实且跨时间仍为正，但最赚钱的成交高度集中，并且公开数据看不到 maker/taker、原始挂单时间和当时盘口，尚不能证明我们能以相同价格复制。

## 它到底在做什么

### 1. 地域集中，而不是全球扫天气

| 城市 | portfolio | buy cost | public PnL | ROI |
|---|---:|---:|---:|---:|
| Hong Kong | 60 | $43,672.20 | +$10,140.10 | 23.22% |
| Shenzhen | 54 | $26,546.83 | +$2,187.89 | 8.24% |
| Guangzhou | 40 | $10,357.82 | +$2,125.65 | 20.52% |

三城占 89.25% buy cost、93.96% PnL，合计 ROI 17.94%。这更像对华南温度路径和结算源有长期专注，而不是跨城市无差别套利。

### 2. 主表达是混合 ladder，不是单腿方向盘

| event-level expression | portfolio | buy cost | PnL | ROI |
|---|---:|---:|---:|---:|
| mixed YES/NO | 118 | $74,302.00 | +$12,448.68 | 16.75% |
| NO only | 21 | $6,275.69 | +$247.56 | 3.94% |
| single YES | 60 | $4,976.10 | +$820.61 | 16.49% |
| YES strip | 22 | $4,730.27 | +$1,866.02 | 39.45% |

`mixed YES/NO` 消耗 82.3% buy cost，也贡献 80.9% PnL。单独照抄 NO-only 最没有吸引力；YES strip 的 39.45% 很高，但只有 22 个 portfolio，且是事后 expression 分类，不能直接升级为新 gate。

### 3. 它反复换仓，主动退出是策略主体

- 146/221 = 66.1% portfolio 同一 token 出现 BUY→SELL round trip；这些 active-sell portfolio ROI 17.87%，不 SELL 的 75 个为 8.06%。
- SELL proceeds 为 $49,824.83，相当于总 buy cost 的 55.19%；另有 $10,540.84 MERGE cash，52 个 portfolio 使用过 MERGE。
- 71/221 = 32.1% portfolio 在同一 condition 买过 YES 和 NO；114/658 个 traded condition 出现双边买入。
- 7,498 个独立 public trade transaction 的相邻时间中位数只有 27 秒，29.5% 相隔不超过 2 秒；峰值 49 transaction/min。执行明显是自动化的。

这些数字支持“库存管理 + 路径换档”，但不能单凭双边交易就称为 market making：public API 不给 maker/taker 和原始 order-post time。

### 4. 时间结构：D-1 质量最高，D-2 资金效率最低

| 首次入场 | portfolio | buy cost | PnL | ROI |
|---|---:|---:|---:|---:|
| D-2 或更早 | 69 | $50,890.33 | +$3,312.82 | 6.51% |
| D-1 | 63 | $21,837.65 | +$9,491.23 | 43.46% |
| D0 | 86 | $16,623.04 | +$2,568.82 | 15.45% |

D-1 只用 24.2% cost，却贡献 61.7% PnL；D-2-or-earlier 用掉 56.4% cost，只贡献 21.5% PnL。复制时应先研究 D-1→D0 的状态转换，不应复制它所有更早库存。

注意：这是按 portfolio 的“首次 fill 日期”事后分组，不是随机化 A/B；D-1 的高 ROI 受香港 7/14 极端成交影响，不能直接定成 eligibility。

## 四个逐日案例

### Hong Kong 2026-07-14：利润核心来自不可默认复制的极低价库存

- winner：28°C；buy cost $1,364.56；PnL +$6,605.93，ROI 484.11%。
- D-1 已买 28 YES 和 28 NO；随后在 215 秒内通过 60 个 transaction，以 0.002–0.003 买入 12,749.13 股 28 YES，现金成本仅 $35.50。
- D0 随 28 YES 上涨分批 SELL，仍保留部分 winner token 结算。

这一 event 独自贡献全钱包 42.94% PnL。它证明低价库存可能有巨大 convexity，却不能证明我们可以用 taker order 获得同样 0.2–0.3¢ 的数量；更可能包含提前挂出的 passive liquidity 和队列优势。

### Hong Kong 2026-07-16：错误的 overshoot 换档会快速吞掉利润

- winner：27°C；buy cost $1,244.93；PnL -$466.44。
- 钱包先交易 27 YES，随后大量增加 27 NO 和 28 YES，表达“会越过 27 到 28”。
- 最终最高温仍停在 27；28 YES 和 27 NO 都错，后段分别接近 0.001 退出。

这不是普通预测误差，而是 exact-bracket 语义下的 overshoot state transition 失败。复制版本必须显式估计 `P(overshoot | current path)`，不能把“已到 27”自动翻译成 27 NO / 28 YES。

### Shenzhen 2026-07-09：会认错并旋转到相邻赢家

- winner：30°C；buy cost $939.60；PnL +$434.88。
- D-1/早盘先持有 34 YES、再买约 2,500 股 33 YES，随后逐步把错误高温腿卖到接近 0.001。
- 目标日转为 29 NO，并晚些买 30 YES；最终 29 NO 与 30 YES 同时兑付。

这说明盈利机制不只是 forecast pick，关键是沿路径把远端错误腿换成与当前状态相容的相邻组合。

### Guangzhou 2026-06-27：尾部库存命中

- winner：35°C or higher；buy cost $213.14；PnL +$720.74。
- D-1 多次小额累积 35+ YES，目标日继续加仓；同 condition 也持有少量 NO。
- 最终尾部 YES 命中。

它支持“D-1 小成本尾部库存 + D0 confirmation”的候选机制，但一个成功案例不足以区分物理 edge 与廉价 convexity。

## 盈利质量与稳定性

主口径：

- resolved buy cost：$90,284.06
- public cashflow PnL：+$15,382.88
- turnover ROI：17.04%
- target-date block bootstrap 95% CI：5.61%–36.09%
- positive target-date share：63.77%；median target-date ROI：4.53%

稳定性：

- 前 34 个 target_date（4/25–6/24）：ROI 24.27%
- 后 35 个 target_date（6/25–7/29）：ROI 15.33%
- 最近 30 个 target_date：ROI 15.04%
- 去掉最大 event：ROI 9.87%
- 去掉盈利最高的 5 个 target_date：ROI 6.12%
- top 5 event 占总 PnL 68.03%

所以“长期为正”成立，“17% 可稳定复制”不成立。剔除极端日仍为正是最值得继续研究的部分。

## Signal Funnel

单位必须分开：

1. **raw opportunity（city-day）**：69 个 target_date、221 个完整 city-day ladder portfolio；
2. **regional mechanism candidate（city-day）**：香港/深圳/广州 154 个 portfolio；
3. **path-managed candidate（portfolio）**：146 个有主动 SELL，71 个出现同 condition 双边买入；
4. **proposed shadow signal（首次 city-day signal）**：尚未生成。公开 wallet fill 不能反推唯一、事前可执行的 signal。

本报告没有把“有公开 fill”伪装成我们可执行的 signal。

## Evidence Funnel

1. **public cashflow coverage**：221/227 portfolio complete；
2. **settlement / bracket coverage**：221 个主样本均有完整 ladder metadata 和 winner；
3. **PIT source coverage**：不足。完整钱包从 4/25 开始，而本地高频 source first-seen 档案只覆盖后段，不能给全历史做同口径 source-latency 对齐；
4. **PIT executable book coverage**：不足。快照没有钱包原始挂单、maker/taker、队列和每个 fill 前一刻完整盘口；
5. **actual public fills**：8,099 activity rows，可证明成交和现金流，不能证明别人可获得同样 fill。

coverage gap 只记为 evidence gap，不当成策略过滤。

## 八环验证与可复制设计

1. **问题定义**：判断 WeatherHK2 的长期盈利来自方向预测、区域/结算源、full-ladder relative value，还是执行与库存管理。
2. **机制假设**：主假设是 `D-1 cheap inventory → D0 path update → neighboring YES/NO rotation → SELL/MERGE capital recycling`。
3. **PIT replay**：未通过。现有公开快照只给 fill time；缺原始 signal/order-post 和全历史同口径 PIT book/source。
4. **同分母 baseline**：未通过。不能在相同 city-day/PIT row 上比较 market、static hold、taker-only 和 passive-maker。
5. **统计显著性**：描述性总体 bootstrap 为正，但切片为事后多重探索；最大 event 贡献 42.9%。
6. **forward**：钱包自身早/后半均为正，只是 temporal persistence，不是 frozen policy forward。
7. **执行经济性**：真实 public cashflow 为正，主动 SELL 和 MERGE 是重要组成；我们自己的 queue、fee、slippage、missed-fill 尚未重放。
8. **上线边界**：只允许 zero-notional shadow；不改 live，不用钱包 ROI 直接做 sizing。

建议的 shadow schema：

```text
city, target_date, decision_ts,
source_first_seen_ts, settlement_native_lattice_state,
running_max, forecast_peak_clock, remaining_heat,
p_ladder[], market_bid_ask[],
desired_inventory_yes_no[], reason_code,
taker_counterfactual_fill, maker_counterfactual_fill,
rotation_from, rotation_to, sell_or_merge_action
```

先做两条同分母 challenger：

- `static-D1`：D-1 建仓后不换档，只结算；
- `path-router`：D-1 同库存，D0 按连续 `P(outcome)-market` residual 换档并 SELL/MERGE。

两条都要在相同 city-day、相同 PIT book、官方 fee 下比较；maker 版本与 taker 版本分开，不能用 wallet 的已成交价填补我们的 missed fill。

## Gates

`significance=descriptive-pass / strategy-fail`

总体 target-date bootstrap CI 为正，但 post-hoc 子策略和利润集中度未过预注册显著性。

`baseline=fail`

缺相同 PIT row 的 market/static-hold/taker-only baseline。

`forward=fail`

只有 wallet temporal split，不是 frozen shadow forward。

`conclusion=inconclusive-research-only`

复制机制框架，不复制成交、size 或 17% ROI；下一步是零资金 full-ladder path-router shadow。

## 口径限制

- public timestamp 是 fill time，不是 signal time 或 order-post time。
- condition lifetime YES VWAP + NO VWAP 小于 1 的 54 条记录只是跨时段诊断，不能当同步可成交 arbitrage。
- public API 不暴露 maker/taker、取消单、队列位置、私有 forecast 或未成交机会。
- PnL 使用 cashflow-complete resolved portfolio 的 public activity cashflow；单 fill fee 归因无法独立审计，因此不把该 ROI 称为我们可复制的 fee-adjusted alpha。
- 本报告研究外部公开钱包，不写入 `fact_signal_candidates` / `fact_trades`，也不改变任何生产行为。
