# Entry Timing Rigorous Research Plan - 2026-06-08

## 目标问题

用户观察：`<T-22` 的近期效果也不强，不能只把 `>T-28` 当成唯一坏窗口。

本研究要回答的不是“哪个 timing 桶历史 PnL 最高”，而是：

> `entry_timing_effect` = 在同一策略身份、同一城市池、同一机会定义下，`hours_to_settle` 对机会可成交性、真实成交收益、forecast stale 风险、market adverse move 和 city-day 组合 tail 的独立影响。

关键交易问题：

1. `T-22-24` 是否仍是唯一可保留的主入场窗口？
2. `<T-22` 是模型 edge 失效，还是 live 可成交样本选择/流动性/side mix 导致亏损？
3. `T-24-26`、`T-26-28`、`>T-28` 应该分别是 keep、shadow、drop 还是 conditional keep？
4. timing rule 是否应按 `strategy_instance`、`model_version`、`city`、`side`、`entry_price`、`forecast_run_age` 分层，而不是全局硬切？

## 当前证据边界

这是一份研究计划，不新增 settled PnL 结论。当前计划只引用既有快照的方向性证据：

- `2026-05-29-entry-timing-edge.md`：旧口径里 `<T-22` BUY_NO 已成交样本表现较好，但分母混合 paper/live/早期状态，且 `<T-22` fill rate 很低。
- `2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md`：6 月后 v1 live_real settled 中 `<T-22` 为负，`T-22-24` 明显最好，`>T-28` 和 `T-26-28` 均为坏窗口；但严格 matched cell 不足。
- `2026-06-08-side-band-entry-timing-impact.md`：side-band 的 timing 形态不同，`<T-22` 也为负，但 `>T-28` 不能简单套用 v1 的 drop 结论。
- `2026-06-08-weather-edge-v2-filtered-operational-base-research.md`：近期 regime 下 raw/blend/basket 多数规则都弱，timing 研究必须和 operational-base filter、city/model 降级一起看。

因此当前最重要的修正是：**不要再用“5 月旧全量已成交样本 `<T-22` 还行”推导 live 规则；也不要用“6 月 v1 `<T-22` 亏”直接证明模型晚期失效。**

## 强制数据前置

执行任何新数字结论前，必须按 `docs/WEATHER_ANALYSIS_CONTRACT.md` 重新做：

```bash
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh
python3 scripts/analysis/weather_clob_fill_coverage_gate.py
```

报告头必须包含 5 行 SQL 自检：

```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates;
SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
  FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status;
```

`gate_pass=false` 时停止发布 live_real PnL/ROI，只能写数据缺口。

## 分母分层

所有表必须明确属于哪一层，不能混用。

| Layer | 分母 | 用途 | 禁止解释 |
|---|---|---|---|
| L0 opportunity | `fact_signal_candidates` 中 settled 且 decision window available 的机会 | 判断模型/market edge 是否存在 | 不能当成真实成交 PnL |
| L1 eligible opportunity | L0 中通过策略 gate 的机会 | 判断规则选择质量 | 不能解释 fill cost |
| L2 live-submitted | raw live order / `orders` 中实际提交的订单 | 判断策略是否真的打算交易 | 不能当成已花钱 |
| L3 live-filled | `fact_trades.trade_class='live_real'` | 真实成交收益、现金风险 | 不能和 paper 混合 |
| L4 city-day portfolio | `(target_date, city, strategy_instance, timing_bin)` 聚合 | 防止多腿同日重复放大 | 不能替代逐 fill fill-cost 对账 |

`<T-22` 必须至少同时给 L0/L1/L3/L4 四层结果。若 L0 好但 L3 差，优先解释为成交选择/流动性/side mix/late move 问题，而不是直接说模型失效。

## Timing 桶定义

第一轮使用固定桶，便于和既有报告对齐：

| bin | 条件 |
|---|---|
| `<T-18` | `hours_to_settle < 18` |
| `T-18-20` | `18 <= hours_to_settle < 20` |
| `T-20-22` | `20 <= hours_to_settle < 22` |
| `T-22-24` | `22 <= hours_to_settle <= 24` |
| `T-24-26` | `24 < hours_to_settle <= 26` |
| `T-26-28` | `26 < hours_to_settle <= 28` |
| `>T-28` | `hours_to_settle > 28` |

第二轮做候选 cut sweep：

