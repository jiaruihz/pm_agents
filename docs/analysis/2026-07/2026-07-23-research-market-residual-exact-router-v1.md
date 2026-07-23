# Market residual exact router v1（2026-07-23）

## 结论

本轮已经形成可复跑的三动作候选：`buy_current_yes / buy_d1_yes / no_trade`。
它不是 hard regime filter；market 是 prior，forecast bust、剩余峰值窗口、
post-high confirmation/censoring 和路径趋势只修正 residual。

**Verdict: rejected_as_strategy。**完整 ladder 同分母上的所有 residual
模型都显著差于 market；冻结 first-signal 上还出现了错误的低价 current
YES 路由，`market_plus_compact` historical forward 9/9 全亏。该版本只保留
为 selection-shift negative control，不进入 shadow。

当前最好的 paired proper-score 结果是 `market_plus_compact_city` 对
`market_two_rung_raw`：date-equal logloss delta
`+0.0005`，95% date bootstrap CI
`[-0.0081, +0.0085]`。
delta < 0 才代表优于 market。

## 固定口径

- 训练：历史机制 rows，按 target date expanding OOF；C=0.03，不使用 frozen/live 调参。
- 严格分母：完整 market ladder。
- 扩覆盖分母：current+d1 两档中价构造三态 prior；coverage gap 单列，不算策略过滤。
- 策略评估：冻结 218 个 `d1 YES mid >= 0.80` first signals。
- forward：`2026-06-21` 起历史 forward；clean live 只评分，不参与拟合。
- fee：`0.05*p*(1-p)`，动作必须有可执行 ask；edge buffer 预先列出，不按 forward 挑阈值。

## 覆盖

```json
{
  "atlas_rows": 14369,
  "factory_rows": 99819,
  "market_states": 6540,
  "market_score_ready": 6535,
  "three_state_labels": 13545,
  "mechanism_available": 11554,
  "model_eligible_after_pollution_exclusion": 11132,
  "market_eval_eligible": 6333,
  "excluded_pollution_mechanism_rows": 422,
  "forecast_peak_delta_inconsistent_mechanism_rows": 3684,
  "fixed_city_model_forecast_mechanism_rows": 11554,
  "source_forecast_file_hashes": {
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260519_20260620/reheat_feature_rows.csv": "b6b1f0ad1c0dca0a592892b3d708181fdafdd0e65a335a04f7c2237d6763a32d",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260621_20260623/reheat_feature_rows.csv": "3145300369cc02ab7d48e3657afc9f1502b4e3d14441dd270e17ca7c4a4ac4c3",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260625_20260628/reheat_feature_rows.csv": "2380ff56c79fa5a8e12ff0bdf368fa03663b9c055d37a81e6aeeb5c51c7482c7",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260629_20260630/reheat_feature_rows.csv": "eb09c1571305947564b5d4083dcc445b40aad011869e99aa9f4c64b64ec5fdb3",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260701/reheat_feature_rows.csv": "4542d70568edec1a4608539e1c68b5e658b101a011fd531123bc1d6770805f1d",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260702/reheat_feature_rows.csv": "531090f85c2af52e1d5250f55f179f6f60720969e2d61d9baa16ab2209b484c5",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260703_20260704/reheat_feature_rows.csv": "c33f7d5ff0b8eddc68298e092912f75891003e232725ab787344bdb858cf853e",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260704/reheat_feature_rows.csv": "fd33d08e24b047ed0e8aad653300ef6dc6116d5627a12859e3e14cf89db272f6",
    "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260705_20260708/reheat_feature_rows.csv": "63235cfb80387bae32b37b4afff0482acef3c9a0860792674a72f8ab08a4e4b9"
  },
  "source_forecast_state_groups": 19176,
  "source_forecast_conflict_groups": 0,
  "celsius_d2_threshold_half_step_rows": 7676,
  "label_counts_all": {
    "stall_current": 7942,
    "overshoot_d2plus": 3416,
    "exact_d1": 2187,
    "other": 824
  },
  "two_rung_model_rows": 9968,
  "two_rung_model_dates": 45,
  "oof_rows_by_model": {
    "market_calibrated": 7292,
    "market_full_ladder_raw": 3716,
    "market_plus_compact": 7292,
    "market_plus_compact_city": 7292,
    "market_two_rung_raw": 7292
  },
  "frozen_rows": 218,
  "frozen_state_matches": 216,
  "frozen_oof_rows_by_model": {
    "market_calibrated": 76,
    "market_full_ladder_raw": 18,
    "market_plus_compact": 76,
    "market_plus_compact_city": 76,
    "market_two_rung_raw": 76
  },
  "clean_live_rows": 9
}
```

