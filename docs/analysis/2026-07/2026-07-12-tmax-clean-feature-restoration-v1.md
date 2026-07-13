# Tmax Clean Denominator Feature Restoration v1

## 结论

- 本轮固定复用 lineage replay v2 的 **8,094** 行 clean states（2026-06-19..2026-07-10，22 dates），没有重选 snapshot 或分母。
- 所有模型结果统一标为 `retrospective_expanding_walk_forward_diagnostic`；fresh forward = 0，所有 artifact 仅 shadow research，**不改 live**。
- strict primary 是 B/C/D；E 是 `report_time_reconstruction_only`，F 是 `research_backfill`，两者只作 research upper-bound，不能与 strict 候选混称。
- 这是 `tmax_distribution_v3` work order 的 feature availability / fixed ablation 前置证据，不是另一个模型任务。结论是：恢复 strict quote/source 后仍未改善 market proper score；archive meteo upper-bound 也没有显示增量，F 则不可答。
- paired alpha=.25 logloss delta vs market：minimal +0.0078（CI [+0.0016, +0.0172]）、strict quote +0.0088、strict source +0.0097、archive meteo +0.0112；正值均代表比 market 更差。不能把 execution ROI 点估倒推成 feature selection。

## PIT Enrichment

| enrichment | exact | asof prior | missing | coverage | median lag min | p95 lag min |
|---|---:|---:|---:|---:|---:|---:|
| feature factory city/date + timestamp as-of, lag<=60m | 1250 | 836 | 6008 | 25.8% | 0.0 | 37.3 |

- as-of 以 timestamp 为权威，在同 city/date 内只取 `feature_snapshot_ts <= clean decision_snapshot_ts` 且 lag<=60m 的最近一条；允许整点边界命中前一 local-hour，并另存 hour-match 审计字段。后到的 atlas snapshot 一律不使用。无法恢复的行继续保留，由 imputer/unknown 处理。
- GFS gap strict-asof且lag<=60m non-null=182/8094；ECMWF=182/8094。双模型只出现在 6/19..6/20，6/21+ 为 0，因此 F **not_answerable**，不训练、不输出 imputation 伪结果。meteo 只作 upper-bound，并显式加入 missing indicator；逐日 coverage 见 `asof_60m_coverage_daily.csv`。
- quote geometry 从 raw decision-group cache 的 exact `decision_snapshot_ts` 构造：matched=8094，missing=0；current/d1/d2 与完整 ladder 均来自同 snapshot。

## Proper Score

同一 route 的 delta 只在 market probability 完整的 paired rows 上计算，CI 为 date-block bootstrap。模型自身可在 8,094 corpus 上经 median/unknown 处理；market baseline 可评分分母为 7554。

### Strict Primary

| variant | route | rows | paired_market_rows | logloss | brier | delta_logloss | delta_logloss_ci_low | delta_logloss_ci_high |
|---|---|---|---|---|---|---|---|---|
| minimal_clean | coherent_identity_insufficient_past_oof | 2465 | 2464 | 0.6246 | 0.3028 | 0.0898 | 0.0204 | 0.2275 |
| minimal_clean | coherent_past_oof | 3637 | 3098 | 0.6612 | 0.3264 | 0.0370 | -0.0533 | 0.0828 |
| minimal_clean | market_blend_alpha_0.25 | 5562 | 5562 | 0.5191 | 0.2731 | 0.0078 | 0.0016 | 0.0172 |
| minimal_clean | market_blend_alpha_0.5 | 5562 | 5562 | 0.5332 | 0.2776 | 0.0219 | 0.0074 | 0.0424 |
| minimal_clean | raw | 6102 | 5562 | 0.6878 | 0.3290 | 0.1150 | 0.0430 | 0.2486 |
| strict_quote_geometry | coherent_identity_insufficient_past_oof | 2465 | 2464 | 0.7154 | 0.3297 | 0.1816 | 0.0849 | 0.3455 |
| strict_quote_geometry | coherent_past_oof | 3637 | 3098 | 0.7141 | 0.3283 | 0.1286 | 0.0223 | 0.2978 |
| strict_quote_geometry | market_blend_alpha_0.25 | 5562 | 5562 | 0.5201 | 0.2744 | 0.0088 | 0.0028 | 0.0185 |
| strict_quote_geometry | market_blend_alpha_0.5 | 5562 | 5562 | 0.5381 | 0.2832 | 0.0269 | 0.0121 | 0.0466 |
| strict_quote_geometry | raw | 6102 | 5562 | 0.6924 | 0.3277 | 0.1761 | 0.0847 | 0.2777 |
| strict_source_context | coherent_identity_insufficient_past_oof | 2465 | 2464 | 0.7279 | 0.3296 | 0.1943 | 0.0924 | 0.3675 |
| strict_source_context | coherent_past_oof | 3637 | 3098 | 0.7219 | 0.3270 | 0.1395 | 0.0188 | 0.3335 |
| strict_source_context | market_blend_alpha_0.25 | 5562 | 5562 | 0.5210 | 0.2747 | 0.0097 | 0.0016 | 0.0188 |
| strict_source_context | market_blend_alpha_0.5 | 5562 | 5562 | 0.5405 | 0.2841 | 0.0293 | 0.0103 | 0.0473 |
| strict_source_context | raw | 6102 | 5562 | 0.7056 | 0.3302 | 0.1919 | 0.0875 | 0.2966 |

