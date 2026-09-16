# Subagent Work Packages

> Status note (2026-08-27): 本文件保留完整原始任务合同；当前已完成项、
> remaining DAG和新拆分的R2/08A-C/09A-C任务以
> `P0_REMAINING_WORK_BREAKDOWN.md`为准。下方历史 `Completion Status` 未逐项
> 改写，不应作为当前状态源。

## 使用规则

- 每个包是单一窄任务、单一执行 turn、单一 owner；worker 不得再派生 subagent。
- worker 必须从列出的输入开始，不做全仓重复扫描，不修改其他包 owned 文件。
- 默认建议实现 worker 使用 `gpt-5.6-terra`/medium；关键 contracts、security 与最终集成由 coordinator 主审。实际模型、effort、tool calls、wall time、input/output/cached tokens 必须写入 completion telemetry；缺 telemetry 视为 `COMPLETE_WITH_LIMITATIONS`。
- 允许的 Completion Status 仅为设计包规定的：`COMPLETE`、`COMPLETE_WITH_LIMITATIONS`、`BLOCKED_BY_DEPENDENCY`、`REWORK_REQUIRED`。
- 所有包都处于 `DISCOVERY_AND_INTEGRATION_DESIGN_ONLY` 之后的 P0 implementation 阶段；本文件本身不授权 production deployment、数据库 current-instance migration 或任何 live order。
- 下文 `design pack NN` 分别指 `01_SYSTEM_CHARTER.md`、`02_TARGET_ARCHITECTURE.md`、`03_MVP_EXECUTION_SPEC.md`、`04_INTEGRATION_DISCOVERY_SPEC.md`、`05_CONTRACTS.md`、`06_SUBAGENT_TASK_TEMPLATE.md`、`07_REVIEW_GATES.md`、`08_REFERENCE_INDEX_TEMPLATE.md`。

---

## P0-01-SHARED_CONTRACTS — Shared contracts

**Task ID**: `P0-01-SHARED_CONTRACTS`

**Objective**: 实现common envelope、canonical serializer/id、Market/Book/Recall/Candidate/Rule/BlindCandidateProjection/Packet/claim Evidence/Decision/Prediction合同与版本兼容策略。

**Parent Design References**: design pack 03/04/05/08；ADR-003/004/008/009/011/012/013。

**Current Repository Context**: 输入仅限 `src/models/market.py`、`src/platform/market_data/capture_contract.py`、harness contracts及本目录八份设计文档。

**Owned Scope**: `src/polymarket_alpha/contracts/**`、对应 JSON schema fixtures/tests。

**Explicitly Out of Scope**: migrations、network adapters、business algorithms、orders、production config。

**Inputs**: contract field mapping；Decimal/time/id policy；current HEAD and design-pack hash。

**Required Outputs**: versioned models、BlindCandidateProjection allowlist、BlindResearchQuestion provenance、claim-level EvidenceItem及source/capture/hash scope、Candidate invalidation events、canonical serializer/hash factory、golden fixtures、compatibility matrix、usage telemetry。

**Acceptance Criteria**: deterministic bytes/hash；unknown major rejected；YES/NO identity explicit；Blind projection无法表示slug/URL/market id/candidate side/wallet direction/price-derived reason/book/price；claim evidence字段完整；Prediction position only allowed enum。

**Required Tests**: serialization property tests；Decimal/time boundary；schema golden diff；Blind question/projection nested-string leakage；claim evidence source/capture/hash/replayability；invalidation enum；id determinism。

**Evidence Required**: test JSON/JUnit、generated schema hashes、changed-file list、model/effort/token/tool telemetry。

**Dependencies**: none。

**Risk and Rollback**: contract churn；通过version pin和兼容reader回退，不改下游owner文件。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待本轮 Discovery 结束并显式进入 P0 implementation。

---

## P0-02-ALPHA_STORAGE — Alpha storage and migrations

**Task ID**: `P0-02-ALPHA_STORAGE`

**Objective**: 在临时/fixture `research.db` 上实现 additive `alpha_*` schema、repositories和schema manifest。

**Parent Design References**: ADR-002/005/009/012；P0 plan第4节。

