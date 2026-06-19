# Weather 策略复盘链路（一条策略跑完后，怎么一步步复盘）

Status: `current-reference`
Updated: 2026-06-19 首版
Source of truth: 口径以 `WEATHER_ANALYSIS_CONTRACT.md` 为准；本文只把已有零件串成有序流水线

零件早就齐了（contract 定口径、5 个 skill 给流程、4 个 living doc 收结论、看板做可视化、canonical 表做事实），
但缺一根"按什么顺序复盘"的总线。这份就是那根总线。**它是 strategy-agnostic 基础设施**——换策略方向不重做它，
任何 live / shadow 策略跑完都走同一条链。

> 关联主轴：[0]–[6] 分层定义见 [WEATHER_ARCHITECTURE_SPINE.md](WEATHER_ARCHITECTURE_SPINE.md)；
> 试过哪些策略、各自状态见 [WEATHER_STRATEGY_REGISTRY.md](WEATHER_STRATEGY_REGISTRY.md)。

## 链路总览（挂在量化血缘上）

```text
[0] 数据可信吗  →  [4] 成交质量  →  [5] 绩效归因  →  [5] 账户对账  →  [3] 关联参数  →  [6] 结论=交易动作
 preflight gate     fill/slippage    PnL by slice     wallet/CLOB      strategy_config    keep/filter/shadow/stop
                         │
                    live vs shadow 对比贯穿全程（execution_mode=zero_notional_shadow 的影子单与真实单同口径比）
```

## 各阶段：回答什么 · canonical 源 · 硬 gate · skill · living doc · 输出

| # | 阶段 | 回答什么 | canonical 源 | 硬 gate | skill | living doc |
|---|---|---|---|---|---|---|
| 0 | **数据自检 preflight** | 数据新鲜、底表重建、覆盖够吗 | `WEATHER_ANALYSIS_CONTRACT §0` | 先 sync+rebuild；`weather_clob_fill_coverage_gate.py gate_pass=true`；5 行 SQL；8 环覆盖自检 | `weather-fact-rebuild` | `analysis/data_integrity.md` |
| 1 | **成交质量 / 执行** | 单子成没成、成交价、滑点、live vs shadow | `fact_signal_candidates`（执行微结构三源）；`orders`/`fills` | `submitted/posted/actual_fill_cost` 分开报；不绕 fact 自算滑点 | `weather-strategy-performance` | `analysis/execution_quality.md` |
| 2 | **绩效归因** | PnL/ROI/胜率，按 instance/city/side/date 切片 | `fact_trades`（强制唯一取数源） | 绩效三道门；settled 才报 `pnl_usd_at_fill`，未结算只报 MTM+`val_snapshot_ts_utc`；near-binary 归一化 | `weather-strategy-performance` | `analysis/live_performance.md` |
| 3 | **逐笔血缘 / 单日复盘** | 这单为什么下：signal→plan→order→fill→settlement | `signals`/`plans`/`orders`/`fills`/`settlements` | 用 canonical 链，不自拼 | `weather-strategy-lineage` | （挂 live_performance） |
| 4 | **账户对账** | DB 和钱包/CLOB 对得上吗 | `fills` + raw CLOB + public activity | `fill_date_bj`（**不用 order_date_bj**）；fill_id reconciliation | `weather-live-account-reconcile` | `analysis/account_reconcile.md` |
| 5 | **关联策略参数** | 哪组参数产生了这个结果 | `fact_trades.strategy_instance` → `strategy_config` / `config_aliases` | 参数变更走 deploy 的 git-first | `weather-strategy-deploy` | `WEATHER_CITY_POOL_DECISIONS.md` |
| 6 | **结论 = 交易动作** | 保留 / 过滤 / 降 size / shadow / 停 | 上述切片证据 | 先给动作再给证据；动作回写决策文档 | — | `STRATEGY_ENTRYPOINT` / `CITY_POOL_DECISIONS` |

## live vs shadow 怎么比

- shadow 跑零 notional，记录 `execution_mode=zero_notional_shadow` / `no_order_placed=true`（如 `range_rv_shadow_v0` / `station_basis_shadow`）。
- 复盘时把 shadow 的"假设成交"和 live 真实成交**放进同一套口径**（阶段 1+2），看：同窗口同信号，live 的滑点/逆选/未成交把 shadow 纸面 edge 吃掉多少。
- 这是判断"shadow 看着行的策略真上 live 会不会塌"的关键，也是 current YES 卡在 execution freshness 的复盘入口。

## 入口

- **看板**（人看）：`run_stack.sh` → `/weather/runs`（按 run 复盘）、`/weather/live`（实盘逐单 + live/shadow）。
- **skill**（agent 跑）：按上表阶段 invoke 对应 skill；标准问题别直接写一次性 pandas（见 contract 禁止清单）。
- **口径**：任何数字落地前回 `WEATHER_ANALYSIS_CONTRACT.md` 对口径。

## 反馈回路

阶段 6 的动作回写 `WEATHER_CITY_POOL_DECISIONS.md`（城市池升降级/回滚条件）和 `WEATHER_STRATEGY_ENTRYPOINT.md`（live 实例状态），
策略本身的状态/灵感/血缘归属更新到 `WEATHER_STRATEGY_REGISTRY.md`。复盘 → 决策 → 注册，闭环。
