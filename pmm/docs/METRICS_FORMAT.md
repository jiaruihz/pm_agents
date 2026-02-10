# PMM Metrics JSONL Format

`pmm_logs/metrics_run_demo.jsonl`（以及 `pmm_logs/metrics.jsonl`）是 **JSON Lines** 格式：

- 一行 = 一个 tick 的快照
- 每行都是一个独立 JSON 对象
- 适合用 `tail` 查看，也适合用脚本/日志系统按行流式处理

## Top-Level Schema

每行的顶层字段如下（字段可能随版本增加，但不会随意改语义）：

- 兼容性说明：历史日志（例如较早生成的 `metrics_run_demo.jsonl`）可能缺少新字段；解析时建议用 `dict.get()`。
- `ts`：ISO 时间戳（UTC）
- `tick`：第几轮 tick（从 0 开始）
- `execution_mode`：执行模式
  - `live`：真实账户 + 真实下单
  - `paper`：本地虚拟账户 + 本地撮合（不真实下单）
- `market_data_source`：盘口来源
  - `ws`：WebSocket 本地缓存优先
  - `rest`：每 tick 走 HTTP 拉取

### Account / PnL

- `usdc_balance`：当前可用 USDC（取自执行层）
- `pending_usdc_credit`：merge 在途资金 credit（仅 `live` 模式可能 > 0）
- `effective_usdc_for_sizing`：用于 sizing 的“有效可用 USDC”
  - `effective_usdc_for_sizing = usdc_balance + pending_usdc_credit * ratio`
- `positions`：仓位字典，key 是 `token_id`，value 是数量
- `net_inventory`：净库存信号（YES - NO 的简化净敞口）
- `equity`：账户权益（mark-to-market）
- `pnl`：相对 `baseline_equity` 的账面 PnL（非 realized）

### Market Snapshot

以下字段都是字典结构：key 是 `token_id`。

- `mids`：当前 mid（由 orderbook 计算得到，可能是 weighted/midpoint）
  - 为了避免浮点显示噪声（例如 `0.419999999999`），日志里会按 `PMM_PRICE_TICK` 对齐到价格网格（不影响内部计算精度）。
- `spreads`：`best_ask - best_bid`

### Strategy Internals

- `inventory_signals`：库存信号（[-1, 1]）
- `realized_volatility`：短窗波动率（基于 mid 序列的 returns）
- `required_spreads`：策略“要求的最小可盈利 spread”
- `adaptive_spreads`：实际使用的 spread（`max(base_spread, required_spread)`）

### Quotes

- `target_quotes`：理论报价（策略计算出来的 bid/ask）
  - 结构：`{ token_id: { "bid": float, "ask": float } }`
- `final_quotes`：执行报价（在理论价基础上做盘口锚定 + 公允边界保护后得到）
  - 结构同上

### Alpha / Defense

- `ofi_imbalances`：OFI 指标（订单流不平衡，[-1, 1]）
- `alpha_reference_momentum`：参考市场动量（标量）
- `side_blocks`：被拦截的方向
  - 结构：`{"BUY": [token_id...], "SELL": [token_id...]}`
  - 一旦 token_id 出现在某个 side 里，表示该 tick 对应 side 只撤不挂（防守）

### Execution Stats

- `open_orders_count`：tick 开始时的 open orders 数量
- `placed`：本 tick 新创建订单数量（paper/live 都会计数）
- `canceled`：本 tick 撤单数量
- `errors`：本 tick 执行/请求异常次数

### Merge / Circuit Breaker

- `merge_actions`：本 tick 触发的 merge 动作记录（数组）
- `circuit_breaker_triggered`：是否触发 breaker
- `circuit_breaker_reasons`：触发原因列表（数组）

### Paper Only

- `paper_recent_fills`：paper 撮合引擎在该 tick 刚刚产生的成交列表
- `paper_bootstrap_actions`：paper 启动时的“引导动作”列表（通常用于模拟 `split()`，确保一开始就有 YES/NO 库存可用于挂 SELL）
  - 仅当配置了 `PMM_PAPER_BOOTSTRAP_SPLIT_USDC>0` 且 `PMM_EXECUTION_MODE=paper` 时会出现 `status=ok`
  - 结构示例：
    - `{"status":"ok","mode":"paper","split_usdc":100.0,"yes_token_id":"...","no_token_id":"..."}`

## How To Read It (Examples)

1. 为什么没挂单？
- 看 `side_blocks`：如果 BUY/SELL 都 block 了，说明策略主动不入场。
- 看 `spreads` 和 `required_spreads`：若 `natural_spread < min_profitability`，会进入“只撤不挂”。

2. 报价到底是多少？
- 看 `final_quotes[token_id]`，这就是策略最终想挂的价。

3. 当前是否有毒（风险变大）？
- `realized_volatility` 上升通常会推高 `required_spreads`。
- circuit breaker 触发会在下一轮把 `circuit_breaker_triggered=true`。

4. 资金是否被 merge 卡住？
- 看 `pending_usdc_credit` 和 `effective_usdc_for_sizing`。

## Quick Parsing

```bash
python3 - <<'PY'
import json
path='pmm_logs/metrics_run_demo.jsonl'
with open(path) as f:
    for i,line in enumerate(f):
        o=json.loads(line)
        print(i, o['tick'], o['equity'], o['placed'], o['canceled'], o['side_blocks'])
PY
```
