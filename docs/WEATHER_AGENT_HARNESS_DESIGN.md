# Weather Agent Harness 设计

Status: `implemented / portable-multi-agent-core`
Updated: 2026-08-13
Scope: agent 长任务执行；不替代 production controller、StrategyHead/BaseRunner 或测试框架

Implementation: `src/weather_agent_harness/` · generic CLI: `scripts/ops/agent_harness.py` · weather CLI: `scripts/ops/weather_agent_harness.py` · tests: `tests/research_tests/test_weather_agent_harness.py`

## 1. 核心决定

采用 **薄内核 + 领域插件**：同一个 agent harness 负责持续执行、状态恢复、权限和完成判定；weather 生产审计与策略研究是内置插件，其他项目通过 `TaskSpec.domain` 的 portable contract 接入，不修改顶层 `status/context/certify` 分支。

```text
User Goal
   ↓
Task Compiler ──→ TaskSpec
   ↓
Agent Run Loop ←→ Context/Skills
   ↓                 ↓
Policy Engine → Typed Tools
   ↓                 ↓
Evidence Ledger ← Action Result
   ↓
Completion Verifier → Trace → Graders → Run Certification
   ├─ CONTINUE
   ├─ COMPLETE
   ├─ WAIT_FOR_EVIDENCE
   └─ REQUIRE_AUTHORITY
```

Harness 不负责替模型写策略，也不把工作固化为一条死流水线。它负责保证模型：知道目标、保留状态、只能做获准动作、根据结果继续选择下一步，并且不能用中间结果冒充完成。

## 2. 最小内核

| 组件 | 职责 |
|---|---|
| `TaskSpec` | 目标、范围、验收条件、权限、预算、冻结输入；可携带 portable `DomainSpec` |
| `RunState` | phase、已完成项、blocker、inflight checkpoint、下一动作 |
| `ContextBuilder` | 按任务加载 production identity、相关 skill、family 证据；不堆巨型 prompt |
| `ToolRegistry` | 把现有脚本/API 包成 typed tool，声明副作用与证据输出 |
| `PolicyEngine` | 自动执行、需授权、禁止三类动作判定 |
| `EvidenceLedger` | 单 writer + hash chain 的 append-only 记录，保存输入 identity、动作、结果、代码/config/data hash |
| `CompletionVerifier` | 根据机器证据判终态，不接受 agent 自报“完成” |
| `TrustedVerifierRunner` | 在 coordinator 侧执行声明式 hash、JSON assertion 或有界 command verifier；worker 的 `acceptance_claims` 只作陈述 |
| `Certification` | 独立重放 verifier，并检查 ledger、证据内容 hash、authority、acceptance 与未收口 inflight action |

建议落盘：`production.yaml.research_artifact_root/agent_harness/<run_id>/`。canonical DB 继续只承载领域事实，不写 agent 过程状态。

结果明确拆成三轴：`process_certified`（过程合同可信）、`artifact_verified`（证据内容和机器验收可信）、`domain_outcome`（业务结论）。兼容字段 `harness_certified` 只在三者都可重放时为真。比如 `COMPLETE_FALSIFIED + process/artifact PASS` 表示“策略假设失败，但执行过程和证据可信”，不能把 certification PASS 解释为 alpha 通过。

## 3. 业界设计映射

本实现不照搬某个 SDK，而是落地 OpenAI 与 Anthropic 官方工程资料共同强调的边界：

