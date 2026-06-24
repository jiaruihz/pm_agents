# Current-Bracket NO Prevday PIT Shadow v1

## 数据快照

- 事实表已重建；CLOB fill coverage gate：`True`。
- PIT forecast 来源：`paper_snapshots` + 远端同步的 `weather-predict/cache/gfs_daily`；每个 city-date-model 取目标日前一天可见的 forecast，6/20+ paper snapshot 优先。
- 历史 feature rows：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/reheat_feature_rows.csv`，范围 `2026-05-20`..`2026-06-20`。
- PIT 可检验覆盖：`1223` city-days，日期 `2026-05-20`..`2026-06-25`。
- 生成时间：`2026-06-23T15:13:39+00:00`。

## 结论

这次补上了旧 `weather-predict` 的前一日 GFS daily forecast cache；因此历史 PIT 可检验分母不再只有 `2026-06-20` 一天。

把 backfill-trained classifier 改为消费 PIT prevday forecast 后，在可检验 PIT 窗口上 `pit_prevday_logit_c0p2_p_ge_0p50` 选出 191 笔；ROI +29.7%，CI [+3.8%, +54.6%]。

同一 NO ask/cap baseline 选 361 笔，ROI -8.5%；classifier excess ROI +38.1%，CI [+23.3%, +53.7%]。

注意：历史 PIT 主要来自 GFS daily cache，6/20 后才有 paper snapshot 双模型字段；历史 significance/baseline 两门已过，但 forward 还只有 zero-notional candidates、没有结算结果。

Forward/shadow：`shadow_candidates_generated`，zero-notional candidates `13`。原因：zero-notional forward shadow candidates only; no settlement labels and no orders placed。

交易动作：`shadow_candidate_keep_collecting`，继续收这些候选的 settlement；不 live。

## Variant Table

| variant | raw_signals | selected_trades | active_dates | avg_no_ask | no_win_rate | actual_peak_afternoon_rate | roi | roi_ci_low | roi_ci_high | holdout_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pit_prevday_baseline_ask10_35_depth5_ge5 | 480 | 361 | 32 | 0.232 | 0.205 | 0.449 | -8.5% | -27.6% | +10.0% | +6.7% |
| pit_prevday_logit_c0p2_p_ge_0p50 | 220 | 191 | 32 | 0.222 | 0.283 | 0.681 | +29.7% | +3.8% | +54.6% | +67.4% |

## 口径

- `decision_minus_peak = decision_hour_local - forecast_peak_hour_local`，沿用 feature factory 的实际字段符号。
- 交易价格仍使用同窗真实 `NO ask` 和 `no_depth_ask_5c`，没有用 `1 - YES ask` 推断 NO。
- 6/21+ forward rows 来自本轮新补的 IEM observed cache + reheat feature factory；没有 settlement label，只能做 shadow candidate。

## 输出

- JSON：`docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/summary.json`
- PIT forecast rows：`docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/prevday_pit_forecast_rows.csv`
- Scored PIT rows：`docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/scored_pit_rows.csv`
- Shadow candidates：`docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/shadow_candidates.csv`
- Forward feature factory：`docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/forward_feature_factory/reheat_feature_rows.csv`
