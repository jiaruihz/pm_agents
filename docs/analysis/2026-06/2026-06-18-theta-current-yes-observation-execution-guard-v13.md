# Theta Current YES Observation / Execution Guard v13

Status: research_only / guard_design
Generated: 2026-06-17T16:19:36+00:00

Target metric: `current_yes_obs_execution_guard` = 在 v9 current-YES 候选上，观测时效和衰竭后等待时间是否能提高成功率和收益率。

## 数据完整性自检

- fact_built_at_utc: `2026-06-17T16:09:29.241520+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 31496, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`
- CLOB gate: `{'gate_pass': True, 'fail_reasons': [], 'missing_order_rows': 0, 'over_order_keys': 0, 'db_fill_cost_minus_fact_cost': 0.0}`

## 人话结论

obs-age / METAR blackout 这类 guard 在历史半小时 replay 里没有证明能提升 v9 收益。原因不是 guard 没道理，而是 replay 粒度太粗：真正危险的是像 Helsinki 那种“下一条 METAR 前 1-2 分钟”的分钟级窗口，半小时 orderbook replay 很难直接看见。

把收益口径对齐到 v8/v12 后，v9 holdout 基线回到 31 单 / 29 胜 / ROI +16.2%。但 `obs_age <= 20m` 在半小时 snapshot replay 里一单都留不下，`pre_update_blackout=6m` 又一单都挡不掉；`minutes_since_running_max >= 30m/45m` 也没有挡住两个亏损案例，ROI 反而略低。

所以这次结论是：obs clock 必须作为 live 风控/telemetry 记录，但不是已经被历史 replay 证明的收益增强因子。真正要补的是分钟级 forward 数据，而不是继续在半小时 replay 上调一个看起来漂亮的阈值。

## Guard 结果（holdout, $5/order, taker +2c）

| guard | orders/dates | win | ROI | CI95 | avg ask | obs age | min since max | blackout |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| v9_no_obs_guard | 31/11 | +93.5% | +16.2% | [+2.9%, +27.8%] | 0.794 | 35.3 | 1707.3 | +0.0% |
| obs_age_le_20m | 0/0 | NA | NA | [NA, NA] | NA | NA | NA | NA |
| no_pre_update_blackout_6m | 31/11 | +93.5% | +16.2% | [+2.9%, +27.8%] | 0.794 | 35.3 | 1707.3 | +0.0% |
| combined_obs_clock_guard | 0/0 | NA | NA | [NA, NA] | NA | NA | NA | NA |
| minutes_since_max_ge_30 | 24/9 | +91.7% | +14.7% | [-2.4%, +28.5%] | 0.789 | 33.8 | 2131.3 | +0.0% |
| minutes_since_max_ge_45 | 24/9 | +91.7% | +14.7% | [-2.4%, +28.5%] | 0.789 | 33.8 | 2131.3 | +0.0% |
| obs_clock_and_max_ge_30 | 0/0 | NA | NA | [NA, NA] | NA | NA | NA | NA |

## Slice Diagnostics

| dimension | value | orders/dates | win | ROI | CI95 | avg ask |
|---|---|---:|---:|---:|---|---:|
| obs_age_bin | 30+ | 30/11 | +93.3% | +16.5% | [+2.8%, +28.1%] | 0.789 |
| minutes_since_max_bin | 0-15 | 6/3 | +100.0% | +23.7% | [+21.2%, +27.4%] | 0.792 |
| minutes_since_max_bin | 60+ | 23/9 | +91.3% | +13.8% | [-3.0%, +28.2%] | 0.792 |
| pre_update_blackout_6m | False | 31/11 | +93.5% | +16.2% | [+2.9%, +27.8%] | 0.794 |

## Bad Cases

- v9 holdout losing candidates: `2`.

| date | city | hour | bracket | ask | p | obs age | min-to-next | since max | decline |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 2026-06-01 | Miami | 14 | 90-91 | 0.850 | 0.925 | 37.9 | 22.1 | 97.9 | 1.11 |
| 2026-06-11 | Munich | 15 | 16 | 0.770 | 0.875 | 40.9 | 19.1 | 3310.9 | 3.33 |

## 交易动作

- 不把历史 replay 里的 obs-age / blackout / since-max guard 当成已验证 alpha。
- 实盘准备仍建议保留 `pre_update_blackout=6m`、fresh-book guard、真实 IANA timezone/DST；它们是事故防护，不是收益优化器。
- `obs_age <= 20m` 需要配合分钟级 runner：在半小时 replay 上它会把样本全砍掉，不能直接拿来解释历史 ROI。
- 暂不建议把 `minutes_since_running_max >= 30m` 作为 v9 live hard filter；它减少订单但没有挡住本轮 bad cases。
- 必补分钟级 forward telemetry：每个 would-order 记录 fresh ask、snapshot age、obs age、minutes_to_next_obs、minutes_since_running_max、source profile。

## 三道门

- significance=FAIL：三个简单 guard 都没有在 v8/v12 对齐基线上证明收益增量。
- baseline=PASS：v9 本身仍是 31 单 / 29 胜 / ROI +16.2%，和 v12 对齐。
- forward=FAIL/NA：obs blackout 需要分钟级 forward 数据，半小时 replay 不足以证明。
- conclusion=`telemetry_required` / `risk_guard_only`；不因为 v13 单独升级 live。

## 产物

- JSON: `docs/analysis/2026-06/2026-06-18-theta-current-yes-observation-execution-guard-v13.json`
- guard CSV: `docs/analysis/2026-06/generated/theta_current_yes_observation_execution_guard_v13/guard_summary.csv`
- bin CSV: `docs/analysis/2026-06/generated/theta_current_yes_observation_execution_guard_v13/slice_bins.csv`
- bad cases CSV: `docs/analysis/2026-06/generated/theta_current_yes_observation_execution_guard_v13/bad_cases.csv`
- scored candidates CSV: `docs/analysis/2026-06/generated/theta_current_yes_observation_execution_guard_v13/v9_candidates.csv`
- Script: `scripts/analysis/reheat_risk/research_theta_current_yes_observation_execution_guard_v13.py`
