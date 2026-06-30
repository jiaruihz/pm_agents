# Current-NO Overconfidence Reversal v2

Generated: 2026-07-01

## Verdict

Frozen rule: `no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0`.

更接近可执行的一城一日第一触发口径：43 trades / 26 active dates / 20 cities，avg YES ask 0.402，win +58.1%，ROI +47.9% CI +8.6%..+85.2%；holdout ROI +64.3% CI -30.2%..+140.1%。

按 $5/signal 估算，历史 active day 平均 1.65 笔，投入 $+8.27/active day，full-window 期望 $+3.96/active day，holdout 期望 $+5.05/active day。

`shadow_candidate_keep_collecting`：去重后 full-window CI 仍为正，holdout 点估同号；但 holdout CI 跨 0，且 expression matrix 只覆盖到 2026-06-23，不能 live。

significance=PASS baseline=PASS forward=FAIL conclusion=shadow_candidate

## 数据快照

- DB: `runtime/weather.db`, fact_built_at_utc `2026-06-30T19:07:21.660109+00:00`, CLOB gate_pass=True.
- fact_trades=4412, fact_signal_candidates=41047, unsettled-like=152.
- Expression matrix coverage: 3210 rows, 36 dates, 36 cities, 2026-05-19..2026-06-23.
- 注意：7/1 sync/rebuild 没有把这份 expression matrix 扩展到 6/24 以后；fresh forward 仍待 zero-notional telemetry。

## Execution Granularity A/B

| mode | rows | dates | cities | trades/day | cost/day | pnl/day | win | ROI | CI low | CI high | NO baseline | excess | losing days | <=-50% days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_city_date_no_open_runway | 19 | 16 | 15 | 1 | $+5.94 | $+5.89 | +78.9% | +99.1% | +53.2% | +141.8% | -72.5% | +171.6% | 3 | 3 | $-5.00 |
| daily_cap_5_no_open_runway | 19 | 16 | 15 | 1 | $+5.94 | $+5.89 | +78.9% | +99.1% | +53.2% | +141.8% | -72.5% | +171.6% | 3 | 3 | $-5.00 |
| first_city_date_bracket | 43 | 26 | 20 | 2 | $+8.27 | $+3.96 | +58.1% | +47.9% | +8.6% | +85.2% | -47.0% | +94.8% | 9 | 8 | $-10.00 |
| first_city_date | 43 | 26 | 20 | 2 | $+8.27 | $+3.96 | +58.1% | +47.9% | +8.6% | +85.2% | -47.0% | +94.8% | 9 | 8 | $-10.00 |
| daily_cap_5_earliest | 43 | 26 | 20 | 2 | $+8.27 | $+3.96 | +58.1% | +47.9% | +8.6% | +85.2% | -47.0% | +94.8% | 9 | 8 | $-10.00 |
| raw_city_hour | 84 | 26 | 20 | 3 | $+16.15 | $+7.55 | +59.5% | +46.8% | +7.3% | +87.1% | -47.9% | +94.7% | 10 | 8 | $-20.00 |

## Train / Holdout / Recent

