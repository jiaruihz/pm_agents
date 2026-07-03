# HeadA Low-Price YES 数据审计 + hot-tail 机制边界 v1

Generated: 2026-07-04
Scope: HeadA `forecast_tail_low_price_yes` 独立数据可信度审计 + 新模式挖掘（train ≤6/20 假说生成）+ E1 预注册。
不涉及 METAR reversal / regime-routed NO / tmax distribution。

## One-Line Read

**分母和结算标签核验通过（459/459 一致），但 hot-tail 的回测边际集中在 book 数据缺失或宽 spread 的行上——两边报价紧的 feasible 行 ROI≈0。分母里还混着 28% 根本不是 hot-tail 表达的"预报向下 bust"票（train -10.6%）。两者都已做成 shadow telemetry 字段，由 fresh forward 裁决，不改 live selector。**

```text
significance=NA（本文全部为 train 事后切片，只作假说生成）
baseline=NA
forward=NA（E1/E-book 评估窗从 2026-07-04 起）
conclusion=inconclusive（对任何 live 变更）；E1/E-book 预注册为 shadow 假说
```

## 1. 数据可信度审计

审计对象：冻结分母 `generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`（476 行）、
`runtime/weather.db`（只读）、`generated/low_price_yes_sizing_stop_v1/replay_rows.csv`（路径重放）、
live journals。

### 1.1 通过的检查

| 检查 | 结果 |
|---|---|
| 分母构造 | 476 行 candidate_id 唯一；每 city-date 恰好 1 张；ask 0.05..0.20 与 edge≥0.20 全成立 |
| PIT 入场价 | 476/476 `ask == market_yes_price`（决策快照价，非独立 mid）；`first_seen ≤ decision_ts` 全成立 |
| payoff | 全为 0/1；`win` 与 `payoff` 一致 |
| 结算标签 | 459/459 有 canonical settlement 的行与 `settlement_outcomes` 完全一致 |
| near-binary | settlement 层 1244 行 raw 非 0/1，`final_price` 已全部归一为 0/1 |
| 路径时间序 | 0 行 `max_bid_ts < decision_ts` |
| 路径 vs 标签矛盾行 | 2 行逐一核查均为真实结算：Atlanta 5/23 82-83（bid 冲 0.95 后输，winner 是上一格 84-85——本身就是 hot-tail 机制活例）；KualaLumpur 5/24 34+（安静赢家，settlement=1.0） |
| 采集连续性 | paper_snapshots 活到 2026-07-04 晨；N100 7/1 事故后 Mac 本地采集接住 |
| 峰值时钟污染波及 | HeadA 分母来自决策时刻 live 采集 snapshot（`open_meteo_live_*`），与 7/03 peak-clock backfill 污染（打击 intraday atlas/tmax/metar 证据）基本隔离 |

### 1.2 数据债（按严重度）

1. **live 血缘断裂**：tiny-live 已成交 fill 不在 `fact_trades`（无任何 low_price strategy 行）。
   fresh-forward 裁决 gate 需要"已结算活跃日"计数，当前无法从 canonical 链取数。
   修复 = 走 weather-fact-rebuild 全链路（本文 §5 记录结果）。
2. **closed-forward 17/19 行 payoff 非 canonical**：6/27、6/28 settlement_outcomes 近乎整日缺
   （各 1 城 11 行 vs 正常 47 城 517 行），这 17 行 payoff 是 CLOB 收盘价。
   所有报告的 closed-forward 数字（hold +65.9% 等）在补结算前只能当 telemetry 读。
   5/18 整日缺（0 行）、5/17/5/19 部分缺（老 survivorship 问题）。
3. **TP/stop 证据有效窗口比标称小**：5 月 78/476 行无任何未来 bid 路径（TP replay 中退化为 hold），
   TP20 的 delta 几乎全由 6 月驱动；15 个赢家无 bid 路径（不会被 cap）→ live-like TP20 的 -9.1%
   还**高估**了 TP 表现。TP 结论方向不变（更差），但样本按"6 月"读。
   6/30-7/01 快照密度掉一半（42/38 vs 85+/天），事故期路径判定弱。

