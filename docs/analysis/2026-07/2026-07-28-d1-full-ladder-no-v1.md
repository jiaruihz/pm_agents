# D1 多模型全档位 NO：冻结 forward v1

## 结论

先验主规格是最外两档（两侧共四档）按冻结多模型分布的 `P(NO) - ask - fee` 选择一档；同时按用户要求做固定档距探索。最有价值的探索规格是距两端第 3 档（distance=2），仍只按事前 edge 在左右两档中选择，不使用该 city-day 的结算结果择档。

| expression | baskets | target_dates | mean_no_ask | pnl | fee_adjusted_roi | roi_ci_low | roi_ci_high | roi_delta_vs_endpoint | inner_rung_share |
|---|---|---|---|---|---|---|---|---|---|
| endpoint_model_max_edge | 1222 | 20 | 0.986215 | -6.683676 | -0.005543 | -0.012151 | 0.000538 | 0.000000 | 0.000000 |
| outer_two_model_max_edge | 1222 | 20 | 0.961709 | -2.865791 | -0.002435 | -0.010038 | 0.004685 | 0.003108 | 0.518822 |
| second_rung_model_max_edge | 1222 | 20 | 0.964813 | -0.663973 | -0.000562 | -0.008950 | 0.007892 | 0.004981 | 1.000000 |
| third_rung_model_max_edge | 1222 | 20 | 0.902801 | 16.488227 | 0.014888 | -0.004600 | 0.034494 | 0.020431 | 1.000000 |
| inner_second_or_third_max_edge | 1222 | 20 | 0.905948 | 6.818996 | 0.006137 | -0.008881 | 0.019708 | 0.011680 | 1.000000 |
| full_ladder_model_max_edge | 1222 | 20 | 0.638529 | -56.825710 | -0.071585 | -0.127762 | -0.017481 | -0.066041 | 0.981997 |
| mechanical_half_each_endpoint | 1222 | 20 | 0.985923 | -7.894401 | -0.006549 | -0.011684 | -0.001863 | -0.001006 | 0.000000 |

## 冻结口径

- calibration 截止：`2026-06-16`；残差只来自此前日期。
- forecast run：`floor_6h(decision_ts - 12h)`，是保守可用性重建。
- evidence：1224 model-eligible baskets；1222 个具备 distance=2 表达的固定比较分母 / 20 target dates / 43 cities。
- 模型：ECMWF IFS、ECMWF AIFS、GFS Global、ICON、JMA；每篮至少 3 个。
- 成本：NO ask + `0.05 * price * (1-price)` 官方 fee。

## Signal / evidence funnel

- forecast archive：1774 city-day versions / 28 dates / 8870 model rows（5 模型全覆盖）。
- full-ladder raw：1338 baskets / 20 dates / 13542 rungs。
- executable + settled：13404 / 13542 rungs。
- calibration eligible：1224 baskets / 43 cities；HongKong、Moscow、Seoul、Shenzhen 的冻结训练残差不足 20 条。
- fixed expression denominator：1222 baskets（另 2 个 ladder 没有 distance=2 档）。

## 时间稳定性

- distance=2 early（<= 2026-06-26）：685 baskets，ROI 2.4035%。
- distance=2 late（> 2026-06-26）：537 baskets，ROI 0.3659%。

## 解释边界

- 这是 research replay，不是 live 授权。
- distance=2 是在本次 holdout 看过多个固定档距后发现的 exploratory 结果，必须在新日期 frozen forward 复验，不能把当前 CI 直接当确认。
- Single Runs 保存模型初始化；历史 public first-seen 不可恢复，因此使用 12 小时保守 lag，不把它写成精确 first-seen。
- JRS 原始盘当前 Python 权限失效，本次全档位证据使用 7 月 6 日迁盘前本地不可变镜像；forecast backfill 本身覆盖完整冻结窗口。
