# Weather Analysis Contract

> 任何天气策略分析（绩效 / 血缘 / 持仓敞口）必须遵守本文件中的所有定义。  
> 口径改动必须先 PR 进本文件，然后才能在分析报告或 skill 中使用新口径。

---

## §0 通用规约

### 分析前强制数据同步（硬规定）

**每次触发分析前必须先执行以下两条命令**（skill checklist 第 1.5 步）：

```bash
# Step 1: 从 N100 拉取最新 paper ledger、snapshot CSV、pm_history
scripts/ops/sync_weather_remote.sh

# Step 2: 重建 weather.db（ingest 最新 CSV → DB）
scripts/weather_dashboard/run_stack.sh --no-rebuild
```

> 若 N100 不可达（SSH 超时 / 网络中断），在报告"数据快照"段注明，并写明本地缓存的最后同步时间。

### 数据源优先级（硬规定）

同步完成后按以下优先级使用数据：

1. `weather.db`（`runtime/weather.db`）— 首选
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

### fact_trades（强制唯一取数源）

**绩效分析必须读 `fact_trades`，禁止绕过直接查 fills/orders/signals 等原始表自算指标。**

- DB 表：`runtime/weather.db` 的 `fact_trades` 表（73 列，每 fill 一行）
- Parquet：`runtime/weather_edge_v1/market_data/research/fact_trades.parquet`（与 DB 同步）
- 重建：`run_stack.sh` 在 ingest 后自动调用 `scripts/analysis/build_weather_fact_trades.py`
- 设计文档：[WEATHER_FACT_TRADES_DESIGN.md](WEATHER_FACT_TRADES_DESIGN.md)

**DB 路径只许 `runtime/weather.db`**（其他路径皆废，已删）。

#### 取数 quickstart

```python
import sqlite3
conn = sqlite3.connect("runtime/weather.db")
conn.row_factory = sqlite3.Row

# --- 最常用：取所有已结算成交，直接用预算好的 PnL ---
rows = conn.execute("""
    SELECT
        trade_class,       -- live_real / live_simulated / paper / snapshot_replay
        city, city_pool,
        side, bracket,
        target_date, order_date_bj,
        model_version,     -- ecmwf / gfs
        fill_price, plan_price, fill_qty, fees_usd,
        cost_usd,
        final_yes,         -- 结算价（0.0 或 1.0）
        pnl_usd_at_fill,   -- 唯一授权 PnL，基于 fill_price 算
        pnl_usd_at_plan,   -- 以 plan_price 为基准（衡量滑点影响）
        edge, abs_edge,
        strategy_id, run_id,
        settlement_status  -- settled / missing_bracket / unsettled
    FROM fact_trades
    WHERE settlement_status = 'settled'
""").fetchall()

# --- 按城市/方向切片 ---
rows = conn.execute("""
    SELECT city, side,
           COUNT(*) AS n,
           SUM(pnl_usd_at_fill) AS total_pnl,
           AVG(CASE WHEN pnl_usd_at_fill > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
    FROM fact_trades
    WHERE settlement_status = 'settled'
      AND city_pool = 't1_trading'
    GROUP BY city, side
    ORDER BY total_pnl DESC
""").fetchall()

# --- 只看 live 实盘（区分 paper） ---
rows = conn.execute("""
    SELECT * FROM fact_trades
    WHERE trade_class = 'live_real'
      AND settlement_status = 'settled'
""").fetchall()
```

常用 `trade_class` 枚举值：

| 值 | 含义 |
|---|---|
| `live_real` | execution_mode=live 且 fill_status=filled（真实成交） |
| `live_simulated` | execution_mode=live 且 fill_status=simulated |
| `paper` | paper 模拟下单 |
| `snapshot_replay` | snapshot 快照 replay |

#### 已迁移的参考实现（可以直接抄）

