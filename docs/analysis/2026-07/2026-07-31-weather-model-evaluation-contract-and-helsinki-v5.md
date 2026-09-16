# 通用概率评测 grain/loss 与 Helsinki remaining-heat v4/v5

> **Superseded numeric result (2026-07-31):** 本报告的 v5 数字来自 forecast
> 依赖血缘修复前的混版本 OOF。完整 v2→v7 同 SHA 重放与新结论见
> [Helsinki v7 forecast 血缘与缺失专家](2026-07-31-helsinki-v7-forecast-lineage-missing-expert.md)；
> 本文保留为方法和失败血缘，不再作为当前模型排名。

Status: `training-objective PASS / primary replacement FAIL / keep v2 / no-live-change`
Date: 2026-07-31

## 结论

这轮不是再加一个入场阈值，而是统一修了两个结构问题：

1. 将互相独立的 `30/60/120m/EOD` 概率改成 conditional hazard chain，保证时间概率单调且来自同一个 event-time 分布；
2. 训练分母由“每个 10 分钟 checkpoint 都同权”改成固定的
   `1/3 checkpoint + 1/3 state transition + 1/3 first date-X entry`，每一部分内部再按 `target_date` 等权。

第二项有效：v5 相对 v4 的三 grain 综合 integrated horizon Brier delta
`-0.001358`，95% CI `[-0.002365,-0.000380]`；logloss delta
`-0.002944`，95% CI `[-0.005560,-0.000271]`。

但 v5 **不能替换 v2**。严格的 12 个 replacement gates 只通过 6 个；EOD
binary 有稳定改善，但四档 `Δmax={0,1,2,3+}` 的 ordinal logloss/RPS 仍不稳定，
首次进入新 X 时的 binary logloss CI 也跨 0。v2 继续作为 frozen primary，v5
只保留 challenger。

## Production identity

