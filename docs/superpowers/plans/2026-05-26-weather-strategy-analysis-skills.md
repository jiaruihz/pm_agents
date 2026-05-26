# Weather Strategy Analysis Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create 3 analysis skills + 1 contract doc + 4 report templates that force all agents to use consistent PnL/win_rate metrics and canonical data sources when analysing weather strategy performance.

**Architecture:** One thin skill per analysis type (performance / lineage / exposure) that enforces a pre-flight "read the contract" step, routes to the right data source, and fills a fixed Markdown template. The contract doc is the single source of truth for metric definitions, SQL, city lists, and slice dimensions.

**Tech Stack:** Markdown (skills, contract, templates), SQLite (`weather.db` schema in `weather_dashboard/db/schema_canonical.sql`), Python 3 reference queries. No new Python code — the skills point to existing scripts and the DB.

---

## File Map

| Action | Path |
|---|---|
| Create | `docs/WEATHER_ANALYSIS_CONTRACT.md` |
| Create | `docs/analysis/templates/performance.md` |
| Create | `docs/analysis/templates/performance-compare.md` |
| Create | `docs/analysis/templates/lineage.md` |
| Create | `docs/analysis/templates/exposure.md` |
| Create | `skills/weather-strategy-performance/SKILL.md` |
| Create | `skills/weather-strategy-lineage/SKILL.md` |
| Create | `skills/weather-strategy-exposure/SKILL.md` |
| Modify | `CLAUDE.md` (add skill hard-guide) |
| Modify | `AGENTS.md` (add skill hard-guide) |

---

## Task 1: Write `WEATHER_ANALYSIS_CONTRACT.md` §0–§3 (foundation)

**Files:**
- Create: `docs/WEATHER_ANALYSIS_CONTRACT.md`

- [ ] **Step 1: Create the contract file with §0 通用规约 through §3 时间规约**

