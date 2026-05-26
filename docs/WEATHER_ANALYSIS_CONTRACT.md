# Weather Analysis Contract

> 任何天气策略分析（绩效 / 血缘 / 持仓敞口）必须遵守本文件中的所有定义。  
> 口径改动必须先 PR 进本文件，然后才能在分析报告或 skill 中使用新口径。

---

## §0 通用规约

### 数据源优先级（硬规定）

1. `weather.db`（`runtime/weather_edge_v1/weather.db`）— 首选
2. Dashboard API（`http://localhost:8000`）— DB 不可用时
3. 镜像 JSON/CSV（`runtime/weather_edge_v1/market_data/research/`）— API 不可用时
4. N100 raw（`jiarui@192.168.0.200:~/projects/weather-predict/output/`）— 最后手段

**每降一级必须在报告"数据快照"段写明原因。**

### 禁止清单

- 不准在 `/tmp` 或未 git 的目录写一次性 pandas 脚本，不存档不记录
- 不准自己定义新指标或新切片维度（必须用 §2/§5 里的定义）
- 不准把 strategy_id 之外的字段当作策略唯一标识
- 不准只输出数字结论而不写 Markdown 报告
- 不准跳过报告的"数据完整性自检"段

### 报告头必填项

每份分析报告的第一个 H2 段（`## 数据快照`）必须包含：

| 字段 | 说明 |
|---|---|
| 数据源 | DB / API / 镜像 CSV / N100 raw（注明路径） |
| 数据快照时间 | DB last_modified 或 JSON `generated_at` 字段 |
| 记录行数 | 查询返回的 orders / fills 行数 |
| unsettled 占比 | unsettled_n / total_n |
| missing_bracket 数 | 结算状态为 `missing_bracket` 的笔数 |

---

## §1 数据源清单

### weather.db

- 路径：`runtime/weather_edge_v1/weather.db`
- 刷新方式：`scripts/weather_dashboard/run_stack.sh`（重跑 ingest）
- 覆盖时间：取决于镜像同步时间，详见 `WEATHER_DATA_PIPELINE.md`
- 关键表：`signals` / `plans` / `orders` / `fills` / `settlements` / `runs` / `strategy_config`

关键 join 路径（signal → settlement）：

```
signals
  → plans        ON plans.signal_id = signals.signal_id
  → orders       ON orders.plan_id  = plans.plan_id
  → fills        ON fills.execution_id = orders.execution_id
  → settlements  ON settlements.target_date = signals.target_date
                AND (settlements.condition_id = signals.condition_id
                 OR  settlements.market_id    = signals.market_id)
```

### Dashboard API

- Base URL：`http://localhost:8000`
- 启动：`scripts/weather_dashboard/run_stack.sh --api-only`
- 常用 endpoints：
  - `GET /api/runs` — 所有 run 列表
  - `GET /api/runs/{run_id}/summary` — 单 run 绩效摘要
  - `GET /api/compare?run_ids=A,B` — 多 run 对比
  - `GET /api/live/signals` — 最新信号
  - `GET /api/live/orders` — 最新订单

### 镜像产物（`runtime/weather_edge_v1/market_data/research/`）

| 文件 | 用途 |
|---|---|
| `t24_paper_ledger_summary.json` | 全量 paper ledger 绩效摘要；顶层键：`overall / by_date / by_city / by_pool / by_side / by_model / by_pool_side / by_pool_model / unsettled` |
| `t24_paper_ledger_trades.csv` | 逐笔 ledger，含 city、city_pool、order_side、fill_price、plan_price、fill_qty、pnl_usd、settled、target_date |
| `t24_paper_snapshot_replay_summary.json` | snapshot replay 绩效摘要（与 ledger 同结构） |
| `t24_paper_snapshot_replay_trades.csv` | snapshot replay 逐笔 |
| `t24_snapshot_replay_equity_curve.csv` | 资金曲线（date, cumulative_pnl） |

`overall` 对象的字段：`n / wins / win_rate / cost_usd / pnl_usd / roi`

### N100 raw（降级使用）

- SSH：`ssh jiarui@192.168.0.200`（WSL 内执行，密钥 `~/.ssh/id_ed25519_weather_deploy`）
- 关键路径：`~/projects/weather-predict/output/paper_trades/paper_orders.jsonl`
- 同步命令：`scripts/ops/sync_weather_remote.sh`

---

## §2 核心指标定义

> 参考实现：N100 `scripts/analysis/settle_t24_paper.py`（产出 `t24_paper_ledger_summary.json`）

### 2.1 PnL

**已结算 PnL（settled PnL）**

```sql
-- BUY_YES profit = (final_yes_price - fill_price) × fill_qty - fees
-- BUY_NO  profit = (fill_price - final_yes_price) × fill_qty - fees
-- 原理：NO token 买入成本 = fill_price（以 YES 价格计），结算时 NO 获得 (1 - final_yes_price)
--       等价于持有 YES 时 final_yes_price 收益，但方向相反，因此
--       BUY_NO profit = fill_price - final_yes_price（与 BUY_YES 符号相反）

SELECT
  f.fill_id,
  sig.city,
  sig.city_pool,
  o.order_side,
  o.entry_price          AS plan_price,    -- 计划入场价（entry_price 在 orders 表）
  f.filled_price         AS fill_price,    -- 实际成交价
  f.filled_shares        AS fill_qty,
  f.fees_usd,
  s.final_price          AS settlement_yes_price,
  s.settlement_status,
  CASE o.order_side
    WHEN 'BUY_YES' THEN (s.final_price - f.filled_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (f.filled_price - s.final_price) * f.filled_shares - f.fees_usd
  END AS pnl_usd_at_fill,
  CASE o.order_side
    WHEN 'BUY_YES' THEN (s.final_price - o.entry_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (o.entry_price - s.final_price) * f.filled_shares - f.fees_usd
  END AS pnl_usd_at_plan
FROM fills f
JOIN orders  o   ON o.execution_id = f.execution_id
JOIN plans   p   ON p.plan_id      = o.plan_id
JOIN signals sig ON sig.signal_id  = p.signal_id
LEFT JOIN settlements s
  ON s.target_date = sig.target_date
 AND (s.condition_id = sig.condition_id OR s.market_id = sig.market_id)
WHERE s.settlement_status = 'settled'
```

