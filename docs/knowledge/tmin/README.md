# Tmin Model-Layer Knowledge

Current stage boundary: **2026-08-30**. These documents describe research and
audit state only; `live_authorization` is false.

Current model action: keep V1 as the only operational forward incumbent and
freeze the specification and parameter artifact for
`TMIN_V2_2_FORECAST_THRESHOLD_RESIDUAL_V1`. Target date 2026-08-31 is the
earliest no-backfill start, but the zero-notional runtime consumer has not been
deployed (`PREREGISTERED_NOT_STARTED`). The fitted OOF evidence is
16 active-route rows across four dates; both proper-score point estimates improve
versus raw market, but both intervals cross zero. The fixed V1 selector replay is
1/1 for V2.2 versus 19/19 for the incumbent and is business context, not a model
selection metric. See the [fitted candidate report](../../analysis/2026-08/2026-08-30-tmin-v2-2-fitted-frozen-candidate-v1.md)
and package `reviews/tmin_v2_2_frozen_candidate_readout_v1_r5/`.

## Persisted source documents

- [ELI5 glossary](TMIN_MODEL_LAYER_ELI5_GLOSSARY.md)
- [V1/V2 plain-language teardown](TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN.md)
- [V2.1/V3 execution plan](TMIN_V2_1_V3_CODEX_EXECUTION_PLAN.md)

## Review evidence

- [V2.1/V3 completed strategy readout](../../../reviews/tmin_v2_1_v3_strategy_readout_v1/EXECUTIVE_STRATEGY_READOUT.md)
- [Full GPT Pro V2.1/V3 review packet](../../../reviews/tmin_v2_1_v3_strategy_readout_v1/GPT_PRO_V2_1_V3_FULL_REVIEW_PACKET.md)
- [Prior local GPT review packet](../../../reviews/tmin_model_layer_v2_v3_research_v1/GPT_PRO_REVIEW_PACKET.md)
- [Prior external review source-gap record](PRIOR_EXTERNAL_REVIEW_SOURCE_GAP.md)
  for expected source `TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md`.
- [Prior external audit source-gap record](PRIOR_EXTERNAL_AUDIT_SOURCE_GAP.json)
  for expected source `TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json`.

The two authoritative source files remain evidence-lineage blockers. The gap
records make the index resolvable without reconstructing the missing evidence
from summaries or representing the originals as present.
