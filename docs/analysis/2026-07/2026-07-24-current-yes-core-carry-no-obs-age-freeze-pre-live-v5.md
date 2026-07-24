# Current-YES Residual Carry v2（no obs-age）冻结与 pre-live 准备

Status: `frozen / user-authorized tiny-live on Mac`

## 冻结结论

- 固定策略：当地 13–17 点、market midpoint≥0.80、每小时第一个 minute≥30 且成功读到 fresh two-sided book 的 checkpoint；5-share 全 ask ladder 加官方 fee 后首次正 EV 入场，同 city-day 锁定。
- OOF 五档成本重放：136 笔 / 30 个目标日 / 29 城，130 胜 6 负，胜率 95.59%，均价（含 fee）0.9133，ROI +4.67%，date-block 95% CI [+0.91%,+8.02%]。
- 不加入 faded/support/clean-exhaustion hard gate；这些物理语义会继续记录，但历史同分母没有证明其增量。
- 不等待第二次确认，不设 0.95 ask 门槛，不做 maker；这些都不是 v2 的历史获利定义。
- `obs_age_min` 已从概率模型移除；source age 与 expected cadence 只作 PIT 数据有效性证据，不再让 collector cadence 改变 p_hold。

## 回测/live 时钟一致性

历史 OOF states 中 minute=:30 占 76.58%，minute 28–35 占 86.87%。runner 只负责发现新 snapshot，不允许按盘口轮询频率重复撞策略阈值。

## 上线状态

模型参数、特征顺序、imputation、5-share 成本和 checkpoint contract 已冻结。2026-07-24 用户明确授权 tiny-live 后，Mac 生产启用独立实例 `current_yes_core_carry_tiny_live_v2`，旧 H1/H2 live 与 v1 pre-live 已停止。

- signal：保持当地 13–17 点、每小时首个 `minute>=30` checkpoint、首次正 EV 锁 city-day；
- sizing：每个信号 `5 taker + 5 maker`，每天最多 10 city-days / `$100` posted cost；
- taker：真正提交前重新读取完整 5-share ask ladder，并重验官方 fee 后 EV；
- maker：`best bid + 1 tick`，每 15 秒只向上追，cap 为首次 mid（向下取 tick）与模型概率的较低者；不穿 ask、不转 taker、不设重挂次数上限，15 分钟 TTL 或新 observation epoch 时撤单；
- family：沿用 H1/H2 submitted city-day 去重，避免换代时重复暴露；
- 证据边界：历史 ROI 仍只属于 5-share taker 定义，maker 是 execution probe，不能并入历史 alpha。

生产 checkout：`/Users/deepsleep/projects/pm_agents_prod`，部署分支
`codex/tokyo-jma-hot-wake-20260721`。启动后首轮读取
`snapshot_20260724_1507.json`，artifact hash
`9e3a8bc3daeabebee8a91a6d56fe70fae66035580a62e8040f947446809db665`，
`live_enabled=true`，当轮无符合信号，0 order / 0 fill。runtime monitor 已切换到新实例并健康。

## 与 v1 的同分母结论

- 概率层（v2−v1）：Brier Δ `-0.000080`，95% CI `[-0.000372, +0.000185]`；logloss Δ `+0.000198`，95% CI `[-0.001022, +0.001452]`。两项 CI 都跨 0，去掉 age 没有可辨别的概率质量损失。
- 5-share 全 ladder：v1 `144` 笔、胜率 `95.14%`、ROI `+4.25%`；v2 `136` 笔、胜率 `95.59%`、ROI `+4.67%`。
- city-day overlap：共同 `133`，仅 v1 `11`，仅 v2 `3`。变化集中在临界 EV，而不是策略主体翻转。
- 冻结决定：未来 pre-live 默认切到 `current_yes_core_carry_model_v2`；v1 标为 superseded-for-now 并保留历史血缘。v2 仍是 zero-notional pre-live，本报告不启动真实订单。
