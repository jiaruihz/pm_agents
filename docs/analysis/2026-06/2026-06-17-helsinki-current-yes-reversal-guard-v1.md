# 2026-06-17 Helsinki Current-YES Reversal Guard v1

## Human conclusion

This was a current-YES reversal accident, not a normal small forecast miss.

The live branch bought the current running-max 20C YES because the visible path looked like "20C was reached, then temperature declined". Two minutes later EFHK reported 21C, so the 20C bracket was no longer the final winner.

The trade failure has two live-path bugs and one model-research question:

1. Live-path bug: Helsinki was evaluated with fixed UTC+2 even though June is UTC+3 under DST.
2. Live-path bug: the runner allowed orders in the dangerous window immediately before the next METAR observation, while the latest visible observation was already old.
3. Research question: the model probability was overconfident because quote price, city prior, and decline features reinforced each other.

## Guard shipped in this slice

The runner now uses IANA timezones where known. Helsinki is evaluated as `Europe/Helsinki`, so a 2026-06-16 13:18Z decision is local 16:18, outside the validated 13-15 local-hour window.

The runner now computes observation age and inferred METAR cadence from observations available as of the decision timestamp. It vetoes candidates when the last observation is too old or when the next observation is expected within the blackout window.

The runner now vetoes current-YES candidates when the current running max is within 1.0C of the next higher bracket. This is deliberately conservative after the 20C to 21C reversal case.

The timezone and observation-clock pieces live in `src/strategies/weather_edge_v1/tools/official_observation_clock.py`. New weather live/shadow branches should use that module instead of reimplementing fixed UTC offsets or METAR cadence logic in strategy scripts.

Default live guard values:

- `max_obs_age_min = 20`
- `pre_metar_update_blackout_min = 6`
- `min_gap_to_next_bracket_c = 1`

## Model research plan

Target question: when the current bracket has already been touched, can we produce a calibrated probability that it remains the final winner without letting market quote and city prior create false certainty?

Primary slices:

- Reversal window: decisions within 0-10 minutes before the next inferred METAR update versus all other decisions.
- Observation age: 0-10, 10-20, 20-30, and 30+ minutes since latest observation.
- Distance to next bracket: <=1C, 1-2C, and >2C.
- Local clock: true IANA local hour, not fixed UTC offset.
- City prior stress: compare city-fixed model against city-held-out or shrinkage prior.
- Quote feedback stress: train/evaluate with and without current YES ask as a feature.

Success metric:

- Calibration first: predicted 90-98% buckets must actually settle near that rate.
- Then economics: only after calibration survives, measure quote edge after taker/maker execution and stale-quote filters.

Immediate hypothesis:

The raw physics idea still makes sense, but the model must treat pre-observation-update windows and one-degree headroom as separate hazard regimes. A high market price should not be allowed to confirm the model when the next official observation has not printed yet.
