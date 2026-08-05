# Heat-Death Overshoot-Edge Strategy v2（重建在规范血缘上）

Status: `research / rejected_for_promotion`
Date: 2026-07-21
Family: `current_yes_heat_death_physical_v1` · Strategy id: `heat_death_overshoot_edge_v2`
Verdict: `edge_did_not_survive_out_of_period_rejected_for_promotion`

## 结论先行

**v1 的 edge 没有跨期存活。** 在规范血缘 + 全窗口（42 天）重跑后，操作点 edge>0.03 的整体 ROI 为 +0.3% CI[-6.3%, +6.3%]，而且模型 logloss (0.3532) **劣于**市场 (0.3486)。

分期看：前段（v1 窗口 ≤2026-06-17）224 单 ROI +3.0%、PnL $+4.78；**后段（>2026-06-17）167 单 ROI -4.0%、PnL $-4.03**——几乎把前段利润全部吐回。

**方法论教训：v1 通过的 leave-one-date-out / leave-one-city-out / 安慰剂检验，只能排除“单城或单日假象”，无法发现“整个训练窗口本身处在有利期”。只有跨期外推能发现这一点。**

## v2 相对 v1 改了什么

1. window extended to all factory shards (2026-05-19..07-08)
2. dropped 2026-07-02..07-05 (silent ECMWF->GFS fallback)
3. canonical per-city fixed-model forecast fields instead of gfs_/ecmwf_ dual columns
4. canonical weather_feature_layer.regimes labels instead of hand-rolled proxies

## 数据审计

- factory 分片 9 个；剔除已知静默 fallback 日期 2026-07-02, 2026-07-03, 2026-07-04, 2026-07-05（丢弃 386 行）。
- 最终分母：4076 行 / **42 天** / 36 城，2026-05-19..2026-07-07；base rate 0.6374。
- OOF：2913 行 / 30 天（前 12 天仅训练）。
- bid 覆盖率 0.980（决定 market_mid 能否用中价；缺 bid 时回退用 ask）。

### v2 regime 标签可用性（这是本轮发现的硬缺口）

```json
{
  "running_max_state_v2": {
    "strict_high_clock_unknown": 12271
  },
  "intraday_state_v2": {
    "pullback_uncertain": 4328,
    "active_warming": 4026,
    "slow_warming": 2160,
    "flat_or_cooling": 801,
    "false_fade_risk": 593,
    "reheating_after_dip": 353,
    "state_unknown": 10
  },
  "solar_phase_v2": {
    "solar_geometry_unknown": 12271
  }
}
```

**开放补数据项：**

- solar_phase_v2: needs solar_elevation_deg/_delta_2h_deg, which the factory does not emit. weather_data_feed has no canonical city->lat/lon table, but coordinates for all 36 backtest cities ARE recoverable from the Open-Meteo forecast cache echoes (mixed 0.25-deg grid points and station-precision; both fine for solar geometry). So this is computable now via weather_data_feed.physical_features.solar_geometry_features; the canonical fix is still to add a proper city->coordinate table sourced from the official settlement station.
- running_max_state_v2 / intraday_state_v2: need minutes_since_last_strict_new_high (strict highs only); factory only emits minutes_since_running_max which resets on equal highs

## 模型（未标准化原始系数）

| 特征 | raw_coef |
|---|---:|
| `_mkt_logit` | +1.0707 |
| `forecast_peak_delta_hours_local` | +0.0099 |
| `forecast_gap_to_running_native` | -0.0172 |
| `minutes_since_running_max` | -0.0000 |

## 同分母 proper score（OOF）

| 预测 | logloss | Brier | AUC |
|---|---:|---:|---:|
| market_mid_raw | 0.3486 | 0.1089 | 0.9145 |
| overshoot-edge v2 | 0.3532 | 0.1109 | 0.9116 |

## 连续 edge sweep

