# Polymarket Alpha P0 Discovery Report

## 结论

- 当前阶段：`DISCOVERY_AND_INTEGRATION_DESIGN_ONLY`
- 设计包版本：`polymarket-alpha-design-pack-v1.0`
- 最终 disposition：`READY_FOR_P0_IMPLEMENTATION`
- readiness scope：`OFFLINE_IMPLEMENTATION_ONLY`
- Gate 1（Discovery）：`PASS`
- Gate 2（Integration Design）：`PASS`

P0可以进入offline/fixture实现，但仅限research infrastructure、simulation和人工文件回灌；不得自动交易，也不得新建平行的market collector、wallet tracker、rule lawyer或orchestration system。该结论不等于`READY_FOR_READ_ONLY_LIVE_PILOT`，更不授权生产启用、扩大现有CLOB capture覆盖或任何带鉴权order path。

## 1. 输入与审计快照

严格按设计包规定的顺序阅读了 `00_CODEX_ENTRY.md`，随后依次阅读 `01` 至 `08`。设计包 zip SHA-256：

```text
8decb53071d3180ee43695412a6225e35472b1fe93f2cf5c100c1b58e7ff7f2f
```

仓库快照：

```text
repository: /Users/deepsleep/projects/pm_agents
HEAD: e0ad0c6b41bfd949f47fa4e187a497e4c7d793be
snapshot_date: 2026-08-26 Asia/Shanghai
worktree: dirty, 37 pre-existing entries; this review did not modify them
```

本轮只增加 `docs/design/polymarket_alpha_p0/` 下的设计产物，没有执行数据迁移、部署、启停服务、外部写入或订单操作。

## 2. 设计包引用索引

| 顺序 | 文件 | SHA-256 前缀 | 在本设计中的作用 |
|---|---|---|---|
| 00 | `00_CODEX_ENTRY.md` | `aaeaabb` | 阶段、顺序、八项交付与 disposition |
| 01 | `01_SYSTEM_CHARTER.md` | `5e351894` | P0 冻结边界与总体原则 |
| 02 | `02_TARGET_ARCHITECTURE.md` | `dfd14dd` | 目标组件、数据流和职责边界 |
| 03 | `03_MVP_EXECUTION_SPEC.md` | `35666bf` | P0 最短路径、顺序与验收场景 |
| 04 | `04_INTEGRATION_DISCOVERY_SPEC.md` | `d27f59d` | Discovery、资产决策和集成要求 |
| 05 | `05_CONTRACTS.md` | `3582a067` | 共享 envelope、实体和状态合同 |
| 06 | `06_SUBAGENT_TASK_TEMPLATE.md` | `70a6ee21` | 有界工作包统一格式 |
| 07 | `07_REVIEW_GATES.md` | `1b6f7e01` | Gate、evidence seal 与验收 |
| 08 | `08_REFERENCE_INDEX_TEMPLATE.md` | `6b2af808` | 历史资产引用索引格式 |
| manifest | `MANIFEST.json` | `4600251` | 包内容一致性 |

## 3. 当前仓库拓扑

| 区域 | 当前职责 | 结论 |
|---|---|---|
| `src/platform/clients/`、`src/models/market.py` | Gamma/CLOB API 与市场模型 | 可适配为 catalog/orderbook ingress；合同不完整 |
| `src/platform/market_data/` | capture contract、identity、demand/receipt、WS rebuild，以及一套旧 generic WS | 保留严格 capture primitives；替换旧 generic WS 实现 |
| `weather_data_feed_service/market_books*.py` | 当前唯一生产 CLOB book owner，REST 全梯 + 选择性 WS | 继续单一 ownership；P0 只通过 demand/receipt 请求或读取 |
| `src/strategies/rule_lawyer/` | 规则 parser/audit、市场研究、smart-wallet、历史 auto-order | parser/audit 适配；auto-order 隔离出 P0 |
| `src/strategies/dispute_repricing/` 与 `scripts/analysis/dispute_*` | PIT contract corpus、adjudication、独立 dispute mart | 保留 domain mart，以 adapter 接入；不复制成通用规则库 |
| `src/workflows/research/` | market intel/report 工作流 | 适配为 Recall/Evidence 输入；不能冒充标准 ResearchPacket |
| `scripts/copy_trade/`、`runtime/db/research.db` | 旧 wallet/copy-trade research | 只读迁移源；共享库可做 additive `alpha_*` schema host |
| `runtime/wallets.db`、`scripts/analysis/wallet_weather/` | 历史钱包事实及 weather 专项研究 | 历史 provenance/特征源；不能当作当前信号 |
| `src/weather_agent_harness/` | WorkOrder DAG、lease、EvidenceStore、receipt/certification | 作为唯一任务编排和 evidence-seal 基础；不承担候选业务状态机 |
| `runtime/weather.db` | weather canonical facts 与交易血缘 | 保持 weather 专用，不放通用 Alpha P0 表 |
| `weather_dashboard/`、`frontend/` | FastAPI/React 现有展示资产 | P0 只复用最小 read-only watchlist/export；不先做复杂 UI |

