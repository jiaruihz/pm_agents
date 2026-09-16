# 给 GPT Pro 的完整提示词

你现在是本项目的 Principal Research Workflow Architect、Adversarial Model
Reviewer 与 Integration Gatekeeper。

请先完整阅读本压缩包内的文件，顺序固定为：

1. `01_REVIEW_BRIEF.md`
2. `02_CURRENT_IMPLEMENTED_FLOW.md`
3. `03_PROPOSED_MISSING_MIDDLE_NODES.md`
4. `04_DECISIONS_AND_REVIEW_QUESTIONS.md`
5. `05_REQUIRED_OUTPUT_TEMPLATE.md`
6. `source_docs/` 内的原始设计与 evidence README

## 本轮目标

这不是让你研究某一个 Polymarket 市场，也不是让你写代码。请审查并改进
Polymarket Alpha 从多路召回、语义筛选、规则冻结、盲研、联网研究、盘口审阅
到 `NO_ORDER` Decision Ledger 的完整工作流。

项目 owner 特别要求你解决以下问题：

> GLM-4.7 初筛经过程序校验、GLM-5.3 语义复审后，不能直接跳到网页
> GPT Pro。中间应该有哪些 coordinator、规则、研究问题、source plan、人工
> 审批和 evidence seal 节点？每个节点由谁负责、允许看到什么、输出什么、
> 如何验收和回滚？

## 必须遵守的现有边界

- 不新建第二套 Gamma/CLOB collector、wallet tracker、Rule Lawyer、scheduler
  或 artifact owner。
- Pre-book Recall 不得整体依赖 orderbook。
- Rule A/B 共用一个 `RuleContractCompiler` 和同一个 rule hash。
- Blind 阶段不得看到 Polymarket price/book、market probability、slug/URL、
  candidate/order direction、wallet direction 或 price-derived reason。
- wallet facts 只负责召回，不进入 Blind research packet。
- GPT Pro/GLM 输出都是 untrusted proposal；确定性的 schema、identity、hash、
  deadline、source binding 和生命周期检查不能交给模型替代。
- Alpha domain process 不嵌入 provider SDK、browser、generic HTTP、shell 或
  subprocess；外部 sidecar/human handoff 执行研究，Alpha 只导入 sealed result。
- 当前所有 Decision 必须 `execution=NO_ORDER`。真实下单、签名和私钥严格
  不在本轮范围。
- 不把 controlled pilot 等同于 daily operational readiness 或 production
  capture expansion。

## 你要重点挑战的设计

附件中的 `03_PROPOSED_MISSING_MIDDLE_NODES.md` 是 Codex 的推荐草案，不是既定
答案。请逐节点判断是否：

- 缺失、重复或顺序错误；
- 把 deterministic authority 错交给了模型；
- 产生 correlated model error 或“模型互相盖章”；
- 造成 Blind semantic leakage；
- 缺少人工可理解的 checkpoint；
- 缺少 claim/source/probability 的 point-in-time lineage；
- 无法 replay、invalidate、refresh、quarantine 或 rollback；
- 成本过高，无法批量扫描；
- 让 GLM-5.3、GPT Pro 和 coordinator 的职责重叠。

## 输出要求

严格按照 `05_REQUIRED_OUTPUT_TEMPLATE.md` 输出一份完整 Markdown，文件建议命名：

`GPT_PRO_PIPELINE_REVIEW_RESULT_V3.md`

请给出一个明确 disposition：

```text
ACCEPT_CURRENT_ORCHESTRATION
ACCEPT_WITH_REQUIRED_CHANGES
REWORK_RESEARCH_ORCHESTRATION
```

不要只做原则评论。必须提供：最终推荐 DAG、逐节点 contract、model/human/
deterministic ownership、输入输出 allowlist、failure/invalidation 路径、GPT Pro
人工交接协议、分阶段实施任务、验收证据和明确的 blocking decisions。

默认用中文回答，保留 contract、hash、Rule A/B、Blind、Candidate、Recall、
evidence、sidecar 等英文技术名词。
