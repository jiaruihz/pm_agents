# 给 GLM 的完整提示词 — P0 Read-only Prep + Self Review

你现在是本项目的独立 Discovery / Fixture Design Engineer 与 Evidence Reviewer。

当前固定阶段：

```text
GLM_READ_ONLY_PREP_AND_SELF_REVIEW
```

你的任务不是实现P0代码，而是完成四个只读准备包，形成可直接交给一个全新Codex窗口审阅的单一handoff report。

## 一、工作目录与权威文档

```text
repository: /Users/deepsleep/projects/pm_agents
design root: /Users/deepsleep/projects/pm_agents/docs/design/polymarket_alpha_p0
```

先读取适用的`AGENTS.md`，然后严格按以下顺序读取：

1. `docs/design/polymarket_alpha_p0/P0_WORK_ALLOCATION_GLM_CODEX.md`
2. `docs/design/polymarket_alpha_p0/SUBAGENT_WORK_PACKAGES.md`
3. `docs/design/polymarket_alpha_p0/CURRENT_TO_TARGET_MAPPING.md`
4. `docs/design/polymarket_alpha_p0/GAP_AND_DECISION_LOG.md`
5. `docs/design/polymarket_alpha_p0/DEPENDENCY_GRAPH.md`
6. `docs/design/polymarket_alpha_p0/P0_IMPLEMENTATION_PLAN.md`
7. `docs/design/polymarket_alpha_p0/RISK_REGISTER.md`

冻结约束：

- P0只批准`OFFLINE_IMPLEMENTATION_ONLY`，但你本轮连offline实现也不做，只做只读准备和自审；
- 不新增collector、wallet tracker、Rule Lawyer、orchestrator、migration或状态机；
- 不联网，不访问Gamma/CLOB/Polymarket外部API；
- 不查询或写入current production DB、JRS DB或任何SQLite runtime store；
- 不修改production配置，不启停服务；
- 不导入或触达order、signing、private key、copy-trade execution；
- 不修改八个authoritative设计文件；
- 不修改任何代码、测试、fixture或现有artifact；
- 唯一允许新增的文件是最终handoff report。

## 二、Owned scope

只读检查以下区域及其直接对应tests/fixtures；不要全仓重复扫描：

### Gamma/catalog

```text
src/platform/clients/gamma.py
src/models/market.py
tests/research_tests/test_gamma_normalize_models.py
tests/research_tests/test_gamma_pagination.py
tests/research_tests/polymarket/test_market_resolver.py
```

### Dispute/controversy

```text
src/strategies/dispute_repricing/
scripts/analysis/dispute_repricing/
tests/research_tests/test_dispute_contract_corpus.py
tests/research_tests/test_dispute_canonical.py
tests/research_tests/test_dispute_artifacts.py
```

### Wallet recall/privacy

```text
src/strategies/rule_lawyer/services/smart_wallets.py
src/workflows/research/market_intel_workflow.py
scripts/copy_trade/
scripts/analysis/wallet_weather/
tests/research_tests/test_external_wallet_persist.py
tests/research_tests/test_external_wallet_history_collector.py
tests/research_tests/test_external_wallet_full_ladder_conversion.py
```

### Book anomaly

```text
src/platform/clients/clob.py
src/platform/market_data/
weather_data_feed_service/market_books.py
weather_data_feed_service/market_books_ws.py
tests/test_platform_market_data_contracts.py
tests/research_tests/polymarket/test_clob_orderbook_sorting.py
```

如果某路径不存在，标记`MISSING`并继续；不要新建替代实现。

## 三、四个准备包

### GLM-PREP-01 — Gamma fixture matrix

目标：形成event/market/condition/token/outcome/rule/lifecycle字段与fixture矩阵。

必须覆盖：

- multi-market event；
- missing/duplicate condition id；
- YES/NO outcome label与token顺序反转；
- title/slug不能作canonical key；
- one-character substantive rule change；
- whitespace-only/non-semantic rule change；
- new/closed/resolved/superseded lifecycle；
- pagination duplicate page、empty page、cursor termination；
- missing/extra/unknown source fields；
- source observed clock与ingest clock缺失。

每个case记录：

```text
fixture_id
existing_source_path or MISSING
input_shape
identity/revision condition
expected normalized behavior
expected fail-closed behavior
owner_task
recommended minimal fixture
```

### GLM-PREP-02 — Dispute/controversy PIT fixture matrix

目标：从现有contract corpus/canonical tests提炼P0-06C所需案例。

必须覆盖：

- absolute source identity与relative-path mismatch；
- rule_hash与contract_corpus_sha256分离；
- PIT cutoff；
- adjudication/clarification晚到，不能回填事前Recall；
- duplicate case/fragments；
- missing corpus/source offsets；
- initial/final、binding/non-binding、source precedence；
- canonical source unavailable时只跳过P0-06C，不阻断其他Recall。

每个case必须写清：input revision、decision as-of、预期hit/no-hit、source lineage和后见污染检查。

### GLM-PREP-03 — Wallet freshness/privacy fixture matrix

目标：整理P0-06D所需freshness、identity、pagination与Blind privacy案例。

必须覆盖：

