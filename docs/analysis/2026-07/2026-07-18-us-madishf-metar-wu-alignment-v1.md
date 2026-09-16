# US MADISHF → METAR → WU Settlement Alignment v1

Generated: `2026-07-18T13:30:16.572508+00:00`
Status: `research_snapshot`

## Action

Keep every US MADISHF expression in shadow. Native-F WU labels are clean, but MADISHF is a proxy feature rather than settlement truth; no live expansion is authorized.

## Target

Estimate city-level MADISHF-to-next-routine-METAR and MADISHF-to-native-F-WU settlement basis, with Atlanta-type persistent false crosses as the primary failure metric.

## Data Integrity

- target dates: `2026-07-07..2026-07-17`
- deduplicated causal MADISHF observations: `3193`
- causal next-METAR comparison rows: `2194`
- first market-cross events: `133`
- persistent market-cross events: `54`
- MADISHF + settled winner city-days: `100`
- native-F WU fetch failures: `0`
- three-way AWC-covered city-days: `70`
- Austin/Dallas/Houston lose AWC routine-METAR coverage after July 7; their daily MADISHF↔WU rows remain, but event-level next-METAR precision is a coverage gap.

## Aggregate Result

- native-F WU max inside market winner: `100/100`.
- routine AWC METAR daily max inside market winner: `69/70`.
- MADISHF daily max inside market winner on peak-covered days: `56/86`; mismatch `30/86`.
- MADISHF minus WU daily max: equal `34`, hotter `29`, cooler `23`; absolute error >=2F `10`.
- persistent crosses: next-METAR confirmed `47/54`; final settlement confirmed `53/54`; Atlanta-type terminal false `1/54` (Wilson 95% `(0.0033, 0.0977)`).
- the median city bias is generally 0F, so a single global offset cannot repair the basis mismatch.

## City Summary

| City | WU days / peak-covered / AWC days | MADISHF daily winner | median bias / MAE vs WU | overshoot / >=2F | persistent next METAR | persistent settlement | Atlanta-like false |
|---|---:|---:|---:|---:|---:|---:|---:|
| `Atlanta` | 10 / 9 / 10 | 4/9 (0.4444) | 0.0F / 0.667F | 2 / 1 | 7/9 (0.7778) | 8/9 (0.8889) | 1 |
| `Austin` | 10 / 8 / 0 | 5/8 (0.625) | 1.0F / 0.875F | 5 / 2 | 0/0 (None) | 0/0 (None) | 0 |
| `Chicago` | 10 / 8 / 10 | 5/8 (0.625) | 0.0F / 0.625F | 2 / 1 | 4/5 (0.8) | 5/5 (1.0) | 0 |
| `Dallas` | 10 / 8 / 0 | 6/8 (0.75) | 0.0F / 0.875F | 3 / 0 | 0/0 (None) | 0/0 (None) | 0 |
| `Houston` | 10 / 9 / 0 | 6/9 (0.6667) | 0.0F / 0.444F | 3 / 0 | 0/0 (None) | 0/0 (None) | 0 |
| `LA` | 10 / 9 / 10 | 5/9 (0.5556) | 0.0F / 0.889F | 3 / 1 | 5/7 (0.7143) | 7/7 (1.0) | 0 |
| `Miami` | 10 / 9 / 10 | 6/9 (0.6667) | 0.0F / 0.556F | 2 / 0 | 10/10 (1.0) | 10/10 (1.0) | 0 |
| `NYC` | 10 / 9 / 10 | 6/9 (0.6667) | 0.0F / 0.778F | 3 / 0 | 5/5 (1.0) | 5/5 (1.0) | 0 |
| `SanFrancisco` | 10 / 9 / 10 | 6/9 (0.6667) | 0.0F / 1.111F | 4 / 1 | 12/12 (1.0) | 12/12 (1.0) | 0 |
| `Seattle` | 10 / 8 / 10 | 7/8 (0.875) | 0.0F / 0.75F | 2 / 1 | 4/6 (0.6667) | 6/6 (1.0) | 0 |

## Atlanta-Type Persistent False Crosses

| City/date | prior bracket | MADISHF | next METAR | WU final | winner | fast-WU |
|---|---:|---:|---:|---:|---:|---:|
| `Atlanta 2026-07-17` | 88-89 | 91.4F | 89F | 89F | 88-89 | 2.4F |

## Funnels

- signal funnel (observation/event): `3193` causal fast observations → `2194` comparable rows → `133` first crosses → `54` persistent crosses.
- evidence funnel (city-day): `100` fast+settled days → `100` native-F WU labels → `86` MADISHF peak-covered days → `70` three-way AWC days.
- book/fill evidence is outside this source-basis study; no opportunity, quote, or fill denominator is inferred here.

## Contract

significance=NA; baseline=NA; forward=FAIL; conclusion=inconclusive

The window is short and starts after collector activation. Results diagnose source basis and do not establish a fee-adjusted market residual.
