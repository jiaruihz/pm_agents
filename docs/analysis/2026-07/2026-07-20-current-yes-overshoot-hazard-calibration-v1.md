# Current-YES Overshoot-Hazard Calibration v1

> ## ⚠️ SUPERSEDED — 本文结论已被跨期检验推翻，勿单独引用
>
> 本文的正面结论（edge>0.05 → +12.4%、leave-one-out/安慰剂/分区域全部通过）是在**2026-05-19..06-17 窄窗口 + 非规范 gfs_/ecmwf_ 双模型字段**上得到的。
>
> [overshoot-edge v2](2026-07-21-heat-death-overshoot-edge-strategy-v2.md) 把窗口扩到 42 天规范血缘后，同一 edge **消失**（全窗口 +0.3% CI[-6.3,+6.3]，后段 −4.0%）。
>
> **方法论教训**：本文通过的 leave-one-date-out / leave-one-city-out / 安慰剂只能排除「单城或单日假象」，**无法发现「整个窗口本身处在有利期」**。只有跨期外推能发现。
>
> 当前该家族的 current-reference 是 [regime-routed-carry 预注册](2026-07-21-regime-routed-carry-preregistration-v1.md)。本文保留作方法论负例与概率层记录。

Status: `superseded`
Date: 2026-07-21
Verdict (已作废): `aggregate_gate_not_cleared_but_physics_enabled_positive_edge_selection_is_robust_shadow_candidate`

## 设计（不堆 filter、不筛 cohort）

- 分母：全量午后（13-17 local）分母 3103 行 / 30 天 / 36 城；**不预筛 decline / peak-passed / support**，这些只作连续特征。
- label：`current_bracket_held`（最终最高温停在当前档=current YES 赢）；base rate 0.6449。
- 市场 baseline：`mid=(bid+ask)/2` 作 proper-score 对比，`cost=ask+官方 fee` 作交易口径。
- 验证：expanding-window 逐日 OOF，前 12 天仅训练；OOF 1940 行 / 18 天。
- 三个嵌套模型：market_only（重标定价格）、physics_only（纯物理）、combined（价格+物理）。

## Peak clock provenance

- single-runs 占比 100.0%；factory 内嵌 GFS peak 与干净 backfill 一致率 1.0000（1040 city-dates）；status = `verified_clean`。

## Proper scores（同 rows OOF，越低越好；AUC 越高越好）

| 预测 | logloss | Brier | AUC |
|---|---:|---:|---:|
| market_mid_raw | 0.3573 | 0.1128 | 0.9067 |
| market_only | 0.3584 | 0.1132 | 0.9062 |
| physics_only | 0.4847 | 0.1588 | 0.8220 |
| combined | 0.3554 | 0.1127 | 0.9091 |

## 增量 alpha 检验（核心）

- combined − market_raw logloss = -0.00194，date-bootstrap 95% CI [-0.01003, 0.00702]（<0=物理有增量）。
- combined − market_only(重标定) logloss = -0.00307，95% CI [-0.01180, 0.00670]。

**聚合结论：CI 跨 0，物理特征在*整分布平均* proper score 上没有可确认地打赢市场价——大多数行市场本就对。这是保守口径的硬门，未过。**

## 交易 edge 的来源：物理 vs 纯重标定价格（决定性分解）

同一条连续 edge 规则（`p - cost > thr`）跑在每个嵌套模型的 OOF 概率上：

| prob_source | edge> | rows | dates | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| combined | 0.00 | 653 | 18 | +77.9% | 0.762 | +1.6% | [-1.4%, +4.2%] |
| combined | 0.05 | 147 | 18 | +73.5% | 0.645 | +12.4% | [+4.0%, +20.9%] |
| market_only | 0.00 | 262 | 18 | +89.7% | 0.895 | -0.3% | [-2.9%, +2.3%] |
| market_only | 0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| physics_only | 0.00 | 749 | 18 | +43.8% | 0.446 | -3.0% | [-9.5%, +3.1%] |
| physics_only | 0.05 | 541 | 18 | +34.2% | 0.347 | -3.4% | [-12.5%, +5.9%] |

