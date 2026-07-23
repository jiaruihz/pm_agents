# Current-YES Core Carry v1 冻结与 pre-live 准备

Status: `superseded-for-now / zero-notional historical artifact`

## 冻结结论

- 固定策略：当地 13–17 点、market midpoint≥0.80、每小时第一个 minute≥30 且成功读到 fresh two-sided book 的 checkpoint；5-share 全 ask ladder 加官方 fee 后首次正 EV 入场，同 city-day 锁定。
- OOF 五档成本重放：144 笔 / 30 个目标日 / 29 城，137 胜 7 负，胜率 95.14%，均价（含 fee）0.9126，ROI +4.25%，date-block 95% CI [+0.30%,+7.74%]。
- 不加入 faded/support/clean-exhaustion hard gate；这些物理语义会继续记录，但历史同分母没有证明其增量。
- 不等待第二次确认，不设 0.95 ask 门槛，不做 maker；这些都不是 v1 的历史获利定义。
- v1 包含 obs_age_min；后续 train/serve 审计确认该维主要表达采集节奏，已由 no-age v2 supersede-for-now。

## 回测/live 时钟一致性

历史 OOF states 中 minute=:30 占 76.58%，minute 28–35 占 86.87%。runner 只负责发现新 snapshot，不允许按盘口轮询频率重复撞策略阈值。

## 上线状态

模型参数、特征顺序、imputation、5-share 成本和 checkpoint contract 已冻结。当前仅用于 zero-notional pre-live：记录全部正/负评分与 would-order，不包含签名、私钥或真实下单代码。would-order 会同时读取 H1/H2 submitted city-day 并标记 family conflict，避免未来 canary 重复暴露。真实下单不由本冻结报告自动授权。
