# Current-YES core carry overshoot risk sizing overlay v1

Status: `不采用`
Generated: `2026-07-27T09:10:30+00:00`

## 结论与动作

**动作：不采用。** 失败候选中期望 PnL 最高的是 `probability_only_fixed_size`，但它没有通过预注册晋升门。它只属于 carry 的概率/风险/sizing overlay，不是独立 overshoot 交易策略。

生产基线动态核验为 **10 taker + 5 maker**。当前 summary=`2026-07-27T09:10:06+00:00`，且运行进程参数与 summary 一致。raw order 历史 taker/maker sizes=`[5.0]/[5.0]`，仍是变更前 5+5 记录；它们不能反推当前 policy 仍为 5+5。

在固定 challenger OOF 分母上，compact 相对 frozen core 的 Brier Δ `+0.009071` CI `[+0.006025,+0.012540]`；logloss Δ `+0.043917` CI `[+0.030635,+0.058576]`。负值才是改善。

## 数据快照

- Parent ledger：1349 checkpoint states / 772 city-days / 31 target dates，2026-06-02..2026-07-08；overshoot states=95。
- 历史冻结 5-share selector：136 city-days，130 winners / 6 losses；6 个 loss 全为向上 overshoot。
- DB fact build=`2026-07-27T09:00:46.718792+00:00`；CLOB fill gate=`False`，失败原因 `clob_fills_have_unknown_fee_lineage`。因此不发布 current live PnL。
- Prereg SHA256=`874ee3c06a442b69d4a516af45544edc30f301cc7e2ec92a60972b2eb9137e9a`；execution addendum SHA256=`1f1d36115abf19631bd640198f5e19a597c8fce9b5f4aaeae97cf297a230abe0`；parent SHA256=`56891f793278e2fb059b65e827a8db83c8393aba34f10289a5389598f30975a3`。本次未同步、未重建、未改生产。

## 固定分母与双漏斗

| funnel | stage | grain | rows | dates | 说明 |
|---|---|---|---:|---:|---|
| signal | frozen core carry eligible checkpoint | state | 1349 | 31 | all bounded exact, market-mid>=0.80, five-share executable OOF checkpoints |
| signal | expanding residual OOF | state | 909 | 23 | 8 target-date warm-up; warm-up rows retained with NA challenger |
| signal | frozen baseline selected | city-day expression | 136 | 30 | first frozen-core positive EV; not the model denominator |
| evidence | historical strict-high/remaining-heat proxy | state | 1349 | 31 | report-time IEM/METAR + fixed CITY_MODEL curve |
| evidence | historical source first-seen | state | 0 | 0 | coverage gap: ingest first-seen unavailable; report-time proxy kept separate |
| execution | full ten-share taker ladder covered | state | 1349 | 31 | official per-level taker fee; no future touch assumption |
| execution | actual 10+5 baseline selected in OOF evaluation window | city-day expression | 89 | 22 | first positive full ten-share taker-ladder EV; maker remains a separately stressed leg |
| execution | actual settled maker fills | fill | 0 | 0 | coverage gap; maker evaluated only through explicit stress scenarios |

Signal funnel 与 evidence/execution funnel 分开；strict-high/remaining-heat 是机制特征，book/source/settlement/fill 缺失只记 coverage gap。历史 source first-seen ingest 时刻不可恢复，所以 source surprise 仅用 `report_ts <= decision_ts` proxy，且单列 provenance。

## 概率层：baseline + overshoot residual

| model | features through | rows/dates | Brier | logloss | AUC | ΔBrier vs core | Δlogloss vs core |
|---|---|---:|---:|---:|---:|---:|---:|
| core_recal | over_base_logit | 909/23 | 0.07141 | 0.27768 | 0.698 | +0.008198 | +0.042823 |
| plus_strict_high | strict_high_left_censored_num | 909/23 | 0.07128 | 0.27715 | 0.697 | +0.008065 | +0.042294 |
| plus_remaining_heat | daylight_remaining_minutes | 909/23 | 0.07148 | 0.27781 | 0.686 | +0.008273 | +0.042956 |
| plus_forecast_revision | forecast_peak_hour_revision | 909/23 | 0.07195 | 0.27874 | 0.673 | +0.008742 | +0.043881 |
| plus_path_dynamics | temp_trend_3h_f | 909/23 | 0.07245 | 0.27976 | 0.669 | +0.009243 | +0.044903 |
| compact_v1 | source_to_settlement_basis_mae_pit_f | 909/23 | 0.07228 | 0.27877 | 0.672 | +0.009071 | +0.043917 |

同 rows raw baseline：market Brier/logloss `0.06476/0.24125`；frozen core `0.06321/0.23486`。compact top-decile overshoot lift=`1.27x`，捕获 `+12.77%` 的 overshoot city-days。

特征消融按预注册顺序累计，没有从 6 个坏例子追加 AND gate。`minutes_since_running_max` 未进入 challenger；strict clock 由历史逐报重建，equal high 不重置。
LODO 诊断覆盖 1349 states：Brier/logloss `0.06643/0.25577`，相对 core Δ `+0.004982/+0.027965`；只作审计，不替代 expanding OOF。

## 6 个 loss 与全部 winners 审计

