# Forecast Quality Regime Signal Value

> generated_at_utc: `2026-06-09T15:16:56.414504+00:00`
> target_metric: `forecast_quality_regime_signal_value`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 优先；`fact_trades` 仅用于 contract 自检和 live fill 覆盖背景。
- DB last_modified: `2026-06-09T15:09:06.567273+00:00`
- fact_signal_candidates rows: `25100`
- fact_trades rows: `5473`
- decision_sets used: `270`
- unsettled fact_signal_candidates: `0`
- missing_bracket fact_signal_candidates: `0`
- train: `2026-05-06` -> `2026-05-28` (21 event_dates)
- holdout: `2026-05-29` -> `2026-06-07` (9 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-09T15:08:45.011642+00:00",
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
      "n": 172
    },
    {
      "settlement_status": "settled",
      "n": 5301
    }
  ],
  "candidate_coverage": [
    {
      "rows": 25100,
      "eligible": 8300,
      "paper_ordered": 3127,
      "live_filled": 554
    }
  ],
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

## Data Availability / Join Audit

- forecast_source/model_version coverage rows: `2`
- city coverage rows: `49`
- possible raw cache joins are audited below and in JSON under `cache_audit.join_candidates`.

### forecast_source / model_version coverage

| forecast_source | model_version | rows |
| --- | --- | --- |
| open_meteo_live_ecmwf | ecmwf | 14343 |
| open_meteo_live_gfs | gfs | 10757 |

### city coverage top20

| city | rows |
| --- | --- |
| NYC | 734 |
| Miami | 677 |
| Shanghai | 660 |
| Madrid | 658 |
| Warsaw | 629 |
| Beijing | 617 |
| Seoul | 608 |
| LA | 607 |
| Austin | 604 |
| BuenosAires | 595 |
| Paris | 583 |
| London | 565 |
| SaoPaulo | 557 |
| Chicago | 554 |
| Houston | 551 |
| Tokyo | 548 |
| CapeTown | 545 |
| Wuhan | 544 |
| Atlanta | 541 |
| Moscow | 539 |

### possible raw cache joins

