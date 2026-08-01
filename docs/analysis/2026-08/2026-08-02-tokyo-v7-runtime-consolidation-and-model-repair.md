# Tokyo v7 runtime consolidation and model repair

## 结论

本次同时修复了两个独立问题：

1. `city_probability_shadow_v1` / `v2` 是 2026-08-01 当天 contract cutover 留下的短期分裂，
   不应该成为长期兼容层。历史 journal 已规范化合并进唯一 v2 runtime，旧目录、旧 config、旧 start
   script、旧 entrypoint 和旧 instance registry 项均已删除。
2. Tokyo v6 的“每个 bracket 首次正 edge”会在同一个 exact-bracket event 叠加互斥 YES；这是组合
   逻辑 bug。runtime 现在先在同一 checkpoint 比较 YES/NO 的 fee-adjusted edge，再按
   `city_date_model` 只允许一个 position。Tokyo 在模型未通过独立 forward 前关闭 paper-intent
   发射，只保留每个 checkpoint 的 zero-notional probability telemetry。

没有接入 plan/order/fill/exit，没有真实或 paper notional，也没有修改任何真实下单 runner。

## 模型根因与 v7

目标仍是每次 JMA checkpoint 预测“最终 exact maximum 是否停在当前 official bracket”。v6 以同刻
market midpoint 为 offset，再加入 weather/path residual。它的问题不是二元表达本身，而是训练盘口日太少，
且 state-entry 与普通 checkpoint 的误差结构不同。

严格早于 2026-08-01 的 expanding OOF 为 141 checkpoints / 7 target dates：

| slice | rows | market Brier | v6 Brier | v7 Brier |
|---|---:|---:|---:|---:|
| all | 141 | 0.01995 | 0.01412 | **0.01378** |
| state-entry | 10 | **0.00145** | 0.00620 | **0.00145** |
| ordinary | 88 | 0.00513 | **0.00239** | **0.00239** |
| transition | 43 | 0.05458 | **0.03995** | **0.03995** |

因此 v7 的冻结路由为：新档首次观测使用 PIT market anchor；已有同档历史后才使用 v6
weather-market residual。这个选择没有读取 8/1 label。

## 8/1 frozen forward

8/1 只用于规则冻结后的检验，winner 为 35：

| probability head | checkpoints | accuracy | Brier |
|---|---:|---:|---:|
| market | 50 | **100%** | **0.01712** |
| v6 | 50 | 92% | 0.06331 |
| v7 routed | 50 | 94% | 0.05628 |

v7 相对 v6 有改善，但仍明显输给同分母 market。即使应用“YES/NO 取最佳、全天单 position”，2% edge
下的首个反事实候选仍是 11:50 JST 左右的 32 YES，最终失败。这说明只修组合去重不能把坏概率变成
alpha，也不能靠改阈值追着 8/1 调参。

所以部署结论是：`tokyo_state_entry_routed_market_residual_v7` 进入 zero-notional probability shadow，
但 `emit_paper_intents=false`。它不是可交易模型；等 collector-exact settled PIT book 达到 20 个训练日，
再保留至少 10 个完全未读日期作 frozen holdout。届时必须在 checkpoint / transition / state-entry 三个
grain 同分母比较 market Brier/logloss，之后才重新评估是否发射单一 YES 或 NO intent。

复核产物：

- `generated/tokyo_state_entry_routed_v7/audit.json`
- `scripts/analysis/market_structure_edge/audit_tokyo_state_entry_routed_v7.py`

## Runtime consolidation

迁移前 v1 有 180 evaluation rows，v2 有 98；按 `evaluation_id` 合并后为 276，说明 2 条跨目录重复。
历史 paper intents 按 `position_key` 合并后为 10。合并结果：

- canonical runtime：`/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_v2`
- evaluations：276，其中 178 条带明确 `source_schema_version=v1` migration lineage
- paper intents：10，全部历史 zero-notional
- active errors：61；v1 历史 errors 与 data-quality adjustment 保存在 `v2/history/`
- obsolete runtime：`city_probability_shadow_v1` 已删除
- consolidation manifest：`v2/consolidation_report.json`，记录输入 SHA-256

旧 v1 runner log、latest summary 和目录壳未保留，不能从 runtime 恢复；evaluation、intent、error 和
data-quality evidence 均已保留在 v2。代码侧同步删除：

- `configs/weather/city_probability_shadow_v1.json`
- `scripts/ops/start_weather_city_probability_shadow_v1.sh`
- v1 runtime instance；entrypoint 统一为 `weather_city_probability_shadow_v2.py`

## Deployment evidence

- develop commit：`bd475856`
- production cherry-pick：`c7aa4db9`
- checkout：`/Users/deepsleep/projects/pm_agents_city_runtime_v2_tokyo_fix`
- tmux：`weather_city_probability_shadow_v2`
- PID：`80589`
- loaded entrypoint：`scripts/ops/weather_city_probability_shadow_v2.py`
- runtime：`execution_mode=zero_notional_shadow`、`errors=0`、`orders_submitted=0`、
  `new_paper_intents=0`
- tests：22 passed

## 双漏斗

Signal funnel（8/1）：50 unique checkpoints → 100 YES/NO evaluations → 单 city-day 候选 1（失败）
→ deployed paper intents 0。

Evidence funnel：50 settled PIT checkpoints / 1 target date；actual orders 0，actual fills 0。
这仍是单日 forward，不能发布 ROI 或晋级 live。
