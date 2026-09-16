# Reheat Feature Factory v1

## Data Snapshot

- Data source: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades`, `settlement_outcomes`) plus time-aligned raw orderbook snapshots under `runtime/weather_edge_v1/market_data/orderbook_snapshots`.
- Generated at UTC: `2026-06-29T08:50:13+00:00`.
- DB fact built at UTC: `2026-06-29T08:45:31.922088+00:00`.
- Actual feature target-date range: `2026-06-24`..`2026-06-24` (1 active dates).
- Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome`.
- Evidence layer: time-aligned orderbook replay / opportunity feature layer, not live fills.

## Verdict

This first shared factory is usable for downstream reheat-risk research on observed path, current YES, d1/d2 NO, target YES quotes, and settlement labels. It should replace strategy-private materializers for `current_yes_peak_forming`, `current_yes_fade_confirmed`, `higher_no_carry`, and `low_price_yes_reheat_reversal`.

The former largest gap was forecast peak context. This factory now consumes the documented `forecast_peak_clock_backfill_v1.csv` city-date layer when native `fact_signal_candidates` peak fields are missing, and exposes both GFS and ECMWF peak-clock features. This makes forecast peak clock usable for shared research tables; it is still a backfilled research feature, not proof of production native point-in-time coverage.

No live action is implied. This is an opportunity/replay feature layer, not fill PnL.

## Mandatory SQL Self-Check

```json
{
  "fact_trades_max_built_at_utc": "2026-06-29T08:45:31.922088+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 865
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
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4260
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 40156,
    "eligible": 14660,
    "paper_ordered": 5803,
    "live_filled": 355
  },
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 971,
      "with_fill": 865
    }
  ]
}
```

## Data Funnel

| Stage | Rows/count |
|---|---:|
| orderbook files seen | 48 |
| orderbook records seen | 67898 |
| ok orderbook records | 67873 |
| kept before hourly dedupe | 4476 |
| feature rows after hourly dedupe/enrichment | 2370 |
| date/city/hour state rows | 243 |
| complete core state rows | 185 |
| active dates | 1 |
| cities | 32 |

Core state means current temp, running max, minutes since max, current YES quote, d1 NO quote, and final winner are all present.

## Field Coverage

| Field | Feature-row non-null | State coverage |
|---|---:|---:|
| `current_temp_c` | 100.0% | 100.0% |
| `running_max_c` | 100.0% | 100.0% |
| `decline_from_max_c` | 100.0% | 100.0% |
| `minutes_since_running_max` | 100.0% | 100.0% |
| `forecast_peak_hour_local` | 53.2% | 100.0% |
| `forecast_peak_delta_hours_local` | 53.2% | 100.0% |
| `forecast_values_hash` | 53.2% | 100.0% |
| `gfs_forecast_peak_hour_local` | 0.0% | 100.0% |
| `gfs_forecast_peak_delta_hours_local` | NA | 100.0% |
| `ecmwf_forecast_peak_hour_local` | 0.0% | 100.0% |
| `ecmwf_forecast_peak_delta_hours_local` | NA | 100.0% |
| `dwpf_now` | 99.8% | 99.2% |
| `relative_humidity_pct` | 99.8% | 99.2% |
| `wind_speed_kt` | 99.8% | 99.2% |
| `sky_cover_code` | 73.4% | 76.5% |
| `temp_trend_1h_f` | 99.8% | 99.2% |
| `current_yes_ask` | 83.4% | 85.2% |
| `d1_no_ask` | 86.7% | 81.1% |
| `d2_no_ask` | 84.5% | 70.4% |
| `target_yes_ask` | 99.4% | 97.9% |
| `final_winning_bracket` | 100.0% | 100.0% |

## Output Files

- Feature rows CSV: `docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/reheat_feature_rows.csv`
- Date/city/hour coverage CSV: `docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/coverage_by_date_city_hour.csv`
- JSON manifest: `docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/summary.json`

## Date/City/Hour Missing-Field Summary

The coverage CSV has one row per `target_date + city + decision_hour_local` with booleans and a `missing_fields` list. The largest state-level gaps are:

| Missing field | State rows |
|---|---:|
| `d2_no_quote` | 72 |
| `sky` | 57 |
| `d1_no_quote` | 46 |
| `current_yes_quote` | 36 |
| `any_target_yes_quote` | 5 |
| `dewpoint` | 2 |
| `rh` | 2 |
| `temp_trend` | 2 |
| `wind` | 2 |

Use the coverage CSV to inspect the exact date/city/hour rows before running any strategy-head experiment.

## Schema Notes

- `decision_snapshot_ts_utc` is the raw orderbook snapshot timestamp; quotes are not forward-filled from later books.
- `current_bracket` is the YES bracket containing the rounded running max in the market's unit.
- `d1_no_bracket` and `d2_no_bracket` are the first and second higher NO siblings above the running max.
- `target_yes_*` is the YES sibling quote for the row's own bracket, so low-price YES reheat reversal can use the same table.
- `current_bracket_held`, `d1_hit`, `skip_over_d1`, and `target_hit` come from `settlement_outcomes` source-grain truth.
