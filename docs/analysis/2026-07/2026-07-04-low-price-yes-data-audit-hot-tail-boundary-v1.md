# HeadA Low-Price YES 数据审计 + hot-tail 机制边界 v1

Generated: 2026-07-04
Scope: HeadA `forecast_tail_low_price_yes` 独立数据可信度审计 + 新模式挖掘（train ≤6/20 假说生成）+ E1 预注册。
不涉及 METAR reversal / regime-routed NO / tmax distribution。

## One-Line Read

**分母和结算标签核验通过（459/459→476/476 一致），但 hot-tail 的回测边际集中在 book 数据缺失或宽 spread 的行上——两边报价紧的 feasible 行 ROI≈0。分母里还混着 28% 根本不是 hot-tail 表达的"预报向下 bust"票（cold 票，train -15.0%，剔除后 ROI +20.3%→+33.9%、full CI 转不跨 0）。forward 合理预期：命中率 11-14%、ROI 中心 +10~+20%（maker，含 cold）、上限 +37.6%（剔 cold + 全真）、下限 ≈0（stale-quote 幻觉）。全部为 train 事后切片，由 fresh forward 裁决，本轮不改 live selector（理由见 §9）。**

> 术语 `dist`（票距预报几格、正=hot-tail）见下方"术语"节；forward 情景模拟见 §7；剔 cold 决策全表见 §8；不改 live 的三条理由见 §9。

```text
significance=NA（本文全部为 train 事后切片，只作假说生成）
baseline=NA
forward=NA（E1/E-book 评估窗从 2026-07-04 起）
conclusion=inconclusive（对任何 live 变更）；E1/E-book 预注册为 shadow 假说
```

## 术语：`dist`（bracket 距离）

`dist`（journal 字段 `bracket_dist_br_v1`）= **这张彩票的 bracket 下沿，比决策当时的 forecast 最高温高出几个 bracket 宽度**。

```text
dist = (bracket 下沿温度_°F − forecast_max_°F) / bracket 宽度_°F
换算：C 城 bracket 先 ×9/5+32 转 °F、宽 1.8°F；F 城宽 2.0°F；
      "27+" / "88-89" / "28" 三种 bracket 写法统一取下沿。
```

- **`dist > 0`（hot 票）**：票的温度区间在预报**上方** → 真正"赌天气比预报更热"的 hotter-tail 押注，与 HeadA 论题一致。
- **`dist ≤ 0`（cold 票）**：票的温度区间在预报**内部或下方** → 买 YES 等于赌"预报报高了、实际更凉"，与"forecast tail 被低估"的论题**方向相反**。

例：Busan 2026-07-04 买 bracket 24（=75.2°F），当时 forecast_max 更高，`dist=-0.44` → cold 票，逻辑上与策略自身论题冲突。
`hot_tail_boundary_v1 = (dist > 0)`。

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

## 5. E5 数据债修复状态（2026-07-04 全链路重建，两轮 sync+run_stack）

| 数据债 | 状态 | 结果 |
|---|---|---|
| #1 live 血缘断裂 | **已修** | 根因：`strategy_runtime_orders.DEFAULT_ROOTS` 只扫 `remote_pm_agent`（N100 镜像），Mac 本地 runner 目录和 `live/<strategy>_orders.jsonl` 命名均不被发现。修复：加本地根 `runtime/weather_edge_v1` + `_mac_live_strategy_from_filename` 排除 `live_` 前缀的 cycle journal。重建后 `fact_trades` 出现 15 行 `pm_agent_local_strategy_runtime_low_price_yes_lottery_tiny_live_v1_live` `live_real/filled`（7/02-7/04，含 7/03 maker 挂单成交与 7/04 Manila/Busan/Helsinki 新 fill）；live_real 总数 869→929，coverage gate `gate_pass=true`、`fail_reasons=[]`、DB/cache fill 差异 0 |
| #2 closed-forward payoff 非 canonical | **已修** | 重建的 settlement 链把 6/27、6/28 补到各 517 行/47 城；重验后 **476/476** 分母行标签与 canonical settlement 一致（原 17 行 CLOB payoff 全部被证实正确，closed-forward 数字可信度恢复）。5/18 仍缺：根因是 pm_history 镜像源头无该日、API 回填是 fill 驱动（只补有未结算 fill 的市场）——留作 survivorship 脚注，分母该日行本来就被剔除，不是标签错误 |
| #3 TP 证据窗口 | **已标注** | [take-profit v1](2026-07-03-low-price-yes-take-profit-v1.md) 已加 2026-07-04 data-audit caveat（5 月 78 行无 bid 路径、15 个赢家无路径、TP delta 实为 6 月驱动）+ TP20 overlay 已停用的 superseded 注记 |

