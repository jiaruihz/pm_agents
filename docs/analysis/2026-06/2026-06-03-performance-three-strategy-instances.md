# 三策略实例绩效复盘：mid_price_core_v1 / v2 / side-band

Status: `snapshot`。Used by current decision: yes。本文是 V2 停 live、Amsterdam/BuenosAires 降 T2 的证据入口之一；当前生产口径见 [../../WEATHER_STRATEGY_ENTRYPOINT.md](../../WEATHER_STRATEGY_ENTRYPOINT.md) 和 [../../WEATHER_CITY_POOL_DECISIONS.md](../../WEATHER_CITY_POOL_DECISIONS.md)。

## 数据快照

目标指标：

- `three_strategy_live_real_performance` = “只看 `trade_class='live_real'` 的真实成交 fill，在 `fact_trades` 里按三条实例分支比较 realized PnL / ROI / win rate / 成交质量 / 价位桶 / 城市池分组。”
- `v1_v2_overlap_core9` = “v1/v2 对比只在 v2 实际允许的 core 9 城和 v2 有样本的同一 target_date 窗口内比较；新增城市单独拆分，不混入 v1/v2 主对照。”
- 三实例映射：`mid_price_core_v1_25_75` = `execution_policy=mid_price_core_v1 + entry_price_window=0.25-0.75`；`mid_price_core_v2_25_75` = `execution_policy=mid_price_core_v2 + entry_price_window=0.25-0.75`；`mid_price_core_v1_side_band` = `execution_policy=mid_price_core_v1 + entry_price_window=0.35-0.65`。
- 分母：live 真实成交 `fact_trades.trade_class='live_real'`。Realized PnL 只纳入 `settlement_status='settled'`；`missing_bracket` 和 null settlement 不混入 realized PnL。

数据源：`runtime/weather.db.fact_trades` + `runtime/weather.db.fact_signal_candidates`。
DB last modified：2026-06-03 23:56 +0800。
fact built：2026-06-03T15:56:xxZ。
同步状态：2026-06-03 23:55 +0800 执行 `scripts/ops/sync_weather_remote.sh` 成功，market data 与 remote pm_agent runtime 均已同步。`run_stack.sh --no-rebuild` 执行时 8000 端口被占用，未启动 API，但离线 DB rebuild/fact builder 已完成。
记录行数：`fact_trades` 4055 rows；其中 `live_real` 465 rows / settled 322 rows。
unsettled/null 占比：全表 142 / 4055 = 3.5%。
missing_bracket 数：全表 725；三实例 live_real 中 v1 25-75 有 51，v2 有 38，side-band 0。

完整性自检：

| check | value |
|---|---:|
| total fact_trades rows | 4055 |
| live_real rows / settled | 465 / 342 |
| live_simulated rows / settled | 669 / 475 |
| paper rows / settled | 2285 / 1751 |
| snapshot_replay rows / settled | 636 / 620 |
| settled rows with null PnL | 0 |
| fact_signal_candidates rows | 20144 |
| eligible candidates | 6217 |
| decision_window_missing | 8862 / 20144 = 44.0% |

## 总览：全量 live_real 只作背景

| strategy_instance | fills | settled | missing/null | cost | pnl_fill | ROI | win_rate | avg fill | avg fill-plan |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mid_price_core_v1_25_75 | 274 | 206 | 68 | 733.26 | +34.24 | +4.7% | 60.2% | 0.557 | -0.0003 |
| mid_price_core_v2_25_75 | 74 | 36 | 38 | 94.68 | -28.36 | -30.0% | 33.3% | 0.465 | -0.0072 |
| mid_price_core_v1_side_band | 2 | 2 | 0 | 6.58 | +4.39 | +66.8% | 100.0% | 0.585 | +0.0000 |

这张表不是 v1/v2 的公平 A/B：v1 是全扩展 T1，v2 是 core 9 城，时间窗也更短。公平对照见下一节。

交易动作先给结论：

1. **v1/v2 公平对照口径下，两者都亏；v2 在 core 9 同期亏得少，但样本很小且 missing_bracket 高。**
2. **新城市不能混入 v1/v2 主对照。** 新增 8 城在 v2 下亏损显著；v3 新增 6 城也未兑现 paper 预期。
3. **side-band 样本少是真的，且不是拉取旧数据造成的。** 最新 raw live_cycle 显示 149 轮只产生 266 个 signals、32 个 accepted plans、31 个 live orders，最终 fact_trades 只有 2 个 live_real fills；这 2 笔已结算 +4.39，但样本完全不够。这说明条件/城市/时间窗组合太窄，实验设计本身有问题。

## v1/v2 公平对照：core 9 城，同一日期窗口

v2 和 side-band 启动脚本都限制在 core 9 城：

