# Weather Edge Engine Current State — 2026-06-06

Status: snapshot
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no; historical snapshot
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed


> 本文固定为 2026-06-06 blender/basket 接手快照，不定义 2026-07 当前研究主线或生产状态。当前入口见 `WEATHER_STRATEGY_ENTRYPOINT.md`、`WEATHER_STRATEGY_REGISTRY.md` 与 2026-07-14 strategy-search reset。
>
> 本文档把 PR1/PR2/PR2b dev log 里的关键结论抽成稳定入口，供后续两个新窗口分别接手：
> 1. **跑策略 / shadow 双写链路**
> 2. **研究 basket 核心算法**

生产结论先写在前面：**不退役旧策略、不开新 canary、不改 N100 live 下单配置。**

2026-06-08 最新补充：

- 用户已从 live allowlist 移除近期弱 ECMWF 城市：`BuenosAires / Munich / Jeddah / Karachi / Moscow / Ankara`。
- 用户已 ban `hours_to_settle > 28` 的过早下单。
- 在该新 operational base 下，`blender` 的定位从“可能的硬 gate”降级为“shadow/paper + size/risk signal 候选”。原因是剔除 6 城 + `T<=28` 后，6 月后 `mid_price_core_v1_25_75` settled live overlay 已从 `-$89.06` 改善到 `+$39.53`；再叠纯 `blended_edge>=0.10` 只剩 `+$20.19`，相对新 base 是 `-$19.33` 的负增量。
- 2026-06-08 进一步按 strict execution window `22<=hours_to_settle<=28` 重算 blender 本体价值：新 base 本身 308 settled fills、PnL `+$167.35`、ROI `+20.1%`；`blended_edge>=0.10` hard gate 的 delta 是 `-$115.28`；温和 size curve 的 delta 是 `-$67.65`；7-day walk-forward policy selection 总 delta `-$16.40`。结论：blender 仍不应进 live hard gate，size curve 也未证明能增厚收益。
- 因此当前策略动作是：**保留城市/时间风控，blender 不进 live hard gate，只继续 shadow/paper 和 lineage 双写研究。**

最新交接报告：

```text
docs/analysis/2026-06/2026-06-08-blender-research-state-and-next-plan.md
docs/analysis/2026-06/2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md
docs/analysis/2026-06/2026-06-08-blender-signal-value-research.md
```

---

## 0. 当前拆分：两个大模块

Weather Edge Engine 现在不要混成一个大任务。后续按两条线推进。

### A. Probability Blend / Shadow 数据链路

目标：先把概率层接入真实逐 snapshot 记录，**不改变下单行为**。

当前 shadow 策略身份：

```text
strategy_id = weather_edge_engine_blended_single_v0
strategy_family = weather_edge_engine
probability_source = blended
decision_mode = single_leg_shadow
execution_mode = shadow
```

策略详情与代码位置：

```text
weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json
weather_dashboard/blend/blender.py
weather_dashboard/blend/city_blend_config.json
scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_single.py
scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_entry_bands.py
scripts/ops/weather_blended_shadow_paper.py
scripts/ops/weather_blended_shadow_paper_loop.sh
```

核心公式：

```text
p_yes_used = alpha * model_p_yes_raw + (1 - alpha) * market_implied_p_yes
```

当前默认：

```text
global alpha = 0.30
global beta  = 0.70
blacklist alpha = 0.10
raw_model_blacklist = Milan / Lucknow / Austin / Beijing
```

例子：

```text
raw = 30%
market = 20%
p_yes_used = 0.3 * 30% + 0.7 * 20% = 23%
```

这会把概率往市场拉，通常让 edge 变小。目的不是制造更多交易，而是减少 raw model 的过度自信。

当前证据：

