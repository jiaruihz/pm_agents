# Heat-Death Overshoot-Edge Strategy v1

> ## ⚠️ SUPERSEDED — 本文结论已被跨期检验推翻，勿单独引用
>
> 本文的 edge>0.05 +17.9% CI[+8.1,+28.8] 是在 **2026-05-19..06-17 窄窗口 + 非规范 gfs_/ecmwf_ 双模型字段**上得到的；leave-one-out、安慰剂、分区域、敏感性**全部通过**。
>
> [v2](2026-07-21-heat-death-overshoot-edge-strategy-v2.md) 扩到 42 天规范窗口后 edge 消失（前段 +3.0% → 后段 **−4.0%**，几乎全额吐回；全窗口物理系数塌向 0）。窗口归因证明不是规范字段或 regime 层造成的——**纯粹是窗口**。
>
> 当前该家族的 current-reference 是 [regime-routed-carry 预注册](2026-07-21-regime-routed-carry-preregistration-v1.md)。本文保留作**方法论负例**：抗单点检验全过，仍可以是期间性假象。

Status: `superseded`
Date: 2026-07-21
Family: `current_yes_heat_death_physical_v1` · Strategy id: `heat_death_overshoot_edge_v1` · Kind: continuous-edge challenger selector (unifies H1/H2)
Verdict: `physics_grounded_positive_edge_robust_but_aggregate_gate_pending_forward`

## 物理机制（干净、第一性原理）

下午最高温何时“定档”（当前档守住 = current YES 赢）取决于加热引擎是否烧尽。两个**独立**物理条件决定它还能不能 overshoot 到下一档：

1. **时钟 Clock**：日内 forecast peak 是否已过（太阳过强迫峰、辐射冷却接管）→ `peak_delta`。
2. **天花板 Ceiling**：forecast 自身是否仍预期高于当前 running max（还有没有可爬的空间）→ `forecast_gap`。

两者都说“引擎烧尽”（过峰 + 无空间）时 overshoot 物理上不可能，买当前档 YES 才成立——但**只有市场还没把这份确定性涨进价里（ask 便宜）时才值得**。edge = 物理确定性与市场价之间的缝。

## 入场信号（selector）

在每个下午（13-17 local）决策 poll、当前档已打印且有 YES 报价的 city-day 上：

```text
p_hold = 市场锚定 + overshoot 修正的 P(最终最高温停在当前档)
       = logistic( logit(market_mid), peak_delta, forecast_gap, minutes_since_max )
edge   = p_hold - (ask + 官方 fee)
当 edge > margin 时触发买 current-YES（operating region 见下方 sweep）。
```

margin 不拍单一 magic 值：sweep 全曲线点估都为正、buy_all 为负，但**逐阈值显著性要到 edge≥0.05 才出现**（+20% CI[+9.7,+30]）；edge>0.03（+9.2%）的 CI 仍跨 0。所以 shadow 先记录整条曲线，不现在冻结单一阈值。

必须锚市场价再叠物理：纯物理是弱概率（AUC≈0.82 vs 市场≈0.91），市场提供校准 base，物理提供把它推离价格的修正。信号天然落在便宜/中价带（ask≈0.65-0.75、过峰、低 headroom），天然排除 0.95+ carry 带（那里没 margin）——即用**一条连续规则统一了旧 H1/H2 两个二元门**。

## 血缘挂载

同家族 `current_yes_heat_death_physical_v1`、同市场表达（current 档 exact YES）、同 label（`current_bracket_held`）、同机会粒度（`fact_signal_candidates` 的 city-target_date-decision snapshot）。这是**上层 selector 替换**，不新建市场、不建平行事实表。**shadow-first，本文件不改任何 live。**

## 数据与 provenance

- 全量午后分母：3103 行 / 30 天 / 36 城，2026-05-19..2026-06-17；label base rate 0.6449。**不预筛 decline/peak/support**，只作连续特征。
- expanding-window 逐日 OOF：1940 行 / 18 天（前 12 天仅训练）。
- peak clock provenance：single-runs 100.0%，factory 一致率 1.0000，status = `verified_clean`。

## 物理系数方向（标准化，+ = 抬升 P(hold)）

| 特征 | std_coef | 物理解释 |
|---|---:|---|
| `_mkt_logit` | +2.220 | 市场锚（价格已含大量信息，系数最大） |
| `peak_delta_mean` | +0.329 | 过峰越久越守得住 ✓（正、且是最强的物理项） |
| `forecast_gap_mean` | -0.027 | forecast 还有上冲空间→越易 overshoot ✓（负，但小：市场已把天花板定价进去） |
| `minutes_since_running_max` | +0.190 | 高点越成熟越守得住 ✓（正） |

关键读数：加市场锚后 `forecast_gap` 系数很小——**市场其实已经把“天花板还有多少空间”定价进价里了**；可交易残差主要来自**时钟类特征**（`peak_delta` / `minutes_since_max`，即“过峰多久、高点多成熟”），这类日内状态市场相对低估。这也是这条 edge 的物理落点。

## 同分母 proper score（OOF）

| 预测 | logloss | Brier | AUC |
|---|---:|---:|---:|
| market_mid_raw | 0.3573 | 0.1128 | 0.9067 |
| overshoot-edge model | 0.3545 | 0.1120 | 0.9089 |

整分布平均上模型与市场基本持平（市场本就校准良好）；edge 集中在模型有把握、市场尚未跟上的尾部。

