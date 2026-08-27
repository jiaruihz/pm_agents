# Current Implemented Flow

## 已实现主链

```text
captured Gamma bytes / controlled read-only Gamma proxy
  -> Catalog + immutable raw/revisions + canonical market identity
  -> Change Events
  -> Pre-book Recall routes
       - new/changed
       - structural metadata/market-family
       - controversy/dispute
       - specialist wallet (recall-only)
     Optional side route:
       - existing-owner paired book -> structural book anomaly recall
  -> RecallHit aggregation / dedupe / Candidate lifecycle
  -> Rule A using shared RuleContractCompiler
  -> BlindCandidateProjection / Blind packet
  -> immutable Blind brief / external draft / importer
  -> accepted Blind result
  -> fresh FORMAL_REVIEW paired book demand/receipt
  -> Market packet / external draft / importer
  -> Rule B using exact same rule hash
  -> rank / ReviewDecision / PredictionRecord
  -> NO_ORDER Decision Ledger
  -> caller-supplied authoritative resolution intake
  -> scoring / calibration / backfill
```

P1 还实现了 provider-neutral research job、attempt、work order、return receipt、
lease/retry/quarantine、immutable ArtifactStore、orchestrator 和 synthetic scheduler。
Alpha 自身仍没有 provider client 或浏览器。

## 已新增但尚未接入 canonical 主链的 GLM triage

```text
raw selected markets
  -> allowlist-only SemanticTriageProjection
  -> external sidecar: haiku alias -> glm-4.7
  -> strict JSON result
  -> deterministic importer
       - exact item coverage
       - recursive forbidden semantic scan
       - private market-id rebind
       - deadline eligibility override
       - append-only receipt and decisions
  -> ADVANCE / REVIEW / DEFER (never terminal model rejection)
```

真实 3-market pilot：

- provider model：`glm-4.7`；
- provider 原判：3 `ADVANCE`；
- deterministic effective result：2 `ADVANCE`、1 elapsed `DEFER`；
- provider web/tool use：0；
- execution：`NO_ORDER`；
- 完整 Alpha regression：655 passed；
- commit：`7bf760ce`。

当前 GLM triage 是 additive seam，还没有决定应插在 Candidate aggregation 后、Rule A
前的哪个 canonical transition，也没有实现 GLM-5.3 reviewer。

## 50-market controlled E2E 已证明什么

- 50 个真实 active/open market 进入 catalog；
- 五个 recall provider 都被调用；
- 52 个 RecallHit 聚合成 50 个 Candidate；
- 11 个 paired weather market 获取 existing-owner books；
- 一个目标市场完成 Rule A → Blind fixture → formal book → Market fixture → Rule B
  → Prediction/Decision；
- 最终 `SIMULATE / NO / NO_ORDER`。

但 Blind/Market research 使用 deterministic fixture，只证明 orchestration、隔离、
hash binding 和状态机，没有证明真实联网研究质量。

## 当前真实 operating seams

1. Gamma 和 books 有受控只读 pilot 证据，但还不是每日 scanner；
2. Blind/Market research 可人工或外部 provider 执行，但真实 GPT Pro 浏览器研究
   尚未跑 pilot；
3. GLM triage 已真实运行，但尚未进入 Candidate canonical lifecycle；
4. GLM-5.3 critic、coordinator synthesis、ResearchQuestionCompiler、SourcePlan 和
   human approval receipt 尚未冻结；
5. live order/signing 永远不在当前范围。

## 已有核心合同，可复用而非重建

- `CandidateCard`, `CandidateTransition`, invalidation/refresh events；
- `RuleContract`, `RuleGate`, `RuleGateB`, shared compiler；
- `BlindCandidateProjection`, `BlindResearchQuestion`, `BlindResearchPacket`；
- `MarketResearchPacket`, `ResearchResultEnvelope`, `ClaimEvidence`,
  `SourceArtifact`, `ProbabilityEstimate`；
- `ResearchJob`, `ResearchAttempt`, `ResearchWorkOrder`, return/import receipts；
- `BookCaptureDemand/Receipt`, `OrderbookSnapshot`；
- `ReviewDecision`, `PredictionRecord`, resolution/scoring/calibration contracts；
- public immutable `ArtifactStore` and `AlphaRepository`.

新设计应优先增加 adapter/local contract，不得建立平行 research orchestration。
