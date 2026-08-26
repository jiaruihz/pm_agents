# P0 Work Allocation — GLM / Codex

## 当前状态

```text
DESIGN_APPROVED
IMPLEMENTATION_ACTIVE
P0_COMPLETE=NO
readiness_scope=OFFLINE_IMPLEMENTATION_ONLY
```

2026-08-27后的实际完成状态、剩余DAG和可直接执行的任务合同以
`P0_REMAINING_WORK_BREAKDOWN.md`为准。本文件下方保留最初分工基线，不能
再把其中的`BLOCKED_BY_DEPENDENCY`文字当作当前运行状态。

## 分工原则

- Codex保留所有shared contract、migration、状态机、安全边界和最终跨模块集成owner。
- GLM负责输入输出明确、可用fixture独立验证、不会改变shared schema的adapter/provider窄包。
- GLM不得修改`src/polymarket_alpha/contracts/**`、migration、Candidate核心状态机、Rule A/B核心、Blind projection合同、security policy或production config。
- GLM任务必须通过公开repository/contract接口接入；接口不足时提交change request，不自行扩展shared schema。
- 所有任务均为offline implementation；不得联网、写current DB、改production service或触及order/signing/private key。

## 推荐 ownership

| Work package | 推荐 owner | 原因 | 前置依赖 |
|---|---|---|---|
| P0-01 Shared Contracts | Codex | canonical ID、BlindResearchQuestion、claim evidence、兼容策略是全系统冻结点 | design approved |
| P0-11 Wave-0 policy/CI/adversarial scaffold | Codex | capability boundary和contracts需同步裁决 | policy可立即；concrete transport等待P0-01 |
| P0-02 Alpha Storage/Migrations | Codex | 唯一migration owner，涉及legacy research.db兼容 | P0-01 |
| P0-03 Gamma Catalog Adapter | GLM | 现有client/fixtures清楚，适合有界adapter实现 | P0-01,P0-02,P0-11 subset |
| P0-04 Change Detector | GLM | pure revision-diff，可完全fixture验证 | P0-03 |
| P0-05 Book Demand/Adapter | Codex | 接现有production capture owner，需严格区分SENSING/FORMAL_REVIEW | P0-01,P0-02,P0-04 |
| P0-06A Recall Aggregator | Codex | Candidate merge/refresh与状态失效边界 | P0-01,P0-02 |
| P0-06B Pre-book Structural Recall | GLM | pure provider，无book/fair-value依赖 | P0-03,P0-04,P0-06A |
| P0-06C Controversy Recall | GLM | read-only dispute adapter，source identity明确 | P0-03,P0-06A |
| P0-06D Wallet Recall | GLM | read-only wallet adapter，freshness/privacy规则明确 | P0-03,P0-06A |
| P0-06E Book Anomaly Recall | GLM | 只消费normalized book的pure provider | P0-05,P0-06A |
| P0-07 RuleContractCompiler A/B | Codex | rule hash、PIT corpus、A/B invariant跨域判断 | P0-01,P0-02,P0-03 |
| P0-08 Blind/Market Roundtrip | Codex | semantic isolation、source hash、人工结果导入是核心研究边界 | P0-01,P0-05,P0-06A,P0-07 |
| P0-09 Decision Ledger | Codex | append-only invalidation/rank/prediction状态机 | P0-02,P0-06A,P0-07,P0-08 |
| P0-10 Harness/Evidence Adapter | GLM | 现有harness接口清楚，adapter边界独立 | P0-01,P0-09 |
| P0-11 Final Capability Proof | Codex | 必须独立复核所有adapter旁路 | P0-02,P0-09及全部merge subsets |
| P0-12 Offline Integration | Codex | 唯一最终coordinator和evidence signer | P0-03～P0-11全部完成 |

## 可以现在先交给 GLM 的只读准备包

这些任务不写实现代码，不依赖P0-01冻结，可先准备fixture/field matrix。产物返回后由Codex审阅，再决定是否纳入正式实现任务。

### GLM-PREP-01 — Gamma fixture matrix

```text
Objective:
核对现有Gamma fixtures和models，形成event/market/condition/token/outcome/rule/lifecycle字段矩阵。

Owned Scope:
只读src/platform/clients/gamma.py、src/models/market.py及对应tests/fixtures。

Out of Scope:
不改代码，不联网，不设计shared ID，不新增collector。

Outputs:
fixture清单；字段present/missing/ambiguous矩阵；YES/NO outcome permutation案例；pagination/schema-drift失败案例；建议新增的最小fixtures。

Acceptance:
覆盖multi-market event、missing condition、token order reversal、one-character rule change、closed/resolved lifecycle；每项带现有path或明确MISSING。
```