## Paired proper score

| baseline | model | rows | dates | baseline_logloss | model_logloss | logloss_delta | logloss_delta_ci_low | logloss_delta_ci_high | baseline_brier | model_brier | brier_delta | brier_delta_ci_low | brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_two_rung_raw | market_calibrated | 7292 | 33 | 0.4120 | 0.4191 | 0.0071 | 0.0025 | 0.0119 | 0.2377 | 0.2415 | 0.0039 | 0.0015 | 0.0063 |
| market_two_rung_raw | market_plus_compact | 7292 | 33 | 0.4120 | 0.4151 | 0.0031 | -0.0030 | 0.0094 | 0.2377 | 0.2414 | 0.0037 | -0.0004 | 0.0079 |
| market_two_rung_raw | market_plus_compact_city | 7292 | 33 | 0.4120 | 0.4125 | 0.0005 | -0.0081 | 0.0085 | 0.2377 | 0.2395 | 0.0018 | -0.0041 | 0.0072 |
| market_full_ladder_raw | market_calibrated | 3716 | 18 | 0.4749 | 0.4882 | 0.0133 | 0.0052 | 0.0213 | 0.2818 | 0.2871 | 0.0053 | 0.0017 | 0.0086 |
| market_full_ladder_raw | market_plus_compact | 3716 | 18 | 0.4749 | 0.4906 | 0.0157 | 0.0058 | 0.0260 | 0.2818 | 0.2885 | 0.0067 | 0.0014 | 0.0118 |
| market_full_ladder_raw | market_plus_compact_city | 3716 | 18 | 0.4749 | 0.4907 | 0.0159 | 0.0055 | 0.0263 | 0.2818 | 0.2892 | 0.0074 | 0.0017 | 0.0128 |

## 冻结 first-signal router（edge buffer=0.02）

| model | edge_buffer | period | rows | trades | current_trades | d1_trades | wins | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_calibrated | 0.0200 | pre_forward | 45 | 15 | 15 | 0 | 1 | 1.1724 | -0.1724 | -0.1471 |
| market_calibrated | 0.0200 | historical_forward | 31 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| market_full_ladder_raw | 0.0200 | pre_forward | 18 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| market_full_ladder_raw | 0.0200 | historical_forward | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| market_plus_compact | 0.0200 | pre_forward | 45 | 29 | 29 | 0 | 1 | 1.1439 | -0.1439 | -0.1258 |
| market_plus_compact | 0.0200 | historical_forward | 31 | 9 | 9 | 0 | 0 | 1.3619 | -1.3619 | -1.0000 |
| market_plus_compact_city | 0.0200 | pre_forward | 45 | 28 | 28 | 0 | 1 | 1.1072 | -0.1072 | -0.0968 |
| market_plus_compact_city | 0.0200 | historical_forward | 31 | 9 | 9 | 0 | 0 | 1.8509 | -1.8509 | -1.0000 |
| market_two_rung_raw | 0.0200 | pre_forward | 45 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| market_two_rung_raw | 0.0200 | historical_forward | 31 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |

## Clean live probability score

| model | rows | logloss | brier |
| --- | --- | --- | --- |
| market_calibrated | 9 | 0.9066 | 0.5568 |
| market_full_ladder_raw | 9 | 1.0821 | 0.6716 |
| market_plus_compact | 9 | 0.8809 | 0.5238 |
| market_plus_compact_city | 9 | 0.8758 | 0.4964 |

## 策略定义与可行性

1. 先从盘口得到 `P(current), P(d1), P(d2+)`。
2. compact residual 更新三态概率；forecast 已被打穿且仍升温是 overshoot hazard，
   真正 post-high 的观测确认是 stall/exact 的证据，只有 obs-age 相等则标为 censored。
3. 分别计算 `P(current)-current ask-fee` 与 `P(d1)-d1 ask-fee`；
   最大值超过 edge buffer 才买，否则不交易。
4. city/source 只允许强收缩校准；若 paired score 没有稳定优于 market，就不把它放进候选。

本版本没有资格进入 zero-notional shadow，不改变 live 配置。
