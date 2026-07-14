---
name: weather-strategy-exposure
description: >
  查看当前 weather 策略的未结算持仓和风险敞口（按市场/城市/到期日聚合，含 mid/bid/last_fill 三种未实现 PnL 估值）。
  触发词：持仓、敞口、未结算、未平仓、风险、当前仓位、还挂着哪些单、open position、仓位风险、
  余额少了、钱包余额、账户、USDC、cash、cashflow、资金少了、CLOB 对账。
  禁止：绕过 fact_trades 自己 join fills/orders/settlements；漏掉三估值并列；
  把未实现 PnL 混入历史 realized 绩效；把余额减少直接说成策略亏损。
---

# weather-strategy-exposure

时间点快照：查询所有有 fill 但尚未 settled 的 open positions。

默认唯一取数源：

```text
runtime/weather.db.fact_trades
```

`fact_trades` 已包含 open fill 的基础信息、settlement 状态、`val_mid` / `val_bid` / `val_last_fill` / `unrealized_pnl_mid`。不要绕回 raw 表手写结算 join 或 PnL 公式。

如果用户问的是余额、钱包、USDC、账户现金变化或 CLOB fill 对账，不要只跑本 skill 的 open exposure SQL；先转 `weather-live-account-reconcile`，使用固定脚本：

```bash
python3 scripts/analysis/account_reconcile/weather_live_account_reconcile.py --start YYYY-MM-DD --end YYYY-MM-DD --date-field fill_date_bj --group-by instance,selected_date
```

账户对账必须拆开 `submitted_notional_usd` / `posted_notional_usd` / `actual_fill_cost_usd` / `open_cost_usd` / `realized_pnl_usd`。`fact_trades.order_date_bj` 禁止用于钱包现金流结论；默认用 `fill_date_bj` 解释实际花钱日期，用 raw live order files 解释 submitted/posted notional。

---

## 执行 Checklist

### 第 0 步：读 contract

先读 `docs/WEATHER_ANALYSIS_CONTRACT.md`，确认：
- `fact_trades` 是 fill 绩效和持仓估值的默认取数源。
- 未结算估值必须和 realized PnL 分开。
- 未结算 PnL 至少并列展示 mid / bid / last_fill；缺值要写明原因。

### 第 1 步：确认参数

| 参数 | 默认值 |
|---|---|
| snapshot_time | now |
| city_pool | all |
| trade_class | live_real；若用户问 paper/shadow，再改为对应 trade_class |
| strategy_id | all |

先复述目标：例如 `current_live_real_open_exposure` = “查看 live_real 未结算 fill 的城市/市场/到期日敞口与三估值 unrealized PnL”。

### 第 2 步：数据源与新鲜度

持仓敞口要求数据新鲜。先检查现有 DB：

```bash
ls -la runtime/weather.db
```

目标窗口缺失时优先走增量刷新；只有增量流程不能满足且用户明确同意全量重建时，才调用
`weather-fact-rebuild`。若无法刷新，报告“数据快照”注明使用本地缓存、DB mtime、`MAX(fact_built_at_utc)`。

### 第 3 步：完整性自检

```sql
SELECT MAX(fact_built_at_utc) AS fact_built_at_utc FROM fact_trades;

SELECT trade_class, settlement_status, COUNT(*) AS fills
FROM fact_trades
GROUP BY trade_class, settlement_status
ORDER BY trade_class, settlement_status;

SELECT COUNT(*) AS open_fills
FROM fact_trades
WHERE settlement_status != 'settled'
  AND (:trade_class = 'all' OR trade_class = :trade_class);

SELECT
  SUM(CASE WHEN val_mid IS NULL THEN 1 ELSE 0 END) AS missing_mid,
  SUM(CASE WHEN val_bid IS NULL THEN 1 ELSE 0 END) AS missing_bid,
  SUM(CASE WHEN val_last_fill IS NULL THEN 1 ELSE 0 END) AS missing_last_fill
FROM fact_trades
WHERE settlement_status != 'settled'
  AND (:trade_class = 'all' OR trade_class = :trade_class);
```

如果 `val_mid` / `val_bid` 大面积为空，必须在报告里说明 snapshot valuation 尚未覆盖或快照不可用。

### 第 4 步：查询 open positions

```sql
SELECT
  trade_class,
  strategy_id,
  execution_policy,
  city,
  city_pool,
  target_date,
  bracket,
  side,
  forecast_source,
  model_version,
  fill_id,
  execution_id,
  fill_ts_utc,
  fill_price,
  fill_qty,
  cost_usd,
  settlement_status,
  settlement_join_method,
  val_mid,
  val_bid,
  val_last_fill,
  unrealized_pnl_mid,
  val_snapshot_ts_utc
FROM fact_trades
WHERE settlement_status != 'settled'
  AND (:trade_class = 'all' OR trade_class = :trade_class)
  AND (:city_pool = 'all' OR city_pool = :city_pool)
  AND (:strategy_id = 'all' OR strategy_id = :strategy_id)
ORDER BY target_date, city, side, bracket;
```

按市场/城市/到期日聚合：

```sql
SELECT
  city,
  city_pool,
  target_date,
  side,
  COUNT(*) AS open_fills,
  SUM(cost_usd) AS cost_usd,
  SUM(unrealized_pnl_mid) AS unrealized_pnl_mid,
  SUM(CASE WHEN val_bid IS NOT NULL THEN
    CASE side
      WHEN 'BUY_YES' THEN (val_bid - fill_price) * fill_qty
      WHEN 'BUY_NO' THEN ((1.0 - val_bid) - fill_price) * fill_qty
    END
  END) AS unrealized_pnl_bid
FROM fact_trades
WHERE settlement_status != 'settled'
  AND (:trade_class = 'all' OR trade_class = :trade_class)
GROUP BY city, city_pool, target_date, side
ORDER BY target_date, city, side;
```

只允许在估值层用 `val_mid` / `val_bid` 代入，且必须标注 `[UNSETTLED]`。不要把这些值加入 realized PnL。

### 第 5 步：报告

模板：`docs/analysis/templates/exposure.md`

报告必须包含：
- 数据快照：DB mtime、`fact_built_at_utc`、`val_snapshot_ts_utc`
- 完整性自检
- open fills 总览
- 按 city / target_date / side 的敞口表
- 三估值列并列：mid / bid / last_fill；缺值写 N/A 和原因
- 风险提示：最大城市敞口、最大单日敞口、settlement_join_method none、valuation 缺失

输出路径：

```text
docs/analysis/YYYY-MM/YYYY-MM-DD-exposure-{snapshot_ts}.md
```

只有用户要求提交时才 commit。
