# 0x43cb 是单城市模型还是全球 Tmax 模型：行为架构推断 v1

## 结论

最符合 public fills 的架构不是“单独做某个城市”，也不像“50 个互不相关的独立模型”，而是：

```text
全球共享 Tmax / strip policy
+ city/source/state 输入或校准
+ 同一天统一的策略版本与执行 regime
```

证据对纯 global one-size-fits-all 也不完全支持：城市对 strip 的 shares-weighted center 有明显解释力。因此当前最佳判断是 **shared global backbone + city/source calibration**。public data 看不到私有 forecast、模型代码和未成交订单，不能把行为指纹升级成模型身份确认。

当前动作：把 0x43cb bounded strip 研究并入现有 WCIR/Tmax distribution 主线，采用共享 full-ladder backbone 与薄 city/source adapter；不为每个城市另建模型和执行链，不改 live。

## 研究目标与边界

目标是判断钱包 action policy 是否存在持久的城市指纹，而不是证明它使用某一种机器学习算法。

- grain：selected public wallet `city × target_date` portfolio
- source：2026-08-04 新采集的 0x43cb 全历史快照
- coverage：3,261 portfolios、120 target dates、50 cities
- 行为指标：首买当地时间、strip 宽度、common-base fraction、shares-weighted center、BUY span、BUY transaction 数
- 控制项：calendar month 和 exact target_date
- 跨期稳定性：2026-03/04 与 2026-06/07，同一城市每期至少 10 events

不能观察：私有模型、forecast vintage、未成交/撤单、原始挂单时刻，以及 city effect 究竟来自概率校准、盘口流动性还是执行 policy。

## 不是单城市或少数城市策略

按全部 BUY cost：

| 指标 | 结果 |
|---|---:|
| 城市数 | 50 |
| 最大城市资金占比 | 6.50% |
| 前五城市资金占比 | 25.79% |
| cost HHI | 0.0297 |
| HHI effective city count | 33.70 |

49 个至少有 20 个 cashflow-complete portfolios 的城市中，48 个描述性 turnover ROI 为正；唯一为负的是 Moscow，约 `-0.44%`。这仍是钱包 selected fills，不能当全机会城市 alpha，但足以说明其长期收益不靠一个主城市。

每城策略形态也高度一致：

- city-level YES-strip share 中位 98.04%，最低 89.36%；
- city-level contiguous share 中位 98.57%，最低 93.88%。

因此最稳定的部分是一个跨城市共享的“连续 YES strip”表达政策。

## 月份/日期效应明显强于固定城市效应

用 categorical OLS 做行为方差分解。下表的 `date R²` 是仅用 target_date 解释的比例；`city incremental R²` 是已经控制 exact target_date 后，城市额外解释的比例。

| 行为指标 | target-date R² | city incremental R² | date + city R² |
|---|---:|---:|---:|
| strip width | 41.1% | 5.0% | 46.1% |
| log BUY transactions | 45.6% | 5.2% | 50.8% |
| first BUY local hour | 22.8% | 4.6% | 27.5% |
| BUY span | 24.4% | 4.0% | 28.4% |
| common-base fraction | 14.1% | 3.6% | 17.7% |
| shares-weighted center | 11.8% | 20.0% | 31.8% |

含义：

1. strip 宽度、批次数、入场时间和累积时长主要随全局日期/regime 一起变化；这与 3–4 月宽、早、重交易，6–8 月窄、晚、少批次的整体升级一致。
2. 城市只为上述行为额外解释约 4%～5%，不符合大量独立城市 policy 各自运行的直观形态。
3. weighted center 的城市增量达到 20%，说明它并非完全统一模板。中心重心可能使用 city/source bias、climate shape、settlement lattice 或城市盘口结构。

统计 p 值因样本量较大均很小，架构判断以效应量为主，不以显著性替代机制解释。

## 固定城市指纹没有跨 regime 稳定

35 个城市同时满足早期和后期每期至少 10 events。比较每城指标中位数的跨期 Spearman rank：

| 指标 | rho | p |
|---|---:|---:|
| strip width | +0.169 | 0.331 |
| common-base fraction | +0.095 | 0.589 |
| shares-weighted center | +0.112 | 0.521 |
| first BUY local hour | -0.097 | 0.581 |
| BUY span | -0.128 | 0.466 |
| BUY transactions | +0.014 | 0.935 |

没有一个城市排序跨 regime 稳定。这不支持“每城一套固定参数、长期保持自己的操作风格”。更合理的是共享模型和 policy 随版本统一变化，再根据当日 city weather state、source/basis 和盘口动态产生不同 band/center。

## 对私有模型架构的最佳推断

### 高可信

- 一个跨全球城市复用的完整 ladder/bounded-strip policy；
- target-day 本地时间与 running weather path 标准化；
- 统一的 target-share/basket accumulator；
- 策略版本会整体漂移或升级。

### 中等可信

- city/source calibration、bias table 或 city-family 参数存在；
- weighted center 使用城市相关信息；
- 不同城市可能使用不同 forecast/source profile，但共享最终 optimizer。

### 无法确认

- 是一个 global ML model 加 city feature，还是 global model 加独立 calibration table；
- 是否每城拥有单独 forecast error distribution；
- market price 是模型输入、prior，还是只在执行层使用；
- maker fill availability 对观察到的 city effect 贡献多少。

## 与我们现有基础的直接映射

建议架构：

```text
WCIR shared DecisionContext
→ shared exact-ladder survival / hazard backbone
→ city/source adapter：bias、native lattice、cadence、settlement mapping
→ market-prior residual calibration
→ contiguous strip optimizer
→ common-base TradeIntent
→ shared execution handoff
```

可直接复用：

- `weather_data_feed` 的 observation/forecast/source profile、城市日历和 bracket mapping；
- temperature path、remaining heat、peak clock、cloud/moisture/wind、overshoot 特征；
- `tmax_full_ladder_survival_v2` 的逐档终值分布研究；
- WCIR checkpoint/replay、`ModelOutput`、`SignalCandidate`、`TradeIntent`；
- full-ladder orderbook、market baseline、官方 fee 和 canonical fill/PnL 血缘。

需要新增：

- 跨城 hierarchical exact-ladder calibration；
- city/source random effect 或薄 adapter 的 OOF 对照；
- 连续区间枚举和多腿 effective-cost optimizer；
- common shares 的完整 basket controller 与 legging telemetry。

## 下一轮模型 A/B

在完全相同的 PIT checkpoints、labels 和 executable books 上固定比较：

| Arm | 模型 |
|---|---|
| A | raw market full-ladder baseline |
| B | shared global weather-only full-ladder |
| C | market prior + shared weather residual |
| D | C + city/source hierarchical calibration |
| E | 每城独立模型，仅作高样本城市 diagnostic |

先用 exact outcome logloss、Brier、calibration 和 target-date block CI 判断 C/D 是否打败 A；strip expression 只在概率门通过后比较 `P_band - taker effective cost`。E 若没有稳定改善或跨期退化，就不继续维护独立城市模型。

## 产物

- [可复跑脚本](../../../scripts/analysis/wallet_weather/research_43cb_city_model_architecture_v1.py)
- [统计摘要](generated/wallet_43cb_city_model_architecture_v1/summary.json)
- [城市指纹](generated/wallet_43cb_city_model_architecture_v1/city_fingerprints.csv)

