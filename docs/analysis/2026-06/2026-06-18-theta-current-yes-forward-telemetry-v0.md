# Theta Current YES Forward Telemetry v0

Status: implementation / no_live_policy_change
Generated: 2026-06-18

Target metric: `theta_current_yes_forward_telemetry_v0` = 为 current-YES no-reheat 分支补齐分钟级 would-order 证据层，之后才能按真实前瞻样本测算成功率、收益率和事故 guard。

## 数据完整性自检

- fact_built_at_utc: `2026-06-17T16:09:29.241520+00:00`
- fact_trades trade_class: `live_real=855, live_simulated=624, paper=2285, snapshot_replay=636`
- settlement_status: `settled=4250, null=150`
- fact_signal_candidates coverage: `rows=31496, eligible=10961, paper_ordered=4274, live_filled=348`
- CLOB orders/fills join: `error=33/0 fills, submitted=961/855 fills`
- CLOB gate: `gate_pass=true`, `missing_order_rows=0`, `over_order_keys=0`, `db_fill_cost_minus_fact_cost=0.0`

## 人话结论

v13 的关键结论不是“再调一个阈值”，而是历史半小时 replay 看不见 Helsinki 那种 METAR 更新前 1-2 分钟事故。所以这次把 current-YES tiny-live runner 改成每轮记录 forward telemetry：不管最后是 planned、fresh-book rejected、snapshot-rule rejected，还是 obs/hour guard blocked，都留下同一套可结算字段。

这不是 live 升级，也不改变下单策略、仓位、城市池或 taker 条件。它只是让后续成功率 / ROI 不再靠半小时 replay 猜，而是靠真实运行时的 would-order 样本。

## 已落地字段

Runtime file:

```text
runtime/weather_edge_v1/theta_current_yes_tiny_live_v1/forward_telemetry.jsonl
```

每条 row 的核心字段：

- decision: `decision_status`, `telemetry_run_id`, `city`, `target_date`, `current_bracket`, `decision_local_time`, `decision_timezone`
- observation clock: `obs_source`, `obs_age_min`, `obs_cadence_min`, `minutes_to_next_obs`, `last_obs_utc`, `running_max_obs_utc`, `minutes_since_running_max`
- weather state: `current_temp_c`, `running_max_c`, `decline_c`, `gap_running_to_d1_low_c`
- execution: `yes_current_ask`, `available_notional_at_ask`, `fresh_best_ask`, `fresh_ask_size`, `taker_limit_price`, `edge_at_fresh_ask`, `edge_at_limit`
- forecast clock: `forecast_peak_hour_local`, `forecast_peak_time_local`, `forecast_peak_source`, `forecast_values_hash`, `forecast_peak_delta_hours_local`
- source profile: `source_profile_class`, `source_profile_primary_source`, `source_profile_station_or_feed`, `source_profile_live_eligible`

## 验证

- `scripts/ops/weather_theta_current_yes_tiny_live.py` compiles.
- `tests/pmm_tests/test_theta_current_yes_live_guards.py` passes: `9 passed`.
- 新测试覆盖：
  - `fetch_obs` 计算 `running_max_obs_utc` 和 `minutes_since_running_max`
  - dry-run `run_once` planned candidate 写入 `forward_telemetry.jsonl`
  - telemetry row 保留 obs、fresh book、forecast peak delta 字段

## 三道门

- significance=NA：这是证据层落盘，不是收益结论。
- baseline=NA：没有新 alpha claim。
- forward=IN_PROGRESS：从部署/运行后开始积累真实 would-order 样本。
- conclusion=`telemetry_required_done_locally` / `no_live_policy_change`。

## 下一步

1. 部署到 N100 前走 `weather-strategy-deploy` git-first 流程。
2. 运行 current-YES loop 后，每天同步并审计 `forward_telemetry.jsonl`。
3. 等有 settled forward 样本后，按 `decision_status`、obs clock、forecast peak delta、fresh-book slippage 分层重算 hit rate / taker ROI。
