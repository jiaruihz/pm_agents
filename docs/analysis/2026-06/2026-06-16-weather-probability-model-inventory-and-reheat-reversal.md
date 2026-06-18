# Weather Probability Model Inventory and Reheat-Reversal Reuse

Status: working-reference
Generated: 2026-06-16

## 结论先行

低价 BUY_YES 反转不应该只继续用一个裸 `edge = model_p_yes - price`。更合理的下一版是：

```text
p_reversal_yes = raw_or_blended_bracket_p_yes * p_reheat_to_target_context
edge_reversal = p_reversal_yes - yes_ask
```

`reheat_risk` 这条底层模型可以复用，但不能直接把 `pWin` 当成所有 bracket 的 `model_p_yes`。current YES/no-reheat 模型回答的是“当前 running-max 档会不会守住”；低价 YES 反转要回答的是“后面会不会二次升温，并且升到哪个目标 bracket”。两者是同一个物理机制的正反面，但标签和落点不同。

## 数据底座设计

结论：**共用一个数据底座，分开两个模型头。**

不要让 theta/current YES 和 low-price YES reversal 各自重新 materialize observed max、orderbook、settlement。它们都应该读同一个 source-aligned intraday city-hour fact layer，然后在同一行上派生不同 labels / candidates：

```text
shared_intraday_city_hour_facts
  -> theta_current_yes_head: P(current bracket keeps winning)
  -> low_price_yes_reversal_head: P(target low-price YES wins after reheat)
  -> optional_expression_layer: choose current YES / d1 NO / low-price YES / range basket
```

### 共用底座的 row grain

建议底座 row grain 固定为：

```text
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket/outcome
```

其中 city-hour 级公共字段包括：

- source alignment: `city`, `target_date`, `unit`, `settlement_source_class`, station/ICAO。
- observed state: current temp、running max、running-max bracket、decline from max。
- weather path features: dewpoint、RH、wind、sky、1h/3h temp change、dewpoint/RH change。
- orderbook state: YES/NO best bid/ask、size、spread、snapshot age。
- settlement truth: final winning bracket、whether current bracket held、whether target bracket won。
- forecast distribution: raw `model_p_yes` / blended `p_yes_used` for every bracket when available。

### 分开的模型头

同一个底座上保留两个独立 target：

```text
theta_current_yes_head
label = current_yes_wins
question = current running-max bracket 是否守住
output = p_no_reheat / p_current_yes_win
```

```text
low_price_yes_reversal_head
label = target_yes_wins
question = 低价目标 YES 是否因后续 reheat 命中
output = p_reheat_to_target / p_reversal_yes
```

它们可以共享特征工程，但不要共享 label、阈值、ROI 结论。theta 是 no-reheat 低风险表达；低价 YES 是 reheat/convexity 表达。一个模型头表现好，不自动证明另一个头可 live。

### 为什么不完全分开

完全分开会产生三个问题：

1. observed max 口径容易漂：同一时刻 current bracket、decline、d1 distance 会算出不同版本。
2. orderbook 对齐容易漂：一个脚本用 latest-before，一个脚本用 snapshot file row，结果不能比较。
3. settlement label 容易漂：current YES、d1 NO、target low-price YES 其实应该共享同一个 final winner。

共用底座后，theta 和反转只是在同一事实行上的两个 label/head，后续才方便比较：

```text
same city-hour state:
  current YES 是否更好
  d1 NO 是否更好
  low-price YES reversal 是否更好
```

### 为什么模型头不能合并

合并成一个“万能 p”也不对。原因是 payoff 和分类目标不同：

- theta current YES：高胜率/低赔率，赌“不再升温”。
- low-price YES reversal：低胜率/高凸性，赌“会升温且落入目标档”。
- d1 NO carry：高 ask 低收益，赌“不跳到相邻更高档”。

所以底座统一，模型分头，表达层再统一比较。

## 当前 p 的版本

| 名称 | 含义 | 主要入口 | 可否直接用于低价 YES |
|---|---|---|---|
| `model_p_yes_raw` / `model_p_yes` | 某 city-day 整组 bracket 的天气模型概率，单点 forecast + 历史误差 bootstrap | `compute_bracket_probs()` via `weather_edge_paper.py` | 可以，当前低价 YES edge 就用它 |
| `market_implied_p_yes` / `market_yes_price` | 市场 YES 价格，近似市场隐含概率 | orderbook / snapshot facts | 可以，作为价格和 blend 输入 |
| `model_p_yes_used` / blended p | `alpha * raw_model + (1-alpha) * market`，默认 alpha=0.30，黑名单城市 alpha=0.10 | `weather_dashboard/blend/blender.py` | 可以，适合做更保守的 edge |
| `p_current_yes_win` / theta current YES | 当前 observed running-max bracket 最终守住的概率 | `research_theta_yes_current_full_replay_v8.py` | 不能直接替代 bracket p；可作为 no-reheat/reheat 条件模型 |
| `p_d1_no_lose` / theta NO carry risk | 买 d1 NO 会不会被二次升温打穿的风险 | `research_theta_no_weather_model_selector_v6.py` / v3 walk-forward | 可作为 reheat risk filter |
| Range/RV mass p | 多个 bracket 的模型概率质量和 | Range RV scripts | 不适合单腿低价 YES，适合组合表达 |

