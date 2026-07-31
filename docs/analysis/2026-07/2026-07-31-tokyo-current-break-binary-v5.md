# Tokyo current-bracket stay/break binary v5

> 2026-07-31 更新：v5 保留为 observation-only standalone baseline。它暴露的低价
> residual 系统性高估已由
> [Tokyo market-anchored current-break binary v6](2026-07-31-tokyo-market-anchor-binary-v6.md)
> 修复；v6 仍是 zero-notional research challenger，未通过全部 market/forward gate。

## 结论

Tokyo 第一版应只回答一个问题：

```text
在当前 10-minute checkpoint，最终最高档是否还会高于 current bracket？
```

模型输出 `P(break current)`；市场表达只允许 `current exact YES/NO`：

- `current YES = stay`；
- `current NO = break upward`；
- 不交易 `next exact`；
- 每个 `target_date × current bracket` 第一次 fee-adjusted edge >= 2% 时建仓；
- 同档不补仓。

这修复了 v4 把“还会升温”错误表达成 `next exact` 的结构性问题。相同的
`2026-07-16..30` 诊断窗口内，固定 champion 从 v4 的 `62` 笔、ROI `-25.13%`
变为 v5 的 `34` 笔、ROI `+16.67%`。但这不是新的 frozen-forward 证明：规则是在
看过该窗口后修正，ROI 95% CI 为 `[-18.31%, +93.77%]`，仍跨零。

当前状态：保留为 zero-notional research baseline，不接 live。

## 数据、时钟与防未来函数

| 项目 | 口径 |
|---|---|
| JMA 10-minute + RJTT METAR 历史 | `2024-04-30..2026-07-30`，819 个 feature dates |
| pretrain | 截止 `2025-06-30` |
| temperature selection | `2025-07-01..2025-12-31` |
| final refit | 截止 `2026-07-15`，62,214 rows / 804 dates |
| 诊断窗口 | `2026-07-16..2026-07-30`，1,170 checkpoints / 15 dates |
| 市场同刻覆盖 | 248 book-time states / 12 settled dates |
| collector exact 子集 | 137 states / 8 dates（`7/22..29`） |
| coverage gap | `7/17..18` 无 book；`7/30` 无 settlement |

特征只使用 checkpoint 当时或此前的 JMA 路径、此前 METAR、露点、湿度、风、
气压、云、降水、能见度和太阳位置。`final_metar_*`、最终档、remaining-rise label
均不进入 model feature list；7/16–30 labels 不参与 fit。

历史 JMA 行的时钟等级仍是
`historical_observation_clock_not_first_seen`，不能冒充 exact first-seen。只有当前
collector hash 可验证的 137 rows 标成 `collector_exact_hash_verified`。因此全量历史
模型是 observation-clock baseline；它不包含严格 PIT forecast peak/ceiling，不能据此
升 live。

盘口按 book snapshot timestamp 关联当时最新可见 weather state，最大 state age
30 分钟，不允许用 book 之后的天气状态。交易均为 `research_counterfactual`，actual
order/fill 为 `0/0`。

## 模型选择

比较两个固定候选：普通 date-equal checkpoint HGB 与 multigrain HGB。后者把
checkpoint、transition、state-entry 三种 grain 各占三分之一，避免大量容易的平稳
checkpoints 淹没真正发生换档的时刻。

2025H2 validation：

| grain | checkpoint Brier | multigrain Brier | checkpoint accuracy | multigrain accuracy |
|---|---:|---:|---:|---:|
| checkpoint | 0.07503 | **0.07444** | 89.65% | **89.71%** |
| transition | 0.12194 | **0.10806** | 83.32% | **84.77%** |
| state-entry | 0.13841 | **0.12207** | 80.59% | **82.56%** |

所以 champion 在看 2026-07 诊断结果前固定为
`binary_multigrain_hgb_v5`。

## 天气概率质量

Champion 在 7/16–30 diagnostic：

| grain | Brier | logloss | accuracy |
|---|---:|---:|---:|
| checkpoint | 0.03639 | 0.13862 | 95.90% |
| transition | 0.06163 | 0.21073 | 91.90% |
| state-entry | 0.08739 | 0.28764 | 86.20% |

