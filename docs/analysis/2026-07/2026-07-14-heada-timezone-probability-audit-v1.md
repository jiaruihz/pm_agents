# HeadA Timezone Probability Audit v1

Generated: 2026-07-14
Scope: `forecast_tail_low_price_yes`; same canonical candidate denominator, official Weather fee.

## Verdict

The legacy probability layer has a deterministic calendar bug: GMT forecast archives were grouped by their raw UTC date and joined to station-local `date_local`. The bug is real, but it is **not the explanation for the low hit rate**: correcting it changes mean probability from +24.1% to +24.6% on the neutral universe and does not improve Brier score. It changes some threshold triggers, but it does not rescue probability calibration.

Current action: fix the probability producer; keep HeadA at tiny fixed 5-share risk while fresh corrected snapshots accumulate. No hindsight city blacklist and no new threshold is promoted from this audit.

## Data

- Neutral settled denominator: 2660 rows / 68 dates / 49 cities, 2026-05-06..2026-07-13.
- Canonical fact build: `2026-07-14T07:58:43.800304+00:00`.
- Error histories are recomputed from the same GFS/ECMWF caches and WU station observations, changing only UTC-to-city-local calendar grouping.
- Grain: one city-date-bracket decision; selector matches live's earliest eligible city-date ordering.

## Probability Quality

| head | mean probability | realized | Brier | AUC |
|---|---:|---:|---:|---:|
| legacy model | +24.1% | +11.4% | 0.1250 | 0.5834 |
| timezone-corrected | +24.6% | +11.4% | 0.1287 | 0.5829 |
| market ask | +11.1% | +11.4% | 0.0982 | 0.6416 |

## Selector Replay

| window | arm | rows | dates | wins | win rate | avg ask | avg legacy p | avg corrected p | fee-adjusted ROI [date bootstrap 95% CI] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | legacy_selector | 425 | 67 | 63 | +14.8% | +10.3% | +38.6% | +40.1% | +39.5% [+10.2%, +73.5%] |
| full | corrected_selector | 455 | 67 | 66 | +14.5% | +10.4% | +37.9% | +39.9% | +34.8% [+5.3%, +67.0%] |
| train_through_2026_06_20 | legacy_selector | 285 | 45 | 41 | +14.4% | +10.6% | +38.8% | +40.1% | +31.6% [-3.8%, +70.4%] |
| train_through_2026_06_20 | corrected_selector | 298 | 45 | 43 | +14.4% | +10.7% | +38.4% | +40.1% | +30.7% [-4.6%, +69.4%] |
| recent_ge_2026_06_21 | legacy_selector | 140 | 22 | 22 | +15.7% | +9.7% | +38.3% | +39.9% | +56.8% [+4.6%, +120.0%] |
| recent_ge_2026_06_21 | corrected_selector | 157 | 22 | 23 | +14.6% | +9.9% | +37.0% | +39.6% | +43.2% [-4.2%, +97.9%] |
| forward_ge_2026_07_01 | legacy_selector | 75 | 13 | 12 | +16.0% | +9.4% | +39.0% | +41.0% | +66.0% [-27.2%, +178.3%] |
| forward_ge_2026_07_01 | corrected_selector | 85 | 13 | 12 | +14.1% | +9.6% | +37.4% | +40.5% | +42.4% [-35.1%, +135.0%] |
| fresh_ge_2026_07_08 | legacy_selector | 39 | 6 | 3 | +7.7% | +9.0% | +38.2% | +40.3% | -18.8% [-77.2%, +35.5%] |
| fresh_ge_2026_07_08 | corrected_selector | 44 | 6 | 3 | +6.8% | +9.2% | +37.1% | +39.8% | -30.3% [-80.4%, +18.8%] |

Forward 7/01+ overlap is 74 tickets; legacy-only 1, corrected-only 11. These are opportunity replays, not actual fills.

## Code Audit

1. **Confirmed bug**: `compute_error_distribution` and the ECMWF twin grouped GMT cache timestamps by `t[:10]`; fixed to city-local calendar aggregation in `weather_data_feed.forecast_history`.
2. `model_p_yes` is an empirical unconditional historical-error probability. It does not condition on forecast lead, season, current source freshness, or recent regime. Even after the calendar fix, it is a ranking input, not automatically a trustworthy absolute probability.
3. Live refreshes the book but keeps the candidate snapshot probability for up to six hours. This can create a fresh-price/stale-weather comparison. It needs telemetry and a later PIT replay, not an immediate new gate.
4. The live selector takes the earliest eligible bracket per city-date. Only 11 historical city-dates have more than one eligible bracket, so this ordering mismatch is real but not the main loss driver.
5. `forecast_source_best_D_excluded_v1` is telemetry only in current code; it is not a hidden hard block. Existing `dist<=0` is the only model-shape exclusion in this path.

## Can We Remove Obvious Bad Tickets?

Not cleanly yet.

- Fresh 7/08..7/13: the 39 legacy-selector opportunities won 3 times (7.7%); 5-8c tickets were 0/18, but the same cheap band was profitable inside this report's fixed 2,660-row / 68-date / 49-city neutral denominator (`2026-05-06..2026-07-13`). Cutting it now would be a six-day hindsight rule.
- Recent 6/21..7/13: GFS was 16/70 and ECMWF 6/70, but both model families were positive inside that same declared neutral denominator; the ECMWF deterioration also overlaps the known source outage/pollution window. This is a source-regime diagnostic, not a city/source hard block.
- Higher `model_p_yes` or larger reported edge is not monotonic. Fresh edge quartiles won 2/10, 0/10, 0/9, 1/10. The raw score is therefore unsuitable for sizing and cannot support a simple “remove low score” fix.
- `dist>2 brackets` is 0/4 recently, but four rows are not a selector. `dist<=0` remains the only mechanism-defined exclusion already supported by broader evidence.

The clean optimization target is a new calibrated tail ranking built on PIT source/lead/season features while preserving equal daily capacity. Until it beats the old rank in forward data, the honest live action remains fixed 5 shares rather than adding filters after losses.

## Contract Verdict

significance=FAIL for any new selector because corrected probabilities were reconstructed from historical cache rather than captured prospectively; baseline=FAIL/NA pending corrected live snapshots; forward=FAIL/NA; conclusion=inconclusive.

The software defect is confirmed and fixed. The trading improvement is not yet confirmed; fresh corrected snapshots and the affected-window counterfactual must settle first.

## Eight Rings

Covered: canonical opportunity denominator, probability calibration, official fee, target-date bootstrap, same-selector replay, source/ask diagnostics. Partial/missing: actual corrected fills, fresh PIT forward, orderbook queue execution, capacity, portfolio correlation.
