# Review Brief

## 当前裁决

```text
P0_OFFLINE_PIPELINE=COMPLETE
P1_CONTROLLED_RESEARCH_AUTOMATION=COMPLETE_OFFLINE_BASELINE
CONTROLLED_50_MARKET_E2E=COMPLETE_WITH_LIMITATIONS
GLM_4_7_SEMANTIC_TRIAGE_PILOT=COMPLETE
CONTROLLED_EXTERNAL_RESEARCH_PILOT=READY_FOR_SEPARATE_APPROVAL
DAILY_READ_ONLY_OPERATION=NOT_APPROVED
PRODUCTION_CAPTURE_EXPANSION=NOT_AUTHORIZED
LIVE_ORDER_SIGNING_PRIVATE_KEY=STRICTLY_OUT_OF_SCOPE
```

本轮不是重审早期资产盘点，也不是推倒现有架构。重点是把“模型理解 → 规则
冻结 → 人工审阅 → GPT Pro 联网研究”之间的 orchestration 设计完整。

## 为什么现在需要复审

已经真实跑通 GLM-4.7 对 3 个市场的脱敏语义初筛。一次 exploratory run 把已
过期天气市场判为 `DEFER`，另一次却判为 `ADVANCE`。程序随后增加 deterministic
deadline eligibility，把最终结果稳定为 `DEFER`。

这个实测说明：

1. 模型适合语义抽取和提出建议，不适合拥有 deterministic authority；
2. GLM-5.3 再审可以发现语义问题，但不能只是给 GLM-4.7 盖章；
3. GLM-5.3 后不能直接把原始内容丢给 GPT Pro，需要中间合同和人工 checkpoint；
4. GPT Pro 的主要优势是网页搜索、来源比较和综合判断，应把它放在 sealed
   research plan 之后，而不是让它自行重构整个市场问题。

## 希望 GPT Pro 回答的核心问题

- GLM-5.3 应该 review 所有 Candidate，还是只审 `REVIEW`、低置信度、规则复杂
  或抽样 Candidate？
- GLM-5.3 是 semantic critic、RuleParse proposer，还是两者拆开？
- Coordinator synthesis 应该是 deterministic aggregator、轻量模型、人工，还是
  三者组合？谁拥有最终 `ADVANCE_TO_RULE_A` 权限？
- Rule A 应位于 GLM-5.3 之前还是之后？如果 GLM 提供 parse proposal，如何通过
  exact source quote/offset/hash 编译成 canonical RuleContract？
- 谁生成 BlindResearchQuestion 和 SourcePlan，如何证明它们没有从 price、wallet
  direction 或 recall reason 泄漏？
- 人工 owner 在把 prompt 复制到 GPT Pro 前究竟审什么，如何记录 approve/reject/
  edit，避免不可重放的自由文本改写？
- GPT Pro 返回的网页来源如何转成 source artifact、claim-level evidence 和独立
  probability estimate？REFERENCE_ONLY、EXCERPT_ONLY、FULL_DOCUMENT 各自能证明什么？
- 是否需要两次 GPT Pro：第一次 Blind research，第二次 market-aware critique？
  如果需要，两次的输入边界、成本和 Rule B 顺序是什么？

## 本轮不要求

- 不要求开发代码；
- 不要求访问真实 Polymarket 市场；
- 不要求批准网络 pilot、生产部署或真实交易；
- 不要求为所有长期 P2 功能做完设计，只需给出可实施的下一阶段。
