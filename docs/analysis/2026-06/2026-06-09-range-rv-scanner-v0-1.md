# Range RV Scanner v0

> generated_at_utc: `2026-06-08T17:04:55.531720+00:00`
> target_metric: `city_day_range_relative_value_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` primary; `fact_trades` only for mandatory freshness/status self-check.
- DB last_modified: `2026-06-08T17:00:43.284789+00:00`
- fact built at: `2026-06-08T01:32:49.788100+00:00`
- scanner input candidates: `2488` seen-complete rows; decision sets `635`; range rows `6245`; all-legs-eligible range rows `2219`.
- unsettled used in scanner: `0` (filter `settlement_status='settled'` and `final_yes IS NOT NULL`).
- missing_bracket used in scanner: `0`.
- local cache note: user requested local `runtime/weather.db`; no N100/live sync or config change was run.

## Target Metric

`city_day_range_relative_value_alpha` = train-selected city-day range YES/NO basket ROI minus matched baseline ROI, where the range is enumerated inside one city-day decision snapshot and model edge is `SUM(model_p_yes) - SUM(market_yes_price)`.

Ranges enumerated: single bracket, adjacent 2, adjacent 3, below-tail, above-tail. Positive range edge buys YES across the range; negative range edge buys NO across the range. Shape tags include `single_bracket`, `inside_range_yes`, `outside_range_no`, `below_tail`, and `above_tail`.

## Gates

| gate | status |
|---|---|
| `significance` | `PASS` |
| `baseline` | `PASS` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. This report gives no live action.

## Train / Holdout

- Split field: `event_date`.
- Train dates: `2026-05-06` to `2026-05-28`.
- Holdout dates: `2026-05-29` to `2026-06-06`.
- Rule grid: `range_type x direction x abs(range_edge) threshold`; K tested on train = `50`.

## Selected Train Rule

`single / long_range / abs_range_edge >= 0.2`

| window | rows | dates | taker cost | settled pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 176 | 21 | 26.84 | +13.16 | +49.0% | [+7.1%, +89.4%] | +4.8% | +44.2% | [+3.8%, +82.2%] | +6.4% | -1.00 | +47.7% |
| holdout | 54 | 8 | 9.14 | +3.86 | +42.2% | [-35.5%, +116.5%] | +7.1% | +35.1% | [-22.3%, +114.3%] | -67.8% | -1.00 | +43.8% |

## Follow-up Families

These experiments keep single-leg opportunity visible, but evaluate it separately from true range RV. Each family still selects only on train and only verifies on holdout.

### Seen Complete Universe

| family | train rows | holdout rows | selected train rule | train ROI | train excess | train top5 removed ROI | holdout ROI | holdout excess | holdout top5 removed ROI | gates |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `mixed_single_allowed` | 4755 | 1490 | `single/long_range/edge>=0.2` | +49.0% | +44.2% | +6.4% | +42.2% | +35.1% | -67.8% | `PASS/PASS/FAIL -> inconclusive` |
| `single_only` | 1423 | 461 | `single/long_range/edge>=0.2` | +49.0% | +44.2% | +6.4% | +42.2% | +35.1% | -67.8% | `PASS/PASS/FAIL -> inconclusive` |
| `true_range_only` | 3332 | 1029 | `below_tail/long_range/edge>=0.2` | +6.1% | +7.6% | -22.7% | +3.7% | +8.1% | -19.1% | `FAIL/FAIL/FAIL -> inconclusive` |
| `adjacent_only` | 1430 | 433 | `adjacent_3/long_range/edge>=0.15` | +10.5% | +4.3% | -4.4% | +12.6% | -4.9% | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `tail_only` | 1902 | 596 | `below_tail/long_range/edge>=0.2` | +6.1% | +7.6% | -22.7% | +3.7% | +8.1% | -19.1% | `FAIL/FAIL/FAIL -> inconclusive` |
| `inside_range_yes` | 573 | 175 | `adjacent_3/long_range/edge>=0.15` | +10.5% | +4.3% | -4.4% | +12.6% | -4.9% | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `outside_range_no` | 2000 | 602 | `adjacent_3/short_range/edge>=0.2` | +4.2% | +2.1% | -1.7% | -4.3% | -2.8% | NA | `FAIL/FAIL/FAIL -> inconclusive` |

### Eligible-only Universe

