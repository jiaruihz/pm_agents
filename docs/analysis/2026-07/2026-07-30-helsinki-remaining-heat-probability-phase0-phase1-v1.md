# Helsinki Remaining-Heat Probability Phase 0–1 v1

> 2026-07-31 lineage update：本报告当时关于 A4“历史 exact-run 不可恢复”的判断已被后续数据源发现部分
> supersede。Open-Meteo exact ECMWF IFS run 可回补到 2024-03-14；v2 已完成回补、统一 hurdle
> distribution 和 2025 expanding OOF，详见
> [v2 structural repair](2026-07-31-helsinki-remaining-heat-probability-v2-structural-repair.md)。
> v1 artifact 与 2026 audit 保持原样，未被重写。

## 数据快照

| 项目 | 值 |
|---|---|
| production identity | manifest `2026-07-30T14:24:08Z`；`runtime/weather.db` 与 `/Volumes/jrs/pm_agents/runtime/weather.db` 同 device/inode `16777239/1636907`，DB route healthy；总状态仅因未登记 tmux sessions 为 warning |
| FMI | 站号 `100968`；`2023-07-30..2026-07-29`，`157,781` 个 10 分钟 observation-clock rows / `1,096` 个本地日 |
| EFHK METAR | `52,592` 条 / `1,096` 日；本研究 settlement-facing label |
| WU / market settlement audit | WU 完整 `1,092` 日，METAR/WU `1,092/1,092` exact；真实市场 winner `79` 日，METAR/WU 均 `79/79` exact |
| unsettled / missing_bracket | 本轮真实 market settlement audit `0/79 unsettled`、`0 missing_bracket`；WU 另有 `4` 个接口 coverage-gap 日，不进入 WU exact 分母 |
| artifact freeze | `2026-07-30T14:43:48.836639Z`；model SHA256 `d7814494e033e30b849abc88777de1fec4d0f91ae8a1892e54d623b4a9b75dfd` |
| canonical PIT book diagnostic | candidate v2 `1,111` 个 BUY_NO ladder rows；严格 current-bracket + 最近 20m FMI observation-clock state + book available by decision 后仅 `11` rows / `1` 日（2026-07-28） |
| 同步 / rebuild | 未重复下载、未 rebuild：7/30 已同步产物完整覆盖计划终点 7/29；production DB identity healthy |

## 结论与动作

**Phase 0 PASS，Phase 1 weather head PASS；继续 Phase 2 的 zero-notional collector / canonical first-seen + full-ladder 对齐，不改 live、不做真实下单。**

- 2025 expanding OOF 与一次性 2026 final audit 上，compact logistic、shallow HGB 相对 `W0 season×clock prior` 的 Brier/logloss date-block CI 全部小于 0；multinomial `Δmax={0,1,2,3+}` 也在两段都通过。
- HGB 是 weather-only champion；compact logistic 保留为可解释 baseline；multinomial head提供 `q0/q1/q2/q3+`。
- 这只证明 FMI path/weather/radiation 对剩余升档概率有稳定预测力。canonical 同 rows market 只有 1 个日期，且该日 market Brier/logloss `0.01065/0.05210`，明显优于 logit `0.05847/0.16596` 和 HGB `0.05362/0.15843`。不能声称 market residual。
- A4 因历史 PIT forecast issue/run/first-seen/hash 不可恢复而不拟合；A5 只加入 observation-clock 可得的 cadence/basis/persistence/terminal-risk proxy，真实 first-seen lag/revision 留给 frozen forward。
- `significance=PASS(weather vs W0)`；`baseline=FAIL(market coverage only 1 date and point estimate loses)`；`forward=PASS(2026 weather-head sign)`；`conclusion=inconclusive / weather_predictive_not_residual_yet`。

下一阶段资格：**可继续 Phase 2 research，不能进入 expression promotion、tiny-live 或 size 调整。** 首个真正 untouched 完整 Helsinki target date 是 artifact freeze 后的 `2026-07-31`。

## Target 与 exact-bracket 语义

