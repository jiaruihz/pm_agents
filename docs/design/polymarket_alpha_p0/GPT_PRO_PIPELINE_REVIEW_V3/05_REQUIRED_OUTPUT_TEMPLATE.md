# Required Output Template

请严格使用以下结构输出。

## A. Overall disposition

从三项选择一项，并用一段话说明：

```text
ACCEPT_CURRENT_ORCHESTRATION
ACCEPT_WITH_REQUIRED_CHANGES
REWORK_RESEARCH_ORCHESTRATION
```

同时给出 readiness scope，明确它是否只适用于 controlled manual GPT Pro pilot。

## B. Reconstructed current state

用一张表区分：已实现并真实验证、已实现但只用 fixture、合同已实现但未接真实
provider、尚未实现、明确不授权。纠正文档中任何过时状态。

## C. Missing-middle-node audit

逐个评审 N0–N13：`KEEP / MERGE / SPLIT / MOVE / REPLACE / REMOVE`，说明原因和
owner。重点指出 GLM-5.3 与 GPT Pro 之间究竟应该保留哪些节点。

## D. Recommended final DAG

给出唯一推荐 DAG，不要只列多个方案。清楚标出：

- deterministic nodes；
- GLM-4.7；
- GLM-5.3；
- human checkpoints；
- GPT Pro Blind research；
- optional/required Market-aware review；
- Rule A/B、book、Decision Ledger。

## E. Node contracts

至少包含以下列：

| Node | Sole owner | Allowed inputs | Forbidden inputs | Output contract | Gate/failure | Invalidation/replay |
|---|---|---|---|---|---|---|

为新增 contract 给出最小字段，不需要写完整代码。

## F. Model and human responsibility matrix

分别说明 GLM-4.7、GLM-5.3、Coordinator policy、human operator、GPT Pro、
deterministic importer 有什么 authority、没有什么 authority，以及何时被调用。

## G. GPT Pro manual handoff protocol

给出可执行流程：prompt seal → human preview/approval → copy → GPT Pro → response
capture → source capture → import/quarantine。明确哪些 bytes/hash/时间戳必须保存。

## H. GPT Pro market-research prompt design

提供一个精简但可复用的 prompt skeleton，包括 Blind source restrictions、研究问题、
claim-level evidence、probability/range、unknowns、disconfirming evidence、PIT cutoff 和
machine-readable appendix。不要在本轮实际研究某个市场。

## I. Blocking findings and decisions

用 `BF-xx` 编号。区分：开始 controlled manual GPT Pro pilot 前必须解决，和可以在
pilot 内验证的非阻断项。每项给验收证据。

## J. Implementation work packages

拆成 4–8 个有界任务，给 dependency、single owner、deliverables、tests/evidence、
rollback。不得创建平行 orchestration 或 schema owner。

## K. Pilot acceptance plan

推荐一个 3–10 market 小样本，包含简单规则、复杂规则、模型分歧、过期/失效和来源
不足案例。给指标：throughput、成本、分歧率、人工修改率、source coverage、quarantine、
replay、Blind leakage、研究质量。明确成功不等于 daily operation 或 trading approval。

## L. Exact document changes

列出需要修改/新增的现有文件、section、exact change 和 owner，方便 Codex 回写。

## M. Final owner-facing answer

用不超过 15 行中文告诉项目 owner：推荐流程是什么、下一步先做什么、哪些仍未授权。
