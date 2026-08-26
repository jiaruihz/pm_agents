# Current-to-Target Mapping

## 1. 集成原则

目标架构落在现有 Python 单体和 SQLite/file artifact 基础上。P0 不拆微服务，不增加第二套 collector、wallet tracker、rule engine 或 agent orchestrator。新能力以 `alpha_*` 合同、adapter 和 append-only revision 接入；生产 weather、wallet history、dispute mart 保持物理隔离。

## 2. 能力映射矩阵

| Target capability | Current asset | 决策 | P0 集成形态 | Owner |
|---|---|---|---|---|
| Market Census | Gamma client + market models + rule_lawyer catalog sync | ADAPT/REFACTOR | `AlphaCatalogAdapter` 写 immutable MarketSnapshot revision | Market Data |
| Change Detector | catalog cursor/current-row behavior | BUILD over REFACTOR | 比较相邻 revision 的 lifecycle/rule_hash/price-volume hints；只发 capture demand | Market Data |
| Orderbook Census | `weather_market_books` + capture demand/receipt + REST/WS rebuild | KEEP/ADAPT | 唯一 owner 接收 Alpha demand；P0 消费 receipt/cache，不直连另采 | Market Data/Operations |
| Structural Market Anomaly Recall | 无统一实现；有 market-family/book feature fragments | BUILD | 只识别结构异动，不估 fair value；book-dependent side route | Recall |
| New/Changed Market Recaller | catalog revisions | BUILD | change event → RecallHit | Recall |
| Controversy Recaller | dispute corpus/case evidence + rule audit | ADAPT | domain adapter 输出 RecallHit，不复制 dispute mart | Recall/Dispute |
| Specialist Wallet Recaller | smart_wallets + wallet_weather artifacts | ADAPT | freshness-gated historical/current source → RecallHit only | Wallet Recall |
| Candidate aggregation | 无 | BUILD | pre-book recall可直接建Candidate；book recall为可选异步补充；append-only transitions | Research Core |
| Rule Lawyer A | parser + audit adapter | ADAPT | shared `RuleContractCompiler` Gate A | Rules |
| BlindCandidateProjection | 无 | BUILD | allowlist-only neutral proposition/evidence projection；隔离slug/URL/side/price-derived recall semantics | Research |
| Blind Research Packet | reporting/market-intel artifacts | REFACTOR | 只从BlindCandidateProjection构建；schema-versioned JSON+MD；freeze SHA | Research |
| GPT Pro blind result ingest | 无 | BUILD | manual filesystem outbox/inbox validator | Research |
| Market Packet | CLOB book adapter + frozen blind result | BUILD | only after blind result accepted; append prices/VWAP/depth | Research |
| GPT Pro market result ingest | 无 | BUILD | manual filesystem result validator | Research |
| Rule Lawyer B | clarification court only covers dispute subset | ADAPT/BUILD | same compiler/hash; domain sub-adapters; pass/risk/block | Rules |
| Basic Ranker | legacy scoring fragments | REFACTOR | deterministic versioned rank from Rule/Evidence/Probability/Book | Decision |
| Watchlist | UI patterns exist, no Alpha ledger | ADAPT after BUILD | read-only CLI/JSON first; optional minimal API view | Product |
| Prediction Ledger | 无 | BUILD | append-only PredictionRecord and decision/event audit；预留P1 resolution/dispute/calibration linkage | Decision |
| Task orchestration | weather agent harness | ADAPT | design task contract in WorkOrder payload; no business-state ownership | Governance |
| Evidence seal | harness EvidenceStore/receipt | KEEP/ADAPT | emit design-pack manifest, hashes, tests and rollback evidence | Governance |
| Live execution | real auto-order/copy-trade code exists | ARCHIVE from P0 | capability sandbox + read-only transport；only `NO_POSITION`/`SIMULATED` states | Security |

## 3. 目标 P0 数据流

```text
Gamma API
  -> existing GammaClient
  -> alpha_market_snapshot_revision + alpha_market_identity
  -> alpha_change_event
  -> pre-book recall routes
       new/changed + controversy + specialist-wallet + metadata/family anomaly
  -> alpha_recall_hit
  -> alpha_candidate + alpha_candidate_transition       # book不是公共前置
  -> RuleContractCompiler / Gate A
  -> BlindCandidateProjection                           # allowlist-only
  -> Blind ResearchPacket + manual GPT Pro result       # frozen sha256
  -> FORMAL_REVIEW book demand only after blind result accepted
  -> existing single orderbook owner via demand/receipt
  -> Market ResearchPacket (paired YES/NO book + metrics)
  -> manual GPT Pro market result import
  -> RuleContractCompiler / Gate B using same rule_hash
  -> deterministic ranker
  -> alpha_review_decision
  -> alpha_prediction_record (NO_POSITION or SIMULATED only)
  -> read-only watchlist/export + evidence seal
```