| mode | period | rows | dates | trades/day | pnl/day | win | ROI | CI low | CI high | NO baseline | excess | losing days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raw_city_hour | train | 64 | 19 | 3 | $+8.54 | +60.9% | +50.7% | +4.1% | +95.8% | -49.6% | +100.2% | 7 | $-20.00 |
| raw_city_hour | holdout | 20 | 7 | 3 | $+4.89 | +55.0% | +34.2% | -54.1% | +110.2% | -42.5% | +76.7% | 3 | $-15.00 |
| raw_city_hour | recent | 4 | 2 | 2 | $+13.19 | +100.0% | +131.9% |  |  | -100.0% | +231.9% | 0 | $+8.55 |
| first_city_date_bracket | train | 32 | 19 | 2 | $+3.55 | +56.2% | +42.2% | -0.6% | +87.1% | -44.6% | +86.8% | 7 | $-10.00 |
| first_city_date_bracket | holdout | 11 | 7 | 2 | $+5.05 | +63.6% | +64.3% | -30.2% | +140.1% | -53.8% | +118.1% | 2 | $-10.00 |
| first_city_date_bracket | recent | 2 | 2 | 1 | $+7.25 | +100.0% | +144.9% |  |  | -100.0% | +244.9% | 0 | $+5.94 |
| first_city_date | train | 32 | 19 | 2 | $+3.55 | +56.2% | +42.2% | -0.6% | +87.1% | -44.6% | +86.8% | 7 | $-10.00 |
| first_city_date | holdout | 11 | 7 | 2 | $+5.05 | +63.6% | +64.3% | -30.2% | +140.1% | -53.8% | +118.1% | 2 | $-10.00 |
| first_city_date | recent | 2 | 2 | 1 | $+7.25 | +100.0% | +144.9% |  |  | -100.0% | +244.9% | 0 | $+5.94 |
| daily_cap_5_earliest | train | 32 | 19 | 2 | $+3.55 | +56.2% | +42.2% | -0.6% | +87.1% | -44.6% | +86.8% | 7 | $-10.00 |
| daily_cap_5_earliest | holdout | 11 | 7 | 2 | $+5.05 | +63.6% | +64.3% | -30.2% | +140.1% | -53.8% | +118.1% | 2 | $-10.00 |
| daily_cap_5_earliest | recent | 2 | 2 | 1 | $+7.25 | +100.0% | +144.9% |  |  | -100.0% | +244.9% | 0 | $+5.94 |
| first_city_date_no_open_runway | train | 15 | 13 | 1 | $+4.79 | +73.3% | +83.0% | +23.1% | +136.0% | -65.2% | +148.2% | 3 | $-5.00 |
| first_city_date_no_open_runway | holdout | 4 | 3 | 1 | $+10.64 | +100.0% | +159.6% | +141.5% | +177.8% | -100.0% | +259.6% | 0 | $+8.89 |
| first_city_date_no_open_runway | recent | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| daily_cap_5_no_open_runway | train | 15 | 13 | 1 | $+4.79 | +73.3% | +83.0% | +23.1% | +136.0% | -65.2% | +148.2% | 3 | $-5.00 |
| daily_cap_5_no_open_runway | holdout | 4 | 3 | 1 | $+10.64 | +100.0% | +159.6% | +141.5% | +177.8% | -100.0% | +259.6% | 0 | $+8.89 |
| daily_cap_5_no_open_runway | recent | 0 | 0 |  |  |  |  |  |  |  |  |  |  |

## Daily PnL: first_city_date

| date | period | rows | cost | pnl | ROI | NO pnl |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-19 | train | 1 | $+5.00 | $+5.20 | +104.1% | $-5.00 |
| 2026-05-20 | train | 1 | $+5.00 | $+7.50 | +150.0% | $-5.00 |
| 2026-05-21 | train | 2 | $+10.00 | $+0.42 | +4.2% | $-3.80 |
| 2026-05-22 | train | 1 | $+5.00 | $+5.87 | +117.4% | $-5.00 |
| 2026-05-23 | train | 2 | $+10.00 | $+2.82 | +28.2% | $-4.25 |
| 2026-05-25 | train | 2 | $+10.00 | $+16.48 | +164.8% | $-10.00 |
| 2026-05-27 | train | 3 | $+15.00 | $+11.24 | +74.9% | $-8.51 |
| 2026-05-29 | train | 3 | $+15.00 | $-0.71 | -4.8% | $-1.99 |
| 2026-05-31 | train | 1 | $+5.00 | $-5.00 | -100.0% | $+2.04 |
| 2026-06-01 | train | 1 | $+5.00 | $+9.29 | +185.7% | $-5.00 |
| 2026-06-03 | train | 1 | $+5.00 | $-5.00 | -100.0% | $+1.41 |
| 2026-06-04 | train | 2 | $+10.00 | $-10.00 | -100.0% | $+1.70 |
| 2026-06-05 | train | 1 | $+5.00 | $+8.26 | +165.3% | $-5.00 |
| 2026-06-06 | train | 1 | $+5.00 | $-5.00 | -100.0% | $+1.85 |
| 2026-06-07 | train | 2 | $+10.00 | $-10.00 | -100.0% | $+2.24 |
| 2026-06-08 | train | 3 | $+15.00 | $+11.71 | +78.1% | $-8.24 |
| 2026-06-09 | train | 3 | $+15.00 | $+20.96 | +139.7% | $-15.00 |
| 2026-06-10 | train | 1 | $+5.00 | $+8.51 | +170.3% | $-5.00 |
| 2026-06-11 | train | 1 | $+5.00 | $-5.00 | -100.0% | $+1.17 |
| 2026-06-13 | holdout | 2 | $+10.00 | $+3.89 | +38.9% | $-2.96 |
| 2026-06-14 | holdout | 1 | $+5.00 | $-5.00 | -100.0% | $+1.35 |
| 2026-06-15 | holdout | 3 | $+15.00 | $+23.07 | +153.8% | $-15.00 |
| 2026-06-17 | holdout | 2 | $+10.00 | $-10.00 | -100.0% | $+2.02 |
| 2026-06-20 | holdout | 1 | $+5.00 | $+8.89 | +177.8% | $-5.00 |
| 2026-06-22 | holdout | 1 | $+5.00 | $+5.94 | +118.8% | $-5.00 |
| 2026-06-23 | holdout | 1 | $+5.00 | $+8.55 | +171.0% | $-5.00 |