```text
在每个 FMI checkpoint t，估计
q0/q1/q2/q3+ = P(EFHK final METAR maximum - as-of official running max
                 = 0/1/2/3+ | PIT weather/path state at t)
并检验 P_break_eod = 1-q0 相对同一时点 market probability 的 residual。
```

- `X YES` 只在 EFHK/WU 最终最高温正好为 `X` 时赢。
- 已打印 `X` 不锁定 `X YES`；升到 `X+1` 时 `X NO` 赢、`X YES` 输。
- 历史 decision timestamp 是 FMI `observation_time_utc`，不是伪造的 first-seen。
- official as-of 主口径严格要求 `METAR report_time <= checkpoint - 10m`；5/10/15m 只作预注册 sensitivity。
- 每个 target date 的训练与评分总权重固定为 1；所有 CI 使用 4,000 次 target_date block bootstrap。

## Phase 0 — label / clock parity

| 检查 | 结果 |
|---|---:|
| FMI checkpoint rows / dates | `157,781 / 1,096` |
| 10m lag 下 official bracket 可定义 | `154,493 / 1,096` |
| EFHK final label coverage | `1,096/1,096` |
| feature/report cutoff violation | `0` |
| METAR/WU exact | `1,092/1,092` |
| METAR/market winner exact | `79/79` |
| DST 23h/25h 日 parity | `6/6` |

DST 行数完全符合 Helsinki 本地日：

- spring forward：2024-03-31、2025-03-30、2026-03-29 均 `138` rows；
- fall back：2023-10-29、2024-10-27、2025-10-26 均 `150` rows。

Report-lag sensitivity：

| Lag | scoreable rows | 与 10m current bracket 一致率 | date-weighted `P_break_eod` base rate |
|---:|---:|---:|---:|
| 5m | 154,493 | 100.00% | 50.10% |
| 10m | 154,493 | 100.00% | 50.10% |
| 15m | 153,397 | 96.79% | 50.45% |

5m 与 10m 在两侧都可定义 official state 的共同 rows 上完全一致；15m 有 1,096 个额外不可定义 rows，
其余共同 rows 的 current bracket 一致率为 96.79%。敏感性不用于择优。

## Phase 1 — A0–A5 ablation

保留规则固定为：某组只有在 2025 expanding OOF 的 compact-logit Brier 与 logloss 都优于上一个 retained frame 才保留。

| Stage | 状态 | feature 数 | OOF Brier | OOF logloss | retained |
|---|---|---:|---:|---:|---|
| A0 boundary + clock | available | 11 | 0.10464 | 0.33973 | yes |
| A1 path | available | 21 | 0.09468 | 0.31273 | yes |
| A2 radiation | available | 36 | 0.09352 | 0.30725 | yes |
| A3 air mass / suppression | available | 53 | 0.09053 | 0.29400 | yes |
| A4 forecast remaining heat | unavailable: no historical PIT forecast lineage | 53 | 0.09053 | 0.29400 | no |
| A5 source reliability | partial: no historical first-seen lag/revision | 59 | 0.08967 | 0.29168 | yes |

没有增加事后价格、winner、future persistence 或 ROI 阈值；`fresh_runway/plateau/pullback/fade` 只用于解释切片。

## Proper score、校准与 date-block CI

### 2025 expanding OOF：51,451 rows / 365 dates

| Model | Brier | logloss | AUC | ECE |
|---|---:|---:|---:|---:|
| W0 season×clock prior | 0.11792 | 0.36855 | 0.89471 | 0.03778 |
| W1 compact logistic | 0.08967 | 0.29168 | 0.94162 | 0.03453 |
| W2 shallow HGB | 0.07996 | 0.25897 | 0.95252 | 0.02791 |

| Challenger − W0 | Metric | Delta | target-date bootstrap 95% CI |
|---|---|---:|---|
| W1 | Brier | -0.02826 | [-0.03786, -0.01861] |
| W1 | logloss | -0.07688 | [-0.10711, -0.04645] |
| W2 | Brier | -0.03796 | [-0.04564, -0.02995] |
| W2 | logloss | -0.10958 | [-0.13433, -0.08490] |
| W3 multinomial | multiclass Brier | -0.01664 | [-0.02038, -0.01288] |
| W3 multinomial | multiclass logloss | -0.12079 | [-0.15706, -0.08566] |

