# Current-NO Overconfidence Reversal v1

Generated: 2026-06-30

## Verdict

当前最好 train-only 规则 `no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0`：train ROI +50.7% CI +4.1%..+95.8%，train excess vs same-row current-NO +100.2% CI +29.1%..+167.7%；holdout ROI +34.2% 但 CI -54.1%..+110.2%，recent ROI +131.9%。

结论暂定 `shadow_candidate_keep_collecting`：不是 live-ready，但这是目前比 naive selector 更像样的方向。规则只在 train 选择，train 显著且相对同点 current-NO baseline 显著，holdout/recent 点估同号；但 holdout CI 跨 0，必须先做 zero-notional forward telemetry 和 fresh-book/depth 验证。

significance=PASS baseline=PASS forward=FAIL conclusion=shadow_candidate

## 数据快照

- 数据源：`runtime/weather.db` 自检 + generated current YES/current NO expression matrix。
- 数据快照时间：DB mtime `2026-06-30T11:10:46.438186169+00:00`, fact_built_at_utc `2026-06-30T11:10:16.721495+00:00`。
- 记录行数：fact_trades=4411, fact_signal_candidates=41047, current-pair rows=3210。
- unsettled 占比：fact_trades NULL/unsettled-like=151 / 4411。
- missing_bracket 数：settlement_status_counts={'NULL': 151, 'settled': 4260}; CLOB gate_pass=True。

## Setup

- Thesis: when `current_bracket_no_ask` is high, market is confident final max will not remain in current bracket. Fade that overconfidence by buying `current_high_yes` only when train-selected conflict features say current can still hold.
- Train: target_date < `2026-06-13`. Holdout: target_date >= `2026-06-13`. Recent stress: target_date >= `2026-06-21`.
- Baseline column in this report is the opposite high current-bracket NO leg on the same rows. The primary question is whether buying YES is positive and holds out.

## Candidate Rules

