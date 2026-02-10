# PMM Improvements TODO

这个清单用于跟踪“已实现/待实现”的工程项。完成后请把 `[ ]` 改成 `[x]`。

## Alpha (Edge) Sources

- [ ] 外部信息源接入与摘要（News/RSS/Twitter/官方公告，做 topic->market 映射）
- [ ] 多市场联动 Alpha（同事件不同 market / 同类事件相关市场的 lead-lag）
- [ ] 订单流信号增强（trade prints / taker flow / imbalance 的时间衰减版 OFI）
- [ ] 公允价融合器（microprice + last trade + reference markets + LLM/outside priors）
- [ ] 事件时间特征（接近到期/结算、公告窗口、开盘/收盘效应）

## Data & Execution

- [x] Market 公共 WebSocket（`/ws/market`）接入 + 本地 orderbook 缓存
- [x] WebSocket 应用层心跳（`PING/PONG`）
- [x] 缺失/过期盘口回退 REST
- [x] 执行层可切换：`live` / `paper`
- [x] Paper Trading：真实行情驱动 + 本地撮合引擎
- [ ] User 私有 WebSocket（订单/成交/余额推送）
- [ ] 私有流驱动的本地订单状态机（替代 `GET /orders` 轮询）
- [ ] API 限流与退避（全局令牌桶 + endpoint 级别 budget，避免被 ban）
- [ ] 订单幂等与重试策略（create/cancel 的 request-id、重复提交保护）
- [ ] 撤单/改单的批量化与最小化（减少 cancel storm，降低排队损失）
- [ ] 真实 tick_size / min_size / max_size 从 market metadata 拉取并强校验
- [ ] 执行回执与状态一致性校验（open orders vs local state 的 reconcile）

## Simulation Fidelity

- [x] 两种撮合模型：`conservative` / `optimistic`
- [x] 资金冻结与释放（BUY 冻结 USDC、SELL 冻结仓位）
- [ ] 引入 queue position 模型（基于排队深度的成交概率）
- [ ] 引入部分成交时间分布模型（非固定 queue_share）
- [x] 按市场最小跳动价 `tick_size` 做报价对齐（`PMM_PRICE_TICK`）
- [ ] Paper 模式的撮合应考虑盘口深度（不是只看 top-of-book 的 size=1）
- [ ] Paper 模式支持 “订单被抢跑/被插队” 的 adverse fill 模型（更保守）
- [ ] 统一 paper/live 的 fee 模型（maker/taker fee、链上成本摊销）
- [ ] 资金在途建模：merge/split 的 pending state（不仅是 credit，还要冻结/确认）

## Risk & Strategy

- [x] Circuit breaker（偏离阈值触发全撤）
- [x] OFI + 参考市场动量防守
- [x] 动态 spread（费用/波动/库存风险）
- [x] 可切换 fair price：`weighted mid` / `midpoint`
- [x] merge 在途资金临时 credit（TTL + ratio）
- [ ] Realized PnL 与费用拆分（maker/taker/链上成本）
- [ ] 自动 split / redeem（目前只有 merge）
- [ ] 头寸/库存风控（按方向 exposure、VaR/压力测试、单市场资金上限）
- [ ] 多档报价（1 档->N 档，梯度 size/price，降低被一口吃穿）
- [ ] 动态 join 策略（join_epsilon/edge 随波动与 OFI 调整）
- [ ] Self-trade 与 crossing 防护（避免自己吃自己、避免 bid>=ask）
- [ ] 事件级风控（临近结算/争议市场/规则模糊市场自动降风险或退出）
- [ ] “只撤不挂”后的恢复策略（cooldown、分阶段恢复挂单）

## Operability

- [x] 统一指标日志（`metrics.jsonl`）
- [ ] Prometheus 指标暴露
- [ ] 回放模式（用历史盘口离线复盘）
- [ ] 策略参数热更新（不重启进程，支持按市场维度覆盖）
- [ ] 多市场运行与隔离（每 market 独立状态机 + 全局资金调度器）
- [ ] Crash-safe 状态落盘（open orders/positions/params 的 checkpoint）
- [ ] 运行告警（成交异常、错误率、断线、PnL 急变、仓位超限）
