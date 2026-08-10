# D-1 / D-2 run-aware 数据合同实施报告 v1

weather-only:
significance=blocked_no_settled_run_aware_history
calibration=blocked_no_settled_run_aware_history
pooled_baseline=retained_as_negative_control_not_refit
forward=collector_started_waiting_for_settlement_complete_target_dates

market residual:
baseline=normalized_same_checkpoint_full_ladder
forward=not_run_by_contract
execution=not_run_by_contract

production:
live_action=none
orders_changed=0

## 结论

严格合同、collector 代码、fixture、真实 one-shot、live/replay parity、dataset builder 和 weather-only fail-closed gate已实现。2026-08-05 已取得明确确认并把 coverage-only collector 登记到 Mac canonical JRS production controller。clean 样本尚未结算，因此 clean A-G 仍保持 fail closed；同时已用 legacy 数据完成独立的 challenger 训练与 holdout，未把它冒充 clean forward。

## 设计入口

统一架构图、阶段表、两个 grain、A-G、weather gate、conditional market residual 与 execution gate 已写入 [D-1 / D-2 跨城市 Tmax 概率研究总纲 v1](2026-08-05-d1-d2-cross-city-probability-program-v1.md)。

## 已完成实现

| 交付 | 结果 |
|---|---|
| forecast row/batch/revision contract | `weather_data_feed/forecast_run_contract.py`；真实 run evidence、五时钟、两类 revision、稳定 batch/content hash |
| exact-run collector | `weather_data_feed_service/forecast_run_capture.py`；每模型精确 run、0 fallback、失败写 blocker |
| full ladder contract | native ordering、唯一开放尾档、raw/normalized mass、quote status、ladder hash、D-2 blocker |
| dataset builder | weather signal funnel 与 market evidence funnel 分开，D-2 无 market 仍可 weather-scoreable |
| weather-only gate | A-G 固定登记；clean settled dates 不足时 fail closed，不读取 legacy daily cache |
| tests | 合同初验 47 tests；部署后合同 + legacy v2 定向回归 19 tests passed |
| one-shot sample | Tokyo；5 models；10 D-1/D-2 rows；2 complete batches；0 blockers；0 fallback |
| live/replay parity | immutable raw replay；rows/batches hashes equal；network_fetches=0 |

## 真实 one-shot evidence

采样 run 为 `2026-08-04T12:00Z`。这是请求候选；只有 exact endpoint 返回成功且 request/raw hash 都保存后才成为 row 中的 run evidence。五个模型 `ecmwf_ifs025 / ecmwf_aifs025_single / gfs_global / icon_seamless / jma_seamless` 全部成功。

有效 parity sample：`docs/analysis/2026-08/generated/forecast_run_capture_v1_parity_sample_valid/`。

- forecast rows：10；D-1=5，D-2=5；
- forecast batches：2；每 batch model_count=5、missing_model_keys=[]；
- blockers：0；fallback_attempted=false；
- parity：rows_equal=true、batches_equal=true、network_fetches=0。

## 当前 clean dataset funnel

one-shot dataset 只有 forecast evidence，没有最终 settlement：

| signal funnel stage | count | unit |
|---|---:|---|
| raw forecast versions | 10 | forecast row |
| forecast batches | 2 | forecast batch |
| real-run identified | 2 | forecast batch |
| batch complete | 2 | forecast batch |
| settlement complete | 0 | forecast batch |
| OOF-scoreable | 0 | forecast batch |
| frozen-forward | 0 | forecast batch |

market evidence 当前未与 one-shot 拼接；D-2 market residual 明确为 `d2_market_ladder_unavailable`，不是 signal filter。

## 测试与 production preflight

部署前 focused tests 为 `47 passed`；部署后的合同 + legacy v2 定向回归为 `19 passed`。production controller health/plan、strict manifest 和 storage identity audit 均成功，canonical DB route healthy。collector 部署改变的只有 coverage-only 采集进程；现有 forecast/observation warning 为既存或日切状态，没有订单/fill 影响。