- **纯重标定市场价（无物理）在 edge>0.05 只选出 0 行**：mid<ask+fee，市场对自己的 ask 永远没有正 edge，所以脱离物理，这个策略无可交易机会。
- **combined（物理+价格）的 edge>0.05 cohort 完全由物理带出**，且落在 mid 价带（avg_ask≈0.72），不是 H1 的 0.95+ carry 带。

### 该 cohort 的 robustness（对抗单城/单日幻觉）

- 147 行 / 18 天 / 28 城；39 个输家散在 15 天。
- 单城最大 |PnL| 占比仅 13.5%（对比 registry 里 Busan 单日占 80.9% 的反例）。
- leave-one-date-out ROI 区间 [+10.2%, +14.6%]；leave-one-city-out [+9.0%, +15.9%]。
- 机制画像（tradeable 中位 vs 其余中位）：
  - `gfs_peak_delta` +1.00h vs +0.00h（买在 forecast peak 确已过后）
  - `gfs_gap` +0.056 vs +0.167（forecast 剩余向上空间更小=低 overshoot 燃料）
  - `current_yes_ask` 0.720 vs 0.850（市场尚未把 heat-death 涨上去）

## 残差定位（模型 vs 市场分歧处谁对）

| bucket | n | mean_model_p | mean_market_mid | actual_held_rate |
|---|---:|---:|---:|---:|
| model<<mkt (<-5c) | 250 | 0.4397 | 0.5420 | 0.4960 |
| model<mkt (-5..-1c) | 278 | 0.5187 | 0.5439 | 0.5144 |
| agree (+-1c) | 570 | 0.6567 | 0.6556 | 0.6561 |
| model>mkt (1..5c) | 486 | 0.8077 | 0.7831 | 0.7860 |
| model>>mkt (>5c) | 356 | 0.7269 | 0.6220 | 0.6657 |

## 交易转换（连续 edge sweep，不是挑操作点）

| selector | edge> | rows | dates | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| buy_all | - | 1940 | 18 | +64.9% | 0.681 | -5.4% | [-9.1%, -1.6%] |
| model_combined | 0.00 | 653 | 18 | +77.9% | 0.762 | +1.6% | [-1.4%, +4.2%] |
| model_combined | 0.01 | 401 | 18 | +76.1% | 0.728 | +3.4% | [-1.2%, +7.3%] |
| model_combined | 0.02 | 299 | 18 | +73.9% | 0.703 | +3.9% | [-3.0%, +9.9%] |
| model_combined | 0.03 | 227 | 18 | +70.5% | 0.669 | +4.1% | [-4.3%, +11.0%] |
| model_combined | 0.05 | 147 | 18 | +73.5% | 0.645 | +12.4% | [+4.0%, +20.9%] |

## H1 live regime (ask>=0.95)：模型 hazard 能否 PIT 标出 overshoot 输家

- ask>=0.95 全体：580 行 / 18 天，loss_rate +3.3%，ROI -1.1%。
- 模型 hazard 最高 1/4：145 行，loss_rate +4.8%，ROI -1.5%。
- 其余 3/4：435 行，loss_rate +2.8%，ROI -0.9%。
- 输家总数 19，落在 hazard 最高 1/4 的有 7。

## Feature coverage boundary

可 PIT 重建：温度路径、minutes-since-max、云、湿度、风速、露点差、双模型 forecast gap/peak delta、bracket 几何。仍缺 PIT：降雨、风向、remaining-3h forecast weather、solar geometry。因此这是 historical proxy，不是完整 live 回测。

## 产物

- Script: `scripts/analysis/reheat_risk/research_current_yes_overshoot_hazard_calibration_v1.py`
- OOF predictions: `docs/analysis/2026-07/generated/current_yes_overshoot_hazard_calibration_v1/oof_predictions.csv`
- Residual / trading sweep CSVs 同目录。
