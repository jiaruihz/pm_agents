# D-1 market-residual nonlinear nested expanding-OOF tournament v2

weather-only:
significance=FAIL; W0-market delta=+0.2552, 95% CI +0.1357..+0.3819
calibration=locked robust-tail W0 on identical OOF rows
pooled_baseline=robust-tail W0
forward=reconstructed development only

market residual:
baseline=M0 contemporaneous normalized market on identical rows
forward=nested expanding OOF over 17 target dates; not clean provider-run forward
execution=not_run; reconstructed mids are not executable quotes

production:
live_action=none
orders_changed=0

## 结论

locked weather-only W0 在相同 OOF rows 的 logloss=1.8154，显著差于 market=1.5602，Δ=+0.2552（95% CI +0.1357..+0.3819）。
8 个 residual 版本均按 target_date 做 nested expanding OOF。点估最佳 `V04_revision_spread_bias` 的 date-equal logloss=1.5601，market=1.5602，Δ=-0.0001（95% CI -0.0002..+0.0000；K=8 Bonferroni CI -0.0003..+0.0000）。
baseline gate=FAIL。每个候选都可由 inner OOF 选择 `ridge=∞` 严格退回 market；退回次数：V01_weather_logratio=17/17、V02_ordinal_location_scale=16/17、V03_structured_weather=17/17、V04_revision_spread_bias=16/17、V05_piecewise_surprise_gam=17/17、V06_revision_gated_nonlinear=14/17、V07_random_feature_network=17/17、V08_hybrid_nonlinear=17/17。
只有 delta 与 Bonferroni CI 均小于 0 才称为击败 market。本报告不会把 beta 收缩到 0 后与 market 相近称为 alpha。

## 同分母 OOF scores

| arm | states | dates | cities | logloss | Brier | RPS | winner P | top-1 | ΔLL vs market (95% / Bonf.) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| M0_market | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | reference |
| W0_weather_only | 168 | 17 | 31 | 1.8154 | 0.07334 | 0.08048 | 0.199 | 29.3% | +0.2552 (+0.1357..+0.3819 / +0.1357..+0.3819) |
| V01_weather_logratio | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V02_ordinal_location_scale | 168 | 17 | 31 | 1.5603 | 0.06645 | 0.06301 | 0.269 | 37.2% | +0.0001 (+0.0000..+0.0004 / +0.0000..+0.0005) |
| V03_structured_weather | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V04_revision_spread_bias | 168 | 17 | 31 | 1.5601 | 0.06644 | 0.06298 | 0.269 | 37.2% | -0.0001 (-0.0002..+0.0000 / -0.0003..+0.0000) |
| V05_piecewise_surprise_gam | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V06_revision_gated_nonlinear | 168 | 17 | 31 | 1.5610 | 0.06647 | 0.06306 | 0.269 | 36.0% | +0.0008 (-0.0003..+0.0023 / -0.0004..+0.0029) |
| V07_random_feature_network | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V08_hybrid_nonlinear | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |

## 八个版本

- V01：强正则 `log(weather/market)` offset。
- V02：只允许 coherent ordinal location/scale 调整，不直接使用逐档 weather likelihood。
- V03：V01+V02，并让 model spread 连续调整 weather residual。
- V04：再加入 assigned-minus-consensus bias 与 12–18h→18–24h reconstructed checkpoint revision。该 revision 是 archive reconstruction proxy，不是真实 provider run revision。
- V05：piecewise GAM，将 weather surprise 拆成正/负、0.5/1.0 log-odds hinge，并单独处理 market<15% 的档位。
- V06：按 spread 门控 forecast agreement/disagreement，并拆开升温/降温 revision 与 tail interaction。
- V07：16 节点固定随机 tanh hidden layer + 正则 softmax output；是可复现的非线性 challenger。
- V08：piecewise、revision gate 与 nonlinear hidden features 的联合模型。
- 每个 outer date 的 ridge 都只用更早日期的 inner expanding OOF 选择；候选包含 `ridge=∞`，它严格等于 market。

## 与市场偏离幅度

