# Weather Shadow/Paper Queue v0

> generated_at_utc: `2026-06-10T01:39:13.796176+00:00`
> git_sha: `b164755`
> Scope: local research queue only; no live/N100 config changed; no orders placed.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades` 自检 + 已提交研究报告。
- fact_signal_candidates 当前范围：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。

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

## 人话结论

- 可以同时跑多个 shadow/paper，但要分层：shadow 主策略、paper baseline、shadow tag、工程 shadow、数据 backfill 分开。
- 当前真钱 live 队列为空；live 后面单独排班，不能和 shadow/paper 混在一起。
- 主 shadow 只有 `forecast_quality_medium_adjacent3_shadow_v0`；它最贴天气预测核心，但三门仍不过。
- 旧 side-band 可以继续打 tag 观察；blended single-leg paper profiles 可以继续当 baseline；April preview 是扩样工程输入。

## 队列表

| queue_id | mode | run now | paper now | live now | gates | reason |
| --- | --- | --- | --- | --- | --- | --- |
| forecast_quality_medium_adjacent3_shadow_v0 | shadow_primary | True | False | False | FAIL/FAIL_OR_NA/FAIL | 非 all-YES 里最像天气预测核心策略：模型分布很集中时观察 mode 附近三档 YES。但 matched baseline/holdout 仍不过，只能 shadow。 |
| side_band_mechanism_shadow_tags_v1 | shadow_tag_only | True | False | False | FAIL/FAIL/FAIL | 早期真钱赚过是真的，但机制归因显示收益依赖少数日期和 side/price 形态；适合继续打 tag 观察，不适合独立 paper/live。 |
| blended_single_leg_paper_profiles | paper_baseline | True | True | False | NA/NA/NA | 已有 ops paper profiles，可作为低成本对照组继续跑；它不是当前 live 候选，主要用于比较 adjacent3/side-band 是否真的有增量。 |
| all_yes_underround_engineering_shadow | engineering_shadow_deferred | True | False | False | PASS/PASS/PASS | 统计三门通过，但用户已明确 all-YES 暂不实盘；只能做工程 shadow：多腿 partial fill、滑点、手续费、unwind 仿真。 |
| single_high_conviction_yes_fallback | fallback_shadow_only | True | False | False | FAIL/FAIL/FAIL | 99%+ 与 adjacent3 重叠，不是独立策略；只在 adjacent3 三腿太贵或缺腿时记录 fallback。 |
| april_historical_opportunity_backfill | backfill_input | True | False | False | NA/NA/NA | 这是扩样数据工程，不是策略。已经能生成 April decision price preview；下一步补 model_p_yes/final_yes/orderbook proxy 后，才能回测 shadow 候选。 |

## 建议执行节奏

1. 每日/每次 fact refresh 后跑 primary shadow：`research_adjacent3_quality_shadow_journal_v0.py`。
2. 保持 blended single-leg paper loop 作为 baseline，不从它直接晋级 live。
3. side-band / single-leg fallback 只作为 tag 写入 shadow journal 或独立 attribution，不作为下单规则。
4. April backfill 继续补 `model_p_yes` 和 `final_yes`，补完后重跑 adjacent3/side-band 三门。
5. live 排班只接收三门全过且 execution stress 过关的候选。

## Live 排班硬门

`A queue item can enter live scheduling only after significance/baseline/forward PASS, execution coverage is time-aligned, top5 stress is positive, and user explicitly approves live scheduling.`

## Run Commands

| queue_id | command |
| --- | --- |
| forecast_quality_medium_adjacent3_shadow_v0 | python3 scripts/analysis/market_structure_edge/research_adjacent3_quality_shadow_journal_v0.py |
| side_band_mechanism_shadow_tags_v1 | python3 scripts/analysis/side_alpha/research_side_band_mechanism_attribution_v1.py |
| blended_single_leg_paper_profiles | scripts/ops/weather_blended_shadow_paper_loop.sh |
| all_yes_underround_engineering_shadow | none yet; requires dedicated basket execution simulator |
| single_high_conviction_yes_fallback | covered by hybrid adjacent3/single research; no standalone loop |
| april_historical_opportunity_backfill | python3 scripts/analysis/market_structure_edge/build_april_historical_opportunity_preview_v0.py |
