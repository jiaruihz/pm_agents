# Current-YES core carry overshoot missing mechanisms v2

Status: `不采用 overlay`
Generated: `2026-07-27T10:10:25+00:00`

## 结论

**动作：`不采用 overlay`；不改 live。** 本轮真正补到的机制不是新的 case gate，而是把风险写在 settlement-native upward-exit lattice 上，并把 assigned 单模型扩成双模型 tail/curve 分布；同时用 frozen-core logit offset 避免再次重标定 baseline。

Primary `orthogonal_compact_v2` 相对 frozen core：Brier Δ `+0.001087` CI `[-0.000343,+0.002829]`；logloss Δ `+0.003154` CI `[-0.001925,+0.008588]`。负值才是改善。

**最接近“精髓”的表示法是 native lattice，不是大而全模型，但它尚未成为增量 alpha。** 最简 `offset_lattice` 的 target-date 等权 paired Brier/logloss Δ `-0.000120/-0.000537`，CI 跨 0；而全体 city-day 聚合分数为 `0.07468/0.26506`，反而略差于 core `0.07445/0.26433`，AUC 也未提高。继续加入双模型、curve、path、盘口后没有累积增益。lattice 只应作为后续 telemetry/state 候选保存，当前不能拿来降仓或过滤。

## 上一轮忽略的“精髓”

1. **离输掉 current exact 还差几个 settlement-native tick**，不是 forecast max − running max。C 档要映射到 half-up bracket boundary，再离散到整数 °F source lattice。
2. **预测分布是否跨过 exit boundary**，不是 assigned model 的单点最高温。GFS/ECMWF 的 max、分歧、cross fraction、hourly heat area 都应相对同一个 lattice 表达。
3. **路径速度要除以 boundary buffer**。同样 +1°F/h，对只剩 1 tick 和还差 4 ticks 的风险含义不同。
4. **市场盘口的置信结构**。静态 midpoint 已在 core prior 内；spread、depth、imbalance、PIT price momentum 才是可能正交的信息。
5. **旧 v1 的 forecast revision 是死特征**：同一天使用同一 Single Runs cache，max revision 非空值只有一个取值；不能把它当真正 run-to-run revision。

## 固定分母与数据

- Parent：1349 states / 772 city-days / 31 dates，2026-06-02..2026-07-08。
- Expanding OOF：909 states / 538 city-days / 23 dates。
- 历史 5-share frozen selector audit：136 city-days，6 upward-overshoot losses；model denominator 不限于这些 rows。
- Settlement direction audit：非 hold `95` rows，其中 upward `95`、downward `0`；本轮 target 没被 downward leave 污染。
- Prereg SHA256=`6a62a6c5e5d0d785f06d22ba7242db93eec89563e25bb433450a74938fd2d9f1`；parent SHA256=`d453fa4127459f15ad47656b799baa8826a50cdfaaeb3b5a9f81f8195253b570`。本次未 sync/rebuild、未触碰生产。

## 累积 feature-family OOF

| model | features | Brier | logloss | AUC | ΔBrier vs core | Δlogloss vs core |
|---|---:|---:|---:|---:|---:|---:|
| offset_lattice | 3 | 0.07468 | 0.26506 | 0.753 | -0.000120 | -0.000537 |
| offset_plus_dual_model | 10 | 0.07516 | 0.26633 | 0.752 | +0.000201 | +0.000031 |
| offset_plus_curve | 15 | 0.07553 | 0.26825 | 0.741 | +0.000567 | +0.001745 |
| offset_plus_path | 20 | 0.07576 | 0.26867 | 0.742 | +0.000188 | -0.000192 |
| orthogonal_compact_v2 | 27 | 0.07630 | 0.27148 | 0.728 | +0.001087 | +0.003154 |

Primary top-decile overshoot lift=`1.91x`，捕获 `19.15%` overshoot city-days；frozen core 本身为 `2.12x` / `21.28%`，因此 primary 排序也没有增量。Cumulative/leave-family-out 只作诊断，唯一 primary 已预注册，不从中重选模型。