### 2026 final audit：29,604 rows / 210 dates

| Model | Brier | logloss | AUC | ECE |
|---|---:|---:|---:|---:|
| W0 season×clock prior | 0.10212 | 0.35187 | 0.92763 | 0.03490 |
| W1 compact logistic | 0.08367 | 0.26603 | 0.95551 | 0.01348 |
| W2 shallow HGB | 0.07187 | 0.23710 | 0.96551 | 0.02130 |

| Challenger − W0 | Metric | Delta | target-date bootstrap 95% CI |
|---|---|---:|---|
| W1 | Brier | -0.01845 | [-0.02880, -0.00849] |
| W1 | logloss | -0.08584 | [-0.14179, -0.03907] |
| W2 | Brier | -0.03025 | [-0.03874, -0.02149] |
| W2 | logloss | -0.11477 | [-0.16230, -0.07461] |
| W3 multinomial | multiclass Brier | -0.01588 | [-0.02106, -0.01081] |
| W3 multinomial | multiclass logloss | -0.15981 | [-0.21632, -0.10866] |

W3 的 2026 multiclass Brier/logloss 为 `0.07516/0.58178`，W0 为 `0.09104/0.74159`。

校准未出现系统性极端过置信：2026 logit 0.9–1.0 bin `mean_p=0.9729 / observed=0.9748`，HGB 为 `0.9609/0.9705`；低概率 bin 有轻度高估，但 overall ECE 仍为 `0.0135/0.0213`。

### 辅助 horizon（2026，monotone 后）

| Horizon | Logistic Brier / logloss / AUC | HGB Brier / logloss / AUC |
|---|---|---|
| 30m | 0.04305 / 0.14119 / 0.97228 | 0.04016 / 0.13084 / 0.97627 |
| 60m | 0.05529 / 0.18026 / 0.96837 | 0.05127 / 0.16814 / 0.97268 |
| 120m | 0.07438 / 0.24009 / 0.95477 | 0.06601 / 0.21562 / 0.96494 |

输出强制 `P30 <= P60 <= P120 <= PEOD`；multinomial 输出归一化为 `q0+q1+q2+q3+=1`。

## 天气预测准确度与优化空间复评

2025 OOF headline 已按“每个 target_date 总权重严格相同”重新聚合；原表是先算四个季度再等权平均，
Brier/logloss 的修正幅度均小于 `0.00033`，不改变任何 A0–A5 retention、模型排序或 frozen artifact。
2026 只作既有 frozen audit 诊断，不参与下面 challenger 的选择。

### 人能直接理解的准确度

| 任务 | 2025 OOF | 2026 frozen audit |
|---|---:|---:|
| HGB 判断 EOD 是否还会升档（0.5 threshold） | 88.50% | 90.20% |
| Multinomial 精确命中 `Δmax=0/1/2/3+` | 77.05% | 77.40% |
| Multinomial 预测误差不超过一档 | 90.07% | 89.47% |
| capped class MAE | 0.367 档 | 0.374 档 |

这些是全部 10 分钟 checkpoint 的 date-equal-weighted 结果，不能解释成每个时刻都同样准确。2026 最难的
`10–14` 点 exact-class accuracy 只有 `52.86%`；`18–24` 点达到 `96.68%`，但后者很多已经接近确定，
交易信息量较低。

主要误差区：

- `plateau`：HGB Brier `0.13949`、ECE `0.08872`；
- winter：Brier `0.12081`、ECE `0.08298`；
- FMI 离下一 official boundary `0.5–1.0°C`：Brier `0.13059`；
- `14–18` 点 EOD break：Brier/logloss `0.11193/0.35205`。

### 特征还有没有用

HGB 的 2025 OOF 增量：

