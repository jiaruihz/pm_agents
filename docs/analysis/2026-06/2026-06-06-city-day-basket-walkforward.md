# City-Day Basket Walk-Forward Research — 2026-06-06

> generated_at_utc: `2026-06-06T01:31:28+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline walk-forward diagnostic only; no N100/live behavior changed.

## Method

- Candidate rules are fixed in advance: `blended_single`, `heuristic_pr2b`, `combo_risk`, `combo_market_risk`, `combo_market_tail`.
- Each fold uses 14 settled target dates as train and the next 3 dates as unseen test.
- Train selection score weights top-5-removed ROI more than headline ROI, penalizes missed-profit > avoided-loss, and penalizes negative BUY_NO ROI.
- `combo_market_tail` is the strict tail-objective candidate: market-normalized EV must stay positive after removing the best single final-temp outcome.
- No parameter grid is searched inside folds.

## Aggregate Test Results

| policy | folds | selections | n | cost | pnl | ROI | weighted top5 ROI | positive folds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| selected_by_train_score | 5 | - | 350 | $1745 | $+35 | +1.99% | -42.09% | 40.0% |
| always_blended_single | 5 | 1 | 634 | $3170 | $+16 | +0.49% | -20.04% | 40.0% |
| always_heuristic_pr2b | 5 | 3 | 347 | $1727 | $+62 | +3.61% | -40.74% | 40.0% |
| always_combo_risk | 5 | 0 | 168 | $864 | $+180 | +20.88% | -72.89% | 80.0% |
| always_combo_market_risk | 5 | 1 | 149 | $757 | $+270 | +35.66% | -70.87% | 80.0% |
| always_combo_market_tail | 5 | 0 | 22 | $91 | $-22 | -24.13% | -103.57% | 20.0% |

## Fold Detail

| fold | train | test | selected | selected test ROI | selected top5 ROI | best test rule by PnL |
|---:|---|---|---|---:|---:|---|
| 1 | 2026-05-06 -> 2026-05-20 | 2026-05-21 -> 2026-05-23 | heuristic_pr2b | -15.14% | -41.68% | blended_single |
| 2 | 2026-05-09 -> 2026-05-23 | 2026-05-24 -> 2026-05-26 | heuristic_pr2b | -0.19% | -43.75% | combo_market_risk |
| 3 | 2026-05-12 -> 2026-05-26 | 2026-05-27 -> 2026-05-29 | heuristic_pr2b | +10.62% | -37.50% | combo_market_risk |
| 4 | 2026-05-15 -> 2026-05-29 | 2026-05-30 -> 2026-06-01 | blended_single | -14.81% | -46.74% | combo_risk |
| 5 | 2026-05-19 -> 2026-06-01 | 2026-06-02 -> 2026-06-04 | combo_market_risk | +21.82% | -63.56% | combo_market_risk |

## Quant Read

- If train-selected rules do not beat a simple always-on baseline in unseen test windows, the selector is not yet useful.
- Positive headline ROI with negative top-5-removed ROI is still tail-dependent and should stay shadow-only.
- This is a small sample; use it to reject overfit candidates, not to approve production.

Production remains unchanged.
