# Reheat Feature Factory v1

## Data Snapshot

- Data source: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades`, `settlement_outcomes`) plus time-aligned raw orderbook snapshots under `runtime/weather_edge_v1/market_data/orderbook_snapshots`.
- Generated at UTC: `2026-06-16T16:02:08+00:00`.
- DB fact built at UTC: `2026-06-16T15:50:05.971834+00:00`.
- Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome`.
- Evidence layer: time-aligned orderbook replay / opportunity feature layer, not live fills.

## Verdict

This first shared factory is usable for downstream reheat-risk research on observed path, current YES, d1/d2 NO, target YES quotes, and settlement labels. It should replace strategy-private materializers for `current_yes_peak_forming`, `current_yes_fade_confirmed`, `higher_no_carry`, and `low_price_yes_reheat_reversal`.

The important gap is forecast peak context: the columns exist in `fact_signal_candidates`, but the current DB snapshot has effectively no populated `forecast_peak_hour_local` or `forecast_values_hash`, so forecast-peak-clock experiments can consume the schema but must wait for upstream population or a documented backfill.

No live action is implied. This is an opportunity/replay feature layer, not fill PnL.

## Mandatory SQL Self-Check

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

## Data Funnel

| Stage | Rows/count |
|---|---:|
| orderbook files seen | 1289 |
| orderbook records seen | 1630457 |
| ok orderbook records | 1594612 |
| kept before hourly dedupe | 165491 |
| feature rows after hourly dedupe/enrichment | 88621 |
| date/city/hour state rows | 8696 |
| complete core state rows | 6210 |
| active dates | 27 |
| cities | 36 |

Core state means current temp, running max, minutes since max, current YES quote, d1 NO quote, and final winner are all present.

## Field Coverage

| Field | Feature-row non-null | State coverage |
|---|---:|---:|
| `current_temp_c` | 100.0% | 100.0% |
| `running_max_c` | 100.0% | 100.0% |
| `decline_from_max_c` | 100.0% | 100.0% |
| `minutes_since_running_max` | 96.9% | 95.8% |
| `forecast_peak_hour_local` | 0.0% | 0.0% |
| `forecast_peak_delta_hours_local` | 0.0% | 0.0% |
| `forecast_values_hash` | 0.0% | 0.0% |
| `dwpf_now` | 98.4% | 98.3% |
| `relative_humidity_pct` | 98.4% | 98.3% |
| `wind_speed_kt` | 98.4% | 98.3% |
| `sky_cover_code` | 74.7% | 74.3% |
| `temp_trend_1h_f` | 98.4% | 98.3% |
| `current_yes_ask` | 84.6% | 81.7% |
| `d1_no_ask` | 85.8% | 79.1% |
| `d2_no_ask` | 81.5% | 67.6% |
| `target_yes_ask` | 98.6% | 97.7% |
| `final_winning_bracket` | 100.0% | 100.0% |

## Output Files

- Feature rows CSV: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv`
- Date/city/hour coverage CSV: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/coverage_by_date_city_hour.csv`
- JSON manifest: `docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.json`

## Date/City/Hour Missing-Field Summary

The coverage CSV has one row per `target_date + city + decision_hour_local` with booleans and a `missing_fields` list. The largest state-level gaps are:

| Missing field | State rows |
|---|---:|
| `forecast_hash` | 8696 |
| `forecast_peak_hour` | 8696 |
| `d2_no_quote` | 2815 |
| `sky` | 2236 |
| `d1_no_quote` | 1818 |
| `current_yes_quote` | 1592 |
| `minutes_since_max` | 368 |
| `any_target_yes_quote` | 202 |
| `temp_trend` | 147 |
| `dewpoint` | 146 |
| `rh` | 146 |
| `wind` | 146 |

Use the coverage CSV to inspect the exact date/city/hour rows before running any strategy-head experiment.

## Schema Notes

- `decision_snapshot_ts_utc` is the raw orderbook snapshot timestamp; quotes are not forward-filled from later books.
- `current_bracket` is the YES bracket containing the rounded running max in the market's unit.
- `d1_no_bracket` and `d2_no_bracket` are the first and second higher NO siblings above the running max.
- `target_yes_*` is the YES sibling quote for the row's own bracket, so low-price YES reheat reversal can use the same table.
- `current_bracket_held`, `d1_hit`, `skip_over_d1`, and `target_hit` come from `settlement_outcomes` source-grain truth.
