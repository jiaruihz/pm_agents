# Weather Edge Engine Implementation Plan

> 定稿 2026-06-05。本文档是当前天气策略的**统一执行方案**，不再拆成多套模型版本。
> 核心目标：把单 bracket 的独立 edge 信号，升级成按 `city + target_date`
> 做一组相关决策的交易引擎。

---

## 0. 最终目标

最终系统只保留一条主路径：

```text
raw forecast distribution
→ model_p_yes_raw
→ market-anchored probability blend
→ forecast stability checks
→ city-day basket decision
→ orderbook executable edge
→ basket-level sizing and execution
→ weekly kill criteria
```

一句话：**概率层给每个 bracket 一个更可信的概率，basket 层决定同一个城市同一天到底该交易哪一组 bracket。**

不再做：

- 不再让每个 bracket 独立 `edge > threshold` 就下单。
- 不再裸用 `model_p_yes_raw - market_price`。
- 不先做 per-city `alpha/beta/gamma` 训练系统、seasonal `gamma`、weekly retrain、复杂 dashboard API。
- 不先做 Kelly、多档联立最优仓位、跨城组合优化。

---

## 1. 设计原则

### 1.1 交易单位是 city-day basket

同一个城市同一天最终只会落在一个温度结果上，所以 bracket 之间天然相关：

- 多个 YES 是互斥结果下注。
- 多个 NO 是相关 basket，大多数会赢，但命中其中一个被买 NO 的 bracket 时该腿亏满。
- 同一 bracket 当天 YES/NO 翻边后继续加仓，会把 forecast 噪音变成自我冲突。

因此主逻辑必须从：

```text
for each bracket:
    if edge > threshold:
        trade
```

改成：

```text
for each city-date:
    collect all bracket candidates
    evaluate combined payoff by possible final temp
    choose one coherent basket
    size by basket risk, not leg count
```

### 1.2 概率层先用 market-anchored blend

当前证据支持的低风险概率口径：

```text
p_yes_used = 0.30 * model_p_yes_raw + 0.70 * market_implied_p_yes
```

其中：

- `model_p_yes_raw` 继续来自现有 `compute_bracket_probs()`。
- `market_implied_p_yes` 来自当前 market price。
- Milan / Lucknow / Austin / Beijing 这类 raw model 明显差的城市，标记为 raw-model blacklist，默认更靠近 market 或只 shadow。

这个 blend 不是终局模型的全部，它只是比 raw probability 更稳的输入。

### 1.3 每城保留 primary forecast model

不要因为一次 snapshot 或少量样本动态切 GFS/ECMWF。

- 每个城市保留当前验证过的 primary forecast model。
- 多模型 spread 后续作为不确定性和降 size 信号。
- 只有 walk-forward 证明某城市换模型稳定更好，才改 primary。

### 1.4 执行 edge 必须用 orderbook

纸面 edge 不是可交易 edge。最终交易必须看真实可成交价格：

```text
BUY_YES executable_edge = p_yes_used - yes_best_ask
BUY_NO  executable_edge = (1 - p_yes_used) - no_best_ask
```

注意：YES / NO 是两个独立 token，不能长期用 `1 - yes_bid` 假装 NO ask。
如果某些历史记录缺 NO book，可以在分析里显式标记为 fallback，但生产逻辑必须读独立 NO book。

---

## 2. 核心模块

### 2.1 Probability Blend

新增一个纯函数模块，用于生成 `p_yes_used`。

目标文件：

```text
weather_dashboard/blend/blender.py
tests/blend/test_blender.py
weather_dashboard/blend/city_blend_config.json
```

最小功能：

- 输入：`city`, `model_p_yes_raw`, `market_implied_p_yes`。
- 输出：`p_yes_used`, `blend_alpha`, `blend_beta`, `blend_mode`。
- 默认：`alpha=0.30`, `beta=0.70`。
- blacklist 城市：使用 market-heavy 或 shadow 标记。
- clip 输出到 `[0.001, 0.999]`，避免下游极端值问题。

暂不做：

