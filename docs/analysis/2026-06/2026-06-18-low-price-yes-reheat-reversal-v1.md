# Low-Price YES Reheat Reversal v1

> generated_at_utc: `2026-06-18T12:29:42+00:00`
> Scope: research / shadow-candidate evaluation only; no N100/live behavior changed.

## 一句话结论

低价 YES 这条已经从“便宜彩票”升级成 `low_price_yes_reheat_reversal` 的独立 reheat-conditioned head。v1 可以作为 zero-notional shadow 的候选继续跑，但不够 live：同状态替代表达（current YES / d1 NO / d2 NO）还没有形成稳定 baseline excess，forecast peak clock 在主窗口仍不是可用硬门。

## 数据快照

- Feature source: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv`
- Forecast prior: `runtime/weather.db.fact_signal_candidates` raw `model_p_yes` by city/date/bracket
- fact_trades MAX built: `2026-06-17T17:09:13.232107+00:00`
- CLOB gate: `True`; 本报告不发布 live_real PnL/ROI。

### 强制 5 行 SQL 自检

```json
{
  "fact_trades_max_built_at_utc": "2026-06-17T17:09:13.232107+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 855
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
  "fact_trades_by_settlement_status": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 31499,
    "eligible": 10961,
    "paper_ordered": 4274,
    "live_filled": 348
  },
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## Target Metric

`low_price_yes_reheat_reversal_v1` row grain = `city + target_date + decision_snapshot_ts_utc + decision_hour_local + target YES bracket`。

- label: `target_yes_wins = target_hit`，不是 `current_yes_wins`。
- candidate: `YES ask <= 0.25`、target bracket 高于 observed running max、source-aligned、settled、forecast prior 可 join。
- probability: `p_target_yes_wins = raw_model_p_yes * p_reheat_context`。
- edge: `p_target_yes_wins - target_yes_ask`，主阈值看 `0.05 / 0.08 / 0.10`。

## Coverage

```json
{
  "feature_rows": 88621,
  "feature_states": 11512,
  "candidate_rows": 8731,
  "candidate_active_dates": 26,
  "candidate_cities": 36,
  "candidate_hit_rate": 0.046500973542549534,
  "target_distance_bucket_rows": {
    "d3plus": 5105,
    "d2": 1825,
    "d1": 1801
  },
  "forecast_peak_any_present_rate": 0.9612873668537395,
  "feature_factory_csv": "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv",
  "forecast_peak_note": "backfilled GFS/ECMWF peak-clock fields are mostly present and used as research features; production-native peak-clock availability is still not a live hard gate"
}
```

## Model Metrics

```json
{
  "train": {
    "rows": 4074,
    "positives": 204,
    "hit_rate": 0.050073637702503684,
    "auc_context": 0.918156254749962,
    "brier_context": 0.12484941703483637
  },
  "holdout": {
    "rows": 4657,
    "positives": 202,
    "hit_rate": 0.043375563667597164,
    "auc_context": 0.7467680101343468,
    "brier_context": 0.14400189821669104
  }
}
```

## Edge / Expression Comparison

