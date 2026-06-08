---
name: weather-strategy-performance
description: >
  科学评估 weather 策略的历史绩效、PnL、ROI、win rate、城市 alpha、稳定性、多维切片、
  A/B 或回测表现。触发词：绩效、PnL、ROI、win rate、胜率、切片、对比策略、A/B、
  回测结果、策略表现、历史表现、收益分析、城市 alpha、稳定性、分析最近 N 天 X 策略效果。
  本 skill 的产出是带置信区间、零模型基准、前瞻复核状态的推断结论，不是裸点估计。
  任何 keep/cut/降 size/上 live 建议必须通过显著性门、基准门、前瞻门；否则只能标
  shadow_candidate 或 inconclusive。禁止绕过 fact_trades 自算成交 PnL；禁止绕过
  weather.db 直接用 raw JSON/CSV 跑 pandas；禁止把候选信号或未成交机会混进 fill-grain
  绩效表，机会 alpha/成交质量/漏单/滑点必须读 fact_signal_candidates。
---

# weather-strategy-performance

分析 weather 策略绩效时，先把问题当成统计推断问题，而不是描述性切片问题。目标不是只算出
PnL/ROI/win rate，而是回答：这个 edge 相对零模型是否显著、是否不是 base-rate、是否在未用于
挑选的时间窗仍同号。

## 数据边界

已成交 fill 绩效唯一授权源：

```text
runtime/weather.db.fact_trades
```

机会粒度、成交质量、漏单、滑点、全机会 alpha 唯一授权源：

```text
runtime/weather.db.fact_signal_candidates
```

| 表 | grain | 回答 | PnL 列 |
|---|---|---|---|
| `fact_trades` | 每 fill | 已成交 realized / shadow / replay 绩效 | `pnl_usd_at_fill`, `pnl_usd_at_plan` |
| `fact_signal_candidates` | 每机会 `(condition_id, side, event_date)` | 全机会 alpha、成交率、漏单、滑点、反事实 | `counterfactual_pnl`, `counterfactual_pnl_best` |

禁止互相硬塞：
- 不要拿候选反事实 PnL 冒充已成交绩效。
- 不要用成交样本结论否定全机会 alpha。
- 不要回到 raw JSON/CSV 自己 join 或自算 PnL。
- 关联键用 `(condition_id, side, event_date)`；`fact_trades` 侧对应 `condition_id + side + target_date`。

余额、钱包现金流、CLOB fill 漏记、账户亏损对账不是普通绩效问题，转 `weather-live-account-reconcile`。
本 skill 不得用 `fact_trades.order_date_bj` 或 `cost_usd` 解释钱包现金流；现金流默认看
`fill_date_bj` 的 actual fill cost，并区分 open cost 与 realized PnL。

## 结论分级

三道门的硬来源是 `docs/WEATHER_ANALYSIS_CONTRACT.md` 的“绩效结论三道门”。本节只复述执行规则；
若与 contract 冲突，以 contract 为准。任何交易动作建议必须先过三道门：

| 门 | 通过条件 | 不通过时 |
|---|---|---|
| 显著性门 | ROI、超额、delta 的 bootstrap 95% CI 不跨 0 或不跨基准 | `inconclusive`，不得给 live 动作 |
| 基准门 | 相对零模型的超额显著大于 0 | 只是 base-rate，不算 alpha |
| 前瞻门 | train 上选出的候选，在 holdout 或后续日期仍同号且仍有超额 | 只能 `shadow_candidate`，不得改 live |

| 等级 | 条件 | 允许动作 |
|---|---|---|
| `confirmed` | 三门全过 | 可建议 keep / cut / 调 size live |
| `shadow_candidate` | 显著且超额，但前瞻未验证 | 只能 shadow/paper，不得改 live |
| `inconclusive` | CI 跨 0、样本不足、未超额、或口径缺失 | 禁止 live 动作 |

报告中每条结论旁必须标 `significance=PASS/FAIL/NA`、`baseline=PASS/FAIL/NA`、
`forward=PASS/FAIL/NA`。没有三门证据的 keep/cut/调 size 建议是 bug。

## 执行 Checklist

### 第 0 步：读 contract

先读 `docs/WEATHER_ANALYSIS_CONTRACT.md`，确认：
- `fact_trades` 是 fill-grain 绩效分析强制源。
- `fact_signal_candidates` 是机会粒度问题强制源。
- PnL 只读 `pnl_usd_at_fill` / `pnl_usd_at_plan`；不要重写 BUY_YES / BUY_NO 公式。
- 切片维度必须来自 contract 白名单。
- live_real PnL/ROI/排名/曲线发布前必须跑 `weather_clob_fill_coverage_gate.py`，`gate_pass=false` 时先修 fill 链路。