| 业界做法 | 本项目落点 |
|---|---|
| OpenAI 把 harness 定义为 instructions、tools、routing、output contract、validation 的整体，并以 traces → feedback/evals → harness changes 迭代 | `TaskSpec + Domain + ToolRegistry + EvidenceLedger + Certification`；每个真实 case 同时是可复跑 eval |
| OpenAI Agents SDK 用 session 支持持久状态/恢复，用 tracing 记录 model/tool/guardrail/handoff，并把 approval interruption 做成可恢复状态 | `RunState`、`inflight_action`、`trace.json`、exact-action approval、`recover-interrupted` |
| OpenAI 明确区分 trusted harness 与 compute/sandbox；harness 持有 loop、tools、secrets、guardrails、tracing | `HarnessEngine` 不接受任意 shell；真实 weather 脚本经 typed adapter 调用，production controller 与资金权限不下放给 planner |
| Anthropic 长任务采用 initializer/incremental agent 与结构化 progress artifact，避免一轮做太多或过早宣布完成 | 初始化 `TaskSpec` 后逐 action 推进，状态/evidence 跨 context 保存，Verifier 独占终态权 |
| Anthropic 将 planner/generator/evaluator 分开，并强调 evaluator-optimizer 只在评价标准清晰时使用 | Planner 只选动作，tool 生成结果，domain verifier + deterministic graders 评价；策略循环必须有 proper score/market/forward/execution 标准 |
| Anthropic managed agents 抽象为 append-only session、harness loop、sandbox compute | `evidence.jsonl`、`HarnessEngine`、agent-side adapter 分层对应；本项目不另造一套 sandbox runtime |

