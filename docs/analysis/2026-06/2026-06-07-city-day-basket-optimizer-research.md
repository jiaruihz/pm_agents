# City-Day Basket Optimizer Research — 2026-06-06

> generated_at_utc: `2026-06-07T10:01:24+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline research only; no N100/live behavior changed.

## Research Question

Can a normalized city-day distribution plus combo enumeration beat the current PR2b edge-ranked top-N heuristic more robustly?

Algorithms compared:

- `raw_single`: original raw model probability single-market rule.
- `blended_single`: market-anchored probability, still single-market.
- `heuristic_pr2b`: current PR2b basket, edge-ranked top-N.
- `combo_ev`: enumerate feasible combinations and maximize normalized EV.
- `combo_risk`: enumerate feasible combinations and maximize EV + 0.5 * CVaR20.
- `combo_guarded`: same as `combo_risk`, but drops legs whose max win is > 12x notional.
- `combo_policy_v1`: fixed robust policy using blend EV, market-normalized sanity checks, and CVaR floors.
- `combo_market_risk`: candidates from blend edge, but objective uses market-normalized distribution.
- `combo_market_tail`: market-normalized objective that requires positive EV even after removing the best single final-temp outcome.

Research constraints:

- Uses `fact_signal_candidates` T-22~24h representative snapshot, not full N100 snapshot replay.
- Normalizes per-bracket `p_yes_used` into a city-day distribution for objective scoring.
- Candidate pool limited to top 12 executable-edge legs per city-day; max selected legs = 4.
- `combo_guarded` is a first tail-control test, not a tuned production rule.
- BUY_NO book still has historical proxy limitations where source data lacks independent NO ask.

## Slice Summary

| slice | rule | n | cost | PnL | ROI | top5 ROI | missed | avoided | gates |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | raw_single | 1966 | $9830 | $+388 | +3.95% | -6.94% | $0 | $0 | 0/4 |
| full | blended_single | 1401 | $7005 | $+700 | +9.99% | -4.44% | $0 | $0 | 0/4 |
| full | heuristic_pr2b | 955 | $4766 | $+1025 | +21.51% | +1.04% | $854 | $935 | 4/4 |
| full | combo_ev | 712 | $3631 | $+994 | +27.37% | +0.22% | $1435 | $1450 | 4/4 |
| full | combo_risk | 487 | $2561 | $+900 | +35.16% | -3.37% | $1872 | $1815 | 2/4 |
| full | combo_guarded | 424 | $2237 | $+155 | +6.95% | -5.04% | $2997 | $2320 | 1/4 |
| full | combo_policy_v1 | 507 | $2721 | $+368 | +13.51% | -18.13% | $2528 | $2225 | 2/4 |
| full | combo_market_risk | 394 | $1912 | $+822 | +42.99% | -11.34% | $2234 | $2225 | 2/4 |
| full | combo_market_tail | 78 | $409 | $+68 | +16.52% | -20.56% | $3963 | $3295 | 2/4 |
| train_pre_2026_05_26 | raw_single | 1189 | $5945 | $+827 | +13.91% | -3.41% | $0 | $0 | 0/4 |
| train_pre_2026_05_26 | blended_single | 826 | $4130 | $+977 | +23.65% | +0.86% | $0 | $0 | 0/4 |
| train_pre_2026_05_26 | heuristic_pr2b | 540 | $2685 | $+1038 | +38.66% | +6.60% | $585 | $555 | 3/4 |
| train_pre_2026_05_26 | combo_ev | 405 | $2060 | $+948 | +46.00% | +3.73% | $1009 | $815 | 3/4 |
| train_pre_2026_05_26 | combo_risk | 299 | $1572 | $+873 | +55.55% | +0.11% | $1203 | $950 | 3/4 |
| train_pre_2026_05_26 | combo_guarded | 262 | $1371 | $+264 | +19.25% | +0.19% | $2069 | $1235 | 3/4 |
| train_pre_2026_05_26 | combo_policy_v1 | 272 | $1486 | $+765 | +51.49% | -6.24% | $1546 | $1285 | 2/4 |
| train_pre_2026_05_26 | combo_market_risk | 240 | $1125 | $+685 | +60.86% | -12.16% | $1508 | $1200 | 2/4 |
| train_pre_2026_05_26 | combo_market_tail | 62 | $336 | $+65 | +19.26% | -23.03% | $2716 | $1750 | 2/4 |
| holdout_from_2026_05_26 | raw_single | 777 | $3885 | $-439 | -11.30% | -20.99% | $0 | $0 | 0/4 |
| holdout_from_2026_05_26 | blended_single | 575 | $2875 | $-277 | -9.63% | -22.74% | $0 | $0 | 0/4 |
| holdout_from_2026_05_26 | heuristic_pr2b | 415 | $2082 | $-12 | -0.60% | -23.08% | $269 | $380 | 2/4 |
| holdout_from_2026_05_26 | combo_ev | 307 | $1571 | $+46 | +2.94% | -27.85% | $426 | $635 | 2/4 |
| holdout_from_2026_05_26 | combo_risk | 188 | $989 | $+27 | +2.75% | -46.69% | $669 | $865 | 2/4 |
| holdout_from_2026_05_26 | combo_guarded | 162 | $866 | $-109 | -12.53% | -32.31% | $928 | $1085 | 1/4 |
| holdout_from_2026_05_26 | combo_policy_v1 | 235 | $1235 | $-397 | -32.18% | -39.52% | $982 | $940 | 0/4 |
| holdout_from_2026_05_26 | combo_market_risk | 154 | $787 | $+137 | +17.44% | -42.33% | $725 | $1025 | 2/4 |
| holdout_from_2026_05_26 | combo_market_tail | 16 | $73 | $+3 | +3.91% | -62.78% | $1247 | $1545 | 3/4 |
| recent_from_2026_06_01 | raw_single | 163 | $815 | $-53 | -6.56% | -16.60% | $0 | $0 | 0/4 |
| recent_from_2026_06_01 | blended_single | 121 | $605 | $-27 | -4.41% | -16.83% | $0 | $0 | 0/4 |
| recent_from_2026_06_01 | heuristic_pr2b | 64 | $346 | $-58 | -16.75% | -49.67% | $131 | $95 | 0/4 |
| recent_from_2026_06_01 | combo_ev | 46 | $253 | $-60 | -23.56% | -72.17% | $170 | $125 | 0/4 |
| recent_from_2026_06_01 | combo_risk | 34 | $187 | $-11 | -6.03% | -71.73% | $176 | $170 | 0/4 |
| recent_from_2026_06_01 | combo_guarded | 34 | $187 | $-11 | -6.03% | -71.73% | $176 | $170 | 0/4 |
| recent_from_2026_06_01 | combo_policy_v1 | 25 | $140 | $-77 | -54.95% | -86.00% | $220 | $205 | 0/4 |
| recent_from_2026_06_01 | combo_market_risk | 33 | $184 | $-11 | -5.81% | -72.91% | $177 | $175 | 0/4 |
| recent_from_2026_06_01 | combo_market_tail | 5 | $15 | $-7 | -45.21% | +0.00% | $240 | $270 | 3/4 |
| live_filled_only | raw_single | 363 | $1815 | $-15 | -0.82% | -5.58% | $0 | $0 | 0/4 |
| live_filled_only | blended_single | 276 | $1380 | $+45 | +3.29% | -2.92% | $0 | $0 | 0/4 |
| live_filled_only | heuristic_pr2b | 113 | $618 | $+27 | +4.35% | -16.04% | $303 | $290 | 1/4 |
| live_filled_only | combo_ev | 78 | $439 | $+34 | +7.80% | -22.77% | $372 | $365 | 1/4 |
| live_filled_only | combo_risk | 62 | $336 | $+76 | +22.48% | -16.93% | $388 | $410 | 3/4 |
| live_filled_only | combo_guarded | 62 | $336 | $+87 | +26.01% | -13.09% | $388 | $415 | 3/4 |
| live_filled_only | combo_policy_v1 | 34 | $207 | $-53 | -25.59% | -46.67% | $581 | $505 | 0/4 |
| live_filled_only | combo_market_risk | 58 | $309 | $+87 | +28.02% | -14.61% | $395 | $420 | 3/4 |
| live_filled_only | combo_market_tail | 4 | $12 | $+13 | +108.84% | +0.00% | $614 | $590 | 3/4 |

## Current Findings

- Full sample: `combo_risk` has the highest ROI (+35.16%) but still fails the missed-profit gate.
- Holdout: `combo_risk` has positive headline ROI (+2.75%) but top-5 removed ROI is -46.69%, worse than the heuristic.
- Recent slice: `combo_ev` underperforms the heuristic and `combo_risk` remains tail-dependent (top-5 removed ROI -71.73%).
- Live-filled opportunity subset: `combo_risk` is the only basket variant with 3/4 gates, but top-5 removed ROI is still -16.93%.
- `combo_guarded` shows that a blunt high-payout filter is too destructive: it reduces full-sample PnL and does not fix holdout tail risk.
- `combo_policy_v1` tests a fixed robust objective. Full-sample ROI is +13.51%, holdout ROI is -32.18%, recent ROI is -54.95%.
- `combo_market_risk` directly tests the distribution-quality finding. Full-sample ROI is +42.99%, holdout ROI is +17.44%, recent ROI is -5.81%.
- `combo_market_tail` is the strict tail-objective test. Full-sample ROI is +16.52%, holdout ROI is +3.91%, recent ROI is -45.21%.

## Quant Read

- Combo enumeration is directionally useful for finding higher headline ROI, but it currently concentrates risk into fewer, higher-tail legs.
- The core problem is not just top-N vs enumeration. The objective needs a better calibrated city-day temperature distribution and explicit tail controls.
- Simple lottery-leg filtering is not enough. The next research step should improve the probability distribution and evaluate combinations with walk-forward objectives.
- A viable candidate should improve holdout and recent tail-removed ROI, not only headline ROI.

Production remains unchanged.
