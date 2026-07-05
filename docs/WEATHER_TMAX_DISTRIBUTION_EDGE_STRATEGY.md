# Weather Tmax Distribution Edge Strategy

Status: current-reference
Updated: 2026-07-05 exact-book bridge experiment
Source of truth: yes for this strategy family
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_STRATEGY_REGISTRY.md

## 当前结论

这条策略目前叫：

```text
tmax_distribution_edge
```

当前运行实例：

```text
tmax_distribution_edge_shadow_v1
```

状态：

```text
zero-notional shadow
no_order_placed = true
live_action = none
```

人话：它现在不是实盘策略，也不是 paper 下单策略，而是一个按真实策略逻辑持续记录“会选什么表达、为什么选/为什么不选”的 forward 取证系统。
Lucknow 7/05 首个 live 日暴露出执行层去重/持仓血缘问题后，tmax tiny-live 保持暂停；任何恢复 live
都必须先证明 runner 执行口径与回测口径一致。

## 策略本体

旧版本是：

```text
识别 weather regime -> 固定 route 到 current NO / d2 NO / current YES
```

现在的版本是：

```text
估计 Tmax 最终分布 -> 计算每个可买表达的 P(win)-ask -> 选择最高 edge 表达
```

模型输出四个 bucket：

- `current`
- `d1`
- `d2`
- `tail`

可选表达：

- `current YES`
- `current NO`
- `d1 NO`
- `d2 NO`

核心公式：

```text
model_edge = P(expression wins | market + weather state) - ask
```

同一 `city + target_date + decision_hour_local` 先只选择一个最高 edge 表达。
执行/绩效主口径再按 live-like 风险单位收敛：同一 `shadow_config_id + scope + city + target_date`
只把第一条 edge-pass 机会标成 `selected`。

后续同一 city-day 再次触发的小时信号不会丢弃，会记录为
`blocked / city_day_after_first_selected`，用于复盘“如果重复追单会怎样”。
如果最高 edge 没过阈值，则记录为 `blocked / below_edge_threshold`。

## Exact-Book Bridge v1

Lucknow 复盘后的表达层实验不是“删掉 d1 NO”或“马上改买 d1 YES”，而是先把 exact bracket 的
YES/NO sibling 放到同一个矩阵里比较。

当前已完成的最小 bridge：

```text
fixed four-bucket probability: current / d1 / d2 / tail
legacy_4expr: current_yes / current_no / d1_no / d2_no
bridge_6expr: legacy_4expr + d1_yes + d2_yes
bridge_no_current_yes_5expr: current_no / d1_no / d2_no / d1_yes / d2_yes
selection: first eligible city-day, fee-adjusted edge >= 0.02
ask source for d1/d2 YES: 1 - sibling NO bid
```

关键结果见
[2026-07-05-tmax-exact-book-bridge-v1.md](analysis/2026-07/2026-07-05-tmax-exact-book-bridge-v1.md)：

- `ask>=0.40 + fee_edge02` verified 上，`bridge_no_current_yes_5expr` 点估 +9.7%，legacy_4expr +8.4%。
- dev/verified 的 CI 仍宽，且 `d1_yes` 单腿偏弱；所以这是 `shadow_bridge_complete_not_live`，不是 live 替代。
- `current_yes` 在 settlement-basis / below bucket 修复前仍只做 shadow；`d1_no` 保留为合法补集表达。

这版 bridge 还不是 full ladder target book。真正完整版本需要逐格 hazard / full-ladder 概率、
实时 sibling book、以及 target-book reconciliation 的平仓成本账本。

## 为什么不是继续用原来的 live 版本

原来的 `regime_routed_no` 更像 rule-based route：

- `day_open_runway / day_marginal_runway -> current NO`
- `day_forecast_capped -> d2 NO`

它的问题不是“没成交”这么简单，而是表达层不够统一：
天气形态只能说明状态，不应该直接决定买哪一档。

新体系把它拆开：

```text
weather/regime/context = 特征
bucket probability = 模型输出
ask/expression = 执行选择
selected/blocked = shadow 记录
```

这样不会因为某天错了就继续加 gate，也不会因为当前 runner 没成交就误判没有机会。

## 当前候选配置

| config | method | edge threshold | 角色 |
|---|---|---:|---|
| `tmax_dist_clean_edge02` | `loo_no_city_source_blend` | 0.02 | 主候选：机制更干净，少依赖 city/source 记忆 |
| `tmax_dist_city_source_edge02` | `mkt_city_source_blend` | 0.02 | 容量/城市源偏移对照：点估更强、交易更多 |
| `tmax_dist_clean_edge10` | `loo_no_city_source_blend` | 0.10 | 高 edge 压力测试：旧样本和 verified 很强，但 recent 变薄 |

