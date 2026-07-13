# HeadA Rank-Preserving Calibration v3

Generated: 2026-07-13  
Scope: `forecast_tail_low_price_yes` only. Research layer; no live selector, sizing, or runner change.

## Verdict

`inconclusive`. The next clean architecture is valid, but only its calibration half earns a role today:

- Keep the old model-market disagreement as the selection rank.
- Replace the literal old `model_p_yes` interpretation with expanding-date monotone calibration for expected hit rate and bankroll diagnostics.
- Source/overshoot do not enter the selector unless their near-tie paired lift is positive and significant. The primary test is source tie-break inside a fixed 2pp old-score band at N=5/day.

Primary source tie-break delta: +4.4% with target-date 95% CI [-11.1%, +20.6%]; conditional pair concordance +54.3% [+48.4%, +59.4%].

## Data And Funnel

- Neutral denominator: 2529 settled 5-20c BUY_YES tickets / 65 target dates / 49 cities, 2026-05-06..2026-07-11.
- Strict expanding OOS: 1795 tickets / 41 dates, 2026-05-31..2026-07-11.
- Canonical fact build: `2026-07-12T16:48:26.875859+00:00`. The 2026-07-13 source sync succeeded; rebuild then stopped at strategy-order migration on `database is locked`, so this study intentionally uses the last successful canonical fact snapshot and does not claim later settlement coverage.
- Grain: one city-date-bracket ticket; equal-capacity selection dedupes to one bracket per city-date before N=1/3/5 per target date.
- PIT: station/source quality uses target dates strictly earlier than each test date. No city identity and no future observation.
- Entry cost: canonical decision ask plus official Weather taker fee `0.05*p*(1-p)`. Risk table uses fixed 5 shares.

## Probability Quality

| window | probability head | rows | dates | realized | mean p | Brier | logloss | AUC |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| oos_all | market | 1795 | 41 | +10.9% | +10.9% | 0.0954 | 0.3352 | 0.6274 |
| oos_all | old_raw_model | 1795 | 41 | +10.9% | +23.8% | 0.1223 | 0.4100 | 0.5729 |
| oos_all | old_score_platt | 1795 | 41 | +10.9% | +11.6% | 0.0974 | 0.3453 | 0.5094 |
| oos_all | source_augmented | 1795 | 41 | +10.9% | +10.8% | 0.0978 | 0.3472 | 0.5383 |
| oos_all | overshoot_augmented | 1795 | 41 | +10.9% | +10.7% | 0.0975 | 0.3458 | 0.5490 |
| recent_ge_2026_06_21 | market | 825 | 20 | +10.1% | +10.8% | 0.0895 | 0.3203 | 0.6105 |
| recent_ge_2026_06_21 | old_raw_model | 825 | 20 | +10.1% | +23.9% | 0.1174 | 0.4014 | 0.5892 |
| recent_ge_2026_06_21 | old_score_platt | 825 | 20 | +10.1% | +11.4% | 0.0906 | 0.3269 | 0.5234 |
| recent_ge_2026_06_21 | source_augmented | 825 | 20 | +10.1% | +10.4% | 0.0924 | 0.3366 | 0.4826 |
| recent_ge_2026_06_21 | overshoot_augmented | 825 | 20 | +10.1% | +10.3% | 0.0921 | 0.3357 | 0.4920 |
| fresh_ge_2026_07_08 | market | 137 | 4 | +8.8% | +10.5% | 0.0778 | 0.2840 | 0.6867 |
| fresh_ge_2026_07_08 | old_raw_model | 137 | 4 | +8.8% | +24.3% | 0.1149 | 0.3928 | 0.5237 |
| fresh_ge_2026_07_08 | old_score_platt | 137 | 4 | +8.8% | +11.4% | 0.0809 | 0.3022 | 0.4360 |
| fresh_ge_2026_07_08 | source_augmented | 137 | 4 | +8.8% | +10.6% | 0.0824 | 0.3090 | 0.3753 |
| fresh_ge_2026_07_08 | overshoot_augmented | 137 | 4 | +8.8% | +10.6% | 0.0821 | 0.3084 | 0.4347 |

Negative paired delta is better.

