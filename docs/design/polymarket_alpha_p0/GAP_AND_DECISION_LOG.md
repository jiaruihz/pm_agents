# Gap and Decision Log

## 1. Gap register

| Gap ID | Gap | Severity | Resolution | Task |
|---|---|---:|---|---|
| GAP-001 | 无共享 Alpha envelope/canonical id factory | P0 | BUILD，唯一 shared-contract owner | P0-01 |
| GAP-002 | catalog current-row overwrite，缺 immutable rule/market revision | P0 | additive `alpha_*` revision schema | P0-02/P0-03 |
| GAP-003 | Gamma condition/token/event mapping不完整 | P0 | explicit identity/alias/join tables + label validation | P0-03 |
| GAP-004 | 无标准 change detector | P0 | revision diff consumer；产出change events，book sensing是可选side route | P0-04 |
| GAP-005 | book 数据 token-level、选择性覆盖，缺 paired P0 contract | P0 | existing-owner adapter + demand/receipt | P0-05 |
| GAP-006 | recall输出不统一且被book错误串行化风险 | P0 | pre-book providers、optional book provider和aggregator拆分；pure versioned RecallHit | P0-06A..P0-06E |
| GAP-007 | Rule parser 不是不可变 RuleContract，A/B 未统一 | P0 | one compiler facade + same-hash gates | P0-07 |
| GAP-008 | Blind/Market packet不存在；字段过滤不能阻止slug/URL/side/reason等语义泄漏 | P0 | BlindCandidateProjection allowlist + source policy + manual filesystem gates | P0-01/P0-08 |
| GAP-009 | Candidate状态机缺增量失效/刷新；ReviewDecision/PredictionRecord不存在 | P0 | append-only lifecycle/invalidation ledger | P0-01/P0-09 |
| GAP-010 | wallet stores schema冲突、数据陈旧且trade direction可能泄漏Blind | P0 | historical read-only adapter、freshness fail-closed、Blind projection剥离方向 | P0-06D/P0-08 |
| GAP-011 | harness seal layout与设计包不一致 | P0 | thin seal adapter，harness仍是治理 owner | P0-10 |
| GAP-012 | real order modules、通用HTTP、dynamic import与subprocess在同仓可绕过模块denylist | P0/security | process/runtime capability sandbox、精确read-only transport/env allowlist、canary | P0-11 |
| GAP-013 | Decimal/JSON/SQLite 表示未统一 | P0 | Decimal internal，canonical decimal string storage，contract serializer emits validated JSON number/string by field policy | P0-01/P0-02 |
| GAP-014 | canonical dispute DB 相对路径 identity 可歧义 | P0 | absolute path + device/inode/schema evidence gate | P0-07/P0-10 |
| GAP-015 | 默认VWAP target与真实API rate未验证 | offline implementation probe + operational gate | versioned config + fixture；真实rate/smoke只在read-only operational pilot | P0-03/P0-05/OP-GATE |
| GAP-016 | ResearchResult缺claim-level source/effective-time lineage | P0 | canonical claim EvidenceItem + source snapshot/hash | P0-01/P0-08 |
| GAP-017 | offline implementation、read-only pilot、production capture expansion readiness混淆 | P0 governance | 三层readiness + 独立operational pilot gate | P0-10/P0-12 |

## 2. ADR-001 — P0 deployment shape

**Context**：目标功能多，但共享 schema、排序协议和 no-live 证明优先于独立扩缩容。

**Decision**：P0 是现有 Python 仓库内的模块化单体和一个研究 SQLite logical store；不拆微服务。

**Alternatives rejected**：为 census/rule/wallet/research 各建 service；会立即制造 schema、ownership 和部署重复。

**Consequences**：接口仍以版本化 Pydantic/JSON 合同隔离；未来有负载证据后再拆。

**Rollback**：删除 Alpha feature flag/入口即可，既有 production services 不受影响。

## 3. ADR-002 — Storage ownership

