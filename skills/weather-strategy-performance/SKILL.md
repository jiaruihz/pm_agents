---
name: weather-strategy-performance
description: 评估 weather 策略、模型、shadow/live probe 的 fee-adjusted PnL、ROI、胜率、概率质量、A/B、城市/来源/side 切片、成交质量、漏单与稳定性。用于绩效、回测结果、策略对比、alpha、最近 N 天表现。必须锁 grain 与同分母基准，区分 signal funnel/evidence funnel、research/shadow/live，按 target_date block bootstrap 并做 frozen forward；禁止从少量 selected trades 或 gross ROI 直接升 live。
---

# Weather strategy performance

目标是判断是否存在可重复的 market residual，不是寻找最高历史 ROI 切片。

## 先读

1. `AGENTS.md`
2. `docs/WEATHER_ANALYSIS_CONTRACT.md`
3. `docs/WEATHER_STRATEGY_REGISTRY.md`
4. 用户点名策略的 living report

新机制/新特征/新策略的研究设计先用 `weather-strategy-research`；已有策略的绩效与 A/B 用本 skill。

## 数据层

| 问题 | grain | 授权源 |
|---|---|---|
| 已成交绩效 | fill | `fact_trades` |
| 全机会 alpha / fill selection | opportunity | `fact_signal_candidates` |
| 概率/分布质量 | 固定 PIT state/label | canonical feature/model artifact + settlement source |
| 当前 order/fill 状态 | raw event/order/fill | 当前 Mac strategy runtime |
| 钱包现金流 | account | `weather-live-account-reconcile` |

不得把 opportunity replay 称为 actual fills，也不得用 fill 样本替代全机会分母。

## 第一步：冻结目标和分母

写成一句话：

```text
在 [PIT 窗口] 的 [固定 universe/grain] 上，比较 [candidate] 与 [market/same-denominator baseline]，
主指标为 [logloss/Brier 或 fee-adjusted ROI delta]，forward 只复核不调参。
```

必须声明：

- unit：event / city-day / state / expression / order / fill。
- `trade_class`：research replay、paper、shadow、live_real 分层。
- 时间：`target_date` 为策略归因；`fill_date_bj` 只用于现金流。
- settlement：realized 只含 settled；unsettled 单列。
- strategy identity：优先 `instance_id + strategy_id + config_id + execution_policy`，不只看 routing label。
- price：YES price、selected-side ask、bid/mid、freshness 和 fee basis。

## Signal funnel 与 evidence funnel

分别输出，不得混成一个“筛选漏斗”。

```text
signal funnel:
raw universe -> mechanism candidates -> first city-day/event signal -> policy selected

evidence funnel:
PIT weather coverage -> PIT quote coverage -> settlement coverage -> executable expression -> actual fill
```

每层标 grain、行数、独立 target dates。盘口/结算缺失是 coverage gap，不是策略筛除。

## 数据检查

先确认 DB 目标窗口与 raw 覆盖；需要刷新时走 `weather-fact-rebuild`。普通历史查询不为形式重建。

```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled),
       SUM(decision_window_missing)
FROM fact_signal_candidates;
SELECT o.status, COUNT(*), COUNT(f.execution_id)
FROM orders o LEFT JOIN fills f USING(execution_id)
WHERE o.venue='polymarket_clob'
GROUP BY o.status;
```

发布 `live_real` 前：

```bash
.venv/bin/python scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

gate 不通过时只做数据链诊断。

## 概率层先于交易层

模型或物理特征必须在固定全分母 PIT state 上先和 market 比：

- logloss、Brier、calibration、AUC/rank。
- 同一 row、同一 label、同一时间窗。
- market-anchored residual 与模型增量分开。
- source/city overlay 用 expanding/OOF，不能泄漏 target date。

模型 proper score 没有 forward 打败 market 时，selected trade ROI 只能算探索性，不得包装成已证实 alpha。

## 交易层

使用可执行 side ask/bid/depth 与官方 Weather fee：

```text
edge = p_win - executable_cost
executable_cost = side ask + taker fee + declared friction
```

- maker 口径不扣 taker fee，但必须建 fill probability、queue、adverse selection；future touch 不是 fill。
- `fact_trades.pnl_usd_at_fill` 已含 canonical fee 结果；同时报告 `fees_usd` 与 evidence class。
- gross 只作诊断，不得作主结论。
- exact bracket：触到 X 不代表 X YES 赢；继续到 X+1 会使 X YES 输。

## 同分母 A/B

先固定 rows/labels/quotes，再比较 source policy、模型、特征、阈值、execution overlay。A/B 输出 paired delta 与 target-date block bootstrap CI。不同 coverage、不同日期或不同 eligible universe 不能直接排名。

## 推断与 forward

- 按 `target_date` block/cluster bootstrap 95% CI。
- 报 fills/states、独立日期、active days、unsettled/coverage。
- 本轮试验 K 个版本/切片，报告多重检验处理或明确未校正。
- train 选模型/阈值；frozen holdout/forward 只复核。
- 当前默认 live 动作门仍是 significance、same-denominator baseline、forward 三门全过。

结论等级：

| 等级 | 含义 | 允许动作 |
|---|---|---|
| `confirmed` | 三门全过且执行口径成立 | 才能讨论 keep/cut/size；仍走 deploy |
| `shadow_candidate` | 机制/历史成立但 forward 或执行证据不足 | zero-notional shadow/collector |
| `inconclusive` | CI、基准、coverage、PIT 或 forward 不足 | 不改 live |
| `rejected_for_expression` | 宽分母 fee-adjusted 反证该交易表达 | 保留数据/代码，停用该表达，不等于删除整个方向 |

## 报告

使用 `docs/analysis/templates/performance.md` 或 `performance-compare.md`。必须包含数据快照、目标 metric/grain、双漏斗、probability 与 trade 两层、fee/执行口径、paired baseline、CI/forward、三门、动作。

默认先报 fee-adjusted PnL，并拆 YES/NO；不要让一侧掩盖另一侧亏损。最终一句：

```text
在 [窗口/分母]，[candidate] 相对 [same-denominator baseline] 的 [主指标 delta] 为 X
（95% CI [a,b]），forward [PASS/FAIL/NA]，结论 [等级]，动作 [shadow/保持/不改 live]。
```