## 4. 已验证能力与证据

| 能力 | 证据 | 当前判定 |
|---|---|---|
| Gamma pagination/normalize | `src/platform/clients/gamma.py`；相关测试通过 | 可适配，缺完整 canonical identity/revision |
| CLOB REST book | `src/platform/clients/clob.py` | 可适配，缺通用 paired snapshot 和配置化 target-size |
| 严格 capture lineage | `capture_contract.py`、`identity.py` | 可直接保留 |
| 单一 capture-demand owner | `capture_demand.py`、receipt、`weather_market_books` | 可保留并扩展 adapter；不得另建 collector |
| Rule parsing/audit | rule_lawyer parser/adapter/workflow | 可适配；不是目标 RuleContract |
| PIT dispute corpus | dispute canonical/corpus/clarification | 可保留 domain primitives；Rule B 仍需统一 facade |
| Wallet discovery/profile | `smart_wallets.py`、market-intel workflow | 可适配为 recall-only |
| Append-only evidence/DAG | weather agent harness | 可保留，需加设计包 seal adapter |
| Prediction Ledger | 未找到 | 需要 BUILD |
| Blind/Market roundtrip | 未找到 | 需要 BUILD |
| 目标 Candidate 状态机 | 未找到 | 需要 BUILD |

验证命令及结果：

```text
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
=> canonical weather DB identity healthy; Mac is current production

.venv/bin/python scripts/ops/weather_production_ctl.py health
=> HEALTHY; dispute_repricing/clarification are intentionally paused zero-notional shadow

.venv/bin/pytest -q <market/rule/dispute/wallet/harness targeted suites>
=> 58 passed in 4.11s

PYTHONPATH=. .venv/bin/pytest -q <wallet persistence/history + harness suites>
=> 23 passed in 3.02s
```

## 5. 运行与数据事实

### 5.1 Production identity

- 当前生产主机：Mac。
- canonical weather DB：`/Volumes/jrs/pm_agents/runtime/weather.db`，仓库 `runtime/weather.db` symlink 与其 device/inode 一致；约 16.27 GB，schema version 19。
- 当前 health 总体 `HEALTHY`。`dispute_repricing` 与 clarification session 的 `UNKNOWN` 是 `production.yaml` 中明确 `desired_state: paused`、`actual_notional: 0` 的结果，不是本轮应修复的故障。
- `src/strategies/rule_lawyer/auto_order.py` 存在真实鉴权下单能力，因此 P0 必须证明“不可达”，不能声称仓库全局没有 live capability。

### 5.2 Stores

| Store | 物理事实 | 设计用途 |
|---|---|---|
| canonical `weather.db` | 现有 generic-adjacent 表只含 runs/signals/plans/orders/fills/settlements/fact_*；weather 专用且关键 | 不迁入 Alpha P0；仅借鉴 lineage/adapter |
| `runtime/db/research.db` | 69,632 bytes；只有 `copy_trade_*`；0 wallets/discoveries/signals/reports，1 review（2026-08-05） | 作为 Alpha P0 additive `alpha_*` schema 的逻辑 host；legacy 表只读保留 |
| `runtime/wallets.db` | 44 wallets；46 snapshots（最新 2026-07-09）；181 positions（最新 2026-06-08）；243 topic PnL | 历史只读 provenance；不作为当日 wallet truth |
| `/Volumes/jrs/pm_agents/runtime/dispute_repricing/dispute.db` | schema v2；193 cases、773 fragments、3277 signal candidates、2658 receipts、733 adjudications；0 intents/orders | 保持 domain mart，通过 revision/hash adapter 引用 |

wallet 数据相对 2026-08-26 分别陈旧 48 天、79 天；旧 research review 陈旧 21 天。任何 P0 recall 都必须携带 `observed_at` 和 freshness gate，不能把这些历史记录标成当前 wallet signal。