| 脚本 | 说明 |
|---|---|
| `scripts/analysis/weather_live_full_research.py` | 按城市/方向/模型全量切片，`_load_trades()` 展示了典型的 fact_trades 读法 |
| `scripts/analysis/weather_city_day_portfolio.py` | `load_live_fills()` 展示了只取 live_real + settled 的过滤方式 |
| `weather_dashboard/metrics/calc.py` | `compute_metrics()` 展示了 run 级别聚合 |

### fact_signal_candidates（机会粒度授权源）

**机会粒度问题必须读 `fact_signal_candidates`，禁止回到 raw JSON 自己 JOIN 重算。**
它和 `fact_trades` 是**互补的两张授权底表**，grain 不同，结论不准互相硬塞：

| 表 | grain | 回答 | PnL 列 |
|---|---|---|---|
| `fact_trades` | 每 fill 一行（只有成交了的） | 已成交 realized 绩效 | `pnl_usd_at_fill` / `pnl_usd_at_plan` |
| `fact_signal_candidates` | 每机会一行 `(condition_id, side, event_date)`（全机会宇宙） | 全机会 alpha、成交率、漏单、滑点、漏掉的赢家 | `counterfactual_pnl`（反事实，非成交 PnL） |

- DB 表：`runtime/weather.db` 的 `fact_signal_candidates` 表（每机会一行）
- Parquet：`runtime/weather_edge_v1/market_data/research/fact_signal_candidates.parquet`
- 重建：`run_stack.sh` 在 fact_trades **之后**调用 `scripts/analysis/build_weather_signal_candidates.py`
- 设计文档：[WEATHER_SIGNAL_CANDIDATES_DESIGN.md](WEATHER_SIGNAL_CANDIDATES_DESIGN.md)

**口径硬规定：**
- **反事实主口径 = `counterfactual_pnl`**，用**决策窗 `decision_entry_price`**（T-22~24h 真能看到的价）算，
  公式沿用 §2.1 已验证的 BUY_YES/BUY_NO（含 2026-05-29 BUY_NO 勘误）。
  `counterfactual_pnl_best`（用 `best_entry_price`）**仅作诊断上限，禁止当主绩效**（交易时不可能预知全天最优价）。
- **链路标志**：`seen`(恒1) / `eligible` / `paper_ordered` / `live_filled`。派生视图：
  `missed_fill = paper_ordered=1 AND live_filled=0`、
  `counterfactual_win = paper_ordered=0 AND win_by_count=1`、
  `filtered_out = eligible=0`。
- **可用反事实分母**：只在 `final_yes IS NOT NULL AND decision_window_missing=0 AND eligible=1` 上算城市/方向/模型 alpha，
  其余留空不估值（本表不做 Phase 1.5）。
- **`live_filled` 只认 `trade_class='live_real'`**；`live_pnl_usd` 直接引用 `fact_trades.pnl_usd_at_fill`，不重算。
- **`paper_ordered` 来自全池 paper ledger（T1+T2 paper 决定），不是 live 下单意图**。
  因此 `missed_fill` 大多是 paper≠live 的设计差异，**不是 live 执行漏单**；
  不要把 paper 成交率当 live 成交率。真实 live 覆盖率看 `live_filled / eligible`。

#### 取数 quickstart

```python
import sqlite3
conn = sqlite3.connect("runtime/weather.db"); conn.row_factory = sqlite3.Row

# 全 eligible 机会宇宙的城市/方向反事实 alpha（settled + 决策窗存在）
rows = conn.execute("""
    SELECT city, side,
           COUNT(*) AS n,
           SUM(paper_ordered) AS ordered, SUM(live_filled) AS live_fill,
           AVG(CAST(win_by_count AS REAL)) AS win_rate,
           SUM(counterfactual_pnl) AS cf_pnl
    FROM fact_signal_candidates
    WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0
    GROUP BY city, side
    ORDER BY cf_pnl DESC
""").fetchall()

# 滑点（live 实际成交价 − paper 想进价）
rows = conn.execute("""
    SELECT side, COUNT(*) n, AVG(slippage_vs_paper) avg_slippage
    FROM fact_signal_candidates
    WHERE slippage_vs_paper IS NOT NULL GROUP BY side
""").fetchall()
```

