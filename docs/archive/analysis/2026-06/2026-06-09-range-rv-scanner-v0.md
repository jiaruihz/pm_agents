# Range RV Scanner v0

> generated_at_utc: `2026-06-08T16:50:07.793489+00:00`
> target_metric: `city_day_range_relative_value_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` primary; `fact_trades` only for mandatory freshness/status self-check.
- DB last_modified: `2026-06-08T01:32:53.116501+00:00`
- fact built at: `2026-06-08T01:32:49.788100+00:00`
- scanner input candidates: `2212` rows; decision sets `635`; range rows `6240`.
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
| train | 170 | 21 | 25.70 | +13.30 | +51.7% | [+10.3%, +94.3%] | +6.9% | +44.8% | [+3.4%, +84.9%] | +9.6% | -1.00 | +47.1% |
| holdout | 52 | 8 | 8.82 | +2.18 | +24.8% | [-50.1%, +83.8%] | -0.2% | +25.0% | [-26.7%, +67.6%] | -100.0% | -1.00 | +43.5% |

## Range Type Background

| range_type | rows | dates | taker cost | settled pnl | ROI | avg abs edge | top5 removed ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `above_tail` | 1248 | 30 | 1219.75 | +51.25 | +4.2% | +23.4% | +1.5% |
| `adjacent_2` | 1248 | 30 | 1257.19 | +48.81 | +3.9% | +25.3% | +0.9% |
| `adjacent_3` | 613 | 29 | 968.37 | +23.64 | +2.4% | +25.2% | -0.1% |
| `below_tail` | 1248 | 30 | 1359.10 | +18.90 | +1.4% | +22.4% | -2.0% |
| `single` | 1883 | 30 | 937.06 | +15.94 | +1.7% | +17.0% | -2.5% |

## Shape Background

| shape | rows | dates | settled pnl | ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|
| `outside_range_no` | 2604 | 30 | +81.34 | +2.0% | -0.71 | +39.1% |
| `inside_range_yes` | 744 | 30 | +41.78 | +12.2% | -1.78 | +27.1% |
| `above_tail` | 541 | 30 | +22.44 | +11.5% | -2.05 | +37.5% |
| `single_bracket` | 1883 | 30 | +15.94 | +1.7% | -1.00 | +36.8% |
| `below_tail` | 468 | 30 | -2.97 | -2.0% | -1.93 | +40.0% |

## Orderbook Executable Subset

Orderbook prices are matched with the same constraint used by executable-edge research: latest `snapshot_ts_utc <= decision_snapshot_ts_utc`. This subset is a capacity/execution check, not a replacement for the full decision proxy.

- Leg coverage: `6524` / `11204`.
- Fully matched ranges: `3543` / `6240`.

| window | rows | dates | taker cost | settled pnl | ROI | baseline ROI | excess ROI | top5 removed ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 114 | 9 | 38.96 | +2.04 | +5.2% | -0.3% | +5.6% | -13.3% |
| holdout | 109 | 9 | 32.16 | -6.16 | -19.2% | -13.9% | -5.3% | -87.0% |

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
