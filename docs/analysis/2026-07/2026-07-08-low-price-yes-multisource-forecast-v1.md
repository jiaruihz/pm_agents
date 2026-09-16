# HeadA Multi-Source Forecast Counterfactual v1

Generated: 2026-07-08T04:08:38+00:00

## Question

If HeadA consumes the newer multi-source forecast layer instead of only the city-assigned source,
what changes in order count, hit rate, and ROI?

## Short Answer

Historical evidence says source information is useful as a **shadow ranking/telemetry layer**, but
it does not justify replacing the current assigned-source HeadA selector yet.

The true GFS/ECMWF multi-source curve replay only starts on 2026-07-03, so the direct forward sample
is still tiny and partly unsettled. In that direct replay, relaxing to **any source can trigger**
increases order count sharply, but it adds mostly unsettled/fresh rows and is not yet validated.
The conservative **both-sources-agree** variant cuts order count and has too little settled evidence.

The newer `forecast_enrichment` layer does contain many more Open-Meteo models (AIFS, ICON, GEM,
JMA, HRRR/NAM/NBM, etc.), but it only starts on 2026-07-07 in the local runtime and has no
model-specific historical error calibration in canonical facts yet. Under strict as-of joining,
the current local sample has not overlapped a HeadA decision row yet, so it has no measurable
order-count or ROI impact today.

Default action: keep current live selector unchanged; log GFS/ECMWF p/edge/dist as shadow fields and
evaluate again after more settled days.

```text
significance=NA/FAIL for live change
baseline=current assigned-source HeadA
forward=insufficient settled multi-source sample
conclusion=shadow_candidate_telemetry_only
```

## Data Snapshot

```json
{
  "cache_dir": "/Users/deepsleep/projects/weather_data_feed_service_runtime/cache",
  "db": "/Users/deepsleep/projects/pm_agents/runtime/weather.db",
  "fact_forecast_hourly_curves": {
    "by_model": {
      "ecmwf": 12487,
      "gfs": 21714
    },
    "max_snapshot_ts_utc": "2026-07-08T03:28:59Z",
    "rows": 34201
  },
  "fact_signal_candidates": {
    "dates": [
      "2026-07-03",
      "2026-07-04",
      "2026-07-05",
      "2026-07-06",
      "2026-07-07",
      "2026-07-08"
    ],
    "max_decision_snapshot_ts_utc": "2026-07-08T03:46:30Z",
    "rows": 266
  },
  "fact_window": {
    "end": "2026-07-09",
    "start": "2026-07-03"
  },
  "fee_model": "price_tier_6_8_10_shares + Weather taker fee shares * 0.05 * price * (1-price)",
  "forecast_enrichment_proxy": {
    "candidate_model_rows_asof": 0,
    "max_snapshot_ts_utc": "2026-07-08T03:45:16.421092+00:00",
    "models": [
      "AI-GFS",
      "AROME HD",
      "ECMWF",
      "ECMWF AIFS",
      "GDPS",
      "GEM",
      "GFS",
      "GFS Global",
      "HRDPS",
      "HRRR",
      "ICON",
      "ICON-D2",
      "ICON-EU",
      "JMA",
      "NAM",
      "NBM",
      "RDPS"
    ],
    "path": "/Volumes/jrs/weather_data_feed_service_runtime/output/forecast_enrichment/forecast_enrichment.jsonl",
    "rows": 13781
  },
  "historical_denominator": {
    "dates": [
      "2026-05-06",
      "2026-06-30"
    ],
    "path": "docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/base_rows.csv",
    "rows": 476
  }
}
```

## Historical Feature-Layer View

This uses the broad HeadA historical denominator. It is not a full alternate forecast-max replay;
it measures already-materialized source-aware and GFS/ECMWF-disagreement features.

| selector | rows | settled | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hist_current_hot_dist_gt0 | 333 | 333 | 53 | 47 | +15.0% | +10.4% | +41.8% | +10.0% | +77.2% | 19.000 | $-11.04 |
| hist_model_agree_gap_lt1_or_missing_hot | 326 | 326 | 53 | 45 | +15.3% | +10.4% | +45.3% | +12.8% | +81.7% | 19.000 | $-10.48 |
| hist_model_disagree_gap_ge1_hot | 7 | 7 | 6 | 5 | +0.0% | +11.1% | -100.0% | -100.0% | -100.0% | 6.000 | $-2.03 |
| hist_source_aware_v3_hot | 200 | 200 | 53 | 43 | +19.5% | +11.9% | +59.2% | +17.1% | +105.1% | 23.000 | $-7.91 |
| hist_source_aware_wide_hot | 237 | 237 | 53 | 45 | +18.6% | +11.4% | +58.5% | +20.1% | +101.5% | 21.000 | $-8.67 |

### Historical Recent Window

| selector | rows | settled | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hist_current_hot_dist_gt0 | 58 | 58 | 9 | 28 | +17.2% | +9.9% | +76.7% | +32.2% | +133.6% |
| hist_model_agree_gap_lt1_or_missing_hot | 58 | 58 | 9 | 28 | +17.2% | +9.9% | +76.7% | +32.2% | +133.6% |
| hist_model_disagree_gap_ge1_hot | 0 | 0 | 0 | 0 |  |  |  |  |  |
| hist_source_aware_v3_hot | 32 | 32 | 9 | 20 | +25.0% | +11.6% | +103.9% | +19.9% | +196.9% |
| hist_source_aware_wide_hot | 40 | 40 | 9 | 24 | +22.5% | +11.0% | +96.9% | +20.6% | +184.3% |

