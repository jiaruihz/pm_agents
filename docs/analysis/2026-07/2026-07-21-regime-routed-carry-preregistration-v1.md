# Regime-Routed Carry v1 — 历史候选（已被 7/22 审计取代）

Status: `superseded_for_decision_use`（保留机制研究；不得据此建新算法或改 live）
Date: 2026-07-21
Family: `current_yes_heat_death_physical_v1` · Strategy id: `regime_routed_carry_v1`

权威后续：[2026-07-22 carry 机制 / timing / execution 审计](2026-07-22-current-yes-carry-mechanism-timing-audit-v1.md)。后续审计修正了 expression 行选择、PIT bias 权重、first-city-day 分母、official taker fee 和 proper-score baseline；本文数字只作历史机制线索。

## 1. 物理论点

买当前档 YES 是一笔 **carry**：为一个几乎已定的结果付高价。它只在「这一天物理上已经结束」时成立。
两个**独立**条件定义「结束」，必须同时满足：

1. **天花板已破 `ceiling_busted`** — running max 已超过预报最高温，**且该预报先按该城历史偏差校正**。
   校正是必需的：原始 gap 继承各城系统性预报误差（实测 Lucknow +5.1F、Dallas −3.3F），会把偏差城市双向误标。
2. **路径已消退 `path_faded`** — 观测路径已经掉头（canonical `intraday_state` / `running_max_state` 的 fade/pullback）。

```text
ceiling_busted : (forecast_max - running_max) - city_bias_PIT < -unit_step
path_faded     : intraday_state in ['mature_fade', 'pullback_uncertain'] OR running_max_state in ['mature_fade', 'pullback_from_high']
route          : ceiling_busted AND path_faded
expression     : buy current bracket YES
price          : entry price enters only through cost, never as eligibility
```

## 2. 分母与数据

- 全量午后（13-17 local）双边报价、已结算：**4061 行 / 1305 city-day / 46 天 / 36 城**，2026-05-19..2026-07-08。
- 已剔除静默 ECMWF→GFS fallback 日期：2026-07-02, 2026-07-03, 2026-07-04, 2026-07-05。
- 分母 base 守住率 66.7%；PIT 城市偏差可用占比 97.9%。

## 3. 主结果

| selector | state行 | city-day | 天 | 胜率 | 均ask | **taker ROI [95%CI]** | maker上界 |
|---|---:|---:|---:|---:|---:|---:|---:|
| no_routing | 4061 | 1305 | 46 | +66.7% | 0.702 | -5.7% [-8.0%,-3.4%] | +5.9% |
| ceiling_busted_only | 962 | 401 | 44 | +80.6% | 0.823 | -2.6% [-7.2%,+1.4%] | +6.5% |
| path_faded_only | 1022 | 614 | 45 | +85.2% | 0.864 | -1.8% [-4.2%,+0.5%] | +4.7% |
| regime_routed_carry | 249 | 171 | 42 | +93.2% | 0.927 | +0.2% [-4.6%,+4.7%] | +5.0% |
| routed_front | 187 | 126 | 28 | +94.7% | 0.930 | +1.5% [-4.7%,+6.9%] | +6.1% |
| routed_back | 62 | 45 | 14 | +88.7% | 0.918 | -3.7% [-11.6%,+3.3%] | +1.6% |

**历史 state-row 描述：路由 taker ROI +0.2%，CI [-4.6%, +4.7%]，胜率 +93.2%。** 这个分母会让同一 city-day 的多个状态重复计权，只保留为影响对照；权威结论必须看 7/22 审计的 first-city-day 分母。
两个组件及其交集均未通过独立 frozen-forward 验证，不能据此宣称 AND 后有效。

## 4. 与现有 H1 / H2 的关系

| selector | state行 | city-day | 天 | 胜率 | 均ask | taker ROI [95%CI] | maker上界 |
|---|---:|---:|---:|---:|---:|---:|---:|
| incumbent_gate | 469 | 322 | 44 | +94.9% | 0.941 | +0.6% [-1.9%,+3.0%] | +5.0% |
| H1 | 339 | 268 | 44 | +97.9% | 0.980 | -0.2% [-2.0%,+1.3%] | +2.5% |
| H2 | 108 | 89 | 34 | +85.2% | 0.817 | +3.4% [-5.4%,+11.1%] | +14.7% |
| regime_routed_carry | 249 | 171 | 42 | +93.2% | 0.927 | +0.2% [-4.6%,+4.7%] | +5.0% |

**区别（三点，都是结构性的）：**

