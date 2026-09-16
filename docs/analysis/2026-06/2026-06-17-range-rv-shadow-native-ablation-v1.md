# Range RV Shadow-Native Ablation v1

> generated_at_utc: `2026-06-17T15:58:25.982481+00:00`
> target_metric: `forecast_bounded_range_rv_shadow_native_ablation_v1`
> strategy_id: `forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0`

## Data Snapshot

- Data source: `runtime/weather.db` for historical decision-set replay; synced N100 zero-notional shadow journal plus `pm_history` for forward shadow telemetry.
- Evidence layer: historical orderbook-native replay + shadow/counterfactual telemetry. This is not live PnL and not a live edge claim.
- journal_rows: `1947`; event_dates: `2026-06-14, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18`.
- shadow source_buckets: `{"default_wu": 1947}`.
- all_no_order_placed: `True`; all_zero_notional_shadow: `True`.

### Mandatory SQL Self-Check

```json
{
  "candidate_coverage": {
    "eligible": 10961,
    "live_filled": 348,
    "paper_ordered": 4274,
    "rows": 31496
  },
  "max_fact_built_at_utc": "2026-06-17T15:49:12.423102+00:00",
  "order_fill_coverage": [
    {
      "orders": 33,
      "status": "error",
      "with_fill": 0
    },
    {
      "orders": 961,
      "status": "submitted",
      "with_fill": 855
    }
  ],
  "settlement_status_distribution": [
    {
      "rows": 150,
      "settlement_status": ""
    },
    {
      "rows": 4250,
      "settlement_status": "settled"
    }
  ],
  "trade_class_distribution": [
    {
      "rows": 855,
      "trade_class": "live_real"
    },
    {
      "rows": 624,
      "trade_class": "live_simulated"
    },
    {
      "rows": 2285,
      "trade_class": "paper"
    },
    {
      "rows": 636,
      "trade_class": "snapshot_replay"
    }
  ]
}
```

## Source/Quality Required Lines

These are historical `forecast_bounded_w3_cheaper` rows with `orderbook_edge>=0.02`, shown to keep the source-aware base visible. Forward shadow currently writes only `default_wu/no_filter`, so forecast-quality-low is not available in the journal yet.

| scope | rows | groups | dates | cities | gross_cost | eff_cost | pnl | gross_roi | eff_roi | win_rate | avg_win | avg_loss | avg_eff | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_filter_all_sources | 99 | 97 | 17 | 41 | 85.156 | 71.156 | +9.844 | +11.6% | +13.8% | +81.8% | +0.217 | -0.428 | 0.719 | 0.234 |
| exclude_forecast_quality_low | 24 | 24 | 9 | 14 | 23.508 | 19.508 | +1.492 | +6.3% | +7.6% | +87.5% | +0.166 | -0.667 | 0.813 | 0.165 |
| source_bucket_default_wu | 74 | 73 | 16 | 32 | 63.120 | 54.120 | +7.880 | +12.5% | +14.6% | +83.8% | +0.217 | -0.464 | 0.731 | 0.220 |

## Main Variant Summary

Historical rows use the train/holdout split from the forecast-quality base. Forward rows use the latest row that would have been eligible under each fixed variant at `city + event_date + forecast_source + model_version`; only settled event dates contribute PnL.

