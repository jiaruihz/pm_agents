# Low-Price YES Reheat Reversal v0

> generated_at_utc: `2026-06-16T15:56:45.286660+00:00`
> Scope: research/shadow candidate only; no N100/live behavior changed.

## 交易结论

这版把低价 BUY_YES 正式改成 `low_price_yes_reheat_reversal`：row grain 是 intraday city-hour target YES quote，label 是 `target_yes_wins`。它不是 current YES/no-reheat，也不和 higher NO carry 合并 PnL。

v0 结论是：这个方向可以继续做 shadow/research，但还没有 live 版本。原因是 reheat-adjusted edge 在 holdout 有筛选作用但样本仍薄，且本版只是 bridge 到旧 observed-max materializer，还不是最终共享 `reheat_feature_factory`。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` forecast prior + raw orderbook snapshots + observed running-max materializer.
- fact_trades MAX built: `2026-06-16T15:50:05.971834+00:00`
- CLOB gate: `True`; 本报告不发布 live_real PnL/ROI。

### 强制 5 行 SQL 自检

```json
{
  "fact_trades_max_built_at_utc": "2026-06-16T15:50:05.971834+00:00",
  "fact_trades_by_class": [
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
  "fact_trades_by_settlement_status": [
    {
      "settlement_status": "",
      "rows": 90
    },
    {
      "settlement_status": "settled",
      "rows": 4310
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 30919,
    "eligible": 10685,
    "paper_ordered": 4123,
    "live_filled": 348
  },
  "clob_order_fill_join": [
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

## Target Metric

`low_price_yes_reheat_reversal_v0` = 在已经有 intraday observed running max 的 city-hour 状态下，买 `best_ask<=0.25` 且 target bracket 高于当前 running value 的 YES。目标标签是 `target_yes_wins`。

概率对照：

- raw edge = `raw_model_p_yes - low_price_yes_ask`。
- blended edge = `(0.30 * raw_model_p_yes + 0.70 * market_ask) - low_price_yes_ask`。
- reheat-adjusted edge = `(raw_model_p_yes * p_reheat_context) - low_price_yes_ask`。

## Coverage

```json
{
  "source_aligned_whitelist_cities": 36,
  "pm_history_city_dates_loaded": 1266,
  "observed_rows": 4750,
  "joined_quote_rows": 25703,
  "paired_tail_no_rows": 3209,
  "date_min": "2026-05-20",
  "date_max": "2026-06-14",
  "active_dates": 26,
  "orderbook_files_seen": 1289,
  "orderbook_records_seen": 1630457,
  "orderbook_records_kept_before_hourly_dedupe": 60912,
  "quote_rows_after_hourly_dedupe": 33481,
  "records_missing_history": 29846,
  "records_missing_tz": 0,
  "records_wrong_day_hour": 1465732,
  "records_no_best_ask": 38122,
  "records_bad_bracket": 0,
  "raw_orderbook_files_seen": 1289,
  "raw_orderbook_records_seen": 1630457,
  "raw_orderbook_records_kept_before_hourly_dedupe": 60912,
  "raw_quote_rows_after_hourly_dedupe": 33481,
  "raw_records_missing_history": 29846,
  "raw_records_missing_tz": 0,
  "raw_records_wrong_day_hour": 1465732,
  "raw_records_no_best_ask": 38122,
  "raw_records_bad_bracket": 0,
  "low_price_yes_reheat_rows_with_prior": 4347,
  "cities": 36,
  "hit_rate": 0.03036576949620428,
  "prior_coverage_note": "forecast prior joined from fact_signal_candidates by city/date/bracket"
}
```

## Model Metrics

```json
{
  "train": {
    "rows": 2030,
    "positives": 62,
    "hit_rate": 0.030541871921182268,
    "auc_context": 0.950088512981904,
    "brier_context": 0.09518165518315701
  },
  "holdout": {
    "rows": 2317,
    "positives": 70,
    "hit_rate": 0.030211480362537766,
    "auc_context": 0.8175344904316867,
    "brier_context": 0.08978627893456793
  }
}
```

## Edge Comparison

| selector | rows | active_dates | cities | hit_rate | avg_price | cost_proxy | pnl_proxy | roi_proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train:all_low_price_reheat_yes | 2030 | 12 | 36 | +3.1% | +4.5% | +91.38 | -29.38 | -32.2% |
| train:raw_edge_gt_0 | 1886 | 12 | 36 | +2.9% | +4.1% | +77.98 | -23.98 | -30.8% |
| train:blended_edge_gt_0 | 1886 | 12 | 36 | +2.9% | +4.1% | +77.98 | -23.98 | -30.8% |
| train:reheat_adjusted_edge_gt_0 | 527 | 12 | 33 | +9.5% | +7.0% | +36.77 | +13.23 | +36.0% |
| train:reheat_adjusted_edge_top_decile | 203 | 12 | 25 | +20.2% | +11.1% | +22.49 | +18.51 | +82.3% |
| holdout:all_low_price_reheat_yes | 2317 | 14 | 36 | +3.0% | +4.1% | +94.18 | -24.18 | -25.7% |
| holdout:raw_edge_gt_0 | 2167 | 14 | 36 | +3.0% | +3.8% | +81.85 | -16.85 | -20.6% |
| holdout:blended_edge_gt_0 | 2167 | 14 | 36 | +3.0% | +3.8% | +81.85 | -16.85 | -20.6% |
| holdout:reheat_adjusted_edge_gt_0 | 515 | 14 | 33 | +7.4% | +6.7% | +34.70 | +3.30 | +9.5% |
| holdout:reheat_adjusted_edge_top_decile | 232 | 14 | 30 | +12.1% | +10.1% | +23.46 | +4.54 | +19.4% |

## 当前动作

`shadow/research only`。下一步应该把这个 v0 bridge 迁到正式 `reheat_feature_factory`，再做 walk-forward / event_date bootstrap / top-k removal，而不是直接 live。
