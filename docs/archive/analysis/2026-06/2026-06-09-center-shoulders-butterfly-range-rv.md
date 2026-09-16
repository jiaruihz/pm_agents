# Center vs Shoulders / Butterfly Range RV

> generated_at_utc: `2026-06-09T15:25:04.684378+00:00`
> target_metric: `forecast_center_shoulders_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: counterfactual research only; no N100/live config changed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates`; `fact_trades` 只用于强制自检。
- DB last_modified: `2026-06-09T15:09:06.567273+00:00`
- fact built at: `2026-06-09T15:09:02.643978+00:00`
- sync/rebuild: `ran scripts/ops/sync_weather_remote.sh and scripts/weather_dashboard/run_stack.sh --api-only before research`
- unsettled used in research: `0`; missing_bracket used in research: `0`.
- 不使用旧单腿策略 `eligible` 作为 Range RV 硬门；仅在 JSON 中保留 `all_legs_eligible` 诊断字段。
- Mode stable 定义：同 decision snapshot 有多模型时要求 mode 一致；只有单模型时要求 mode 与第二名概率 gap >= 0.03，并标为 single-model unique-mode proxy。

## Target Metric

`forecast_center_shoulders_range_rv_alpha` = forecast-first center/shoulder/butterfly 表达的 selected ROI 减同表达 family baseline ROI。先锁模型分布，再看市场中心/肩部/尾部相对形状；baseline 是同 family、同 train/holdout 窗口内通过 forecast-first 结构但未按 score 过滤的候选全集。

## Filter Funnel

| step | count |
|---|---:|
| fact_signal_candidates rows | 25100 |
| settled + decision_window present rows | 2292 |
| decision_sets | 301 |
| center/shoulder/butterfly raw candidate count | 684 |
| selected strategy rows after all filters | 332 |
| orderbook fully matched selected rows | 149 |

### Strategy Filter Counts

| strategy | raw | mode stable | mode prob | adj3 mass | tail mass | risk | spread baseline | score selected |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `center_cheap_butterfly` | 195 | 175 | 175 | 175 | 174 | 116 | 114 | 107 |
| `shoulders_cheap_butterfly` | 195 | 175 | 175 | 175 | 174 | 34 | 33 | 7 |
| `center_band_cheap_neighbors` | 147 | 131 | 131 | 131 | 130 | 130 | 127 | 114 |
| `tail_center_contrast` | 147 | 131 | 131 | 131 | 128 | 128 | 125 | 104 |

## Train / Holdout

- Split field: `event_date`; split_date: `2026-05-26`.
- Train: `2026-05-06` to `2026-05-23`, active_dates `13`, family_rows `227`, selected_rows `188`.
- Holdout: `2026-05-26` to `2026-06-01`, active_dates `6`, family_rows `172`, selected_rows `144`.
- Bootstrap: cluster by `event_date`, iters `1000`.

## Verdict

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final conclusion: `inconclusive`. `some positive point estimates but holdout/orderbook sample too small or unstable`; classification `sample_limited`. No live action.

## Structure vs Sample

| strategy | assessment | proxy selected rows | orderbook train selected | orderbook holdout selected | proxy holdout excess | orderbook holdout excess | reason |
|---|---|---:|---:|---:|---:|---:|---|
| `center_cheap_butterfly` | `structurally_weak_proxy` | 107 | 5 | 48 | -2.2% | -2.1% | decision-proxy holdout excess is non-positive versus same-family baseline; executable coverage is still a separate limitation |
| `shoulders_cheap_butterfly` | `sample_limited` | 7 | 0 | 3 | +79.0% | +67.7% | selected rows or executable train coverage below gate |
| `center_band_cheap_neighbors` | `structurally_weak_proxy` | 114 | 0 | 49 | -0.1% | +0.1% | decision-proxy holdout excess is non-positive versus same-family baseline; executable coverage is still a separate limitation |
| `tail_center_contrast` | `structurally_weak_proxy` | 104 | 0 | 44 | -1.7% | -1.4% | decision-proxy holdout excess is non-positive versus same-family baseline; executable coverage is still a separate limitation |

## Decision Proxy Results

