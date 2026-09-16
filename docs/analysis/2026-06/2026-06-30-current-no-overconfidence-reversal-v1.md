# Current-NO Overconfidence Reversal v1

Generated: 2026-06-30

## Verdict

当前最好 train-only 规则 `no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=1`：train ROI +50.7% CI -4.7%..+100.5%，train excess vs same-row current-NO +102.3% CI +14.1%..+181.9%；holdout ROI +39.9% 但 CI -61.3%..+125.7%，recent ROI +102.5%。

结论 `inconclusive`：train 显著性、baseline 或 holdout 同号没同时满足，继续找下一方向。

significance=FAIL baseline=PASS forward=FAIL conclusion=inconclusive

## 数据快照

- 数据源：`runtime/weather.db` 自检 + generated current YES/current NO expression matrix。
- 数据快照时间：DB mtime `2026-07-01T02:11:43.166351557+00:00`, fact_built_at_utc `2026-07-01T02:11:08.518593+00:00`。
- 记录行数：fact_trades=4412, fact_signal_candidates=41047, current-pair rows=3363。
- unsettled 占比：fact_trades NULL/unsettled-like=152 / 4412。
- missing_bracket 数：settlement_status_counts={'NULL': 152, 'settled': 4260}; CLOB gate_pass=True。

## Setup

- Thesis: when `current_bracket_no_ask` is high, market is confident final max will not remain in current bracket. Fade that overconfidence by buying `current_high_yes` only when train-selected conflict features say current can still hold.
- Train: target_date < `2026-06-13`. Holdout: target_date >= `2026-06-13`. Recent stress: target_date >= `2026-06-21`.
- Baseline column in this report is the opposite high current-bracket NO leg on the same rows. The primary question is whether buying YES is positive and holds out.

## Candidate Rules

