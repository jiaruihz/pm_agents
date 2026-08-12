# D-1 legacy weather-only v2 训练与 holdout 报告

weather-only:
significance=improves_pooled
calibration=legacy_holdout_only_not_clean_forward
pooled_baseline=B_pooled_normal_zero_bias
forward=legacy_reconstructed_holdout_not_clean_run_aware_forward

market residual:
baseline=market_same_rows
forward=not_run_by_contract
execution=not_run_by_contract

production:
live_action=none
orders_changed=0

Artifact routing: historical machine outputs are recoverable from the archive
manifests referenced by the repository artifact index. New runs require a
stable `--run-id` and write beneath the configured JRS research artifact root;
the runner no longer overwrites this snapshot report by default.

## 结论

F ensemble/spread holdout logloss=1.9763；相对 zero-bias pooled Δ=-0.9139（95% CI -1.3574..-0.4532）。相对 market Δ=+0.4266（+0.1211..+0.7907）。

legacy holdout 为 46 states / 9 target dates / 21 cities。它可以否定坏结构和选择开发方向，但不能替代新 collector 的 frozen forward。

## 同分母 holdout score

| arm | logloss | Brier | RPS | winner P | top-1 |
|---|---:|---:|---:|---:|---:|
| market | 1.5497 | 0.0684 | 0.0624 | 0.257 | 28.5% |
| A_climatology | 2.5377 | 0.0901 | 0.1504 | 0.110 | 12.5% |
| B_pooled_normal_zero_bias | 2.8902 | 0.0873 | 0.1451 | 0.129 | 16.8% |
| C_bias_corrected_pooled_normal | 2.8714 | 0.0865 | 0.1410 | 0.134 | 9.0% |
| D_coherent_pooled_empirical | 2.6997 | 0.0868 | 0.1410 | 0.132 | 9.0% |
| E_partial_hierarchy_v2 | 2.8647 | 0.0880 | 0.1406 | 0.141 | 10.7% |
| F_ensemble_spread | 1.9763 | 0.0771 | 0.0903 | 0.200 | 21.9% |

G（physical-width）没有进入本轮：旧 reconstruction 没有同 clock 的 rain/convective/cloud/wind 完整特征。缺特征记 coverage blocker，不用事后天气或 hard filter 补洞。

## 训练合同

- legacy summer best-model training slice：5561 rows / 39 cities；lineage=`legacy_daily_cache_non_strict_pit_training_prior`。
- denominator funnel：`daily_error_rows.csv` 42,705 rows / 52 cities / 738 dates → `is_best_model=true` 16,916 rows / 39 cities → May–Aug training slice 5,561 rows / 39 cities / 253 dates。5,561 不是项目全部历史，42,705 也只代表这个 artifact 的输入 scope。
- partial hierarchy：center λ=10，scale λ=60；只在历史 validation 选。
- F overlay：ensemble weight=0.75，spread beta=0.00；只在前 18 个 reconstructed dates 选。
- holdout：2026-07-07..2026-07-23；没有调 λ、weight 或 beta。
- market 只作同 rows baseline，没有进入 A-F；market residual 和交易表达均未运行。
- F 中 run revision / lead / run age 在旧 reconstruction 中不可用；本轮 F 实际只检验 ensemble median 与 spread。开发集选择 `spread beta=0`，现有 spread 没有增量价值。

## Calibration 与 tail

- F rung ECE=0.0269，pooled=0.0702，market=0.0196。
- F bottom tail：预测 0.3% / 实际 2.2%；top tail：预测 0.7% / 实际 0.0%。
- F 给真实 winner ≤1% 概率的 state=2；仍需在更大 clean forward 上检查 city 灾难尾。

## 下一步

把本轮最好的 weather-only 结构作为 challenger 固定下来；新 exact-run collector 的首个完整 target date 之后，按相同代码重估/验证。只有 clean run-aware forward 通过 weather gate，才运行 market residual。