### weather.db（原始规范化表，仅 fact_trades / fact_signal_candidates builder 使用）

- 路径：`runtime/weather.db`
- 刷新方式：`scripts/weather_dashboard/run_stack.sh`（重跑 ingest）
- 覆盖时间：取决于镜像同步时间，详见 `WEATHER_DATA_PIPELINE.md`
- **直接读原始表的唯一授权场景**：`build_weather_fact_trades.py` 和 `build_weather_signal_candidates.py`（两个 builder）；其他代码禁止绕过这两张底表自己 JOIN 多表算 PnL

关键表结构：`signals` / `plans` / `orders` / `fills` / `settlements` / `runs` / `strategy_config`

### snapshot replay 脚本（机会反事实 — 标准问题改走 fact_signal_candidates）

以下两个脚本分析的是**候选信号 replay**（如果当时按快照价格入场会怎样），不是 DB 里的实际 fills：

| 脚本 | 数据源 | 说明 |
|---|---|---|
| `scripts/analysis/weather_city_pool_contribution_analysis.py` | `paper_snapshots/*.json` + `cache/pm_history/*.json` | 候选信号 × 当前城市池的反事实 replay |
| `scripts/analysis/weather_window_capture_performance.py` | 同上 | 时间窗口捕获率 A/B 对比 |

这两个脚本的 `pnl_usd=(payout - entry_price) * shares` 是 snapshot 级假设入场，不是成交层 PnL，**不需要** fact_trades。

> **口径更新（2026-05-29）**：城市/方向/模型反事实 alpha、成交率、滑点、漏掉的赢家这类**标准机会粒度问题，
> 现在一律走已物化的 `fact_signal_candidates`，不要再写一次性脚本扫 raw JSON**。
> 上面两个脚本只保留给本表未覆盖的研究维度（如多窗口 A/B、城市池假设重组）；
> 新增标准机会分析以 `fact_signal_candidates` 为准。

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

**已结算 PnL（settled PnL）— 直接读 fact_trades**

```sql
-- 正确做法：直接读预算好的列，不要自己实现公式
SELECT
    fill_id,
    city, city_pool, side, bracket, target_date,
    fill_price, plan_price, fill_qty, fees_usd,
    final_yes          AS settlement_yes_price,
    pnl_usd_at_fill,   -- 基于实际成交价
    pnl_usd_at_plan    -- 基于计划价（衡量滑点影响）
FROM fact_trades
WHERE settlement_status = 'settled'
```

**公式说明（仅供理解，禁止在消费者代码里重新实现）**

```
BUY_YES profit = (final_yes - fill_price) × fill_qty - fees
BUY_NO  profit = ((1 - final_yes) - fill_price) × fill_qty - fees
```

> ⚠️  BUY_NO 公式勘误（2026-05-29 修正）：  
> 旧（错误）：`fill_price - final_yes`  
> 正确：`(1 - final_yes) - fill_price`  
> 原理：`fills.filled_price` 对 BUY_NO 存的是 NO token 自身价，不是 YES 等效价。  
> 用 651 笔 N100 生产已结算 BUY_NO 对账验证，8/8 命中此公式。  
> **唯一授权实现**：`scripts/analysis/build_weather_fact_trades.py`

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
-- 数据源：fact_trades WHERE settlement_status = 'settled'

-- 按订单数
SELECT
  COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0)  AS wins_by_count,
  COUNT(*)                                      AS total_count,
  ROUND(
    1.0 * COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0) / COUNT(*), 4
  ) AS win_rate_by_count
FROM fact_trades
WHERE settlement_status = 'settled'

-- 按 notional（fill_price × fill_qty）
SELECT
  SUM(fill_price * fill_qty) FILTER (WHERE pnl_usd_at_fill > 0)  AS win_notional,
  SUM(fill_price * fill_qty)                                       AS total_notional,
  ROUND(
    SUM(fill_price * fill_qty) FILTER (WHERE pnl_usd_at_fill > 0)
    / SUM(fill_price * fill_qty), 4
  ) AS win_rate_by_notional