| rule | train rows | train dates | cities | train YES ask | train NO ask | train win | train ROI | CI low | CI high | holdout rows | holdout dates | holdout ROI | recent rows | recent ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=1 | 33 | 14 | 11 | 0.42 | 0.79 | +63.6% | +50.7% | -4.7% | +100.5% | 14 | 7 | +39.9% | 7 | +102.5% |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=1 | 32 | 13 | 10 | 0.42 | 0.79 | +62.5% | +49.5% | -8.0% | +102.2% | 14 | 7 | +39.9% | 7 | +102.5% |
| no_ask>=0.75 & yes_ask<=0.60 & forecast_error_native>=0 | 35 | 15 | 13 | 0.43 | 0.83 | +57.1% | +33.1% | -27.6% | +84.4% | 13 | 6 | +35.2% | 4 | +35.5% |
| no_ask>=0.75 & yes_ask<=0.50 & forecast_error_native>=0 | 33 | 14 | 12 | 0.42 | 0.83 | +54.5% | +29.9% | -33.9% | +82.1% | 13 | 6 | +35.2% | 4 | +35.5% |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0 | 64 | 19 | 16 | 0.41 | 0.78 | +57.8% | +42.2% | -4.8% | +90.5% | 25 | 10 | +27.0% | 8 | +77.2% |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=1 | 37 | 14 | 11 | 0.46 | 0.79 | +67.6% | +48.2% | -3.6% | +92.6% | 15 | 7 | +30.6% | 7 | +102.5% |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=1 | 34 | 14 | 11 | 0.43 | 0.79 | +64.7% | +51.0% | -4.0% | +99.0% | 15 | 7 | +30.6% | 7 | +102.5% |
| no_ask>=0.75 & yes_ask<=0.90 & forecast_error_native>=0 | 51 | 15 | 15 | 0.52 | 0.86 | +64.7% | +25.2% | -16.6% | +58.0% | 14 | 7 | +25.5% | 4 | +35.5% |
| no_ask>=0.75 & yes_ask<=0.80 & forecast_error_native>=0 | 48 | 15 | 15 | 0.50 | 0.86 | +62.5% | +25.8% | -19.6% | +59.5% | 14 | 7 | +25.5% | 4 | +35.5% |
| no_ask>=0.75 & yes_ask<=0.70 & forecast_error_native>=0 | 43 | 15 | 15 | 0.47 | 0.85 | +58.1% | +25.5% | -20.3% | +65.8% | 14 | 7 | +25.5% | 4 | +35.5% |
| no_ask>=0.75 & yes_ask<=0.50 & forecast_gap_to_running_native<=0 | 22 | 8 | 6 | 0.40 | 0.79 | +45.5% | +12.0% | -70.2% | +90.3% | 17 | 5 | +25.2% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=0 | 68 | 20 | 17 | 0.42 | 0.78 | +60.3% | +44.8% | -2.3% | +89.5% | 28 | 11 | +13.4% | 8 | +77.2% |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=0 | 85 | 20 | 18 | 0.48 | 0.80 | +64.7% | +38.1% | -0.3% | +74.1% | 29 | 11 | +9.4% | 8 | +77.2% |
| no_ask>=0.70 & yes_ask<=0.40 & tail_skew>=0.2 | 42 | 14 | 12 | 0.38 | 0.74 | +50.0% | +31.2% | -24.5% | +89.4% | 27 | 11 | +16.2% | 10 | +56.8% |
| no_ask>=0.70 & yes_ask<=0.80 & forecast_error_native>=0 | 82 | 20 | 18 | 0.46 | 0.80 | +63.4% | +38.9% | -0.8% | +75.6% | 29 | 11 | +9.4% | 8 | +77.2% |
| no_ask>=0.70 & yes_ask<=0.40 & forecast_error_native>=0 | 37 | 15 | 10 | 0.38 | 0.76 | +64.9% | +70.1% | -1.8% | +138.4% | 14 | 9 | +17.1% | 4 | +35.5% |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=0 | 77 | 20 | 18 | 0.44 | 0.79 | +61.0% | +39.6% | -2.0% | +79.1% | 29 | 11 | +9.4% | 8 | +77.2% |
| no_ask>=0.75 & yes_ask<=0.90 & forecast_gap_to_running_native<=0 | 37 | 9 | 7 | 0.52 | 0.81 | +51.4% | +2.6% | -55.9% | +65.6% | 27 | 7 | +15.7% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.80 & forecast_gap_to_running_native<=0 | 36 | 9 | 7 | 0.51 | 0.81 | +50.0% | +2.3% | -60.5% | +65.6% | 27 | 7 | +15.7% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.70 & forecast_gap_to_running_native<=0 | 33 | 9 | 7 | 0.48 | 0.82 | +54.5% | +11.6% | -51.4% | +68.9% | 23 | 7 | +12.9% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.40 | 77 | 19 | 18 | 0.38 | 0.75 | +44.2% | +17.0% | -29.5% | +64.5% | 44 | 14 | +0.8% | 19 | +36.2% |
| no_ask>=0.70 & yes_ask<=0.40 & wind_regime=moderate_wind | 28 | 9 | 10 | 0.37 | 0.74 | +57.1% | +53.5% | -36.1% | +123.2% | 19 | 8 | +8.8% | 10 | +78.9% |
| no_ask>=0.75 & yes_ask<=0.60 & forecast_gap_to_running_native<=0 | 26 | 9 | 6 | 0.43 | 0.80 | +42.3% | +1.9% | -65.4% | +74.5% | 20 | 6 | +6.4% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.40 | 27 | 10 | 8 | 0.38 | 0.80 | +48.1% | +26.1% | -56.3% | +99.5% | 25 | 11 | +4.7% | 10 | +31.1% |
| no_ask>=0.70 & yes_ask<=0.50 & tail_skew>=0.6 | 28 | 9 | 4 | 0.41 | 0.75 | +46.4% | +8.2% | -54.7% | +76.1% | 15 | 5 | +77.4% | 1 | -100.0% |
| no_ask>=0.70 & yes_ask<=0.90 & tail_skew>=0.6 | 52 | 12 | 4 | 0.54 | 0.79 | +59.6% | +8.9% | -33.4% | +47.6% | 23 | 6 | +38.6% | 1 | -100.0% |
| no_ask>=0.70 & yes_ask<=0.80 & tail_skew>=0.6 | 49 | 12 | 4 | 0.53 | 0.79 | +57.1% | +8.4% | -33.9% | +51.1% | 23 | 6 | +38.6% | 1 | -100.0% |
| no_ask>=0.70 & yes_ask<=0.70 & tail_skew>=0.6 | 41 | 10 | 4 | 0.48 | 0.77 | +56.1% | +14.0% | -34.1% | +63.2% | 19 | 6 | +40.0% | 1 | -100.0% |
| no_ask>=0.70 & yes_ask<=0.60 & tail_skew>=0.6 | 32 | 9 | 4 | 0.43 | 0.74 | +53.1% | +17.4% | -44.8% | +77.4% | 19 | 6 | +40.0% | 1 | -100.0% |
| no_ask>=0.70 & yes_ask<=0.50 & day_regime=day_forecast_capped | 34 | 13 | 12 | 0.42 | 0.78 | +41.2% | +1.1% | -61.4% | +65.2% | 16 | 6 | +27.7% | 4 | -100.0% |

## Top Rules Full-Window Shape

