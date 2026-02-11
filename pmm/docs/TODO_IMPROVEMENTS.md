# PMM Roadmap

已实现和待实现的工程项清单。

## Alpha / Edge Sources

- [x] OFI（订单流不平衡，盘口深度差异）
- [x] 参考市场动量（关联 token 短窗 momentum）
- [ ] 外部信息源（News / Twitter / 公告 → topic-market 映射）
- [ ] 多市场 lead-lag（同事件不同 market 之间的价格领先）
- [ ] 公允价融合器（weighted_mid + last_trade + reference + LLM prior）
- [ ] 事件时间特征（距结算时间 / 公告窗口）

## Data & Execution

- [x] Market WS 接入 + 本地 L2 book 维护
- [x] WS 心跳（`PING/PONG`）+ 过期回退 REST
- [x] 执行层切换（live / paper）
- [x] Paper Trading：真实行情 + 本地撮合
- [ ] User 私有 WS（订单 / 成交 / 余额推送）
- [ ] 私有流驱动的本地订单状态机（替代 `GET /orders` 轮询）
- [ ] API 限流与退避（全局令牌桶 + endpoint 级 budget）
- [ ] 订单幂等（request-id / 重复提交保护）
- [ ] 批量撤改单优化（减少 cancel storm）
- [ ] 从 market metadata 拉取真实 tick_size / min_size

## Simulation

- [x] 两种撮合模型（conservative / optimistic）
- [x] 资金冻结与释放
- [x] 价格 tick 对齐
- [ ] 队列位置模型（排队深度 → 成交概率）
- [ ] 部分成交时间分布模型
- [ ] 深度穿透撮合（不只看 top-of-book）
- [ ] Adverse fill（抢跑 / 插队模型）
- [ ] Fee 模型（maker / taker / 链上成本）
- [ ] Merge / split 在途状态建模

## Risk & Strategy

- [x] 熔断器（偏离阈值 → cancel_all）
- [x] OFI + 动量防守
- [x] 动态 spread（费用 / 波动 / 库存）
- [x] Weighted mid / midpoint 切换
- [x] Merge 在途 credit（TTL + ratio）
- [ ] **多档报价**（N 档梯度 size / price）
- [ ] Realized PnL + 费用拆分
- [ ] 自动 split / redeem
- [ ] 方向性 exposure 风控 / VaR
- [ ] 动态 join（join_epsilon 随波动调整）
- [ ] Self-trade / crossing 防护
- [ ] 事件级风控（临近结算 → 降风险）
- [ ] 熔断恢复策略（cooldown + 分阶段恢复）

## Operability

- [x] 统一指标日志（metrics.jsonl）
- [ ] 结构化 logging（替代 print）
- [ ] Prometheus 指标暴露
- [ ] 策略参数热更新
- [ ] 多市场并行运行（独立状态 + 全局资金调度）
- [ ] Crash-safe checkpoint（状态落盘 + 恢复）
- [ ] 运行告警（PnL 急变 / 错误率 / 断线 / 仓位超限）
