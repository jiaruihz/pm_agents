# Regime-Routed Live Feature Parity Rerun v1

Status: snapshot
Generated: 2026-07-06
Scope: offline replay; no live order path touched

## Verdict

The existing `replay_regime_routed_no_live_feature_parity_v1.py` script was
rerun after the feature-layer import rewires. It does not fully close the live
runner rewire acceptance gate because the NYC as-of replay now cannot recover
historical AviationWeather records from the live API.

What passed:

- Historical selected-trade replay ran successfully.
- Main variant rows: 279.
- Live feature-parity pass rows: 275.
- Pass rate: 98.57%.
- Missing core field count is only `minutes_since_running_max=4`.
- Unknown label count is only `running_max_state=4`.

What did not close:

- NYC 2026-06-25 as-of replay returned `live_feature_status=no_asof_records`.
- That means this rerun cannot prove the old NYC live order moment still
  reconstructs as `active_warming` / `fresh_running_high` under the current
  external fetch path.

## Inputs And Outputs

Command:

```bash
.venv/bin/python scripts/analysis/reheat_risk/replay_regime_routed_no_live_feature_parity_v1.py
```

Outputs updated by the script:

- `docs/analysis/2026-06/generated/regime_routed_no_live_feature_parity_v1/summary.json`
- `docs/analysis/2026-06/generated/regime_routed_no_live_feature_parity_v1/historical_parity_summary.csv`
- `docs/analysis/2026-06/generated/regime_routed_no_live_feature_parity_v1/nyc_live_order_parity_replay.csv`
- `docs/analysis/2026-06/2026-06-25-regime-routed-no-live-feature-parity-v1.md`

## Interpretation

This rerun is useful as a current import/boundary smoke replay: the live runner
imports cleanly after moving `temperature_context_multiplier()` into strategy
space, and historical parity gate coverage remains high.

It is not enough to mark the live/shadow route-decision replay acceptance gate
fully closed. To close that gate, rerun against archived PIT observation records
or shadow journal rows that do not depend on the current live API retaining old
as-of METAR payloads.
