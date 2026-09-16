# Low-Price YES Lottery Selector Refinement v1

Generated: 2026-07-02

## Verdict

The best current version is not the raw 1c-25c fixed-cost lottery. The cleaner shadow candidate is:

```text
BUY_YES
edge >= 0.20
ask 0.05..0.20
one candidate per city-date
earliest PIT decision snapshot, lower ask tie-break
sizing = min($5, ask * 25 shares)
```

This removes sub-5c dust tickets and caps maximum payout/shares. It gives up some headline convexity but materially improves forward robustness versus the raw fixed-$5 rule.

```text
significance=PARTIAL (historical positive; no fresh forward CI significance because only 3 closed forward dates)
baseline=PARTIAL (beats raw dust-dependent robustness; still not a clean same-row market/base-rate proof)
forward=PARTIAL (positive first closed forward, but too few dates)
conclusion=shadow_candidate_keep_collecting; no live
```

## Data Snapshot

- DB: `runtime/weather.db`, fact built `2026-07-01T16:27:01.770598+00:00`.
- fact_signal_candidates: 41047 rows, 2026-05-05..2026-07-01.
- Forward cut: `2026-06-27`. Historical excludes target dates >= forward cut.
- Forward candidate pool: 223 low-price BUY_YES rows; CLOB closed overlay rows: 219; open/unresolved rows: 4; API errors: 0.
- `run_stack.sh` rebuilt DB/fact tables but exited non-cleanly because frontend port 5174 stayed busy; CLOB fill coverage gate was checked separately and passed.

## Best Candidate vs Raw Baseline

| selector | sizing | hist rows | hist ask | hist ROI | hist top-removed | recent rows | recent ROI | fwd rows | fwd ask | fwd avg cost | fwd ROI | fwd top-removed | fwd max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_edge20_ask01_25 | fixed5 | 674 | 0.087 | +50.0% | +41.0% | 330 | +36.6% | 32 | 0.058 | 5.000 | +32.3% | -56.9% | $-51.43 |
| no_dust_edge20_ask05_20 | payout25_cap5 | 457 | 0.105 | +28.8% | +26.9% | 226 | +22.8% | 19 | 0.100 | 2.491 | +58.5% | +8.8% | $-4.44 |
| gfs_edge20_ask05_25 | payout25_cap5 | 220 | 0.128 | +9.2% | +5.8% | 116 | +17.4% | 7 | 0.100 | 2.489 | +186.9% | +55.8% | $-5.54 |

## Candidate Menu