| 新增特征组 | Brier 相对改善 | logloss 相对改善 | 判断 |
|---|---:|---:|---|
| A1 path | 4.91% | 4.75% | 最大、确定应保留 |
| A2 radiation | 1.82% | 1.74% | 有用但边际较小 |
| A3 air mass / suppression | 1.64% | 1.77% | 有用，尤其对应冬季/云风误差 |
| A5 observation-clock reliability | 1.10% | 0.94% | 小幅有效 |
| A4 forecast remaining heat | 未评估 | 未评估 | 历史 PIT lineage 不足，只能 forward |

因此不是“特征已经到顶”。最值得补的是 A4：forecast peak clock、未来 1/2/3h heating integral、
forecast ceiling margin；它们正好针对当前最差的中午/plateau/临界 boundary。不能用事后 forecast 回填历史。

### 模型还有没有用

HGB 相对 compact logistic 的 date-block delta：

| 窗口 | Brier delta（95% CI） | logloss delta（95% CI） |
|---|---:|---:|
| 2025 OOF | -0.00970 `[-0.01502,-0.00418]` | -0.03270 `[-0.04979,-0.01557]` |
| 2026 diagnostic | -0.01180 `[-0.01828,-0.00526]` | -0.02893 `[-0.04567,-0.01125]` |

非线性模型的提升是真实且跨期的。反过来，当前 multinomial logistic 派生的 `P_break=1-q0`
显著弱于 HGB：2025 Brier delta `+0.00645 [0.00108,0.01159]`，2026 为
`+0.00873 [0.00238,0.01496]`。下一版最合理的模型 challenger 不是深度网络，而是
**三个 cumulative HGB heads**：分别预测 `P(Δ>=1)`、`P(Δ>=2)`、`P(Δ>=3)`，
再做 monotone projection，兼顾 HGB 的非线性优势和完整 `q0/q1/q2/q3+` 分布。

只用 2025 Q1–Q3 选择的 `80% HGB + 20% logistic` blend，在完全未参与选择的 2025 Q4
点估改善 Brier/logloss `-0.00145/-0.00360`，但 CI 都跨 0；保留 challenger，不替换 HGB。
普通 Platt calibration 在 Q4 反而恶化 `+0.00366/+0.01030`，不采用。

优化动作顺序：

1. 保留当前 HGB 作为 frozen baseline；
2. 新增 cumulative/ordinal HGB distribution challenger；
3. 从 collector-exact forward 加入 A4 forecast remaining-heat；
4. 对 winter/plateau 做连续 calibration diagnostic，不做 hard gate；
5. 等新的 frozen-forward dates 比较，不用已经打开的 2026 audit 继续挑模型。

## 晚间分母、预处理、非线性模型与 METAR 复核

这轮只用 2025 expanding OOF 选择新 challenger；2026 frozen audit 只用来解释分母和误差，没有参与调参。
本轮共比较 6 个新 binary candidates 和 2 个 distribution candidates。

### 晚上是否进入分母

是。主分母包含本地日全部 scoreable 10 分钟 FMI checkpoints，18–24 点也在内：

| 2026 slice | rows / dates | HGB Brier | threshold accuracy | 最终仍升档率 |
|---|---:|---:|---:|---:|
| 全日 00–24 | 29,604 / 210 | 0.07187 | 90.20% | 54.27% |
| 去掉 18–24 | 22,044 / 210 | 0.09032 | 87.62% | 71.75% |
| 06–18 | 15,120 / 210 | 0.09347 | 87.04% | 63.93% |
| 10–18 | 10,080 / 210 | 0.10504 | 85.13% | 52.68% |
| 18–19 | 1,260 / 210 | 0.03336 | 95.71% | 5.71% |

晚间容易样本确实把全日 headline accuracy 抬高约 2.6pp。处理方式不是从主分母删除，而是以后固定同时报
`all-day` 和 `10–18 core-heating-window`；训练仍保留晚间，因为它们也是完整概率轨迹和 `q0` 校准的一部分。

### 已进入和未进入的天气数据

当前 A5 有 59 个特征。三年 FMI 中可事前使用的温度路径、日照/长短波辐射、湿度/露点差、风速和 u/v、
气压/趋势、云量、能见度、降水、solar clock、source cadence/basis 基本都已进入；未来
`cross_next_lattice_within_*` labels 明确未作特征。