E1/E-book telemetry 落地：`low_price_yes_integrated_tail_shadow_v2.py` 新字段经 dry-run 验证
（示例：Busan 7/04 bracket 24 → `bracket_dist_br_v1=-0.444`、`hot_tail_boundary_v1=False`、
`book_state_v1=missing`——当天 live 确实买了 cold 票，tag 如实记录），shadow loop 已重启加载新代码。

## 6. 当前 live 姿态（重申，无变更）

- Entry probe：`low_price_yes_lottery_tiny_live_v1` maker-first（`buy_yes_edge20_ask05_20_maker_first_v1`），
  fee-adjusted edge≥0.15、fresh-book cushion、$≈1/票。
- Exit：hold-to-settlement。TP20 exit overlay 已于 2026-07-03 15:21Z 停用并撤单
  （`disable_tp20_exit_overlay`，依据 sizing-stop v1 live-like replay -9.1%）。
- 本文不改 selector、不改 sizing、不 size-up；E1/E-book 只是 shadow 字段。

## 7. Forward 预期：命中率与 ROI 情景模拟

问题：这个彩票的 forward ROI / 命中率合理能到什么程度？方法：用 train hot 分母（275 行/44 天，6.2 票/天，
avg ask 10.5c）做 **day-block 重采样**（保留同日多城相关性），fixed 8 shares，模拟 20,000 个"15 个活跃日"
的 forward 窗口，在四种"边际真实性"假设下看 ROI 与命中率分布。fee 用官方 `shares*0.05*p*(1-p)`。

### 7.1 命中率

| 情景 | 中位命中率 | 含义 |
|---|---:|---|
| S0 无边际（p=ask） | 10.3% | 地板：市场定价就是对的 |
| S2 边际半真（lift ÷2） | 12.4% | train lift 打五折 |
| S3 train hot 完整重现 | **14.4%** | 上限：+38% 边际全真 |
| 现 live selector（含 cold） | 12.8% | 今天实际在跑的口径 |

**命中率合理区间 11-14%，地板 10.4%。** 对 10.5c 均价，只需 3-4pp 超额命中即盈利。样本要求高的根源：
90 张票的命中率标准误 ±3.2pp，单个 15 天窗口几乎分不清"半真"和"无边际"。

### 7.2 ROI（15 活跃日窗口分布，fixed 8 shares）

| 情景 | 中位 ROI | P5..P95 | P(窗口亏损) |
|---|---:|---|---:|
| S3 边际全真，maker 成交 | **+37.6%** | [-14.5%, +99.1%] | 12% |
| S3 + taker fee | +33.0% | [-19.1%, +95.3%] | 16% |
| S3 + taker fee + 1c slip | +21.3% | [-25.9%, +77.7%] | 24% |
| S2 边际半真，maker | +17.8% | [-32.5%, +75.1%] | 28% |
| S1 幻觉假设（只 feasible 可成交） | **-0.9%** | [-44.2%, +50.2%] | 51% |
| S0 无边际 + taker fee | -5.5% | [-51.1%, +46.5%] | 57% |
| 现 selector（含 cold），maker | +22.7% | [-25.6%, +82.1%] | 23% |

三个读数：

1. **中心预期取决于 book-state 假说（§2.4）怎么裁决**——这是最大分岔。边际真实（注意力机制）→ forward 合理 **+20~+35%**（maker）；stale-quote 幻觉 → 真实宇宙是 feasible 行 → **≈0 甚至小负**。当前中心估计放两者之间偏正（**S2 附近：+10~+20%、命中 12-13%**），依据是 7/02-04 的 live 成交是部分证据（6 张 taker 单在挂出价成交、7/03 maker 挂单成交 5 张，说明 ask 至少部分可吃）。
2. **执行方式值 ~17pt**：taker fee 吃 ~5pt，+1c slippage 再吃 ~12pt。maker-first 是这策略从"勉强正"到"值得跑"的分界线，因此 maker fill 率是最重要实测量。
3. **剔 cold 值 ~10-15pt**：现 selector +22.7% vs hot-only +37.6%（同 S3 口径）。见 §8。

### 7.3 方差比均值重要：别用短窗口 PnL 下判断

train hot 单日形态：**41% 活跃日亏钱、34% 活跃日一张不中**，中位盈利日 +28%。即便边际全真，15 天窗口仍
12-24% 概率整体为负；无边际时也有 43% 概率为正。**一个 15 天窗口的正负号只有约 2:1 的证据强度**——
所以裁决靠预注册 date-block CI + book-state 成交率 + top-trade-removed，不是看两周赚没赚钱。
$1 票、6 票/天、日成本 ~$5，S2 情形日均 PnL ~$0.5-1：**这阶段买的是"机制是否成立"的信息，不是收入。**

## 8. 剔除 cold 票（dist≤0）的完整决策表

口径：fixed 8 shares，官方 taker fee 已扣，date-block bootstrap CI。

