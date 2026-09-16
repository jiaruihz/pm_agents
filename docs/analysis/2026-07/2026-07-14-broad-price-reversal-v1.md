# Broad Price Direction v1

> 2026-07-14; research-only; broad denominator; zero notional.

## 结论

简单的盘口反转或动量都不是可用 alpha。这里没有按 city/source/天气状态/ask 价格筛选：只要求同一 exact bracket、连续小时的 YES midpoint 至少移动 2c，然后在新 ask 买入并持有到结算。

底表覆盖 `2026-05-19..2026-07-08`，50 个 target dates、36 城、7018 条同 bracket 连续状态。

| rule | scope | rows | dates | cities | ROI | date-block 95% CI | depth |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| reversal_buy_no_after_yes_rise | train | 2354 | 33 | 36 | -19.6% | [-28.0%, -11.0%] | ask_size>=5 |
| reversal_buy_no_after_yes_rise | forward | 689 | 16 | 36 | -5.6% | [-19.4%, +6.6%] | ask_size>=5 |
| reversal_buy_no_after_yes_rise | all | 3043 | 49 | 36 | -16.1% | [-23.4%, -8.8%] | ask_size>=5 |
| reversal_buy_yes_after_yes_fall | train | 509 | 32 | 36 | -10.5% | [-19.0%, -2.4%] | YES depth unavailable; diagnostic |
| reversal_buy_yes_after_yes_fall | forward | 140 | 16 | 36 | -6.1% | [-26.7%, +13.6%] | YES depth unavailable; diagnostic |
| reversal_buy_yes_after_yes_fall | all | 649 | 48 | 36 | -9.6% | [-17.5%, -1.6%] | YES depth unavailable; diagnostic |
| momentum_buy_yes_after_yes_rise | train | 2419 | 33 | 36 | -2.0% | [-4.3%, +0.1%] | YES depth unavailable; diagnostic |
| momentum_buy_yes_after_yes_rise | forward | 714 | 16 | 36 | -6.0% | [-9.8%, -1.8%] | YES depth unavailable; diagnostic |
| momentum_buy_yes_after_yes_rise | all | 3133 | 49 | 36 | -2.9% | [-5.0%, -1.0%] | YES depth unavailable; diagnostic |
| momentum_buy_no_after_yes_fall | train | 493 | 32 | 36 | -8.4% | [-14.1%, -2.9%] | ask_size>=5 |
| momentum_buy_no_after_yes_fall | forward | 139 | 16 | 36 | -8.9% | [-22.8%, +2.8%] | ask_size>=5 |
| momentum_buy_no_after_yes_fall | all | 632 | 48 | 36 | -8.5% | [-14.0%, -3.5%] | ask_size>=5 |

## 判定

- 两条 NO 表达都有 ask_size>=5 的执行深度；上涨后买 NO 与下跌后买 NO 在 train/forward/full 都没有稳定正收益。
- 两条 YES 表达也都为负；atlas 没有持久化 YES ask depth，所以它们是价格方向诊断，不是可发布的 executable 证据。
- 失败发生在宽分母，不是窄 source/city gate 造成；价格变化包含信息，但同向/反向 taker 都要跨 spread 并付 fee。
- conclusion=`inconclusive_no_directional_alpha`; 不创建 momentum 或 reversal live runner。

Artifact: `docs/analysis/2026-07/generated/broad_price_reversal_v1/summary.json`.