- per-city alpha/beta/gamma 训练。
- seasonal residual 接入。
- weekly retrain。

这些只作为研究脚本，不进入主执行路径。

### 2.2 Forecast Stability Checks

新增稳定性诊断字段，先用于 basket 降级：

```text
forecast_values_hash
forecast_max_hour_local
forecast_jump_f
side_flip_count_today
narrow_bracket
```

规则初版：

- 同一 city-date-bracket 当天出现 YES/NO flip：该 bracket 默认 shadow 或降 size。
- 最近窗口 `forecast_jump_f > 1.0°F`：该 city-day basket 降 size。
- 窄 bracket 且 forecast 接近边界：提高 edge 门槛。

### 2.3 City-Day Basket Builder

这是本计划的核心新增模块。

目标文件：

```text
weather_dashboard/basket/city_day_basket.py
tests/basket/test_city_day_basket.py
scripts/analysis/eval_city_day_basket.py
```

输入是一组同一 `city + target_date + snapshot_ts` 的 bracket candidates：

```text
city
target_date
snapshot_ts
bracket
p_yes_used
yes_best_ask
no_best_ask
raw_edge
executable_edge_yes
executable_edge_no
forecast_jump_f
side_flip_count_today
narrow_bracket
existing_city_day_exposure
```

输出一个 basket plan：

```text
city
target_date
snapshot_ts
basket_id
selected_legs[]
payoff_by_final_temp{}
worst_case_loss
expected_value
max_notional
decision: TRADE | REDUCE | SHADOW | SKIP
reason_codes[]
```

初版规则：

1. 每个 city-day 只生成一个 basket。
2. BUY_NO 优先，但限制最多 N 条 NO legs。
3. 同一 bracket 当天发生 side flip 后，不允许加反向 live 仓，只允许 shadow。
4. YES 和 NO 同时有 edge 时，按 basket payoff 选择整体更稳的一组，不允许两边都打。
5. 如果 basket worst-case loss 超过 city-day cap，按比例缩小或 shadow。
6. 如果所有 edge 只在 paper price 上成立、orderbook ask 上不成立，直接 skip。

### 2.4 Basket-Level Sizing

初版不用 Kelly，只做简单风险分档：

```text
SHADOW_ONLY: executable_edge <= threshold or stability bad
SMALL:       edge medium, basket risk acceptable
NORMAL:      edge high, no forecast jump, no side flip
SKIP:        executable edge <= 0 or worst-case loss too high
```

建议初始参数：

```text
single_leg_notional_small = 3
single_leg_notional_normal = 5
city_day_notional_cap = 15
daily_notional_cap = 50
max_no_legs_per_city_day = 3
forecast_jump_reduce_threshold_f = 1.0
```

这些参数要写成配置，不散落在代码里。

---

## 3. 数据字段

### 3.1 Snapshot / Signal 双写字段

新旧字段都保留，方便归因：

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

### 3.2 Orderbook 字段

生产和分析都应优先使用独立 YES / NO token book：

```text
yes_best_bid
yes_best_ask
yes_bid_size
yes_ask_size
no_best_bid
no_best_ask
no_bid_size
no_ask_size
book_fetched_at_utc
book_status
```

### 3.3 Basket 字段

新增 basket 级别输出：

```text
basket_id
basket_decision
basket_reason_codes
basket_expected_value
basket_worst_case_loss
basket_max_notional
basket_selected_leg_count
basket_payoff_json
```

---

## 4. 实施顺序

### Step 1: 本机实现概率 blend 和 basket 纯函数

文件：

```text
weather_dashboard/blend/blender.py
weather_dashboard/basket/city_day_basket.py
tests/blend/test_blender.py
tests/basket/test_city_day_basket.py
```

验收：

- 单元测试覆盖 blacklist、clip、缺 book、side flip、worst-case cap。
- 不改 N100。
- 不改 live 行为。

### Step 2: 离线回放评估 basket

文件：

```text
scripts/analysis/eval_city_day_basket.py
docs/analysis/2026-06/2026-06-XX-city-day-basket-eval.md
```

评估内容：