**Current Repository Context**: 读取现有 `runtime/db/research.db` schema snapshot、rule_lawyer storage、harness store patterns；不得写 current runtime DB。

**Owned Scope**: `src/polymarket_alpha/storage/**`、migration fixtures/tests。

**Explicitly Out of Scope**: legacy table修改/删除、weather/wallet/dispute DB写入、业务adapter。

**Inputs**: P0-01 released contracts；pre-migration DB manifest。

**Required Outputs**: transactional migrations、repositories、idempotency/uniqueness/FK/CHECK constraints、read-view rollback说明、telemetry。

**Acceptance Criteria**: empty/current-legacy/repeat/interrupted migration pass；legacy counts/hashes unchanged；only Alpha migration owner writes schema。

**Required Tests**: migration matrix、foreign_key_check、integrity_check、concurrent duplicate insert、NO_POSITION/SIMULATED DB CHECK。

**Evidence Required**: pre/post schema SQL、DB identity/count/hash、test report、rollback rehearsal、telemetry。

**Dependencies**: P0-01。

**Risk and Rollback**: migration damage；只在copy上开发，事务回滚，feature flag/read-view pin；不得drop。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待 P0-01。

---

## P0-03-GAMMA_CATALOG — Gamma catalog adapter

**Task ID**: `P0-03-GAMMA_CATALOG`

**Objective**: 将现有 Gamma client 输出转为 raw artifact、canonical identity和immutable MarketSnapshot revisions。

**Parent Design References**: ADR-003/005；mapping 4.2。

**Current Repository Context**: `src/platform/clients/gamma.py`、`src/models/market.py`及其现有fixtures；复用，不复制HTTP client。

**Owned Scope**: `src/polymarket_alpha/adapters/gamma*`、`census/catalog*`和adapter tests。

**Explicitly Out of Scope**: new collector service、book fetching、rule decisions、production scheduling。

**Inputs**: P0-01/02 APIs；frozen Gamma fixtures；offline sandbox。

**Required Outputs**: pagination adapter、raw capture、event-market/token/alias mapping、revision writer、schema-drift receipt、telemetry。

**Acceptance Criteria**: outcome reorder不破坏YES/NO；title不参与主键；同输入幂等；变化保留旧revision；缺关键identity fail closed。

**Required Tests**: pagination termination、duplicate pages、missing/extra fields、token label permutations、one-char rule change；不得把live smoke作为offline完成条件。

**Evidence Required**: golden raw/normalized hashes、DB queries、offline sandbox receipt、tests、telemetry。

**Dependencies**: P0-01,P0-02。

**Risk and Rollback**: remote schema/rate drift；禁用adapter且保留raw/error receipt。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-04-CHANGE_DETECTOR — Change detector and event producer

**Task ID**: `P0-04-CHANGE_DETECTOR`

**Objective**: 对相邻MarketSnapshot revisions生成versioned change events；不把orderbook或capture demand作为所有recall的公共前置。

**Parent Design References**: ADR-006；P0 scenarios A03/A04。

**Current Repository Context**: 读取P0-03 revisions；book demand由P0-05唯一拥有；禁止使用旧generic feeder。

**Owned Scope**: `src/polymarket_alpha/census/change_detector*`及tests。

**Explicitly Out of Scope**: capture demand写入、socket/REST book连接、capture service config、candidate ranking。

**Inputs**: P0-03 revision stream。

**Required Outputs**: new/closed/lifecycle/rule/metadata change classifiers、append-only change events、version manifest、telemetry。

**Acceptance Criteria**: deterministic diffs；no-op revision不产生重复logical event；无book时events仍完整可消费。

**Required Tests**: all change types、dedupe/restart、cursor replay、closed/resolved/superseded events、no-book fixture。

**Evidence Required**: fixture matrix、event ids/hashes、DB/artifact diff、telemetry。

**Dependencies**: P0-03。

**Risk and Rollback**: event storm；feature flag关闭producer，catalog revisions与capture owner不变。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待 P0-03。

---

## P0-05-ORDERBOOK_ADAPTER — Orderbook adapter

