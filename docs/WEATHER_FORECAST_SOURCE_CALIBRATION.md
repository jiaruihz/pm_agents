# Weather Forecast Source Calibration

Status: current-reference
Updated: 2026-07-08 multi-model historical backfill
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_DATA_FEED_MODULE.md; WEATHER_PROBABILITY_MODEL_ROADMAP.md

## Purpose

This is the living entry for forecast-source calibration.

The core question is:

```text
For each city and official settlement station, which forecast source/model is
historically closest to final WU/Polymarket settlement, and how wide is the
remaining error distribution?
```

This document does not define a live selector. It defines how forecast source
quality should be measured and consumed by downstream probability models,
shadow features, and sizing.

## Current Evidence

Current durable reports:

| Report | Role |
|---|---|
| [2026-07-08-historical-forecast-enrichment-bias-v1](analysis/2026-07/2026-07-08-historical-forecast-enrichment-bias-v1.md) | Main historical backfill: 2026-05-04..2026-07-07, official station coordinates, Open-Meteo multi-model daily max vs `settlement_outcomes` |
| [2026-07-08-forecast-enrichment-settlement-bias-v1](analysis/2026-07/2026-07-08-forecast-enrichment-settlement-bias-v1.md) | First live `forecast_enrichment` capture day, useful as a capture/protocol smoke test |
| [2026-06-30-historical-forecast-station-bias-v1](analysis/2026-06/2026-06-30-historical-forecast-station-bias-v1.md) | Older GFS/ECMWF-only station-bias baseline from legacy weather-predict caches |

Current generated artifacts:

```text
docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/
  daily_error_rows.csv
  city_model_summary.csv
  city_best_model.csv
  overall_model_summary.csv
  station_coordinates.csv
  metadata.json
```

Rebuild command:

```bash
.venv/bin/python scripts/analysis/forecast_quality/research_historical_forecast_enrichment_bias_v1.py --fetch-missing
```

The script caches AviationWeather stationinfo and Open-Meteo historical daily
forecast responses under the generated output directory. Re-running the same
window uses cache.

## Data Contract

Labels:

- Source table: `runtime/weather.db:settlement_outcomes`.
- Label row: winning bracket where `final_price >= 0.999` and `settlement_status='settled'`.
- Label value: winning bracket midpoint converted to Fahrenheit.
- Limitation: this is not raw WU tenth-degree truth. Celsius exact brackets have rounding noise.

Forecasts:

- Source: Open-Meteo historical forecast endpoint.
- Fields: per-model `temperature_2m_max`.
- Coordinate source: `weather_data_feed/source_profiles.json`.
- Station coordinate resolution:
  - prefer `official_station_or_feed` when it is an ICAO/METAR station;
  - parse `site=XXXX` from official URL sources such as `weather.gov` timeseries;
  - skip unresolved or non-airport special sources rather than silently using old configured airports.

Skipped in the current run:

| City | Reason |
|---|---|
| HongKong | `HKO` special source, not airport-station forecast coordinate |
| Moscow | `unknown_effective_source` |
| Seoul | `unknown_effective_source` |
| Shenzhen | `unresolved_wu_feed` |

## Current Findings

Historical backfill window:

```text
2026-05-04..2026-07-07
45 covered cities
26741 city-date-model rows
18 Open-Meteo model keys observed
```

Overall model ranking by MAE:

| Model | Coverage | MAE F | Bias F | Notes |
|---|---:|---:|---:|---|
| ICON-D2 | 5 cities / 289 rows | 1.04 | +0.23 | Strong but Europe-only, limited coverage |
| ICON-EU | 11 cities / 635 rows | 1.26 | +0.56 | Strong European regional model |
| AROME HD | 6 cities / 351 rows | 1.46 | -0.81 | Strong but tends overforecast in this sample |
| ICON | 45 cities / 2454 rows | 2.09 | +0.50 | Best broad-coverage alternative |
| GFS | 45 cities / 2454 rows | 2.39 | +0.07 | Strong baseline in several cities |
| ECMWF | 45 cities / 2454 rows | 2.72 | +0.42 | Not globally dominant in this window |

City-level best-model distribution, requiring `n >= 20`:

| Best-model MAE bucket | City count | Interpretation |
|---|---:|---|
| `<= 1.0F` | 4 | Forecast can be a strong prior, still not an exact answer |
| `1.0F..1.5F` | 13 | Good forecast-prior cities |
| `1.5F..2.0F` | 14 | Usable with observation path and market confirmation |
| `2.0F..2.5F` | 7 | Weak prior; size down or require stronger live evidence |
| `2.5F..3.5F` | 3 | Do not trade from single-point forecast |
| `> 3.5F` | 2 | Forecast max is too noisy for direct bracket inference |