## raw `model_p_yes` 算法

当前生产/事实表主概率链路是：

1. 每个 city-day 取 GFS/ECMWF 预测日最高温。
2. 取该城市历史 forecast error 分布。
3. 用 `forecast_max + historical_error` 模拟最终最高温。
4. round 到整数温度。
5. 统计模拟最高温落入每个 bracket 的比例。

这个输出是一个 city-day 下天然连贯的逐档分布。低价 BUY_YES 当前的 edge 是：

```text
BUY_YES edge = model_p_yes - decision_entry_price
```

## blended p 算法

blender 不是新天气模型，只是把 raw model 往市场价格收缩：

```text
p_yes_used = alpha * model_p_yes_raw + (1 - alpha) * market_implied_p_yes
```

默认 `alpha=0.30`；raw 模型黑名单城市用更低 alpha。它适合低价 YES 反转的原因是：裸 `model_p_yes` 在低价尾部容易过度自信，blend 可以防止极端 edge 被模型噪音放大。

## reheat-risk p 算法

reheat-risk 的核心不是 day-1 forecast，而是日内 observed max：

```text
current observed max = O
final max M >= O
剩余问题 = 22:00 前还会不会二次升温改写 O
```

当前 theta current YES 模型把 label 定义为：

```text
current_yes_wins = winner_bracket == current_running_max_bracket
```

用 logistic regression 训练 `p(current bracket keeps winning)`，特征包括：

- local decision hour
- month
- 当前温度相对 running max 的 decline
- running max 到相邻更高档的距离
- 当前 METAR/IEM 温度、露点、湿度、风速、云量
- 1h/3h 温度变化、露点变化、湿度变化
- city/unit categorical
- 可选加入 current YES / d1 NO 价格和盘口 size

所以 theta 的 `pWin` 本质是 `p(no_reheat)`，不是 `p(某个低价 bracket yes)`。

## 能否反向用于低价 YES 反转

可以，但要改目标标签。

当前 theta YES 是：

```text
p_no_reheat_current = P(final bracket == current running-max bracket)
```

低价 YES 反转需要的是：

```text
p_reheat_to_target = P(final bracket == target low-price YES bracket)
```

或者更粗一点：

```text
p_any_reheat = P(final max exceeds current running max enough to leave current bracket)
```

然后再和传统 bracket 分布结合：

```text
p_reversal_yes =
  base_bracket_p_yes
  * reheat_context_multiplier
```

交易条件可以变成：

```text
side = BUY_YES
yes_ask < 0.25
base_edge = base_p_yes - yes_ask >= 0.10/0.15
p_reheat_to_target 或 p_any_reheat 足够高
decline 不强，或出现 warm-advection / cloud / wind / dewpoint 支持
每 city-date 只选 1 个最高 convexity 候选
```

## 为什么这比裸低价 YES edge 更合理

裸低价 YES edge 只知道“模型觉得这个 bracket 便宜”。它不知道这条便宜 YES 是：

- 早盘 forecast 分布给出的尾部概率；
- 还是日内已经出现二次升温迹象，市场还没反应；
- 或只是模型误差分布过宽造成的假尾部。

reheat-risk 底层加进去后，信号会从：

```text
forecast distribution says cheap
```

升级成：

```text
forecast distribution says cheap
AND observed intraday state supports reheat into this bracket
```

这才是“抓反转”的正确定义。

## 下一版实验建议

下一版不直接上 live，先做 `low_price_yes_reheat_reversal_v2`：

1. 从 raw orderbook/materialized observed-max replay 中枚举低价 YES。
2. 为每个候选补 reheat-risk 特征。
3. 训练两个标签：
   - `target_yes_wins`: 目标低价 YES 是否最终命中。
   - `any_reheat_out_of_current`: 是否离开当前 running-max bracket。
4. 比较三组 edge：
   - raw `model_p_yes - price`
   - blended `p_yes_used - price`
   - reheat-adjusted `p_reversal_yes - price`
5. 只允许输出 shadow candidates，不输出 live action。

## 当前动作

整理概率口径；不改 N100/live；不发布 live 策略。