| selector | rows | days | cities | hit | ask | target_roi | ci | top3_removed | current_yes_roi | d1_no_roi | d2_no_roi |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train:all_low_price_above_running | 4074 | 12 | 36 | +5.0% | +6.0% | -16.2% | [-33.0%, +1.9%] | -29.0% | -6.7% | -2.7% | -3.2% |
| train:raw_edge_gt_0 | 3629 | 12 | 36 | +4.7% | +5.6% | -16.6% | [-31.0%, -1.3%] | -27.3% | -6.3% | -2.9% | -3.0% |
| train:blended_edge_gt_0 | 3629 | 12 | 36 | +4.7% | +5.6% | -16.6% | [-31.0%, -1.3%] | -27.3% | -6.3% | -2.9% | -3.0% |
| train:adjusted_edge_ge_0.05 | 557 | 12 | 33 | +20.5% | +11.3% | +81.3% | [+42.2%, +125.3%] | +49.0% | -20.6% | -1.2% | -6.0% |
| train:d1d2_adjusted_edge_ge_0.05 | 265 | 12 | 29 | +19.2% | +10.9% | +76.0% | [+21.1%, +129.8%] | +31.0% | -18.2% | -7.4% | -8.5% |
| train:d1d2_not_deep_fade_adjusted_edge_ge_0.05 | 229 | 12 | 29 | +20.1% | +11.3% | +77.3% | [+24.6%, +130.9%] | +32.1% | -21.5% | -6.6% | -10.0% |
| train:adjusted_edge_ge_0.08 | 397 | 12 | 33 | +21.4% | +11.5% | +85.7% | [+38.8%, +132.6%] | +45.3% | -23.4% | -2.4% | -4.3% |
| train:d1d2_adjusted_edge_ge_0.08 | 185 | 12 | 26 | +21.1% | +11.4% | +84.6% | [+16.2%, +141.9%] | +36.8% | -20.8% | -10.7% | -5.9% |
| train:d1d2_not_deep_fade_adjusted_edge_ge_0.08 | 159 | 12 | 26 | +21.4% | +11.6% | +83.9% | [+15.2%, +144.6%] | +29.8% | -23.5% | -9.4% | -7.4% |
| train:adjusted_edge_ge_0.10 | 341 | 12 | 32 | +22.0% | +12.0% | +83.3% | [+38.4%, +128.7%] | +44.4% | -24.2% | -3.7% | -3.4% |
| train:d1d2_adjusted_edge_ge_0.10 | 158 | 12 | 24 | +21.5% | +12.1% | +78.3% | [+7.2%, +134.4%] | +23.2% | -22.6% | -12.9% | -4.6% |
| train:d1d2_not_deep_fade_adjusted_edge_ge_0.10 | 136 | 12 | 24 | +22.1% | +12.2% | +81.0% | [+4.7%, +144.8%] | +20.4% | -27.2% | -12.4% | -6.0% |
| holdout:all_low_price_above_running | 4657 | 14 | 36 | +4.3% | +5.5% | -21.4% | [-47.4%, +6.2%] | -43.0% | -2.9% | -2.6% | -0.5% |
| holdout:raw_edge_gt_0 | 4146 | 14 | 36 | +4.0% | +5.1% | -21.3% | [-51.9%, +13.9%] | -49.1% | -2.3% | -2.4% | -0.6% |
| holdout:blended_edge_gt_0 | 4146 | 14 | 36 | +4.0% | +5.1% | -21.3% | [-51.9%, +13.9%] | -49.1% | -2.3% | -2.4% | -0.6% |
| holdout:adjusted_edge_ge_0.05 | 605 | 14 | 33 | +9.3% | +10.3% | -10.5% | [-44.6%, +33.0%] | -40.6% | -4.5% | -3.5% | -0.8% |
| holdout:d1d2_adjusted_edge_ge_0.05 | 302 | 14 | 30 | +10.3% | +10.9% | -6.1% | [-33.1%, +23.0%] | -26.1% | -8.5% | -4.6% | -3.6% |
| holdout:d1d2_not_deep_fade_adjusted_edge_ge_0.05 | 286 | 14 | 30 | +10.5% | +11.3% | -6.9% | [-33.0%, +22.1%] | -28.4% | -9.5% | -5.3% | -3.5% |
| holdout:adjusted_edge_ge_0.08 | 445 | 14 | 29 | +11.0% | +10.9% | +1.2% | [-42.0%, +54.4%] | -35.5% | -7.0% | -2.4% | -2.7% |
| holdout:d1d2_adjusted_edge_ge_0.08 | 224 | 13 | 27 | +12.5% | +11.2% | +11.4% | [-26.7%, +50.3%] | -15.5% | -12.6% | -4.4% | -6.3% |
| holdout:d1d2_not_deep_fade_adjusted_edge_ge_0.08 | 214 | 13 | 27 | +12.6% | +11.5% | +9.9% | [-27.1%, +49.9%] | -17.6% | -13.8% | -5.2% | -6.1% |
| holdout:adjusted_edge_ge_0.10 | 361 | 14 | 28 | +12.2% | +11.3% | +8.1% | [-42.3%, +73.1%] | -37.8% | -6.9% | -2.2% | -2.1% |
| holdout:d1d2_adjusted_edge_ge_0.10 | 180 | 13 | 24 | +12.8% | +11.5% | +10.9% | [-30.7%, +56.6%] | -20.9% | -11.6% | -4.3% | -5.5% |
| holdout:d1d2_not_deep_fade_adjusted_edge_ge_0.10 | 172 | 13 | 24 | +12.8% | +11.8% | +8.6% | [-34.7%, +55.7%] | -25.4% | -12.1% | -5.0% | -5.1% |

## Shadow / Live Readiness

当前判定：`shadow_candidate / not_live`。

第一层 zero-notional shadow 已补齐并启动，入口见：

```text
scripts/ops/low_price_yes_reheat_reversal_shadow_v1.py
runtime/weather_edge_v1/low_price_yes_reheat_reversal_v1/shadow_candidates.jsonl
docs/analysis/2026-06/2026-06-18-low-price-yes-reheat-shadow-v1.md
```

首次启动使用当前 v1 scored rows，source max target_date=`2026-06-14`，最新日期无满足冻结规则的候选，因此 `appended=0`。这表示 shadow 证据链已经可运行，等待下一批 forward feature rows，而不是允许 live。

Shadow 第一层已经完成：

1. 冻结 shadow trigger：`d1/d2 + adjusted_edge>=0.08 + ask<=0.25`。
2. 写入 zero-notional would-order journal，不下真钱。
3. journal row 保留同状态 current YES / d1 NO / d2 NO 对照字段。

Shadow 下一层还差：

1. 新日期 feature/scored rows 进入后，runner 产生真实 forward would-order rows。
2. 每日 settlement 后输出 same-state paired scorecard。
3. 观察 0.05/0.08/0.10 旁路阈值，但主规则先不漂移。

Live 还差的是证据门：

1. forward shadow 至少覆盖多个 active dates，且相对 same-state 表达的 excess 不靠单日大赢家。
2. official observation / forecast peak clock 在生产链路可用；当前 peak 字段不能作为硬门。
3. tiny-live 前需要独立 risk cap、fresh ask 滑点/深度门、CLOB gate=true，以及 deploy skill 的 git-first 流程。

## Contract Verdict

```json
{
  "status": "shadow_candidate",
  "reference_rule": "holdout:d1d2_adjusted_edge_ge_0.08",
  "reason": "positive holdout point estimate with enough rows to start zero-notional telemetry, but not live-significant",
  "significance_gate": "FAIL",
  "baseline_gate": "FAIL",
  "forward_gate": "PARTIAL"
}
```
