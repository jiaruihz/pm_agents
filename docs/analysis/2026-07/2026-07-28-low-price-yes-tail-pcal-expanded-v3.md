# Low-price YES Tail p_cal expanded v3

Generated: 2026-07-28T14:37:54+00:00

## 数据快照

| 字段 | 值 |
| --- | --- |
| canonical denominator | no-edge cheap YES, first ticket per city-date |
| train | ≤2026-07-12: 2102 rows / 67 dates / 216 wins |
| retrospective forward | 2026-07-13..2026-07-27: 510 rows / 15 dates / 40 wins |
| fresh frozen | 2026-07-28..2026-08-11: 15 calendar days, not scored |
| settlement missing in scored rows | 0 |
| trade class | research replay / zero-notional |
| fee | Weather official taker curve, rate=0.05 |

> Scope：本报告及 frozen artifact 只定义 `P4_pcal_v3`——在 no-edge cheap-YES
> first city-target_date 宽分母上直接选票，不包含 raw edge、dist 或 22–24h gate。
> 从 2026-07-29 起，`low_price_yes_parallel_frozen_profiles_v1.json` 在同一宽分母并行记录
> broad / edge20 / mechanism / legacy HeadA / p_cal / mechanism+p_cal 六个 profiles。

## 目标与固定改动

只改变训练截止：旧 active p_cal 训练截止 2026-06-20；candidate 扩到
2026-07-12。feature set、C=0.2、city identity exclusion、`max 5 tickets/day`
theta rule 均不变。主指标是同一 retrospective-forward rows 上相对 market 及旧模型的
Brier/logloss；selected ROI 只是 secondary。

## Train（in-sample，仅诊断）

| model | rows | dates | wins | observed_rate | mean_p | brier | market_brier | brier_delta_vs_market | logloss | market_logloss | logloss_delta_vs_market |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_pcal_v2 | 2102 | 67 | 216 | 0.10276 | 0.10245 | 0.08975 | 0.09011 | -0.00036 | 0.31832 | 0.31987 | -0.00155 |
| expanded_pcal_v3 | 2102 | 67 | 216 | 0.10276 | 0.10585 | 0.08963 | 0.09011 | -0.00047 | 0.31663 | 0.31987 | -0.00324 |

| model | theta | opportunity_rows | selected_rows | selected_dates | wins | win_rate | pnl | fee_adjusted_roi | roi_ci_low | roi_ci_high | top5_removed_roi | top10_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_pcal_v2 | 0.01881 | 2102 | 346 | 66 | 54 | 0.15607 | 13.86668 | 0.34552 | 0.01320 | 0.69394 | 0.23045 | 0.11688 |
| expanded_pcal_v3 | 0.02412 | 2102 | 336 | 66 | 52 | 0.15476 | 5.79734 | 0.12548 | -0.11450 | 0.37401 | 0.02554 | -0.07441 |

## Retrospective forward（2026-07-13..27）

| model | rows | dates | wins | observed_rate | mean_p | brier | market_brier | brier_delta_vs_market | brier_delta_ci_low | brier_delta_ci_high | logloss | market_logloss | logloss_delta_vs_market | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_pcal_v2 | 510 | 15 | 40 | 0.07843 | 0.12751 | 0.07271 | 0.07232 | 0.00039 | -0.00168 | 0.00210 | 0.27554 | 0.27368 | 0.00187 | -0.00676 | 0.00843 |
| expanded_pcal_v3 | 510 | 15 | 40 | 0.07843 | 0.12882 | 0.07345 | 0.07232 | 0.00113 | -0.00085 | 0.00291 | 0.27863 | 0.27368 | 0.00495 | -0.00175 | 0.01110 |

Expanded 相对旧 p_cal，负数才是改善：

```json
{
  "rows": 510,
  "dates": 15,
  "brier_delta_expanded_vs_old": 0.0007352805892296332,
  "brier_delta_expanded_vs_old_ci_low": -0.0005789915578879563,
  "brier_delta_expanded_vs_old_ci_high": 0.0022396536785036143,
  "logloss_delta_expanded_vs_old": 0.0030838976425798296,
  "logloss_delta_expanded_vs_old_ci_low": -0.0024566366670919194,
  "logloss_delta_expanded_vs_old_ci_high": 0.010584236862518808
}
```

Fee-adjusted selected expression：