FROM fact_trades
WHERE settlement_status = 'settled'
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

---

## §4 策略身份

- **策略唯一标识 = `strategy_id`**（对应 `runs.config_id` → `strategy_config.config_id`）
- sizing 参数改动（`notional` / `sizing_mode`）**不改变** strategy_id；改动体现在 `strategy_config.params` 字段的 `code_version` / `sizing_mode` 子键
- A/B 对比切片使用 `strategy_config.params` 里的 `code_version` × `sizing_mode` 子键，不使用 strategy_id

---

## §5 切片维度白名单

分析报告只能使用以下切片，**新切片必须先 PR 进本文件再使用**。

所有切片直接 `GROUP BY fact_trades.<列名>`，不需要回到原始表 JOIN：

| 切片键 | `fact_trades` 列名 | 说明 |
|---|---|---|
| by_date | `target_date` | 目标日期（北京时间日） |
| by_city | `city` | 城市名 |
| by_model | `model_version` | 预测模型（ecmwf / gfs 等） |
| by_side | `side` | BUY_YES / BUY_NO |
| by_pool | `city_pool` | t1_trading / t2_research |
| by_pool_side | `city_pool, side` | 组合切片 |
| by_pool_model | `city_pool, model_version` | 组合切片 |
| by_trade_class | `trade_class` | live_real / paper / snapshot_replay 等 |
| by_strategy | `strategy_id` | 策略版本 |

---

## §6 默认城市池

### T1 交易池（`city_pool = 't1_trading'`）

默认分析范围。**城市归属以 `weather.db` 里 `signals.city_pool = 't1_trading'` 字段为准**，本列表仅供参考：

Amsterdam, Ankara, Atlanta, Austin, Beijing, BuenosAires, Busan, CapeTown,
Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, HongKong,
Houston, Istanbul, Jakarta, Jeddah, Karachi, KualaLumpur, LA, Lagos,
London, Lucknow, Madrid, Manila, MexicoCity, Miami, Milan, Moscow,
Munich, NYC, PanamaCity, Paris, SanFrancisco, SaoPaulo, Seattle, Seoul,
Shanghai, Shenzhen, Singapore, Taipei, TelAviv, Tokyo, Warsaw, Wellington, Wuhan

> 城市池调整后以 DB 数据为准，无需更新本列表。

### 其他池

- `t2_research`：T2 研究池，非默认分析范围。使用时需在 prompt 里显式指定 `city_pool=t2_research`
- 混合分析：在报告"对比设定"段注明 pool 范围

---

## §7 报告模板字段顺序

| 模式 | 模板文件 | 触发 skill |
|---|---|---|
| M1 绩效切片 | `docs/analysis/templates/performance.md` | weather-strategy-performance |
| M3 A/B 对比 | `docs/analysis/templates/performance-compare.md` | weather-strategy-performance |
| M2 单日血缘 | `docs/analysis/templates/lineage.md` | weather-strategy-lineage |
| M4 持仓敞口 | `docs/analysis/templates/exposure.md` | weather-strategy-exposure |

**输出路径规约**：`docs/analysis/YYYY-MM/YYYY-MM-DD-<mode>-<topic>.md`  
每份报告产出后必须 git commit。

---

## §8 待定项（遇到再补）

以下口径分歧暂未钉死，实际分析遇到时在此补充并 PR：

- **滑点扣减**：`plan_price` vs `fill_price` 差值当前两列并列展示，不单独作为成本项扣除
- **基准对比**：vs "随机下单" / vs "全 BUY_NO" / vs "持有 YES 到结算"
- **重复计数处理**：同一笔单同时出现在 ledger CSV + DB fills 的去重逻辑
- **部分成交**：`fills.status = 'partial'` 的订单如何计入 win_rate 分母
