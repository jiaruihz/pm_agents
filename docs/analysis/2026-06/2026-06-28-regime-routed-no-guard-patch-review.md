# Regime-Routed NO Date-Lineage And Route-Validity Review

## Data Snapshot

- Generated UTC: `2026-06-28T16:11:36+00:00`.
- Historical research file: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv`.
- Historical range: `2026-05-20`..`2026-06-23`; dates=35; cities=35; rows=271.
- Live runtime audit source: `runtime/weather_edge_v1/remote_pm_agent/regime_routed_no_tiny_live_v1`; recent window starts `2026-06-27`.

## Code Review Result

- Confirmed bug: raw snapshot market rows are internally date-consistent, but the live runner grouped rows by city only, so a city with today and tomorrow markets in one snapshot could build a candidate using today's observation state and a tomorrow orderbook row.
- Recent raw snapshot sanity: checked `30` snapshots / `3617` rows; raw row internal date mismatches=`0`, but city-level multi-target groups=`173` across `5` snapshots.
- Confirmed strategy bug: `day_open_runway/day_marginal_runway` are day-level forecast-space labels and can be stale at the current observation level. The current-NO route definition is now fixed so stale/fade states do not produce `runway_current_no` candidates.
- Confirmed lineage gap: historical executor `live_orders.jsonl` rows did not preserve `route_leg/expression`, so filled-order guard counterfactuals must be reconstructed from candidate/plan logs. New plans should carry those route fields.
- Operational patch: the shell loop now logs non-zero runner exit codes instead of swallowing them with bare `|| true`. That is telemetry hardening, not an alpha change.

## Historical A/B

| slice | rows | dates | cities | wins | win_rate | exec_roi | weighted_roi | weighted_roi_95ci |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| historical_main_before_guard | 271 | 35 | 35 | 140 | +51.7% | +11.5% | +26.3% | [+3.9%, +47.7%] |
| historical_main_after_date_lineage_patch | 271 | 35 | 35 | 140 | +51.7% | +11.5% | +26.3% | [+3.9%, +47.7%] |
| historical_main_after_current_no_route_validity_fix | 193 | 35 | 34 | 102 | +52.8% | +6.6% | +22.3% | [-2.1%, +49.1%] |
| historical_removed_by_current_no_route_validity_fix | 78 | 34 | 29 | 38 | +48.7% | +23.7% | +34.5% | [-5.0%, +71.2%] |

## Interpretation

- Correct date-lineage-only conclusion: historical backtest numbers do not change. The historical selected file is already city/date/hour normalized, so the live city-only grouping bug is not represented as a historical PnL row to remove.
- Correct route-validity conclusion: fixing the current-NO route definition changes the historical expression. It removes stale/fade current-NO rows and lowers the historical point estimate because some of those invalid-shape rows happened to win in this short sample.
- The market-date lineage fix has no meaningful historical A/B on this file because the file is already normalized to city/date/hour; its evidence is the live runtime audit of candidate-level row mixing, not broken raw market rows.
- Combined conclusion: date-lineage fix improves live candidate safety but does not change historical PnL; route-validity fix changes the strategy expression and makes historical evidence weaker, not stronger.

## Live Runtime Audit

- Recent unique runtime candidates since 2026-06-27: `133`.
- Candidate-level market-date mismatches caused by city-only row mixing that the patch would block: `83`; by city: `{'Wellington': 1, 'Beijing': 6, 'Busan': 2, 'Chengdu': 5, 'Chongqing': 6, 'Singapore': 5, 'Wuhan': 1, 'Taipei': 1, 'Lucknow': 4, 'Manila': 2, 'Tokyo': 1, 'Shanghai': 3, 'Karachi': 4, 'Ankara': 3, 'Jeddah': 2, 'Amsterdam': 3, 'CapeTown': 2, 'Munich': 1, 'Helsinki': 1, 'Madrid': 2, 'BuenosAires': 2, 'SaoPaulo': 1, 'Miami': 2, 'NYC': 2, 'Atlanta': 3, 'Houston': 2, 'Denver': 5, 'LA': 3, 'Dallas': 2, 'Seattle': 3, 'Austin': 2, 'Guangzhou': 1}`.
- Current-NO route-invalid runtime candidates that the route fix would not produce: `17`; by city: `{'Singapore': 2, 'Lucknow': 1, 'Tokyo': 1, 'Shanghai': 1, 'CapeTown': 1, 'Amsterdam': 1, 'BuenosAires': 1, 'NYC': 3, 'Houston': 1, 'Dallas': 1, 'Denver': 1, 'Seattle': 2, 'Austin': 1}`.
