# Polymarket Alpha — 项目总览与当前事实

状态日期：2026-08-28
项目简称：`Polymarket Alpha`
当前框架：`Gate R Controlled Manual Research Pipeline`
中文简称：`Gate R 多路召回与人工深研框架`
日常简称：`Alpha Gate R (AGR)`

## 1. 一句话结论

项目已经完成从市场事实接入、多路召回、Candidate、规则编译、模型语义初筛、Blind 研究合同、正式盘口比较、Rule B、`NO_ORDER` Prediction Ledger 到离线 resolution learning 的工程骨架和测试闭环。

目前获准并实际验证到的最高运行状态是：一次受控、只读、无下单的 5 市场 smoke sample 已完成 Gamma 抓取并逐市场推进到 `BLIND_PACKET_FROZEN`。它还不是完整 Gate R WP6，也不是每日扫描服务，更不是交易系统。

```text
ENGINEERING_BASELINE=COMPLETE_OFFLINE
GATE_R_WP1_TO_WP5=COMPLETE
CONTROLLED_5_MARKET_SMOKE=PARTIAL_SUCCESS
SMOKE_STOP_STATE=BLIND_PACKET_FROZEN
GPT_PRO_BLIND_RESULT=NOT_RECEIVED
FRESH_PAIRED_BOOK_AFTER_BLIND=NOT_REQUESTED
RULE_B_AND_PREDICTION_LEDGER_FOR_SAMPLE=NOT_REACHED
OFFICIAL_GATE_R_WP6_8_CASE_PILOT=NOT_EXECUTED
DAILY_READ_ONLY_OPERATION=NOT_APPROVED
LIVE_ORDER=STRICTLY_OUT_OF_SCOPE
```

## 2. 项目要解决什么问题

目标不是做一个看到低价就下注的机器人，而是建立一个可审计的研究流水线：

1. 用多种低成本信号从大量市场中召回值得研究的 Candidate；
2. 先冻结市场规则和研究问题；
3. 在不向研究模型泄漏价格、方向、钱包交易和平台身份的情况下估计事件概率；
4. Blind 结果被接受后才读取 fresh paired YES/NO book；
5. 用确定性程序比较独立概率与可执行盘口，记录证据、edge 和决策；
6. 当前只写 `NO_ORDER` 预测记录，结算后再做评分与校准。

## 3. 当前完整流程

```text
Gamma market facts
  -> Catalog / Change Events
  -> 多路 Recall
       - New / Changed
       - Structural / Metadata anomaly
       - Controversy / Dispute
       - Specialist Wallet（recall-only）
       - Optional Book Anomaly
  -> Candidate aggregation / dedupe / lifecycle
  -> CandidateSnapshotSeal
  -> GLM-4.7 semantic triage（非决策权威）
  -> deterministic rule dry-run / risk routing
  -> conditional independent GLM-5.3 rule review
  -> sole RuleContractCompiler
  -> Rule A
  -> BlindResearchQuestionSet + SourcePlan
  -> exact Blind prompt seal + human export approval
  -> fresh isolated GPT Pro Blind web research
  -> raw return / source capture / claim-level importer
       - accepted
       - insufficient evidence
       - quarantined
  -> accepted 后才请求 fresh paired YES/NO book
  -> deterministic Blind-vs-Book MarketComparison
  -> MARKET result importer
  -> Rule B
  -> ReviewDecision + PredictionRecord（NO_ORDER）
  -> authoritative resolution intake
  -> Brier / Log Loss / calibration / backfill
```

### 各节点的权力边界

| 节点 | 可以做什么 | 不能做什么 |
|---|---|---|
| Recall | 提名 Candidate | 宣称已经发现 fair-value 错价 |
| GLM-4.7 | 低成本语义理解、研究性初筛 | 决定 Rule A/B 或下注 |
| GLM-5.3 | 独立规则语义与结构化 parse proposal | 看到 GLM-4.7 输出、替代规则编译器 |
| GPT Pro Blind | 联网查证并独立估计概率 | 看到价格、方向、slug、市场 URL、钱包交易或前序模型意见 |
| Deterministic code | identity、hash、lifecycle、Rule、接受/隔离、edge 比较 | 自由发挥研究事实 |
| Human | 批准精确 prompt、处理有界规则例外 | 绕过 hash、来源、freshness、隔离或 Rule gate |

## 4. 已完成工程资产

