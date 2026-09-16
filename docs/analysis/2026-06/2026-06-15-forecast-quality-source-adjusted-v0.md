# Forecast Quality Source-Adjusted v0

> generated_at_utc: `2026-06-14T16:34:28.657440+00:00`
> target_metric: `forecast_quality_source_adjusted_reliability_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: opportunity-grain reliability research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` for decision-set reliability; settlement source classes from `settlement_source_registry_v0`.
- DB last_modified: `2026-06-14T16:08:24.970078+00:00`.
- fact_signal_candidates rows: `29313`.
- decision_sets used: `262` settled city/event/model/snapshot distributions.
- source-matched decision_sets: `262`.
- strategy overlay rows: `776`.
- train: `2026-05-06` -> `2026-05-28` (21 event_dates).
- holdout: `2026-05-29` -> `2026-06-10` (10 event_dates).
- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-14T16:08:13.367378+00:00",
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
    "rows": 29313,
    "eligible": 10031,
    "paper_ordered": 3824,
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

## Target Metric

`forecast_quality_source_adjusted_reliability_v0` = whether the shared forecast-quality labels remain useful after city decision sets are stratified by Polymarket settlement source class.

The key distinction is source alignment, not payout truth: `pm_history` / `final_yes` remains the settlement label, while `settlement_source_class` says whether local forecast/observed features are aligned with the station/feed/rule Polymarket settles against.

## Population Comparison

| population | all_rows | cities | holdout_rows | holdout_dates | holdout_adj3 | holdout_tail_miss | mp_rate | low_rate | reliable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_decision_sets | 262 | 48 | 64 | 10 | +95.3% | +4.7% | +20.3% | +79.7% | +45.3% |
| default_wu_only | 188 | 34 | 46 | 10 | +95.7% | +4.3% | +23.9% | +76.1% | +45.7% |
| default_wu_plus_watchlist | 188 | 34 | 46 | 10 | +95.7% | +4.3% | +23.9% | +76.1% | +45.7% |
| source_sensitive_confirmed | 45 | 8 | 11 | 5 | +90.9% | +9.1% | +0.0% | +100.0% | +27.3% |
| blocked_unresolved | 19 | 3 | 5 | 3 | +100.0% | +0.0% | +40.0% | +60.0% | +80.0% |

## Source Bucket Quality

| source_bucket | all_rows | cities | holdout_rows | holdout_dates | holdout_adj3 | holdout_tail_miss | mp_rate | low_rate | reliable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| default_wu | 188 | 34 | 46 | 10 | +95.7% | +4.3% | +23.9% | +76.1% | +45.7% |
| source_sensitive_confirmed | 45 | 8 | 11 | 5 | +90.9% | +9.1% | +0.0% | +100.0% | +27.3% |
| blocked_unresolved | 19 | 3 | 5 | 3 | +100.0% | +0.0% | +40.0% | +60.0% | +80.0% |
| other_or_unknown | 10 | 3 | 2 | 2 | +100.0% | +0.0% | +0.0% | +100.0% | +50.0% |

## Settlement Source Class Quality

| settlement_source_class | all_rows | cities | holdout_rows | holdout_dates | holdout_adj3 | holdout_tail_miss | mp_rate | low_rate | reliable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| default_wu_station_by_rules | 188 | 34 | 46 | 10 | +95.7% | +4.3% | +23.9% | +76.1% | +45.7% |
| official_station_diff_confirmed | 40 | 7 | 10 | 5 | +90.0% | +10.0% | +0.0% | +100.0% | +20.0% |
| blocked_unresolved_settlement_basis | 19 | 3 | 5 | 3 | +100.0% | +0.0% | +40.0% | +60.0% | +80.0% |
| non_wu_source_by_rules | 8 | 2 | 2 | 2 | +100.0% | +0.0% | +0.0% | +100.0% | +50.0% |
| special_source_confirmed | 5 | 1 | 1 | 1 | +100.0% | +0.0% | +0.0% | +100.0% | +100.0% |
| no_recent_market_or_unknown_rules | 2 | 1 | 0 | 0 | NA | NA | NA | NA | NA |

## Strategy Overlay By Source Bucket

Rows are decision-price proxy overlays. They answer whether a reliability tag behaves similarly across settlement-source buckets, not whether it is executable.

