# Theta Current YES Forecast Clock Model v12

Status: research_only / no_live_upgrade
Generated: 2026-06-17T16:06:09+00:00

Target metric: `forecast_clock_model_incremental_value` = forecast peak clock 作为模型特征，是否提升 current-YES 的成功率、校准和可交易 ROI。

## 数据完整性自检

- fact_built_at_utc: `2026-06-17T15:49:12.423102+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 31496, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`
- CLOB gate: `{'gate_pass': True, 'fail_reasons': [], 'missing_order_rows': 0, 'over_order_keys': 0, 'db_fill_cost_minus_fact_cost': 0.0}`

## 人话结论

forecast-clock 特征接进模型后，没有给 current-YES 带来足够稳定的增量。它能改变一些排序和校准点估，但在真正 live-like slice 里没有打赢市场 ask，也没有明显打赢现有 v9 artifact。

交易上，继续保留 v9 fixed fade-confirmed tiny-live 候选；forecast-clock 只作为生产 telemetry 和后续模型特征落盘，不能作为本轮升级 live 的理由。

## Holdout 全样本模型表现

| model | rows/dates | actual | mean p | AUC | Brier | LogLoss | Acc@0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| forecast_clock_logit_iso | 1712/14 | 68.8% | 66.9% | 0.9270 | 0.0930 | 0.3044 | 87.6% |
| forecast_clock_hgb_iso | 1712/14 | 68.8% | 68.6% | 0.9283 | 0.0931 | 0.3018 | 87.1% |
| forecast_clock_logit | 1712/14 | 68.8% | 67.3% | 0.9266 | 0.0940 | 0.3089 | 87.3% |
| market_yes_ask | 1712/14 | 68.8% | 70.7% | 0.9288 | 0.0946 | 0.3042 | 86.9% |
| live_v9_artifact | 1712/14 | 68.8% | 67.9% | 0.9246 | 0.0954 | 0.3130 | 87.3% |
| weather_price_logit | 1712/14 | 68.8% | 67.9% | 0.9246 | 0.0954 | 0.3130 | 87.3% |

## Holdout Live-Like Slice

| model | rows/dates | actual | mean p | AUC | Brier | LogLoss | Acc@0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_yes_ask | 278/13 | 93.2% | 91.7% | 0.7177 | 0.0659 | 0.2406 | 93.2% |
| live_v9_artifact | 278/13 | 93.2% | 90.7% | 0.7161 | 0.0688 | 0.2497 | 91.7% |
| weather_price_logit | 278/13 | 93.2% | 90.7% | 0.7161 | 0.0688 | 0.2497 | 91.7% |
| forecast_clock_hgb_iso | 278/13 | 93.2% | 89.8% | 0.7450 | 0.0719 | 0.2485 | 91.4% |
| forecast_clock_logit_iso | 278/13 | 93.2% | 87.3% | 0.7377 | 0.0752 | 0.2603 | 90.3% |
| forecast_clock_logit | 278/13 | 93.2% | 88.9% | 0.7440 | 0.0790 | 0.2667 | 89.2% |

## Live Rule ROI Using Each Model

规则：holdout / live-like slice / `p>=0.5` / `p-ask>=0.05` / top ask notional >= $2 / $5 notional / taker +2c。

| model | orders/dates | win | ROI | CI95 | avg ask | avg edge |
|---|---:|---:|---:|---|---:|---:|
| live_v9_artifact | 31/11 | +93.5% | +16.2% | [+2.7%, +27.9%] | 0.794 | +9.5% |
| weather_price_logit | 31/11 | +93.5% | +16.2% | [+2.7%, +27.9%] | 0.794 | +9.5% |
| forecast_clock_hgb_iso | 11/8 | +90.9% | +12.2% | [-7.6%, +29.2%] | 0.798 | +7.5% |
| forecast_clock_logit_iso | 24/10 | +91.7% | +9.4% | [-1.7%, +24.0%] | 0.828 | +9.6% |
| forecast_clock_logit | 32/11 | +90.6% | +9.1% | [-2.5%, +21.2%] | 0.821 | +10.5% |

## Bootstrap Deltas

负数表示候选模型的 Brier/LogLoss 比 baseline 更好。

