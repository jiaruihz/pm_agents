---
name: weather-strategy-lineage
description: >
  追踪某一天 weather 策略的完整执行血缘：信号→计划→订单→成交→结算，逐笔展开复盘。
  触发词：单日、血缘、逐笔、为什么下了这单、信号到结算、当日复盘、某天下了什么单、当天执行了什么、复盘某日。
  禁止：在不读 contract 的情况下写一次性分析脚本；跳过异常订单段；重写 PnL 公式；
  用旧 settlements join 代替 fact_trades 的 fill 级结算/PnL。
---

# weather-strategy-lineage

逐笔追踪单日执行血缘，用于复盘和异常排查。

血缘分析需要两层数据：
- raw lineage：`signals → plans → orders → fills`，用于解释为什么下单、有没有下单、有没有成交。
- fill result：`fact_trades`，用于读取成交后的结算、PnL、估值和审计字段。

禁止在 lineage 报告里重写 BUY_YES / BUY_NO PnL 公式。凡是 fill 级 PnL、win、settlement、unsettled valuation，一律从 `fact_trades` 取。

---

## 执行 Checklist

### 第 0 步：读 contract

先读 `docs/WEATHER_ANALYSIS_CONTRACT.md`，重点确认：
- `fact_trades` 是 fill 绩效唯一取数源。
- 血缘 raw 表只用于解释链路，不用于自算 PnL。
- 时间规约：下单日 vs 目标日区分，双时区展示。

未读 contract 不得继续。

### 第 1 步：确认分析参数

| 参数 | 默认值 |
|---|---|
| target_date | 必须由用户指定，YYYY-MM-DD |
| strategy_id | all |
| city_pool | all |
| trade_class | all；若复盘 live 实绩，默认 `live_real` |

先复述目标：例如 `single_day_lineage_2026-05-23_live_real` = “复盘 2026-05-23 target_date 下 live_real 成交链路，逐笔解释信号、计划、订单、fill、settlement/PnL”。

### 第 2 步：同步与数据源

需要最新数据时：

```bash
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh
```

检查：

```bash
ls -la runtime/weather.db
```

### 第 3 步：数据完整性自检

用 `fact_trades` 检查成交结果层：

```sql
SELECT trade_class, COUNT(*) AS fills, SUM(settled) AS settled_fills
FROM fact_trades
WHERE target_date = :target_date
GROUP BY trade_class;

SELECT settlement_status, settlement_join_method, COUNT(*) AS fills
FROM fact_trades
WHERE target_date = :target_date
GROUP BY settlement_status, settlement_join_method;

SELECT COUNT(*) AS bad_settled_null_pnl
FROM fact_trades
WHERE target_date = :target_date
  AND settlement_status = 'settled'
  AND pnl_usd_at_fill IS NULL;
```

raw lineage 检查计划/订单覆盖：

```sql
SELECT
  COUNT(DISTINCT sig.signal_id) AS signals,
  COUNT(DISTINCT p.plan_id) AS plans,
  COUNT(DISTINCT o.execution_id) AS orders,
  COUNT(DISTINCT f.fill_id) AS fills
FROM signals sig
LEFT JOIN plans p ON p.signal_id = sig.signal_id
LEFT JOIN orders o ON o.plan_id = p.plan_id
LEFT JOIN fills f ON f.execution_id = o.execution_id
WHERE sig.target_date = :target_date;
```

### 第 4 步：查询全链路

以 raw 表拉链路，以 `fact_trades` 补 fill 结果：

```sql
SELECT
  sig.city,
  sig.city_pool,
  sig.signal_id,
  sig.snapshot_ts_utc,
  sig.forecast_source,
  sig.target_date,
  sig.bracket,
  sig.signal_side,
  sig.model_p_yes,
  sig.market_price,
  sig.edge,
  p.plan_id,
  p.order_side AS plan_side,
  p.notional,
  p.desired_shares,
  p.execution_policy,
  p.sizing_mode,
  p.status AS plan_status,
  p.skip_reason,
  o.execution_id,
  o.venue,
  o.order_side,
  o.entry_price AS plan_price,
  o.limit_price,
  o.status AS order_status,
  o.created_at_utc AS order_ts_utc,
  f.fill_id,
  f.status AS fill_status,
  f.filled_at_utc,
  ft.trade_class,
  ft.fill_price,
  ft.fill_qty,
  ft.cost_usd,
  ft.settlement_status,
  ft.settlement_join_method,
  ft.final_yes,
  ft.pnl_usd_at_fill,
  ft.pnl_usd_at_plan,
  ft.win_by_count,
  ft.bracket_hit,
  ft.val_mid,
  ft.val_bid,
  ft.val_last_fill,
  ft.unrealized_pnl_mid
FROM signals sig
LEFT JOIN plans p ON p.signal_id = sig.signal_id
LEFT JOIN orders o ON o.plan_id = p.plan_id
LEFT JOIN fills f ON f.execution_id = o.execution_id
LEFT JOIN fact_trades ft ON ft.fill_id = f.fill_id
WHERE sig.target_date = :target_date
  AND (:city_pool = 'all' OR sig.city_pool = :city_pool)
  AND (:strategy_id = 'all' OR p.config_id = :strategy_id OR ft.strategy_id = :strategy_id)
  AND (:trade_class = 'all' OR ft.trade_class = :trade_class OR ft.trade_class IS NULL)
ORDER BY sig.city, sig.snapshot_ts_utc, o.created_at_utc;
```

时间字段必须转换为北京时间 + 当地时间两列。

### 第 5 步：异常识别

必须标记：
- `plan_status` / `skip_reason`：有信号但被策略跳过。
- `order_status` 非正常提交/成交。
- `fill_id IS NULL`：有订单但未成交。
- `settlement_status != 'settled'`：未结算、missing_event、missing_bracket。
- `settlement_join_method = 'none'`：结算未命中。
- `ABS(fill_price - plan_price) / plan_price > 0.05`：成交价偏离计划价超过 5%。
- `pnl_usd_at_fill IS NULL AND settlement_status='settled'`：底表异常，必须暂停结论。

### 第 6 步：报告

模板：`docs/analysis/templates/lineage.md`

报告必须包括：
- 数据快照
- 数据完整性自检
- 目标日期/分母说明
- 按 city 的逐笔链路表
- 异常订单段；若无异常，写“无”
- 结论先给交易动作或排查动作

输出路径：

```text
docs/analysis/YYYY-MM/YYYY-MM-DD-lineage-{target_date}.md
```

只有用户要求提交时才 commit。