Leave-one-family-out 诊断：

| omitted family | ΔBrier vs core | Δlogloss vs core | ΔBrier vs primary |
|---|---:|---:|---:|
| native_lattice | +0.000997 | +0.002781 | -0.000089 |
| dual_model_tail | +0.000987 | +0.003325 | -0.000100 |
| forecast_curve_heat_budget | +0.000702 | +0.001475 | -0.000385 |
| path_to_boundary | +0.001492 | +0.005031 | +0.000405 |
| market_microstructure | +0.000188 | -0.000192 | -0.000899 |

## 稳定性

- Front 15 dates：primary vs core Brier/logloss Δ `+0.002170/+0.006910`，CI `[+0.000587,+0.004283]` / `[+0.001089,+0.013634]`；前段恶化。
- Frozen-forward 8 dates：Brier/logloss Δ `-0.000945/-0.003890`，CI `[-0.003006,+0.001508]` / `[-0.010964,+0.003514]`；点估改善但两项 CI 都跨 0。
- Leave-date-out：Brier/logloss `0.07634/0.27190`；vs core target-date 等权 Δ `+0.000401/+0.001317`，仍无稳定增量。

## 6 个 loss 机制审计

| city/date | category | bracket | exit ticks | assigned margin | dual max margin | trend/tick | observed exit delay | audit risk |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Busan 2026-06-04 | assigned_forecast_cross | 24 | 2.0 | 2.30 | 2.30 | 0.00 | 89m | 0.182 |
| LA 2026-06-05 | assigned_forecast_cross | 68-69 | 1.0 | 2.10 | 12.60 | 1.00 | 22m | 0.124 |
| Seattle 2026-06-06 | fresh_path_continuation_only | 60-61 | 2.0 | -6.00 | -4.60 | 0.50 | 22m | 0.100 |
| Istanbul 2026-06-16 | assigned_forecast_cross | 23 | 2.0 | 0.50 | 3.60 | 0.00 | 109m | 0.091 |
| Karachi 2026-06-20 | assigned_forecast_cross | 34 | 2.0 | 2.00 | 2.00 | 0.00 | 28m | 0.093 |
| Istanbul 2026-06-26 | alternate_forecast_cross_only | 27 | 1.0 | -0.60 | 3.50 | 0.00 | 49m | 0.165 |

六个 loss 的事后机制可以完整描述：4 个 assigned forecast 已跨 exit boundary，1 个只有 alternate model 跨界，1 个（Seattle）两套 forecast 都 miss、但路径仍在升温。这只是解释力，不等于 eligibility。

把同样的自然机制分类放回完整 winner 分母：

| category | city-days | winners | losses | loss rate | lift |
|---|---:|---:|---:|---:|---:|
| alternate_forecast_cross_only | 27 | 26 | 1 | 3.70% | 0.84x |
| assigned_forecast_cross | 33 | 29 | 4 | 12.12% | 2.75x |
| fresh_path_continuation_only | 21 | 20 | 1 | 4.76% | 1.08x |
| no_mechanism_warning | 55 | 55 | 0 | 0.00% | 0.00x |

但这个 selected cohort 会制造错觉：`no_mechanism_warning` 在 55 个 selected winner 中看似零 loss。放回 expanding-OOF 的首个 city-day 分母后，分类结果是：

| category | OOF city-days | winners | losses | loss rate | lift |
|---|---:|---:|---:|---:|---:|
| alternate_forecast_cross_only | 102 | 96 | 6 | 5.88% | 0.67x |
| assigned_forecast_cross | 135 | 121 | 14 | 10.37% | 1.19x |
| fresh_path_continuation_only | 125 | 113 | 12 | 9.60% | 1.10x |
| no_mechanism_warning | 176 | 161 | 15 | 8.52% | 0.98x |

