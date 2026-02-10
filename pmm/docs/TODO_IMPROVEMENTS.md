# PMM Improvements TODO

这个清单用于跟踪“已实现/待实现”的工程项。完成后请把 `[ ]` 改成 `[x]`。

## Data & Execution

- [x] Market 公共 WebSocket（`/ws/market`）接入 + 本地 orderbook 缓存
- [x] WebSocket 应用层心跳（`PING/PONG`）
- [x] 缺失/过期盘口回退 REST
- [x] 执行层可切换：`live` / `paper`
- [x] Paper Trading：真实行情驱动 + 本地撮合引擎
- [ ] User 私有 WebSocket（订单/成交/余额推送）
- [ ] 私有流驱动的本地订单状态机（替代 `GET /orders` 轮询）

## Simulation Fidelity

- [x] 两种撮合模型：`conservative` / `optimistic`
- [x] 资金冻结与释放（BUY 冻结 USDC、SELL 冻结仓位）
- [ ] 引入 queue position 模型（基于排队深度的成交概率）
- [ ] 引入部分成交时间分布模型（非固定 queue_share）
- [ ] 按市场最小跳动价 `tick_size` 做撮合和报价对齐

## Risk & Strategy

- [x] Circuit breaker（偏离阈值触发全撤）
- [x] OFI + 参考市场动量防守
- [x] 动态 spread（费用/波动/库存风险）
- [x] 可切换 fair price：`weighted mid` / `midpoint`
- [x] merge 在途资金临时 credit（TTL + ratio）
- [ ] Realized PnL 与费用拆分（maker/taker/链上成本）
- [ ] 自动 split / redeem（目前只有 merge）

## Operability

- [x] 统一指标日志（`metrics.jsonl`）
- [ ] Prometheus 指标暴露
- [ ] 回放模式（用历史盘口离线复盘）
