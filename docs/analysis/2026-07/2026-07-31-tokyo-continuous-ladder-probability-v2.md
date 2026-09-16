# Tokyo continuous ladder probability v2

> 2026-07-31 修正：本文 market-specific score/trade 数字使用了
> weather-state → future-book join，可能让同一 book 匹配多个旧 weather
> states。相关数字已由 v3 的 book-time latest-as-of join 取代；weather-only
> 模型分数不受影响。

## 结论

Tokyo v2 已完成全量训练、三粒度 frozen 评估、exact first-seen 盘口回放和
zero-notional `now vs next JMA checkpoint` A/B。它现在能在每个 JMA 10-minute
checkpoint 输出同一份一致的
`P(remaining rise = 0 / 1 / 2 / 3+)`；`P(stay)` 直接等于该分布的 `P(delta=0)`，
不再维护互相可能矛盾的 binary/full 两套概率。

但本轮不能把新结构升级成交易模型：

- 主 checkpoint champion 仍是 direct multinomial HGB；
- multi-grain coherent hurdle 只在 2026H1 的 state-entry 粒度显著改善，7 月
  holdout 反向退化，保留为 challenger；
- collector-exact 同分母下，三套 weather-only 模型都没有在 Brier 上打败
  market，trade gate 仍为 `blocked`；
- 固定等下一份 JMA 的效果在 development 与 holdout 不稳定，不设为 entry
  rule。

研究代码没有修改 runner、plan/order/fill/exit 或任何 live 行为。

## v2 做了什么

1. **全历史 refit**
   - 参数拟合：2024-04-30 至 2025-06-30；
   - 独立选参/温度校准：2025-07-01 至 2025-12-31；
   - 冻结参数后，用截至 2025-12-31 的全部 `608` 个历史日期重训；
   - 2026H1、7 月 development/holdout 均未进入最终参数选择。
2. **一致概率结构**
   - direct champion：单一四分类分布；
   - coherent challenger：
     `P(0)=P(stay)`，
     `P(k)=P(leave)×P(k|leave), k∈{1,2,3+}`。
3. **三种粒度**
   - checkpoint：每个 10-minute 更新；
   - transition：`current_bracket` 或事前 path phase 改变；
   - state-entry：每日首次进入一个新 official-METAR bracket。
4. **multi-grain objective**
   - 每个 target date 内，checkpoint / transition / state-entry 各占 `1/3`
     训练质量，避免高频 plateau 行淹没真正的状态变化。
5. **可追溯 artifact**
   - feature semantic SHA256；
   - model-spec SHA256；
   - joblib artifact SHA256；
   - long-form prediction table，一行一个 outcome；
   - archive clock 明确标为
     `archive_observation_timestamp_not_first_seen`，不会冒充 exact first-seen。
6. **固定 wait policy**
   - 同一模型/target date 的首个 fee-adjusted edge `>=2%` 作为 trigger；
   - 机械等待下一个已连接的 JMA checkpoint；
   - 不看 label 决定是否等待；没有下一状态或 edge 消失时记为 no-trade。

## 数据与分母

| 层 | checkpoint | transition | state-entry | 日期 |
|---|---:|---:|---:|---:|
| train | 32,674 | 12,336 | 2,047 | 424 |
| validation | 14,259 | 5,165 | 847 | 184 |
| frozen weather 2026H1 | 14,111 | 5,081 | 875 | 181 |
| market development | 1,638 | 687 | 108 | 21 |
| market holdout | 702 | 289 | 59 | 9 |

总计 `63,384` checkpoint / `819` 日期。盘口证据为 `797` 个 joined states /
`13` 日期；其中 collector exact 为 `431` states / `8` 日期。其余盘口 clock
仍是 archive `observation + 15m` 重建，只用于 development，不叫 exact。

hard support floor 只来自**严格早于决策时刻的 RJTT METAR running max**。
JMA 温度、风、阵风、风向、降水、斜率、湿度/露点、云、pressure 等均为 soft
features，不会把 bracket 硬置零。

## Frozen probability 结果

数值均按 target date 等权；Brier、RPS、logloss 越低越好。

| split / grain | model | Brier | RPS | logloss | exact | within-one |
|---|---|---:|---:|---:|---:|---:|
| 2026H1 checkpoint | direct champion | **0.3603** | **0.0908** | **0.6855** | 73.34% | 90.14% |
| 2026H1 checkpoint | coherent multi-grain | 0.3667 | 0.0928 | 0.6966 | 72.57% | 90.12% |
| 2026H1 transition | direct champion | 0.4707 | 0.1251 | 0.8791 | 67.98% | 91.46% |
| 2026H1 transition | coherent multi-grain | **0.4628** | **0.1192** | **0.8557** | 66.25% | 91.36% |
| 2026H1 state-entry | direct champion | 0.5630 | 0.1611 | 1.0538 | 67.20% | 89.26% |
| 2026H1 state-entry | coherent multi-grain | **0.5340** | **0.1467** | **0.9879** | 65.26% | 88.69% |
| July holdout checkpoint | direct champion | **0.1943** | **0.0441** | 0.3758 | 86.18% | 98.15% |
| July holdout checkpoint | coherent multi-grain | 0.1981 | 0.0484 | **0.3745** | 86.18% | 97.44% |
| July holdout state-entry | direct champion | **0.3580** | **0.0815** | **0.6640** | 67.80% | 94.92% |
| July holdout state-entry | coherent multi-grain | 0.3922 | 0.0961 | 0.7140 | 66.10% | 91.53% |

