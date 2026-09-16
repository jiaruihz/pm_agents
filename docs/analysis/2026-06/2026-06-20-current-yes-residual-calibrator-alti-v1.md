# Current-YES Residual Calibrator + Alti v1

Status: research-only
Generated: 2026-06-19T17:06:23.842841+00:00

## 结论先行

**交易动作：不加模型、不改 live。** A/B 都值得继续研究，但本轮没有任何候选通过 promotion gate。

A 的结论：`market + METAR core` 的 logistic residual calibrator 点估赢 raw market 和 base v9，但日期 bootstrap CI 跨 0；HGB/ML 版本没有更好，反而退化。B 的结论：IEM 可以拉到 `alti`，`d_alti_3h` 有小的条件信号，但候选模型仍没过 raw market/base promotion gate。

## 数据快照

- source rows: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv`
- date range: `2026-05-19`..`2026-06-14`
- rows: total 3239, train 1527, holdout 1712 / 14 dates
- alti cache: `docs/analysis/2026-06/generated/current_yes_residual_calibrator_alti_v1/iem_ext_alti`
- DB snapshot: fact_trades max built at `2026-06-19T16:58:40.212686+00:00`
- missing_bracket rows: 0
- CLOB fill gate: `gate_pass=true` from `weather_clob_fill_coverage_gate.py` rerun before this report

5 行 SQL 自检:

| check | result |
|---|---|
| trade_class | `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]` |
| settlement | `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]` |
| candidate coverage | `{'rows': 33141, 'eligible': 11643, 'paper_ordered': 4509, 'live_filled': 348}` |
| CLOB order/fill join | `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]` |

## Holdout Model Metrics

| model | AUC | logloss | Brier | dLL vs market | CI | dLL vs base v9 | CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_price_as_probability | 0.929 | 0.3042 | 0.0946 | 0.0000 | [0.0000, 0.0000] | -0.0087 | [-0.0217, 0.0159] |
| base_current_yes_v9 | 0.925 | 0.3130 | 0.0954 | 0.0087 | [-0.0159, 0.0217] | 0.0000 | [0.0000, 0.0000] |
| logistic_l2_market_alti_tendency_C0.5 | 0.930 | 0.3000 | 0.0934 | -0.0042 | [-0.0094, 0.0104] | -0.0130 | [-0.0258, 0.0234] |
| logistic_l2_base_alti_tendency_C0.5 | 0.925 | 0.3126 | 0.0952 | 0.0083 | [-0.0171, 0.0211] | -0.0004 | [-0.0019, 0.0005] |
| logistic_l2_market_metar_core_C0.2 | 0.931 | 0.2978 | 0.0924 | -0.0064 | [-0.0153, 0.0058] | -0.0151 | [-0.0252, 0.0128] |
| logistic_l2_base_metar_core_C0.2 | 0.925 | 0.3122 | 0.0951 | 0.0080 | [-0.0162, 0.0208] | -0.0007 | [-0.0018, 0.0005] |
| logistic_l2_market_metar_core_alti_C0.2 | 0.931 | 0.2978 | 0.0924 | -0.0065 | [-0.0154, 0.0080] | -0.0152 | [-0.0256, 0.0158] |
| logistic_l2_base_metar_core_alti_C0.2 | 0.925 | 0.3121 | 0.0950 | 0.0079 | [-0.0135, 0.0209] | -0.0008 | [-0.0030, 0.0044] |
| market_logit_calibrated | 0.929 | 0.3022 | 0.0942 | -0.0020 | [-0.0065, 0.0141] | -0.0107 | [-0.0230, 0.0270] |
| base_logit_calibrated | 0.925 | 0.3148 | 0.0959 | 0.0106 | [-0.0157, 0.0240] | 0.0019 | [-0.0006, 0.0033] |
| hgb_market_metar_core_{'max_iter': 80, 'learning_rate': 0.03, 'max_leaf_nodes': 5, 'l2_regularization': 5.0, 'min_samples_leaf': 60} | 0.926 | 0.3205 | 0.0962 | 0.0162 | [0.0083, 0.0421] | 0.0075 | [-0.0036, 0.0530] |
| hgb_market_metar_core_alti_{'max_iter': 80, 'learning_rate': 0.03, 'max_leaf_nodes': 5, 'l2_regularization': 5.0, 'min_samples_leaf': 60} | 0.926 | 0.3204 | 0.0962 | 0.0162 | [0.0083, 0.0415] | 0.0075 | [-0.0036, 0.0524] |
| hgb_base_metar_core_{'max_iter': 80, 'learning_rate': 0.03, 'max_leaf_nodes': 3, 'l2_regularization': 1.0, 'min_samples_leaf': 40} | 0.916 | 0.3265 | 0.0979 | 0.0222 | [-0.0034, 0.0357] | 0.0135 | [0.0049, 0.0225] |

## Alti Tests

| feature | coverage | AUC | dLL vs market | CI | dLL vs base v9 | CI |
|---|---:|---:|---:|---:|---:|---:|
| alti_now | 100.0% | 0.583 | -0.0004 | [-0.0024, 0.0002] | 0.0014 | [-0.0008, 0.0057] |
| d_alti_3h | 100.0% | 0.549 | -0.0021 | [-0.0053, -0.0005] | -0.0011 | [-0.0022, -0.0003] |

## ML 方向判断

值得尝试，但只值得作为严格打擂，不值得直接迁移生产。本轮 HGB 在 train-date CV 选参后，holdout logloss 输给 logistic residual calibrator，也输给 raw market；这说明当前 27 日期窗口太薄，非线性模型更容易吃到日期/城市噪声。后续若扩样到更多日期，可以继续让 HGB/GBDT 参赛，但 promotion gate 必须是 holdout + 日期 bootstrap 同时赢 raw market 和 base v9。

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

Best non-baseline candidate is logistic_l2_market_metar_core_alti_C0.2 with holdout logloss 0.2978; delta vs market -0.00646 CI [-0.015435831758235355, 0.008044856144402267], delta vs base_v9 -0.01519 CI [-0.025581090251579088, 0.015766395336204355]. CI gate did not pass, so no model/live change.

## Outputs

- metrics: `docs/analysis/2026-06/generated/current_yes_residual_calibrator_alti_v1/model_metrics.csv`
- CV selection: `docs/analysis/2026-06/generated/current_yes_residual_calibrator_alti_v1/cv_selection.csv`
- alti tests: `docs/analysis/2026-06/generated/current_yes_residual_calibrator_alti_v1/alti_feature_tests.csv`
- JSON: `docs/analysis/2026-06/2026-06-20-current-yes-residual-calibrator-alti-v1.json`