```markdown
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
  → plans        (signal_id)
  → orders       (plan_id)
  → fills        (execution_id)
  → settlements  (target_date + condition_id / market_id)
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
| `t24_paper_ledger_summary.json` | 全量 paper ledger 绩效摘要，字段见 §2 |
| `t24_paper_ledger_trades.csv` | 逐笔 ledger，列：trade_id, city, city_pool, order_side, fill_price, plan_price, fill_qty, pnl_usd, settled, target_date |
| `t24_paper_snapshot_replay_summary.json` | snapshot replay 绩效摘要（与 ledger 同结构） |
| `t24_paper_snapshot_replay_trades.csv` | snapshot replay 逐笔 |
| `t24_snapshot_replay_equity_curve.csv` | 资金曲线（date, cumulative_pnl） |

### N100 raw（降级使用）

- SSH：`ssh jiarui@192.168.0.200`（WSL 内执行，密钥 `~/.ssh/id_ed25519_weather_deploy`）
- 关键路径：`~/projects/weather-predict/output/paper_trades/paper_orders.jsonl`
- 同步命令：`scripts/ops/sync_weather_remote.sh`

---

## §2 核心指标定义

> 参考实现：N100 `scripts/analysis/settle_t24_paper.py`（生成 `t24_paper_ledger_summary.json`）

### 2.1 PnL

**已结算 PnL（settled PnL）**

```sql
-- BUY_YES: profit = (final_yes_price - fill_price) * fill_qty - fees_usd
-- BUY_NO:  profit = (fill_price - final_yes_price) * fill_qty - fees_usd
-- (因为 NO token 买入成本 = 1 - fill_price，结算价 = 1 - final_yes_price)
SELECT
  f.fill_id,
  sig.city,
  sig.city_pool,
  o.order_side,
  o.entry_price          AS plan_price,    -- 信号下的计划入场价（entry_price 在 orders 表，不在 plans 表）
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
    WHEN 'BUY_YES' THEN (s.final_price - p.entry_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (p.entry_price - s.final_price) * f.filled_shares - f.fees_usd
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

报告里必须单独列出，标注 `[UNSETTLED]`，用三种估值：

| 估值 | 说明 | 字段/来源 |
|---|---|---|
| mid | 当前盘口 mid price | 需要最新 snapshot（镜像 CSV 或 API） |
| bid | 当前买一价 | 同上 |
| last_fill | 最近一笔成交价 | `fills.filled_price` |

未结算估值仅供参考，**不计入"已结算 PnL"总览**。

**report 必须同时列**：`pnl_usd_at_fill`（实际成交价口径）和 `pnl_usd_at_plan`（计划价口径），以及 `fill_qty`。

### 2.2 Win Rate

```sql
-- 按订单数
SELECT
  COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0)  AS wins_by_count,
  COUNT(*)                                      AS total_count,
  ROUND(1.0 * COUNT(*) FILTER (WHERE pnl_usd_at_fill > 0) / COUNT(*), 4)
    AS win_rate_by_count

-- 按 notional
SELECT
  SUM(f.filled_shares * f.filled_price) FILTER (WHERE pnl_usd_at_fill > 0)
    AS win_notional,
  SUM(f.filled_shares * f.filled_price) AS total_notional,
  ROUND(win_notional / total_notional, 4) AS win_rate_by_notional
```

报告**必须同时列两套**（by_count 和 by_notional）。

**含未结算的版本**：未结算订单按 mid 估值 > 0 视为 tentative win，标注 `[含未结算]`。

### 2.3 ROI / Sharpe

```
ROI = total_pnl_usd / total_cost_usd

avg_daily_pnl = sum(daily_pnl) / n_trading_days
std_daily_pnl = std(daily_pnl)
sharpe_like   = avg_daily_pnl / std_daily_pnl   -- 未年化，仅供参考
```

---

## §3 时间规约

### 双时区

报告里涉及时间的列**必须同时显示**：
- **北京时间** (`Asia/Shanghai`)：所有时间戳转为北京时间展示
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

**以 `orders.created_at_utc`（下单时间戳）所在北京时间日期为准。** 结算日期在 `signals.target_date`；两者可能不同——报告里两列都列出。
```

- [ ] **Step 2: Verify headings**

```bash
grep "^## §" docs/WEATHER_ANALYSIS_CONTRACT.md
```

Expected:
```
## §0 通用规约
## §1 数据源清单
## §2 核心指标定义
## §3 时间规约
```

- [ ] **Step 3: Commit**

```bash
git add docs/WEATHER_ANALYSIS_CONTRACT.md
git commit -m "docs: add WEATHER_ANALYSIS_CONTRACT §0–§3 (PnL/win_rate/time rules)"
```

---

## Task 2: Write `WEATHER_ANALYSIS_CONTRACT.md` §4–§8

**Files:**
- Modify: `docs/WEATHER_ANALYSIS_CONTRACT.md` (append)

- [ ] **Step 1: Append §4–§8 to the contract file**

```markdown
---

## §4 策略身份

- **策略唯一标识 = `strategy_id`**（`runs.config_id` 对应的 `strategy_config.config_id`）
- sizing 参数改动（`notional` / `sizing_mode`）**不改变** strategy_id；改动体现在 `strategy_config.params` 字段的 `code_version` 子键
- A/B 对比使用 `strategy_config.params` 里的 `code_version` × `sizing_mode` 子键作为切片

---

## §5 切片维度白名单

分析报告只能使用以下切片，**新切片必须先 PR 进本文件再使用**：

| 切片键 | 对应字段 | 说明 |
|---|---|---|
| by_date | `signals.target_date` | 目标日期（北京时间日） |
| by_city | `signals.city` | 城市名 |
| by_model | `signals.forecast_source` | 预测模型（ecmwf / gfs 等） |
| by_side | `orders.order_side` | BUY_YES / BUY_NO |
| by_pool | `signals.city_pool` | t1_trading / t2_research |
| by_pool_side | city_pool × order_side | 组合切片 |
| by_pool_model | city_pool × forecast_source | 组合切片 |

---

## §6 默认城市池

### T1 交易池（`city_pool = 't1_trading'`）

默认分析范围。具体城市列表以 `weather.db` 里 `city_pool = 't1_trading'` 的 signals 为准。

已知在 ledger 中出现过 t1_trading 的城市（截至 2026-05-26，从镜像产物采样）：
- Amsterdam, Ankara, Atlanta, Austin, Beijing, BuenosAires, Busan, CapeTown
- Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, HongKong
- Houston, Istanbul, Jakarta, Jeddah, Karachi, KualaLumpur, LA, Lagos
- London, Lucknow, Madrid, Manila, MexicoCity, Miami, Milan, Moscow
- Munich, NYC, PanamaCity, Paris, SanFrancisco, SaoPaulo, Seattle, Seoul
- Shanghai, Shenzhen, Singapore, Taipei, TelAviv, Tokyo, Warsaw, Wellington, Wuhan

> 注：**T1/T2 归属以 DB 字段为准，此列表仅供参考。** 城市池调整后以 DB 数据为准，无需更新本列表。

### 其他池

- `t2_research`：T2 研究池，非默认。使用时需在 prompt 里显式指定 `city_pool=t2_research`
- 混合分析：在报告"对比设定"段注明 pool 范围

---

## §7 报告模板字段顺序

每种报告模式的模板文件在 `docs/analysis/templates/` 下（见 Task 3）。

| 模式 | 模板文件 | 触发 skill |
|---|---|---|
| M1 绩效切片 | `templates/performance.md` | weather-strategy-performance |
| M3 A/B 对比 | `templates/performance-compare.md` | weather-strategy-performance |
| M2 单日血缘 | `templates/lineage.md` | weather-strategy-lineage |
| M4 持仓敞口 | `templates/exposure.md` | weather-strategy-exposure |

**输出路径规约**：`docs/analysis/YYYY-MM/YYYY-MM-DD-<mode>-<topic>.md`

---

## §8 待定项（遇到再补）

以下口径分歧暂未钉死，遇到实际分析需求时在此补充：

- **滑点扣减**：`plan_price` vs `fill_price` 差值是否作为独立指标展示（当前两列并列，不单独扣）
- **基准对比**：vs "随机下单" / vs "全 BUY_NO" / vs "持有 YES 到结算"
- **重复计数处理**：同一笔单同时出现在 ledger CSV + DB fills 的去重逻辑
- **部分成交**：`fills.status = 'partial'` 的订单如何计入 win_rate 分母
```

- [ ] **Step 2: Verify all sections present**

```bash
grep "^## §" docs/WEATHER_ANALYSIS_CONTRACT.md
```

Expected:
```
## §0 通用规约
## §1 数据源清单
## §2 核心指标定义
## §3 时间规约
## §4 策略身份
## §5 切片维度白名单
## §6 默认城市池
## §7 报告模板字段顺序
## §8 待定项（遇到再补）
```

- [ ] **Step 3: Commit**

```bash
git add docs/WEATHER_ANALYSIS_CONTRACT.md
git commit -m "docs: complete WEATHER_ANALYSIS_CONTRACT §4–§8 (strategy identity, slices, city pool)"
```

---

## Task 3: Write 4 report templates

**Files:**
- Create: `docs/analysis/templates/performance.md`
- Create: `docs/analysis/templates/performance-compare.md`
- Create: `docs/analysis/templates/lineage.md`
- Create: `docs/analysis/templates/exposure.md`

- [ ] **Step 1: Create directory**

```bash
mkdir -p docs/analysis/templates
```

- [ ] **Step 2: Write `templates/performance.md`**

```markdown
<!--
  M1 绩效切片报告模板
  规则：H2 顺序不得改变，不得删除段落，可在末尾追加"观察与建议"。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md
-->

# 绩效分析：{topic}

> 时间窗：{date_start} — {date_end}（北京时间）  
> 策略：{strategy_id}  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | {source_path} |
| 数据快照时间 | {snapshot_ts} |
| 记录行数（fills） | {fill_row_count} |
| unsettled 占比 | {unsettled_n} / {total_n} ({unsettled_pct}%) |
| missing_bracket 数 | {missing_bracket_n} |

## 总览

| 指标 | 已结算（fill 口径） | 已结算（plan 口径） | 含未结算（mid 估值） |
|---|---|---|---|
| 总 PnL (USD) | | | [UNSETTLED] |
| ROI | | | [UNSETTLED] |
| Win rate (by count) | | | [UNSETTLED] |
| Win rate (by notional) | | | [UNSETTLED] |
| 总 fills 数 | | | |
| 总 notional (USD) | | | |
| 总 fill_qty (shares) | | | |
| Sharpe-like (daily) | | | |

## 切片：by_date

| 日期（北京时间） | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---|---|---|---|---|---|---|

## 切片：by_city

| city | fills | wins | win_rate | cost_usd | pnl_usd (fill) | roi |
|---|---|---|---|---|---|---|

## 切片：by_model

| model | fills | wins | win_rate | pnl_usd (fill) | roi |
|---|---|---|---|---|---|

## 切片：by_side

| side | fills | wins | win_rate | avg_fill_price | pnl_usd (fill) | roi |
|---|---|---|---|---|---|---|

## 切片：by_pool

| pool | fills | wins | win_rate | pnl_usd (fill) | roi |
|---|---|---|---|---|---|

## Top Winners / Top Losers

**Top 5 winners（by pnl_usd_at_fill）：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement | pnl_usd |
|---|---|---|---|---|---|---|---|

**Top 5 losers：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement | pnl_usd |
|---|---|---|---|---|---|---|---|

## 数据完整性自检

- [ ] fill_row_count 与预期时间窗匹配
- [ ] unsettled_pct < 20%（否则说明结算延迟，总览数字不可靠）
- [ ] missing_bracket_n 列出具体城市/日期
- [ ] by_date 行数 = 时间窗天数（否则说明某天无数据，需注明原因）

## 观察与建议

{agent 自由发挥，不超过 500 字}
```

- [ ] **Step 3: Write `templates/performance-compare.md`**

```markdown
<!--
  M3 A/B 对比报告模板
  规则：H2 顺序不得改变，每个切片段都必须有 A | B | delta | delta% 四列。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-compare-<A>-vs-<B>.md
-->

# 策略对比：{selector_A} vs {selector_B}

> 时间窗：{date_start} — {date_end}（北京时间）  
> 对比维度：{compare_dimension}（如 strategy_id / city_pool / code_version）  
> 数据源：{data_source}

## 数据快照（A / B）

| 项目 | A | B |
|---|---|---|
| selector | {selector_A} | {selector_B} |
| 数据快照时间 | | |
| fill 行数 | | |
| unsettled 占比 | | |
| missing_bracket 数 | | |

## 对比设定

- **Selector A**：{selector_A 完整描述}
- **Selector B**：{selector_B 完整描述}
- **对齐方式**：{同时间窗 / 同城市池 / 其他}

## 总览对比

| 指标 | A | B | delta (B-A) | delta% |
|---|---|---|---|---|
| PnL (USD, fill 口径) | | | | |
| ROI | | | | |
| Win rate (by count) | | | | |
| Win rate (by notional) | | | | |
| fills 数 | | | | |
| 总 notional | | | | |

## 切片对比：by_date

| 日期 | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 切片对比：by_city

| city | A pnl | B pnl | delta | A roi | B roi |
|---|---|---|---|---|---|

## 切片对比：by_model

| model | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 切片对比：by_side

| side | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 显著差异 Top-N

**B 显著优于 A 的城市/日期（delta > +$2 或 win_rate delta > +10%）：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|

**A 显著优于 B 的城市/日期：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|

## 数据完整性自检

- [ ] A 和 B 时间窗完全对齐
- [ ] A 和 B 城市池范围一致（或差异已在"对比设定"中注明）
- [ ] unsettled 占比双方均 < 20%

## 观察与建议

{agent 自由发挥，不超过 500 字}
```

- [ ] **Step 4: Write `templates/lineage.md`**

```markdown
<!--
  M2 单日血缘报告模板
  规则：H2 顺序不得改变，全链路表按 city 分组，每笔订单独立一行。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-lineage-<date>.md
-->

# 单日血缘：{target_date}

> 目标日期：{target_date}  
> 策略：{strategy_id}  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | {source_path} |
| 数据快照时间 | {snapshot_ts} |
| 信号数 | {signal_count} |
| 计划数 | {plan_count} |
| 订单数 | {order_count} |
| 成交数（filled） | {fill_count} |
| 结算数（settled） | {settled_count} |
| 未结算数 | {unsettled_count} |
| missing_bracket | {missing_bracket_n} |

## 当日策略身份与配置

| 字段 | 值 |
|---|---|
| strategy_id | |
| code_version | |
| sizing_mode | |
| city_pool | |
| run_id | |

## 全链路表（按 city 分组）

每个城市一个小节，格式如下：

### {city}（{city_pool}）

| 阶段 | 字段 | 值 |
|---|---|---|
| **Signal** | signal_id | |
| | snapshot_ts_utc（北京时间） | |
| | forecast_source（model） | |
| | signal_value | |
| **Plan** | plan_id | |
| | order_side | |
| | entry_price（plan_price） | |
| | notional | |
| | desired_shares | |
| **Order** | execution_id | |
| | venue | |
| | limit_price | |
| | created_at_utc（北京时间 / 当地时间） | |
| **Fill** | fill_id | |
| | filled_price | |
| | filled_shares | |
| | fill_status | |
| | filled_at_utc（北京时间 / 当地时间） | |
| **Settlement** | final_price | |
| | settlement_status | |
| | bracket | |
| **PnL** | pnl_usd (fill 口径) | |
| | pnl_usd (plan 口径) | |

## 异常订单列表

| city | 异常类型 | 说明 |
|---|---|---|
| | missing_bracket | |
| | plan_vs_fill 偏差 > 5% | fill_price - plan_price = |
| | 未成交（no fill） | |

## 当日 PnL 汇总

| 指标 | 已结算（fill） | 已结算（plan） |
|---|---|---|
| 总 PnL (USD) | | |
| 总 fills | | |
| Win rate (by count) | | |

## 数据完整性自检

- [ ] signal_count = plan_count（每个信号都有对应计划）
- [ ] plan_count = order_count（每个计划都下了单）
- [ ] 无 orphan fills（fill 有 execution_id 但 order 不存在）
- [ ] 列出无法结算的城市（missing_bracket / missing_event）

## 观察与建议

{agent 自由发挥，不超过 500 字}
```

- [ ] **Step 5: Write `templates/exposure.md`**

```markdown
<!--
  M4 持仓敞口报告模板
  规则：H2 顺序不得改变，未实现 PnL 三估值必须并列。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-exposure-<snapshot_time>.md
-->

# 持仓敞口快照：{snapshot_time}

> 快照时间：{snapshot_time}（北京时间）  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | {source_path} |
| 快照时间 | {snapshot_ts} |
| 未结算持仓总笔数 | {open_position_count} |
| 涉及城市数 | {city_count} |
| 最早到期日 | {earliest_settle_date} |
| 最晚到期日 | {latest_settle_date} |

## 未结算持仓清单

| city | city_pool | target_date | side | fill_price | fill_qty | cost_usd | condition_id |
|---|---|---|---|---|---|---|---|

## 聚合：by_market（按 condition_id）

| condition_id | city | target_date | open_positions | total_cost_usd |
|---|---|---|---|---|

## 聚合：by_city

| city | open_positions | total_cost_usd | avg_fill_price |
|---|---|---|---|

## 聚合：by_settle_date

| target_date | open_positions | total_cost_usd |
|---|---|---|

## 未实现 PnL（三估值并列）

> 注：三列均标注 `[UNSETTLED]`，不计入历史绩效。

| city | target_date | side | cost_usd | unrealized_pnl (mid) | unrealized_pnl (bid) | unrealized_pnl (last_fill) |
|---|---|---|---|---|---|---|

**合计：**

| 估值口径 | 未实现 PnL (USD) |
|---|---|
| mid（当前盘口中间价） | [UNSETTLED] |
| bid（当前买一价） | [UNSETTLED] |
| last_fill（最近成交价） | [UNSETTLED] |

## 集中度风险

**单市场占比 Top-5（by cost_usd）：**

| rank | condition_id / city | cost_usd | pct_of_total |
|---|---|---|---|

**单到期日占比 Top-5：**

| rank | target_date | cost_usd | pct_of_total |
|---|---|---|---|

## 数据完整性自检

- [ ] 所有持仓都有对应 fill 记录
- [ ] 没有已过到期日但未结算的持仓（若有，列出并标注异常）
- [ ] mid/bid 估值数据来源说明（snapshot 时间是否新鲜）

## 观察与建议

{agent 自由发挥，不超过 500 字}
```

- [ ] **Step 6: Verify all 4 template files exist**

```bash
ls docs/analysis/templates/
```

Expected:
```
exposure.md  lineage.md  performance-compare.md  performance.md
```

- [ ] **Step 7: Commit**

```bash
git add docs/analysis/templates/
git commit -m "docs: add 4 analysis report templates (performance/compare/lineage/exposure)"
```

---

## Task 4: Write 3 SKILL.md files

**Files:**
- Create: `skills/weather-strategy-performance/SKILL.md`
- Create: `skills/weather-strategy-lineage/SKILL.md`
- Create: `skills/weather-strategy-exposure/SKILL.md`

- [ ] **Step 1: Create directories**

```bash
mkdir -p skills/weather-strategy-performance
mkdir -p skills/weather-strategy-lineage
mkdir -p skills/weather-strategy-exposure
```

- [ ] **Step 2: Write `skills/weather-strategy-performance/SKILL.md`**

```markdown
---
name: weather-strategy-performance
description: >
  分析 weather 策略的历史绩效（PnL / ROI / win_rate）或对比两个策略/参数版本（A/B）。
  触发词：绩效、PnL、ROI、win rate、切片、对比策略、A/B、回测结果、策略表现、胜率。
  禁止：在不读 contract 的情况下写一次性分析脚本；自定义指标；绕过 weather.db。
---

# weather-strategy-performance

分析 weather 策略聚合绩效。支持两个子模式：
- **M1 单跑绩效切片**：指定时间窗 + 策略，输出多维切片报告
- **M3 A/B 对比**：两个 selector 的双栏对比报告

## 必须按照以下 checklist 执行

### 第 0 步：强制前置（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，确认理解：
- §0 禁止清单与报告头必填项
- §2 PnL / win_rate 计算公式（含 plan 口径 vs fill 口径）
- §5 切片维度白名单

**未读 contract 不得继续。**

### 第 1 步：确认分析参数

在开始前与用户确认以下参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| mode | M1 单跑 / M3 A/B | 用户指定 |
| date_start / date_end | 时间窗（北京时间） | 用户指定 |
| strategy_id | 策略 ID，或 "all" | 用户指定 |
| city_pool | t1_trading / t2_research / all | t1_trading |
| data_source | db / api / mirror / n100raw | db |
| M3 专用：selector_A, selector_B | 对比维度和值 | 用户指定 |

### 第 2 步：数据源选择

按 contract §0 优先级：

1. **首选 DB**：
   ```bash
   # 确认 DB 存在且时间戳新鲜
   ls -la runtime/weather_edge_v1/weather.db
   ```

2. **降级到镜像 JSON**（DB 不可用时）：
   ```bash
   cat runtime/weather_edge_v1/market_data/research/t24_paper_ledger_summary.json \
     | python3 -m json.tool | head -30
   ```

3. **降级到 Dashboard API**（前两者不可用时）：
   ```bash
   curl -s http://localhost:8000/api/runs | python3 -m json.tool | head -30
   ```

在报告"数据快照"段注明使用了哪一级，以及原因（若不是首选）。

### 第 3 步：数据完整性自检

运行以下自检后再计算指标：

```python
import sqlite3, json
conn = sqlite3.connect("runtime/weather_edge_v1/weather.db")
# 总 fills 数
total = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
# unsettled（无对应 settlement）
unsettled = conn.execute("""
  SELECT COUNT(*) FROM fills f
  JOIN orders o ON o.execution_id = f.execution_id
  JOIN plans p ON p.plan_id = o.plan_id
  JOIN signals sig ON sig.signal_id = p.signal_id
  WHERE NOT EXISTS (
    SELECT 1 FROM settlements s
    WHERE s.target_date = sig.target_date
      AND s.settlement_status = 'settled'
  )
""").fetchone()[0]
# missing_bracket
mb = conn.execute(
  "SELECT COUNT(*) FROM settlements WHERE settlement_status='missing_bracket'"
).fetchone()[0]
print(f"total={total}, unsettled={unsettled} ({100*unsettled/max(total,1):.1f}%), missing_bracket={mb}")
```

### 第 4 步：计算指标

使用 contract §2 的 SQL（plan_price 口径 + fill_price 口径双路并算）。

**M3 A/B**：对 selector_A 和 selector_B 各跑一次 §2 SQL，然后按切片键 join、算 delta。

### 第 5 步：填写报告模板

- M1 → `docs/analysis/templates/performance.md`
- M3 → `docs/analysis/templates/performance-compare.md`

**严格按模板 H2 顺序填写，不得删除段落，不得改字段顺序。**

输出文件：`docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md`

### 第 6 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: performance report <topic> <date_range>"
```
```

- [ ] **Step 3: Write `skills/weather-strategy-lineage/SKILL.md`**

```markdown
---
name: weather-strategy-lineage
description: >
  追踪某一天 weather 策略的完整执行血缘：信号→计划→订单→成交→结算，逐笔展开。
  触发词：单日、血缘、逐笔、为什么下了这单、信号到结算、当日复盘、某天下了什么单。
  禁止：在不读 contract 的情况下写一次性分析脚本；跳过异常订单段。
---

# weather-strategy-lineage

逐笔追踪单日执行血缘，用于复盘和异常排查。

## 必须按照以下 checklist 执行

### 第 0 步：强制前置（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，重点确认：
- §0 禁止清单与报告头必填项
- §1 DB join 路径（signals → plans → orders → fills → settlements）
- §3 时间规约（下单日 vs 目标日区分）

**未读 contract 不得继续。**

### 第 1 步：确认分析参数

| 参数 | 说明 |
|---|---|
| target_date | 目标日期（北京时间，格式 YYYY-MM-DD） |
| strategy_id | 策略 ID，或 "all" |
| city_pool | 默认 t1_trading |

### 第 2 步：数据源

首选 DB。按 contract §0 优先级降级。

```bash
ls -la runtime/weather_edge_v1/weather.db
```

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
  o.entry_price        AS plan_price,    -- entry_price 在 orders 表，不在 plans 表
  p.notional,
  p.desired_shares,
  o.execution_id,
  o.venue,
  o.limit_price,
  o.created_at_utc,
  f.fill_id,
  f.filled_price,
  f.filled_shares,
  f.status             AS fill_status,
  f.filled_at_utc,
  s.final_price,
  s.settlement_status,
  s.bracket,
  CASE o.order_side
    WHEN 'BUY_YES' THEN (s.final_price - f.filled_price) * f.filled_shares - f.fees_usd
    WHEN 'BUY_NO'  THEN (f.filled_price - s.final_price) * f.filled_shares - f.fees_usd
  END AS pnl_usd_at_fill
FROM signals sig
JOIN plans   p   ON p.signal_id    = sig.signal_id
JOIN orders  o   ON o.plan_id      = p.plan_id
LEFT JOIN fills  f ON f.execution_id = o.execution_id
LEFT JOIN settlements s
  ON s.target_date = sig.target_date
 AND (s.condition_id = sig.condition_id OR s.market_id = sig.market_id)
WHERE sig.target_date = '{target_date}'
  AND sig.city_pool   = '{city_pool}'
ORDER BY sig.city, o.created_at_utc
```

### 第 4 步：填写报告模板

模板：`docs/analysis/templates/lineage.md`

按 city 分组填写全链路表。**异常订单段不得留空**——若无异常，写"无"。

输出文件：`docs/analysis/YYYY-MM/YYYY-MM-DD-lineage-{target_date}.md`

### 第 5 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: lineage report {target_date}"
```
```

- [ ] **Step 4: Write `skills/weather-strategy-exposure/SKILL.md`**

```markdown
---
name: weather-strategy-exposure
description: >
  查看当前 weather 策略的未结算持仓和风险敞口（按市场/城市/到期日聚合）。
  触发词：持仓、敞口、未结算、未平仓、风险、当前仓位、还挂着哪些单。
  禁止：在不读 contract 的情况下写一次性分析脚本；漏掉三种估值并列。
---

# weather-strategy-exposure

时间点快照：查询所有 open positions（有 fill 但尚无 settled 结算记录）。

## 必须按照以下 checklist 执行

### 第 0 步：强制前置（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，重点确认：
- §0 禁止清单与报告头必填项
- §2.1 未结算 PnL 三种估值定义
- §1 数据源清单（mid/bid 估值需要最新 snapshot）

**未读 contract 不得继续。**

### 第 1 步：确认分析参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| snapshot_time | 快照时间（北京时间） | now |
| city_pool | t1_trading / t2_research / all | t1_trading |

### 第 2 步：查询 open positions

```sql
SELECT
  sig.city,
  sig.city_pool,
  sig.target_date,
  o.order_side,
  f.filled_price,
  f.filled_shares,
  f.filled_price * f.filled_shares AS cost_usd,
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
ORDER BY sig.target_date, sig.city
```

### 第 3 步：未实现 PnL 估值

对每条 open position 按三种口径估值（参考 contract §2.1）：

- **last_fill**：`filled_price`（已有，直接从 fills 取）
- **mid / bid**：需要从最新 snapshot CSV 或 API 取当前盘口价

```python
# 从镜像 snapshot CSV 取最新 mid（若 API 不可用）
import pandas as pd
df = pd.read_csv(
  "runtime/weather_edge_v1/market_data/paper_snapshots/latest_snapshot.csv"
)
# join by condition_id 取 mid_price, best_bid
```

若盘口数据不可用，mid/bid 两列填 `N/A（无 snapshot）`，并在报告数据快照段说明。

### 第 4 步：填写报告模板

模板：`docs/analysis/templates/exposure.md`

**三估值列必须并列，标注 `[UNSETTLED]`，不得合并为一列。**

输出文件：`docs/analysis/YYYY-MM/YYYY-MM-DD-exposure-{snapshot_ts}.md`

### 第 5 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: exposure snapshot {snapshot_ts}"
```
```

- [ ] **Step 5: Verify 3 SKILL.md files exist**

```bash
ls skills/weather-strategy-performance/ skills/weather-strategy-lineage/ skills/weather-strategy-exposure/
```

Expected: `SKILL.md` in each directory.

- [ ] **Step 6: Commit**

```bash
git add skills/weather-strategy-performance/ skills/weather-strategy-lineage/ skills/weather-strategy-exposure/
git commit -m "feat: add 3 weather strategy analysis skills (performance/lineage/exposure)"
```

---

## Task 5: Update CLAUDE.md and AGENTS.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Add hard skill guide to CLAUDE.md**

Find the `## 文档索引` section (bottom of CLAUDE.md) and insert the following block **before** it:

```markdown
## Weather 策略分析强制规约

**任何 weather 策略分析请求必须先 invoke 对应 skill，不准跳过：**

| 分析类型 | 触发词 | Skill |
|---|---|---|
| 历史绩效 / A/B 对比 | 绩效、PnL、ROI、win rate、切片、对比、A/B | `weather-strategy-performance` |
| 单日血缘 / 逐笔复盘 | 单日、血缘、逐笔、当日复盘 | `weather-strategy-lineage` |
| 持仓敞口 / 未平仓 | 持仓、敞口、未结算、风险、当前仓位 | `weather-strategy-exposure` |

**禁止**：在不 invoke skill 的情况下直接写一次性 pandas 脚本做策略分析。  
**口径唯一来源**：[docs/WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md)

```

- [ ] **Step 2: Add same block to AGENTS.md**

Same content, placed in a section titled `## Weather 策略分析强制规约` near the top of AGENTS.md (after any existing intro paragraphs).

- [ ] **Step 3: Verify lines appear**

```bash
grep -n "weather-strategy-performance" CLAUDE.md AGENTS.md
```

Expected: at least 1 match in each file.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: add weather analysis skill hard-guide to CLAUDE.md and AGENTS.md"
```

---

## Task 6: Smoke test — run one real analysis

- [ ] **Step 1: Invoke performance skill manually**

Ask Claude (or Sonnet) to perform M1 analysis:

> "请用 weather-strategy-performance skill 分析 2026-05-20 到 2026-05-25 t1_trading 策略的绩效"

- [ ] **Step 2: Verify it read the contract**

Agent 的第一步输出应该包含"读 `docs/WEATHER_ANALYSIS_CONTRACT.md`"或类似说明。如果 agent 直接写脚本没有提到 contract，则 skill description 触发词需要调整。

- [ ] **Step 3: Verify report landed in correct path**

```bash
ls docs/analysis/2026-05/
```

Expected: file named `2026-05-*-performance-*.md`

- [ ] **Step 4: Verify report has all required H2 sections**

```bash
grep "^## " docs/analysis/2026-05/*performance*.md
```

Expected sections (in order):
```
## 数据快照
## 总览
## 切片：by_date
## 切片：by_city
## 切片：by_model
## 切片：by_side
## 切片：by_pool
## Top Winners / Top Losers
## 数据完整性自检
## 观察与建议
```

- [ ] **Step 5: Commit smoke test report**

```bash
git add docs/analysis/
git commit -m "analysis: smoke test performance report 2026-05-20-25"
```
