# Range Dual Expression v0

> generated_at_utc: `2026-06-11T09:08:19.179768+00:00`
> target_metric: `range_dual_expression_alpha_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 一句话

- 这次把同一个温度区间同时用 inside YES、outside NO、choose-cheaper 三种方式表达；verdict=`inconclusive`。
- generated rows: `10248`; algorithms: `18`; orderbook matched: `5259` / `10248`。
- outside NO 与 inside YES 是等效区间表达，但 outside NO 通常腿数更多、gross cost 更大；本报告同时保留 gross ROI 和 effective range cost 选择逻辑。

## 数据快照

- fact_signal_candidates rows: `26621`; fact built: `2026-06-11T08:49:33.218573+00:00`。
- union rows: `14944`; decision sets: `1574`; settled/inferred decision sets: `570`。
- train: `2026-05-06` -> `2026-05-29`; holdout: `2026-05-30` -> `2026-06-09`。
- settlement inference modes: `{'inferred_from_one_winner': 570, 'incomplete': 1004}`。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-11T08:49:24.667736+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 853
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "",
      "rows": 216
    },
    {
      "settlement_status": "settled",
      "rows": 4182
    }
  ],
  "candidate_coverage": {
    "rows": 26621,
    "eligible": 8924,
    "paper_ordered": 3343,
    "live_filled": 345
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 962,
      "with_fill": 853
    }
  ]
}
```

## Decision Proxy Results

