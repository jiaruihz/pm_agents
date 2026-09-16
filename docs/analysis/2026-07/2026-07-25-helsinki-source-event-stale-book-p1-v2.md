# Helsinki/FMI Source-Event Stale-Book Alpha P1 v2

## Verdict

`rejected_for_current_expression`。Helsinki/FMI 的信息领先值得继续作为资料，但目前没有证据证明它在**source first-seen 后、盘口尚未重定价**这个可交易时间窗中打败 market；更不能据此扩 live。P0 的 5-share displayed-top replay 只是 4 笔 / 3 天的反事实，样本与 depth 都不够，不能视为 PnL 证据。

## PIT 口径

- universe：raw collector 的 Helsinki/FMI distinct first-seen cross episode；不是只保留便宜盘口、成交或赢家。
- label：canonical `settlement_outcomes` 中 old exact bracket 是否最终失效（BUY old-bracket NO 的 payout）。
- P0：collector 启动前 Helsinki/FMI first-cross `18/18`，随后按 target_date expanding、Beta(1,1) 平滑；同日绝不回灌。
- market baseline：同一 episode 的 NO mid `(bid+ask)/2`；若没有 bid 则不用该行做 proper score。EV/replay 仍使用可买到的 ask 和官方 fee `0.05*p*(1-p)`。

## 结果

- raw episodes `39` / `9` 天；有 canonical label `33`，有 t0 direct quote `16`，二者同存 `16` / `6` 天。
- P0 vs same-row market logloss delta（source − market）`0.13582098955433214`，date-block 95% CI `[-0.09677682140523569, 0.4790166714738371]`，Gate A `fail`。CI 不完全落在 0 以下，不能主张 FMI 概率优于盘口。
- +60 秒 NO-mid change `0.0047777777777777775`，CI `[0.0, 0.010750000000000001]`，Gate B `fail`；支持行只有 `9`，不足以证明稳定重定价。
- P0-EV 5-share displayed-top replay：`4` 笔、cost `$13.69564575`、PnL `$1.30435425`、ROI `0.09523860895715704`（date bootstrap `[-0.2924291082070122, 0.7472742521666199]`）。Gate C 固定为 FAIL：没有 20 个独立 event/date、没有实际 fill，且无 depth ladder。

## 为什么停止在这里

这不是加天气特征或价格 gate 能解决的问题。当前直接失败于：同分母 score 未赢 market、event-time repricing 样本不稳，以及 10-share VWAP 不可观测。`P0_EV` / `P0_EV_plus_1c` 的少数正 replay 都只可记录为 research opportunity，不能反推策略。

## Atlanta terminal-false 负面对照

同期 observer 有 `34` 个有效 Atlanta/MADIS episode，历史 eligibility label 能配 `26` 个，其中 `2` 个最终没有离开 old bracket；`0` 个 false event 在这个 observer 的 t0 window 有 ask。它不改变 Atlanta 2026-07-17 已记录的真实 false-cross fill 事故，反而说明**没有新一次 t0 ask 不能洗白 source basis 风险**。Atlanta 绝不与 Helsinki pool，也不作为复现实验城市。

## 交付与后续状态

- 不新增、修改或停止 runner；本研究全程 zero-notional。
- 5-share 仅为当时 displayed top size 足够时的 counterfactual；10-share 不能回放。
- 本方向保持 `dormant / collector_only`。只有在新的完整 source observation + full depth collector 下，累计至少 20 个独立 settled source events 并重跑全部 Gate A/B/C 后，才允许重新评估。