Side inputs:

```text
dispute.db --revision/hash adapter--> Rule/Evidence/Controversy Recall
wallets.db + wallet_weather artifacts --freshness adapter--> Wallet Recall
weather agent harness --WorkOrder/EvidenceStore--> task/evidence governance only

optional sensing side route:
alpha_change_event -> SENSING book demand -> existing book owner
  -> Structural Market Anomaly Recall -> RecallHit -> Candidate merge/refresh
```

`SENSING` book demand只服务结构异动召回，可以晚到或缺失；它不阻断其他recall和Candidate。`FORMAL_REVIEW` demand必须由accepted blind result触发，生成Market Packet所需的新鲜paired book。两种demand共享同一capture owner和合同，但purpose、trigger、TTL、target sizes与receipt lineage必须分开。

## 4. 合同字段映射

### 4.1 Common envelope

| Target field | Current source | Mapping/conflict |
|---|---|---|
| `schema_version` | scattered model/module versions | 必须成为每个 Alpha artifact 的必填字段 |
| `artifact_id` | capture ids/stable ids in several modules | 使用 type + canonical identity + revision/input hashes 生成；禁止各模块私造不兼容算法 |
| `run_id` | runs/harness work order | 统一引用 harness WorkOrder/run；同一 scan/review 可关联多个 artifact |
| `created_at` | 多数模块有时间 | UTC RFC3339；不能替代 source observed clock |
| `source_observed_at` | capture contract 有双时钟，旧 catalog/wallet 不齐 | 无法证明时 fail closed 或标 historical；不得填当前时间冒充 |
| `producer`/`producer_version` | module/prompt version 零散 | 从 build SHA + component config hash 形成 |
| `content_sha256` | capture/evidence/dispute 已有 | 保留；对 canonical serialized payload 计算 |

### 4.2 Market identity and snapshot

| Target | Current | Decision |
|---|---|---|
| `event_id` | Gamma event array/slug | 建 `alpha_event_market` join；不从 title 推断 |
| `market_id` | Gamma market id | primary canonical id |
| `condition_id` | 部分 scripts/models | unique alternate id；缺失时显式 null/pending |
| `yes_token_id`/`no_token_id` | token arrays/outcome labels | 按规范化 outcome label 校验后映射；绝不假定 index 0/1 |
| aliases/slugs | 各脚本各用 slug | `alpha_market_alias` 带 source/effective interval |
| raw rules | model/storage current row | raw revision append-only保留 |
| normalized rules | 无统一合同 | 仅 Unicode NFC、换行和非语义尾空白标准化 |
| `rule_hash` | 缺失；dispute 有 corpus hash | SHA-256(normalized raw rule)，与 corpus hash 分列 |
| lifecycle/times | Gamma model 部分有 | 保留 source raw value、normalized UTC 和 observed clock |

### 4.3 OrderbookSnapshot

| Target | Current | Decision |
|---|---|---|
| paired YES/NO book | token-level REST/WS rows | 由 canonical token map join；同一 capture group 封装 |
| bid/ask/spread/mid | REST/WS 可得 | Decimal 计算；缺边时不可伪造 mid |
| VWAP/depth | WS rebuild 部分支持 requested shares | 配置化 target sizes；同时保存 insufficient-depth flag |
| clocks/provenance | strict capture contract | 直接保留 source/ingest clocks、raw hash、receipt |
| staleness | 当前调用点不统一 | 由 config version 定义；过期/单边结果阻断 market review |

### 4.4 RuleContract

| Target | Current | Decision |
|---|---|---|
| entity/trigger/deadline/timezone/examples/ambiguities/clarity | parser/audit 可部分提供 | 通过一个 compiler facade 适配 |
| typed threshold | 字符串/启发式 | 新 typed union；解析失败进入 `NEEDS_RULE_REVIEW` |
| resolution sources/precedence | dispute corpus 较强，generic 较弱 | generic rule + optional domain corpus composition |
| initial/final semantics | dispute corpus 部分可表达 | 成为显式字段和 Gate 条件 |
| `rule_hash` | 无 | 必须绑定 MarketSnapshot revision |
| `contract_revision_id` | corpus sha 可用作 dispute 维度 | 由 market/rule/parser/corpus hashes 组合；不覆盖任一原 hash |
| Gate A/B | workflow 混合 | 相同 compiler core、相同 rule_hash；A 在 blind 前，B 在 market review 后 |

