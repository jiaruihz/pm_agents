# Weather Strategy Denominator Audit v0

> generated_at_utc: `2026-06-11T08:28:05.515834+00:00`
> target_metric: `weather_research_denominator_integrity_audit_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: local research audit only; no N100/live config changed; no live action.

## 一句话

- 是的，类似 denominator / 方法口径风险不止 adjacent3 一个；老 Range RV、matched baseline、market-shape、forecast-first 里都有需要复核的硬过滤。
- 最大风险不是 SQL 写错，而是把 `fact_signal_candidates` 当完整 bracket 分布使用时先按 side、eligible、settlement 或 orderbook 过滤，导致 city-day universe 被切残。
- 本报告只给审计优先级，不给策略 live 结论。

## 数据快照

- fact_signal_candidates rows: `26572`; fact built: `2026-06-11T05:19:42.922086+00:00`。
- fact_trades max built: `2026-06-11T05:19:34.417822+00:00`。

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

## 实证分母漏斗

| denominator | decision sets | n>=9 | n>=11 | mode adj3 selected settled | selected dates | all brackets settled |
| --- | --- | --- | --- | --- | --- | --- |
| buy_yes_only | 1213 | 2 | 0 | 38 | 14 | 13 |
| buy_no_only | 1357 | 74 | 2 | 53 | 16 | 13 |
| union_buy_yes_buy_no | 1568 | 992 | 488 | 185 | 25 | 4 |
| union_eligible_only | 534 | 335 | 149 | 56 | 17 | 2 |
| union_settled_prefilter | 291 | 7 | 3 | 291 | 30 | 291 |
| union_eligible_and_settled_prefilter | 93 | 1 | 1 | 93 | 24 | 93 |

## 静态扫描优先级

| script | risk_score | patterns |
| --- | --- | --- |
| scripts/analysis/market_structure_edge/research_range_rv_variant_lab_v03.py | 30 | buy_yes_only_universe, eligible_hard_gate, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_center_shoulders_butterfly_range_rv.py | 30 | buy_yes_only_universe, eligible_hard_gate, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_range_rv_scanner.py | 25 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter, full_orderbook_match_only |
| scripts/analysis/market_structure_edge/research_range_rv_market_shape_v05.py | 25 | buy_yes_only_universe, eligible_hard_gate, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_forecast_first_adjacent_range_rv.py | 25 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter, full_orderbook_match_only |
| scripts/analysis/market_structure_edge/research_range_rv_temporal_reversion_v06.py | 23 | buy_yes_only_universe, eligible_hard_gate, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/side_alpha/weather_side_band_alpha_summary.py | 21 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter |
| scripts/analysis/market_structure_edge/research_range_rv_tail_fade_uncertainty_v11.py | 20 | buy_yes_only_universe, settled_prefilter, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_range_rv_regime_v08.py | 20 | buy_yes_only_universe, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_range_rv_noarb_v09.py | 20 | buy_yes_only_universe, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_side_band_forecast_regime_v0.py | 17 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter |
| scripts/analysis/execution_quality/research_executable_edge.py | 16 | eligible_hard_gate, settled_prefilter, full_orderbook_match_only |
| scripts/analysis/market_structure_edge/research_adjacent3_quality_matched_baseline_v0.py | 15 | buy_yes_only_universe, eligible_hard_gate, all_legs_settled, full_orderbook_match_only |
| scripts/analysis/side_alpha/weather_side_band_timing_impact.py | 14 | buy_yes_only_universe, settled_prefilter |
| scripts/analysis/market_structure_edge/research_market_structural_edge.py | 14 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter |
| scripts/analysis/market_structure_edge/research_hybrid_adjacent3_single_v0.py | 14 | buy_yes_only_universe, all_legs_settled, full_orderbook_match_only |
| scripts/analysis/side_alpha/weather_side_band_entry_analysis.py | 13 | buy_yes_only_universe, eligible_hard_gate, settled_prefilter |
| scripts/analysis/side_alpha/research_side_band_forecast_regime_clean_test_v0.py | 12 | settled_prefilter, full_orderbook_match_only |
| scripts/analysis/market_structure_edge/research_range_rv_underround_robust_v10.py | 12 | buy_yes_only_universe, full_orderbook_match_only, posthoc_best_or_top |
| scripts/analysis/market_structure_edge/research_range_rv_positive_v02.py | 11 | eligible_hard_gate, full_orderbook_match_only, posthoc_best_or_top |

## 需要优先复核的脚本

1. `research_adjacent3_quality_shadow_journal_v0.py` / `research_adjacent3_quality_matched_baseline_v0.py`: BUY_YES-only universe，必须用 union builder 重跑。
2. `research_range_rv_scanner.py`: 先 `settlement_status='settled'` / `final_yes IS NOT NULL` 再 group，容易把未结算 bracket 从分布删掉。
3. `research_range_rv_variant_lab_v03.py`: 继承 scanner denominator，且部分算法用 eligible 子集；需要 union + selected-leg settled 口径复跑。
4. `research_forecast_first_adjacent_range_rv.py`: `all_legs_eligible` / all legs price/spread 属于执行覆盖过滤，不能当主机会分母。
5. `research_range_rv_market_shape_v05.py`: `require_eligible=True` 默认值需要复核，避免旧 planner 过滤器进入新策略研究。

## 新标准

- city-day/range 研究先建 `BUY_YES + BUY_NO` union bracket universe。
- 选择策略时可以用完整 universe；评估 PnL 时只要求被选中的腿有 `final_yes`。
- `eligible=1`、`all_legs_eligible`、`fully_matched_orderbook` 只能作为子分析或执行压力测试，不能默认替代机会全集。
- 每份新报告必须输出 bracket coverage distribution 和每层漏斗。

## 三门状态

| gate | status | reason |
| --- | --- | --- |
| significance | NA | 本报告是 denominator 审计，不估计策略 ROI 显著性。 |
| baseline | NA | 不比较交易规则收益，只审计分母风险。 |
| forward | NA | 无 live 动作；下一步是按新分母重跑候选。 |

结论：`audit_only`。不改 live、不加 size、不扩池。
