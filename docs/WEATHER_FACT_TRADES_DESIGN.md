# Weather Fact Trades 底表设计

Status: design-draft
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; draft/design reference, not current production fact

> 目的:消除"每次分析各写各的取数+派生逻辑"导致的口径漂移。
> 建立**已成交交易绩效分析的唯一物化底表 `fact_trades`**——每笔成交(fill)一行、
> 所有维度和指标预先算死,绩效分析只许 `filter + groupby`,禁止再碰原始源。
>
> **范围边界(重要)**:`fact_trades` 的 grain 是 **fill(已成交)**,只服务"实际成交了的交易"绩效分析。
> counterfactual / missed-signal / capture-window / snapshot candidate replay 这类**未成交候选**分析
> **不属于本表**,需另建候选事实表 `fact_signal_candidates`(见 §8)。不要把未成交候选塞进 fill 表混淆 grain。
>
> 状态:设计已定稿,待实现。
> 关联:[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)(口径)、[WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md)(管道)

---

## §0 背景:为什么需要底表

contract 是纸面口径,但没有一个物化的派生层强制执行,导致同一份数据在不同脚本里算出不同结果:

1. **公式漂移(实锤)**:`weather_live_full_research.py:241` 的 BUY_NO 用 `(1-final)-fill`,
   而 contract §2.1 写的是 `fill-final`,两者不等价。用 N100 生产 ledger 651 笔已结算 BUY_NO
   对账,**8/8 命中 `(1-final)-fill`**,证明 contract §2.1 公式错误、脚本/生产正确。
2. **数据源漂移**:`weather_city_pool_contribution_analysis.py` 读 DB+snapshot 现算,
   `weather_live_full_research.py` 读 ledger CSV,口径不同。
3. **DB 路径漂移(已修)**:曾有两个 `weather.db`(`runtime/weather.db` 真库 17MB /
   `runtime/weather_edge_v1/weather.db` 空库),不同脚本指向不同库,反复误报"DB 是空的"。
   空库已删,所有脚本统一指向 `runtime/weather.db`。

底表把"派生"收敛到**一个 builder 脚本**,从根上消除以上三类漂移。

---

## §1 设计决策(已拍板)

| 决策 | 选择 | 依据 |
|---|---|---|
| 派生层位置 | 方案 A:builder 从 DB 规范化表派生 | DB 已是规范化事实源,单点派生最干净 |
| 颗粒度 | 每 fill 一行,**含未结算** | 未结算行 pnl 留空;估值列见下 |
| 存储 | **双写**:`weather.db.fact_trades` 表 + `fact_trades.parquet` | DB 给 API/前端,Parquet 给离线 pandas |
| 覆盖范围 | live / paper / snapshot_replay **全收**,保留原始 status 列 + 派生 `trade_class` | `runs.execution_mode` 实测有三值;fills 有 filled/simulated 两态。**不能只 paper/live**,否则 simulated 混进 live 实绩 |
| BUY_NO 公式 | `((1 − final_yes) − price) × shares − fees`(公式 B) | N100 生产 ledger 651 笔对账验证 |
| 未结算估值 | Phase 1 估值列留空;**Phase 1.5** 单独做 valuation join | DB 无 snapshot 表,估值需读 snapshot JSON,不属"纯 DB 派生",拆开避免污染核心 builder |
| 唯一 DB 路径 | `runtime/weather.db` | 删空库后强制规约 |

---

## §2 数据来源与 join 路径

grain = 一个 `fills` 行。沿规范化表展开:

```
fills (grain)
  → orders        ON orders.execution_id = fills.execution_id
  → plans         ON plans.plan_id       = orders.plan_id
  → signals       ON signals.signal_id   = plans.signal_id
  → runs          ON runs.run_id         = orders.run_id        # 取 source/code_version
  → strategy_config ON strategy_config.config_id = runs.config_id  # 取 strategy_name/params
  → settlements   ON settlements.token_id = signals.token_id    # 精确到 bracket 的 final_price
       fallback:  ON settlements.target_date = signals.target_date
                 AND (settlements.condition_id = signals.condition_id
                  OR  settlements.market_id    = signals.market_id)
                 AND settlements.bracket = signals.bracket
```

**settlement join(token-first + 审计 + 确定性去重)**:

实测覆盖率:fill 背后 1529 个 distinct signal,只有 **167** 个有 `token_id`,settled 行 final_price 严格 ∈ {0,1}。
所以 token-first 命中很少,大量靠 fallback。**因此"token_id 没命中" 不是强告警**,只记录命中方式。