| rule | train rows | train dates | cities | train YES ask | train NO ask | train win | train ROI | CI low | CI high | holdout rows | holdout dates | holdout ROI | recent rows | recent ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0 | 64 | 19 | 15 | 0.41 | 0.78 | +60.9% | +50.7% | +4.1% | +95.8% | 20 | 7 | +34.2% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=1 | 38 | 13 | 10 | 0.47 | 0.79 | +73.7% | +59.6% | +17.5% | +97.7% | 11 | 5 | +33.6% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=1 | 35 | 13 | 10 | 0.44 | 0.78 | +71.4% | +63.3% | +17.5% | +104.5% | 11 | 5 | +33.6% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=0 | 69 | 20 | 16 | 0.41 | 0.78 | +63.8% | +53.3% | +8.8% | +94.8% | 24 | 8 | +19.0% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=0 | 89 | 20 | 17 | 0.48 | 0.80 | +68.5% | +45.1% | +10.6% | +77.6% | 26 | 9 | +9.9% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.80 & forecast_error_native>=0 | 85 | 20 | 17 | 0.47 | 0.80 | +67.1% | +46.4% | +10.2% | +78.8% | 26 | 9 | +9.9% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=0 | 80 | 20 | 17 | 0.45 | 0.79 | +65.0% | +47.6% | +9.7% | +82.2% | 26 | 9 | +9.9% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.40 & forecast_error_native>=-1 | 55 | 17 | 12 | 0.38 | 0.76 | +60.0% | +59.2% | +6.7% | +108.2% | 13 | 7 | +5.3% | 1 | +171.0% |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=2 | 20 | 9 | 6 | 0.45 | 0.80 | +80.0% | +79.3% | +6.7% | +140.1% | 4 | 2 | +133.8% | 3 | +118.8% |
| no_ask>=0.70 & yes_ask<=0.50 & tail_skew>=0.6 | 29 | 10 | 4 | 0.41 | 0.75 | +48.3% | +13.3% | -50.6% | +83.4% | 14 | 4 | +90.0% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=1 | 32 | 13 | 10 | 0.42 | 0.79 | +68.8% | +63.5% | +15.3% | +108.5% | 9 | 4 | +63.2% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=1 | 30 | 12 | 9 | 0.41 | 0.79 | +66.7% | +61.8% | +7.7% | +106.3% | 9 | 4 | +63.2% | 4 | +131.9% |
| no_ask>=0.70 & yes_ask<=0.40 & forecast_error_native>=0 | 40 | 15 | 10 | 0.38 | 0.76 | +65.0% | +70.6% | +1.8% | +132.8% | 9 | 6 | +52.0% | 1 | +171.0% |
| no_ask>=0.70 & yes_ask<=0.90 & tail_skew>=0.6 | 58 | 13 | 4 | 0.56 | 0.80 | +60.3% | +8.1% | -33.7% | +46.0% | 21 | 5 | +45.5% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.70 & tail_skew>=0.6 | 43 | 11 | 4 | 0.48 | 0.77 | +55.8% | +14.6% | -31.1% | +64.8% | 18 | 5 | +47.8% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.80 & tail_skew>=0.6 | 52 | 13 | 4 | 0.53 | 0.79 | +55.8% | +7.1% | -34.8% | +49.9% | 21 | 5 | +45.5% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.60 & tail_skew>=0.6 | 33 | 10 | 4 | 0.43 | 0.74 | +54.5% | +21.6% | -36.1% | +83.2% | 18 | 5 | +47.8% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.50 & forecast_gap_to_running_native<=0 | 24 | 10 | 8 | 0.40 | 0.79 | +50.0% | +24.4% | -48.6% | +108.1% | 16 | 5 | +46.3% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.60 & forecast_error_native>=0 | 37 | 16 | 14 | 0.42 | 0.82 | +59.5% | +39.8% | -18.1% | +87.2% | 11 | 5 | +35.2% | 1 | +171.0% |
| no_ask>=0.75 & yes_ask<=0.50 & forecast_error_native>=0 | 34 | 15 | 13 | 0.41 | 0.83 | +55.9% | +35.7% | -27.2% | +87.4% | 11 | 5 | +35.2% | 1 | +171.0% |
| no_ask>=0.75 & yes_ask<=0.90 & forecast_error_native>=1 | 22 | 9 | 8 | 0.49 | 0.84 | +72.7% | +53.8% | +3.2% | +87.4% | 4 | 3 | +37.4% | 1 | +171.0% |
| no_ask>=0.70 & yes_ask<=0.40 & tail_skew>=0.2 | 41 | 14 | 12 | 0.37 | 0.74 | +53.7% | +40.6% | -12.0% | +97.1% | 18 | 9 | +30.7% | 3 | +161.3% |
| no_ask>=0.75 & yes_ask<=0.90 & forecast_gap_to_running_native<=0 | 44 | 11 | 9 | 0.54 | 0.82 | +54.5% | +6.1% | -42.4% | +70.8% | 25 | 7 | +28.2% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.40 & forecast_error_native>=-2 | 29 | 12 | 10 | 0.38 | 0.79 | +55.2% | +44.9% | -34.8% | +114.4% | 12 | 6 | +31.4% | 1 | +171.0% |
| no_ask>=0.75 & yes_ask<=0.80 & forecast_gap_to_running_native<=0 | 40 | 11 | 9 | 0.51 | 0.81 | +50.0% | +5.1% | -52.2% | +71.6% | 25 | 7 | +28.2% | 0 |  |
| no_ask>=0.70 & yes_ask<=0.40 | 80 | 20 | 18 | 0.38 | 0.75 | +46.2% | +22.5% | -23.3% | +71.0% | 26 | 11 | +20.9% | 5 | +159.3% |
| no_ask>=0.75 & yes_ask<=0.70 & forecast_gap_to_running_native<=0 | 36 | 11 | 9 | 0.48 | 0.82 | +55.6% | +16.7% | -39.6% | +75.1% | 22 | 7 | +27.7% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.40 & forecast_gap_to_running_native<=2 | 27 | 12 | 10 | 0.38 | 0.80 | +51.9% | +35.2% | -37.0% | +107.8% | 10 | 6 | +29.9% | 1 | +171.0% |
| no_ask>=0.75 & yes_ask<=0.90 & tail_skew>=0.6 | 39 | 10 | 4 | 0.63 | 0.85 | +64.1% | +6.8% | -41.6% | +59.0% | 17 | 4 | +23.4% | 0 |  |
| no_ask>=0.75 & yes_ask<=0.80 & tail_skew>=0.6 | 33 | 10 | 4 | 0.58 | 0.84 | +57.6% | +5.1% | -45.1% | +67.6% | 17 | 4 | +23.4% | 0 |  |

