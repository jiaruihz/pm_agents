# d1 exact landing v2：宽分母两段 landing 物理检验

Status: `research / no live change`

## 结论

**Verdict: 当前可得物理因子下 `d1 exact landing` 没有独立 market residual。**
宽分母中最接近 market 的模型是 `d1_market_calibrated`，但 date-equal logloss
delta 仍为 `+0.0069`，95% CI
`[+0.0006, +0.0132]`；
正值表示劣于 market。冻结 high-mid first-signal 中最接近的模型
`d1_market_calibrated` 也为 `+0.0279`。

因此不新增 d1 landing strategy、不把 forecast ceiling 或 d1 distance 写成 hard veto，
也不替换 Current-YES Carry。此前 first-signal-only calibration 的正结果应理解为窄
high-mid cohort 的 base-rate 重标定；宽分母和 clean-live 都不支持把它解释为物理 alpha。

## 目标

在 direct d1 YES mid >= 0.50 的 PIT d1 expression 分母上，预测
`P(final exactly d1)`；这同时要求未来能达到 d1、且到达后不会继续到 d2+。
训练每个 city-day 总权重为一，冻结 d1>=0.80 first signal 与 clean live 只作评估。

## 预注册模型组

- `d1_market_calibrated`：d1 market probability 的连续校准。
- `d1_market_plus_carry_core`：加 peak clock、dewpoint depression、wind、local hour。
- `d1_market_plus_landing_geometry`：加 d1 distance、forecast runway、forecast ceiling 到 d2 的 rung-normalized 距离。
- `d1_market_plus_core_landing`：两组一起；不使用已知语义有缺陷的 strict-high/path clock，也不引入 city gate。

