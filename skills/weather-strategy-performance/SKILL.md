---
name: weather-strategy-performance
description: >
  分析 weather 策略的历史绩效（PnL / ROI / win_rate / 多维切片），或对比两个策略/参数版本的 A/B 表现。
  触发词：绩效、PnL、ROI、win rate、胜率、切片、对比策略、A/B、回测结果、策略表现、历史表现、收益分析。
  禁止：在不读 contract 的情况下写一次性分析脚本；自定义指标；绕过 weather.db 直接用 pandas 跑 raw 文件。
---

# weather-strategy-performance

分析 weather 策略聚合绩效。两个子模式：
- **M1 单跑绩效切片**：指定时间窗 + 策略，输出多维切片报告
- **M3 A/B 对比**：两个 selector 的双栏对比报告

---

## 执行 Checklist（必须按顺序完成，不得跳步）

### 第 0 步：强制前置——读 contract（不得跳过）

读 `docs/WEATHER_ANALYSIS_CONTRACT.md` 全文，确认理解：
- §0 通用规约（禁止清单 + 报告头必填项 + 数据源优先级）
- §2 PnL / win_rate 定义（含 plan 口径 vs fill 口径、未结算三估值）
- §5 切片维度白名单

**未读 contract 不得继续任何分析动作。**

---

### 第 1 步：确认分析参数

与用户确认（若用户已在 prompt 里提供则直接使用）：

| 参数 | 说明 | 默认值 |
|---|---|---|
| mode | M1 单跑 / M3 A/B | 必须由用户指定 |
| date_start / date_end | 时间窗（北京时间，YYYY-MM-DD） | 必须由用户指定 |
| strategy_id | 策略 ID，或 "all" | "all" |
| city_pool | t1_trading / t2_research / all | t1_trading |
| data_source | db / api / mirror / n100raw | db |
| M3 专用：selector_A, selector_B | 对比维度（strategy_id / code_version / city_pool / 时段） | 必须由用户指定 |

---

### 第 1.5 步：强制数据同步（不得跳过）

每次分析前必须先从 N100 拉取最新数据并重建 DB：

```bash
# 从 N100 同步最新 paper ledger / snapshot CSV
scripts/ops/sync_weather_remote.sh

# 重建 weather.db（ingest CSV → DB）
scripts/weather_dashboard/run_stack.sh --no-rebuild
```

> `--no-rebuild` 表示不重建 schema（schema 已存在），只重跑 ingest。
> 若 DB 不存在或 schema 有变，去掉 `--no-rebuild`。
>
> 如果 N100 不可达（SSH 超时），在报告"数据快照"段说明，并注明使用的是本地缓存数据及缓存时间。

---

### 第 2 步：数据源选择（按 contract §0 优先级）

**优先 DB：**
```bash
# 确认 DB 存在且时间戳新鲜（同步后应刚刚更新）
ls -la runtime/weather_edge_v1/weather.db
```

**降级到镜像 JSON（DB 不可用时）：**
```bash
python3 -c "
import json
d = json.load(open('runtime/weather_edge_v1/market_data/research/t24_paper_ledger_summary.json'))
print('generated_at:', d.get('generated_at'))
print('overall:', d.get('overall'))
"
```

**降级到 Dashboard API（前两者均不可用时）：**
```bash
curl -s http://localhost:8000/api/runs | python3 -m json.tool | head -20
```

在报告"数据快照"段注明使用了哪一级，以及原因（若非首选）。

---

### 第 3 步：数据完整性自检

```python
import sqlite3

conn = sqlite3.connect("runtime/weather_edge_v1/weather.db")

total = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]

unsettled = conn.execute("""
    SELECT COUNT(*) FROM fills f
    JOIN orders  o   ON o.execution_id = f.execution_id
    JOIN plans   p   ON p.plan_id      = o.plan_id
    JOIN signals sig ON sig.signal_id  = p.signal_id
    WHERE NOT EXISTS (
        SELECT 1 FROM settlements s
        WHERE s.target_date = sig.target_date
          AND (s.condition_id = sig.condition_id OR s.market_id = sig.market_id)
          AND s.settlement_status = 'settled'
    )
""").fetchone()[0]

mb = conn.execute(
    "SELECT COUNT(*) FROM settlements WHERE settlement_status = 'missing_bracket'"
).fetchone()[0]

print(f"total={total}, unsettled={unsettled} ({100*unsettled/max(total,1):.1f}%), missing_bracket={mb}")
conn.close()
```

---

### 第 4 步：计算指标

使用 contract §2 的 SQL 查询。**两种口径都要算：`pnl_usd_at_fill` 和 `pnl_usd_at_plan`**。

M3 A/B：对 selector_A 和 selector_B 各跑一次 §2 SQL，按切片键 join，逐列算 delta 和 delta%。

按 contract §5 白名单逐一做切片：by_date / by_city / by_model / by_side / by_pool。  
**不准使用白名单之外的切片维度。**

---

### 第 5 步：填写报告模板

- M1 → `docs/analysis/templates/performance.md`
- M3 → `docs/analysis/templates/performance-compare.md`

严格按模板 H2 顺序填写，不得删除段落，不得改字段顺序。  
模板里的 `{placeholder}` 全部替换为实际值。

输出路径：`docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md`

---

### 第 6 步：Git commit

```bash
git add docs/analysis/YYYY-MM/
git commit -m "analysis: performance report <topic> <date_range>"
```
