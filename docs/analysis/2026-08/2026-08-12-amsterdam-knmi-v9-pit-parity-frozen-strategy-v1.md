# Amsterdam KNMI V9：PIT 特征合同修复、冻结评测与策略表达

状态：`V7 production evidence invalidated / V9 release pinned / activation waiting for shared data-feed recovery / no live capital`

## 结论

Amsterdam 旧 V7 不是“效果差一点”，而是 production feature contract 有 bug：artifact 要求 145 个字段，runtime 只真正生成 80 个，65 个 forecast/path forcing 字段长期以 NaN 进入模型；WCIR migration 又没有把 `feature_coverage` 与 `missing_features` 传到公共 lineage。该生产证据不得再用于评价 V7 算法。

修复后的 V9 使用 2024-03-01..2025-12-31 的 92,637 个 10-minute checkpoints、665 个 target dates，输入为 80 个 KNMI/official/solar/path 特征加 20 个固定 `ECMWF previous_day1` forecast-path 特征。训练和 expanding OOF 均不读取 2026-08 数据。

V9 在 2025 expanding OOF 的结果：

| grain | rows / dates | accuracy | Brier | logloss |
|---|---:|---:|---:|---:|
| all checkpoints | 50,525 / 363 | 93.67% | 0.04574 | 0.15819 |
| 12:00–16:00 local | 8,994 / 362 | 85.84% | 0.10258 | 0.32913 |

真正的一次性 8 月 frozen test 是先冻结的 V8，不是后来吸收诊断得到的 V9。V8 在 2026-08-01..11 的 1,101 settled checkpoints / 10 dates 上 accuracy 91.37%、Brier 0.05497、logloss 0.18012；同盘口 361 rows / 8 dates 上 Brier 0.09516，显著差于 market 0.03250，paired date-bootstrap ΔBrier（model-market）为 `+0.06267`，95% CI `[+0.00671,+0.15144]`。

V9 的同一 8 月窗口只能称 post-freeze development audit：全 checkpoint accuracy 97.46%、Brier 0.02182、logloss 0.08230；同盘口 Brier 0.04252，已把 model-market ΔBrier 收窄到 `+0.01002`，95% CI `[-0.00473,+0.02503]`，但尚未打败 market。

## 策略口径

表达只看 KNMI `:10/:40`（下一份 routine METAR 前约 10 分钟），在同一 current exact bracket 的 YES/NO 中选择模型净 edge 更高的一侧；entry 使用 t0 captured best ask、top ask depth 至少 5 shares、5 shares sizing，并扣 Weather fee。每个 `target_date × current bracket` 只取首次 eligible 信号。

8 月 post-freeze development audit 中，`edge_after_fee >= 2pp`、side probability >=55% 得到 7 笔 / 5 dates，4 胜 3 负，taker cost `$19.5305`、PnL `+$0.4695`、ROI `+2.40%`。但 target-date bootstrap 95% ROI CI 为 `[-100%,+34.46%]`；同 clock 的 market-favorite baseline 为 16 笔 16 胜、ROI `+12.72%`。所以当前结论是“正收益 point estimate，alpha 未确认”，只能进入 zero-notional clean forward，不能升 live。

V9 的 `2pp` policy 是在 post-freeze development 后锁定，配置的最早 clean-forward boundary 为 Amsterdam local 2026-08-12 00:00（UTC 2026-08-11 22:00）；真正样本从 production controller 成功重启后的首个 V9 decision 开始。以后不再用 8 月 1–11 日调该 policy。

## Bug 影响半径与修复

- 受影响窗口：WCIR V7 自 2026-08-03 至 2026-08-11。
- raw 中去重后 538 个 V7 model outputs / 8 dates，产生 10 个 selected zero-notional intents / 8 dates。
- 10 个 intent 的 `requested_size=0`；公共 execution handoff 中 0 条命中；canonical `fact_trades` 为 0 fills、`$0` fill cost。没有真实资金损失，但这段 V7 probability/shadow evidence 标记为 feature-contract polluted。
- 新 adapter 对结构缺列 fail closed；每个 ModelOutput/SignalCandidate 保存 `feature_coverage`、`missing_features`、forecast capture path/line/hash/available-at。
- operational forecast collector 新增 Amsterdam `ECMWF previous_day1` immutable curve；2026-08-12/13 已各生成 24 小时完整曲线。训练历史使用同一参数与固定 lead semantics，避免 current forecast、GFS 与 ECMWF 混用。
- expression 已扩为互补 YES/NO，映射各自 token、ask、mid；仍为 `zero_notional`、requested size 0。
- code 与 `forecast_collector/city_runtime` release 均已 pin 到 `aa4040daeaa1943ed919d5db7591430ef9a42a07`。forecast collector 已加载该 SHA，并在 shared weather proxy 缺失时按新合同输出 `degraded`；city runtime 因既有 `weather_data_feed_jrs -> weather_market_books -> 7896` dependency unhealthy 被 controller fail-closed 阻止重启，尚未产生首个 V9 forward decision。不得把 release pin 写成 shadow 已激活。

## 可重复证据

- V8 frozen reproduction：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v8/frozen_2026_08_reproduced/metrics.json`
- V9 artifact：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v9_ecmwf_day1/run=61b8cde77f5320a4/model_v9.pkl`，SHA-256 `c0bcb0afe1a86da376e86affb411e146b26c292c68bad9c9b6db246acaecd4d2`
- V9 training metrics：同 run 下 `metrics.json`
- V9 development replay：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v9_ecmwf_day1/development_2026_08_run61b8/metrics.json`
- fixed-lead history：`/Volumes/jrs/weather_data_feed_service_runtime/research/amsterdam_ecmwf_previous_day1_path_v1/forecast_hourly.csv.gz`，SHA-256 `821ae0c4c613a5b863b9c86927dd9f5c02c4cbaecb9d60bd982001fd96c57b42`

下一次策略判断只看 V9 实际激活后新增的 settled target dates；30 dates 前只报告累计 funnel 与诊断，不用同一 forward 标签继续换阈值。