| cohort | dedup | variant | rows | groups | dates | cities | gross_cost | eff_cost | pnl | gross_roi | eff_roi | win_rate | avg_win | avg_loss | avg_eff | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_train | none | current_all | 38 | 38 | 8 | 25 | 32.524 | 28.524 | +4.476 | +13.8% | +15.7% | +86.8% | +0.209 | -0.487 | 0.751 | 0.206 |
| historical_train | none | current_no_low_cost_gt025 | 37 | 37 | 8 | 25 | 32.296 | 28.296 | +4.704 | +14.6% | +16.6% | +89.2% | +0.209 | -0.551 | 0.765 | 0.198 |
| historical_train | none | current_mid_cost_050_075 | 12 | 12 | 3 | 9 | 9.937 | 7.937 | +3.063 | +30.8% | +38.6% | +91.7% | +0.326 | -0.518 | 0.661 | 0.285 |
| historical_train | none | current_cap075_gt025 | 15 | 15 | 3 | 12 | 12.184 | 9.184 | +2.816 | +23.1% | +30.7% | +80.0% | +0.340 | -0.423 | 0.612 | 0.313 |
| historical_train | none | current_cap080_gt025 | 20 | 20 | 4 | 15 | 16.108 | 13.108 | +3.892 | +24.2% | +29.7% | +85.0% | +0.304 | -0.423 | 0.655 | 0.283 |
| historical_train | none | current_inside_expression_only | 34 | 34 | 8 | 23 | 25.835 | 25.835 | +4.165 | +16.1% | +16.1% | +88.2% | +0.205 | -0.495 | 0.760 | 0.200 |
| historical_train | none | current_outside_expression_only_diag | 4 | 4 | 3 | 4 | 6.689 | 2.689 | +0.311 | +4.6% | +11.6% | +75.0% | +0.254 | -0.452 | 0.672 | 0.259 |
| historical_train | none | inside_same_decisions_all | 38 | 38 | 8 | 25 | 28.669 | 28.669 | +4.331 | +15.1% | +15.1% | +86.8% | +0.206 | -0.496 | 0.754 | 0.202 |
| historical_train | none | inside_native_edge002 | 39 | 39 | 8 | 26 | 29.399 | 29.399 | +3.601 | +12.2% | +12.2% | +84.6% | +0.206 | -0.535 | 0.754 | 0.203 |
| historical_train | none | inside_native_edge002_mid_cost_050_075 | 13 | 13 | 4 | 10 | 8.755 | 8.755 | +2.245 | +25.6% | +25.6% | +84.6% | +0.318 | -0.624 | 0.673 | 0.275 |
| historical_train | none | inside_native_edge002_cap080_gt025 | 21 | 21 | 5 | 16 | 13.974 | 13.974 | +3.026 | +21.7% | +21.7% | +81.0% | +0.298 | -0.511 | 0.665 | 0.274 |
| historical_holdout | none | current_all | 36 | 35 | 8 | 22 | 30.596 | 25.596 | +3.404 | +11.1% | +13.3% | +80.6% | +0.225 | -0.447 | 0.711 | 0.234 |
| historical_holdout | none | current_no_low_cost_gt025 | 35 | 35 | 8 | 22 | 30.479 | 25.479 | +3.521 | +11.6% | +13.8% | +82.9% | +0.225 | -0.502 | 0.728 | 0.216 |
| historical_holdout | none | current_mid_cost_050_075 | 15 | 15 | 3 | 12 | 13.789 | 9.789 | +1.211 | +8.8% | +12.4% | +73.3% | +0.322 | -0.583 | 0.653 | 0.286 |
| historical_holdout | none | current_cap075_gt025 | 20 | 20 | 3 | 16 | 15.879 | 11.879 | +2.121 | +13.4% | +17.9% | +70.0% | +0.367 | -0.502 | 0.594 | 0.317 |
| historical_holdout | none | current_cap080_gt025 | 22 | 22 | 3 | 18 | 18.429 | 13.429 | +2.571 | +14.0% | +19.1% | +72.7% | +0.349 | -0.502 | 0.610 | 0.303 |
| historical_holdout | none | current_inside_expression_only | 31 | 30 | 8 | 19 | 22.375 | 22.375 | +3.625 | +16.2% | +16.2% | +83.9% | +0.218 | -0.407 | 0.722 | 0.229 |
| historical_holdout | none | current_outside_expression_only_diag | 5 | 5 | 2 | 5 | 8.221 | 3.221 | -0.221 | -2.7% | -6.9% | +60.0% | +0.292 | -0.548 | 0.644 | 0.267 |
| historical_holdout | none | inside_same_decisions_all | 36 | 35 | 8 | 22 | 25.726 | 25.726 | +3.274 | +12.7% | +12.7% | +80.6% | +0.222 | -0.452 | 0.715 | 0.230 |
| historical_holdout | none | inside_native_edge002 | 36 | 35 | 8 | 22 | 25.726 | 25.726 | +3.274 | +12.7% | +12.7% | +80.6% | +0.222 | -0.452 | 0.715 | 0.230 |
| historical_holdout | none | inside_native_edge002_mid_cost_050_075 | 13 | 13 | 3 | 11 | 8.661 | 8.661 | +1.339 | +15.5% | +15.5% | +76.9% | +0.322 | -0.625 | 0.666 | 0.272 |
| historical_holdout | none | inside_native_edge002_cap080_gt025 | 22 | 22 | 3 | 18 | 13.559 | 13.559 | +2.441 | +18.0% | +18.0% | +72.7% | +0.343 | -0.508 | 0.616 | 0.297 |
| forward_settled | latest | current_all | 34 | 34 | 3 | 17 | 53.751 | 22.751 | -1.751 | -3.3% | -7.7% | +61.8% | +0.202 | -0.460 | 0.669 | 0.230 |
| forward_settled | latest | current_no_low_cost_gt025 | 34 | 34 | 3 | 17 | 58.479 | 24.479 | -3.479 | -5.9% | -14.2% | +61.8% | +0.202 | -0.593 | 0.720 | 0.169 |
| forward_settled | latest | current_mid_cost_050_075 | 29 | 29 | 2 | 16 | 49.477 | 18.477 | -0.477 | -1.0% | -2.6% | +62.1% | +0.358 | -0.629 | 0.637 | 0.252 |
| forward_settled | latest | current_cap075_gt025 | 32 | 32 | 2 | 17 | 51.590 | 18.590 | -0.590 | -1.1% | -3.2% | +56.2% | +0.367 | -0.514 | 0.581 | 0.296 |
| forward_settled | latest | current_cap080_gt025 | 32 | 32 | 2 | 17 | 39.337 | 19.337 | -1.337 | -3.4% | -6.9% | +56.2% | +0.340 | -0.533 | 0.604 | 0.271 |
| forward_settled | latest | current_inside_expression_only | 34 | 34 | 3 | 17 | 20.713 | 20.713 | -2.713 | -13.1% | -13.1% | +52.9% | +0.265 | -0.468 | 0.609 | 0.268 |
| forward_settled | latest | current_outside_expression_only_diag | 33 | 33 | 3 | 17 | 93.679 | 22.679 | -2.679 | -2.9% | -11.8% | +60.6% | +0.212 | -0.532 | 0.687 | 0.206 |
| forward_settled | latest | inside_same_decisions_all | 34 | 34 | 3 | 17 | 24.194 | 24.194 | -3.194 | -13.2% | -13.2% | +47.1% | +0.214 | -0.368 | 0.712 | 0.187 |
| forward_settled | latest | inside_native_edge002 | 34 | 34 | 3 | 17 | 22.006 | 22.006 | -2.006 | -9.1% | -9.1% | +58.8% | +0.229 | -0.470 | 0.647 | 0.248 |
| forward_settled | latest | inside_native_edge002_mid_cost_050_075 | 28 | 28 | 2 | 16 | 18.186 | 18.186 | -1.186 | -6.5% | -6.5% | +60.7% | +0.346 | -0.643 | 0.650 | 0.241 |
| forward_settled | latest | inside_native_edge002_cap080_gt025 | 32 | 32 | 2 | 17 | 19.428 | 19.428 | -1.428 | -7.4% | -7.4% | +56.2% | +0.340 | -0.539 | 0.607 | 0.270 |

