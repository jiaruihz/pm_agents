# P0 Implementation Plan

## 1. 目标与边界

P0 交付一条可审计、可重放、无自动交易能力的研究最短路径。pre-book recall不等待orderbook；book既可作为可选异步召回来源，也在blind result接受后作为正式市场审阅输入：

```text
Census -> Change -> Pre-book Recall -> Candidate -> Rule A
-> BlindCandidateProjection -> Blind packet/result
-> fresh FORMAL_REVIEW Book snapshot -> Market packet/result
-> Rule B -> Rank -> Watchlist/Prediction Ledger

optional: Change -> SENSING Book -> Structural Market Anomaly Recall -> Candidate merge/refresh
```

P0 不交付：自动下单、真实 position、复杂前端、微服务拆分、全网钱包实时 tracker、第二套 market collector、自动 GPT Pro API、现有生产服务启停或参数变更。

## 2. 实施批次

### 2.1 Current status (2026-08-27)

P0-01 core、P0-02 core、P0-03、P0-06A、P0-07以及P0-11
Wave-0/concrete offline policy已有实现证据；P0仍未完成。P0-04/05、
P0-06B～E、P0-08/09/10/12和P0-11 final仍在剩余DAG中。后续唯一当前状态源
为`P0_REMAINING_WORK_BREAKDOWN.md`。

| Batch | 范围 | Entry criteria | Exit evidence |
|---|---|---|---|
| P0.0 | contracts、migration、capability sandbox scaffold | 本设计 Gate 1/2 PASS | schema snapshot、contract fixtures、offline network-deny/import graph |
| P0.1 | catalog/change、pre-book recall、candidate、Rule A；book side route可并行 | P0.0 pass | 无book仍可形成candidate、deterministic replay、Rule A fixtures |
| P0.2 | Blind projection/roundtrip、formal-review book、Rule B、ledger、seal | P0.1 pass | 端到端fixture、semantic leakage、capability bypass、failure scenarios |

## 3. 任务总表

| Task | Deliverable | Depends on | Sole owner | Acceptance evidence | Rollback |
|---|---|---|---|---|---|
| P0-01 | shared Pydantic contracts、canonical serializer/id factory | none | Shared Contracts | golden JSON、schema compatibility、hash determinism | pin previous schema package |
| P0-02 | additive `alpha_*` migrations/repositories | P0-01 | Alpha Storage | empty/legacy DB migrate、idempotence、pre/post manifest | feature flag/read-view rollback; no drop |
| P0-03 | Gamma catalog/raw/revision/identity adapter | P0-01,P0-02 | Market Data Catalog | offline fixtures、pagination/schema drift tests | disable adapter; raw retained |
| P0-04 | revision change detector/event producer | P0-03 | Market Change | new/closed/rule/lifecycle diff fixtures；不把book作为公共前置 | stop producer; revisions intact |
| P0-05 | existing-owner orderbook demand/receipt adapter；区分SENSING/FORMAL_REVIEW | P0-01,P0-02,P0-04 | Market Book Adapter | precise purpose/trigger、no direct socket、paired book/stale/depth tests | remove Alpha consumer/demands |
| P0-06A | RecallHit consumer/aggregator + Candidate merge/refresh | P0-01,P0-02 | Recall Aggregation | zero/one/many providers；无book fixture仍可建candidate | disable aggregator writer; hits retained |
| P0-06B | New/Changed + metadata/market-family structural recall | P0-03,P0-04,P0-06A | Pre-book Structural Recall | no book dependency、no fair-value claim、deterministic hits | disable provider version |
| P0-06C | Controversy/dispute recall adapter | P0-03,P0-06A | Controversy Recall | absolute source identity、PIT revision、no copied mart | disable provider version |
| P0-06D | Specialist wallet recall adapter | P0-03,P0-06A | Wallet Recall | stale/address-entity/direction isolation | disable provider version |
| P0-06E | Optional book structural anomaly recall | P0-05,P0-06A | Book Anomaly Recall | missing book skips only this route；spread/depth/family consistency only | disable provider version |
| P0-07 | RuleContractCompiler and Gate A/B | P0-01,P0-02,P0-03 | Rules | rule revision/hash fixtures、A/B same-hash/mismatch block | revert compiler version; records retained |
| P0-08 | BlindCandidateProjection、Blind/Market packet、claim-level result import | P0-01,P0-05,P0-06A,P0-07 | Research Packet | semantic allowlist、source deny policy、freeze hash、roundtrip/idempotence | stop importer; files retained |
| P0-09 | candidate invalidation/refresh state machine、ranker、decision/prediction ledger | P0-02,P0-06A,P0-07,P0-08 | Decision Ledger | transition/invalidation matrix、idempotence、NO_POSITION/SIMULATED check；P0-12再集成P0-06B～E | read prior projection/version |
| P0-10 | harness WorkOrder + evidence-seal adapter | P0-01,P0-09 | Governance | DAG/lease/hash-chain/seal verify | sequential runner; domain state intact |
| P0-11 | capability-based no-live sandbox；Wave 0 policy + per-merge subset + final proof | P0-01；final depends P0-02,P0-09 | Security | offline network deny、endpoint/env allowlist、generic transport/importlib/subprocess bypass tests | disable Alpha entrypoint |
| P0-12 | end-to-end offline fixture pilot and operator runbook | P0-03..P0-11 | Integration | acceptance suite A01-A18、sealed run | preserve artifacts, reset feature flag |

