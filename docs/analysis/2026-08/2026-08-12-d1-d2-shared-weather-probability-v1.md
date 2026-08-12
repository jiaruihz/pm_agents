# D-1/D-2 shared weather probability v1

weather-only:
significance=PASS_vs_legacy_raw / FAIL_vs_market
calibration=shared_structure_selected
pooled_baseline=legacy_probability_telemetry
forward=secondary_reconstructed_holdout_not_strict_provider_run

market residual:
baseline=same-row normalized market
forward=development beta selected as zero for D-1 and D-2
execution=not_run_no_positive_residual

production:
live_action=none
orders_changed=0

## 结论

D-1 与 D-2 现在使用同一套概率架构，不再维护两套互不相干的模型：共同候选包括 incumbent
probability telemetry、42,705 条长期 city/source residual、temperature、ordinal location shift、
adjacent-rung diffusion、tail floor，以及 strongly-shrunk city center。D-1/D-2 可以分别校准 lead
参数，但 city 不能独立学习 tail shape。

开发集 inner validation 最终选择 `legacy telemetry + shared calibration`，而不是重新构造的
`empirical physical prior`，也没有选择 lead-specific 或 city-specific tail。正式参数为：

```text
temperature=1.1274
ordinal shift=+0.354 rung
adjacent diffusion=0.3442
uniform tail floor=1.08%
market feature used=false
```

它显著修复旧模型的过度自信，但仍不足以从市场中提取可交易 residual。market-offset 的 inner
selection 在 D-1 与 D-2 都选择 `beta=0`；在
`log P_post=(1-beta)log P_market+beta log P_weather-log Z` 中，这严格等于原 market。

## 数据与漏斗

- recovered probability panel：13,107 rows；2026-05-20..08-10。
- native lattice 连续且 winner 可映射：9,988 rows；缺档/不连续 ladder 不静默保留。
- 长历史 empirical physical prior 可构造并进入共同比较：9,894 rows。
- D-1：9,648 states / 74 dates / 48 cities。
- D-2：246 states / 29 dates / 14 cities。
- development：至 2026-06-12，3,956 states / 24 dates。
- shared D-1/D-2 secondary holdout：06-13..06-20，1,139 states / 8 dates。
- late D-1 temporal stress：06-21..08-10，4,799 states / 42 dates。
- lineage 是 recovered strategy snapshots，不是 response-complete provider-run frozen forward。

## 同分母结果

| lead | arm | states / dates | logloss | Brier | RPS |
|---|---|---:|---:|---:|---:|
| D-1 | legacy raw | 1,069 / 8 | 2.6199 | 0.08636 | 0.10698 |
| D-1 | shared calibrated weather | 1,069 / 8 | 1.9633 | 0.08132 | 0.10217 |
| D-1 | market | 1,069 / 8 | 1.5779 | 0.07281 | 0.06897 |
| D-2 | legacy raw | 70 / 7 | 2.7809 | 0.07880 | 0.09136 |
| D-2 | shared calibrated weather | 70 / 7 | 1.8773 | 0.07467 | 0.08706 |
| D-2 | market | 70 / 7 | 1.7008 | 0.07171 | 0.07047 |

相对 legacy raw 的 logloss 改善：D-1 `-0.6566`，95% CI
`[-0.9751,-0.3543]`；D-2 `-0.9036`，CI `[-2.0055,-0.0512]`。相对 market：
D-1 仍差 `+0.3854`，CI `[+0.2672,+0.5047]`；D-2 点估差 `+0.1766`，CI
`[-0.0652,+0.4048]`。D-2 CI 跨零不是通过，而是样本不足。

late D-1 42-date stress 中 calibrated weather logloss `1.8921`，market `1.3656`，差
`+0.5265`，CI `[+0.4664,+0.5998]`，说明 gap 没有随时间消失。

## 历史组件复用判定

- 保留：共享结构、city/source center bias、temperature widening、coherent ordinal shift、adjacent
  diffusion、tail floor、lead-specific challenger。
- 未选中：用长期 daily residual 从 forecast max 重新生成 empirical exact-ladder distribution；inner
  logloss 最优仍为 `1.8375`，差于 incumbent shared calibration `1.7958`。
- 不再使用：city-only tail、固定 Normal、未校准旧 `model_prob`、用 market blend 掩盖 weather-only。
- market residual：当前 development 明确收缩至0；不能据此产生 D-2 买入信号。

## 对交易思路的含义

“D-2 公允概率高于 ask 后买入，回归公允价后动态卖出”的机制仍是正确待检验形式；但当前最新版
probability head 没有证明自己的公允价优于市场。因此现在不能把 `p_weather-ask` 当 edge。下一步不是继续
更换持有分钟数，而是把 response-complete exact-run 的 multi-model consensus、revision、spread、run age
接入同一模型，并在新的 D-2 ladder panel 上冻结验证；只有 market residual 出现非零稳定权重，才训练
`current ask -> future executable bid` 的 convergence/exit head。

## 产物

- runner：`scripts/analysis/forecast_quality/research_d1_d2_weather_only_v2.py`
- model library：`weather_model_evaluation/d1_d2_probability.py`
- artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/d1_d2_weather_only_probability/shared_calibration_20260812_v4`
- tests：`tests/pmm_tests/test_d1_d2_probability.py`