**Decision**：`runtime/db/research.db` 承载 additive `alpha_*` 表；`weather.db`、`wallets.db`、canonical `dispute.db` 保持各自 owner。legacy `copy_trade_*` 只读保留。Alpha Storage 包是唯一 migration owner。

**Why**：避免污染 16 GB production weather canonical DB，同时不创建新的平行 research DB；现 research DB 很小且是既有 research namespace。

**Conflict resolved**：不再保留“wallet canonical owner”或“shared ledger owner”待定。Alpha Research Core owns normalized P0 records；Wallet/Dispute 只 owns source facts。

**Migration**：新增 schema version table scoped to Alpha，事务迁移，migration manifest 记录 DB absolute path/device/inode/pre/post hash/count。

**Rollback**：停 feature flag；保留新增表和 artifacts 为 dormant，不删除；通过 version view 回退读路径。

## 4. ADR-003 — Canonical market identity

**Decision**：Gamma `market_id` 为主键，`condition_id` 为唯一 alternate key；event-market 关系显式建表；YES/NO token 依据 outcome label 验证后映射；slug/URL 仅为带 source/effective interval 的 alias。

**Rejected**：以 title、slug 或 token index 作为 identity。

**Consequence**：identity 未完成的 market 可被 catalog 收录，但不得进入 orderbook/research market stage。

**Rollback**：adapter 可关闭；raw revision 与 legacy ids 均保留。

## 5. ADR-004 — Rule versioning and Gate A/B

**Decision**：raw rule immutable 保存；只做非语义 normalization，`rule_hash=sha256(normalized_raw_rule)`。`contract_corpus_sha256` 独立保存。一个 `RuleContractCompiler` core 供 Gate A/B；B 必须引用 A 的 `rule_hash`，若当前 revision 不同则阻断并重新走 A/Blind。

**Rejected**：把 dispute corpus hash 当 rule hash；继续覆盖 current rule；为 B 另建一个 rule lawyer。

**Consequence**：parser/prompt/compiler/corpus 都进入 `contract_revision_id`，可 PIT 重放。

**Rollback**：旧 parser 仍可独立运行，但不能签发 P0 Gate pass。

## 6. ADR-005 — Raw-to-normalized lineage

**Decision**：所有 source fetch 先产生 immutable raw artifact（source clock、ingest clock、request metadata、content hash、capture id），normalized revision 只引用 raw id。Orderbook YES/NO 必须属于同一 capture group，并记录每腿 freshness。

**Rejected**：只存最新 normalized row或在分析时重新请求网络补字段。

**Rollback**：停止 materializer，raw artifacts 仍可由旧 owner消费。

## 7. ADR-006 — Single orderbook owner

**Decision**：现有 `weather_market_books` + capture demand/receipt 继续是唯一 CLOB capture owner。P0只有book adapter可提交两类purpose明确的demand：`SENSING`用于可选book结构异动召回，`FORMAL_REVIEW`只能由accepted blind result触发并用于Market Packet。pre-book recall与Candidate不得依赖任一book demand成功。Alpha adapter读取receipt/artifact并生成paired snapshot。

**Rejected**：新建 all-market collector、直接在 recaller 内调用 WS、复活 legacy generic feeder。

**Operational boundary**：P0可用fixtures/offline cache实现与验收；扩大生产token coverage要先通过独立read-only operational pilot gate，再走`weather-strategy-deploy`明确审批，不阻断offline编码。

**Rollback**：撤销 Alpha demand producer和consumer；现有 weather capture参数不变。

## 8. ADR-007 — Wallet ownership and semantics

**Decision**：地址标准化为 lowercase `0x...`，但 `Address != Entity`；source identity/provenance 必填。`wallets.db` 和 wallet-weather artifacts 是 historical source，Alpha adapter按 freshness生成 `SPECIALIST_WALLET_ENTRY` RecallHit，永不直接生成 trade intent。旧 copy-trade status 不迁移为 P0 candidate status。

**Rejected**：建立第三个 wallet tracker；把 stale snapshot 当当前信号；复用 legacy `live` status。

