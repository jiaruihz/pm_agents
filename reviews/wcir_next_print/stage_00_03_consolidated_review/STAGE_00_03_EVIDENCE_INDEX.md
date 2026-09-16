# Stage 0–3 Consolidated Evidence Index

## Start here

1. `GPT_PRO_CONSOLIDATED_REVIEW_AND_MODEL_TRAINING_CONSULTATION.md`
2. `authoritative/WCIR_NEXT_PRINT_CODEX_MASTER_PLAN_v1.md`
3. Per-stage review packets under `stage_packets/`
4. Full immutable stage archives under `stage_packages/`

## Stage packages

| Stage | Archive | Purpose |
|---|---|---|
| Stage 0 original | `stage_packages/stage_00_original.zip` | Initial repository/data/runtime boundary reviewed as `ACCEPT_WITH_BLOCKING_FIXES` |
| Stage 0 rev2 | `stage_packages/stage_00_rev2.zip` | Position-scope and immutable input closure |
| Stage 1 | `stage_packages/stage_01.zip` | Canonical event/clock/city contracts and legacy integration |
| Stage 2 | `stage_packages/stage_02.zip` | Deterministic book truth and executable sweep evidence |
| Stage 3 | `stage_packages/stage_03.zip` | Next-report dataset, lead/oracle/baselines and city decisions |

Every archive identity is frozen in `CONSOLIDATED_EVIDENCE_MANIFEST.json`. Stage 0 rev2 and Stages 1–3 also include their original external SHA-256 sidecars under `stage_sidecars/`.

## Plain-text key evidence

The combined zip duplicates key review-facing files outside the nested archives so GPT Pro can read them without extracting every package:

- `stage_packets/STAGE_00_REV2_PACKET.md`
- `stage_packets/STAGE_01_PACKET.md`
- `stage_packets/STAGE_02_PACKET.md`
- `stage_packets/STAGE_03_PACKET.md`
- `stage_decisions/STAGE_00_ORIGINAL_INDEPENDENT_REVIEW.md`
- `stage_decisions/STAGE_01_INDEPENDENT_REVIEW.md`
- `stage_decisions/STAGE_02_03_INDEPENDENT_REVIEW.md`
- `stage_decisions/STAGE_03_CITY_DECISION.md`
- `stage_decisions/STAGE_03_ORACLE_RESULTS.json`

## Integrity contract

The combined archive must contain exactly the manifest-declared entries plus `CONSOLIDATED_EVIDENCE_MANIFEST.json`. The external combined `.sha256` sidecar is deliberately outside the zip to avoid recursive hashing.