## 6. 文档—代码—运行态不一致

1. 旧架构文档描述了更宽泛的 PMM/ARB/Rule Lawyer 能力，但 README 和生产配置明确当前主线是 weather；旧文档只能做历史证据。
2. rule_lawyer catalog schema 存在于代码，但当前 `research.db` 没有对应表；不能将“有 schema 文件”等同于“已部署能力”。
3. 现有 rule storage 覆盖 current rule，`market_rule_parses` 的唯一键也不足以表达不可变 rule revision；不满足目标 `rule_hash` 合同。
4. dispute `contract_corpus_sha256` 是 corpus revision，不等于 generic `rule_hash`。
5. weather CLOB WS 是选择性 capture，不是 all-market census；P0 必须由 catalog/change detector 提交 demand。
6. market-intel 和 clarification packet 有 JSON/Markdown 产物，但没有 schema-versioned Blind/Market roundtrip、冻结 hash 或结果兼容性 gate。
7. 两套 wallet store 的 identity/status/schema 不同且陈旧，不能直接合并，也不能延续 legacy `live` 状态。
8. `unified_copy_trade.py` 和 smart-money PMM adapter 可直接形成交易命令，与 P0 冻结的 recall-only/no-auto-trade 边界冲突。
9. dispute mart 的本地相对路径与 canonical JRS 查询曾返回不同计数；所有后续实现与验收必须记录绝对路径、device/inode/schema version，禁止只写 `runtime/...`。

## 7. 未确认项及处理方式

以下均已转成有界 implementation probe，不阻断 P0 编码：

- Gamma实时字段、分页终止语义和rate limit：offline P0先以frozen fixture/schema-drift测试实现；真实read-only smoke/rate/load只在独立operational pilot gate验收。
- 默认 VWAP/depth target size：作为版本化 config，不硬编码进合同；上线前由研究配置审查。
- 将现有 capture owner 扩展到更广 token 集：先在 fixture/offline mode 完成；任何生产配置变化另走 deploy 审批。
- GPT Pro 是人工文件交换边界：P0 不自动调用外部模型，不把其非确定性隐藏成同步函数。

## 8. Discovery Gate 评估

| Gate 条件 | 结果 | 证据 |
|---|---|---|
| 已盘点相关目录、入口、store、runtime | PASS | 本报告、`ASSET_INVENTORY.csv` |
| 已验证关键能力，不靠名称推断 | PASS | 81 个 targeted test passes；DB/runtime 查询 |
| 已记录 docs/code/runtime mismatch | PASS | 第 6 节 |
| 已标出未知项及验证方法 | PASS | 第 7 节及 P0 tasks |
| 不新建平行 owner | PASS | ADR-002/006/007/010 |
| no-live 边界可验证 | PASS | capability-based ADR-011 与 acceptance A14/A18 |

本设计复用了项目既有统一血缘思想和 append-only evidence 机制，并把新实体接在其上；没有重做 order/fill/PnL 基础设施。

## 9. 历史资产 Reference Index

| Reference ID | 名称 | 路径/仓库 | 原始用途 | 目标映射 | 当前状态 | 核实证据 | 备注 |
|---|---|---|---|---|---|---|---|
| REF-DATA-001 | Gamma/CLOB/capture stack | `src/platform/clients/`、`src/platform/market_data/`、`weather_data_feed_service/` | 市场与盘口数据 | Catalog / Orderbook Sensing | VERIFIED_ACTIVE/PARTIAL | code、fixtures、production manifest/health | single capture owner |
| REF-WALLET-001 | smart-wallet/copy-trade/weather wallet assets | `smart_wallets.py`、`scripts/copy_trade/`、`scripts/analysis/wallet_weather/`、两 wallet DB | 钱包发现/研究 | Wallet Recall | VERIFIED_STALE/PARTIAL | DB counts/timestamps；23 tests | recall-only，Address != Entity |
| REF-RULE-001 | rule_lawyer parser/audit/clarification | `src/strategies/rule_lawyer/` | 规则解析与审阅 | Rule Lawyer A/B | VERIFIED_PARTIAL | parser/adapter fixtures | auto-order excluded |
| REF-DISPUTE-001 | dispute corpus/canonical mart | `src/strategies/dispute_repricing/`、canonical JRS `dispute.db` | dispute/resolution研究 | Dispute Library / Rule Evidence | VERIFIED_PAUSED | absolute DB query；corpus/canonical tests | separate domain mart |
| REF-WEATHER-001 | weather canonical lineage/model/runtime | `src/strategies/weather_*`、canonical JRS `weather.db` | weather模型与实盘血缘 | Model Adapter / lineage reference | VERIFIED_ACTIVE | manifest/health/schema | P0不写该DB |
| REF-ORCH-001 | weather agent harness | `src/weather_agent_harness/` | agent任务和证据编排 | Orchestrator / Evidence Seal | VERIFIED_ACTIVE_RESEARCH | 23 harness/wallet tests | 不管理candidate业务状态 |
| REF-REVIEW-001 | market reporting/intel/clarification packets | `src/workflows/research/`、clarification artifacts | review packet/report | Blind/Market Packet素材 | VERIFIED_PARTIAL | output/code/fixtures | 尚无标准roundtrip |