- raw 单腿规则 vs blended 单腿规则 vs blended basket 规则。
- BUY_NO basket 的最坏情形亏损。
- side flip 样本如果 shadow，会少亏/少赚多少。
- orderbook ask 后 executable edge 是否还成立。
- 去掉 top-1/top-5 后 ROI 是否仍为正。

验收：

- basket 规则不能只靠减少交易数提高 ROI，必须报告 missed profit 和 avoided loss。
- 若 orderbook 缺口导致结论不稳，显式列出缺口，不允许静默 fallback。

### Step 3: N100 shadow 双写

生产端只做双写，不改变下单：

```text
model_p_yes_raw
model_p_yes_used
blend metadata
stability metadata
basket shadow decision
```

验收：

- 7 天 ingest 无错误。
- 同一 snapshot 可以解释 raw → used → basket decision 的完整链路。
- shadow basket 与实际 live fills 可在本机 join。

### Step 4: 小额 canary

只开一个统一策略实例，不创建多套版本：

```text
strategy_instance = weather_edge_engine
probability_source = blended
decision_mode = city_day_basket
notional_per_leg = 3-5
city_day_notional_cap = 15
daily_notional_cap = 50
```

验收：

- canary fills 足够后，比较同期 raw/live shadow。
- 没有 forecast jump / side flip 样本贡献主要亏损。
- orderbook executable edge 仍为正。

### Step 5: 扩大到主路径

只有在 Step 2-4 都通过后，才把旧的单腿规则退到 shadow。

不做“大量并行版本”。最终只保留：

```text
weather_edge_engine = blended probability + city-day basket + executable edge + basket sizing
```

---

## 5. Kill Criteria

这些判据写成代码和 dashboard 检查，不只留在文档里：

| 检查 | 触发线 | 动作 |
|---|---|---|
| 尾部敏感性 | n >= 80 且删 top-5 后 ROI < 0 | 退回 shadow |
| BUY_NO 方向 | BUY_NO 30d ROI < 0 | BUY_NO 降 size 或暂停 |
| 集中度 | 去掉最大贡献城市后 ROI < 0 且无第二城接力 | 降总 notional |
| 执行 edge | 主要利润价桶 ask 后 edge <= 0 | 禁止 live，只留 paper |
| forecast 稳定性 | 大额亏损集中在 jump/flip 样本 | jump/flip 默认 shadow |
| basket 风险 | worst-case loss 超 city-day cap | 缩 size 或 skip |

---

## 6. 第一批 PR

### PR 1: 本机纯函数骨架

```text
weather_dashboard/blend/blender.py
weather_dashboard/basket/city_day_basket.py
tests/blend/test_blender.py
tests/basket/test_city_day_basket.py
```

不碰生产，不碰 N100。

### PR 2: 离线回放评估 + 周度重新校准脚本

```text
scripts/analysis/eval_city_day_basket.py
scripts/analysis/recalibrate_blend.py       # sklearn 唯一生产相邻用途
docs/analysis/2026-06/2026-06-XX-city-day-basket-eval.md
```

`recalibrate_blend.py` 的位置：

- 每周离线跑一次，输入是近 N 天的 snapshot + pm_history。
- 用 `sklearn` 的 `IsotonicRegression` + `LogisticRegression` 重做一次 raw vs market vs ensemble 的 Brier 对比。
- 输出当前最优 alpha 与 global 0.30 的偏差。
- **不自动改生产**。偏差超过阈值（例如 |delta_alpha| > 0.10 或 Brier 改进 > 2%）只产生研究告警，由人决定是否更新 `city_blend_config.json` 的 global default。
- 与 §5 kill criteria 联动：alpha 漂移大幅扩张是「市场/模型相对结构变了」的信号。

### PR 3: shadow 双写

部署到 N100 前必须走 `weather-strategy-deploy` 的 git-first 流程。

---

## 7. 暂缓项

这些不是否定，而是不要挡住当前主路径：

- per-city alpha/beta/gamma 训练。
- seasonal residual 接入生产。
- 多模型 spread 生产切换。
- Kelly / multi-outcome optimizer。
- 跨城资金分配。
- 新 ML 框架。

