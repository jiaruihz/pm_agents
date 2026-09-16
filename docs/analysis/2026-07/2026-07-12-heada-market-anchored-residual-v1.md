# HeadA Market-Anchored Residual v1

Generated: 2026-07-12  
Scope: HeadA `forecast_tail_low_price_yes` only. Live entry is unchanged; this is an independent shadow research head.

## Verdict

`inconclusive`. Best OOS probability arm by Brier: `market_75_source_residual_25`. The experiment uses the same candidate denominator, strict expanding target-date walk-forward, no city identity and no optimized entry threshold.

The key question is whether a strongly regularized source/bias or overshoot correction improves on the market ask itself. A positive historical ROI is insufficient if calibration does not beat the market prior.

## Data / PIT

- Input: `docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2/candidate_rows.csv`
- OOS window: `2026-06-05..2026-07-07`
- OOS denominator: 217 rows / 32 dates / 43 cities
- Each test date is predicted using only earlier target dates; minimum train rows=150.
- Logistic residual correction uses strong L2 regularization `C=0.05`; no city one-hot and no ROI-fitted thresholds.
- The tradable candidate is structurally anchored: `p = 75% * market ask + 25% * residual model`. The unshrunk model is diagnostic only.
- Per-share fee uses the official Weather curve `0.05 * ask * (1-ask)`; the input's historical position-level `entry_fee` is not mixed into this one-share replay.

## Probability A/B

| arm | rows | dates | realized | mean predicted | Brier↓ | logloss↓ | AUC↑ |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_ask | 217 | 32 | +14.7% | +10.0% | 0.1253 | 0.4195 | 0.6079 |
| current_model_p_yes | 217 | 32 | +14.7% | +38.3% | 0.1810 | 0.5504 | 0.5760 |
| unshrunk_source_model_diagnostic | 217 | 32 | +14.7% | +17.6% | 0.1400 | 0.4470 | 0.6179 |
| market_75_source_residual_25 | 217 | 32 | +14.7% | +11.9% | 0.1248 | 0.4142 | 0.6150 |
| market_75_source_overshoot_residual_25 | 217 | 32 | +14.7% | +12.0% | 0.1249 | 0.4144 | 0.6155 |

### Brier delta vs market

Negative is better. CI is target-date block bootstrap.

| arm | Brier delta | 95% CI |
|---|---:|---:|
| market_75_source_residual_25 | -0.000521 | [-0.003556, +0.002422] |
| market_75_source_overshoot_residual_25 | -0.000443 | [-0.003675, +0.002749] |

## Executable Selection Diagnostics

`positive_residual` buys every row whose predicted probability exceeds ask+fee. `top1_positive_per_date` is a fixed-capacity diagnostic, not an optimized strategy threshold.

| arm | policy | rows | dates | wins | win rate | avg ask | ROI | date-block 95% CI | losing days | max daily loss/share |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| current_model_p_yes | positive_residual | 217 | 32 | 32 | +14.7% | +10.0% | +43.0% | [+0.9%, +91.7%] | 10 | -1.1959 |
| current_model_p_yes | top1_positive_per_date | 32 | 32 | 5 | +15.6% | +9.0% | +69.5% | [-65.8%, +214.2%] | 27 | -0.1408 |
| market_75_source_residual_25 | positive_residual | 168 | 32 | 23 | +13.7% | +9.7% | +36.7% | [-21.7%, +105.8%] | 16 | -0.9682 |
| market_75_source_residual_25 | top1_positive_per_date | 32 | 32 | 8 | +25.0% | +11.1% | +120.6% | [-7.6%, +266.5%] | 24 | -0.2080 |
| market_75_source_overshoot_residual_25 | positive_residual | 162 | 32 | 23 | +14.2% | +9.7% | +41.8% | [-19.3%, +113.8%] | 16 | -0.9682 |
| market_75_source_overshoot_residual_25 | top1_positive_per_date | 32 | 32 | 8 | +25.0% | +11.2% | +119.5% | [-6.4%, +266.4%] | 24 | -0.2080 |

## Interpretation

- `market_ask` is the probability baseline, not a tradable edge: buying at ask must still overcome fees and spread.
- `current_model_p_yes` tests the old HeadA belief directly on the identical OOS denominator.
- `market_75_source_residual_25` asks whether PIT source/bias fields add a small correction after market price.
- `market_75_source_overshoot_residual_25` adds the old kernel's below/overshoot decomposition only as residual features; the kernel never replaces the market prior.
- No live selector or sizing change follows from this v1. Only a residual arm that improves probability scoring and fee-after forward EV should be wired into a zero-notional shadow runner.

## Contract Verdict

significance=FAIL (Brier delta and selection ROI CIs cross 0); baseline=FAIL against market probability scoring; forward=PARTIAL because predictions are historical expanding OOS, not fresh post-registration shadow; conclusion=inconclusive.

## Eight Rings

Covered: descriptive performance, probability calibration/ranking, execution fee approximation, target-date bootstrap, market baseline. Missing/partial: real queue fill, capacity, portfolio correlation, fresh post-registration forward.
