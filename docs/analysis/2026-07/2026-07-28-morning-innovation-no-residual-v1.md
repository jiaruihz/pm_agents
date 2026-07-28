# Morning innovation NO residual v1

## 结论与动作

本策略把 forecast innovation 落成同刻 market-anchored exact-bracket NO：每个 city-day/checkpoint 只买 fee-adjusted EV 最大的一档 direct NO。它是 retrospective expanding OOF，不是 actual fills；三门与动作见末尾。

## Frozen contract

- checkpoints：每个城市当地 `09:00` / `12:00`，IANA timezone 已由上游 checkpoint artifact 验证。
- probability：同刻 NO midpoint 为 market prior；baseline 加 forecast bracket distance；candidate 再加 `innovation × signed bracket distance`、innovation² 与 open-tail interaction。
- train：严格 prior target dates，至少 5 天；`LogisticRegression C=0.1`，不含 city/region selector。
- execution：direct NO ask、ask size≥5、官方 Weather fee `0.05*p*(1-p)`、edge≥0.02、每 state 一档、5 shares、hold to settlement。

## Signal funnel

- settled checkpoint states：853 states / 13 dates。
- direct two-sided NO expression rows：1,287 expressions / 271 states。
- expanding OOF：973 expressions / 186 states / 8 dates。

## Evidence funnel

- direct NO bid+ask：1,287 expression rows。
- top ask depth≥5：1,256 rows。
- candidate selected replay：147 research trades；actual fills=0。

## Probability quality（state/date equal）

| checkpoint_hour_local | variant | expressions | states | dates | date_equal_logloss | date_equal_brier | logloss_delta_vs_distance | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | market_raw | 586 | 99 | 8 | 0.2277 | 0.0727 | -0.0574 | -0.0660 | -0.0453 |
| 9 | market_calibrated | 586 | 99 | 8 | 0.2845 | 0.0861 | 0.0024 | -0.0001 | 0.0046 |
| 9 | market_plus_distance | 586 | 99 | 8 | 0.2825 | 0.0858 | 0.0000 | 0.0000 | 0.0000 |
| 9 | market_plus_innovation | 586 | 99 | 8 | 0.2820 | 0.0857 | -0.0007 | -0.0016 | 0.0007 |
| 12 | market_raw | 387 | 87 | 8 | 0.3281 | 0.1076 | -0.0653 | -0.0955 | -0.0368 |
| 12 | market_calibrated | 387 | 87 | 8 | 0.3980 | 0.1265 | 0.0028 | -0.0042 | 0.0060 |
| 12 | market_plus_distance | 387 | 87 | 8 | 0.3966 | 0.1262 | 0.0000 | 0.0000 | 0.0000 |
| 12 | market_plus_innovation | 387 | 87 | 8 | 0.3981 | 0.1271 | 0.0022 | -0.0006 | 0.0049 |

主检验是 candidate 相对完全同 rows 的 `market_plus_distance`；delta<0 才表示 improvement。

## Fee-adjusted executable replay

| checkpoint_hour_local | variant | opportunity_states | trades | dates | wins | mean_ask | capital | fee | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | market_raw | 99 | 0 | 0 | 0 | NA | 0.0000 | 0.0000 | 0.0000 | NA | NA | NA |
| 9 | market_calibrated | 99 | 77 | 8 | 35 | 0.5356 | 206.2200 | 4.5672 | -35.7872 | -0.1735 | -0.2636 | -0.0130 |
| 9 | market_plus_distance | 99 | 77 | 8 | 34 | 0.5362 | 206.4200 | 4.5626 | -40.9826 | -0.1985 | -0.3197 | -0.0140 |
| 9 | market_plus_innovation | 99 | 77 | 8 | 34 | 0.5359 | 206.3200 | 4.5653 | -40.8853 | -0.1982 | -0.3193 | -0.0134 |
| 12 | market_raw | 87 | 0 | 0 | 0 | NA | 0.0000 | 0.0000 | 0.0000 | NA | NA | NA |
| 12 | market_calibrated | 87 | 71 | 8 | 34 | 0.4855 | 172.3650 | 4.0760 | -6.4410 | -0.0374 | -0.1531 | 0.0777 |
| 12 | market_plus_distance | 87 | 71 | 8 | 36 | 0.4869 | 172.8400 | 4.0649 | 3.0951 | 0.0179 | -0.1489 | 0.1794 |
| 12 | market_plus_innovation | 87 | 70 | 8 | 34 | 0.4838 | 169.3150 | 4.0165 | -3.3315 | -0.0197 | -0.1577 | 0.1394 |

## Candidate vs no-innovation paired daily PnL

| checkpoint_hour_local | dates | candidate_pnl | baseline_pnl | pnl_delta | date_mean_pnl_delta | date_mean_delta_ci_low | date_mean_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | 8 | -40.8853 | -40.9826 | 0.0973 | 0.0122 | 0.0000 | 0.0365 |
| 12 | 8 | -3.3315 | 3.0951 | -6.4266 | -0.8033 | -2.4100 | 0.0000 |

## Three gates

- significance/probability：FAIL。
- fee-adjusted execution：FAIL。
- fresh frozen forward：NA。

```text
status=inconclusive
live_action=none
actual_fill_class=research_replay
```

无论历史点估如何，本轮不按 region/city/price 继续切片造 gate；只有 probability 与 execution 同时过门，才允许进入完整分母 zero-notional forward。
