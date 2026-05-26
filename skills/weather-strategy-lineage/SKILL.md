---
name: weather-strategy-lineage
description: >
  追踪某一天 weather 策略的完整执行血缘：信号→计划→订单→成交→结算，逐笔展开复盘。
  触发词：单日、血缘、逐笔、为什么下了这单、信号到结算、当日复盘、某天下了什么单、当天执行了什么、复盘某日。
  禁止：在不读 contract 的情况下写一次性分析脚本；跳过异常订单段；不写数据完整性自检。
---

# weather-strategy-lineage

逐笔追踪单日执行血缘，用于复盘和异常排查。

---

## 执行 Checklist（必须按顺序完成，不得跳步）

### 第 0 步：强制前置——读 contract（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，重点确认：
- §0 通用规约（禁止清单 + 报告头必填项）
- §1 DB join 路径（signals → plans → orders → fills → settlements）
- §3 时间规约（下单日 vs 目标日区分，双时区展示）

**未读 contract 不得继续任何分析动作。**

---

### 第 1 步：确认分析参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| target_date | 目标日期（YYYY-MM-DD，北京时间） | 必须由用户指定 |
| strategy_id | 策略 ID，或 "all" | "all" |
| city_pool | t1_trading / t2_research / all | t1_trading |

---

### 第 2 步：数据源（按 contract §0 优先级）

```bash
ls -la runtime/weather_edge_v1/weather.db
```

若 DB 不可用，按 contract §0 降级并在报告"数据快照"段说明原因。

---

### 第 3 步：查询全链路数据

```sql
SELECT
  sig.city,
  sig.city_pool,
  sig.signal_id,
  sig.snapshot_ts_utc,
  sig.forecast_source,
  sig.target_date,
  p.plan_id,
  p.order_side,
  o.entry_price          AS plan_price,
  p.notional,
  p.desired_shares,
  o.execution_id,
  o.venue,
  o.limit_price,
  o.created_at_utc,
  f.fill_id,
  f.filled_price,
  f.filled_shares        AS fill_qty,
  f.status               AS fill_status,
  f.filled_at_utc,
  s.final_price          AS settlement_yes_price,
  s.settlement_status,
  s.bracket,
  CASE o.order_side
    WHEN 'BUY_YES' THEN (s.final_price - f.filled_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (f.filled_price - s.final_price) * f.filled_shares - f.fees_usd
  END AS pnl_usd_at_fill,
  CASE o.order_side
    WHEN 'BUY_YES' THEN (s.final_price - o.entry_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (o.entry_price - s.final_price) * f.filled_shares - f.fees_usd
  END AS pnl_usd_at_plan
FROM signals sig
JOIN plans   p   ON p.signal_id    = sig.signal_id
JOIN orders  o   ON o.plan_id      = p.plan_id
LEFT JOIN fills  f ON f.execution_id = o.execution_id
LEFT JOIN settlements s
  ON  s.target_date  = sig.target_date
 AND (s.condition_id = sig.condition_id OR s.market_id = sig.market_id)
WHERE sig.target_date = '{target_date}'
  AND sig.city_pool   = '{city_pool}'
ORDER BY sig.city, o.created_at_utc;
```

时间字段（`snapshot_ts_utc` / `created_at_utc` / `filled_at_utc`）需转换为北京时间 + 当地时间两列（见 contract §3 城市 timezone 表）。

---

### 第 4 步：异常识别

标记以下异常：
- `settlement_status = 'missing_bracket'` 或 `'missing_event'`
- `ABS(fill_price - plan_price) / plan_price > 0.05`（超 5% 偏差）
- `fill_id IS NULL`（下单但未成交）

---

### 第 5 步：填写报告模板

模板：`docs/analysis/templates/lineage.md`

按 city 分组填写全链路表，alphabetical 排序。  
**异常订单段不得留空**——若无异常，写"无"。

输出路径：`docs/analysis/YYYY-MM/YYYY-MM-DD-lineage-{target_date}.md`

---

### 第 6 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: lineage report {target_date}"
```