| city | target_date | bracket | final | market mid | strict-high age | remaining gap | slope accel | source surprise | LODO risk |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| Busan | 2026-06-04 | 24 | 25.00 | 0.800 | 390.9 | 1.50 | 0.00 | 0.00 | +23.17% |
| LA | 2026-06-05 | 68-69 | 70.00 | 0.835 | 37.9 | 0.10 | 1.00 | 1.00 | +13.11% |
| Seattle | 2026-06-06 | 60-61 | 62.00 | 0.852 | 37.9 | -4.60 | 1.00 | 1.00 | +14.81% |
| Istanbul | 2026-06-16 | 23 | 23.89 | 0.821 | 370.9 | 0.22 | -1.80 | 0.00 | +18.53% |
| Karachi | 2026-06-20 | 34 | 35.00 | 0.825 | 91.6 | 1.72 | NA | 0.00 | +14.93% |
| Istanbul | 2026-06-26 | 27 | 27.78 | 0.800 | 280.9 | -0.50 | -1.80 | 0.00 | +22.73% |

Winner audit 完整保存 130 行；没有只对 loss 做事后过滤。LODO risk 只用于逐例审计，不作为 expanding-forward 晋升证据。

## Sizing A/B（maker/taker 分账）

主压力口径是 `adverse_same_overall_rate`：loss maker 100% fill，winner maker fill rate 下调，使总 fill rate仍等于当前观察值。Future touch 从未当作 fill。

| policy | city-days | capital | PnL | ROI | max day loss | ES/CVaR 10% |
|---|---:|---:|---:|---:|---:|---:|
| baseline_actual_10_taker_5_maker | 89 | $977.25 | $+53.16 | +5.44% | $-10.67 | $-9.65 |
| probability_only_fixed_size | 9 | $91.67 | $+17.08 | +18.64% | $+0.00 | $+0.00 |
| continuous_fractional_kelly_capped | 5 | $17.46 | $+3.51 | +20.12% | $+0.00 | $+0.00 |
| discrete_train_tertiles_15_10_5 | 5 | $30.22 | $+6.03 | +19.95% | $+0.00 | $+0.00 |

| candidate vs baseline | mean daily PnL Δ [95%CI] | ROI Δ [95%CI] | winner share harm | frozen-forward mean daily PnL Δ |
|---|---:|---:|---:|---:|
| probability_only_fixed_size | $-1.569 [$-3.591,$+0.758] | +13.20% [+8.96%,+18.67%] | +94.19% | $-0.064 |
| continuous_fractional_kelly_capped | $-2.159 [$-4.130,$+0.173] | +14.68% [+9.85%,+20.72%] | +97.72% | $-0.929 |
| discrete_train_tertiles_15_10_5 | $-2.049 [$-4.012,$+0.259] | +14.51% [+9.81%,+20.37%] | +96.51% | $-0.760 |

Maker adverse-selection sensitivity（不是 realized PnL）：

| maker scenario | baseline PnL | selected candidate PnL | PnL Δ |
|---|---:|---:|---:|
| no_fill | $+47.58 | $+13.86 | $-33.72 |
| outcome_neutral_observed_rate | $+61.21 | $+17.08 | $-44.13 |
| adverse_same_overall_rate | $+53.16 | $+17.08 | $-36.08 |
| losses_only | $+35.36 | $+13.86 | $-21.50 |

Sizing leave-one-date-out adverse mean-daily-PnL Δ range：`probability_only_fixed_size` [$-2.125,$-1.174]；`continuous_fractional_kelly_capped` [$-2.742,$-1.791]；`discrete_train_tertiles_15_10_5` [$-2.628,$-1.676]。

Maker actual evidence 仍不足：taker city-days=12，maker city-days=5，settled maker fills=0。因此 maker 结果只可读为 sensitivity，不是实现 PnL。

## 稳健性与三门

- significance proper-score CI gate=`False`；adverse PnL delta CI gate=`False`。
- baseline=same-row market + frozen core；gate=`False`（点估）/
`False`（CI）。
- forward=last 8 OOF dates frozen slice；same-sign gate=`False`。
- front/back、frozen-forward 与 sizing leave-one-date-out 均保存为固定产物；没有按结果重选日期或阈值。
- conclusion=`不采用`；任何生产变更都必须另走 `weather-strategy-deploy` 并再次取得用户显式确认。

## 8 环覆盖

- 描述性切片：PASS（完整 parent、loss/winner audit）。
- 统计推断：PASS（city-day 等权、target-date block bootstrap）。
- 信号判别/概率：PASS（expanding OOF、market/core baseline、ablation）。
- 执行微结构：PARTIAL（真实 taker ladder；maker fill/queue 尚无 settled 分母）。
- 容量：PASS 到 10 taker / 15 total desired cap 的历史 ladder；更大未测试。
- 组合相关性：PASS（同 target_date 合并 city-day cashflow）。
- 基准/反事实：PASS（同 rows、同 quote、只改概率/size）。
- frozen forward：PARTIAL/FAIL 取决于上面的 same-sign 与 CI，日期仍少。

## 产物与复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_overshoot_risk_sizing_overlay_v1.py
```

- script: `scripts/analysis/reheat_risk/research_current_yes_core_carry_overshoot_risk_sizing_overlay_v1.py`
- preregistration: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/preregistration.json`
- execution_addendum: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/preregistration_execution_addendum.json`
- opportunity_ledger: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/opportunity_ledger.csv`
- oof_predictions: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/oof_predictions.csv`
- feature_ablation: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/feature_ablation.csv`
- leave_date_out_predictions: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/leave_date_out_predictions.csv`
- loss_audit: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/loss_audit.csv`
- winner_audit: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/winner_audit.csv`
- sizing_ab: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/sizing_ab.csv`
- daily_portfolio: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/daily_portfolio.csv`
- funnel: `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_risk_sizing_overlay_v1/funnel.csv`
- json: `docs/analysis/2026-07/2026-07-27-current-yes-core-carry-overshoot-risk-sizing-overlay-v1.json`
- report: `docs/analysis/2026-07/2026-07-27-current-yes-core-carry-overshoot-risk-sizing-overlay-v1.md`
