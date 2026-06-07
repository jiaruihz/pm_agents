# City-Day Basket vs Legacy Bucket Baselines — 2026-06-08

> generated_at_utc: `2026-06-07T16:32:36+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline fair-baseline diagnostic only; no N100/live behavior changed.

## Target Question

Does city-day basket selection improve over the legacy per-bucket independent selector on the same live-like decision timing and entry universe?

This report separates two failure modes:

- If both legacy and basket lose on the same slice, the weather/probability model or market regime is likely the first problem.
- If legacy wins but basket loses, the city-day grouping/objective is likely the first problem.

## Timing

The source table is `fact_signal_candidates` with `decision_window_missing = 0`, i.e. the existing T-22~24h representative decision snapshot. It is close to the old live timing but is not a full 30-minute snapshot replay.

## Rules

- `legacy_raw_independent`: old-style per bucket raw model gate with live-like entry band and min edge.
- `legacy_blended_independent`: same per bucket gate, replacing raw probability with blend.
- `basket_pr2b_same_entry_band`: PR2b basket over the same entry-band universe.
- `basket_pr2b_legacy_raw_triggers`: PR2b basket restricted to rows the legacy raw rule would have triggered.
- `combo_market_risk_same_entry_band`: combo optimizer over the same entry-band universe.
- `combo_market_risk_legacy_raw_triggers`: combo optimizer restricted to legacy raw triggered rows.

## Backtest Summary

| slice | profile | rule | n | cost | pnl | ROI | top5 ROI | missed vs legacy | avoided vs legacy |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| full | legacy_25_75_e10 | legacy_raw_independent | 930 | $4650 | $+131 | +2.82% | +1.24% | $0 | $0 |
| full | legacy_25_75_e10 | legacy_blended_independent | 96 | $480 | $+104 | +21.63% | +7.15% | $1717 | $1690 |
| full | legacy_25_75_e10 | basket_pr2b_same_entry_band | 274 | $1434 | $+40 | +2.80% | -4.89% | $1315 | $1185 |
| full | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 265 | $1351 | $+70 | +5.18% | -2.79% | $1284 | $1185 |
| full | legacy_25_75_e10 | combo_market_risk_same_entry_band | 170 | $860 | $+98 | +11.44% | -1.67% | $1506 | $1405 |
| full | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 159 | $757 | $+131 | +17.37% | +2.70% | $1491 | $1415 |
| full | legacy_side_band | legacy_raw_independent | 459 | $2295 | $+171 | +7.46% | +3.65% | $0 | $0 |
| full | legacy_side_band | legacy_blended_independent | 71 | $355 | $+17 | +4.77% | -4.78% | $1024 | $870 |
| full | legacy_side_band | basket_pr2b_same_entry_band | 287 | $1484 | $+116 | +7.79% | -1.59% | $664 | $595 |
| full | legacy_side_band | basket_pr2b_legacy_raw_triggers | 119 | $816 | $+207 | +25.41% | +9.41% | $724 | $715 |
| full | legacy_side_band | combo_market_risk_same_entry_band | 192 | $936 | $+151 | +16.14% | +1.05% | $870 | $835 |
| full | legacy_side_band | combo_market_risk_legacy_raw_triggers | 59 | $467 | $+166 | +35.60% | +6.73% | $918 | $855 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_raw_independent | 359 | $1795 | $-122 | -6.77% | -10.94% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_blended_independent | 40 | $200 | $+36 | +18.21% | -16.61% | $577 | $735 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 113 | $613 | $-71 | -11.65% | -29.96% | $472 | $490 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 110 | $591 | $-57 | -9.68% | -28.24% | $451 | $490 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 64 | $337 | $+3 | +0.95% | -33.76% | $545 | $620 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 60 | $300 | $+7 | +2.22% | -36.66% | $539 | $620 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_raw_independent | 185 | $925 | $+52 | +5.66% | -4.01% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_blended_independent | 24 | $120 | $-25 | -21.24% | -54.34% | $463 | $385 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_same_entry_band | 125 | $683 | $+121 | +17.74% | -2.54% | $209 | $245 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 62 | $430 | $+147 | +34.19% | +4.39% | $231 | $290 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_same_entry_band | 78 | $414 | $+142 | +34.41% | +0.25% | $307 | $365 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 30 | $240 | $+122 | +50.83% | -6.40% | $341 | $365 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 130 | $650 | $-54 | -8.29% | -16.38% | $0 | $0 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 11 | $55 | $-12 | -20.92% | -100.00% | $218 | $260 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 51 | $260 | $-85 | -32.60% | -58.77% | $173 | $145 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 46 | $230 | $-93 | -40.24% | -65.78% | $170 | $145 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 28 | $144 | $-48 | -33.20% | -85.16% | $208 | $200 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 25 | $120 | $-57 | -47.28% | -98.96% | $202 | $195 |
| recent_from_2026_06_01 | legacy_side_band | legacy_raw_independent | 68 | $340 | $+18 | +5.20% | -14.39% | $0 | $0 |
| recent_from_2026_06_01 | legacy_side_band | legacy_blended_independent | 7 | $35 | $-17 | -48.73% | -100.00% | $165 | $130 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 49 | $252 | $-21 | -8.38% | -54.56% | $88 | $60 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 23 | $150 | $-26 | -17.03% | -90.37% | $104 | $75 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 30 | $160 | $+0 | +0.25% | -75.64% | $122 | $110 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 11 | $88 | $-20 | -23.19% | -100.00% | $140 | $110 |
| live_filled_only | legacy_25_75_e10 | legacy_raw_independent | 278 | $1390 | $+34 | +2.47% | -2.58% | $0 | $0 |
| live_filled_only | legacy_25_75_e10 | legacy_blended_independent | 37 | $185 | $+7 | +3.61% | -31.17% | $513 | $485 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_same_entry_band | 88 | $457 | $+28 | +6.02% | -16.84% | $352 | $350 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 85 | $433 | $+28 | +6.36% | -17.81% | $357 | $355 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_same_entry_band | 48 | $229 | $+71 | +31.01% | -17.47% | $423 | $445 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 48 | $229 | $+71 | +31.01% | -17.47% | $423 | $445 |
| live_filled_only | legacy_side_band | legacy_raw_independent | 138 | $690 | $+64 | +9.25% | -2.42% | $0 | $0 |
| live_filled_only | legacy_side_band | legacy_blended_independent | 27 | $135 | $-28 | -20.81% | -51.26% | $327 | $235 |
| live_filled_only | legacy_side_band | basket_pr2b_same_entry_band | 85 | $425 | $+70 | +16.57% | -12.80% | $199 | $160 |
| live_filled_only | legacy_side_band | basket_pr2b_legacy_raw_triggers | 39 | $258 | $+75 | +29.11% | -15.65% | $218 | $205 |
| live_filled_only | legacy_side_band | combo_market_risk_same_entry_band | 49 | $232 | $+127 | +54.75% | -0.39% | $258 | $270 |
| live_filled_only | legacy_side_band | combo_market_risk_legacy_raw_triggers | 17 | $131 | $+85 | +64.86% | -35.52% | $277 | $270 |

## Actual Live Reference

This is actual settled `live_real` PnL by recorded instance; it is not forced to the same representative snapshot, so use it as context, not a strict counterfactual.

| instance | fills | date range | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| live_weather_edge_v1_4b07f7abc42f | 62 | 2026-06-01 -> 2026-06-06 | $175 | $-5 | -3.04% | 53.2% |
| live_weather_edge_v1_4ef9b3ec3e2e | 774 | 2026-05-16 -> 2026-06-06 | $2093 | $+8 | +0.36% | 52.7% |
| live_weather_edge_v1_91f019941593 | 54 | 2026-06-01 -> 2026-06-06 | $107 | $+27 | +25.62% | 38.9% |
| live_weather_edge_v1_986d901ccc58 | 177 | 2026-05-29 -> 2026-06-06 | $431 | $-76 | -17.67% | 39.0% |
| live_weather_edge_v1_c13ccf0c3181 | 76 | 2026-05-24 -> 2026-06-01 | $213 | $-28 | -13.28% | 53.9% |
| live_weather_edge_v1_edb2b6f8df82 | 59 | 2026-05-27 -> 2026-05-30 | $188 | $+23 | +12.21% | 52.5% |

## Quant Read

- Judge basket against `legacy_raw_independent` first; ROI alone is not enough.
- `basket_pr2b_legacy_raw_triggers` isolates the grouping/sizing effect on the exact old-triggered rows.
- `same_entry_band` variants test whether basket finds better opportunities when given the same live-like price universe.
- Negative top5-removed ROI remains a tail-dependency warning even if headline ROI is positive.

Production remains unchanged.
