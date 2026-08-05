# D-1 market-residual nested expanding-OOF tournament v1

weather-only:
significance=not_retested; locked W0 used only as a residual feature
calibration=legacy W0
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

四个预注册 residual 版本均按 target_date 做 nested expanding OOF。点估最佳 `V04_revision_spread_bias` 的 date-equal logloss=1.5601，market=1.5602，Δ=-0.0001（95% CI -0.0002..+0.0000；K=4 Bonferroni CI -0.0003..+0.0000）。
baseline gate=FAIL：当前没有一个版本可以声明击败 market。V01/V03 在 17/17 个 outer folds 都选择 market null；V02/V04 也分别在 16/17 folds 选择 null，因此 V04 的微小负 delta 只来自一个日期，不是稳定 residual。
只有 delta 与 Bonferroni CI 均小于 0 才称为击败 market。本报告不会把 beta 收缩到 0 后与 market 相近称为 alpha。

## 同分母 OOF scores

| arm | states | dates | cities | logloss | Brier | RPS | winner P | top-1 | ΔLL vs market (95% / Bonf.) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| M0_market | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | reference |
| V01_weather_logratio | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V02_ordinal_location_scale | 168 | 17 | 31 | 1.5603 | 0.06645 | 0.06301 | 0.269 | 37.2% | +0.0001 (+0.0000..+0.0004 / +0.0000..+0.0005) |
| V03_structured_weather | 168 | 17 | 31 | 1.5602 | 0.06644 | 0.06299 | 0.269 | 37.2% | +0.0000 (+0.0000..+0.0000 / +0.0000..+0.0000) |
| V04_revision_spread_bias | 168 | 17 | 31 | 1.5601 | 0.06644 | 0.06298 | 0.269 | 37.2% | -0.0001 (-0.0002..+0.0000 / -0.0003..+0.0000) |

## 四个版本

- V01：强正则 `log(weather/market)` offset。
- V02：只允许 coherent ordinal location/scale 调整，不直接使用逐档 weather likelihood。
- V03：V01+V02，并让 model spread 连续调整 weather residual。
- V04：再加入 assigned-minus-consensus bias 与 12–18h→18–24h reconstructed checkpoint revision。该 revision 是 archive reconstruction proxy，不是真实 provider run revision。
- 每个 outer date 的 ridge 都只用更早日期的 inner expanding OOF 选择；候选包含 `ridge=∞`，它严格等于 market。

## 数据 / 双漏斗

- denominator_scope：`reconstructed_single_run_D1_18_24_first_complete_native_ladder_settled_contemporaneous_normalized_market`。
- 输入 forecast artifact：/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_single_runs_backfill_v1/forecast_rows.csv（SHA 327957b9b20f…）。
- 输入 baskets：/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv（SHA 04e157bc60d5…）。
- signal funnel：1774 forecast snapshots → 1255 assigned snapshots → 279 primary-policy prepared states；前 10 dates 只训练，后 17 dates OOF。
- evidence funnel：1774 basket snapshots → 694 all-policy market+settlement scoreable → 168 primary OOF states / 17 dates；actual fills=0。
- coverage blockers：invalid_ladder=561，missing_market_mid=0，missing_settlement=0。它们是 evidence gaps，不是策略筛选。

## Calibration / city catastrophe

- M0_market: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (NA).
- V01_weather_logratio: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V02_ordinal_location_scale: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0014 (Shanghai).
- V03_structured_weather: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0000 (Atlanta).
- V04_revision_spread_bias: rung ECE=0.0124，winner≤1%=1，worst city mean ΔLL=+0.0011 (Shanghai).

## Evidence boundary

- 输入 lineage：forecast=`d1_single_runs_backfill_v1/forecast_rows.csv`、book=`d1_extreme_no_snapshot_history_v4/executable_baskets.csv`；label/native ladder/normalized market 读取上游 fixed-denominator `d1_cross_city_hierarchy_v1/scored_states.csv`（其 label 来自当时 materialized canonical `settlement_outcomes`），本 runner 不重扫 mutable live DB。没有读取 intraday atlas/P3 forecast backfill。
- PIT：market/forecast 是 single-run conservative reconstruction，不是严格 first-seen/provider-run capture；因此 OOF 防标签泄漏，但不能升级为 clean forward，也不能写 `beat_market` claim。
- W0/W1：本轮只把 locked legacy robust-tail W0 当 residual feature；真实 provider-run/first-seen/revision W1 尚未可评分，未训练、未伪造。
- M0 是同一 checkpoint normalized mid distribution，不是 ask/depth/fee/slippage execution baseline。
- K=4 同时报 95% 与 Bonferroni family-wise CI；未进行城市/价格/赢家事后筛选。
- 覆盖 8 环中的概率分布、统计推断和同分母 baseline；缺 execution、capacity、fills、clean frozen forward。
- 无论点估如何，本轮唯一动作都是 research/collector，不改 live。

## Runtime artifact

- point-estimate best=V04_revision_spread_bias，final expanding selection ridge=1.0；artifact schema=`weather_d1_market_residual_artifact_v1` / family=`linear_market_log_offset`。
- baseline_gate_pass=False，shadow_eligible=false，converter=blocked_missing_rung_feature_materializer。artifact 可被现有 loader 校验，但在 WCIR 生成同 lineage 的逐 rung features 前不可运行，更不可下单。
