# Tokyo 10 分钟升温路径概率与 full-ladder 回放 v1

> 2026-07-31 修正：本文 market-specific score/trade 数字使用了
> weather-state → future-book join，可能让同一 book 匹配多个旧 weather
> states。相关数字已由 v3 的 book-time latest-as-of join 取代；weather-only
> 模型分数不受影响。

## 结论

用户提出的核心策略定义是对的：**每收到一条 Tokyo JMA 10-minute observation，就更新“最终温度落在哪一档”的概率，再与同一时点盘口比较**。但本轮结果表明，首版应保留两个 weather probability head，不应直接交易：

1. `P(final=current)` / `P(final>current)` 二分类 head，用于 current bracket YES/NO；
2. `P(remaining rise=0/1/2/3+)` 联合分布 head，用于保证 current/next/tail 概率一致。

不推荐当前的 absolute full-ladder head。它虽然实现了“已确认越过的档位精确归零、剩余档位重归一化”，但结构性概率质量明显差于相对 remaining-rise 模型和盘口。将 absolute weather distribution 以 development 选择出的 10% 权重混入盘口后，collector-exact holdout 也没有稳定打败 market。

因此本轮动作是：

- 保留 binary + relative ladder 作为 research probability telemetry；
- absolute ladder 与 market residual **不进入 signal candidate / plan / order / fill / exit**；
- 不修改任何 live runner 或实盘行为；
- 下一步先补严格 PIT forecast peak clock / ceiling，再进行新的 frozen forward，而不是继续追交易阈值。

## 目标、标签与“过档归零”

状态粒度固定为 Tokyo 当地 05:00–18:00 的每个 JMA 10-minute checkpoint。所有 feature 只使用该 checkpoint 之前可见的信息。

`current` 的硬下界只来自严格早于 checkpoint 的 RJTT METAR running maximum（含 routine 与 special reports）。JMA 更快，但有 source-basis / false-cross 风险，只作为概率特征，不能独自将 market bracket 置零。

三个标签语义必须区分：

- binary：`0 = final=current`，`1 = final>current`。它适合 current YES/NO，但不能给出 next exact YES，因为 `current+2` 以上也会被归到 1；
- relative full：`Δfinal={0,1,2,3+}`。`P(Δ=1)` 才是 next exact 的主体，`3+` 显式保留 overshoot tail；
- absolute full：对所有绝对温度档建模，然后把 `<current` 的概率 mask 为 0 并重归一化。

测试中发现并修复了一处与用户担忧完全一致的 bug：temperature scaling 的数值 clipping 会把已 mask 的零概率重新变成极小正数。当前实现会在 scaling 后再次施加 lower-bound mask，再归一化；测试断言已越过档位必须是**精确 0**。全历史及盘口窗口的 lower-bound violation 均为 0。

## 数据与时间切分

历史输入：

- JMA Haneda 10-minute：118,344 rows；
- RJTT METAR：40,347 rows；
- 原始 feature rows：116,153；
- 白天连续 checkpoint：63,384 states / 819 target dates。

历史 JMA/METAR 只有 observation clock，明确标记为 `historical_non_pit_observation_clock`，不冒充 exact first-seen。

固定切分：

- train：截至 2025-06-30；
- validation / calibration：2025-07-01..12-31；
- frozen weather：2026-01-01..06-30；
- market development：截至 2026-07-21；
- market holdout：2026-07-22 起。

盘口 evidence funnel：

- market-window signal states：2,340 / 30 dates；
- 有同 checkpoint book + settlement：797 states / 13 dates；
- hash-verified collector exact：431 states / 8 dates；
- 其余 366 states 是显式 `archive_reconstructed_plus_15m`，只作覆盖诊断。

## 三轮建模与修改

### Round 1：简单 binary 与线性 relative ladder

- `binary_v1_logit`：current stay vs leave；
- `full_v1_multinomial_logit`：`Δ={0,1,2,3+}`。

在 frozen 2026H1，binary Brier 为 `0.09166`；relative full Brier 为 `0.37791`。这建立了可解释 baseline，但无法充分学习非线性的 remaining heat / peak-clock 关系。

### Round 2：非线性 direct distribution 与 ordered hazard

- `binary_v2_hgb`；
- `full_v2_multinomial_hgb`；
- `full_v3_ordinal_hazard`，依次预测是否跨过 `current/current+1/current+2`。

frozen 2026H1：

| model | binary Brier | 4-class Brier | 4-class logloss |
|---|---:|---:|---:|
| binary v2 | 0.08817 | — | — |
| full v1 linear | 0.09119 | 0.37791 | 0.73134 |
| full v2 direct HGB | 0.08811 | **0.36974** | **0.69860** |
| full v3 ordinal | **0.08804** | 0.37479 | 0.70917 |

direct HGB 是 weather-only 的最佳联合分布点估；ordinal 与它差异很小，日期 block CI 均未证明稳定优于 linear baseline。因此推荐把 direct relative distribution 作为主 head，binary 作为最简单、最容易监控的 current bracket head。

### Round 3：absolute ladder 与 market anchor

`full_v4_absolute_ladder_hgb` 直接训练绝对最终档，并施加 lower-bound mask。它在 validation 选择的 temperature 已触及 grid 上界 `2.0`，说明原始概率明显过度自信；frozen 2026H1 4-class Brier `0.70316`，远差于 relative HGB 的 `0.36974`。

