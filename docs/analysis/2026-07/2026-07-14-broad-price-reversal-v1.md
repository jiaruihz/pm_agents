# Broad Price Reversal v1

> 2026-07-14; research-only; broad denominator; zero notional.

## 结论

简单的盘口反转不是可用 alpha。这里没有按 city/source/天气状态/ask 价格筛选：只要求同一 exact bracket、连续小时的 YES midpoint 至少移动 2c，然后在新 ask 反向买入并持有到结算。

底表覆盖 `2026-05-19..2026-07-08`，50 个 target dates、36 城、7018 条同 bracket 连续状态。

| rule | scope | rows | dates | cities | ROI | date-block 95% CI | depth |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| reversal_buy_no_after_yes_rise | train | 2354 | 33 | 36 | -19.6% | [-28.0%, -11.0%] | ask_size>=5 |
| reversal_buy_no_after_yes_rise | forward | 689 | 16 | 36 | -5.6% | [-19.4%, +6.6%] | ask_size>=5 |
| reversal_buy_no_after_yes_rise | all | 3043 | 49 | 36 | -16.1% | [-23.4%, -8.8%] | ask_size>=5 |
| reversal_buy_yes_after_yes_fall | train | 509 | 32 | 36 | -10.5% | [-19.0%, -2.4%] | YES depth unavailable; diagnostic |
| reversal_buy_yes_after_yes_fall | forward | 140 | 16 | 36 | -6.1% | [-26.7%, +13.6%] | YES depth unavailable; diagnostic |
| reversal_buy_yes_after_yes_fall | all | 649 | 48 | 36 | -9.6% | [-17.5%, -1.6%] | YES depth unavailable; diagnostic |

## 判定

- BUY NO after a YES rise is the executable primary test and is negative in train, forward, and the full sample.
- BUY YES after a YES fall is also negative; because the atlas did not persist YES ask depth, it is diagnostic rather than publishable executable evidence.
- The failure is broad rather than caused by a narrow source/city gate: observed repricing is mostly informative, while spread and taker fee consume the remaining margin.
- conclusion=`rejected_as_main_strategy`; do not create a reversal live runner from this result.

Artifact: `docs/analysis/2026-07/generated/broad_price_reversal_v1/summary.json`.
