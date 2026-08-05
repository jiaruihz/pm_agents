# Tmax City x Source Structure Mining v1

Generated: 2026-07-10
Parent: [2026-07-10-tmax-multisource-forecast-swap-v1.md](2026-07-10-tmax-multisource-forecast-swap-v1.md)
Script: `scripts/analysis/reheat_risk/research_tmax_city_source_structure_mining_v1.py`

## Question

母报告只回答了"整体换源做 selector 行不行"（不行）。这里把同一份数据按 **city × source** 拆开：
换源实验的收益到底集中在哪些城市？assigned GFS/ECMWF 的误差是随机的还是结构性的？
不换源、只做每城 bias 校正能吃回多少？以及"源差"和"forward 交易赚不赚钱"到底什么关系？

## Bottom Line

1. **"整体换源无效"是个被平均稀释的伪命题**：36 城里约一半 assigned 已是（或几乎是）最优源（gap≈0），
   换源收益集中在 ~10 个城市（Jeddah/Munich/Ankara/Helsinki/SF/Karachi/Milan/Lucknow/Wellington/KL）。
2. **反直觉核心发现：assigned 源越差的城市，原 tmax model forward ROI 反而越高**
   （gap>0.6F 城市 roi 0.152 vs gap≈0 城市 0.039；city 级 corr(gap, roi)=+0.31, corr(|bias|, roi)=+0.33）。
   一致假说：市场也在用同样的坏公共预报定价，预报系统性坏的城市恰是错定价集中地——
   源质量差不是要修的 bug，而是 edge 所在地。**换源的正确用法是"更准的私有预报 vs 市场的坏预报"做定价对抗，
   而不是把 anchor 修准后跟市场趋同。**
3. **城市 bias 是持久、可学习的属性**：half1/half2 符号一致率 0.77、corr 0.64。
   12 城持久 |bias|>1.5°F（Jeddah -5.5 / Munich +3.0 / Ankara +3.2 / SF -2.8 / Karachi -2.3 / Lucknow +2.5 / Wellington +2.0 / Guangzhou +2.2 等）。
4. **不换源、只做 rolling-14d guarded bias 校正，就能吃掉大部分换源收益**（walk-forward，eval 6/21+）：
   assigned MAE 1.88 → 校正后 1.56，vs 全量 best-model swap 1.51。零新增数据依赖、一个特征搞定。
5. 区域模型（ICON-D2/AROME/ICON-EU）是**欧洲城市专属**的第二层收益；美国城 HRRR/NAM/NBM 增益≈0（GFS 本来就行）；
   21 个亚洲/南美/非洲城根本没有区域模型可换——对它们谈"换源"无意义，只有 bias 校正和 spread 特征可用。

## Data Snapshot

```json
{
  "inputs": {
    "enrichment": "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1",
    "swap_trades": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/selected_trades.csv"
  },
  "eval": {
    "walk_forward_window": "2026-06-21+",
    "trade_scope": "original_tmax_model verified_forward, 162 trades, 36 cities"
  },
  "generated": {
    "city_assigned_vs_best": "generated/tmax_city_source_structure_mining_v1/city_assigned_vs_best.csv",
    "spread": "generated/tmax_city_source_structure_mining_v1/city_day_model_spread.csv",
    "forward_roi_slices": "generated/tmax_city_source_structure_mining_v1/forward_roi_by_{gap_bucket,spread_bucket,bias_side}.csv",
    "city_roi_vs_diag": "generated/tmax_city_source_structure_mining_v1/city_forward_roi_vs_source_diag.csv",
    "bias_persistence": "generated/tmax_city_source_structure_mining_v1/city_bias_persistence.csv",
    "walk_forward_correction": "generated/tmax_city_source_structure_mining_v1/walk_forward_bias_correction.csv",
    "regional_coverage": "generated/tmax_city_source_structure_mining_v1/regional_model_coverage.csv",
    "metadata": "generated/tmax_city_source_structure_mining_v1/metadata.json"
  }
}
```

PIT 限制继承母报告：`historical_forecast_enrichment_bias_v1` 是 city-date 级 historical daily 回放，
不是决策时刻 PIT forecast version。所有结论是 **feature-candidate 证据，不是 live 许可**。

