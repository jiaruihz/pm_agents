# City-Day Basket Research State and Plan - 2026-06-08

> Status: research snapshot.
> Scope: offline analysis and shadow-design only. No live order behavior changed.
> Data protocol: use current `runtime/weather.db` fact tables, CLOB coverage gate,
> and canonical `pm_history`; do not use `runtime/_legacy`.

## 1. One-Line Conclusion

City-day basket is still a valid research direction, but it is not approved for
live canary. The current strongest finding is not "combo optimizer is ready";
it is that basket decisions must be built on a stable city-day temperature
distribution, explicit tail controls, and the updated operational base that bans
known bad timing/model slices.

## 2. Current Research Modules

There are two separate modules:

| module | purpose | current status |
|---|---|---|
| Probability / blender / raw-filter module | Decide which single-market signal universe is trustworthy before basket selection. | New operational base removes 6 ECMWF-heavy losing cities and bans `T>28`. This materially improves `mid_price_core_v1_25_75` live-real results. |
| City-day basket module | Given a city-day candidate universe, choose which YES/NO legs to hold together. | PR2b and combo rules improve some slices, but still fail tail and walk-forward robustness checks. |

These should not be mixed. A bad single-market universe can make any basket look
bad; an overfit basket can make a good universe look fragile.

## 3. Latest Data Boundary

The latest basket rerun used:

- `fact_built_at_utc = 2026-06-07T05:48:57.787507+00:00`
- settled target-date range: `2026-05-06 -> 2026-06-05`
- `fact_signal_candidates`: `23299` rows, `7567` eligible, `2795` paper ordered, `503` live filled
- CLOB coverage gate: `gate_pass=true`
- raw/db fill IDs: `1302 / 1302`
- raw not in DB / DB not in raw: `0 / 0`

Important: `2026-06-06` canonical `pm_history` was not present during the latest
basket rerun, so 2026-06-06 was not used as a settled target-date sample.

## 4. What Changed After Banning T28 and ECMWF Slice

The 2026-06-08 overlay for `mid_price_core_v1_25_75` found:

| gate | kept fills | kept PnL | kept ROI | filtered PnL | delta |
|---|---:|---:|---:|---:|---:|
| no gate original | 707 | `$+10.00` | `+0.5%` | `$0.00` | `$0.00` |
| remove 6 ECMWF cities only | 537 | `$+151.05` | `+10.2%` | `$-141.06` | `$+141.06` |
| ban `T>28` only | 370 | `$+140.51` | `+14.0%` | `$-130.52` | `$+130.52` |
| remove 6 cities and `T<=28` | 329 | `$+174.56` | `+19.5%` | `$-164.57` | `$+164.57` |

The post-2026-06-01 slice is especially important:

| gate | kept fills | kept PnL | kept ROI | filtered PnL | delta |
|---|---:|---:|---:|---:|---:|
| no gate original | 277 | `$-89.06` | `-12.1%` | `$0.00` | `$0.00` |
| remove 6 cities and `T<=28` | 82 | `$+39.53` | `+20.3%` | `$-128.58` | `$+128.58` |

Interpretation: the T28/ECMWF ban is a first-layer operational filter. Basket
research should now be rerun on this new base. The 2026-06-07 basket results are
still useful as a diagnostic, but they are not the final conclusion after the
new operational filter.

## 5. Basket Results Before Applying New Operational Base

The latest basket experiment compared against both legacy baselines:

| slice | baseline | legacy raw | PR2b basket | combo market risk |
|---|---|---:|---:|---:|
| full | `25-75` | `+3.54%`, top5 `+1.92%` | `+4.27%`, top5 `-3.58%` | `+14.78%`, top5 `+1.37%` |
| holdout | `25-75` | `-5.55%`, top5 `-10.01%` | `-8.93%`, top5 `-28.25%` | `+9.04%`, top5 `-27.97%` |
| recent | `25-75` | `-4.75%`, top5 `-14.69%` | `-28.78%`, top5 `-59.14%` | `-19.16%`, top5 `-81.32%` |
| live-filled | `25-75` | `+4.33%`, top5 `-1.12%` | `+8.53%`, top5 `-15.66%` | `+40.32%`, top5 `-12.23%` |
| full | `side_band` | `+8.24%`, top5 `+4.34%` | `+9.63%`, top5 `+0.05%` | `+19.33%`, top5 `+3.90%` |
| holdout | `side_band` | `+7.54%`, top5 `-2.70%` | `+22.36%`, top5 `+1.18%` | `+43.05%`, top5 `+7.17%` |
| recent | `side_band` | `+10.86%`, top5 `-12.33%` | `+1.32%`, top5 `-51.96%` | `+18.81%`, top5 `-70.24%` |
| live-filled | `side_band` | `+12.14%`, top5 `-0.46%` | `+20.88%`, top5 `-10.52%` | `+66.17%`, top5 `+6.62%` |

