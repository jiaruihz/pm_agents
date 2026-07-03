# Tail / METAR Reversal Review Prompt v1

Generated: 2026-07-03

## Purpose

This is a handoff prompt for Fabel to review two related but separate weather strategy heads:

1. `forecast_tail_low_price_yes`: forecast/station-basis driven low-price YES convex sleeve.
2. `metar_reversal_false_fade`: intraday METAR/orderbook conflict driven reversal sleeve.

Do not merge them by default. Treat them as separate hypotheses unless the evidence says one is only an expression selector for the other.

## Latest Data Refresh

Actions completed before this prompt:

- Ran `scripts/ops/sync_weather_remote.sh` on 2026-07-03.
- Correction after audit: the first sync used the legacy `weather-predict` source, which no longer had 2026-07-01 full snapshots. Re-ran `scripts/ops/sync_weather_remote.sh --market-only --market-source=weather-data-feed` to pull the current `weather_data_feed_service_runtime` output.
- Ran `scripts/weather_dashboard/run_stack.sh`; DB/fact rebuild completed, final exit was only frontend port 5174 still busy.
- Verified `weather_clob_fill_coverage_gate.py`: `gate_pass=true`, DB fills and local CLOB cache both 869 fill ids.
- Verified `fact_trades`: 4,414 rows, target dates 2026-05-06..2026-07-01, built at `2026-07-03T13:32:04Z`.
- Verified `fact_signal_candidates` after rebuilding from the corrected data-feed sync: 2026-07-01 now has 864 rows, 558 with `decision_snapshot_ts_utc`; fact layer built at `2026-07-03T13:47:09Z`.
- Verified `settlement_outcomes`: 24,050 rows, canonical full sibling settlement only through 2026-06-26, plus partial 2026-06-27 TelAviv and 2026-06-28 Singapore.
- Built 2026-07-01 IEM/WU observation caches: 36 stations ok.
- Built 2026-07-01 observed-max detail: 432 rows, 36 cities, h10..h21.
- Built 2026-07-01 reheat feature rows after corrected sync: 2,490 quote feature rows / 229 city-date-hour state rows.
- Re-ran METAR expression matrix: matrix rows now cover 2026-05-19..2026-07-01, with 8,206 total matrix rows and 7,750 canonical-settled rows.

Important limitation:

- 2026-07-01 historical orderbook snapshots do exist in the data-feed runtime and are now mirrored locally; the earlier missing-state was a sync-source bug, not missing upstream data.
- Canonical `settlement_outcomes` still has full sibling settlement only through 2026-06-26, with partial 6/27-6/28. Therefore 2026-07-01 can enter same-denominator expression matrix / telemetry, but not canonical settled PnL until settlement is ingested.
- N100 data-feed timers were found inactive after 2026-07-01 15:58 UTC because systemd hit `Read-only file system` while setting service stdout. That is a separate production data-feed continuity issue; 2026-07-02+ full-snapshot coverage should be treated as suspect until the N100 filesystem/timers are repaired and re-enabled.

## Current Strategy Head A: Forecast-Tail Low-Price YES

Core idea:

Low-price YES tickets may be underpriced when the model still assigns meaningful tail probability and the city/station/model basis makes hotter outcomes more plausible than the market implies. The alpha hypothesis is not "cheap YES by itself"; it is:

```text
as-of station-basis prior
+ model probability edge
+ market ask band 0.05..0.20
+ convex payout
+ one-shot city-date selection discipline
```

Current live state:

- Tiny live on Mac as `low_price_yes_lottery_tiny_live_v1`.
- Current notional: small fixed notional, recently reduced to about $1/order.
- Entry execution: maker-first / taker-fallback, not pure taker.
- Exit overlay: `low_price_yes_take_profit_exit_v1`, pre-place full-position `SELL YES @0.20` maker after wallet position exists; cancel/repost by TTL; taker fallback only after maker cancel path.
- No stop loss.