- `market_only` 在 PR2/PR2b replay 中触发 0 条腿，说明交易不是由 market 本身产生。
- loose edge=0.03 replay 里，`raw_single -> blended_single` 在 full sample 和 live_filled opportunity 子集上都有改善。
- 但保留当前 live 的 `entry band + min_edge` 后，blend 会变成更严格的 filter，交易数明显下降，recent/live_filled 子集不稳。
- 但最近 7 天 recalibration 显示 best alpha = 0.00，raw model 近期退化，不能贸然加 raw 权重。
- 2026-06-07 live fill overlay 显示，纯 `blended_edge>=0.10` 在 6 月前伤害收益、6 月后改善亏损：pre delta `-$67.63`，post delta `+$86.56`。它更像近期 drift filter，不是稳定 alpha。
- 2026-06-08 控制变量重算显示，剔除 6 个弱 ECMWF 城市并 ban `T>28` 后，blender 对新 base 的边际收益为负：post 新 base `+$39.53`，新 base + blender `+$20.19`。
- 2026-06-08 strict window 重算显示，在 `22<=T<=28` 的新 base 内，blender hard gate 和 size curve 都没有通过：base `+$167.35`，hard gate delta `-$115.28`，gentle size curve delta `-$67.65`，walk-forward delta `-$16.40`。

推荐下一步：

```text
PR3a: N100 shadow 双写 blend 字段，不改下单
```

需要双写的字段：

```text
model_p_yes_raw
market_implied_p_yes
model_p_yes_used
blend_alpha
blend_beta
blend_mode
edge_raw_yes
edge_used_yes
edge_used_no
```

验收口径：

- 7-14 天逐 snapshot 数据可 join 到 signal / plan / order lineage。
- 可以按 city/date/bracket 对比 raw vs market vs blend 的 Brier、edge 触发率、错过/减少交易。
- 不用 aggregated `fact_signal_candidates` 代替真实逐 snapshot 行为做最终判断。
- 复核报告必须同时给出两种 delta：
  - `相对原始 delta`：和原始 `mid_price_core_v1_25_75` 比。
  - `相对新 base delta`：和“剔除 6 城 + T<=28”后的 base 比。

当前不推荐的用法：

```text
if blended_edge < 0.10:
    skip_live_order
```

更合理但尚未通过的研究方向：

```text
if operational_base_pass:
    use blended_edge as size/risk signal
```

候选 sizing 方向：

```text
blended_edge < 0.00       -> skip / size 0
0.00 <= blended_edge < .05 -> 0.25x
0.05 <= blended_edge < .10 and raw_edge <= .25 -> 0.5x
otherwise                 -> 1.0x
```

这条 size curve 已在 2026-06-08 strict window 样本上回测，当前结果为负增量；只保留为 future shadow 字段和模型健康告警候选，不作为 live 参数。

### B. City-Day Basket / 核心算法研究

目标：研究同一个 `city + target_date` 下多个 bracket 怎么组成一个统一决策。

当前 research/shadow 策略身份：

```text
strategy_id = weather_edge_engine_basket_v0
strategy_family = weather_edge_engine
probability_source = blended
decision_mode = city_day_basket_shadow
execution_mode = shadow
```

策略详情：

```text
weather_dashboard/strategy_specs/weather_edge_engine_basket_v0.json
```

原来：

```text
for each bracket:
    if edge > threshold:
        trade
```

新方向：

```text
for each city-date:
    collect all bracket candidates
    evaluate combined payoff by possible final temp
    choose one coherent basket
    size by basket risk, not leg count
```

Polymarket 实际下单仍是一条腿一条腿下 CLOB order；basket 不是交易所组合订单。区别是下单前先生成一个 basket plan，统一约束：

```text
selected_legs
payoff_by_final_temp
worst_case_loss
expected_value
max_notional
decision = TRADE / REDUCE / SHADOW / SKIP
reason_codes
```

当前 PR2b 最好配置只是研究候选，不是生产参数：

```text
small/normal = $3/$8
city_day_cap = $15
max_legs = 4
edge_small/normal = 0.03/0.06
prefer_no_over_yes = False
```

这组参数说明一个方向：**不要强制 BUY_NO 优先，YES/NO 应按 edge 和组合风险一起竞争。**

但这些数字都是样本内 sweep 产物，必须继续实验。不能把 `$3/$8` 或 cap `$15` 当成稳健结论。

---

## 1. 已完成产物

### PR1：纯函数骨架

文件：