任何一个 task 都不得自行修改另一 task owned 的 migration 或 shared contract；变更通过 owner review 合并。

## 4. Schema/migration sequence

1. 只读记录 `runtime/db/research.db` 的绝对路径、device/inode、size、schema、table counts 和 file hash。
2. 在临时复制库上运行 migration：建立 `alpha_schema_migrations`、identity、raw/revision、recall/candidate/transitions、rule、packet/result、decision/prediction 表。
3. 对 legacy `copy_trade_*` 做只读兼容检查；不 rename、不 drop、不把 `live` status 映射进 Alpha。
4. 空库、当前 legacy 库、重复 migration、故障中断四类测试必须通过。
5. 生成 schema SQL、foreign-key check、integrity check、table/index manifest 和 rollback read-view。
6. 合并实现不代表写 current runtime DB。真正对当前 research DB 执行 migration 需要在实施 task 的明确 rollout step 进行，先备份与 hash，再事务提交。

### Proposed `alpha_*` aggregates

```text
alpha_raw_artifact
alpha_event, alpha_market, alpha_event_market, alpha_market_alias
alpha_market_snapshot_revision, alpha_market_token_map, alpha_change_event
alpha_orderbook_snapshot, alpha_orderbook_leg
alpha_recall_hit
alpha_candidate, alpha_candidate_transition
alpha_rule_contract_revision, alpha_rule_gate_decision
alpha_research_packet, alpha_research_result, alpha_evidence_item
alpha_review_decision, alpha_prediction_record
alpha_run_artifact_link
```

表名是 logical proposal；P0-02 可在保持 aggregate/owner不变的前提下细化，但不得把 wallet/dispute/weather source facts复制进来。

## 5. Protocol implementation order

每个 candidate 的首次研究必须执行以下顺序，状态机拒绝跳步：

1. `RECALLED`
2. `CANDIDATE_MERGED`
3. `RULE_A_PASSED` 或 terminal `RULE_A_BLOCKED`
4. `BLIND_PROJECTION_FROZEN`、`BLIND_PACKET_FROZEN`
5. `BLIND_RESULT_ACCEPTED`
6. `BOOK_SNAPSHOT_ACCEPTED`
7. `MARKET_PACKET_FROZEN`
8. `MARKET_RESULT_ACCEPTED`
9. `RULE_B_PASSED/RISK/BLOCKED`
10. `RANKED`
11. `WATCHLISTED` 或 `REJECTED`
12. optional `SIMULATION_RECORDED`

`RULE_B_BLOCKED`、rule revision mismatch、stale/one-sided/insufficient book、result schema mismatch均fail closed。任何price/book字段、market id/slug/URL、candidate/wallet交易方向、price-derived reason，或Polymarket source出现在BlindCandidateProjection/packet/result，都是构建或导入失败，不是warning。

每日/增量运行通过显式append-only事件使既有candidate失效或刷新，不静默倒退current state：

```text
RECALL_EXPIRED
EVIDENCE_STALE
RESEARCH_REFRESH_REQUIRED
RULE_REVISION_INVALIDATED
BOOK_REFRESH_REQUIRED
MARKET_CLOSED
RESOLVED
SUPERSEDED
ARCHIVED
```

新RecallHit在`BLIND_PROJECTION_FROZEN`之后到达时，若其input hash会改变blind projection，则产生`RESEARCH_REFRESH_REQUIRED`并从新Gate A/Blind revision开始；旧packet/result保留但不得继续驱动decision。rule变化必须产生`RULE_REVISION_INVALIDATED`，不能只在代码中“返回重审”。

