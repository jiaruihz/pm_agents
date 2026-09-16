# Forecast Quality Live Candidate v0

> generated_at_utc: `2026-06-13T02:41:39.973414+00:00`
> scope: local research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- DB last_modified_utc: `2026-06-13T02:33:01.796274+00:00`
- fact_trades MAX(fact_built_at_utc): `2026-06-13T02:32:52.534281+00:00`
- fact_signal_candidates MAX(fact_built_at_utc): `2026-06-13T02:33:01.564137+00:00`
- CLOB coverage gate: `False`; fail_reasons: `cache_fills_missing_or_mismatched_order_id:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl, cache_fills_exceed_order_cap:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl, db_cache_fill_id_mismatch, db_cache_cost_mismatch, db_fills_exceed_order_cap`
- run_stack status: `fact_tables_built_but_exit_nonzero_due_clob_coverage_gate`
- raw settled candidate rows: `2268`; settled quality labels missing: `134`
- quality label rows rebuilt from fact_signal_candidates: `1665`
- orderbook matched: `1336` / `2134` (+62.6%)
- orderbook constraint: `latest orderbook snapshot_ts_utc <= decision_snapshot_ts_utc`
- train: `2026-05-06` -> `2026-05-30`; holdout: `2026-06-01` -> `2026-06-11`

### 5 行 SQL 自检

```text
SELECT MAX(fact_built_at_utc) FROM fact_trades; -> 2026-06-13T02:32:52.534281+00:00
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class; -> [{'trade_class': 'live_real', 'rows': 856}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status; -> [{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4251}]
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates; -> {'rows': 28197, 'eligible': 9583, 'paper_ordered': 3591, 'live_filled': 348}
SELECT o.status, COUNT(*), with_fill FROM orders LEFT JOIN fills ...; -> [{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 962, 'with_fill': 856}]
```

## Final Candidate

```text
ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0
BUY_NO only
model_version = ecmwf
forecast_quality_low = 0
0.40 <= no_cost <= 0.75
no_edge = market_yes_price - model_p_yes >= 0.10
per city + event_date keep only highest no_edge bracket
$5/order; shares = $5 / no_cost
```

这不是裸逐 bucket 策略，而是 forecast quality base + single-leg BUY_NO selector + city-date top1。

## Evaluation

| profile | tr rows | tr dates | tr ROI | tr excess | tr excess CI | ho rows | ho dates | ho ROI | ho excess | ho excess CI | ho top5 removed | ho avg/day | max city-date | min shares @ $5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0 | 58 | 11 | +13.1% | +5.9% | [+0.6%, +11.6%] | 34 | 11 | +16.3% | +6.2% | [-4.7%, +16.0%] | -3.1% | 3.09 | 1 | 6.67 |
| raw_multi_bucket_cost40_75 | 66 | 11 | +9.4% | +6.9% | [+0.5%, +15.1%] | 36 | 11 | +18.7% | +5.0% | [-4.5%, +13.4%] | -0.8% | 3.27 | 2 | 6.67 |
| no_quality_cost40_75 | 163 | 11 | -2.3% | -1.2% | [-4.0%, +2.1%] | 69 | 11 | +7.3% | +4.8% | [-3.5%, +12.1%] | -14.2% | 6.27 | 1 | 6.67 |
| cost25_75 | 63 | 11 | +11.3% | +5.3% | [+0.1%, +11.0%] | 34 | 11 | +16.3% | +6.2% | [-4.7%, +16.0%] | -3.1% | 3.09 | 1 | 6.67 |
| cost55_75 | 53 | 11 | +12.7% | +8.2% | [+1.2%, +15.2%] | 30 | 11 | +12.8% | +2.7% | [-8.0%, +12.9%] | -10.0% | 2.73 | 1 | 6.67 |
| edge08 | 66 | 11 | +8.3% | +1.0% | [-3.7%, +6.2%] | 39 | 11 | +10.5% | +0.4% | [-8.3%, +9.2%] | -7.0% | 3.55 | 1 | 6.67 |
| edge12 | 50 | 11 | +13.1% | +5.8% | [-1.3%, +15.7%] | 29 | 11 | +9.2% | -0.9% | [-16.5%, +11.4%] | -12.8% | 2.64 | 1 | 6.67 |
| medium_plus | 80 | 11 | +4.6% | +0.8% | [-3.2%, +5.3%] | 47 | 11 | +19.1% | +2.9% | [-4.7%, +9.3%] | -0.1% | 4.27 | 1 | 6.76 |
| city_model_reliable | 81 | 11 | -3.5% | -2.9% | [-7.8%, +3.0%] | 34 | 11 | +4.9% | +6.8% | [-4.4%, +18.4%] | -32.8% | 3.09 | 1 | 6.67 |

