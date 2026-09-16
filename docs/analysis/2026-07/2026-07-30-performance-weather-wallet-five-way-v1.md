# 五个 Weather 钱包长期盈亏与可复制性 v1

## 结论

直接跟单五个钱包都不成立；公开 activity 出现时，真正的 source/book edge 往往已经消失。若复制的是策略机制：

1. **先拆 `WeatherHK2`**：长期 `+$15.38k`，资金加权 ROI `17.04%`，去掉最赚钱五天仍有 `6.12%`；区域集中、每个 city-day 中位 8 笔 BUY、4 个 session，首买到首卖约 9.25 小时，是收益、稳健性和工程复杂度之间最好的平衡。
2. **以 `jjavi` 做收益上限参考**：长期 `+$28.27k`、ROI `19.93%`，五者最高，去掉最赚钱五天仍有 `11.17%`；但每个 city-day 中位 20 笔 BUY、7 个 session，宽 YES distribution 的 sizing/rebalance 更复杂。
3. **`badatmath` 只拆分布建模，不照搬执行**：长期 `+$46.18k`、ROI `6.18%`，95% target-date bootstrap CI 为 `[1.58%, 11.46%]`；但中位 40 笔 BUY、21 个 session、建仓跨度约 40.9 小时，最新 30 个 target dates ROI 已降至 `4.27%`。
4. **`HighTempTation` 是 latency benchmark，不是复制对象**：`+$49.93k`、ROI `9.67%`，稳定性最好；但首买到首卖中位只有约 11 秒，96.0% SELL proceeds 在 `>=99c`，公开 activity 跟随没有可执行窗口。
5. **`MidYes56b` 暂不优先**：`+$13.26k`、ROI `6.93%`，但 CI 跨零，去掉最赚钱五天只剩 `1.14%`。

## 同口径结果

主 ROI 为：

```text
资金加权长期 ROI
= Σ(SELL + REDEEM + MERGE - BUY - SPLIT) / ΣBUY cost
```

“平均日 ROI”先把同一钱包同一 `target_date` 的所有城市合并，再对独立 target dates 等权平均；它用于看典型日，不是账户资本回报率。

| 钱包 | 完整 city-day | 独立日期 | BUY cost | 长期 PnL | 长期 ROI | 平均日 ROI | 中位日 ROI | 95% target-date CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `jjavi` | 418 | 71 | $141.80k | **+$28.27k** | **19.93%** | 25.64% | 12.06% | [10.52%, 30.85%] |
| `WeatherHK2` | 221 | 69 | $90.28k | +$15.38k | 17.04% | 31.28% | 4.53% | [5.66%, 35.61%] |
| `HighTempTation` | 1,002 | 73 | $516.22k | **+$49.93k** | 9.67% | 9.35% | 9.15% | **[8.10%, 11.19%]** |
| `MidYes56b` | 543 | 121 | $191.40k | +$13.26k | 6.93% | 5.94% | 5.78% | [-0.01%, 14.45%] |
| `badatmath` | 3,837 | 90 | $746.71k | +$46.18k | 6.18% | 8.87% | 7.15% | [1.58%, 11.46%] |

按长期 ROI 排序和按绝对 PnL 排序不同：`HighTempTation` 的绝对利润最高，`jjavi` 的资金效率最高。平均日 ROI 会放大小资金日，因此决策以资金加权长期 ROI 为主。

## 稳健性与复制成本

