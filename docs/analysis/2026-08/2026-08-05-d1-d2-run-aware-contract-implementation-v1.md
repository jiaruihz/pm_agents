# D-1 / D-2 run-aware 数据合同实施报告 v1

weather-only:
significance=blocked_no_settled_run_aware_history
calibration=blocked_no_settled_run_aware_history
pooled_baseline=retained_as_negative_control_not_refit
forward=not_started_before_verified_collector_deployment

market residual:
baseline=normalized_same_checkpoint_full_ladder
forward=not_run_by_contract
execution=not_run_by_contract

production:
live_action=none
orders_changed=0

## 结论

严格合同、collector 代码、fixture、真实 one-shot、live/replay parity、dataset builder 和 weather-only fail-closed gate 已实现。真实 one-shot 证明 Open-Meteo exact single-run endpoint 可为五个模型提供可审计 run；但当前只有未结算的新样本，clean OOF rows 为 0，因此 A-G 不允许拟合，更不允许继续 market residual 或 ROI。

下一步需要把新 collector 登记到 Mac canonical JRS production controller，持续积累 append-only D-1/D-2 run evidence。该动作会改变生产 collector 行为，尚未执行，必须先取得明确确认。

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
| tests | 47 focused tests passed |
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

Focused tests：`47 passed`。production controller health/plan、strict manifest 和 storage identity audit 均只读执行成功；canonical DB route healthy。现有 warning 为当前系统既存的 forecast curve/state coverage 与 storage schema warnings，本次没有改生产进程，也没有订单/fill 影响。

## 尚未做与 blocker

- 未登记/启动 production collector；所以没有部署后 first complete target date，frozen forward 尚未开始。
- 未拟合 A-G；原因是 clean settled target dates=0，不是用 legacy history 补洞。
- 未运行 market residual、交易表达、shadow/live；均由合同阻断。
- 未修改 live 策略、订单、city pool、sizing 或 execution policy。

## 生产采集变更请求

拟部署对象只是一条 research data collector：Mac host、canonical JRS tmux、exact single-run API、五模型、全城市、D-1/D-2 append-only rows/batches/blockers；execution mode=`coverage-only`，不产生 SignalCandidate、TradeIntent 或订单。

建议调度为每 30 分钟尝试当前明确 6h cycle candidate；同一个 candidate unavailable 时只写 blocker并在下一轮重试，绝不改请求到旧 run。成功后保存真实 request/raw hash和 collector first-seen。rollback 是从 production desired state 移除此 collector并停止其精确 session，保留已采 raw。
