# Current-YES 气象语义 residual case library v1

## 数据快照

- 数据源：`docs/analysis/2026-07/generated/current_yes_core_carry_physical_semantic_audit_v2/semantic_augmented_feature_ledger.csv`；PIT report-time proxy + 同时点盘口 + 最终结算。
- 生成时间：2026-07-29T11:25:27.818908+00:00。
- 原始 checkpoint：1349；机制首次出现 city-day：702；独立 target dates：31。
- unsettled=0；missing_bracket=0；本库只收录已有最终 exact-bracket label 的研究 rows。
- production manifest 的 canonical DB route healthy；整体仍有临时 checkout/进程漂移 warning。本库只读冻结研究 ledger，不发布 live PnL。

## 归因规则

- 统计分母使用每个 `(mechanism, city, target_date)` **首次出现**的 checkpoint，避免事后挑当天最错的一刻。
- `core_more_overconfident_tail_loss`：最终越档，core 与市场均偏向 hold，且 core 比市场至少高 3pp。
- `shared_high_confidence_tail_loss`：最终越档，双方概率相差不足 3pp；优先解释为共同尾部或共同信息盲区。
- `market_more_overconfident_tail_loss`：市场比 core 至少高 3pp；不能算 core 独有的语义错误。
- 系统性判断只看 target-date block bootstrap 的 core-minus-market proper-score；单个故事不晋升为模型特征或 hard gate。

## 先给结论

- 共找到 59 个高置信 tail-loss 机制 membership，来自 43 个独立 city-day；同一事件可同时属于两个物理机制。
- core 独有的明显过度自信只有 1 个；双方共同高置信错为 31 个；市场比 core 更过度自信或只有市场达到高置信为 27 个。
- 因此原先列出的多数“常识偏差”不是 core 单独漏识别，而是市场与 core 共同面对的真实尾部；唯一清晰的 core-specific 样本是 Istanbul 2026-06-16 的强风机械混合平台。
- 机制层没有任何一类显示 core 的日期 bootstrap Brier 显著差于市场；暖湿平流和下坡/焚风类反而显著优于市场，锋面与太阳再加热为 inconclusive。

## 机制 scorecard

| mechanism | city-days/dates | tail losses | actual/core/market hold | ΔBrier core-market [95% CI] | 归因 |
|---|---:|---:|---:|---:|---|
| 暖湿平流 proxy | 34/21 | 3 | 91.2%/94.2%/90.9% | -0.0081 [-0.0153, -0.0014] | core_systematically_better_than_market |
| 强风机械混合平台 | 32/21 | 1 | 96.9%/95.9%/92.3% | -0.0001 [-0.0104, +0.0163] | low_sample_inconclusive |
| 冷平流 proxy | 28/19 | 1 | 96.4%/97.1%/95.3% | -0.0042 [-0.0079, -0.0014] | low_sample_inconclusive |
| 夜间辐射降温 | 1/1 | 1 | 0.0%/58.6%/80.0% | -0.2960 NA | low_sample_inconclusive |
| 夜间云层保温/增湿 | 1/1 | 0 | 100.0%/98.5%/98.7% | +0.0000 NA | low_sample_inconclusive |
| 下坡/焚风干暖增温 | 70/26 | 6 | 91.4%/92.8%/91.6% | -0.0024 [-0.0039, -0.0010] | core_systematically_better_than_market |
| 锋面/气团转换 | 128/29 | 11 | 91.4%/90.8%/92.6% | -0.0000 [-0.0077, +0.0072] | inconclusive_vs_market |
| 太阳再加热 | 347/31 | 34 | 90.2%/87.7%/90.2% | +0.0012 [-0.0037, +0.0059] | inconclusive_vs_market |
| 降雨/蒸发冷却 | 55/23 | 2 | 96.4%/95.2%/95.5% | -0.0004 [-0.0036, +0.0017] | low_sample_inconclusive |
| 阵风混合平台 | 6/6 | 0 | 100.0%/95.9%/90.7% | -0.0101 [-0.0200, -0.0020] | low_sample_inconclusive |