```text
weather_dashboard/blend/blender.py
weather_dashboard/basket/city_day_basket.py
tests/blend/test_blender.py
tests/basket/test_city_day_basket.py
docs/dev_logs/2026-06-05-weather-edge-engine-pr1.md
```

状态：

```text
tests/blend tests/basket: 28 passed
```

### PR2：离线 replay + recalibration

文件：

```text
scripts/analysis/blender_shadow/recalibrate_blend.py
scripts/analysis/city_selection/eval_city_day_basket.py
docs/analysis/2026-06/2026-06-05-city-day-basket-eval.md
docs/analysis/2026-06/2026-06-05-recalibrate-blend.json
docs/dev_logs/2026-06-05-weather-edge-engine-pr2.md
```

结论：

```text
raw_single      ROI +6.84%
blended_single  ROI +15.33%
basket v1       ROI +1.24%
```

PR2 gate 未通过：

```text
basket missed_profit $3007 > avoided_loss $2170
```

### PR2b：basket 参数 sweep + robustness

文件：

```text
scripts/analysis/city_selection/tune_city_day_basket.py
scripts/analysis/city_selection/validate_city_day_basket_robustness.py
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.md
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.md
docs/dev_logs/2026-06-06-weather-edge-engine-pr2b.md
```

全样本最佳候选：

```text
raw_single      ROI +6.48%
blended_single  ROI +14.48%
basket PR2b     ROI +28.16%
top5 removed    ROI +3.44%
missed_profit $784 <= avoided_loss $825
```

但是 robustness 不稳：

| slice | raw ROI | blended ROI | basket ROI | basket top5 ROI | gates |
|---|---:|---:|---:|---:|---:|
| full | +6.48% | +14.48% | +28.16% | +3.44% | 4/4 |
| train_pre_2026_05_26 | +14.47% | +24.52% | +40.53% | +7.44% | 3/4 |
| holdout_from_2026_05_26 | -10.76% | -5.91% | +4.23% | -29.31% | 2/4 |
| recent_from_2026_06_01 | +1.54% | +4.27% | +8.04% | -33.60% | 1/4 |
| live_filled_only | +2.52% | +5.56% | +11.67% | -15.35% | 1/4 |

量化结论：

- PR2b 全样本通过，但存在明显 overfit / tail dependence。
- holdout / recent / live_filled 子集的 top-5 removed ROI 均为负。
- `live_filled_only` 是机会子集反事实，不是实际钱包 PnL，不代表新策略已经 live。
- 因此 PR2b 只能作为 shadow 候选，不能直接 canary。

### PR3a 前置：blended single v0 回测

文件：

```text
weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json
scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_single.py
docs/archive/analysis/2026-06/2026-06-06-blended-single-v0-backtest.md
docs/archive/analysis/2026-06/2026-06-06-blended-entry-band-backtest.md
docs/archive/analysis/2026-06/2026-06-06-blended-paper-fill-estimate.md
```

策略 ID：

```text
weather_edge_engine_blended_single_v0
```

机会集回测结论：

| slice | raw_single ROI | blended_single_v0 ROI | raw PnL | blended PnL |
|---|---:|---:|---:|---:|
| full | +6.48% | +14.48% | +$550 | +$869 |
| holdout_from_2026_05_26 | -10.76% | -5.91% | -$289 | -$117 |
| recent_from_2026_06_01 | +1.54% | +4.27% | +$10 | +$20 |
| live_filled_only | +2.52% | +5.56% | +$36 | +$61 |

解释：

- `blended_single_v0` 在同一机会集上优于 raw-only。
- holdout 里仍为负，只是亏损小于 raw-only，不能直接视作可上线策略。
- `live_filled_only` 是机会子集反事实，不是新策略 live PnL。
- 下一步仍是 N100 shadow 双写，不改真实下单。

保留当前 live selector gate 后的公平对比：

