# M3 温度衰竭买 NO 策略 v0

Status: snapshot
Updated: 2026-06-12
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; analysis/observed_max_m3.md
前置: 2026-06-12-official-resolution-source-and-entry-timing-v0.md

## 问题

用户提出的策略表达：监控（官方站）METAR，在一个城市**当日最高温已经过去**
（温度从峰值明显回落）之后，挑选**不存在二次升温条件**的城市/日期，买入高于
running max 档位的 NO，收剩余不确定性溢价。

本报告把"最高温已经过去"形式化为衰竭信号
`decline = running_max_c - current_temp_c`（决策时点当前温度距当日已观测
最高温的回落幅度），并在 10-21h 全时段验证三层：物理层（信号是否预测穿档
崩塌）、市场层（市场是否已定价该信号）、策略层（具体规则的样本外式检验）。

## 数据与脚本

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_observed_max_residual.py \
  --decision-hours 10,...,21 --output-dir .../m3_observed_max_v3_h10_21
.venv/bin/python scripts/analysis/observed_max/research_official_station_running_max.py   # 10-21h + current_temp
.venv/bin/python scripts/analysis/observed_max/research_m3_orderbook_best_ask_backtest.py \
  --decision-hours 10,11,12,13 --output-dir .../m3_orderbook_best_ask_v3_h10_13