它们以后都可以接在同一个 `weather_edge_engine` 后面，不需要重写方向。

---

## 8. 训练数据与样本量

这一节说明本计划「为什么敢用 global 0.30/0.70」「为什么暂时不做 per-city α/β/γ」「过去两年数据可以用到什么程度」。

### 8.1 现有数据窗口

| 数据 | 覆盖 | 用途 | 能不能训 blender |
|---|---|---|---|
| Polymarket snapshot（含 market_implied_p） | ~31 天 | blend 输入 / 校准 / executable edge | 是，但样本短 |
| `pm_history`（结算真值） | ~25 天 | label | 是，但样本短 |
| live_real fills | 26 天，67 笔 | 实盘 PnL 归因 | 不够训权重，只够诊断 |
| GFS / ECMWF / HRRR / JMA / ICON-EU / AROME cache | 355 天左右 | raw model 输入、多模型 spread 研究 | 训 raw model，不训 blender |
| WU observation cache | ~735 天 | seasonal residual / raw model 再训 | 训 raw model 和 seasonal γ，不训 blender |

### 8.2 26 天样本对训权重意味着什么

时间分割 + LOO 验证显示 global `alpha=0.30, beta=0.70` 相对单纯 market 提升 ~1% Brier，提升微弱但方向一致。

26 天 + 1577 条已结算信号支持以下做法：

- **可以**：定一个 global 默认值（本计划用 0.30/0.70）。
- **可以**：把 raw-model 明显差的城市（Milan / Lucknow / Austin / Beijing）单独 blacklist。
- **不可以**：每个城市训一套独立 α/β/γ。城市切片样本太小，权重会过拟合最近 3 周噪音。

因此 v1 只做 global blend + 4 城黑名单，不开 per-city 训练。

### 8.3 过去两年数据能干嘛、不能干嘛

`market_implied_p` 只在 Polymarket snapshot 开始采集后才存在（~25–31 天）。这意味着：

- **过去两年数据可以做的事**：
  - 重新训 raw forecast → bracket probability 的映射（GFS/ECMWF + 观测）。
  - 训 seasonal residual：用 day-of-year k-NN 把同期偏差作为修正项，作为后续 γ 通道接入。
  - 多模型 spread 校准、coverage 检查、forecast jump 阈值的统计基线。
- **过去两年数据不能做的事**：
  - 训 `alpha / beta` 权重：没有历史 market price，没法拟合 raw vs market 的最优 mixture。
  - 训 BUY_NO basket 的 worst-case loss 分布：需要历史 orderbook，目前 orderbook 只回到 2026-05-19。

### 8.4 分阶段使用 2 年数据

| 阶段 | 时间 | 用什么数据 | 输出 |
|---|---|---|---|
| v1（本计划 Step 1-4） | 现在 | 26 天 snapshot + 25 天 pm_history | global 0.30/0.70 blend + basket + executable edge |
| v1.5 | Step 4 通过后 | 735 天 WU obs + 355 天 GFS | 训 seasonal residual，作为 γ 通道接到现有 blend |
| v2 | 实盘样本 ≥ 12 周后 | 累计实盘 + snapshot | 重新评估是否值得开 per-city α/β，仍需 LOO 验证 |

v1.5、v2 都不进本 PR 系列。先把 v1 跑稳。

### 8.5 我们实际使用的概率链

最终走的不是单一概率模型，是一条带前置和后置门的链：

```text
raw forecast (GFS / ECMWF / WU obs)
    └── compute_bracket_probs()              ← 现有生产函数，保留不动
        → model_p_yes_raw                    ← 仍是单点预测 + global error bootstrap
                                                  即「原来的概率模型」
        ↓
    market_implied_p_yes（从当前 Polymarket price）
        ↓
    global blend (alpha=0.30, beta=0.70；blacklist 城市偏 market)
        → p_yes_used                          ← 用于决策的概率
        ↓
    forecast stability gate（jump / flip / narrow → 降级或 shadow）
        ↓
    city-day basket decision（互斥+相关 bracket 整体决策）
        ↓
    orderbook executable edge gate（独立 YES/NO book，真实 ask）
        ↓
    basket-level sizing → 下单
```

