# Low-Price YES Lottery Shadow v1

Generated: 2026-07-01

## Verdict

`low_price_yes_lottery_reversal_shadow_v1` is now started as an independent zero-notional shadow head. It does not connect to the regime-routed NO runner and does not place orders.

Historical backtest for the frozen prompt is strong enough to collect forward evidence, but not enough for live:

```text
side = BUY_YES
decision_entry_price between 0.01 and 0.25
edge >= 0.20
one candidate per city-date, earliest PIT decision snapshot, lower ask tie-break
```

On the settled v2 source-grain overlay, this prompt has 674 rows / 50 dates / 48 cities, avg ask 0.087, win rate 11.9%, ROI +50.0%, target-date block bootstrap CI [+15.3%, +87.6%]. Holdout is +36.3% and recent is +36.6%, but holdout/recent CI still cross zero and top-day concentration remains material.

```text
significance=PASS for full historical v2
baseline=PARTIAL (beats market ask break-even, but no same-row expression baseline)
forward=PARTIAL (offline holdout/recent positive, no true forward settled shadow yet)
conclusion=shadow_candidate
```

## Core Logic

This is a tail-reversal / lottery head, not a current-runner expression selector.

The trade shape is: the market prices a hotter or exact high bracket as a cheap tail outcome, while the fact-table model still gives it materially higher probability. If the day later resolves through a forecast miss, station-vs-forecast basis, intraday reheat, or market underreaction to a high-tail path, the YES pays convexly. Most days lose small fixed stakes; the edge, if real, comes from occasional 5x-20x wins.

What is not confirmed yet:

- It is not yet proven to be pure forecast-bias alpha.
- It may include market/base-rate favorite-longshot mispricing.
- City/source/regime attribution remains descriptive until more forward settlements arrive.

## Historical Expected Return

At `$5/signal` on the v2 settled backtest:

| Window | Rows | Dates | Avg rows/day | Cost/day | ROI | PnL/day | Losing days | <= -50% days | Max daily loss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 674 | 50 | 13.5 | $67.40 | +50.0% | +$33.72 | 22 | 14 | -$80.00 |
| holdout | 418 | 26 | 16.1 | $80.38 | +36.3% | +$29.22 | 13 | 9 | -$80.00 |
| recent | 330 | 19 | 17.4 | $86.84 | +36.6% | +$31.82 | 9 | 8 | -$80.00 |

This payoff profile is not smooth income. It is a capped lottery sleeve: many losing dates are normal, and position size has to assume clustered zero-payoff days.

## First Forward Capture

Durable writer:

```bash
.venv/bin/python scripts/ops/low_price_yes_lottery_reversal_shadow_v1.py --min-event-date 2026-06-27
```

Runtime outputs:

- `runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/shadow_candidates.jsonl`
- `runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/latest_summary.json`
- `runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/summary_history.jsonl`

First run result:

| Target date | Selected rows | Cities | Avg ask | Avg edge | Hypothetical cost |
|---|---:|---:|---:|---:|---:|
| 2026-06-27 | 16 | 16 | 0.0585 | 0.2835 | $80 |
| 2026-06-28 | 13 | 13 | 0.0546 | 0.2827 | $65 |
| 2026-06-30 | 3 | 3 | 0.0720 | 0.2884 | $15 |
| total | 32 | 23 | 0.0582 | 0.2837 | $160 |

Raw matching rows were 40; city-date dedupe reduced them to 32. Every journal row has `execution_mode=zero_notional_shadow`, `no_order_placed=true`, and `shadow_notional_usd=0.0`.

## Next Checks

Before even discussing tiny live, this head needs:

- true forward settlement for the 6/27-6/30 journaled rows;
- daily loss distribution after settlement, not just historical backfill;
- city/source/regime attribution on forward rows;
- comparison against cheap-YES base-rate and random low-price YES controls;
- execution feasibility check on real depth/spread if it survives settlement.

Current action: keep collecting shadow only.

## Forward Settlement Check - 2026-07-01

Canonical `settlements` / `settlement_outcomes` still only cover through 2026-06-26 after N100 sync + DB rebuild, so this check uses a research-only CLOB market overlay keyed by the journaled `condition_id`. It does not write canonical settlement tables.

Evaluator:

```bash
.venv/bin/python scripts/analysis/forecast_quality/evaluate_low_price_yes_lottery_shadow_settlement_v1.py
```

All 32 journaled rows are now closed on the CLOB market API.

| Metric | Value |
|---|---:|
| settled rows | 32 |
| dates / cities | 3 / 23 |
| avg ask | 0.0582 |
| wins | 3 |
| win rate | 9.4% |
| hypothetical cost | $160.00 |
| PnL | +$51.67 |
| ROI | +32.3% |
| date-block CI | [-64.3%, +866.2%] |
| losing days | 2 / 3 |
| <= -50% days | 1 / 3 |
| max daily loss | -$51.43 |
| top trade removed ROI | -56.9% |

Daily:

| target_date | rows | wins | cost | PnL | ROI |
|---|---:|---:|---:|---:|---:|
| 2026-06-27 | 16 | 1 | $80.00 | -$51.43 | -64.3% |
| 2026-06-28 | 13 | 1 | $65.00 | -$26.83 | -41.3% |
| 2026-06-30 | 3 | 1 | $15.00 | +$129.93 | +866.2% |

Winners:

| target_date | city | bracket | ask | PnL |
|---|---|---:|---:|---:|
| 2026-06-30 | Beijing | 30 | 0.0345 | +$139.93 |
| 2026-06-28 | Shanghai | 30 | 0.1310 | +$33.17 |
| 2026-06-27 | Jeddah | 39+ | 0.1750 | +$23.57 |

Interpretation: the first true forward settlement is directionally supportive because it stayed positive out-of-sample, but it is not robust evidence. The result is dominated by one 3.45c Beijing YES; removing the top trade turns ROI to -56.9%. This confirms the payoff shape is genuinely lottery-like: small daily losses are expected, and a small number of tail hits determines PnL.

Updated conclusion remains `shadow_candidate_keep_collecting`, not live.
