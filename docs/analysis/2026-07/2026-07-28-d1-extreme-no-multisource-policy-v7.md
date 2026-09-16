# D-1 两端 NO：多预测源 city policy v7

## 结论

系统并非只有 ECMWF/GFS：采集器配置了 18 个模型、7 个 provider。但历史研究与严格 PIT 采集必须分开使用。

- 历史 multi-model replay 只用于在训练窗冻结城市/source policy；它不是 decision-time 版本，不能直接拿来生成交易概率。
- 当前 D-1 immutable denominator 中，严格 as-of multi-model 仅覆盖 23/4086 baskets、2 个 target dates（0.56%），不足以判断 alpha。
- 本地采集契约此前只落当前城市日的模型值；本次已改为额外保留请求返回的全部 forecast dates，后续 D-1 才能积累真正 PIT evidence。
- 关键反证：按训练期最佳多源 MAE 选城，并没有改善 holdout。因此不要把“预报最准城市”直接当交易 gate；多源更适合衡量 assigned forecast 与独立 consensus 的偏离。
- 更直接的 frozen 检验也失败：训练期替代模型相对 assigned source MAE 改善至少 0.5°F 的城市组，在 holdout 的机械两端 NO 仍为负。天气预报更准没有自动转化成交易 alpha。

## Frozen holdout：城市池是否改变

- Train cutoff：2026-06-16；holdout：2026-06-17..2026-07-23。交易价格、fee、结算与 v4/v6 完全同分母。
- `multisource_accurate_half` 按训练窗内每城最佳可用模型 MAE 排名前半；不是逐笔挑事后最优模型。

| policy | cohort | baskets / dates | ROI (95% CI) |
|---|---|---:|---:|
| D-1_12_18_first | all_holdout | 908 / 28 | -0.45% [-0.86%, -0.05%] |
| D-1_18_24_first | all_holdout | 866 / 27 | -0.58% [-1.02%, -0.15%] |
| D-1_12_18_first | assigned_accurate_half | 452 / 28 | +0.20% [-0.27%, +0.55%] |
| D-1_18_24_first | assigned_accurate_half | 428 / 27 | +0.22% [-0.33%, +0.64%] |
| D-1_12_18_first | multisource_accurate_half | 435 / 28 | -0.28% [-0.81%, +0.20%] |
| D-1_18_24_first | multisource_accurate_half | 388 / 27 | -0.69% [-1.36%, -0.06%] |
| D-1_12_18_first | multisource_accurate_quartile | 215 / 27 | -0.95% [-1.82%, -0.12%] |
| D-1_18_24_first | multisource_accurate_quartile | 187 / 25 | -1.84% [-3.09%, -0.71%] |
| D-1_12_18_first | alternative_gain_ge_0_5f | 314 / 27 | -0.64% [-1.43%, +0.10%] |
| D-1_18_24_first | alternative_gain_ge_0_5f | 304 / 27 | -0.85% [-1.65%, -0.04%] |
| D-1_12_18_first | alternative_gain_ge_1_0f | 156 / 27 | -0.89% [-2.14%, +0.32%] |
| D-1_18_24_first | alternative_gain_ge_1_0f | 149 / 26 | -0.87% [-2.23%, +0.37%] |

| policy | comparison | ROI delta (95% CI) |
|---|---|---:|
| D-1_12_18_first | multisource_half_vs_complement | +0.33% [-0.44%, +1.11%] |
| D-1_12_18_first | multisource_half_vs_assigned_half | -0.47% [-1.01%, +0.02%] |
| D-1_18_24_first | multisource_half_vs_complement | -0.20% [-1.10%, +0.64%] |
| D-1_18_24_first | multisource_half_vs_assigned_half | -0.91% [-1.63%, -0.19%] |

## Source policy

现阶段推荐把多源作为 universe/reliability 与 uncertainty feature：

1. 保留现有 city-assigned GFS/ECMWF 作为稳定基线，并做只用过去数据的 rolling bias correction。
2. 欧洲城市增加 ICON-D2 / ICON-EU / AROME；全球补充 ICON、GDPS、AIFS、AI-GFS/JMA，使用 ensemble median、spread 和 assigned-minus-consensus。
3. 在严格 PIT 覆盖形成 frozen forward 前，不把“某城历史最佳模型”硬编码成 live gate，也不据此扩大真实交易。

## 双漏斗

- signal funnel：raw executable 4,086 → holdout 1,774 → multisource half 823。
- evidence funnel：strict PIT as-of 23 baskets / 2 dates；缺失属于 collector coverage gap，不是策略筛除。

## 产物

- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/frozen_multisource_city_policy.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/trade_summary.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/paired_ab_summary.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/strict_pit_coverage_rows.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/source_inventory.csv`