## Top Rules Full-Window Shape

| rule | rows | dates | cities | YES ask | NO ask | win | ROI | CI low | CI high | losing days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0 | 84 | 26 | 20 | 0.41 | 0.78 | +59.5% | +46.8% | +7.3% | +87.1% | 10 | $-20.00 |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=1 | 49 | 18 | 15 | 0.47 | 0.78 | +69.4% | +53.8% | +10.5% | +90.7% | 6 | $-15.00 |
| no_ask>=0.70 & yes_ask<=0.70 & forecast_error_native>=1 | 46 | 18 | 15 | 0.45 | 0.78 | +67.4% | +56.2% | +10.5% | +94.2% | 6 | $-15.00 |
| no_ask>=0.70 & yes_ask<=0.60 & forecast_error_native>=0 | 93 | 28 | 21 | 0.42 | 0.77 | +60.2% | +44.5% | +5.4% | +83.7% | 11 | $-20.00 |
| no_ask>=0.70 & yes_ask<=0.90 & forecast_error_native>=0 | 115 | 29 | 23 | 0.48 | 0.79 | +63.5% | +37.1% | +5.0% | +67.1% | 12 | $-20.00 |

## Best Rule Daily

| date | rows | cost | pnl | ROI |
| --- | --- | --- | --- | --- |
| 2026-05-19 | 4 | $+20.00 | $+20.82 | +104.1% |
| 2026-05-20 | 4 | $+20.00 | $+28.37 | +141.8% |
| 2026-05-21 | 5 | $+25.00 | $+6.25 | +25.0% |
| 2026-05-22 | 1 | $+5.00 | $+5.87 | +117.4% |
| 2026-05-23 | 4 | $+20.00 | $+18.46 | +92.3% |
| 2026-05-25 | 4 | $+20.00 | $+30.87 | +154.4% |
| 2026-05-27 | 8 | $+40.00 | $+24.97 | +62.4% |
| 2026-05-29 | 3 | $+15.00 | $-0.71 | -4.8% |
| 2026-05-31 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-01 | 1 | $+5.00 | $+9.29 | +185.7% |
| 2026-06-03 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-04 | 4 | $+20.00 | $-20.00 | -100.0% |
| 2026-06-05 | 3 | $+15.00 | $+24.79 | +165.3% |
| 2026-06-06 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-07 | 4 | $+20.00 | $-20.00 | -100.0% |
| 2026-06-08 | 3 | $+15.00 | $+11.71 | +78.1% |
| 2026-06-09 | 5 | $+25.00 | $+35.96 | +143.8% |
| 2026-06-10 | 3 | $+15.00 | $+25.54 | +170.3% |
| 2026-06-11 | 1 | $+5.00 | $-5.00 | -100.0% |
| 2026-06-13 | 4 | $+20.00 | $-6.11 | -30.6% |
| 2026-06-14 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-15 | 5 | $+25.00 | $+35.05 | +140.2% |
| 2026-06-17 | 3 | $+15.00 | $-15.00 | -100.0% |
| 2026-06-20 | 1 | $+5.00 | $+8.89 | +177.8% |
| 2026-06-22 | 3 | $+15.00 | $+17.82 | +118.8% |
| 2026-06-23 | 1 | $+5.00 | $+8.55 | +171.0% |