.venv/bin/python scripts/analysis/observed_max/research_m3_exhaustion_no.py
```

产物：`generated/m3_exhaustion_no_v0/`（物理表 / 市场 cell / 规则检验 / 全部
tail NO quotes）。结算口径：pm_history 单 winner；白名单城市 running max 用
wu_obs（站点=官方），修复 6 城用官方站 IEM。剔除 "or below" 底档。
样本：盘口 2026-05-19..06-09（21 个交易日），物理表 2024-04-30..2026-06-10。

## 发现 1：物理层 — 衰竭信号成立且强于钟点

白名单 36 城 P(round 穿档≥1)，小时 × 衰竭幅度：

| 小时 | 峰值附近(<0.5°C) | 回落1-2°C | 回落≥2°C |
|---:|---:|---:|---:|
| 13 | 65.0% | 24.4% | 10.7% |
| 14 | 47.1% | 12.1% | **4.7%** |
| 15 | 29.0% | 5.0% | **1.4%** |
| 16 | 15.7% | 2.1% | **0.4%** |
| 17 | 9.7% | 0.9% | **0.1%** |

14-15h 回落≥2°C ≈ 17-18h 无条件入场的物理安全性。**警告**：10-11h 的
"衰竭"不可信（10h 回落≥2°C 后仍有 40% 穿档——早晨假峰值+午后正常升温），
信号只在 13h 后有效。

反向检验：未衰竭（<0.5°C）时买 NO 在两组都显著为负（白名单 t=-7.6 /
修复组 t=-4.7）——物理方向正确。

## 发现 2：市场层 — 白名单城市的市场已定价衰竭信号

策略规则检验（每 city-day-bracket 只在首个满足条件的时点进一次，日度 PnL t-stat）：

| 规则 | 笔数 | ROI | 日度 t | 正天数 |
|---|---:|---:|---:|---|
| 白名单 基线 15-17h d≥1（无衰竭条件） | 665 | -5.6% | -4.3 | 5/21 |
| 白名单 衰竭≥1.0 10-17h d≥1 | 697 | -1.5% | -1.4 | 7/21 |
| 白名单 衰竭≥2.0 10-17h d≥1 | 209 | -1.6% | -0.8 | 9/21 |
| **修复6城 衰竭≥1.0 10-17h d≥1** | **119** | **+5.5%** | **+3.0** | **16/21** |
| 修复6城 基线 15-17h d≥1 | 110 | +6.5% | +1.6 | 15/21 |

关键结构：

1. **白名单：衰竭条件把 ROI 从 -5.6% 拉到 -1.5%，但永远到不了正**。衰竭日的
   ask 平均 0.81-0.86——做市方对可观测的温度回落定价很快。36 个干净城市里
   taker 端没有钱可赚，衰竭信号也救不回来。
2. **修复 6 城：衰竭规则是目前找到的最强 NO 规则**——+5.5%、日度 t=3.0、
   16/21 天为正、6/6 城为正（Panama +17.5% / Chicago +13.2% / Paris +7.7% /
   London +4.1% / KL +2.8% / Milan +1.1%）。
3. 同组无衰竭基线 ROI 更高（+6.5%）但 t 只有 1.6：**衰竭信号的价值不是抬高
   均值，是砍掉方差**（去掉了"还在升温途中被穿档"的输单）。
4. 距离分层：edge 集中在 **d1**（紧邻 running max 上方第一档，+12.8%，53 笔）；
   d2 +1.7%、d3 -2.0% ——衰竭信息对最近的档位最值钱。d1-only 是事后选格，
   列为参考不作为主规则。

## 结论（回答原始问题）

1. **思路本身（监控 METAR → 等衰竭 → 买 NO）物理上完全成立**，且衰竭信号
   显著优于纯钟点入场。
2. **但在市场盯对了站的城市（白名单 36 城），这个策略没有钱**：市场对衰竭
   的定价速度足够快，所有变体 ROI 为负。纯"温度 theta"作为公开信息策略
   已经被套利掉了。
3. **可行版本 = 衰竭信号 × 站点 basis**：在 Polymarket 官方站 ≠ 大众认知站
   的 6 个城市（Paris/London/Milan/Chicago/KL/Panama），盯**官方站**的衰竭
   买 d1 NO，+5.5%（t=3.0）。edge 来源不是 theta 本身，而是我们盯的站是
   结算真相、对手盯的是错误站。
4. 该规则与前一报告的 official-bucket YES 表达（+13~23%）是同一信息的两种
   变现；YES 表达每笔更肥，NO 表达胜率更高更平滑。组合时按每档报价择优。

## 容量与执行现实

- 21 天窗口规则总成本 $92（top-of-book），日均可执行名义 ~$190；加上 YES
  表达约 $300-500/天。小资金策略，深档与 maker 容量未计。
- live 需要分钟级官方站 METAR（aviationweather.gov / NWS），IEM 为归档源。
- 入场前必须逐市场核对 rules 站点未变更（历史有漂移证据）。

## 附录（2026-06-12 追加）：截面选择也救不回白名单 taker

用户假设：0.81-0.86 的 ask 反映的是平均二次升温风险；如果用 METAR 历史训练
P(穿档 | 城市,时点,衰竭深度) 的截面模型，挑出真实风险≈0 的 city-day 买 NO，
应该能在 36 个正常城市也取得正收益。

检验（`research_m3_cross_section_no.py`）：27 个月物理数据训练（严格截断在
2026-05-19 前，无泄漏，Beta-binomial 收缩），对 eval 窗口全部白名单 tail-NO
报价打分选择。结果：

| 模型胜率分桶 | 实际胜率 | 市场 ask |
|---|---:|---:|
| 0.90-0.95 | 0.783 | 0.814 |
| 0.95-0.98 | 0.854 | 0.849 |
| 0.98-1.00 | **0.917** | **0.917** |

两个结论：

1. **气候学模型样本外过度自信**：模型说 98-100% 赢的格子实际只赢 91.7%
   ——5-6 月对流季的二次升温风险高于 27 个月均值，纯历史频率模型跟不上
   当季 regime。
2. **市场 ask 对实际胜率的校准几乎完美**（0.917 vs 0.917）。白名单市场的
   定价器比我们的 27 个月 METAR 气候学**更准**（大概率用了实时数值预报）。
   模型筛出的"高 EV"全部是模型误差而非市场误差：选中组 realized ROI
   -1.5%~-2.4%，没有任何阈值/距离组合为正。

含义：白名单城市的对手盘输入是对的、校准是好的，taker 端没有可收割的
"笨溢价"。要在截面上打赢它需要比它更好的风险模型（实时 NWP 条件化），
那是和市场比公开信息处理速度的游戏——正是此前 forecast 路线失败的原因。
6 城 edge 的本质区别在于对手盘**输入错误**（盯错站），不是处理慢。
白名单剩余可探索方向只有 maker（收点差而非付点差）。

### NWP 条件化也只到公允（2026-06-12 第二次追加）

继续用户假设的完整版：把**决策时刻的实时 ECMWF 预报**（生产 paper snapshot
里逐半小时记录的 `forecast_max_f`，无前视）+ METAR 衰竭组合成选择模型，
在白名单城市买"预报说到不了的档位"的 NO（`research_m3_nwp_locked_no.py`，
5,287 条同日 13-17h 报价，23 天 36 城，官方结算）：

| 模型 | ROI | 日度 t |
|---|---:|---:|
| 仅钟点（15-17h，前报告） | -5.6% | -4.3 |
| 仅 METAR 衰竭≥1°C | -2.3% | -1.4 |
| 仅 NWP locked | -5.1% | -4.8 |
| **衰竭 + NWP margin≥1（全套公开信息）** | **-0.06%** | -0.02 |

模型越完整，ROI 越逼近 **0**——这是教科书式的"对 taker 有效"市场：用全部
公开信息（实况+预报）能做到不亏，但赚不到，点差吃掉一切。ask 对 realized
的校准在每个 nwp_margin 分桶都成立，市场已包含预报信息。LLM 充当专家判断
本质是同一信息集的有噪声版本，上限同样是公允价。白名单 taker 路线**正式
关闭**（三层证据：钟点/衰竭/截面+NWP），唯一剩余方向是 maker。

分城市检验（回应"总体校准可能掩盖单城失准"的质疑）：36 城逐城 ROI 分布为
+0.4% ~ -9.9% 的连续带，仅 2/36 为正且≈0（NYC +0.4% / LA +0.3%）——不存在
"强正城市被强负城市抵消"的结构。前后半样本稳定性：前 11 天为正的 8 个城市，
后 10 天 ROI -1.7%，与未入选城市（-2.3%）无差别——单城正收益不可持续，是
噪声。对照：修复 6 城 6/6 为正且有机制解释，这才是真结构的形状。
校准点估计的精度注记：0.917 vs 0.917 桶 n=276，二项 95% CI ±3.3pp，结论
应表述为"检测不出失准"；承载结论的是策略实测 ROI 而非校准表本身。

## 下一步

1. shadow 验证组合规则（YES@官方bucket + NO@d1/d2 衰竭），2-4 周。
   **已上线（2026-06-12）**：`scripts/ops/weather_station_basis_shadow.py`，
   15 分钟一个 cycle，实时 METAR（aviationweather.gov）+ Gamma/CLOB top-of-book，
   入场前逐市场核对 rules 站点（mismatch 写 alerts.jsonl 并跳过）。
   启动/输出：

   ```bash
   scripts/ops/start_weather_station_basis_shadow.sh        # 启动 loop（nohup + pid）
   .venv/bin/python scripts/ops/weather_station_basis_shadow.py report   # 看战绩
   # 数据：runtime/weather_edge_v1/station_basis_shadow/{entries,cycles,settlements,alerts}.jsonl
   ```

   注意：跑在本机（Mac），睡眠会断采集；如需 7×24 应迁移 N100（走
   weather-strategy-deploy 流程）。
2. 13h 前禁入（物理表硬约束）已写进规则（NO_HOURS=13..17）。
3. maker 变体：衰竭确认后在 d1 NO bid 侧挂单收 spread。
4. HKO 数据发布后验证 HongKong；每周 probe 5 个未挂牌城市。