| slice | profile | raw ROI | blended ROI | raw legs | blended legs |
|---|---|---:|---:|---:|---:|
| full | current_25_75 | +4.95% | +23.87% | 790 | 80 |
| holdout_from_2026_05_26 | current_25_75 | -5.62% | +13.90% | 235 | 26 |
| recent_from_2026_06_01 | current_25_75 | +2.32% | -1.94% | 84 | 7 |
| live_filled_only | current_25_75 | +5.74% | -3.54% | 206 | 28 |
| full | current_side_band | +6.80% | +11.03% | 385 | 62 |
| holdout_from_2026_05_26 | current_side_band | +3.56% | -16.55% | 119 | 16 |
| recent_from_2026_06_01 | current_side_band | +28.25% | -56.14% | 44 | 4 |
| live_filled_only | current_side_band | +17.88% | -16.74% | 99 | 21 |

量化结论：

- `blended_single_v0` 不能直接套用现有 raw selector 的 `min_edge=0.10/0.20`。
- 在当前入场区间和 min_edge 下，blend 的主要作用是缩窄触发面；headline ROI 可能好看，但 recent/live_filled 子集没有通过。
- 下一步应先 shadow 双写 `edge_raw_*` / `edge_used_*`，再单独研究 blend 专属 threshold，而不是直接替换现在 live 的 raw edge gate。

已选择的初始 shadow/paper 范围：

| profile | strategy_instance | city scope | entry gate | blended min_edge | paper notional |
|---|---|---|---|---:|---:|
| blended_25_75_e05 | `weather_edge_engine_blended_25_75_e05_paper` | 当前 operational T1 blend shadow 城市，已剔除弱 ECMWF 城市 | YES/NO 0.25-0.75 | 0.05 | $1 |
| blended_side_band_e05 | `weather_edge_engine_blended_side_band_e05_paper` | legacy core 9 cities | YES 0.20-0.45; NO 0.35-0.65 | 0.05 | $1 |

选择理由：

- `0.10/0.20` 是 raw edge gate，直接用于 blend 太保守。
- `0.03` 交易面更宽，适合 shadow 观察；但 paper 初始先用 `0.05`，降低噪声和账本膨胀。
- 本机 smoke（2026-06-06 北京时间，使用本机镜像、不同步远端）已能产出 shadow signal / plan / paper order，且 `live_requested=false`。

运行入口：

```bash
.venv/bin/python scripts/ops/weather_blended_shadow_paper.py --profile all
scripts/ops/start_weather_blended_shadow_paper_loop.sh
```

本机只做 smoke 时可跳过远端同步：

```bash
.venv/bin/python scripts/ops/weather_blended_shadow_paper.py --profile all --no-sync
WEATHER_BLEND_NO_SYNC=1 INITIAL_DELAY_SEC=0 scripts/ops/start_weather_blended_shadow_paper_loop.sh
```

产物路径：

```text
runtime/weather_edge_v1/signals/shadow_weather_edge_engine_blended_*_signals.jsonl
runtime/weather_edge_v1/plans/shadow_weather_edge_engine_blended_*_trade_plans.jsonl
runtime/weather_edge_v1/paper/shadow_weather_edge_engine_blended_*_paper_orders.jsonl
runtime/weather_edge_v1/live_cycle/*_weather_edge_engine_blended_shadow_paper.json
```

历史成交率折算估算：

```text
submitted order fill rate = 88.6%（844/953）
all-attempt fill rate     = 76.4%（含 error order attempts）
```

按 `$1/leg`、submitted fill rate 估算：

| slice | profile | opportunity ROI | est fills | est PnL |
|---|---|---:|---:|---:|
| full | blended_25_75_e05 | +10.36% | 414.5 | +$42.95 |
| full | blended_side_band_e05 | +14.32% | 256.8 | +$36.79 |
| holdout_from_2026_05_26 | blended_25_75_e05 | -3.56% | 130.2 | -$4.63 |
| holdout_from_2026_05_26 | blended_side_band_e05 | +4.21% | 80.6 | +$3.39 |
| recent_from_2026_06_01 | blended_25_75_e05 | +1.34% | 42.5 | +$0.57 |
| recent_from_2026_06_01 | blended_side_band_e05 | +23.10% | 27.5 | +$6.34 |

读法：

- `blended_25_75_e05` 全样本好，但 holdout 仍负、recent 很薄；paper 可以跑，但不要据此升 live。
- `blended_side_band_e05` 在 holdout/recent/live_filled 子集更稳，优先观察。
- 旧 live overlap 是更严格口径：`25_75_e05` recent overlap ROI 为 -4.29%，说明它对历史实际能成交样本不占优；`side_band_e05` recent overlap ROI 为 +24.67%。

