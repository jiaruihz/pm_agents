# Forecast-first adjacent2/3 Range RV

## 数据快照

- 数据源：`runtime/weather.db` 的 `fact_signal_candidates` / `fact_trades`；orderbook 仅用于 executable price replay。
- 数据血缘：`fact_signal_candidates` 是 paper snapshot 全机会宇宙 + paper intended orders + `fact_trades` live_real actual fills 的机会表。本实验主分母来自全机会宇宙，不要求这条 Range RV 策略历史上真的下过单。
- `decision_proxy` 用历史 decision snapshot 里的 market YES price 模拟反事实成本；`time_aligned_orderbook` 用历史 raw orderbook 的 best ask，且必须满足 `orderbook_snapshot_ts <= decision_snapshot_ts_utc`。
- 因为这是新 Range RV 表达，当前没有“这个策略自己的 live fills”；若要真实 live 级别 fill/slippage/queue 证据，需要先以 shadow/paper 或小额受控 live 跑出订单和成交记录。
- DB mtime UTC：`2026-06-09T15:09:06.567273+00:00`。
- fact built at：trades `2026-06-09T15:08:45.011642+00:00`；candidates `2026-06-09T15:09:02.643978+00:00`。
- fact rows：trades `5473`；signal candidates `25100`。
- unsettled rows：`172`；missing_bracket rows：`0`。
- CLOB coverage gate：`True`。本报告不是 live_real PnL/ROI 发布；该 gate 只作为数据完整性状态声明。

## Target Metric

`forecast_first_adjacent_range_rv_alpha` = 在每个 `city + event_date + decision_snapshot_ts_utc` decision set 内，先由 forecast distribution 的 mode 确定相邻 2/3 个 YES bracket range，再评估 model mass 相对 executable market cost 的超额 ROI。

## Filter Funnel

| step | count |
| --- | --- |
| fact_signal_candidates rows | 25100 |
| settled + decision_window present rows | 2293 |
| YES leg rows used for range enumeration | 837 |
| decision_sets count | 196 |
| enumerated adjacent2 / adjacent3 ranges | 296 |
| around model mode | 260 |
| all legs have price | 260 |
| all legs have spread | 120 |
| spread cap | 114 |
| orderbook fully matched strategy rows | 114 |

- mass thresholds counts：`{'0.55': 113, '0.6': 112, '0.65': 111, '0.7': 106}`
- market cost thresholds counts：`{'0.7': 109, '0.75': 111, '0.8': 112}`
- edge thresholds counts：`{'0.05': 114, '0.1': 114, '0.15': 114}`
- hour buckets counts：`{'T-12-18': 0, 'T-18-24': 113, 'T-24-36': 1, 'T-36+': 0}`
- train rows/date：`86` rows, `9` dates `2026-05-20` to `2026-05-29`。
- holdout rows/date：`28` rows, `5` dates `2026-05-30` to `2026-06-08`。

## Results

| slice | source | verdict | gates | train rows | train ROI | train excess | holdout rows | holdout ROI | holdout excess | holdout top5 removed ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_opportunity | decision_proxy | inconclusive | FAIL/FAIL/FAIL | 63 | +8.7% | +4.0% | 23 | +1.5% | -2.6% | NA |
| full_opportunity | time_aligned_orderbook | inconclusive | FAIL/PASS/FAIL | 63 | -0.0% | +3.3% | 23 | -7.2% | -2.9% | NA |
| old_eligible_control | decision_proxy | inconclusive | FAIL/FAIL/FAIL | 23 | +20.2% | +10.2% | 14 | +7.5% | -2.5% | NA |
| old_eligible_control | time_aligned_orderbook | inconclusive | FAIL/FAIL/FAIL | 23 | +15.3% | +9.5% | 14 | -0.6% | -2.7% | NA |

## Selected Profiles

### full_opportunity / decision_proxy

- profiles preregistered/tested on train：`288` / `108`。
- selected profile：`{'range_type': 'adjacent2', 'model_mass_min': 0.6, 'market_cost_max': 0.75, 'edge_min': 0.05, 'decision_hours_bucket': 'T-18-24'}`。
- train ROI：+8.7% CI [-22.4%, +32.1%]；baseline +4.7%；excess +4.0% CI [+0.0%, +6.1%]；top5 removed -27.7%。
- holdout ROI：+1.5% CI [-100.0%, +33.3%]；baseline +4.1%；excess -2.6% CI [-21.4%, +0.9%]。
- holdout top5 removed ROI：NA；active dates `5`。
- gates：`FAIL/FAIL/FAIL` -> `inconclusive`；reasons `['train_roi_ci_crosses_or_below_zero', 'train_excess_roi_ci_crosses_or_below_zero', 'holdout_roi_or_excess_ci_crosses_or_below_zero']`。

### full_opportunity / time_aligned_orderbook