| window | comparison | metric delta | target-date 95% CI |
|---|---|---:|---:|
| oos_all | platt_vs_old_brier | -0.024940 | [-0.030315, -0.019665] |
| oos_all | platt_vs_market_brier | +0.001930 | [+0.000579, +0.003299] |
| oos_all | source_aug_vs_platt_brier | +0.000414 | [-0.000687, +0.001478] |
| oos_all | overshoot_aug_vs_platt_brier | +0.000123 | [-0.000993, +0.001167] |
| recent_ge_2026_06_21 | platt_vs_old_brier | -0.026814 | [-0.035287, -0.018770] |
| recent_ge_2026_06_21 | platt_vs_market_brier | +0.001110 | [-0.001050, +0.003328] |
| recent_ge_2026_06_21 | source_aug_vs_platt_brier | +0.001811 | [+0.000407, +0.003499] |
| recent_ge_2026_06_21 | overshoot_aug_vs_platt_brier | +0.001557 | [+0.000171, +0.003122] |
| fresh_ge_2026_07_08 | platt_vs_old_brier | -0.033952 | [-0.054695, -0.019958] |
| fresh_ge_2026_07_08 | platt_vs_market_brier | +0.003102 | [+0.001483, +0.003978] |
| fresh_ge_2026_07_08 | source_aug_vs_platt_brier | +0.001446 | [-0.001984, +0.004029] |
| fresh_ge_2026_07_08 | overshoot_aug_vs_platt_brier | +0.001191 | [-0.003110, +0.004127] |

The Platt slope stayed positive in every OOS fit (0.190..1.247), so calibration never reverses the old rank. Its AUC should therefore match `old_score`; its job is honest probability, not a new alpha claim.

### Ranking AUC

| window | score | AUC |
|---|---|---:|
| oos_all | market_ask | 0.6274 |
| oos_all | old_model_p | 0.5729 |
| oos_all | old_disagreement_score | 0.5219 |
| oos_all | source_augmented | 0.5383 |
| oos_all | overshoot_augmented | 0.5490 |
| recent_ge_2026_06_21 | market_ask | 0.6105 |
| recent_ge_2026_06_21 | old_model_p | 0.5892 |
| recent_ge_2026_06_21 | old_disagreement_score | 0.5479 |
| recent_ge_2026_06_21 | source_augmented | 0.4826 |
| recent_ge_2026_06_21 | overshoot_augmented | 0.4920 |
| fresh_ge_2026_07_08 | market_ask | 0.6867 |
| fresh_ge_2026_07_08 | old_model_p | 0.5237 |
| fresh_ge_2026_07_08 | old_disagreement_score | 0.4360 |
| fresh_ge_2026_07_08 | source_augmented | 0.3753 |
| fresh_ge_2026_07_08 | overshoot_augmented | 0.4347 |

Important nuance: the old disagreement score is not a strong broad-universe ranker; its full OOS AUC is 0.5219. Its positive trading result is concentrated in the extreme daily top-N selection. Therefore v3 preserves that observed top-tail ordering, not a claim that every score increment is monotonically informative across all 5-20c tickets.

## Old Score Dose Response

| decile | rows | dates | wins | realized | avg ask | avg old score | avg calibrated p |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 186 | 40 | 20 | +10.8% | +10.3% | +0.1% | +10.7% |
| 2 | 173 | 41 | 19 | +11.0% | +11.2% | +1.5% | +10.8% |
| 3 | 180 | 40 | 14 | +7.8% | +10.9% | +3.5% | +10.9% |
| 4 | 179 | 40 | 18 | +10.1% | +11.1% | +5.8% | +11.1% |
| 5 | 180 | 40 | 25 | +13.9% | +11.8% | +8.2% | +11.2% |
| 6 | 179 | 40 | 17 | +9.5% | +11.4% | +11.2% | +11.5% |
| 7 | 179 | 40 | 18 | +10.1% | +11.3% | +14.5% | +11.7% |
| 8 | 180 | 39 | 22 | +12.2% | +11.0% | +18.5% | +12.0% |
| 9 | 179 | 40 | 21 | +11.7% | +10.4% | +24.3% | +12.3% |
| 10 | 180 | 41 | 22 | +12.2% | +9.9% | +36.9% | +13.3% |

## Fixed 5-Share Selection

Only the predeclared 2pp tie-break is shown here; 1pp/5pp sensitivity and all paired deltas are retained below and in CSV/JSON.