## A. Gap 集中在哪些城市（assigned vs best，全窗 MAE °F）

| city | assigned | assigned_mae | assigned_bias | best_model | best_mae | gap |
| --- | --- | --- | --- | --- | --- | --- |
| Jeddah | ecmwf | 5.55 | **-5.51**（98% overforecast） | ecmwf_aifs025 | 1.98 | 3.56 |
| Munich | ecmwf | 3.21 | **+3.02**（89% underforecast） | icon_d2 | 1.17 | 2.05 |
| Ankara | ecmwf | 3.40 | +3.21 | gem_global | 1.58 | 1.82 |
| Helsinki | ecmwf | 2.57 | +2.01 | gem_regional/icon | 1.24 | 1.32 |
| SanFrancisco | ecmwf | 3.15 | -2.82 | ecmwf_aifs025 | 2.05 | 1.11 |
| Karachi | ecmwf | 2.34 | -2.31（80% overforecast） | icon_seamless | 1.27 | 1.08 |
| Milan | ecmwf | 1.98 | +1.54 | icon_d2 | 0.93 | 1.05 |
| Lucknow | ecmwf | 2.67 | +2.51 | icon_seamless | 1.82 | 0.85 |
| Wellington | gfs | 2.02 | +1.99 | gem_global | 1.24 | 0.78 |
| KualaLumpur | ecmwf | 2.58 | +1.96 | icon_seamless | 1.81 | 0.77 |

另一半城市（LA/Atlanta/Seattle/Miami/Houston/Shanghai/Singapore/Taipei/SaoPaulo/BuenosAires…）gap=0，
assigned 已是全模型最优。**任何全局换源实验对这些城市只会引入噪声——这就是母报告整体 replay 变差的机制。**

注意 gap 大城市的误差几乎全是**单向 bias**（over/underforecast ≥1°F 占比 80-98%），不是方差大。
这直接指向 bias 校正而不是换模型。

## B. 模型间 spread 是有效 uncertainty 特征，但不是要避开的 filter

全球模型（≥5 个）forecast max 的 std 按分位分桶后，consensus 误差单调上升：
q1 1.86°F → q4 2.41°F（p90: 3.5 → 5.1）。spread 确实度量了"这天有多难报"。

但在 original tmax model 的 verified forward 交易上，高 spread 桶 ROI 反而不差
（q4 roi 0.165 vs q1 0.068，样本小仅方向参考）——**spread 应进 sizing/uncertainty，不应做 hard filter**。

## C. 源差 ↔ 交易结果：方向和直觉相反

verified forward（original model，162 笔）：

| 切片 | n | win_rate | roi |
| --- | --- | --- | --- |
| gap≤0.15F 城市 | 83 | 0.675 | 0.039 |
| gap 0.15-0.6F | 41 | 0.683 | 0.074 |
| **gap>0.6F 城市** | 38 | 0.737 | **0.152** |

city 级（n≥3，30 城）：corr(gap, roi)=+0.31，corr(|bias|, roi)=+0.33，corr(spread, roi)≈0。
forward 最赚的城市恰是 bias 最大的：Munich(+3.0F bias, roi 1.21)、Wellington(+2.0, 0.66)、
Guangzhou(+2.2, 0.71)、Jeddah(-5.5, 0.41)；亏钱城市（Tokyo/Miami/Busan/Dallas/Wuhan/TelAviv）大多 gap≈0。

bias 方向 × 表达方向（样本小，只作方向假说）：

| assigned bias | 表达 | n | roi |
| --- | --- | --- | --- |
| warm_bias（系统性报低，settle 常更高） | no | 48 | +0.121 |
| warm_bias | yes | 17 | +0.241 |
| neutral | no / yes | 59 / 12 | +0.047 / +0.297 |
| cool_bias（系统性报高） | no | 23 | -0.023 |
| cool_bias | yes | 3 | **-1.000（3/3 全输）** |

cool-bias 城市（Karachi/SF/Denver/NYC/Chicago）目前是 forward 的净拖累，且 yes 腿全军覆没——
`assigned_bias_direction` 本身就该是 expression-level 特征。

