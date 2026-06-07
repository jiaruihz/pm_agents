# City-Day Basket Walk-Forward Research — 2026-06-06

> generated_at_utc: `2026-06-07T10:01:29+00:00`
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
| selected_by_train_score | 5 | - | 388 | $1908 | $+51 | +2.66% | -35.45% | 40.0% |
| always_blended_single | 5 | 0 | 792 | $3960 | $-115 | -2.91% | -20.49% | 40.0% |
| always_heuristic_pr2b | 5 | 3 | 482 | $2388 | $+66 | +2.77% | -33.17% | 60.0% |
| always_combo_risk | 5 | 0 | 231 | $1178 | $+119 | +10.11% | -64.63% | 80.0% |
| always_combo_market_risk | 5 | 1 | 196 | $973 | $+232 | +23.86% | -62.26% | 60.0% |
| always_combo_market_tail | 5 | 1 | 24 | $102 | $-5 | -5.14% | -87.17% | 20.0% |

## Fold Detail

| fold | train | test | selected | selected test ROI | selected top5 ROI | best test rule by PnL |
|---:|---|---|---|---:|---:|---|
| 1 | 2026-05-06 -> 2026-05-20 | 2026-05-21 -> 2026-05-23 | heuristic_pr2b | -15.14% | -41.68% | blended_single |
| 2 | 2026-05-09 -> 2026-05-23 | 2026-05-24 -> 2026-05-26 | heuristic_pr2b | +20.28% | -24.45% | heuristic_pr2b |
| 3 | 2026-05-12 -> 2026-05-26 | 2026-05-27 -> 2026-05-29 | heuristic_pr2b | +3.86% | -31.14% | combo_market_risk |
| 4 | 2026-05-15 -> 2026-05-29 | 2026-05-30 -> 2026-06-01 | combo_market_risk | -5.17% | -81.96% | combo_market_tail |
| 5 | 2026-05-19 -> 2026-06-01 | 2026-06-02 -> 2026-06-04 | combo_market_tail | -31.51% | +0.00% | combo_market_risk |

## Quant Read

- If train-selected rules do not beat a simple always-on baseline in unseen test windows, the selector is not yet useful.
- Positive headline ROI with negative top-5-removed ROI is still tail-dependent and should stay shadow-only.
- This is a small sample; use it to reject overfit candidates, not to approve production.

Production remains unchanged.
