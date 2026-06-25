# Range RV Timing Stability v2

> generated_at_utc: `2026-06-25T13:54:21.816060+00:00`
> target_metric: `forecast_bounded_range_rv_timing_stability_v2`
> strategy_id: `forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0`

## Data Snapshot

- Evidence layer: N100 zero-notional Range RV shadow journal plus `pm_history` settlement truth.
- This is not live PnL. Every journal row is shadow-only and must keep `no_order_placed=true`.
- journal_rows: `7041`; selected policy rows: `4912`.
- snapshot range: `2026-06-14T17:00:20Z` -> `2026-06-25T12:30:37Z`.
- event_dates: `2026-06-14, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-20, 2026-06-21, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26`.
- all_no_order_placed: `True`; source_buckets: `{"default_wu": 7041}`.

### Mandatory SQL Self-Check

```json
{
  "candidate_coverage": {
    "eligible": 13596,
    "live_filled": 348,
    "paper_ordered": 5374,
    "rows": 37655
  },
  "max_fact_built_at_utc": "2026-06-25T05:08:16.112570+00:00",
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

## Policy Summary

Each policy selects at most one row per `city + event_date + forecast_source + model_version`. Cutoff policies are prediction-style rules; regime labels are attached only when an hourly atlas row exists.

| policy | rows | dates | cities | cost | pnl | roi | ci_low | ci_high | hit | avg_hour | hrs_to_end | regime_join | description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| latest_before_local_18 | 165 | 11 | 17 | 109.021 | +9.979 | +9.2% | +2.1% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% | prediction cutoff: last trigger no later than local 18:59 on target date |
| latest_before_local_20 | 165 | 11 | 17 | 109.026 | +9.974 | +9.1% | +2.0% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% | prediction cutoff: last trigger no later than local 20:59 on target date |
| latest_before_local_22 | 165 | 11 | 17 | 109.026 | +9.974 | +9.1% | +2.0% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% | prediction cutoff: last trigger no later than local 22:59 on target date |
| latest_trigger | 165 | 11 | 17 | 109.026 | +9.974 | +9.1% | +2.0% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% | baseline: latest eligible trigger |
| latest_min_lead_06h | 165 | 11 | 17 | 109.338 | +9.662 | +8.8% | +1.8% | +15.8% | +72.1% | 11.3 | 13.9 | +70.3% | prediction cutoff: last trigger with at least 6h before local day end |
| latest_before_local_16 | 165 | 11 | 17 | 109.477 | +9.523 | +8.7% | +1.7% | +15.7% | +72.1% | 11.2 | 14.0 | +70.3% | prediction cutoff: last trigger no later than local 16:59 on target date |
| latest_min_lead_08h | 165 | 11 | 17 | 110.339 | +8.661 | +7.8% | +1.3% | +14.3% | +72.1% | 11.0 | 14.1 | +69.7% | prediction cutoff: last trigger with at least 8h before local day end |
| stable2_before_local_18 | 161 | 11 | 17 | 102.394 | +5.606 | +5.5% | -2.9% | +15.2% | +67.1% | 11.1 | 14.1 | +65.2% | stability plus not-too-late local 18 cutoff |
| stable2_latest | 161 | 11 | 17 | 102.432 | +5.568 | +5.4% | -2.9% | +15.1% | +67.1% | 11.2 | 14.0 | +65.2% | range stability: same inside range for 2 consecutive triggers |
| latest_min_lead_10h | 165 | 11 | 17 | 112.919 | +5.081 | +4.5% | -3.3% | +11.4% | +71.5% | 10.7 | 14.5 | +69.1% | prediction cutoff: last trigger with at least 10h before local day end |
| latest_before_local_14 | 165 | 11 | 17 | 114.063 | +4.937 | +4.3% | -3.0% | +10.7% | +72.1% | 10.9 | 14.3 | +69.1% | prediction cutoff: last trigger no later than local 14:59 on target date |
| stable2_mass085_before18 | 118 | 11 | 16 | 80.551 | +1.449 | +1.8% | -7.5% | +10.7% | +69.5% | 11.1 | 14.7 | +63.6% | calibration control: stable and high model mass before local 18 |
| first_trigger | 165 | 11 | 17 | 101.946 | -6.946 | -6.8% | -15.1% | +2.3% | +57.6% | 6.5 | 41.2 | +13.3% | baseline: first eligible trigger |
| stable2_inside_yes_before18 | 155 | 10 | 17 | 93.793 | +8.207 | +8.8% | -3.0% | +21.4% | +65.8% | 10.8 | 15.9 | +54.8% | diagnostic: stable inside-YES only before local 18 |
| latest_before_local_12 | 164 | 10 | 17 | 111.530 | +5.470 | +4.9% | -2.3% | +11.3% | +71.3% | 10.3 | 15.4 | +67.1% | prediction cutoff: last trigger no later than local 12:59 on target date |
| stable3_latest | 156 | 10 | 17 | 94.912 | +4.088 | +4.3% | -5.9% | +16.6% | +63.5% | 10.7 | 14.7 | +62.2% | range stability: same inside range for 3 consecutive triggers |
| latest_min_lead_12h | 163 | 10 | 17 | 110.085 | +3.915 | +3.6% | -4.2% | +11.5% | +69.9% | 9.9 | 16.1 | +65.6% | prediction cutoff: last trigger with at least 12h before local day end |
| latest_min_lead_24h | 148 | 10 | 17 | 93.883 | +3.117 | +3.3% | -9.8% | +15.3% | +65.5% | 20.7 | 26.9 | +16.9% | prediction cutoff: last trigger with at least 24h before local day end |
| latest_min_lead_16h | 157 | 10 | 17 | 101.267 | +2.733 | +2.7% | -7.7% | +11.8% | +66.2% | 7.3 | 19.5 | +6.4% | prediction cutoff: last trigger with at least 16h before local day end |
| latest_before_local_10 | 162 | 10 | 17 | 105.343 | +2.657 | +2.5% | -5.4% | +10.7% | +66.7% | 9.4 | 16.9 | +58.0% | prediction cutoff: last trigger no later than local 10:59 on target date |
| latest_min_lead_20h | 155 | 10 | 17 | 101.300 | +1.700 | +1.7% | -10.8% | +12.7% | +66.5% | 5.3 | 22.9 | +9.0% | prediction cutoff: last trigger with at least 20h before local day end |
| mid_cost_latest | 144 | 10 | 17 | 95.830 | +0.170 | +0.2% | -10.5% | +11.8% | +66.7% | 10.7 | 17.1 | +55.6% | risk control: mid effective cost only |
| stable2_mid_cost_before18 | 143 | 10 | 17 | 94.148 | -1.148 | -1.2% | -13.1% | +11.4% | +65.0% | 10.5 | 18.0 | +51.0% | candidate v2: stable range, mid cost, not later than local 18 |
| stable2_mid_cost_latest | 143 | 10 | 17 | 94.148 | -1.148 | -1.2% | -13.1% | +11.4% | +65.0% | 10.5 | 18.0 | +51.0% | stability plus mid cost |

## Focus Policies By Time Split

| policy | split | rows | dates | cities | cost | pnl | roi | ci_low | ci_high | hit | avg_hour | hrs_to_end | regime_join |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_trigger | all_settled | 165 | 11 | 17 | 101.946 | -6.946 | -6.8% | -15.1% | +2.3% | +57.6% | 6.5 | 41.2 | +13.3% |
| first_trigger | early_shadow_0614_0620 | 99 | 7 | 17 | 60.484 | -6.484 | -10.7% | -20.4% | -0.3% | +54.5% | 9.6 | 39.2 | +18.2% |
| first_trigger | recent_shadow_0621_0624 | 66 | 4 | 17 | 41.462 | -0.462 | -1.1% | -10.4% | +12.3% | +62.1% | 1.9 | 44.2 | +6.1% |
| latest_trigger | all_settled | 165 | 11 | 17 | 109.026 | +9.974 | +9.1% | +2.0% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% |
| latest_trigger | early_shadow_0614_0620 | 99 | 7 | 17 | 68.495 | +3.505 | +5.1% | -3.4% | +12.4% | +72.7% | 11.3 | 13.4 | +72.7% |
| latest_trigger | recent_shadow_0621_0624 | 66 | 4 | 17 | 40.531 | +6.469 | +16.0% | +6.3% | +25.9% | +71.2% | 11.3 | 14.6 | +65.2% |
| latest_before_local_18 | all_settled | 165 | 11 | 17 | 109.021 | +9.979 | +9.2% | +2.0% | +16.1% | +72.1% | 11.3 | 13.9 | +69.7% |
| latest_before_local_18 | early_shadow_0614_0620 | 99 | 7 | 17 | 68.495 | +3.505 | +5.1% | -3.4% | +12.4% | +72.7% | 11.3 | 13.4 | +72.7% |
| latest_before_local_18 | recent_shadow_0621_0624 | 66 | 4 | 17 | 40.526 | +6.474 | +16.0% | +6.4% | +25.9% | +71.2% | 11.2 | 14.7 | +65.2% |

## Calibration Buckets For Focus Policy

Focus policy: `latest_before_local_18`.

| bucket | rows | dates | cities | cost | pnl | roi | ci_low | ci_high | hit | avg_hour | hrs_to_end | regime_join |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model_mass_lt075 | 18 | 9 | 4 | 8.447 | +0.553 | +6.5% | -40.8% | +58.0% | +50.0% | 10.9 | 14.3 | +66.7% |
| model_mass_075_085 | 30 | 10 | 11 | 17.767 | +4.233 | +23.8% | +6.2% | +48.8% | +73.3% | 10.8 | 14.5 | +63.3% |
| model_mass_085_095 | 65 | 10 | 16 | 45.095 | +6.905 | +15.3% | +6.4% | +24.3% | +80.0% | 11.2 | 14.8 | +66.2% |
| model_mass_ge095 | 52 | 11 | 16 | 37.712 | -1.712 | -4.5% | -16.1% | +6.8% | +69.2% | 11.7 | 12.4 | +78.8% |
| cost_le050 | 34 | 10 | 12 | 6.438 | +1.562 | +24.3% | -44.5% | +78.6% | +23.5% | 14.1 | 11.0 | +85.3% |
| cost_050_080 | 67 | 10 | 16 | 46.507 | +5.493 | +11.8% | +1.0% | +23.3% | +77.6% | 10.7 | 15.2 | +64.2% |
| cost_gt080 | 64 | 11 | 16 | 56.076 | +2.924 | +5.2% | -2.7% | +12.4% | +92.2% | 10.4 | 14.1 | +67.2% |

## Regime Overlay

Regime rows are explanatory only. They are more real-time than the forecast-bounded Range RV thesis, so they should not become hard gates without a separate forward test.

| day_regime | rows | dates | cities | cost | pnl | roi | ci_low | ci_high | hit | avg_hour | hrs_to_end | regime_join |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| day_open_runway | 38 | 10 | 13 | 25.938 | +2.062 | +7.9% | -11.9% | +29.1% | +73.7% | 12.4 | 11.9 | +100.0% |
| day_marginal_runway | 23 | 10 | 13 | 14.270 | -1.270 | -8.9% | -45.3% | +22.5% | +56.5% | 12.2 | 12.6 | +100.0% |
| None | 50 | 9 | 16 | 37.333 | +5.667 | +15.2% | +1.3% | +26.2% | +86.0% | 8.1 | 18.6 | +0.0% |
| day_forecast_busted | 17 | 8 | 8 | 11.192 | +1.808 | +16.2% | -7.1% | +34.8% | +76.5% | 13.2 | 12.0 | +100.0% |
| day_forecast_capped | 15 | 6 | 7 | 7.797 | +1.203 | +15.4% | -8.6% | +66.4% | +60.0% | 13.5 | 11.9 | +100.0% |
| day_space_unknown | 22 | 4 | 11 | 12.491 | +0.509 | +4.1% | +0.8% | +6.7% | +59.1% | 12.5 | 11.1 | +100.0% |

| intraday_state | rows | dates | cities | cost | pnl | roi | ci_low | ci_high | hit | avg_hour | hrs_to_end | regime_join |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_warming | 75 | 11 | 17 | 54.487 | +2.513 | +4.6% | -5.1% | +14.9% | +76.0% | 12.0 | 12.3 | +100.0% |
| None | 50 | 9 | 16 | 37.333 | +5.667 | +15.2% | +1.3% | +26.2% | +86.0% | 8.1 | 18.6 | +0.0% |
| fresh_high | 14 | 9 | 6 | 6.080 | +0.920 | +15.1% | -18.6% | +56.4% | +50.0% | 13.6 | 10.2 | +100.0% |
| mature_fade | 9 | 6 | 7 | 2.468 | -0.468 | -19.0% | -100.0% | +29.2% | +22.2% | 16.6 | 12.5 | +100.0% |
| false_fade_risk | 9 | 5 | 7 | 5.813 | +1.187 | +20.4% | -32.8% | +59.9% | +77.8% | 11.7 | 12.0 | +100.0% |
| pullback_uncertain | 4 | 4 | 4 | 0.771 | +0.229 | +29.7% | -100.0% | +36.6% | +25.0% | 15.0 | 8.7 | +100.0% |
| plateau_near_high | 3 | 2 | 3 | 1.390 | -0.390 | -28.1% | -100.0% | -21.1% | +33.3% | 13.0 | 10.8 | +100.0% |
| slow_warming | 1 | 1 | 1 | 0.679 | +0.321 | +47.3% | NA | NA | +100.0% | 10.0 | 13.5 | +100.0% |

## Interpretation

- `first_trigger` remains the wrong entry model. It is the signal-formation phase, not the confirmed forecast-relative-value phase.
- The useful improvement is not a city filter. It is entry timing: use the latest forecast-bounded range before an explicit local cutoff, while preserving enough lead time for this to remain a forecast strategy.
- Stability and mid-cost filters did not improve this sample. They reduce support and erase much of the latest-entry edge, so they should remain diagnostics rather than candidate gates.
- METAR/regime labels explain weather state, but they are closer to real-time current-bracket logic. For Range RV they should be soft diagnostics unless future shadow data proves they add forward excess.
- Cost and model-mass buckets are calibration diagnostics. If high model mass does not map to high hit rate inside a timing policy, the fix is probability calibration, not wider threshold search.

## Current V2 Candidate

`latest_before_local_18` is the current operational candidate to keep shadowing, not to trade live. It means one entry per city/event/source/model: take the latest valid Range RV signal no later than local 18:59 on the target date.

Focus summary: rows `165`, dates `11`, ROI `+9.2%`, CI `+2.1%` to `+16.1%`, hit `+72.1%`.

significance=FAIL, baseline=FAIL, forward=FAIL, conclusion=inconclusive/shadow_only