**Task ID**: `P0-05-ORDERBOOK_ADAPTER`

**Objective**: 通过现有capture owner提交purpose-typed demand并从receipt/artifact生成paired YES/NO OrderbookSnapshot及target-size metrics。

**Parent Design References**: ADR-005/006；mapping 4.3。

**Current Repository Context**: `weather_data_feed_service/market_books*.py`、capture contract/demand/receipt、`ws_incremental_book.py`、CLOB client；只读复用owner surface。

**Owned Scope**: `src/polymarket_alpha/adapters/orderbook*`、book fixtures/tests。

**Explicitly Out of Scope**: 新socket/client owner、production restart/config、order execution。

**Inputs**: P0-01/02 contracts；P0-04 change events；accepted blind-result trigger contract；existing receipts/raw books。

**Required Outputs**: `SENSING`与`FORMAL_REVIEW` demand API、canonical token join、paired capture group、Decimal bid/ask/mid/spread/VWAP/depth、stale/one-sided/insufficient flags、telemetry。

**Acceptance Criteria**: no direct network connection in package；FORMAL_REVIEW仅接受accepted blind result id；两类purpose独立TTL/熔断；每个normalized field可定位raw hash/clock；缺腿不伪造mid。

**Required Tests**: purpose/trigger authorization、ladder sort、crossed/empty/one-sided、stale clocks、insufficient target depth、YES/NO permutation、import graph。

**Evidence Required**: paired golden fixtures、receipt links、metrics recomputation、no-direct-socket graph、telemetry。

**Dependencies**: P0-01,P0-02,P0-04。

**Risk and Rollback**: capture load/identity mismatch；关闭Alpha consumer/demand，不动owner。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-06A-RECALL_AGGREGATOR — Recall aggregation

**Task ID**: `P0-06A-RECALL_AGGREGATOR`

**Objective**: 实现provider-neutral RecallHit ingestion、dedupe、Candidate merge与late-hit refresh接口；任何单个provider或book不得成为公共前置。

**Parent Design References**: ADR-006/007/009；mapping capability matrix；revised DAG。

**Current Repository Context**: RecallHit schema归P0-01、repository归P0-02；本包不实现具体provider。

**Owned Scope**: `src/polymarket_alpha/recall/{registry,aggregate,merge}*`及tests。

**Explicitly Out of Scope**: provider algorithms、shared migrations、Rule A/B、ranker、books、orders。

**Inputs**: P0-01 RecallHit/Candidate contracts；P0-02 repository API；provider fixtures。

**Required Outputs**: provider registry、stable dedupe、Candidate merge/refresh、late-hit impact classification、telemetry。

**Acceptance Criteria**: zero/one/many providers均可运行；无book fixture时pre-book hits生成Candidate；late material hit产生RESEARCH_REFRESH_REQUIRED而非覆盖frozen research。

**Required Tests**: provider missing/disabled、multi-hit merge、duplicate/restart、late hit before/after blind freeze、projection rebuild。

**Evidence Required**: event/query fixtures、input/output hashes、provider-isolation proof、tests、telemetry。

**Dependencies**: P0-01,P0-02。

**Risk and Rollback**: merge语义污染；停aggregator writer，保留RecallHit并从events重建。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-06B-PREBOOK_STRUCTURAL — New/Changed and structural recall

**Task ID**: `P0-06B-PREBOOK_STRUCTURAL`

**Objective**: 从catalog/change/metadata/market-family关系产生pre-book RecallHit，不依赖订单簿且不声称fair-value gap。

**Parent Design References**: ADR-006；design pack 01/03；revised mapping/DAG。

**Current Repository Context**: 使用P0-03/04 public outputs；legacy feature fragments只作参考。

**Owned Scope**: `src/polymarket_alpha/recall/{new_changed,structural_metadata}*`及tests。

**Explicitly Out of Scope**: book、wallet、dispute、probability/fair-value model、Candidate repository、orders。

**Inputs**: MarketSnapshot revisions、change events、family/threshold metadata、P0-06A provider interface。

**Required Outputs**: NEW_CHANGED与STRUCTURAL_METADATA RecallHit providers、reason/features/version、telemetry。

