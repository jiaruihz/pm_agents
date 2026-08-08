# mid_price_core_v2 执行策略设计（历史 tombstone）

Status: `superseded`
Source of truth: no
Current authority: `WEATHER_EXECUTION_ARCHITECTURE.md`; production controller/manifest

`mid_price_core_v2` 是 2026-05/06 的旧执行策略设计，已停止作为 live 生产依据。原文使用 338-row
历史切片推导低价拆单、高价缩量和 maker/taker 参数；这些数字不满足当前 canonical denominator、fee、
frozen-forward 与 execution-profile 合同，不能恢复为默认配置或 live gate。

仍有效的工程结论已经吸收到统一执行架构：

- strategy 只选择 versioned `execution_profile` 和总风险预算；
- shared execution engine 负责 child split、quote、TTL、reprice/cancel 与 venue semantics；
- maker/taker 子腿属于同一经济意见，必须共享 plan/order/fill 血缘；
- 当前参数只从 production config、进程和 raw/exchange evidence 动态核对。

历史 `maker_queue_v1/v2`、`mid_price_core_v2` 名称可用于解释旧 journal，不表示当前策略可部署。
完整旧参数、样本表和 rollout 计划可从本文件的 git history 恢复。
