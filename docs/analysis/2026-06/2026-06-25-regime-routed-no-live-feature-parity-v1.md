# Regime-Routed NO Live Feature Parity V1

Generated: `2026-06-26T07:20:30+00:00`

## Verdict

- This is a parity/incident replay, not a live approval.
- Historical selected rows mostly survive the new live feature-parity gate; this says the live feature gate is not cutting the sample to zero.
- The 2026-06-25 NYC order would still have passed the feature and sizing gates if there had been no prior duplicate order; with the new duplicate gate, the second same-token order is blocked.
- Live remains blocked until a broader point-in-time replay and deploy review pass.

## Historical Main Variant

| slice | rows | dates | cities | cost_usd | pnl_usd | roi | hit_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| main_before_parity_gate | 271 | 35 | 35 | $+1,355.00 | $+155.73 | +11.5% | +51.7% |
| main_after_parity_gate | 264 | 35 | 35 | $+1,320.00 | $+124.94 | +9.5% | +51.5% |
| main_failed_parity_gate | 7 | 7 | 7 | $+35.00 | $+30.80 | +88.0% | +57.1% |

## NYC As-Of Replay

| label | asof_utc | bracket | ask | day_regime | intraday_state | moisture_cloud_regime | wind_regime | running_max_state | soft_balanced | soft_shares | live_feature_parity_ok | would_execute_without_prior_duplicate | would_execute_with_existing_duplicate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_live_order | 2026-06-25T15:54:21+00:00 | 82-83 | 0.350 | day_marginal_runway | active_warming | cloud_suppression | light_wind | fresh_running_high | 1.000 | 14.286 | True | True | False |

## Boundary

This replay uses archived selected-trade rows for historical performance and live-style AviationWeather METAR reconstruction for the NYC as-of cases. It does not restore live.