## 同型高置信 tail-loss 案例

下面只展示机制首次出现时已经高置信、后来仍越档的案例。`market` 为当时 current-YES midpoint；bid/ask 保存在 CSV。

| mechanism | city/date/time UTC | bracket→final | core | market | YES bid/ask | tail odds(mid) | attribution |
|---|---|---|---:|---:|---:|---:|---|
| 冷平流 proxy | Wellington 2026-06-13 2026-06-13T02:30:53+00:00 | 14→15 | 95.3% | 96.9% | 0.961/0.976 | 31.7x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Jeddah 2026-06-07 2026-06-07T11:30:53+00:00 | 37→38 | 94.8% | 95.5% | 0.940/0.970 | 22.2x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Jeddah 2026-06-15 2026-06-15T12:30:10+00:00 | 37→38+ | 93.5% | 94.0% | 0.920/0.960 | 16.7x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Denver 2026-06-09 2026-06-09T21:30:53+00:00 | 90-91→92-93 | 88.3% | 87.0% | 0.820/0.920 | 7.7x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Jeddah 2026-06-14 2026-06-14T12:30:31+00:00 | 36→37 | 89.2% | 88.7% | 0.886/0.887 | 8.8x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Denver 2026-06-27 2026-06-27T20:30:53+00:00 | 94-95→96-97 | 86.7% | 87.5% | 0.860/0.890 | 8.0x | shared_high_confidence_tail_loss |
| 下坡/焚风干暖增温 | Dallas 2026-06-09 2026-06-09T21:30:53+00:00 | 90-91→92-93 | 84.8% | 83.5% | 0.810/0.860 | 6.1x | shared_high_confidence_tail_loss |
| 锋面/气团转换 | Wellington 2026-06-16 2026-06-16T05:30:56+00:00 | 11→12 | 58.6% | 80.0% | 0.710/0.890 | 5.0x | market_only_high_confidence_tail_loss |
| 锋面/气团转换 | Lucknow 2026-06-27 2026-06-27T09:00:53+00:00 | 40→41 | 77.9% | 87.5% | 0.860/0.890 | 8.0x | market_only_high_confidence_tail_loss |
| 锋面/气团转换 | BuenosAires 2026-06-27 2026-06-27T17:30:43+00:00 | 18→19 | 88.9% | 94.0% | 0.930/0.950 | 16.7x | market_more_overconfident_tail_loss |
| 锋面/气团转换 | SaoPaulo 2026-06-29 2026-06-29T17:23:37+00:00 | 27→28 | 94.3% | 97.0% | 0.961/0.980 | 33.9x | shared_high_confidence_tail_loss |
| 锋面/气团转换 | Munich 2026-06-06 2026-06-06T14:30:53+00:00 | 23→24+ | 97.3% | 98.0% | 0.970/0.990 | 50.0x | shared_high_confidence_tail_loss |
| 锋面/气团转换 | Miami 2026-06-15 2026-06-15T17:30:53+00:00 | 92-93→94-95 | 81.7% | 88.0% | 0.860/0.900 | 8.3x | market_more_overconfident_tail_loss |
| 锋面/气团转换 | Shanghai 2026-06-30 2026-06-30T07:26:03+00:00 | 27→28 | 96.8% | 97.2% | 0.960/0.985 | 36.4x | shared_high_confidence_tail_loss |
| 锋面/气团转换 | Atlanta 2026-06-11 2026-06-11T20:30:53+00:00 | 90-91→92-93 | 80.7% | 86.0% | 0.840/0.880 | 7.1x | market_more_overconfident_tail_loss |
| 强风机械混合平台 | Istanbul 2026-06-16 2026-06-16T11:30:53+00:00 | 23→24 | 90.6% | 82.0% | 0.808/0.833 | 5.6x | core_more_overconfident_tail_loss |
| 夜间辐射降温 | Wellington 2026-06-16 2026-06-16T05:30:56+00:00 | 11→12 | 58.6% | 80.0% | 0.710/0.890 | 5.0x | market_only_high_confidence_tail_loss |
| 降雨/蒸发冷却 | Munich 2026-06-11 2026-06-11T12:30:53+00:00 | 16→17 | 74.8% | 80.5% | 0.800/0.810 | 5.1x | market_only_high_confidence_tail_loss |
| 降雨/蒸发冷却 | Guangzhou 2026-07-01 2026-07-01T07:56:37+00:00 | 32→34 | 87.0% | 86.1% | 0.801/0.920 | 7.2x | shared_high_confidence_tail_loss |
| 太阳再加热 | Denver 2026-06-12 2026-06-12T22:30:53+00:00 | 90-91→92-93 | 79.6% | 94.5% | 0.940/0.950 | 18.2x | market_only_high_confidence_tail_loss |
| 太阳再加热 | Madrid 2026-06-23 2026-06-23T14:30:22+00:00 | 40→41 | 77.1% | 91.5% | 0.910/0.920 | 11.8x | market_only_high_confidence_tail_loss |
| 太阳再加热 | Chengdu 2026-06-14 2026-06-14T07:30:47+00:00 | 32→33 | 73.6% | 86.0% | 0.850/0.870 | 7.1x | market_only_high_confidence_tail_loss |
| 太阳再加热 | Beijing 2026-06-11 2026-06-11T07:00:53+00:00 | 31→32 | 75.5% | 86.5% | 0.860/0.870 | 7.4x | market_only_high_confidence_tail_loss |
| 太阳再加热 | Austin 2026-06-12 2026-06-12T19:30:53+00:00 | 92-93→94-95 | 74.1% | 85.5% | 0.810/0.900 | 6.9x | market_only_high_confidence_tail_loss |
| 太阳再加热 | Lucknow 2026-06-27 2026-06-27T09:00:53+00:00 | 40→41 | 77.9% | 87.5% | 0.860/0.890 | 8.0x | market_only_high_confidence_tail_loss |
| 太阳再加热 | BuenosAires 2026-06-27 2026-06-27T17:30:43+00:00 | 18→19 | 88.9% | 94.0% | 0.930/0.950 | 16.7x | market_more_overconfident_tail_loss |
| 太阳再加热 | Wuhan 2026-06-09 2026-06-09T07:30:53+00:00 | 29→30 | 78.5% | 87.5% | 0.870/0.880 | 8.0x | market_only_high_confidence_tail_loss |
| 暖湿平流 proxy | Wellington 2026-06-04 2026-06-04T02:30:53+00:00 | 16→18 | 74.8% | 85.5% | 0.850/0.860 | 6.9x | market_only_high_confidence_tail_loss |
| 暖湿平流 proxy | Istanbul 2026-06-16 2026-06-16T12:30:53+00:00 | 23→24 | 97.9% | 96.0% | 0.951/0.970 | 25.3x | shared_high_confidence_tail_loss |
| 暖湿平流 proxy | Jeddah 2026-06-26 2026-06-26T10:30:38+00:00 | 37→38 | 86.7% | 86.0% | 0.840/0.880 | 7.1x | shared_high_confidence_tail_loss |

## 维护方式

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_physical_semantic_audit_v2.py
.venv/bin/python scripts/analysis/reheat_risk/build_current_yes_semantic_residual_case_library_v1.py
```

生成物：

- `docs/analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/checkpoint_case_library.csv`：所有机制 checkpoint，允许一行属于多个机制。
- `docs/analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/first_mechanism_city_day_cases.csv`：统计授权分母。
- `docs/analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/mechanism_scorecard.csv`：同分母 proper-score 与日期 bootstrap。
- `docs/analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/representative_tail_loss_cases.csv`：人工复盘展示集，不作统计分母。

状态：`research / inconclusive`。该库维护模型诊断，不授权 live 变更。
