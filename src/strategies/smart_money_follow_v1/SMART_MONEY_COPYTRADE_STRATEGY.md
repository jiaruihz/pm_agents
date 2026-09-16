# Smart Money Copytrade Strategy v1

## 1. 目标

在 Polymarket 上持续发现高质量账户（高胜率/高稳定性/可解释风格），并将其当前仓位信号转换为可执行跟单信号，用于 `smart_money_follow_v1`。

## 2. 策略总流程

1. 发现候选账户（Discover）
2. 账户评分（Score）
3. 风格画像（Profile）
4. LLM 解释（Explain）
5. 生成 token 级信号（Signal）
6. 执行与风控（Execution + Risk）

## 3. 找人方法（Discovery Plan）

### A. `single_market`（默认起步）

- 用一个目标市场（例如某个事件中的一个子市场）抓 `holders + trades`
- 优点：快、相关性高
- 缺点：样本较小，容易漏掉跨市场高手

### B. `multi_market`（推荐主模式）

- 以事件为中心，扫描 N 个子市场（建议 5-15）
- 候选池来自每个市场的 `holders + trades`
- 优点：覆盖更全面，稳定性更高
- 缺点：耗时上升

### C. `global_recent`（补充模式）

- 从全站近期交易中抓活跃账户，再反查其历史表现
- 优点：能发现新活跃资金
- 缺点：噪声高，必须配强过滤

## 4. 账户评分与过滤（v1）

- 基础门槛：
  - `resolved_trades >= min_resolved_trades`
  - `resolved_markets >= min_resolved_markets`
  - `win_rate >= min_win_rate`
  - `resolved_notional >= min_position_notional`
- 评分模式：
  - `pnl_proxy`（快速近似）
  - `resolved_trades`（更严谨但慢）
- 质量增强：
  - `min_behavior_trades` 避免低样本账号误判
  - 对极端低频、大额单边账户单独标签，不直接高权重

## 5. 账户风格画像（Heuristic + LLM）

### Heuristic 标签（已实现）

- `bot_like_market_maker`
- `concentrated_conviction_trader`
- `low_freq_whale`
- `event_rotator`
- `balanced_discretionary`

### LLM 解释（可选）

输入：账户统计 + 行为特征（交易频率、买卖比、市场集中度、单笔规模、胜率稳定性）  
输出：

- 风格一句话总结
- 证据点（2-4 条）
- 风险提示（例如“样本不足”“可能过拟合单事件”）

建议只对 Top K 账户启用 LLM，控制成本与延迟。

## 6. 信号构建

从 `wallets.json` 生成 `token_signals.json`：

- 账户权重 = 胜率置信度 * 样本规模 * 近期一致性
- token 方向 = 账户在该 token 的净 conviction 聚合
- 输出字段：
  - `signal`（-1 到 1）
  - `support_wallets`（支持该信号的钱包清单）
  - `confidence`

## 7. 执行与风控

- 每 token 最大敞口限制
- 单账户信号权重上限，防止“单人带飞”
- 仅在盘口 spread/深度满足阈值时下单
- 冷却期 + 反转确认，避免频繁追涨杀跌
- 每日回撤上限触发降档/停机

## 8. 推荐运行顺序

1. 先跑 `single_market` 验证口径
2. 升级到 `multi_market` 扩样本
3. 打开 LLM 对 Top 账户解释
4. 生成信号并接入 `smart_money_follow_v1` 回测
5. 通过纸面/小仓位实盘逐步放量

## 9. 关键命令（示例）

```bash
# 1) 发现账户（单市场）
python scripts/python/pmm_find_smart_wallets.py \
  --slug bytedance-ipo-before-2027 \
  --discovery-mode single_market \
  --max-candidate-wallets 40 \
  --top-wallets 12 \
  --out-file runtime/smart_money_wallets.json

# 2) 生成 token 信号
python scripts/python/pmm_smart_money_signal.py \
  --wallets-file runtime/smart_money_wallets.json \
  --out-file runtime/smart_money_signals.json
```

## 10. 当前状态

- 账户发现、评分、风格启发式、信号生成：已可用
- LLM 解释：依赖 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`
- 实盘下单：依赖本地 tool-service（`PM_API_BASE_URL`）
