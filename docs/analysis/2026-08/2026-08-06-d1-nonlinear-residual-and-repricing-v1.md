# D-1 nonlinear residual 与 forecast-repricing 研究 v1

weather-only:
significance=FAIL_on_reconstructed_OOF
calibration=legacy_robust_tail_reference
pooled_baseline=retained_negative_control
forward=clean_exact_run_accumulating

market residual:
baseline=contemporaneous_normalized_market
forward=V05_to_V10_failed_to_improve_on_17_date_fixed_denominator
execution=not_run_no_probability_or_repricing_gate

production:
live_action=none
orders_changed=0

## 结论

V04 不是最终模型，只是线性 market-offset baseline。本轮已把候选扩到 V10：piecewise GAM、revision/spread gate、固定 nonlinear hidden layer、hybrid nonlinear、shallow gradient boosting 和 interaction gradient boosting。

在当前可直接读取的 168 states / 17 OOF dates 上：

- market logloss=1.560207；V04=1.560129，仅改善约 0.000077，CI 仍跨 0；
- V06 曾在 5 个 states 偏离 market 超过 2pp，最大 8.56pp，但 logloss=1.561013，反而差约 0.000806；
- V05/V07/V08 被 inner OOF 全部收缩回 market；
- V09/V10 是真正的 gradient-boosted nonlinear interaction model，但 17/17 folds 都选择 `alpha=0`，严格回到 market。

这否定的是“用同一 reconstructed checkpoint 的 forecast level/revision 去修最终 settlement 概率”这一表达，不是否定天气交易。能制造大偏离的 V06 已经实际验证：没有更好的事件信息时，大偏离只会放大错误。

## 盈利目标的转向

下一主目标不是继续在 terminal probability 上堆模型，而是：

```text
真实 provider run first-seen
  → consensus / assigned forecast revision
  → 当时尚未完全更新的完整 ladder
  → 5 / 10 / 30 / 60 / 90m signed market repricing
  → direct ask→future bid 与 maker queue 分开验证
```

已经把 `directional_repricing_summary` 接入稳定 runner：它分别统计 consensus/assigned revision 与 immediate、5/10/30/60/90m ladder mean shift 的方向一致率、平均 directional shift 和每 1°F revision 的市场位移。该目标直接对应 LMVM 短持策略，不再拿最终结算 logloss 代替短期可交易 markout。

旧 LMVM archive 的 6,357 snapshots / 12,237 paired update events 只能作反例：旧 `model_prob` 明显坏于 market（logloss 2.7019 vs 1.4148）；D-1 late holdout 60m taker ask→future bid ROI=-13.74%，最高 innovation decile 假设 maker entry、taker exit 后仍约 -1.34%。因此不能复用旧 probability，也不能用“假设挂到 maker”挽救负 taker edge。

## 扩展历史重建

本轮本地重新完成：

- 4,086 snapshot jobs / 56 target dates / 48 cities；
- 17,750 forecast rows，五模型各 3,550；
- 2026-06-11..16 的 536 jobs 明确记 `known unavailable`，没有拿更旧 daily cache 或估计 run 代替；
- Gamma settlement 文件 3,036 个，另 84 个 event `not_found` 作为 coverage gap。

数量与此前 JRS 上的 expanded forecast artifact 一致。当前无法在本地重物化 571-state full-ladder denominator，原因不是 forecast 或 settlement 缺失，而是历史逐档 snapshot 只在 JRS，当前 canonical tmux permission host 的 read/write probe 失败；直接读返回 `Operation not permitted`。已有 expanded V04 结果仍有效：571 OOF states / 39 dates，market=1.460752、V04=1.459326、delta=-0.001426，95% CI [-0.004173,+0.001028]。

## 状态与动作

- V04：保留为 baseline，不 freeze、不 shadow；
- V05–V10：保留为 negative controls；
- terminal probability 路线：不再盲调参数，等待 clean exact-run forward 后再训练；
- repricing 路线：runner 已实现连续 signed markout；JRS 恢复后直接重跑当前 exact-run events；
- execution：只有 signed repricing 在 target-date OOF/forward 为正，才加入 direct ask、fee、slippage、depth；maker queue 单独 A/B。

测试：相关 10 tests 全部通过。没有修改 live strategy、city pool、sizing、execution policy 或订单。

## 市场基准的正确含义

同一 checkpoint 的 weather probability `p_t` 与 market distribution `q_t` 并不是互相拟合，而是分别对最终 winner `Y` 评分：

```text
weather logloss = -log p_t(Y)
market logloss  = -log q_t(Y)
delta           = weather logloss - market logloss
```

因此，市场当时定价错误并不会把 weather 模型判错；只要 weather 给最终赢家的概率更合理，它就会得到更低的 loss。V05–V10 失败的原因不是“偏离市场”，而是这些偏离在后来的真实 winner 上没有稳定改善。市场是同时间、同信息集的强基线，settlement 才是概率准确度的真值。

短持 repricing 是另一项任务，不能与 terminal probability 混算。该任务使用事件对齐的四个时点：

```text
first-seen 前最后一份完整 ladder = pre-event market state
first-seen 后第一份新鲜完整 ladder = entry / immediate response
+5/+10/+30/+60/+90m ladder          = future repricing markout
最终 settlement                     = terminal accuracy label
```

未来市场也不是真值，它只回答“其他参与者随后是否向 forecast revision 的方向调整”；是否真有盈利，还必须使用 entry ask、future bid、fee、slippage 和 depth 单独验收。

## 2026-08-06 首轮 repricing smoke

当前最后一份可读的 exact-run artifact 只有 2026-08-05 一个 target date、68 个 revision rows；其中 34 个是 existing-run bootstrap，34 个是 partial batch 完成，尚无 `forward_new_complete_run`。因此只作机制 smoke，不作 alpha 结论：

| horizon | scoreable events | revision 与 ladder shift 同向率 | mean signed rung shift |
|---|---:|---:|---:|
| immediate | 13 | 69.23% | +0.02004 |
| 5m | 16 | 0.00% | 0.00000 |
| 10m | 16 | 0.00% | 0.00000 |
| 30m | 14 | 21.43% | -0.00251 |
| 60m | 14 | 42.86% | -0.00594 |
| 90m | 14 | 57.14% | -0.00764 |

immediate 的正方向反应可能是真实首跳，也可能混有同批采集/partial-completion timing；5/10m 完全不动显示现有 book cadence 或 freshness 仍不足；30–90m 没有稳定延续。当前结果不支持交易，但支持继续积累真正的 new-run first-seen forward 后再判断。

2026-08-06 当前生产检查为 `CRITICAL`：canonical JRS permission host 的真实 read/write probe 失败，forecast-run collector 与 full-ladder feed stale，无法生成新的 clean forward event。此次研究没有恢复或改变任何生产进程；恢复 collector 属于生产行为变更，需按 deployment contract 取得显式确认后执行。
