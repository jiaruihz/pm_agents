# US direct MADIS OMO → IEM → WU alignment v1

Generated: `2026-07-18T19:16:19.889889+00:00`
Status: `research_snapshot`; no live authorization

## Action

Direct MADIS improves latency but does not improve settlement accuracy: it is the same raw OMO signal carried by IEM. Keep US previous-bracket NO in shadow. NOAA temperature QC does not catch the Atlanta terminal false cross.

## Fixed denominator

- dates: `2026-07-13..2026-07-17`
- cities: `10` US airport markets with WU labels
- direct source: NOAA public `LDAD/hfmetar/netCDF`, same station and exact observation timestamp
- settlement label: native-F WU-aligned winning market bracket from the prior v1 study
- persistent expression: BUY NO on the previous Fahrenheit bracket

## Source identity and latency

- direct observations: `12775`; hourly download failures: `17` (all are the expired 2026-07-13 00:00-16:00 UTC portion of the public rolling archive)
- same-timestamp direct↔IEM overlap: `1522`
- direct↔IEM rounded-temperature agreement: `1522/1522 (100.0%)`
- median NOAA received lag: `136.0` seconds
- median IEM first-seen lag on the same observations: `1269.1` seconds

The faster feed is not a new thermometer or a new settlement basis. It is earlier delivery of the same OMO observations.

## Accuracy hierarchy against final WU

| source / expression | fixed denominator | aligned with final WU |
|---|---:|---:|
| native-F WU final vs winning market bracket | city-day | `100/100 (100.0%)` |
| routine AWC METAR daily max vs winning bracket | city-day | `69/70 (98.6%)` |
| IEM MADISHF OMO daily max vs winning bracket, WU-peak-covered | city-day | `56/86 (65.1%)` |
| direct NOAA MADIS OMO daily max vs winning bracket, full files | city-day | `22/40 (55.0%)` |
| MADISHF persistent previous-bracket NO vs WU final | event | `53/54 (98.1%)` |

WU native-F is the settlement label and passes the market-winner contract. Routine METAR is close to that label but is later. Both IEM MADISHF and direct MADIS are the faster OMO family; direct delivery changes latency, not the underlying basis risk.

## Daily max vs WU settlement

- city-days with direct coverage spanning the WU peak: `49/50`
- city-days with every local-day hourly file still downloadable: `40/50`
- direct MADIS daily max equals IEM daily max: `39/50 (78.0%)`
- direct MADIS daily max falls inside the WU winning bracket (WU-peak-covered): `28/49 (57.1%)`
- same result on fully downloadable local days: `22/40 (55.0%)`
- fully downloadable day bias: mean `+0.375F`, MAE `0.875F`; delta distribution `{-2: 1, -1: 8, 0: 11, 1: 15, 2: 5}`

| city | days | direct=IEM daily max | direct in WU winner | MAE vs WU |
|---|---:|---:|---:|---:|
| `Atlanta` | 5 | 5/5 | 2/5 | 0.80F |
| `Austin` | 5 | 5/5 | 2/5 | 1.00F |
| `Chicago` | 5 | 4/5 | 3/5 | 0.80F |
| `Dallas` | 5 | 2/5 | 3/5 | 0.80F |
| `Houston` | 4 | 3/4 | 3/4 | 0.50F |
| `LA` | 5 | 4/5 | 3/5 | 1.00F |
| `Miami` | 5 | 5/5 | 4/5 | 0.60F |
| `NYC` | 5 | 5/5 | 3/5 | 0.80F |
| `SanFrancisco` | 5 | 3/5 | 2/5 | 1.20F |
| `Seattle` | 5 | 3/5 | 3/5 | 1.00F |

The direct=IEM daily-max row is not an identity test: the historical IEM collector sampled fewer observations than the direct hourly archive. Exact same-timestamp observations above are the identity test. Daily exact agreement is a source-basis diagnostic, not the previous-NO label. A source can miss the final exact bracket but still correctly prove that an older bracket was left.

## Persistent previous-NO label

- persistent events with direct timestamp match: `25/28`
- direct event temperature equals IEM event temperature: `25/25 (100.0%)`
- settlement left the old bracket: `24/25 (96.0%)`
- terminal false crosses: `1`
- false crosses with `temperatureQCR=0`: `1/1`

The Atlanta 2026-07-17 91.4F observation is present in direct NOAA MADIS, arrives about 134 seconds after observation, and carries `temperatureQCR=0`; neither direct delivery nor the available QC flag removes it.

## Decision

1. Direct MADIS/LDM can solve most of the 24-minute IEM delay.
2. It cannot solve OMO→WU basis risk because it is the identical raw observation family.
3. No US live order should be authorized from a raw cross alone. A future direct-source model must estimate `P(WU leaves old bracket | OMO path, margin, persistence, station bias, time)` and beat the same-time market after fee/depth.
4. Continue only zero-notional direct collector until at least 30 new settled persistent events with complete direct-book coverage.

## Contract

significance=NA; baseline=IEM same-source + WU; forward=FAIL for raw deterministic trigger; conclusion=direct source useful for latency, not sufficient for live
