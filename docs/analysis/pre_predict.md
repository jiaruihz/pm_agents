# Pre-Predict Branch

Status: current-reference
Created: 2026-06-16

`pre_predict` 是赛前/早盘预测分支。它回答的是：

```text
在还没有充分看到当天真实升温路径时，最终最高温会落在哪一档？
```

它和 `reheat_risk` 是并列分支，不是上下级关系。`pre_predict` 给全天最高温分布 prior；`reheat_risk` 在看到当天 observed path 以后更新剩余升温风险。

## Scope

包括：

- forecast max + historical error distribution。
- GFS/ECMWF/Open-Meteo forecast quality。
- city/model/source reliability。
- model-vs-market calibration。
- forecast-bounded range / adjacent / basket 表达。
- 低价 YES 的赛前 prior。

不包括：

- 已经出现 running max 后的 no-reheat 判断。
- current YES 是否守住。
- higher bracket NO carry。
- 日内 reheat reversal 条件模型。

这些属于 [reheat_risk.md](reheat_risk.md)。

## Shared Inputs

`pre_predict` 可以读共享事实层里的 settlement truth、city/source registry、orderbook snapshot 和 forecast fields，但不能依赖 decision-time 后才可见的 METAR path 特征来解释赛前 edge。

## Expected Outputs

```text
model_p_yes_raw
model_p_yes_blended
forecast_quality_label
city_model_reliability
forecast_bounded_range_candidates
```

## Research Directions

1. Forecast-quality calibration: 哪些 city/model/source 组合的 raw probability 能打过市场。
2. Forecast-bounded Range RV: forecast 分布已经收窄时，区间/相邻表达是否有可成交 edge。
3. Low-price YES prior: 低价 YES 的 prior 是否真的来自 forecast tail，而不是模型误差过宽。
4. Source-aware model quality: 官方结算源和默认天气源不一致时，forecast 是否要按官方站点重算。