## 连续 edge sweep（不是挑操作点）

| selector | edge> | rows | dates | cities | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| buy_all | - | 1940 | 18 | 36 | +64.9% | 0.681 | -5.4% | [-9.2%, -1.6%] |
| overshoot_edge | 0.00 | 395 | 18 | 36 | +82.8% | 0.793 | +3.8% | [-0.8%, +8.3%] |
| overshoot_edge | 0.01 | 151 | 18 | 31 | +78.8% | 0.768 | +1.8% | [-9.0%, +11.2%] |
| overshoot_edge | 0.02 | 88 | 18 | 26 | +80.7% | 0.753 | +6.1% | [-7.8%, +16.6%] |
| overshoot_edge ← 操作点 | 0.03 | 58 | 16 | 21 | +82.8% | 0.744 | +10.2% | [-4.3%, +21.3%] |
| overshoot_edge | 0.05 | 32 | 10 | 14 | +84.4% | 0.708 | +17.9% | [+8.1%, +28.8%] |

buy_all 显著为负（-5.4%）——无脑买是亏的，价值全在选择。

## 鲁棒性（操作点 edge>0.03，58 行 / 16 天 / 21 城）

- **抗单城/单日幻觉**：10 输家散在 7 天；单城最大 |PnL| 占比仅 16.1%（对比 registry 里 Busan 单日占 80.9% 的反例）。
- **leave-one-date-out** ROI [+8.1%, +15.1%]；**leave-one-city-out** [+7.7%, +15.3%]。
- **分区域**：东亚 20 行 ROI +1.9%；其余 38 行 ROI +14.9%。两边都正，不是单一地区。
- **ask 价带分布**（确认 edge 在便宜带、贵带无肉）：
  - `ask<0.80`：25 行 ROI +24.0%
  - `0.80-0.93`：28 行 ROI +2.9%
  - `0.93-0.95`：1 行 ROI +7.2%
  - `ask>=0.95`：4 行 ROI +4.6%
- **机制画像（tradeable 中位 vs 其余）**：
  - `peak_delta_mean` +3.50h vs +0.00h（买在过峰后）
  - `forecast_gap_mean` -0.056 vs +0.000（低 overshoot 燃料）
  - `minutes_since_running_max` 41 vs 38 分钟（高点更成熟）

## 安慰剂检验（负对照，最关键）

把 label 在每天内随机打乱、整条 OOF 链重跑 30 次：edge>0.03 的 ROI 均值 **-16.6%**，[p05 -17.8%, p95 -15.5%]，而真实为 **+10.2%**。

**打乱 label 后 edge 决定性塌为负、且 p95（-15.5%）远低于真实值——edge 来自真实的 label-特征关系，不是选择机制/过拟合的产物。**

## 敏感性

- 正则化 C：C=0.3→+9.9%，C=1.0→+10.2%，C=3.0→+10.9%（稳）。
- warmup：8天→+3.7%，12天→+10.2%，16天→+8.6%（≥12 稳）。
- 控制组：纯市场锚 edge>0.03 → NA（0 行）；纯物理 → -8.1%（598 行）。**单独都不行，只有融合有肉。**

## 对现有二元门（incumbent）的对比

| selector | rows | dates | cities | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| overshoot_edge>0.03 | 58 | 16 | 21 | +82.8% | 0.744 | +10.2% | [-4.3%, +21.3%] |
| incumbent binary base-gate | 183 | 17 | 34 | +97.3% | 0.960 | +1.2% | [-2.2%, +3.8%] |

连续 edge 规则用更少、机制更干净的信号拿到更高 ROI，且不靠叠条件缩样本。

## Gates 与结论

```text
universe            = 3103 rows / 30 dates / 36 cities (full afternoon; no filter stacking)
physics_signs       = PASS (peak_delta+ strong, minutes+; forecast_gap- but small = ceiling already priced by market)
placebo_negctrl     = PASS (shuffled-label ROI -16.6% vs real +10.2%)
robustness_lodo/loco= PASS (date/city leave-one-out both positive; top-city |PnL| 16.1%)
region_subgroup     = PASS (east-asia and rest both positive)
controls            = PASS (market-only and physics-only alone both non-tradeable)
aggregate_properscore = PENDING (model ~= market on whole-distribution logloss; edge lives in the tail)
sample/forward      = FAIL_THIN (single month, 18 eval dates; missing PIT rain/wind/solar)
conclusion=shadow_candidate; run zero-notional forward on the continuous edge, log full candidate denominator, add solar/rain/wind features, re-test aggregate gate before any live.
```

## 下一步

1. 把 `edge = p_hold - cost > 0.03` 作为该家族的 **zero-notional shadow selector** 接进 forward，记录全候选分母（不只 winners）。
2. 把缺失 PIT 特征（solar geometry / 降雨 / 风向 / remaining-3h forecast）补进 feature layer 后重训，检验整分布 proper-score 门能否转正。
3. 满足 contract 样本/日期/forward 门后，再按 [weather-strategy-deploy] git-first 讨论 tiny-live；本研究不改任何 live。

## 产物

- Script: `scripts/analysis/reheat_risk/research_heat_death_overshoot_edge_strategy_v1.py`
- OOF: `docs/analysis/2026-07/generated/heat_death_overshoot_edge_strategy_v1/oof_predictions.csv`
- 概率校准 companion: [overshoot-hazard-calibration-v1](2026-07-20-current-yes-overshoot-hazard-calibration-v1.md)