| window | arm | N/day | rows | dates | wins | win rate | avg ask | avg calibrated p | ROI [95% CI] | losing days | <=-50% days | max daily loss USD | daily PnL p10/med/p90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| oos_all | old | 1 | 41 | 41 | 9 | +22.0% | +10.7% | +14.1% | +100.6% [-7.9%, +211.5%] | 32 | 32 | -0.96 | -0.78/-0.38/+4.17 |
| oos_all | source_tiebreak | 1 | 41 | 41 | 7 | +17.1% | +10.8% | +14.1% | +53.8% [-41.1%, +158.5%] | 34 | 34 | -0.96 | -0.78/-0.42/+4.11 |
| oos_all | overshoot_tiebreak | 1 | 41 | 41 | 7 | +17.1% | +10.8% | +14.1% | +53.8% [-41.1%, +158.5%] | 34 | 34 | -0.96 | -0.78/-0.42/+4.11 |
| oos_all | old | 3 | 123 | 41 | 17 | +13.8% | +10.1% | +13.6% | +32.1% [-28.4%, +96.2%] | 26 | 26 | -2.32 | -1.90/-1.27/+3.67 |
| oos_all | source_tiebreak | 3 | 123 | 41 | 17 | +13.8% | +10.2% | +13.6% | +31.7% [-28.4%, +96.1%] | 26 | 26 | -2.32 | -1.97/-1.27/+3.67 |
| oos_all | overshoot_tiebreak | 3 | 123 | 41 | 17 | +13.8% | +10.2% | +13.6% | +31.3% [-21.9%, +85.5%] | 25 | 25 | -2.32 | -1.97/-1.15/+3.75 |
| oos_all | old | 5 | 205 | 41 | 26 | +12.7% | +10.1% | +13.2% | +21.3% [-18.9%, +64.2%] | 19 | 19 | -3.39 | -3.04/+1.25/+3.01 |
| oos_all | source_tiebreak | 5 | 205 | 41 | 27 | +13.2% | +10.1% | +13.2% | +25.8% [-13.2%, +66.5%] | 18 | 18 | -3.39 | -3.01/+1.67/+3.01 |
| oos_all | overshoot_tiebreak | 5 | 205 | 41 | 26 | +12.7% | +10.2% | +13.2% | +20.4% [-19.0%, +62.5%] | 19 | 19 | -3.39 | -3.04/+1.25/+3.01 |
| recent_ge_2026_06_21 | old | 1 | 20 | 20 | 4 | +20.0% | +10.7% | +14.1% | +82.9% [-57.9%, +237.2%] | 16 | 16 | -0.96 | -0.75/-0.40/+4.12 |
| recent_ge_2026_06_21 | source_tiebreak | 1 | 20 | 20 | 3 | +15.0% | +10.6% | +14.1% | +37.5% [-104.4%, +189.2%] | 17 | 17 | -0.96 | -0.76/-0.43/+4.05 |
| recent_ge_2026_06_21 | overshoot_tiebreak | 1 | 20 | 20 | 3 | +15.0% | +10.6% | +14.1% | +37.5% [-104.4%, +189.2%] | 17 | 17 | -0.96 | -0.76/-0.43/+4.05 |
| recent_ge_2026_06_21 | old | 3 | 60 | 20 | 7 | +11.7% | +10.1% | +13.4% | +11.2% [-54.8%, +82.0%] | 13 | 13 | -2.01 | -1.86/-1.22/+3.52 |
| recent_ge_2026_06_21 | source_tiebreak | 3 | 60 | 20 | 7 | +11.7% | +10.0% | +13.4% | +12.1% [-54.1%, +82.8%] | 13 | 13 | -2.01 | -1.86/-1.21/+3.52 |
| recent_ge_2026_06_21 | overshoot_tiebreak | 3 | 60 | 20 | 8 | +13.3% | +10.0% | +13.4% | +29.2% [-38.6%, +104.5%] | 12 | 12 | -2.01 | -1.86/-1.11/+3.68 |
| recent_ge_2026_06_21 | old | 5 | 100 | 20 | 13 | +13.0% | +10.0% | +13.1% | +25.7% [-23.3%, +76.5%] | 8 | 8 | -3.07 | -2.82/+1.82/+2.67 |
| recent_ge_2026_06_21 | source_tiebreak | 5 | 100 | 20 | 13 | +13.0% | +10.1% | +13.1% | +24.8% [-16.0%, +62.0%] | 7 | 7 | -3.07 | -2.64/+2.02/+2.66 |
| recent_ge_2026_06_21 | overshoot_tiebreak | 5 | 100 | 20 | 12 | +12.0% | +10.2% | +13.1% | +13.8% [-28.4%, +53.7%] | 8 | 8 | -3.26 | -2.82/+1.82/+2.66 |
| fresh_ge_2026_07_08 | old | 1 | 4 | 4 | 1 | +25.0% | +10.6% | +14.4% | +130.6% [-104.6%, +409.5%] | 3 | 3 | -0.52 | -0.50/-0.42/+2.79 |
| fresh_ge_2026_07_08 | source_tiebreak | 1 | 4 | 4 | 0 | +0.0% | +10.1% | +14.4% | -104.5% [-104.6%, -104.3%] | 4 | 4 | -0.75 | -0.68/-0.49/-0.40 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | 1 | 4 | 4 | 0 | +0.0% | +10.1% | +14.4% | -104.5% [-104.6%, -104.3%] | 4 | 4 | -0.75 | -0.68/-0.49/-0.40 |
| fresh_ge_2026_07_08 | old | 3 | 12 | 4 | 2 | +16.7% | +10.1% | +13.7% | +60.9% [-104.5%, +211.5%] | 2 | 2 | -1.69 | -1.58/+0.85/+3.48 |
| fresh_ge_2026_07_08 | source_tiebreak | 3 | 12 | 4 | 2 | +16.7% | +10.1% | +13.7% | +60.9% [-104.5%, +211.5%] | 2 | 2 | -1.69 | -1.58/+0.85/+3.48 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | 3 | 12 | 4 | 2 | +16.7% | +10.1% | +13.7% | +60.9% [-104.5%, +211.5%] | 2 | 2 | -1.69 | -1.58/+0.85/+3.48 |
| fresh_ge_2026_07_08 | old | 5 | 20 | 4 | 2 | +10.0% | +10.1% | +13.3% | -5.6% [-104.5%, +79.4%] | 2 | 2 | -2.79 | -2.58/-0.22/+2.36 |
| fresh_ge_2026_07_08 | source_tiebreak | 5 | 20 | 4 | 2 | +10.0% | +10.1% | +13.3% | -5.6% [-104.5%, +79.4%] | 2 | 2 | -2.79 | -2.58/-0.22/+2.36 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | 5 | 20 | 4 | 2 | +10.0% | +10.1% | +13.3% | -5.6% [-104.5%, +79.4%] | 2 | 2 | -2.79 | -2.58/-0.22/+2.36 |

