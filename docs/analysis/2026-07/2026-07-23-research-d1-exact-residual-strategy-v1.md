# d1 exact residual strategy v1（2026-07-23）

## 结论

候选策略已收敛为：在每个 city-day 首次 `d1 YES mid >= 0.80` 时，
先用只含 market 的 expanding calibration 估计 exact probability，再计算
fee-adjusted edge。候选研究规则还加入两类 mechanism veto：
forecast ceiling 已到 d2，或 forecast 已被打穿至少 1 native unit 且仍升温；
恰好卡在 0.80 trigger 边界也不成交。否则 edge 超过 0.02 才买 d1 YES。

最佳 OOF proper-score 模型 `d1_market_calibrated` 相对 d1 market 的 date-equal
binary logloss delta 为 `-0.0234`，95% date bootstrap CI
`[-0.0448, -0.0008]`。
delta < 0 才是优于 market。

## 分母与防泄漏

- signal funnel：冻结 218 first signals → 216 行可回连 corrected state →
  剔除 11 行 7/02-05 known forecast pollution → 205 行模型分母。
- evidence funnel：direct d1 bid/ask 与 settlement 可用；缺完整 ladder 不会被写成策略筛除。
- 历史：date-expanding OOF，最少 12 个训练日期，C=0.03。
- forward：2026-06-21 起单列；clean live 只在历史冻结拟合后评分。
- fee-adjusted cost：direct ask + `0.05*p*(1-p)`。