### GLM-PREP-02 — Dispute/controversy fixture matrix

```text
Objective:
从现有dispute corpus/canonical tests提炼Controversy Recall所需PIT案例。

Owned Scope:
只读dispute_repricing代码、fixtures、tests和已记录的canonical schema信息。

Out of Scope:
不读取/写入production DB，不改RuleContract，不复制dispute mart。

Outputs:
case fixture索引；source/corpus revision字段；PIT cutoff、duplicate、missing source、identity mismatch失败场景；建议Recall reason codes。

Acceptance:
每个案例说明输入revision、as-of、预期hit/no-hit和source lineage；不得使用后到adjudication作为事前特征。
```

### GLM-PREP-03 — Wallet recall/privacy fixtures

```text
Objective:
整理wallet source的freshness、Address!=Entity、pagination和Blind privacy测试案例。

Owned Scope:
只读smart_wallets、wallet research scripts、wallet相关tests及已记录DB schema/count/time。

Out of Scope:
不联网、不更新wallet DB、不做tracker/scoring、不触及copy-trade execution。

Outputs:
fresh/stale边界fixtures；address/entity alias案例；truncated/incomplete案例；wallet-derived payload必须完全排除Blind的adversarial字段清单。

Acceptance:
明确当前48–79天stale数据只能作historical；任何wallet payload均不得进入BlindCandidateProjection。
```

### GLM-PREP-04 — Book anomaly fixture matrix

```text
Objective:
整理只依赖normalized paired book的结构异动测试，不做fair-value判断。

Owned Scope:
只读CLOB/book/capture fixtures和tests。

Out of Scope:
不联网、不提交demand、不修改capture owner、不设计概率模型。

Outputs:
missing/stale/one-sided/insufficient/crossed book；spread/depth；threshold monotonicity；market-family consistency；route-isolation fixtures。

Acceptance:
每个输出只允许STRUCTURAL_ANOMALY语义；没有book时只跳过本provider，不阻断Candidate。
```

## contracts冻结后可交给GLM的正式实现包

正式prompt直接使用`SUBAGENT_WORK_PACKAGES.md`对应完整合同，并附以下额外约束：

| 顺序 | GLM task | 可并行性 | Codex merge gate |
|---|---|---|---|
| G1 | P0-03 Gamma Catalog Adapter | P0-01/02后单独开始 | identity/hash/schema-drift复核 |
| G2 | P0-04 Change Detector | G1后 | no-book和append-only event复核 |
| G3 | P0-06C Controversy Recall | G1与P0-06A后，可与G4并行 | PIT/source identity复核 |
| G4 | P0-06D Wallet Recall | G1与P0-06A后，可与G3并行 | freshness/Blind privacy复核 |
| G5 | P0-06B Pre-book Structural Recall | G2与P0-06A后 | no-book/no-fair-value复核 |
| G6 | P0-06E Book Anomaly Recall | P0-05与P0-06A后 | route isolation/coverage denominator复核 |
| G7 | P0-10 Evidence Adapter | P0-09后 | harness/domain ownership与seal hash复核 |

## GLM统一回传合同

每个GLM任务必须返回：

```text
Task ID
Completion Status: COMPLETE / COMPLETE_WITH_LIMITATIONS / BLOCKED_BY_DEPENDENCY / REWORK_REQUIRED
Changed files
Exact test commands and results
Golden fixture/output hashes
Known limitations
Unresolved questions
Explicit confirmation of out-of-scope files not modified
Model / effort / tool calls / wall time / token telemetry（平台能提供多少就提供多少）
```

以下任一情况直接退回，不进入Codex集成：

- 修改shared contracts或migration；
- 新建HTTP/CLOB/wallet collector；
- 直接读取/写入current production DB；
- 引入order、signing、private key或live status；
- 用title/slug/token index作为canonical identity；
- 让pre-book provider依赖book；
- 将wallet、price-derived或operator market commentary带入Blind；
- 缺fixture replay、failure case或idempotency evidence。

## 推荐启动顺序

```text
现在：owner分配GLM-PREP-01～04（只读，不实现）

解除implementation hold后：
Wave 0  Codex P0-01 + Codex P0-11 policy scaffold
Wave 1  Codex P0-02
Wave 2  GLM P0-03 + Codex P0-06A + Codex P0-07
Wave 3  GLM P0-04/P0-06C/P0-06D
Wave 4  Codex P0-05 + GLM P0-06B
Wave 5  GLM P0-06E + Codex P0-08
Wave 6  Codex P0-09
Wave 7  GLM P0-10 + Codex P0-11 final
Wave 8  Codex P0-12 integration
```