### Research Upper-bound

| variant | route | rows | paired_market_rows | logloss | brier | delta_logloss | delta_logloss_ci_low | delta_logloss_ci_high |
|---|---|---|---|---|---|---|---|---|
| atlas_meteo_regime_upper_bound | coherent_identity_insufficient_past_oof | 2465 | 2464 | 0.7272 | 0.3300 | 0.1934 | 0.0911 | 0.3647 |
| atlas_meteo_regime_upper_bound | coherent_past_oof | 3637 | 3098 | 0.7156 | 0.3280 | 0.1335 | 0.0220 | 0.3141 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.25 | 5562 | 5562 | 0.5225 | 0.2753 | 0.0112 | 0.0033 | 0.0207 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.5 | 5562 | 5562 | 0.5437 | 0.2856 | 0.0324 | 0.0139 | 0.0516 |
| atlas_meteo_regime_upper_bound | raw | 6102 | 5562 | 0.7106 | 0.3346 | 0.1967 | 0.0984 | 0.2933 |
| gfs_ecmwf_backfill_upper_bound | not_answerable | 0 | 0 |  |  |  |  |  |

## Execution First-lock

- 每个 `variant + route` arm 独立按 city-day 的 snapshot 顺序扫描，首次有任一固定表达满足 ask/fee/edge 即锁定；arm 之间不抢第一笔。
- 5 shares；YES 只用 direct YES ask；ask size 必须 finite 且 >=5；official fee=`0.05*p*(1-p)`；ask 0.40..0.99；edge >=0.02。

| variant | route | rows | dates | pnl | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|
| atlas_meteo_regime_upper_bound | coherent_identity_insufficient_past_oof | 144 | 5 | -2.0705 | -0.0039 | -0.1104 | 0.0777 |
| atlas_meteo_regime_upper_bound | coherent_past_oof | 166 | 12 | -15.1600 | -0.0251 | -0.0936 | 0.0286 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.25 | 168 | 12 | -20.2459 | -0.0358 | -0.1418 | 0.0496 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.5 | 251 | 13 | -8.3107 | -0.0094 | -0.0961 | 0.0571 |
| atlas_meteo_regime_upper_bound | raw | 322 | 17 | -11.8169 | -0.0100 | -0.0601 | 0.0384 |
| market_only | market | 23 | 13 | 6.9218 | 0.1017 | -0.2279 | 0.3906 |
| minimal_clean | coherent_identity_insufficient_past_oof | 137 | 5 | 34.6307 | 0.0679 | -0.0573 | 0.1544 |
| minimal_clean | coherent_past_oof | 174 | 12 | 3.7647 | 0.0059 | -0.0752 | 0.0872 |
| minimal_clean | market_blend_alpha_0.25 | 122 | 12 | 8.5022 | 0.0212 | -0.0949 | 0.1225 |
| minimal_clean | market_blend_alpha_0.5 | 218 | 13 | -15.4630 | -0.0198 | -0.1045 | 0.0529 |
| minimal_clean | raw | 303 | 17 | 15.0866 | 0.0136 | -0.0596 | 0.0779 |
| strict_quote_geometry | coherent_identity_insufficient_past_oof | 142 | 5 | 12.1770 | 0.0229 | -0.0889 | 0.1220 |
| strict_quote_geometry | coherent_past_oof | 162 | 12 | -26.3137 | -0.0441 | -0.1099 | 0.0020 |
| strict_quote_geometry | market_blend_alpha_0.25 | 155 | 12 | -25.1935 | -0.0480 | -0.1648 | 0.0495 |
| strict_quote_geometry | market_blend_alpha_0.5 | 236 | 13 | -24.5047 | -0.0292 | -0.1185 | 0.0602 |
| strict_quote_geometry | raw | 316 | 17 | 14.7728 | 0.0126 | -0.0422 | 0.0639 |
| strict_source_context | coherent_identity_insufficient_past_oof | 146 | 5 | 16.7514 | 0.0308 | -0.0861 | 0.1170 |
| strict_source_context | coherent_past_oof | 166 | 12 | -2.3819 | -0.0040 | -0.0740 | 0.0477 |
| strict_source_context | market_blend_alpha_0.25 | 163 | 12 | -34.0686 | -0.0615 | -0.1907 | 0.0311 |
| strict_source_context | market_blend_alpha_0.5 | 254 | 13 | -33.8868 | -0.0379 | -0.1367 | 0.0347 |
| strict_source_context | raw | 323 | 17 | 16.8870 | 0.0142 | -0.0378 | 0.0619 |

