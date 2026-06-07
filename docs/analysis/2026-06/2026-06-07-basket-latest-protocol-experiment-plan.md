# City-Day Basket Latest-Protocol Experiment Plan - 2026-06-07

> generated_at_bj: 2026-06-07
> DB: `runtime/weather.db`
> fact_built_at_utc: `2026-06-07T05:48:57.787507+00:00`
> Scope: offline research only. No N100/live behavior changed.

## 1. Data Gate

This run uses the current `weather-fact-rebuild` / `weather-live-account-reconcile`
style data protocol, not old `runtime/_legacy` data and not old analysis reports.

Required checks:

| check | result |
|---|---:|
| `fact_trades` built at | `2026-06-07T05:48:57.787507+00:00` |
| `fact_trades` settled range | `2026-05-06 -> 2026-06-05` |
| `fact_signal_candidates` settled event range | `2026-05-06 -> 2026-06-05` |
| `fact_signal_candidates` rows / eligible / paper / live | `23299 / 7567 / 2795 / 503` |
| `pm_history` for `2026-06-05` | 52 files, 47 non-null, near-binary normalized |
| `pm_history` for `2026-06-06` | 0 files |
| CLOB coverage gate | `gate_pass=true` |
| raw/db fill IDs | `1302 / 1302` |
| raw not in DB / DB not in raw | `0 / 0` |
| DB/cache/fact cost diff | `0` |

Important boundary: `2026-06-06` settlement source is not yet present in canonical
`pm_history`, so this experiment does not use 2026-06-06 as a settled target-date
sample. Open/live cashflow after that is account reconciliation context, not
settled strategy backtest evidence.

## 2. Cashflow Context

The basket experiment is target-date strategy attribution. Wallet cashflow is
reported separately by `fill_date_bj`.

For live real fills from `2026-05-06` to `2026-06-07`:

| metric | value |
|---|---:|
| actual fill cash cost | `$3457.59` |
| settled cost | `$3015.69` |
| realized PnL | `$-43.33` |
| open cost | `$441.91` |
| open MTM mid | `$-5.99` |
| open MTM bid | `$-4.60` |
| valuation snapshot | `2026-06-07T05:00:53Z` |

Largest live instances:

| instance | cash cost | realized PnL | open cost | open MTM mid |
|---|---:|---:|---:|---:|
| `mid_price_core_v1_25_75` | `$2318.50` | `$+10.00` | `$368.71` | `$-6.17` |
| `mid_price_core_v2_25_75` | `$430.97` | `$-72.74` | `$8.40` | `$0.00` |
| `mid_price_core_v1_side_band` | `$306.53` | `$+24.76` | `$64.80` | `$+0.18` |

Do not explain wallet cashflow with `order_date_bj`.

## 3. Experiment Run

Fresh outputs generated from current DB:

- `docs/analysis/2026-06/2026-06-07-city-day-basket-vs-legacy-baselines.md`
- `docs/analysis/2026-06/2026-06-07-city-day-basket-optimizer-research.md`
- `docs/analysis/2026-06/2026-06-07-city-day-basket-walkforward.md`
- `docs/analysis/2026-06/2026-06-07-city-day-distribution-quality.md`

The comparison uses `fact_signal_candidates` with `decision_window_missing = 0`.
The representative decision snapshot has `hours_to_settle` p10/median/p90 around
23 hours, so it is close to the old live T-22 to T-26 decision timing, but it is
not yet a full 30-minute snapshot replay.

## 4. Current Result

Short answer: basket research remains directionally useful, but no basket variant
is ready for live canary.

Against the original `mid_price_v1` style 25-75 baseline:

| slice | legacy raw | PR2b basket | combo market risk |
|---|---:|---:|---:|
| full | `+3.54%`, top5 `+1.92%` | `+4.27%`, top5 `-3.58%` | `+14.78%`, top5 `+1.37%` |
| holdout from 2026-05-26 | `-5.55%`, top5 `-10.01%` | `-8.93%`, top5 `-28.25%` | `+9.04%`, top5 `-27.97%` |
| recent from 2026-06-01 | `-4.75%`, top5 `-14.69%` | `-28.78%`, top5 `-59.14%` | `-19.16%`, top5 `-81.32%` |
| live-filled subset | `+4.33%`, top5 `-1.12%` | `+8.53%`, top5 `-15.66%` | `+40.32%`, top5 `-12.23%` |

Against the newer `mid_price_v1 side_band` baseline:

| slice | side-band raw | PR2b basket | combo market risk |
|---|---:|---:|---:|
| full | `+8.24%`, top5 `+4.34%` | `+9.63%`, top5 `+0.05%` | `+19.33%`, top5 `+3.90%` |
| holdout from 2026-05-26 | `+7.54%`, top5 `-2.70%` | `+22.36%`, top5 `+1.18%` | `+43.05%`, top5 `+7.17%` |
| recent from 2026-06-01 | `+10.86%`, top5 `-12.33%` | `+1.32%`, top5 `-51.96%` | `+18.81%`, top5 `-70.24%` |
| live-filled subset | `+12.14%`, top5 `-0.46%` | `+20.88%`, top5 `-10.52%` | `+66.17%`, top5 `+6.62%` |

