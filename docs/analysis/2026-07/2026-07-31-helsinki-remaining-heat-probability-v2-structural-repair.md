# Helsinki Remaining-Heat Probability v2 — 结构修复与 A4 历史回补

## 人话结论

这次不是给坏天气日加规则，而是同时修了两件根因：

1. 把旧的“是否升档”和“四档分布”拆成一个一致的 hurdle 结构：先预测今天还会不会升档，再预测若升档会是
   `+1/+2/+3+`，最终四档概率严格加总为 1；
2. 实际回补 ECMWF 历史单次 run，把 forecast 剩余峰值、峰值时钟、剩余热量、云/风/辐射和
   `forecast−FMI` 偏差加进模型。

固定 2025 全部 `51,451` 个 10 分钟 checkpoint、365 天，不删除晚上或困难时段。v2 判断“今天还会不会升档”的
准确率从 v1 的 `88.50%` 提高到 `93.08%`；Brier 从 `0.07996` 降到 `0.05137`，logloss 从 `0.25897`
降到 `0.17164`。target-date block bootstrap 的改善区间完整低于 0，不是少数日期撑起来。

但结果也很明确：

- 单独换 hurdle 结构、不补 forecast，并没有显著改善 binary head；主要增益来自 A4 forecast。
- METAR 条件式站点偏差对 full-distribution logloss 有极小改善，但使 primary binary Brier/logloss 变差，
  所以不进入选中的 v2。
- `Δmax=+1` 仍是结构性弱点，v2 在这一类的 Brier/logloss 反而略差。
- 这仍只是天气概率模型；本轮 market evidence 为 0，不能据此做 Polymarket 赔率或改 live。

## 数据是否补得到

可以补，但不是三年全齐。

| 数据层 | 实际结果 |
|---|---:|
| 本地 immutable forecast archive | `2026-07-04` 才开始，不能覆盖 2025 OOF |
| Open-Meteo exact ECMWF IFS run 可回补范围 | `2024-03-14..2025-12-31` |
| 请求 run | `2,632` |
| 成功取得 run | `2,222`（`84.42%`） |
| archive 明确不存在 | `410` |
| 下载限流/网络造成的 transient missing | `0`（低并发重试后清零） |
| materialized hourly rows | `106,656` |
| 2025 OOF checkpoint forecast coverage | `48,483 / 51,451 = 94.23%` |
| 2024 training checkpoint forecast coverage | `77.98%` |
| 2023 exact individual run | `0`；当前无 ECMWF MARS 凭证 |

2025 有 360/365 天至少部分有 exact run；完全无 forecast 的 5 天是
`2025-06-20`、`2025-06-23`、`2025-06-25`、`2025-08-05`、`2025-12-15`。
缺 run 没有从模型分母删除，仍由 HGB 原生 missing 路径评分。

PIT 口径是 run initialization 加固定 6 小时 availability lag。官方说明 `run` 是初始化时间，不是公开发布时间，
全球模型通常需要额外 4–6 小时；ECMWF IFS 单次 run archive 从 2024-03-14 开始。这里取 6 小时是保守重建，
不是伪造 collector first-seen。[Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api)