| bucket | algorithm | filter | train_rows | train_roi | holdout_rows | holdout_dates | holdout_roi | holdout_excess | top5_removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blocked_unresolved | adjacent3_yes_cost085 | city_model_reliable | 5 | +35.6% | 2 | 1 | +88.2% | -2.5% | NA |
| blocked_unresolved | adjacent3_yes_cost085 | exclude_forecast_quality_low | 1 | +18.3% | 1 | 1 | +95.9% | +5.2% | NA |
| blocked_unresolved | adjacent3_yes_cost085 | forecast_quality_medium_plus | 1 | +18.3% | 1 | 1 | +95.9% | +5.2% | NA |
| blocked_unresolved | adjacent3_yes_cost085 | no_quality_filter | 11 | +49.3% | 3 | 1 | +90.7% | +0.0% | NA |
| blocked_unresolved | side_band_best_leg_mid_cost_e008_top4 | city_model_reliable | 10 | -17.6% | 6 | 3 | +14.6% | +9.9% | NA |
| blocked_unresolved | side_band_best_leg_mid_cost_e008_top4 | exclude_forecast_quality_low | 7 | -32.9% | 4 | 2 | -48.1% | -52.8% | NA |
| blocked_unresolved | side_band_best_leg_mid_cost_e008_top4 | forecast_quality_medium_plus | 7 | -32.9% | 4 | 2 | -48.1% | -52.8% | NA |
| blocked_unresolved | side_band_best_leg_mid_cost_e008_top4 | no_quality_filter | 22 | -20.9% | 7 | 3 | +4.7% | +0.0% | NA |
| blocked_unresolved | single_leg_buy_no_cost40_75_edge010_top1 | city_model_reliable | 4 | +49.8% | 4 | 3 | +60.6% | +0.0% | NA |
| blocked_unresolved | single_leg_buy_no_cost40_75_edge010_top1 | exclude_forecast_quality_low | 3 | +42.5% | 1 | 1 | +68.1% | +7.4% | NA |
| blocked_unresolved | single_leg_buy_no_cost40_75_edge010_top1 | forecast_quality_medium_plus | 3 | +42.5% | 1 | 1 | +68.1% | +7.4% | NA |
| blocked_unresolved | single_leg_buy_no_cost40_75_edge010_top1 | no_quality_filter | 11 | +18.9% | 4 | 3 | +60.6% | +0.0% | NA |
| default_wu | adjacent3_yes_cost085 | city_model_reliable | 18 | +28.3% | 8 | 2 | -5.4% | -29.9% | NA |
| default_wu | adjacent3_yes_cost085 | exclude_forecast_quality_low | 20 | +29.5% | 5 | 2 | +18.4% | -6.1% | NA |
| default_wu | adjacent3_yes_cost085 | forecast_quality_medium_plus | 20 | +29.5% | 5 | 2 | +18.4% | -6.1% | NA |
| default_wu | adjacent3_yes_cost085 | no_quality_filter | 90 | +30.4% | 26 | 3 | +24.4% | +0.0% | NA |
| default_wu | side_band_best_leg_mid_cost_e008_top4 | city_model_reliable | 57 | +14.9% | 39 | 8 | +11.8% | +13.9% | -100.0% |
| default_wu | side_band_best_leg_mid_cost_e008_top4 | exclude_forecast_quality_low | 60 | +18.7% | 23 | 4 | +19.2% | +21.3% | NA |
| default_wu | side_band_best_leg_mid_cost_e008_top4 | forecast_quality_medium_plus | 60 | +18.7% | 23 | 4 | +19.2% | +21.3% | NA |
| default_wu | side_band_best_leg_mid_cost_e008_top4 | no_quality_filter | 215 | +11.4% | 73 | 10 | -2.1% | +0.0% | -45.8% |
| default_wu | single_leg_buy_no_cost40_75_edge010_top1 | city_model_reliable | 27 | +19.3% | 18 | 7 | +2.3% | +17.4% | -100.0% |
| default_wu | single_leg_buy_no_cost40_75_edge010_top1 | exclude_forecast_quality_low | 28 | +19.5% | 10 | 4 | +5.7% | +20.8% | NA |
| default_wu | single_leg_buy_no_cost40_75_edge010_top1 | forecast_quality_medium_plus | 28 | +19.5% | 10 | 4 | +5.7% | +20.8% | NA |
| default_wu | single_leg_buy_no_cost40_75_edge010_top1 | no_quality_filter | 113 | +9.7% | 39 | 10 | -15.1% | +0.0% | -23.6% |
| other_or_unknown | adjacent3_yes_cost085 | no_quality_filter | 7 | +25.2% | 1 | 1 | +37.0% | +0.0% | NA |
| other_or_unknown | side_band_best_leg_mid_cost_e008_top4 | city_model_reliable | 0 | NA | 2 | 1 | -12.3% | +8.7% | NA |
| other_or_unknown | side_band_best_leg_mid_cost_e008_top4 | no_quality_filter | 12 | -38.7% | 4 | 2 | -20.9% | +0.0% | NA |
| other_or_unknown | single_leg_buy_no_cost40_75_edge010_top1 | city_model_reliable | 0 | NA | 1 | 1 | +62.6% | +10.5% | NA |
| other_or_unknown | single_leg_buy_no_cost40_75_edge010_top1 | no_quality_filter | 6 | -23.5% | 2 | 2 | +52.1% | +0.0% | NA |
| source_sensitive_confirmed | adjacent3_yes_cost085 | city_model_reliable | 3 | -35.5% | 1 | 1 | +17.6% | +10.0% | NA |
| source_sensitive_confirmed | adjacent3_yes_cost085 | no_quality_filter | 16 | -7.9% | 6 | 2 | +7.6% | +0.0% | NA |
| source_sensitive_confirmed | side_band_best_leg_mid_cost_e008_top4 | city_model_reliable | 12 | -8.1% | 5 | 2 | +12.4% | +4.4% | NA |
| source_sensitive_confirmed | side_band_best_leg_mid_cost_e008_top4 | no_quality_filter | 53 | -11.6% | 19 | 5 | +8.0% | +0.0% | NA |
| source_sensitive_confirmed | single_leg_buy_no_cost40_75_edge010_top1 | city_model_reliable | 6 | -46.5% | 2 | 2 | -24.2% | -10.2% | NA |
| source_sensitive_confirmed | single_leg_buy_no_cost40_75_edge010_top1 | no_quality_filter | 27 | -25.2% | 9 | 5 | -14.0% | +0.0% | NA |