Read:

- Beating the old `25-75` baseline is not enough.
- `side_band` is the harder and more relevant baseline.
- `combo_market_risk` has the best headline ROI, but recent tail-removed ROI is
  still weak.
- `PR2b` is simple and sometimes robust, but it does not consistently beat
  side-band after tail removal.

## 6. Anti-Overfit Result

Walk-forward validation used fixed candidate rules and selected by train-window
robust score, then evaluated unseen 3-day test windows.

| rule | test ROI | weighted top5-removed ROI | positive folds |
|---|---:|---:|---:|
| train-selected rule | `+2.66%` | `-35.45%` | `40.0%` |
| always heuristic PR2b | `+2.77%` | `-33.17%` | `60.0%` |
| always combo risk | `+10.11%` | `-64.63%` | `80.0%` |
| always combo market risk | `+23.86%` | `-62.26%` | `60.0%` |
| always combo market tail | `-5.14%` | `-87.17%` | `20.0%` |

This rejects the naive "highest ROI combo optimizer" interpretation. The rules
with the best headline ROI still depend too much on a few large winners.

## 7. Distribution Result

City-day distribution quality is the bottleneck:

| slice | best distribution by log loss | implication |
|---|---|---|
| full | `market_norm` / `blend_norm` close | market remains a strong anchor |
| holdout | `market_norm` | raw/blend do not beat market out of sample |
| recent | `market_norm` | do not let raw/blend dominate basket objective |
| live-filled | `market_norm` / `blend_norm` close | blend can be a filter, not yet an objective truth |

Therefore, basket scoring should use candidate legs from signal edge, but risk
and objective sanity should stay market-normalized until raw/blend distributions
prove better out of sample.

## 8. Current Decision

Do now:

- Keep the T28/ECMWF operational filter as the first-layer signal universe.
- Keep basket as shadow/research only.
- Rerun basket experiments on the filtered operational base.
- Add basket lineage fields so future settled weeks can compare real shadow
  decisions against `side_band`.

Do not do yet:

- Do not start a basket canary.
- Do not tune notional/cap values directly for ROI.
- Do not choose `combo_market_risk` only because its headline ROI is high.
- Do not treat blender as independent alpha after the operational base filter;
  it is currently a second-layer confirmation signal.

## 9. Next Research Plan

### Step 1 - Filtered-Base Basket Rerun

Add an experiment option that applies the new operational base before basket
selection:

```text
exclude cities = Ankara, BuenosAires, Jeddah, Karachi, Moscow, Munich
decision_hours_to_settle <= 28
```

Then rerun:

- `legacy_25_75`
- `legacy_side_band`
- `blended_single`
- `heuristic_pr2b`
- `combo_market_risk`
- one strict tail-control candidate

Required slices:

- full settled
- pre/post 2026-06-01
- holdout from 2026-05-26
- recent settled
- live-filled subset
- walk-forward folds

### Step 2 - Objective Sanity

Keep the optimizer objective simple:

1. Build candidate legs from raw/blend edge.
2. Normalize city-day `market_yes_price` into `market_norm`.
3. Score portfolio payoff under `market_norm`.
4. Require:
   - market EV positive;
   - leave-best-out EV not strongly negative;
   - CVaR20 bounded relative to cost;
   - missed profit no larger than avoided loss on matched legacy universe.

### Step 3 - Parameter Sensitivity, Not Parameter Search

Study `small_notional`, `normal_notional`, `city_day_cap`, and `max_legs` as risk
budget sensitivity bands. Do not approve any setting unless it survives
walk-forward and recent slices.

Recommended first bands:

| parameter | values |
|---|---|
| small notional | `$3`, `$5` |
| normal notional | `$5`, `$8` |
| city-day cap | `$10`, `$15`, `$20` |
| max legs | `3`, `4` |

### Step 4 - Shadow Lineage

Before any canary, write shadow artifacts per city-day:

- rule id
- candidate universe filter reason
- city-day distribution source
- selected legs
- rejected legs
- objective score
- EV / CVaR20 / leave-best-out EV
- worst-case payoff
- missed/avoided attribution versus legacy trigger universe

This produces real forward data without changing orders.

## 10. Future Canary Gate

Basket canary requires all of:

1. CLOB gate passes and raw/db fill IDs reconcile.
2. Settlement source coverage passes for all evaluated target dates.
3. Filtered-base basket beats `side_band` on holdout or recent settled slice.
4. Top5-removed ROI is non-negative or no worse than `side_band`.
5. Missed profit does not exceed avoided loss on matched legacy universe.
6. Walk-forward positive fold rate is at least `60%`.
7. One additional settled week of shadow decisions shows the same improvement.

Current status: approved for filtered-base rerun and shadow-lineage development;
not approved for canary.