随后构建 `full_v5_market_anchored = w × weather + (1-w) × conditional market`。只在 development 选权重，得到 `w=0.10`。

collector-exact holdout（431 states / 8 dates）：

| probability | exact Brier | exact logloss | Brier delta vs market |
|---|---:|---:|---:|
| absolute weather v4 | 0.92846 | 2.13979 | +0.40141，95% CI `[+0.00072,+0.78902]` |
| 10% weather + 90% market v5 | 0.52354 | 1.11629 | -0.00350，95% CI `[-0.03247,+0.02180]` |
| conditional market | 0.52704 | **1.09846** | 0 |

v4 被明确否定。v5 的 Brier 点估略好、logloss 略差，且只有 8 个日期、CI 跨 0，不能说打败 market。

对 current-stay binary，collector-exact holdout 上 linear relative head Brier `0.02833`，market `0.02859`，delta `-0.00026`，CI `[-0.01640,+0.01462]`；仍是证据不足，不是 alpha confirmed。

## 盘口模拟

统一 counterfactual policy：

- 每个 checkpoint 对可表达的 exact bracket YES/NO 计算 `p_win - direct ask - official fee`；
- 每 state 只保留最大 edge；
- 每 model / city-day 只取首个 fee 后 edge ≥2c 的机会；
- 固定 5 shares，并回查 raw ask size ≥5；
- 全部是 research counterfactual，不是假设实际成交。

全 holdout 的 8 个 target dates 上：

| model | trades | wins | fee-adjusted ROI | 95% target-date CI |
|---|---:|---:|---:|---:|
| binary v2 | 8 | 0 | -100.0% | `[-100.0%,-100.0%]` |
| relative full v2 | 8 | 1 | -0.24% | `[-100.0%,+6.55%]` |
| ordinal v3 | 8 | 1 | +4.56% | `[-100.0%,+6.79%]` |
| absolute v4 | 8 | 4 | +42.52% | `[-39.01%,+133.59%]` |
| market anchor v5 | 8 | 3 | +17.98% | `[-70.01%,+63.31%]` |

这些正 ROI 不能覆盖 proper-score 失败：它们是 8 日、模型迭代后的 selected trades，CI 极宽。collector-exact 单独重选后，v4 ROI 为 `-4.84%`；v5 为 `+25.95%`，但 CI `[-48.36%,+64.49%]`，仍不可用。

binary 的 0/8 很有解释力：凌晨/清晨模型认为某个低 current bracket 留存概率约 2%–8%，而 market ask 只有约 0.1%–0.8%，表面上仍有正 residual；当天继续升温后全部归零。也就是说，**“模型概率比廉价盘口高”并不足以构成交易**，尤其在 current 很低、`3+` tail 很大的时段。

## 典型正确与错误

- 正确，2026-07-22：v5 买 `35 NO @0.59`，winner 34，5-share fee-adjusted `+$1.99`。模型对高尾作了小幅向下修正。
- 正确，2026-07-24/25：v5 的 `32 NO @0.60`、`33 NO @0.61` 均赢，合计约 `+$3.83`。
- 错误，2026-07-26：v5 买 `32 NO @0.66`，winner 正是 32，`-$3.36`。这是 exact-bracket 语义的关键反例：winner 与 expression 相同，NO 必输。
- 错误，2026-07-27..29：v5 连续选择廉价低档 YES，全部被后续升温穿过。它暴露了缺失 PIT forecast ceiling / peak clock 后，模型对绝对 seasonal level 的外推不可靠。
- 结构错误，absolute v4：collector-exact exact Brier `0.92846`，temperature 需要最大幅度软化；其少量正 ROI 主要来自高价 NO 的结果集中，不能抵消概率分布整体严重失准。

## 策略启示与下一版

第一版最合适的结构不是二选一，而是：

```text
JMA 10m + prior METAR state
    → binary head: P(final=current), P(final>current)
    → relative head: P(Δfinal=0/1/2/3+)
    → market residual telemetry（zero-notional）
```

binary head 负责最简单的 current YES/NO 解释；relative head负责 next exact 与 overshoot tail 的一致性检查。任何时候 METAR 确认 current 上移，旧档精确归零，整个 relative distribution 以新 current 重新计算，而不是把旧概率机械平移。

absolute full-ladder 要重启，必须先补：

1. 严格 PIT forecast peak time、forecast ceiling margin、forecast revision；
2. JMA→RJTT→最终 settlement source 的 basis head；
3. 当前时刻距 forecast peak 的 remaining heat 状态；
4. 至少 30 个 collector-exact + full-ladder + settlement target dates，预注册模型与 2c expression 后再做 untouched forward。

在此之前只输出 zero-notional probability / residual telemetry。现有 8 个 exact OOF 日期不足以授权交易，更不能用 366 条 reconstructed clock 行冒充 first-seen。

## 可复现产物

- 训练与回放：`scripts/analysis/market_structure_edge/research_tokyo_continuous_ladder_probability_v1.py`
- 源 feature 修复：`scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_path_v1.py`
- 生成目录：`docs/analysis/2026-07/generated/tokyo_continuous_ladder_probability_v1/`
- 核心表：`historical_scores.csv`、`collector_exact_ladder_scores.csv`、`collector_exact_trade_summary.csv`、`counterfactual_trades.csv`、`funnel.csv`
