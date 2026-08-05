# Helsinki remaining-heat v7：forecast 血缘修复与缺失专家

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | FMI 10m 三年特征、EFHK METAR label/basis、Open-Meteo exact ECMWF single runs |
| 天气 OOF | 2025-01-01..2025-12-31；51,451 checkpoints / 365 target dates |
| 评测 grain | 51,451 checkpoint / 15,965 transition / 1,994 first date-X entry |
| forecast 回补 | 2,632 requested runs；2,241 available；391 archive unavailable；0 transient missing |
| forecast PIT 解释 | model run + 固定 6h availability lag；不是历史 collector first-seen |
| settlement/unsettled/missing_bracket | 本轮只评天气 label，均不适用；未读取 canonical market/fill |
| production identity | manifest exit 0；DB route healthy；未同步、未重建、未写 canonical DB |
| final audit / forward | 2026 final audit 未读；2026-07-31+ forward 未读 |

## 结论

v7 通过了预先固定的 weather-head replacement 标准：相对 frozen v2，
checkpoint、transition、first date-X 三个 grain 上的 EOD Brier/logloss 与
`Δmax={0,1,2,3+}` logloss/RPS 共 12 个 paired target-date bootstrap gate
全部 `CI high < 0`。

这只允许把 v7 冻结为下一次 audit/forward 的 weather challenger；**不允许改
live**。本轮没有读取盘口，旧 Phase 2 的 market baseline 与 15-date ROI
不足问题也没有消失。

## 先修的数据与血缘问题

回补脚本重新请求了全部 2,632 个 ECMWF run。相比旧物化，新增取得 19 个 run，
使 2025 forecast-available checkpoint 从 48,483 增到 48,660，补回 177 行；
缺失行从 2,968 降到 2,791，fully-missing target date 从 5 降到 4。剩余 391
个 run 是 API 明确的 archive unavailable，不作伪填。

同时发现旧 v5 流程存在依赖血缘污染：forecast 文件刷新后可以只重跑 v5，
但 v5 仍会继承旧 v3/v4 OOF 的 baseline/coverage 列。修复后：

- v2→v3→v4→v5 每层写入 forecast decompressed-content SHA；
- 下游 SHA 不一致直接失败；
- gzip header timestamp 不再改变 semantic SHA；
- v4 的 `forecast_available` 明确来自本轮 fresh feature frame，不再来自旧 v3
  baseline。

旧 v5 报告的 `6/12` replacement 结果因此 superseded。按同一 SHA 全链重放后，
v5 为 `11/12`，只剩 checkpoint RPS 的 CI 上界 `+0.000155`。

## v6 与 v7 做了什么

v6 尝试 prior-quarter expanding 的 joint simplex calibration，并显式加入
forecast-missing context。结果失败：相对 v2 为 `0/12`，相对 v5 为 `0/18`，
说明前季校准不能稳定迁移到后季；v6 保留为失败实验，不选用。

v7 不重写所有概率，只修明确的缺失机制：

- forecast available 的 48,660 行原样使用 v5；
- forecast missing 的 2,791 行路由到独立 HGB hazard/severity expert；
- expert 只用 FMI 物理路径、辐射/湿度/风/云、时间/季节和 PIT latest METAR
  state/basis，不使用缺失 forecast，也不删行；
- 没有新增价格带、edge 阈值或 eligibility gate。

## 同分母结果

| grain / model | EOD accuracy | Brier | logloss | AUC | ECE-10 | Δmax exact | RPS |
|---|---:|---:|---:|---:|---:|---:|---:|
| checkpoint v2 | 92.96% | 0.05174 | 0.17334 | 0.98216 | 0.00907 | 83.93% | 0.04521 |
| checkpoint v5 | 93.22% | 0.04937 | 0.16654 | 0.98454 | 0.01841 | 84.30% | 0.04410 |
| checkpoint v7 | **93.26%** | **0.04893** | **0.16476** | **0.98481** | 0.01765 | **84.31%** | **0.04352** |
| transition v2 | 91.13% | 0.06391 | 0.21129 | 0.97329 | 0.01387 | 80.83% | 0.05248 |
| transition v7 | **91.66%** | **0.06079** | **0.20095** | **0.97660** | 0.01783 | **81.35%** | **0.05040** |
| date-X v2 | 87.03% | 0.09620 | 0.30954 | 0.93935 | 0.05127 | 72.66% | 0.07999 |
| date-X v7 | **88.38%** | **0.08856** | **0.29271** | **0.94416** | **0.04116** | **73.44%** | **0.07532** |