**Acceptance Criteria**: no-book输入仍稳定命中；只描述新/变/截止/家族逻辑异常，不输出“mispriced”或fair value。

**Required Tests**: new/closed/rule/family/threshold fixtures、no-book test、determinism、reason-code allowlist。

**Evidence Required**: fixture matrix、RecallHit hashes、forbidden fair-value assertion scan、tests、telemetry。

**Dependencies**: P0-03,P0-04,P0-06A。

**Risk and Rollback**: provider过宽；按provider version禁用，其他routes不受影响。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-06C-CONTROVERSY_RECALL — Controversy/dispute recall

**Task ID**: `P0-06C-CONTROVERSY_RECALL`

**Objective**: 将canonical dispute/corpus中的PIT争议与规则先例适配为Controversy RecallHit，不复制domain mart。

**Parent Design References**: ADR-004/005/006；REF-DISPUTE-001。

**Current Repository Context**: canonical JRS dispute DB和contract corpus为read-only source；绝对identity必须核实。

**Owned Scope**: `src/polymarket_alpha/recall/controversy*`和dispute adapter tests。

**Explicitly Out of Scope**: dispute migration、RuleContractCompiler、Candidate schema、book、orders。

**Inputs**: P0-03 identity、dispute revisions/hashes、P0-06A interface。

**Required Outputs**: PIT controversy provider、source/revision mapping、freshness/completeness状态、telemetry。

**Acceptance Criteria**: 每个hit可追溯absolute DB identity/corpus hash/source offsets；无dispute source时只跳过本provider。

**Required Tests**: canonical identity mismatch、PIT cutoff、duplicate cases、missing source、determinism。

**Evidence Required**: source identity、fixture outputs/hashes、no copied-table proof、tests、telemetry。

**Dependencies**: P0-03,P0-06A。

**Risk and Rollback**: 相对路径/后见污染；禁用provider，保留source refs。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-06D-WALLET_RECALL — Specialist wallet recall

**Task ID**: `P0-06D-WALLET_RECALL`

**Objective**: 将wallet source facts适配为freshness-gated specialist RecallHit，保持Address!=Entity且不向Blind泄漏wallet交易方向。

**Parent Design References**: ADR-007/008；REF-WALLET-001。

**Current Repository Context**: smart_wallets、wallets.db、wallet-weather artifacts均为read-only source；已知数据陈旧48–79天。

**Owned Scope**: `src/polymarket_alpha/recall/wallet*`和wallet adapter tests。

**Explicitly Out of Scope**: wallet tracker、live status、copy-trade execution、Candidate schema、Blind packet implementation。

**Inputs**: P0-03 identity、wallet source adapters、freshness policy、P0-06A interface。

**Required Outputs**: specialist provider、address/entity provenance、freshness/incomplete flags、direction-sensitive private features、telemetry。

**Acceptance Criteria**: stale source不生成current hit；wallet只召回；private trade direction不进入BlindCandidateProjection surface。

**Required Tests**: staleness boundaries、address/entity aliases、pagination truncation、direction redaction handoff、no execution imports。

**Evidence Required**: provenance/freshness queries、private/public feature diff、tests、telemetry。

**Dependencies**: P0-03,P0-06A。

**Risk and Rollback**: stale/directional anchoring；禁用provider，历史source不变。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-06E-BOOK_ANOMALY — Optional book structural anomaly recall

**Task ID**: `P0-06E-BOOK_ANOMALY`

**Objective**: 从SENSING paired books产生可选结构异动RecallHit；只描述价格区间、spread/depth、阈值单调性和family consistency，不估fair value。

**Parent Design References**: ADR-006；mapping Orderbook/Structural Recall；revised DAG。

**Current Repository Context**: 只消费P0-05 normalized book；不得连接capture/API。

**Owned Scope**: `src/polymarket_alpha/recall/book_anomaly*`及tests。

**Explicitly Out of Scope**: capture demand/connection、probability model、fair-value gap、Candidate repository、orders。

**Inputs**: P0-05 SENSING snapshots、family relations、P0-06A interface。

**Required Outputs**: book structural provider、typed insufficient/stale/one-sided states、reason/features/version、telemetry。

