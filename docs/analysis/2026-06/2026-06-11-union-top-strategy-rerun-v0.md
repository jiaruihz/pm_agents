# Union Top Strategy Rerun v0

> generated_at_utc: `2026-06-11T08:34:38.706642+00:00`
> target_metric: `union_denominator_top_strategy_rerun_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 一句话

- 没有策略同时通过 proxy 和 time-aligned orderbook 三门，所以没有 live 动作。
- mode_adj3_yes_cost085: orderbook holdout rows=11, dates=1, ROI=+22.8%, excess=+11.4%。
- shoulders_over_center_e010: orderbook holdout rows=22, dates=6, ROI=+0.6%, excess=-3.6%。
- flexible_best_yes_no_legs_e008_top4: orderbook holdout rows=38, dates=8, ROI=-7.8%, excess=+1.5%。
- shape_single_peak_no_e010_union: orderbook holdout rows=133, dates=10, ROI=-2.0%, excess=-3.6%。

## 数据快照

- fact_signal_candidates rows: `26572`; fact built: `2026-06-11T05:19:42.922086+00:00`。
- union rows: `14915`; decision sets: `1568`; generated strategy rows: `5216`; algorithms: `17`。
- train: `2026-05-06` -> `2026-05-29`; holdout: `2026-05-30` -> `2026-06-09`。
- orderbook fully matched rows: `3118` / `5216`。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-11T05:19:34.417822+00:00",
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
    "rows": 26572,
    "eligible": 8903,
    "paper_ordered": 3339,
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

## Decision Proxy Top Results

| algorithm | train family | train rows | train dates | train ROI | train excess | train excess CI | holdout family | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates | reasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `shape_trough_center_yes_pair_e012_union` | 221 | 110 | 18 | +5.8% | +1.4% | [-6.2%, +8.1%] | 33 | 10 | 2 | +8.9% | +15.3% | [-45.1%, +24.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `range_vs_neighbors_e010` | 72 | 37 | 14 | +5.7% | +1.7% | [-3.5%, +8.7%] | 7 | 5 | 1 | +18.4% | +14.6% | [+14.6%, +14.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_yes_cost085` | 167 | 102 | 16 | +6.6% | +1.4% | [-7.7%, +10.7%] | 18 | 11 | 1 | +32.8% | +9.9% | [+6.8%, +10.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `center_over_shoulders_e010` | 221 | 117 | 18 | +5.3% | +0.4% | [-6.4%, +6.4%] | 33 | 11 | 2 | +2.9% | +9.2% | [-43.8%, +17.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `flexible_best_yes_no_legs_e008_top4` | 319 | 238 | 22 | +10.6% | +1.7% | [-0.9%, +4.9%] | 65 | 38 | 8 | -3.3% | +2.0% | [-18.1%, +7.7%] | -39.2% | `PASS/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shoulders_over_center_e010` | 221 | 131 | 21 | +11.1% | +5.5% | [-4.1%, +15.5%] | 33 | 22 | 6 | +5.8% | -3.3% | [-61.4%, +8.7%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_single_peak_no_e010_union` | 627 | 428 | 22 | +6.6% | +2.2% | [-1.3%, +5.8%] | 199 | 133 | 10 | +1.3% | -3.5% | [-7.1%, +2.6%] | -13.6% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_adjacent_inversion_pair_e010_union` | 460 | 364 | 22 | -0.8% | -1.0% | [-5.5%, +3.6%] | 126 | 90 | 10 | -8.7% | -7.3% | [-15.5%, +1.0%] | -35.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `pair_spread_adjacent_e010` | 460 | 362 | 22 | -0.9% | -1.1% | [-4.3%, +2.5%] | 126 | 90 | 10 | -10.5% | -8.1% | [-15.6%, +0.3%] | -27.8% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_peak_center_no_pair_e012_union` | 221 | 107 | 20 | +11.9% | +5.8% | [-6.3%, +17.4%] | 33 | 18 | 5 | -1.3% | -10.4% | [-90.6%, +6.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_rows<20,top5_removed_not_positive_or_NA |
| `tail_fade_overpriced_e015` | 181 | 85 | 22 | -0.4% | +0.3% | [-4.8%, +3.4%] | 26 | 8 | 3 | -12.6% | -12.4% | [-105.8%, -6.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_single_trough_yes_e010_union` | 627 | 281 | 22 | +21.7% | +18.2% | [+0.2%, +35.8%] | 199 | 67 | 10 | -12.1% | -21.9% | [-55.0%, +1.4%] | -72.7% | `PASS/PASS/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_adj3_yes_medium_quality` | 167 | 45 | 14 | +7.4% | +2.3% | [-10.6%, +12.8%] | 18 | 6 | 1 | -6.0% | -28.9% | [-32.3%, -28.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_yes_low_uncertainty` | 167 | 41 | 14 | +5.7% | +0.6% | [-13.0%, +12.0%] | 18 | 4 | 1 | -13.6% | -36.4% | [-39.9%, -35.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_above_tail_inversion_pair_e010_union` | 103 | 30 | 15 | -9.1% | -18.4% | [-48.0%, +13.0%] | 12 | 3 | 2 | -59.3% | -73.4% | [-146.4%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_outside_no_tail_e010` | 8 | 7 | 4 | +2.5% | -0.4% | [-21.7%, +0.3%] | 0 | 0 | 0 | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_below_tail_inversion_pair_e010_union` | 27 | 11 | 8 | -16.1% | -15.7% | [-71.5%, +41.3%] | 1 | 0 | 0 | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |

## Time-Aligned Orderbook Top Results

Orderbook uses latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; all selected legs must match.

| algorithm | train family | train rows | train dates | train ROI | train excess | train excess CI | holdout family | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates | reasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `shape_trough_center_yes_pair_e012_union` | 97 | 43 | 8 | +5.4% | +1.5% | [-11.0%, +14.2%] | 33 | 10 | 2 | +6.1% | +15.0% | [-43.6%, +23.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `range_vs_neighbors_e010` | 32 | 19 | 5 | -2.1% | -4.7% | [-7.2%, -0.7%] | 7 | 5 | 1 | +13.9% | +13.6% | [+13.6%, +13.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_yes_cost085` | 67 | 42 | 6 | -0.2% | +1.9% | [-17.2%, +16.1%] | 18 | 11 | 1 | +22.8% | +11.4% | [+9.7%, +12.5%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `center_over_shoulders_e010` | 97 | 46 | 8 | +4.5% | +0.7% | [-12.1%, +12.8%] | 33 | 11 | 2 | +0.3% | +9.2% | [-43.0%, +16.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `flexible_best_yes_no_legs_e008_top4` | 151 | 100 | 10 | -1.2% | -1.4% | [-4.8%, +3.3%] | 65 | 38 | 8 | -7.8% | +1.5% | [-18.0%, +6.9%] | -41.2% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_single_peak_no_e010_union` | 367 | 245 | 10 | -0.8% | +0.5% | [-3.6%, +3.9%] | 199 | 133 | 10 | -2.0% | -3.6% | [-7.1%, +2.5%] | -15.0% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shoulders_over_center_e010` | 97 | 59 | 9 | +3.0% | -0.7% | [-17.3%, +12.3%] | 33 | 22 | 6 | +0.6% | -3.6% | [-50.0%, +8.3%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_adjacent_inversion_pair_e010_union` | 251 | 195 | 10 | -7.1% | -3.2% | [-8.4%, +3.1%] | 126 | 90 | 10 | -12.5% | -7.4% | [-15.0%, +0.6%] | -37.5% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `pair_spread_adjacent_e010` | 251 | 192 | 10 | -5.5% | -2.2% | [-5.7%, +2.2%] | 126 | 90 | 10 | -14.2% | -8.1% | [-15.8%, +0.5%] | -30.6% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `shape_peak_center_no_pair_e012_union` | 97 | 46 | 8 | +1.4% | -1.9% | [-26.2%, +13.1%] | 33 | 18 | 5 | -6.1% | -10.3% | [-113.0%, +5.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_rows<20,top5_removed_not_positive_or_NA |
| `tail_fade_overpriced_e015` | 94 | 43 | 10 | -6.1% | -1.4% | [-5.2%, +3.4%] | 26 | 8 | 3 | -15.0% | -11.2% | [-93.3%, -5.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_single_trough_yes_e010_union` | 366 | 134 | 10 | +19.7% | +16.7% | [-6.4%, +41.1%] | 199 | 67 | 10 | -18.5% | -21.6% | [-53.7%, +1.9%] | -85.4% | `FAIL/FAIL/FAIL -> inconclusive` | top5_removed_not_positive_or_NA |
| `mode_adj3_yes_medium_quality` | 67 | 15 | 6 | +9.2% | +11.3% | [-15.7%, +21.0%] | 18 | 6 | 1 | -10.2% | -21.6% | [-23.3%, -20.5%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_yes_low_uncertainty` | 67 | 14 | 6 | +9.7% | +11.9% | [-15.3%, +22.8%] | 18 | 4 | 1 | -16.9% | -28.3% | [-30.0%, -27.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_above_tail_inversion_pair_e010_union` | 53 | 15 | 6 | -23.7% | -35.7% | [-81.9%, +22.2%] | 12 | 3 | 2 | -62.0% | -70.0% | [-135.6%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `mode_adj3_outside_no_tail_e010` | 4 | 4 | 4 | -16.4% | +0.0% | [+0.0%, +0.0%] | 0 | 0 | 0 | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |
| `shape_below_tail_inversion_pair_e010_union` | 13 | 3 | 2 | -35.3% | -34.5% | [-110.6%, +92.2%] | 1 | 0 | 0 | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | holdout_dates<5,holdout_rows<20,top5_removed_not_positive_or_NA |

## 三门状态

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | 没有策略在 proxy 和 orderbook 两层同时给出稳定正超额 CI。 |
| baseline | FAIL | 同 family baseline 后，正收益多为不稳定或只在单一 holdout 日期出现。 |
| forward | FAIL | 需要 holdout >=5 日期、>=20 行、top5 removed ROI >0；候选没同时满足。 |

结论：`inconclusive`。不改 live、不加 size、不扩池。

## 下一步

- `mode_adj3_yes_cost085` 可继续做 shadow/paper，因为逻辑最干净，但当前 holdout 日期太少。
- `flexible_best_yes_no_legs` 和 pair/shape spread 方向暂时降优先级；它们更像噪声或执行后变差。
- 继续提升应转向 forecast quality gating / model calibration，而不是继续调交易形态阈值。