| arm | median max shift | p90 | max | states ≥2pp | ≥5pp | ≥10pp |
|---|---:|---:|---:|---:|---:|---:|
| V01_weather_logratio | 0.00% | 0.00% | 0.00% | 0 | 0 | 0 |
| V02_ordinal_location_scale | 0.00% | 0.00% | 0.16% | 0 | 0 | 0 |
| V03_structured_weather | 0.00% | 0.00% | 0.00% | 0 | 0 | 0 |
| V04_revision_spread_bias | 0.00% | 0.00% | 0.40% | 0 | 0 | 0 |
| V05_piecewise_surprise_gam | 0.00% | 0.00% | 0.00% | 0 | 0 | 0 |
| V06_revision_gated_nonlinear | 0.00% | 0.91% | 8.56% | 5 | 1 | 0 |
| V07_random_feature_network | 0.00% | 0.00% | 0.00% | 0 | 0 | 0 |
| V08_hybrid_nonlinear | 0.00% | 0.00% | 0.00% | 0 | 0 | 0 |

## 数据 / 双漏斗

- denominator_scope：`reconstructed_single_run_D1_18_24_first_complete_native_ladder_settled_contemporaneous_normalized_market`。
- 输入 forecast artifact：/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_single_runs_backfill_v1/forecast_rows.csv（SHA 327957b9b20f…）。
- 输入 baskets：/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv（SHA 04e157bc60d5…）。
- signal funnel：1774 forecast snapshots → 1255 assigned snapshots → 279 primary-policy prepared states；前 10 dates 只训练，后 17 dates OOF。
- evidence funnel：1774 basket snapshots → 694 all-policy market+settlement scoreable → 168 primary OOF states / 17 dates；actual fills=0。
- coverage blockers：invalid_ladder=561，missing_market_mid=0，missing_settlement=0。它们是 evidence gaps，不是策略筛选。

## Calibration / city catastrophe

- M0_market: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (NA).
- W0_weather_only: rung ECE=0.0031，winner≤1%=3，worst city mean ΔLL=+1.0109 (Jeddah).
- V01_weather_logratio: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V02_ordinal_location_scale: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0014 (Shanghai).
- V03_structured_weather: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V04_revision_spread_bias: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0011 (Shanghai).
- V05_piecewise_surprise_gam: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V06_revision_gated_nonlinear: rung ECE=0.0114，winner≤1%=2，worst city mean ΔLL=+0.0228 (Chongqing).
- V07_random_feature_network: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V08_hybrid_nonlinear: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).

## Evidence boundary

- 输入 lineage：forecast=`d1_single_runs_backfill_v1/forecast_rows.csv`、book=`d1_extreme_no_snapshot_history_v4/executable_baskets.csv`；label/native ladder/normalized market 读取上游 fixed-denominator `d1_cross_city_hierarchy_v1/scored_states.csv`（其 label 来自当时 materialized canonical `settlement_outcomes`），本 runner 不重扫 mutable live DB。没有读取 intraday atlas/P3 forecast backfill。
- PIT：market/forecast 是 single-run conservative reconstruction，不是严格 first-seen/provider-run capture；因此 OOF 防标签泄漏，但不能升级为 clean forward，也不能写 `beat_market` claim。
- W0/W1：本轮在同一 OOF rows 独立评分 locked legacy robust-tail W0，并把它作为 residual feature；真实 provider-run/first-seen/revision W1 尚未可评分，未训练、未伪造。
- M0 是同一 checkpoint normalized mid distribution，不是 ask/depth/fee/slippage execution baseline。
- K=8 同时报 95% 与 Bonferroni family-wise CI；未进行城市/价格/赢家事后筛选。
- 覆盖 8 环中的概率分布、统计推断和同分母 baseline；缺 execution、capacity、fills、clean frozen forward。
- 无论点估如何，本轮唯一动作都是 research/collector，不改 live。

## Runtime artifact

- point-estimate best=V04_revision_spread_bias，final expanding selection ridge=1.0；artifact schema=`weather_d1_market_residual_artifact_v1` / family=`regularized_nonlinear_basis_market_log_offset`。
- baseline_gate_pass=False，shadow_eligible=false，converter=blocked_missing_rung_feature_materializer。artifact 可被现有 loader 校验，但在 WCIR 生成同 lineage 的逐 rung features 前不可运行，更不可下单。