先执行：

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
```

canonical DB route 为 `healthy`：仓库兼容入口与
`/Volumes/jrs/pm_agents/runtime/weather.db` 是同一 device/inode。manifest 总状态仍有
既存 critical：`com.pm-agents.weather-canonical-refresh` 上次退出码为 1；另有未登记
tmux session warning。本轮只读历史研究文件，不读写 canonical DB、不部署、不改变
live，因此没有绕过该生产告警。

## 通用实现

新增 `weather_model_evaluation`，供其他城市复用：

- checkpoint、state-transition、state-entry grain；
- date-equal Brier、logloss、AUC、10-bin calibration ECE；
- ordered outcome 的 multiclass logloss、Brier、RPS；
- conditional hazards 到 coherent event-time distribution；
- 单 grain 和固定多 grain 的 paired target-date block bootstrap；
- 固定多 grain 训练权重。

模块不读盘口、不选交易、不写 runtime。城市仍可用各自的 adapter、特征与算法。

## 数据与固定分母

训练只用 2023-07-30 至 2024-12-31；评估为 2025 expanding OOF：

| signal funnel 层 | rows | target dates | 定义 |
|---|---:|---:|---|
| checkpoint | 51,451 | 365 | 每份去重 FMI 10 分钟 observation-clock |
| transition | 15,965 | 365 | current X 或 path state 发生变化 |
| first date-X entry | 1,994 | 365 | 每日首次进入每个 confirmed current X |

没有按价格、edge、winner、时段或事后错误筛行。forecast feature 在 checkpoint
中覆盖 48,483/51,451 rows、360/365 个完整覆盖日；2,968 rows 位于缺 forecast
run 的日期/时点，仍保留在分母内。

evidence funnel 与天气训练分开：

| evidence 层 | 覆盖 |
|---|---:|
| 三年 FMI / EFHK METAR | 1,096 / 1,096 days |
| WU 可用日 | 1,092 days；另 4 days 为接口 coverage gap |
| 既有 Phase 2 settled full-ladder replay | 15 dates / 534 PIT checkpoints |
| 预注册 first-positive expressions | 35 |
| actual fills | 0 |

本轮没有拿稀疏盘口反过来调天气模型；既有市场结论不变：同 rows market proper
score 仍优于 v2，Phase 2 尚未达到 30 settled dates / 50 executable expressions。

## v2、v4、v5 同分母结果

### 三种决策场景

| grain | model | EOD accuracy | Brier | logloss | AUC | ECE10 | 4-class exact |
|---|---|---:|---:|---:|---:|---:|---:|
| checkpoint | v2 | 93.08% | 0.05137 | 0.17164 | 0.98255 | 0.00858 | 84.14% |
| checkpoint | v5 | **93.42%** | **0.04903** | **0.16607** | **0.98465** | 0.01899 | 84.19% |
| transition | v2 | 91.40% | 0.06325 | 0.20896 | 0.97390 | 0.01283 | 81.21% |
| transition | v5 | **91.83%** | **0.06084** | **0.20246** | **0.97654** | 0.02104 | 81.35% |
| first date-X | v2 | 86.93% | 0.09535 | 0.30463 | 0.94037 | **0.04783** | **73.19%** |
| first date-X | v5 | **88.63%** | **0.08871** | **0.29384** | **0.94327** | 0.04871 | 72.59% |

v5 提高了 binary discrimination，但 checkpoint/transition 的 ECE 变差，first
date-X 的四档 exact accuracy 也没有胜过 v2。这就是“不因 headline accuracy
上升就宣布替换”的主要原因。

### 固定条件切片

以下都是描述性切片，不参与 eligibility：

| 场景 | rows / dates | v2→v5 EOD accuracy | v2→v5 Brier | v2→v5 logloss | v2→v5 4-class exact |
|---|---:|---:|---:|---:|---:|
| forecast available | 48,483 / 360 | 93.20→93.55% | 0.0502→0.0475 | 0.1668→0.1600 | 84.74→84.89% |
| forecast missing | 2,968 / 47 | 90.35→90.41% | **0.0678→0.0701** | **0.2342→0.2411** | **78.63→77.44%** |
| forecast peak 前 0–60m | 2,328 / 323 | 78.89→80.80% | 0.1490→0.1387 | 0.4591→0.4324 | 70.62→71.79% |
| forecast peak 后 0–60m | 2,089 / 332 | 87.22→87.18% | 0.0989→0.0947 | 0.3251→0.3128 | **84.72→83.79%** |
| 10–14 local | 8,757 / 365 | 88.56→89.08% | 0.0842→0.0771 | 0.2769→0.2527 | 69.43→69.41% |
| 18–24 local | 13,140 / 365 | 98.31→98.40% | **0.0120→0.0128** | **0.0469→0.0540** | 97.49→97.47% |
| fresh runway | 9,383 / 339 | 88.91→90.41% | 0.0809→0.0731 | 0.2589→0.2356 | 71.93→73.07% |
| plateau | 3,790 / 327 | 86.25→86.78% | 0.0973→0.0940 | 0.3119→0.2965 | **73.05→72.19%** |
| winter | 12,679 / 90 | 89.68→90.01% | 0.0809→0.0787 | 0.2706→0.2649 | **79.99→79.14%** |

明显结构性弱点已经从“泛泛地说晚间/平台期不准”收敛为：

- forecast 缺失时 v5 全面退化；
- peak 前后 60 分钟仍是最难的 transition window；
- v5 对 EOD break 的改善大于对最终 `Δmax` 档位的改善；
- 18–24 点 accuracy 很高是 base rate 容易，不代表概率质量更好，v5 在该段
  Brier/logloss 反而恶化；
- calibration 是下一版的主要修复对象，不能靠再加入场阈值掩盖。

## Replacement gates

相对 frozen v2，checkpoint、transition、first date-X 三个 grain 各检查：
binary Brier/logloss 与 ordinal logloss/RPS，共 12 项。

- PASS 6/12：三个 grain 的 binary Brier；checkpoint/transition binary logloss；
  first date-X ordinal RPS。
- FAIL 6/12：三个 grain 的 ordinal logloss、checkpoint/transition ordinal RPS、
  first date-X binary logloss。

因此：

```text
training_grain_change=PASS
primary_replacement=FAIL
weather_primary=v2
v5_status=research_challenger
```

## 下一阶段资格

- 继续模型升级：**具备资格**。下一版应在相同 development OOF 内做 coherent
  joint ordinal calibration，并单独处理 forecast-missing representation；目标是修复
  calibration/严重度，不改变 signal denominator。
- 读取 2026 final audit：**暂不做**。本轮 artifact train end 仍为 2025-12-31，
  `final_audit_2026_read=false`。
- artifact 后 2026-07-31+ frozen forward：**保持 untouched**，不能用于调参。
- 进入盘口策略/live：**不具备资格**。市场层样本和 executable/fill 证据不足，
  且 v5 尚未通过主模型替换门。

## 产物

- `weather_model_evaluation/probability.py`
- `scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v4.py`
- `scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v5.py`
- `docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v4/`
- `docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v5/`

v5 artifact SHA256:
`eac6dd2998bedbc6a3e227c5fad43f0578601adb0fc4d08e53953130d124ac39`。
