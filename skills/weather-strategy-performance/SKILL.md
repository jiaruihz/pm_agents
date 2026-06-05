---
name: weather-strategy-performance
description: >
  分析 weather 策略的历史绩效、PnL、ROI、win rate、城市 alpha、稳定性、多维切片，
  或对比两个策略/参数版本的 A/B 表现。触发词：绩效、PnL、ROI、win rate、胜率、
  切片、对比策略、A/B、回测结果、策略表现、历史表现、收益分析、城市 alpha、稳定性。
  禁止：绕过 fact_trades 自算成交 PnL；绕过 weather.db 直接用 raw JSON/CSV 跑 pandas；
  把候选信号/未成交机会混进 fill-grain 绩效表（成交质量/机会 alpha 改读 fact_signal_candidates）。
---

# weather-strategy-performance

分析 weather 策略**已成交 fill** 的历史绩效。已成交 PnL 唯一取数源是：

```text
runtime/weather.db.fact_trades
```

如果用户问的是“余额少了 / 钱包还有多少 / 最近几天账户到底亏没亏 / CLOB fill 是否漏记”，这不是普通绩效问题，先转 `weather-live-account-reconcile`。绩效 skill 不得用 `fact_trades.order_date_bj` 或 `cost_usd` 解释钱包现金流；余额现金流默认按 `fill_date_bj` 的 actual fill cost 对账，并把 open cost 与 realized PnL 分开。

`fact_trades` 的 grain 是每 fill 一行。它回答“实际成交后的 realized / shadow / replay 绩效”，不回答 missed signal、候选信号、窗口捕获、未成交机会成本、全市场机会质量。

**这些机会粒度问题现在有授权底表 `fact_signal_candidates`**（每机会一行，已物化，见 contract §1）。
当用户问“成交质量 / 成交率 / 漏单 / 滑点 / 全机会集真实 alpha / 漏掉的赢家”时，**join 这张表**，不要回去扫 raw JSON。

| 表 | grain | 回答 | PnL 列 |
|---|---|---|---|
| `fact_trades` | 每 fill | 已成交 realized 绩效 | `pnl_usd_at_fill` |
| `fact_signal_candidates` | 每机会 `(condition_id,side,event_date)` | 全机会 alpha / 成交率 / 滑点 / 漏单 | `counterfactual_pnl`（反事实，非成交 PnL） |

**禁止互相硬塞**：不要拿候选反事实 PnL 冒充已成交绩效，也不要用成交样本结论否定全机会 alpha。
关联键 = `(condition_id, side, event_date)`（fact_trades 侧用 `condition_id + side + target_date`）。

两个子模式：
- **M1 单跑绩效切片**：指定时间窗 + 策略/来源，输出多维切片报告。
- **M3 A/B 对比**：两个 selector 的双栏对比报告。

---

## 执行 Checklist

### 第 0 步：读 contract

先读 `docs/WEATHER_ANALYSIS_CONTRACT.md`，至少确认：
- `fact_trades` 是绩效分析强制唯一取数源。
- PnL 只读 `pnl_usd_at_fill` / `pnl_usd_at_plan`，不要在分析脚本里重写 BUY_YES / BUY_NO 公式。
- 切片维度白名单与当前报告要求。

未读 contract 不得继续分析。

### 第 1 步：收敛 target metric / target slice

先把用户问题收敛成一句明确口径。例如：
- 城市 alpha：`by_city_realized_alpha` = “按 city 聚合已结算 fill 的 PnL、ROI、win_rate、active_days、positive_day_rate、worst_day_pnl、daily_sharpe_like”。
- live 实绩：只取 `trade_class = 'live_real'`。
- paper/shadow/replay：按 `trade_class` 分层，不混算。

必须先锁分母：
- `trade_class`: `live_real` / `live_simulated` / `paper` / `snapshot_replay` / `all`
- 时间字段：默认 `target_date`；如果问题是策略下单归属诊断，可用 `order_date_bj`；如果问题是钱包现金流，不能用本 skill，必须转 `weather-live-account-reconcile` 的 `fill_date_bj`
- settlement：realized PnL 默认只纳入 `settlement_status='settled'`；未结算估值单独列

### 第 2 步：确认分析参数

若用户没提供，使用默认值并在报告头写明：

| 参数 | 默认值 |
|---|---|
| mode | M1 |
| date_start / date_end | `fact_trades` 中可用 `target_date` 全范围 |
| strategy_id | all |
| city_pool | all |
| trade_class | 分开列；live 结论默认只用 `live_real` |
| data_source | db: `runtime/weather.db.fact_trades` |

M3 A/B 必须明确 selector_A / selector_B，例如不同 `strategy_id`、`code_version`、`execution_policy`、`city_pool`、时间段或 `trade_class`。

### 第 3 步：同步与数据源自检

如果用户需要最新数据，先同步 N100 并重建底表：

```bash
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh
```

如果只做本地缓存分析，必须在报告“数据快照”说明。

检查 DB：

```bash
ls -la runtime/weather.db
```

用 `fact_trades` 做完整性自检，不要回到 raw join：

```sql
SELECT COUNT(*) AS total_rows FROM fact_trades;

SELECT trade_class, COUNT(*) AS rows, SUM(settled) AS settled_rows
FROM fact_trades
GROUP BY trade_class
ORDER BY trade_class;

SELECT settlement_join_method, settlement_status, COUNT(*) AS rows
FROM fact_trades
GROUP BY settlement_join_method, settlement_status
ORDER BY settlement_join_method, settlement_status;

SELECT COUNT(*) AS bad_settled_null_pnl
FROM fact_trades
WHERE settlement_status = 'settled' AND pnl_usd_at_fill IS NULL;
```

