# Current-YES Core Carry residual taker A/B v1

## 数据快照

- 数据源：9 份 PIT feature-factory 历史分片，以及
  `current_yes_core_carry_no_obs_age_freeze_pre_live_v5/oof_states_five_share_cost.csv`
  的历史完整 ask ladder。
- 模型：与 production v3 相同的 expanding-OOF no-peak-clock logistic；
  `market_logit + local hour + dewpoint depression + wind`。
- 原始 settled universe `4,061` rows；expanding OOF `2,758` rows / `32`
  target dates / `895` city-days。
- 固定交易父分母：bounded current exact YES、market mid `0.80–0.9895`、
  full 5-share taker executable，共 `1,349` checkpoint rows。
- settlement coverage `100%`；unsettled `0`，missing bracket `0`。
- 费用：每一档历史 ask ladder principal 加官方 Weather fee；所有金额按每次
  `5 shares` 计算。
- 复跑：
  `.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_residual_taker_ab_v1.py`。

目标：固定 PIT rows、概率、label 和 taker cost，只比较是否要求
`p_model > full taker cost`，验证现行 gate 是否过滤过多。

## 结论

**不能把所有 `p_model > market mid` 状态直接改成 taker 买入。**

现行 first-positive-taker-EV 策略为 `104` 笔、正确率 `95.19%`、fee-adjusted
ROI `+4.38%`。若每个 `p_model > mid` checkpoint 都买，扩为 `482` 笔，
正确率降到 `93.15%`，成本 `$2,270.23`、PnL `-$25.23`、ROI `-1.11%`。

被现行 taker-cost gate 专门过滤的 `318` 个 checkpoint，正确率 `92.77%`，
平均有效成本 `0.9507`；盈亏平衡正确率需要约 `95.07%`。实际低约 `2.30pp`，
因此成本 `$1,511.59`、PnL `-$36.59`、ROI `-2.42%`。

## Overall A/B

| policy | entries | city-days | win rate | avg taker cost | 5-share cost | 5-share PnL | fee ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current: first `p>taker cost` | 104 | 104 | 95.19% | 0.9120 | $474.23 | +$20.77 | +4.38% | [+0.67%, +7.88%] |
| relaxed: first `p>mid` per city-day | 285 | 285 | 92.98% | 0.9271 | $1,321.15 | +$3.85 | +0.29% | [-2.80%, +3.28%] |
| relaxed: every `p>mid` checkpoint | 482 | 285 | 93.15% | 0.9420 | $2,270.23 | -$25.23 | -1.11% | [-4.18%, +1.89%] |
| filtered-only: every `mid<p<=taker cost` | 318 | 236 | 92.77% | 0.9507 | $1,511.59 | -$36.59 | -2.42% | [-6.02%, +0.93%] |
| filtered-only: first per city-day | 236 | 236 | 92.80% | 0.9430 | $1,112.72 | -$17.72 | -1.59% | [-5.18%, +1.75%] |

`first p>mid` 点估略正，但区间跨 0；它增加约 2.7 倍 city-day entries，却几乎
耗尽当前策略的收益率。所有 checkpoint 都买还会在同一 city-day 重复叠仓，结果转负。

## Frozen last-8-date check

| policy | entries | win rate | 5-share PnL | fee ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|
| current: first `p>taker cost` | 11 | 100.00% | +$4.68 | +9.29% | [+6.50%, +13.08%] |
| relaxed: first `p>mid` | 45 | 93.33% | -$1.92 | -0.91% | [-7.06%, +6.89%] |
| relaxed: every `p>mid` | 75 | 94.67% | -$1.66 | -0.46% | [-5.62%, +5.13%] |
| filtered-only: every checkpoint | 55 | 92.73% | -$9.27 | -3.51% | [-11.05%, +4.20%] |

relaxed 表达在 frozen last-8 dates 没有保持正号。

## 双漏斗

Signal funnel：

```text
4,061 settled raw states
→ 2,758 expanding-OOF states
→ 1,349 bounded/executable/mid-domain checkpoints
→ 482 p_model > market mid
→ 164 p_model > full taker cost checkpoints
→ 104 first-positive-taker-EV city-days
```

Evidence funnel：

```text
1,349 PIT rows
→ 1,349 archived full-ladder/executable rows
→ 1,349 settled exact labels
→ research replay only; not actual fills
```

## 判断与动作

- taker-cost gate 不是无效过滤；它正在剔除一组整体 fee-adjusted 负收益的表达。
- `p_model > mid` 仍可作为 maker-only 候选，但不能用本 taker 回测证明 maker
  盈利；maker 必须另建 queue、fill probability 和 adverse-selection 分母。
- 不修改 Core Carry live taker gate，不扩大 taker entries。

三门：

- significance：relaxed first/all 均 `FAIL`，95% CI 跨 0；
- baseline：filtered-only taker point estimate 为负，`FAIL`；
- forward：relaxed 与 filtered-only frozen point estimate均为负，`FAIL`；
- conclusion：`rejected_for_taker_expression`；maker-only 方向仍是独立
  `inconclusive shadow hypothesis`。