## Forward Timing Sensitivity

| dedup | variant | rows | groups | dates | cities | gross_cost | eff_cost | pnl | gross_roi | eff_roi | win_rate | avg_win | avg_loss | avg_eff | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first | current_all | 34 | 34 | 3 | 17 | 122.056 | 21.056 | -2.056 | -1.7% | -9.8% | +55.9% | +0.321 | -0.543 | 0.619 | 0.233 |
| first | current_no_low_cost_gt025 | 34 | 34 | 3 | 17 | 122.056 | 21.056 | -2.056 | -1.7% | -9.8% | +55.9% | +0.321 | -0.543 | 0.619 | 0.233 |
| first | current_mid_cost_050_075 | 29 | 29 | 2 | 16 | 74.523 | 18.523 | -0.523 | -0.7% | -2.8% | +62.1% | +0.352 | -0.623 | 0.639 | 0.220 |
| first | current_cap075_gt025 | 32 | 32 | 2 | 17 | 96.935 | 17.935 | -0.935 | -1.0% | -5.2% | +53.1% | +0.397 | -0.512 | 0.560 | 0.284 |
| first | current_cap080_gt025 | 32 | 32 | 2 | 17 | 110.526 | 18.526 | -1.526 | -1.4% | -8.2% | +53.1% | +0.388 | -0.541 | 0.579 | 0.269 |
| first | current_inside_expression_only | 34 | 34 | 3 | 17 | 21.945 | 21.945 | -1.945 | -8.9% | -8.9% | +58.8% | +0.316 | -0.590 | 0.645 | 0.208 |
| first | current_outside_expression_only_diag | 33 | 33 | 3 | 17 | 170.566 | 20.566 | -3.566 | -2.1% | -17.3% | +51.5% | +0.320 | -0.563 | 0.623 | 0.236 |
| first | inside_same_decisions_all | 34 | 34 | 3 | 17 | 21.940 | 21.940 | -2.940 | -13.4% | -13.4% | +55.9% | +0.298 | -0.574 | 0.645 | 0.207 |
| first | inside_native_edge002 | 34 | 34 | 3 | 17 | 21.830 | 21.830 | -2.830 | -13.0% | -13.0% | +55.9% | +0.304 | -0.574 | 0.642 | 0.210 |
| first | inside_native_edge002_mid_cost_050_075 | 28 | 28 | 2 | 16 | 17.869 | 17.869 | -0.869 | -4.9% | -4.9% | +60.7% | +0.350 | -0.620 | 0.638 | 0.222 |
| first | inside_native_edge002_cap080_gt025 | 32 | 32 | 2 | 17 | 18.800 | 18.800 | -1.800 | -9.6% | -9.6% | +53.1% | +0.380 | -0.551 | 0.588 | 0.259 |
| latest | current_all | 34 | 34 | 3 | 17 | 53.751 | 22.751 | -1.751 | -3.3% | -7.7% | +61.8% | +0.202 | -0.460 | 0.669 | 0.230 |
| latest | current_no_low_cost_gt025 | 34 | 34 | 3 | 17 | 58.479 | 24.479 | -3.479 | -5.9% | -14.2% | +61.8% | +0.202 | -0.593 | 0.720 | 0.169 |
| latest | current_mid_cost_050_075 | 29 | 29 | 2 | 16 | 49.477 | 18.477 | -0.477 | -1.0% | -2.6% | +62.1% | +0.358 | -0.629 | 0.637 | 0.252 |
| latest | current_cap075_gt025 | 32 | 32 | 2 | 17 | 51.590 | 18.590 | -0.590 | -1.1% | -3.2% | +56.2% | +0.367 | -0.514 | 0.581 | 0.296 |
| latest | current_cap080_gt025 | 32 | 32 | 2 | 17 | 39.337 | 19.337 | -1.337 | -3.4% | -6.9% | +56.2% | +0.340 | -0.533 | 0.604 | 0.271 |
| latest | current_inside_expression_only | 34 | 34 | 3 | 17 | 20.713 | 20.713 | -2.713 | -13.1% | -13.1% | +52.9% | +0.265 | -0.468 | 0.609 | 0.268 |
| latest | current_outside_expression_only_diag | 33 | 33 | 3 | 17 | 93.679 | 22.679 | -2.679 | -2.9% | -11.8% | +60.6% | +0.212 | -0.532 | 0.687 | 0.206 |
| latest | inside_same_decisions_all | 34 | 34 | 3 | 17 | 24.194 | 24.194 | -3.194 | -13.2% | -13.2% | +47.1% | +0.214 | -0.368 | 0.712 | 0.187 |
| latest | inside_native_edge002 | 34 | 34 | 3 | 17 | 22.006 | 22.006 | -2.006 | -9.1% | -9.1% | +58.8% | +0.229 | -0.470 | 0.647 | 0.248 |
| latest | inside_native_edge002_mid_cost_050_075 | 28 | 28 | 2 | 16 | 18.186 | 18.186 | -1.186 | -6.5% | -6.5% | +60.7% | +0.346 | -0.643 | 0.650 | 0.241 |
| latest | inside_native_edge002_cap080_gt025 | 32 | 32 | 2 | 17 | 19.428 | 19.428 | -1.428 | -7.4% | -7.4% | +56.2% | +0.340 | -0.539 | 0.607 | 0.270 |
| persistence2_latest | current_all | 34 | 34 | 3 | 17 | 53.751 | 22.751 | -1.751 | -3.3% | -7.7% | +61.8% | +0.202 | -0.460 | 0.669 | 0.230 |
| persistence2_latest | current_no_low_cost_gt025 | 34 | 34 | 3 | 17 | 58.479 | 24.479 | -3.479 | -5.9% | -14.2% | +61.8% | +0.202 | -0.593 | 0.720 | 0.169 |
| persistence2_latest | current_mid_cost_050_075 | 25 | 25 | 2 | 15 | 43.693 | 15.693 | -1.693 | -3.9% | -10.8% | +56.0% | +0.373 | -0.629 | 0.628 | 0.267 |
| persistence2_latest | current_cap075_gt025 | 30 | 30 | 2 | 17 | 50.093 | 17.093 | -1.093 | -2.2% | -6.4% | +53.3% | +0.381 | -0.514 | 0.570 | 0.307 |
| persistence2_latest | current_cap080_gt025 | 32 | 32 | 2 | 17 | 39.337 | 19.337 | -1.337 | -3.4% | -6.9% | +56.2% | +0.340 | -0.533 | 0.604 | 0.271 |
| persistence2_latest | current_inside_expression_only | 30 | 30 | 2 | 17 | 17.759 | 17.759 | -2.759 | -15.5% | -15.5% | +50.0% | +0.265 | -0.449 | 0.592 | 0.283 |
| persistence2_latest | current_outside_expression_only_diag | 31 | 31 | 2 | 17 | 87.844 | 20.844 | -2.844 | -3.2% | -13.6% | +58.1% | +0.226 | -0.532 | 0.672 | 0.215 |
| persistence2_latest | inside_same_decisions_all | 34 | 34 | 3 | 17 | 24.194 | 24.194 | -3.194 | -13.2% | -13.2% | +47.1% | +0.214 | -0.368 | 0.712 | 0.187 |
| persistence2_latest | inside_native_edge002 | 33 | 33 | 3 | 17 | 21.176 | 21.176 | -2.176 | -10.3% | -10.3% | +57.6% | +0.232 | -0.470 | 0.642 | 0.254 |
| persistence2_latest | inside_native_edge002_mid_cost_050_075 | 24 | 24 | 2 | 15 | 15.409 | 15.409 | -2.409 | -15.6% | -15.6% | +54.2% | +0.359 | -0.643 | 0.642 | 0.256 |
| persistence2_latest | inside_native_edge002_cap080_gt025 | 32 | 32 | 2 | 17 | 19.428 | 19.428 | -1.428 | -7.4% | -7.4% | +56.2% | +0.340 | -0.539 | 0.607 | 0.270 |
| persistence3_latest | current_all | 31 | 31 | 2 | 17 | 47.146 | 20.146 | -2.146 | -4.6% | -10.7% | +58.1% | +0.213 | -0.460 | 0.650 | 0.244 |
| persistence3_latest | current_no_low_cost_gt025 | 31 | 31 | 2 | 17 | 51.874 | 21.874 | -3.874 | -7.5% | -17.7% | +58.1% | +0.213 | -0.593 | 0.706 | 0.177 |
| persistence3_latest | current_mid_cost_050_075 | 22 | 22 | 2 | 15 | 33.869 | 13.869 | -1.869 | -5.5% | -13.5% | +54.5% | +0.374 | -0.636 | 0.630 | 0.279 |
| persistence3_latest | current_cap075_gt025 | 28 | 28 | 2 | 17 | 45.616 | 15.616 | -1.616 | -3.5% | -10.3% | +50.0% | +0.398 | -0.514 | 0.558 | 0.323 |
| persistence3_latest | current_cap080_gt025 | 31 | 31 | 2 | 17 | 38.567 | 18.567 | -1.567 | -4.1% | -8.4% | +54.8% | +0.347 | -0.533 | 0.599 | 0.277 |
| persistence3_latest | current_inside_expression_only | 28 | 28 | 2 | 17 | 16.208 | 16.208 | -3.208 | -19.8% | -19.8% | +46.4% | +0.271 | -0.449 | 0.579 | 0.298 |
| persistence3_latest | current_outside_expression_only_diag | 31 | 31 | 2 | 17 | 87.844 | 20.844 | -2.844 | -3.2% | -13.6% | +58.1% | +0.226 | -0.532 | 0.672 | 0.215 |
| persistence3_latest | inside_same_decisions_all | 31 | 31 | 2 | 17 | 21.442 | 21.442 | -3.442 | -16.1% | -16.1% | +45.2% | +0.222 | -0.386 | 0.692 | 0.202 |
| persistence3_latest | inside_native_edge002 | 31 | 31 | 2 | 17 | 19.493 | 19.493 | -2.493 | -12.8% | -12.8% | +54.8% | +0.240 | -0.470 | 0.629 | 0.265 |
| persistence3_latest | inside_native_edge002_mid_cost_050_075 | 22 | 22 | 2 | 14 | 14.124 | 14.124 | -3.124 | -22.1% | -22.1% | +50.0% | +0.359 | -0.643 | 0.642 | 0.270 |
| persistence3_latest | inside_native_edge002_cap080_gt025 | 31 | 31 | 2 | 17 | 18.658 | 18.658 | -1.658 | -8.9% | -8.9% | +54.8% | +0.347 | -0.539 | 0.602 | 0.276 |