## Direct Multi-Source Curve Replay 2026-07-03+

This is the direct counterfactual using `fact_forecast_hourly_curves` GFS/ECMWF as-of each decision snapshot.

| selector | rows | settled | open_rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_any_source_earliest | 24 | 18 | 6 | 5 | 15 | +33.3% | +9.6% | +200.2% | +16.7% | +387.0% |
| forward_both_sources_agree | 17 | 11 | 6 | 5 | 11 | +45.5% | +9.2% | +313.8% | +104.7% | +705.0% |
| forward_current_assigned | 34 | 28 | 6 | 6 | 19 | +21.4% | +9.8% | +93.4% | -67.8% | +296.6% |
| forward_ecmwf_only | 11 | 9 | 2 | 3 | 9 | +11.1% | +10.8% | +11.1% | -100.0% | +148.7% |
| forward_ensemble_mean_p_both_hot | 18 | 12 | 6 | 5 | 12 | +41.7% | +9.5% | +252.5% | +47.6% | +705.0% |
| forward_gfs_only | 14 | 10 | 4 | 5 | 9 | +50.0% | +8.4% | +439.6% | +246.7% | +705.0% |

### Settled Subset Only

| selector | rows | settled | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_any_source_earliest | 18 | 18 | 3 | 14 | +33.3% | +10.1% | +200.2% | +16.7% | +387.0% |
| forward_both_sources_agree | 11 | 11 | 3 | 8 | +45.5% | +9.8% | +313.8% | +104.7% | +705.0% |
| forward_current_assigned | 28 | 28 | 5 | 19 | +21.4% | +10.1% | +93.4% | -67.8% | +296.6% |
| forward_ecmwf_only | 9 | 9 | 2 | 9 | +11.1% | +11.1% | +11.1% | -100.0% | +148.7% |
| forward_ensemble_mean_p_both_hot | 12 | 12 | 3 | 9 | +41.7% | +10.1% | +252.5% | +47.6% | +705.0% |
| forward_gfs_only | 10 | 10 | 3 | 7 | +50.0% | +8.8% | +439.6% | +246.7% | +705.0% |

### Open / Unsettled Rows

| selector | rows | open_rows | dates | cities | avg_entry |
| --- | --- | --- | --- | --- | --- |
| forward_any_source_earliest | 6 | 6 | 2 | 5 | +8.2% |
| forward_both_sources_agree | 6 | 6 | 2 | 5 | +8.2% |
| forward_current_assigned | 6 | 6 | 2 | 5 | +8.2% |
| forward_ecmwf_only | 2 | 2 | 1 | 2 | +9.8% |
| forward_ensemble_mean_p_both_hot | 6 | 6 | 2 | 5 | +8.2% |
| forward_gfs_only | 4 | 4 | 2 | 3 | +7.5% |

## Forecast-Enrichment Multi-Model Proxy 2026-07-07+

This reads `weather_data_feed_service_runtime/output/forecast_enrichment/forecast_enrichment.jsonl`.
It tests only whether extra models mark the same candidate as hotter than the forecast bracket
(`dist>0`). It does **not** recompute calibrated `model_p_yes` for AIFS/ICON/GEM/JMA/etc.
Rows are zero here because strict `enrichment_snapshot_ts_utc <= decision_snapshot_ts_utc` has no
overlap with HeadA candidates in the current local sample; the data is being collected but was not
available at those exact decision snapshots.

| selector | rows | settled | open_rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| enrich_all_model_hot_proxy | 0 | 0 | 0 | 0 | 0 |  |  |  |  |  |
| enrich_any_model_hot_proxy | 0 | 0 | 0 | 0 | 0 |  |  |  |  |  |
| enrich_current_assigned_asof | 0 | 0 | 0 | 0 | 0 |  |  |  |  |  |
| enrich_majority_model_hot_proxy | 0 | 0 | 0 | 0 | 0 |  |  |  |  |  |

### Enrichment Settled Subset Only

_No rows._

## Interpretation

- `any_source` is dangerous as a live rule: it mechanically increases triggers by letting either
  forecast source say "hot tail". That is useful for shadow discovery, but without settled evidence
  it can become a false-positive amplifier.
- `both_sources_agree` is cleaner but sample-starves the strategy. It may be useful as a confidence
  tag or sizing shadow, not as a hard live gate yet.
- Historical `source_aware_v3`-style features improved shape before `dist>0`, but after the current
  hot-tail boundary the source layer is better treated as probability/ranking input than a new
  selector.
- The next useful implementation is to write `gfs_p_yes`, `ecmwf_p_yes`, `gfs_dist`, `ecmwf_dist`,
  `source_disagreement`, and `both_sources_hot` into the live decision/feature frame and accumulate
  settled forward evidence.
- For the broader enrichment models, the next required step is not to switch live selection. It is to
  materialize PIT per-model forecast max rows into canonical facts and build per-model/city error
  distributions; until then, extra models can only be shadow confidence features.
