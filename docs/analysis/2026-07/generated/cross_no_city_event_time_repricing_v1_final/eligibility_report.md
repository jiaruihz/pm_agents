# High-Frequency Source Strategy Eligibility v2

Status: `research_snapshot`
Generated: `2026-07-18T08:07:23.833255+00:00`

## Verdict

本报告只批准 source/city 进入 shadow feature layer，不直接批准 live。行动状态按双观测确认事件判断，不按重复轮询行或单次跳档判断。

- strict shadow candidates: `Atlanta, Chicago, Helsinki, Miami, NYC, SanFrancisco`
- not ready: `Austin, Dallas, Houston, Istanbul, LA, Seattle, TelAviv`
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
| `Austin` | 2026-07-07..2026-07-18 (12d) | 5.0m / 8.796m | 20.0m / 20.389m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |
| `Dallas` | 2026-07-07..2026-07-18 (12d) | 5.0m / 8.77m | 25.0m / 20.236m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |
| `Houston` | 2026-07-07..2026-07-18 (12d) | 5.0m / 8.774m | 25.0m / 20.676m | `NA: missing_concurrent_awc_label_after_primary_source_switch` |

## Data Integrity

- deduplicated fast observations: `5473`
- causal event comparisons: `4132`
- first-cross events: `146`
- persistent two-observation cross events: `53`
- settlement/proxy city-days: `114`
- paired AWC/Synoptic reports: `907`
- next METAR is deduplicated by city/report timestamp; the same report arriving on another route is not a new label.
- settlement proxy is WU history for default-WU cities and WRH/Synoptic for Istanbul/TelAviv.
- live eligibility is judged against the actual winning bracket when settled; WU-history daily max is only a bias diagnostic because monitoring began on July 10.
- Austin/Dallas/Houston switched source-events primary to Synoptic; without concurrent AWC rows, neither the new Synoptic path nor IEM MADISHF can be independently labelled as lead-to-next-METAR there.

## City And Source

| City/source | Market | Sample | Fast obs cadence / detect lag | METAR cadence | Single next-METAR | Persistent next-METAR | Persistent settled | Median lead | Action status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `Atlanta/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 20.439m | 60.0m | 17/18 (0.9444) | 6/6 (1.0) | 6/6 (1.0) | 3.065m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Austin/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Chicago/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 20.847m | 60.0m | 9/12 (0.75) | 3/4 (0.75) | 4/4 (1.0) | 9.935m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Dallas/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Helsinki/fmi` | `exact_1c` | 2026-07-08..2026-07-18 (11d) | 10.0m / 3.166m | 30.0m | 19/25 (0.76) | 8/10 (0.8) | 10/10 (1.0) | 10.346m | `strict_shadow_candidate` / `blocked_low_sample` |
| `Houston/noaa_madis_hfmetar` | `range_2f` | .. (0d) | m / m | m | 0/0 (None) | 0/0 (None) | 0/0 (None) | m | `accumulate_only` / `blocked_low_sample` |
| `Istanbul/mgm` | `exact_1c` | 2026-07-10..2026-07-18 (9d) | 10.0m / 19.244m | 30.0m | 3/14 (0.2143) | 2/3 (0.6667) | 3/3 (1.0) | 15.893m | `accumulate_only` / `blocked_low_sample` |
| `LA/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 22.023m | 60.0m | 8/13 (0.6154) | 3/5 (0.6) | 5/5 (1.0) | 20.766m | `accumulate_only` / `blocked_low_sample` |
| `Miami/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 21.658m | 60.0m | 15/19 (0.7895) | 7/7 (1.0) | 7/7 (1.0) | 4.414m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `NYC/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 22.203m | 60.0m | 11/13 (0.8462) | 4/4 (1.0) | 4/4 (1.0) | 3.379m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `SanFrancisco/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 22.55m | 60.0m | 8/13 (0.6154) | 7/7 (1.0) | 7/7 (1.0) | 4.891m | `strict_shadow_candidate` / `blocked_range_handler_and_low_sample` |
| `Seattle/noaa_madis_hfmetar` | `range_2f` | 2026-07-08..2026-07-17 (10d) | 20.0m / 22.516m | 60.0m | 10/15 (0.6667) | 4/6 (0.6667) | 6/6 (1.0) | 4.274m | `accumulate_only` / `blocked_low_sample` |
| `TelAviv/ims_lod` | `exact_1c` | 2026-07-10..2026-07-17 (6d) | 10.0m / 16.446m | 30.0m | 4/4 (1.0) | 1/1 (1.0) | 1/1 (1.0) | 0.504m | `accumulate_only` / `blocked_low_sample` |

## First-Arrival Route

- `Istanbul`: paired reports `369`, median Synoptic-AWC `-0.66` sec, Synoptic first rate `0.6856`.
- `TelAviv`: paired reports `376`, median Synoptic-AWC `139.998` sec, Synoptic first rate `0.4096`.

## Not Yet Evaluable

True Synoptic `ICAO1M` OMO, WIS2, Météo-France, KNMI, DWD, AEMET, MetService and ECCC have no production event history yet. They remain probe candidates and are not assigned strategy eligibility from documentation alone.

## Contract

significance=NA; baseline=NA; forward=FAIL; conclusion=shadow_candidate

The sample is below the 10-day/30-fill live threshold. No live city-pool, size, or execution-policy change is authorized.
