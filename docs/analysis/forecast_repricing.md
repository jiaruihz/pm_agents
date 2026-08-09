# Forecast Repricing

`Forecast Repricing` 是独立于 Core Carry 的短周期 market-response family。它预测 D-2/D-1 forecast revision 被本系统 first-seen 后，完整 temperature ladder 在未来 5/15/30/60 分钟的可执行价格变化；它不预测最终 Tmax winner，也不继承 Core Carry 的持仓、阈值或 live 授权。

## 当前判定

状态：`inconclusive / no tradable edge / zero-notional forward not deployed`。

- 固定 `city × target_date × forecast-event` 分母的历史重建覆盖 2,767 个 events、28,038 个 ladder rungs，其中 selected 2,767、未 selected 25,271；D-1 为 2,666 events/40 dates，D-2 只有 101 events/11 dates。
- 5m 无覆盖；15m 只有 4 dates；只有 30m/60m 达到历史建模下限。development expanding OOF 选中 60m `weather + market level` Ridge，threshold 固定为 predicted taker net markout > 0。
- secondary chronological holdout 中，weather+market 相对 market-level 的 full-ladder MSE delta 为 `-0.0000020`，target-date bootstrap 95% CI `[-0.0000058,+0.0000009]`，跨 0。加入静态 microstructure 后相对 market+micro control 的 weather 增量同样跨 0。因此尚未证明独立 forecast alpha。
- 同一 holdout 的 taker 为 28 个可评分 signals/4 dates，fee-adjusted ROI `-31.96%`，CI `[-58.23%,-17.74%]`；拒绝 taker。
- 60m conditional-maker point estimate 为 `+6.62%`，CI `[-18.56%,+13.46%]`，actual maker fills=0。future touch 从未当作 fill；queue、partial fill、expire 与 adverse selection 均未观测，所以它不是可实现 ROI。
- terminal settlement head 单独失败：legacy model Brier `0.08212` vs market `0.06737`，delta `+0.01474` CI `[+0.01249,+0.01691]`；该负结论不能拿来替代短周期 repricing 检验。

## 冻结研究合同

- primary horizon：60m；5/15/30m 只保留 coverage/secondary diagnostics，不能在 forward 中重选。
- primary model：`weather_plus_market_level` fixed Ridge；threshold=0；每个 forecast event 最多一档、每个 city-target_date 只取 first signal。
- required comparisons：market level only、weather innovation only、weather+market、weather+market+static microstructure；另保留 market+static microstructure control，用于隔离 weather 的真实增量。
- 权重：target_date 等权，再 event 等权，再 rung 等权；bootstrap block=`target_date`。
- clocks：forecast issue time、provider availability/first-seen、collector first-seen、book snapshot/available time、virtual execution time 分列。缺 provider clock 时保持 missing，不得借 collector clock替代。
- taker：entry ask、真实 top depth、future bid、双边 Weather fee；maker：posted bid、queue ahead、partial fill、expire、adverse selection；touch-only 仅作诊断。
- formal forward：只能用冻结后 `collector_exact` forecast events 与 policy-valid D-2/D-1 ladder capture；archive secondary holdout 不叫 forward。

## 下一步唯一动作

在取得生产 collector 变更确认后，给共享 `weather_market_books` 增加 D-2/D-1 forecast-event full-ladder burst，并运行 zero-notional order-lifecycle forward；不部署真实订单、不改现有 live 策略。固定设计见 [2026-08-09 full-ladder/readiness snapshot](2026-08/2026-08-09-forecast-repricing-full-ladder-readiness-v1.md)。

可复跑 artifact：

- base：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260809`
- evaluation：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_evaluation_20260809`
- input SHA-256：`8be4d7c0a0f209f692df7935f6cb99ba9ad8914ef55f518818b20dd62e5f3900`
- scorer SHA-256：`e6f83778841a864e6770d2a6c9b174c08e82b7bacecc2071be8dbf51a04859ad`；仅 collector-only scoring，不是 live artifact。
