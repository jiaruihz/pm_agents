# Weather Edge Engine Current State — 2026-06-06

> 本文档把 PR1/PR2/PR2b dev log 里的关键结论抽成稳定入口，供后续两个新窗口分别接手：
> 1. **跑策略 / shadow 双写链路**
> 2. **研究 basket 核心算法**

生产结论先写在前面：**不退役旧策略、不开新 canary、不改 N100 live 下单配置。**

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
scripts/analysis/backtest_weather_edge_engine_blended_single.py
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
- `raw_single -> blended_single` 在 full sample 和 live_filled opportunity 子集上都有改善。
- 但最近 7 天 recalibration 显示 best alpha = 0.00，raw model 近期退化，不能贸然加 raw 权重。

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
scripts/analysis/recalibrate_blend.py
scripts/analysis/eval_city_day_basket.py
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
scripts/analysis/tune_city_day_basket.py
scripts/analysis/validate_city_day_basket_robustness.py
docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.md
docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.md
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
scripts/analysis/backtest_weather_edge_engine_blended_single.py
docs/analysis/2026-06/2026-06-06-blended-single-v0-backtest.md
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

### Basket Optimizer Research：组合枚举初版

文件：

```text
scripts/analysis/research_city_day_basket_optimizer.py
docs/analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.md
docs/analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.json
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
```

初步结论：

- `combo_ev` / `combo_risk` 能提高 headline ROI，但会更集中地选择少数高尾部腿。
- holdout / recent 的 top-5 removed ROI 比 heuristic 更差，说明组合枚举本身不自动带来稳健性。
- `combo_guarded` 这种简单高赔率过滤太粗，会砍掉全样本盈利，也没有修复 holdout tail risk。
- 当前问题不是“top-N vs 枚举”这么简单，而是 objective 和 city-day 温度分布还不够可靠。
- 下一步应优先研究归一化温度分布和 walk-forward objective，而不是继续加参数。

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
1. 构建归一化 city-day temperature distribution
2. 枚举候选组合，而不是 edge 排序 top N
3. 对组合计算 EV / worst loss / CVaR / tail sensitivity
4. 做 walk-forward 参数选择，禁止同窗调参+验参
5. 用 PR3 shadow 的逐 snapshot 数据复核 forecast jump / side flip
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