## 2. 新发现（train ≤6/20 事后切片，共测约 6 组假说，多重检验风险自知）

### 2.1 发现 A：分母机制混杂——28% 的票不是 hot-tail

用 `forecast_max_f`（fact 层 476/476 全覆盖，绕过 98% null 的 native 字段）把 bracket 下沿换算 °F
后算 `dist_br = (bracket_low_f - forecast_max_f) / bracket_width_f`：

| 子分母 (train) | rows | win | ROI (fixed-share) | date-block CI |
|---|---:|---:|---:|---|
| 全体 | 383 | 13.1% | +24.7% | [-10.0%, +64.6%] |
| **hot（dist>0）** | 275 | 14.5% | **+38.3%** | **[+2.5%, +78.3%]** |
| cold/bust（dist≤0） | 108 | 9.3% | -10.6% | [-69.3%, +58.8%] |

`dist≤0` 的票（bracket 不在预报上方）买 YES 等于赌"预报向下 bust"，与 hot-tail 论题相反，
且 train 上亏钱。`dist>0` 是论题的天然零点，不是扫出来的阈值。诚实标注：hot 对全分母的
paired excess CI [-4.1%, +31.9%] 仍跨 0；hot top10-winners-removed +6.2%（集中度仍高）。

### 2.2 发现 B：市场对 bracket 距离不定价（alpha 的最干净表述）

各距离档 ask 平坦（0.085-0.11），但按站点偏差修正距离后真实胜率单调拉开：

| bias 修正距离(格) | rows | win | ROI |
|---|---:|---:|---:|
| < -0.5 | 184 | 10.9% | +6.8% |
| -0.5~0 | 115 | 13.0% | +17.5% |
| 0~0.5 | 61 | 18.0% | +67.8% |
| 0.5~1 | 19 | 21.1% | +137.5% |
| >1（真远尾） | 4 | 0% | -100% |

即"市场按同一个价卖所有 tail，真实概率随'偏差修正后预报离票距离'单调变化，
且超过 1 格后归零"。caveat：bias 层是 6/30 静态快照（与 train 重叠，泄漏），
且本文假设 `bias_p50` 为 native 度（待核实）；此形状只作 E2 预注册假说。

### 2.3 发现 C：输家解剖——一半是被向上打穿的

333 个 train 输家中 166 个的实际 winner 落在买的 bracket 上方（overshoot）。
其中 65 个来自 cold 票（=市场是对的，与发现 A 汇合）；**hot 票输家中仍有 43% 是 overshoot**
（热对了、格子买矮了）。naive 阶梯不可行：overshoot winner bracket 只有 35/166 有决策时候选行、
其中仅 10 个在 5-20c 可买（0 个过 edge 门）。表达实验（单票 vs +1 格 ladder vs "+"档）
需要决策时全 book 重放（E3）。

### 2.4 发现 D（最重要，对策略不利）：边际集中在 book 数据差的行

hot 子集按决策时 book 状态分解：

| book 状态 (train hot) | rows | ROI | 备注 |
|---|---:|---:|---|
| feasible（spread≤3c 且 depth≥$25） | 172 | **-0.0%** | 6 月单看也是 -0.2% |
| thin_wide（spread 中位 5c，depth 中位 $75） | 48 | +68.7% | 主要是宽 spread 而非薄 depth |
| missing（book 字段缺失，全在 5 月） | 55 | +117.3% | 入场价可执行性无法验证 |

两种解读，fresh forward 必须裁决：
- **解读①（假边际）**：宽 spread/缺 book 行的 ask 是 stale/ghost quote，实际买不到 →
  回测边际不可兑现；feasible 行的 ~0 才是真实水平。
