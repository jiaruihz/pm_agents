# Metrics JSONL Format

`pmm_logs/metrics.jsonl` — JSON Lines 格式，一行一个 tick 快照。

## Schema

### 元信息

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | ISO 8601 UTC 时间戳 |
| `tick` | int | tick 序号（从 0 开始） |
| `execution_mode` | string | `live` / `paper` |
| `market_data_source` | string | `ws` / `rest` |

### 账户

| 字段 | 类型 | 说明 |
|------|------|------|
| `usdc_balance` | float | 当前可用 USDC |
| `pending_usdc_credit` | float | merge 在途资金（仅 live 可能 > 0） |
| `effective_usdc_for_sizing` | float | sizing 用的有效余额 = `balance + credit × ratio` |
| `positions` | dict | `{token_id: 持仓量}` |
| `net_inventory` | float | YES 仓位 - NO 仓位 |
| `equity` | float | mark-to-market 权益 |
| `pnl` | float | 相对初始 equity 的账面损益 |

### 行情快照

| 字段 | 类型 | 说明 |
|------|------|------|
| `mids` | dict | `{token_id: mid_price}`（按 tick 对齐后的值） |
| `spreads` | dict | `{token_id: best_ask - best_bid}` |

### 策略内部状态

| 字段 | 类型 | 说明 |
|------|------|------|
| `inventory_signals` | dict | `{token_id: signal}`，∈ [-1, 1] |
| `realized_volatility` | dict | `{token_id: rv}`，短窗口 return 标准差 |
| `required_spreads` | dict | 策略要求的最小可盈利 spread |
| `adaptive_spreads` | dict | 实际使用的 spread = `max(base, required)` |

### 报价

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_quotes` | dict | `{token_id: {bid, ask}}` — 理论报价（锚定前） |
| `final_quotes` | dict | `{token_id: {bid, ask}}` — 执行报价（锚定 + 量化后） |

### 防护 & 风控

| 字段 | 类型 | 说明 |
|------|------|------|
| `ofi_imbalances` | dict | `{token_id: ofi}`，∈ [-1, 1] |
| `alpha_reference_momentum` | float | 参考市场平均动量 |
| `side_blocks` | dict | `{BUY: [token_ids...], SELL: [token_ids...]}` |
| `circuit_breaker_triggered` | bool | 是否触发熔断 |
| `circuit_breaker_reasons` | list | 触发详情（偏离度、移动均值等） |

### 执行统计

| 字段 | 类型 | 说明 |
|------|------|------|
| `open_orders_count` | int | tick 开始时的挂单数 |
| `placed` | int | 本 tick 新下单数 |
| `canceled` | int | 本 tick 撤单数 |
| `errors` | int | 本 tick 执行异常数 |

### Merge & Paper

| 字段 | 类型 | 说明 |
|------|------|------|
| `merge_actions` | list | 本 tick 触发的 merge 动作 |
| `paper_recent_fills` | list | paper 撮合引擎本 tick 的成交记录 |
| `paper_bootstrap_actions` | list | paper 启动时的 split 引导动作 |

---

## 常见排查

| 现象 | 检查路径 |
|------|----------|
| 没有挂单 | `side_blocks` 是否有 token 被 block；`required_spreads` 是否 > 自然 spread |
| 挂单价偏离预期 | 对比 `target_quotes`（理论价）和 `final_quotes`（执行价），差异来自锚定逻辑 |
| 成交少 | 检查 `placed` 是否正常，`adaptive_spreads` 是否过宽 |
| PnL 下降 | `realized_volatility` 是否在升高；是否频繁被 taker 吃方向单 |
| merge 失败 | `merge_actions` 中的 `status` 和 `error` 字段 |

## 快速解析

```bash
# 打印关键列
python3 -c "
import json
with open('pmm_logs/metrics.jsonl') as f:
    for line in f:
        o = json.loads(line)
        print(o['tick'], f\"{o['equity']:.2f}\", o['placed'], o['canceled'], o['side_blocks'])
"
```