## Findings

- Default WU cities remain the largest generic denominator: `188` decision sets across `34` cities. This should be the default denominator for generic forecast-reliability claims.
- Source-sensitive confirmed cities are not garbage data, but they are a different feature-source problem: `45` decision sets across `8` cities. HK must use HKO semantics; station-diff cities must use the official station/feed.
- Blocked unresolved cities are small but should not train or validate generic source-sensitive claims: `19` decision sets across `3` cities.
- The shared forecast-quality base is still useful, but the reusable contract must include `settlement_source_class`. A city-model reliability tag that mixes default, special-source, and blocked settlement bases is too easy to misread.
- HK/Jakarta follow the earlier registry conclusion: HK is HKO Daily Extract + floor mapping, Jakarta is WIHH/Halim. They should be separate source adapters, not generic VHHH/WIII forecast-quality rows.

## Reuse Contract Update

- Forecast-quality sidecar grain stays `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc`, but it must carry `settlement_source_class`, `official_station_or_feed`, and `mapping_rule`.
- Generic model-reliability reporting should default to `default_wu` plus an explicit optional watchlist column. Do not silently pool `blocked_unresolved` into generic training/holdout summaries.
- Source-sensitive cities can be used in broad strategy overlays only as tagged covariates. For feature-source claims, rebuild features from official sources first: HKO for HongKong, WIHH for Jakarta, official station for station-diff cities.
- Range RV / adjacent3 / side-band / BUY_NO / basket consumers should report no-quality baseline, forecast-quality filter, and source bucket side by side.
- Any orderbook/execution claim remains subject to `snapshot_ts_utc <= decision_snapshot_ts_utc`; this report does not make executable claims.

## Next Research Directions

1. Materialize `settlement_source_class`, `official_station_or_feed`, and `mapping_rule` into a generated forecast-quality sidecar or fact-table join artifact.
2. Build HK HKO and Jakarta WIHH source adapters, then rerun source-sensitive reliability using official source features rather than generic configured-station features.
3. Rerun BUY_NO single-leg and side-band consumers with source buckets shown as a required table; if a tag only works in source-sensitive cities, it is not a generic forecast-quality base.
4. Keep `blocked_unresolved` cities out of source-sensitive feature research until Moscow/Seoul/Shenzhen root causes are resolved.
5. After more forward settlements, redo event-date cluster bootstrap and top-date stress inside each source bucket before promoting any tag beyond shadow telemetry.

## Three-Gate Verdict

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | This is a source-stratified reliability audit; bucket samples are thin and no excess ROI CI is durable enough for live action. |
| baseline | FAIL | Decision-price overlays are not a time-aligned executable baseline and do not uniformly beat family baselines across buckets. |
| forward | FAIL | Holdout exists, but source-bucket support is too small for confirmed generalization. |

`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive` for live action.

## Plain-English Conclusion

The forecast-quality base should survive as a reusable reliability layer, but it must become source-aware. HK/Jakarta/station-diff cities are not ordinary default-station rows; they need official-source feature adapters or explicit source-sensitive treatment. Blocked unresolved cities should stay out of source-sensitive claims.