**Acceptance Criteria**: missing/stale book只跳过或标本route；不阻断其他recall；输出不使用“mispriced”或fair-value assertion。

**Required Tests**: no-book/stale/one-sided/insufficient、spread/depth、monotonic/family consistency、determinism。

**Evidence Required**: coverage denominator、fixture outputs、provider-isolation proof、tests、telemetry。

**Dependencies**: P0-05,P0-06A。

**Risk and Rollback**: price anomaly被误解为alpha；禁用provider，其他routes不受影响。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-07-RULE_GATES — RuleContractCompiler and gates

**Task ID**: `P0-07-RULE_GATES`

**Objective**: 适配现有parser/audit/dispute corpus，形成唯一RuleContractCompiler及Gate A/B。

**Parent Design References**: ADR-004；design pack 04。

**Current Repository Context**: rule_lawyer parser/adapter、dispute contract corpus、clarification court；保留domain adapters，不复制mart。

**Owned Scope**: `src/polymarket_alpha/rules/**`和rule fixtures/tests。

**Explicitly Out of Scope**: alternate rule service、market packet、DB migration、auto-order。

**Inputs**: P0-01/02 APIs；P0-03 rule revisions；read-only canonical dispute identity。

**Required Outputs**: normalized rule hash、typed contract、compiler revision id、Gate A/B decisions、same-hash invariant、telemetry。

**Acceptance Criteria**: unparseable/ambiguous fail closed；corpus hash不覆盖rule hash；B mismatch blocks；quotes可定位source offsets/hashes。

**Required Tests**: rule corpus variants、timezone/deadline/threshold、initial/final/source precedence、A/B mismatch、backend unavailable。

**Evidence Required**: golden RuleContracts/hashes、absolute dispute DB identity、gate traces、tests、telemetry。

**Dependencies**: P0-01,P0-02,P0-03。

**Risk and Rollback**: parser nondeterminism/version drift；pin compiler/prompt/corpus版本并回退旧reader，不签发新Gate。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-08-PACKET_ROUNDTRIP — Research packet roundtrip

**Task ID**: `P0-08-PACKET_ROUNDTRIP`

**Objective**: 实现BlindCandidateProjection、Blind/Market packet导出、人工GPT Pro claim-level结果导入、语义隔离、冻结与兼容性校验。

**Parent Design References**: ADR-008；design pack 04/05/08。

**Current Repository Context**: market reporting/market intel artifacts和clarification packet仅作adapter素材；不自动调用外部模型。

**Owned Scope**: `src/polymarket_alpha/research/**`、filesystem fixtures/tests。

**Explicitly Out of Scope**: external API automation、ranker、rule compiler修改、UI。

**Inputs**: P0-01 projection/packet/claim schemas、P0-05 FORMAL_REVIEW book、P0-06A Candidate、P0-07 Gate A；manual inbox/outbox root。

**Required Outputs**: allowlist-onlyBlindCandidateProjection、blind/market builders、Blind source-domain deny policy、canonical JSON+MD、claim-level EvidenceItem importer、freeze manifest、quarantine/error receipts、telemetry。

**Acceptance Criteria**: Blind projection不含market id/slug/URL/candidate side/wallet direction/price-derived reason/book/price；禁止Polymarket页面/API/镜像来源；Market仅在accepted blind result后提交FORMAL_REVIEW demand并构建；每个claim可PIT重放；违规/tamper/version mismatch拒绝且不推进状态。

**Required Tests**: price/probability/bid/ask/slug/URL/Yes-No direction/wallet hint/price-reason嵌入任意question或嵌套字符串的adversarial fixtures；Polymarket source拒绝；claim source_artifact/capture_scope/hash_scope及importer重算hash；REFERENCE_ONLY replay限制；roundtrip、duplicate/partial/wrong version/hash、atomic rename。

**Evidence Required**: Candidate→projection private/public diff、packet/result/claim/source hashes、allowlist/deny-policy proof、import receipts、tests、telemetry。

**Dependencies**: P0-01,P0-05,P0-06A,P0-07。

