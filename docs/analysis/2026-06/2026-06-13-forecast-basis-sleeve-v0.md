# Forecast-Window Basis Sleeve 可行性研究 v0

Status: snapshot
Updated: 2026-06-13
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; analysis/observed_max_m3.md
前置: 2026-06-12-official-resolution-source-and-entry-timing-v0.md（其下一步：把 basis edge 前移到预报时段）

## 任务与结论一句话

检验"峰值前（早场 10-13h）的预报阶段能否捕捉站点 basis，从而开一个早场
sleeve 补充现有的峰值后观测 sleeve"。结论：**预报确实能区分同城两个机场
（71% 的城市-日整数档位不同，城市层面有系统偏移），早场市场价整体更贴官方站
（peak-match 56% vs 错误站 38%）。早场用官方站预报买 YES 的聚合 ROI +17.9%
（t=1.86），白名单对照 -1.9%（t=-0.47，干净）。但 edge 高度不均匀：London/Milan
强正（ROI +50%/+51%，t=2.5/3.1），KualaLumpur/Chicago 反号（-46%/-28%）。
verdict：早场 sleeve 在"市场跟随官方站"的城市（London/Milan/Paris/Panama）部分
成立，但不是干净的 6 城 sleeve；在 KL（市场跟错误站）反而有害。需逐市场核对
"市场实际锚哪个站"作为入场前置，不能整池开。**

