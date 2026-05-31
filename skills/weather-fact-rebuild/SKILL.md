---
name: weather-fact-rebuild
description: >
  补全/重建 weather 分析底表（fact_trades + fact_signal_candidates）与刷新本机 weather.db。
  适用场景：数据陈旧、analysis 前要拉最新 N100 数据、近几天的 fill/结算没进底表、
  settled 口径漏掉未结算或 missing_bracket、要在更长样本上复跑研究脚本。
  触发词：补全底表、重建底表、刷新底表、同步数据、sync N100、数据陈旧、数据落后、
  重建 weather.db、fact_trades 旧了、fact_signal_candidates 旧了、重新结算、rebuild、resync。
  禁止：手搓单跑 build_weather_fact_trades.py / build_weather_signal_candidates.py（破坏链条顺序）；
  跳过 sync 直接重建（拿旧镜像）；跳过重建直接拿陈旧 weather.db 出分析结论；
  改 --decision-hts-min/max 等 builder flag 而不在文档说明原因。
---

# weather-fact-rebuild

把本机 `runtime/weather.db` 和两张授权底表刷新到 N100 最新真相。**底表是分析唯一授权派生层**
（口径见 `docs/WEATHER_ANALYSIS_CONTRACT.md`），任何 weather 绩效/血缘/敞口/研究在数据可能陈旧时，
都必须先走本 skill，再去 invoke 对应分析 skill。

## 为什么需要这条规范

- `weather.db` 是**可弃派生物**（CLAUDE.md：可随时删掉重建，不是源头）。真相在 N100 + 镜像 CSV。
- 重建是**幂等 + 内容寻址**的：重复跑不会重复计数，可反复执行。
- 但 **sync 和 rebuild 是两步**，且 rebuild 内部有**固定顺序**（先补结算、再 build 底表）。
  漏掉 sync → 拿旧镜像；单跑 build 脚本 → 底表基于未补结算的旧数据。两者都会得出错误结论。

## 硬规则：重建底表一律走这两条命令，按顺序，别手搓

```bash
# 第 1 步：拉 N100 镜像（先决条件，缺它 run_stack 会 warn "research CSVs missing"）
scripts/ops/sync_weather_remote.sh

# 第 2 步：全链路重建（幂等）。两张底表是链条末端自动产出，不要单独跑 build_*.py
scripts/weather_dashboard/run_stack.sh
```

桌面端在 Windows shell 时，每条命令包一层：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && scripts/ops/sync_weather_remote.sh"
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && scripts/weather_dashboard/run_stack.sh"
```

`run_stack.sh`（默认 `--rebuild`）内部固定顺序（**不要拆开手跑**）：

1. `db-canonical-rebuild`（内容寻址 ingest）
2. 迁移 legacy research CSV → 迁移 live-cycle 血缘
3. **pm_history 结算（权威）→ Polymarket API 回填缺失结算 → 同步真实 CLOB fills**
4. consolidate configs
5. **build `fact_trades`**（`scripts/analysis/build_weather_fact_trades.py`）
6. **build `fact_signal_candidates`**（`--decision-hts-min 22 --decision-hts-max 24`）
7. `metrics-refresh`

> 重建是第 1 阶段，起 API/FE 服务在后面。即使前端 node 起不来，底表也已经重建完成。
> 如果只想重建不在意服务，照样跑默认；不要为省事改成单跑 make/build 子步骤。

## 执行 Checklist

### 第 0 步：判断是否真的需要重建
- 看 `runtime/weather.db` mtime 与 `MAX(fact_built_at_utc)`；若用户问的是「最新/今天/最近几天」且缓存落后，则需要重建。
- 纯历史窗口分析且缓存已覆盖该窗口 → 可不重建，但必须在报告「数据快照」标明用的是哪天缓存。

### 第 1 步：sync + rebuild
- 长耗时，建议后台跑（`run_in_background`），完成后再继续分析；不要 sleep 轮询。
- 链路命令：
  ```bash
  scripts/ops/sync_weather_remote.sh && scripts/weather_dashboard/run_stack.sh
  ```

### 第 2 步：日志与失败排查
- 重建日志：
  - `runtime/_dashboard_logs/migrate_live_cycle.log`（迁移 + 结算 + build 底表）
  - `runtime/_dashboard_logs/migrate_legacy_research.log`
- 结算/CLOB 回填是 **non-fatal warn**（缺网络/凭证会跳过），但会导致底表仍缺最近结算 → 报告里要点明。
- build 失败是 **fatal**（脚本 exit）；必须修复后重跑，不要拿半成品底表出结论。

### 第 3 步：重建后自检（出分析前必做）
```sql
-- 新鲜度
SELECT MAX(fact_built_at_utc) FROM fact_trades;
-- trade_class 必须存在 live_real（见下方致命陷阱）
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
-- 结算覆盖（settled 的 target_date 真实覆盖到哪天）
SELECT trade_class,
       MAX(CASE WHEN settlement_status='settled' THEN target_date END) AS settled_to,
       SUM(settlement_status='settled') AS settled,
       SUM(settlement_status IS NULL) AS unsettled,
       SUM(settlement_status='missing_bracket') AS missing_bracket
