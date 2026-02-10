# PMM Strategy Playbook (Human-Readable)

## 目标

个人做市策略的核心是三件事：

- 稳定挂单，不被市场噪音带着跑。
- 控制库存风险，不把仓位堆到单边。
- 在异常行情下快速切到防守模式。

## 策略框架

1. 公允价值与基础价差

- 默认用 weighted mid（微观价格近似）作为短期公允价值。
- 若盘口尺寸不可用，回退到普通中点（`midpoint`）。
- 在 `mid` 两侧挂出基础价差（`base_spread`）。

2. 库存驱动报价偏移（Non-Linear Skew）

- 不再用线性库存函数，改用 sigmoid 非线性曲线。
- 仓位越接近上限，倾斜力度越大，推动策略主动去库存。

3. 盘口锚定，但不盲目追单

- 通过 `join_epsilon` 让报价靠近买一/卖一，提高成交概率。
- 同时用 `min_edge` 保持最小边际，不允许报价无限追着盘口走。
- 含义：执行层尊重理论价，不会为了排队把预期收益压到 0。

4. AS（Adverse Selection）保护

- OFI 防守：若近端盘口显示明显买盘失衡，撤卖单；明显卖盘失衡，撤买单。
- 参考市场动量：监控关联市场（`PMM_ALPHA_REFERENCE_TOKEN_IDS`），若动量超阈值，单侧防守，不做对手盘。（后续再实现）
- 目标：降低“被消息优势资金点杀”的概率。

5. 动态 Spread + 入场阈值

- 不用固定 spread，按风险动态调整：
  - `required_spread = fee_floor + target_profit + vol_component + inventory_component`
- 若自然盘口 spread 太薄（低于可盈利阈值），直接不入场并撤掉相关订单。

6. Diffing + Deadband

- 不做“每轮全撤全挂”。
- 只有当价格/数量变化超过阈值才改单，降低手续费、限流与排队损失。

7. Circuit Breaker 防守模式

- 监控当前价格相对短窗均价的偏离。
- 偏离超阈值（如 10%）时：
  - 立即 `cancel_all`
  - 记录事件
  - 按配置停止做市或短暂停机

8. 延迟优先（Latency First）

- 盘口数据优先用 WebSocket 推送，不依赖每 tick REST 轮询。
- 本地维护 Orderbook 缓存，缺失/过期再回退 REST。
- 连接 market 频道时使用 `detail_level`（默认 `agg`）。
- 发送应用层心跳 `"PING"` 并接收 `"PONG"`，降低被服务端踢断概率。
- 目标是减少“看到过期价格再挂单”导致的被动吃亏。

9. 资金利用率（Capital Efficiency）

- 若同时持有 YES/NO，可周期性执行 `merge` 释放为 USDC。
- 避免资金长期沉淀在对冲仓位中，提升可用保证金与挂单能力。
- live 模式可启用 merge 在途资金临时 credit，缓解链上确认延迟导致的停挂单。

10. 模拟盘（Paper Trading）

- 数据层仍使用真实盘口（WS/REST）。
- 执行层切到本地撮合引擎，不向交易所发单。
- 支持两种成交假设：
  - `conservative`：只有盘口“穿透你的限价”才算成交。
  - `optimistic`：盘口触碰你的限价即成交。
- `queue_share` 控制每个 tick 的可成交上限，近似排队劣后。

## 默认风险立场

- 行情平稳时：偏被动做市，靠双边价差吃流。
- 行情突变时：先活下来，先撤单，再考虑恢复。
- 库存过重时：优先去库存，接受一定成交劣后。

## 建议起步参数（示例）

- `PMM_BASE_SPREAD=0.04`
- `PMM_SKEW_FACTOR=0.05`
- `PMM_INVENTORY_SIGMOID_K=4.0`
- `PMM_MID_PRICE_MODE=weighted`
- `PMM_JOIN_EPSILON=0.001`
- `PMM_MIN_EDGE=0.002`
- `PMM_MIN_PROFITABILITY_SPREAD=0.03`
- `PMM_FEE_SPREAD_FLOOR=0.002`
- `PMM_TARGET_PROFIT_SPREAD=0.002`
- `PMM_VOLATILITY_SPREAD_COEFF=2.0`
- `PMM_INVENTORY_RISK_SPREAD_COEFF=0.01`
- `PMM_ALPHA_ENABLED=1`
- `PMM_ALPHA_REFERENCE_TOKEN_IDS=...`
- `PMM_ALPHA_REF_MOMENTUM_THRESHOLD=0.03`
- `PMM_ALPHA_OFI_ENABLED=1`
- `PMM_ALPHA_OFI_DELTA=0.01`
- `PMM_ALPHA_OFI_IMBALANCE_THRESHOLD=0.60`
- `PMM_DEADBAND=0.01`
- `PMM_CB_ENABLED=1`
- `PMM_CB_WINDOW_SEC=60`
- `PMM_CB_THRESHOLD=0.10`
- `PMM_CB_MIN_POINTS=5`
- `PMM_CB_HALT=1`
- `PMM_MARKET_DATA_SOURCE=ws`（或 `rest`）
- `PMM_EXECUTION_MODE=paper`（或 `live`）
- `PMM_PAPER_INITIAL_USDC=1000`
- `PMM_PAPER_INITIAL_POSITIONS_JSON='{\"<yes_token>\":100,\"<no_token>\":50}'`
- `PMM_PAPER_FILL_MODEL=conservative`（或 `optimistic`）
- `PMM_PAPER_FILL_EPSILON=0.001`
- `PMM_PAPER_QUEUE_SHARE=0.25`
- `PMM_WS_MARKET_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market`
- `PMM_WS_DETAIL_LEVEL=agg`（或 `l2`）
- `PMM_WS_APP_PING_INTERVAL_SEC=10`
- `PMM_WS_STALE_AFTER_SEC=3`
- `PMM_AUTO_MERGE_ENABLED=1`
- `PMM_AUTO_MERGE_EVERY_TICKS=30`
- `PMM_MERGE_PLANS_JSON='[{...}]'`
- `PMM_MERGE_PENDING_CREDIT_ENABLED=1`
- `PMM_MERGE_PENDING_CREDIT_TTL_SEC=20`
- `PMM_MERGE_PENDING_CREDIT_RATIO=1.0`
- `PMM_MERGE_AMOUNT_SCALE=1000000`

## 指标怎么看

- `pnl/equity`：看账户总表现（当前为账面）。
- `net_inventory`：看仓位是否长期偏单边。
- `placed/canceled/open_orders_count`：看挂撤是否过于频繁。
- `required_spreads/adaptive_spreads`：看当前策略要求的利润空间与实际报价宽度。
- `ofi_imbalances`：看是否经常出现单边吃单风险。
- `side_blocks`：看被风控拦截的方向和频率。
- `merge_actions`：看自动 merge 是否成功执行，是否释放了流动性。
- `circuit_breaker_triggered`：看风控是否频繁触发。
