# D1 Bounded-Reheat / Overshoot Hazard v2

> generated_at_utc: `2026-07-24T17:06:23.425568+00:00`
> Status: `research / zero-notional / no live change`

## 结论

- **保持 D1 dormant zero-notional research；不新增 filter、不做 D1 YES replay、更不恢复 live。**
- D1 YES 的 bounded-reheat physical residual 结论为 `rejected_for_current_expression`；这是对当前表达/特征族的停止条件，不删除三态概率资产，也不否定未来有独立 source 或 execution 证据的研究。
- Gate A（conditional overshoot head）=`FAIL`；Gate B（三态/exact D1）=`FAIL`。
- 本 run 先在 complete PIT ladder 的同分母上比较 market residual；没有把盘口/结算覆盖缺失解释成策略筛选，也没有使用 live rows 训练。

## 数据快照

- 数据源：固定 historical atlas + reheat feature factory；`runtime/weather.db` 只读审计。DB fact build=`2026-07-24T17:04:17.939139+00:00`，candidate build=`2026-07-24T07:13:04.450917+00:00`。
- 记录：atlas `14369` states；bounded label `13545`；physics eligible `11132`；complete-ladder `6333`；OOF `3938` states / `18` dates。
- DB unsettled=`32` / `4677`；missing_bracket=`32`。本研究不发布 fill PnL。
- 已知 2026-07-02..05 forecast fallback 污染窗口排除；Celsius d1/d2 boundary 继续使用 native half-up lattice。

## Frozen universe 与双漏斗

| funnel | stage | grain | rows | dates | note |
| --- | --- | --- | ---: | ---: | --- |
| signal | bounded PIT label | state | 13545 | 50 | current/d1/d2+ 三态 |
| signal | physics eligible | state | 11132 | 45 | 排除已知 forecast 污染 |
| signal | complete ladder | state | 6333 | 30 | 同时有 current/d1/all d2+ midpoint |
| signal | expanding OOF | state | 3938 | 18 | 12 target-date warm-up |
| evidence | PIT feature/source parity | state | 11132 | 45 | fixed CITY_MODEL lineage |
| evidence | fresh full ladder | state | 6333 | 30 | historical archive coverage gap outside this denominator |
| evidence | 5/10-share executable replay | expression | NA | NA | Gate B 未通过，按计划不运行 |
| evidence | shadow/order/fill | fill | NA | NA | zero-notional research，无新增订单 |

## Conditional overshoot head：paired proper score

负值表示 candidate 优于同一 market h_over；CI 为 target-date block bootstrap。
| model | rows | dates | over_logloss_delta | over_logloss_delta_ci_low | over_logloss_delta_ci_high | over_brier_delta | over_brier_delta_ci_low | over_brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_raw | 3938 | 18 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| market_calibrated | 3938 | 18 | 0.0231 | 0.0115 | 0.0350 | 0.0059 | 0.0020 | 0.0101 |
| l2_plus_geometry | 3938 | 18 | 0.0246 | 0.0131 | 0.0366 | 0.0070 | 0.0025 | 0.0112 |
| l2_plus_remaining_heat | 3938 | 18 | 0.0244 | 0.0091 | 0.0386 | 0.0076 | 0.0020 | 0.0130 |
| l2_plus_path | 3938 | 18 | 0.0222 | 0.0063 | 0.0372 | 0.0069 | 0.0007 | 0.0130 |
| l2_plus_atmosphere | 3938 | 18 | 0.0204 | 0.0040 | 0.0367 | 0.0060 | -0.0007 | 0.0125 |
| l2_compact_residual | 3938 | 18 | 0.0235 | 0.0065 | 0.0393 | 0.0072 | 0.0007 | 0.0134 |
| elastic_net_compact_residual | 3938 | 18 | 0.0321 | 0.0145 | 0.0501 | 0.0092 | 0.0023 | 0.0160 |
| spline_compact_residual | 3938 | 18 | 0.0346 | 0.0108 | 0.0613 | 0.0103 | 0.0018 | 0.0187 |
| hgb_compact_residual | 3938 | 18 | 0.0077 | -0.0008 | 0.0162 | 0.0013 | -0.0021 | 0.0046 |