官方参考：[OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents#choose-one-conversation-strategy)、[observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)、[agent evals](https://developers.openai.com/api/docs/guides/agent-evals)、[guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)、[agent improvement loop](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop)、[harness / compute boundary](https://developers.openai.com/cookbook/examples/agents_sdk/migrate-from-claude-agent-sdk/readme#why-migrate)；[Anthropic long-running harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)、[planner/generator/evaluator](https://www.anthropic.com/engineering/harness-design-long-running-apps)、[building effective agents](https://www.anthropic.com/engineering/building-effective-agents)、[agent evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)、[managed agents](https://www.anthropic.com/engineering/managed-agents)。

## 4. 多代理扩展定案：Adaptive Agent Harness

最终采用 **Sol 控制面 + Codex 原生 subagent 调度 + Terra/Luna 执行面 + Harness 认证层**。不自建模型会话系统，不自建通用 DAG/workflow engine；Harness 只持久化项目需要的依赖、权限、证据和终态。

```text
User Request
    ↓
RequestRouter
    ├─ L0 Direct：Sol 直接完成，不创建 run
    ├─ L1 Single：单代理 Harness
    ├─ L2 Multi：Sol → WorkOrders → Terra/Luna → Verifier
    └─ L3 Controlled：L2 + production/资金/删除 authority pause
                                     ↓
                     Trace + Certification + RunReceipt
```

### 4.1 路由规则

- 默认走 L0；简单问答、小范围只读检查、单文件低风险修改不进入 Harness。
- 多阶段、需要反复迭代、中断恢复或客观验收时进入 L1。
- 有两个以上真正独立的工作包，或需要执行者之外的复核时进入 L2。
- 生产、资金、删除和不可逆外部副作用强制进入 L3。
- 用户明确要求完整闭环、不要停在中间态或使用多代理时，至少进入 L1/L2。

同一用户目标的多轮补充继续写入同一个 run；新的独立目标创建新 run，必要时用 `parent_run_id` 关联。Harness 不保存完整聊天或模型思维，只保存结构化事件、摘要和 artifact 指针。

### 4.2 角色与责任

| 角色 | 责任 | 权限边界 |
|---|---|---|
| Sol | 编译目标、拆 WorkOrder、决定依赖和并行、处理冲突、整合结果、推进终态 | 不能用自己的结论替代独立 grader；不可逆动作仍需用户授权 |
| Terra | 复杂实现、根因诊断、研究实验、跨模块修复 | 不扩大 scope，不宣布整个 run 完成 |
| Luna | 快速检索、数据抽取、批量检查、回归验证、窄范围 reviewer | 默认 read-only；模型不可用时必须显式记录 fallback，不能冒充已调用 |
| Harness | 状态、依赖、预算、权限、evidence、trace、certification | 不替 agent 做业务判断 |

角色配置使用项目级 `.codex/agents/*.toml`；实际 spawn、消息路由、等待、interrupt 和 thread 生命周期复用 Codex 原生能力。并行优先用于互不依赖的读取/分析；写任务只设一个明确 owner，同文件写冲突时串行，不默认给每个任务创建 worktree。

### 4.3 最小编排合同

只增加四个持久对象，不引入通用 workflow DSL：

```python
class WorkOrder:
    work_order_id: str
    objective: str
    role: str
    depends_on: list[str]
    scope: dict
    acceptance: list[str]
    verifiers: list[dict]        # acceptance_id → hash / JSON assertion / command
    authority: str
    status: str                 # pending / ready / running / review / complete / blocked

class WorkResult:
    work_order_id: str
    status: str
    summary: str
    evidence_refs: list[str]
    evidence_records: list[dict] # captured URI/snapshot/SHA-256/size/producer
    mutations: list[dict]
    unresolved: list[str]
    observed_model: str
    execution_mode: str          # agent / coordinator_recovery
    usage: dict                  # measured Codex session tokens

class RoleSpec:
    name: str
    requested_model: str
    reasoning_effort: str
    sandbox: str
    allowed_tools: list[str]
    writable_roots: list[str]
    network_access: bool
    require_exact_model: bool
    require_usage: bool
    baseline_model: str

class RunReceipt:
    run_id: str
    route_level: str
    outcome: str
    harness_certified: bool
    process_certified: bool
    artifact_verified: bool
    domain_outcome: str
    agents: list[dict]
    usage: dict
    worker_usage: dict
    coordinator_usage: dict
    retry_waste_credits: float
    usage_coverage_ratio: float
    evidence_refs: list[str]
```

依赖解析只做一件事：`depends_on` 全部完成的 WorkOrder 变成 `ready`。只有出现跨进程队列、定时调度、数百节点、分布式重试或 SLA 后，才考虑接入 Temporal 等外部 workflow engine。

### 4.4 执行记录

L1–L3 每个 run 只生成一份最终 `run_receipt.json`，包含：实际/请求模型、reasoning、worker/thread 数、WorkOrder/重试数、wall time、tool calls、测试结果、文件/commit/artifact identity、终态和认证指针。成功 WorkOrder 默认必须从专属 Codex session JSONL 提取真实 input/cached/output token；缺 usage 或 requested/observed model 不一致时不得 accept。失败和重试 attempt 的可用 usage 同样计入 worker 总量，并单列 `retry_waste_credits`；`usage_coverage_ratio` 公开未测量 attempt，不能用部分 usage 冒充全成本。`coordinator_recovery` 是 execution mode，不是模型名；若 Sol 接管 Terra/Luna 任务，必须作为显式 fallback 重新派单并单独计费。

成本使用 ChatGPT Codex credits 计量，不把 credits 伪装成 USD。当前 versioned rate card（访问于 2026-08-13）按每 1M tokens 记录：Sol `125 / 12.5 / 750`、Terra `50 / 5 / 300`、Luna `5 / 0.5 / 30`（input / cached input / output）；fast mode 乘 `2.5x`。每条 usage 同时计算实际模型 credits 与相同 token mix 的 Sol baseline，receipt 汇总 `estimated_cost_credits`、`baseline_cost_credits` 和 routing savings。这个 same-token baseline 只衡量模型路由折扣；要判断 Harness 是否真的比原单-agent 便宜，还必须与一个实测单-agent control receipt 比较总 credits，因为多 agent 会增加总 token。来源：[OpenAI Codex pricing](https://developers.openai.com/codex/pricing/) 与 [Codex multi-agents](https://developers.openai.com/codex/multi-agent/)。

扩展点固定为接口而不是提前实现：`Dispatcher`（当前 Codex，未来可换 API/远端队列）、`UsageProvider`、`ArtifactStore`、`DomainProfile`。核心合同不绑定 weather，也不绑定 Sol/Terra/Luna 的具体版本号。中立入口为 `scripts/ops/agent_harness.py`；weather CLI 只保留领域初始化与场景命令。

### 4.5 当前实现与下一边界

已实现 L0–L3 router、WorkOrder dependency/write ownership、attempt/lease fencing、heartbeat 延租、expiry sweep、异常 runtime terminal callback、机器 verifier accept、exact-model/usage gate 和 terminal RunReceipt。每次 `status/ready/dispatch/receipt` 先 sweep；Codex wait 返回 interrupted/cancelled/crashed/lost 时，dispatch integration 立即调用 terminal callback。长时间无回调的 worker 在 lease 到期后生成正式 failed `WorkResult`，未耗尽 attempt 时自动回到 ready，耗尽后进入 failed。每个 attempt 使用独立 thread，避免复用 thread 的累计 token 无法归因或重复计费。当前是 bounded tick/sweep，不是后台常驻 supervisor：它能可靠回收超时 lease，但不能在没有 coordinator 调用时主动拉起新的 Codex thread。

dispatch 会把角色编译为可执行的 `EffectiveExecutionProfile`：`luna_verifier → read-only`、`terra_worker → workspace-write`，并把原生 `agent_type` 写入派工合同。当前 Codex spawn 边界不能逐 WorkOrder 动态实施 tool allowlist、任意 writable roots 或 network policy，因此这些声明一旦非空就 fail closed；`write_owners` 只负责调度互斥，不冒充 OS sandbox。需要更细权限时，应预先定义 Codex permissions profile 后再扩展 dispatcher。参考 [OpenAI Codex config reference](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml)。

L3 WorkOrder 还有独立的 authority gate：`production_change / live_funds / destructive` 只有当自身稳定 `work_order_hash` 精确出现在 `TaskSpec.authority.explicit_action_grants` 时才允许 prepare/start；两层都校验，不能绕过 dispatcher 直接启动。该 grant 只覆盖这一份不可变 WorkOrder，不扩散到同风险后续动作。

顶层 domain 由 `DomainRegistry` 解析：weather task type 命中代码插件；`chainlove.bounty_batch` 等其他类型走 portable `GenericDomain`。portable WorkOrder 只有在 coordinator 侧 verifier 覆盖全部 acceptance 且通过后，才写入带 verifier id 与 evidence hash 的 `machine_verified_acceptance`，然后复用同一套 `status → context → certify → receipt`。单纯写 `completed_acceptance` 或 worker 自报 claims 不再能让 GenericDomain 完成。回归测试已覆盖 `chainlove.bounty_batch` 以及伪造验收、失败 assertion 和证据篡改攻击。

2026-08-13 ChainLove pilot 暴露了一个控制面缺口：WorkOrder 写了 Terra，但 coordinator 调用原生 spawn 时漏传 model override，实际线程继承为 Sol；retry 又把 `coordinator-recovery` 写进 model 字段，implementation review 与 submission gate 还复用了累计 usage 的同一 thread。旧 receipt 因 usage 为空没有暴露成本失控。修复后的合同把 `model + fork_turns=none` 放进 dispatch payload，在 dispatch/result 两处校验实际模型，从 session 提取 usage，并要求每个 attempt 使用独立 thread；以上任一项缺失都不能完成验收。

## 5. 统一合同

```python
class TaskSpec:
    task_type: str
    objective: str
    scope: dict
    acceptance: list[str]
    authority: dict
    budgets: dict
    frozen_inputs: dict
    domain: dict | None          # portable phase/tools/terminal contract

class ActionResult:
    status: str                 # succeeded / failed / blocked
    evidence_refs: list[str]
    findings: list[dict]
    mutations: list[dict]
    suggested_next_actions: list[str]

class CompletionDecision:
    state: str                  # continue / complete / wait_for_evidence / require_authority
    unmet_acceptance: list[str]
    evidence_refs: list[str]
```

所有 tool 必须返回 `ActionResult`；自由文本只能解释结果，不能充当完成证据。

## 6. 主题包 A：生产审计与修复

状态机：

```text
PREFLIGHT → SYNC → RECONCILE → EXPOSURE → LINEAGE
→ DIAGNOSE → REPAIR → REPLAY → TEST → DELIVER
```

它不是固定命令序列：例如 reconcile 发现 canonical 缺 fill，agent 可转入最小 refresh；修复后 verifier 会把任务送回 replay，而不是允许直接交付。

完成条件必须同时满足：

- production/canonical identity 已验证；
- 账户与 exchange fills 对平，或有逐条未对平清单；
- 全部 open exposure 有估值时钟；
- 目标窗口 lineage 已覆盖；
- 已确认问题完成修复和受影响窗口 replay；
- 错单、漏单、变化单有数字与逐条清单；
- 测试通过并生成一页摘要。

生产部署、真实下单、扩大 notional、删除数据仍返回 `REQUIRE_AUTHORITY`。

## 7. 主题包 B：策略研究与训练

适用，但必须拆成两个隔离循环，防止“自主迭代”变成反复偷看 holdout 的自动过拟合。

### 7.1 Development search loop

```text
READINESS
  → establish same-denominator baseline
  → propose one falsifiable change
  → train + OOF/expanding evaluation
  → compare proper score / calibration / executable value
  → diagnose errors
  → keep or reject candidate
  → next iteration
```

每轮保存 `ExperimentRecord`：

- `parent_run_id` 与本轮唯一变化；
- hypothesis、data/build/config/code identity；
- signal/evidence funnel；
- 与 market、当前 champion 的同 rows 指标；
- 接受/拒绝理由和下一轮动作。

模型可以反复改特征、模型或表达，但每轮只改变一个可归因因素。价格带、城市或结果切片不能在看到结果后直接变成 eligibility gate。

### 7.2 Qualification loop

当 development champion 达到预注册门槛：

1. 冻结 code、params、feature schema、training dates 和 denominator。
2. 关闭 searcher 对 frozen-forward labels/results 的读取权限。
3. 在 untouched forward 上只执行一次资格评测。
4. 输出 `shadow_candidate / keep_research / falsified / wait_for_evidence`。
5. 若看过 forward 后再改模型，必须创建新 generation，并等待新的 forward 窗口；不能回头把旧 forward 当 clean evidence。

### 7.3 研究终止规则

Harness 不要求“跑到盈利才停”。允许停下的终态只有：

| 终态 | 必需证据 |
|---|---|
| `COMPLETE_QUALIFIED` | 预注册 probability、market baseline、forward、execution 门全部通过 |
| `COMPLETE_FALSIFIED` | 假设在固定分母上失败，且约定的合理 challenger/消融预算已完成 |
| `WAIT_FOR_EVIDENCE` | 缺新的 settlement、PIT book 或 frozen dates；已启动 collector/shadow 并记录恢复条件 |
| `REQUIRE_AUTHORITY` | 下一步涉及 live、资金或扩大采集/部署范围 |

单次训练成功、某个正 ROI 切片、一个中间模型、一次报错、预算尚未用完，都不是终态。

## 8. 预算与防失控

`budgets` 至少包含：

- 最大实验数、计算时长和并行度；
- 每轮只允许一个主变化；
- `patience`：连续若干轮无 OOF/market-baseline 改善后进入收敛审查；
- 最大候选族数量，禁止无界换题；
- artifact/网络/采集预算；
- 用户给出的 token/时间预算（如有）。

预算耗尽本身不等于成功。Verifier 应输出 `COMPLETE_FALSIFIED`（证据已足）或带明确恢复条件的 `WAIT_FOR_EVIDENCE`，不能交一个“目前试了一版”的中间态。

## 9. 代码边界

```text
src/weather_agent_harness/
├── contracts.py
├── engine.py
├── context.py
├── policy.py
├── evidence.py
├── certification.py
├── planner.py
├── tools.py
├── catalog.py
├── orchestration/
│   ├── contracts.py
│   ├── router.py
│   ├── dependency.py
│   ├── dispatch.py
│   ├── store.py
│   └── receipt.py
├── scenarios/
│   └── market_prior_training.py
└── domains/
    ├── generic.py
    ├── registry.py
    ├── production_audit.py
    └── strategy_research.py
```

- 现有 weather skills 是领域说明，由 `ContextBuilder` 按 phase 装载。
- 现有 sync/reconcile/lineage/replay/research runner 通过 adapter 注册为 typed tools，不复制实现。
- `weather_production_ctl.py` 仍是生产控制面的唯一入口。
- `StrategyHead/BaseRunner` 是策略进程 runtime；本设计是驱动 agent 长任务的 runtime，两者不合并。

## 10. 完整纵向场景

主验收 case 是真实 Busan market-prior residual：读取 SHA 锁定的 archived PIT artifact（936 rows / 35 target_dates），自动完成 market baseline、5 个 ridge 的 expanding-date OOF、champion freeze 和一次 678 rows / 22 dates temporal holdout。development 选出 `ridge=0.3`，holdout logloss delta 却为 `+0.08159`、fee-adjusted PnL `-$8.78`，因此 Verifier 收敛到 `COMPLETE_FALSIFIED`。完整报告：[Busan real-case HTML](analysis/2026-08/2026-08-13-weather-agent-harness-busan-real-case.html)。

这证明 Harness 的目标不是“自动跑出成功”，而是强制模型跑到可信终态：中间 improvement 不能提前交付，holdout 失败必须否决。该 historical holdout 已被项目看过，只用于 Harness replay 与机制证伪，不冒充新的 clean-forward admission。

另保留 deterministic golden scenario 作为内核回归测试：

`market_prior_training_golden_v1`：

- 构造 deterministic、PIT-shaped golden fixture；它只认证 Harness，不作为 alpha 证据；
- development 固定 12 个 target_date、1,920 rows，同分母比较 market baseline 与 4 个单因素 challenger；
- searcher 读取 forward rows 数为 0；最佳 challenger 冻结 code/params/schema/training dates/denominator identity；
- 在 6 个 untouched forward target_date、960 rows 上只 qualification 一次；
- 7 个 typed actions 后由 verifier 收敛到 `COMPLETE_QUALIFIED`，不是由 planner 自报完成；
- 每个 action、result、completion check 均写入 append-only evidence；
- 开跑前执行真实 production preflight。若 production critical，场景仍可在隔离 fixture 上完成，但禁止触碰 canonical 或恢复生产。

完整人类报告：[market-prior Harness 场景 HTML](analysis/2026-08/2026-08-13-weather-agent-harness-market-prior-scenario.html)。机器证据位于 `runtime/weather_agent_harness_runs/market-prior-golden-20260813-certified/`。

生产 E2E audit 的状态机与 agent-side adapters 也已经落地并由测试锁定，但在 production preflight critical 时不会冒充完成或越权恢复。多代理最小内核已经实现；通用工作流 DSL、Web UI 和自动生产部署不属于当前范围。

每次完整运行额外生成三个机器合同：`trace.json`（逐 action span）、`certification.json`（10 个 grader，分别覆盖 process/artifact/domain）和 `run_manifest.json`（输入、sealed evidence snapshot 与 SHA-256 inventory）。小于等于 10 MiB 的 worker evidence 在 record 时复制到 run 内 immutable-by-contract snapshot；更大文件保留外部 URI，但固定 hash/size，并在 accept、certify、receipt 三处重验。

## 11. 使用方式

```bash
# 直接跑完整 golden scenario，并生成 HTML
.venv/bin/python scripts/ops/weather_agent_harness.py run-market-prior-training \
  --run-id market-prior-golden-20260813-certified \
  --report-out docs/analysis/2026-08/2026-08-13-weather-agent-harness-market-prior-scenario.html

# 直接跑真实 Busan archived PIT case
.venv/bin/python scripts/ops/weather_agent_harness.py run-busan-market-prior-case \
  --run-id busan-market-prior-real-20260813-certified \
  --report-out docs/analysis/2026-08/2026-08-13-weather-agent-harness-busan-real-case.html

# 生产 E2E 审计任务
.venv/bin/python scripts/ops/weather_agent_harness.py init-production-audit \
  --last-target-dates 30

# 单一 family 自主研究任务
.venv/bin/python scripts/ops/weather_agent_harness.py init-strategy-research \
  --family FAMILY \
  --hypothesis "ONE FALSIFIABLE HYPOTHESIS" \
  --scope '{"target_dates":"development_only"}'

# Agent 每轮读取当前状态和唯一允许的 typed tools
.venv/bin/python scripts/ops/weather_agent_harness.py status --run-dir RUN_DIR
.venv/bin/python scripts/ops/weather_agent_harness.py context --run-dir RUN_DIR

# Agent 先校验 ActionRequest；高风险动作会在这里转 REQUIRE_AUTHORITY
.venv/bin/python scripts/ops/weather_agent_harness.py prepare \
  --run-dir RUN_DIR --action-json ACTION.json

# 通过既有 adapter 执行动作后，写入结构化 ActionResult
.venv/bin/python scripts/ops/weather_agent_harness.py record \
  --run-dir RUN_DIR --action-json ACTION.json --result-json RESULT.json

# 进程在 action 中间退出后：仅 replay_safe tool 可释放重试；其他动作转人工对账
.venv/bin/python scripts/ops/weather_agent_harness.py recover-interrupted --run-dir RUN_DIR

# 独立重放 verifier 并生成 trace / certification / run manifest
.venv/bin/python scripts/ops/weather_agent_harness.py certify --run-dir RUN_DIR

# 先把用户目标结构化，再由 deterministic router 判 L0-L3
.venv/bin/python scripts/ops/weather_agent_harness.py route --profile-json PROFILE.json

# 在已有 Harness run 上初始化角色、增加 WorkOrder、生成 Codex 派工合同
.venv/bin/python scripts/ops/weather_agent_harness.py init-orchestration \
  --run-dir RUN_DIR --profile-json PROFILE.json --roles-json ROLES.json
.venv/bin/python scripts/ops/weather_agent_harness.py add-work-orders \
  --run-dir RUN_DIR --work-order-json WORK_ORDER.json
.venv/bin/python scripts/ops/weather_agent_harness.py prepare-dispatch \
  --run-dir RUN_DIR --work-order-id WORK_ORDER_ID

# worker 定期续 lease；runtime 异常终止时直接写标准 terminal callback
.venv/bin/python scripts/ops/agent_harness.py heartbeat-work-order \
  --run-dir RUN_DIR --work-order-id WORK_ORDER_ID --attempt N --lease-id LEASE_ID
.venv/bin/python scripts/ops/agent_harness.py record-agent-terminal \
  --run-dir RUN_DIR --work-order-id WORK_ORDER_ID --attempt N --lease-id LEASE_ID \
  --runtime-status interrupted

# supervisor tick；status/ready/dispatch/receipt 也会自动执行同一 sweep
.venv/bin/python scripts/ops/agent_harness.py reap-work-orders --run-dir RUN_DIR

# worker 回传后记录证据；accept 会执行 WorkOrder 中预声明的机器 verifier
.venv/bin/python scripts/ops/weather_agent_harness.py record-work-result \
  --run-dir RUN_DIR --result-json WORK_RESULT.json \
  --codex-session-jsonl ~/.codex/sessions/YYYY/MM/DD/rollout-SESSION.jsonl
.venv/bin/python scripts/ops/weather_agent_harness.py accept-work-order \
  --run-dir RUN_DIR --work-order-id WORK_ORDER_ID
.venv/bin/python scripts/ops/weather_agent_harness.py write-receipt --run-dir RUN_DIR \
  --coordinator-session-jsonl ~/.codex/sessions/YYYY/MM/DD/rollout-SOL-SESSION.jsonl
```

生产审计领域工具标为 `agent_side`：现有 weather scripts/API 仍完成真实动作，Harness 只接受带 durable `evidence_refs` 的成功结果。完整 golden scenario 则注册了可直接执行的 typed tools，可在单进程内从 readiness 跑到 qualification。生产变更、live funds 和 destructive 动作不会自动执行；命中时状态转为 `REQUIRE_AUTHORITY`。`approve --reason ...` 只写入当前 pending action（tool + arguments）的 fingerprint，不会把同风险的后续动作一并放行。

当前已实现并由测试锁定：checkpoint/resume、inflight crash recovery、阶段工具约束、失败预算、exact-action 权限、hash-chained ledger、trace、三轴 certification、L0–L3 routing、WorkOrder dependencies、write ownership、heartbeat/expiry/runtime-exit recovery、attempt/lease stale-result fencing、sealed `EvidenceRecord`、trusted machine verifier、portable domain fail-closed completion、原生 agent sandbox mapping、requested/observed exact-model gate、Codex session usage 抽取、失败/重试成本与 coverage、versioned credit accounting、同 token Sol baseline、terminal RunReceipt，以及生产/研究领域原有完成门。
