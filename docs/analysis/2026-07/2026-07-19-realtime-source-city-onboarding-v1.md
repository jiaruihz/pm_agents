# Realtime source city onboarding v1

Generated: `2026-07-19T01:36:00.299422+00:00`
Status: `collector_research_only`; no live authorization

## Action

Connect only zero-notional research collectors, in this order:

1. **Paris / Météo-France 6-minute LFPB** and **Amsterdam / KNMI 10-minute EHAM** are the two strongest additions. They point at the configured settlement station and both cities have current market plus settlement coverage.
2. **Madrid / AEMET LEMD** is the next free-key probe, but measure actual cadence and publication lag before promoting it to a continuous collector.
3. **TelAviv / IMS 1-minute LLBG** and **Wellington / MetService 1-minute NZWN** are technically attractive second-wave probes. TelAviv has a non-WU settlement contract; Wellington needs a commercial trial.
4. Do not add Munich DWD or Toronto ECCC as latency sources now. Taipei CWA and Jeddah NCM remain blocked by station basis, not by missing code.

“Connect” here means raw first-seen + routine-reference + WU/rules settlement + fresh-book telemetry. It does not mean enabling a previous-NO order path.

## Candidate matrix

| priority | city/source | nominal cadence | source → settlement station | station match | current smoke | canonical market/labels | action |
|---|---|---:|---|---:|---|---|---|
| `P1_connect_shadow` | `Paris/meteofrance_6m` | 6m | LFPB → LFPB | 1 | `auth_required` | 10d/178 rows; 75 settled days | `obtain_free_api_credential_and_collect` |
| `P1_connect_shadow` | `Amsterdam/knmi` | 10m | EHAM → EHAM | 1 | `auth_required` | 10d/198 rows; 67 settled days | `obtain_api_key_and_collect` |
| `P2_probe_then_collect` | `Madrid/aemet_10m` | unknown_near_realtime | LEMD → LEMD | 1 | `auth_required` | 10d/192 rows; 75 settled days | `obtain_api_key_and_measure_cadence_first` |
| `P2_probe_then_collect` | `TelAviv/ims_1m` | 1m | LLBG → https://www.weather.gov/wrh/timeseries?site=LLBG | 1 | `auth_required` | 10d/162 rows; 67 settled days | `obtain_token_and_parallel_existing_ims_lod` |
| `P2_probe_then_collect` | `Wellington/metservice_1m` | 1m | NZWN → NZWN | 1 | `auth_required` | 11d/186 rows; 67 settled days | `request_trial_only_after_P1` |
| `defer_no_speed` | `Munich/dwd_10m` | 10m | EDDM → EDDM | 1 | `ok; wall age 46.0m` | 10d/192 rows; 67 settled days | `do_not_add_realtime_collector` |
| `defer_no_speed` | `Toronto/eccc_swob` | 60m_MAN | CYYZ → missing | 0 | `ok; wall age 36.0m` | 0d/0 rows; 0 settled days | `do_not_add_until_AUTO_or_AMQP` |
| `defer_basis` | `Taipei/cwa` | 10m | 466920 → RCSS | 0 | `auth_required` | 11d/199 rows; 67 settled days | `do_not_use_as_bracket_trigger` |
| `defer_basis` | `Jeddah/ncm_jeddah` | unknown | OEJN → OEJN | 1 | `auth_required` | 10d/185 rows; 67 settled days | `resolve_station_and_access_contract_first` |

The current smoke was executed from the research environment. Auth-required means the adapter is present but no credential was available; no credential value is written to this report. Munich and Toronto public endpoints returned data, but their observed wall age and cadence do not beat routine METAR.

## Atlanta admission test for every new source

The 2026-07-17 Atlanta failure is the required negative control: MADISHF/OMO printed `91.4F`, direct MADIS contained the same observation with `temperatureQCR=0`, while routine METAR and native-F WU finished at `89F` and the `88-89` bracket won. A new source is not trusted merely because it is official, same-airport, persistent, or QC-clean.

For each candidate city/source, collect and report:

1. `source first-seen → same-timestamp routine METAR → native-F WU/rules settlement` with native units and no double rounding.
2. Daily-max winning-bracket match, signed source-WU error, and Atlanta-type `terminal_false_cross` frequency.
3. First event per city-day/bracket, not repeated polls; separate single cross and persistent cross.
4. Fresh direct-book cost/depth at source first-seen and correct-versus-false executable/fill rates.
5. A calibrated `P(final leaves old bracket | source/path/time/basis)` versus same-time market probability; raw cross is only a feature.

## Existing evidence that sets the bar

- native-F WU final vs winning bracket: `100/100`.
- routine AWC METAR daily max vs winning bracket: `69/70`.
- IEM MADISHF OMO daily max vs winner on peak-covered days: `56/86`; direct MADIS complete days: `22/40`.
- US persistent previous-NO: `53/54`, but correct runner events executable within ten minutes `0/22`; the one false Atlanta event became executable and filled.
- Existing non-US same/near-airport shadows with the cleanest next-METAR basis remain Tokyo/JMA, Singapore/MSS, Busan/AMOS, and Helsinki/FMI. They stay collector/shadow evidence and are not promoted by this report.

## Funnels

Signal funnel (source observation grain): authenticated raw rows → distinct observation timestamps → first bracket cross → first city-day expression.

Evidence funnel (city-day/event grain): PIT first-seen → concurrent routine reference → WU/rules settlement → fresh executable book → fill. The five auth/contract rows above currently stop before raw coverage; that is a coverage gap, not a failed strategy signal.

## Files

- `candidate_source_matrix.csv`: adapter smoke, station basis, current market coverage, and onboarding action.

## Contract

significance=NA; baseline=same-time market + routine reference; forward=FAIL; conclusion=Paris/Amsterdam P1 collector candidates, no new live city