| selector | edge> | rows | dates | cities | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| buy_all | - | 2913 | 30 | 36 | +63.7% | 0.671 | -5.8% | [-8.7%, -2.9%] |
| overshoot_edge | 0.00 | 1050 | 30 | 36 | +76.2% | 0.760 | -0.5% | [-3.6%, +2.4%] |
| overshoot_edge | 0.01 | 669 | 30 | 36 | +71.3% | 0.713 | -0.9% | [-5.5%, +3.2%] |
| overshoot_edge | 0.02 | 503 | 30 | 36 | +68.0% | 0.681 | -1.2% | [-7.2%, +4.0%] |
| overshoot_edge ←操作点 | 0.03 | 391 | 29 | 36 | +66.8% | 0.657 | +0.3% | [-6.3%, +6.3%] |
| overshoot_edge | 0.05 | 238 | 29 | 36 | +61.8% | 0.623 | -2.3% | [-13.6%, +8.6%] |
| overshoot_edge | 0.07 | 152 | 28 | 34 | +57.2% | 0.609 | -7.5% | [-25.5%, +8.6%] |

## Ablation：规范 regime 层有没有加分

| spec | #feat | AUC | logloss | edge>0.03 rows | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| market_only | 1 | 0.9140 | 0.3487 | 0 | NA | [NA, NA] |
| market+physics | 4 | 0.9130 | 0.3511 | 89 | -5.1% | [-19.8%, +17.1%] |
| market+regime | 25 | 0.9127 | 0.3507 | 305 | -1.2% | [-10.2%, +6.9%] |
| market+physics+regime | 28 | 0.9116 | 0.3532 | 391 | +0.3% | [-6.3%, +6.3%] |

## 跨期检验（决定性）

| 期间 | 单数 | 天数 | 胜率 | 均价 | fee ROI | 95% CI | PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| 前段 ≤2026-06-17（v1 窗口） | 224 | 18 | +73.2% | 0.703 | +3.0% | [-4.9%, +10.0%] | $+4.78 |
| **后段 >2026-06-17（期外）** | 167 | 11 | +58.1% | 0.597 | **-4.0%** | [-13.6%, +6.0%] | **$-4.03** |

分母基础条件也确实变难（不是纯运气）：

- front：1981 行，base 守住率 65.0%，市场均中价 0.647
- back：932 行，base 守住率 61.2%，市场均中价 0.619

### 窗口归因（同特征、只换窗口）

| 窗口 | OOF 行 | OOF 天 | 触发 | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| `window_le_2026-06-17` | 1981 | 18 | 224 | +3.0% | [-4.9%, +10.0%] |
| `window_gt_2026-06-17` | - | - | - | not enough dates after warmup | - |
| `window_full` | 2913 | 30 | 391 | +0.3% | [-6.3%, +6.3%] |

## 鲁棒性（edge>0.03）

- 391 行 / 29 天 / 36 城；130 输家散在 27 天；单城最大 |PnL| 占比 14.1%。
- leave-one-date-out ROI [-0.6%, +2.1%]；leave-one-city-out [-0.7%, +2.8%]。
- 分区域：东亚 127 行 ROI +0.5%；其余 264 行 ROI +0.2%。
- ask 价带：`ask<0.60` 128行 -7.0%；`0.60-0.75` 67行 +2.0%；`0.75-0.85` 84行 -5.6%；`ask>=0.85` 112行 +6.7%

## 安慰剂负对照

训练用打乱 label、PnL 用真实 label，20 次：ROI 均值 **-24.6%**，[p05 -26.3%, p95 -22.8%]，真实为 **+0.3%**。

**通过：打乱后决定性塌负。**

## 敏感性与控制组

- C：0.3→-1.1%，1.0→+0.3%，3.0→+0.4%
- warmup：8天→-0.2%，12天→+0.3%，16天→-0.9%
- 纯市场锚 → NA（0 行）；纯物理 → -17.8%（1080 行）。

## 对现有二元门

| selector | rows | dates | win | avg_ask | fee ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| overshoot_edge>0.03 | 391 | 29 | +66.8% | 0.657 | +0.3% | [-6.3%, +6.3%] |
| incumbent base-gate | 343 | 29 | +92.7% | 0.926 | -0.2% | [-3.8%, +2.7%] |

## 产物

- Script: `scripts/analysis/reheat_risk/research_heat_death_overshoot_edge_strategy_v2.py`
- OOF: `docs/analysis/2026-07/generated/heat_death_overshoot_edge_strategy_v2/oof_predictions.csv`
- v1（窄窗口、手搓特征）: [strategy-v1](2026-07-21-heat-death-overshoot-edge-strategy-v1.md)