- lower cut: `18, 20, 21, 22, 23`
- upper cut: `24, 25, 26, 27, 28`
- policy candidates: `only_22_24`, `only_22_26`, `only_20_24`, `drop_lt22`, `drop_gt26`, `drop_gt28`, `conditional_lt22_no_only`, `conditional_22_26_by_model`

所有 sweep 必须用 walk-forward 或 leave-date-out 评估，不能只报全样本最优。

## 阶段 1：现状复核

目标：确认 `<T-22` 近期变差是不是数据/口径问题。

输出：

1. `strategy_instance x timing_bin`：fills、city_days、settled_cost、pnl、ROI、win_rate、open_cost。
2. `period x strategy_instance x timing_bin`：`pre_2026_06_01`、`post_2026_06_01`、`holdout_from_2026_05_26`。
3. `trade_class x timing_bin`：live_real、paper、snapshot_replay 分开，禁止混合 headline。
4. `settlement_status x timing_bin`：确认 `<T-22` 是否因未结算占比偏高而被误判。

通过标准：

- fill coverage gate 通过；
- `<T-22` 在 live_real 和 city-day 两个口径方向一致；
- 如方向不一致，报告必须先解释重复腿/城市日集中度。

## 阶段 2：机会层 vs 成交层拆解

目标：判断 `<T-22` 是 edge 本身差，还是成交选择差。

表格：

1. L0 opportunity timing 表：`n, cf_cost, cf_pnl, ROI, model_accuracy, avg_abs_edge, avg_entry_price`。
2. L1 eligible timing 表：同上，并拆 gate pass rate。
3. L2 submitted timing 表：submitted orders、posted notional、submitted-to-eligible rate。
4. L3 filled timing 表：fills、fill rate、fill_price、slippage、realized PnL。
5. L0 -> L3 conversion 表：每个 timing bin 的 `eligible -> submitted -> filled -> settled` 漏斗。

判读规则：

- L0 好、L3 差：研究 fill selection、late liquidity、market adverse move。
- L0 差、L3 差：timing 本身应进入 hard drop 候选。
- L0 差、L3 好：可能是成交选择过滤了坏机会，不可放大 size。
- L0 好、L3 好：才允许考虑扩大该 timing 桶。

## 阶段 3：控制变量 matched analysis

目标：减少 city/model/side mix 误导。

最小 matched key：

```text
strategy_instance
city
side
model_version
entry_price_bin
raw_edge_bin
period
```

降级顺序：

1. `city + side + model_version + entry_price_bin + raw_edge_bin`
2. `city + side + model_version`
3. `side + model_version + entry_price_bin + raw_edge_bin`
4. `side + model_version`

每个 matched 表必须报告：

- matched cells；
- matched fills / opportunities；
- 每个 timing bin 的 ROI；
- `T-22-24 - other_bin` 的 bootstrap delta；
- 若 strict matched cells 太少，明确降级为相关性证据。

## 阶段 4：机制归因

目标：解释为什么某个 timing 桶亏，而不是只给分桶 PnL。

给每个 fill / opportunity 补以下 lineage 特征：

| 字段 | 含义 |
|---|---|
| `entry_model_run_age_hours` | 入场时 forecast run 已老化多久 |
| `next_model_run_available_before_settle` | 结算前是否还有下一轮 forecast |
| `forecast_jump_after_entry_f` | 入场后同 city/bracket forecast 最大变化 |
| `side_flip_after_entry` | 入场后同机会是否出现 BUY_YES/BUY_NO 翻转 |
| `worst_side_market_delta` | 入场后同 side implied price 最不利移动 |
| `best_side_market_delta` | 入场后同 side implied price 最有利移动 |
| `entry_local_hour` | 城市本地入场小时 |
| `snapshot_age_minutes` | 使用 snapshot 相对 cycle 的年龄 |

机制表：

- timing bin x mechanism flag；
- loss fills 中各 flag 覆盖率；
- flag 之间重叠矩阵；
- leave-one-city/date 后机制是否仍成立。

特别检查 `<T-22`：

- 是否集中在 BUY_YES；
- 是否集中在少数城市如 LA/Miami/NYC；
- 是否是 late catch-up / dedup 漏洞造成的非标准样本；
- 是否入场后 market 已经明显 adverse；
- 是否 forecast run age 比 `T-22-24` 更旧。

## 阶段 5：city-day portfolio 口径

目标：避免同一个 city-day 多腿把统计显著性放大。

聚合 key：

```text
target_date
city
strategy_instance
timing_bin
```

输出：