## 数据完整性与双漏斗

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
  "wide_mid_floor": 0.5,
  "wide_rows": 1459,
  "wide_city_days": 909,
  "wide_dates": 45,
  "wide_label_counts": {
    "exact_d1": 950,
    "overshoot_d2plus": 294,
    "stall_current": 215
  },
  "wide_oof_rows_by_model": {
    "d1_market_calibrated": 1131,
    "d1_market_plus_carry_core": 1131,
    "d1_market_plus_core_landing": 1131,
    "d1_market_plus_landing_geometry": 1131,
    "d1_market_raw": 1131
  },
  "wide_oof_dates": 33,
  "frozen_rows": 218,
  "frozen_oof_rows_by_model": {
    "d1_market_calibrated": 139,
    "d1_market_plus_carry_core": 139,
    "d1_market_plus_core_landing": 139,
    "d1_market_plus_landing_geometry": 139,
    "d1_market_raw": 139
  },
  "frozen_oof_dates": 32,
  "clean_live_rows": 9,
  "data_snapshot_note": "Mac market-data mirror incrementally synced 2026-07-24; existing canonical DB covers event_date through 2026-07-23; model inputs are fixed historical atlas/factory plus clean-live journal"
}
```

signal funnel 与 evidence funnel 的缺口均记录在审计；缺完整 ladder 不被解释成策略筛选。
费用为 direct executable d1 ask 加官方 Weather fee。当前尚无历史 full five-share d1 ladder
复放，因此交易层只是价格覆盖诊断，不能视作容量或 live 结论。

## 宽分母同分母 proper score

| model | rows | city_days | dates | market_logloss | model_logloss | logloss_delta | logloss_delta_ci_low | logloss_delta_ci_high | market_brier | model_brier | brier_delta | brier_delta_ci_low | brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | 1131 | 689 | 33 | 0.5915 | 0.5984 | 0.0069 | 0.0006 | 0.0132 | 0.2057 | 0.2084 | 0.0027 | 0.0002 | 0.0052 |
| d1_market_plus_carry_core | 1131 | 689 | 33 | 0.5915 | 0.6014 | 0.0100 | 0.0010 | 0.0199 | 0.2057 | 0.2099 | 0.0042 | 0.0004 | 0.0085 |
| d1_market_plus_landing_geometry | 1131 | 689 | 33 | 0.5915 | 0.6047 | 0.0133 | 0.0038 | 0.0231 | 0.2057 | 0.2114 | 0.0057 | 0.0014 | 0.0100 |
| d1_market_plus_core_landing | 1131 | 689 | 33 | 0.5915 | 0.6082 | 0.0168 | 0.0044 | 0.0294 | 0.2057 | 0.2129 | 0.0072 | 0.0019 | 0.0129 |

## 冻结 d1>=0.80 first-signal proper score

| model | rows | city_days | dates | market_logloss | model_logloss | logloss_delta | logloss_delta_ci_low | logloss_delta_ci_high | market_brier | model_brier | brier_delta | brier_delta_ci_low | brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | 139 | 139 | 32 | 0.2129 | 0.2408 | 0.0279 | 0.0050 | 0.0471 | 0.0487 | 0.0562 | 0.0075 | 0.0036 | 0.0111 |
| d1_market_plus_carry_core | 139 | 139 | 32 | 0.2129 | 0.2440 | 0.0311 | 0.0050 | 0.0511 | 0.0487 | 0.0576 | 0.0088 | 0.0045 | 0.0129 |
| d1_market_plus_landing_geometry | 139 | 139 | 32 | 0.2129 | 0.2433 | 0.0304 | -0.0033 | 0.0563 | 0.0487 | 0.0582 | 0.0095 | 0.0029 | 0.0152 |
| d1_market_plus_core_landing | 139 | 139 | 32 | 0.2129 | 0.2478 | 0.0349 | -0.0033 | 0.0649 | 0.0487 | 0.0602 | 0.0114 | 0.0034 | 0.0187 |

## 冻结 first-signal fee replay（edge > 0.02；诊断，不调阈值）

| evaluation | model | period | rows | trades | wins | overshoots | stalls | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frozen_first_signal | d1_market_calibrated | all_oof | 139 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_calibrated | pre_forward | 96 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_calibrated | historical_forward | 43 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_plus_carry_core | all_oof | 139 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_plus_carry_core | pre_forward | 96 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_plus_carry_core | historical_forward | 43 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_plus_core_landing | all_oof | 139 | 1 | 1 | 0 | 0 | 0.8467 | 0.1533 | 0.1810 |
| frozen_first_signal | d1_market_plus_core_landing | pre_forward | 96 | 1 | 1 | 0 | 0 | 0.8467 | 0.1533 | 0.1810 |
| frozen_first_signal | d1_market_plus_core_landing | historical_forward | 43 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_plus_landing_geometry | all_oof | 139 | 1 | 1 | 0 | 0 | 0.8467 | 0.1533 | 0.1810 |
| frozen_first_signal | d1_market_plus_landing_geometry | pre_forward | 96 | 1 | 1 | 0 | 0 | 0.8467 | 0.1533 | 0.1810 |
| frozen_first_signal | d1_market_plus_landing_geometry | historical_forward | 43 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_raw | all_oof | 139 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_raw | pre_forward | 96 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |
| frozen_first_signal | d1_market_raw | historical_forward | 43 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |

## Clean live score（历史拟合；不参与选择）

| model | rows | logloss | brier | trades | wins | pnl |
| --- | --- | --- | --- | --- | --- | --- |
| d1_market_calibrated | 9 | 0.8162 | 0.3004 | 0 | 0 | 0.0000 |
| d1_market_plus_carry_core | 9 | 0.7962 | 0.2934 | 0 | 0 | 0.0000 |
| d1_market_plus_core_landing | 9 | 0.7829 | 0.2903 | 0 | 0 | 0.0000 |
| d1_market_plus_landing_geometry | 9 | 0.8065 | 0.2978 | 0 | 0 | 0.0000 |
| d1_market_raw | 9 | 0.8499 | 0.3109 | 0 | 0 | 0.0000 |

## 判定规则

只有 landing geometry 在 wide 和 frozen 同分母 proper score 都有稳定、前瞻方向一致的
market residual，且新 shadow forward 独立复现，才把它升级为 d1 landing sleeve。否则保留为
共同 ladder 概率层的研究特征；不加 hard veto，不改现有 d1 live 或 Current-YES Carry。
