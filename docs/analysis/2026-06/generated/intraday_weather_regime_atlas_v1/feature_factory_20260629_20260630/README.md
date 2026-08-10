# Reheat Feature Factory v1

## Data Snapshot

- Data source: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades`, `settlement_outcomes`) plus time-aligned raw orderbook snapshots under `runtime/weather_edge_v1/market_data/orderbook_snapshots`.
- Generated at UTC: `2026-07-22T07:07:27+00:00`.
- DB fact built at UTC: `2026-07-22T07:03:00.520616+00:00`.
- Actual feature target-date range: `2026-06-29`..`2026-06-30` (2 active dates).
- Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome`.
- Evidence layer: time-aligned orderbook replay / opportunity feature layer, not live fills.

## Verdict

This first shared factory is usable for downstream reheat-risk research on observed path, current YES, d1/d2 NO, target YES quotes, and settlement labels. It should replace strategy-private materializers for `current_yes_peak_forming`, `current_yes_fade_confirmed`, `higher_no_carry`, and `low_price_yes_reheat_reversal`.

The former largest gap was forecast peak context. This factory now consumes the documented `forecast_peak_clock_backfill_v1.csv` city-date layer when native `fact_signal_candidates` peak fields are missing, and exposes both GFS and ECMWF peak-clock features. This makes forecast peak clock usable for shared research tables; it is still a backfilled research feature, not proof of production native point-in-time coverage.

No live action is implied. This is an opportunity/replay feature layer, not fill PnL.

## Mandatory SQL Self-Check

```json
{
  "fact_trades_max_built_at_utc": "2026-07-22T07:03:00.520616+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 1111
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
      "settlement_status": "missing_bracket",
      "rows": 31
    },
    {
      "settlement_status": "settled",
      "rows": 4625
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 62258,
    "eligible": 24120,
    "paper_ordered": 5834,
    "live_filled": 510
  },
  "clob_order_fill_join": [
    {
      "status": "blocked",
      "orders": 1866,
      "with_fill": 0
    },
    {
      "status": "cancelled",
      "orders": 5,
      "with_fill": 0
    },
    {
      "status": "error",
      "orders": 64,
      "with_fill": 0
    },
    {
      "status": "failed",
      "orders": 146,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 1373,
      "with_fill": 1111
    }
  ]
}
```

## Data Funnel

| Stage | Rows/count |
|---|---:|
| orderbook files seen | 48 |
| orderbook records seen | 70342 |
| ok orderbook records | 70305 |
| kept before hourly dedupe | 7823 |
| feature rows after hourly dedupe/enrichment | 4758 |
| date/city/hour state rows | 418 |
| complete core state rows | 307 |
| active dates | 2 |
| cities | 36 |

Core state means current temp, running max, minutes since max, current YES quote, d1 NO quote, and final winner are all present.

## Field Coverage

| Field | Feature-row non-null | State coverage |
|---|---:|---:|
| `current_temp_c` | 100.0% | 100.0% |
| `running_max_c` | 100.0% | 100.0% |
| `decline_from_max_c` | 100.0% | 100.0% |
| `minutes_since_running_max` | 100.0% | 100.0% |
| `forecast_peak_hour_local` | 100.0% | 100.0% |
| `forecast_peak_delta_hours_local` | 100.0% | 100.0% |
| `forecast_values_hash` | 100.0% | 100.0% |
| `gfs_forecast_peak_hour_local` | 100.0% | 100.0% |
| `gfs_forecast_peak_delta_hours_local` | 100.0% | 100.0% |
| `ecmwf_forecast_peak_hour_local` | 100.0% | 100.0% |
| `ecmwf_forecast_peak_delta_hours_local` | 100.0% | 100.0% |
| `dwpf_now` | 99.7% | 99.3% |
| `relative_humidity_pct` | 99.7% | 99.3% |
| `wind_speed_kt` | 99.7% | 99.3% |
| `sky_cover_code` | 76.3% | 74.4% |
| `temp_trend_1h_f` | 99.7% | 99.3% |
| `current_yes_ask` | 86.1% | 85.9% |
| `d1_no_ask` | 84.0% | 78.0% |
| `d2_no_ask` | 80.6% | 65.6% |
| `target_yes_ask` | 99.4% | 99.3% |
| `final_winning_bracket` | 100.0% | 100.0% |

## Output Files

- Feature rows CSV: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260629_20260630/reheat_feature_rows.csv`
- Date/city/hour coverage CSV: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260629_20260630/coverage_by_date_city_hour.csv`
- JSON manifest: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260629_20260630/summary.json`

## Date/City/Hour Missing-Field Summary

The coverage CSV has one row per `target_date + city + decision_hour_local` with booleans and a `missing_fields` list. The largest state-level gaps are:

| Missing field | State rows |
|---|---:|
| `d2_no_quote` | 144 |
| `sky` | 107 |
| `d1_no_quote` | 92 |
| `current_yes_quote` | 59 |
| `any_target_yes_quote` | 3 |
| `dewpoint` | 3 |
| `rh` | 3 |
| `temp_trend` | 3 |
| `wind` | 3 |

Use the coverage CSV to inspect the exact date/city/hour rows before running any strategy-head experiment.

## Schema Notes

- `decision_snapshot_ts_utc` is the raw orderbook snapshot timestamp; quotes are not forward-filled from later books.
- `current_bracket` is the YES bracket containing the rounded running max in the market's unit.
- `d1_no_bracket` and `d2_no_bracket` are the first and second higher NO siblings above the running max.
- `target_yes_*` is the YES sibling quote for the row's own bracket, so low-price YES reheat reversal can use the same table.
- `current_bracket_held`, `d1_hit`, `skip_over_d1`, and `target_hit` come from `settlement_outcomes` source-grain truth.