| scope | candidate | baseline | metric | delta | CI95 |
|---|---|---|---|---:|---|
| holdout_all | forecast_clock_logit | live_v9_artifact | brier | -0.00136 | [-0.00663, 0.00390] |
| holdout_all | forecast_clock_logit | live_v9_artifact | logloss | -0.00408 | [-0.02232, 0.01534] |
| holdout_all | forecast_clock_logit_iso | live_v9_artifact | brier | -0.00236 | [-0.00802, 0.00318] |
| holdout_all | forecast_clock_logit_iso | live_v9_artifact | logloss | -0.00857 | [-0.03046, 0.01332] |
| holdout_all | forecast_clock_hgb_iso | live_v9_artifact | brier | -0.00230 | [-0.00693, 0.00230] |
| holdout_all | forecast_clock_hgb_iso | live_v9_artifact | logloss | -0.01117 | [-0.02691, 0.00418] |
| holdout_all | forecast_clock_logit | market_yes_ask | brier | -0.00058 | [-0.00844, 0.00751] |
| holdout_all | forecast_clock_logit | market_yes_ask | logloss | 0.00464 | [-0.01880, 0.02863] |
| holdout_all | forecast_clock_logit_iso | market_yes_ask | brier | -0.00158 | [-0.00898, 0.00580] |
| holdout_all | forecast_clock_logit_iso | market_yes_ask | logloss | 0.00016 | [-0.02490, 0.02477] |
| holdout_all | forecast_clock_hgb_iso | market_yes_ask | brier | -0.00151 | [-0.00675, 0.00311] |
| holdout_all | forecast_clock_hgb_iso | market_yes_ask | logloss | -0.00245 | [-0.01623, 0.01017] |
| holdout_live_slice | forecast_clock_logit | live_v9_artifact | brier | 0.01019 | [-0.00507, 0.02323] |
| holdout_live_slice | forecast_clock_logit | live_v9_artifact | logloss | 0.01694 | [-0.03474, 0.06206] |
| holdout_live_slice | forecast_clock_logit_iso | live_v9_artifact | brier | 0.00643 | [-0.00503, 0.01560] |
| holdout_live_slice | forecast_clock_logit_iso | live_v9_artifact | logloss | 0.01060 | [-0.03725, 0.05252] |
| holdout_live_slice | forecast_clock_hgb_iso | live_v9_artifact | brier | 0.00316 | [-0.00327, 0.00893] |
| holdout_live_slice | forecast_clock_hgb_iso | live_v9_artifact | logloss | -0.00126 | [-0.02112, 0.01727] |
| holdout_live_slice | forecast_clock_logit | market_yes_ask | brier | 0.01310 | [-0.00383, 0.02867] |
| holdout_live_slice | forecast_clock_logit | market_yes_ask | logloss | 0.02603 | [-0.03868, 0.08084] |
| holdout_live_slice | forecast_clock_logit_iso | market_yes_ask | brier | 0.00934 | [-0.00323, 0.02023] |
| holdout_live_slice | forecast_clock_logit_iso | market_yes_ask | logloss | 0.01969 | [-0.03788, 0.06825] |
| holdout_live_slice | forecast_clock_hgb_iso | market_yes_ask | brier | 0.00607 | [0.00000, 0.01204] |
| holdout_live_slice | forecast_clock_hgb_iso | market_yes_ask | logloss | 0.00784 | [-0.01165, 0.02684] |

## 交易动作

- 不替换 v9 current-YES 模型。
- 不因为 forecast-clock 特征上 live。
- 下一步实盘准备应该是：生产 snapshot 原生落 `forecast_peak_*`、fresh-book/execution survival 特征、obs age/METAR blackout guard，然后继续 forward shadow。

## 三道门

- significance=FAIL：forecast-clock 模型相对 market ask / v9 的 live-like Brier/logloss delta 没有稳定过门。
- baseline=FAIL：可交易 ROI 没有稳定打赢 v9 fixed rule。
- forward=FAIL：该特征来自 historical backfill，不是生产前瞻原生字段。
- conclusion=`inconclusive` / `research_only`；不允许 live upgrade。

## 产物

- JSON: `docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-clock-model-v12.json`
- metrics CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_clock_model_v12/model_metrics.csv`
- trade CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_clock_model_v12/live_rule_summaries.csv`
- delta CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_clock_model_v12/bootstrap_deltas.csv`
- scored rows CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_clock_model_v12/scored_rows.csv`
- Script: `scripts/analysis/reheat_risk/research_theta_current_yes_forecast_clock_model_v12.py`