| source | join_key | safe | used | leakage_risk |
| --- | --- | --- | --- | --- |
| fact_signal_candidates / fact_trades | condition_id + side + event_date(target_date); distribution grain deduped to city/event_date/source/model/snapshot/bracket | True | True | low when settlement fields are used only as labels |
| orderbook_snapshots/*.jsonl.gz | token/condition_id + snapshot_ts_utc <= decision_snapshot_ts_utc | True | False | medium if latest snapshot is used instead of time-aligned <= decision snapshot |
| cache/pm_history/*.json | city + event_date + bracket | False | False | final settlement; labels only |
| cache/iem/*.csv and cache/wu_obs/*.csv | icao/city + observation timestamp | False | False | weather observations can occur after decision/event; needs timestamp <= decision_snapshot_ts_utc materialized before use |
| raw forecast issue/cache files | city + event_date + forecast_source + forecast_run_ts <= decision_snapshot_ts_utc | False | False | not currently materialized in local fact tables; forecast run age needs pipeline support |

### 当前 fact 表已有 features

- `city`, `event_date`, `forecast_source`, `model_version`, `decision_hours_to_settle`, `model_p_yes`, `market_yes_price`, `final_yes`, `settlement_status`.
- 从同一 decision-set 的 bracket distribution 可构造 `model_entropy`, `model_mode_probability`, `adjacent2/adjacent3 mass`, `tail mass`, `model-market entropy gap`, `distribution_variance`。
- 从历史 event_date expanding window 可构造 `city/source historical calibration error`，本报告用过去样本的 adjacent3 miss rate，不用未来 outcome。

### 需要后续管道补充的 features

- `forecast run age`: fact 表没有 forecast issuance/run timestamp；需要物化 `forecast_run_ts_utc <= decision_snapshot_ts_utc`。
- `observed trend / weather stability`: 本地 `iem/wu_obs` 是观测 cache，必须按 observation timestamp 截断到 decision 前才可做 feature；当前未安全物化。
- 更严格的 `ECMWF/GFS disagreement`: 本报告只在 fact decision snapshot 内做同 city-day/source-model 分布差异，完整版本应物化同一 forecast issuance/checkpoint 的 paired distribution。

## Forecast Quality Regime Evaluation

| split | regime | n | dates | mode_hit | adj2_hit | adj3_hit | tail_miss |
| --- | --- | --- | --- | --- | --- | --- | --- |
| train | high_uncertainty_no_trade | 157 | 21 | +31.7% | +80.8% | +94.9% | +5.1% |
| train | low_uncertainty_adjacent3_allowed | 15 | 10 | +45.0% | +91.7% | +100.0% | +0.0% |
| train | medium_uncertainty_adjacent2_only_if_cheap | 29 | 16 | +28.1% | +98.4% | +100.0% | +0.0% |
| train | tail_overpriced_low_model_tail_risk | 66 | 20 | +32.1% | +88.1% | +100.0% | +0.0% |
| holdout | high_uncertainty_no_trade | 53 | 9 | +22.9% | +73.6% | +98.4% | +1.6% |
| holdout | low_uncertainty_adjacent3_allowed | 9 | 4 | +27.1% | +66.7% | +100.0% | +0.0% |
| holdout | medium_uncertainty_adjacent2_only_if_cheap | 7 | 3 | +22.2% | +77.8% | +100.0% | +0.0% |
| holdout | tail_overpriced_low_model_tail_risk | 20 | 5 | +18.3% | +72.8% | +100.0% | +0.0% |

### Holdout cluster bootstrap CI

| regime | adj3_mean | adj3_ci | tail_miss_mean | tail_miss_ci |
| --- | --- | --- | --- | --- |
| high_uncertainty_no_trade | +98.4% | [+96.1%, +100.0%] | +1.6% | [+0.0%, +3.9%] |
| low_uncertainty_adjacent3_allowed | +100.0% | [+100.0%, +100.0%] | +0.0% | [+0.0%, +0.0%] |
| medium_uncertainty_adjacent2_only_if_cheap | +100.0% | [+100.0%, +100.0%] | +0.0% | [+0.0%, +0.0%] |
| tail_overpriced_low_model_tail_risk | +100.0% | [+100.0%, +100.0%] | +0.0% | [+0.0%, +0.0%] |

## Feature Signal Value

Holdout AUC uses feature-high predicts label. For `tail_miss`, higher is worse; for `adjacent3`, higher is better.

| feature | label | auc | spearman | n |
| --- | --- | --- | --- | --- |
| model_entropy | final_in_model_adjacent3 | 0.591 | 0.064 | 69 |
| model_entropy | tail_miss | 0.409 | -0.064 | 69 |
| model_mode_probability | final_in_model_adjacent3 | 0.889 | 0.275 | 69 |
| model_mode_probability | tail_miss | 0.111 | -0.275 | 69 |
| model_adjacent2_mass | final_in_model_adjacent3 | 0.843 | 0.243 | 69 |
| model_adjacent2_mass | tail_miss | 0.157 | -0.243 | 69 |
| model_adjacent3_mass | final_in_model_adjacent3 | 0.924 | 0.335 | 69 |
| model_adjacent3_mass | tail_miss | 0.076 | -0.335 | 69 |
| model_tail_mass_outside_adjacent3 | final_in_model_adjacent3 | 0.076 | -0.454 | 69 |
| model_tail_mass_outside_adjacent3 | tail_miss | 0.924 | 0.454 | 69 |
| model_market_entropy_gap | final_in_model_adjacent3 | 0.449 | -0.036 | 69 |
| model_market_entropy_gap | tail_miss | 0.551 | 0.036 | 69 |
| model_market_l1_gap | final_in_model_adjacent3 | 0.131 | -0.260 | 69 |
| model_market_l1_gap | tail_miss | 0.869 | 0.260 | 69 |
| distribution_variance | final_in_model_adjacent3 | 0.232 | -0.189 | 69 |
| distribution_variance | tail_miss | 0.768 | 0.189 | 69 |
| ecmwf_gfs_mode_distance | final_in_model_adjacent3 | NA | NA | 0 |
| ecmwf_gfs_mode_distance | tail_miss | NA | NA | 0 |
| city_source_historical_adj3_miss_rate_filled | final_in_model_adjacent3 | 0.467 | -0.028 | 69 |
| city_source_historical_adj3_miss_rate_filled | tail_miss | 0.533 | 0.028 | 69 |

## 推荐给 Range RV Planner 的 regimes

- `low_uncertainty_adjacent3_allowed`: model entropy 低、mode probability 高、adjacent3 mass 高、历史 adjacent3 miss 低、跨模型 mode disagreement 低。可作为 Range RV 的 adjacent3 允许前置 regime。
- `medium_uncertainty_adjacent2_only_if_cheap`: 不满足 low，但 adjacent2 mass 仍高且未触发 high uncertainty。只能在 market price 足够便宜时考虑 adjacent2，不作为独立交易信号。
- `high_uncertainty_no_trade`: entropy 高、历史 miss 高或跨模型 disagreement 高。Range RV 应默认拒绝。
- `tail_overpriced_low_model_tail_risk`: model outside-adjacent3 tail mass 低而 market outside-adjacent3 tail mass 高，且未触发 high uncertainty。仅允许 tail fade 研究路径，不是 live action。

## 8 环覆盖自检

- 1 描述性绩效切片: NA，本报告不输出 PnL/ROI。
- 2 统计推断: covered，用 event_date cluster bootstrap 评估 hit/miss rate。
- 3 信号判别: covered，评估 quality features 对 mode/adjacent/tail labels 的信号价值。
- 4 概率分布评估: covered，本报告核心。
- 5 执行微结构: partial，仅审计可 join source，不做 executable PnL。
- 6 容量: NA。
- 7 组合相关性: partial，bootstrap cluster 按 event_date。
- 8 基准/反事实: NA，不训练 PnL classifier，不输出策略收益。

## 结论等级

`significance=NA`, `baseline=NA`, `forward=NA`, `conclusion=inconclusive` for live action. This is a research regime artifact only.