- 优先 `signals.token_id = settlements.token_id`,直接命中具体 bracket 的 `final_price`;
- token 缺失 → 降级 contract 的 condition_id/market_id + bracket 组合;
- fallback 的 OR join(condition_id OR market_id)必须**确定性去重**:取唯一 settlement(如按 settlement_id 排序取首条),保证**一笔 fill 只出一行**;
- 三类审计列入表(见 §3.6):`settlement_join_method` ∈ {token, fallback, none}、`settlement_id`、`settlement_match_count`。

fill 入选条件:`fills.status IN ('filled','simulated')`。被拒的 live 下单(JSONL status=error)不进 fills,自然不入底表。
`filled` vs `simulated` 通过 §3.2 的 `fill_status` 列保留,并参与 `trade_class` 派生。

---

## §3 fact_trades 列定义

> 设计原则:**每个切片维度 = 一列**(groupby 即可),**每个指标预算好**(不在分析时现算)。
> 扩展方式:新增切片 = 已是列,直接 groupby;新增指标 = builder 加一列。

### 3.1 主键 / 血缘键

| 列 | 来源 | 说明 |
|---|---|---|
| `fill_id` | fills.fill_id | 主键,grain |
| `execution_id` | fills.execution_id | → orders |
| `order_id` | fills.order_id | |
| `plan_id` | orders.plan_id | |
| `signal_id` | plans.signal_id | 血缘根 |
| `run_id` | orders.run_id | |
| `config_id` | runs.config_id | = strategy_id |

### 3.2 策略身份 / 来源维度

| 列 | 来源 | 说明 |
|---|---|---|
| `execution_mode` | runs.execution_mode | 原始值:live / paper / snapshot_replay |
| `fill_status` | fills.status | 原始值:filled / simulated |
| `order_status` | orders.status | 原始值 |
| `trade_class` | 派生 | **核心切片**:`live_real`(live+filled)/ `live_simulated`(live+simulated)/ `paper` / `snapshot_replay`。统计 live 实绩只取 `live_real`,杜绝 simulated 混入 |
| `strategy_id` | runs.config_id | 策略唯一标识(contract §4) |
| `strategy_name` | strategy_config.name | 可读名 |
| `code_version` | runs.code_version | A/B 子维度 |
| `execution_policy` | plans.execution_policy | mid_price_core_v1 / maker_queue 等 |
| `sizing_mode` | plans.sizing_mode | notional / fixed_shares 等 |
| `venue` | orders.venue | polymarket_clob 等 |
| `producer_system` | runs.producer_system | 血缘/排查 |
| `producer_run_id` | runs.producer_run_id | 血缘/排查 |
| `universe_id` | runs.universe_id | 城市池版本血缘 |

### 3.3 市场 / 信号维度

| 列 | 来源 | 说明 |
|---|---|---|
| `city` | signals.city | by_city |
| `city_pool` | signals.city_pool | t1_trading / t2_research,by_pool |
| `icao` | signals.icao | |
| `target_date` | signals.target_date | 结算日(北京日),by_date |
| `bracket` | signals.bracket | 温度档 |
| `unit` | signals.unit | |
| `side` | orders.order_side | BUY_YES / BUY_NO,by_side |
| `signal_side` | signals.signal_side | 信号方向原值;与 `side` 交叉校验,不一致**告警** |
| `forecast_source` | signals.forecast_source | ecmwf / gfs,by_model |
| `model_version` | signals.model_version | |
| `condition_id` / `market_id` / `token_id` | signals | join / 排查用 |
| `market_key` | 派生 = `market_id|bracket` | 单市场聚合键(DB 无此列,统一派生) |
| `city_day_key` | 派生 = `city|target_date` | 城市/日组合键,供组合优化器直接 groupby |

### 3.4 时间维度

| 列 | 来源 | 说明 |
|---|---|---|
| `order_ts_utc` | orders.created_at_utc | 下单时间 |
| `order_date_bj` | 派生 | 下单时间转 `Asia/Shanghai` 的日期,**跨日订单归属**(contract §3) |
| `order_date_local` | 派生 | 转城市 timezone 的日期 |
| `fill_ts_utc` | fills.filled_at_utc | 成交时间 |
| `snapshot_ts_utc` | signals.snapshot_ts_utc | 信号快照时间 |
| `hours_to_settle` | signals.hours_to_settle | |

### 3.5 价格 / 数量指标