**Rollback**：禁用 wallet recaller；其他 recallers与历史源均不受影响。

## 9. ADR-008 — Manual GPT Pro boundary and price isolation

**Decision**：P0使用filesystem outbox/inbox，并在Candidate与Blind packet之间增加独立`BlindCandidateProjection`。它采用字段allowlist和新的blind id，只允许规则正文、neutral proposition、实体、截止时间、受控研究问题及非盘口证据；禁止canonical market id、slug、URL、candidate/order side、全部wallet-derived payload、price-derived recall reason/features、book/price/market probability。Blind source policy禁止访问或引用Polymarket页面、Gamma/CLOB API及其镜像；导入器拒绝违规claim。

每个`BlindResearchQuestion`必须包含`question_id`、`template_id`、`generated_from_rule_contract_hash`、`generated_from_evidence_ids`和`text`。问题只能由RuleContract、neutral proposition和已允许的非市场事实缺口模板生成；不得来自RecallHit free text、wallet facts、price/book features、candidate direction或operator free-form market commentary。projection builder对所有嵌套字符串递归执行语义扫描，不只检查顶层字段。

Blind packet只能由projection构建并冻结projection/packet SHA。只有blind result通过packet/schema/producer/source-policy hash校验后才能提交`FORMAL_REVIEW` book demand并构建Market packet。ResearchResult不是一份不可分Markdown：每个claim至少保存`claim`、`supports_yes_or_no`（相对neutral proposition，不是交易指令）、`source_tier`、`source_url_or_source_id`、`published_at`、`accessed_at`、`effective_as_of`、`primary_or_secondary`、`quotation_or_paraphrase_location`、`confidence`、immutable`source_artifact_id`、`capture_scope=FULL_DOCUMENT|EXCERPT_ONLY|REFERENCE_ONLY`、`hash_scope=RAW_BYTES|NORMALIZED_TEXT|CLAIM_EXCERPT`及对应content hash。只有实际保存的bytes/text artifact才能声明content hash；REFERENCE_ONLY不能声称完整PIT replay；importer必须重算hash，不信任GPT自报值；没有完整快照时至少冻结模型实际使用的excerpt与上下文。原始报告仍可作为父artifact保留。

**Rejected**：自动外部API调用；只删除price字段；靠prompt文本要求“忽略价格”；把price-based reason、wallet方向或market URL带入Blind；只保存一份无法按claim重放的Markdown。

**Rollback**：停止 importer；已冻结 packets和results保留审计。

## 10. ADR-009 — Candidate and Prediction Ledger

**Decision**：Candidate current projection来自append-only transitions；PredictionRecord、ReviewDecision同样append-only。稳定幂等键覆盖market/revision/decision/model/config/as-of。首次研究保持严格A→Blind→Book→B顺序；每日扫描通过`RECALL_EXPIRED`、`EVIDENCE_STALE`、`RESEARCH_REFRESH_REQUIRED`、`RULE_REVISION_INVALIDATED`、`BOOK_REFRESH_REQUIRED`、`MARKET_CLOSED`、`RESOLVED`、`SUPERSEDED`、`ARCHIVED`显式事件失效或刷新，不能静默回退/覆盖。position enum P0仅允许`NO_POSITION`或`SIMULATED`，DB CHECK与validator双重限制。

**Rejected**：复用 wallet/copy-trade/live states；update-in-place覆盖历史。

**Rollback**：切换读 view 到上个 schema version；新 records 保留 dormant。

## 11. ADR-010 — Orchestration ownership

**Decision**：weather agent harness 是唯一 task orchestration/evidence owner；扩展 WorkOrder payload承载设计包 task contract，EvidenceStore加 seal manifest adapter。Alpha DB只 owns domain state，不实现 leases/worker scheduling。

**Rejected**：在 Alpha DB 再建 job queue；让 harness成为 candidate state machine。

**Rollback**：用本地 sequential runner提交相同 task functions，domain artifacts格式不变。

## 12. ADR-011 — No-live proof

**Decision**：no-live是process/runtime capability boundary，不只是module denylist。