## D. 关键反事实：bias 校正 vs 换源（walk-forward，eval 2026-06-21+，43 城）

| 方案 | forward MAE °F | 新增数据依赖 |
| --- | --- | --- |
| assigned 原样 | 1.88 | — |
| expanding-mean 校正 | 1.70（Chicago/Denver/Austin 被 5 月坏窗反噬） | 无 |
| **rolling-14d median、\|bias\|>1F 才启用** | **1.56** | 无 |
| 全量 per-city best-model swap | 1.51 | 16 模型 PIT 采集 + 每城选型 |

单城收益（rolling 校正）：Jeddah +2.94、Munich +2.78、**Guangzhou +1.41（换源收益=0 的城市，校正照样赚）**、
KualaLumpur +1.23、Chengdu +1.22、Ankara +0.94。16 个 swap_gain>0.3F 的城市里 10 个可由校正吃到 ≥60%。

## E. 区域模型覆盖：换源只对欧洲是真命题

- 欧洲 8 城有 ICON-D2/ICON-EU/AROME：Munich/Milan/Ankara/Helsinki 增益 1.0-2.1°F，是校正之外的真增量。
- 美国 10 城有 HRRR/NAM/NBM/RDPS：除 SF 外增益≈0，GFS 本来就好。
- **21 城（几乎全部亚洲 + 南美非洲大洋洲）没有任何区域模型**：Beijing/Shanghai/Guangzhou/Wuhan/Chengdu/
  Chongqing/Tokyo/Busan/Taipei/Singapore/Manila/Jeddah/Karachi/Lucknow/KL/Jakarta/Wellington/CapeTown/
  SaoPaulo/BuenosAires/Lagos。对这些城市，"换源"上限就是换个全球模型，主要工具只有 bias 校正 + spread。

## F. 数据审计点（顺带发现）

1. **Jeddah assigned ECMWF 常年 -5.5°F**、98% overforecast ≥1F，AIFS 只 -1.0——量级像站点/网格错位
   （沿海站 vs 内陆格点），值得对 `station_coordinates.csv` 的 OEJN 坐标与 ECMWF 取点做一次核对。
2. **Chicago/Denver/Austin 2026-05 有 -33/-27/-22°F 级误差行**（`official_station_diff_confirmed`），
   6 月后恢复正常（Chicago 月 MAE 8.4 → 2.8 → 2.0）。5 月窗口不可用于训练任何 bias/源选择；
   凡用全窗 MAE 选"每城最优模型"的结论都被这段污染（本报告 walk-forward 部分已避开）。
3. Lagos 只有 4 天数据、Jakarta 8 天，city_best_model 里这两城的结论无效。

## Interpretation → 交易动作排序

1. **不换 live anchor source**（维持母报告结论，且本报告解释了为什么整体换源必然失败）。
2. **P0（零新增依赖）**：把 `assigned_bias_rolling14`（14d median、|bias|>1F guard、shift(1)）作为
   shadow 特征接进 tmax feature payload，跑现有模型 ablation。预期它吸收母报告里
   `best_model_asof / assigned_minus_best` 想表达的大部分信息。
3. **P1**：`model_spread` 进 sizing/uncertainty；`assigned_bias_direction` 进 expression 选择
   （cool-bias 城市压 yes 腿）。
4. **P2（唯一真需要新数据的）**：欧洲 8 城接 ICON-D2/AROME 的 PIT 采集，作为 city-scoped 第二 anchor 实验；
   亚洲城不做换源实验（没有可换的）。
5. 审计 Jeddah 站点取点与 5 月 Chicago/Denver 数据段。

## Verdict

```text
significance=diagnostic (PIT caveat inherited)
baseline=assigned CITY_MODEL (GFS/ECMWF)
finding=source gap is city-concentrated, single-direction bias, persistent (sign-agree 0.77);
        rolling bias correction recovers most of swap gain (1.88->1.56 vs swap 1.51) with zero new sources;
        worse-source cities show HIGHER forward roi (corr +0.31) -> edge lives where public forecast is bad
conclusion=bias_correction_feature_first; regional_model_swap_europe_only; no_global_source_swap
```
