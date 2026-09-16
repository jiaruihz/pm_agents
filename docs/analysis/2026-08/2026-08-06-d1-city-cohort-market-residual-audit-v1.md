# D-1 city-cohort market residual audit

weather-only:
significance=not_retested; locked robust-tail W0
calibration=legacy reconstructed development evidence
pooled_baseline=robust-tail W0
forward=not clean; final 9 dates were already observed by prior research

market residual:
baseline=same-row normalized market ladder
forward=secondary holdout only
execution=not run; historical market probability is non-executable

production:
live_action=none
orders_changed=0

## 结论

三个事前 cohort 都没有同时通过 inner validation、secondary holdout 与 K=3 Bonferroni gate。现有 D-1 city selection 不能作为击败 market 的可运行策略；保留 runner，等待 clean exact-run frozen forward。

三个版本共用锁定 W0，只改变事前 city cohort；market temperature 与 weather beta 只在最早 12 个 reconstructed target dates 选择。城市选择不读取 inner validation 或 final secondary holdout。

| version | rule | gamma / beta | inner-val states/dates | ΔLL vs raw market (95% CI) | secondary states/dates | ΔLL vs raw market (95% CI) | status |
|---|---|---:|---:|---:|---:|---:|---|
| V05_all_cities_market_offset | all scoreable assigned cities | 1.10 / 0.000 | 99/6 | +0.0057 [-0.0138,+0.0311] | 46/9 | +0.0030 [-0.0171,+0.0256] | rejected_inner_validation |
| V06_gfs_lower_source_mae | GFS-assigned; lower pre-test source MAE | 1.10 / 0.000 | 41/6 | +0.0224 [-0.0087,+0.0635] | 21/6 | -0.0110 [-0.0304,+0.0134] | rejected_inner_validation |
| V07_lower_half_city_mae | city pre-test summer MAE <= cross-city median | 1.20 / 0.000 | 46/6 | -0.0254 [-0.0565,+0.0037] | 24/9 | +0.0236 [-0.0139,+0.0630] | secondary_holdout_failed |

三个版本最终 `beta=0`：weather-only W0 对 calibrated market 没有增量。V07 在 inner validation 看似改善，但 secondary holdout 反号，是本轮最清楚的已有窗过拟合；V06 只有 secondary logloss 点估为负，Brier/RPS 与 CI 未共同通过。

## Proper-score paired deltas

负值表示 candidate 优于同 rows raw market。Bonferroni interval 已按本轮 K=3 调整。

| version | phase | metric | delta | 95% CI | K=3 Bonferroni CI |
|---|---|---|---:|---:|---:|
| V05_all_cities_market_offset | inner_validation | logloss | +0.005689 | [-0.013790,+0.031139] | [-0.016188,+0.036695] |
| V05_all_cities_market_offset | inner_validation | brier | -0.000252 | [-0.000587,+0.000234] | [-0.000616,+0.000391] |
| V05_all_cities_market_offset | inner_validation | rps | -0.000167 | [-0.000456,+0.000210] | [-0.000505,+0.000304] |
| V05_all_cities_market_offset | secondary_holdout | logloss | +0.003035 | [-0.017145,+0.025623] | [-0.020583,+0.030691] |
| V05_all_cities_market_offset | secondary_holdout | brier | +0.000313 | [-0.000245,+0.000744] | [-0.000379,+0.000798] |
| V05_all_cities_market_offset | secondary_holdout | rps | -0.000060 | [-0.000417,+0.000255] | [-0.000497,+0.000319] |
| V06_gfs_lower_source_mae | inner_validation | logloss | +0.022359 | [-0.008677,+0.063514] | [-0.010106,+0.075720] |
| V06_gfs_lower_source_mae | inner_validation | brier | -0.000052 | [-0.000614,+0.000695] | [-0.000682,+0.000853] |
| V06_gfs_lower_source_mae | inner_validation | rps | +0.000122 | [-0.000321,+0.000614] | [-0.000399,+0.000716] |
| V06_gfs_lower_source_mae | secondary_holdout | logloss | -0.011047 | [-0.030438,+0.013381] | [-0.034243,+0.016253] |
| V06_gfs_lower_source_mae | secondary_holdout | brier | +0.000100 | [-0.000679,+0.000653] | [-0.000906,+0.000711] |
| V06_gfs_lower_source_mae | secondary_holdout | rps | -0.000131 | [-0.000632,+0.000245] | [-0.000756,+0.000290] |
| V07_lower_half_city_mae | inner_validation | logloss | -0.025391 | [-0.056549,+0.003744] | [-0.062646,+0.009962] |
| V07_lower_half_city_mae | inner_validation | brier | -0.001273 | [-0.002220,-0.000425] | [-0.002375,-0.000309] |
| V07_lower_half_city_mae | inner_validation | rps | -0.000932 | [-0.001525,-0.000442] | [-0.001657,-0.000376] |
| V07_lower_half_city_mae | secondary_holdout | logloss | +0.023650 | [-0.013904,+0.063029] | [-0.021491,+0.072150] |
| V07_lower_half_city_mae | secondary_holdout | brier | +0.001633 | [+0.000138,+0.003042] | [-0.000190,+0.003309] |
| V07_lower_half_city_mae | secondary_holdout | rps | +0.000429 | [-0.000430,+0.001221] | [-0.000631,+0.001384] |

