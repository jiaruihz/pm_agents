# Hybrid Adjacent3 + Single / Outside NO v0

> generated_at_utc: `2026-06-09T18:27:54.178082+00:00`
> git_sha: `f2cb544`
> target_metric: `city_day_hybrid_adjacent3_single_alpha`
> Scope: local research only; no N100/live config changed; no live orders.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- fact_signal_candidates event range：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。
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

## 固定规则

- `adjacent3_base`：围绕模型 mode 买相邻 3 档 YES，`market_cost <= 0.85` 且 `model_mass > market_cost`。
- `adjacent3_medium_quality`：同上，附加 train-only forecast quality soft gate。
- `single_high_conviction_yes`：每个 city-day decision set 最多选 1 个 YES，要求固定阈值 `model_prob_norm >= 0.18`、`model_prob_norm - market_yes_price >= 0.10`、`market_yes_price <= 0.25`。
- `adjacent3_medium_plus_single_yes`：medium adjacent3 之外最多叠加 1 个外侧强 YES；如果强 YES 已在 adjacent3 内，不重复加仓。
- `outside_range_no_overlay`：只看 adjacent3 外侧，若市场 YES 比模型高估至少 10pct、模型命中概率不超过 20%、NO 成本不超过 0.80，则买该外侧档 NO。

## Filter Funnel

| step | count |
| --- | --- |
| fact_signal_candidates rows | 25117 |
| decision sets | 1145 |
| eval rows | 2645 |
| settled eval rows | 303 |
| orderbook matched eval rows | 152 |

## 当前证据

| rule | split | decision rows | decision ROI | decision CI | decision top5 removed | orderbook rows | orderbook ROI | orderbook CI | orderbook top5 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adjacent3_base | train | 19 | +7.8% | [-46.2%, +55.5%] | -100.0% | 1 | -2.0% | [-2.0%, -2.0%] | NA |
| adjacent3_base | holdout | 19 | +23.7% | [-67.9%, +71.1%] | NA | 19 | +7.6% | [-72.5%, +48.2%] | NA |
| adjacent3_medium_quality | train | 7 | +52.9% | [-35.6%, +121.6%] | -100.0% | 1 | -2.0% | [-2.0%, -2.0%] | NA |
| adjacent3_medium_quality | holdout | 8 | +72.9% | [-17.1%, +110.1%] | NA | 8 | +49.5% | [-25.3%, +81.6%] | NA |
| single_high_conviction_yes | train | 106 | +40.4% | [-19.9%, +69.7%] | -61.6% | 1 | -100.0% | [-100.0%, -100.0%] | NA |
| single_high_conviction_yes | holdout | 101 | +35.8% | [-24.0%, +103.2%] | NA | 100 | +23.0% | [-31.1%, +84.0%] | NA |
| adjacent3_medium_plus_single_yes | train | 0 | NA | NA | NA | 0 | NA | NA | NA |
| adjacent3_medium_plus_single_yes | holdout | 0 | NA | NA | NA | 0 | NA | NA | NA |
| outside_range_no_overlay | train | 0 | NA | NA | NA | 0 | NA | NA | NA |
| outside_range_no_overlay | holdout | 0 | NA | NA | NA | 0 | NA | NA | NA |

## 单腿重叠检查

| rule | rows | overlay rows | inside adjacent3 rows | inside rate |
| --- | --- | --- | --- | --- |
| adjacent3_base | 1137 | 0 | 0 | NA |
| adjacent3_medium_quality | 505 | 0 | 0 | NA |
| single_high_conviction_yes | 1003 | 1003 | 1001 | +99.8% |
| adjacent3_medium_plus_single_yes | 0 | 0 | 0 | NA |
| outside_range_no_overlay | 0 | 0 | 0 | NA |

## 三门状态

| rule | significance | baseline | forward | conclusion |
| --- | --- | --- | --- | --- |
| adjacent3_base | FAIL | FAIL | FAIL | inconclusive |
| adjacent3_medium_quality | FAIL | FAIL | FAIL | inconclusive |
| single_high_conviction_yes | FAIL | FAIL | FAIL | inconclusive |
| adjacent3_medium_plus_single_yes | FAIL | FAIL | FAIL | inconclusive |
| outside_range_no_overlay | FAIL | FAIL | FAIL | inconclusive |

## 人话结论

- 单腿可以作为 Range RV 的补充，但前提是分清重复加注、降级表达和区间外互补；本脚本把内部 YES、外侧 YES、外侧 NO 分开看。
- 当前 fixed hybrid 点估计没有形成可 live 的证据：orderbook matched 行数太少，baseline 还没有做成 matched no-trade/random 对照，三门不可能过。
- 这次固定口径下 `outside_range_no_overlay` 没有触发，说明区间外 NO 暂时只是值得继续 shadow 的概念，不是当前 fact 近窗里的实盘候选。
- 如果后续要继续，优先把这个 hybrid 作为 shadow journal 规则之一，而不是直接真钱。

## 产物

- JSON summary：`/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-hybrid-adjacent3-single-v0.json`