所以回答「最后用的是哪个概率模型」：

- **底层仍是原来的概率模型**：现有 `compute_bracket_probs()` 不被替换，它产出 `model_p_yes_raw`，是这条链的第一节。
- **它不再被直接当作下单概率**。它的输出会被市场价 blend、被稳定性门降级、被 basket 重新组合、再被 orderbook 验过一遍。
- 真正用于交易决策的不是某个新模型，是 **raw + market + 稳定性 + basket + 执行盘口** 这一整套流水线。命名上叫 `weather_edge_engine`，它的「概率」就是上面流水线中的 `p_yes_used`。

后续 seasonal γ、per-city α/β、Kelly、多模型 spread 都是接在这条链同一个位置（probability 段或 basket 段）的扩展，不需要替换主链。

---

## 9. 参数清单与可调边界

所有写死的数字集中在这里，方便后续替换。**约束：除黑名单/白名单外，参数必须放在 `city_blend_config.json` 或同级配置，不许散在代码常量里。**

### 9.1 概率层

| 参数 | v1 写死值 | 位置 | 何时改 | 改的方法 |
|---|---|---|---|---|
| `blend_alpha`（raw 权重） | 0.30 | `city_blend_config.json` global | 周度 `recalibrate_blend.py` 报告 \|Δα\| > 0.10 持续 4 周 | 改 config，重新 shadow 1 周再切 |
| `blend_beta`（market 权重） | 0.70 | 同上 | 同上 | 同上 |
| `blend_clip_lo / hi` | 0.001 / 0.999 | 同上 | 不预期改 | — |
| `raw_model_blacklist` | [Milan, Lucknow, Austin, Beijing] | 同上 | 该城连续 4 周 raw Brier 接近 market（差距 < 5%） | 移出黑名单，进入 shadow 评估 |
| `blacklist_alpha`（黑名单城市的 raw 权重） | 0.10 | 同上 | recalibrate 报告该城最优权重显著偏离 | 改 config |

### 9.2 稳定性门

| 参数 | v1 写死值 | 何时改 |
|---|---|---|
| `forecast_jump_reduce_threshold_f` | 1.0 °F | 回放显示阈值附近样本损失集中 → 降到 0.7；显著过保守 → 升到 1.3 |
| `side_flip_action` | shadow | 4 周内 flip 后样本 ROI 显著为正 → 放宽到「降 size 一档」 |
| `narrow_bracket_edge_multiplier` | 1.5×（窄桶需要 1.5 倍 edge 才进 live） | 窄桶 EV 长期为正 → 降到 1.2× |
| `stability_lookback_window` | 当日内 + 最近 2 个 snapshot | 不预期短期改 |

### 9.3 Basket 决策

| 参数 | v1 写死值 | 何时改 |
|---|---|---|
| `max_no_legs_per_city_day` | 3 | 4-leg basket 在回放上 worst-case loss 可接受且 EV 提升 > 10% → 升到 4 |
| `prefer_no_over_yes` | True | BUY_YES 在新样本上出现持续正 EV 时切到「按 EV 排序」 |
| `simultaneous_yes_no` | 禁止 | 不预期改（结构性约束） |
| `paper_only_edge_action` | skip | 不改 |

### 9.4 Sizing 与上限

| 参数 | v1 写死值 | 何时改 |
|---|---|---|
| `single_leg_notional_small` | $3 | canary 一周后 ROI > 0 且无单日大额回撤 → $5 |
| `single_leg_notional_normal` | $5 | 同上递进到 $8 |
| `city_day_notional_cap` | $15 | 单档 leg 升到 $5 后同步升到 $25 |
| `daily_notional_cap` | $50 | 月度 ROI 验证后升到 $100 |
| `edge_small_threshold` | 0.03 | 校准结果显示 0.03 边界附近样本 EV 不稳 → 调 0.04 |
| `edge_normal_threshold` | 0.06 | 同上 |