```text
Boston, LA, London, Miami, NYC, Phoenix, Shanghai, Tokyo, Warsaw
```

因此公平对照只取 core 9 城，且取 v2 有样本的 `target_date=2026-05-29..2026-06-01`。

| strategy_instance | fills | settled | missing/null | cost | pnl | ROI | win_rate | avg fill | avg fill-plan |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mid_price_core_v1_25_75 | 34 | 17 | 17 | 59.88 | -1.94 | -3.2% | 52.9% | 0.542 | +0.0047 |
| mid_price_core_v2_25_75 | 34 | 12 | 22 | 33.52 | -5.34 | -15.9% | 41.7% | 0.455 | -0.0008 |

按方向：

| strategy_instance | side | fills | settled | cost | pnl | win_rate | avg fill |
|---|---|---:|---:|---:|---:|---:|---:|
| v1 25-75 | BUY_NO | 23 | 11 | 35.35 | -2.83 | 63.6% | 0.668 |
| v1 25-75 | BUY_YES | 11 | 6 | 24.53 | +0.89 | 33.3% | 0.312 |
| v2 25-75 | BUY_NO | 16 | 6 | 18.72 | +1.89 | 50.0% | 0.600 |
| v2 25-75 | BUY_YES | 18 | 6 | 14.80 | -7.23 | 33.3% | 0.310 |

这个口径下，最新结算后 v1 已接近打平，v2 仍小亏。更准确的说法是：**core 9 同期里两者都没有强正收益证据；v1 暂时略好，但样本太小且 missing_bracket 高，不能定胜负。**

## 分支归因

### 方向

| strategy_instance | side | fills | settled | cost | pnl | win_rate | avg fill |
|---|---|---:|---:|---:|---:|---:|---:|
| v1 25-75 | BUY_NO | 212 | 157 | 589.08 | +1.95 | 64.3% | 0.626 |
| v1 25-75 | BUY_YES | 62 | 45 | 131.47 | +23.19 | 44.4% | 0.315 |
| v2 25-75 | BUY_NO | 41 | 21 | 64.88 | -22.45 | 38.1% | 0.570 |
| v2 25-75 | BUY_YES | 33 | 14 | 24.80 | -7.95 | 21.4% | 0.291 |

v1 的钱主要来自 BUY_YES，而不是 BUY_NO；BUY_NO 胜率高但赔率吃掉了大部分收益。v2 两侧都亏，BUY_NO 尤其差。

### 赔率 / 价位桶

| strategy_instance | fill bucket | settled | cost | pnl | win_rate |
|---|---|---:|---:|---:|---:|
| v1 25-75 | 20-35c | 35 | 99.65 | +4.61 | 37.1% |
| v1 25-75 | 35-50c | 29 | 98.66 | +36.53 | 62.1% |
| v1 25-75 | 50-65c | 51 | 185.72 | -41.34 | 51.0% |
| v1 25-75 | 65-80c | 86 | 334.67 | +27.19 | 74.4% |
| v2 25-75 | 20-35c | 14 | 21.00 | -4.15 | 21.4% |
| v2 25-75 | 35-50c | 3 | 12.17 | -12.17 | 0.0% |
| v2 25-75 | 50-65c | 9 | 31.82 | -5.11 | 44.4% |
| v2 25-75 | 65-80c | 8 | 23.82 | -8.10 | 50.0% |

v1 的 50-65c 桶是当前最明显的赔率坑，35-50c 和 65-80c 反而贡献正收益。v2 没有任何一个主要价位桶站住，说明不是“某个 price bucket 轻微调参”能直接救回来。

机会宇宙的 `fact_signal_candidates` 也支持这个方向：在可估值分母 `eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0` 上，T1 BUY_YES 反事实 `cf_pnl=+50.28`，T1 BUY_NO 反事实 `cf_pnl=-43.56`。但注意 decision window 缺失 44.0%，这个机会结论只覆盖能估值的另一半机会，不能当作全量市场真相。

## 新城市单独分析

新增城市不属于 v2/core 9 主对照，单独看 `target_date=2026-05-29..2026-06-01`：

| strategy_instance | generation | fills | settled | cities | cost | pnl | win_rate |
|---|---|---:|---:|---:|---:|---:|---:|
| v1 25-75 | new_t1_v2 2026-05-26 八城 | 36 | 26 | 7 | 103.39 | -1.64 | 46.2% |
| v1 25-75 | new_t1_v3 2026-05-27 六城 | 32 | 22 | 6 | 79.43 | -11.50 | 59.1% |
| v2 25-75 | new_t1_v2 2026-05-26 八城 | 24 | 18 | 7 | 48.54 | -27.66 | 22.2% |
| v2 25-75 | new_t1_v3 2026-05-27 六城 | 14 | 5 | 5 | 9.46 | -1.48 | 40.0% |