### Basket Optimizer Research：组合枚举初版

文件：

```text
scripts/analysis/city_selection/research_city_day_basket_optimizer.py
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.md
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.json
```

研究问题：

```text
用 city-day 归一化温度分布 + 组合枚举，是否比 PR2b edge-ranked top-N heuristic 更稳？
```

比较算法：

```text
heuristic_pr2b = 当前 PR2b edge-ranked top-N
combo_ev       = 枚举组合，最大化 normalized EV
combo_risk     = 枚举组合，最大化 EV + 0.5 * CVaR20
combo_guarded  = combo_risk + 过滤单腿最大盈利 > 12x notional 的腿
combo_policy_v1 = blend EV + market sanity + CVaR floor
combo_market_risk = blend edge 触发，market-normalized objective
combo_market_tail = market-normalized objective，去掉最好温度结果后 EV 仍需为正
```

初步结论：

- `combo_ev` / `combo_risk` 能提高 headline ROI，但会更集中地选择少数高尾部腿。
- holdout / recent 的 top-5 removed ROI 比 heuristic 更差，说明组合枚举本身不自动带来稳健性。
- `combo_guarded` 这种简单高赔率过滤太粗，会砍掉全样本盈利，也没有修复 holdout tail risk。
- `combo_policy_v1`（blend EV + market sanity + CVaR floor）失败：full +21.06%，holdout -36.97%，recent -51.91%。
- `combo_market_risk`（blend edge 触发、market-normalized objective）headline 很强：full +55.69%，holdout +43.47%，recent +33.32%；但 top-5 removed 仍为负，说明仍高度 tail-dependent。
- `combo_market_tail`（严格 tail objective）也失败：full +13.63%，holdout -18.42%，recent -31.51%。这说明简单地“去彩票化”会把机会砍太干净，不能解决核心问题。
- 当前问题不是“top-N vs 枚举”这么简单，而是 objective 和 city-day 温度分布还不够可靠。
- 组合枚举目前只能作为研究信号，不应替代 PR2b heuristic 进入 canary。

### City-Day Distribution Quality：分布质量诊断

文件：

```text
scripts/analysis/city_selection/research_city_day_distribution_quality.py
docs/archive/analysis/2026-06/2026-06-06-city-day-distribution-quality.md
docs/archive/analysis/2026-06/2026-06-06-city-day-distribution-quality.json
```

研究问题：

```text
在做更复杂 basket objective 前，先确认 city-day 温度分布本身是否可靠。
```

比较分布：

```text
uniform
raw_norm
market_norm
blend_norm
dist_blend_norm
```

关键结果：

| slice | best stable read |
|---|---|
| full | `blend_norm` logloss 0.7949, close to `market_norm` 0.8000 |
| train | `dist_blend_norm` / `blend_norm` 略优 |
| holdout | `market_norm` 最优，logloss 0.7075 vs `blend_norm` 0.7321 vs `raw_norm` 1.1897 |
| recent | `uniform` / `market_norm` 接近，`raw_norm` 明显差 |
| live_filled_only | `blend_norm`/`market_norm` 接近，都是机会子集诊断 |

量化解读：

- raw-normalized 分布在 holdout/recent 显著不可靠。
- holdout 上 market-normalized 分布最稳，因此 basket objective 应保持 market-anchored。
- blend 可用于候选触发，但如果直接作为 optimizer 概率目标，会放大 raw 近期退化和尾部依赖。
- 继续加复杂 objective 前，应该先解决分布校准和 walk-forward 验证。

### Basket Walk-Forward Research：目标选择复核

文件：

```text
scripts/analysis/city_selection/research_city_day_basket_walkforward.py
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-walkforward.md
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-walkforward.json
```

研究问题：

```text
不用同一窗口选规则又验规则；用 14 个 settled target dates 训练选目标，再用后 3 天测试。
```

候选规则固定为：

```text
blended_single
heuristic_pr2b
combo_risk
combo_market_risk
combo_market_tail
```

测试结果：

