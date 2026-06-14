# Forecast-Bounded Range RV Live-Standard v1

> generated_at_utc: `2026-06-14T17:22:23.304676+00:00`
> target_metric: `forecast_bounded_range_rv_live_standard_v1`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: live-standard research gate only; no N100/live config changed; no orders placed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` plus time-aligned orderbook snapshots; `fact_trades` only for mandatory self-check/live coverage context.
- DB last_modified: `2026-06-14T17:21:22.141278+00:00`.
- fact_signal_candidates rows: `29317`.
- decision_sets: `262`; expression rows: `1346`; orderbook-matched rows: `666`.
- train: `2026-05-06` -> `2026-05-28` (21 event_dates).
- holdout: `2026-05-29` -> `2026-06-10` (10 event_dates).
- CLOB coverage gate: `True`.

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-14T17:21:10.519431+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "candidate_coverage": {
    "rows": 29317,
    "eligible": 10032,
    "paper_ordered": 3840,
    "live_filled": 348
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## Live-Standard Definition

A candidate must be `default_wu`, selected from orderbook-native edge, and pass: train rows >=30, train dates >=10, holdout rows >=20, holdout dates >=5, ROI/excess CI lower > 0, train/holdout top5-removed ROI > 0, CLOB gate true, and all holdout selected rows must show at least 5 shares at top ask on every leg.

## Best Live-Standard Attempts

| algorithm | filter | family | edge threshold | train rows | train dates | train ROI | train excess | train excess CI | train top5 removed | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | holdout top5 removed | 3 gates | live-standard blockers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `forecast_bounded_w4_cheaper` | `default_wu/exclude_forecast_quality_low` | 14 | 0.07 | 2 | 2 | +16.3% | +24.8% | [+8.7%, +62.9%] | NA | 1 | 1 | +11.9% | +10.5% | [+8.7%, +10.5%] | NA | `PASS/PASS/PASS -> confirmed` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |
| `forecast_bounded_w4_inside_yes` | `default_wu/exclude_forecast_quality_low` | 14 | 0.07 | 2 | 2 | +15.5% | +24.8% | [+9.3%, +61.7%] | NA | 1 | 1 | +11.2% | +10.3% | [+8.2%, +10.3%] | NA | `PASS/PASS/PASS -> confirmed` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |
| `forecast_bounded_w3_inside_yes` | `all/no_filter_diagnostic` | 134 | 0.1 | 39 | 8 | +16.6% | +12.4% | [+5.9%, +20.6%] | -18.4% | 34 | 4 | +18.1% | +8.8% | [-5.8%, +26.4%] | NA | `PASS/PASS/FAIL -> inconclusive` | not_default_wu_generic,train_dates<10,holdout_dates<5,forward_gate_fail |
| `forecast_bounded_w3_inside_yes` | `default_wu/exclude_forecast_quality_low` | 28 | 0.05 | 9 | 4 | +22.5% | +17.3% | [+7.6%, +34.6%] | NA | 6 | 2 | +12.3% | +7.9% | [-6.5%, +26.2%] | NA | `PASS/PASS/FAIL -> inconclusive` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |
| `forecast_bounded_w3_inside_yes` | `default_wu/no_filter` | 98 | 0.05 | 34 | 7 | +17.7% | +12.5% | [+5.4%, +21.3%] | -20.0% | 28 | 4 | +14.7% | +5.8% | [-2.6%, +33.6%] | NA | `PASS/PASS/FAIL -> inconclusive` | train_dates<10,holdout_dates<5,forward_gate_fail,train_top5_removed<=0 |
| `forecast_bounded_w3_cheaper` | `default_wu/exclude_forecast_quality_low` | 28 | 0.05 | 9 | 4 | +17.9% | +13.2% | [+5.8%, +24.6%] | NA | 6 | 2 | +8.1% | +4.7% | [-4.2%, +16.6%] | NA | `PASS/PASS/FAIL -> inconclusive` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |
| `forecast_bounded_w3_cheaper` | `all/no_filter_diagnostic` | 130 | 0.02 | 49 | 8 | +11.1% | +4.3% | [+1.1%, +9.4%] | +0.7% | 50 | 9 | +12.0% | +3.4% | [+0.2%, +7.9%] | +3.3% | `PASS/PASS/PASS -> confirmed` | not_default_wu_generic,train_dates<10,holdout_some_rows_below_5share_top_ask |
| `forecast_bounded_w3_cheaper` | `default_wu/no_filter` | 95 | 0.02 | 38 | 8 | +13.8% | +4.8% | [+0.8%, +10.8%] | -2.4% | 36 | 8 | +11.1% | +3.0% | [+0.5%, +8.5%] | +2.7% | `PASS/PASS/PASS -> confirmed` | train_dates<10,train_top5_removed<=0,holdout_some_rows_below_5share_top_ask |
| `forecast_bounded_w2_cheaper` | `default_wu/no_filter` | 95 | 0.01 | 44 | 7 | +14.3% | +4.8% | [-2.6%, +13.7%] | -8.9% | 42 | 10 | +17.7% | +1.8% | [-0.5%, +4.1%] | -33.2% | `PASS/FAIL/FAIL -> inconclusive` | train_dates<10,baseline_gate_fail,forward_gate_fail,train_top5_removed<=0 |
| `forecast_bounded_w2_cheaper` | `all/no_filter_diagnostic` | 130 | 0.01 | 58 | 9 | +14.7% | +5.9% | [+0.6%, +15.4%] | +2.8% | 56 | 10 | +10.3% | -0.3% | [-2.1%, +1.9%] | -32.1% | `PASS/PASS/FAIL -> inconclusive` | not_default_wu_generic,train_dates<10,forward_gate_fail,holdout_top5_removed<=0 |
| `forecast_bounded_w2_inside_yes` | `all/no_filter_diagnostic` | 134 | 0.01 | 58 | 9 | +23.4% | +12.4% | [+2.8%, +25.9%] | +5.5% | 56 | 10 | +12.8% | -0.7% | [-2.6%, +2.3%] | -32.8% | `PASS/PASS/FAIL -> inconclusive` | not_default_wu_generic,train_dates<10,forward_gate_fail,holdout_top5_removed<=0 |
| `forecast_bounded_w4_cheaper` | `all/no_filter_diagnostic` | 67 | 0.0 | 25 | 4 | -1.3% | +1.2% | [-0.9%, +3.5%] | NA | 17 | 2 | -6.0% | -2.1% | [-4.6%, +3.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | not_default_wu_generic,train_rows<30,train_dates<10,holdout_rows<20 |
| `forecast_bounded_w4_cheaper` | `default_wu/no_filter` | 51 | 0.0 | 20 | 4 | -2.0% | +1.3% | [-1.6%, +9.0%] | NA | 13 | 2 | -6.0% | -2.3% | [-4.5%, +11.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |
| `forecast_bounded_w2_inside_yes` | `default_wu/no_filter` | 98 | 0.05 | 39 | 7 | +16.7% | +7.2% | [-8.8%, +32.8%] | -5.7% | 38 | 9 | +19.3% | -2.4% | [-12.8%, +3.4%] | -46.4% | `PASS/FAIL/FAIL -> inconclusive` | train_dates<10,baseline_gate_fail,forward_gate_fail,train_top5_removed<=0 |
| `forecast_bounded_w4_inside_yes` | `all/no_filter_diagnostic` | 71 | 0.0 | 24 | 3 | -3.5% | +8.9% | [-1.4%, +25.9%] | NA | 17 | 2 | -7.4% | -2.8% | [-5.7%, +3.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | not_default_wu_generic,train_rows<30,train_dates<10,holdout_rows<20 |
| `forecast_bounded_w4_inside_yes` | `default_wu/no_filter` | 54 | 0.0 | 19 | 3 | -4.4% | +10.7% | [-1.7%, +35.8%] | NA | 13 | 2 | -7.9% | -3.3% | [-5.8%, +9.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | train_rows<30,train_dates<10,holdout_rows<20,holdout_dates<5 |

## Capacity Snapshot

The table below is for the top-ranked attempt only.

```json
{
  "algorithm": "forecast_bounded_w4_cheaper",
  "row_filter": "default_wu/exclude_forecast_quality_low",
  "threshold": 0.07,
  "train_capacity": {
    "selected_rows": 2,
    "rows_with_all_legs_ask_size": 2,
    "min_min_ask_size": 5.0,
    "median_min_ask_size": 7.275,
    "rows_with_all_legs_depth_ask_5c": 2,
    "min_depth_ask_5c": 30.82,
    "median_depth_ask_5c": 172.545,
    "max_orderbook_age_minutes": 0.0,
    "median_orderbook_age_minutes": 0.0,
    "share5_capacity_rows": 2
  },
  "holdout_capacity": {
    "selected_rows": 1,
    "rows_with_all_legs_ask_size": 1,
    "min_min_ask_size": 6.5,
    "median_min_ask_size": 6.5,
    "rows_with_all_legs_depth_ask_5c": 1,
    "min_depth_ask_5c": 275.27,
    "median_depth_ask_5c": 275.27,
    "max_orderbook_age_minutes": 0.0,
    "median_orderbook_age_minutes": 0.0,
    "share5_capacity_rows": 1
  }
}
```

## Verdict

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | train_rows<30; train_dates<10; holdout_rows<20; holdout_dates<5; train_top5_removed<=0; holdout_top5_removed<=0 |
| baseline | FAIL | train_rows<30; train_dates<10; holdout_rows<20; holdout_dates<5; train_top5_removed<=0; holdout_top5_removed<=0 |
| forward | FAIL | train_rows<30; train_dates<10; holdout_rows<20; holdout_dates<5; train_top5_removed<=0; holdout_top5_removed<=0 |
| live_standard | FAIL | train_rows<30; train_dates<10; holdout_rows<20; holdout_dates<5; train_top5_removed<=0; holdout_top5_removed<=0 |

`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive`.

Plain-English conclusion: this stricter run did not produce a live-standard Range RV candidate. The right next step is not live deployment; it is forward shadow telemetry for the closest default-WU width-3 orderbook-native family, or waiting for more settled forward samples.
