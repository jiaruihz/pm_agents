# Official Resolution Source 识别 + reheat-risk 入场时机研究 v0

Status: snapshot
Updated: 2026-06-12
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; analysis/reheat_risk.md
前置: 2026-06-11-m3-tail-no-retail-diagnosis-v0.md（其下一步 1/2 即本报告）

## 任务与结论一句话

执行 v0 报告的下一步 1（官方 resolution source 识别）与 2（研究窗口前移到
14-17h）。结果：**官方源全部识别并验证；6 个"错位城市"用官方站修复后对齐率
~100%；在这 6 个城市上发现了机制明确、对照组干净的正边际（站点 basis edge），
14-16h 入场，两种表达 ROI +5%~+23%；白名单城市所有时段仍无 taker 边际。**

## 数据与脚本（全部本机可复现）

```bash
.venv/bin/python scripts/analysis/observed_max/research_official_resolution_source.py
.venv/bin/python scripts/analysis/observed_max/research_official_station_alignment.py
.venv/bin/python scripts/analysis/observed_max/research_official_station_running_max.py
.venv/bin/python scripts/analysis/observed_max/research_m3_observed_max_residual.py \
  --decision-hours 14,15,16,17,18,19,20,21 --output-dir docs/analysis/2026-06/generated/m3_observed_max_v2_h14_21
.venv/bin/python scripts/analysis/observed_max/research_m3_orderbook_best_ask_backtest.py \
  --decision-hours 14,15,16,17 --observed-detail .../m3_observed_max_v2_h14_21/... \
  --output-dir docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h14_17   # 18,19 同理
.venv/bin/python scripts/analysis/observed_max/research_m3_entry_timing.py
```

产物目录：

