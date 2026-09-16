# HeadA 多源 + forecast bias/升温路径增量检验 v1

Generated: 2026-07-28T10:11:04+00:00

## 结论与动作

**有帮助，但帮助发生在两个不同阶段，不能把两类信息直接混成原 entry selector。**

- D-1/前夜 entry：multi-source 单独加入后两个测试窗都变差；严格 prior rolling bias 在
  historical recent 明显改善、current shadow 仅小幅改善且 CI 跨 0。不改 HeadA eligibility。
- 当天 09:00/12:00：actual warming innovation 对已经持有的 exact ticket 有条件增量，12:00 的
  exact-ticket Brier / logloss 相对 rolling-bias baseline 改善且 date-block CI 不跨 0。
  但只有 32 tickets / 6 dates / 3 wins，且没有同刻 fresh book，当前只能作为 hold/exit/re-entry
  shadow feature。

裁决：`entry=inconclusive`；`intraday=shadow_candidate`；`live_action=none`。

## 时钟与 leakage 边界

当前 84 张：

```json
{
  "rows": 84,
  "before_target_local_09_rows": 84,
  "on_prior_local_date_rows": 83,
  "entry_local_hour_median": 15.541666666666668,
  "entry_local_hour_min": 0.13333333333333333,
  "entry_local_hour_max": 23.566666666666666
}
```

84/84 都在目标日当地 09:00 前进入，其中 83/84 在目标日前一当地日期。
所以 09:00/12:00 的 `observed - model` innovation 在原 entry 尚未发生。把它回填到 entry 模型会产生
future leakage；正确架构是：

```text
D-1 entry:
  market + multi-source state + prior rolling bias

target-day update:
  actual warming path innovation
  -> hold / exit / re-entry / exact-bracket redistribution
```

## 固定分母与证据漏斗

### Signal funnel

- historical entry denominator：333 tickets / 53 dates。
- current frozen shadow：84 tickets / 8 dates。
- 09:00 overlap：31 tickets。
- 12:00 overlap：32 tickets。

### Evidence funnel

- entry fresh ask / settlement：current 84/84。
- multi-source archive replay：current 82/84；
  无精确 decision-version alternate-source timestamp，只作 post-hoc。
- prior bias：只使用 target_date 之前 14 天 settlement error，避免 outcome leakage。
  current 有 prior error 的票为 82/84。
- morning innovation：PIT observation + assigned curve OOF，但 overlap 只有 6 dates。
- morning fresh exact-ticket quote：缺失；因此不发布 intraday fee-adjusted ROI。
- actual fill：0，zero-notional/research replay。

## A. Entry-time ablation

训练：historical ≤2026-06-20；测试：historical 2026-06-21+ 与 current frozen shadow。
固定 rows/labels/ask；L2 logistic 不扫阈值。

| eval_window | model | rows | dates | wins | mean_p | brier | logloss |
| --- | --- | --- | --- | --- | --- | --- | --- |
| historical_recent | market_raw | 58 | 9 | 10 | 0.09900 | 0.13902 | 0.44602 |
| current_shadow | market_raw | 84 | 8 | 12 | 0.11598 | 0.11782 | 0.38660 |
| historical_recent | entry_base | 58 | 9 | 10 | 0.13323 | 0.13906 | 0.45218 |
| current_shadow | entry_base | 84 | 8 | 12 | 0.16201 | 0.11513 | 0.37915 |
| historical_recent | entry_plus_multisource | 58 | 9 | 10 | 0.13059 | 0.14811 | 0.48454 |
| current_shadow | entry_plus_multisource | 84 | 8 | 12 | 0.15608 | 0.11974 | 0.38936 |
| historical_recent | entry_plus_prior_bias | 58 | 9 | 10 | 0.15138 | 0.13459 | 0.43368 |
| current_shadow | entry_plus_prior_bias | 84 | 8 | 12 | 0.17819 | 0.11443 | 0.37905 |
| historical_recent | entry_plus_multisource_and_bias | 58 | 9 | 10 | 0.15652 | 0.14881 | 0.47868 |
| current_shadow | entry_plus_multisource_and_bias | 84 | 8 | 12 | 0.17869 | 0.12272 | 0.39607 |

candidate 相对同 rows `entry_base` 的 paired target-date block delta；负数才是改善：