**Risk and Rollback**: human file error/non-determinism；quarantine并停止importer，冻结原文件。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-09-DECISION_LEDGER — Candidate, ranker and ledger

**Task ID**: `P0-09-DECISION_LEDGER`

**Objective**: 实现append-only Candidate首次研究与持续失效/刷新transitions、deterministic ranker、ReviewDecision和PredictionRecord ledger。

**Parent Design References**: ADR-009；protocol order；design pack 03/04/05。

**Current Repository Context**: 禁止复用wallet/copy-trade/live statuses；使用P0-02 repository、P0-06A～E/07/08 outputs。

**Owned Scope**: `src/polymarket_alpha/decision/**`、candidate projection和tests；不改migrations。

**Explicitly Out of Scope**: execution、position sync、harness job queue、complex portfolio sizing。

**Inputs**: Recall/Candidate、Gate A/B、Blind/Market results、book snapshot、versioned rank config。

**Required Outputs**: transition/invalidation matrix、projection、refresh impact handler、ranker、decision/prediction writers、watchlist query API、telemetry。

**Acceptance Criteria**: no skipped protocol states；rule/evidence/recall/book/lifecycle变化产生显式事件；旧frozen artifact不驱动新decision；same input deterministic；position onlyNO_POSITION/SIMULATED。

**Required Tests**: full initial+invalidation transition table、invalid edges、late RecallHit、stale evidence/book、rule revision、closed/resolved/superseded、rank golden、idempotency/concurrency、projection rebuild。

**Evidence Required**: event/projection reconciliation、golden scores、DB uniqueness/CHECK outputs、tests、telemetry。

**Dependencies**: P0-02,P0-06A,P0-07,P0-08；P0-12集成P0-06B～E全部provider。

**Risk and Rollback**: state corruption/score drift；stop writer，rebuild projection from events，pin rank config。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-10-EVIDENCE_SEAL — WorkOrder and evidence seal

**Task ID**: `P0-10-EVIDENCE_SEAL`

**Objective**: 让现有weather agent harness承载统一task contract，并生成设计包要求的evidence seal。

**Parent Design References**: ADR-010；design pack 06/07/08；P0 plan第8节。

**Current Repository Context**: `src/weather_agent_harness/orchestration/**`、`evidence.py`和已通过的harness tests。

**Owned Scope**: Alpha-to-harness adapter、seal manifest/receipt layout、tests；harness core更改必须最小且后向兼容。

**Explicitly Out of Scope**: candidate state、research DB migrations、new scheduler、subagent tree。

**Inputs**: P0-01 task/evidence contracts；P0-09 completion artifacts。

**Required Outputs**: WorkOrder payload adapter、DAG dependency import、evidence chain/seal/receipt、verification CLI、explicit readiness scope（offline/read-only/production）、telemetry。

**Acceptance Criteria**: lease/owner/idempotence继续pass；manifest hash可独立验证；task自报完成不能绕过certification；offline seal不能声明read-only operational/production readiness。

**Required Tests**: existing harness regression、tampered/missing evidence、DAG cycle/owner overlap、receipt replay。

**Evidence Required**: sample sealed run、independent verify output、legacy regression、telemetry。

**Dependencies**: P0-01,P0-09。

**Risk and Rollback**: governance coupling；adapter关闭后用sequential runner，domain artifacts不变。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-11-NO_LIVE — No-live security proof

**Task ID**: `P0-11-NO_LIVE`

**Objective**: 从Wave 0建立capability-based sandbox，并持续证明Alpha P0无法通过已知模块、通用网络、动态import、subprocess或shell下单、签名或接触私钥。

**Parent Design References**: ADR-011；design pack frozen no-auto-trade boundary。

**Current Repository Context**: real `auto_order.py`、copy-trade executor和platform execution存在，须按可达性而非仓库存在性验证。

**Owned Scope**: Alpha process sandbox policy、`AlphaReadOnlyTransport`、host/path/method与env allowlists、AST/import policy、security tests、canary/sentinel、CLI surface audit；可阻断任何P0 merge。

**Explicitly Out of Scope**: 删除/修改legacy execution、exchange smoke、真实凭证测试。

