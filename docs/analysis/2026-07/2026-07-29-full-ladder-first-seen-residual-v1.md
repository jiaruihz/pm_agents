# Full-Ladder First-Seen Residual v1 — Phase 0 / 首轮 forward 审计

Status: `inconclusive_measurement_head_not_ready`

Scope: `research + zero-notional`; no plan/order/fill/exit/live change

## 结论

当前动作是**继续 collector，但不启动 expression policy**。first-seen canonical
分母已经产生，但概率更新头和可执行证据都没有达到可评价状态：

1. `1,501` 个 information events / `1,501`
   个 checkpoint 展开为 `33,022` 个 v2 expressions；
2. 只有 `196` 个 checkpoint 至少有一个
   scored expression，完整双边可执行 ladder 为
   `0`；
3. 已结算 checkpoint/date 为 `0` /
   `0`，因此不能计算同 rows market proper score、
   fee ROI 或任何 live 结论；
4. 当前 `model_probability_before/after` 只是 paper-snapshot model telemetry。
   `55` /
   `1,501` checkpoints 出现数值变化，但 builder 没有让一个
   fitted first-seen residual head 消费 trigger event 的 `feature_frame_ref`；所以这些变化
   不能归因给 source event，`diagnostic_net_edge` 也不能当策略 edge。

这轮没有把 coverage gap 包装成筛选器，也没有从未结算的正 residual 推导收益。

## 数据快照

- candidates：`/Volumes/jrs/weather_data_feed_service_runtime/output/first_seen_zero_notional/candidates.jsonl`
- candidate mtime UTC：`2026-07-28T16:09:05.003707+00:00`
- settlement DB：`/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- settlement DB mtime UTC：`2026-07-28T16:26:01.924612+00:00`
- rows：`33,022`；unsettled checkpoint：
  `1,501` /
  `1,501`；missing bracket 无法在未结算期判定

## Frozen research target

在每个 genuinely new information event 首次可用后，估计完整
`P(final exact bracket | PIT event + path + forecast + market)`，并在相同 checkpoint
上检验 M2/M3 相对 normalized market ladder 的 multiclass logloss、Brier 和 RPS。
先赢 probability baseline，再评 full-depth taker expression。

## Signal funnel

- v2 candidate rows：`33,022`
- trigger events / checkpoints：`1,501` /
  `1,501`
- cities / target dates：`47` / `3`
- scored expressions：`472`
- diagnostic edge > 0 / > 1c：`332` /
  `185`（仅 telemetry，不是策略选择）
- policy_selected：`0`

## Evidence funnel

- PIT lineage class 随 candidate export：`0`；
  当前必须回连 canonical event table 才能区分 `collector_exact` 与 archive lineage
- feature-frame available checkpoints：`1,394`
- full model distribution / simplex±2c：`945` /
  `900`
- full market distribution / simplex±2c：`1,501` /
  `786`
- any scored checkpoint：`196`
- full two-sided executable ladder：`0`
- settled checkpoints / dates：`0` /
  `0`
- actual fills：`0`

主要 blocker：`{"missing_executable_ask": 23895, "missing_feature_frame": 2354, "missing_model_probability": 12232, "missing_post_event_book": 20096}`。

## 研究判定

- 当前输出是 M0/M1 measurement baseline，不是已经训练完成的 M2 source innovation head。
- 下一份 frozen artifact 只允许：
  `normalized market prior + source innovation + compact path/remaining heat`；
  train/OOF 按 target_date 整块滚动，不能同日泄漏。
- 每个 checkpoint 保存全部 bracket×side，执行层每 city-date 最多选择一个 expression；
  price/depth 缺失进入 evidence funnel，不是 eligibility filter。
- Atlanta 2026-07-17 必须固定进入 source-basis negative-control 报告；它不能被 QC pass
  或 persistent cross 洗掉。

## Gate

`significance=NA baseline=NA
forward=FAIL conclusion=inconclusive`。

冻结 forward 的最低复核条件仍是 ≥15 settled target dates、≥300 个全分母
scoreable observations、≥30 个 full-depth policy opportunities；在此之前只继续
zero-notional collector，不改变任何 live runner。

## 8 环

- covered：canonical v2 opportunity denominator、signal/evidence funnel、full-distribution
  consistency、event前后 measurement audit、top-book/fee diagnostic。
- missing：settlement/proper score、full-depth execution、capacity、真实 fill/queue、
  frozen forward significance、组合相关性。