## 数据与脚本（全部本机可复现）

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_forecast_basis_sleeve.py
```

- open-meteo historical-forecast daily `temperature_2m_max`，缓存
  `runtime/rule_source_research/forecast_cache/`（复跑不依赖网络）。
- 结算唯一真相：`runtime/weather_edge_v1/market_data/cache/pm_history/{City}_{date}.json`
  单 winner（`final_price==1.0` 且 winner_count==1）。
- 早场报价：`paper_snapshots/snapshot_*.json`，取 event_date 当地 10-13h 窗口内
  每个 bracket 的最后一条报价（yes/no best_ask、forecast_max_f）。
- 评估窗口 2026-05-19..06-09。

产物目录 `docs/analysis/2026-06/generated/m3_forecast_basis_sleeve_v0/`：
`step1_station_forecast_diff_{detail,summary}.csv`、
`step2_early_quotes_detail.csv`、`step2_market_alignment_{detail,summary}.csv`、
`step3_early_backtest_{summary,trades}.csv`、`manifest.json`。

站点经纬度来自 IEM ASOS 元数据（权威，非估计）：

| 城市 | 官方站 | 错误站 |
|---|---|---|
| Paris | LFPB 48.967,2.427 | LFPG 49.015,2.534 |
| London | EGLC 51.505,0.055 | EGLL 51.479,-0.461 |
| Milan | LIMC 45.63,8.723 | LIML 45.461,9.263 |
| Chicago | KORD 41.960,-87.932 | KMDW 41.786,-87.752 |
| KualaLumpur | WMKK 2.717,101.7 | WMSA 3.132,101.582 |
| PanamaCity | MPMG 8.983,-79.517 | MPTO 9.05,-79.367 |

## 步骤 1：预报能否区分两个站（核心不确定性的正面回答）

对每个 basis 城市，拉官方站 vs 错误站经纬度的 daily max 预报，看
`forecast(官方) − forecast(错误)` 分布（评估窗口 22 天/城）。

| 城市 | 单位 | mean diff (°C) | std (°C) | mean&#124;diff&#124; (°C) | frac 整数档位不同 |
|---|---|---:|---:|---:|---:|
| Chicago | F | +0.32 | 0.80 | 0.64 | 0.73 |
| KualaLumpur | C | **−1.28** | 0.89 | 1.31 | 0.86 |
| London | C | +0.24 | 0.99 | 0.86 | 0.77 |
| Milan | C | **−0.81** | 0.66 | 0.91 | 0.77 |
| PanamaCity | C | −0.28 | 0.52 | 0.46 | 0.50 |
| Paris | C | +0.45 | 0.35 | 0.50 | 0.64 |
| **ALL** | mixed | −0.23 | 0.96 | 0.78 | **0.71** |

**回答："预报无法捕捉 basis"这个负结论不成立。** 网格预报能分辨出同城两个机场：
全样本 71% 的城市-日两个站落到不同整数档位，平均绝对差 0.78°C。差异不是随机
噪声而是城市系统性的：KualaLumpur（官方站系统性低 1.28°C）、Milan（低 0.81°C）、
Paris（高 0.45°C）方向稳定。因此早场 sleeve 的物理前提（预报阶段就有可见 basis）
成立——可以进入步骤 2/3。

注意：basis 方向因城市而异。错误站不总是更高；Paris 官方站（LFPB）反而比戴高乐
（LFPG）预报更高 +0.45°C。所以"早场买官方站对应档位 YES"对每个城市是不同方向的
偏移，不能套用单一规则。

## 步骤 2：早场市场价对齐哪个站

在 event_date 当地 10-13h 窗口，对每个城市-日取市场隐含 YES 概率最高的 bracket
（market peak），看官方站预报落档 vs 错误站预报落档哪个更常命中这个 peak。
`forecast_max_f`（快照里 open-meteo 城市点预报）实际对应 FULL_CITY_CONFIGS 的
ICAO 坐标，即**错误站**侧。

| 城市 | n city-days | 官方站落档=市场peak | 错误站落档=市场peak |
|---|---:|---:|---:|
| Chicago | 14 | 0.71 | 0.43 |
| KualaLumpur | 21 | **0.14** | **0.43** |
| London | 21 | 0.52 | 0.29 |
| Milan | 21 | **0.81** | 0.24 |
| PanamaCity | 19 | 0.47 | 0.53 |
| Paris | 21 | 0.76 | 0.43 |
| **ALL** | 117 | **0.56** | **0.38** |

**整体：早场市场更贴官方站（56% vs 38%）。** 但分城市看是混合的：
- 强对齐官方站：Milan(0.81)、Paris(0.76)、Chicago(0.71)、London(0.52)。
- **反向（市场跟错误站）：KualaLumpur(0.14 vs 0.43)、PanamaCity(0.47 vs 0.53)**。

KL 这条与步骤 1 一致：KL 官方站预报系统性低 1.28°C，而早场市场仍锚在更高的错误站
（WMSA）档位。这正是"早场用官方站买 YES"在 KL 失败的机制——市场早场没认官方站。

## 步骤 3：早场 YES/NO 回测（官方站预报 vs 白名单对照）

规则：basis 城市 10-13h，用官方站预报 max → round_half_up → 落档买对应 YES
（`yes_best_ask`）；tail-NO 腿买严格低于该预报档位 low 的档位（`no_best_ask`）。
对照组：白名单城市（match_round 均值==1.0 且 ≥20 天，36 城）同窗口用城市点预报
（`forecast_max_f`）买 YES。settle vs pm_history 单 winner。无前视：只用 event_date
当地 10-13h 已可见的当日预报。

| group | leg | trades | cities | city-days | cost | pnl | ROI | win | t-stat |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| basis_official | early_yes | 85 | 6 | 85 | 43.25 | +7.76 | **+17.9%** | 0.60 | **1.86** |
| basis_official | early_tail_no | 74 | 6 | 58 | 59.45 | −1.45 | −2.4% | 0.78 | −0.47 |
| basis_official | ALL | 159 | 6 | 88 | 102.7 | +6.31 | +6.1% | 0.69 | 1.21 |
| control_whitelist | early_yes | 547 | 36 | 547 | 198.9 | −3.85 | −1.9% | 0.36 | −0.46 |

**对照组干净**：白名单 36 城早场用城市点预报买 YES ROI −1.9%、t=−0.46——市场在
"站点一致"的城市早场已有效，无 taker 边际。这把 basis 城市的正 ROI 与"早场预报
本身有 alpha"区分开：edge 来自站点 basis，不是预报阶段的普遍优势。

**但 basis early-YES 高度不均匀（逐城市）**：

| 城市 | n | ROI | win | t | avg entry cost |
|---|---:|---:|---:|---:|---:|
| Milan | 20 | **+50.7%** | 0.80 | +3.10 | 0.531 |
| London | 17 | **+49.9%** | 0.76 | +2.47 | 0.510 |
| Paris | 18 | +9.0% | 0.61 | +0.38 | 0.561 |
| PanamaCity | 8 | +8.2% | 0.62 | +0.51 | 0.578 |
| Chicago | 8 | −27.8% | 0.50 | −1.02 | 0.693 |
| KualaLumpur | 14 | **−45.9%** | 0.14 | −1.50 | 0.264 |

聚合 +17.9% 主要由 London/Milan 两城贡献，与步骤 2 的"市场锚官方站"排名完全一致：
市场早场就跟官方站的城市（Milan/London/Paris/Panama）正，市场早场跟错误站的城市
（KL，部分 Panama）负或反号。Chicago 样本少（8）且 entry cost 偏高（0.69），统计上
不显著。tail-NO 腿整体小负，无独立价值。

入场价不是廉价尾部驱动：early-YES 平均成本 0.51（中位 0.55），是中档价位，正 ROI
来自 win-rate（basis 城市 0.60 vs 白名单 0.36），不是高赔率长尾。

## Verdict：早场 sleeve 成立/不成立

**部分成立，但不是干净的整池 6 城 sleeve。**

1. **物理前提成立**：预报能区分同城两机场（71% 城市-日档位不同，城市系统偏移
   0.45~1.28°C）。"预报分辨不出 basis"的负结论被否定。
2. **市场前提是 city-specific**：早场市场整体更贴官方站（56% vs 38%），但
   KualaLumpur（市场锚错误站，官方站预报低 1.28°C）和 PanamaCity 是例外。
3. **回测确认机制**：basis 城市 early-YES +17.9%（t=1.86）、对照白名单 −1.9%
   （t=−0.47），edge 归因于站点 basis 而非早场预报普遍 alpha。但 ROI 由
   London/Milan 主导（t=2.5/3.1），KL/Chicago 反号或不显著。

**实践含义**：早场 sleeve 只在"早场市场实际锚官方站"的城市可开（当前样本：Milan、
London，可能 Paris/Panama），必须把步骤 2 的"市场早场锚哪个站"做成逐市场入场前置
检查；KualaLumpur 这类市场早场跟错误站的城市，早场买官方站 YES 反而亏，应排除或
等到峰值后观测 sleeve。不要为了凑整池 6 城而把 KL/Chicago 一起开。

**样本与稳健性限制**：评估窗口仅 22 天/城，每城早场 YES 8~20 笔；London/Milan
的强正 ROI 需更长窗口复核（前置报告也提示 Polymarket 历史换过站、modal offset
逐月漂移，站点恒定不能假设）。早场 sleeve 的下一步应是：(a) 扩样本到更长历史；
(b) 把"市场锚站"判定（步骤 2 的 peak-match）做成实时入场 gate；(c) 与现有峰值后
观测 sleeve 做组合，看早场是否提供独立增量还是仅提前同一笔。