| policy | ROI | weighted top5 removed ROI | positive folds |
|---|---:|---:|---:|
| selected_by_train_score | +1.99% | -42.09% | 40.0% |
| always_blended_single | +0.49% | -20.04% | 40.0% |
| always_heuristic_pr2b | +3.61% | -40.74% | 40.0% |
| always_combo_risk | +20.88% | -72.89% | 80.0% |
| always_combo_market_risk | +35.66% | -70.87% | 80.0% |
| always_combo_market_tail | -24.13% | -103.57% | 20.0% |

量化解读：

- train-selected selector 没有跑赢固定 baseline，暂时不是可用的目标选择器。
- `combo_market_risk` headline 最强，但 top5 removed 更差，仍是 tail-dependent。
- `combo_market_tail` 过度严格，交易数只有 22 legs，测试 ROI 为负。
- walk-forward 当前用途是拒绝过拟合候选，不是批准任何 basket live/canary。

### Basket vs Legacy Bucket Baselines：公平 baseline 对比

文件：

```text
scripts/analysis/city_selection/compare_city_day_basket_vs_legacy_baselines.py
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-vs-legacy-baselines.md
docs/archive/analysis/2026-06/2026-06-06-city-day-basket-vs-legacy-baselines.json
```

研究问题：

```text
basket 是否真的比旧的逐 bucket 独立下单更好，还是只是继承了同一个天气模型/市场 regime 问题？
```

对比口径：

- 同一批 `fact_signal_candidates` settled rows。
- `decision_window_missing = 0`，代表 T-22~24h 决策窗；本轮样本 `hours_to_settle` p10/median/p90 都是 23h。
- 旧策略 baseline 必须拆开看，不能都叫 `mid_price_v1`：
  - `mid_price_core_v1 25-75` 是最早版本，历史 live 样本最多，应作为 primary legacy baseline。
  - `mid_price_core_v1 side_band` 是后续优化版本，live 样本明显更短，应作为 optimized short-window baseline。
- 反事实 baseline 不是 loose edge，而是 live-like entry band：
  - `legacy_25_75_e10`: BUY_YES/BUY_NO 0.25-0.75, edge >= 0.10
  - `legacy_side_band`: BUY_YES 0.20-0.45 edge >= 0.20; BUY_NO 0.35-0.65 edge >= 0.10
- basket/optimizer 同时看：
  - `same_entry_band`: 给 basket 同样 entry-price universe
  - `legacy_raw_triggers`: 只在旧 raw 策略会触发的 rows 上做 grouping/sizing

关键结果：

| slice/profile | legacy raw | basket same band | combo market risk same band | 量化解读 |
|---|---:|---:|---:|---|
| full / 25_75 | +4.95% | +6.38% | +17.90% | basket 略优，combo 更强 |
| holdout / 25_75 | -5.62% | -12.49% | +12.03% | 旧 25_75 本身退化，概率/市场 regime 是主因之一 |
| recent / 25_75 | +2.32% | -5.87% | +14.52% | PR2b basket 不如旧逐 bucket，combo headline 好但 tail 仍差 |
| recent / side_band | +28.25% | +23.83% | +43.97% | side_band baseline 很强，basket 不能甩锅给天气模型 |
| live_filled / side_band | +17.88% | +35.46% | +87.04% | 真实可成交机会子集上 combo 有信号，但仍要看 top5 tail |

实际 live settled 参考：

| live strategy | settled fills | target date range | ROI | 备注 |
|---|---:|---|---:|---|
| `mid_price_core_v1 0.25-0.75` | 331 | 2026-05-16 -> 2026-06-04 | +2.95% | 最早版本，历史样本最多 |
| `mid_price_core_v1 side_band` | 48 | 2026-06-01 -> 2026-06-04 | +18.76% 合并口径约值 | 优化后短样本，不能直接代表长期 baseline |

量化解读：

