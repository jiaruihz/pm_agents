# HeadA Would-Live Shadow Performance v1

Generated: 2026-07-24

> Correction: this report evaluates every captured `would_live_entry`, but the
> runtime had not materialized `forecast_to_bracket_low_native` from its live
> snapshot. The configured `dist>0` boundary therefore did not actually run
> for this window. Treat the metrics below as the captured, polluted cohort;
> the corrected intended-policy audit is
> [2026-07-24-heada-shadow-distance-enforcement-audit-v1.md](2026-07-24-heada-shadow-distance-enforcement-audit-v1.md).

## Verdict

The first true HeadA zero-notional window is directionally positive but far too short to validate the strategy. It remains shadow-only.

```text
window = target_date 2026-07-16..2026-07-23
primary expression = BUY YES at first would-live fresh best ask + official Weather taker fee
settled entries = 75 / 7 target dates / 39 cities
win rate = 14.7% (11 / 75), avg best ask = 12.2c
fee-adjusted PnL = +$7.44 on $47.56 cost
ROI = +15.6%, target-date block bootstrap CI [-22.1%, +60.6%]
market-fee baseline ROI = -4.1%; excess ROI = +19.8pp, CI [-18.0pp, +64.7pp]

significance=FAIL
baseline=FAIL
forward=NA
conclusion=inconclusive
action=keep zero-notional shadow; do not restore HeadA live or change source/size gates
```

The positive point estimate is not confirmed alpha. On the same 75 settled rows, the market ask is a better probability forecast than HeadA: Brier `0.121` versus `0.193`, and logloss `0.393` versus `0.572`. Model `p_yes` averaged 41.0% while realized win rate was 14.7%.

## Scope And Data Integrity

This is the current HeadA runner, not the older `low_price_yes_lottery_reversal_v1` shadow head.

- Strategy: `forecast_quality.low_price_yes_lottery` / `low_price_yes_lottery_tiny_live_v1`.
- Signal grain: first `would_live_entry=true` record per `signal_id` from `runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/would_live_entries.jsonl`.
- Performance attribution: `target_date`, not journal-write date.
- Settlement: canonical `settlement_outcomes`, joined by `(city, target_date, bracket)`. This fallback is required because source-grain settlement rows have no condition id.
- Refresh: Mac market mirror synchronized, then 2026-07-22 and 2026-07-23 PM-history settlement partitions incrementally ingested before this run.
- 2026-07-24 has 11 would-live entries and is excluded because it is not settled.

This is zero-notional opportunity performance. There are no HeadA shadow orders or fills, so this is not live PnL or maker execution evidence.

## Funnels

| Funnel | Grain | Count | Meaning |
|---|---|---:|---|
| signal | first would-live entry | 86 | frozen selector produced a first eligible city/bracket signal |
| signal | distinct signal ids | 86 | no duplicate signal-id rows |
| evidence | fresh best ask captured | 86 | every would-live entry retained top-of-book terms |
| evidence | canonical settled | 75 | 7 target dates available for realized evaluation |
| evidence | not settled | 11 | target date 2026-07-24, not losing outcomes |
| execution | actual HeadA fills | 0 | expected in zero-notional shadow |

## Primary Performance

Primary cost is `fresh_best_ask * shares + 0.05 * ask * (1-ask) * shares`. The fee term totals $1.97.

| Metric | Value |
|---|---:|
| rows / target dates / cities | 75 / 7 / 39 |
| wins / win rate | 11 / 14.7% |
| avg fresh best ask | 12.2c |
| cost / fee / PnL | $47.56 / $1.97 / +$7.44 |
| fee-adjusted ROI | +15.6% |
| target-date block bootstrap 95% CI | [-22.1%, +60.6%] |
| losing days | 3 / 7 |
| `<= -50%` days | 1 / 7 |
| max daily loss | -$3.78 |
| top-winning-ticket removed ROI | +6.3% |

| target date | rows | wins | cost | PnL | ROI |
|---|---:|---:|---:|---:|---:|
| 2026-07-16 | 8 | 2 | $5.32 | +$4.68 | +87.8% |
| 2026-07-18 | 1 | 0 | $0.26 | -$0.26 | -100.0% |
| 2026-07-19 | 5 | 1 | $2.51 | +$2.49 | +99.4% |
| 2026-07-20 | 12 | 3 | $8.78 | +$6.22 | +70.9% |
| 2026-07-21 | 14 | 1 | $8.78 | -$3.78 | -43.1% |
| 2026-07-22 | 20 | 3 | $13.45 | +$1.55 | +11.5% |
| 2026-07-23 | 15 | 1 | $8.46 | -$3.46 | -40.9% |

## Same-Denominator Baseline And Probability Layer

The mechanical market baseline uses fresh best ask as the probability for the same selected YES rows and charges the same official fee. Its expected ROI is `-4.1%`; realized HeadA excess is `+19.8pp`, but the date-block CI crosses zero.

| Probability metric | HeadA model | fresh market ask |
|---|---:|---:|
| mean probability | 41.0% | 12.2% |
| realized win rate | 14.7% | 14.7% |
| Brier, lower is better | 0.193 | 0.121 |
| logloss, lower is better | 0.572 | 0.393 |
| model AUC | 0.668 | n/a |

The model has some rank information, but its point probabilities are badly overconfident. This result cannot justify probability sizing or a claim that the strategy's positive ROI proves forecast alpha.

## Execution And Source Diagnostics

At recorded maker-limit prices, the price-only counterfactual is +37.3% ROI (`+$14.94` on `$40.06`). It is not the primary result: shadow cannot establish maker queue position, fill probability, waiting time, or adverse selection.

| actual forecast source | rows | wins | fee-adjusted ROI |
|---|---:|---:|---:|
| ECMWF | 44 | 9 | +66.0% |
| GFS | 31 | 2 | -51.1% |

This source split is descriptive only. Seven target dates mix city and day regimes, so promoting ECMWF or excluding GFS would be a post-hoc filter.

## Next Check

Keep the identical frozen shadow selector. The next run should add 2026-07-24 settlements with no tuning. No live, sizing, source, TP, or stop change follows from this result.

Artifacts: [summary.json](generated/heada_would_live_shadow_v1/summary.json), [entries.csv](generated/heada_would_live_shadow_v1/entries.csv), and [daily.csv](generated/heada_would_live_shadow_v1/daily.csv). Evaluator: `scripts/analysis/forecast_quality/evaluate_heada_would_live_shadow_v1.py`.