- profiles preregistered/tested on train：`288` / `108`。
- selected profile：`{'range_type': 'adjacent2', 'model_mass_min': 0.6, 'market_cost_max': 0.75, 'edge_min': 0.05, 'decision_hours_bucket': 'T-18-24'}`。
- train ROI：-0.0% CI [-29.3%, +23.2%]；baseline -3.3%；excess +3.3% CI [+0.1%, +4.9%]；top5 removed -34.1%。
- holdout ROI：-7.2% CI [-100.0%, +21.5%]；baseline -4.4%；excess -2.9% CI [-20.7%, +0.0%]。
- holdout top5 removed ROI：NA；active dates `5`。
- gates：`FAIL/PASS/FAIL` -> `inconclusive`；reasons `['train_roi_ci_crosses_or_below_zero', 'holdout_roi_or_excess_ci_crosses_or_below_zero']`。

### old_eligible_control / decision_proxy

- profiles preregistered/tested on train：`288` / `72`。
- selected profile：`{'range_type': 'adjacent2', 'model_mass_min': 0.55, 'market_cost_max': 0.75, 'edge_min': 0.05, 'decision_hours_bucket': 'T-18-24'}`。
- train ROI：+20.2% CI [-43.2%, +69.5%]；baseline +10.0%；excess +10.2% CI [-1.3%, +18.6%]；top5 removed -100.0%。
- holdout ROI：+7.5% CI [-100.0%, +53.5%]；baseline +10.0%；excess -2.5% CI [-21.4%, +5.1%]。
- holdout top5 removed ROI：NA；active dates `5`。
- gates：`FAIL/FAIL/FAIL` -> `inconclusive`；reasons `['train_roi_ci_crosses_or_below_zero', 'train_excess_roi_ci_crosses_or_below_zero', 'holdout_roi_or_excess_ci_crosses_or_below_zero']`。

### old_eligible_control / time_aligned_orderbook

- profiles preregistered/tested on train：`288` / `72`。
- selected profile：`{'range_type': 'adjacent2', 'model_mass_min': 0.55, 'market_cost_max': 0.75, 'edge_min': 0.05, 'decision_hours_bucket': 'T-18-24'}`。
- train ROI：+15.3% CI [-44.6%, +66.6%]；baseline +5.8%；excess +9.5% CI [-1.0%, +17.5%]；top5 removed -100.0%。
- holdout ROI：-0.6% CI [-100.0%, +41.4%]；baseline +2.1%；excess -2.7% CI [-20.6%, +3.1%]。
- holdout top5 removed ROI：NA；active dates `5`。
- gates：`FAIL/FAIL/FAIL` -> `inconclusive`；reasons `['train_roi_ci_crosses_or_below_zero', 'train_excess_roi_ci_crosses_or_below_zero', 'holdout_roi_or_excess_ci_crosses_or_below_zero']`。

## 三门结论

- significance：`FAIL`；baseline：`PASS`；forward：`FAIL`。
- final conclusion：`inconclusive`。
- live action：`none; three gates must pass before any live action`。
- `top5 removed ROI` 不是三门硬门；它只是压力测试，用来提示收益是否过度依赖少数日期。它不单独否决策略，但若 train/holdout CI 已经不稳，它会提高过拟合风险判断。

## 8 环覆盖自检

- 1 描述性绩效切片：覆盖，基于 `fact_signal_candidates` counterfactual range rows。
- 2 统计推断：覆盖，bootstrap 按 `event_date` cluster。
- 3 信号判别：部分覆盖，用 forecast mode/mass 和 model-cost edge，不做额外 IC。
- 4 概率分布评估：部分覆盖，仅检验 range mass，不做全分布校准。
- 5 执行微结构：覆盖，可执行版本使用 time-aligned orderbook all-leg match。
- 6 容量：未覆盖，仅使用 best ask，不做 depth/size 放大。
- 7 组合相关性：部分覆盖，event_date cluster bootstrap。
- 8 基准/反事实：覆盖，baseline 是同 width/hour bucket 的 around-mode range before mass/cost/edge filters。

## 样本过滤误伤风险

- 风险判断：`high`。
- 依据：tradable proxy rows 114 / around-mode rows 260; executable orderbook rows 114 / decision-proxy rows 114. The main injury risk is the all-leg spread requirement and spread cap; orderbook all-leg matching did not remove additional rows in this run.

## Notes

- 本任务是 counterfactual research，未改 N100/live 配置。
- 旧单腿 `eligible` 没有作为主分析硬门，只作为 old eligible control slice。
- 不按城市或日期事后挑 winner；profile 网格按 width/mass/cost/edge/hour bucket 预注册。
- 证据阶梯：先用历史 fact opportunity + time-aligned orderbook 判断是否值得观察；若仍有希望，下一步应跑 shadow/paper 记录 would-trade 决策和盘口；只有 shadow/paper 稳定后，才考虑小额受控 live 来收集真实 fill、滑点和排队证据。