| 层 | 当前状态 | 主要实现 |
|---|---|---|
| Shared contracts / lifecycle | 完成 | `src/polymarket_alpha/contracts/`, `protocol/` |
| SQLite repository / additive migrations | 完成 | `storage/` |
| Gamma capture ingest / catalog / change | 完成 | `operational/gamma_ingest.py`, `census/`, `change/` |
| Recall providers / aggregation | 完成 | `recall/`, `pipeline/recall.py` |
| Rule compiler / Rule A/B | 完成 | `rules/` |
| GLM semantic triage / deterministic routing | 完成 | `triage/` |
| Blind packet / research job / artifact store | 完成 | `research/`, `artifacts/` |
| Manual GPT Pro handoff / return importer | 完成（离线合同与人工 seam） | Gate R WP3/WP4 |
| Book demand / existing-owner bridge | 完成 | `books/`, `operational/book_bridge.py` |
| Blind-vs-Book comparison / decision ledger | 完成（fixture 验证） | `pipeline/review.py`, `decision/` |
| Resolution / scoring / calibration | 完成（caller-supplied offline input） | `learning/` |
| No-live / read-only capability boundary | 完成工程与测试；真实长期运行未获批 | `security/`, governance seals |
| Daily scheduler / autonomous web research | 未授权、未启用 | 保持外部 seam |
| Trading / signing / private key | 不在范围内 | 无 Alpha 执行路径 |

## 5. 核心集成决策

- 不创建第二套 Gamma/CLOB collector；真实 book 继续服从现有 single-owner invariant。
- 不创建第三套 wallet tracker；wallet 只提供 recall facts，任何 wallet payload 都不得进入 Blind packet。
- Rule A/B 共用同一 RuleContractCompiler、同一 rule hash。
- dispute/controversy 保持独立事实域，通过 adapter 形成标准 RecallHit。
- harness 只管 WorkOrder、lease、attempt、evidence；Candidate 业务状态由 Alpha repository 管。
- Alpha 数据只用 `alpha_*` additive schema；未获授权不得迁移当前生产数据库。
- Alpha domain process 不嵌入 browser、通用 HTTP、provider SDK、shell、subprocess、签名或 order capability。
- Blind 研究采用 allowlist-only projection；不是简单删除 price 字段。
- 正式 book 只能在 Blind result accepted 后获取；SENSING book route 只能是可选召回支路。
- 模型输出都是不可信 proposal；确定性程序拥有 identity、Rule、接受/隔离和 ledger 权威。

## 6. 当前 readiness

| 能力 | 状态 |
|---|---|
| Offline fixture implementation | `PASS` |
| Gate R WP1–WP5 engineering | `PASS` |
| 受控一次性 Gamma read-only sample | `PASS` |
| 5 市场 Candidate → Rule A → GLM triage → Blind prompt | `PASS_WITH_LIMITATIONS` |
| 正式 8-case Gate R WP6 | `NOT_EXECUTED` |
| GPT Pro Blind 结果导入 | `WAITING_FOR_REAL_RESULTS` |
| Blind accepted 后 fresh paired book | `NOT_REACHED` |
| 5 市场 Rule B / Prediction Ledger | `NOT_REACHED` |
| Read-only operational pilot | `NOT_APPROVED` |
| Daily scanner / scheduler | `NOT_APPROVED` |
| Production capture expansion | `NOT_AUTHORIZED` |
| Trading / signing / private key | `STRICTLY_OUT_OF_SCOPE` |

## 7. 2026-08-28 受控 5 市场试跑

### Gamma capture

- 时间：约 2026-08-28 09:55 CST；
- 显式本地代理，Gamma `/events`，HTTP 200；
- 5 events / 24 nested markets；
- redirect 0；无 auth header；
- raw bytes：102,181；SHA256 `9085fbb8dd018ef6d54df011b23f7536ca5b7fa38db73391dfc757f3e1522f0e`；
- 执行边界：`NO_ORDER`，未修改生产配置或当前 runtime DB。

### 五个市场

行情数字是抓取时快照，不是当前价格，也不是独立 fair probability。

| 市场 | 快照 YES ask | spread | GLM 初筛 | 当前状态 |
|---|---:|---:|---|---|
| Kraken IPO by 2026-12-31 | 13.0¢ | 4.0¢ | ADVANCE / HIGH | BLIND_PACKET_FROZEN |
| Macron out by 2026-12-31 | 6.8¢ | 2.4¢ | ADVANCE / HIGH | BLIND_PACKET_FROZEN |
| UK election called by 2026-12-31 | 8.0¢ | 1.0¢ | ADVANCE / HIGH | BLIND_PACKET_FROZEN |
| China–India military clash by 2026-12-31 | 8.0¢ | 1.0¢ | ADVANCE / HIGH | BLIND_PACKET_FROZEN |
| NATO/EU troops fighting in Ukraine by 2026-12-31 | 6.7¢ | 2.3¢ | ADVANCE / HIGH | BLIND_PACKET_FROZEN |

GLM provider receipt显示：五次请求实际 reported model 均为 `glm-4.7`，虽然 CLI 请求 alias 为 `haiku`。合计 input 5,199 tokens、cache-read 1,984、output 3,874，provider-reported cost 为约 `$0.123837`，web search/fetch 均为 0。

### 这轮证明了什么

- 受控 Gamma read-only transport 可以通过显式代理工作；
- 真实市场 payload 能进入 catalog/scan/Recall/Candidate；
- 五个市场均能通过 Rule A 和 GLM 语义初筛；
- 五份 Blind prompt 均通过 recursive leakage scan；
- pipeline 在没有真实 GPT Pro return 时正确停住，没有提前请求 book、写最终判断或触达订单能力。