- **解读②（真机制）**：宽 spread = 无人照看的 book = 时区注意力机制的直接证据；
  taker 吃真实 ask 对 $1-5 票仍可成交（depth 中位 $75 够用），紧 book 的做市商已把 tail 定价对，
  错价只存在于被忽略的市场。

live runner 的 fresh-book cushion（fresh ask 超出决策价 +1c 即 block）恰好是裁决仪器：
若解读①成立，thin_wide/missing 类候选会大量被 block 或 fill 失败；若解读②成立，
它们能以决策价附近成交且 forward 胜率复现。**这就是 tiny live probe 目前最有价值的问题。**

## 3. E1 / E-book 预注册（shadow telemetry，2026-07-04 起生效）

`scripts/ops/low_price_yes_integrated_tail_shadow_v2.py` 新增逐行字段（K=2 个预注册假说）：

```text
bracket_low_native / bracket_low_f
bracket_dist_br_v1        # (bracket_low_f - forecast_max_f) / width_f
hot_tail_boundary_v1      # dist > 0
dec_yes_spread / dec_yes_depth_ask_5c
book_state_v1             # feasible / thin_wide / missing
```

评估窗从 2026-07-04 起，中间不看不调：

- **E1 假说**：`hot_tail_boundary_v1 = true` 子集 ROI > `false` 子集（paired date-block），
  且 hot 子集 date-block CI > 0。
- **E-book 假说**：thin_wide/missing 候选的 live/fresh-book 可成交率（fresh ask ≤ 决策价+1c）≥ 60%，
  且 feasible 子集 forward ROI 与 train 一致（≈0）——即解读②成立、feasible 行可以从分母移除。
- **验收门**（沿用 tail review W3）：≥12 个已结算活跃日 + 上述 CI 条件 + top-trade-removed > 0。
  过门才谈把 `dist>0` 写进 live selector；不过门则 telemetry 保留、结论回落 inconclusive。

## 4. 实验清单（E2-E4，均不动 live）

- **E2 连续 EV**：W0 as-of station-bias 层完成后，用 `p_hat(adj_dist, bias_asof) - ask` 替代
  `edge≥0.20` 双阈值；发现 B 的单调形状即预注册假说。
- **E3 表达实验**：hot 触发 city-date 上用 paper_snapshots 决策时全 book 重放
  单票 / +1 格 ladder / "+"档，官方 fee、maker/taker 双口径。
- **E4 双模型分歧**：candidates 每城 source 固定（train 覆盖仅 3/383），需从 forecast cache 层
  物化同城同日 GFS vs ECMWF 分歧再测"分歧大 → tail 更值钱"。

## 5. E5 数据债修复状态（2026-07-04 全链路重建）

<!-- REBUILD_RESULTS -->

## 6. 当前 live 姿态（重申，无变更）

- Entry probe：`low_price_yes_lottery_tiny_live_v1` maker-first（`buy_yes_edge20_ask05_20_maker_first_v1`），
  fee-adjusted edge≥0.15、fresh-book cushion、$≈1/票。
- Exit：hold-to-settlement。TP20 exit overlay 已于 2026-07-03 15:21Z 停用并撤单
  （`disable_tp20_exit_overlay`，依据 sizing-stop v1 live-like replay -9.1%）。
- 本文不改 selector、不改 sizing、不 size-up；E1/E-book 只是 shadow 字段。

## 复核 artifacts

- 本文全部数字可从 `generated/low_price_yes_integrated_tail_v2/enriched_rows.csv` +
  `fact_signal_candidates`（forecast_max_f/unit/yes_spread/yes_depth_ask_5c，按 candidate_id join）+
  `settlement_outcomes`（win 复核与 winner bracket）+
  `generated/low_price_yes_sizing_stop_v1/replay_rows.csv`（路径覆盖）逐行复算。
- bracket 换算：C 城 `low_f = low*9/5+32`，宽 1.8°F；F 城宽 2.0°F；`27+`/`88-89`/`28` 三种格式取下沿。