未读 contract 不得继续分析。

### 第 1 步：锁 target metric、分母和零模型

先把用户问题收敛成一句口径，例如：

```text
by_city_live_real_alpha = trade_class='live_real' 且 settlement_status='settled' 的 fill-grain
城市绩效，相对同价位无脑买 NO 的超额 ROI，并用 target_date block bootstrap 给 CI。
```

必须显式锁定：
- `trade_class`: `live_real` / `live_simulated` / `paper` / `snapshot_replay` / `all`。默认分层，不混算。
- 时间字段：默认 `target_date`；策略下单归属诊断可用 `order_date_bj`；钱包现金流转账户对账 skill。
- settlement：realized PnL 默认只纳入 `settlement_status='settled'`；未结算单独列 `[UNSETTLED]`。
- 零模型：默认同价位无脑买 NO；可加市场隐含价 EV=0 或随机选边。

零模型要写清楚字段语义：
- 若使用 `fact_signal_candidates`，优先用 `market_yes_price` 与 `final_yes` 构造基准。
- 若只使用 `fact_trades.market_price`，先用 `PRAGMA table_info` 和 contract 确认它是 YES 价还是所选 side 价；无法确认时，不得发布“超额于无脑 NO”的结论，只能标 `baseline=NA`。

### 第 2 步：同步、重建和数据自检

默认先执行 contract 要求的同步与重建：

```bash
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh
```

若 N100 不可达或用户明确要求只看本地缓存，在报告“数据快照”写明原因、DB mtime、`MAX(fact_built_at_utc)`。

强制自检：

```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates;
SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
  FROM orders o LEFT JOIN fills f USING(execution_id)
  WHERE o.venue='polymarket_clob'
  GROUP BY o.status;
```

若分析 `live_real`，还必须跑：

```bash
python3 scripts/analysis/weather_clob_fill_coverage_gate.py
```

`gate_pass=false` 时禁止发布 live_real PnL、ROI、city/side rank、近 7/15 天曲线。

### 第 3 步：点估计只作为输入

只读 `fact_trades` 聚合已成交绩效。点估计必须标注“输入，非结论”。

常用指标：
- `fills`, `settled_fills`, `active_days`
- `cost_usd`, `cost_usd_at_plan`
- `pnl_usd_at_fill`, `pnl_usd_at_plan`
- `fill_roi = SUM(pnl_usd_at_fill) / SUM(cost_usd)`
- `plan_roi = SUM(pnl_usd_at_plan) / SUM(cost_usd_at_plan)`
- `win_rate_by_count`, `win_rate_by_notional`
- `avg_pnl_per_fill`, `positive_day_rate`, `worst_day_pnl`, `best_day_pnl`, `daily_sharpe_like`

稳定性必须先按 `city + target_date` 聚合 daily PnL，再算正收益天比例、最差日、Sharpe-like。
不要用 fill 级标准差冒充日稳定性。

未结算估值单独列，标 `[UNSETTLED]`，不得混入 realized PnL：
- `val_mid`
- `val_bid`
- `val_last_fill`
- `unrealized_pnl_mid`
- `val_snapshot_ts_utc`

### 第 4 步：统计推断

对每个要上结论的指标做推断，而不是只报点估计。

最低要求：
- 对 ROI、超额 ROI、A/B delta 做 bootstrap 95% CI。
- 优先按 `target_date` 做 block/cluster bootstrap，避免把同日多城天气相关性当成独立 fill。
- 报 `active_days`、`settled_fills`、样本窗口、unsettled 占比。
- 城市或切片级 keep/cut 默认需要 `active_days >= 10` 且 `settled_fills >= 30`；不满足时标 `low_sample`，只能 `inconclusive`，除非用户明确只要探索性描述。
- 若本轮试了 K 个城市/切片/版本，报告 K，并对“最优者”做 Bonferroni、Deflated Sharpe 或至少明确“未校正，多重检验风险高”。

相关性折减可以作为风险标注，不作为唯一硬闸：
- 可估计同日跨城 outcome 平均相关 `rho_bar`。
- 可报告 `n_eff = n / (1 + (n - 1) * rho_bar)`。
- 若 `n_eff` 远低于 naive n，结论降级或标高风险。

### 第 5 步：零模型基准

必须把“赚了”翻译成“相对基准有超额”。

默认基准：
- 同价位无脑买 NO：用同一价桶、同一日期/城市池/side universe 构造。
- 市场隐含价 EV=0：作为理论零基线，只能辅助解释。
- 随机选边：仅用于 sanity check。