数据质量不是完全一致：

- `precipitation_1h_mm` 缺失 83.78%；
- `diffuse_fraction` 缺失 51.33%；
- 其余 57 个 A0–A5 特征中，除 `precipitation_10m_mm` 缺 0.55% 外都低于 0.3%。

真正没有进入的是：

1. A4 forecast peak clock、future heating integral、forecast ceiling margin；
2. historical first-seen latency/revision；
3. settlement-source METAR 的 current temp/trend/age/pullback 完整状态；
4. TAF/radar/云层转变等历史 PIT lineage。

### 归一化和缺失值处理

- Logistic 已经是 `median impute + StandardScaler`，不是拿原始量纲硬拟合。
- HGB/ExtraTrees 这类树模型不需要归一化。
- Logistic 换 `RobustScaler` 后 Brier `0.08967→0.08963`，基本没有变化。
- HGB 从 median impute 改为原生 NaN，Brier `0.07996→0.08018`，CI 跨 0，没有改善。
- 因此当前不需要做全局归一化重构。下一轮数据处理应针对两个高缺失字段做
  `drop / missing-indicator / native-missing` 固定 A/B，而不是继续换 scaler。

### 其他机器学习算法

| 2025 OOF model | Brier | logloss | 结论 |
|---|---:|---:|---|
| standardized logistic | 0.08967 | 0.29168 | 线性 baseline |
| current shallow HGB | 0.07996 | 0.25897 | 当前 baseline |
| ExtraTrees | 0.09742 | 0.33073 | 明显更差 |
| cumulative HGB `P(Δ>=1)` | 0.07933 | 0.25709 | 小幅显著优于 current HGB |

cumulative HGB 对 binary break 的 Brier delta `-0.00063`
CI `[-0.00133,-0.00013]`，logloss delta `-0.00188`
CI `[-0.00401,-0.00034]`。但直接由三个独立 tail heads 相减得到完整分布时，
multiclass Brier 改善 `-0.00256`，multiclass logloss 却恶化 `+0.05422`
CI `[+0.00308,+0.11477]`。原因是独立 heads 相减会制造过小的中间档概率。

结论：非线性算法需要继续，但下一步是做 **joint ordinal calibration / smooth monotone distribution**，
不是直接采用当前 naive cumulative 版本，也不是上 LSTM/Transformer。1096 个独立日期不支持深度序列模型的复杂度。

### FMI 与 EFHK METAR 偏差、是否应校准

最终 label 和 as-of official running max 本来就来自 EFHK METAR，因此模型已经在预测 METAR settlement，
并不是拿 FMI 自己当结算真相。

三种偏差口径：

| 口径 | rows / dates | FMI−METAR mean | MAE | 同 1°C lattice |
|---|---:|---:|---:|---:|
| 每日最终 Tmax | 1,096 / 1,096 | +0.130°C | 0.175°C | 82.48% |
| 最近同刻、相差≤20m | 157,755 / 1,096 | -0.048°C | 0.322°C | 83.09% |
| PIT 最新可用 METAR、age≤60m | 154,478 / 1,096 | -0.046°C | 0.427°C | 70.34% |

每日 Tmax 的 mismatch 主要是 FMI 偏热一档；exact rate 在 winter 为 92.25%，summer 只有 75.36%。
PIT latest-METAR 的 lattice exact 较低主要来自 METAR cadence/staleness：age 10–15m 时为 78.01%，
30–45m 时降到 63.23%。

把 6 个 raw PIT METAR state 特征
（latest temp、pullback、last-report delta、age、gap、FMI−METAR）直接加入 HGB 后反而变差：
Brier delta `+0.00067` CI `[+0.00014,+0.00124]`。加入 cumulative HGB 也没有改善。

因此结论不是“不用 METAR”，而是：

- METAR 继续作为 label、official running state 和 source-basis calibration truth；
- 不做一个固定 `-0.05°C/+0.13°C` 的全局修正，也不把 lagged METAR raw state硬塞进模型；
- 下一版测试按 prior dates 学出的 `season × hour × report-age` probabilistic station-basis，
  以及旧 atlas 的 continuous temperature/RH/dewpoint/cloud/wind/peak-clock components；
