# Live-Test Readiness Scoreboard v0

Runner lifecycle: `retired-snapshot`. This historical scoreboard is retained
for lineage but must not be regenerated as a current readiness view; current
strategy status comes from `WEATHER_STRATEGY_REGISTRY.md` and the production
manifest. Exact producer source is recoverable as git blob
`e7d19f49291f0d13d60078876b23e7e4410f209a`.

> generated_at_utc: `2026-06-09T18:48:31.920035+00:00`
> git_sha: `2e31cd9`
> Scope: synthesis of submitted fact-table research; no N100/live config changed; no live orders.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`，以及已提交研究 JSON。
- fact_signal_candidates event range：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。
- 主 opportunity usable 近似分母：`{'rows': 918, 'event_dates': 26, 'city_days': 372}`。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-09T17:37:40.513320+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "n": 1405
    },
    {
      "trade_class": "live_simulated",
      "n": 1147
    },
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": null,
      "n": 232
    },
    {
      "settlement_status": "settled",
      "n": 5241
    }
  ],
  "candidate_coverage": {
    "rows": 25117,
    "eligible": 8306,
    "paper_ordered": 3139,
    "live_filled": 554
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 1635,
      "with_fill": 1405
    }
  ]
}
```

## 一句话结论

当前仍不选真钱 live-test。最接近的是 `forecast-quality adjacent3`，但只能做 shadow/paper；`all-YES underround` 统计上 confirmed，但执行复杂且用户已明确暂不实盘。

## Readiness 表

| candidate | readiness | decision | gates | evidence | reason |
| --- | --- | --- | --- | --- | --- |
| all-YES underround | confirmed_but_deferred | 不选当前 live | PASS/PASS/PASS | verdict=confirmed; confirmed_orderbook_algorithms=5; fully_matched_strategy_rows=137 | 统计三门通过，但用户已明确 all-YES 暂不实盘；多腿速度、partial fill、滑点和手续费执行风险仍是主问题。 |
| forecast-quality adjacent3 | best_shadow_candidate | 不选 live，选 shadow/paper 主线 | FAIL/NA/FAIL | decision holdout rows=8, decision ROI=+72.9%; orderbook rows=8, orderbook ROI=+47.1%; eligible matched-baseline holdout rows=2, eligible matched-baseline orderbook ROI=-100.0%; same-cost random match rate=+1.3% | 点估计最好且逻辑贴近天气预测，但 full-opportunity 样本薄；eligible matched-baseline 主口径下 holdout 更薄且不支持 live。 |
| single high-conviction YES | feature_not_strategy | 不选 live | FAIL/FAIL/FAIL | decision holdout rows=101, decision ROI=+35.8%; orderbook rows=100, orderbook ROI=+23.0%; inside adjacent3 rate=+99.8% | 99%+ 都在 adjacent3 内，更多是重复加注或降级表达，不是独立互补 edge。 |
| side-band + forecast regime | rejected_for_live | 不选 live | FAIL/FAIL/FAIL | holdout selected rows=10, selected ROI=-67.3%; baseline ROI=-18.7%; excess ROI=-48.6% | 真钱早期赚过是真的，但 clean holdout 反向，top5 stress 不稳。 |
| outside-range NO overlay | concept_only | 不选 live | FAIL/FAIL/FAIL | hybrid v0 固定归一化口径下触发 0 行。 | 概念上互补，但当前 fact 近窗固定规则没有样本。 |

## 下一步门槛

| candidate | needed evidence |
| --- | --- |
| all-YES underround | 只做工程 shadow：完整篮子下单仿真、partial fill unwind、fee/slippage 容量测试。 |
| forecast-quality adjacent3 | 固定规则跑 forward shadow：>=30 event_dates、>=100 settled decisions、>=50 full orderbook matched decisions、same-cost random baseline excess CI 下界 >0。 |
| single high-conviction YES | 只作为 adjacent3 太贵/盘口不全时的 fallback 记录，不做叠加真钱。 |
| side-band + forecast regime | 保留为特征输入，不再按旧 side-band 规则独立实盘。 |
| outside-range NO overlay | 等更长 opportunity fact 或 shadow 期自然触发后再评估。 |

## 冻结 Shadow 规则

- rule_id：`forecast_quality_medium_adjacent3_shadow_v0`
- status：`frozen_shadow_not_live`
- 人话：只在模型分布非常集中、尾部很低、价格没贵到离谱时，记录 mode 附近三档 YES would-trade。

| field | value |
| --- | --- |
| range_width | 3 |
| range | model mode around adjacent 3 YES brackets |
| decision_hours_to_settle | [22, 24] |
| market_cost_sum_max | 0.85 |
| range_edge_min | 0.0 |
| model_adjacent3_mass_min | 0.9841447792833155 |
| model_tail_mass_outside_adjacent3_max | 0.015855220716684548 |
| entropy | record_only_not_hard_gate |

记录字段：

- `rule_version`
- `git_sha`
- `generated_at_utc`
- `city`
- `event_date`
- `forecast_source`
- `model_version`
- `decision_snapshot_ts_utc`
- `decision_hours_to_settle`
- `selected_brackets`
- `leg condition_id/market_id/bracket/model_p_yes/market_yes_price`
- `market_cost_sum`
- `model_adjacent3_mass`
- `model_tail_mass_outside_adjacent3`
- `entropy`
- `mode_probability`
- `range_edge`
- `per-leg orderbook_snapshot_ts_utc <= decision_snapshot_ts_utc`
- `best ask/spread/depth/full-match/taker cost`
- `settlement final bracket and event_date cluster metrics`

## Live-Test Readiness 硬门

| requirement | threshold |
| --- | --- |
| forward_event_dates_min | 30 |
| settled_shadow_decisions_min | 100 |
| full_orderbook_matched_decisions_min | 50 |
| decision_proxy_cluster_ci95_lower_gt_zero | True |
| orderbook_taker_cluster_ci95_lower_gt_zero | True |
| matched_baseline_excess_ci95_lower_gt_zero | True |
| top5_removed_orderbook_roi_gt_zero | True |
| no_orderbook_leakage | all orderbook_snapshot_ts_utc <= decision_snapshot_ts_utc |

## 推荐路径

- real live test candidate：`None`。
- primary shadow：`forecast_quality_adjacent3`。
- engineering shadow：`all_yes_underround`。
- next action：`freeze forecast_quality_medium_adjacent3_shadow_v0 and accumulate forward shadow evidence`。
- 原因：没有非 all-YES 策略通过三门。all-YES 是 confirmed 但执行复杂且用户暂不实盘；forecast-quality adjacent3 是最接近天气预测核心逻辑的 shadow 主线。