2026H1 state-entry 的 coherent Brier 相对 direct 改善 `-0.0290`，
target-date bootstrap 95% CI `[-0.0561, -0.0035]`；但 July state-entry
退化 `+0.0342`，95% CI `[+0.0045, +0.0615]`。这不是可部署的稳定增益，
所以不做基于 grain 的硬路由。

全历史 refit 让 2026H1 checkpoint Brier 从 v1 的 `0.3697` 降到 `0.3603`；
但 July weather-only holdout 从 `0.1863` 升到 `0.1943`。这说明多用历史改善了
跨季平均，却没有解决 7 月极热日的 regime/forecast 信息缺口。

## 同分母 market baseline

collector-exact holdout 为 `431` states / `8` target dates：

| model | multiclass Brier | RPS | Brier delta vs market | 95% CI |
|---|---:|---:|---:|---:|
| conditional market | **0.1236** | **0.0266** | — | — |
| direct champion | 0.1402 | 0.0302 | +0.0166 | [-0.0311, +0.0601] |
| coherent checkpoint | 0.1513 | 0.0347 | +0.0277 | [-0.0179, +0.0739] |
| coherent multi-grain | 0.1434 | 0.0344 | +0.0197 | [-0.0344, +0.0769] |

没有一个模型的 CI upper bound 小于零，因此不能声称 probability alpha。
counterfactual trade 的少量正 ROI 不能覆盖这个失败：它只有 8 个 exact
target dates，且多数候选是 `0.1¢–4.5¢` 的早晨低价 YES，概率尾部误差被放大。

## 典型结构性错误

最重要的错误不是 hurdle 公式，而是**早晨缺少严格 PIT forecast ceiling /
peak clock**：

- `2026-07-25 05:50 JST`，official floor 仍为 `29`，direct 模型给
  `29 YES` 约 `18.2%`，盘口 ask `0.1%`，看起来有巨大 residual；
  最终 winner 为 `36+`，该笔当然输。
- `2026-07-26 12:10 JST`，JMA `33.2°C`、30m slope `+2.2°C/h`、
  path phase=`new_high`，模型仍把 `delta=0` 压到 `4.5%`，预测下一档；
  最终却停在当前 official bracket。说明 JMA 小数温度/新高是强特征，
  但不能替代 RJTT/WU settlement lattice 和 terminal ceiling。
- 正确样本主要集中在下午明显 pullback/fade：例如 `2026-07-25`
  official floor 已到 `36` 后，模型连续给 `P(stay)≈99.4%–99.5%`。
  这类“已经过峰”的 easy state 解释了整体高 accuracy，不能证明早盘交易
  概率足够准。

因此第一版策略可以使用本模型来做**每 10 分钟路径概率更新**，但不能把
`p_model - ask` 直接当下单信号。尤其不能因为模型给一个低档 5%–20% 就去买
市场 0.1% 的 YES；那通常是在交易模型缺失 forecast ceiling，而不是市场定价错。

## wait-one checkpoint

exact holdout 上：

- direct：now ROI `+2.54%`，wait ROI `-1.79%`；
- coherent checkpoint：now `+0.07%`，wait `+5.03%`；
- coherent multi-grain：now `-1.28%`，wait `-1.06%`。

三个模型方向不一致；development 上 coherent checkpoint 的 wait 还更差。
固定等待只保留为 telemetry 字段，不进入 eligibility。策略的正确比较应是
每个 checkpoint 都重新计算：

```text
P_model(outcome | current PIT state) - executable market probability - fee
```

只有 probability gate 先过，再比较“现在买”与“等下一次更新”的条件 EV。

## 当前可用方式

当前 research champion 是 `direct_checkpoint_hgb_v1`（名称保留用于与 v1
架构对照，但 artifact 已用截至 2025-12-31 的全历史 refit）。每次 JMA 更新
读取同一 `distribution_id` 的四行：

```text
delta_0      = 当前 official bracket 最终胜出
delta_1      = 下一档最终胜出
delta_2      = 再下一档最终胜出
delta_3plus  = 至少再升三档
```

若第一版只交易当前档/下一档，可以直接取 `delta_0` 与 `delta_1`，但其它质量
不能丢弃：它们解释“当前档已过后概率去哪了”，也保证 full ladder 总和恒等于
`1`。实测 `49,353` 个 state/model distribution 的最大归一化误差为
`4.44e-16`。

下一轮真正可能改善 early-day residual 的输入是 conservative single-run
GFS/JMA forecast ceiling、forecast peak clock、相对 ceiling margin。现有历史
JMA/METAR observation clock 可以训练 path model，但不能把晚到 forecast
archive 冒充 first-seen；在这条 PIT forecast lineage 补齐前，v2 保持
weather-only research/shadow，不接 live。

## 产物与复现

代码：
`scripts/analysis/market_structure_edge/research_tokyo_continuous_ladder_probability_v2.py`

测试：
`tests/research_tests/test_tokyo_continuous_ladder_probability_v2.py`

生成目录：
`docs/analysis/2026-07/generated/tokyo_continuous_ladder_probability_v2/`

关键文件：

- `prediction_long.csv.gz`
- `model_scores_by_grain.csv`
- `stay_calibration_summary.csv`
- `market_scores.csv`
- `wait_policy_summary.csv`
- `wait_policy_paired_delta.csv`
- `typical_cases.csv`
- `model_decisions.csv`
- `models/*.spec.json` / `models/*.joblib`
- `summary.json`

复现命令：

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_continuous_ladder_probability_v2.py
```