## Holdout City Contribution ($5/order)

| city | orders | dates | notional | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| Madrid | 3 | 3 | $15.00 | $+14.01 | +93.4% |
| Lucknow | 3 | 3 | $15.00 | $+8.21 | +54.7% |
| Moscow | 2 | 2 | $10.00 | $+6.03 | +60.3% |
| Karachi | 4 | 4 | $20.00 | $+5.72 | +28.6% |
| London | 6 | 6 | $30.00 | $+5.39 | +18.0% |
| Munich | 1 | 1 | $5.00 | $+4.62 | +92.3% |
| Ankara | 1 | 1 | $5.00 | $+3.20 | +63.9% |
| Warsaw | 6 | 6 | $30.00 | $+0.56 | +1.9% |
| Amsterdam | 2 | 2 | $10.00 | $-1.53 | -15.3% |
| Istanbul | 2 | 2 | $10.00 | $-2.86 | -28.6% |
| Jeddah | 1 | 1 | $5.00 | $-5.00 | -100.0% |
| BuenosAires | 3 | 3 | $15.00 | $-7.19 | -47.9% |

## Holdout Date Contribution ($5/order)

| event_date | orders | notional | pnl | roi |
| --- | --- | --- | --- | --- |
| 2026-06-04 | 5 | $25.00 | $+11.43 | +45.7% |
| 2026-06-03 | 4 | $20.00 | $+7.11 | +35.6% |
| 2026-06-02 | 2 | $10.00 | $+6.29 | +62.9% |
| 2026-06-10 | 2 | $10.00 | $+5.57 | +55.7% |
| 2026-06-09 | 2 | $10.00 | $+5.04 | +50.4% |
| 2026-06-07 | 2 | $10.00 | $+3.42 | +34.2% |
| 2026-06-08 | 1 | $5.00 | $+2.94 | +58.7% |
| 2026-06-06 | 4 | $20.00 | $+2.47 | +12.3% |
| 2026-06-01 | 6 | $30.00 | $+1.12 | +3.7% |
| 2026-06-11 | 1 | $5.00 | $-5.00 | -100.0% |
| 2026-06-05 | 5 | $25.00 | $-9.23 | -36.9% |

## Cost / Edge Buckets

| cost bucket | orders | dates | pnl | roi |
| --- | --- | --- | --- | --- |
| 0.55-0.75 | 83 | 22 | $+54.35 | +13.1% |
| 0.40-0.55 | 9 | 8 | $+17.02 | +37.8% |

| edge bucket | orders | dates | pnl | roi |
| --- | --- | --- | --- | --- |
| 0.15-0.25 | 43 | 19 | $+30.41 | +14.1% |
| 0.10-0.15 | 36 | 19 | $+25.52 | +14.2% |
| 0.25+ | 13 | 12 | $+15.43 | +23.7% |

## Fresh Zero-Notional Shadow Candidates

- output: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.md` and `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.csv`
- fresh event_date min: `2026-06-13`
- orders: `3`; active_dates: `1`; avg/day: `3.00`; max daily notional: `$15.00`
- cities: `Amsterdam, Lucknow, Moscow`
- direction: `BUY_NO` only; order_type: `zero_notional_shadow`.

## Minimal RangeRV / Adjacent3 Check

- rule: `ecmwf, forecast_quality_low=0, range_type=adjacent_3, abs(range_edge)>=0.10, city-date top1, time-aligned orderbook`
- train: rows `2`, dates `2`, ROI `+50.4%`, top5 removed `NA`
- holdout: rows `0`, dates `0`, ROI `NA`, top5 removed `NA`
- verdict: `range_rv_not_in_current_live_queue`
- RangeRV 不进当前 live queue，除非后续同 universe 明显打败 BUY_NO candidate。

## Verdict

- verdict: `shadow_only`
- reasons: `train_orderbook_active_dates<12, holdout_excess_ci_crosses_zero, holdout_top5_removed_not_positive`
- significance=FAIL, baseline=PASS, forward=FAIL, conclusion=shadow_only/inconclusive.
- stale rerun曾接近 near-live；fresh rerun降级为 shadow_only，因为 holdout excess CI 跨 0 且 top5 removed 为负。
- 适合进入 zero-notional shadow；不适合直接 $5/order tiny live，因为 train active_dates<12，且 holdout 稳健性不通过。
- 因 CLOB coverage gate=false，本报告不发布 live_real PnL/ROI/rank/curve。

## Files

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.md`
