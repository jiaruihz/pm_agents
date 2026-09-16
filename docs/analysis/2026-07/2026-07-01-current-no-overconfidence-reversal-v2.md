# Current-NO Overconfidence Reversal v2

Generated: 2026-07-01

## Verdict

Frozen rule: `no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0`.

更接近可执行的一城一日第一触发口径：46 trades / 29 active dates / 24 cities，avg YES ask 0.401，win +52.2%，ROI +32.6% CI -9.6%..+75.8%；holdout ROI +20.5% CI -60.3%..+98.1%。

按 $5/signal 估算，历史 active day 平均 1.59 笔，投入 $+7.93/active day，full-window 期望 $+2.59/active day，holdout 期望 $+1.53/active day。

`inconclusive`：可执行化去重后显著性、baseline 或 forward 不足。

significance=FAIL baseline=PASS forward=FAIL conclusion=inconclusive

## 数据快照

- DB: `runtime/weather.db`, fact_built_at_utc `2026-07-01T02:11:08.518593+00:00`, CLOB gate_pass=True.
- fact_trades=4412, fact_signal_candidates=41047, unsettled-like=152.
- Expression matrix coverage: 3363 rows, 39 dates, 36 cities, 2026-05-19..2026-06-26.
- 注意：本轮已把可结算 expression matrix 扩到 2026-06-26；6/27..6/29 有部分状态但缺 final winner，6/30 缺日内 orderbook replay/settlement，不能计入 ROI。

## Execution Granularity A/B

| mode | rows | dates | cities | trades/day | cost/day | pnl/day | win | ROI | CI low | CI high | NO baseline | excess | losing days | <=-50% days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_city_date_no_open_runway | 19 | 16 | 16 | 1 | $+5.94 | $+5.08 | +73.7% | +85.6% | +34.4% | +130.7% | -65.7% | +151.3% | 4 | 4 | $-5.00 |
| daily_cap_5_no_open_runway | 19 | 16 | 16 | 1 | $+5.94 | $+5.08 | +73.7% | +85.6% | +34.4% | +130.7% | -65.7% | +151.3% | 4 | 4 | $-5.00 |
| raw_city_hour | 89 | 29 | 24 | 3 | $+15.34 | $+5.82 | +56.2% | +37.9% | -3.5% | +77.8% | -42.8% | +80.7% | 13 | 11 | $-20.00 |
| first_city_date_bracket | 46 | 29 | 24 | 2 | $+7.93 | $+2.59 | +52.2% | +32.6% | -9.6% | +75.8% | -38.6% | +71.2% | 12 | 11 | $-10.00 |
| first_city_date | 46 | 29 | 24 | 2 | $+7.93 | $+2.59 | +52.2% | +32.6% | -9.6% | +75.8% | -38.6% | +71.2% | 12 | 11 | $-10.00 |
| daily_cap_5_earliest | 46 | 29 | 24 | 2 | $+7.93 | $+2.59 | +52.2% | +32.6% | -9.6% | +75.8% | -38.6% | +71.2% | 12 | 11 | $-10.00 |

## Train / Holdout / Recent

