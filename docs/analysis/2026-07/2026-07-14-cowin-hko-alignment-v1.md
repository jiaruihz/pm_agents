# CoWIN to HKO Alignment v1

Status: `snapshot`
Generated: `2026-07-14T12:09:52.568422+00:00`

## Summary

- Coverage: `2026-07-11, 2026-07-12, 2026-07-13, 2026-07-14` (4 overlapping days).
- Same-minute matched rows: `330`; Pearson `0.8715`.
- CoWIN-HKO bias: mean `0.802 C`, median `0.2 C`, MAE `0.972 C`.
- Raw floor-cross precision: `0.7895`; shared crosses `15`; median PIT detection lead `138.9 min`.
- Raw persistent rule: `{'triggers': 16, 'hits': 10, 'precision': 0.625, 'active_dates': 4}`.
- Median-bias-adjusted persistent rule: `{'triggers': 14, 'hits': 10, 'precision': 0.7143, 'active_dates': 4}`.

## Daily Max

| date | CoWIN max | HKO max | delta | CoWIN detect lag | HKO detect lag |
|---|---:|---:|---:|---:|---:|
| 2026-07-11 | 37.1 | 34.2 | +2.9 | 2.2m | 8.2m |
| 2026-07-12 | 35.5 | 34.5 | +1.0 | 2.2m | 8.1m |
| 2026-07-13 | 33.7 | 33.7 | +0.0 | 2.2m | 8.1m |
| 2026-07-14 | 28.8 | 28.6 | +0.2 | 2.2m | 8.1m |

## Verdict

significance=NA; baseline=NA; forward=NA; conclusion=inconclusive

- The HKO official-lock runner has no demonstrated entry edge: by the time HKO confirms the floor, the NO book is usually absent or already above the configured price cap.
- CoWIN is useful as an earlier predictor, not as a substitute settlement observation. Its same-minute correlation with HKO is strong, but the daily maximum basis ranges from 0.0 C to +2.9 C in this sample.
- The 138.9-minute median shared-cross lead mixes publication latency with station-temperature basis. It must not be interpreted as pure feed-speed advantage.
- Even after a fixed median-bias adjustment, the persistent rule has 4 false triggers in 14 opportunities. That is not sufficient for live exact-bracket NO orders.
- Recommended next state: keep HKO official-lock as telemetry and run a separate CoWIN-to-HKO probabilistic shadow with dynamic intraday bias and trigger-time book capture.

This report uses four overlapping dates, PIT local detection timestamps, and no execution/PnL denominator. It does not authorize CoWIN-based live orders.