2023 不能用 stitched Historical Forecast API 冒充单次 run，因为该接口只拼接各 run 的前几小时，不保留完整
forecast horizon。[Open-Meteo Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
更早的 ECMWF operational archive 理论上可通过 MARS 获取，但当前机器没有相应访问凭证。
[ECMWF archive access](https://www.ecmwf.int/en/forecasts/access-forecasts/access-archive-datasets)

## 模型结构与同分母比较

所有结果都是 2025 expanding OOF、每个 target_date 总权重为 1；2026 final audit 没有被本脚本读取。

### `P(今天还会不会升档)`

| 模型 | Accuracy | Brier | Logloss | AUC |
|---|---:|---:|---:|---:|
| empirical prior | 82.25% | 0.11792 | 0.36855 | 0.91451 |
| compact logistic A5 | 87.48% | 0.08967 | 0.29168 | 0.94898 |
| shallow HGB A5 v1 | 88.50% | 0.07996 | 0.25897 | 0.95993 |
| hurdle HGB A5（只修结构） | 88.58% | 0.08053 | 0.26082 | 0.95938 |
| compact logistic A5+A4+METAR | 92.14% | 0.05746 | 0.18800 | 0.97963 |
| **hurdle HGB A5+A4 v2** | **93.08%** | **0.05137** | **0.17164** | **0.98255** |
| hurdle HGB A5+A4+METAR | 92.93% | 0.05161 | 0.17260 | 0.98230 |

v2 相对 v1：

| Metric | Delta | target-date bootstrap 95% CI |
|---|---:|---:|
| Brier | `-0.02859` | `[-0.03653, -0.02142]` |
| logloss | `-0.08733` | `[-0.11319, -0.06339]` |

校准 ECE 从 v1 的 `0.01731` 降到 v2 的 `0.00858`。

### `Δmax={0,1,2,3+}` 完整分布

| 模型 | Multiclass Brier | Multiclass logloss | 精确命中 | 误差≤1档 |
|---|---:|---:|---:|---:|
| empirical prior | 0.09604 | 0.75102 | 71.89% | 84.81% |
| multinomial logistic A5 | 0.07941 | 0.63023 | 77.05% | 90.07% |
| hurdle HGB A5 | 0.07756 | 0.61045 | 76.98% | 91.12% |
| **hurdle HGB A5+A4 v2** | **0.05418** | **0.40123** | **84.14%** | **96.81%** |
| hurdle HGB A5+A4+METAR | 0.05411 | 0.40062 | 83.89% | 96.78% |

结构修复本身相对 multinomial 的 proper-score CI 仍跨 0；加入 A4 后，
multiclass Brier delta `-0.02523`、95% CI `[-0.03027,-0.02063]`，
logloss delta `-0.22900`、95% CI `[-0.27954,-0.18285]`。

## ECMWF forecast 本身有多准

这组 exact run 的绝对 Tmax 并不算很准：

| Slice | Bias | MAE | ±1°C | 最终整数档 exact | 对“还会升档”的确定性判断 |
|---|---:|---:|---:|---:|---:|
| all | -0.45°C | 0.84°C | 69.65% | 39.55% | 91.42% |
| winter | -0.74°C | 0.88°C | 71.48% | 38.58% | 87.90% |
| spring | -0.11°C | 0.77°C | 72.45% | 40.71% | 93.02% |
| summer | -0.53°C | 0.87°C | 65.26% | 37.02% | 92.82% |
| autumn | -0.44°C | 0.84°C | 69.29% | 41.78% | 91.89% |

所以正确用法不是把 forecast Tmax 当 settlement latch，而是把它当“剩余热量/峰值时钟”的连续条件。
模型显著改善，正是因为它把 forecast 与已经发生的 FMI/METAR path 合起来，而不是照抄 forecast 的最终整数档。

## 不同情况下的正确率

| 场景 | v1 | v2 | 变化 |
|---|---:|---:|---:|
| 全部 checkpoint | 88.50% | 93.08% | +4.64pp |
| winter | 80.46% | 89.68% | +9.22pp |
| spring | 90.32% | 94.09% | +3.77pp |
| summer | 94.66% | 95.47% | +0.81pp |
| autumn | 88.40% | 93.02% | +4.61pp |
| 10–14 点 | 82.47% | 88.56% | +6.10pp |
| 14–18 点 | 85.30% | 89.13% | +3.82pp |
| 18–24 点（仍在总分母） | 97.79% | 98.31% | +0.52pp |
| plateau | 78.54% | 86.25% | +7.71pp |
| fresh runway | 84.69% | 88.91% | +4.22pp |
| 离下一 boundary 0.5–1.0°C | 79.66% | 88.92% | +9.26pp |
| forecast 峰值前 >60m | 85.95% | 92.03% | +6.08pp |
| forecast 峰值 ±60m | 75.72% | 82.17% | +6.45pp |
| forecast 峰值后 >60m | 95.54% | 97.63% | +2.08pp |

晚上 18–24 点没有被排除；它们仍是总分母的一部分，只是单列，防止 `98%+` 的低信息 easy rows 掩盖
中午和峰值附近的问题。

### 仍明显不准的地方

- `Δmax=+1`：v2 accuracy 79.72% vs v1 79.27%，但 Brier `0.14797` vs `0.14038`、
  logloss `0.46200` vs `0.43576`，概率质量反而变差。这是当前最清楚的剩余结构性误差。
- forecast peak ±60m：虽从 75.72% 提高到 82.17%，仍是最难时段。
- plateau：虽提高到 86.25%，仍明显低于整体 93.08%。
- forecast 缺失的 2,968 rows / 47 dates：v2 Brier `0.06782` 与 v1 `0.06816` 基本相同，
  v2 logloss `0.23422` 反而差于 v1 `0.21746`；A4 的收益不能外推到无 run 行。
- raw ECMWF 有系统性冷偏，尤其 winter `-0.74°C`；不能用固定温差直接修正 settlement。

## METAR 校准、归一化和非线性模型

- label、official running max 和最终验证继续使用 EFHK METAR，这是正确口径。
- 已加入 `season×3h clock×METAR age` 的 FMI−METAR mean/MAE/lattice-match prior，以及最新 METAR
  温度、pullback、变化、age、gap。
- 它让 full-distribution logloss从 `0.40123` 小幅变成 `0.40062`，但 primary binary Brier/logloss
  从 `0.05137/0.17164` 变差为 `0.05161/0.17260`，所以没有进入 selected v2。
- compact logistic 使用 median imputation、missing indicator 和 StandardScaler；HGB 使用原生 missing，
  不需要归一化。此前 RobustScaler、ExtraTrees、原生 missing HGB、直接加入 6 个 METAR state均未改善。
- shallow HGB 已是非线性模型；本轮 hurdle HGB 进一步解决完整概率分布的一致性，但不靠 A4 时没有显著
  binary 增益。不能把“换更复杂算法”本身当改进。

## 固定分母与双漏斗

Signal funnel：

| 层 | 单位 | 数量 |
|---|---|---:|
| 三年 FMI observation-clock | checkpoint | 157,781 |
| official state 可定义 | checkpoint | 154,493 |
| 2025 expanding OOF 固定分母 | checkpoint / target_date | 51,451 / 365 |

Evidence funnel：

| 层 | 单位 | 数量 |
|---|---|---:|
| 2025 exact-run 可重建 | checkpoint / target_date | 48,483 / 360 |
| forecast missing 但仍评分 | checkpoint / target_date | 2,968 / 47 |
| 本轮 canonical market rows | checkpoint | 0 |

## Freeze、资格与失败项

- research artifact：
  `docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v2/helsinki_remaining_heat_v2.joblib`
- SHA256：`78242ae77fd63731a0c536faf4fad8d83324ac71967290a582dc099f40aa55c1`
- artifact train end：`2025-12-31`
- 2026 final audit read：`false`
- untouched forward：Helsinki local `2026-07-31` 起，保持 untouched
- live change / real order：`none`

资格判断：

- **天气概率 v2：具备 frozen-forward 资格。**
- **market residual / Polymarket expression：不具备晋升资格。** 本轮没有同 rows market 分母；继续
  Phase 2 collector，等盘口积累够后再比较天气概率与盘口赔率。
- **不能改 live。** 当前结果只支持研究 artifact freeze，不支持 tiny-live、真实下单或 size 调整。

失败/未完成项：

1. 2023 exact ECMWF individual runs 未补到；没有 MARS 凭证。
2. 2024–2025 archive 有 410 个明确不存在的 cycle；已记录，不伪造。
3. historical public first-seen timestamp 不可恢复；固定 6h lag 是保守近似。
4. `Δmax=+1` 和 forecast-missing 行未被修好，必须进入 frozen-forward 重点监控。

## 执行证据

- Backfill：
  `scripts/analysis/reheat_risk/backfill_helsinki_ecmwf_single_runs_v1.py`
- v2 research：
  `scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v2.py`
- Tests：
  `tests/research_tests/test_helsinki_remaining_heat_probability_v2.py`
- Generated lineage：
  `docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v2/`
- 定向测试：`5 passed`
- production manifest：physical canonical DB route healthy、无 split；总状态 warning 仅来自未登记 tmux session，
  本轮没有写 canonical DB、没有改生产进程。
