# 结算源审计第二批 v0（HongKong/Jakarta 修复，Moscow/Seoul/Shenzhen 诊断）

Status: snapshot
Updated: 2026-06-13
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md
前置: 2026-06-12-official-resolution-source-and-entry-timing-v0.md

> 本报告由 subagent 完成主体分析（产物齐全），报告正文由主会话根据
> `generated/settlement_basis_batch2_v0/` 产物补写。

## 结论总表

| 城市 | 此前对齐 | 假设 | 验证后 | verdict |
|---|---:|---|---:|---|
| **HongKong** | 44% | HKO Daily Extract 小数 max + **floor** 映射 | **100%** (27/27) | ✅ 修复，第 7 城 |
| **Jakarta** | 12.5% | 官方站是 **WIHH**（Halim），非 WIII | **100%** (8/8) | ✅ 修复，第 8 城（样本仅 8 天，需累积） |
| Shenzhen | 7% (IEM) | wu_obs feed ≠ IEM METAR | 71% (wu feed) | ⚠️ 部分解释，未修复 |
| MexicoCity | 96.4% | 各口径无差 | 96.4% | 维持（1 天 miss） |
| Moscow | 88.9% | F-chain 取整/wu feed 均无效 | 88.9% | ❌ 未解决 |
| Seoul | 77.8% | 同上 | 77.8% | ❌ 未解决 |

## 关键发现

1. **HongKong 映射规则是 floor 不是 round**：HKO "Absolute Daily Max" 30.4°C
   → winner "30"；31.6 → "31+"（顶档）。27/27 全对。数据源为 HKO Daily
   Extract（5-6 月数据可得，agent 已落地抓取路径于 detail CSV）。
2. **Jakarta 官方站是 Halim（WIHH）**：从其有市场日期的 rules 文本直接提取，
   8/8 天 IEM WIHH raw max 与 winner 全对。我们管线一直配置的 WIII
   （Soekarno-Hatta）只有 12.5% ——又一个"站点 basis"城市。
3. Moscow(89%)/Seoul(78%) 的错位假设（weather.gov F→C 取整链、wu feed、
   6h 组）全部无效，根因未明，**不得进入交易池**，列为待研究。
4. 旁证（主会话补充）：公开 GitHub bot（suislanchez/polymarket-kalshi-weather-bot）
   的站点配置 NYC=KNYC（官方 KLGA）、Denver=KDEN（官方 KBKF）——市场参与者
   配置错误站点是真实存在的普遍现象，支撑站点 basis edge 的机制。

## 产物

`docs/analysis/2026-06/generated/settlement_basis_batch2_v0/`：
逐城对齐明细 CSV、`hypothesis_summary.csv`、`jakarta_rules_stations.csv`。

## 下一步

1. Jakarta（WIHH）已加入 station-basis shadow 城市表；HongKong 需要 HKO
   实时数据源（rhrread API）变体后再入 shadow，且注意其 **floor** 映射与
   一位小数精度（衰竭信号阈值需按 0.1°C 粒度重新定义）。
2. Moscow/Seoul 根因研究：候选方向——官方源页面展示值与 METAR 的逐日 diff
   定位 mismatch 日的共同模式（misses 集中在哪些温度/天气形势）。
3. Shenzhen wu feed 71% 仍不可交易；继续观察 WU 数据源变化。