- `day_regime/intraday_state` 只作解释或 soft feature。HGB 已直接消费其底层连续变量，不能把同一规则标签再当新信息包装。

旧 METAR atlas 中可复用的部分已经大量存在：temperature path、running-high age、RH、dewpoint depression、
wind、cloud、pressure 和 plateau/pullback/fade；真正新增信息仍是 forecast day-space/peak clock、
METAR report age/basis 的条件分布，以及未来 TAF/radar/cloud transition。

## 缺失字段、checkpoint 分母与明显失败复核（2026-07-31）

### “缺失”不是同一种问题

1. `diffuse_fraction` 缺 51.33%，但主要是夜间分母为零导致比例无定义：89.08% 的缺失行
   `global_radiation_wm2<=0`。白天缺失仅 7.03%，10–18 点缺失 10.86%。模型同时已有
   global/diffuse radiation 原值，因此这不是丢掉一半有效日照信息。
2. `precipitation_1h_mm` 缺 83.78%，是 FMI archive 该字段覆盖稀疏；其中 92.35% 的缺失行
   `precipitation_10m_mm=0`。它不能被解释成“确定无雨”，但已有 10m precipitation、
   present weather、RH、cloud 等替代输入。
3. A4 forecast peak clock/heating integral/ceiling margin 是整段历史 PIT lineage 不存在，
   不是某些 cell 为 NaN。这类信息从物理机制上最可能帮助当前失败，但不能拿今天看到的 forecast
   倒填历史；只允许 collector-exact forward。

冻结 HGB 在 2026 audit 上做不重拟合 counterfactual：把 `precipitation_1h_mm` 所有值换成训练中位数，
Brier/logloss 完全不变；把 `diffuse_fraction` 换成中位数，Brier 仅恶化 `0.000016`、logloss
`0.000059`。这是 diagnostic-only，不用于模型选择，但说明两个高缺失字段都不是当前误差主因。

同架构的 2025 expanding-OOF drop-column 重拟合在当前 Mac 内存压力下未完成，进程中途终止，
没有把半成品或 2026 counterfactual 包装成正式 OOF ablation。

### 10 分钟分母到底是什么

历史主 grain 是每一份 FMI **observation-clock checkpoint**：

| 层 | rows / dates | 说明 |
|---|---:|---|
| raw FMI checkpoint | 157,781 / 1,096 | `(target_date, observation_time)` 无重复 |
| official state 可事前定义 | 154,493 / 1,096 | 每日最早 3 行因 10m lag 前尚无 METAR state，合计排除 3,288 |
| 2025 OOF + 2026 audit | 81,055 / 575 | 真正报告的 OOF/audit prediction rows |

99.992% 相邻 observation interval 正好 10 分钟，仅 12 个 interval 大于 10 分钟；每日本地日中位
144 rows，DST/少量 archive gap 下为 127–150。训练和评分时每个 target_date 总权重固定为 1。

历史 archive 没有真实 ingest/first-seen timestamp 和 revision，因此不能说“当时收到数据的精确瞬间”。
forward 的正确语义是：每出现一份去重后的新 FMI observation，即使温度没变，也更新 plateau/time/path
并重算一次；重复轮询到同一 payload 不新增分母。forecast revision 或新的 METAR report 若进入
canonical information event，则形成各自的新 checkpoint，不能伪装成 FMI 10m row。

### 明显预测不准的情况

有，而且错误会连续多个 checkpoint，不只是边缘概率：

| 2026 frozen diagnostic | 全日 | 10–18 点 |
|---|---:|---:|
| threshold accuracy | 90.20% | 85.13% |
| threshold wrong rows | 2,903 | 1,499 |
| 高置信错误 rows | 388 | 115 |
| 出现高置信错误的 target dates | 48 / 210 | 35 / 210 |

10–18 点高置信错误中，31 rows 是模型给 `P(rise)<=0.1` 但后来仍升档，84 rows 是
`P(rise)>=0.9` 但最终没升档。最明显的独立失败模式：

