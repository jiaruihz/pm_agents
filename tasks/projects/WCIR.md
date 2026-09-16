# WCIR

PROJECT_ID: WCIR
TITLE: Weather City Intraday Reaction / next-print research
REPO_ROOT: /Users/deepsleep/projects/pm_agents
SOURCE_STATE_BASE_COMMIT: 673767a1
PROTOCOL_INSTALL_COMMIT: 0f18eb409466b1af13cb44690fa06b004ec63197
LATEST_RELEVANT_COMMIT: 10ebb309
LAST_RECONCILED_AT: 2026-08-29T17:44:12+08:00
ACTIVE_TASKS: Amsterdam prospective score-only evidence collection; continuous owner registration remains blocking

## Current state

CURRENT_DISPOSITION: ACCEPT_WITH_BLOCKING_FIXES
CURRENT_CANDIDATE_STAGE: Amsterdam pilot v1.1 package complete; frozen three-arm journal initialized, not continuously scheduled
CURRENT_ACCEPTED_STAGE: UNKNOWN_REQUIRES_OWNER_CONFIRMATION
LAST_OWNER_DECISION: UNKNOWN_REQUIRES_OWNER_CONFIRMATION

Amsterdam pilot v1.1 已关闭 source/label contract、FeatureBuilderV2 parity、P0/P1/P2 同分母评估和历史 market archive reconciliation。captured 75/87 具完整 path；B2 是 P2 primary reference，M1/M2 仅 challengers。Tier-A entry=0，Tier-A+B entry=36，但 markout 仅2 rows/1 date，Layer B 不可估。三臂 append-only journal 已初始化，未注册 continuous owner；未把候选状态晋升成 accepted 或 live。

## Capability boundary

- NEXT_ALLOWED_ACTION: 为冻结 Amsterdam scorer 登记隔离的 continuous zero-notional owner，并积累 prospective next-print/markout evidence；不热调。
- EXPLICITLY_NOT_AUTHORIZED: Helsinki 复制、model/selector/threshold 热调、position、execution、production order path 或 live 变更。

## Canonical pointers

- `reviews/wcir_next_print/stage_03/GPT_PRO_REVIEW_PACKET_STAGE_03.md`
- `reviews/wcir_next_print/STAGE_02_03_INDEPENDENT_REVIEW_EVIDENCE.md`
- `reviews/wcir_unified_data_amsterdam_pilot_v1_1/GPT_PRO_REVIEW_PACKET_AMSTERDAM_V1_1.md`
- `reviews/wcir_unified_data_amsterdam_pilot_v1_1/EVIDENCE_MANIFEST.json`
- relevant commit `10ebb309`
