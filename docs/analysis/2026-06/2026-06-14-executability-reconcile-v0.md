# Station-Basis 可成交比例对账 (Executability Reconcile) v0

Status: snapshot
Updated: 2026-06-14
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; 2026-06-14-station-basis-live-candidate-v1.md
前置: 2026-06-12-official-resolution-source-and-entry-timing-v0.md（station-basis edge 的来源）

## 任务与结论一句话

回测 station-basis taker（YES 买官方 running bucket / NO 衰竭后买高档）的 +17%/+5.5%
ROI 一直是"**条件于 ask 已被预过滤到 [0.005, 0.97/0.995]**"算出来的，从没统计过全集里
真正可成交的比例。本报告用原始历史盘口重建**全集**（含 0.999 和无卖单候选），量化可成交占比。

**结论：策略是 (a) 真实但稀有 + 强时钟衰减，不是 (b) 回测假象，也不是 (c) 单日问题。**
回测 edge 的符号在 executable 子集上复现（YES +11.7%、NO d1 +7.3%），但"可成交"高度依赖
**入场要早（14h）+ 城市要对（不能是 PanamaCity）**。N100 v1 实时 0/41 不是矛盾，而是
**单日(Jun13) + 只覆盖两个最差城市(PanamaCity/Chicago) + 采样在 15h（已开始收敛）**
的小样本抽签，与历史这两城在该时段的低可成交率完全一致。