| eval_window | model | brier_delta_vs_entry_base | brier_delta_ci_low | brier_delta_ci_high | logloss_delta_vs_entry_base | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| historical_recent | entry_plus_multisource | 0.00905 | -0.00212 | 0.01820 | 0.03236 | -0.01975 | 0.07855 |
| historical_recent | entry_plus_prior_bias | -0.00447 | -0.01121 | 0.00029 | -0.01851 | -0.04776 | -0.00015 |
| historical_recent | entry_plus_multisource_and_bias | 0.00975 | -0.00872 | 0.02258 | 0.02650 | -0.04934 | 0.08404 |
| current_shadow | entry_plus_multisource | 0.00461 | -0.00321 | 0.01451 | 0.01021 | -0.01936 | 0.05002 |
| current_shadow | entry_plus_prior_bias | -0.00070 | -0.00541 | 0.00454 | -0.00009 | -0.01338 | 0.01558 |
| current_shadow | entry_plus_multisource_and_bias | 0.00760 | -0.00329 | 0.02044 | 0.01693 | -0.02096 | 0.06331 |

解读：

1. multi-source 的价值目前只是 uncertainty / reach-hot-tail state，**没有转化为 exact-ticket alpha**：
   current Brier delta `+0.00461`，joint model `+0.00760`，方向都是变差。
2. prior bias 能修正每城/每源长期偏热偏冷：historical recent Brier delta `-0.00447`
   （CI 略跨 0），logloss delta `-0.01851`（CI 刚好不跨 0）；current 只有
   Brier `-0.00070`，CI 跨 0。exact bracket 仍受 overshoot 与分布宽度控制。
3. 若 joint model 不能在 historical recent 和 current 同时保持负 delta，就只能继续 telemetry，
   不能按 score 过滤彩票。

current 按 assigned source 拆开（只是诊断，不是独立显著性检验）：

| model | source | rows | wins | brier_delta_vs_entry_base | logloss_delta_vs_entry_base |
| --- | --- | --- | --- | --- | --- |
| entry_plus_multisource | ecmwf | 54 | 11 | 0.00756 | 0.02144 |
| entry_plus_multisource | gfs | 30 | 1 | -0.00068 | -0.00998 |
| entry_plus_prior_bias | ecmwf | 54 | 11 | -0.00266 | -0.00630 |
| entry_plus_prior_bias | gfs | 30 | 1 | 0.00282 | 0.01109 |
| entry_plus_multisource_and_bias | ecmwf | 54 | 11 | 0.00735 | 0.01785 |
| entry_plus_multisource_and_bias | gfs | 30 | 1 | 0.00804 | 0.01528 |

prior bias 的 current 改善来自 ECMWF；GFS 反而变差，因此它不能解释为“加入 bias 后可恢复 GFS”。

## B. Target-day warming innovation

概率构造固定为 `Normal(predicted Tmax, σ=3.0F)` 在所买 exact bracket settlement interval
上的质量；只比较 rolling bias 与 expanding-OOF innovation，同 rows、同 label。

| checkpoint_hour_local | model | rows | dates | wins | mean_p | brier | logloss | tmax_mae_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | rolling_bias | 31 | 6 | 3 | 0.16744 | 0.09639 | 0.35209 | 1.69157 |
| 9 | innovation | 31 | 6 | 3 | 0.16817 | 0.08930 | 0.31691 | 1.80320 |
| 9 | innovation_regime | 31 | 6 | 3 | 0.16449 | 0.08852 | 0.31359 | 1.91170 |
| 12 | rolling_bias | 32 | 6 | 3 | 0.17606 | 0.09719 | 0.35675 | 1.75657 |
| 12 | innovation | 32 | 6 | 3 | 0.16776 | 0.08350 | 0.30221 | 1.27247 |
| 12 | innovation_regime | 32 | 6 | 3 | 0.16625 | 0.08783 | 0.31562 | 1.35525 |

paired target-date block delta：

| checkpoint_hour_local | model | rows | dates | wins | brier_delta_vs_rolling_bias | brier_delta_ci_low | brier_delta_ci_high | logloss_delta_vs_rolling_bias | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | innovation | 31 | 6 | 3 | -0.00709 | -0.02528 | 0.00362 | -0.03518 | -0.11675 | 0.01103 |
| 9 | innovation_regime | 31 | 6 | 3 | -0.00787 | -0.02661 | 0.00326 | -0.03851 | -0.12097 | 0.00974 |
| 12 | innovation | 32 | 6 | 3 | -0.01369 | -0.03101 | -0.00327 | -0.05454 | -0.12887 | -0.00933 |
| 12 | innovation_regime | 32 | 6 | 3 | -0.00935 | -0.01971 | -0.00256 | -0.04113 | -0.09127 | -0.01084 |

12:00 plain innovation 的改善不是完全普遍：

- 全部 rows mean Brier delta：
  -0.01369。
- 去掉贡献最大的 Lucknow city-date 后：
  -0.00635。
- 只看 losers：
  -0.00520。
- checkpoint 在原 entry 后的中位时间：
  15.2 小时；最小
  9.5 小时。

12:00 的机制切片：

