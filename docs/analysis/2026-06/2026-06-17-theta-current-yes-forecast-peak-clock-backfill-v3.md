# Theta Current YES Forecast Peak Clock Backfill v3

Status: research_backfill / not_live_ready
Generated: 2026-06-17T15:58:35+00:00

Target metric: `forecast_peak_clock_current_yes_backfill` = 用历史预报 API 补出 forecast peak hour 后，测试 current-YES 的固定小时规则能否被“相对预报峰值时间”替代或增强。

## 数据完整性自检

- fact_built_at_utc: `2026-06-17T15:49:12.423102+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 31496, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- forecast peak fact coverage: `{'rows': 31496, 'with_peak_hour': 108, 'with_hash': 108, 'min_event_date': '2026-05-05', 'max_event_date': '2026-06-18'}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`
- CLOB gate: `{'gate_pass': True, 'fail_reasons': [], 'missing_order_rows': 0, 'over_order_keys': 0, 'db_fill_cost_minus_fact_cost': 0.0}`

## 人话结论

这次把缺失的 forecast peak clock 用 Open-Meteo Historical Forecast API 补到了 replay 层，终于可以做第一版策略测算。但它仍然是 `backfilled forecast`：说明这个因子方向是否值得继续，不等于生产当时 snapshot 已经可靠落盘。

最重要的结果：forecast peak clock 没有自动把 current-YES 变成更强 live 规则。固定 v9 post-decline 规则仍是当前最稳的候选；GFS fade 分支点估很好但只有 10 单/6 天，样本太薄；peak-forming 分支样本更多但 CI 跨 0，不能靠它直接上线。

## Holdout 核心结果（$5/order, taker +2c）

| rule | orders | dates | win | ROI | CI95 | avg ask | avg p | orders/day |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| v9_fixed_fade_confirmed | 31 | 11 | +93.5% | +16.2% | [+2.7%, +27.9%] | 0.794 | 0.888 | 2.8 |
| gfs_after_peak_fade_confirmed | 10 | 6 | +100.0% | +12.1% | [+10.3%, +14.3%] | 0.873 | 0.955 | 1.7 |
| ecmwf_after_peak_fade_confirmed | 15 | 11 | +86.7% | +1.4% | [-25.7%, +19.6%] | 0.832 | 0.923 | 1.4 |
| gfs_peak_forming_plateau | 40 | 12 | +85.0% | +9.1% | [-11.1%, +26.4%] | 0.772 | 0.875 | 3.3 |
| ecmwf_peak_forming_plateau | 54 | 13 | +83.3% | +6.3% | [-6.9%, +18.0%] | 0.768 | 0.871 | 4.2 |

## Grid Search Sanity

下面只展示 train ROI 最高的 10 个 forecast-clock 变体，用来看有没有明显可前瞻迁移的参数族；它不是 live 参数选择器。

| rule | train rows/dates | train ROI | holdout rows/dates | holdout ROI | holdout win |
|---|---:|---:|---:|---:|---:|
| gfs_peak_forming|ask>=0.55|edge>=0.08|d<=0.1|delta-1-0.0 | 16/12 | +24.0% | 22/11 | +9.3% | +81.8% |
| gfs_peak_forming|ask>=0.65|edge>=0.08|d<=0.1|delta-1-0.0 | 15/11 | +20.6% | 18/11 | -2.4% | +77.8% |
| gfs_peak_forming|ask>=0.65|edge>=0.08|d<=0.1|delta-1-1.0 | 23/11 | +20.3% | 25/12 | +6.9% | +84.0% |
| gfs_peak_forming|ask>=0.55|edge>=0.08|d<=0.1|delta-1-1.0 | 26/12 | +19.6% | 28/12 | +13.9% | +85.7% |
| ecmwf_peak_forming|ask>=0.55|edge>=0.08|d<=0.1|delta-1-1.0 | 28/11 | +16.9% | 36/13 | +14.2% | +86.1% |
| gfs_peak_forming|ask>=0.65|edge>=0.08|d<=0.1|delta-1-2.0 | 26/11 | +16.3% | 26/12 | +8.2% | +84.6% |
| ecmwf_fade_confirmed|ask>=0.55|edge>=0.05|d>=0.5|delta0-4.0 | 14/9 | +16.1% | 15/11 | +1.4% | +86.7% |
| ecmwf_fade_confirmed|ask>=0.55|edge>=0.05|d>=0.5|delta0-6.0 | 14/9 | +16.1% | 15/11 | +1.4% | +86.7% |
| ecmwf_peak_forming|ask>=0.55|edge>=0.03|d<=0.1|delta-1-0.0 | 57/12 | +15.4% | 53/14 | +1.0% | +81.1% |
| gfs_peak_forming|ask>=0.55|edge>=0.08|d<=0.1|delta-1-2.0 | 28/12 | +15.2% | 29/12 | +14.9% | +86.2% |

## 交易动作

- 不把 forecast peak clock 直接推进 live。
- 保留 v9 fixed post-decline tiny-live 候选，但部署前必须用生产 snapshot 原生 peak fields 重新确认，而不是只信 backfill。
- forecast-clock 下一步应该作为 live/shadow telemetry 字段落盘：记录 `decision_hour - forecast_peak_hour`、forecast max gap、GFS/ECMWF peak disagreement，再用新增前瞻样本复核。

## 三道门

- significance=FAIL/LOW_SAMPLE：GFS fade 的 CI 不跨 0 但只有 10 单/6 天，低于样本门槛；peak-forming 样本更多但 CI 跨 0。
- baseline=FAIL：未证明 forecast-clock 规则优于 v9 fixed fade-confirmed。
- forward=FAIL：train grid 高 ROI 变体没有稳定迁移到 holdout。
- conclusion=`inconclusive` for forecast-clock live；`telemetry_required` for production field logging。

## 产物

- JSON: `docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-peak-clock-backfill-v3.json`
- summary CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3/rule_summary.csv`
- grid CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3/grid_search.csv`
- joined rows CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3/forecast_peak_joined_rows.csv`
- Script: `scripts/analysis/reheat_risk/research_theta_current_yes_forecast_peak_clock_backfill_v3.py`