- 用户指出的问题成立：basket 不能只看 ROI，必须先对比旧的 per-bucket independent baseline。
- primary baseline 是 `mid_price_core_v1 25-75`；`side_band` 是短样本优化 baseline，二者要分开报告。
- 当旧 baseline 也亏时，优先怀疑概率模型/市场 regime；当旧 baseline 赚钱而 basket 输时，优先怀疑 basket objective。
- `legacy_side_band` 在 recent 和 live_filled 子集很强，说明不是所有问题都能归因给天气模型。
- `combo_market_risk` 在 fair baseline 下仍有 headline alpha，但多处 top5 removed ROI 为负，不能据此上线。
- 后续研究目标应从“提高 basket ROI”改成“在不牺牲 legacy side_band 稳健性的前提下，证明 grouping/sizing 带来增量”。

---

## 2. Basket 算法本身为什么还要研究

当前 `city_day_basket.py` 是启发式 prototype，不是最终组合优化器。

当前做法：

```text
1. 对每个 bracket 算 BUY_YES / BUY_NO executable edge
2. 同 bracket YES/NO 冲突时保留 edge 更大的一边
3. 按 edge 排序选 top N legs
4. 计算 payoff_by_final_temp / worst_case_loss / expected_value
5. 过 city_day_cap / EV / stability gate
```

核心不足：

1. **不是全组合优化**  
   现在没有枚举所有组合，也没有比较 `YES 23` vs `NO 24+NO 25` 等组合的 risk-adjusted objective。

2. **概率分布未严格归一化**  
   现在 EV 用 bracket 级 `p_yes_used`，不是一个严格的 city-day 温度分布。理想状态应该有：

   ```text
   P(23C) + P(24C) + P(25C) + ... = 1
   ```

3. **risk objective 还简单**  
   当前只有 `worst_case_loss` 和 cap。下一步应比较 EV、worst loss、CVaR、tail sensitivity、hit-bracket stress。

4. **参数样本内优化风险高**  
   `$3/$8`、cap `$15`、`max_legs=4` 都必须 walk-forward 验证。

推荐 basket 研究方向：

```text
1. 用 market-normalized 分布作为 basket objective 的主 anchor
2. blend 只先作为候选触发和 shadow 对比，不直接放大到 optimizer objective
3. 保留 legacy per-bucket raw selector 和 PR2b heuristic 作为双 baseline；combo optimizer 继续研究，不 canary
4. 对 objective 做 walk-forward 验证，禁止同窗调参+验参；当前 walk-forward 还没有通过
5. 用 PR3 shadow 的逐 snapshot 数据复核 forecast jump / side flip / 独立 NO book
```

---

## 3. 时间窗和上游模型口径

当前 PR2/PR2b 离线评估读取 `fact_signal_candidates`，代表 snapshot 是 T-22~24h 决策窗内最接近中点的 snapshot，约等于 T-23h。

这不是完整生产 replay：

- 不是每 30 分钟逐 snapshot replay。
- 不含真实 `forecast_jump_f` / `side_flip_count_today`。
- BUY_NO book 仍有 `1 - yes_bid` proxy 缺口。

GFS/ECMWF 选择本轮不改：

- basket 只消费上游已生成好的候选信号。
- 每城 primary forecast model 继续沿用现有配置。
- GFS 更新时间、forecast jump、side flip 等问题应先在 PR3 shadow 双写中记录，再决定是否改 live。

---

## 4. 后续新窗口建议

### 窗口 1：跑策略 / PR3 shadow

目标：

```text
先把 blender 字段双写到 N100 lineage，不改下单。
再把 basket shadow decision 双写，不改下单。
```

边界：

- 不改 production execution policy。
- 不开新 strategy_instance。
- 不提交 basket live order。
- 只增加可观测字段和 shadow artifact。

### 窗口 2：研究 basket 算法

目标：

```text
把 heuristic top-N basket 升级成可解释的 city-day portfolio optimizer。
```

优先问题：

- 温度分布归一化。
- 组合枚举和 objective 设计。
- 和 legacy per-bucket raw selector 的同窗、同 entry-band 对比。
- `$3/$8/cap/max_legs/edge_threshold` walk-forward。
- tail dependency 和 top-k sensitivity。
- live_filled 子集与真实 live PnL 的口径区分。

---

## 5. 一句话结论

当前正确路线是：

```text
先 shadow 跑通 blender 数据链路；
并行研究 basket 核心算法；
basket 未通过稳健性验证前，不进入 canary。
```