因此 assigned-forecast cross 只有约 1.19x lift，path continuation 约 1.10x；“无 warning 就安全”在完整 OOF 分母上只有 0.98x，不能转成 hard gate。

Future observed exit 只用于 loss audit/label，不进入特征。完整 130-row winner audit 与全部 1349-state feature ledger 已保存，避免再次围着 6 个坏例子补 AND gate。

## 10-share taker expression sanity

- Baseline：89 city-days，86W/3L，PnL `$+47.58`。
- Candidate：114 city-days，109W/5L，PnL `$+41.49`。
- Mean daily PnL Δ `$-0.265` CI `[$-2.329,$+1.688]`；baseline winner selection harm=`20.93%`。
- 这里只是同分母 historical 10-share taker diagnostic；没有 maker settled evidence，不称 realized PnL，也不导向 sizing/deploy。

## 双漏斗

| funnel | stage | grain | rows | dates | note |
|---|---|---|---:|---:|---|
| signal | frozen carry parent | state | 1349 | 31 | all checkpoints; not selected/loss-only |
| signal | expanding residual OOF | state | 909 | 23 | eight target-date warm-up |
| signal | historical frozen selector audit | city-day expression | 136 | 30 | descriptive only; six losses do not define model denominator |
| evidence | native lattice + dual forecast max | state | 1349 | 31 | fixed previous-run Single Runs |
| evidence | dual hourly forecast curves | state | 1349 | 31 | cache-path PIT coverage |
| evidence | historical source first-seen | state | 0 | 0 | coverage gap; not synthesized |
| execution | ten-share full ladder | state | 1349 | 31 | official per-level taker fee; diagnostic only |
| execution | actual settled maker fills for overlay | fill | 0 | 0 | not claimed; no maker replay used |

## 三门与边界

- significance=`FAIL`。
- baseline=`FAIL` （same-row frozen core + market）。
- forward=`PASS`。
- conclusion=`不采用 overlay`；deployment authorized=`False`。
- 历史 source first-seen 仍不可恢复；真正 forecast run-to-run revision 也没有多版本 PIT archive。这两项是 evidence gap，不是策略过滤。双模型 deterministic max 也不等于校准后的 forecast tail distribution；更细的 radiation/cloud transition 与 checkpoint 内 order-flow 同样缺 PIT archive。

## 8 环覆盖

- 描述性：PASS（全部 parent、loss/winner audit）。
- 推断：PASS（city-day 等权、target-date bootstrap）。
- 判别/概率：PASS（offset expanding OOF、same-row baselines）。
- 执行：PARTIAL（真实 10-share ladder replay；maker/fill 不在本题证据内）。
- 容量：PASS 到历史 10-share taker。
- 组合：PASS（target_date block）。
- 基准/反事实：PASS。
- Forward：按三门结果。

## 复现与产物

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_overshoot_missing_mechanisms_v2.py
```

- script: `scripts/analysis/reheat_risk/research_current_yes_core_carry_overshoot_missing_mechanisms_v2.py`
- preregistration: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/preregistration.json`
- feature_ledger: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/feature_ledger.csv`
- oof_predictions: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/oof_predictions.csv`
- model_scores: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/model_scores.csv`
- leave_family_out_scores: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/leave_family_out_scores.csv`
- front_forward: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/front_forward.csv`
- leave_date_out_predictions: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/leave_date_out_predictions.csv`
- case_path_audit: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/case_path_audit.csv`
- mechanism_category_summary: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/mechanism_category_summary.csv`
- loss_audit: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/loss_audit.csv`
- winner_audit: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/winner_audit.csv`
- trade_diagnostic: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/trade_diagnostic.csv`
- daily_trade: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/daily_trade_diagnostic.csv`
- funnel: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/funnel.csv`
- json: `docs/analysis/2026-07/2026-07-27-current-yes-core-carry-overshoot-missing-mechanisms-v2.json`
- report: `docs/analysis/2026-07/2026-07-27-current-yes-core-carry-overshoot-missing-mechanisms-v2.md`