| model | theta | opportunity_rows | opportunity_dates | selected_rows | selected_dates | wins | win_rate | cost | pnl | fee_adjusted_roi | roi_ci_low | roi_ci_high | top5_removed_roi | top10_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_pcal_v2 | 0.01881 | 510 | 15 | 223 | 15 | 21 | 0.09417 | 27.10817 | -6.10817 | -0.22533 | -0.49311 | 0.04641 | -0.40015 | -0.57793 |
| expanded_pcal_v3 | 0.02412 | 510 | 15 | 193 | 15 | 21 | 0.10881 | 27.20766 | -6.20766 | -0.22816 | -0.49960 | 0.06161 | -0.39971 | -0.57636 |

Expanded−old selected ROI：

```json
{
  "dates": 15,
  "roi_delta_expanded_vs_old": -0.0028326370974094683,
  "roi_delta_ci_low": -0.26878501702186963,
  "roi_delta_ci_high": 0.2809534808095025
}
```

## 为什么样本更多却没改善

训练量从旧版的 1,351 rows / 46 dates / 136 wins 增至
2,102 rows / 67 dates / 216 wins；
不是“新样本太少到完全没变化”。问题是新增窗口学到的 source/city regime 没延续到后 15 日。
这里的 `forecast_model` 是每城 assigned source，不能解释为模型间随机实验。

| period | forecast_model | rows | dates | wins | observed_rate | mean_ask |
| --- | --- | --- | --- | --- | --- | --- |
| old_train | all | 1351 | 46 | 136 | 0.10067 | 0.10380 |
| old_train | ecmwf | 837 | 46 | 95 | 0.11350 | 0.10497 |
| old_train | gfs | 514 | 45 | 41 | 0.07977 | 0.10191 |
| added_train | all | 751 | 21 | 80 | 0.10652 | 0.10314 |
| added_train | ecmwf | 366 | 19 | 30 | 0.08197 | 0.10422 |
| added_train | gfs | 385 | 21 | 50 | 0.12987 | 0.10211 |
| retrospective_forward | all | 510 | 15 | 40 | 0.07843 | 0.11060 |
| retrospective_forward | ecmwf | 308 | 15 | 27 | 0.08766 | 0.11182 |
| retrospective_forward | gfs | 202 | 15 | 13 | 0.06436 | 0.10873 |

在 7/13..27 同分母上：

| forecast_model | rows | wins | observed_rate | mean_ask | mean_p_old | mean_p_expanded |
| --- | --- | --- | --- | --- | --- | --- |
| ecmwf | 308 | 27 | 0.08766 | 0.11182 | 0.14143 | 0.13096 |
| gfs | 202 | 13 | 0.06436 | 0.10873 | 0.10628 | 0.12556 |

新增训练窗里 GFS 城组的命中率高于 ECMWF 城组，expanded 因而在后窗提高 GFS 概率、
降低 ECMWF 概率；但后窗 GFS 实际命中率降到约 6.4%，这个短期关系反转了。
selector 也不是只做了微调：共同选择 130 条，旧版独有
93 条，expanded 独有 63 条，
两者最终都中 21 条。当前证据更像 source/city × seasonal regime 不稳定，而不是单纯
训练样本不足；后续应让 source/city effects 做 shrinkage，并用多源 spread/bias 连续特征，
而不是继续等权追加日期。

## Fresh frozen

`2026-07-28..2026-08-11` 是连续 15 个日历 target dates。
本脚本不读取/发布该窗口的 settlement score 或 ROI。零信号日、missing-book 日保留在
signal/evidence coverage，不顺延窗口。8/11 所需结算全部到齐后一次性开封。

## 双漏斗与裁决边界

Signal funnel：canonical cheap-YES → first city-date ticket → p_cal edge selector。
Evidence funnel：PIT candidate → ask → settlement → fee-adjusted hypothetical taker；
actual fill=0。

expanded 的 retrospective proper score 点估劣于旧模型和 market，因此不具备替换 active
p_cal 的依据。固定 artifact 仍保留到 fresh frozen 做一次前瞻诊断，用来区分短窗口噪声与
稳定退化；无论结果如何，开封前均不改模型、不选阈值、不部署，`live_action=none`。

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_low_price_yes_tail_pcal_expanded_v3.py`
- frozen artifact：`configs/weather/low_price_yes_tail_pcal_expanded_v3_research.json`
- generated mirror：`generated/low_price_yes_tail_pcal_expanded_v3/expanded_pcal_v3_artifact.json`
- scorecards：`probability_scorecard.csv`, `trade_scorecard.csv`