| strategy | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout mode p | holdout adj3 mass | holdout tail mass | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `shoulders_cheap_butterfly` | 19 | 4 | 3 | 14 | 3 | 3 | +7.5% | [-100.0%, +94.2%] | +11.1% | [-88.2%, +107.4%] | +85.0% | [+8.1%, +155.4%] | +79.0% | [+29.9%, +147.1%] | NA | +45.6% | +100.0% | +0.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_rows<10,holdout_active_dates<5,holdout_top5_removed_roi<=0_or_na` |
| `center_band_cheap_neighbors` | 72 | 65 | 10 | 55 | 49 | 5 | +11.8% | [+6.1%, +18.2%] | +0.5% | [-0.6%, +1.8%] | +7.3% | [-8.4%, +19.2%] | -0.1% | [-4.8%, +2.5%] | NA | +46.7% | +91.2% | +8.8% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |
| `tail_center_contrast` | 72 | 60 | 10 | 53 | 44 | 5 | +6.7% | [+0.6%, +14.9%] | -1.0% | [-2.9%, +0.8%] | +1.9% | [-13.6%, +11.8%] | -1.7% | [-6.1%, +1.7%] | NA | +47.3% | +91.8% | +8.2% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |
| `center_cheap_butterfly` | 64 | 59 | 13 | 50 | 48 | 6 | +5.8% | [-4.7%, +12.9%] | -0.5% | [-6.4%, +2.6%] | +4.7% | [-13.0%, +22.1%] | -2.2% | [-4.1%, +0.0%] | -25.0% | +49.4% | +91.9% | +8.1% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |

## Executable Orderbook Subset

Orderbook 匹配复用既有逻辑：同 condition/token 取 latest `snapshot_ts_utc <= decision_snapshot_ts_utc`，并要求该 strategy 所有 leg 都匹配成功。

- Leg matched: `706` / `1552`.
- Fully matched family rows: `180` / `399`.
- Fully matched selected rows: `149` / `332`.

| strategy | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout mode p | holdout adj3 mass | holdout tail mass | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `shoulders_cheap_butterfly` | 0 | 0 | 0 | 14 | 3 | 3 | NA | NA | NA | NA | +68.2% | [+3.1%, +140.1%] | +67.7% | [+28.5%, +134.5%] | NA | +45.6% | +100.0% | +0.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_rows<10,holdout_active_dates<5,holdout_top5_removed_roi<=0_or_na` |
| `center_band_cheap_neighbors` | 1 | 0 | 0 | 55 | 49 | 5 | NA | NA | NA | NA | +3.5% | [-11.4%, +14.8%] | +0.1% | [-4.0%, +2.9%] | NA | +46.7% | +91.2% | +8.8% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |
| `tail_center_contrast` | 1 | 0 | 0 | 53 | 44 | 5 | NA | NA | NA | NA | -1.3% | [-16.9%, +8.4%] | -1.4% | [-5.8%, +1.6%] | NA | +47.3% | +91.8% | +8.2% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |
| `center_cheap_butterfly` | 6 | 5 | 3 | 50 | 48 | 6 | +14.2% | [-46.2%, +59.6%] | +6.0% | [+0.0%, +10.4%] | +2.1% | [-13.4%, +19.1%] | -2.1% | [-4.0%, +0.0%] | -26.4% | +49.4% | +91.9% | +8.1% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_na` |

## Forecast / Market Diagnostics

| bucket | rows |
|---|---:|
| `22-24` | 399 |

| strategy | rows | avg mode p | avg model adj3 | avg market adj3 | avg model entropy | avg market entropy | avg tail model | avg tail market | avg expected pnl | avg worst loss | avg loss prob | avg large loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `center_cheap_butterfly` | 107 | +50.8% | +93.2% | +67.6% | 1.126 | 1.213 | +6.8% | +32.4% | +0.30 | -0.78 | +42.4% | +0.3% |
| `shoulders_cheap_butterfly` | 7 | +45.1% | +99.1% | +99.6% | 1.070 | 0.951 | +0.9% | +0.4% | +0.17 | -0.92 | +46.1% | +5.7% |
| `center_band_cheap_neighbors` | 114 | +47.8% | +92.4% | +60.7% | 1.173 | 1.245 | +7.6% | +39.3% | +0.57 | -1.29 | +7.3% | +5.0% |
| `tail_center_contrast` | 104 | +47.9% | +92.5% | +58.9% | 1.168 | 1.244 | +7.5% | +41.1% | +0.69 | -1.16 | +7.5% | +4.1% |

## Payoff Examples

完整 `payoff_by_final_temp`、`expected_pnl`、`worst_case_loss`、`loss_probability`、`large_loss_probability` 写入 JSON 的 `candidate_samples_by_strategy`。

## 8 环覆盖自检

| 环 | 覆盖 | 说明 |
|---|---|---|
| 1 描述性绩效切片 | yes | opportunity-grain counterfactual from fact_signal_candidates |
| 2 统计推断 | yes | event_date cluster bootstrap |
| 3 信号判别 | partial | forecast-first mode/adj3/tail filters, no independent calibration proof |
| 4 概率分布评估 | partial | entropy/mass diagnostics only |
| 5 执行微结构 | partial | time-aligned orderbook executable subset |
| 6 容量 | no | no size/depth sweep |
| 7 组合相关性 | partial | event_date cluster bootstrap, no rho model |
| 8 基准/反事实 | yes | same-family baseline and excess ROI |

## Notes

- 四个表达是预注册固定定义；没有按城市/日期/model post-pick winner。
- 三门不过即 `inconclusive`；不允许改 live、city_pool、paper_policy 或 N100 配置。
- `eligible` 没有参与过滤；这是 full opportunity counterfactual 研究。