`95.90%` 不是“策略有 95.90% 胜率”。它包含全天大量已经很容易判断的平稳状态；
交易只会挑模型与市场分歧最大的少数尾部，这部分更难，也有 selection/winner's
curse。策略质量必须看同一交易行上的 probability score、market baseline 和 ROI，
不能用 checkpoint accuracy 代替。

同分母 market baseline：

| slice | model Brier | market Brier | model-market delta | 95% CI |
|---|---:|---:|---:|---:|
| all market-available，248 rows / 12 dates | 0.02050 | **0.01928** | +0.00122 | [-0.01825,+0.01744] |
| collector exact，137 rows / 8 dates | **0.01982** | 0.03030 | -0.01048 | [-0.03656,+0.00860] |

Exact 子集点估优于 market，但只有 8 天且 CI 跨零；baseline gate 仍 FAIL。

## Zero-notional 策略诊断

Signal funnel：

```text
1,170 weather checkpoints / 15 dates
→ 724 current-contract YES/NO expressions / 12 dates
→ 34 champion first date×bracket positions / 12 dates
```

Evidence funnel：

```text
248 settled PIT book-time states / 12 dates
→ 137 collector-exact states / 8 dates
→ 0 actual fills
```

| slice | trades | wins | hit rate | cost | fee-adjusted PnL | ROI | ROI 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| champion all available | 34 | 7 | 20.59% | $29.9991 | +$5.0009 | +16.67% | [-18.31%,+93.77%] |
| collector exact only | 12 | 4 | 33.33% | $13.7794 | +$6.2207 | +45.14% | [-32.48%,+290.40%] |

34 笔就是 34 个唯一 `model × date × bracket` positions，没有 add-on。入场时间
约为 `05:00..16:10 JST`，平均约 `09:44 JST`，确实包含 10:00、10:10、10:20
这类连续决策，而不是每天只看清晨一笔。

事后 side slice 仅作诊断，不能转成 gate：

| side | trades | wins | fee-adjusted ROI |
|---|---:|---:|---:|
| current YES / stay | 28 | 5 | +41.83% |
| current NO / break | 6 | 2 | -19.18% |

选中交易的均值显示 residual tail 仍过度自信：actual win rate `20.59%`，mean model
`P(win)=30.44%`，mean market probability `16.64%`；selected-row Brier 为 model
`0.08804`、market `0.04996`。正 ROI 点估来自赔率 payout，不代表概率已经稳定打败
market。

## 典型正确与错误

正确例子：

- `7/19 current 30 NO`：model `P(win)=97.2%`、ask `92.4%`，最终 32；
- `7/22 current 34 YES`：model `60.6%`、ask `22.0%`，最终 34；
- `7/23 current 34 NO`：model `95.9%`、ask `90.0%`，最终 35。

结构性错误：

- 清晨低档 current YES 仍可能被极低 ask 触发，例如 `7/25 05:10 current 29
  YES`，最终 36+；
- `7/28 13:10 current 30 YES` 给到 `93.3%`，最终 31，说明 observation-only
  模型缺 forecast peak/ceiling 与 source-settlement basis；
- `7/23 12:50 current 35 NO` 给到 `83.3%`，最终仍 35；
- `7/24 16:10 current 34 NO` 给到 `2.8%` 仍因极低 ask 入选，最终仍 34。

这些错误先记录为模型缺失机制，不事后加清晨、价格、path-phase 等 hard filter。
下一 challenger 应加入严格 PIT forecast peak clock、remaining heating window、ceiling
margin 和 JMA→settlement basis，然后在相同 denominator 上比较 Brier/logloss 与
fee-adjusted residual。

## Gate 与下一步

```text
significance=FAIL
baseline=FAIL
frozen_forward=NA
conclusion=inconclusive
deployment=zero-notional_only
```

v5 的目标、champion 和 selector 从现在冻结。真正 forward 从未参与本次修正规则的
新 target dates 开始积累；所有 checkpoints、eligible candidates、first position 和
would-add telemetry 都保留，但 v1 不执行 add-on。不修改 plan/order/fill/exit、live
runner 或实盘参数。

复现：

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_current_break_binary_v5.py
```

核心产物位于
`docs/analysis/2026-07/generated/tokyo_current_break_binary_v5/`。
