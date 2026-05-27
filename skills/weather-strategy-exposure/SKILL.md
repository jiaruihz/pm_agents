---
name: weather-strategy-exposure
description: >
  查看当前 weather 策略的未结算持仓和风险敞口（按市场/城市/到期日聚合，含三种未实现 PnL 估值）。
  触发词：持仓、敞口、未结算、未平仓、风险、当前仓位、还挂着哪些单、open position、仓位风险。
  禁止：在不读 contract 的情况下写一次性分析脚本；漏掉三估值并列；把未实现 PnL 混入历史绩效。
---

# weather-strategy-exposure

时间点快照：查询所有 open positions（有 fill 但尚无 settled 结算记录的持仓）。

---

## 执行 Checklist（必须按顺序完成，不得跳步）

### 第 0 步：强制前置——读 contract（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，重点确认：
- §0 通用规约（禁止清单 + 报告头必填项）
- §2.1 未结算 PnL 三种估值定义（mid / bid / last_fill）
- §1 数据源清单（mid/bid 需要最新 snapshot）

**未读 contract 不得继续任何分析动作。**

---

### 第 1 步：确认分析参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| snapshot_time | 快照时间（北京时间） | now |
| city_pool | t1_trading / t2_research / all | t1_trading |

---

### 第 1.5 步：强制数据同步（不得跳过）

持仓敞口要求数据最新，必须先同步：

```bash
# 从 N100 同步最新 paper ledger / snapshot CSV
scripts/ops/sync_weather_remote.sh

# 重建 weather.db（ingest CSV → DB）
scripts/weather_dashboard/run_stack.sh --no-rebuild
```

> 若 N100 不可达，在报告"数据快照"段注明，并标注本地缓存数据时间。

---

### 第 2 步：数据源（按 contract §0 优先级）

```bash
ls -la runtime/weather.db
```

---

### 第 3 步：查询 open positions

```sql
SELECT
  sig.city,
  sig.city_pool,
  sig.target_date,
  o.order_side,
  f.filled_price,
  f.filled_shares                            AS fill_qty,
  f.filled_price * f.filled_shares           AS cost_usd,
  sig.condition_id,
  sig.market_id,
  f.fill_id,
  f.filled_at_utc
FROM fills f
JOIN orders  o   ON o.execution_id = f.execution_id
JOIN plans   p   ON p.plan_id      = o.plan_id
JOIN signals sig ON sig.signal_id  = p.signal_id
WHERE f.status IN ('filled', 'partial', 'simulated')
  AND sig.city_pool = '{city_pool}'
  AND NOT EXISTS (
    SELECT 1 FROM settlements s
    WHERE s.target_date = sig.target_date
      AND (s.condition_id = sig.condition_id OR s.market_id = sig.market_id)
      AND s.settlement_status = 'settled'
  )
ORDER BY sig.target_date, sig.city;
```

---

### 第 4 步：未实现 PnL 三估值

对每条 open position 按三种口径估值（contract §2.1）：

**last_fill**（直接从 fills 取，无需额外数据）：
```python
# BUY_YES: unrealized = (last_fill_price - entry_price) * fill_qty
# BUY_NO:  unrealized = (entry_price - last_fill_price) * fill_qty
# last_fill_price = filled_price（用入场价本身估值，偏保守）
```

**mid / bid**（需要最新 snapshot）：
```python
import pandas as pd, glob, os

# 取最新 snapshot CSV
snapshots = glob.glob("runtime/weather_edge_v1/market_data/paper_snapshots/*.csv")
if snapshots:
    latest = max(snapshots, key=os.path.getmtime)
    df = pd.read_csv(latest)
    # join by condition_id，取 mid_price, best_bid 列
    # 若列名不同，以实际文件列名为准
else:
    print("无 snapshot 文件，mid/bid 估值填 N/A")
```

若盘口数据不可用：mid/bid 列填 `N/A（无 snapshot）`，在报告"数据快照"段说明。

---

### 第 5 步：填写报告模板

模板：`docs/analysis/templates/exposure.md`

**三估值列必须并列，全部标注 `[UNSETTLED]`，不得合并为一列，不得混入已结算 PnL。**

输出路径：`docs/analysis/YYYY-MM/YYYY-MM-DD-exposure-{snapshot_ts}.md`

---

### 第 6 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: exposure snapshot {snapshot_ts}"
```