**Inputs**: P0 package import graph、P0-01 transport/security contracts、P0-02 DB checks、各Wave entrypoints、P0-09 final entrypoints。

**Required Outputs**: offline network-deny profile、versionedread-only endpoint/env allowlists、transport facade、AST/import graph checker、generic transport/websocket/importlib/subprocess/shell guards、sentinel order/signing client、canary receipts、telemetry。

**Acceptance Criteria**: offline profile没有网络；read-only profile默认拒绝且只允许精确host/path/method（包括逐endpoint只读POST）；requests/httpx/aiohttp/websocket/importlib/subprocess/shell绕过失败；Authorization/private-key material不可达；position DB check生效。

**Required Tests**: injected banned/dynamic imports、requests/httpx/aiohttp/raw websocket/socket、subprocess/os.system/shell旧CLI、redirect chain、proxy env、encoded/noncanonical path、unexpected query/body/header、Authorization/Cookie/signature/key env、DNS/connect target、monkeypatched order/sign attempt、allowed publicGET/precise POST-books canary、CLI/config/DB checks。

**Evidence Required**: Wave-0 policy、每merge subset receipts、final import/capability graph、canary/sentinel traces、endpoint/env snapshots、tests、telemetry。

**Dependencies**: Wave-0 policy/denylist/CI/adversarial scaffold与P0-01并行；concrete transport/receipt/env-endpoint artifact schema等待P0-01 release；final proof依赖P0-02,P0-09；每个Wave merge均依赖其subset PASS。

**Risk and Rollback**: hidden egress/dynamic process capability；发现即disable整个Alpha entrypoint并Gate fail，不降级继续运行。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

---

## P0-12-INTEGRATION_PILOT — Integration pilot

**Task ID**: `P0-12-INTEGRATION_PILOT`

**Objective**: coordinator在固定fixtures上集成P0-01..11，执行A01-A18、provider isolation、回滚演练和完整证据封存；只签offline readiness。

**Parent Design References**: 全部八份设计包文档；本目录全部产物。

**Current Repository Context**: 使用单次repo/runtime snapshot；复用各task outputs，不重复全仓发现。

**Owned Scope**: offline integration tests/fixtures、offline operator CLI/runbook、final seal；跨模块修改回到sole owner审查。

**Explicitly Out of Scope**: production config、current runtime DB migration（除非另有明确rollout授权）、live orders、external model automation。

**Inputs**: 所有依赖包的sealed artifacts和telemetry；fixed raw fixtures；read-only external smoke只能作为后续独立operational gate证据。

**Required Outputs**: A01-A18 report、legacy regression、rollback rehearsal、sample watchlist/prediction ledger、offline seal、readiness scope声明、known limitations、aggregate telemetry。

**Acceptance Criteria**: every scenario PASS；无book仍形成pre-book Candidate；semantic Blind和capability no-live gates PASS；replay hashes stable；只声明OFFLINE_IMPLEMENTATION完成，不声明daily operation。

**Required Tests**: full offline E2E、each-provider disabled、restart/replay、invalidation、migration rollback、adapter disable、semantic tamper、capability bypass、targeted legacy regression。

**Evidence Required**: complete seal layout、commands/results、source identities、DB manifests、hashes、screens/export、worker telemetry review。

**Dependencies**: P0-03 through P0-11（含P0-06A～E）all `COMPLETE`；不接受削弱Blind/no-live/provider-isolation gate的limitations。

**Risk and Rollback**: integration reveals contract break；不得patch around owner，退回对应owner/version，保留failed run seal。

**Completion Status**: `BLOCKED_BY_DEPENDENCY` — 等待前置任务。

## Coordinator review checklist

- 对每包核验 owned files，没有越界migration/collector/orchestrator/execution。
- 比较实际 model/effort/usage telemetry；异常用量立即熔断，不续用同一worker。
- 重跑而非只接受worker声称的关键 contract/migration/no-live tests。
- 对输出hash、DB identity、absolute source path做独立复核。
- 只有P0-12 coordinator可以签offline implementation completion；worker的`COMPLETE`不等于系统完成，offline completion也不等于read-only operational pilot ready。