## Evidence Summary

### Verified Settlement: settlement-backed forward rows after 2026-07-05 settlement rejoin

这组只统计 `label_source=settlement_outcomes` 的 forward rows。2026-07-04 重新把 atlas 老 shard
接到 Single Runs PIT backfill，2026-07-05 又补齐 7/03-7/04 pm_history settlement 并重跑
atlas/P5/P6 后，点估仍为正；clean primary 的 verified CI 下界刚转正，但这仍是 backfill/rejoin
证据，不是真 P7 fresh-forward。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_clean_edge02` | 226 | +10.5% | [+0.8%, +20.0%] | 主机制候选，唯一 tiny-live 候选，但仍需 P7 fresh-forward |
| `tmax_dist_city_source_edge02` | 236 | +11.3% | [-1.7%, +24.3%] | 点估正，但 city/source 风险更高 |
| `tmax_dist_clean_edge10` | 33 | +34.3% | [-18.7%, +92.6%] | 样本太薄，只作压力测试 |

### Observed-Max Pressure Test: remaining non-settled / non-four-bucket rows

当前 P6 feature/state layer 只覆盖到 7/03，且 7/03 已经由 pm_history settlement 验证；
因此本轮 `extension_forward = 0`。7/04 已有 settlement_outcomes，但还没有进入 atlas state rows，
不能算作 P6 分母。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_city_source_edge02` | 0 | n/a | n/a | 无 extension rows |
| `tmax_dist_clean_edge02` | 0 | n/a | n/a | 无 extension rows |
| `tmax_dist_clean_edge10` | 0 | n/a | n/a | 无 extension rows |

结论：

```text
conclusion = inconclusive_positive_signal
promotion = no live
next_action = zero-notional shadow
```

## Runtime

当前 shadow runner：

```bash
.venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py run
```

循环运行：

```bash
scripts/ops/start_tmax_distribution_edge_shadow_v1.sh
```

当前本机 shadow loop：

```text
tmux session = tmax_distribution_edge_shadow_v1
mode = zero-notional shadow
orders = none
```

默认 runtime 目录：

```text
runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/
```

关键文件：

| 文件 | 含义 |
|---|---|
| `shadow_events.jsonl` | append-only shadow event journal |
| `latest_events.json` | 最近一轮完整 selected/blocked events |
| `latest_summary.json` | dashboard / registry 可读摘要 |
| `summary_history.jsonl` | 每轮 summary 历史 |
| `shadow_loop.log` | start script 循环日志 |

当前 materialized source refresh：

```text
source = docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
source_rows = 16416
source_date_range = 2026-06-02..2026-07-03
selected_rows = 1562
blocked_rows = 14854
```

这证明 P6 source 已经刷新到当前可评分分母；runtime loop 是否已消费这批 source 需要看
`runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/latest_summary.json`，不能用旧 journal 数字替代。

Runtime registry 已接入：

```text
strategy_instance = tmax_distribution_edge_shadow_v1
lifecycle_status = shadow
execution_mode = zero_notional_shadow
health_status = healthy
shadow_rows = 90
telemetry_rows = 90
blocker_count = 0
```

重要边界：`tmax_distribution_edge_shadow_v1` 这个旧 shadow loop 消费的是 P6 materialized source：

```text
docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
```

这已经足够验证 runtime journal / dashboard registry / selected+blocked event contract，
但它不是完整的 current-day live snapshot selector。current-day 入口现在由
`tmax_distribution_edge_live_candidate_v1` 负责：

```text
current snapshot + observation cache -> tmax distribution candidates -> latest_candidates/latest_blocked/trade_plans -> weather_order_executor
```

## Tiny-Live Candidate Policy v1

2026-07-05 收敛出的执行候选：

```text
strategy_id = tmax_dist_clean_edge02_tiny_live_v1
runtime_state = zero_notional_shadow_only
model_config = tmax_dist_clean_edge02 / loo_no_city_source_blend
expression_set = current_yes / current_no / d1_no / d2_no
selector = max(p_win - ask)
edge_threshold = 0.02
ask_floor = 0.20
shares = fixed 5 shares
dedupe = first accepted per city + target_date
daily_time_order_cap = none
mechanism_filter = no_trend3h_flat
trend3h_flat = -0.5F <= temp_trend_3h_f < +0.5F
missing_trend3h = block for live candidate; record in blocked telemetry
```