v7 相对 v2 的 12/12 loss gates 全过。v7 相对 v5 的 18 个
binary/ordinal/horizon gates 只有 4 个显著改善，其余多数点估改善但 CI 跨 0；
因此“缺失专家优于 v5 的每个子任务”尚未确认。选择 v7 的依据是它在未放宽的
primary replacement contract 下补齐了 v5 唯一失败项。此次比较的新增候选
`K=2`（v6/v7），未作事后 threshold 搜索，也未作多重检验修正；必须由 untouched
audit/forward 复核。

## 不同情况的正确率

这些切片只解释误差，不作为交易过滤器。

| 情况 | rows | EOD accuracy | Brier | Δmax exact | RPS |
|---|---:|---:|---:|---:|---:|
| forecast available | 48,660 | 93.38% | 0.04773 | 85.03% | 0.04105 |
| forecast missing | 2,791 | 90.23% | 0.06508 | 77.36% | 0.06580 |
| fresh runway | 9,383 | 90.31% | 0.07326 | 73.64% | 0.07136 |
| plateau | 3,790 | 86.77% | 0.09223 | 72.06% | 0.07283 |
| pullback | 21,693 | 93.36% | 0.04898 | 86.03% | 0.03928 |
| fade | 16,585 | 93.86% | 0.04218 | 89.32% | 0.03050 |
| winter | 12,679 | 89.97% | 0.07718 | 79.40% | 0.05764 |
| spring | 12,964 | 94.24% | 0.04066 | 84.88% | 0.04202 |
| summer | 12,971 | 95.42% | 0.03106 | 86.75% | 0.03648 |
| autumn | 12,837 | 93.35% | 0.04741 | 86.12% | 0.03820 |

| local hour | rows | EOD accuracy | Brier | Δmax exact |
|---|---:|---:|---:|---:|
| 00–06 | 12,045 | 92.16% | 0.05310 | 81.93% |
| 06–10 | 8,760 | 94.24% | 0.04642 | 80.63% |
| 10–14 | 8,757 | 89.28% | 0.07635 | 69.97% |
| 14–18 | 8,749 | 90.03% | 0.07334 | 85.93% |
| 18–24 | 13,140 | 98.42% | 0.01237 | 97.46% |

明显弱点仍是 plateau、forecast peak 前后 60 分钟、winter、10–14 点和
forecast missing。18–24 点的 98.42% 主要是“当天基本结束”的容易分母，不能
拿来代表临场核心预测能力。

## 双漏斗与八环

```text
signal funnel:
51,451 raw 10m checkpoints
→ 15,965 path/current-X transitions
→ 1,994 first date-X entries

evidence funnel:
51,451 weather-labelled rows / 365 dates
→ 48,660 rows with exact-run reconstruction
→ market rows: 0（本轮刻意不加入）
→ executable expressions/fills: not evaluated
```

本轮覆盖八环中的信号判别、概率分布评估和 target-date 统计推断；未覆盖 market
baseline、执行微结构、容量、组合相关性和真实 fill。结论等级：
`weather-head selected / market alpha inconclusive / forward NA / no-live-change`。

## 产物与验证

- `scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v7.py`
- `weather_model_evaluation/lineage.py`
- `docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v7/`
- v7 artifact SHA256:
  `33820c1f96ec9b49d5ac4e1cb23166bd6b576a46d1512187544f0f3cbb029ffb`
- forecast semantic SHA256:
  `297e0b5d8ac55a457af29f7ebbf516392936e6f3c2c97e20f3869e4b333ddff8`
- targeted tests: 24 passed