- fresh / stale / missing observed_at boundary；
- 当前已知48–79天陈旧数据只能标historical；
- lowercase address normalization；
- Address != Entity；
- proxy/alias identity变化；
- pagination truncation/incomplete coverage；
- wallet source unavailable时只跳过P0-06D；
- wallet只能生成RecallHit，不能生成TradeIntent/Order；
- 任何wallet-derived payload都不得进入BlindCandidateProjection，不只是隐藏buy/sell方向；
- wallet hint被嵌入研究问题、nested string、reason text时必须被Blind隔离。

每个case记录private source fields、允许进入RecallHit的字段、允许进入Blind的字段（应为none）及预期失败方式。

### GLM-PREP-04 — Book structural anomaly fixture matrix

目标：整理P0-06E只消费normalized paired book的结构异动案例，不估fair value。

必须覆盖：

- no book；
- stale book；
- one-sided YES/NO；
- missing leg；
- insufficient target-size depth；
- crossed/invalid ladder；
- spread/depth异常；
- threshold monotonicity；
- market-family consistency；
- duplicate/out-of-order snapshots；
- missing book只跳过P0-06E，不阻断P0-06A～D或Candidate；
- 输出只能是`STRUCTURAL_ANOMALY`语义，不得出现fair value、mispriced、edge或交易建议。

每个case记录paired capture要求、freshness、coverage denominator、预期RecallHit/no-hit和route-isolation结果。

## 四、允许运行的验证

只允许运行现有fixture/unit tests；不得使用network、production DB或外部服务。

优先尝试：

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
```

如果环境或路径导致测试不能运行，原样记录失败命令和错误；不要修改代码让测试“变绿”。

## 五、唯一交付文件

只允许新增：

```text
docs/design/polymarket_alpha_p0/glm_handoff/GLM_P0_PREP_HANDOFF.md
```

不要创建其他文件。不要修改已有设计、代码、fixture或测试。

该文件必须包含：

### 1. Executive disposition

只允许：

```text
COMPLETE
COMPLETE_WITH_LIMITATIONS
BLOCKED_BY_DEPENDENCY
REWORK_REQUIRED
```

### 2. Scope confirmation

逐项确认：

- 是否修改代码：NO；
- 是否修改authoritative设计：NO；
- 是否联网：NO；
- 是否访问runtime/production DB：NO；
- 是否触及order/signing/private key：NO；
- 新增文件清单。

### 3. Repository evidence snapshot

列出实际读取的路径、文件SHA或git HEAD（如果可得）、现有test命令和结果。不要粘贴大段源码。

### 4. GLM-PREP-01 matrix

使用紧凑Markdown表格。每行一个fixture/case；区分`EXISTING`、`PARTIAL`、`MISSING`。

### 5. GLM-PREP-02 matrix

同上。

### 6. GLM-PREP-03 matrix

同上。

### 7. GLM-PREP-04 matrix

同上。

### 8. Coverage summary

```text
prep_id
required_cases
existing_cases
partial_cases
missing_cases
tests_covering
future_owner_task
```

### 9. Findings requiring Codex review

仅列真正需要Codex裁决的事项，按以下格式：

```text
Finding ID
Severity: BLOCKING_FOR_FUTURE_TASK / NON_BLOCKING
Evidence path
Problem
Recommended owner
Required contract decision
Acceptance evidence
```

不要把“缺少未来P0实现”本身写成问题；本轮本来就不实现。

### 10. GLM self-review

必须逐条回答：

- 是否误把历史报告当当前能力？
- 是否使用了后到数据作为PIT输入？
- 是否让pre-book Recall依赖book？
- 是否让wallet进入Blind？
- 是否在book anomaly中声称fair value/edge？
- 是否建议了第二collector/tracker/rule lawyer/orchestrator？
- 是否遗漏failure/idempotency/route-isolation案例？
- 是否存在没有evidence path支撑的结论？

### 11. Exact Codex handoff block

文件末尾必须给出一个可以原样复制到全新Codex窗口的块：

```text
----- BEGIN_CODEX_HANDOFF -----
Task: Independently review GLM P0 read-only prep outputs.
Repository: /Users/deepsleep/projects/pm_agents
Authoritative GLM report: docs/design/polymarket_alpha_p0/glm_handoff/GLM_P0_PREP_HANDOFF.md
Stage: REVIEW_ONLY
Implementation authorized: NO
Production/network/DB/order actions authorized: NO

Review requirements:
1. Verify GLM changed only the allowed handoff file.
2. Recheck evidence paths and targeted test claims.
3. Audit all four fixture matrices for missing canonical identity, PIT, freshness, Blind privacy, route-isolation and failure cases.
4. Reject any proposal that creates a parallel collector, wallet tracker, Rule Lawyer or orchestrator.
5. Map accepted cases to P0-03, P0-04, P0-06B, P0-06C, P0-06D and P0-06E.
6. Return disposition: ACCEPT / ACCEPT_WITH_FIXES / REWORK.
7. Do not implement code in the review turn.
----- END_CODEX_HANDOFF -----
```

## 六、完成前自检

交回前必须核对：

- 四个PREP包都有数字化coverage summary；
- 每个case有evidence path或明确MISSING；
- 没有读取生产DB或联网；
- 没有修改任何代码、测试、fixture或authoritative设计；
- 唯一新增文件是规定handoff；
- test结果诚实，失败不掩盖；
- handoff block完整；
- 最终回复给owner时，提供文件路径、disposition、case数量、tests结果和所有限制。

信息足够，不要停下来问是否继续。完成四个准备包、自审和handoff后再交回。