FROM fact_trades GROUP BY trade_class;
```
- **未结算 + missing_bracket 不是 0 是常态**：最近 1–2 天的单往往还没结算。分析 realized PnL 时
  必须单列这两块（别像 settled-only 那样假装它们不存在），口径见 `WEATHER_ANALYSIS_CONTRACT.md`。

### ⚠️ 致命陷阱：`live_real` 是网络派生的，重建可能把它整个抹掉

`trade_class='live_real'` 的定义是 `execution_mode='live' AND fill_status='filled'`
（builder `_derive_trade_class`）。`filled` 这个状态**不是存量**，靠链路里的 `clob_fill_sync`
**每次重建时现拉 Polymarket activity/CLOB API** 匹配真实成交才打上。

这一步是 **non-fatal**：API 网络/SSL 失败时（`data-api.polymarket.com` 偶发
`SSL: UNEXPECTED_EOF`），它只 warn 不报错，但**所有 live 单退回 `live_simulated`，`live_real`
从重建后的 DB 里彻底消失**。因为 canonical rebuild 是从零重建，它会**连上一份缓存里好端端的
live_real 一起抹掉**。

**强制自检 + 恢复流程：**
1. 重建后第一件事：`SELECT COUNT(*) FROM fact_trades WHERE trade_class='live_real';`
   —— 若为 0（且你知道有真实 live 成交），**不要用这份 DB 出任何 live 战绩**。
2. 查日志确认是不是 clob_fill_sync 网络失败：
   ```bash
   grep -iE 'clob|activity|SSL|TRADE events' runtime/_dashboard_logs/migrate_live_cycle.log | tail
   ```
3. 测连通性：
   ```bash
   curl -sS -m 25 -o /dev/null -w 'http_code=%{http_code}\n' \
     'https://data-api.polymarket.com/activity?user=<FUNDER_ADDR>&limit=5&offset=0'
   ```
4. 通了就**幂等重跑** `scripts/weather_dashboard/run_stack.sh`（无需再 sync），live_real 会恢复。
   SSL 抖动通常是瞬时的，重跑即可。

### 第 4 步：交回分析
- 重建完成后再 invoke `weather-strategy-performance` / `-lineage` / `-exposure` 做分析。
- 报告「数据快照」必须写：DB mtime、`MAX(fact_built_at_utc)`、settled 覆盖到哪天、
  unsettled / missing_bracket 计数，以及**是否本次 sync 过**。

## 治理这条链的权威文档（改动前必读）

| 文档 | 管什么 |
|---|---|
| `docs/WEATHER_DATA_PIPELINE.md` | N100→镜像→DB→底表 全链路 + 每个脚本职责 + 运维 runbook |
| `docs/WEATHER_FACT_TRADES_DESIGN.md` | `fact_trades` 底表设计（builder 权威） |
| `docs/WEATHER_SIGNAL_CANDIDATES_DESIGN.md` | `fact_signal_candidates` 底表设计 |
| `docs/WEATHER_ANALYSIS_CONTRACT.md` | 分析口径唯一源 + 底表是唯一授权派生层 |

## 禁止事项（防止"其他窗口乱搞"）

- ❌ 单独手跑 `build_weather_fact_trades.py` / `build_weather_signal_candidates.py` 而不先跑前置的
  结算/CLOB 同步步骤 → 底表基于旧结算，错。
- ❌ 跳过 `sync_weather_remote.sh` 直接重建 → 拿旧镜像。
- ❌ 跳过重建直接拿陈旧 `weather.db` 给「最新战绩」结论。
- ❌ 改 builder flag（如 `--decision-hts-min/max`）不在文档/commit 说明原因和回滚时机。
- ❌ 手写/UPDATE 进底表 → 底表只能由 builder 产出（contract 规定）。
