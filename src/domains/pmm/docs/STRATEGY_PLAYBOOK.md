# 策略手册

做市策略的设计理念、参数逻辑和运行指南。

## 定位

在 Polymarket 小盘二元市场上做双边流动性提供。核心约束：盘口薄、信息不对称风险高、资金有限。策略围绕三条原则设计：

1. **不追价** — 队列优先级比毫厘的 edge 更值钱
2. **先活下来** — 异常行情下先撤单，再考虑恢复
3. **仓位中性** — 不赌方向，靠价差吃流

---

## 公允价值

| 模式 | 公式 | 适用场景 |
|------|------|----------|
| `weighted` (默认) | `(bid × ask_size + ask × bid_size) / (bid_size + ask_size)` | 薄盘口下更稳健的微价格估计 |
| `midpoint` | `(best_bid + best_ask) / 2` | 深盘口或调试用 |

当 orderbook 完全为空时，fallback 到 `GET /market/{token_id}` 的 `outcome_prices`。

## 报价生成

```
理论报价:
  bid = mid - spread/2 - skew
  ask = mid + spread/2 - skew
  skew = skew_factor × inventory_signal
```

- **spread** 不是固定值，而是取 `max(base_spread, required_spread)`
- **required_spread** = `fee_floor` + `target_profit` + `vol_coeff × realized_vol` + `inv_coeff × |inventory_signal|`
- 含义：只有当市场能覆盖全部成本加上目标利润时才入场

## 盘口锚定

理论报价 → 执行报价的映射：

1. 用 `join_epsilon` 贴近 best bid/ask（提高成交率）
2. 用 `min_edge` 保证与 fair value 的最小距离（防止追到 0 edge）
3. 按 `price_tick` 对齐到价格网格（消除浮点噪声、减少无意义改单）

**设计意图**：执行层尊重理论价，不会为了拿到更好的队列位置把预期利润压到零。

## 库存管理

使用 Sigmoid 非线性倾斜：

```
raw = net_position / max_position     # ∈ [-1, 1]
signal = 2 / (1 + exp(-k × raw)) - 1  # 非线性映射
```

- 仓位居中时 skew 很小，正常双边做市
- 仓位接近上限时 skew 急剧加大，主动用价格推动去库存
- `k` 越大曲线越陡，边际的 skew 变化越剧烈

## 逆向选择防护

| 信号 | 触发条件 | 动作 |
|------|----------|------|
| OFI（订单流不平衡） | 近端 bid depth 显著 > ask depth | 撤卖单 |
| OFI 反向 | ask depth 显著 > bid depth | 撤买单 |
| 参考市场动量 | 关联市场短窗动量超阈值 | 撤对手方向 |
| 盘口过薄 | 自然 spread < min_profitability | 双边全撤 |

撤单不等于停止观察。下一个 tick 如果信号消退，正常恢复挂单。

## 熔断器

监控 `mid` 对近 N tick 移动均价的偏离度。偏离超过阈值（默认 10%）：
- 立即 `cancel_all`
- 记录触发原因
- 可选停止做市（`halt = true`）或 cooldown 后恢复

## 资金回收

同时持有 YES 和 NO 本质是对冲锁仓，不产生 PnL。定期执行 `merge`：

```
min(yes_pos, no_pos) units → USDC collateral
```

live 模式下链上确认有延迟，此时启用 pending credit 让策略在确认前就可以用这笔资金继续挂单（TTL + ratio 可配）。

## 订单管理

**不做每轮全撤全挂**。`OrderManager` 用 diff + deadband：
- 对比现有挂单和目标价 / 量
- 价格变动未超过 deadband → 保留现有挂单（保持队列位置）
- 超过 deadband → 撤旧单、挂新单

---

## 推荐参数

以下参数适用于小盘（日成交量 < $50k）的二元事件市场：

| 参数 | 值 | 理由 |
|------|----|------|
| `base_spread` | `0.04 ~ 0.08` | 小盘 spread 天然较宽，不需要激进竞价 |
| `base_size` | `3 ~ 5` | 减小单次被吃穿的敞口 |
| `skew_factor` | `0.05` | 适中的库存倾斜力度 |
| `inventory_sigmoid_k` | `4.0` | 仓位 60%+ 开始明显加速去库存 |
| `max_position` | `100` | 单市场最大仓位 |
| `deadband` | `0.01` | 1 分钱以内的波动不改单 |
| `join_epsilon` | `0.001` | 贴 1 mill 以内的 BBO |
| `min_edge` | `0.002` | 至少保持 0.2% 的 fair value edge |
| `price_tick` | `0.001` | 报价对齐到 1 mill |
| `min_profitability_spread` | `0.03` | 自然 spread < 3% 就不入场 |
| `alpha_ofi_imbalance_threshold` | `0.60` | 小盘 OFI 容易不均衡，阈值不宜太低 |

---

## 监控指标

日常运行关注这几个数：

| 指标 | 健康范围 | 异常信号 |
|------|----------|----------|
| `pnl` | 稳步上升或持平 | 持续下滑 → 检查费用 / 逆向选择 |
| `net_inventory` | 绝对值 < max_position × 40% | 持续偏单边 → skew 参数可能不够 |
| `placed / canceled` 比值 | < 3:1 | 频繁改单 → deadband 太小 |
| `side_blocks` | 偶发 | 频繁 block → OFI 阈值或 spread 阈值需调整 |
| `circuit_breaker_triggered` | false | 频繁触发 → 该市场可能不适合做市 |
| `merge_actions` | 定期成功 | 连续失败 → 检查仓位 / 链上 gas |
