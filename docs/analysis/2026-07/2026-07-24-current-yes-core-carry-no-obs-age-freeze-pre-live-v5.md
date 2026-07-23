# Current-YES Residual Carry v2（no obs-age）冻结与 pre-live 准备

Status: `frozen / zero-notional pre-live / no real-live activation`

## 冻结结论

- 固定策略：当地 13–17 点、market midpoint≥0.80、每小时第一个 minute≥30 且成功读到 fresh two-sided book 的 checkpoint；5-share 全 ask ladder 加官方 fee 后首次正 EV 入场，同 city-day 锁定。
- OOF 五档成本重放：136 笔 / 30 个目标日 / 29 城，130 胜 6 负，胜率 95.59%，均价（含 fee）0.9133，ROI +4.67%，date-block 95% CI [+0.91%,+8.02%]。
- 不加入 faded/support/clean-exhaustion hard gate；这些物理语义会继续记录，但历史同分母没有证明其增量。
- 不等待第二次确认，不设 0.95 ask 门槛，不做 maker；这些都不是 v2 的历史获利定义。
- `obs_age_min` 已从概率模型移除；source age 与 expected cadence 只作 PIT 数据有效性证据，不再让 collector cadence 改变 p_hold。

## 回测/live 时钟一致性

历史 OOF states 中 minute=:30 占 76.58%，minute 28–35 占 86.87%。runner 只负责发现新 snapshot，不允许按盘口轮询频率重复撞策略阈值。

## 上线状态

模型参数、特征顺序、imputation、5-share 成本和 checkpoint contract 已冻结。当前仅用于 zero-notional pre-live：记录全部正/负评分与 would-order，不包含签名、私钥或真实下单代码。would-order 会同时读取 H1/H2 submitted city-day 并标记 family conflict，避免未来 canary 重复暴露。真实下单不由本冻结报告自动授权。

## 与 v1 的同分母结论

- 概率层（v2−v1）：Brier Δ `-0.000080`，95% CI `[-0.000372, +0.000185]`；logloss Δ `+0.000198`，95% CI `[-0.001022, +0.001452]`。两项 CI 都跨 0，去掉 age 没有可辨别的概率质量损失。
- 5-share 全 ladder：v1 `144` 笔、胜率 `95.14%`、ROI `+4.25%`；v2 `136` 笔、胜率 `95.59%`、ROI `+4.67%`。
- city-day overlap：共同 `133`，仅 v1 `11`，仅 v2 `3`。变化集中在临界 EV，而不是策略主体翻转。
- 冻结决定：未来 pre-live 默认切到 `current_yes_core_carry_model_v2`；v1 标为 superseded-for-now 并保留历史血缘。v2 仍是 zero-notional pre-live，本报告不启动真实订单。