| 钱包 | 最近 30 个 target dates ROI | 去掉最佳五日 ROI | 后半段 ROI | target-date 最大回撤 | target-day BUY | 首买→首卖中位 | 中位 BUY 笔数 / sessions | 判断 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `WeatherHK2` | 15.04% | 6.12% | 15.33% | -$558 | 69.2% | 9.25h | 8 / 4 | **首个复制对象** |
| `jjavi` | 16.69% | **11.17%** | 15.46% | -$1,666 | 55.0% | 13.65h | 20 / 7 | **最佳主参考，执行较复杂** |
| `HighTempTation` | 10.13% | 8.65% | 10.37% | -$1,339 | 98.8% | **约 11 秒** | 1 / 1 | 只作 source-event/latency benchmark |
| `badatmath` | 4.27% | 2.56% | 5.24% | **-$18,311** | 43.9% | 48.70h | **40 / 21** | 研究 forecast distribution，暂不照搬 |
| `MidYes56b` | 12.75% | 1.14% | 6.17% | -$5,304 | 93.9% | 1.37h | 9 / 4 | 收益集中，暂不优先 |

### 为什么首选 `WeatherHK2`

- 收益不是少数大日完全撑起：去掉最佳五天仍为 `+6.12%`。
- 交易窗口以小时计，不依赖 `HighTempTation` 的秒级竞争。
- 华南城市集中，source profile、settlement lattice 和跨城 relative value 的研究边界更小。
- 执行复杂度低于 `jjavi` / `badatmath`，可以先重建 HKO/华南 path residual，再验证 mixed YES/NO ladder 的增量。

`jjavi` 的 ROI 与尾部稳健性更好，但它更适合当模型基准：先复原 Europe forecast-distribution / YES-strip 概率，再决定是否需要 20 笔级的动态调仓，不应直接模仿钱包交易序列。

## 数据快照与证据边界

| 项目 | 值 |
|---|---|
| 数据源 | Polymarket public Data API activity / positions、Gamma event metadata |
| 快照 | `WeatherHK2=20260730T135201Z`；其余四个 `20260730T135339Z` |
| 覆盖 | 307,407 weather activity rows；6,147 city × target-date portfolios；6,021 cashflow-complete resolved portfolios |
| 时间范围 | 各钱包可见 weather 历史从 2026-02-12 至 2026-07-30 不等 |
| settlement | 只纳入 Gamma resolved、snapshot current value 接近 0、且 public cashflow 完整的 portfolio |
| fee basis | 使用 public activity `usdcSize` 的实际成交现金效果，不用 leaderboard `PnL / volume` 代替 ROI |
| `badatmath` 特例 | closed-position discovery endpoint 在 10,000 行截断；PnL 主数据来自完整 256,659 行 weather activity，因此不影响现金流计算。4 个 metadata-incomplete portfolios 未进入主结果 |

机器可读结果：

- `generated/weather_wallet_performance_compare_20260730_v1/comparison.csv`
- `generated/weather_wallet_performance_compare_20260730_v1/comparison.json`

```text
signal funnel:
5 shortlisted wallets
-> complete public weather activity
-> city × target_date mutually-exclusive ladder
-> cashflow-complete resolved portfolio
-> wallet × target_date performance block

evidence funnel:
leaderboard discovery
-> public BUY / SELL / SPLIT / MERGE / REDEEM cashflow
-> Gamma bracket and settlement metadata
-> resolved currentValue cross-check
-> target-date bootstrap / concentration / timing
```

Public API 不提供 private signal、unfilled/cancelled orders、原始挂单时间、当时完整 order book 或 source first-seen。因而本报告能确认历史钱包盈利和交易形态，不能确认照搬后仍有同样 edge：

```text
baseline=NA
private_signal=NA
public_follow_copy=not_executable
mechanism_replication=research_candidate
```

## 下一步研究动作

先做两个同分母机制重建：

1. `WeatherHK2`：华南 city-day 全 ladder 时间线，按 HKO / settlement source / market residual 拆出“信息 edge、路径 edge、主动 rebalance”各自 PnL。
2. `jjavi`：Europe pre-target + target-day YES distribution，重建每档 shares 与 market-implied distribution，比较固定 strip 与动态 20 笔调仓。

`HighTempTation` 留作快源事件延迟上限，`badatmath` 留作 D-2/D-1 forecast-distribution 结构样本，`MidYes56b` 暂不进入下一轮。