详细逐项字段、last-used、tests、owner和 disposition 见 `ASSET_INVENTORY.csv`；本索引不是独立能力声明。

## 10. Subagent 产物主审

| Discovery package | 结果 | 主审处理 |
|---|---|---|
| Market data/catalog/orderbook | 完成只读发现；12 targeted tests pass | 接受“现有capture service为唯一owner”；将旧generic WS判为REPLACE而非新增collector |
| Rule/dispute | 完成只读发现；19 fixture tests pass | 接受compiler facade与hash分离；以canonical JRS absolute DB查询覆盖相对路径计数歧义 |
| Wallet/governance | 完成只读发现；23 tests pass | 接受staleness/recall-only/harness ownership；其“canonical owner待定”建议由ADR-002/007/010正式消解 |

三项产物均未修改文件。worker未暴露model/effort和rollout token telemetry，因此其发现证据由主agent独立用路径、DB/runtime查询和targeted tests复核后才纳入；未把缺telemetry的局部 disposition 直接作为最终结论。

## 11. Integration Gate 评估

| Gate 条件 | 结果 | 证据 |
|---|---|---|
| 每个目标模块有 reuse/adapt/build 结论 | PASS | `CURRENT_TO_TARGET_MAPPING.md` |
| canonical identity/shared contracts无未决冲突 | PASS | ADR-002/003/004/009/012 |
| P0 dependencies形成DAG | PASS | 修订`DEPENDENCY_GRAPH.md`；pre-book routes不依赖book，optional book route独立 |
| task scopes/owners不重叠 | PASS | `SUBAGENT_WORK_PACKAGES.md`将原P0-06拆为P0-06A～E及ownership locks |
| migration/rollback明确 | PASS | `P0_IMPLEMENTATION_PLAN.md`、ADR-002 |
| no-live-order边界可验证 | PASS | capability-based ADR-011、A14/A18、P0-11 contract |

## 12. Independent review revision

GPT Pro独立评审首次将Gate 2判为`PASS_WITH_BLOCKING_FIXES`，提出三项blocker：

1. BF-1：原task DAG让全部Recall依赖P0-05 book，与目标runtime flow冲突；
2. BF-2：Blind只过滤price/book字段，未隔离slug/URL/wallet side/price-derived reasons和Polymarket source；
3. BF-3：no-live只围绕已知module path，未封通用HTTP、dynamic import、subprocess/shell和env/credential能力。

本修订不重做Discovery，而是分别通过以下证据关闭：

| Finding | Design closure | Acceptance closure |
|---|---|---|
| BF-1 | ADR-006；P0-06A～E；pre-book与optional book双路径DAG | no-book Candidate fixture；SENSING不阻断；accepted blind后才FORMAL_REVIEW demand |
| BF-2 | ADR-008；BlindCandidateProjection allowlist；claim-level Evidence；Polymarket source deny policy | Candidate→projection diff及语义adversarial fixtures A08/A15/A17 |
| BF-3 | ADR-011 capability sandbox；offline network deny；precise transport/env allowlist；Wave-0/per-merge/final proof | generic transport/importlib/subprocess/auth/key bypass A14/A18 |

因此修订后的Gate 2恢复为`PASS`。原设计包最终disposition只允许三个值，故使用`READY_FOR_P0_IMPLEMENTATION`；必须与`readiness_scope=OFFLINE_IMPLEMENTATION_ONLY`一起解释。独立`READ_ONLY_OPERATIONAL_PILOT_GATE`通过前，不得声称daily read-only operation ready。
