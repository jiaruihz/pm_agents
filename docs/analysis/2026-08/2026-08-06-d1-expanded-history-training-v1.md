# D-1 expanded-history weather-only 训练 v1

weather-only:
significance=expanded_history_does_not_improve_locked_W0
calibration=archive_multimodel_reduces_near_zero_winners_but_not_proper_scores
pooled_baseline=robust_tail_W0_legacy_reference
forward=legacy_reconstructed_development_only; clean_exact_run_settlement_complete=0

market residual:
baseline=market_same_rows
forward=not_run_by_contract
execution=not_run_by_contract

production:
live_action=none
orders_changed=0

## 结论

该 legacy daily-error artifact 远多于上一版列出的 5,561 行，但使用其完整 42,705-row scope 后没有改善 D-1 概率模型。5,561 只是 `best source × 5–8月` training slice；该 artifact 为 42,705 rows / 52 cities / 738 dates，另有 26,741 rows / 45 cities / 18 models 的 7 月 8 日 archive-known multi-model calibration。两者都不等于项目全部历史。

同一 46 states / 9 target dates holdout 上，扩大到全年 best-source、加入 seasonal harmonic、以及使用全部 42,705 行估计 source residual，logloss 分别为 `2.1096 / 2.1060 / 2.1126`，均差于原 summer F `1.9763`，更差于已经锁定的 robust-tail W0 `1.8506`。因此 W0 保持不变；新增口径作为已检验的 negative controls，不冻结为 W1。

7 月 8 日才可审计的 18-model archive 只在 7 月 16–23 日评分，未回灌早期 decision；当时 reconstructed decision 实际有 5 个与 archive 重叠的 model，H 对这 5 个 source 等权。H logloss=`1.9555`；相对同 8 日 W0 的 delta=`+0.0683`，95% CI `[-0.1109,+0.2521]`，没有增量；相对 market delta=`+0.3922`，CI `[+0.1042,+0.6763]`，显著更差。H 把 winner probability ≤1% 的状态降为 0，但 RPS 比 W0 差 `+0.0089`，所以只能保留为未来 scale/tail feature，不可单独替换 W0。

## 数据全貌

| 数据层 | rows | cities | models | dates | 正确用途 |
|---|---:|---:|---:|---:|---|
| legacy artifact, summer best slice | 5,561 | 39 | assigned GFS/ECMWF | 253 | 原 W0 location/residual prior |
| legacy artifact, all-season best slice | 16,916 | 39 | assigned GFS/ECMWF | 738 | 检验季节外扩 |
| legacy artifact, all-season all-model rows | 42,705 | 52 | GFS + ECMWF | 738 | source-level residual shape；city correction 仍只落 assigned source |
| reconstructed D-1 single run | 8,870 | 47 | 5 | 28 | legacy development/secondary holdout，不是真实 run-aware forward |
| multi-model archive calibration | 26,741 | 45 | 18 | 64 | 2026-07-08 以后才允许启用的 archive-known challenger |
| executable basket archive | 4,086 | 48 | — | 56 | market/execution coverage inventory；本轮不算 ROI |

`42,705` 不是 42,705 个独立 D-1 forecast decisions。它是 daily cache 的 forecast-error rows，缺少真实 provider run、first-seen vintage、run age 和 revision；未 assigned source 的行只用于 pooled source residual，不能冒充该城额外独立信号。

## 同分母结果

| model | train rows | logloss | Brier | RPS | rung ECE | winner≤1% |
|---|---:|---:|---:|---:|---:|---:|
| summer F | 5,561 | 1.9763 | 0.07710 | 0.09033 | 0.02694 | 2 |
| all-season F | 16,916 | 2.1096 | 0.08168 | 0.10183 | 0.05037 | 2 |
| all-season + harmonic F | 16,916 | 2.1060 | 0.08148 | 0.10206 | 0.04114 | 2 |
| all-season + all models F | 42,705 | 2.1126 | 0.08203 | 0.10239 | 0.04795 | 1 |
| robust-tail W0 | 5,561 | **1.8506** | **0.07506** | **0.08282** | **0.01604** | 1 |
| market | — | **1.5497** | **0.06836** | **0.06239** | 0.01963 | 0 |

全年历史的主要问题不是行数不足，而是训练分布与 D-1 decision vintage 不同。更多冬季和未 assigned source rows 改变 residual scale/tail，却没有真实 run age、lead、revision 和同 clock physical width 来解释这种异质性，最终把当前夏季 D-1 分布校准得更差。harmonic 只能调整 center，无法修复 vintage-dependent width/tail。

## Signal / evidence funnel

- signal funnel：1,774 reconstructed forecast snapshots → 1,255 assigned-model snapshots → 694 scoreable states；561 因 native ladder 无效进入 coverage blocker。
- legacy holdout：46 states / 9 dates / 21 cities；已被多次用于开发诊断，不再称 frozen forward。
- archive H：41 states / 8 dates / 19 cities；只覆盖 2026-07-16..23。
- clean exact-run readiness（2026-08-06 重跑）：20,944 raw forecast rows → 4,828 poll batches → 784 material batches → 305 complete batches → 102 D-1 events；这 102 个均属 bootstrap/partial→complete coverage，`forward_new_complete_run_events=0`，settlement-complete=0，probability-scoreable=0。
- market repricing coverage：1,030 checkpoints / 293 complete；当前只用于 coverage/clock 检查，actual fills=0。

## 模型决定

1. 保持 robust-tail W0 为 locked legacy reference；不把 16,916/42,705 口径替换进去。
2. H 不作为独立 W1。把它提供的 multi-model disagreement 与 broad-tail 信息保留为 W1 的连续 scale/tail feature候选。
3. W1 的真正训练仍等待 clean exact-run settlement。首批样本进入 development 后，固定比较 `W0 / revision+spread+run-age / +physical width`，按 target-date block inner CV 选择；不是等 30 日才第一次运行，7/14/21 日只跑 learning-curve diagnostic、不选最终 family。
4. weather-only gate 未通过前，不用 market blend 包装结果；本轮不算交易 ROI，不改 live。

## 可复跑产物

- runner：`scripts/analysis/forecast_quality/research_d1_legacy_weather_only_v2.py`
- 聚合分数：`generated/d1_expanded_history_training_v1/history_policy_score_summary.csv`
- archive H 对照：`generated/d1_expanded_history_training_v1/archive_multimodel_score_summary.csv`
- 完整逐 state / calibration / bootstrap artifacts：`/Volumes/jrs/pm_agents/research/artifact_store/active/d1_weather_only_probability_challenger/history_policy_*_20260806`
- clean readiness：`/Volumes/jrs/pm_agents/research/artifact_store/active/d1_weather_only_probability_challenger/revision_repricing_readiness_20260806`
