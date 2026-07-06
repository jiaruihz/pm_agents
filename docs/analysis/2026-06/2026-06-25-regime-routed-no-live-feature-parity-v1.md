# Regime-Routed NO Live Feature Parity V1

Generated: `2026-07-06T14:53:24+00:00`

## Verdict

- This is a parity/incident replay, not a live approval.
- Historical selected rows mostly survive the new live feature-parity gate; this says the live feature gate is not cutting the sample to zero.
- The 2026-06-25 NYC order would still have passed the feature and sizing gates if there had been no prior duplicate order; with the new duplicate gate, the second same-token order is blocked.
- Live remains blocked until a broader point-in-time replay and deploy review pass.

## Historical Main Variant

| slice | rows | dates | cities | cost_usd | pnl_usd | roi | hit_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| main_before_parity_gate | 279 | 38 | 35 | $+1,395.00 | $-8.69 | -0.6% | +48.4% |
| main_after_parity_gate | 275 | 38 | 35 | $+1,375.00 | $-37.82 | -2.8% | +48.0% |
| main_failed_parity_gate | 4 | 4 | 4 | $+20.00 | $+29.13 | +145.6% | +75.0% |

## NYC As-Of Replay

| label | asof_utc | bracket | ask | day_regime | intraday_state | moisture_cloud_regime | wind_regime | running_max_state | soft_balanced | soft_shares | live_feature_parity_ok | would_execute_without_prior_duplicate | would_execute_with_existing_duplicate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_live_order | 2026-06-25T15:54:21+00:00 | None | None | None | None | None | None | None | None | None | None | None | None |

## Boundary

This replay uses archived selected-trade rows for historical performance and live-style AviationWeather METAR reconstruction for the NYC as-of cases. It does not restore live.