## City Contribution: first_city_date

| city | rows | dates | win | YES ask | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CapeTown | 4 | 4 | +75.0% | 0.38 | +102.2% | $+20.45 | -31.3% | +176.3% |
| Warsaw | 2 | 2 | +100.0% | 0.36 | +177.8% | $+17.78 |  |  |
| Chongqing | 3 | 3 | +66.7% | 0.40 | +88.1% | $+13.21 | -100.0% | +185.7% |
| BuenosAires | 3 | 3 | +66.7% | 0.39 | +78.6% | $+11.79 | -100.0% | +185.7% |
| Istanbul | 5 | 5 | +60.0% | 0.39 | +45.6% | $+11.40 | -57.4% | +148.7% |
| TelAviv | 3 | 3 | +66.7% | 0.38 | +71.4% | $+10.71 | -100.0% | +170.3% |
| Manila | 3 | 3 | +66.7% | 0.40 | +68.8% | $+10.32 | -100.0% | +156.4% |
| Busan | 2 | 2 | +50.0% | 0.38 | +42.9% | $+4.29 |  |  |
| Seattle | 2 | 2 | +50.0% | 0.36 | +31.6% | $+3.16 |  |  |
| Chengdu | 2 | 2 | +50.0% | 0.44 | +28.2% | $+2.82 |  |  |
| Jeddah | 2 | 2 | +50.0% | 0.43 | +8.7% | $+0.87 |  |  |
| SanFrancisco | 3 | 3 | +33.3% | 0.46 | -32.0% | $-4.80 | -100.0% | +104.1% |
| Amsterdam | 2 | 2 | +0.0% | 0.37 | -100.0% | $-10.00 |  |  |

## Interpretation

- The raw city-hour result is intentionally not the expected live return because it can buy repeated states in the same city/date.
- The cleaner expected-return proxy is `first_city_date`: one exposure per city/date, earliest trigger, no city/regime tuning.
- `no_open_runway` improves point estimates but is exploratory because it is informed by the v1 regime contribution; it should be a telemetry tag, not a live filter.
- Capacity is not proven. All PnL assumes $5 filled at replay ask with no fresh-book depth or queue/slippage penalty.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_current_no_overconfidence_reversal_v2.py`
- JSON: `docs/analysis/2026-07/2026-07-01-current-no-overconfidence-reversal-v2.json`
- Mode summary: `docs/analysis/2026-07/generated/current_no_overconfidence_reversal_v2/mode_summary.csv`
- Period summary: `docs/analysis/2026-07/generated/current_no_overconfidence_reversal_v2/period_summary.csv`
- Daily PnL: `docs/analysis/2026-07/generated/current_no_overconfidence_reversal_v2/first_city_date_daily.csv`