1. **换掉了 peak clock**：现有门用「预报峰值已过 ≥0.25h」，本规则用**去偏后的预报天花板是否被打穿**。   peak clock 的预报可靠性实测很差（预报峰值时刻 vs 实际 corr 仅 0.458），而预报最高温 corr 0.893。
2. **不按入场价拆头**：H1/H2 用 ask≥0.95 / ≤0.93 把同一信号劈成两个策略。本规则**完全不用价格做资格门**，   价格只进成本。价格带实测跨期持续性为 0（pearson 0.010 / spearman 0.000），按价格拆头没有统计依据。
3. **消费 canonical regime 标签**而不是自造阈值，新自由度更少。

**重叠情况：**

- 路由命中 249 行；其中同时被现有 gate 命中 131 行，**现有 gate 抓不到的有 118 行**；现有 gate 命中但本规则不要的有 338 行。
- 路由行里同时满足 H1(ask≥0.95) 的 103 行、H2(ask≤0.93) 的 23 行。
- 路由行的 ask 分位：{0.1: 0.82, 0.25: 0.93, 0.5: 0.97, 0.75: 0.989, 0.9: 0.99}。

**当前取舍**：本规则只保留为 frozen-forward 的候选机制分量；现有证据不支持它取代 H1/H2，也不支持把两套条件强行 AND 合并。H1/H2 的血缘、config、execution 资产继续保留。

## 5. 稳健性

- **跨期**：前段 taker +1.5%、后段 taker -3.7%；maker 上界 前 +6.1% / 后 +1.6%。
- **leave-one-date-out** taker ROI 区间 ['-0.8%', '+1.7%']；**leave-one-city-out** ['-0.7%', '+1.4%']。
- 单城最大 |PnL| 占比 0.125；17 个输家散在 11 天。
- **安慰剂**（每日内随机打乱路由标记、保持每日选中数量不变）：真实 taker +0.2% vs 随机均值 -4.7%、p95 -0.4% → **PASS**。

### 阈值敏感性（不是调参，是看规则稳不稳）

| 变体 | state行 | 胜率 | taker ROI | maker上界 |
|---|---:|---:|---:|---:|
| 0.0 | 374 | +93.6% | +0.6% | +5.7% |
| -0.5 | 310 | +93.2% | +0.4% | +5.2% |
| -1.0 | 249 | +93.2% | +0.2% | +5.0% |
| -1.5 | 191 | +93.7% | +0.9% | +5.5% |
| -2.0 | 138 | +93.5% | +0.4% | +4.5% |
| intraday_only | 160 | +95.0% | -0.0% | +4.0% |
| running_only | 242 | +93.0% | +0.2% | +5.1% |
| either(default) | 249 | +93.2% | +0.2% | +5.0% |
| both | 153 | +94.8% | -0.1% | +4.0% |
| raw_gap_no_debias | 441 | +91.8% | +0.3% | +5.9% |

## 6. 盘口与报文时钟（用**现有**数据，无需新采集）

factory 9 个分片**全部带盘口字段**（bid/ask、两侧 size、5c 深度），覆盖 49 天、非空约 94.6%；`decision_snapshot_ts_utc − decision_last_obs_utc` 即报文年龄（本路由样本覆盖 1.000）。

**报文年龄(分钟)**

| 桶 | n | 胜率 | taker ROI | maker上界 | 均半价差 |
|---|---:|---:|---:|---:|---:|
| (30.0, 60.0] | 239 | +93.3% | +0.4% | +5.2% | 0.020 |

**ask 5c 深度**

| 桶 | n | 胜率 | taker ROI | maker上界 | 均半价差 |
|---|---:|---:|---:|---:|---:|
| (10.0, 50.0] | 8 | +75.0% | +5.1% | +22.4% | 0.047 |
| (50.0, 200.0] | 27 | +85.2% | +8.1% | +24.9% | 0.049 |
| (200.0, 1000000000.0] | 212 | +94.8% | -1.0% | +2.3% | 0.015 |

**best ask size**

| 桶 | n | 胜率 | taker ROI | maker上界 | 均半价差 |
|---|---:|---:|---:|---:|---:|
| (-1.0, 10.0] | 49 | +91.8% | +0.2% | +5.6% | 0.022 |
| (10.0, 25.0] | 42 | +88.1% | -0.9% | +4.9% | 0.023 |
| (25.0, 100.0] | 83 | +92.8% | -0.5% | +5.1% | 0.023 |
| (100.0, 1000000000.0] | 75 | +97.3% | +1.6% | +4.7% | 0.013 |