1. city-day ROI / PnL distribution；
2. top-1 / top-3 / top-5 worst city-day removed ROI；
3. CVaR20；
4. win city-day rate；
5. pure YES / pure NO / mixed basket 拆分；
6. same city-day 多 timing 同时入场时的 attribution。

交易动作必须以 L4 作为主依据，L3 逐 fill 只做细节解释。

## 阶段 6：policy simulation

目标：把研究转成候选 live rule，但先只做 no-replacement overlay。

候选：

| policy | 规则 |
|---|---|
| baseline_current | 当前 live 规则 |
| only_T22_24 | 只保留 `T-22-24` |
| only_T22_26 | 保留 `T-22-24` + `T-24-26` |
| drop_lt22 | 去掉 `<T-22` |
| drop_gt26 | 去掉 `T-26-28` + `>T-28` |
| drop_lt22_gt26 | 只保留 `T-22-26` |
| conditional_lt22_no_only | `<T-22` 只保留 BUY_NO 且 edge/price 达标 |
| conditional_by_instance | v1 / side-band 分别设 timing 规则 |
| conditional_by_model | ECMWF / GFS 分别设 upper/lower cut |

每个 policy 输出：

- kept/dropped fills；
- kept/dropped city-days；
- kept/dropped cost；
- realized PnL delta；
- missed positive PnL；
- avoided loss；
- top5-removed ROI；
- CVaR20；
- positive folds；
- by-date walk-forward 表现。

不做替代成交假设；如果后续要做资金再部署，另开研究。

## 阶段 7：显著性与稳定性门槛

候选 rule 进入 shadow 的最低门槛：

- `post_2026_06_01` 和 `holdout_from_2026_05_26` 不同时恶化；
- city-day bootstrap `delta_roi` 正概率 `>=70%`；
- leave-one-date 后不因单日翻负到不可接受；
- leave-one-city 后不因单城翻负到不可接受；
- top5-worst removed ROI 不比 baseline 更差；
- dropped bucket 的 avoided loss 大于 missed gain；
- `<T-22` 若要保留，必须在 L0/L3/L4 中至少两层为正，且机制上不是单日/单城驱动。

候选 rule 进入 live/canary 的最低门槛：

- 先 shadow 一周或至少新增 5 个 settled target days；
- CLOB coverage gate 通过；
- live_filled_only 和 opportunity 层方向一致；
- L4 city-day ROI 和 CVaR20 优于 baseline；
- 不扩大 total daily notional，只替换 timing gate；
- rollback 条件明确：连续 2 个 settled target days 低于 baseline，或累计 live_real delta PnL 低于 `-$50`，或 CLOB gate 失败。

## 阶段 8：工程化落地

建议新增一个脚本：

```text
scripts/analysis/research_weather_entry_timing_rigorous.py
```

输出：

```text
docs/analysis/2026-06/YYYY-MM-DD-entry-timing-rigorous-research.md
docs/analysis/2026-06/YYYY-MM-DD-entry-timing-rigorous-research.json
```

脚本要求：

- 只读 `runtime/weather.db` 和 synced paper snapshots；
- realized PnL 只读 `fact_trades.pnl_usd_at_fill`；
- opportunity 只读 `fact_signal_candidates`；
- raw snapshots 只用于 lineage 特征，不自算 PnL；
- 所有 policy simulation 都标注 no-replacement；
- JSON 保留每张表原始 rows，便于后续 dashboard/审计。

后续可以把 lineage 字段物化到 `fact_signal_candidates` 或新 fact 表：

```text
fact_signal_timing_lineage
```

但第一版研究脚本可以先离线扫 raw snapshots，优先把结论跑通。

## 预期决策输出

最终报告必须给出明确动作之一：

| 动作 | 含义 |
|---|---|
| `keep_current` | timing rule 不改 |
| `shadow_drop_lt22` | `<T-22` 先 shadow/drop，不改 live |
| `shadow_only_T22_26` | 只保留 `T-22-26` 做 shadow 对照 |
| `canary_only_T22_26` | 小 size canary，仅在门槛全部通过后 |
| `conditional_timing_by_instance` | v1 / side-band 分别配置 timing |
| `conditional_timing_by_model_city` | 城市/模型条件化 timing，需额外防过拟合 |

当前先验不是“马上砍 `<T-22`”，而是：

> `<T-22` 已经不能被视为安全补仓窗口；下一轮研究要证明它是可条件保留，还是应该和 `>T-28` 一样从 live 主路径移出。
