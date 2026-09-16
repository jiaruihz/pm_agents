# Blender Shadow

> Living doc for module [1]: whether blend / edge-engine fields are useful as shadow signals, sizing inputs, or future live gates.
> Current status: `shadow_candidate`; not approved as a live hard gate.
> Last updated: 2026-08-13 blender evidence consolidation.

## Current Conclusion

The current edge-engine direction is intentionally split: probability blend fields should be wired into lineage and shadow/paper first, while city-day basket research remains separate until robustness validation passes. Existing evidence does **not** support using blender as a live hard gate.

Blender's current use is narrower:

1. **Lineage field**: record `raw / market / blended` probabilities, edge, disagreement, and candidate size multiplier on every shadow/paper decision.
2. **Risk / size signal**: test whether low or negative blended edge consistently identifies avoidable loss without sacrificing too much winner PnL.
3. **Drift alert**: flag raw-vs-market disagreement as a model-health signal.

It should not be used as a standalone alpha claim. The strongest recent evidence says city/time controls explained more of the June degradation than a global blended threshold, and once those controls are applied, blender hard filters become negative marginal PnL.

Live behavior changes still require `weather-strategy-deploy` flow and the gates in this document.

## Consolidated Quantitative Evidence

| Check | Fixed denominator | Result | Decision |
|---|---|---|---|
| Live-fill overlay | 707 settled v1 fills, 2026-05-16..06-05 | gate delta full `+$18.93`, pre-06-01 `-$67.63`, post-06-01 `+$86.56` | unstable drift filter |
| City/time controlled overlay | same 707 fills; remove six weak cities and require `T<=28` | post base improved from `-$89.06` to `+$39.53`; adding `blended_edge>=0.10` reduced it to `+$20.19` | city/time explains more than blender |
| Strict operational-base signal value | 308 settled fills, `22<=T<=28` | base PnL `+$167.35`; hard gate delta `-$115.28`; gentle size curve delta `-$67.65`; walk-forward delta `-$16.40` | no hard gate or sizing promotion |

Exact legacy JSON results were content-address archived under manifest
`/Volumes/jrs-archive/pm_agents/research/artifact_store/manifests/basket_blender_legacy_cleanup_20260813.json`.
The dated Markdown renderings were removed after consolidation; git history preserves their narrative audit trail.

## Absorbed Historical Claims

1. Early 2026-06-06 blended opportunity backtests are superseded because they predate the near-binary correction and current fact-table gate. They are useful only as search history.
2. The 2026-06-07 live-fill overlay showed blender helped recent June fills but hurt pre-June fills. It is a recent-drift filter, not a stable all-window hard gate.
3. The 2026-06-08 "remove six ECMWF cities + T28 ban" overlay is the key current control: city/time filtering moved the post-June v1 slice from negative to positive, while adding `blended_edge>=0.10` on top reduced PnL.
4. The 2026-06-08 signal-value research confirms hard gates reduce trade count and can improve ROI optics while lowering net PnL. Size curves and `blended_edge<0` risk detection remain research candidates, but walk-forward selection did not pass live hard-gate standards.
5. Weather edge v2 basket/shadow reports belong to the edge-engine research track, not to blender hard-gate approval. They can share lineage fields, but basket rules need their own forward, top5-removed, capacity, and execution checks.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md` | current handoff | stable edge-engine handoff and split-track direction | current-source |
| JRS manifest `basket_blender_legacy_cleanup_20260813` | 2026-05-16..06-06 | exact overlay, signal-value and walk-forward machine results | archived-machine-evidence |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-shadow-lineage.md` | 2026-06 | v2 shadow lineage | snapshot-evidence |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-filtered-operational-base-research.md` | 2026-06 | filtered operational base research | snapshot-evidence |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Lineage | Shadow fields present in signal/plan/order lineage with stable strategy_instance attribution |
| Baseline | Excess over current live base and market-price baseline |
| Forward | Date-forward holdout; no selecting rule on the same window used for promotion |
| Net PnL | Report avoided loss and missed profit; do not promote a rule that only improves ROI by deleting profitable volume |
| Separation | Keep blender sizing/risk claims separate from city/time controls and basket rules |
| Safety | Deployment through `weather-strategy-deploy`; no direct scp/rsync production change |

## Open Work

1. Keep probability blend as measured shadow data until action gates pass.
2. Do not merge blender and city-day basket conclusions into one live proposal.
3. Standardize shadow output fields for `raw_p_yes`, `market_p_yes`, `blended_p_yes`, `blended_edge`, `raw_market_disagreement`, and `size_multiplier_candidate`.
4. Re-run on future settled windows and evaluate daily median delta, top-winner dependence, avoided loss, missed profit, and actual order/fill feasibility.

The former dated blender reports were removed after their distinct denominators and durable conclusions were absorbed above. Future runs update this living doc and write one machine format to JRS; they do not recreate parallel dated JSON/Markdown reports.