Lowest-error examples:

| City | Station | Best model | MAE F |
|---|---|---|---:|
| TelAviv | LLBG | ICON-EU | 0.76 |
| LA | KLAX | GFS | 0.81 |
| London | EGLC | ICON-D2 | 0.90 |
| Milan | LIMC | ICON-D2 | 0.93 |
| Miami | KMIA | GFS | 1.00 |
| Paris | LFPB | ICON-D2 | 1.09 |
| Amsterdam | EHAM | ICON-D2 | 1.12 |
| Munich | EDDM | ICON-D2 | 1.17 |

Highest-error examples:

| City | Station | Best model | MAE F |
|---|---|---|---:|
| Chicago | KORD | AI-GFS | 4.60 |
| Denver | KBKF | GFS Global | 4.36 |
| MexicoCity | MMMX | RDPS | 3.07 |
| Austin | KAUS | GFS | 2.97 |
| Guangzhou | ZGGG | GFS Global | 2.90 |

New models with meaningful improvement over old `min(GFS, GFS Global, ECMWF)`:

| City | Best model | Best MAE F | Old-best MAE F | Gain F |
|---|---|---:|---:|---:|
| Munich | ICON-D2 | 1.17 | 3.21 | 2.05 |
| Karachi | ICON | 1.27 | 2.34 | 1.08 |
| Ankara | GDPS | 1.58 | 2.59 | 1.01 |
| Lucknow | ICON | 1.82 | 2.67 | 0.85 |
| KualaLumpur | ICON | 1.81 | 2.58 | 0.77 |
| Amsterdam | ICON-D2 | 1.12 | 1.69 | 0.57 |
| Milan | ICON-D2 | 0.93 | 1.49 | 0.56 |

## Interpretation

Forecast is not useless, but raw single-point forecast is not a trading signal.

For exact bracket markets, even a `1.5F..2.5F` MAE can move probability across
multiple contracts. A forecast max should be treated as a prior center plus an
error distribution:

```text
forecast max
+ city/model historical error distribution
+ observed path / running max
+ peak clock / heating window
+ weather context
+ market price
= calibrated P(each exact bracket)
```

Do not use:

```text
forecast max == final settlement temperature
```

## Reliability Buckets

Use the city best-model MAE as a reliability prior:

| Bucket | Rule | Default use |
|---|---|---|
| A | `best_mae_f <= 1.25` | Strong forecast prior; can influence bracket distribution materially |
| B | `1.25 < best_mae_f <= 2.0` | Usable prior; require observation path and price confirmation |
| C | `2.0 < best_mae_f <= 2.5` | Weak prior; smaller size or shadow-only unless live path is strong |
| D | `best_mae_f > 2.5` | Do not drive direction from single forecast max |

Current A/B/C/D boundaries are research defaults, not production gates. They
should be calibrated against strategy-specific forward outcomes before changing
live sizing.

## Strategy Consumption

Recommended feature fields:

```text
forecast_reliability_bucket
forecast_best_model_label
forecast_best_model_mae_f
forecast_best_model_bias_f
forecast_old_source_mae_f
forecast_model_gain_vs_old_f
forecast_error_underforecast_ge_1f_pct
forecast_error_overforecast_ge_1f_pct
```

Recommended usage:

- Distribution models: use city/model residual distributions to spread mass
  around forecast max.
- HeadA / forecast-tail: use reliability as a confidence/sizing feature, not a
  hard city whitelist.
- Regime NO / current bracket paths: use forecast reliability to decide how much
  to trust forecast ceiling vs live observation path.
- Live promotion: require same-denominator replay and shadow evidence after
  adding these fields.

Do not directly switch production `CITY_MODEL` from this document alone.

## Next Work

1. Materialize the reliability fields into the canonical feature layer.
2. Recompute HeadA / regime / tmax replays with reliability fields as shadow
   features.
3. Extend labels from bracket midpoint to raw observed max where official station
   daily max is available.
4. Add PIT run-horizon comparison: previous-day 00Z/06Z/12Z/18Z vs same-day
   snapshots.
5. Resolve HongKong, Moscow, Seoul, Shenzhen source coordinates or keep them
   excluded from forecast-source calibration.