## 6. Acceptance scenarios and exact evidence

| ID | Scenario | Expected result | Required evidence |
|---|---|---|---|
| A01 | 同一 Gamma fixtures 重跑两次 | 相同 revisions/ids；无重复 logical rows | DB diff、artifact hashes、idempotence test |
| A02 | event含多 market、outcome顺序交换 | event join正确，YES/NO按 label映射 | golden identity fixture |
| A03 | rule文本变化一字符 | 新 revision/new rule_hash/change event；旧行保留 | before/after query、hashes |
| A04 | market closed/new/lifecycle change | 对应 versioned change event/RecallHit；closed产生显式lifecycle transition | change detector fixture |
| A05 | 无任何book fixture，new/changed、wallet或controversy命中 | 仍生成Candidate并进入Rule A；只跳过P0-06E | route-isolation integration test |
| A06 | stale wallet source | wallet recall拒绝或标 historical，不进入当前 candidate | freshness boundary test |
| A07 | Rule A ambiguous/unparseable | terminal block或needs review；不生成 Blind packet | transition/event log |
| A08 | Candidate含price-derived RecallHit、wallet trade side、slug/URL | BlindCandidateProjection仅保留allowlist字段；所有语义侧信道被剥离 | structural allowlist + adversarial fixture |
| A09 | blind result tampered/wrong version | import拒绝，状态不推进 | file hashes、error receipt、DB unchanged |
| A10 | paired book stale/one-sided/insufficient depth | market stage阻断或明确 risk；不伪造 mid/VWAP | book fixture + gate decision |
| A11 | A后rule revision改变 | B拒绝；candidate回到重审路径 | same-hash invariant test |
| A12 | result重复导入/runner重启 | ledger无重复；transition有幂等 receipt | DB counts/hashes |
| A13 | rank/watchlist/prediction写入 | deterministic score；position仅NO_POSITION/SIMULATED | golden ranking、DB CHECK test |
| A14 | 尝试通过已知order模块、requests/httpx/aiohttp/websocket、importlib、subprocess/shell绕过 | build/runtime立即失败；无未授权网络/子进程 | AST/import graph、sandbox/canary trace |
| A15 | Blind研究引用Polymarket页面/API/镜像 | source policy/import拒绝该claim/result，状态不推进 | domain denylist adversarial result |
| A16 | 同一candidate的Recall/Evidence/Rule/Book到期或变化 | 产生明确invalidation/refresh event；旧artifact保留且不可驱动新decision | projection replay + transition audit |
| A17 | GPT Pro返回多项事实主张 | 每项保存claim、支持方向、source tier/id/url、published/accessed/effective-as-of、primary/secondary、定位、confidence和source hash | claim-level evidence fixture/replay |
| A18 | read-only transport尝试未授权host/path/method、Authorization或private-key material | 全部fail closed；允许的公开GET或精确`POST /books`记录canary receipt | endpoint/env allowlist tests |

## 7. Test layers

| Layer | Required coverage |
|---|---|
| Unit | canonical serialization/id、Decimal/time、rule normalization、recaller purity、state transitions |
| Contract | every JSON schema version；BlindCandidateProjection allowlist；claim-level evidence；backward reader；reject unknown major |
| Migration | empty DB/current legacy DB/repeat/interruption/foreign-key/integrity |
| Adapter | Gamma/CLOB/wallet/dispute fixtures; schema drift and missing field |
| Protocol | A→Blind→Market→B ordering and forbidden transitions |
| Security | offline network deny；exact host/path/method allowlist；env allowlist；generic transport/websocket/importlib/subprocess/shell bypass；no auth/key；P0 CLI surface |
| Replay | fixed raw artifacts produce identical normalized/decision artifacts |
| Integration | full A01-A18 offline fixture run；read-only external smoke只属于后续operational pilot gate |

Network smoke不能替代 deterministic fixture tests；生产数据也不能成为单测前置条件。

## 8. Evidence seal layout

严格采用 `07_REVIEW_GATES.md` 的外层格式；扩展证据放入其子目录：

```text
<GATE>-evidence-seal/
  README.md
  manifest.json
  hashes.sha256
  commands.log
  test-results/
    test_results.json
    import_graph.json
  sample-artifacts/
    work_order.json
    input_manifest.json
    source_identity.json
    schema_manifest.json
    dependency_graph.json
    outputs/
    decisions/
    evidence_chain.jsonl
    run_receipt.json
  migrations/
    migration_evidence.json
    rollback/
  risk-register.md
  known-limitations.md
```

