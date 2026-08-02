# Weather 策略复盘链路（一条策略跑完后，怎么一步步复盘）

Status: `current-reference`
Updated: 2026-08-03 WCIR decision contract, canonical build identity, JRS control plane
Source of truth: 口径以 `WEATHER_ANALYSIS_CONTRACT.md` 为准；本文只把已有零件串成有序流水线

零件早就齐了（contract 定口径、8 个 weather skill 给流程、living docs 收结论、看板做可视化、canonical 表做事实），
但缺一根"按什么顺序复盘"的总线。这份就是那根总线。**它是 strategy-agnostic 基础设施**——换策略方向不重做它，
任何 live / shadow 策略跑完都走同一条链。

> 关联主轴：[0]–[6] 分层定义见 [WEATHER_ARCHITECTURE_SPINE.md](WEATHER_ARCHITECTURE_SPINE.md)；
> 试过哪些策略、各自状态见 [WEATHER_STRATEGY_REGISTRY.md](WEATHER_STRATEGY_REGISTRY.md)。

## 链路总览（挂在量化血缘上）

```text
[0] 数据可信吗 → [1-3] 概率/机制 → [4] 成交质量 → [5] 绩效归因 → [5] 账户对账 → [3] 参数 → [6] 动作
 preflight gate     market residual    fill/slippage     PnL by slice    wallet/CLOB     config    shadow/stop/keep
                         │
                    live vs shadow 对比贯穿全程（execution_mode=zero_notional_shadow 的影子单与真实单同口径比）
```

## 各阶段：回答什么 · canonical 源 · 硬 gate · skill · living doc · 输出

| # | 阶段 | 回答什么 | canonical 源 | 硬 gate | skill | living doc |
|---|---|---|---|---|---|---|
| 0 | **数据自检 preflight** | 数据新鲜、现有底表覆盖够吗 | `WEATHER_ANALYSIS_CONTRACT §0` | 先查 freshness/build identity/目标窗口；live fill 先最小 canonical refresh，WCIR 用审批的增量 materialization，仅全局失效且明确同意时 `--rebuild`；发布 live_real 前 `weather_clob_fill_coverage_gate.py gate_pass=true` | `weather-fact-rebuild` | `analysis/data_integrity.md` |
| 1 | **机制 / 概率 / residual** | PIT state 是否在同分母 proper score 上增量胜 market | `fact_signal_candidates` + canonical feature/model artifact + settlement | signal/evidence 双漏斗；source basis；frozen forward；不从 selected ROI 反推模型 | `weather-strategy-research` | family living doc |
| 2 | **成交质量 / 执行** | 单子成没成、成交价、滑点、live vs shadow | `fact_signal_candidates`；current raw `orders/fills` | submitted/posted/actual fill、fee evidence 分开；maker touch 不当 fill | `weather-strategy-performance` | `analysis/execution_quality.md` |
| 3 | **绩效归因** | fee-adjusted PnL/ROI/概率质量，按 instance/city/source/side/date 切片 | `fact_trades` + fixed-denominator opportunity/model rows | 三道门；同分母 market baseline；YES/NO 分拆；target-date bootstrap | `weather-strategy-performance` | `analysis/live_performance.md` |
| 4 | **逐笔血缘 / 单日复盘** | EventEnvelope→DecisionContext→ModelOutput→SignalCandidate→TradeIntent→handoff→plan/order/fill/settlement | current raw + canonical chain | 四时钟、candidate grain、feature/execution quote、intent/blocker、submitted/fill/cap/rejection 分拆；事故后影响重放 | `weather-strategy-lineage` | incident/family living doc |
| 5 | **账户与敞口** | 现金、fees、reserved、open positions、settled | raw CLOB + `fact_trades` | `fill_date_bj`；fill_id/fee reconciliation；三估值 | `weather-live-account-reconcile` / `weather-strategy-exposure` | `analysis/account_reconcile.md` |
| 6 | **参数与动作** | 哪组参数产生结果；是否 collector/shadow/keep/stop | `instance_id` / `config_id` / runtime command | 只有 confirmed 才讨论扩 live；生产行为走 git-first deploy | `weather-strategy-deploy` | registry / entrypoint |
| 7 | **JRS runtime 故障切换** | 是权限宿主、磁盘还是进程上下文问题；如何保留唯一可写正本 | stopped runtime tree + canonical helper + raw/exchange pre-state | canonical write probe；tiered/exact tree verification；禁止双 writer；live 启停仍需明确授权 | `weather-jrs-runtime-failover` + `weather-strategy-deploy` | incident/governance living doc |

## live vs shadow 怎么比

- shadow 跑零 notional，记录 `execution_mode=zero_notional_shadow` / `no_order_placed=true`（如 `range_rv_shadow_v0` / `station_basis_shadow`）。
- 复盘时把 shadow 的"假设成交"和 live 真实成交**放进同一套口径**（阶段 1+2），看：同窗口同信号，live 的滑点/逆选/未成交把 shadow 纸面 edge 吃掉多少。
- 这是判断"shadow 看着行的策略真上 live 会不会塌"的关键，也是 current YES 卡在 execution freshness 的复盘入口。

## 入口

- **看板**（人看）：`run_stack.sh` → `/weather/runs`（按 run 复盘）、`/weather/live`（实盘逐单 + live/shadow）。
- **skill**（agent 跑）：按上表阶段 invoke 对应 skill；标准问题别直接写一次性 pandas（见 contract 禁止清单）。
- **口径**：任何数字落地前回 `WEATHER_ANALYSIS_CONTRACT.md` 对口径。

## 反馈回路

阶段 6 的动作回写 family living doc、`WEATHER_STRATEGY_REGISTRY.md` 与 `WEATHER_STRATEGY_ENTRYPOINT.md`。
`WEATHER_CITY_POOL_DECISIONS.md` 只保留 2026-05/06 历史城市池账，不再承载当前实例 allowlist。复盘 → 决策 → 注册，闭环。
