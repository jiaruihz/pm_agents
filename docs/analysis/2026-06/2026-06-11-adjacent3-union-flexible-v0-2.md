# Adjacent3 Union Flexible v0.2

> generated_at_utc: `2026-06-11T05:28:32.911013+00:00`
> target_metric: `forecast_quality_adjacent3_union_flexible_alpha_v02`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 一句话

- 旧 adjacent3 只看 `BUY_YES`，会把大量 bracket 丢掉；v0.2 改用 `BUY_YES + BUY_NO` union 后，样本恢复明显。
- 修正分母后，forecast-adjacent3 仍有正 holdout 点估计，但 train 稳定性和 orderbook 子样本没有过三门，所以结论仍是 `inconclusive`。
- 这次同时测试了 NO / YES+NO 混合表达，不再强制三腿 YES；目前没有任何表达达到 live 试探门槛。

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates`；`fact_trades` 只用于强制自检。
- fact_signal_candidates rows：`26572`；fact built：`2026-06-11T05:19:42.922086+00:00`。
- union base rows：`14915`；decision sets：`1568`；mode-adj3 evaluable rows：`185`；strategy rows：`2327`。
- train：`2026-05-06` -> `2026-05-29`；holdout：`2026-05-30` -> `2026-06-09`。

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

## 分母修正

| metric | old BUY_YES-only reference | v0.2 union |
| --- | --- | --- |
| decision sets | 1213 | 1568 |
| n_brackets >= 9 | 2 | 992 |
| mode-adj3 selected legs settled | 38 | 185 |
| all-bracket fully settled sets | NA | 4 |
| generated strategy rows | NA | 2327 |

## 表达定义

- `mode_adj3_yes_cost085`：模型 mode 附近三档 YES，`score=model_mass-cost>0` 且 `cost<=0.85`。
- `mode_adj3_yes_medium_quality` / `low_uncertainty`：只用 train 中位数阈值做 forecast-quality 软过滤。
- `mode_adj3_outside_no_tail_e010`：模型 mode 三档之外的 tail 被市场高估时买外侧 NO。
- `flexible_best_yes_no_legs_e008_top4`：每个 city-day 最多选 4 条绝对 edge 最大的 YES/NO 腿，不强制三 YES。
- 其它混合表达沿用 variant lab：adjacent pair spread、center/shoulders、range vs neighbors、tail fade。

## Decision Proxy

| algorithm | train family | train selected | train dates | train ROI | train ROI CI | train excess | train excess CI | holdout family | holdout selected | holdout dates | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `center_over_shoulders_e010` | 221 | 117 | 18 | +5.3% | [-5.2%, +14.2%] | +0.4% | [-6.4%, +6.4%] | 33 | 11 | 2 | +2.9% | [-48.3%, +8.5%] | +9.2% | [-43.8%, +17.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `flexible_best_yes_no_legs_e008_top4` | 319 | 238 | 22 | +10.6% | [+1.2%, +18.7%] | +1.7% | [-0.9%, +4.9%] | 65 | 38 | 8 | -3.3% | [-39.3%, +7.3%] | +2.0% | [-18.1%, +7.7%] | -39.2% | `PASS/FAIL/FAIL -> inconclusive` |
| `mode_adj3_outside_no_tail_e010` | 8 | 7 | 4 | +2.5% | [-47.2%, +8.4%] | -0.4% | [-21.7%, +0.3%] | 0 | 0 | 0 | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `mode_adj3_yes_cost085` | 167 | 102 | 16 | +6.6% | [-7.2%, +20.8%] | +1.4% | [-7.7%, +10.7%] | 18 | 11 | 1 | +32.8% | [+32.8%, +32.8%] | +9.9% | [+6.8%, +10.7%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `mode_adj3_yes_low_uncertainty` | 167 | 41 | 14 | +5.7% | [-10.7%, +20.3%] | +0.6% | [-13.0%, +12.0%] | 18 | 4 | 1 | -13.6% | [-13.6%, -13.6%] | -36.4% | [-39.9%, -35.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `mode_adj3_yes_medium_quality` | 167 | 45 | 14 | +7.4% | [-8.3%, +20.4%] | +2.3% | [-10.6%, +12.8%] | 18 | 6 | 1 | -6.0% | [-6.0%, -6.0%] | -28.9% | [-32.3%, -28.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `pair_spread_adjacent_e010` | 460 | 362 | 22 | -0.9% | [-12.8%, +9.4%] | -1.1% | [-4.3%, +2.5%] | 126 | 90 | 10 | -10.5% | [-26.6%, +15.8%] | -8.1% | [-15.6%, +0.3%] | -27.8% | `FAIL/FAIL/FAIL -> inconclusive` |
| `range_vs_neighbors_e010` | 72 | 37 | 14 | +5.7% | [-1.2%, +13.9%] | +1.7% | [-3.5%, +8.7%] | 7 | 5 | 1 | +18.4% | [+18.4%, +18.4%] | +14.6% | [+14.6%, +14.6%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `shoulders_over_center_e010` | 221 | 131 | 21 | +11.1% | [-7.4%, +27.3%] | +5.5% | [-4.6%, +15.7%] | 33 | 22 | 6 | +5.8% | [-36.4%, +41.5%] | -3.3% | [-56.2%, +10.0%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` |
| `tail_fade_overpriced_e015` | 181 | 85 | 22 | -0.4% | [-8.5%, +5.3%] | +0.3% | [-4.9%, +3.2%] | 26 | 8 | 3 | -12.6% | [-100.0%, -7.8%] | -12.4% | [-98.3%, -6.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |

## Time-Aligned Orderbook

- 价格匹配复用 `research_executable_edge.py` 逻辑，只允许 `orderbook_snapshot_ts <= decision_snapshot_ts_utc`。
- Fully matched strategy rows：`1271` / `2327`。

| algorithm | train family | train selected | train dates | train ROI | train ROI CI | train excess | train excess CI | holdout family | holdout selected | holdout dates | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `center_over_shoulders_e010` | 97 | 46 | 8 | +4.5% | [-11.9%, +23.9%] | +0.7% | [-12.1%, +12.8%] | 33 | 11 | 2 | +0.3% | [-50.2%, +6.0%] | +9.1% | [-43.2%, +16.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `flexible_best_yes_no_legs_e008_top4` | 151 | 100 | 10 | -1.2% | [-13.1%, +11.6%] | -1.4% | [-4.8%, +3.3%] | 65 | 38 | 8 | -8.4% | [-43.7%, +1.8%] | +1.3% | [-18.2%, +6.5%] | -51.9% | `FAIL/FAIL/FAIL -> inconclusive` |
| `mode_adj3_outside_no_tail_e010` | 4 | 4 | 4 | -16.4% | [-66.0%, +20.5%] | +0.0% | [+0.0%, +0.0%] | 0 | 0 | 0 | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `mode_adj3_yes_cost085` | 67 | 42 | 6 | -0.2% | [-29.2%, +21.0%] | +1.9% | [-17.2%, +16.1%] | 18 | 11 | 1 | +22.8% | [+22.8%, +22.8%] | +11.4% | [+9.7%, +12.5%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `mode_adj3_yes_low_uncertainty` | 67 | 14 | 6 | +9.7% | [-24.8%, +26.5%] | +11.9% | [-15.3%, +22.8%] | 18 | 4 | 1 | -16.9% | [-16.9%, -16.9%] | -28.3% | [-30.0%, -27.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `mode_adj3_yes_medium_quality` | 67 | 15 | 6 | +9.2% | [-28.6%, +24.1%] | +11.3% | [-15.7%, +21.0%] | 18 | 6 | 1 | -10.2% | [-10.2%, -10.2%] | -21.6% | [-23.3%, -20.5%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `pair_spread_adjacent_e010` | 251 | 192 | 10 | -5.5% | [-21.2%, +11.2%] | -2.2% | [-5.7%, +2.2%] | 126 | 90 | 10 | -14.3% | [-30.0%, +8.6%] | -8.1% | [-15.8%, +0.4%] | -30.6% | `FAIL/FAIL/FAIL -> inconclusive` |
| `range_vs_neighbors_e010` | 32 | 19 | 5 | -2.1% | [-9.1%, +6.5%] | -4.7% | [-7.2%, -0.7%] | 7 | 5 | 1 | +13.9% | [+13.9%, +13.9%] | +13.6% | [+13.6%, +13.6%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `shoulders_over_center_e010` | 97 | 59 | 9 | +3.0% | [-28.8%, +29.9%] | -0.7% | [-16.5%, +12.7%] | 33 | 22 | 6 | +0.5% | [-35.5%, +38.5%] | -3.6% | [-46.8%, +8.6%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` |
| `tail_fade_overpriced_e015` | 94 | 43 | 10 | -6.1% | [-13.9%, +2.4%] | -1.4% | [-5.4%, +3.3%] | 26 | 8 | 3 | -15.0% | [-100.0%, -10.5%] | -11.2% | [-98.5%, -5.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |

## 三门状态

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | cluster bootstrap CI 仍未形成稳定正超额，且部分候选 train 样本/OB 覆盖太薄。 |
| baseline | FAIL | 没有表达在 decision proxy 与 time-aligned orderbook 两层同时相对同 family baseline 过门。 |
| forward | FAIL | holdout 点估计有正值，但 train 稳定性、top5 removed 与 orderbook 子样本不够。 |

结论：`inconclusive`，不改 live、不加 size、不扩池。

## 下一步

- 可以把 union universe 的 bracket coverage 修正吸收到正式 adjacent3 scanner，然后继续 shadow/paper。
- 需要补更多已结算 forward 日期后再看；当前不能把正 holdout 点估计当 live edge。