- winter：accuracy 82.58%、Brier 0.12081；容易漏掉午后暖平流/后续 reheat。
- plateau：accuracy 80.12%、Brier 0.14003，是最差 path state。
- 离下一 official boundary 0.5–1.0°C：accuracy 81.49%、Brier 0.13059。
- 14–18 点：accuracy 83.85%、Brier 0.11193；false-no-rise 比 10–14 点更多。

具体 frozen examples：

- 2026-01-01 14:00–17:50，模型平均 `P(rise)=0.179`，EFHK 最终从 -11 档升到 -8 档；
- 2026-01-12 13:30–17:50，平均 `P(rise)=0.158`，最终再升 2 档；
- 2026-06-13 10:00–13:00，平均 `P(rise)=0.903`，最终完全没升档；
- 2026-05-04 10:00–12:10，平均 `P(rise)=0.911`，最终完全没升档。

前两类说明只靠已发生路径/辐射会漏掉未来天气机制；后两类说明“太阳强、路径仍升”不等于
settlement station 一定跨下一 lattice。下一 challenger 应优先补 A4 forecast future heat
和按 `season×hour×METAR age` 学出的 station-basis probability，而不是给这四天各加一条 hard rule。

## 同 rows market baseline

canonical candidate v2 当前没有 FMI-trigger checkpoint。本轮没有把 METAR/forecast trigger 冒充 FMI first-seen，而是在每个 canonical decision 只取其之前 20 分钟内最近 FMI **observation-clock** state，并明确保留 provenance gap。

| Model | rows / dates | Brier | logloss | AUC |
|---|---:|---:|---:|---:|
| raw market | 11 / 1 | 0.01065 | 0.05210 | 1.000 |
| compact logistic | 11 / 1 | 0.05847 | 0.16596 | 1.000 |
| shallow HGB | 11 / 1 | 0.05362 | 0.15843 | 1.000 |

这 1 日只能说明当前可对齐样本中 market 更好；日期不足，既不能拒绝整个 weather head，也不能宣称 residual。Phase 2 必须先让 FMI collector-exact event 真正进入 canonical checkpoint，再扩充同 rows full ladder。

## Negative controls

- 10m stale prediction：W1 Brier/logloss `0.08367/0.26603 → 0.08822/0.28436`；W2 `0.07187/0.23710 → 0.07697/0.25345`。
- 同月 target-date block permutation：W1 `0.12568/0.46566`，W2 `0.12219/0.40362`，明显劣于真实标签。
- Helsinki `single-print source-above-official` diagnostic：`2,442` checkpoint rows / `192` dates；其中 terminal-false `40` rows / `26` dates，terminal-correct `2,402` rows / `191` dates。它只作连续风险特征/切片，不转成“两次确认” hard gate。
- Atlanta 2026-07-17 canonical negative control保持原结论：22 个正确 runner candidates 无一在 10m 内可执行，唯一 terminal false 反而成交 15 shares并亏损；本轮不重算 fill/PnL、不与 Helsinki pool。

## Signal funnel

| 层 | grain | rows / events | dates |
|---|---|---:|---:|
| raw universe | historical FMI observation-clock checkpoint | 157,781 | 1,096 |
| as-of official bracket 可定义 | checkpoint | 154,493 | 1,096 |
| full A0–A3 + partial A5 frame | checkpoint | 154,493 | 1,096 |
| expanding OOF + final-audit probability evidence | checkpoint | 81,055 | 575 |
| expression first-positive / router selected | `(date,X,arm)` | 0 | 0 |

历史 archive 不是 first-seen event，不能把 157,781 rows 改名为 semantic events。

## Evidence funnel