## 三态与 exact D1 一致性

| model | tri_logloss_delta | tri_logloss_delta_ci_low | tri_logloss_delta_ci_high | tri_brier_delta | tri_brier_delta_ci_low | tri_brier_delta_ci_high | exact_logloss_delta | exact_brier_delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_raw | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| market_calibrated | 0.0597 | 0.0461 | 0.0727 | 0.0233 | 0.0171 | 0.0293 | 0.0343 | 0.0083 |
| l2_plus_geometry | 0.0605 | 0.0468 | 0.0748 | 0.0250 | 0.0181 | 0.0321 | 0.0334 | 0.0084 |
| l2_plus_remaining_heat | 0.0591 | 0.0468 | 0.0731 | 0.0286 | 0.0211 | 0.0368 | 0.0336 | 0.0097 |
| l2_plus_path | 0.0534 | 0.0406 | 0.0668 | 0.0261 | 0.0182 | 0.0339 | 0.0307 | 0.0093 |
| l2_plus_atmosphere | 0.0488 | 0.0357 | 0.0620 | 0.0227 | 0.0147 | 0.0309 | 0.0271 | 0.0079 |
| l2_compact_residual | 0.0487 | 0.0357 | 0.0624 | 0.0225 | 0.0145 | 0.0304 | 0.0272 | 0.0079 |
| elastic_net_compact_residual | 0.0693 | 0.0535 | 0.0862 | 0.0303 | 0.0213 | 0.0398 | 0.0384 | 0.0113 |
| spline_compact_residual | 0.0585 | 0.0419 | 0.0769 | 0.0272 | 0.0186 | 0.0359 | 0.0300 | 0.0090 |
| hgb_compact_residual | 0.0148 | 0.0091 | 0.0203 | 0.0042 | 0.0008 | 0.0075 | 0.0086 | 0.0019 |

## 连续 feature ablation

L2 行是固定 rows 的依次加组，而非事后 selector；elastic/spline/HGB 都只是 predefined challengers。HGB 的 depth/L2 在每个 outer test date 的可见训练日期内嵌套选择，不会读取该 test date。
| model | over_logloss_delta | over_brier_delta | tri_logloss_delta | exact_logloss_delta |
| --- | --- | --- | --- | --- |
| market_calibrated | 0.0231 | 0.0059 | 0.0597 | 0.0343 |
| l2_plus_geometry | 0.0246 | 0.0070 | 0.0605 | 0.0334 |
| l2_plus_remaining_heat | 0.0244 | 0.0076 | 0.0591 | 0.0336 |
| l2_plus_path | 0.0222 | 0.0069 | 0.0534 | 0.0307 |
| l2_plus_atmosphere | 0.0204 | 0.0060 | 0.0488 | 0.0271 |
| l2_compact_residual | 0.0235 | 0.0072 | 0.0487 | 0.0272 |

## 解释与边界

- `p_exact_d1 = h_reach * (1-h_over)`，三态始终和为 1；没有另训一个不一致的 D1 selector。
- 交易层未执行：计划要求 Gate A 与 Gate B 后才能使用 5/10-share ask ladder、Weather taker fee、first-positive-EV city-day 和 frozen replay。未过 gate 时产出 ROI 会违反预注册流程。
- Atlanta terminal false-cross 仍只可作为 source reliability feature/negative control，不是 settlement label；本 historical state asset 未把 fast-source cross 当 reach 或 overshoot label。
- significance=FAIL；baseline=PASS（same-row market）；forward=NA；策略 promotion conclusion=inconclusive，但本 physical residual expression=rejected_for_current_expression。

## 复现

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_d1_bounded_reheat_overshoot_v2.py
```

产物包括 `audit.json`、`oof_state_predictions.csv`、`paired_model_scores.csv`、`daily_paired_deltas.csv` 和 `feature_ablation.csv`。