## Production collector 实证

- instance/session：`weather_forecast_run_capture_v1`；checkout=`/Users/deepsleep/projects/pm_agents_forecast_run_prod`；chronological-lineage deployed SHA=`42f6dff511f4658352b1e86c53a4b07030082b5a`。
- 调度：每 30 分钟依次精确请求最近四个 6h candidates；每次请求独立，unavailable 写结构化 blocker，`fallback_attempted=false`。
- 首轮：`2026-08-04T12:00Z` 与 `18:00Z` 均为 34 cities / 5 models / 340 rows / 68 complete batches；`2026-08-05T00:00Z` 为 3/5 models partial；`06:00Z` 当时 5/5 unavailable 并显式 blocked。
- controller health 显示 collector OK；strict manifest 的 DB route healthy。整体 health 的 observation rollover warning 为部署前既存状态，与本 collector 无关。
- 修复后首轮 `15:45:11Z..15:45:37Z` returncode=0；cutover 后 1,020 rows、680 previous-run links、0 backward previous-run，真实 state 已包含 `run_history_by_model_city_target`。这些日期尚未结算，因此先进入 clean development accumulation，不计作 untouched forward。

## Legacy 模型开发（与 clean forward 分轨）

已有数据并未停用。`research_d1_legacy_weather_only_v2.py` 使用 legacy artifact 的 May–Aug best-model training slice：原输入 42,705 rows / 52 城，经 best-model 过滤为 16,916，再得到 5,561 rows / 39 城；这不是项目全部历史。前 18 个 reconstructed dates 选参数，最后 9 dates / 46 states / 21 cities 保持未调参 holdout。最佳 F 为 75% ensemble median、25% assigned forecast，spread beta 被选为 0：

- logloss `1.9763`，pooled Normal `2.8902`，paired Δ=`-0.9139`，95% CI `[-1.3574,-0.4532]`；
- Brier `0.0771` vs `0.0873`；RPS `0.0903` vs `0.1451`；
- rung ECE `0.0269` vs pooled `0.0702`；
- 仍显著差于 market logloss `1.5497`，Δ=`+0.4266`，95% CI `[+0.1211,+0.7907]`。

因此 legacy 结果足以冻结当前 challenger 方向，但不构成 clean weather gate；market residual 仍不运行。正式表与 calibration/tail 见 [D-1 legacy weather-only v2](2026-08-05-d1-legacy-weather-only-v2.md)。

## 尚未做与 blocker

- clean collector 已启动，但尚无部署后 settlement-complete target date，因此 clean frozen forward 仍为 0。
- clean A-G 未拟合；legacy A-F 已独立训练，G 因没有同 clock physical features 明确 blocked。
- D-2 legacy daily cache 无法证明严格 lead/run lineage，因此不强行训练 D-2；新 collector 同时积累 D-1/D-2，结算后分别拟合。
- 未运行 market residual、交易表达、shadow/live；均由合同阻断。
- 未修改 live 策略、订单、city pool、sizing 或 execution policy。

## 当前生产边界

collector execution mode=`coverage-only`，不产生 SignalCandidate、TradeIntent 或订单。`live_action=none`、`orders_changed=0`；未修改 live 策略、city pool、sizing 或 execution policy。

## D-1 独立 gate 修正

`research_d1_d2_weather_only_v2.py` 已改为按 horizon 独立判断 readiness：D-1 满足自身 minimum settled target dates 后即可进入 inner train/development scoring，不再等待 D-2 同时满样本。D-2 继续独立积累，不阻塞 D-1。输出固定保存 `weather_only_status_by_horizon`、`d1_blocked_by_d2=false`，并登记 W0 locked-reference spec SHA；W1 与 M2/M3 必须完成 clean development 结果评审后才生成新 freeze artifact，之后的日期才进入 untouched forward。空数据 smoke 仍明确 blocked，market residual 仍为 `not_run_by_contract`。