| 层 | grain | rows / events | dates | gap |
|---|---|---:|---:|---|
| historical weather | FMI observation-clock checkpoint | 157,781 | 1,096 | 无真实 first-seen |
| current collector-exact FMI | distinct `(date, observation_ts, payload)` | 779 | 10 | 其中已结算到 7/29 为 706 / 9 日 |
| canonical current-bracket PIT market probability | checkpoint | 11 | 1 | FMI trigger 尚未 canonical，采用最近 prior observation-clock state |
| 5/10-share full-ladder executable | expression | 0 | 0 | Phase 2 未执行 |
| EFHK final label | target date | 1,096 | 1,096 | WU 完整 1,092；4 日 coverage gap |
| true market settlement audit | target date | 79 | 79 | 79/79 exact |
| shadow / order / fill under frozen spec | opportunity/order/fill | 0 | 0 | Phase 4 未开始；本任务不下单 |

盘口、depth、first-seen canonical 缺失均作为 evidence coverage gap，没有包装成策略筛除。

## Expression / execution

| Arm | 本阶段状态 | 原因 |
|---|---|---|
| E1 current `X NO` | probability mapped；未 replay | canonical同 rows仅1日，无 5/10-share depth |
| E2 upper-YES strip | 未 replay | full-ladder cost parity 属 Phase 2 |
| E3 current `X YES` | `q0` 已输出；未 replay | 不能与 E1/E2 混 selected ROI 分母 |
| E4 `X+1 YES` | `q1` 已输出；未 replay | exact-one-step diagnostic only |

没有 fee-adjusted ROI、没有 maker 假设、没有 future-touch fill、没有 selected-trade PnL。

## Frozen forward 与下一步资格

- Feature/model/parameter selection 最晚只读到 2025-12-31。
- `pre_audit_freeze_spec.json` 先写；artifact 写盘后才生成 2026 final predictions。
- 第二次 `freeze-audit` 会直接失败；后续 `verify` 只读既有 predictions、补统计/coverage，不重拟合。
- Artifact freeze 发生在 Helsinki 7/30 本地日未结束时；真正 untouched forward 从 7/31 起。
- Phase 2 前置 blocker：
  1. FMI collector-exact information event 尚未进入 canonical checkpoint；
  2. current-bracket canonical同 rows仅 11 rows / 1 date；
  3. 5/10-share full-ladder depth、upper-strip cost parity、official fee replay均未做；
  4. A4 forecast PIT lineage历史不可恢复，只能 forward；
  5. 至少需要扩充独立 dates 后再判断 weather-vs-market。

动作：继续 collector、修 FMI first-seen→canonical checkpoint 路由、保持 zero-notional；**不改 live、不加事后阈值、不启动真实下单。**

## 8 环覆盖与门状态

| 环 | 状态 |
|---|---|
| 1 描述性切片 | PASS：hour/season/boundary/path state |
| 2 统计推断 | PASS：target-date block bootstrap |
| 3 信号判别 | PASS：AUC + stale/permutation controls |
| 4 概率分布 | PASS：binary + multinomial proper score/calibration |
| 5 执行微结构 | FAIL/coverage gap：仅 1 日 raw market probability，无 depth/replay |
| 6 容量 | FAIL/not started |
| 7 组合相关性 | N/A：单城市；日期聚类已处理 |
| 8 基准/反事实 | W0 PASS；market baseline FAIL/insufficient |

```text
significance=PASS(weather head vs W0)
baseline=FAIL(market residual not established)
forward=PASS(2026 confirmatory weather head); true frozen forward=NOT STARTED
conclusion=inconclusive / weather_predictive_not_residual_yet
```

## 血缘与产物

```text
FMI observation-clock raw + EFHK as-of METAR
→ checkpoint/label parity
→ A0–A3 + partial A5 feature frame
→ W0/W1/W2/W3 OOF + frozen artifact
→ future collector-exact weather_information_event
→ weather_state_checkpoint
→ fact_signal_candidates E1–E4
→ future plan/order/fill/settlement
```

- 可复跑脚本：`scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v1.py`
- 测试：`tests/research_tests/test_helsinki_remaining_heat_probability_v1.py`
- 机器产物：`docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v1/`
- 关键文件：`phase0_summary.json`、`ablation_stages.csv`、`oof_predictions_2025.csv.gz`、`pre_audit_freeze_spec.json`、`frozen_models.joblib`、`final_audit_predictions_2026.csv.gz`、`canonical_market_summary.json`、`negative_controls.csv`、`verification.json`。
