# Codex Independent Review — GLM P0 Read-only Prep

Review stage: `REVIEW_ONLY`

Reviewed artifact: `GLM_P0_PREP_HANDOFF.md`

Review date: 2026-08-26 (UTC+8)

Reviewed repository commit: `7d41e252`

Implementation/network/runtime DB/order actions performed by this review: `NO`

## 1. Disposition

```text
ACCEPT_WITH_FIXES
```

GLM 的四张 fixture matrix 可以作为 P0-03、P0-04、P0-06B、P0-06C、P0-06D、P0-06E 和 P0-08 的输入 backlog。40 个 case 的计数正确，主要源码判断、测试结果和 SHA16 均可独立复现，没有发现平行 collector、wallet tracker、Rule Lawyer 或 orchestrator 提案。

本结论不批准联网、runtime DB migration、生产 capture 扩容或 order/signing。它也不授权在本轮直接实现 matrix 中的缺口。

需要修正的是任务映射和个别 finding 的合同解释，不需要退回重做四张矩阵。

## 2. Independent evidence verification

| 审核项 | 结果 | 证据 |
|---|---|---|
| 指定 11 个测试文件 | PASS | 独立重跑：`32 passed in 1.31s` |
| adjacent book 测试 | PASS | 独立重跑：`14 passed in 0.08s` |
| 报告列出的 12 个源码 SHA16 | PASS | 全部与当前工作树实际文件一致 |
| 矩阵计数 | PASS | Gamma `0/6/4`、Dispute `2/5/1`、Wallet `0/2/8`、Book `0/8/4`；合计 `2/21/17` |
| 原始 snapshot HEAD | PASS_WITH_PROVENANCE_LIMITATION | `e0ad0c6b` 是当前历史祖先；review 时 HEAD 已为 `7d41e252`，但所有被引用源码指纹一致 |
| “GLM 只新增 handoff 文件” | CONSISTENT_BUT_NOT_CRYPTOGRAPHICALLY_PROVABLE | GLM 开始时 worktree 已 dirty，缺少提交级 clean baseline；当前未发现 GLM code output，源码指纹与报告一致 |

复核命令：

```text
PYTHONPATH=. .venv/bin/pytest -q \
  tests/research_tests/test_gamma_normalize_models.py \
  tests/research_tests/test_gamma_pagination.py \
  tests/research_tests/polymarket/test_market_resolver.py \
  tests/research_tests/test_dispute_contract_corpus.py \
  tests/research_tests/test_dispute_canonical.py \
  tests/research_tests/test_dispute_artifacts.py \
  tests/research_tests/test_external_wallet_persist.py \
  tests/research_tests/test_external_wallet_history_collector.py \
  tests/research_tests/test_external_wallet_full_ladder_conversion.py \
  tests/test_platform_market_data_contracts.py \
  tests/research_tests/polymarket/test_clob_orderbook_sorting.py
→ 32 passed in 1.31s

PYTHONPATH=. .venv/bin/pytest -q tests/pmm_tests/test_weather_ws_incremental_book.py
→ 14 passed in 0.08s
```

## 3. Findings audit

| Finding | Review decision | Required correction / implementation consequence |
|---|---|---|
| F-01 dispute owned path 不存在 | ACCEPT | 真实 legacy source 在 `src/strategies/rule_lawyer/` 和 `scripts/analysis/dispute_repricing/`。未来 P0-06C prompt 必须使用真实 source path；正式实现 owner 仍是 `src/polymarket_alpha/recall/controversy*`，不得在 legacy 目录内另建 provider。 |
| F-02 Gamma duplicate full-page 可无限翻页 | ACCEPT，升级为 `BLOCKING_FOR_P0-03_ACCEPTANCE` | P0-03 必须加入 content fingerprint/offset progress guard 与 duplicate-page fixture。默认优先在 P0-03 adapter 边界 fail closed；若要修改共享 `src/platform/clients/gamma.py`，需由 coordinator 显式扩展 ownership。 |
| F-03 wallet 语义泄漏 | ACCEPT_WITH_CORRECTION | 风险真实，但不是“阻塞 P0-01 release”。P0-01 已有 allowlist-only `BlindCandidateProjection`、`EvidenceOrigin.WALLET` 拒绝、嵌套字符串扫描及现有 adversarial tests。剩余工作是 `BLOCKING_FOR_P0-08_ACCEPTANCE`：按 source origin/taint/lineage 整体排除 wallet-derived artifact，并用真实 nested wallet artifact 形状补 W09/W10。不得全局禁止 `name`、`price`、`outcome`、`conditionId` 等通用词或字段名，因为它们可能属于合法非 wallet 证据。 |
| F-04 superseded 无 source-of-truth | ACCEPT，升级为 `BLOCKING_FOR_P0-04_IMPLEMENTATION` | P0-03/P0-04 开工前必须冻结 Gamma source field 或显式、可审计的推导规则；不得用 slug/title 或 ingest-time 猜测 superseded。 |
| F-05 Gamma query 取首项且不核验 identity | ACCEPT，升级为 `BLOCKING_FOR_P0-03_ACCEPTANCE` | 增加 wrong-market-returned fixture；request/response market id、condition id 或受控 alias 必须匹配，否则 fail closed 并留 receipt。ownership 规则与 F-02 相同。 |

