# P0 Risk Register

## 评分

- Impact：1（低）至 5（会破坏研究结论、生产稳定性或资金安全）。
- Likelihood：1（罕见）至 5（高概率）。
- Score = Impact × Likelihood。15–25 为 P0 gate risk，8–14 必须有测试/监控，1–7 记录并接受。

## Risks

| Risk ID | Risk | I | L | Score | Detection / trigger | Mitigation | Rollback | Owner | Status |
|---|---|---:|---:|---:|---|---|---|---|---|
| R-001 | P0 新建第二个 CLOB collector，与当前 owner 重复连接/写入 | 5 | 3 | 15 | import graph出现socket/HTTP book client；重复capture ids/负载 | ADR-006；只生产demand、消费receipt；code-owner review | disable Alpha demand/consumer；现有owner不动 | Market Data/Security | gated |
| R-002 | 扩大capture coverage影响production weather服务 | 5 | 2 | 10 | demand spike、latency/health/storage/coverage变化 | P0 offline fixtures；独立READ_ONLY_OPERATIONAL_PILOT_GATE；生产扩展另走manifest/deploy/approval | 分别撤SENSING/FORMAL_REVIEW demand，恢复原desired config | Operations | deferred activation |
| R-003 | canonical market identity错配，YES/NO token反转 | 5 | 3 | 15 | outcome label/token permutation test失败；condition冲突 | explicit IDs/join/aliases；label validation；禁止index/title key | quarantine market revision，不进入book/research | Shared Contracts/Market Data | gated |
| R-004 | Gamma API schema、pagination或rate limit漂移 | 3 | 4 | 12 | unknown/missing fields、repeated cursor、429/5xx | raw capture、strict adapter、fixture + read-only smoke、bounded retries | stop catalog adapter，保留raw/error receipt | Market Data | monitored |
| R-005 | catalog只保存current row导致PIT/rule history丢失 | 4 | 3 | 12 | old revision缺失、same id content hash变化 | append-only revision+unique hash；legacy current row不作为truth | stop writer，replay immutable raw | Alpha Storage | gated |
| R-006 | `rule_hash`与`contract_corpus_sha256`混淆 | 5 | 3 | 15 | A/B引用不同字段或hash长度虽同但语义不明 | typed distinct fields/ids；compiler invariant tests | Gate block并从A重跑 | Rules | gated |
| R-007 | Rule A/B使用不同parser/revision，产生不可重放结论 | 5 | 3 | 15 | compiler/version/rule hash mismatch | single compiler facade；B same-hash hard gate | block candidate；pin prior compiler | Rules | gated |
| R-008 | Blind通过slug/URL、candidate side、wallet方向、price-derived reason或Polymarket source发生语义泄漏 | 5 | 4 | 20 | Candidate→projection diff或adversarial/source-policy fixture发现侧信道 | BlindCandidateProjection allowlist、blind id、domain deny policy、semantic fixtures、freeze hash | quarantine projection/packet/result，从Gate A重建Blind stage | Research/Security | gated |
| R-009 | GPT Pro结果文件被篡改、错配或重复导入 | 4 | 3 | 12 | packet/result/version/hash mismatch；duplicate key | atomic outbox/inbox、canonical hash、idempotent import、quarantine | stop importer；状态不推进；原文件保留 | Research | gated |
| R-010 | 外部模型非确定性被误当程序确定性 | 3 | 4 | 12 | 同packet多结果无producer/version；结论覆盖 | 每个result独立artifact；保存model/prompt/as-of；不覆盖 | pin accepted result或重新人工review | Research | monitored |
| R-011 | orderbook stale、one-sided、insufficient depth却生成mid/VWAP | 5 | 3 | 15 | freshness/leg/depth flags失败 | paired group、per-leg clocks、fail closed、明确insufficient | block market stage；等待新receipt | Market Book/Decision | gated |
| R-012 | wallet历史陈旧被当当前smart-money signal | 4 | 4 | 16 | `observed_at`超TTL；当前已知48/79天stale | freshness gate；historical标签；默认不产生current entry | disable wallet recaller | Wallet Recall | gated |
| R-013 | Address 被误当 Entity，多地址/代理钱包合并错误 | 4 | 3 | 12 | source identity冲突、代理钱包变化 | Address!=Entity；identity provenance/alias interval | quarantine entity merge，保留address hits | Wallet Recall/Contracts | monitored |
| R-014 | wallet API pagination/truncation造成偏置 | 3 | 4 | 12 | page upper bound、5500 cap、receipt不完整 | bounded recursive windows、coverage receipt、incomplete flag | 不生成当前RecallHit；仅historical evidence | Wallet Recall | monitored |
| R-015 | 复用legacy `live` status泄漏交易语义 | 5 | 2 | 10 | Alpha enum/schema出现live/position sync | 独立candidate states；DB position CHECK；migration不映射legacy状态 | stop ledger writer，rebuildprojection | Decision/Security | gated |
| R-016 | 同仓real auto-order可通过已知模块或通用HTTP/dynamic import/subprocess/shell动态到达 | 5 | 3 | 15 | canary发现未授权host/path/method/auth；sentinel/import/subprocess被调用 | offline network deny；AlphaReadOnlyTransport；endpoint/env allowlist；AST/runtime guards；sentinel | disable整个Alpha entrypoint，Gate fail | Security | gated |
| R-017 | PredictionRecord重复或update-in-place覆盖历史 | 4 | 3 | 12 | same natural key多logical rows；missing transitions | append-only events、stable idempotency key、unique constraint | stop writer；projection from event replay | Decision Ledger | gated |
| R-018 | candidate状态跳过Rule/Blind/Market顺序 | 5 | 3 | 15 | invalid transition attempt | central transition matrix、repository enforcement、protocol tests | reject transition并封存failed receipt | Decision Ledger | gated |
| R-019 | research.db migration损坏legacy copy_trade数据 | 4 | 2 | 8 | pre/post hash/count diff、integrity/FK失败 | copy-first tests、transaction、additive only、backup manifest | transaction rollback/read-view pin；不drop | Alpha Storage | gated |
| R-020 | 将Alpha表写进canonical weather.db造成生产耦合 | 5 | 2 | 10 | DB identity显示weather canonical；migration target不符 | absolute path/device/inode allowlist；ADR-002 | refuse migration；若未提交则rollback transaction | Alpha Storage/Operations | gated |
| R-021 | dispute mart相对路径指向非canonical副本 | 4 | 3 | 12 | counts/schema/device/inode与manifest不符 | absolute JRS identity、schema version和hash进入run evidence | disable dispute adapter，使用fixtures | Dispute/Governance | gated |
| R-022 | harness与Alpha各自实现业务/task状态，形成双真相 | 4 | 3 | 12 | harness出现candidate transitions或Alpha出现leases/jobs | ownership lock：harness task，Alpha domain | disableadapter/sequential runner，保留domain events | Governance/Decision | gated |
| R-023 | stable ID算法在各adapter分叉 | 4 | 3 | 12 | same logical fixture产生不同id | P0-01唯一serializer/id factory；golden hashes | pin shared package；拒绝adapter写入 | Shared Contracts | gated |
| R-024 | float rounding影响probability/price/rank和幂等hash | 4 | 3 | 12 | binary float进入model/SQL；cross-platform hash diff | Decimal internal、canonical formatting、boundary/property tests | pinserializer/config；不改历史记录 | Shared Contracts/Decision | gated |
| R-025 | source clock和ingest clock混用，产生未来数据泄漏 | 5 | 3 | 15 | observed_at缺失/晚于decision as-of | 双时钟合同、PIT gate、缺失fail closed | block artifact/candidate，重放正确as-of | Market Data/Rules | gated |
| R-026 | raw artifact丢失或normalized row无法反查 | 4 | 2 | 8 | missing raw id/hash/path；seal verify失败 | immutable raw-first、FK/hash、retention manifest | stop materializer，restore artifact backup | Market Data/Governance | monitored |
| R-027 | dirty worktree导致实现/证据基线不清 | 3 | 4 | 12 | source SHA与changed files未记录；覆盖用户修改 | 每run记录HEAD/status；新增isolated paths；冲突即停 | revert only owned new changes via patch；不碰用户改动 | Integration | monitored |
| R-028 | 子任务ownership重叠或agent用量异常 | 3 | 3 | 9 | 两包改同migration/contract；telemetry缺失/膨胀 | sole-owner locks、单层worker、coordinator usage review | interrupt worker；coordinator接管；未审产物不合并 | Integration | monitored |
| R-029 | 旧文档/暂停session被误报为当前能力或故障 | 3 | 3 | 9 | 仅凭文档/session名下结论 | manifest/process/DB/runtime证据优先；archive标签 | 撤回结论，刷新单次snapshot | Integration/Operations | accepted control |
| R-030 | watchlist UI scope扩张并直连production DB | 3 | 3 | 9 | UI先于ledger；写API出现 | CLI/JSON read-only first；repository API；no weather DB writes | disable UI route，保留export | Product | deferred |
| R-031 | secrets/private key进入packet、log或evidence seal | 5 | 2 | 10 | secret scan/env dump包含敏感值 | allowlisted metadata、redaction、禁止全env dump、P0不需key | quarantine/delete only generated leaked artifact after explicit security procedure；rotate externally | Security/Governance | gated |
| R-032 | 证据seal由任务自报success，未独立验证 | 4 | 3 | 12 | missing manifest hashes/certification；tamper未发现 | reuse harness hash-chain/verification/receipt；independent verify | status降为FAILED/limitations，保留failed seal | Governance | gated |
| R-033 | 全部recall错误依赖book，破坏低成本全量扫描并扩大capture | 5 | 3 | 15 | no-book fixture无法生成Candidate；DAG/provider依赖P0-05 | pre-book P0-06A～D与optional P0-06E拆分；route-isolation tests | disableP0-06E/book demand，pre-book routes继续 | Architecture/Recall | gated |
| R-034 | Candidate每日扫描后沿用stale Recall/Evidence/Rule/Book而无显式失效 | 5 | 3 | 15 | input TTL/revision变化但projection无event | append-only invalidation/refresh lifecycle；old artifact不可驱动new decision | stop decision writer，从events重建projection | Decision Ledger | gated |
| R-035 | GPT Pro报告有hash但claim无法定位source/effective-as-of，无法重放概率依据 | 4 | 3 | 12 | result只有Markdown或缺claim/source/time/location | claim-level EvidenceItem、source snapshot/hash、PIT validation | reject result，保留原报告于quarantine | Research | gated |
| R-036 | offline完成被误报为daily read-only operational readiness | 4 | 4 | 16 | 未跑real token/receipt/load/rollback就启用scheduler | readiness三分层；独立operational pilot gate；manifest/deploy approval | disableAlpha scheduler/egress/demand | Governance/Operations | gated |
| R-037 | weather域book owner被永久固化为通用平台owner，形成长期域耦合 | 3 | 3 | 9 | Alpha contract依赖weather私有path/config | P1 platform-level owner extraction；P0仅依赖demand/receipt contract | 保持现owner和consumer adapter，暂停抽取 | Architecture/Market Data | planned P1 |

## Gate risks and release rule

以下风险任一未有自动化证据即不得通过 P0 implementation gate：

```text
R-001 R-003 R-006 R-007 R-008 R-011 R-012 R-016
R-018 R-020 R-025 R-031 R-032 R-033 R-034 R-035 R-036
```

`R-002` 的控制方式是明确不在P0实现过程中修改生产capture配置；这不是“接受无控制风险”，而是把激活动作留在后续独立deploy gate。

## Residual risk accepted for implementation start

- Gamma真实API字段/rate limit仍需只读smoke确认；fixture-first可安全开始。
- target-size VWAP参数尚未选择最终research配置；合同支持版本化配置即可开始。
- GPT Pro人工响应质量不能由软件完全保证；P0通过blind isolation、版本、hash和人工review保留可审计性。
- wallet源当前陈旧；P0可先完成adapter和negative/freshness tests，不能据此发布当前wallet结论。

这些residual risks不造成shared ownership/schema/no-live决策空缺，因此不阻断`READY_FOR_P0_IMPLEMENTATION`，但该disposition仅表示`OFFLINE_IMPLEMENTATION_ONLY`。read-only operational pilot和production capture expansion仍为未通过/未授权。