| mode | period | rows | dates | trades/day | pnl/day | win | ROI | CI low | CI high | NO baseline | excess | losing days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raw_city_hour | train | 64 | 19 | 3 | $+7.10 | +57.8% | +42.2% | -4.8% | +90.5% | -45.1% | +87.3% | 7 | $-20.00 |
| raw_city_hour | holdout | 25 | 10 | 2 | $+3.37 | +52.0% | +27.0% | -56.1% | +94.9% | -36.8% | +63.7% | 6 | $-15.00 |
| raw_city_hour | recent | 8 | 3 | 3 | $+10.29 | +75.0% | +77.2% | -100.0% | +171.0% | -68.1% | +145.3% | 1 | $-10.00 |
| first_city_date_bracket | train | 31 | 19 | 2 | $+3.14 | +54.8% | +38.5% | -7.3% | +88.9% | -42.6% | +81.1% | 7 | $-10.00 |
| first_city_date_bracket | holdout | 15 | 10 | 2 | $+1.53 | +46.7% | +20.5% | -60.3% | +98.1% | -30.3% | +50.8% | 5 | $-10.00 |
| first_city_date_bracket | recent | 4 | 3 | 1 | $+1.50 | +50.0% | +22.5% | -100.0% | +171.0% | -36.3% | +58.7% | 1 | $-10.00 |
| first_city_date | train | 31 | 19 | 2 | $+3.14 | +54.8% | +38.5% | -7.3% | +88.9% | -42.6% | +81.1% | 7 | $-10.00 |
| first_city_date | holdout | 15 | 10 | 2 | $+1.53 | +46.7% | +20.5% | -60.3% | +98.1% | -30.3% | +50.8% | 5 | $-10.00 |
| first_city_date | recent | 4 | 3 | 1 | $+1.50 | +50.0% | +22.5% | -100.0% | +171.0% | -36.3% | +58.7% | 1 | $-10.00 |
| daily_cap_5_earliest | train | 31 | 19 | 2 | $+3.14 | +54.8% | +38.5% | -7.3% | +88.9% | -42.6% | +81.1% | 7 | $-10.00 |
| daily_cap_5_earliest | holdout | 15 | 10 | 2 | $+1.53 | +46.7% | +20.5% | -60.3% | +98.1% | -30.3% | +50.8% | 5 | $-10.00 |
| daily_cap_5_earliest | recent | 4 | 3 | 1 | $+1.50 | +50.0% | +22.5% | -100.0% | +171.0% | -36.3% | +58.7% | 1 | $-10.00 |
| first_city_date_no_open_runway | train | 14 | 12 | 1 | $+4.54 | +71.4% | +77.7% | +14.5% | +133.4% | -62.7% | +140.4% | 3 | $-5.00 |
| first_city_date_no_open_runway | holdout | 5 | 4 | 1 | $+6.73 | +80.0% | +107.7% | -30.6% | +177.8% | -74.0% | +181.7% | 1 | $-5.00 |
| first_city_date_no_open_runway | recent | 1 | 1 | 1 | $-5.00 | +0.0% | -100.0% |  |  | +29.9% | -129.9% | 1 | $-5.00 |
| daily_cap_5_no_open_runway | train | 14 | 12 | 1 | $+4.54 | +71.4% | +77.7% | +14.5% | +133.4% | -62.7% | +140.4% | 3 | $-5.00 |
| daily_cap_5_no_open_runway | holdout | 5 | 4 | 1 | $+6.73 | +80.0% | +107.7% | -30.6% | +177.8% | -74.0% | +181.7% | 1 | $-5.00 |
| daily_cap_5_no_open_runway | recent | 1 | 1 | 1 | $-5.00 | +0.0% | -100.0% |  |  | +29.9% | -129.9% | 1 | $-5.00 |

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
| 2026-06-08 | train | 1 | $+5.00 | $+8.89 | +177.8% | $-5.00 |
| 2026-06-09 | train | 3 | $+15.00 | $+20.96 | +139.7% | $-15.00 |
| 2026-06-10 | train | 1 | $+5.00 | $+8.51 | +170.3% | $-5.00 |
| 2026-06-11 | train | 2 | $+10.00 | $-10.00 | -100.0% | $+3.32 |
| 2026-06-13 | holdout | 2 | $+10.00 | $+3.89 | +38.9% | $-2.96 |
| 2026-06-14 | holdout | 1 | $+5.00 | $-5.00 | -100.0% | $+1.35 |
| 2026-06-15 | holdout | 3 | $+15.00 | $+23.07 | +153.8% | $-15.00 |
| 2026-06-17 | holdout | 2 | $+10.00 | $-10.00 | -100.0% | $+2.02 |
| 2026-06-18 | holdout | 1 | $+5.00 | $-5.00 | -100.0% | $+1.94 |
| 2026-06-19 | holdout | 1 | $+5.00 | $-5.00 | -100.0% | $+2.14 |
| 2026-06-20 | holdout | 1 | $+5.00 | $+8.89 | +177.8% | $-5.00 |
| 2026-06-22 | holdout | 1 | $+5.00 | $+5.94 | +118.8% | $-5.00 |
| 2026-06-23 | holdout | 1 | $+5.00 | $+8.55 | +171.0% | $-5.00 |
| 2026-06-26 | holdout | 2 | $+10.00 | $-10.00 | -100.0% | $+2.74 |

## City Contribution: first_city_date

| city | rows | dates | win | YES ask | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CapeTown | 4 | 4 | +75.0% | 0.38 | +102.2% | $+20.45 | -31.3% | +176.3% |
| Warsaw | 2 | 2 | +100.0% | 0.36 | +177.8% | $+17.78 |  |  |
| Istanbul | 4 | 4 | +75.0% | 0.40 | +82.0% | $+16.40 | -37.5% | +157.6% |
| Chongqing | 3 | 3 | +66.7% | 0.40 | +88.1% | $+13.21 | -100.0% | +185.7% |
| BuenosAires | 3 | 3 | +66.7% | 0.39 | +78.6% | $+11.79 | -100.0% | +185.7% |
| TelAviv | 3 | 3 | +66.7% | 0.38 | +71.4% | $+10.71 | -100.0% | +170.3% |
| Busan | 2 | 2 | +50.0% | 0.38 | +42.9% | $+4.29 |  |  |
| Seattle | 2 | 2 | +50.0% | 0.36 | +31.6% | $+3.16 |  |  |
| Chengdu | 2 | 2 | +50.0% | 0.44 | +28.2% | $+2.82 |  |  |
| Manila | 2 | 2 | +50.0% | 0.41 | +25.0% | $+2.50 |  |  |
| NYC | 2 | 2 | +50.0% | 0.40 | +9.4% | $+0.94 |  |  |
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