| dimension | value | rows | dates | wins | brier_delta_innovation | logloss_delta_innovation |
| --- | --- | --- | --- | --- | --- | --- |
| source | ecmwf | 20 | 6 | 3 | -0.01909 | -0.07814 |
| source | gfs | 12 | 5 | 0 | -0.00468 | -0.01520 |
| consensus_bucket | mixed_hot_29_71 | 12 | 5 | 3 | -0.03204 | -0.12770 |
| consensus_bucket | strong_hot_75_100 | 18 | 6 | 0 | -0.00208 | -0.00764 |
| consensus_bucket | weak_hot_0_25 | 2 | 2 | 0 | -0.00807 | -0.03763 |
| weather_regime | clear_low_cloud | 9 | 5 | 1 | -0.00003 | -0.00313 |
| weather_regime | cloud_suppressed | 11 | 5 | 1 | -0.01087 | -0.03328 |
| weather_regime | mixed_cloud | 5 | 4 | 0 | -0.00738 | -0.02525 |
| weather_regime | rain_convective | 7 | 5 | 1 | -0.04019 | -0.17495 |

改善主要出现在 mixed-hot 共识（3 个 winners 全在该组）与 rain/convective，但都是同一小样本的
post-hoc 解释；strong-hot 组只有微弱改善。GFS 的 12 张虽有小幅 Brier 改善，但 0 win，
只能说明概率被往低处校准得更合理，不能证明 GFS 彩票变得可买。

分布宽度敏感性（负数为改善）：

| checkpoint_hour_local | sigma_f | rows | brier_delta_vs_rolling_bias | logloss_delta_vs_rolling_bias |
| --- | --- | --- | --- | --- |
| 9.00000 | 2.00000 | 31.00000 | -0.01049 | -0.06927 |
| 12.00000 | 2.00000 | 32.00000 | -0.02993 | -0.12152 |
| 9.00000 | 2.50000 | 31.00000 | -0.00899 | -0.04768 |
| 12.00000 | 2.50000 | 32.00000 | -0.02012 | -0.07894 |
| 9.00000 | 3.00000 | 31.00000 | -0.00709 | -0.03518 |
| 12.00000 | 3.00000 | 32.00000 | -0.01369 | -0.05454 |
| 9.00000 | 3.50000 | 31.00000 | -0.00549 | -0.02722 |
| 12.00000 | 3.50000 | 32.00000 | -0.00956 | -0.03957 |
| 9.00000 | 4.00000 | 31.00000 | -0.00427 | -0.02173 |
| 12.00000 | 4.00000 | 32.00000 | -0.00687 | -0.02984 |
| 9.00000 | 5.00000 | 31.00000 | -0.00267 | -0.01471 |
| 12.00000 | 5.00000 | 32.00000 | -0.00382 | -0.01851 |

从 σ=2F 到 5F，point estimate 方向一致；因此 12:00 改善不是 σ=3F 的单点产物。

这说明 innovation 有物理信息，但主要用途是**重新分配 exact-bracket 概率**：

- 正 innovation 可能提高 reach，同时增加 hotter-bracket overshoot；
- 负 innovation 可能降低 reach，但也可能把过热预测拉回当前 ticket；
- 因此不能用 `innovation > 0` 做 BUY-YES hard gate，应把整条 Tmax distribution 平移/缩放后重算每档概率。

## 最终答案

1. **混合预测源：有解释价值，但这轮没有可量化的 entry score 增益。**
   post-hoc coverage 和 forward baseline 都未过门，不能替换 assigned source 或筛 GFS。
2. **prior forecast bias：值得作为 D-1 概率头的 shadow candidate。** 它事前可用、机制清楚；
   但 current 增益尚不显著，只应连续校准中心，不做 city/source blacklist。
3. **实际升温路径 innovation：对尾部彩票更可能有用在 target-day 管理，不是初始进场。**
   12:00 exact-ticket proper score 有初步正增量，但样本和 book coverage 不足。
4. **最合理的下一版是两阶段概率策略**，不是再加一条 filter：

```text
entry distribution = multi-source consensus + prior city/source bias
intraday posterior = entry distribution + beta(clock, regime) * forecast innovation
trade decision = posterior exact-bracket probability - fresh executable cost
```

三门：

```text
entry significance/baseline = FAIL
intraday feature significance = PARTIAL PASS (12:00 only, 6 dates)
same-time market/execution = FAIL/NA
fresh frozen forward = NA
conclusion = intraday shadow_candidate; no live change
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_multisource_innovation_ablation_v1.py`
- structured：`generated/heada_multisource_innovation_ablation_v1/summary.json`
- entry：`entry_probability_scorecard.csv`, `entry_paired_deltas.csv`
- intraday：`late_probability_scorecard.csv`, `late_paired_deltas.csv`, `late_details.csv`