**深度失衡(bid-ask)/(bid+ask)**

| 桶 | n | 胜率 | taker ROI | maker上界 | 均半价差 |
|---|---:|---:|---:|---:|---:|
| (-1.01, -0.33] | 196 | +95.4% | +0.5% | +4.9% | 0.019 |
| (-0.33, 0.33] | 41 | +85.4% | -5.9% | -0.8% | 0.022 |
| (0.33, 1.01] | 12 | +83.3% | +22.2% | +35.7% | 0.030 |

## 7. 微观结构研究：能做什么、缺什么、格式怎么统一

**现在就能做**（上面第 6 节已经是）：静态截面的深度/价差/失衡/报文年龄与结果的关系。

**确实缺、必须 forward 采的：**

- intra-decision book time series (how the book evolves while an order rests)
- queue-ahead at own price level and per-reprice size journal
- real exchange fill event time and 1/5/15m + next-report markout

**格式统一原则**：tmax_v2_ladder_rung_quotes already carries yes_direct_bid/ask, *_size, depth_bid/ask_5c/10c and book_status; extend that table's cadence and add queue/fill-event columns rather than inventing a new format

即：**扩展 `tmax_v2_ladder_*` 的采集频率并补 queue/fill-event 列，不要新建平行表**；报文时钟直接复用 factory 已有的 `decision_last_obs_utc`，不引入第二套时间定义。

## 8. 冻结判据（进入 forward 前）

```text
primary expression = current bracket YES（不设价格资格门）
primary metric     = fee-adjusted taker ROI（maker 只作上界参考，永不作为晋升依据）
样本门   = >=30 个独立 target_date 的 forward，且 >=8 城
显著性门 = taker ROI 的 target_date block bootstrap 95% CI 下界 > 0
机制门   = ceiling_busted 与 path_faded 单独都不得达标（证明是交集机制而非其一）
失效门   = forward 窗口内 taker ROI 95% CI 上界 < 0 则降级 rejected_for_expression
执行     = maker 仅在补齐 queue/markout 仪表后才允许评估；在此之前一律按 taker 记账
```

## 8b. 与既有冻结文档的关系（重要，勿在读旧文档时被误导）

[2026-07-15 H1/H2 晋升判据预注册](2026-07-15-heat-death-live-promotion-preregistration-v1.md) 是**冻结文档**，本文**不修改它**。但本文的两项测量与它的前提冲突，读那份文档时必须同时知道：

| 那份冻结文档的前提 | 本文的测量 |
|---|---|
| 按入场价把同一信号拆成 H1(ask≥0.95) / H2(ask≤0.93) 两条独立线 | **价格带跨期持续性 pearson 0.010 / spearman 0.000（≈零）**；按价格拆头缺乏统计依据 |
| 共同硬门含「forecast peak 已过 ≥0.25h」 | **峰值时刻预报可靠性远低于最高温预报**（城内归一化后 corr 0.458 vs 0.893）；本文改用去偏后的预报天花板 |

若要按本文结论调整 H1/H2 判据，按那份文档自身的规定应**新开 v2 判据文档**，不得原地改。

同时作废的还有本家族本轮的两份中间产物，两者都已在文首标注 SUPERSEDED：
[overshoot-hazard-calibration v1](2026-07-20-current-yes-overshoot-hazard-calibration-v1.md)、[overshoot-edge strategy v1](2026-07-21-heat-death-overshoot-edge-strategy-v1.md)。

## 9. 诚实边界

- `maker` 全部是**在 bid 全成交的上界**。唯一真实成交证据（2026-07-20，6 对同信号）是 3/6 成交、**漏掉的两单是赢家**、净比 taker 差 0.493pp。**maker 数字是奖品尺寸，不是收益预期。**
- 本轮路由是在看过分期结果之后收敛的，因此**它是预注册候选，不是已验证结论**；判据冻结后只能 forward 复核。
- 后段样本仍薄，且我在收敛过程中比较了约 18 个 regime 格子，**未做多重检验校正**。
- 历史层仍缺 solar geometry / 降雨 / 风向；`running_max_state_v2` 因缺 strict-high 时钟无法物化。

## 10. 产物

- Script: `scripts/analysis/reheat_risk/research_regime_routed_carry_v1.py`
- 路由明细: `docs/analysis/2026-07/generated/regime_routed_carry_v1/routed_rows.csv`
- 前序: [overshoot-edge v2（跨期证伪）](2026-07-21-heat-death-overshoot-edge-strategy-v2.md)
