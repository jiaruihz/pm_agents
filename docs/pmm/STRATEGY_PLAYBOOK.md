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

## Weather Edge v1（`weather_edge_v1`）

这个策略已经不是纯 theta/carry。当前主线是天气概率 edge：

- `weather-predict` 生成 T-24 bracket 概率和 paper decision
- `pm_agent` 导入 signal，去重后生成 trade plan
- paper/live 共用同一份 trade plan
- live 默认关闭，必须经过本地 `SafetyGuard` 和显式确认
- 旧的 NO-side carry 逻辑保留为 PMM variant 能力，但不再代表完整策略定义

核心参数（`PMM_STRATEGY_PARAMS_JSON`）：

- `weather_no_token_ids`: 仅交易这些 token（可选）
- `weather_entry_min_price`, `weather_entry_max_price`: 入场价格安全区间
- `weather_position_pct`: 单次建仓预算占可用 USDC 比例（默认 `0.05`）
- `weather_take_profit_abs`, `weather_stop_loss_abs`: 绝对价差止盈/止损阈值
- `weather_min_forecast_edge`, `weather_exit_forecast_edge`: 日最高温预测和目标桶位的最小安全偏离
- `weather_token_forecast_map`: token -> `{lat, lon, target_date, unit, bucket_value/bucket_min/bucket_max}`
- `weather_max_hold_hours`: 最大持有时长（默认 `48`）
- `weather_exit_before_hours`: 结算前强制离场窗口（需配合 `weather_token_end_ts`）
- `weather_reentry_cooldown_hours`: 平仓后冷却时长

示例：

```bash
export PMM_STRATEGY_KEY="weather_edge_v1"
export PMM_STRATEGY_PARAMS_JSON='{
  "weather_no_token_ids": ["NO_TOKEN_A", "NO_TOKEN_B"],
  "weather_entry_min_price": 0.80,
  "weather_entry_max_price": 0.97,
  "weather_position_pct": 0.05,
  "weather_take_profit_abs": 0.02,
  "weather_stop_loss_abs": 0.03,
  "weather_min_forecast_edge": 2.0,
  "weather_exit_forecast_edge": 1.0,
  "weather_max_hold_hours": 48,
  "weather_exit_before_hours": 6,
  "weather_reentry_cooldown_hours": 12,
  "weather_token_forecast_map": {
    "NO_TOKEN_A": {"lat": 51.5072, "lon": -0.1276, "target_date": "2026-03-09", "unit": "C", "bucket_value": 12}
  }
}'
```

## 聪明钱跟随（`smart_money_follow_v1`）

这个策略在 `single_level_v1` 的基础上，加入“方向性倾斜”：

- 信号来源：`PMM_STRATEGY_PARAMS_JSON`
  - `smart_money_token_signals` / `token_signals`: `{"TOKEN_ID": -1~1}`
  - `smart_money_wallets`: 钱包胜率 + 当前 conviction（策略内会过滤高胜率钱包）
  - `smart_money_signal_file`: 可选 JSON 文件路径，按 `smart_money_signal_reload_sec` 热加载（用于监听外部进程）
- 价格倾斜：正信号上移 bid/ask，负信号下移 bid/ask
- 仓位倾斜：正信号放大 BUY、缩小 SELL；负信号反之
- 极强信号：`abs(signal) >= smart_money_one_side_only_threshold` 时可单边挂单

### 找账户方法（建议固定流程）

1. `single_market`（按目标市场找人，优先）
   - 从该 market 的 `holders + trades` 找候选
   - 适合“我要做某个具体市场跟单”
2. `multi_market`（跨市场找人，补充）
   - 扫多个高成交/高流动市场，找稳定活跃钱包
   - 适合构建长期候选池
3. `global_recent`（兜底）
   - 从全站近期成交抓钱包，速度快但噪声大

钱包评估分两层：

- `score_mode=pnl_proxy`：基于 positions 的 pnl 代理评分（快，适合实时刷新）
- `score_mode=resolved_trades`：基于已结算市场成交推断胜率（更严谨，但更慢）

风格解释分两层：

- 启发式标签（默认可用）：`bot_like_market_maker` / `low_freq_whale` / `concentrated_conviction_trader` ...
- LLM 解释（可选）：当 `.env` 配置了 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` 时启用

示例：

```bash
# 1) 先发现候选高胜率账户（输出 wallets 快照）
python scripts/python/pmm_find_smart_wallets.py \
  --slug "your-market-slug" \
  --discovery-mode single_market \
  --score-mode pnl_proxy \
  --max-candidate-wallets 20 \
  --min-win-rate 0.5 \
  --top-wallets 10 \
  --out-file runtime/smart_money_wallets.json

# 2) 把 wallets 快照转换成 token_signals（可 watch）
python scripts/python/pmm_smart_money_signal.py \
  --wallets-file runtime/smart_money_wallets.json \
  --out-file runtime/smart_money_signals.json \
  --watch \
  --interval-sec 15

# 3) 策略读取 signals 文件并执行
export PMM_STRATEGY_KEY="smart_money_follow_v1"
export PMM_STRATEGY_PARAMS_JSON='{
  "smart_money_signal_file": "runtime/smart_money_signals.json",
  "smart_money_signal_reload_sec": 5,
  "smart_money_price_tilt_factor": 0.35,
  "smart_money_size_tilt_factor": 0.75,
  "smart_money_one_side_only_threshold": 0.9
}'
```

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
