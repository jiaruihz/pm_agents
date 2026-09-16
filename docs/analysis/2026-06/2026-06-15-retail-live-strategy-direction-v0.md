# Retail Live Strategy Direction v0

> generated_at_utc: `2026-06-14T16:15:52Z`
> target_metric: `retail_feasible_weather_live_candidate`
> scope: strategy-goal consolidation only; no N100/live config changed; no orders placed.

## Data Snapshot

- Data source: `runtime/weather.db`, `fact_signal_candidates`, `fact_trades`, plus existing analysis reports listed below.
- DB fact build: `2026-06-14T16:08:13.367378+00:00`.
- CLOB fill coverage gate: `gate_pass=true`; `missing_order_rows=0`; `over_order_keys=0`; `db_fill_cost_minus_fact_cost=0.0`.
- This report does not publish `live_real` PnL, ROI, rank, or curves. The CLOB gate is recorded only as data-integrity context.

### Mandatory 5-Line SQL Self-Check

```text
MAX(fact_built_at_utc) FROM fact_trades -> 2026-06-14T16:08:13.367378+00:00
trade_class distribution -> live_real 855, live_simulated 624, paper 2285, snapshot_replay 636
settlement_status distribution -> blank 150, settled 4250
fact_signal_candidates coverage -> rows 29313, eligible 10031, paper_ordered 3824, live_filled 348
CLOB orders with fills -> error 33 / with_fill 0, submitted 961 / with_fill 855
```

## Corrected Goal State

The goal is not "find the best historical ROI row". The goal is a strategy a small retail account can actually execute with limited speed, limited queue priority, and visible orderbook slippage.

That changes the ranking:

| family | latest status | retail-live interpretation |
|---|---|---|
| `all_yes_underround_basket_v0` | offline-confirmed, paper-shadow engineering path exists | Park as market-structure research. It is not the current retail live path because it needs low-latency all-leg-or-none execution, clean FOK behavior, partial-fill unwind, and enough depth across every YES leg. |
| forecast-quality BUY_NO single-leg | `shadow_only` | Keep as low-frequency shadow sleeve. The strict `forecast_quality_low=0` filter over-shrinks the sample; `city_model_reliable` is a better soft tag but still has thin forward support. |
| forecast-quality reliability base | research / shared feature layer | Keep as a tag layer for later strategies. Do not use it as a hard live gate. |
| broad Range RV variants | mostly `inconclusive` | Prior broad searches failed holdout, top-date stress, or executable orderbook gates. Do not reopen broad search. |
| forecast-bounded Range RV combo | next retail-feasible research direction | This is the preferred next branch: small basket around a forecast interval, fewer legs than all-YES, explicit orderbook checks, and tolerance rules for partial execution. |

## What Was Tried

### 1. BUY_NO Forecast-Quality Sleeve

The strongest tested single-leg sleeve was:

```text
BUY_NO only
model_version = ecmwf
no_edge >= 0.10
0.40 <= no_cost <= 0.75
city + event_date top1
$5/order
```

The first hardening used `forecast_quality_low=0`, but that was too strict: it compressed the family from a broad same-family baseline into a small sample and left the holdout fragile. The better lesson was that reliability tags should rank or size candidates, not hard-delete most of the universe.

Current action: keep shadow only; do not tiny-live.

### 2. Forecast-Quality / Reliability Base

The useful output is a shared label layer:

- `forecast_quality_medium_plus`: useful soft allow / risk tag.
- `city_model_reliable`: useful soft tag, especially for BUY_NO and side-band overlays.
- `forecast_quality_low`: weak-quality diagnostic, not an automatic no-trade rule.
- `model_market_disagreement_high` and `tail_risk_high`: diagnostic tags only.

Current action: consume these labels in future Range RV / side-band / BUY_NO studies, but always compare against that family its own no-quality baseline.

### 3. Range RV And Adjacent Families

The broad Range RV line tested adjacent2/3, market-shape, temporal reversion, tail fade, center/shoulders/butterfly, no-arb, and expression variants. Most failed one of the hard gates: significance, baseline, forward, top-date stress, or time-aligned orderbook executability.

The useful correction from this line is not another broad scanner. It is a narrower retail expression:

```text
forecast-bounded Range RV combo =
  pick a compact interval around the forecast distribution
  express it with inside YES and/or outside NO legs
  price every leg on orderbook snapshots with snapshot_ts <= decision_ts
  cap basket width and notional
  evaluate by city-day / event_date, not independent legs
```

Current action: make this the next research branch if continuing toward live.

### 4. All-YES Underround Basket

The historical and offline result is real: all-YES underround passed proxy and time-aligned executable threshold tests in the 2026-06-09 robustness run.

The retail-live problem is also real:

- Opportunities are sparse and flickery.
- A 2%-3% gross underround on a 5-share basket is only about `$0.10-$0.15` gross profit.
- The basket needs every YES leg filled at the observed ask; one missing leg can turn a no-arb expression into directional exposure.
- A small account is unlikely to beat latency and queue-quality constraints consistently.
- Partial fills require cancel/unwind logic that is more complex than the edge size justifies for a first live probe.

Current action: keep as a market-structure reference and engineering sandbox, but do not treat it as the leading retail live strategy.

## Current Production Distance

| requirement | current state | gap |
|---|---|---|
| Historical edge | all-YES confirmed offline; BUY_NO and broad Range RV not confirmed for live | Need retail-feasible expression with confirmed gates. |
| Forward paper/shadow | all-YES has TTL-valid pending baskets but no settled forward proof yet | Need settled forward baskets and active dates before any live review. |
| Executability | all-YES dry-run/FOK proof exists, but not live armed | Retail path should reduce leg count and avoid all-market all-leg dependency. |
| Capacity | all-YES capacity exists only at tiny gross profit and high execution precision | Need range combo depth checks and per-basket slippage stress. |
| Operational safety | CLOB coverage gate is healthy; no production config changed | Any live change must go through `weather-strategy-deploy`. |

## Recommended Next Engineering Track

Freeze the current all-YES work as `retail_live_blocked / research_only`, then build a narrow `forecast_bounded_range_rv_shadow_v0` branch.

Pre-register the first version instead of searching broadly:

```text
universe: settled city-day weather markets with complete bracket set
forecast source: ECMWF first, with forecast-quality labels logged
interval: compact range around forecast mode / adjacent mass
expressions: inside YES basket, outside NO basket, choose-cheaper equivalent expression
max legs: 2-4 preferred; reject all-market all-YES baskets
pricing: best ask from orderbook snapshot_ts_utc <= decision_snapshot_ts_utc
unit: city + event_date basket, not per leg
size: zero-notional shadow first, then paper, then tiny-live only after gates
```

Promotion gates:

| gate | minimum |
|---|---|
| sample | at least 20 settled baskets and 7 active event_dates in forward paper/shadow |
| significance | event_date block bootstrap excess ROI CI does not cross 0 |
| baseline | beats same-city same-cost simple expression baseline |
| robustness | top5 removed ROI remains positive |
| execution | all selected legs have matched orderbook rows and bounded spread/depth |
| retail feasibility | median basket has 2-4 legs, non-dust depth, and expected gross edge larger than one tick plus slippage buffer |

## Verdict

`all_yes_underround_basket_v0` is demoted from "best live candidate" to `offline_confirmed_but_retail_live_blocked`.

The best current live-oriented direction is not all-YES. It is a narrower forecast-bounded Range RV basket that borrows the reliable parts of the prior work: forecast-quality tags, city-day basket accounting, time-aligned orderbook pricing, and strict forward shadow gates.

```text
significance=NA
baseline=NA
forward=NA
conclusion=research_consolidation_only
live_action=none
next_branch=forecast_bounded_range_rv_shadow_v0
```

## Evidence Pointers

- `docs/analysis/2026-06/2026-06-09-range-rv-underround-robust-v1-0.md`
- `docs/analysis/2026-06/2026-06-14-all-yes-underround-live-prep-v0.md`
- `docs/analysis/2026-06/2026-06-14-all-yes-underround-persistence-v0.md`
- `docs/analysis/2026-06/2026-06-13-forecast-quality-base-v0.md`
- `docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.md`
- `docs/analysis/2026-06/2026-06-11-range-dual-expression-v0.md`