| algorithm | family | train rows | train dates | train ROI | train excess | train excess CI | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates | reasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `best_width3_inside_yes` | 570 | 433 | 22 | -7.1% | +0.1% | [-0.6%, +0.9%] | 124 | 10 | -17.6% | +0.0% | [+0.0%, +0.1%] | -41.1% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_outside_no` | 570 | 434 | 22 | +0.7% | +0.1% | [-0.0%, +0.2%] | 124 | 10 | -0.1% | +0.0% | [-0.1%, +0.1%] | -1.3% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_cheaper` | 570 | 443 | 22 | +1.2% | +0.0% | [+0.0%, +0.0%] | 126 | 10 | +0.3% | +0.0% | [+0.0%, +0.0%] | -1.1% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_inside_yes` | 570 | 442 | 22 | +12.4% | +1.1% | [+0.0%, +3.3%] | 126 | 10 | -9.1% | +0.0% | [+0.0%, +0.0%] | -49.9% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_cheaper` | 570 | 443 | 22 | +1.0% | +0.0% | [+0.0%, +0.0%] | 126 | 10 | -0.6% | +0.0% | [+0.0%, +0.0%] | -2.5% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_inside_yes` | 570 | 439 | 22 | +1.9% | +0.6% | [+0.0%, +2.2%] | 126 | 10 | -18.5% | +0.0% | [+0.0%, +0.0%] | -45.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width3_cheaper` | 570 | 441 | 22 | +0.5% | +0.0% | [-0.0%, +0.1%] | 126 | 10 | -0.8% | +0.0% | [+0.0%, +0.0%] | -2.8% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width3_outside_no` | 564 | 432 | 22 | +0.0% | +0.0% | [-0.0%, +0.1%] | 125 | 10 | -1.0% | -0.0% | [-0.0%, +0.0%] | -2.5% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_outside_no` | 570 | 436 | 22 | +0.5% | +0.0% | [-0.0%, +0.1%] | 125 | 10 | -0.8% | -0.0% | [-0.1%, +0.0%] | -2.1% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_inside_yes` | 570 | 322 | 22 | +8.6% | +1.2% | [-1.1%, +3.5%] | 86 | 10 | +9.4% | -0.2% | [-5.2%, +4.2%] | -7.7% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_cheaper` | 570 | 358 | 22 | +2.2% | +0.1% | [-0.1%, +0.3%] | 97 | 10 | +2.0% | -0.2% | [-0.7%, +0.3%] | -0.3% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_outside_no` | 564 | 342 | 22 | +1.3% | -0.1% | [-0.2%, +0.1%] | 91 | 10 | +1.0% | -0.3% | [-0.8%, -0.0%] | -0.7% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_outside_no` | 570 | 369 | 22 | +0.5% | -0.2% | [-0.4%, +0.1%] | 103 | 10 | -0.0% | -0.6% | [-1.0%, -0.3%] | -0.9% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_outside_no` | 570 | 331 | 22 | +0.4% | -0.3% | [-0.5%, -0.0%] | 95 | 10 | +0.0% | -0.7% | [-1.1%, -0.3%] | -1.2% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_cheaper` | 570 | 381 | 22 | +1.0% | -0.2% | [-0.5%, +0.0%] | 106 | 10 | +0.5% | -0.8% | [-1.2%, -0.3%] | -0.8% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_cheaper` | 570 | 348 | 22 | +1.0% | -0.2% | [-0.5%, +0.1%] | 100 | 10 | +0.6% | -0.8% | [-1.2%, -0.2%] | -1.1% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_inside_yes` | 570 | 363 | 22 | +8.5% | +1.4% | [-6.1%, +9.4%] | 101 | 10 | +1.1% | -11.5% | [-24.1%, -0.8%] | -31.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_inside_yes` | 570 | 314 | 22 | +2.4% | -0.8% | [-5.4%, +4.5%] | 93 | 10 | -5.8% | -13.5% | [-25.3%, -5.3%] | -33.8% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |

## Time-Aligned Orderbook Results

Orderbook uses latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; all selected legs must match.

| algorithm | family | train rows | train dates | train ROI | train excess | train excess CI | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates | reasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `best_width3_inside_yes` | 380 | 246 | 10 | -18.5% | +0.6% | [+0.0%, +1.5%] | 124 | 10 | -24.4% | +0.0% | [+0.0%, +0.1%] | -47.1% | `FAIL/PASS/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_outside_no` | 229 | 144 | 10 | -1.0% | +0.1% | [-0.1%, +0.3%] | 76 | 10 | -0.7% | +0.0% | [-0.1%, +0.2%] | -2.2% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_inside_yes` | 380 | 252 | 10 | -6.1% | +1.5% | [+0.0%, +4.6%] | 126 | 10 | -17.0% | +0.0% | [+0.0%, +0.0%] | -54.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_inside_yes` | 380 | 250 | 10 | -10.3% | +1.3% | [+0.0%, +4.4%] | 126 | 10 | -24.9% | +0.0% | [+0.0%, +0.0%] | -49.8% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width3_cheaper` | 271 | 174 | 10 | -2.0% | +0.1% | [+0.0%, +0.2%] | 95 | 10 | -1.8% | +0.0% | [+0.0%, +0.0%] | -4.2% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width1_cheaper` | 268 | 174 | 10 | -0.6% | +0.0% | [-0.0%, +0.0%] | 93 | 10 | -0.6% | +0.0% | [+0.0%, +0.0%] | -2.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_cheaper` | 268 | 174 | 10 | -1.0% | +0.0% | [-0.0%, +0.0%] | 93 | 10 | -1.7% | +0.0% | [+0.0%, +0.0%] | -4.3% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width3_outside_no` | 228 | 145 | 10 | -2.0% | +0.1% | [-0.0%, +0.2%] | 79 | 10 | -1.6% | -0.0% | [-0.0%, +0.0%] | -3.0% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `best_width2_outside_no` | 228 | 146 | 10 | -1.2% | +0.1% | [-0.0%, +0.2%] | 77 | 10 | -1.6% | -0.0% | [-0.1%, +0.0%] | -3.1% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_cheaper` | 268 | 149 | 10 | +1.4% | -0.1% | [-0.3%, +0.1%] | 70 | 10 | +2.0% | -0.1% | [-0.7%, +0.6%] | -0.4% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_outside_no` | 225 | 117 | 10 | +0.6% | -0.2% | [-0.4%, +0.0%] | 53 | 10 | +1.2% | -0.3% | [-0.9%, +0.3%] | -0.5% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_outside_no` | 228 | 124 | 10 | -1.3% | -0.3% | [-0.7%, +0.1%] | 60 | 10 | -0.5% | -0.6% | [-1.5%, +0.0%] | -1.8% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width3_inside_yes` | 380 | 185 | 10 | +0.1% | -0.4% | [-1.7%, +1.3%] | 86 | 10 | +2.6% | -0.7% | [-5.3%, +3.5%] | -13.0% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_outside_no` | 229 | 112 | 10 | -1.3% | -0.7% | [-1.0%, -0.4%] | 56 | 10 | -0.4% | -0.8% | [-1.4%, -0.2%] | -1.5% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_cheaper` | 268 | 141 | 10 | -0.7% | -0.6% | [-1.0%, -0.3%] | 72 | 10 | -0.2% | -0.9% | [-1.6%, -0.2%] | -1.9% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_cheaper` | 268 | 155 | 10 | -0.9% | -0.4% | [-0.9%, +0.1%] | 76 | 10 | -0.3% | -1.1% | [-1.8%, -0.2%] | -2.3% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width1_inside_yes` | 381 | 216 | 10 | -0.8% | +0.9% | [-9.3%, +10.2%] | 101 | 10 | -6.6% | -11.6% | [-22.9%, -1.0%] | -36.3% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_width2_inside_yes` | 380 | 185 | 10 | -6.3% | -3.9% | [-7.4%, +0.0%] | 93 | 10 | -11.9% | -13.1% | [-23.9%, -5.6%] | -30.6% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |

## 三门状态

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | 没有表达在 proxy/orderbook 两层同时给出稳定正超额 CI。 |
| baseline | FAIL | outside NO/choose-cheaper 没有稳定超过同 family baseline。 |
| forward | FAIL | holdout/top5/orderbook 后仍不稳定。 |

结论：`inconclusive`。不改 live、不加 size、不扩池。
