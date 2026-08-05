# High-Frequency Source Strategy Eligibility v2

Status: `research_snapshot`
Generated: `2026-07-14T16:39:55.328105+00:00`

## Verdict

本报告只批准 source/city 进入 shadow feature layer，不直接批准 live。行动状态按双观测确认事件判断，不按重复轮询行或单次跳档判断。

- strict shadow candidates: `Atlanta, Chicago, Helsinki, Miami, SanFrancisco`
- not ready: `Austin, Dallas, Houston, Istanbul, LA, NYC, Seattle, TelAviv`
- US tiny live remains blocked: the market is normally a 2°F range ladder and the current generic exact-bracket executor is not a valid trade-expression handler.

## Signal Definition

- causal window: fast observation must be first seen no more than 30 minutes after its observation timestamp; the next distinct AWC METAR must first appear within 90 minutes.
- single cross: the rounded fast value leaves the market bracket containing the prior known METAR running max.
- persistent cross: two distinct observation timestamps stay at least 0.5 market units above that bracket's upper edge, the latest is at least 0.7 above it, and the latest detection is within 20 minutes of the next routine METAR report clock.
- US example: when the prior max is inside `90-91`, thresholds are `>=91.5F` then `>=91.7F`; a move from 90F to 91F is not a market-bracket cross.
- next-METAR precision: the next distinct AWC report also leaves the prior bracket. Settled precision: the final winning bracket differs from the prior bracket.

## New US Source Speed

The rows below are the standard Synoptic 5-minute station feed currently used for Austin/Dallas/Houston. They are faster than the IEM MADISHF rows in the city table, but they are not the true `ICAO1M` one-minute OMO product.

| City | Sample | New Synoptic cadence / lag | Old IEM cadence / lag | Cross precision |
|---|---:|---:|---:|---|
| `Austin` | 2026-07-07..2026-07-14 (8d) | 5.0m / 8.798m | 20.0m / 20.819m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |
| `Dallas` | 2026-07-07..2026-07-14 (8d) | 5.0m / 8.789m | 25.0m / 20.401m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |
| `Houston` | 2026-07-07..2026-07-14 (8d) | 5.0m / 8.774m | 25.0m / 20.626m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |

## Data Integrity

- deduplicated fast observations: `3691`
- causal event comparisons: `2711`
- first-cross events: `92`
- persistent two-observation cross events: `38`
- settlement/proxy city-days: `91`
- paired AWC/Synoptic reports: `408`
- next METAR is deduplicated by city/report timestamp; the same report arriving on another route is not a new label.
- settlement proxy is WU history for default-WU cities and WRH/Synoptic for Istanbul/TelAviv.
- live eligibility is judged against the actual winning bracket when settled; WU-history daily max is only a bias diagnostic because monitoring began on July 10.
- Austin/Dallas/Houston switched source-events primary to Synoptic; without concurrent AWC rows, neither the new Synoptic path nor IEM MADISHF can be independently labelled as lead-to-next-METAR there.

## City And Source

| City/source | Market | Sample | Fast obs cadence / detect lag | METAR cadence | Single next-METAR | Persistent next-METAR | Persistent settled | Median lead | Action status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `Atlanta/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 20.954m | 60.0m | 10/10 (1.0) | 3/3 (1.0) | 3/3 (1.0) | 2.937m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Austin/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Chicago/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 25.0m / 21.618m | 60.0m | 6/9 (0.6667) | 3/4 (0.75) | 4/4 (1.0) | 9.935m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Dallas/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Helsinki/fmi` | `exact_1c` | 2026-07-08..2026-07-14 (7d) | 10.0m / 4.339m | 30.0m | 14/18 (0.7778) | 7/8 (0.875) | 8/8 (1.0) | 10.156m | `strict_shadow_candidate` / `blocked_low_sample` |
| `Houston/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Istanbul/mgm` | `exact_1c` | 2026-07-10..2026-07-14 (5d) | 10.0m / 19.13m | 30.0m | 2/9 (0.2222) | 2/3 (0.6667) | 3/3 (1.0) | 15.893m | `accumulate_only` / `blocked_low_sample` |
| `LA/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 21.902m | 60.0m | 5/8 (0.625) | 1/3 (0.3333) | 3/3 (1.0) | 20.766m | `accumulate_only` / `blocked_low_sample` |
| `Miami/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 20.807m | 60.0m | 8/11 (0.7273) | 4/4 (1.0) | 4/4 (1.0) | 4.045m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `NYC/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 21.947m | 60.0m | 9/10 (0.9) | 3/3 (1.0) | 3/3 (1.0) | 3.215m | `accumulate_only` / `blocked_low_sample` |
| `SanFrancisco/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 22.924m | 60.0m | 3/6 (0.5) | 5/5 (1.0) | 5/5 (1.0) | 4.086m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Seattle/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-14 (7d) | 20.0m / 22.683m | 60.0m | 4/8 (0.5) | 2/4 (0.5) | 4/4 (1.0) | 3.39m | `accumulate_only` / `blocked_low_sample` |
| `TelAviv/ims_lod` | `exact_1c` | 2026-07-10..2026-07-14 (5d) | 10.0m / 16.446m | 30.0m | 3/3 (1.0) | 1/1 (1.0) | 1/1 (1.0) | 0.504m | `accumulate_only` / `blocked_low_sample` |

## First-Arrival Route

- `Istanbul`: paired reports `203`, median Synoptic-AWC `-0.64` sec, Synoptic first rate `0.6897`.
- `TelAviv`: paired reports `205`, median Synoptic-AWC `140.004` sec, Synoptic first rate `0.3659`.

## Not Yet Evaluable

True Synoptic `ICAO1M` OMO, WIS2, Météo-France, KNMI, DWD, AEMET, MetService and ECCC have no production event history yet. They remain probe candidates and are not assigned strategy eligibility from documentation alone.

## Contract

significance=NA; baseline=NA; forward=FAIL; conclusion=shadow_candidate

The sample is below the 10-day/30-fill live threshold. No live city-pool, size, or execution-policy change is authorized.