## d2_no

旧 minimal reference 是 174 笔；本轮各固定 variant/route 如下。改善与否只描述，不用于选择 spec/alpha/threshold。

| variant | route | rows | pnl | cost | roi |
|---|---|---|---|---|---|
| atlas_meteo_regime_upper_bound | coherent_identity_insufficient_past_oof | 87 | 1.2954 | 348.7046 | 0.0037 |
| atlas_meteo_regime_upper_bound | coherent_past_oof | 91 | -4.3898 | 349.3898 | -0.0126 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.25 | 93 | -10.9575 | 320.9575 | -0.0341 |
| atlas_meteo_regime_upper_bound | market_blend_alpha_0.5 | 148 | -16.4741 | 546.4741 | -0.0301 |
| atlas_meteo_regime_upper_bound | raw | 184 | -2.0140 | 717.0140 | -0.0028 |
| market_only | market | 6 | 3.9131 | 16.0869 | 0.2433 |
| minimal_clean | coherent_identity_insufficient_past_oof | 81 | 14.4004 | 320.5996 | 0.0449 |
| minimal_clean | coherent_past_oof | 101 | 0.7480 | 394.2520 | 0.0019 |
| minimal_clean | market_blend_alpha_0.25 | 66 | 8.0047 | 221.9953 | 0.0361 |
| minimal_clean | market_blend_alpha_0.5 | 140 | -19.4398 | 519.4398 | -0.0374 |
| minimal_clean | raw | 192 | -6.5423 | 736.5423 | -0.0089 |
| strict_quote_geometry | coherent_identity_insufficient_past_oof | 88 | 0.5891 | 349.4109 | 0.0017 |
| strict_quote_geometry | coherent_past_oof | 93 | -1.2904 | 356.2904 | -0.0036 |
| strict_quote_geometry | market_blend_alpha_0.25 | 90 | -8.9931 | 308.9931 | -0.0291 |
| strict_quote_geometry | market_blend_alpha_0.5 | 152 | -26.1037 | 566.1037 | -0.0461 |
| strict_quote_geometry | raw | 190 | 13.2735 | 736.7265 | 0.0180 |
| strict_source_context | coherent_identity_insufficient_past_oof | 88 | 3.3816 | 351.6184 | 0.0096 |
| strict_source_context | coherent_past_oof | 89 | -6.4518 | 341.4518 | -0.0189 |
| strict_source_context | market_blend_alpha_0.25 | 95 | -14.9516 | 329.9516 | -0.0453 |
| strict_source_context | market_blend_alpha_0.5 | 153 | -32.9601 | 562.9601 | -0.0585 |
| strict_source_context | raw | 187 | 7.5284 | 727.4716 | 0.0103 |

## 三道门与裁决

- statistical gate：paired date-block logloss delta CI upper < 0。
- economics gate：5-share fee-adjusted ROI date-block CI lower > 0。
- fresh-forward gate：固定 FAIL（fresh dates=0）。因此 promotion 全部 FAIL，artifact 只 shadow。
- coherent second-stage 只使用更早日期的 expanding OOF prediction；不足 5 个既有 OOF dates 时 identity/skip，绝不读取测试日 label。

完整逐日、表达、YES/NO、C/F、transition、top10 removed、coverage/provenance 与三道门见 `generated/tmax_clean_feature_restoration_v1/`。