`manifest.json` 至少包含 gate、run_id、git_commit、generated_at、artifacts、tests、blocking_issues、disposition_requested；`hashes.sha256` 覆盖 seal 内所有证据文件（自身除外）。run receipt 由现有 harness verification/certification 生成或适配，不能由任务自报 success 代替。

## 9. Rollback matrix

| Failure | Immediate action | Data handling | Recovery proof |
|---|---|---|---|
| contract incompatibility | pin previous reader/writer version | 新 artifact保留并标 incompatible | compatibility suite |
| migration failure | rollback transaction，停 Alpha feature | 原 DB/hash不变；备份保留 | pre/post identity + integrity |
| Gamma/schema drift | stop adapter, keep raw response | 不填默认值掩盖 | drift fixture/error receipt |
| capture demand overload | disable Alpha SENSING/FORMAL_REVIEW producers independently | existing owner继续原配置 | demand purpose counts/health |
| wallet source stale/truncated | disable wallet recaller | historical refs保留 | freshness/pagination receipt |
| rule A/B mismatch | block candidate and re-run from A | 旧 contract/packet不可覆盖 | transition audit |
| packet/result corruption | quarantine inbox file | frozen packet/result保留 | hash mismatch receipt |
| duplicate ledger writes | stop runner, inspect idempotency key | 不删除重复疑似记录，标 investigation | DB uniqueness/replay |
| any unauthorized network/process/order capability reachable | disable entire Alpha entrypoint | evidence seal marked failed | capability sandbox + canary pass |

## 10. P0 completion gate

只有同时满足以下条件才可声明 P0 实现完成：

- A01-A18 全部 PASS；
- targeted legacy regression tests PASS；
- migration、source identities、dirty-worktree SHA 均写入 evidence；
- no shared schema owner overlap、no parallel collector、no second orchestrator；
- Blind/Market packets及导入结果可由 hash完整重放；
- Wave 0/per-merge/final capability-based no-live proof全部PASS；
- rollback rehearsal至少覆盖 schema、adapter、packet importer和Alpha entrypoint；
- 未批准任何 production config或live execution change。

## 11. Readiness levels and operational pilot gate

原设计包的最终disposition枚举保持不变，但`READY_FOR_P0_IMPLEMENTATION`在本计划中严格等价于：

```text
READY_FOR_OFFLINE_IMPLEMENTATION = READY
READY_FOR_READ_ONLY_LIVE_PILOT = NOT_READY
READY_FOR_PRODUCTION_CAPTURE_EXPANSION = NOT_AUTHORIZED
```

offline implementation完成不自动升级为每日真实扫描。进入read-only operational pilot前必须单独通过`READ_ONLY_OPERATIONAL_PILOT_GATE`：

- arbitrary binary market token可通过现有demand/receipt owner获取，且canonical token mapping正确；
- production manifest/health证明weather capture没有coverage、latency或ownership退化；
- Gamma/CLOB API rate、吞吐、重试、staleness、artifact/DB增长有固定窗口实测；
- paired YES/NO receipts在预注册sample/time window稳定，缺失也计入coverage分母；
- capability sandbox在联网模式仅放行精确公开read-only endpoints；
- SENSING预算明确`max_markets_per_scan`、`max_tokens_per_batch`、`max_demands_per_minute`、`max_artifact_bytes_per_day`、priority、TTL/dedupe window；
- SENSING与FORMAL_REVIEW demand可独立熔断；rollback在目标运行上下文真实演练；
- 任何production config变更另走`weather-strategy-deploy`与显式批准。

## 12. Post-P0 roadmap（不阻断offline P0）

### P1 — Platform-level market-book owner extraction

保留现有demand/receipt、capture identity和single-owner invariant，将`weather_market_books`实现逐步抽到platform-level owner；weather和Alpha都变成consumer。迁移必须做REST/WS parity、双消费者shadow、load/storage预算和逐步回退，不能直接复制或切换collector。

### P1 — Resolution backfill and learning closure

为Prediction Ledger补actual resolution、resolution source、dispute status、final simulated return、Brier/Log Loss及按market type、Rule clarity、price bucket校准。P0 ledger必须预留append-onlylinkage/version字段，但P0不伪造尚无的settlement learning结果。