### 4.5 Recall, Candidate, Research, Decision

| Target | Current | Decision |
|---|---|---|
| RecallHit | WalletSignal/SignalCandidate/各类 JSON 不兼容 | 新共享合同；adapter 只负责转换，原 artifact 保留引用 |
| CandidateCard | 无统一实现 | 新 aggregate；一个 market 可有多个 RecallHit |
| Candidate transitions | legacy statuses 不一致且含 live | 新 append-only `from/to/reason/actor/input_hash`；增加`RECALL_EXPIRED`、`EVIDENCE_STALE`、`RESEARCH_REFRESH_REQUIRED`、`RULE_REVISION_INVALIDATED`、`BOOK_REFRESH_REQUIRED`、`MARKET_CLOSED`、`RESOLVED`、`SUPERSEDED`、`ARCHIVED`；不复用legacy status |
| EvidenceItem | market-intel/dispute/harness evidence 不同 | 业务claim-level EvidenceItem与治理EvidenceStore分层；每项含claim、支持方向、source tier/id/url、PIT times、定位、confidence、immutable source_artifact_id、capture_scope、hash_scope、recomputed content hash和replayability |
| BlindCandidateProjection | 无 | allowlist-only：规则正文、neutral proposition、实体、截止时间、受控BlindResearchQuestion、非盘口证据；使用blind id，不含market id/slug/URL、candidate side、任何wallet-derived payload、price-derived reason/features或book字段；递归扫描嵌套字符串；source policy禁止Polymarket页面/API/镜像 |
| ResearchPacket | 无 | 新 Blind/Market discriminated union；Blind只能引用BlindCandidateProjection；Market仅在blind result accepted后引用fresh paired book |
| ProbabilityEstimate | 零散 | 保存 estimate、range、assumptions、calibration/version；Blind 与 Market 分开 |
| ReviewDecision | clarification verdict/legacy reviews | 新统一 decision；引用所有 input hashes |
| PredictionRecord | 无 | 新 append-only ledger；幂等键覆盖 market/decision/model/version/as-of |

## 5. 存储拓扑

| Store | P0 ownership | 写入规则 |
|---|---|---|
| `runtime/db/research.db` | Alpha Research Core owns new `alpha_*` migrations | additive only；legacy `copy_trade_*` 不改写；单一 migration owner |
| JRS raw market-book artifacts | existing market-book service owns | P0 仅提交 demand、读取 receipt/artifact |
| `runtime/wallets.db` | historical wallet system owns | P0 read-only；adapter 输出带 provenance/freshness 的 RecallHit |
| canonical JRS `dispute.db` | dispute mart owns | P0 read-only adapter；按绝对 identity 连接 |
| canonical JRS `weather.db` | weather runtime owns | Alpha P0 不增表、不写入 |
| harness artifact/evidence root | harness owns | 保存 WorkOrder、tests、manifest、seal；不保存候选 authoritative state |

## 6. 重复实现消除

- 不新建 CLOB websocket/REST collector；旧 `market_ws.py` 被现有 strict capture owner 取代。
- 不把 `wallets.db`、`copy_trade_*` 和 wallet-weather artifacts 合并成第三个 tracker；只做 read-only identity/provenance adapters。
- 不复制 dispute corpus 为 generic rule tables；RuleContract 保存 revision references。
- 不新建 agent scheduler；harness 保持唯一 task owner，Alpha DB 只管理业务状态。
- 不沿用三个不同 stable-id 算法；共享合同包提供 canonical serializer/id factory。
- 不创建新的 execution layer；P0 dependency graph 明确排除所有 order/signing modules。
- P0 offline进程无网络；read-only pilot只能经`AlphaReadOnlyTransport`访问精确host/path/method allowlist，禁止adapter直接使用通用HTTP/socket/subprocess。

## 7. Target module layout（实现建议，不是本轮实现）

```text
src/polymarket_alpha/
  contracts/          # shared envelope, ids, validators
  storage/            # one alpha_* migration owner/repositories
  adapters/           # gamma, books, dispute, wallet, harness
  census/             # catalog revision and change detector
  recall/             # pre-book routes, optional book anomaly route, aggregator
  rules/              # RuleContractCompiler facade and A/B gates
  research/           # packet build/export/import
  decision/           # ranker, review, prediction ledger
  watchlist/          # read-only CLI/export
```

目录是单体内部模块边界，不代表微服务边界。
