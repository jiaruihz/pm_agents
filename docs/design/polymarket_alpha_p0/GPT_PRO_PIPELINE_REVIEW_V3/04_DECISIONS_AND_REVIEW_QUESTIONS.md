# Decisions and Review Questions

## 已冻结，不应重新打开

1. 单一 collector/book owner、单一 migration owner、单一 Rule compiler、单一
   ArtifactStore/AlphaRepository owner。
2. Pre-book recall 不依赖 book；book anomaly 只是可选召回支路。
3. Blind allowlist 和 venue/price/direction/wallet isolation。
4. Rule A/B 同源同 hash；mismatch fail closed。
5. 模型/provider 返回永远 untrusted；确定性事实不能由模型最终裁决。
6. append-only artifacts、explicit invalidation/refresh/quarantine。
7. 当前只能 `NO_ORDER`。

## 请明确裁决的问题

### Q1. GLM-5.3 的触发范围

全量复审、仅 `REVIEW/low confidence/complex rule`，还是风险分层 + 随机抽样？请给
推荐 policy、阈值、成本和漏检权衡。

### Q2. GLM-5.3 是否兼任 RuleParse proposer

请判断 semantic critic 与 RuleParse proposal 是否应一次调用完成。若拆开，说明收益
是否足以抵消成本与状态复杂度。

### Q3. Coordinator Synthesis 的 authority

哪些决定必须 deterministic，哪些可以使用模型 proposal，哪些需要人工？请避免再
引入一个无边界的 coordinator LLM。

### Q4. Human Checkpoint 放在哪里

应在 Rule A 前、GPT Pro export 前，还是两处都有？什么情况可以自动直通，什么情况
必须人工？人工编辑如何 hash-bound？

### Q5. ResearchQuestionCompiler 与 SourcePlan

请给最小 contract 字段、允许输入、禁止输入、版本/identity 和 acceptance tests。

### Q6. GPT Pro 第一轮研究合同

请给适合人工复制到网页 GPT Pro 的 prompt 结构、Blind source policy、claim-level
输出、probability 格式、citation/capture 要求和失败重试规则。

### Q7. GPT Pro 返回如何导回

直接复制 Markdown、要求 JSON appendix、下载 source files，还是组合方式？请推荐在
“操作简单”和“可重放证据”之间最合适的 P1 pilot 方案。

### Q8. 是否需要第二次 market-aware GPT Pro

若需要，第二轮应看到什么，如何绑定 Blind baseline，如何避免事后修改独立概率？
若不需要，Market review 由哪个现有节点承担？

### Q9. correlated model error

GLM-4.7、GLM-5.3 和 GPT Pro 可能共享相似推理偏差。请设计 disagreement、抽样、
人工复核和 source evidence 机制，避免“三个模型一致”被误认为事实。

### Q10. 下一阶段实施顺序

请拆成 4–8 个有界 work package。每个必须包含 owner、输入依赖、owned files/contracts、
验收证据、rollback 和不得修改的边界。

## 当前倾向，允许你推翻

- P1 pilot 先采用人工复制 GPT Pro prompt/response，不自动控制浏览器；
- GLM-4.7 做全量廉价筛选；GLM-5.3 做风险分层复审而不是全量；
- Rule A 和 research question/source plan 必须在 GPT Pro 前冻结；
- GPT Pro 第一轮只做 Blind research；是否做第二轮等待 pilot 证据；
- GPT Pro response 使用 human-readable Markdown + machine-readable JSON appendix；
- source bytes 能下载则由本地保存并重算 hash，不能下载则显式 `REFERENCE_ONLY`。