## Tie-Break Paired Delta

`overlap` is `common/old/new`. Candidate count K=6 (source/overshoot x 1pp/2pp/5pp); no best-band cherry-pick.

| window | arm | band | N/day | ROI delta vs old | target-date 95% CI | overlap |
|---|---|---:|---:|---:|---:|---:|
| oos_all | source_tiebreak | +1.0% | 1 | -46.4% | [-115.9%, +0.6%] | 37/41/41 |
| oos_all | source_tiebreak | +2.0% | 1 | -46.8% | [-116.5%, +0.0%] | 36/41/41 |
| oos_all | source_tiebreak | +5.0% | 1 | -68.5% | [-145.7%, -5.5%] | 33/41/41 |
| oos_all | overshoot_tiebreak | +1.0% | 1 | -46.4% | [-115.9%, +0.6%] | 37/41/41 |
| oos_all | overshoot_tiebreak | +2.0% | 1 | -46.8% | [-116.5%, +0.0%] | 36/41/41 |
| oos_all | overshoot_tiebreak | +5.0% | 1 | -68.5% | [-145.7%, -5.5%] | 33/41/41 |
| oos_all | source_tiebreak | +1.0% | 3 | -1.0% | [-3.5%, +0.3%] | 121/123/123 |
| oos_all | source_tiebreak | +2.0% | 3 | -0.4% | [-3.8%, +2.9%] | 119/123/123 |
| oos_all | source_tiebreak | +5.0% | 3 | -1.9% | [-22.2%, +19.4%] | 111/123/123 |
| oos_all | overshoot_tiebreak | +1.0% | 3 | -9.5% | [-28.7%, +0.2%] | 120/123/123 |
| oos_all | overshoot_tiebreak | +2.0% | 3 | -0.8% | [-25.9%, +24.4%] | 117/123/123 |
| oos_all | overshoot_tiebreak | +5.0% | 3 | -3.3% | [-34.4%, +28.5%] | 108/123/123 |
| oos_all | source_tiebreak | +1.0% | 5 | +4.8% | [-0.9%, +15.3%] | 199/205/205 |
| oos_all | source_tiebreak | +2.0% | 5 | +4.4% | [-11.1%, +20.6%] | 194/205/205 |
| oos_all | source_tiebreak | +5.0% | 5 | +5.7% | [-0.6%, +16.7%] | 186/205/205 |
| oos_all | overshoot_tiebreak | +1.0% | 5 | +4.8% | [-0.9%, +15.3%] | 199/205/205 |
| oos_all | overshoot_tiebreak | +2.0% | 5 | -0.9% | [-21.0%, +18.8%] | 193/205/205 |
| oos_all | overshoot_tiebreak | +5.0% | 5 | +0.3% | [-15.0%, +15.2%] | 186/205/205 |
| recent_ge_2026_06_21 | source_tiebreak | +1.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | source_tiebreak | +2.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | source_tiebreak | +5.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +1.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +2.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +5.0% | 1 | -45.4% | [-141.0%, +0.0%] | 19/20/20 |
| recent_ge_2026_06_21 | source_tiebreak | +1.0% | 3 | +0.0% | [+0.0%, +0.0%] | 60/60/60 |
| recent_ge_2026_06_21 | source_tiebreak | +2.0% | 3 | +1.0% | [-2.6%, +5.8%] | 58/60/60 |
| recent_ge_2026_06_21 | source_tiebreak | +5.0% | 3 | -1.4% | [-43.1%, +44.4%] | 55/60/60 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +1.0% | 3 | +0.0% | [+0.0%, +0.0%] | 60/60/60 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +2.0% | 3 | +18.0% | [-2.0%, +57.3%] | 57/60/60 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +5.0% | 3 | +15.3% | [-32.6%, +72.2%] | 54/60/60 |
| recent_ge_2026_06_21 | source_tiebreak | +1.0% | 5 | -0.3% | [-1.1%, +0.0%] | 99/100/100 |
| recent_ge_2026_06_21 | source_tiebreak | +2.0% | 5 | -0.9% | [-31.6%, +28.8%] | 96/100/100 |
| recent_ge_2026_06_21 | source_tiebreak | +5.0% | 5 | +11.1% | [-1.5%, +33.7%] | 90/100/100 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +1.0% | 5 | -0.3% | [-1.1%, +0.0%] | 99/100/100 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +2.0% | 5 | -11.9% | [-47.0%, +20.1%] | 95/100/100 |
| recent_ge_2026_06_21 | overshoot_tiebreak | +5.0% | 5 | -0.1% | [-31.6%, +30.5%] | 90/100/100 |
| fresh_ge_2026_07_08 | source_tiebreak | +1.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | source_tiebreak | +2.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | source_tiebreak | +5.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +1.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +2.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +5.0% | 1 | -235.1% | [-513.8%, +0.0%] | 3/4/4 |
| fresh_ge_2026_07_08 | source_tiebreak | +1.0% | 3 | +0.0% | [+0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | source_tiebreak | +2.0% | 3 | -0.0% | [-0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | source_tiebreak | +5.0% | 3 | -13.2% | [-44.9%, +0.0%] | 11/12/12 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +1.0% | 3 | +0.0% | [+0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +2.0% | 3 | -0.0% | [-0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +5.0% | 3 | -13.2% | [-44.9%, +0.0%] | 11/12/12 |
| fresh_ge_2026_07_08 | source_tiebreak | +1.0% | 5 | +0.0% | [+0.0%, +0.0%] | 20/20/20 |
| fresh_ge_2026_07_08 | source_tiebreak | +2.0% | 5 | -0.0% | [-0.0%, +0.0%] | 20/20/20 |
| fresh_ge_2026_07_08 | source_tiebreak | +5.0% | 5 | -0.5% | [-1.7%, +0.0%] | 18/20/20 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +1.0% | 5 | +0.0% | [+0.0%, +0.0%] | 20/20/20 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +2.0% | 5 | -0.0% | [-0.0%, +0.0%] | 20/20/20 |
| fresh_ge_2026_07_08 | overshoot_tiebreak | +5.0% | 5 | -0.5% | [-1.7%, +0.0%] | 19/20/20 |

## Conditional Near-Tie Test

This removes the broad ranking question: only winner-loser pairs from the same target date whose old scores differ by at most the stated band are compared. `50%` means the increment adds no ordering information.

| window | increment | band | pairs | dates | concordance | target-date 95% CI |
|---|---|---:|---:|---:|---:|---:|
| oos_all | source_increment | +1.0% | 529 | 39 | +56.5% | [+49.9%, +62.6%] |
| oos_all | source_increment | +2.0% | 967 | 39 | +54.3% | [+48.4%, +59.4%] |
| oos_all | source_increment | +5.0% | 2253 | 39 | +54.7% | [+48.6%, +60.6%] |
| oos_all | overshoot_increment | +1.0% | 529 | 39 | +57.8% | [+52.3%, +63.0%] |
| oos_all | overshoot_increment | +2.0% | 967 | 39 | +56.7% | [+51.4%, +61.5%] |
| oos_all | overshoot_increment | +5.0% | 2253 | 39 | +56.9% | [+51.6%, +62.1%] |
| recent_ge_2026_06_21 | source_increment | +1.0% | 192 | 19 | +46.4% | [+34.7%, +55.8%] |
| recent_ge_2026_06_21 | source_increment | +2.0% | 372 | 19 | +46.0% | [+36.1%, +54.6%] |
| recent_ge_2026_06_21 | source_increment | +5.0% | 826 | 19 | +47.5% | [+37.4%, +56.3%] |
| recent_ge_2026_06_21 | overshoot_increment | +1.0% | 192 | 19 | +49.0% | [+39.8%, +56.4%] |
| recent_ge_2026_06_21 | overshoot_increment | +2.0% | 372 | 19 | +50.0% | [+41.3%, +57.9%] |
| recent_ge_2026_06_21 | overshoot_increment | +5.0% | 826 | 19 | +51.2% | [+42.2%, +59.3%] |
| fresh_ge_2026_07_08 | source_increment | +1.0% | 22 | 3 | +36.4% | [+9.1%, +75.0%] |
| fresh_ge_2026_07_08 | source_increment | +2.0% | 53 | 3 | +30.2% | [+16.7%, +62.5%] |
| fresh_ge_2026_07_08 | source_increment | +5.0% | 138 | 3 | +41.3% | [+24.6%, +73.9%] |
| fresh_ge_2026_07_08 | overshoot_increment | +1.0% | 22 | 3 | +45.5% | [+27.3%, +75.0%] |
| fresh_ge_2026_07_08 | overshoot_increment | +2.0% | 53 | 3 | +41.5% | [+33.3%, +75.0%] |
| fresh_ge_2026_07_08 | overshoot_increment | +5.0% | 138 | 3 | +47.8% | [+35.4%, +82.6%] |

## Interpretation

HeadA is a convex tail-lottery, so a small number of winners carrying PnL is expected and is not a standalone rejection test. The relevant question is whether a feature improves ex-ante capture at the same ticket capacity without seeing settlement. Market anchoring improved v2 calibration mostly by copying price and damaged this disagreement rank; v3 therefore does not let market probability overwrite selection.

The monotone calibration head is useful for forecasting hit cadence, losing streaks, and bankroll stress, but it cannot improve trading ROI because it deliberately preserves order. A new selector requires paired lift. Source/overshoot are tested only where they could plausibly help: near-tied old scores. If that lift is absent, they stay telemetry rather than adding complexity.

## Contract Verdict

significance=FAIL for the predeclared source 2pp/N=5 paired delta and pair concordance; baseline=FAIL versus old equal-capacity rank; forward=FAIL/NA because the fresh slice remains thin; conclusion=inconclusive.

No live action. Current fixed 5-share HeadA remains unchanged. Promotion would require a positive target-date paired CI and same-sign recent/fresh evidence, followed by executable-book/fill replay.

## Eight Rings

Covered: neutral opportunity denominator, target-date inference, signal ranking, probability calibration, official fee, fixed-share daily risk, same-capacity counterfactual, recent/fresh windows. Missing/partial: fresh executable-book replay, actual maker fill probability, size capacity, live portfolio correlation, pristine post-registration forward.