## Interpretation

- A simple expression fix is not enough. Historical outside-NO was weak, but the forward `inside_yes_same_decisions` and `inside_native_edge002` counterfactuals are also negative, so the miss is more likely model/timing/calibration than only the cheaper router.
- Cost bands help but do not solve it. `current_mid_cost_050_075` and `current_cap075_gt025` stay positive in historical train/holdout and reduce the forward loss, yet forward remains below zero. That is a watchlist candidate, not a promotion.
- Persistence is not a free quality filter in this sample; requiring 2-3 repeated triggers mostly worsens the forward result. Repeated shadow appearances can mean stale conviction, not confirmed edge.
- The `inside_yes_same_decisions` rows are counterfactual when the shadow runner chose outside-NO; they use the runner's recorded inside YES cost and settlement truth, but they do not prove live executable capacity for those alternate legs.
- The journal does not yet carry `forecast_quality_low`; if this branch continues, v1 shadow telemetry should write forecast-quality labels at the same decision grain so the required no-filter/exclude-low/default-WU lines can be evaluated forward too.

## Current Verdict

`inconclusive / keep shadow only`: there is enough evidence to instrument the next shadow candidate, not enough evidence for paper/live. The cleanest next implementation is a v1 zero-notional runner that records both current-cheaper and inside-YES counterfactual fields, plus forecast-quality labels, while pre-registering cost floor/cap diagnostics instead of city-specific filters.

significance=FAIL, baseline=FAIL, forward=FAIL, conclusion=inconclusive