## 审计

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
  "model_first_signal_rows": 205,
  "model_first_signal_dates": 44,
  "model_labels": {
    "exact_d1": 193,
    "overshoot_d2plus": 11,
    "stall_current": 1
  },
  "frozen_rows": 218,
  "frozen_corrected_state_matches": 216,
  "frozen_model_rows_after_pollution_exclusion": 205,
  "frozen_missing_corrected_state_rows": 2,
  "frozen_label_mismatches": 0,
  "oof_rows_by_model": {
    "d1_market_calibrated": 139,
    "d1_market_plus_compact": 139,
    "d1_market_plus_compact_city": 139,
    "d1_market_raw": 139
  },
  "oof_dates": 32,
  "historical_forward_oof_rows": 43,
  "clean_live_rows": 9
}
```

## Proper score

| model | rows | dates | market_logloss | model_logloss | logloss_delta | logloss_delta_ci_low | logloss_delta_ci_high | market_brier | model_brier | brier_delta | brier_delta_ci_low | brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | 139 | 32 | 0.2129 | 0.1895 | -0.0234 | -0.0448 | -0.0008 | 0.0487 | 0.0435 | -0.0053 | -0.0097 | -0.0004 |
| d1_market_plus_compact | 139 | 32 | 0.2129 | 0.1927 | -0.0202 | -0.0472 | 0.0081 | 0.0487 | 0.0443 | -0.0044 | -0.0094 | 0.0010 |
| d1_market_plus_compact_city | 139 | 32 | 0.2129 | 0.1917 | -0.0212 | -0.0478 | 0.0089 | 0.0487 | 0.0442 | -0.0045 | -0.0096 | 0.0010 |

## 策略结果（edge buffer=0.02）

| model | policy | edge_buffer | period | rows | vetoed_rows | trades | wins | overshoots | stalls | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | edge_only | 0.0200 | historical_forward | 43 | 9 | 21 | 21 | 0 | 0 | 18.1945 | 2.8055 | 0.1542 | 0.1450 | 0.1676 |
| d1_market_calibrated | edge_only | 0.0200 | pre_forward | 96 | 10 | 38 | 34 | 3 | 1 | 32.7360 | 1.2640 | 0.0386 | -0.0721 | 0.1326 |
| d1_market_raw | edge_only | 0.0200 | historical_forward | 43 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| d1_market_raw | edge_only | 0.0200 | pre_forward | 96 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| d1_market_calibrated | edge_plus_mechanism_veto_discovery | 0.0200 | historical_forward | 43 | 9 | 12 | 12 | 0 | 0 | 10.4016 | 1.5984 | 0.1537 | 0.1443 | 0.1677 |
| d1_market_calibrated | edge_plus_mechanism_veto_discovery | 0.0200 | pre_forward | 96 | 10 | 28 | 26 | 1 | 1 | 24.0288 | 1.9712 | 0.0820 | -0.0374 | 0.1680 |
| d1_market_raw | edge_plus_mechanism_veto_discovery | 0.0200 | historical_forward | 43 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| d1_market_raw | edge_plus_mechanism_veto_discovery | 0.0200 | pre_forward | 96 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |

## Mechanism veto sensitivity

| bust_threshold_native | market_floor | period | trades | wins | losses | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5000 | 0.8000 | pre_forward | 26 | 24 | 2 | 1.7610 | 0.0792 |
| 0.5000 | 0.8000 | historical_forward | 10 | 10 | 0 | 1.3496 | 0.1560 |
| 0.5000 | 0.8050 | pre_forward | 21 | 20 | 1 | 1.8592 | 0.1025 |
| 0.5000 | 0.8050 | historical_forward | 9 | 9 | 0 | 1.2253 | 0.1576 |
| 0.5000 | 0.8100 | pre_forward | 21 | 20 | 1 | 1.8592 | 0.1025 |
| 0.5000 | 0.8100 | historical_forward | 8 | 8 | 0 | 1.0623 | 0.1531 |
| 1.0000 | 0.8000 | pre_forward | 28 | 26 | 2 | 1.9712 | 0.0820 |
| 1.0000 | 0.8000 | historical_forward | 12 | 12 | 0 | 1.5984 | 0.1537 |
| 1.0000 | 0.8050 | pre_forward | 23 | 22 | 1 | 2.0694 | 0.1038 |
| 1.0000 | 0.8050 | historical_forward | 11 | 11 | 0 | 1.4740 | 0.1547 |
| 1.0000 | 0.8100 | pre_forward | 23 | 22 | 1 | 2.0694 | 0.1038 |
| 1.0000 | 0.8100 | historical_forward | 10 | 10 | 0 | 1.3111 | 0.1509 |
| 1.5000 | 0.8000 | pre_forward | 29 | 26 | 3 | 1.1052 | 0.0444 |
| 1.5000 | 0.8000 | historical_forward | 14 | 14 | 0 | 1.8124 | 0.1487 |
| 1.5000 | 0.8050 | pre_forward | 24 | 22 | 2 | 1.2034 | 0.0579 |
| 1.5000 | 0.8050 | historical_forward | 13 | 13 | 0 | 1.6881 | 0.1492 |
| 1.5000 | 0.8100 | pre_forward | 23 | 22 | 1 | 2.0694 | 0.1038 |
| 1.5000 | 0.8100 | historical_forward | 11 | 11 | 0 | 1.4008 | 0.1459 |

## Clean live（edge buffer=0.02）

| model | rows | trades | wins | logloss | pnl |
| --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | 9 | 3 | 3 | 1.2417 | 0.3924 |
| d1_market_plus_compact | 9 | 3 | 3 | 1.2799 | 0.3924 |
| d1_market_plus_compact_city | 9 | 2 | 2 | 1.2765 | 0.2873 |
| d1_market_raw | 9 | 0 | 0 | 0.8499 | 0.0000 |

## 策略状态

`edge_only` 已被 clean live 否定。`edge_plus_mechanism_veto_discovery`
在当前 clean live 上修复了三个 overshoot 和一个 trigger-boundary stall，
但这个 veto 是看过这些 live miss 后形成的，clean live 只能算 discovery，
不能再算独立 forward。它现在有资格进入 zero-notional shadow，尚无资格 live。
本研究不修改 live 配置，也不把 discovery slice 当成 confirmed alpha。
