# Tokyo / HongKong Fast-Source Live Readiness v1

Generated: `2026-07-11T02:32:51.089545+00:00`

## Verdict

- **Tokyo: keep shadow; do not add to live yet.** The same-station settlement basis is correct and JMA is visibly earlier than the next METAR detect, but immediate next-report confirmation is only 6/10 correlated cross increments across two days. This audit has one independently priced, economically eligible cross date, so it cannot pass the strategy contract's significance, baseline, or forward gates.
- **HongKong: keep the dedicated HKO observer shadow-only; do not add it to the generic METAR runner.** HKO is the confirmed settlement source, but realtime HKO-to-Daily-Extract overlap is only two complete source days here. The source profile is intentionally still `live_eligible=false`, and the observer currently carries prior-day active events into the new market day.

## Source First-Seen Latency

| City | Day | unique obs | median min | p90 min | max min |
|---|---:|---:|---:|---:|---:|
| HongKong | 2026-07-08 | 46 | 12.43 | 17.03 | 17.85 |
| HongKong | 2026-07-09 | 97 | 8.40 | 9.19 | 10.81 |
| HongKong | 2026-07-10 | 97 | 8.35 | 8.77 | 10.48 |
| Tokyo | 2026-07-08 | 45 | 12.05 | 16.33 | 46.18 |
| Tokyo | 2026-07-09 | 105 | 8.41 | 11.08 | 16.53 |
| Tokyo | 2026-07-10 | 97 | 7.51 | 7.88 | 12.73 |

The HKO endpoint is named `latest_1min_temperature`, but our actual distinct timestamps in this window are predominantly 10-minute observations. Its observed first-seen delay is therefore the operational fact used here.

## Daily Source / Settlement Alignment

| City | Day | source peak C | source bracket | official HKO max C | official bracket | Polymarket winner | same official bracket |
|---|---:|---:|---:|---:|---:|---:|---|
| Tokyo | 2026-07-08 | 28.3 | 28 |  |  | 28 |  |
| Tokyo | 2026-07-09 | 30.1 | 30 |  |  | 30 |  |
| Tokyo | 2026-07-10 | 30.3 | 30 |  |  | 30 |  |
| HongKong | 2026-07-08 | 31.8 | 31 | 31.8 | 31 | 31 | yes |
| HongKong | 2026-07-09 | 31.6 | 31 | 31.8 | 31 | 31 | yes |
| HongKong | 2026-07-10 | 33.0 | 33 |  |  | 33 |  |

For Tokyo, `source bracket` is arithmetic whole-degree rounding to match the WU rule. For HongKong it is floor(decimal daily max), exactly matching the HKO contract.

## Tokyo Crosses

| Day | JMA first seen UTC | JMA C/bracket | prior METAR max | next METAR max | lead min | next confirms | T-1 NO | final outcome | fresh NO ask | executable then |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| 2026-07-09 | 15:58 | 24.6/25 | 24 | 24 | 9.357 | no | 24 | 30 |  | no |
| 2026-07-09 | 22:30 | 25.6/26 | 25 | 26 | 9.945 | yes | 25 | 30 |  | no |
| 2026-07-09 | 23:06 | 27.0/27 | 26 | 27 | 4.135 | yes | 26 | 30 |  | no |
| 2026-07-09 | 23:48 | 27.8/28 | 27 | 27 | 22.867 | no | 27 | 30 |  | no |
| 2026-07-09 | 01:28 | 28.6/29 | 28 | 29 | 12.42 | yes | 28 | 30 |  | no |
| 2026-07-09 | 04:18 | 29.7/30 | 29 | 29 | 20.624 | no | 29 | 30 | 0.870 | yes |
| 2026-07-10 | 21:37 | 25.5/26 | 25 | 26 | 1.167 | yes | 25 | 30 |  | no |
| 2026-07-10 | 23:47 | 27.5/28 | 27 | 28 | 21.33 | yes | 27 | 30 |  | no |
| 2026-07-10 | 00:27 | 28.5/29 | 28 | 28 | 11.656 | no | 28 | 30 | 0.999 | no |
| 2026-07-10 | 01:46 | 29.5/30 | 29 | 30 | 20.44 | yes | 29 | 30 | 0.995 | no |

This is `6/10` immediate next-report confirmation, but the rows cluster into two Tokyo local dates. Only `3` first-cross records have a captured fresh NO ask; only `1` met the historical execution limits.

## Chain Findings

1. Tokyo uses JMA Haneda (station 44166) as a faster same-airport temperature source and RJTT METAR/WU as the settlement-facing reference. The generic previous-NO runner is semantically valid for this city, but it remains shadow by configuration.
2. HongKong must bypass the generic `fast_source_prev_no_trial` METAR comparison: VHHH is not the payout source. The dedicated HKO observer correctly applies floor semantics and fetches fresh CLOB books, but it is telemetry only.
3. An observed temperature at the authoritative HKO station is structurally suitable to lock `T-1 NO`: the final daily maximum cannot fall below an observed reading. The two available overlaps preserve the same floor bracket even though one raw intraday peak was 0.2C below the Daily Extract. It does not lock `T YES`, because a later higher temperature can make exact bracket `T YES` lose.
4. This audit is source/chain readiness, not a live-performance result. The evidence does not meet the required 10 active days / 30 settled fills support, a price-matched NO baseline, or holdout validation.

## Required Before Promotion

- Tokyo: accumulate at least 10 active days of first-seen JMA/METAR/orderbook telemetry, then evaluate only first cross per `(day, T, prior METAR max)` against a same-price NO baseline and post-fee outcome.
- HongKong: fix the cross-day active-event carryover in the observer, promote `hko_obs` into the source profile only after a forward HKO realtime-to-Daily-Extract audit, and use a separate HKO execution strategy rather than adding HongKong to the generic METAR runner.