## 4. Required report corrections

### CR-01 — 补 P0-06B 的显式映射

GLM handoff 的 coverage summary 没有把 Gamma/Change cases 显式映射到 P0-06B，虽然 Codex handoff 要求包含 P0-06B。应补充：P0-06B 消费 P0-03 catalog 与 P0-04 change event，覆盖 new/changed/closed、rule/metadata/family/threshold structural recall、no-book operation、determinism 和 reason-code allowlist；它不得进行 fair-value 判断。

### CR-02 — 更新 route-isolation 状态说明

GLM snapshot 之后，P0-06A 已实现 generic provider isolation：missing/disabled/historical/expired provider 不阻断其他 provider，book provider 会得到 `BOOK_PROVIDER_SKIPPED`，pre-book hit 在没有 book 时仍能形成 Candidate。因此：

- D08：generic dispute-provider isolation 已关闭；dispute adapter 自身的 unavailable/error receipt 仍归 P0-06C。
- W07：generic wallet-provider isolation 已关闭；wallet adapter 自身的 unavailable/error receipt 仍归 P0-06D。
- B01/B11：generic no-book route isolation 已关闭；P0-06E 仍需 typed no-book/one-sided/stale output。

原矩阵作为 snapshot 可以保留原状态，但后续 task prompt 不得继续把上述 generic isolation 当作完全 MISSING。

### CR-03 — 修正 wallet hardening 方法

将 F-03 的“字段 deny 枚举”改为：

1. wallet-derived `source_artifact_id`、origin 或 lineage 进入 projection 前整体拒绝；
2. nested free text 继续递归扫描 wallet/whale/smart-money/direction hints；
3. 使用现有 `smart_wallets.json`、summary/report 的真实嵌套形状做 W09/W10；
4. 保留 allowlist-only projection，不把通用业务字段名加入全局 denylist。

### CR-04 — 标明 snapshot 双版本

原报告的 `e0ad0c6b` 是 GLM 只读扫描 snapshot，不是当前 review HEAD。供下一窗口使用时应同时保留：

```text
GLM source snapshot: e0ad0c6b
Codex reviewed commit: 7d41e252
Cited source fingerprints: unchanged and verified
```

## 5. Accepted case-to-task map

| Owner task | Accepted inputs from GLM matrix | Mandatory interpretation |
|---|---|---|
| P0-03 | G01–G04、G07–G10、F-02、F-05 | canonical identity、token pairing、pagination termination、field drift、dual clock、wrong-result fail closed |
| P0-04 | G05–G07、F-04 | semantic/non-semantic rule revisions、lifecycle change events、superseded source decision |
| P0-06B | G05、G07、G09、G10 及 P0-03/04 outputs | new/changed/closed、rule/metadata/family structural recall；无 book 也运行；只陈述 structural anomaly |
| P0-06C | D01–D08 | absolute source identity、PIT cutoff、precedence、dedupe、source-unavailable receipt |
| P0-06D | W01–W08 | freshness、Address != Entity、pagination coverage、recall-only、source-unavailable receipt |
| P0-06E | B01–B12 | paired/stale/one-sided/depth/crossed/family/determinism/semantic allowlist；不得输出 fair value/edge |
| P0-08 | W09–W10、F-03 corrected remedy | wallet source taint/lineage exclusion + real nested artifact adversarial fixtures |

## 6. Final handoff to the next reviewer

```text
GLM_PREP_ARTIFACT: ACCEPT_WITH_FIXES
MATRIX_REWORK_REQUIRED: NO
P0_01_CONTRACT_REOPEN_REQUIRED: NO
BLOCKING_FOR_P0_03_ACCEPTANCE: F-02, F-05
BLOCKING_FOR_P0_04_IMPLEMENTATION: F-04 source-of-truth decision
BLOCKING_FOR_P0_08_ACCEPTANCE: F-03 corrected wallet lineage fixtures
GENERIC_ROUTE_ISOLATION: ALREADY_CLOSED_BY_P0_06A
NETWORK_OR_PRODUCTION_AUTHORIZATION: NONE
```

下一窗口应基于本审阅的修正后 mapping 下发实现任务，不应再用 GLM 报告中的旧 snapshot 状态覆盖已经完成的 P0-01、P0-02、P0-06A 和 P0-11 evidence seals。