| 列 | 来源 | 说明 |
|---|---|---|
| `model_p_yes` | signals.model_p_yes | 模型 YES 概率 |
| `market_price` | signals.market_price | 信号时市场 YES 价 |
| `edge` / `abs_edge` | signals | |
| `plan_price` | orders.entry_price | 计划入场价 |
| `limit_price` | orders.limit_price | |
| `entry_price_window` | plans.entry_price_window | 入场价窗口 |
| `fill_price` | fills.filled_price | 实际成交价 |
| `fill_qty` | fills.filled_shares | 实际成交量 |
| `desired_shares` | plans.desired_shares | 计划量 |
| `order_shares` | orders.shares | 下单量 |
| `fees_usd` | fills.fees_usd | |
| `cost_usd` | 派生 = `fill_price × fill_qty` | 实际成本,**重算不信 orders.cost_usd** |
| `cost_usd_at_plan` | 派生 = `plan_price × fill_qty` | 计划成本,作 `pnl_usd_at_plan` 的 ROI 分母,口径统一 |
| `notional` | orders.notional | 计划名义额 |

### 3.6 结算 / 盈亏指标(公式 B 钉死)

**硬规定:`final_yes` / 所有 pnl / win 列,仅在 `settled = true` 时填值,否则一律 NULL。**
2026-06-06 口径勘误：`pm_history` raw `final_price` 常见 `0.9995/0.0005` 这类 near-binary 值，必须在 ingest/builder 归一化为已结算 `1/0` 后再计算 PnL。旧版“非精确 1/0 一律 missing_bracket”的规则已废弃。

| 列 | 公式 / 来源 | 说明 |
|---|---|---|
| `settled` | settlement_status = 'settled' | bool |
| `settlement_status` | settlements.settlement_status | settled / missing_bracket 等 |
| `settlement_id` | settlements.settlement_id | 审计:命中哪条结算 |
| `settlement_join_method` | 派生 | 审计:token / fallback / none |
| `settlement_match_count` | 派生 | 审计:join 命中条数(>1 说明 fallback 有歧义,已去重) |
| `final_yes` | settlements.final_price normalized | 仅 settled 填；builder 输出严格 ∈ {0,1}，但 upstream pm_history raw 可能是 0.9995/0.0005 |
| `pnl_usd_at_fill` | BUY_YES: `(final_yes − fill_price)×qty − fees`<br>BUY_NO: `((1−final_yes) − fill_price)×qty − fees` | 仅 settled,否则 NULL |
| `pnl_usd_at_plan` | 同上,price 换 `plan_price` | 仅 settled,否则 NULL |
| `win_by_count` | `pnl_usd_at_fill > 0` | 仅 settled,否则 NULL |
| `contract_won` | `final_yes == 1` | 该合约 YES 是否中签(仅 settled) |
| `bracket_hit` | 该笔 side 是否押中结果:BUY_YES→`final_yes==1`,BUY_NO→`final_yes==0` | 仅 settled |

### 3.7 未结算估值(open position)— **Phase 1.5,Phase 1 留空**

DB 无 snapshot 表,估值需读 snapshot JSON,不属"纯 DB 派生"。Phase 1 这些列建表即留 NULL,
Phase 1.5 单独实现 valuation join,避免污染核心 builder 的纯 DB 派生属性。

数据源(Phase 1.5 钉死):`runtime/weather_edge_v1/market_data/paper_snapshots/snapshot_*.json`,
取**最新** snapshot,按 `token_id`(缺失则 `market_id`+`bracket`)匹配该笔的盘口。

| 列 | 来源 | 说明 |
|---|---|---|
| `val_mid` / `val_bid` | 最新 snapshot 盘口 | 仅未结算行填,已结算 NULL |
| `val_last_fill` | 该 market 最近一笔 fill_price | |
| `unrealized_pnl_mid` | 用 val_mid 代入公式 B | `[UNSETTLED]`,不计入已结算总览 |
| `val_snapshot_ts_utc` | 估值用 snapshot 时间 | 写入报告"数据快照"头 |

### 3.8 构建元数据

| 列 | 说明 |
|---|---|
| `fact_built_at_utc` | 本行物化时间 |
| `src_snapshot_ts_utc` | 估值用的 snapshot 时间(用于"数据快照"报告头) |

---

## §4 builder:`scripts/analysis/build_weather_fact_trades.py`

唯一派生层。职责:

1. 连 `runtime/weather.db`,按 §2 join 路径拉全字段(Phase 1 不含 §3.7 估值)
2. 按 §3 计算派生列:`trade_class`、日期转换、cost/cost_at_plan 重算、公式 B 盈亏(仅 settled)、settlement 审计列、market_key/city_day_key
3. settlement fallback OR-join 确定性去重,保证一笔 fill 一行
4. **告警分级**(不静默兜底,符合 CLAUDE.md 工程姿态):
   - 强告警:`side` 与 `signal_side` 不一致;settled 行 `final_yes` 非 {0,1};fallback join 去重前命中 >1
   - 仅记录不告警:`settlement_join_method = none`(token 覆盖本就低,记 method 即可)
5. 双写:
   - `weather.db` 的 `fact_trades` 表(DROP + CREATE + INSERT,幂等重建)
   - 导出 `runtime/weather_edge_v1/market_data/research/fact_trades.parquet`