| 窗口 | 口径 | 行数/天数 | 票/天 | 命中率 | avg ask | ROI | date-block CI |
|---|---|---:|---:|---:|---:|---:|---|
| **TRAIN ≤6/20（可信）** | 现 selector（含 cold） | 383/44 | 8.7 | 13.1% | 10.5c | +20.3% | [-14.8%, +59.7%] |
| | 剔 cold（=仅 dist>0） | 275/44 | **6.2** | **14.5%** | 10.5c | **+33.9%** | [-2.4%, +76.5%] |
| | 被剔的 cold 单独看 | 108/41 | 2.6 | 9.3% | 10.4c | -15.0% | [-74.1%, +54.3%] |
| RECENT 6/21-30（burned，仅方向） | 现 selector | 93/9 | 10.3 | 16.1% | 10.7c | +46.8% | [-4.9%, +110.8%] |
| | 剔 cold | 58/9 | 6.4 | 17.2% | 9.9c | +69.7% | [+17.5%, +141.1%] |
| FULL 5/06-6/30 | 现 selector | 476/53 | 9.0 | 13.7% | 10.5c | +25.5% | [-4.7%, +59.4%] |
| | 剔 cold | 333/53 | 6.3 | 15.0% | 10.4c | +39.9% | **[+8.2%, +76.0%]** |

**三个决策数（剔除 dist≤0 的影响，train 口径）：**
- 订单量：8.7 → 6.2 票/天，**少 28%**（全窗 -30%，recent -38%）。
- 命中率：13.1% → 14.5%，**+1.4pp**。
- ROI：+20.3% → +33.9%，**+13.6pt**；且这是唯一让 CI 从跨 0 收到不跨 0 的切法（train [-2.4%,+76.5%] 压线，**full [+8.2%,+76.0%] 已清楚不跨 0**）。所有其他切法（source-aware / station-bias / p_cal）都做不到这点。

**副作用 / regret 检查：** train 108 张 cold 里 10 个赢家（分散在 Chicago/Jeddah/NYC/Atlanta/SaoPaulo/LA/MexicoCity/Houston，
dist ∈ [-0.9, -0.05] 全是边缘案例，无城市或日期集中），cold 子集净亏 -$13.44。**最大单日跌幅完全不变**
（-104.6% vs -104.6%，同日全灭），说明剔 cold 不改尾部风险，只去结构性拖累。**losing-day 从 50% 降到 41%。**

## 9. 决策与理由（为什么现在不改 live selector）

结论：**dist≤0 剔除、dist>0 切 selector 都不现在动 live，两条统一走 W3 forward 门。** 三条理由：

1. **全部是 train 事后切片，不是 forward 验证过的规则。** `dist>0` 与被推翻的 v3 source-aware、
   station-bias 是同一类证据（CLAUDE.md §4：事后切片只定假说、不定规则）。
2. **同一策略线三天前刚翻车。** TP20 止盈 backtest（max-bid touch 计价）+96%，7/03 上 live，
   同日 live-like replay（resting sell）测出真实 **-9.1%**，当场撤单停用。`dist>0` 现在的证据强度
   ≤ TP20 当时（TP20 有 476 行全窗多子窗），不该更激进。
3. **dist>0 的边际还没拆干净。** §2.4：hot 子集里 feasible book 行 ROI≈0，边际全在宽 spread/缺 book 行。
   现在收紧到只买 dist>0，可能把仓位更集中压进"是否 stale quote"未裁决的那一撮——这比不动更差。

**唯一可考虑现在动的是 dist≤0 剔除**（不是加新 alpha，是修策略自身的自相矛盾：买 YES 却押"预报报高"）：
自由度 1（一个符号）、是纯减法不增加 book 存疑侧的集中度、下行风险不变、无大赢家 regret、full CI 不跨 0。
但因用户要求两条统一走 forward 门，**当前维持不动，等 ≥12 个已结算活跃日 + book_state 成交率数据后一并裁决。**
（如后续决定先剔 cold，仅需在 selector 加 `forecast_max_f` 可得时的 `dist>0` 硬判断，`dist` 不可得时保守保留，
是低风险工程改动，不涉及阈值调参。）

## 复核 artifacts

- 本文全部数字可从 `generated/low_price_yes_integrated_tail_v2/enriched_rows.csv` +
  `fact_signal_candidates`（forecast_max_f/unit/yes_spread/yes_depth_ask_5c，按 candidate_id join）+
  `settlement_outcomes`（win 复核与 winner bracket）+
  `generated/low_price_yes_sizing_stop_v1/replay_rows.csv`（路径覆盖）逐行复算。
- bracket 换算：C 城 `low_f = low*9/5+32`，宽 1.8°F；F 城宽 2.0°F；`27+`/`88-89`/`28` 三种格式取下沿。