`no_trend3h_flat` 是 row-level mechanism filter，不是整天 veto：如果上午 3h flat 被 block，
后面同 city-day 重新升温并再次出现合格信号，可以重新评估。这个避免把“早盘蓄势 flat”和
“峰值附近熄火 flat”混成一个永久 city-day 禁止项。

当前 candidate runner：

```bash
scripts/ops/start_tmax_distribution_edge_candidate_shadow_v1.sh
```

默认输出：

```text
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_summary.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_events.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_blocked.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/summary_history.jsonl
```

Realtime candidate bridge 已接入：

```text
current snapshot -> candidate event -> fresh CLOB ask -> weather_order_executor
```

默认仍是 paper executor，不会真实下单：

```bash
.venv/bin/python scripts/ops/tmax_distribution_edge_live_candidate_v1.py run --execute --max-orders 1
scripts/ops/start_tmax_distribution_edge_live_candidate_v1.sh
```

默认 runtime：

```text
runtime/weather_edge_v1/tmax_distribution_edge_live_candidate_v1/
```

真实 live 边界：

```text
default = paper_executor_only
live requires = --live --confirm-live
start script requires =
  TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_LIVE=1
  TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_CONFIRM_LIVE=1
```

执行前检查：

```text
before order:
  refresh CLOB orderbook
  use fresh best ask, not best bid
  require fresh ask size >= 5 shares
  require fresh ask <= snapshot ask + 0.02
  require p_win - fresh_ask - 0.05 * fresh_ask * (1 - fresh_ask) >= 0.02
exit = hold to settlement
```

2c 磨损测试是 taker 保守压力测试，不是 maker 预期。Maker 可能改善价格，但会引入
fill selection bias：能成交的票可能正是价格朝我们不利方向移动的票。因此 maker-first
不能直接把 2c 当收益加回去；必须单独记录 maker quote、成交率、未成交反事实和成交后 PnL。

## Event Contract

每条 shadow event 至少包含：

- `shadow_event_id`
- `shadow_schema_version`
- `strategy_instance`
- `shadow_config_id`
- `selection_policy`
- `zero_notional`
- `no_order_placed`
- `selection_status`
- `selection_reason`
- `city_day_eligible_rank`
- `scope`
- `city`
- `target_date`
- `decision_hour_local`
- `method`
- `chosen_expression`
- `ask`
- `p_win`
- `model_edge`
- `actual_bucket`
- `label_source`
- `day_regime`
- `intraday_state`
- `running_max_state`

当前 `selection_policy = first_eligible_city_day`。
`selected` 和 `blocked` 都必须保留。这是这个体系的硬要求，因为后续需要验证
“没选的机会是不是其实更好”，同时避免把 hourly 诊断行误当成实际会重复下注的回测行。

## 和当前没成交 live runner 的关系

`regime_routed_no_soft_balanced_tiny_live_v1` 是旧 rule-route 体系。
它最近没成交，可能来自：

- 盘口/最小股数/金额约束；
- fresh feature / snapshot 缺口；
- route-price discipline 后候选变少；
- 策略本身表达过窄。

但这个现象不能直接用来判断 `tmax_distribution_edge`。
新体系现在只做 zero-notional forward 取证，不依赖实际成交。

## Promotion Gates

这条线要从 shadow 升级，至少需要：

1. 真 forward shadow 连续运行。
2. official settlement 补齐，而不是 observed-derived label。
3. depth/fill feasibility replay，不能只看 ask。
4. 至少 10 个新 settled forward dates。
5. 每个主 config 至少 80 个 settled selected events。
6. 相对 market/local baseline 或同分母 no-trade baseline 有正超额。
7. 日块 bootstrap CI 不跨 0。
8. 明确最大单日亏损和容量。

没过这些之前，不改 live、不 size-up。

## 下一步

当前已经完成：

- research synthesis
- shadow event contract
- runtime shadow runner
- smoke test
- runtime registry / dashboard visibility

下一步：

1. 等 atlas / orderbook / settlement 更新后，用 `--refresh-source` 或上游定时 job 刷新 source artifact。
2. 跑连续 fresh-forward shadow。
3. 补 official settlement 后重算 selected/blocked 的真实结果。
4. 再决定是否需要做 depth/fill-aware shadow replay。

## Related Artifacts

- Report: `docs/analysis/2026-07/2026-07-03-tmax-distribution-research-synthesis-v1.md`
- Shadow telemetry report: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md`
- Runner: `scripts/ops/tmax_distribution_edge_shadow_v1.py`
- Start script: `scripts/ops/start_tmax_distribution_edge_shadow_v1.sh`
- Runtime journal: `runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/shadow_events.jsonl`