Key evidence:

- Main thesis: [2026-07-02-low-price-yes-tail-strategy-thesis-v1.md](2026-07-02-low-price-yes-tail-strategy-thesis-v1.md)
- TP replay / live exit overlay: [2026-07-03-low-price-yes-take-profit-v1.md](2026-07-03-low-price-yes-take-profit-v1.md)

Numbers to audit:

- V1 no-dust denominator: 457 rows / 50 dates, avg ask about 0.105, win about 13.6%, fixed-$1 proxy ROI about +24.2%.
- Broad TP replay denominator: 476 rows / 53 dates, ask 0.05..0.20.
- Hold-to-settlement, +1c entry / -1c exit stress: ROI +13.9%, CI [-12.3%, +58.7%].
- Full sell when future bid >= 0.20, +1c entry / -1c exit stress: ROI +72.5%, delta vs hold +58.6pp, delta CI [+35.4pp, +66.1pp].
- Forward/closed 2026-06-27..06-30 is only 19 rows / 3 dates; too thin for promotion.

Current verdict:

```text
forecast_tail_low_price_yes:
  conclusion=shadow_candidate_keep_collecting
  live=tiny only, already approved
  do_not_size_up=true
  do_not_treat_TP20_as_entry_alpha_confirmation=true
```

Main doubts:

- Execution edge may be materially lower if maker queues do not fill and taker fees dominate.
- Recent forward is tiny and convex sleeves are top-winner sensitive.
- Station-basis / source-aware variants look interesting but have city/source confounding.
- TP20 improves replay strongly, but it can cap true 100c winners; it is an execution overlay, not proof that entries are good.

## Current Strategy Head B: METAR False-Fade / Reheat Reversal

Core idea:

This is not a D-1 forecast-tail lottery. It is an intraday runway/tail-reversal strategy:

```text
physical runway still exists
+ market has not fully priced the break above current
+ METAR says temperature is still rising
+ d1 YES is still cheap
=> buy d1 YES, or compare sibling reversal expressions
```

Current live state:

- Not live.
- Should remain independent research/shadow head.
- Candidate runner exists only for zero-notional/shadow style telemetry: `scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py`.

Key evidence:

- Plan: [2026-07-03-metar-reversal-expression-matrix-plan-v1.md](2026-07-03-metar-reversal-expression-matrix-plan-v1.md)
- Expression matrix: [2026-07-03-metar-reversal-expression-matrix-v1.md](2026-07-03-metar-reversal-expression-matrix-v1.md)
- TP replay: [2026-07-03-metar-reversal-take-profit-v1.md](2026-07-03-metar-reversal-take-profit-v1.md)
- Strategy lineage: [2026-07-03-metar-reversal-strategy-lineage-v1.md](2026-07-03-metar-reversal-strategy-lineage-v1.md)

Pre-registered trigger currently tested:

```text
temp_trend_1h_f >= 0.5
forecast_gap_native >= 1.0
forecast_peak_delta_hours_local <= 0
d1_yes_ask <= 0.30
current_high_yes_ask >= 0.40
expression = BUY d1 YES
```

Key result after rerun:

- Denominator: 7,750 settled city-date-hour snapshots, 2026-05-19..2026-06-26, 36 cities.
- Trigger rows: 32 rows / 22 dates / 15 cities.
- Avg ask: 0.175.
- Win rate: 43.8%.
- ROI: +119.5%.
- Date-block CI: [+29.8%, +216.2%].
- +1c taker stress ROI: +107.6%.
- Top5-removed ROI: +27.2%.
- Same-trigger alternative `current_bracket_no`: ROI +93.0%, CI [+17.9%, +176.0%], but weaker top5-removed +3.8%.
- Same-leg warming-pool complement: d1 YES ROI -17.7%, CI [-31.7%, +0.5%].
- Monthly split: May +237.8%; June +38.6% with CI [-49.3%, +148.7%].
- Fresh trigger rows after 2026-06-20: none under the strict trigger.