结论格式必须是：

```text
策略相对 [零模型] 的超额 ROI = X%，95% CI [a, b]，baseline=PASS/FAIL/NA。
```

裸 `win_rate=73%` 或 `ROI=+12%` 不是 live 动作依据。BUY_NO 的高 win rate 可能只是 base-rate。

### 第 6 步：前瞻复核

若要给 keep/cut/调 size/live 建议，必须做前瞻复核：
- 按 `target_date` 切 train/holdout，默认后 30% 为 holdout。
- 只能在 train 上挑选候选规则、城市、side 或参数。
- holdout 只复核，不再调参。
- holdout 同号且仍有超额，才算 `forward=PASS`。

这是历史内的伪前瞻 sanity check，不等同于上线后的真实 out-of-sample。报告必须写明。

### 第 7 步：切片与 A/B

优先使用 contract 白名单字段：
- `trade_class`
- `strategy_id`, `code_version`, `execution_policy`, `sizing_mode`
- `city`, `city_pool`, `forecast_source`, `model_version`
- `side`, `target_date`, `order_date_bj`, `bracket`
- `strategy_instance` 若表中存在，必须作为 live 策略拆分的一等维度

A/B：两个 selector 各自过滤、各自聚合，再按同一切片键 join，输出 delta PnL / delta ROI /
delta win rate / delta active_days。delta 必须配 bootstrap CI；CI 跨 0 判为“无显著差异”，不得据此调参。

### 第 8 步：成交质量、漏单、机会 alpha

当用户问成交质量、成交率、漏单、漏赢家、滑点、全机会集真实 alpha 时，读 `fact_signal_candidates`。
不要把它混进 fill-grain realized 绩效。

默认输出：
- 成交覆盖：`eligible`, `paper_ordered`, `live_filled`，live 覆盖率 = `live_filled / eligible`。
- 滑点：`AVG(slippage_vs_paper)`；负值表示成交价较 paper 更便宜，对买方有利。
- 漏掉的赢家：`paper_ordered=0 AND win_by_count=1` 的 `counterfactual_pnl`。
- 全机会 alpha vs 成交样本：同一 city/side 并排候选反事实与 `fact_trades` 已成交。
- 执行微结构：使用当前表真实存在字段，如 `market_yes_price`, `decision_entry_price`,
  `yes_spread`, `no_spread`, `best_entry_price`, `live_fill_price`, `slippage_vs_paper`,
  `counterfactual_pnl`, `counterfactual_pnl_best`。跑前用 `PRAGMA table_info` 确认列名；缺列就标 NA。

机会粒度分母：

```sql
SELECT city, side, COUNT(*) n,
       SUM(paper_ordered) ordered,
       SUM(live_filled) live_fill,
       AVG(CAST(win_by_count AS REAL)) win_rate,
       SUM(counterfactual_pnl) cf_pnl
FROM fact_signal_candidates
WHERE eligible=1
  AND final_yes IS NOT NULL
  AND decision_window_missing=0
GROUP BY city, side
ORDER BY cf_pnl DESC;
```

必须报告 `decision_window_missing` 占比。`paper_ordered` 是全池 paper ledger，不是 live 意图；
不要把 paper 未下单自动解释成 live 漏单。

### 第 9 步：报告产出

正式报告写到：

```text
docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md
```

报告必须包含：
- 数据快照：数据源、DB mtime 或 `MAX(fact_built_at_utc)`、行数、unsettled 占比、missing_bracket、CLOB gate 状态。
- 目标指标与分母：target metric、time field、trade_class、settlement、零模型。
- 覆盖声明：描述切片、统计推断、零模型、前瞻、机会粒度/微结构是否覆盖。
- 点估计总览：明确标“输入，非结论”。
- 推断表：每条带 CI、超额、样本数、active_days、多重检验风险。
- 三门判定表：`significance`、`baseline`、`forward`、结论等级。
- 交易动作：只允许 `confirmed` 给 live keep/cut/调 size；`shadow_candidate` 只能 shadow/paper；`inconclusive` 不改 live。
- 残余风险：样本不足、unsettled、trade_class 混用、CLOB gate、`decision_window_missing`、相关性、多重检验。

一句话总结必须采用：

```text
在 [窗口]，[策略/切片] 相对 [零模型] 的超额 ROI 为 X%（95% CI [a,b]），
前瞻 [PASS/FAIL/NA]，结论等级 [confirmed/shadow_candidate/inconclusive]。
```

不要用“ROI +Y%，建议保留 A 城砍 B 城”作为最终结论。
