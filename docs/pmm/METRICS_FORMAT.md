# 指标日志（metrics.jsonl）格式说明

`src/strategies/pmm/backtest/.artifacts/logs/metrics.jsonl` 使用 JSON Lines 格式：每一行对应一个 tick 快照。

## 字段结构

### 1) 元信息

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | ISO 8601 UTC 时间戳 |
| `tick` | int | tick 序号（从 0 开始） |
| `execution_mode` | string | `live` / `paper` |
| `market_data_source` | string | `ws` / `rest` |

### 2) 账户维度

| 字段 | 类型 | 说明 |
|------|------|------|
| `usdc_balance` | float | 当前可用 USDC |
| `usdc_total` | float | 当前总 USDC（`free + reserved`；回测里用于计算 equity） |
| `pending_usdc_credit` | float | merge 在途资金（通常仅 live 可能 > 0） |
| `effective_usdc_for_sizing` | float | sizing 使用的有效余额 = `balance + credit × ratio` |
| `positions` | dict | `{token_id: 持仓量}` |
| `net_inventory` | float | YES 仓位 - NO 仓位 |
| `equity` | float | 按 mid 估算的权益 |
| `pnl` | float | 相对初始 equity 的账面损益 |

### 3) 行情快照

| 字段 | 类型 | 说明 |
|------|------|------|
| `mids` | dict | `{token_id: mid_price}`（tick 对齐后） |
| `spreads` | dict | `{token_id: best_ask - best_bid}` |

### 4) 策略内部状态

| 字段 | 类型 | 说明 |
|------|------|------|
| `inventory_signals` | dict | `{token_id: signal}`，范围约为 `[-1, 1]` |
| `realized_volatility` | dict | `{token_id: rv}`，短窗收益率标准差 |
| `required_spreads` | dict | 策略要求的最小可盈利 spread |
| `adaptive_spreads` | dict | 实际使用的 spread（通常为 `max(base, required)`） |

### 5) 报价结果

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_quotes` | dict | `{token_id: {bid, ask}}`，理论报价（锚定前） |
| `final_quotes` | dict | `{token_id: {bid, ask}}`，执行报价（锚定+量化后） |

### 6) 防护与风控

| 字段 | 类型 | 说明 |
|------|------|------|
| `ofi_imbalances` | dict | `{token_id: ofi}`，范围约为 `[-1, 1]` |
| `alpha_reference_momentum` | float | 参考市场平均动量 |
| `side_blocks` | dict | `{BUY: [token_ids...], SELL: [token_ids...]}` |
| `circuit_breaker_triggered` | bool | 是否触发熔断 |
| `circuit_breaker_reasons` | list | 熔断细节（偏离度、移动均值等） |

### 7) 执行统计

| 字段 | 类型 | 说明 |
|------|------|------|
| `open_orders_count` | int | tick 开始时挂单数 |
| `placed` | int | 本 tick 新下单数 |
| `canceled` | int | 本 tick 撤单数 |
| `errors` | int | 本 tick 执行异常数 |

### 8) Merge 与 Paper 专属

| 字段 | 类型 | 说明 |
|------|------|------|
| `merge_actions` | list | 本 tick 触发的 merge 动作记录 |
| `paper_recent_fills` | list | paper 撮合引擎本 tick 的成交记录 |
| `paper_bootstrap_actions` | list | paper 启动时 split 引导动作 |

---

## 常见排查

| 现象 | 排查建议 |
|------|----------|
| 没有挂单 | 看 `side_blocks`；看 `required_spreads` 是否高于自然盘口 |
| 报价偏离预期 | 对比 `target_quotes` 与 `final_quotes`，差异通常来自锚定/量化 |
| 成交少 | 看 `placed`、`adaptive_spreads` 是否过宽 |
| PnL 下滑 | 看 `realized_volatility` 是否升高；是否持续被单边吃单 |
| merge 失败 | 查看 `merge_actions` 里的 `status` / `error` |

## 快速解析示例

```bash
python3 -c "
import json
with open('src/strategies/pmm/backtest/.artifacts/logs/metrics.jsonl') as f:
    for line in f:
        o = json.loads(line)
        print(o['tick'], f\"{o['equity']:.2f}\", o['placed'], o['canceled'], o['side_blocks'])
"
```
