# Adjacent3 Quality Shadow Journal v0

> generated_at_utc: `2026-06-10T14:23:34.839536+00:00`
> git_sha: `f60deeb`
> journal_schema_version: `adjacent3_quality_shadow_journal_v0`
> target_metric: `adjacent3_quality_shadow_forward_readiness`
> Scope: local shadow journal only; no N100/live config changed; no live orders.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- fact_signal_candidates rows：`25117`；fact built：`2026-06-09T17:38:03.570994+00:00`。
- fact_trades rows：`5473`；unsettled/null：`232`；missing_bracket：`0`。
- train：`2026-05-06` -> `2026-05-20`；holdout：`2026-05-26` -> `2026-05-30`。

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

## 固定 Shadow 规则

- `adjacent3_core_no_filter`：围绕模型 mode 选相邻 3 档 YES，`market_cost <= 0.85`，`model_mass > market_cost`。
- `adjacent3_medium_quality`：同上，但要求 train-only 阈值下 `adjacent3_mass` 不低、tail mass 不高。
- 这是 would-trade journal，不是 live 下单；orderbook 只用 `snapshot_ts_utc <= decision_snapshot_ts_utc` 的 time-aligned best ask。

## Filter Funnel

| step | count |
| --- | --- |
| fact_signal_candidates rows | 25117 |
| decision sets | 1145 |
| shadow rows | 1642 |
| orderbook fully matched shadow rows | 29 |
| journal rows | 1642 |

## 当前证据

| rule | split | decision rows | decision ROI | decision top5 removed | orderbook rows | orderbook ROI | orderbook top5 removed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| adjacent3_core_no_filter | train | 19 | +7.8% | -100.0% | 1 | -2.0% | NA |
| adjacent3_core_no_filter | holdout | 19 | +23.7% | NA | 19 | +5.7% | NA |
| adjacent3_medium_quality | train | 7 | +52.9% | -100.0% | 1 | -2.0% | NA |
| adjacent3_medium_quality | holdout | 8 | +72.9% | NA | 8 | +47.1% | NA |

## 三门状态

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | 未跑 bootstrap CI；usable rows 很薄，medium holdout 只有 8 行，不能把点估计当显著性。 |
| baseline | NA | 本产物是 shadow journal，不做无脑 NO / market EV matched baseline 结论。 |
| forward | FAIL | 历史 holdout 点估计为正，但 orderbook 全腿匹配只有 29 行，且没有真实 forward shadow 期。 |

## 人话结论

- 这份产物不是为了证明能 live，而是把最接近的候选固定下来，避免继续事后调参。
- 点估计看起来不错，尤其 `adjacent3_medium_quality` 的 holdout；但样本太薄，train 的 top5 removed 直接变成 -100%，说明现在还可能是少数日期撑起来。
- 如果 shadow 期之后，orderbook taker/maker proxy 在 holdout/forward 仍同号、top5 removed 仍为正、event_date cluster CI 不跨 0，才讨论 tiny live test。
- 当前动作：只记录、不下单、不改 size、不改城市池。

## 产物

- JSON summary：`/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-adjacent3-quality-shadow-journal-v0.json`
- JSONL journal：`/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-adjacent3-quality-shadow-journal-v0.jsonl`
