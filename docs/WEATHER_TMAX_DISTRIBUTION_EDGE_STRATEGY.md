# Weather Tmax Distribution Edge Strategy

Status: current-reference  
Updated: 2026-07-03 tmax_distribution_edge_shadow_v1 runner  
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

同一 `city + target_date + decision_hour_local` 只选择一个最高 edge 表达。  
如果最高 edge 没过阈值，也会记录为 `blocked / below_edge_threshold`。

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

### Verified Settlement: settlement-backed forward rows through 2026-06-30

这组只统计 `label_source=settlement_outcomes` 的 forward rows。当前已接入 6/29-6/30 官方结算；
6/27-6/28 的 settlement 源头目前只有 1 个城市覆盖，且 below/five-bucket 修正还没完成。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_clean_edge02` | 459 | +13.5% | [+2.7%, +23.2%] | 主机制候选 |
| `tmax_dist_city_source_edge02` | 577 | +11.1% | [+4.9%, +17.2%] | 日块较稳，但 city/source 风险更高 |
| `tmax_dist_clean_edge10` | 127 | +49.2% | [+35.7%, +58.8%] | 样本少，仍只作压力测试 |

### Observed-Max Pressure Test: remaining non-settled / non-four-bucket rows through 2026-07-01

这段不是 canonical settlement，只是 observed max derived label 压力测试。这里包含 6/27-6/28、
7/01，以及 6/29-6/30 中官方赢家没有被当前四桶表达干净吸收的 rows；这些正是下一步
`below/current/d1/d2/tail` 五桶修正要处理的部分。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_city_source_edge02` | 247 | +5.5% | [-3.0%, +25.7%] | 点估仍正，但 CI 跨 0 |
| `tmax_dist_clean_edge02` | 207 | +3.0% | [-16.0%, +28.2%] | 方向没坏，但不稳 |
| `tmax_dist_clean_edge10` | 89 | +6.8% | [-21.8%, +63.9%] | 高 edge recent 仍偏薄 |

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

当前 runtime refresh：

```text
source = docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
source_rows = 14088
source_date_range = 2026-06-02..2026-07-01
latest_target_date = 2026-07-01
latest_rows_written = 396
latest_selected = 157
latest_blocked = 239
journal_shadow_rows = 562
```

这证明 runner 可以写 runtime journal 且幂等，不会重复追加同一批 shadow event。

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

重要边界：当前 loop 消费的是 P6 materialized source：

```text
docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
```

这已经足够验证 runtime journal / dashboard registry / selected+blocked event contract，
但还不是完整的 current-day live snapshot selector。要产生真正每天新增的 forward shadow 样本，
下一步需要补：

```text
current snapshot + observation cache -> tmax distribution candidates -> shadow_events.csv/current runtime events
```

## Event Contract

每条 shadow event 至少包含：

- `shadow_event_id`
- `strategy_instance`
- `shadow_config_id`
- `zero_notional`
- `no_order_placed`
- `selection_status`
- `selection_reason`
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

`selected` 和 `blocked` 都必须保留。  
这是这个体系的硬要求，因为后续需要验证“没选的机会是不是其实更好”。

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