- `generated/official_resolution_source_v0/`（52 城官方源 + 6 城对齐验证）
- 6 城官方站 running max 明细已迁出仓库；恢复索引为
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/manifests/pm_agents_generated_cleanup_small_20260805.json`。
  当前 producer 使用稳定 `--run-id` 写入 JRS research artifact root，历史消费者按内容 SHA 读取，不再依赖 dated docs 路径。
- `generated/m3_observed_max_v2_h14_21/`（14-21h 物理 residual，300,397 rows）
- `generated/m3_orderbook_best_ask_v2_h14_17/`、`v2_h18_19/`（14-19h 盘口重建）
- `generated/m3_entry_timing_v0/`（入场时机 EV 表 + 全部 tail NO trades）

注意：Gamma API 抓取走本机代理（mihomo mixed-port 7897/7890），结果已缓存在
`runtime/rule_source_research/gamma_cache/`，复跑不依赖网络。

## 发现 1：官方 resolution source 全景（52 城）

从每城最近 5 天 event 的 market rules 文本提取（2026-06-06..06-10）：

| 类别 | 数量 | 城市 |
|---|---:|---|
| WU + 站点与我们一致 | 37 | Tokyo/Beijing/NYC/Madrid/... |
| **WU + 站点不一致（DIFF）** | 6 | 见下表 |
| 非 WU 源（NONWU） | 4 | HongKong(HKO, **一位小数**) / Istanbul / Moscow / TelAviv（后三个用 weather.gov timeseries，ICAO 与我们一致） |
| 近期无市场（probe 5 天全空） | 5 | Boston / Jakarta / Lagos / Minneapolis / Phoenix |

DIFF 6 城（v0 报告中的"错位城市"全部在此）：

| 城市 | 我们用的站 | 官方站 | v0 round 对齐 | 官方站对齐（本报告） |
|---|---|---|---:|---:|
| Chicago | KMDW 中途 | **KORD 奥黑尔** | 64% | **100%** (37d) |
| PanamaCity | MPTO 托库门 | **MPMG Albrook** | 63% | **100%** (28d) |
| Paris | LFPG 戴高乐 | **LFPB 勒布尔热** | 46% | **100%** (36d) |
| KualaLumpur | WMSA 梳邦 | **WMKK KLIA** | 33% | **100%** (28d) |
| Milan | LIML 利纳特 | **LIMC 马尔彭萨** | 22% | **100%** (28d) |
| London | EGLL 希思罗 | **EGLC 伦敦城** | 26% | **97%** (35/36d，5/27 单日差 1°C) |

验证方法：IEM METAR（官方站）→ 本地日 max → round → 对 pm_history 单 winner
city-days。v0 的"常数偏移修不好"现象由此解释：不是偏移，是站点不同。

HongKong：官方源为香港天文台 "Absolute Daily Max"（一位小数），HKO 开放 API
的日最高温数据只发布到 2026-04-30，与 pm_history 覆盖（05-12 起）无交集，
**验证待数据发布**；该市场整数 bracket × 小数结算的映射规则也未验证，HK 维持
排除。

风险提示：v0 观察到错位城市 modal offset 逐月漂移（Paris 5月+1→6月0），最可能
的解释是 **Polymarket 历史上换过站**。本验证只覆盖 2026-04..06 的当前 rules。
任何依赖官方站的策略必须把"逐市场 rules 站点核对"做成入场前置检查，不能假设
站点恒定。

## 发现 2：14-17h 物理精算表（49 城全样本）

| 当地小时 | P(round 穿档≥1) | P95 residual |
|---:|---:|---:|
| 14 | 32.9% | 2.2°C |
| 15 | 17.1% | 1.1°C |
| 16 | 7.2% | 1.1°C |
| 17 | 2.8% | 0 |
| 18 | 1.2% | 0 |

修复 6 城（官方站，2026-04-01..06-10，~71 天/城）：17h 后 Chicago/London/Milan/
KL/Panama 穿档 0-1.4%，Paris 16h 仍 19.7%、17h 降至 2.8%。热带两城（KL/Panama）
16h 起即 ~0%。

## 发现 3：14-17h 盘口窗口比 20/21h 大 47 倍

14-17h joined 报价 19,574 行（20/21h 仅 417），top-of-book 名义合计 ~$3.4 万。
v0 的"散户容量≈0"结论只在 20/21h 成立，14-17h 不成立。

## 发现 4（核心）：站点 basis edge — 修复 6 城有正边际，白名单没有

结算口径：只用 pm_history 单 winner；修复城市 running max 用官方站 IEM 重建；
剔除 "or below" 底档（label 无法与点档区分的解析陷阱，v0 沿用的 joined 数据
同样受影响）。样本 2026-05-19..06-09，21 个交易日。

表达 A：tail NO（买高于官方 running max 的档的 NO，ask≤0.97）

| 入场小时 | 修复6城 ROI | 白名单36城 ROI（对照） |
|---:|---:|---:|
| 14 | +3.3% (159笔) | -4.7% (1031笔) |
| 15 | **+6.6%** (99) | -5.9% (620) |
| 16 | +5.0% (61) | -7.0% (337) |
| 17 | +1.2% (31) | -5.4% (109) |
| 18 | +6.2% (13) | +1.8% (29) |

距离分层（修复6城）：14h 入场只有 **距离≥2 档**为正（d2: **+14.2%**, 53笔,
胜率92.5%）；d1 要等到 **15h 起**才转正（+7.8%）。

表达 B：YES on 官方 observed bucket（买官方 running max 所在档的 YES，
0.05≤ask≤0.95）

| 入场小时 | 修复6城 ROI | 白名单 ROI（对照） |
|---:|---:|---:|
| 14 | **+23.4%** (80笔) | -7.4% (470笔) |
| 15 | **+20.4%** (68) | -9.2% (325) |
| 16 | **+13.4%** (47) | -9.8% (210) |
| 17 | +4.1% (26) | -6.5% (83) |

稳健性：

- 表达 B @14-15h 按城市拆分**全部 6 城为正**（Chicago +45% / Panama +43% /
  Paris +17% / Milan +17% / London +15% / KL +9%）；按日 16/21 天为正。
- 表达 A 按城市 5/6 为正（London -2%），按日 14/21 为正。
- 机制 + 对照组：同样的表达在白名单城市全时段为负 → 排除"普遍 theta/季节
  效应"，edge 来源就是站点 basis（做市方盯错站）。

容量（top-of-book，单小时快照）：修复 6 城 14-16h 合计每天可执行名义约
$300-600，21 天窗口 top-book 总 PnL：表达 A +$12.6 / 表达 B +$22（两表达高度
重叠，不可加总）。比 20/21h 的 $11 上限大一个数量级，但仍是小资金策略；
深档容量与挂单（maker）容量未计入。

注意不可加总：A 与 B 是同一物理判断（max 不再升）的两种表达，同一 city-day
会同时触发；组合时应按每档报价择优，不能两边全吃。

## 局限

1. 样本只有 21 个交易日 × 6 城；单格显著性 1.5-2.2σ，靠机制 + 对照组 + 全城
   一致性支撑，不是大样本统计证据。
2. top-of-book 单快照回测：无 queue/部分成交/滑点/费用；ask size 真实可吃量
   未验证。
3. 官方站 IEM 数据偶有缺口（KORD 5/20 类问题已通过底档剔除修复，但 IEM 实时
   延迟在 live 执行时需要换 aviationweather/NWS 实时 METAR 源）。
4. 站点可能再次变更（历史漂移证据），rules 核对必须做成自动化前置。
5. 仍为研究产物：**禁止直接导出 live 动作**，先走 shadow/paper。

## 下一步（按优先级）

1. **shadow 验证（建议立即）**：在修复 6 城上线 shadow tracking：每天 14-16h
   本地时间按表达 B（官方 bucket YES）+ 表达 A（d≥2 tail NO @14h, d≥1 @15h+）
   记录虚拟成交，跑 2-4 周看 realized vs 回测。
2. **实时官方站数据源**：live 执行需要分钟级 METAR（aviationweather.gov /
   NWS API），IEM 是归档源不保证实时。
3. **rules 自动核对**：入场前逐市场抓 description 验证站点未变（已有缓存
   fetcher 可复用）。
4. **maker 变体**：在修复城市挂单而非吃单（v0 下一步 3），容量上限更高。
5. HongKong：等 HKO 5-6 月数据发布后验证小数→整数 bracket 映射。
6. 5 个无市场城市（Boston/Phoenix/...）每周 probe 一次是否重新挂牌。