## Secondary same-row absolute scores

| version | arm | logloss | Brier | RPS | states / dates |
|---|---|---:|---:|---:|---:|
| V05_all_cities_market_offset | market | 1.549745 | 0.068361 | 0.062394 | 46 / 9 |
| V05_all_cities_market_offset | calibrated_market | 1.552780 | 0.068674 | 0.062334 | 46 / 9 |
| V05_all_cities_market_offset | posterior | 1.552780 | 0.068674 | 0.062334 | 46 / 9 |
| V06_gfs_lower_source_mae | market | 1.401876 | 0.064810 | 0.052262 | 21 / 6 |
| V06_gfs_lower_source_mae | calibrated_market | 1.390829 | 0.064911 | 0.052131 | 21 / 6 |
| V06_gfs_lower_source_mae | posterior | 1.390829 | 0.064911 | 0.052131 | 21 / 6 |
| V07_lower_half_city_mae | market | 1.575276 | 0.070788 | 0.062570 | 24 / 9 |
| V07_lower_half_city_mae | calibrated_market | 1.598926 | 0.072421 | 0.063000 | 24 / 9 |
| V07_lower_half_city_mae | posterior | 1.598926 | 0.072421 | 0.063000 | 24 / 9 |

## 数据与固定分母

- forecast input：`/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_single_runs_backfill_v1/forecast_rows.csv`，8870 rows / 28 target dates / 47 cities。
- executable-basket input：`/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv`，4086 rows / 56 target dates / 48 cities。
- history artifact：`/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv`，42705 rows；实际 W0 training slice=5561 rows / 253 dates / 39 cities。
- state funnel：assigned=1255 → scored=694；invalid ladder=561，missing settlement=0，missing market=0。
- phase split：beta fit=2026-06-17..2026-06-28 (12 dates)；inner validation=2026-06-30..2026-07-06 (6 dates)；secondary holdout=2026-07-07..2026-07-23 (9 dates)。

## City selection provenance

- source-level pre-test summer MAE：GFS=1.606°F，ECMWF=2.214°F；所以 V06 固定 GFS cohort。
- V07 cutoff 是 pre-test city MAE 中位数 1.797°F；保留 17 城。
- 这两条都是 forecast/coverage 规则，不是看 secondary holdout 后挑赢家。

## Evidence boundary

- reconstructed forecast 使用 conservative 12h lag，不是真实 provider run/first-seen PIT。state/settlement/market 使用既有 D-1 canonical-labeled score artifact 的固定 exact-ladder rows；本机 canonical DB/JRS 只读查询本轮超时，未混入另一个 build。
- target 是完整 native exact ladder final settlement；primary grain 每个 `city × target_date` 至多一个 18–24h checkpoint。没有使用 binary/relative bucket，也没有靠 repeated checkpoints 放大样本。
- 同 rows market 是 normalized contemporaneous ladder probability；不是可执行 ask/depth，故本轮不计算 fee-adjusted ROI。
- final 9 target dates 在本项目的早期 W0 研究中已被查看；本 runner 没有用它们选 cohort/beta，但仍只能称 secondary holdout，不能包装成 frozen forward。
- raw market 与 fold-fit calibrated market 都保存。weather 增量必须看 posterior vs calibrated market；若 beta=0，它严格等于 calibrated market，不算 weather alpha。
- K=3；表中同时保存 95% 与 Bonferroni family-wise 95% intervals，只有后者全负才算本轮多重检验后显著。
- `runnable_policy.json` 只是 deterministic research/shadow policy artifact；不授权 deployment、live 或 order。

## 双漏斗与 8 环

signal funnel：reconstructed D-1 states → fixed cohort → posterior probability rows；没有交易 selection。

evidence funnel：forecast snapshot + complete native ladder + same-clock market + settlement → probability score；executable expression/fill=not available。

覆盖统计推断、概率分布和同分母 baseline；缺 clean forward、execution microstructure、capacity、fills/PnL。
