# City-Day Basket Optimizer Research — 2026-06-06

> generated_at_utc: `2026-06-05T18:16:39+00:00`
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

Research constraints:

- Uses `fact_signal_candidates` T-22~24h representative snapshot, not full N100 snapshot replay.
- Normalizes per-bracket `p_yes_used` into a city-day distribution for objective scoring.
- Candidate pool limited to top 12 executable-edge legs per city-day; max selected legs = 4.
- `combo_guarded` is a first tail-control test, not a tuned production rule.
- BUY_NO book still has historical proxy limitations where source data lacks independent NO ask.

## Slice Summary

| slice | rule | n | cost | PnL | ROI | top5 ROI | missed | avoided | gates |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | raw_single | 1696 | $8480 | $+550 | +6.48% | -6.14% | $0 | $0 | 0/4 |
| full | blended_single | 1200 | $6000 | $+869 | +14.48% | -2.36% | $0 | $0 | 0/4 |
| full | heuristic_pr2b | 792 | $3944 | $+1111 | +28.16% | +3.44% | $784 | $825 | 4/4 |
| full | combo_ev | 577 | $2961 | $+1062 | +35.88% | +2.60% | $1318 | $1270 | 3/4 |
| full | combo_risk | 408 | $2164 | $+1028 | +47.53% | +1.99% | $1615 | $1520 | 3/4 |
| full | combo_guarded | 355 | $1870 | $+169 | +9.02% | -5.03% | $2769 | $1930 | 1/4 |
| train_pre_2026_05_26 | raw_single | 1159 | $5795 | $+839 | +14.47% | -3.30% | $0 | $0 | 0/4 |
| train_pre_2026_05_26 | blended_single | 804 | $4020 | $+986 | +24.52% | +1.11% | $0 | $0 | 0/4 |
| train_pre_2026_05_26 | heuristic_pr2b | 525 | $2600 | $+1054 | +40.53% | +7.44% | $572 | $540 | 3/4 |
| train_pre_2026_05_26 | combo_ev | 390 | $1995 | $+965 | +48.37% | +4.74% | $992 | $810 | 3/4 |
| train_pre_2026_05_26 | combo_risk | 290 | $1540 | $+889 | +57.73% | +1.14% | $1174 | $930 | 3/4 |
| train_pre_2026_05_26 | combo_guarded | 254 | $1342 | $+277 | +20.61% | +1.15% | $2040 | $1210 | 3/4 |
| holdout_from_2026_05_26 | raw_single | 537 | $2685 | $-289 | -10.76% | -24.54% | $0 | $0 | 0/4 |
| holdout_from_2026_05_26 | blended_single | 396 | $1980 | $-117 | -5.91% | -24.59% | $0 | $0 | 0/4 |
| holdout_from_2026_05_26 | heuristic_pr2b | 267 | $1344 | $+57 | +4.23% | -29.31% | $212 | $285 | 2/4 |
| holdout_from_2026_05_26 | combo_ev | 187 | $966 | $+97 | +10.08% | -38.52% | $327 | $460 | 2/4 |
| holdout_from_2026_05_26 | combo_risk | 118 | $624 | $+139 | +22.35% | -53.57% | $441 | $590 | 2/4 |
| holdout_from_2026_05_26 | combo_guarded | 101 | $528 | $-108 | -20.42% | -46.62% | $729 | $720 | 1/4 |
| recent_from_2026_06_01 | raw_single | 129 | $645 | $+10 | +1.54% | -10.79% | $0 | $0 | 0/4 |
| recent_from_2026_06_01 | blended_single | 95 | $475 | $+20 | +4.27% | -10.88% | $0 | $0 | 0/4 |
| recent_from_2026_06_01 | heuristic_pr2b | 49 | $263 | $+21 | +8.04% | -33.60% | $99 | $75 | 1/4 |
| recent_from_2026_06_01 | combo_ev | 34 | $187 | $+2 | +1.32% | -63.12% | $139 | $90 | 0/4 |
| recent_from_2026_06_01 | combo_risk | 26 | $133 | $+43 | +32.12% | -56.24% | $143 | $120 | 1/4 |
| recent_from_2026_06_01 | combo_guarded | 26 | $133 | $+43 | +32.12% | -56.24% | $143 | $120 | 1/4 |
| live_filled_only | raw_single | 288 | $1440 | $+36 | +2.52% | -3.41% | $0 | $0 | 0/4 |
| live_filled_only | blended_single | 221 | $1105 | $+61 | +5.56% | -2.15% | $0 | $0 | 0/4 |
| live_filled_only | heuristic_pr2b | 86 | $454 | $+53 | +11.67% | -15.35% | $254 | $240 | 1/4 |
| live_filled_only | combo_ev | 57 | $311 | $+68 | +21.91% | -19.87% | $306 | $305 | 1/4 |
| live_filled_only | combo_risk | 46 | $238 | $+94 | +39.64% | -14.24% | $319 | $335 | 3/4 |
| live_filled_only | combo_guarded | 46 | $238 | $+94 | +39.64% | -14.24% | $319 | $335 | 3/4 |

## Current Findings

- Full sample: `combo_risk` has the highest ROI (+47.53%) but still fails the missed-profit gate.
- Holdout: `combo_risk` has positive headline ROI (+22.35%) but top-5 removed ROI is -53.57%, worse than the heuristic.
- Recent slice: `combo_ev` underperforms the heuristic and `combo_risk` remains tail-dependent (top-5 removed ROI -56.24%).
- Live-filled opportunity subset: `combo_risk` is the only basket variant with 3/4 gates, but top-5 removed ROI is still -14.24%.
- `combo_guarded` shows that a blunt high-payout filter is too destructive: it reduces full-sample PnL and does not fix holdout tail risk.

## Quant Read

- Combo enumeration is directionally useful for finding higher headline ROI, but it currently concentrates risk into fewer, higher-tail legs.
- The core problem is not just top-N vs enumeration. The objective needs a better calibrated city-day temperature distribution and explicit tail controls.
- Simple lottery-leg filtering is not enough. The next research step should improve the probability distribution and evaluate combinations with walk-forward objectives.

Production remains unchanged.