| selector | sizing | hist rows | hist ROI | hist top-removed | recent ROI | fwd rows | fwd avg cost | fwd ROI | fwd top-removed | fwd max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gfs_edge20_ask05_25 | payout25_cap5 | 220 | +9.2% | +5.8% | +17.4% | 7 | 2.489 | +186.9% | +55.8% | $-5.54 |
| gfs_edge20_ask05_25 | ask_scaled_05_20 | 220 | +6.7% | +3.3% | +13.4% | 7 | 2.100 | +194.8% | +53.2% | $-5.08 |
| gfs_edge20_ask05_25 | payout50_cap5 | 220 | +15.4% | +10.6% | +25.8% | 7 | 3.971 | +217.2% | +52.4% | $-7.83 |
| gfs_edge20_ask05_25 | fixed5 | 220 | +22.1% | +15.0% | +29.9% | 7 | 5.000 | +268.8% | +27.2% | $-10.00 |
| no_dust_edge20_ask05_20 | ask_scaled_05_20 | 457 | +29.6% | +27.7% | +21.2% | 19 | 2.019 | +75.0% | +19.7% | $-0.27 |
| no_dust_edge20_ask05_25 | ask_scaled_05_20 | 513 | +26.2% | +24.7% | +16.8% | 19 | 2.019 | +75.0% | +19.7% | $-0.27 |
| base_edge20_ask01_25 | payout25_cap5 | 674 | +36.0% | +34.3% | +27.1% | 32 | 1.455 | +61.1% | +9.4% | $+1.60 |
| no_dust_edge20_ask05_20 | payout25_cap5 | 457 | +28.8% | +26.9% | +22.8% | 19 | 2.491 | +58.5% | +8.8% | $-4.44 |
| no_dust_edge20_ask05_25 | payout25_cap5 | 513 | +26.1% | +24.6% | +18.9% | 19 | 2.491 | +58.5% | +8.8% | $-4.44 |
| edge25_ask05_25 | ask_scaled_05_20 | 314 | +19.5% | +17.0% | +24.8% | 12 | 1.940 | +90.8% | +7.8% | $+6.98 |
| model25_ask05_20 | ask_scaled_05_20 | 788 | +13.7% | +12.7% | +8.6% | 37 | 2.841 | +27.8% | +7.4% | $-9.29 |
| model25_ask05_20 | payout25_cap5 | 788 | +14.1% | +13.1% | +10.4% | 37 | 3.259 | +24.4% | +4.9% | $-13.19 |
| model25_ask05_20 | payout50_cap5 | 788 | +14.4% | +13.1% | +12.6% | 37 | 4.584 | +26.9% | -0.9% | $-25.77 |
| edge25_ask05_25 | payout25_cap5 | 314 | +17.8% | +15.2% | +25.2% | 12 | 2.418 | +72.3% | -2.9% | $+4.50 |
| model25_ask05_20 | fixed5 | 788 | +14.6% | +12.6% | +12.5% | 37 | 5.000 | +38.5% | -8.2% | $-34.39 |
| base_edge20_ask01_25 | ask_scaled_05_20 | 674 | +40.6% | +35.1% | +26.0% | 32 | 1.558 | +61.7% | -8.6% | $-1.77 |
| no_dust_edge20_ask05_20 | payout50_cap5 | 457 | +26.1% | +23.8% | +22.8% | 19 | 4.097 | +50.0% | -11.1% | $-18.05 |
| no_dust_edge20_ask05_25 | payout50_cap5 | 513 | +24.9% | +22.8% | +20.4% | 19 | 4.097 | +50.0% | -11.1% | $-18.05 |
| ask10_edge20_ask10_25 | payout25_cap5 | 286 | +24.4% | +22.4% | +15.2% | 9 | 3.669 | +51.4% | -16.0% | $-3.62 |
| base_edge20_ask01_25 | payout50_cap5 | 674 | +38.8% | +36.6% | +33.0% | 32 | 2.548 | +43.2% | -16.4% | $-11.23 |
| ask10_edge20_ask10_25 | ask_scaled_05_20 | 286 | +25.9% | +24.0% | +15.6% | 9 | 3.226 | +53.0% | -17.1% | $-3.17 |
| no_dust_edge20_ask05_20 | fixed5 | 457 | +24.2% | +20.9% | +20.1% | 19 | 5.000 | +65.9% | -25.8% | $-26.43 |
| no_dust_edge20_ask05_25 | fixed5 | 513 | +23.5% | +20.5% | +18.4% | 19 | 5.000 | +65.9% | -25.8% | $-26.43 |
| ask10_edge20_ask10_25 | fixed5 | 286 | +21.3% | +18.2% | +14.5% | 9 | 5.000 | +48.3% | -28.6% | $-5.00 |

## Champion Forward Rows

