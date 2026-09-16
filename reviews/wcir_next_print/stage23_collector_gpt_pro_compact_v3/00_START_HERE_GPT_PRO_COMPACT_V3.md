# WCIR Stage 2/3 + collector clock compact review v3

## 这是什么

这是一个仅用于确认审阅主题、合同方向和下一步 disposition 的 compact 包。
它不包含 row-level evidence、50,460 条 oracle rows、841-row 明细、旧 Stage 2/3
evidence roots、源代码或完整 pip freeze，因此不能替代 full evidence seal 的逐行独立验收。

Full evidence seal 已另行冻结：

```text
wcir-stage23-rev2-full-evidence-seal-v2-20260828T021700Z.zip
SHA-256: 121dc3e637a7a6ddbe902098312a3356f4750467a14c92d123974386c36d76a5
```

Bounded network canary full package：

```text
wcir-collector-clock-v2-bounded-network-canary-20260828T021700Z.zip
SHA-256: 813b10c9674059f46b95791a7e86532f344b2d9a83a3b81f2e0ca2273d5283b1
```

## 本轮只审五个主题

### 1. Full evidence seal 是否已从“摘要”升级为可复核证据边界

- 原 15.8m raw-frame replay 未重跑；已验证 immutable Stage 2/3 manifests 和既有
  deterministic replay identities。
- Full package 冻结 row evidence、旧 evidence roots、代码、测试、环境和 strict
  package manifest。
- Extracted package 已验证 exact entry set、86 个文件 hash，并离线重建 9 个确定性产物。

### 2. Causal entry hard gate 是否闭合

全部 policy、size、horizon 和 aggregate 使用：

```text
effective_entry_decision_ready_ts < official_first_seen_ts
AND effective_lead_seconds > 0
```

等号、缺失 clock、不可比较 clock 均 fail closed。Entry book checkpoint 还必须：

```text
entry_checkpoint <= decision_ready
AND entry_checkpoint < official_first_seen
```

Action 不读取 official first_seen 之后的 book/reaction/exit；这些数据只能用于 measurement。

### 3. Legacy latency provenance 是否不再冒充 forward SLO

841/841 rows 均保存 latency provenance。49.773 秒 global pooled p95 明确标记：

```text
LEGACY_DIAGNOSTIC_ONLY
NOT_FORWARD_PRIMARY
```

历史 collector_epoch 缺失保持 NULL，不补造。Future primary 必须按
city + source + collector_epoch、pre-cutoff estimation 和 frozen fallback 另行建立。

### 4. Operational 与 research denominator 是否彻底拆开

```text
EVENT_UNIVERSE                    841
OPERATIONAL_FAIL_CLOSED           841
RESEARCH_MARKET_DATA_ELIGIBLE      89
PAIRWISE_BASELINE_COMPARABLE        1
```

Operational disposition：89 `POLICY_ACTION`、87 `POLICY_ABSTAIN`、665
`DATA_FAIL_CLOSED`。研究不可用 row 的 conditional alpha PnL 为 NULL，不再把数据缺失
和策略主动 abstain 混为一谈。当前仍不授权 alpha/futility/model claim。

### 5. Collector 是否已经从 synthetic-only 前进到 bounded real-network canary

- 一个 Amsterdam weather token 先通过 public CLOB book endpoint 验证 token/condition，
  再进入 public read-only market WS。
- 5 个 bounded phases：initial connect、hard reconnect、process restart、真实 WS frame
  queue overflow、真实 baseline 后 controlled sequence-gap injection。
- 3 个正常/恢复阶段均得到 token-scoped protocol receipt、完整 baseline 和 PONG。
- 13 个 frame metadata、10,603 wire bytes、0 parse error、0 normal-path drop。
- 8/8 controlled faults fail closed。
- 0 credentials、0 order/user channel、0 production consumer、0 daemon、0 orders、
  0 fills、0 notional。

公开 market WS 协议没有 standalone subscription ACK；材料只把第一个 token-scoped
server frame 称为 protocol receipt，不把它伪装成 exchange ACK。单 token 的 100%
request fraction 只代表 canary transport，不代表 future demand coverage，也不计入正式
10 个 clean-forward target dates。

## 请求的两项独立 disposition

Evidence closure 请选择一个：

```text
ACCEPT_STAGE23_REV2_EVIDENCE_CLOSURE
ACCEPT_WITH_BLOCKING_FIXES
REWORK_STAGE23_REV2_EVIDENCE_CLOSURE
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

Collector clock 请选择一个：

```text
ACCEPT_COLLECTOR_CLOCK_AMENDMENT_FOR_ISOLATED_ZERO_NOTIONAL_SHADOW_DEPLOYMENT
ACCEPT_WITH_BLOCKING_FIXES
REWORK_COLLECTOR_CLOCK_AMENDMENT
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

## 不在本轮审阅范围

- 不训练模型；Stage 3 仍为 `CONTINUE_COLLECTION_WITHOUT_MODELING`。
- 不启动正式 forward epoch。
- 不部署常驻 shadow collector；即使 collector amendment 被接受，部署仍需单独授权。
- 不进入 Stage 4。
- 不重新讨论已经接受的 validity/oracle 总体边界，只审上述五个 closure 主题。

## 快速阅读顺序

1. 本文件；
2. `GPT_PRO_REVIEW_PACKET_STAGE23_FULL_SEAL_V2.md`；
3. `GPT_PRO_REVIEW_PACKET_COLLECTOR_CLOCK_V2_CANARY.md`；
4. 四个小型 summary JSON；
5. `NETWORK_CANARY_RESULTS.json` 与 `CONTROLLED_FAULT_MATRIX.json`；
6. `INDEPENDENT_CODE_REVIEW.md`。

如需确认任意 row ID、PnL、lineage、完整代码 provenance 或 package entry hash，应再读取
full evidence seal，而不是要求本 compact 包冒充 full evidence。