## 数据与脚本（本机可复现）

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_executability_reconcile.py
```

产物目录 `docs/analysis/2026-06/generated/m3_executability_reconcile_v0/`：

- `executability_candidates.csv` — 全集候选明细（每候选含 status/ask/size/token/winner）
- `executability_share_by_rule.csv`、`..._by_rule_city.csv` — 可成交占比（per-quote）
- `executability_share_by_rule_one_per_cityday.csv`、`..._city_one_per_cityday.csv` — 每 city-day-bracket 最早一次入场的可成交占比（更接近真实单次下单）
- `executability_share_by_hour.csv` — 按决策小时的可成交占比（收敛机制）
- `executable_ask_size_distribution.csv` — executable 的 ask/size 分布
- `subset_roi_by_rule.csv` — 三个嵌套子集 ROI（executable_strict / inband_any_size / backtest_band）
- `executable_one_per_cityday_roi.csv`、`manifest.json`

### 口径

- basis 6 城：Paris/London/Milan/Chicago/KualaLumpur/PanamaCity。Jakarta 无官方站
  running-max 数据（`official_station_running_max_v0` 不含 Jakarta），排除。
- running max 源：`official_station_running_max_v0`（官方站；F 城 round(running_max_raw)，C 城 round(running_max_c)）。
- 决策小时 14-17h 本地；每候选取该小时内**最接近决策时刻（小时内最后一张）**的快照。
- 结算唯一真相：`m3_settlement_alignment_v1` 里 `pm_history_valid & winner_count==1` 的 city-day 单 winner。
- **全集 = 规则触发的每个 (city, date, hour, bracket, outcome) 报价，不预过滤 ask**（与回测逐报价口径一致）。
- 候选窗口受盘口快照覆盖限制：**2026-05-19 → 2026-06-09，22 天 ≈ 3 周，6 城**。

### status 分类

| status | 定义 | 含义 |
|---|---|---|
| `executable` | ask ∈ [0.05, 0.97] **且** size ≥ 5 股 | 真正可吃 |
| `no_edge` | ask > 0.97 | 市场已收敛，无 taker edge |
| `no_asks` | token 当时没有卖单（或 not_found） | 根本没盘口 |
| `thin` | ask 在带内但 size < 5 | dust，算不可成交 |
| `below_floor` | ask < 0.05 | 退化（只 YES 出现，YES 极便宜也无意义） |
| `no_snapshot` | 该小时无快照 | 数据缺口 |

## (a) 全集里 executable 占比（按规则）

**Per-quote 全集**（每 city-date-hour-bracket 一行）：

| rule | total | executable | no_edge | no_asks | thin | **executable %** |
|---|---|---|---|---|---|---|
| yes_bucket | 404 | 231 | 126 | 16 | 9 | **57.2%** |
| no_d1_exh | 181 | 44 | 115 | 16 | 1 | **24.3%** |
| no_d2_exh | 153 | 12 | 89 | 33 | 0 | **7.8%** |

**每 city-day-bracket 最早一次入场**（更接近"这个机会到底能不能下一单"）：

| rule | total | executable | no_edge | no_asks | **executable %** |
|---|---|---|---|---|---|
| yes_bucket | 144 | 119 | 9 | 1 | **82.6%** |
| no_d1_exh | 73 | 34 | 33 | 1 | **46.6%** |
| no_d2_exh | 64 | 11 | 42 | 7 | **17.2%** |

关键：NO 的主要失败模式是 **`no_edge`（ask > 0.97，市场已把尾部概率收敛到 ~1）**，
不是 `no_asks`。这正是 NO 能赢的原因（尾部确实不会发生），但也意味着 taker 只能在
**市场还没完全收敛的早窗口**吃到 < 0.97 的卖单。

## (b) executable 的 ask/size 分布

| rule | n | ask_min | ask_p25 | ask_median | ask_p75 | ask_max | size_min | size_median | size_max |
|---|---|---|---|---|---|---|---|---|---|
| yes_bucket | 231 | 0.05 | 0.44 | 0.75 | 0.895 | 0.97 | 5.0 | 21.0 | 1497 |
| no_d1_exh | 44 | 0.44 | 0.795 | 0.890 | 0.959 | 0.97 | 5.0 | 26.7 | 500 |
| no_d2_exh | 12 | 0.71 | 0.868 | 0.945 | 0.963 | 0.97 | 6.8 | 57.2 | 414 |

- executable NO 的 ask 中位数 **0.89–0.945**：这是"高价 NO 收尾部 theta"，单笔毛利只有 3–11¢，
  靠 ~91% 胜率拉正。
- **size 不是 dust**：executable NO 的 size 中位数 27–57 股，仅 10/56 笔 size < 10 股；
  executable YES 中位 21 股（45/231 < 10 股，YES 便宜档常薄一点，但 NO 高价档普遍厚）。
  回测里 0.48/0.83 那类 ask 的 size 是真实可吃的，不是 dust。

## (c) executable 子集 ROI 是否 = 回测

`subset_roi_by_rule.csv`（per-quote，官方单 winner 结算）：

| 子集 | rule | trades | win_rate | ROI |
|---|---|---|---|---|
| executable_strict (ask∈[0.05,0.97] & size≥5) | yes_bucket | 231 | 73.6% | **+11.7%** |
| executable_strict | no_d1_exh | 44 | 90.9% | **+7.3%** |
| executable_strict | no_d2_exh | 12 | 91.7% | +0.7% |
| backtest_band (YES ask≤0.995 / NO ask≤0.97) | yes_bucket | 314 | 78.0% | +10.2% |
| backtest_band | no_d1_exh | 45 | 91.1% | +8.6% |

对照原回测 CSV：

- **NO d1 对得上**：原 `m3_exhaustion_no_v0` repaired6 +9.8% / live5 +12.8%；
  本报告 NO d1 executable +7.3%、one-per-city-day +9.8%。符号和量级一致。
- **YES 符号一致但量级不同**：原 `m3_orderbook_best_ask_v2_h14_17` YES h14-16 repaired6 +30.9%。
  差异原因已定位：**原 YES 回测用的是 wu_obs observed running max（`m3_observed_max_v2_h14_21`），
  而本报告按 brief 要求用官方站 running max**。对这 6 个"错位城市"，官方站 ≠ wu_obs，
  running bucket 落在不同档 → YES 候选集不同（195 笔 my-only / 140 笔 orig-only / 119 共有）。
  本报告口径对 station-basis 论点更自洽（整套论点就是官方站 ≠ 大众站）；原 YES 回测在
  repaired 城用错了 running 源。one-per-city-day YES executable ROI **+21.7%**，与"+17%"
  这一头条数量级吻合。

→ **executable 子集 ROI 复现了回测 edge 的符号与量级；+17%/+5.5% 不是凭空，但它们等价于
"条件于早窗口、对的城市、且 ask < 0.97" 的子集 ROI**，而不是"任何规则触发都能下单"的 ROI。

## (d) 历史 vs v1 实时是否一致 → 一致，不是矛盾

N100 v1 `candidate_audit.jsonl`：41 行，全部 `2026-06-13` 单日，只有 **PanamaCity(25) + Chicago(16)**，
13 个 distinct 候选，status = `no_asks`(25) / `ask_out_of_band`(16，ask 0.97–0.999)，**0 个 executable**。

把历史按这两个城市切片：

| rule | city | executable % (per-quote) |
|---|---|---|
| no_d1_exh | PanamaCity | **7.7%** |
| no_d2_exh | PanamaCity | **0.0%** |
| no_d1_exh | Chicago | 25.0% |
| no_d2_exh | Chicago | 4.3% |

v1 实时 0/41 与历史完全自洽：

1. **只覆盖两个最差城市**：PanamaCity 历史 NO d1 仅 7.7%、d2 0% executable（几乎从不可成交）；
   v1 没看到 Paris/London/Milan（这几城 NO d1 25–37%）。
2. **采样在 15h**：v1 audit hour_local=15。NO d1 全集按小时的 executable% 是
   **14h 65.6% → 15h 32.4% → 16h 18.0% → 17h 3.2%**（见下）。15h 已经在快速收敛。
3. **单日**：41 行其实只是同一天 13 个候选被监控循环重复记录多次。
4. v1 audit 里 Chicago d1（84-85°F）的 ask 反复落在 **0.97–0.99**，正好压在 0.97 上沿——
   印证 NO 收敛到 no_edge 是主导失败模式，而非完全无盘口。

所以 0/41 是"小样本 + 最差城市 + 偏晚采样"的抽签，不构成对历史 edge 的反驳。

## 收敛机制：executable % 随小时单调衰减

`executability_share_by_hour.csv`：

| rule | h14 | h15 | h16 | h17 |
|---|---|---|---|---|
| yes_bucket | 80.4% | 72.0% | 49.5% | 26.7% |
| no_d1_exh | 65.6% | 32.4% | 18.0% | 3.2% |
| no_d2_exh | 24.0% | 16.1% | 2.3% | 0.0% |

市场在 14h 之后快速把概率定到 0/1，taker 窗口在 16–17h 基本关闭。**入场必须在 14h（最迟 15h）。**

## (e) Verdict + 机会频率

**Verdict: (a) 真实但稀有，受强时钟衰减 + 城市异质性约束。** 不是回测假象（executable 子集
ROI 复现 edge 符号），不是单日问题（历史 3 周横截面就稀疏）。

每周可成交机会（6 城、~3 周历史外推，one-per-city-day-bracket、executable_strict）：

| 规则 | executable city-day-brackets / 3 周 | **≈ 每周** |
|---|---|---|
| YES bucket（6 城） | 119 | **~40** |
| YES bucket（LIVE5，排除 Milan） | 90 | **~30** |
| NO d1（6 城） | 34 | **~11** |
| NO d1（LIVE5） | 29 | **~10** |
| NO d2（LIVE5） | 9 | **~3** |

注意 LIVE5 NO d1 的 city-day-bracket 级 executable 率是 **45%**（29/64），比 per-quote 的
24% 高很多——因为只要**最早合格小时**有 < 0.97 的卖单就算可成交，而真实下单只需要一次。

### 对实盘的含义

- YES 充裕（~30–40/周），但必须 14h 入场、避开 Milan/PanamaCity（PanamaCity YES per-quote 0% executable）。
- NO 稀缺（LIVE5 d1 ~10/周、d2 ~3/周），单笔毛利仅 3–11¢，且只有早窗口能吃到；
  晚于 16h 基本只剩 ask=0.999 的 no_edge。
- v1 实时若想看到 executable，必须把采样提前到 14h 并覆盖 Paris/London/Milan，
  否则会持续看到接近 0 的可成交率——这是策略本身的稀有性，不是 bug。

## 局限

- 历史可成交窗口仅 ~3 周（盘口快照 2026-05-19 起）；每周频率是小样本外推。
- "最接近决策时刻"取小时内最后一张快照（偏保守，更收敛）；若取整点第一张，executable% 会更高。
- top-of-book best ask + size，未建模排队、部分成交、手续费、延迟。
- 仅 taker 形态；maker 挂单可成交性是另一个问题。
```