报告“数据快照”必须写：DB 路径、DB mtime 或 `MAX(fact_built_at_utc)`、行数、settled/unsettled 占比、missing_bracket 数、`trade_class` 分布、`settlement_join_method` 分布。

### 第 4 步：指标计算，只读 fact_trades

禁止重写 BUY_YES / BUY_NO 公式。常用聚合：

```sql
SELECT
  city,
  COUNT(*) AS fills,
  SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
  SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost_usd,
  SUM(CASE WHEN settlement_status='settled' THEN cost_usd_at_plan ELSE 0 END) AS cost_usd_at_plan,
  SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl_usd_at_fill,
  SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_plan ELSE 0 END) AS pnl_usd_at_plan,
  AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate_by_count,
  SUM(CASE WHEN settlement_status='settled' AND win_by_count=1 THEN cost_usd ELSE 0 END)
    / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS win_rate_by_notional
FROM fact_trades
WHERE target_date BETWEEN :date_start AND :date_end
  AND (:trade_class = 'all' OR trade_class = :trade_class)
  AND (:city_pool = 'all' OR city_pool = :city_pool)
  AND (:strategy_id = 'all' OR strategy_id = :strategy_id)
GROUP BY city;
```

ROI:
- fill ROI = `SUM(pnl_usd_at_fill) / SUM(cost_usd)`
- plan ROI = `SUM(pnl_usd_at_plan) / SUM(cost_usd_at_plan)`

未结算估值单独列，标注 `[UNSETTLED]`：
- `val_mid`
- `val_bid`
- `val_last_fill`
- `unrealized_pnl_mid`

未结算估值不得混入 realized PnL。

### 第 5 步：城市 alpha / 稳定性默认维度

城市 alpha 分析默认输出：
- `city`, `city_pool`
- `fills`, `settled_fills`, `active_days`
- `cost_usd`, `pnl_usd_at_fill`, `roi`
- `win_rate_by_count`, `win_rate_by_notional`
- `avg_pnl_per_fill`
- `positive_day_rate`
- `worst_day_pnl`, `best_day_pnl`
- `daily_sharpe_like`
- `side_mix`, `model_mix`, `execution_policy_mix`

稳定性必须先按 `city + target_date` 聚合 daily PnL，再算正收益天比例、最差日、Sharpe-like。不要直接用 fill 级 PnL 的标准差冒充日稳定性。

样本量不足要降级结论：默认少于 5 个 settled fills 或少于 3 个 active days 的城市只能标注为 `low_sample`，不能给强保留/剔除结论。

### 第 6 步：切片与 A/B

优先使用这些 `fact_trades` 列做 filter/groupby：
- `trade_class`
- `strategy_id`, `code_version`, `execution_policy`, `sizing_mode`
- `city`, `city_pool`, `forecast_source`, `model_version`
- `side`, `target_date`, `order_date_bj`, `bracket`

M3 A/B：两个 selector 都从 `fact_trades` 过滤，分别聚合，再按同一切片键 join，输出 delta PnL / delta ROI / delta win_rate / delta active_days。

### 第 6.5 步：成交质量 / 机会 alpha（结合 fact_signal_candidates）

当问题涉及“成交质量、有没有漏单/漏赢家、滑点、城市真实 alpha（不只成交样本）”时，
**补一段机会粒度分析，读 `fact_signal_candidates`**（口径见 contract §1）。默认输出：

- **成交覆盖**：每 city/side 的 `eligible` 机会数、`paper_ordered`、`live_filled`，
  真实 live 覆盖率 = `live_filled / eligible`（**不要**用 paper 成交率冒充 live 成交率）。
- **滑点**：`AVG(slippage_vs_paper)`（负=成交价更便宜，对买方有利）。
- **漏掉的赢家**：`counterfactual_win`（`paper_ordered=0 AND win_by_count=1`）放弃的 `counterfactual_pnl`。
- **全机会 alpha vs 成交样本**：同一 city/side 把候选反事实（全 eligible）和 fact_trades 已成交并排，
  暴露执行选择偏差（成交样本好/坏是不是只是吃到了机会集的一个子集）。

```sql
-- 全 eligible 机会宇宙的城市/方向反事实 alpha（settled + 决策窗存在）
SELECT city, side, COUNT(*) n,
       SUM(paper_ordered) ordered, SUM(live_filled) live_fill,
       AVG(CAST(win_by_count AS REAL)) win_rate,
       SUM(counterfactual_pnl) cf_pnl
FROM fact_signal_candidates
WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0
GROUP BY city, side ORDER BY cf_pnl DESC;
```

机会粒度的硬约束（必须在报告里点明）：
- 反事实可用分母仅 `eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0`，样本通常很小 → 弱结论。
- `decision_window_missing` 占比要单列（当前 T-22~24h 带内缺失可达 ~46%，系统性缺口）。
- `paper_ordered` 是全池 paper ledger，不是 live 意图；`missed_fill` 大多是 paper≠live 设计差异，不是执行漏单。

### 第 7 步：报告与保存

正式报告写到：

```text
docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md
```

报告必须包含：
- 数据快照
- 数据完整性自检
- 目标指标与分母说明
- 总览
- 关键切片
- 交易动作建议：保留 / 过滤 / 降 size / shadow / 不改 live
- 残余风险：样本量、unsettled、trade_class 混用风险；若用了 fact_signal_candidates，必须点明
  `decision_window_missing` 占比（反事实只覆盖另一半机会）与 `paper_ordered ≠ live 意图`（勿把 paper 成交率当 live 成交率）

只有用户要求提交时才 commit。