**未结算 PnL（unsettled PnL）**

报告里必须单独列出，标注 `[UNSETTLED]`，三种估值并列：

| 估值 | 说明 | 来源 |
|---|---|---|
| mid | 当前盘口 mid price | 最新 snapshot CSV 或 API |
| bid | 当前买一价 | 最新 snapshot CSV 或 API |
| last_fill | 最近一笔成交价 | `fills.filled_price` |

未结算估值**不计入"已结算 PnL"总览**，仅供参考。

**报告必须同时列**：`pnl_usd_at_fill`（实际成交价）和 `pnl_usd_at_plan`（计划价），以及 `fill_qty`。

### 2.2 Win Rate

```sql
-- 在已结算集合上计算，按 pnl_usd_at_fill > 0 判断胜负

-- 按订单数
SELECT
  COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0)  AS wins_by_count,
  COUNT(*)                                      AS total_count,
  ROUND(
    1.0 * COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0) / COUNT(*), 4
  ) AS win_rate_by_count

-- 按 notional（fill_price × fill_qty）
SELECT
  SUM(f.filled_price * f.filled_shares) FILTER (WHERE pnl_usd_at_fill > 0)
    AS win_notional,
  SUM(f.filled_price * f.filled_shares)
    AS total_notional,
  ROUND(
    SUM(f.filled_price * f.filled_shares) FILTER (WHERE pnl_usd_at_fill > 0)
    / SUM(f.filled_price * f.filled_shares), 4
  ) AS win_rate_by_notional
```

报告**必须同时列两套**（by_count 和 by_notional）。

**含未结算版本**：未结算订单按 mid 估值 > 0 视为 tentative win，标注 `[含未结算]`。

### 2.3 ROI / Sharpe-like

```
ROI           = total_pnl_usd / total_cost_usd
total_cost_usd = SUM(filled_price × filled_shares)

avg_daily_pnl  = SUM(daily_pnl) / n_trading_days
std_daily_pnl  = STDDEV(daily_pnl)
sharpe_like    = avg_daily_pnl / std_daily_pnl   -- 未年化，仅供参考
```

---

## §3 时间规约

### 双时区

报告里涉及时间的列**必须同时显示**：
- **北京时间** (`Asia/Shanghai`)：所有 UTC 时间戳转北京时间展示
- **当地时间**：按城市 timezone 表（见下）

### 城市 timezone 表

| City | IANA timezone |
|---|---|
| Amsterdam | Europe/Amsterdam |
| Ankara | Europe/Istanbul |
| Atlanta | America/New_York |
| Austin | America/Chicago |
| Beijing | Asia/Shanghai |
| BuenosAires | America/Argentina/Buenos_Aires |
| Busan | Asia/Seoul |
| CapeTown | Africa/Johannesburg |
| Chengdu | Asia/Shanghai |
| Chicago | America/Chicago |
| Chongqing | Asia/Shanghai |
| Dallas | America/Chicago |
| Denver | America/Denver |
| Guangzhou | Asia/Shanghai |
| Helsinki | Europe/Helsinki |
| HongKong | Asia/Hong_Kong |
| Houston | America/Chicago |
| Istanbul | Europe/Istanbul |
| Jakarta | Asia/Jakarta |
| Jeddah | Asia/Riyadh |
| Karachi | Asia/Karachi |
| KualaLumpur | Asia/Kuala_Lumpur |
| LA | America/Los_Angeles |
| Lagos | Africa/Lagos |
| London | Europe/London |
| Lucknow | Asia/Kolkata |
| Madrid | Europe/Madrid |
| Manila | Asia/Manila |
| MexicoCity | America/Mexico_City |
| Miami | America/New_York |
| Milan | Europe/Rome |
| Moscow | Europe/Moscow |
| Munich | Europe/Berlin |
| NYC | America/New_York |
| PanamaCity | America/Panama |
| Paris | Europe/Paris |
| SanFrancisco | America/Los_Angeles |
| SaoPaulo | America/Sao_Paulo |
| Seattle | America/Los_Angeles |
| Seoul | Asia/Seoul |
| Shanghai | Asia/Shanghai |
| Shenzhen | Asia/Shanghai |
| Singapore | Asia/Singapore |
| Taipei | Asia/Taipei |
| TelAviv | Asia/Jerusalem |
| Tokyo | Asia/Tokyo |
| Warsaw | Europe/Warsaw |
| Wellington | Pacific/Auckland |
| Wuhan | Asia/Shanghai |

### 跨日订单归属

**以 `orders.created_at_utc`（下单时间戳）所在北京时间日期为准。**  
结算日期在 `signals.target_date`；两者可能不同——报告中两列都列出。
