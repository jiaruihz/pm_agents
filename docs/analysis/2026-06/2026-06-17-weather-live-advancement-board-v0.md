# 2026-06-17 Weather Live Advancement Board v0

Status: current working summary / no new live action
Generated: 2026-06-17

## Data Self-Check

This board summarizes completed or active research threads. It is not a new PnL backtest and does not publish new `live_real` ROI.

Mandatory DB self-check snapshot:

```text
MAX(fact_built_at_utc): 2026-06-16T15:50:05.971834+00:00
fact_trades by class: live_real=855, live_simulated=624, paper=2285, snapshot_replay=636
fact_trades settlement: settled=4310, blank/unsettled=90
fact_signal_candidates: rows=30919, eligible=10685, paper_ordered=4123, live_filled=348
CLOB orders/fills: error=33/0 fills, submitted=961/855 fills
```

## Human Summary

The cleanest path toward live is still current-YES no-reheat, but the live form must be the late/fade-confirmed version with hard observation-clock guards. The Helsinki accident did not kill the thesis; it showed that a high model probability is unsafe when the next METAR update is imminent, the city timezone is wrong, or the running max is only one degree below the next bracket.

The shared reheat feature factory is now the center of the research stack. New strategy heads should consume it instead of each rebuilding observed max, orderbook quotes, and settlement labels. The biggest remaining data gap is forecast peak timing: the columns exist, but coverage is currently 0% in the DB snapshot used by the factory.

NO carry is not a separate live direction yet. It remains an expression-layer sibling of current YES. d1/d2/ladder have some structural convexity, but paired evidence has not shown they beat current YES.

Low-price YES reheat reversal is interesting, but it is a convexity sleeve, not the same thing as current YES. It should be shadowed with a separate `target_yes_wins` model head before any real money.

The METAR-cross previous-NO idea is a separate latency-arb branch. It could become live later, but only after a shadow bot proves source alignment, event rules parsing, quote availability, and false-positive handling.

## Stage Map

| Direction | Current Stage | Evidence | Live Implication |
|---|---|---|---|
| `theta_current_yes_tiny_live_v1` fade-confirmed | tiny-live guarded / do not expand | v9 was live-ready before incident; guard hotfix now deployed; factory-backed peak/fade favors fade | Keep tiny only; add telemetry and model calibration before size/city expansion |
| `current_yes_peak_forming` | shadow only | factory-backed fixed holdout peak ROI -7.1%; prefix CI crosses 0 | Do not live; only record early quotes as zero-notional telemetry |
| `higher_no_carry` d1/d2/ladder | research/shadow | paired holdout excess vs current YES crosses 0 | Do not replace current YES; no live queue |
| `low_price_yes_reheat_reversal` | shadow candidate | reheat-adjusted holdout improves hit/ROI, but no bootstrap/walk-forward live gate yet | Build shadow journal; no live |
| `metar_cross_prev_no` latency bot | shadow prototype active in separate thread | thesis is source-latency, not forecast alpha; source alignment unresolved for several cities | Shadow first; live only after station/rules whitelist and fill-rate evidence |
| `range_rv_shadow_v0` | already shadow / wait | forward settled sample still thin | Keep collecting; not current live priority |

## Research Threads Read

### A. Reheat Feature Factory

Thread: `Build reheat-risk feature factory`

Result:

- Materialized `88,621` feature rows and `8,696` date/city/hour states.
- Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome`.
- Usable: current temp, running max, decline, minutes since max, dewpoint/RH/wind/temp trend, current YES, d1/d2 NO, target YES, settlement label.
- Blocking gap: `forecast_peak_hour_local`, `forecast_peak_delta_hours_local`, `forecast_values_hash` are 0% populated.

Decision:

Use this as the shared feature base for current YES, NO carry, and low-price YES reversal. Do not let new strategy heads build private observed-max/orderbook/settlement layers.

### B. Peak-Forming vs Fade-Confirmed Current YES

Thread: `Compare peak vs fade current YES`, plus active factory-backed migration in `Build reheat-risk feature factory`.

Latest factory-backed conclusion:

- Peak-forming fixed holdout: ROI -7.1%, win 72.0%, avg ask 0.755.
- Fade-confirmed fixed holdout: ROI +10.6%, win 90.3%, avg ask 0.797.
- Prefix walk-forward fade: ROI +11.0%, CI lower bound approximately 0.
- Prefix walk-forward peak: ROI +3.4%, CI crosses 0.

Decision:

Late/fade-confirmed remains the main current-YES timing head. Peak-forming is not live-worthy. The next live-adjacent work is execution freshness and calibration, not another threshold search.

### C. Higher NO Carry Expression

Thread: `分析 NO Carry 表达选择`

Paired holdout excess ROI versus sibling current YES:

```text
d1 NO:     +0.4%, CI [-3.4%, +4.3%]
d2 NO:     +5.4%, CI [-6.1%, +16.4%]
NO ladder: +5.1%, CI [-7.2%, +16.7%]
```

Decision:

NO carry is not an independent live alpha. d2/ladder have research flavor, but sample uncertainty is too large. Default expression remains current YES when its own gate passes.

### D. Low-Price YES Reheat Reversal

Thread: `迁移-低价BUY_YES彩票研究`

Current split:

- Old low-price YES edge rule `edge >= 0.20` is a convexity pattern, not current-YES.
- Reheat-conditioned v0 uses `target_yes_wins`, not `current_yes_wins`.
- Intraday low-price target YES rows with prior: `4347`.
- Holdout all low-price reheat YES: ROI proxy negative.
- Holdout `reheat_adjusted_edge > 0`: hit 7.4%, ROI proxy +9.5%.
- Holdout top decile: hit 12.1%, ROI proxy +19.4%.

Decision:

This is worth shadowing as a separate convexity sleeve, but it needs event-date bootstrap, top-k stress, walk-forward frozen rules, and a factory-native model head before tiny-live.

### E. METAR-Cross Previous-NO Latency Bot

Thread: `抢单子bot`

Current status:

- Target metric: `metar_cross_prev_no_taker`.
- This is source-latency arbitrage: when an official or rules-aligned observation first crosses from T-1 to T, buy T-1 NO if stale quote remains.
- Shadow script prototype is being added: `scripts/ops/weather_metar_cross_prev_no_shadow.py`.
- Seoul is blocked due unresolved settlement/source mismatch; Shanghai/Tokyo-like source-aligned cities are better candidates, but rules parsing and settlement-source validation must be strict.

Decision:

Do not live immediately. This branch needs a source whitelist, no-duplicate crossing state, fresh CLOB quote capture, and a zero-notional opportunity ledger first. It is promising because risk is mostly "no fill" only if source alignment is correct; if source alignment is wrong, it becomes a fast wrong-order machine.

## Current Live Path

The current live path should be:

```text
guarded theta_current_yes_tiny_live_v1
  -> observation clock guard
  -> source/timezone correctness
  -> fade-confirmed only
  -> fresh-book ask guard
  -> $5/order, $10/city-day cap
  -> no expansion until forward telemetry confirms fills and no-reheat calibration
```

Current guard work already deployed:

- IANA timezone / DST mapping.
- `max_obs_age_min=20`.
- `pre_metar_update_blackout_min=6`.
- `min_gap_to_next_bracket_c=1`.
- Shared observation clock module for reuse.

## Next Work To Reach Live Expansion

1. Current-YES execution freshness board:
   - Join every accepted plan to fresh CLOB ask, snapshot age, obs age, minutes to next obs, and eventual fill/cancel.
   - Output expected profit at limit and at fresh ask.
   - Decide whether taker crossing is allowed only when `p_yes_win - fresh_ask` remains positive after cushion.

2. Current-YES model calibration after Helsinki:
   - Calibration by price bucket, city, local hour, obs age, minutes-to-next-METAR, and gap to next bracket.
   - Stress `yes_current_ask` coefficient and city prior.
   - Compare model with and without quote price as feature in the 90-98% probability bucket.

3. Low-price YES reheat reversal shadow:
   - Convert v0 into a daily zero-notional ledger.
   - Separate raw model edge, blended edge, and reheat-adjusted edge.
   - Cap one candidate per city-date and record final settlement.

4. METAR-cross shadow bot:
   - Finish tests and dry-run.
   - Whitelist only source-aligned cities.
   - Write cycle/opportunity ledger with quote freshness and no duplicate state.
   - After enough rows, evaluate fillable stale-quote rate and source false positives.

5. Forecast peak upstream population:
   - Populate `forecast_peak_hour_local`, `forecast_peak_delta_hours_local`, and `forecast_values_hash`.
   - Re-run peak/fade and forecast-peak-clock research on the shared factory.

## Current Verdict

Nothing new should be upgraded to larger live size today.

The closest live expansion candidate is still fade-confirmed current YES, but the next gate is not another backtest. It is live/shadow telemetry around observation timing, fresh quotes, and calibration after the Helsinki failure.

Low-price YES and METAR-cross should run as shadow ledgers. NO carry stays research-only unless paired evidence starts beating current YES.