| date | city | bracket | ask | edge | model_p | YES final | cost | PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-30 | Shanghai | 28 | 0.055 | 0.240 | 0.295 | 1.000 | $+1.38 | $+23.62 |
| 2026-06-28 | Shanghai | 30 | 0.131 | 0.264 | 0.395 | 1.000 | $+3.28 | $+21.73 |
| 2026-06-27 | Jeddah | 39+ | 0.175 | 0.425 | 0.600 | 1.000 | $+4.38 | $+20.62 |
| 2026-06-27 | Amsterdam | 33 | 0.055 | 0.418 | 0.473 | 0.000 | $+1.38 | $-1.38 |
| 2026-06-27 | LA | 68-69 | 0.057 | 0.465 | 0.522 | 0.000 | $+1.41 | $-1.41 |
| 2026-06-28 | Milan | 38 | 0.060 | 0.264 | 0.324 | 0.000 | $+1.50 | $-1.50 |
| 2026-06-28 | Miami | 88-89 | 0.060 | 0.238 | 0.298 | 0.000 | $+1.50 | $-1.50 |
| 2026-06-28 | Istanbul | 25 | 0.065 | 0.276 | 0.341 | 0.000 | $+1.62 | $-1.62 |
| 2026-06-27 | Busan | 25 | 0.073 | 0.281 | 0.355 | 0.000 | $+1.84 | $-1.84 |
| 2026-06-27 | Beijing | 37 | 0.077 | 0.230 | 0.307 | 0.000 | $+1.94 | $-1.94 |
| 2026-06-27 | Moscow | 18 | 0.080 | 0.258 | 0.338 | 0.000 | $+2.00 | $-2.00 |
| 2026-06-28 | Atlanta | 96-97 | 0.085 | 0.510 | 0.594 | 0.000 | $+2.11 | $-2.11 |
| 2026-06-27 | BuenosAires | 17 | 0.090 | 0.304 | 0.394 | 0.000 | $+2.25 | $-2.25 |
| 2026-06-27 | Munich | 35 | 0.115 | 0.243 | 0.358 | 0.000 | $+2.88 | $-2.88 |
| 2026-06-28 | Moscow | 22 | 0.115 | 0.248 | 0.363 | 0.000 | $+2.88 | $-2.88 |
| 2026-06-27 | MexicoCity | 22 | 0.125 | 0.267 | 0.392 | 0.000 | $+3.12 | $-3.12 |
| 2026-06-30 | Tokyo | 26 | 0.145 | 0.234 | 0.379 | 0.000 | $+3.62 | $-3.62 |
| 2026-06-27 | KualaLumpur | 32 | 0.165 | 0.229 | 0.394 | 0.000 | $+4.12 | $-4.12 |
| 2026-06-27 | Houston | 90-91 | 0.165 | 0.269 | 0.434 | 0.000 | $+4.12 | $-4.12 |

## Champion Daily Forward

| date | rows | wins | cost | PnL | ROI |
| --- | --- | --- | --- | --- | --- |
| 2026-06-27 | 11 | 1 | $+29.44 | $-4.44 | -15.1% |
| 2026-06-28 | 6 | 1 | $+12.89 | $+12.11 | +94.0% |
| 2026-06-30 | 2 | 1 | $+5.00 | $+20.00 | +400.0% |

## Core Logic

- The alpha hypothesis is not simply `cheap ticket go brrr`. It is: when market asks 5c-20c while the model still assigns at least 20c of edge, the market may be underpricing a tail path created by forecast miss, station basis, or intraday reheat.
- The 5c floor removes hard-to-fill dust and reduces dependence on one 3c winner.
- The `ask * 25 shares` cap means each signal has a fixed maximum payout, not fixed cash spend. A 5c ticket costs $1.25; a 20c ticket costs $5. This keeps cheap tickets from dominating share exposure.
- GFS-only is interesting as a tag: forward is strong and top-removed stays positive, but historical sample/ROI is thinner than the broader no-dust sleeve, so it should not be the main rule yet.

## Reliability Judgment

- Raw baseline forward ROI was +32.3%, but top-trade removed was -56.9%; it relied too much on one dust winner.
- Champion forward ROI is +58.5%, and top-trade removed is +8.8%; the point estimate is lower than the dust-max version but the shape is healthier.
- Still not live: closed forward support is only 3 target dates, and the baseline question is not fully separated from broad cheap-YES/base-rate effects.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_lottery_selector_refinement_v1.py`
- Summary CSV: `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/summary.csv`
- Details CSV: `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv`
- Daily CSV: `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/daily.csv`
- JSON: `docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.json`
