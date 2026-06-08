# Blender Shadow

> Living doc for module [1]: whether blend / edge-engine fields are useful as shadow signals, sizing inputs, or future live gates.
> Current status: `shadow_candidate`; not approved as a live hard gate.
> Last updated: 2026-06-09.

## Current Conclusion

The current edge-engine direction is intentionally split: probability blend fields should be wired into lineage and shadow/paper first, while city-day basket research remains separate until robustness validation passes. Existing evidence does not support using blender as a live hard gate.

This document tracks blender as a shadow or sizing signal. Live behavior changes still require `weather-strategy-deploy` flow and the gates in this document.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md` | current handoff | stable edge-engine handoff and split-track direction | current-source |
| `docs/analysis/2026-06/2026-06-08-blender-research-state-and-next-plan.md` | 2026-06 | blender research state and next plan | snapshot |
| `docs/analysis/2026-06/2026-06-08-blender-signal-value-research.md` | 2026-06 | hard-gate and sizing signal value | snapshot |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-shadow-lineage.md` | 2026-06 | v2 shadow lineage | snapshot |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-filtered-operational-base-research.md` | 2026-06 | filtered operational base research | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Lineage | Shadow fields present in signal/plan/order lineage with stable strategy_instance attribution |
| Baseline | Excess over current live base and market-price baseline |
| Forward | Date-forward holdout; no selecting rule on the same window used for promotion |
| Safety | Deployment through `weather-strategy-deploy`; no direct scp/rsync production change |

## Open Work

1. Keep probability blend as measured shadow data until action gates pass.
2. Do not merge blender and city-day basket conclusions into one live proposal.