新城市结论：

- v1 下 2026-05-26 新增 8 城从小赚修正为小亏 -1.64，不能扩大。
- v1 下 2026-05-27 v3 新增 6 城仍亏 -11.50，paper 晋升逻辑没有被 live 样本确认。
- v2 下新增 8 城亏 -27.66，是全量 v2 亏损的主因；这部分不该拿来否定 core 9 的 v2，但应该否定“v2 + 新增城市继续扩大”。

新增城市里，v1 当前最差：Manila -13.53、Munich -7.99、Jeddah -5.82、Ankara -4.73、Singapore -3.23。较好的只有 Istanbul +9.66、Chengdu +6.39、BuenosAires +3.96、Amsterdam +2.89、Moscow +2.04、Karachi +1.63。v2 的新增 8 城几乎全线亏：Istanbul -8.23、Ankara -5.85、Karachi -4.79、Guangzhou -3.47、Jeddah -2.66、Seattle -2.07。

这里有一个治理问题：当前 fact 表里仍出现 Beijing / Madrid / Chicago / Austin 等“决策文档里已不在 T1”的 live_real 历史样本，原因是它们发生在城市池更新前或历史 ingest 保留。当前结论不要把这些城市当作仍应 live 的证据。

## side-band 为什么样本这么少

最新同步后，raw live_cycle 统计：

| layer | count |
|---|---:|
| side-band runs | 149 |
| records scanned | 335724 |
| candidate signals | 266 |
| signals | 266 |
| accepted plans | 32 |
| live orders | 31 |
| fact_trades live_real fills | 2 |

主要过滤项：

| filter | skipped |
|---|---:|
| city_pool_not_t1 | 174829 |
| city_not_allowed | 80273 |
| hours_to_settle_above_max | 34534 |
| hours_to_settle_below_min | 32211 |

最新一轮 `20260602T163929Z_mid_price_core_v1_side_band`：`records=1619`，`signals=0`，`plans=0`，`live_orders=0`。它不是 DB 没拉到，而是信号层就没有通过条件。side-band 的配置同时限制：

- allowed cities 只有 core 9；
- YES 只进 `0.20 <= price < 0.45` 且 `edge >= 0.20`；
- NO 只进 `0.35 <= price < 0.65` 且 `edge >= 0.10`；
- `22 <= hours_to_settle <= 28`。

这个组合太窄，导致实验没有统计功效。要让它成为有效实验，至少要放宽一个维度：扩大 allowed cities、放宽 YES edge、或把时间窗加宽；否则继续跑也只是收不到样本。

## 成交质量

全量背景里，v2 的平均 `fill_price - plan_price = -0.0080`，v1 约为 -0.0002。core 9 同期里 v2 的 `fill-plan=-0.0008`，v1 为 `+0.0050`。按买方视角，v2 成交价格仍更好；但 realized PnL 没有转正。

所以 v2 的“下单质量”不是简单失败；更准确是：**报价/成交价质量改善了，但没有证明策略 EV 改善。** 下一步要查 v2 的 `quote_reason / child_order_role / rejected` 与最终结算的关系。

## 残余风险

- market/pm_history 全量同步没有完全成功，rsync 在大目录中 Broken pipe；live runtime 已同步成功。结论对 live fill 分支结构可靠，对最新结算完整性要保留折扣。
- Polymarket activity / trades API 在 clob fill sync 中 Connection reset，未能补新真实 CLOB fill 状态。
- core 9 同期 v1/v2 settled 分别只有 17 / 12，且 missing_bracket 很高；这个 A/B 只能给方向，不是最终长期结论。
- side-band 不是结算少，而是信号/计划/成交链路都太少；它现在没有统计功效。
- `fact_signal_candidates` 的反事实只覆盖 `decision_window_missing=0`，且 `paper_ordered` 不是 live 意图，不能把 paper 覆盖率当 live fill rate。

## 建议

短期 live 动作：

1. `mid_price_core_v1_25_75` 保留为唯一真实下单主路径，但新城市池先降 size 或只保留强城市。
2. `mid_price_core_v2_25_75` 不要用全量/新城市池结论一刀切否定；core 9 同期可以继续 shadow 或极低 size 观察，但新增城市必须关掉或单独 shadow。
3. `mid_price_core_v1_side_band` 需要改条件后再跑：当前条件组合太窄，继续原样跑没有实验价值。

研究动作：

1. 对 v2 做逐笔血缘：按 city / side / price_bucket / quote_reason / child_order_role 看它为什么成交到亏损桶。
2. 对 v1 的 50-65c 桶做过滤实验；这个桶当前 -41.34，是 v1 最直接的可优化点。
3. 新城市池分层：v3 新增 6 城整体不要继续按 paper 预期扩 size；v2 新增 8 城在 v2 分支中先关掉。
