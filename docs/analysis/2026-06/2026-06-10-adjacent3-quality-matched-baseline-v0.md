# Adjacent3 Quality Matched Baseline v0

> generated_at_utc: `2026-06-09T18:47:32.543929+00:00`
> git_sha: `2e31cd9`
> target_metric: `forecast_quality_adjacent3_matched_baseline_excess`
> Scope: local research only; no N100/live config changed; no live orders.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- fact_signal_candidates event range：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。
- train：`2026-05-13` -> `2026-05-27`；holdout：`2026-05-28` -> `2026-05-30`。

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

## Baseline 定义

- `model_adjacent3_no_filter`：所有 cost/edge 合格的 model-mode adjacent3。
- `model_adjacent3_medium_quality`：同一 model-mode adjacent3，但要求 train-only medium quality。
- `same_cost_random_adjacent3_for_medium`：只在 medium_quality 触发的同一个 decision set 内，找成本差不超过 0.05 的其它 adjacent3，取 baseline mean。
- `matched_market_mode_adjacent3_for_medium`：只在 medium_quality 触发的同一个 decision set 内，改买 market-mode adjacent3；当前主要用于退化诊断。

## 当前证据

| rule | split | decision rows | decision ROI | decision CI | decision top5 removed | orderbook rows | orderbook ROI | orderbook CI | orderbook top5 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model_adjacent3_no_filter | train | 6 | -16.4% | [-100.0%, +67.0%] | NA | 3 | +44.3% | [-100.0%, +58.0%] | NA |
| model_adjacent3_no_filter | holdout | 6 | +53.8% | [-100.0%, +189.9%] | NA | 6 | +35.8% | [-100.0%, +137.5%] | NA |
| model_adjacent3_medium_quality | train | 3 | +62.5% | [-100.0%, +79.2%] | NA | 3 | +44.3% | [-100.0%, +58.0%] | NA |
| model_adjacent3_medium_quality | holdout | 2 | -100.0% | [-100.0%, -100.0%] | NA | 2 | -100.0% | [-100.0%, -100.0%] | NA |
| same_cost_random_adjacent3_for_medium | train | 0 | NA | NA | NA | 0 | NA | NA | NA |
| same_cost_random_adjacent3_for_medium | holdout | 0 | NA | NA | NA | 0 | NA | NA | NA |
| matched_market_mode_adjacent3_for_medium | train | 3 | +62.5% | [-100.0%, +79.2%] | NA | 3 | +44.3% | [-100.0%, +58.0%] | NA |
| matched_market_mode_adjacent3_for_medium | holdout | 2 | -100.0% | [-100.0%, -100.0%] | NA | 2 | -100.0% | [-100.0%, -100.0%] | NA |

## Excess vs Baseline

| selected | baseline | split | source | selected rows | baseline rows | selected ROI | baseline ROI | excess ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model_adjacent3_medium_quality | model_adjacent3_no_filter | train | decision_proxy | 3 | 6 | +62.5% | -16.4% | +78.9% |
| model_adjacent3_medium_quality | model_adjacent3_no_filter | train | orderbook_taker | 3 | 3 | +44.3% | +44.3% | +0.0% |
| model_adjacent3_medium_quality | model_adjacent3_no_filter | holdout | decision_proxy | 2 | 6 | -100.0% | +53.8% | -153.8% |
| model_adjacent3_medium_quality | model_adjacent3_no_filter | holdout | orderbook_taker | 2 | 6 | -100.0% | +35.8% | -135.8% |
| model_adjacent3_medium_quality | same_cost_random_adjacent3_for_medium | train | decision_proxy | 3 | 0 | +62.5% | NA | NA |
| model_adjacent3_medium_quality | same_cost_random_adjacent3_for_medium | train | orderbook_taker | 3 | 0 | +44.3% | NA | NA |
| model_adjacent3_medium_quality | same_cost_random_adjacent3_for_medium | holdout | decision_proxy | 2 | 0 | -100.0% | NA | NA |
| model_adjacent3_medium_quality | same_cost_random_adjacent3_for_medium | holdout | orderbook_taker | 2 | 0 | -100.0% | NA | NA |
| model_adjacent3_medium_quality | matched_market_mode_adjacent3_for_medium | train | decision_proxy | 3 | 3 | +62.5% | +62.5% | +0.0% |
| model_adjacent3_medium_quality | matched_market_mode_adjacent3_for_medium | train | orderbook_taker | 3 | 3 | +44.3% | +44.3% | +0.0% |
| model_adjacent3_medium_quality | matched_market_mode_adjacent3_for_medium | holdout | decision_proxy | 2 | 2 | -100.0% | -100.0% | +0.0% |
| model_adjacent3_medium_quality | matched_market_mode_adjacent3_for_medium | holdout | orderbook_taker | 2 | 2 | -100.0% | -100.0% | +0.0% |

## Baseline 退化诊断

| field | value |
| --- | --- |
| medium_quality_rows | 157 |
| matched_market_mode_rows | 157 |
| model_mode_equals_market_mode_rows | 157 |
| model_mode_equals_market_mode_rate | 1.0 |
| same_cost_random_candidate_rows | 2 |
| same_cost_random_matched_decision_sets | 2 |
| same_cost_random_match_rate | 0.012738853503184714 |
| interpretation | Market-mode can degenerate when model and market select the same adjacent3. Same-cost random is the primary non-degenerate matched baseline. |

## 三门状态

| gate | status |
| --- | --- |
| significance | FAIL |
| baseline | FAIL |
| forward | FAIL |
| conclusion | inconclusive |

## 人话结论

- medium_quality adjacent3 的点估计仍然好看，但相对 same-cost random adjacent3 的 baseline 证据还不够硬。
- 最公平的 live-readiness baseline 是同一个 decision set 内的 same-cost random adjacent3；它控制了 city/date/time/width/cost。
- market-mode adjacent3 在当前样本里与 model-mode adjacent3 完全重合，所以只作为退化诊断，不能证明模型相对市场 mode 有额外 alpha。
- 当前动作仍是 shadow/paper，不给 live 动作。