## Best Rule Contribution

By city:

| city | rows | dates | win | YES ask | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 7 | 3 | +85.7% | 0.39 | +120.4% | $+42.13 | -100.0% | +170.3% |
| Istanbul | 11 | 5 | +63.6% | 0.39 | +59.9% | $+32.93 | -76.4% | +154.2% |
| BuenosAires | 6 | 3 | +83.3% | 0.40 | +108.9% | $+32.66 | -100.0% | +185.7% |
| CapeTown | 5 | 4 | +80.0% | 0.38 | +116.7% | $+29.18 | -30.6% | +175.7% |
| Chengdu | 4 | 2 | +75.0% | 0.41 | +92.3% | $+18.46 |  |  |
| Atlanta | 3 | 1 | +100.0% | 0.46 | +119.8% | $+17.97 |  |  |
| NYC | 3 | 1 | +100.0% | 0.46 | +118.8% | $+17.82 |  |  |
| Warsaw | 2 | 2 | +100.0% | 0.36 | +177.8% | $+17.78 |  |  |
| Austin | 3 | 1 | +100.0% | 0.48 | +108.3% | $+16.25 |  |  |
| Manila | 7 | 3 | +57.1% | 0.41 | +43.8% | $+15.32 | -100.0% | +156.4% |
| Chongqing | 3 | 3 | +66.7% | 0.40 | +88.1% | $+13.21 | -100.0% | +185.7% |
| Beijing | 1 | 1 | +100.0% | 0.37 | +171.0% | $+8.55 |  |  |
| Seattle | 2 | 2 | +50.0% | 0.36 | +31.6% | $+3.16 |  |  |
| SanFrancisco | 9 | 3 | +44.4% | 0.46 | -9.3% | $-4.18 | -100.0% | +104.1% |
| LA | 1 | 1 | +0.0% | 0.45 | -100.0% | $-5.00 |  |  |
| Singapore | 1 | 1 | +0.0% | 0.42 | -100.0% | $-5.00 |  |  |
| Busan | 4 | 2 | +25.0% | 0.39 | -28.6% | $-5.71 |  |  |
| Jeddah | 4 | 2 | +25.0% | 0.42 | -45.7% | $-9.13 |  |  |
| Ankara | 3 | 1 | +0.0% | 0.44 | -100.0% | $-15.00 |  |  |
| Amsterdam | 5 | 2 | +0.0% | 0.37 | -100.0% | $-25.00 |  |  |

By regime:

| day | rows | dates | win | YES ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- |
| day_marginal_runway | 27 | 12 | +70.4% | 0.40 | +76.4% | $+103.09 |
| day_forecast_capped | 13 | 6 | +100.0% | 0.42 | +141.2% | $+91.81 |
| day_open_runway | 44 | 19 | +40.9% | 0.41 | +0.7% | $+1.48 |

## Interpretation

This is a plausible direction only if the top train-selected rule survives holdout. It is still not a live rule because it uses replay asks, no fresh-book depth, and repeated city-hour states. The next implementation step would be zero-notional forward telemetry for the exact rule fields, not attaching it to the current runner.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_current_no_overconfidence_reversal_v1.py`
- JSON summary: `docs/analysis/2026-06/2026-06-30-current-no-overconfidence-reversal-v1.json`
- Candidate rules: `docs/analysis/2026-06/generated/current_no_overconfidence_reversal_v1/candidate_rules.csv`
- Top rule daily: `docs/analysis/2026-06/generated/current_no_overconfidence_reversal_v1/top_rule_daily.csv`