- offline P0进程默认无网络能力；
- read-only pilot只能通过`AlphaReadOnlyTransport`访问版本化的`host + path + method`精确allowlist；公开`POST /books`等只读POST必须逐endpoint授权，默认拒绝；
- adapter不得直接使用`requests`、`httpx`、`aiohttp`、raw websocket/socket；运行时拦截未注册transport；
- 禁止`importlib`/dynamic import加载execution/signing模块，禁止`subprocess`、`os.system`、shell和旧CLI；
- 进程env使用allowlist，不注入private key、wallet、signing secret或authenticated CLOB credentials；禁止Authorization/signature material；
- 静态AST/import policy、运行时canary transport、sentinel order/signing client、DB position CHECK和CLI/config audit从Wave 0建立，每次merge跑subset，P0-11做final proof；
- 所有允许请求都写host/path/method/status/response hash receipt，未授权尝试直接使Gate fail。
- URL匹配前必须canonicalize；默认禁止redirect，若显式允许则每一跳重新校验host/path/method；不继承`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`；query参数和POST body按endpoint schema验证；禁止Authorization、Cookie、签名头和未知header；receipt同时记录canonical URL、DNS解析与实际连接目标。

**Rejected**：只在文档写“不会下单”；只禁止已知order import；仅靠环境没有key；允许任意通用HTTP后再依赖调用方自律。

**Rollback**：如发现可达路径立即停用整个 Alpha entrypoint并标 Gate fail，不降级继续跑。

## 13. ADR-012 — Numeric and time policy

**Decision**：概率、价格、shares、notional在计算域使用 `Decimal`；SQLite canonical存储decimal string并配合格式/check约束，JSON按目标合同的字段类型由canonical serializer输出，禁止float中间计算。全部规范时间为UTC RFC3339，同时保留source raw和source timezone。

**Rollback**：schema version pin到上一 serializer；禁止静默重写历史数值。

## 14. ADR-013 — Readiness levels and long-term capture ownership

**Decision**：设计包允许的系统disposition仍使用`READY_FOR_P0_IMPLEMENTATION`，但其scope必须显式为`OFFLINE_IMPLEMENTATION_ONLY`。`READ_ONLY_OPERATIONAL_PILOT`和`PRODUCTION_CAPTURE_EXPANSION`是两个独立后续gate，不由代码完成自动获得。read-only gate验证arbitrary binary tokens、paired receipts、API/load/storage/staleness、weather isolation、capability sandbox与真实rollback。production expansion仍需deploy审批。

P1保留single-owner/demand/receipt invariant，把当前`weather_market_books`实现逐步抽为platform-level owner，weather与Alpha作为consumer；P0不新建平行owner，也不把weather域命名永久固化为平台架构。

**Rollback**：offline实现可完全禁用；read-only pilot可独立关闭Alpha demands/egress；production owner在迁移前后保留明确单一owner与回退版本。

## 15. Decision status

| Decision class | Status |
|---|---|
| shared schema owner | RESOLVED — Alpha Storage |
| market-book owner | RESOLVED — existing capture service |
| wallet owner/identity | RESOLVED — source owners + Alpha recall adapter |
| rule A/B owner/version | RESOLVED — one compiler facade |
| task orchestration | RESOLVED — existing harness |
| P0 execution boundary | RESOLVED — capability sandbox合同已冻结；实现必须给动态proof |
| offline implementation | READY after owner accepts revised Gate 2 |
| read-only operational pilot | NOT READY — separate operational gate |
| production capture expansion | NOT AUTHORIZED — later deploy approval |

GPT Pro评审提出的BF-1（Recall/book依赖）、BF-2（Blind语义泄漏）和BF-3（capability no-live）已由ADR-006/008/011/013及修订DAG消解。原设计包只允许三个最终disposition，因此正式值为`READY_FOR_P0_IMPLEMENTATION`，其唯一含义是`readiness_scope=OFFLINE_IMPLEMENTATION_ONLY`；不等同于daily read-only operation或production capture expansion。