TP replay:

- Same 32 historical trigger rows.
- +1c entry / -1c exit stress hold ROI: +107.6%.
- TP20 full-sell ROI: +121.7%, delta +14.1pp, delta CI [-0.8pp, +36.6pp].
- TP30/TP40/recover-stake weaker or not significant.
- Verdict: do not promote TP as default; record TP paths in shadow.

Current verdict:

```text
metar_reversal_false_fade:
  conclusion=shadow_candidate
  live=false
  reason_not_live=thin rows, June decay, no fresh strict trigger, historical quote/fill feasibility unresolved
```

Main doubts:

- Strict `current_high_yes_ask >= 0.40` should be treated as a market-pricing state inside the runway idea, not as a new strategy direction. It may identify one-step runway where d1 YES fits; when current is already conceded, d1 YES may simply be the wrong expression.
- The open research question is whether conceded-runway states need a different expression (`d2 YES`, high-tail, basket, or avoid) rather than `d1 YES`.
- The sample may be dominated by a few May/early-June market structure cases.
- Same-denominator alternatives include d1 YES, current_bracket NO, d2 YES, high-tail YES, and current_high YES; expression selection may matter as much as signal.

## Prompt For Fabel

Please review the two strategy heads above as a skeptical quant/research reviewer. Your goal is to find ignored patterns, invalid assumptions, and the most likely real alpha, not to overfit a nicer ROI.

Use these constraints:

- Keep `forecast_tail_low_price_yes` and `metar_reversal_false_fade` separate unless the evidence shows one is only an expression selector for the other.
- Use same-denominator A/B wherever comparing expressions: same city, target date, decision hour/snapshot, and available sibling quotes.
- Distinguish alpha sources explicitly:
  - forecast/station-basis alpha,
  - intraday METAR/reheat alpha,
  - market/base-rate or price-band alpha,
  - city/date sample noise.
- Do not propose a live size-up unless significance, baseline, and forward gates all pass.
- Do not add a pile of gates just to improve in-sample ROI. Prefer continuous scores or clearly physical branches.
- Account for execution friction: maker fill probability, taker fallback cost, spread, best-ask size, queue risk, and TP exit fill probability.
- Treat 2026-07-01 as expression-matrix/telemetry available after corrected data-feed sync, but not canonical settled-PnL available until `settlement_outcomes` is complete.

Review questions:

1. For `forecast_tail_low_price_yes`, is the real edge in station-basis/model probability, low ask convexity, time-zone/attention effects, or just top-winner noise?
2. Does TP20 make first-principles sense, or does it just fit historical paths where tickets pumped then died?
3. Should the forecast-tail sleeve use a probability/EV score for size and TP behavior instead of fixed notional + fixed TP20?
4. For the runway d1 YES idea, does `current_high_yes_ask >= 0.40` correctly identify the one-step runway sub-state, or should market-current-state be a continuous feature?
5. Are `d1 YES` and `current_bracket NO` two expressions of the same false-fade signal, and when should one dominate the other?
6. Is there a missing branch for "market already killed current high, but still underprices d1/d2/tail skip-over"?
7. Does heat-death belong as a separate current_high YES branch, or is it just a weak diagnostic with no tradable alpha?
8. What forward telemetry should be recorded for the next 7-14 days to decide this cleanly?

Expected output:

```text
verdict:
  forecast_tail_low_price_yes: confirmed / shadow_candidate / inconclusive
  metar_reversal_false_fade: confirmed / shadow_candidate / inconclusive

best next experiment:
  one durable script or runner change
  exact denominator
  exact A/B comparisons
  exact forward metrics

promotion blockers:
  data coverage
  settlement coverage
  execution friction
  sample concentration
  hypothesis ambiguity
```
