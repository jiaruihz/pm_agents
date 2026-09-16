# Current-YES No-Reheat State Slices v1

Status: research-only
Generated: 2026-06-23T15:47:07+00:00

Target metric: fixed `no_reheat_state_slice_v1` asks whether observable current-YES states, not a trained probability model, identify market-lag YES opportunities.

## 一句话结论

没有找到可直接 promotion 的 market-lag no-reheat YES 切片；最好的点估计集中在低/中 ask 的 stalled/fade 状态，但 CI 或 baseline excess 不过门。

## 数据快照

- Feature rows: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv`.
- Feature target-date range: `2026-05-19`..`2026-06-20`.
- Current-YES rows: 8907 rows / 33 dates / 36 cities.
- Holdout window: `2026-06-01`..`2026-06-20`.
- CLOB fill coverage gate: `True`.

注意：这是 opportunity/orderbook replay + settlement label，不是 live_real PnL。它用于找状态切片，不改变 live。

## 数据完整性自检

```json
{
  "fact_trades_freshness": {
    "rows": 4400,
    "max_order_ts_utc": "2026-06-23T15:41:42Z",
    "max_fill_ts_utc": "2026-06-11T09:59:21+00:00",
    "max_target_date": "2026-06-11",
    "max_fact_built_at_utc": "2026-06-23T15:41:51.782120+00:00"
  },
  "fact_trades_by_class": [
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    }
  ],
  "fact_trades_by_settlement": [
    {
      "settlement_status": "settled",
      "rows": 4250
    },
    {
      "settlement_status": "",
      "rows": 150
    }
  ],
  "fact_signal_candidates": {
    "rows": 36282,
    "min_event_date": "2026-05-05",
    "max_event_date": "2026-06-25",
    "max_fact_built_at_utc": "2026-06-23T15:42:11.631926+00:00"
  },
  "settlement_outcomes": {
    "rows": 21960,
    "min_target_date": "2026-05-04",
    "max_target_date": "2026-06-22"
  },
  "orders_fills": {
    "fact_rows": 4400,
    "distinct_orders": 4043,
    "positive_cost_rows": 4400
  },
  "clob_fill_coverage_gate": {
    "gate_pass": true,
    "fail_reasons": [],
    "db_fill_cost_minus_fact_cost": 0.0
  }
}
```

## Best Holdout Slices

| state | ask bucket | rows | dates | win | ROI | CI | baseline ROI | excess | excess CI | avg ask |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stalled_high_ge2obs | ask_35_50 | 72 | 20 | +47.2% | +12.1% | [-17.3%, +41.8%] | -1.6% | +13.7% | [-4.1%, +31.6%] | 0.421 |
| stalled_high_ge30m | ask_35_50 | 72 | 20 | +47.2% | +12.1% | [-17.3%, +41.8%] | -1.6% | +13.7% | [-4.1%, +31.6%] | 0.421 |
| stalled_no_warming | ask_60_70 | 32 | 16 | +71.9% | +12.0% | [-16.4%, +36.3%] | -5.0% | +17.0% | [-4.3%, +33.7%] | 0.642 |
| fade_all_decline_ge_0_5 | ask_60_70 | 31 | 15 | +71.0% | +9.3% | [-17.6%, +31.2%] | -5.0% | +14.3% | [-14.7%, +40.0%] | 0.649 |
| after_peak_stalled | ask_35_50 | 68 | 20 | +45.6% | +8.4% | [-19.8%, +37.9%] | -1.6% | +10.0% | [-8.6%, +28.8%] | 0.421 |
| fade_all_decline_ge_0_5 | ask_70_80 | 61 | 18 | +80.3% | +7.4% | [-17.5%, +24.4%] | +0.7% | +6.7% | [-15.7%, +22.8%] | 0.748 |
| stalled_high_ge2obs | ask_60_70 | 50 | 19 | +68.0% | +4.8% | [-21.3%, +26.2%] | -5.0% | +9.8% | [-7.8%, +23.6%] | 0.649 |
| stalled_high_ge30m | ask_60_70 | 50 | 19 | +68.0% | +4.8% | [-21.3%, +26.2%] | -5.0% | +9.8% | [-7.8%, +23.6%] | 0.649 |
| stalled_no_warming | ask_90_97 | 101 | 20 | +97.0% | +3.2% | [-0.6%, +6.4%] | -1.0% | +4.3% | [+1.8%, +6.5%] | 0.940 |
| early_false_fade_risk | ask_80_90 | 39 | 18 | +87.2% | +2.7% | [-14.1%, +15.3%] | -8.7% | +11.4% | [-4.0%, +24.4%] | 0.849 |
| fresh_high_unconfirmed | ask_50_60 | 94 | 20 | +55.3% | +1.6% | [-16.0%, +18.6%] | +0.6% | +1.1% | [-11.5%, +14.2%] | 0.544 |
| after_peak_stalled | ask_60_70 | 44 | 18 | +65.9% | +1.5% | [-25.8%, +26.0%] | -5.0% | +6.5% | [-12.2%, +22.4%] | 0.649 |

筛选说明：表里只展示 holdout 中 `rows>=30`、`dates>=8` 的状态×价格桶，按 ROI 和 excess 排序。baseline 是同一 ask bucket 的全部 current-YES tradable 行。

## State Summary

| state | rows | dates | win | ROI | CI | baseline ROI | excess | future break | avg ask |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all_current_yes_tradable | 1575 | 20 | +71.4% | -3.1% | [-9.1%, +2.5%] | -3.1% | +0.0% | +28.6% | 0.737 |
| fresh_high_unconfirmed | 838 | 20 | +68.0% | -4.5% | [-9.8%, +0.9%] | -3.1% | -1.4% | +32.0% | 0.712 |
| stalled_high_ge2obs | 493 | 20 | +73.6% | -2.2% | [-9.7%, +4.7%] | -3.1% | +0.9% | +26.4% | 0.753 |
| stalled_high_ge30m | 493 | 20 | +73.6% | -2.2% | [-9.7%, +4.7%] | -3.1% | +0.9% | +26.4% | 0.753 |
| after_peak_stalled | 371 | 20 | +68.2% | -5.3% | [-13.6%, +2.8%] | -3.1% | -2.2% | +31.8% | 0.720 |
| late_after_peak_stalled | 147 | 20 | +69.4% | -12.8% | [-25.5%, -1.6%] | -3.1% | -9.8% | +30.6% | 0.796 |
| stalled_no_warming | 336 | 20 | +75.0% | -3.3% | [-12.0%, +4.1%] | -3.1% | -0.2% | +25.0% | 0.775 |
| strict_no_reheat_candidate | 73 | 20 | +76.7% | -5.8% | [-14.9%, +3.3%] | -3.1% | -2.7% | +23.3% | 0.814 |
| fade_all_decline_ge_0_5 | 378 | 20 | +76.5% | -2.2% | [-11.7%, +5.8%] | -3.1% | +0.9% | +23.5% | 0.782 |
| mature_fade_candidate | 19 | 14 | +73.7% | -12.5% | [-41.0%, +10.1%] | -3.1% | -9.4% | +26.3% | 0.842 |
| early_false_fade_risk | 161 | 20 | +73.3% | -0.1% | [-14.4%, +12.9%] | -3.1% | +3.0% | +26.7% | 0.733 |

## Verdict

significance=FAIL / baseline=FAIL / forward=NA / conclusion=inconclusive

固定 no-reheat 状态切片在 holdout 上没有同时通过 ROI CI 和 same-price baseline excess CI。当前可以用这些状态做 telemetry/shadow 分层，但不能证明某个封顶切片已经可 live。

## 8-Ring Coverage

- Covered: fixed state slicing, price buckets, same-price baseline, date-cluster bootstrap, future-break labels, target-date correlation.
- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask, full settled labels after the feature layer max date.

## Outputs

- slice summary CSV: `docs/analysis/2026-06/generated/current_yes_no_reheat_state_slices_v1/no_reheat_state_slice_summary.csv`
- state rows CSV: `docs/analysis/2026-06/generated/current_yes_no_reheat_state_slices_v1/holdout_state_rows.csv`
- json: `docs/analysis/2026-06/2026-06-23-current-yes-no-reheat-state-slices-v1.json`