Interpretation:

- The original 25-75 baseline is weak in holdout/recent, so beating it is not
  enough.
- The side-band baseline is the real bar. It remains strong on the recent slice.
- Combo enumeration can improve headline ROI, especially `combo_market_risk`,
  but it still has serious tail dependence in recent and walk-forward checks.
- PR2b heuristic is more robust than the old PR2 result, but recent top5-removed
  ROI is too weak.

## 5. Overfit Check

Walk-forward result:

| rule | test ROI | weighted top5-removed ROI | positive folds |
|---|---:|---:|---:|
| train-selected rule | `+2.66%` | `-35.45%` | `40.0%` |
| always heuristic PR2b | `+2.77%` | `-33.17%` | `60.0%` |
| always combo risk | `+10.11%` | `-64.63%` | `80.0%` |
| always combo market risk | `+23.86%` | `-62.26%` | `60.0%` |
| always combo market tail | `-5.14%` | `-87.17%` | `20.0%` |

This is the main anti-overfit result: the rules with best headline ROI have very
bad top5-removed ROI in unseen folds. Treat them as research signals, not
approval candidates.

## 6. Distribution Finding

City-day probability distribution quality is the core bottleneck:

| slice | best distribution by logloss | note |
|---|---|---|
| full | `market_norm` / `blend_norm` close | market slightly best |
| holdout | `market_norm` | raw/blend underperform market |
| recent | `market_norm` | market remains best |
| live-filled subset | `market_norm` / `blend_norm` close | blend top1 ok, logloss not best |

Conclusion: the optimizer objective should stay market-anchored until raw/blend
city-day distributions beat market out of sample. More complex combo objectives
will otherwise amplify model error.

## 7. Development Plan

### Step A - Data and Report Gate

Implement a small preflight for basket research:

1. Verify `pm_history` canonical source coverage for every intended settled
   target date.
2. Refuse to include dates whose settlement file is missing or literal `null`.
3. Run the 5-line fact-table self-check.
4. Run `weather_clob_fill_coverage_gate.py`.
5. Emit raw/db fill-id reconciliation in every live-related report.

This is not strategy logic; it prevents stale or half-settled samples from
driving decisions.

### Step B - Basket Research Harness

Keep the candidate set small:

1. Baselines:
   - `mid_price_v1 25-75`
   - `mid_price_v1 side_band`
   - `blended_single`
2. Basket candidates:
   - `heuristic_pr2b`
   - `combo_market_risk`
   - one strict tail-control variant
3. Required slices:
   - full settled
   - holdout from `2026-05-26`
   - recent settled
   - live-filled subset
   - walk-forward test folds
4. Required metrics:
   - PnL / ROI / win rate
   - top5-removed ROI
   - missed profit vs avoided loss
   - side split: BUY_NO / BUY_YES
   - city split for biggest gains/losses

No parameter grid should be used for approval. Parameter sweeps may be used only
to understand sensitivity, then fixed before walk-forward evaluation.

### Step C - Algorithm Direction

Keep the basket objective simple and market-anchored:

1. Build candidate legs from blended edge.
2. Score city-day portfolio payoff using `market_norm` distribution.
3. Add explicit tail checks:
   - positive market EV;
   - positive or bounded EV after removing best final-temperature outcome;
   - cap worst 20% CVaR relative to cost;
   - avoid portfolios whose headline EV comes from one lottery outcome.
4. Do not tune `3/8/notional cap` directly for ROI. Treat these as risk budget
   knobs and evaluate them with sensitivity bands.

### Step D - Shadow Integration Only

Do not open basket live canary yet.

Next dev target should be shadow-only:

1. Write basket decision artifacts into lineage for each city-day.
2. Include rule id, candidate legs, selected legs, objective score, market_norm
   distribution, tail metrics, and rejection reason.
3. Keep actual live orders unchanged.
4. After at least one more settled week, rerun the same report on real shadow
   decisions and compare against both legacy baselines.

## 8. Approval Gate for Any Future Canary

Basket canary should require all of:

1. CLOB coverage gate passes.
2. Settlement source coverage passes for the evaluated target dates.
3. Beats `mid_price_v1 side_band` on holdout or recent settled slice.
4. Does not rely on top5 winners: top5-removed ROI must be non-negative or at
   least no worse than side-band.
5. Missed profit must not exceed avoided loss on the matched legacy universe.
6. Walk-forward positive fold rate must be at least `60%`.
7. Live shadow decisions must produce the same class of improvement before any
   real notional is allocated.

Current status: not approved for canary; approved for shadow-lineage development
and continued distribution/objective research.