| rule | rows | dates | cities | YES ask | NO ask | win | ROI | CI low | CI high | losing days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=1 | 47 | 21 | 17 | 0.42 | 0.78 | +61.7% | +47.5% | -4.3% | +92.2% | 9 | $-15.00 |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=1 | 46 | 20 | 16 | 0.42 | 0.78 | +60.9% | +46.6% | -6.3% | +93.7% | 9 | $-15.00 |
| no_ask>=0.75 & yes_ask<=0.60 & forecast_error_native>=0 | 48 | 21 | 18 | 0.42 | 0.82 | +56.2% | +33.6% | -19.7% | +79.0% | 10 | $-20.00 |
| no_ask>=0.75 & yes_ask<=0.50 & forecast_error_native>=0 | 46 | 20 | 17 | 0.41 | 0.82 | +54.3% | +31.4% | -23.7% | +79.6% | 10 | $-20.00 |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0 | 89 | 29 | 24 | 0.41 | 0.77 | +56.2% | +37.9% | -3.5% | +77.8% | 13 | $-20.00 |

## Best Rule Daily

| date | rows | cost | pnl | ROI |
| --- | --- | --- | --- | --- |
| 2026-05-19 | 5 | $+25.00 | $+26.02 | +104.1% |
| 2026-05-21 | 3 | $+15.00 | $+16.25 | +108.3% |
| 2026-05-25 | 1 | $+5.00 | $+9.29 | +185.7% |
| 2026-05-27 | 7 | $+35.00 | $+16.24 | +46.4% |
| 2026-05-29 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-01 | 1 | $+5.00 | $+9.29 | +185.7% |
| 2026-06-03 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-04 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-05 | 3 | $+15.00 | $+24.79 | +165.3% |
| 2026-06-07 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-08 | 1 | $+5.00 | $+8.89 | +177.8% |
| 2026-06-09 | 2 | $+10.00 | $+13.46 | +134.6% |
| 2026-06-11 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-12 | 1 | $+5.00 | $+4.43 | +88.7% |
| 2026-06-13 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-15 | 2 | $+10.00 | $+17.09 | +170.9% |
| 2026-06-18 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-19 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-22 | 4 | $+20.00 | $+23.76 | +118.8% |
| 2026-06-23 | 2 | $+10.00 | $+17.10 | +171.0% |
| 2026-06-26 | 1 | $+5.00 | $-5.00 | -100.0% |

## Best Rule Contribution

By city:

| city | rows | dates | win | YES ask | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Istanbul | 10 | 4 | +70.0% | 0.39 | +75.9% | $+37.93 | -46.8% | +160.0% |
| CapeTown | 3 | 3 | +100.0% | 0.37 | +169.6% | $+25.45 | +156.4% | +177.8% |
| NYC | 5 | 2 | +80.0% | 0.44 | +75.1% | $+18.76 |  |  |
| Chongqing | 2 | 2 | +100.0% | 0.35 | +182.1% | $+18.21 |  |  |
| Beijing | 2 | 1 | +100.0% | 0.37 | +171.0% | $+17.10 |  |  |
| Austin | 3 | 1 | +100.0% | 0.48 | +108.3% | $+16.25 |  |  |
| SanFrancisco | 8 | 2 | +62.5% | 0.46 | +27.6% | $+11.02 |  |  |
| BuenosAires | 1 | 1 | +100.0% | 0.35 | +185.7% | $+9.29 |  |  |
| Seattle | 1 | 1 | +100.0% | 0.38 | +163.2% | $+8.16 |  |  |
| Houston | 1 | 1 | +100.0% | 0.53 | +88.7% | $+4.43 |  |  |
| Chengdu | 1 | 1 | +0.0% | 0.48 | -100.0% | $-5.00 |  |  |
| Guangzhou | 1 | 1 | +0.0% | 0.39 | -100.0% | $-5.00 |  |  |
| Karachi | 1 | 1 | +0.0% | 0.43 | -100.0% | $-5.00 |  |  |
| LA | 1 | 1 | +0.0% | 0.45 | -100.0% | $-5.00 |  |  |
| Lucknow | 1 | 1 | +0.0% | 0.37 | -100.0% | $-5.00 |  |  |
| Ankara | 3 | 1 | +0.0% | 0.44 | -100.0% | $-15.00 |  |  |
| Singapore | 3 | 1 | +0.0% | 0.42 | -100.0% | $-15.00 |  |  |

By regime:

| day | rows | dates | win | YES ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- |
| day_open_runway | 34 | 18 | +58.8% | 0.41 | +42.6% | $+72.43 |
| day_marginal_runway | 13 | 6 | +69.2% | 0.43 | +60.2% | $+39.16 |

## Interpretation

This is a plausible direction only if the top train-selected rule survives holdout. It is still not a live rule because it uses replay asks, no fresh-book depth, and repeated city-hour states. The next implementation step would be zero-notional forward telemetry for the exact rule fields, not attaching it to the current runner.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_current_no_overconfidence_reversal_v1.py`
- JSON summary: `docs/analysis/2026-06/2026-06-30-current-no-overconfidence-reversal-v1.json`
- Candidate rules: `docs/analysis/2026-06/generated/current_no_overconfidence_reversal_v1/candidate_rules.csv`
- Top rule daily: `docs/analysis/2026-06/generated/current_no_overconfidence_reversal_v1/top_rule_daily.csv`