### 这轮没有证明什么

- 没有运行独立 GLM-5.3 rule review；
- 没有收到或伪造 GPT Pro 研究结果；
- 没有验证 claim-level source capture/import；
- 没有在 Blind accepted 后请求 fresh paired book；
- 没有产生正式 edge、Rule B、ReviewDecision 或 PredictionRecord；
- 没有完成预注册 8-case Gate R WP6；
- 没有证明每日联网运行、吞吐、rate、staleness、存储增长或 weather capture 隔离。

## 8. 关键文档和证据入口

### 当前 HEAD 验证

2026-08-28 在当前工作区运行完整 Alpha suite：`724 passed, 3 failed`。
三项失败全部是既有 managed-environment 限制：嵌套调用 macOS
`sandbox-exec` 返回 `sandbox_apply: Operation not permitted`；失败集合与
WP1–WP5 evidence seal 记录一致，不涉及 Alpha 业务逻辑或本轮文档/证据整理。

### 当前权威设计

- `RESEARCH_ORCHESTRATION_V3.md`
- `GATE_R_SHARED_CONTRACTS_V1.md`
- `DETERMINISTIC_TRIAGE_ROUTING_POLICY_V1.md`
- `GLM_5_3_INDEPENDENT_REVIEW_AND_RULE_PARSE_CONTRACT_V1.md`
- `BLIND_RESEARCH_PLAN_CONTRACT_V1.md`
- `GPT_PRO_MANUAL_HANDOFF_PROTOCOL_V1.md`
- `MARKET_AWARE_REVIEW_POLICY_V1.md`
- `GATE_R_CONTROLLED_MANUAL_PILOT_PLAN_V1.md`

### 工程 evidence seals

- `GATE_R_WP1-evidence-seal/`
- `GATE_R_WP2-evidence-seal/`
- `GATE_R_WP3-evidence-seal/`
- `GATE_R_WP4-evidence-seal/`
- `GATE_R_WP5-evidence-seal/`
- `P0_UNIFIED_OFFLINE_PIPELINE-evidence-seal/`
- `P1_CONTROLLED_RESEARCH_AUTOMATION-evidence-seal/`
- `P1_RESOLUTION_LEARNING-evidence-seal/`

### 当前 5 市场样例

- `GATE_R_MVP_5_MARKET_SAMPLE-evidence-seal/README.md`
- `GATE_R_MVP_5_MARKET_SAMPLE-evidence-seal/batch/00_BATCH_INDEX.md`
- `GATE_R_MVP_5_MARKET_SAMPLE-evidence-seal/hashes.sha256`

## 9. 关键 Git lineage

| Commit | 内容 |
|---|---|
| `6c57a608` | 冻结 Gate R research orchestration |
| `eda5c734` | 实现 deterministic routing |
| `596d83ca` | 独立 rule review |
| `11e6fbb0` | Blind research plan / prompt seal |
| `0e4524c6` | 人工 GPT Pro handoff |
| `ba10f2b6` | deterministic market review / Rule B continuity |

更早的 P0/P1、operational bridge、resolution learning 和 controlled 50-market fixture lineage 保留在 `ALPHA_PROGRAM_INTEGRATION_AND_NEXT_PHASE.md` 及各 evidence seal 中。

## 10. 下一阶段的正确顺序

1. 用五个隔离的新 GPT Pro 会话分别执行已经 sealed 的 Blind prompt；
2. 原样保存每个完整回复、JSON appendix 和真实 source artifacts；
3. 由 importer 做 claim-level、PIT、hash、coverage、leakage 验证，接受、判 insufficient 或 quarantine；
4. 仅对 accepted Blind result 请求 fresh paired YES/NO book；
5. 运行 deterministic MarketComparison、Rule B 和 `NO_ORDER` Prediction Ledger；
6. 汇总五张最终人类卡片：独立概率区间、盘口、可执行 edge、证据质量、阻断原因和复制提示词；
7. 再决定是否执行正式预注册 8-case Gate R WP6；
8. WP6 通过后仍需单独申请 `READ_ONLY_OPERATIONAL_PILOT_GATE`，验证真实运行负载与生产隔离。

## 11. 不应混淆的三个“完成”

- **工程完成**：代码、合同和 fixture 能复放完整链路；这一项基本完成。
- **一次研究完成**：某个真实市场必须经过 GPT Pro、fresh book、Rule B 和 ledger；当前五个都还没完成。
- **运营完成**：能每日稳定扫描且不影响生产 owner；当前未获批，也未验证。

因此当前最准确的项目裁决是：

```text
READY_FOR_CONTROLLED_MANUAL_GPT_PRO_BLIND_CONTINUATION
readiness_scope=ONE_OFF_NO_ORDER_RESEARCH_ONLY
```
