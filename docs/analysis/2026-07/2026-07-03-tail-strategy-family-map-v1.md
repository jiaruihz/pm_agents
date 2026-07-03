# Tail Strategy Family Map v1

Generated: 2026-07-03

## One-Line Read

当前尾部研究不要再混成一个“hotter lottery”桶。现在有两条主线：

```text
Head A: forecast-tail low-price YES lottery + TP20
  早入场 / 低价 / forecast-station basis / convex sleeve

Head B: METAR rich-current collapse / runway d1 YES reversal
  日内反转 / current 仍高价 / obs+forecast 指向一格上穿 / d1 YES
```

两者共享 canonical facts、temperature feature layer 和 sibling expression matrix，但 alpha 机制、入场时间、表达和执行假设不同。`rich_current_collapse` 适合单开新窗口/新策略族研究，不应并进低价彩票仓对话里。

## Strategy Families

| family | status | belongs to | timing | expression | execution assumption | current read |
|---|---|---|---|---|---|---|
| `forecast_tail_low_price_yes` / `low_price_yes_lottery_tiny_live_v1` | tiny live + shadow telemetry | **Head A main** | D-1 / early snapshot | low-price hotter YES, ask 0.05..0.20 | maker-first entry, TP20 exit overlay | `shadow_candidate_keep_collecting`; live is tiny forward probe, not confirmed alpha |
| `low_price_yes_take_profit_exit_v1` | tiny live exit overlay | **Head A execution** | after Head A position exists | SELL YES @0.20 | maker resting sell, taker fallback only after trigger/cancel policy | exit overlay only; not new entry alpha |
| `low_price_yes_integrated_tail_shadow_v2` / `pcal_v2` | zero-notional shadow | **Head A diagnostics** | alongside Head A candidates | no order; tags only | records station-basis, p_cal, source-aware, METAR/context fields | tells whether Head A edge is forecast-bias / station-basis / market tail underpricing |
| `hotter_tail_reversal_shapes_v1` Shape A | negative research boundary | **Head A boundary, not selector** | intraday | 3c..10c d1/d2/tail YES | hold / TP20 / TP30 all replayed | intraday cheap hotter pump is invalid; does not disprove D-1 Head A |
| `metar_reversal.rich_current_collapse_d1_yes` / Shape B4 | zero-notional shadow candidate | **Head B main** | intraday after obs prints | d1 YES primary; current bracket NO sibling | taker entry + hold-to-settle | `shadow_candidate`; all qualified rows pre-6/21, needs fresh-forward state frequency |
| `metar_reversal.false_fade_reheat_conflict` | zero-notional shadow candidate | **Head B sibling trigger** | intraday | d1 YES | taker/hold primary; TP only telemetry | same underlying shape as rich-current collapse, older threshold view |
| `metar_reversal.heat_death` | telemetry only | Head B side branch | late intraday | current_high YES / current side siblings | not approved | first rule CI crosses 0; do not live |
| `hotter_tail_state_taxonomy_v1` | support research | Head B evidence layer | intraday hourly | d1/d2/tail/current NO A/B | no runner | shows broad hotter is priced; useful for outcome taxonomy and expression mapping |
| `runway_d1_yes_reversal_expression_matrix_v1` | support research | Head B evidence layer | intraday hourly | d1 YES / d2 YES / tail / current NO / basket | no runner | one-step runway d1 is promising but thin; skip-over d1 is bad |
| `tmax_distribution_edge_shadow_v1` | separate zero-notional shadow | separate probability-engine track | intraday | current YES/NO, d1 NO, d2 NO by P(win)-ask | zero-notional | related infrastructure, not a tail-lottery sleeve |
| `regime_routed_no_route_price_disciplined_tiny_live_v1` | tiny live forward probe | existing NO runner | intraday | current NO / d2 NO routes | route-specific live policy | not the owner of Head A/B; only shares features and telemetry |

## Data Chain

```text
N100 / data-feed snapshots, observations, forecasts
  -> Mac mirror / runtime/weather.db rebuild
  -> canonical fact layer:
       fact_signal_candidates
       fact_trades
       settlement_outcomes
  -> shared feature layer:
       intraday_weather_regime_atlas_v1
       forecast/station-basis rows
       low_price_yes_tail_telemetry_model_v1
  -> same-snapshot expression matrix:
       current_high_yes
       current_bracket_no
       d1_yes / d1_no
       d2_yes / d2_no
       high_tail_yes
       baskets for diagnostics only
  -> family-specific selectors:
       Head A forecast-tail low-price YES selector
       Head B METAR rich-current collapse / runway d1 selector
       separate tmax distribution EV selector
  -> runner layer:
       tiny live only where explicitly approved
       zero-notional shadow otherwise
  -> order / fill / settlement lineage:
       plan -> order -> fill -> settlement
       CLOB fill coverage gate before live_real PnL
  -> reports and reviews:
       same-denominator A/B
       target-date block bootstrap
       forward/recent windows
       execution friction and state-frequency telemetry
```

## What The Latest Review Means

The `hotter_tail_reversal_shapes_v1` review expands the two-tail program like this:

1. For Head A, it adds a boundary:

```text
intraday 3c..10c hotter YES pump is not the same as D-1 forecast-tail low-price YES.
Do not use intraday Shape A as the Head A entry selector.
Keep Head A on its existing tiny-live + TP20 telemetry path.
```

2. For Head B, it adds the current best branch:

```text
rich_current_collapse_d1_yes:
  current_high YES still expensive
  obs still warming
  forecast target lands at least one bracket above current
  forecast peak still ahead
  buy d1 YES
  use taker entry + hold to settlement
```

This branch should be studied in its own strategy/review window. It is not a low-price lottery; it is a medium-priced one-step reversal.

## Do-Not-Mix Rules

- Do not apply Head A TP20 conclusions to Head B. Head B Shape B4 replay says TP20/TP30 hurts.
- Do not apply Head A maker-first entry assumptions to Head B. Head B maker fills are adversely selected in historical replay.
- Do not call `anchoring` a strategy. It is a market-pricing state inside Head B.
- Do not treat broad hotter-tail negative results as proof that forecast-tail low-price YES is dead.
- Do not merge Head A and Head B into the existing regime-routed NO runner unless fresh-forward evidence shows they are only expression-selection variants of that runner.

## Current Next Steps

```text
Head A:
  keep $0.8/$1 tiny live low-price YES probe
  keep TP20 exit overlay
  monitor maker entry fill, TP20 fill, capped-winner regret, live-vs-backtest drift

Head B:
  run zero-notional shadow for rich_current_conflict_b4 + false_fade trigger
  record full denominator rows, triggered rows, fresh-book quotes, obs age, forecast_steps
  do not place live orders until fresh-forward state frequency and fill feasibility are known

Shared:
  keep taxonomy/expression matrix as evidence infrastructure
  keep tmax distribution as separate probability-engine track
```