6. 打印构建摘要:总行数、各 `trade_class` 行数、settled/unsettled 占比、各 join_method 计数、告警计数

CLI:
```bash
python3 scripts/analysis/build_weather_fact_trades.py            # 双写
python3 scripts/analysis/build_weather_fact_trades.py --dry-run  # 只算不写,打摘要
```

---

## §5 接入与消费规约

1. **接入 run_stack.sh**:ingest 之后自动跑 builder,DB 重建即底表重建。
2. **消费规约写进 contract §1**:
   - `fact_trades`(DB 表 / parquet)是分析的**强制唯一取数源**
   - 分析只许 `filter + groupby`,**禁止**再从 signals/orders/fills/snapshot 现算指标
   - DB 路径只许 `runtime/weather.db`
3. **修正 contract §2.1**:BUY_NO 公式改为公式 B,附对账结论。

---

## §6 实现计划(分阶段)

### Phase 1 — builder + 底表(核心,纯 DB 派生)
- [ ] 写 `build_weather_fact_trades.py`,实现 §2 join + §3(除 3.7)全列派生 + §4 双写
- [ ] 估值列(§3.7)建表即留 NULL
- [ ] 单测:用真库抽样,验证 §3.6 盈亏对得上 N100 ledger(BUY_NO 公式 B、BUY_YES);验证一笔 fill 一行无重复
- [ ] `--dry-run` 跑通,打印摘要,告警计数为可解释值

### Phase 1.5 — 未结算估值 join
- [ ] 从最新 snapshot JSON 按 token_id/market_id+bracket 填 §3.7 估值列
- [ ] builder 加 `--with-valuation` 开关,默认开

### Phase 2 — 接入与规约
- [ ] `run_stack.sh` ingest 后调用 builder
- [ ] 更新 `WEATHER_ANALYSIS_CONTRACT.md`:§1 加 fact_trades 唯一源 + DB 路径规约,§2.1 修正公式
- [ ] 文档索引(CLAUDE.md)登记本设计文档

### Phase 3 — 迁移现有消费方(按收益/正确性排序)
- [ ] **①** `weather_live_full_research.py` → 读 fact_trades(本就 fill-grain,收益最大)
- [ ] **②** `weather_dashboard/metrics/calc.py` → 读 fact_trades(共享指标层,API/脚本都依赖它,不迁则继续各算各的)
- [ ] **③** `weather_city_day_portfolio.py` → 读 fact_trades(依赖已补的 city_day_key / bracket_hit / contract_won / model_p_yes / edge)
- [ ] 迁移后用旧报告复算对账,数字一致才算完成
- [ ] **暂缓**:`weather_city_pool_contribution_analysis.py`、`weather_window_capture_performance.py` —— 经代码确认它们是 **snapshot candidate replay(未成交候选)**,grain 与 fill 不同,硬迁会混淆 grain;留给 §8 的 `fact_signal_candidates`

### Phase 4 — 可选扩展(YAGNI,遇到再做)
- [ ] dashboard API 暴露 `/api/fact_trades` 切片端点
- [ ] 前端通用切片透视页

---

## §7 为什么这样设计满足"好统计 / 好扩展"

- **好统计**:完全展平,任意切片(by_date/city/model/side/pool/policy/source/code_version)
  都是现成列,`GROUP BY` 或 pandas `groupby` 一行搞定;指标已预算,不会再现算出不同口径。
- **好扩展**:
  - 新切片维度 → 大概率已是列,直接用;若确需新列,只在 builder 加一处。
  - 新指标 → builder 加一列,全项目自动获得统一口径。
  - 新数据源(如未来加真实 CLOB orderbook 入场价)→ builder 加 join,不动下游分析。
- **唯一真相**:派生只此一处;contract 从"纸面口径"升级为"被 builder 强制执行的口径"。

---

## §8 边界外:`fact_signal_candidates`(未来另立,不在本 spec 实现)

`fact_trades` 只装**已成交**(fill-grain)。以下分析的 grain 是**信号/候选/盘口窗口**,不是 fill,
强行塞进 fact_trades 会混淆 grain,因此另立第二张候选事实表(后续单独 spec):

- counterfactual / missed-signal:有信号但没成交(skip_reason / 未 fill)的反事实绩效
- snapshot candidate replay:`weather_city_pool_contribution_analysis.py` / `weather_window_capture_performance.py` 现在做的事
- capture-window:盘口窗口捕获率分析

grain 候选:每 signal(或 signal×snapshot)一行,带 skip_reason、是否成交、候选估值。
两表通过 `signal_id` 关联,各自 grain 清晰。本 spec **不实现**它,只标明边界。
