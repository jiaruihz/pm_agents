# Proposed Missing Middle Nodes

以下是 Codex 推荐草案，供 GPT Pro 批判和改进，不是最终决定。

## 推荐目标 DAG

```text
Recall providers
  -> Candidate Aggregator
  -> Deterministic Candidate Eligibility
  -> Price-blind Semantic Projection
  -> GLM-4.7 Semantic Triage
  -> Deterministic Triage Import/Validation
  -> GLM-5.3 Semantic + Rule Critic
  -> Coordinator Synthesis
  -> Human Candidate Checkpoint (bounded)
  -> StructuredRuleParse Proposal
  -> Deterministic RuleContractCompiler / Rule A
  -> Blind Research Question Compiler
  -> Source Plan Compiler
  -> Blind Brief + GPT Pro Prompt Seal
  -> Human Export Approval Receipt
  -> Web GPT Pro Blind Research
  -> Research Draft Compiler
  -> Claim/Source/Probability Import Validation
  -> Blind Result Acceptance / Quarantine
  -> Fresh Paired Book Request
  -> Market Packet
  -> Market-aware Critique / probability update
  -> Rule B
  -> Decision + Prediction Ledger (NO_ORDER)
```

## N0 — Deterministic Candidate Eligibility

Owner: Candidate lifecycle/program logic。

检查 market closed/resolved、deadline elapsed、duplicate/superseded、rule/evidence
staleness 和 required source identity。硬事实不交给模型。失败产生 explicit transition，
不删除原 Candidate。

## N1 — GLM-4.7 Semantic Triage

Owner: external provider sidecar；Alpha importer owns acceptance。

用途是便宜地批量抽取 topic、entities、deadline interpretation、source type、
researchability、ambiguity、duplicate hint 和 `ADVANCE/REVIEW/DEFER` proposal。
没有 probability、fair value、edge 或 trade output。

## N2 — Deterministic Triage Gate

Owner: Alpha。

检查 schema、one-to-one item coverage、identity binding、forbidden semantic leakage、
provider/model/usage、deadline override、hash 和 immutable receipts。模型没有 hard reject
权限。

## N3 — GLM-5.3 Semantic + Rule Critic

Owner: external provider sidecar；不拥有 RuleContract。

推荐输入：同一份 price-blind projection、GLM-4.7 structured result、deterministic gate
receipt，以及原规则正文。推荐输出：

- `PASS | NEEDS_CORRECTION | HUMAN_REVIEW`；
- corrected semantic proposal；
- `StructuredRuleParseProposal`；
- 每个 rule field 对应的 exact source quote snippet；
- unresolved ambiguities；
- suggested neutral research gaps；
- disagreement codes，与 GLM-4.7 的差异。

它不得简单输出“同意/不同意”，也不得接触 price/book/wallet direction。是否所有
Candidate 都走该节点，还是 risk-based sampling，需要 GPT Pro 裁决。

## N4 — Coordinator Synthesis

Owner: Alpha orchestration + explicit policy；必要时人工。

它不是第三个自由发挥的模型。它比较 GLM-4.7 proposal、deterministic facts、GLM-5.3
critic 和 rule evidence，形成：

```text
ADVANCE_TO_RULE_A
RETURN_FOR_CORRECTION
HUMAN_REVIEW_REQUIRED
DEFER_NONTERMINAL
```

必须记录 disagreement、使用的版本/hash 和 reason codes。禁止用“两个模型都同意”
作为充分证据。

## N5 — Human Candidate Checkpoint

Owner: project owner/operator。

只对以下情况强制人工：模型分歧、低 confidence、RuleParse 不完整、重大歧义、
高 research cost、敏感来源或抽样质量检查。人工可以 approve、request correction、
defer，但自由文本修改必须另存 patch + before/after hash，不能直接改 sealed packet。

## N6 — Rule Parse Adapter + Rule A

Owner: shared `RuleContractCompiler`。

GLM-5.3 只提出 parse。deterministic adapter 将 quote snippet 映射到 frozen rule artifact
的 exact offsets，重算 hash，验证 field coverage、source precedence、deadline/timezone、
initial/final 和 ambiguities，然后编译 canonical RuleContract。Rule A 未 PASS 不能生成
Blind packet。

## N7 — Blind Research Question Compiler

Owner: deterministic templates/allowlist；模型只可提出 candidate questions。

问题只能由 RuleContract、neutral proposition 和允许的 evidence gaps 生成。禁止从
RecallHit free text、price/book、wallet facts、candidate direction 或 operator market
commentary 派生。每个问题保存 template id、rule hash、evidence ids 和 provenance。

## N8 — Source Plan Compiler

Owner: research orchestration policy。

在 GPT Pro 搜网前定义：目标 claim、首选 primary source 类型、secondary fallback、
禁止来源、PIT cutoff、capture scope、最低 evidence coverage、最大 research budget 和
停止条件。SourcePlan 不是搜索结果，是研究任务合同。

## N9 — GPT Pro Prompt Seal + Human Export Approval

Owner: existing brief/work-order/artifact owners。

系统把 Blind packet、RuleContract、ResearchQuestions、SourcePlan、输出 schema、source
policy 和 run identity 编译成不可变 prompt。人工复制前看到 human-readable summary，
然后签发 approve receipt；复制内容必须等于 sealed bytes。当前阶段可手工复制，不必
自动控制浏览器。

## N10 — Web GPT Pro Blind Research

Owner: human-operated GPT Pro session；返回内容仍是 untrusted draft。

GPT Pro 使用网页搜索做：事实核查、primary/secondary source 比较、claim-level
evidence、独立 probability/range、unknowns、disconfirming evidence 和有效时间。第一轮
禁止访问 Polymarket market page、Gamma/CLOB 或价格镜像。

## N11 — Draft Compile / Import / Acceptance

Owner: existing `ResearchDraftCompiler`、importer、AlphaRepository。

本地重新计算 source bytes/text/excerpt hash，验证 claim/source coverage、PIT clocks、
Blind isolation、probability bounds、packet/rule identity。无法保存实际 source bytes 时
必须降级标记 `REFERENCE_ONLY`，不能声称完整 replay。失败进入 quarantine。

## N12 — Market-aware Review

只有 Blind result accepted 后才请求 fresh paired book。Market packet 同时绑定 exact
Blind baseline 和 book receipt。可使用第二次 GPT Pro、GLM-5.3 或本地 deterministic
analysis 做 price-aware critique，但不得重写 Blind result。需要 GPT Pro 推荐最佳 owner、
必要性和成本控制。

## N13 — Rule B / Decision Ledger

Rule B 验证 exact same rule hash、fresh evidence/book、accepted Blind baseline。最终只写
watchlist/simulated/no-action prediction，`execution=NO_ORDER`。后续 resolution backfill
负责校准和学习。