### 9.5 执行

| 参数 | v1 写死值 | 何时改 |
|---|---|---|
| `require_independent_no_book` | True | 不改（资金安全） |
| `book_max_staleness_seconds` | 60 | 实盘观察 book 抖动幅度后调 |
| `book_fallback_action` | skip live, allow shadow | 不改 |

### 9.6 当前明确**不**做的参数化

这些可调点存在但 v1 不开，避免一次引入太多自由度：

- per-city α/β/γ（属于 v2，仍需 12 周样本）。
- per-side（YES/NO）独立 α/β。
- 时段（早盘 / 临结算）独立阈值。
- 多模型 spread 权重作为输入（仍是 raw 单模型路径）。
- price bucket × side 的 sizing 表（side_band 策略里已经验证有效，详见 §10，留作 v1.5 注入）。

---

## 10. 与现有三策略的关系

线上当前在跑的三个策略实例：

| 实例 | 角色 | 大致 ROI（近 26 天 live_real） | 备注 |
|---|---|---|---|
| `mid_price_core_v1` | 围绕 mid 的简单挂单 | 正，但样本里 BUY_NO 撑大部分 | 单 bracket 独立 edge，没有 basket 概念 |
| `mid_price_core_v2` | v1 的低价拆单 + maker_queue 退役版 | 26 天表现弱于 v1（见 2026-06-03 复盘） | 2026-06-05 review 已建议先退 shadow |
| `side_band` | 0.25–0.75 入场带 + side × price bucket sizing | 短样本下方差大 | 提供了 sizing 的 side 与价位桶洞见 |

### 10.1 替换关系

`weather_edge_engine` 不是「再加一个并行实例」，目标是**一次性替换这三个**，因为它改的是更上游的层：

- 三个旧实例都是**执行策略**（同样的 raw 单 bracket edge → 不同挂单方式）。
- 新引擎改的是**信号 + 决策**层（概率 blend + basket 选择 + executable gate）+ 一个统一 sizing。
- 旧实例的执行洞见（side_band 的 side × price bucket sizing、v2 的低价拆单）会被吸收进 §9.6 的 v1.5 注入项，不是丢掉。

### 10.2 退役顺序（与 §4 实施顺序一致）

```text
Step 1-2（本机纯函数 + 回放）
    三个旧实例不动，照常 live。

Step 3（N100 shadow 双写）
    三个旧实例仍是 live，weather_edge_engine 只写 shadow basket decision。
    可在 dashboard 比对四套口径：v1 / v2 / side_band live  vs  edge_engine shadow。

Step 4（canary，小额）
    优先退役：mid_price_core_v2 → shadow only。
        （已被 2026-06-05 review 单独建议退役，本计划顺势执行。）
    保留 live：mid_price_core_v1、side_band，作为对照基准。
    新开 live：weather_edge_engine，单腿 $3–$5、城市日 cap $15、日 cap $50。

Step 5（扩大）
    canary 通过判据后：
        side_band       → shadow only
        mid_price_core_v1 → shadow only
        weather_edge_engine → 唯一 live 实例
    side_band 的 side × price bucket sizing 在 v1.5 作为参数表注入 weather_edge_engine。

回滚路径（任一阶段触发 §5 kill criteria）：
    weather_edge_engine → shadow only
    mid_price_core_v1   → 恢复 live（曾长期最稳）
```

### 10.3 通过判据（Step 4 → Step 5 的硬门）

不只看 ROI，避免「减少交易数刷高 ROI」假象：

- `weather_edge_engine` canary 累计 fills ≥ 30。
- 同期 ROI ≥ `mid_price_core_v1` 同期 ROI 的 0.8 倍（不需要严格更高，避免短窗噪音）。
- 同期 BUY_NO win_rate 不低于 v1 的 5 个百分点以内。
- §5 全部 kill criteria 未触发。
- shadow basket vs live fills 的偏差报告无系统性大额 missed profit（>20% 总 EV）。

通过后 Step 5 才能执行旧实例退役。否则停在 Step 4，调参再观察。