| family | train rows | holdout rows | selected train rule | train ROI | train excess | train top5 removed ROI | holdout ROI | holdout excess | holdout top5 removed ROI | gates |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `mixed_single_allowed` | 1165 | 1054 | `single/long_range/edge>=0.2` | +64.7% | +55.5% | -3.8% | +58.2% | +48.4% | -61.5% | `PASS/PASS/FAIL -> inconclusive` |
| `single_only` | 370 | 341 | `single/long_range/edge>=0.2` | +64.7% | +55.5% | -3.8% | +58.2% | +48.4% | -61.5% | `PASS/PASS/FAIL -> inconclusive` |
| `true_range_only` | 795 | 713 | `adjacent_3/long_range/edge>=0.15` | +24.8% | +14.0% | +1.4% | +29.0% | +3.5% | NA | `PASS/PASS/FAIL -> inconclusive` |
| `adjacent_only` | 330 | 291 | `adjacent_3/long_range/edge>=0.15` | +24.8% | +14.0% | +1.4% | +29.0% | +3.5% | NA | `PASS/PASS/FAIL -> inconclusive` |
| `tail_only` | 465 | 422 | `below_tail/long_range/edge>=0.2` | +12.3% | +10.7% | -65.8% | +8.3% | +13.8% | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` |
| `inside_range_yes` | 138 | 115 | `adjacent_3/long_range/edge>=0.15` | +24.8% | +14.0% | +1.4% | +29.0% | +3.5% | NA | `PASS/PASS/FAIL -> inconclusive` |
| `outside_range_no` | 468 | 426 | `above_tail/short_range/edge>=0.2` | +12.0% | +8.1% | -0.1% | -4.6% | +0.1% | -9.5% | `PASS/PASS/FAIL -> inconclusive` |

## Range Type Background

| range_type | rows | dates | taker cost | settled pnl | ROI | avg abs edge | top5 removed ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `adjacent_2` | 1249 | 30 | 1250.00 | +52.00 | +4.2% | +25.8% | +1.0% |
| `above_tail` | 1249 | 30 | 1213.23 | +51.77 | +4.3% | +24.1% | +1.6% |
| `below_tail` | 1249 | 30 | 1352.99 | +23.01 | +1.7% | +23.0% | -2.0% |
| `adjacent_3` | 614 | 29 | 967.66 | +22.34 | +2.3% | +26.0% | -0.5% |
| `single` | 1884 | 30 | 929.53 | +18.47 | +2.0% | +17.4% | -1.8% |

## Shape Background

| shape | rows | dates | settled pnl | ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|
| `outside_range_no` | 2602 | 30 | +91.99 | +2.2% | -0.71 | +38.8% |
| `inside_range_yes` | 748 | 30 | +39.88 | +11.6% | -1.77 | +27.3% |
| `above_tail` | 543 | 30 | +20.53 | +10.6% | -2.06 | +37.7% |
| `single_bracket` | 1884 | 30 | +18.47 | +2.0% | -1.00 | +37.1% |
| `below_tail` | 468 | 30 | -3.28 | -2.3% | -1.93 | +40.5% |

## Orderbook Executable Subset

Orderbook prices are matched with the same constraint used by executable-edge research: latest `snapshot_ts_utc <= decision_snapshot_ts_utc`. This subset is a capacity/execution check, not a replacement for the full decision proxy.

- Leg coverage: `6540` / `11220`.
- Fully matched ranges: `3548` / `6245`.

| window | rows | dates | taker cost | settled pnl | ROI | baseline ROI | excess ROI | top5 removed ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 67 | 9 | 13.31 | +0.69 | +5.2% | -1.8% | +6.9% | -53.1% |
| holdout | 54 | 8 | 10.16 | +2.84 | +28.0% | -1.4% | +29.3% | -70.6% |

## 8 环覆盖自检

| 环 | 覆盖 | 说明 |
|---|---|---|
| 1 描述性绩效切片 | yes | range-level settled counterfactual from fact_signal_candidates |
| 2 统计推断 | yes | event_date cluster bootstrap |
| 3 信号判别 | partial | range_edge train selection, no independent model-rank proof |
| 4 概率分布评估 | partial | range probability sums only, no full calibration model |
| 5 执行微结构 | partial | time-aligned orderbook subset plus decision proxy |
| 6 容量 | no | no size/depth capacity sweep |
| 7 组合相关性 | partial | event_date cluster bootstrap, no cross-city rho model |
| 8 基准/反事实 | yes | matched range_type+direction baseline and excess ROI |

## Notes

- Baseline is matched by `range_type + direction` in the same train/holdout window, before applying the train-selected edge threshold.
- `settled_pnl` is a research counterfactual at range-row grain; it is not live_real PnL.
- Any failed gate means `inconclusive`; this scanner must not be used to change live sizing, city pools, or N100 config.
