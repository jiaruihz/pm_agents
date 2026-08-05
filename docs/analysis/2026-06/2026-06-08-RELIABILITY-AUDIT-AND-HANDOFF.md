# 天气策略可靠性审计 + 交接文档

> 生成于 2026-06-08。本文是一份**自包含交接件**：把「现有天气策略到底是猜硬币还是真能赚钱」的
> 正式判断、决定性实验脚本、结构整改、交接清单收在一篇里，供下一个 agent 迁移后续执行。
> **未 git push**。配套两个脚本（见 §6 manifest）需随本文一起迁移。
>
> 所有数字均引自本仓库现有文档（`WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md`、
> `docs/analysis/blender_shadow.md` 汇总的 blender 历史证据等），非本文新测。

---

## 0. 一句话结论（TL;DR）

**以你现在手上的证据，这套策略在统计上与掷硬币不可区分——「零 edge」这个原假设没有被拒绝。**
但这**不等于**「没有 edge 可找」。有一个具体的、有市场微结构理论支撑的真实（但很小）edge 可能存在，
而判定它生死的**两个决定性实验你从没跑过**。

> 判决：**未被证实，也未被证伪；你一直在检验错误的那个假设（H_A 模型 alpha），
> 而真正可能赚钱的假设（H_B 结构性错价）没测过。**

把它从「猜硬币」变成「已证实 edge」，只需要本文 §4 的 Step 1 / Step 2 两刀，一周内出结果。

---

## 1. 四个维度逐项可靠性审计

> 方法纪律：每个维度先列**观测事实**（不解释），再**正反双向**等量举证，最后**判定**。
> 这是为了对冲「编一个连贯叙事、只引用支持结论的证据」的确认偏差。

### 1.1 策略制定（formulation）

**观测：** 现行 live = 0.25–0.75 价带、`edge = model_p_yes − market_price ≥ 0.10`、
maker-only post-only、$5 notional、两实例（25-75 全局 / side-band）、城市 allowlist；
利润几乎全来自 BUY_NO。

**支持「可靠」：** 价带限制（避开极端 longshot）、maker-only（不付 taker 点差）、小额、
双侧分桶——这些都是审慎的工程姿态，没有明显的爆仓式设计。

**支持「不可靠」（更重）：**
- 下单门 `edge ≥ 0.10` 是用 **model_p_yes** 算的，而 model_p_yes 比市场差（§1.2）。
  「模型与市场分歧最大」= **模型最可能错**的地方。**这个门在系统性挑选你最差的信号。**
  BUY_NO 还能赚是**绕过**了这个门（靠 NO 的结构/base rate），不是因为它。
- 形态是 maker（提供流动性），但**度量和门控却是 taker-alpha 的口径**（edge、胜率、ROI）。
  策略在"我预测得更准"和"我做市收点差+收 NO 结构偏差"两个**完全不同的生意**之间混淆。
  证据指向后者，但全套指标为前者而建。

**判定：⚠ 部分不可靠。** 工程姿态 OK，但**核心 alpha 假设（模型门）方向性错误**，
且生意定性混淆。这是「怎么调都没用」的第一个结构性原因。

### 1.2 模型选择（model selection）

**观测：** 多源天气集合（ECMWF/GFS/JMA/HRRR/ICON/AROME）→ 模拟温度分布 → 各档概率；
线上用 `0.3·model + 0.7·market` 凸组合。校准实测：
raw model Brier **0.2226 vs 市场 0.1715（差 +29.8%）**（time-split）；LOO 差 +16.1%；
ensemble time-split 0.1700 vs 0.1715（**赢 0.9%**），LOO 0.1724 vs 0.1794（赢 3.9%）。

**支持「可靠」：** ensemble 在 time-split 和 LOO 两种 holdout 下方向一致赢市场；
isotonic/platt 也都把模型往市场拉近——说明流程自洽、没有 look-ahead 泄漏。

**支持「不可靠」（更重）：**
- raw 模型在干净 OOS 上**输给市场 16–30% Brier**。这是一个干脆、可信的结论：
  **模型对市场价格没有预测增量。**
- 那 0.9% 的 ensemble「胜出」是真的吗？time-split test n≈315，Brier≈0.17，
  单点方差量级下 Brier 的标准误大致 ~0.01–0.02。**0.0015 的差 ≈ 0.1 个标准误，几乎肯定是噪声。**
  LOO 的 3.9% 更大，但 LOO 对小样本城市加权敏感。诚实读法：**ensemble 并没有令人信服地赢市场。**
- 你自己文档的话：「市场已经吃掉大部分信息，模型只剩 30% 补充权重」——
  翻译过来就是：**模型≈市场价格的一个带噪声的副本。**

**判定：❌ 作为 alpha 来源不可靠。** 模型本质上在重新推导市场价。H_A（模型 alpha）这条线，
就这套数据而言，是**猜硬币**。

### 1.3 回测算法（backtest algorithm）

**观测：** paper/sim PnL 用 snapshot price 成交；headline「剔 6 城 + ban T28 → +$39.53」
在引发亏损的**同一窗口**上计算；候选→成交漏斗 0.91%；近-binary 结算口径曾被污染；
`decision_window_missing ≈ 44%`；执行层可成交 edge **从未测过**（5 条 kill 判据里唯一未验的一条）。

**支持「可靠」：** 数据管道有 CLOB fill 对账 gate（`gate_pass`）、口径文档化、
walkforward 脚本存在、near-binary 勘误被发现——说明团队**有**严谨意识。

**支持「不可靠」（压倒性）：**
- **成交价≠可成交价。** 对 maker-only 尤其致命：你只在行情穿过你挂单时成交（逆向选择），
  成交样本系统性偏不利，而回测假设你按 mid 成交 → **系统性高估 edge。**
- **后验修补当回测。** 「剔 6 城 + T28」是看到六月后亏损**之后**做的切割，
  在同窗口上算 +$128 改善——这不是回测，是**拟合**。walkforward 也被污染（切割决策含全窗口信息）。
- **多重检验失控。** 几十个变体/配置打同一个 9 天 live 窗口 + 重叠 paper 窗口，
  无 family-wise 校正、无 deflated 指标。**样本内必然能找到 +$128，但不前瞻复现。**
- 44% 决策 snapshot 缺失 → 反事实分析半盲；结算曾被污染 → 用于结论的账本本身有质量问题。

**判定：❌ 当前不可靠用于 go-live 决策。** 同时**高估 edge**（可成交价、幸存者）
和**把样本内拟合当验证**（后验切割）。这是瓶颈最大的洞，也是「越研究越像过拟合」的根。

### 1.4 分析维度（analysis dimensions）

**观测：** 头条指标是 BUY_NO 胜率 73.7%；尝试在 n=1–4 fills/城做城市级 ROI 排序；
Brier 为模型质量指标（全局，不按交易价带 conditional）。

**支持「可靠」：** 维度丰富（city/side/price-bucket/timing/model-version），
tail-sensitivity、集中度这些 kill 判据**有写进文档**。

**支持「不可靠」：**
- **胜率被当 edge。** 多档互斥市场里 NO 有天然高 base rate，73.7% 可能**全部**由 base rate 解释。
  缺一个「同价位无脑买 NO」的 dumb baseline 做对照——没有它，分不清是 skill 还是 base rate。
- **样本不足却照做城市决策。** 文档明说「67 fills 不足以做城市级排序」，然后又按城市级 PnL 砍 6 城。
  caveat 与 action 自相矛盾。
- **缺最关键的 model-free 维度**：市场自身校准（favorite-longshot）——唯一能直接测真 edge 的维度，
  以及**执行/做市经济学维度**（逆向选择、库存）。现有维度几乎全为 H_A 服务，漏掉了能验 H_B 的维度。

**判定：⚠ 维度丰富但瞄错靶。** 大量精力测「模型对不对」，没测「市场错没错、扣点差还活不活」。

---

## 2. 「猜硬币 vs 真能赚钱」的正式判断

### 2.1 统计上：现在无法拒绝「零 edge」

67 fills / 9 天 / +$31.72，但单笔 Miami +$14.23 占 45%、top-5 city×side 之和 > 全部净额
（即**其余加起来是负的**）。二元高方差 payoff 下，+11% ROI 的置信区间极宽。
粗略地（Step 2A 脚本会精确算）：均值/标准误的 t 统计量大概在 ~1.5 量级，
**|t| < 1.96 → 无法在 95% 置信上拒绝「真实 edge = 0」**。

> 这就是「猜硬币」的精确含义：**不是说一定没 edge，而是现有样本无法把它和零区分开。**

### 2.2 两个被混为一谈的假设

| 假设 | 内容 | 现状 |
|---|---|---|
| **H_A** | 「我的天气模型能识别错价档位」 | **已证伪**：模型 Brier 比市场差，无预测增量 |
| **H_B** | 「市场有结构性 favorite-longshot 偏差，做市 fade 它就能赚，与模型无关」 | **从没干净测过**，但与所有现象吻合 |

H_B 与全部证据自洽：NO 赚 / YES 不赚 / 模型无预测力 / 利润集中在 NO 侧。
而且 H_B 有**理论先验**——小众预测市场（散户主导、天气档位）普遍存在 favorite-longshot 偏差，
散户高估 YES longshot，系统性买 NO 可结构性收割，**不需要好模型**。

### 2.3 判决与现实预期

- **现状 = 猜硬币**：H_A 死了，H_B 没测，live 盈利统计上≈噪声。
- **但很可能有一个真实、很小的 edge**（H_B，做市收结构偏差），你只是**一直在优化错的对象**
  （用 ensemble 把概率往市场收缩，恰恰在**稀释**你唯一可能的真 edge）。
- **现实天花板要降级预期**：即便 H_B 成立，模型在市场之外≈0 增量，结构偏差在有流动性的
  天气档位上大概也就每笔几个百分点。所以真实形态是「**小的、容量受限、执行敏感的做市 edge**」，
  **不是「一个会印钱的模型」**。你可能一直在追一个不存在的大 model-alpha，
  而真正存在的小结构 edge 需要完全不同的优化（执行/点差/容量，而非模型精度）。

### 2.4 什么能把判决从「猜硬币」翻成「已证实」

只有两件事，按序：
1. **Step 1**：在**全部已结算样本**（不是 67 笔）上证明市场自身有显著、前瞻稳定的校准 gap。
2. **Step 2**：证明这个 gap 扣掉**真实点差**后仍 > 0。
两个都 Go，才谈得上「可能真能赚钱」，且必须**冻结配置、只前瞻评估**（Step 3）。

---

## 3. 决定性实验（脚本已写好，待迁移后在有数据的机器上跑）

### Step 1 — 市场结构性错价检验（H_B，model-free）

**脚本：** `scripts/analysis/research_market_structural_edge.py`（已写、已 py_compile + 纯函数自测）

**做什么：** 不用任何模型，只用「市场隐含价格 vs 实际结算频率」：
- 按 market_price 分桶，算每桶实际 YES 频率 + Wilson CI + `gap=实际−隐含` + gap 是否显著；
- 算每桶「无脑买 NO / 买 YES」的 model-free EV/$1；
- 按 lead-time（hours_to_settle）切片，看 gap 是否随提前量稳定（**直接回答 T>28 该不该 ban
  是结构性还是后验**）；
- **H_A 干净对照**：每个价桶内按 model_p_yes 高/低拆两组比实际频率——
  `model_lift≈0` 即模型在价格之外无信息（证伪 H_A、支持 H_B）；
- **预注册前瞻检验**：train 上选 +EV 价桶 → 冻结 → test 上算真实 ROI，对照「全价带无脑买 NO」。

**跑：**
```bash
.venv/bin/python scripts/analysis/research_market_structural_edge.py \
  --db-path runtime/weather.db \
  --out-json docs/analysis/2026-06/2026-06-08-market-structural-edge.json \
  --out-md   docs/analysis/2026-06/2026-06-08-market-structural-edge.md
```

**判读 / Go-No-Go：**
- **No-Go**：gap 普遍不显著、前瞻 ROI≈0 或转负 → 市场有效，无结构 edge，
  live +11% 归因运气/逆向选择 → **停止加注，重想方向**。
- **Go**：某价带 gap 显著 + 前瞻 ROI 明显 > 全价带 baseline → 有 model-free 结构 edge → 进 Step 2。

### Step 2 — 执行现实检验（扣真实点差）

**脚本：** `scripts/analysis/research_executable_edge.py`（已写、已 py_compile）

**做什么（两条独立证据）：**
- **2A 已成交滑点审计**（最可信，只用 fact_trades 真实成交）：声称 edge 是否真的预测结算 PnL？
  集中度（去 top1/top5 后是否还正）、**零-edge t 检验**（直接量化「是不是猜硬币」）、
  BUY_NO/YES 真实战绩。**列名运行时自省，不硬编码。**
- **2B orderbook 可成交 edge**：对 Step 1 标出的价桶用决策时 best_ask 重算 edge，
  看点差是否吃光。复用项目已验证的 `edge_orderbook_source`。

**跑（2A 可直接跑；2B 需先备 token-map，见 §6 VERIFY-2）：**
```bash
.venv/bin/python scripts/analysis/research_executable_edge.py \
  --db-path runtime/weather.db \
  --orderbook-glob 'runtime/weather_edge_v1/market_data/orderbook_snapshots/**/*.jsonl*' \
  --token-map runtime/weather_edge_v1/market_data/token_map.json \
  --out-json docs/analysis/2026-06/2026-06-08-executable-edge.json
```

**判读：** 2A 若 `reject_zero_at_95pct=false` 且 `edge_predicts_pnl≈0` → H_A 执行端坐实失效；
2B 若 `edge_destroyed_by_spread=true` → edge 真但不可成交，问题在执行（要做 maker 收点差，而非 taker）。

### Step 3 — 预注册前瞻（只有 1&2 都 Go 才做）

冻结「价带 + 方向 + size 上限」为代码常量，选一个**不再回看、不再调参**的前瞻窗口跑。
**不再按实现 PnL 砍城市。** 这一步同时治掉「后验修补」的结构病。

---

## 4. 研究主线重定向（一句话）

> 把问题从「**怎么让我的天气模型更准**」换成
> 「**天气档位市场有没有一个 model-free 的结构性错价，且它扛得过真实点差**」——
> 用全部已结算样本、预注册、留前瞻窗口来回答。

并立刻**停止**：城市池增删、timing 窗口微调、模型变体（v1/v2/maker_queue/blend）搜索——
这些都是在 67 笔小样本上挖噪声的过拟合跑步机。

---

## 5. 结构性整改（与研究方法同源）

| 现象 | 病理 | 整改 |
|---|---|---|
| 52 个 analysis JSON 进 git（~29.7 万行，单文件 7.4 万行） | 鼓励无限参数 sweep | `docs/analysis/**/*.json` 移出 git（gitignore + DVC/对象存储），git 只留 md 结论 |
| 143 篇 md，14 篇标 snapshot/退役 | 口径漂移、无 single source of truth | md 分 `_scratch`（不进 git）/ 沉淀（每主题一篇 living doc） |
| 无 thesis living doc | 主线无载体、反复丢失 | 建 `WEATHER_STRATEGY_THESIS.md`：(a)当前 edge 假设 (b)edge 大小+样本 (c)5 条 kill 判据状态 |
| 后验过滤写进 live config | 样本内修补当进步 | 冻结预注册配置 + 只前瞻评估（Step 3） |
| 本机 `pmm/`、`agents/` 遗留目录（未跟踪） | source-of-truth 噪声 | 本机删除/移出工作区（仓库 source of truth 只有 `src/`） |

---

## 6. 交接清单（for 下一个 agent）

### 6.1 随本文一起迁移的文件（manifest）
```
docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md   ← 本文
scripts/analysis/research_market_structural_edge.py                 ← Step 1（已自测）
scripts/analysis/research_executable_edge.py                        ← Step 2（已 compile）
```
两个脚本只依赖 `pandas`/`numpy`（Step 2B 还会 import 仓库内 `edge_orderbook_source`）。

### 6.2 跑之前必须 VERIFY 的 schema 项
- **VERIFY-1（Step 2A）**：`PRAGMA table_info(fact_trades)` 确认 `pick()` 选中的列名，
  尤其可选列 `fill_price / model_p_yes / market_price / edge / notional`；命名不同就补进候选。
- **VERIFY-2（Step 2B）**：`token_id ↔ (condition_id,bracket,outcome)` 映射来源。
  仓库 `apply_orderbook_prices()` 用 `bracket['yes_token_id']/['no_token_id']`；
  确认它在 signals/markets 表还是 event JSON，导出成 `{condition_id|bracket:{yes,no}}` 传 `--token-map`。
- **VERIFY-3（Step 2B）**：决策时刻对齐——当前取每 token 最新 snapshot，
  严格应取「signal 决策时刻对应的 snapshot」（`ts ≤ decision_ts`），否则用了未来盘口。
- Step 1 已对齐 `calibrate_weather_probability.py` 的 loader，schema 无需额外校验。

### 6.3 跑的顺序与决策树
```
1. Step 1  → No-Go: 停，重想方向（不是参数）。 Go: 继续。
2. Step 2A → reject_zero=false 且 edge_predicts_pnl≈0：H_A 坐实失效。
   Step 2B → edge_destroyed_by_spread=true：问题在执行，转做市口径。
3. 都 Go → 建 thesis doc + 冻结预注册配置 → Step 3 前瞻。
```

### 6.4 明确不要做（anti-goals）
- 不要再增删城市池 / 微调 timing / 加模型变体来「找更好的回测数字」。
- 不要在 67 笔样本上做城市级 ROI 排序。
- 不要把任何样本内后验切割当成 edge 证据。
- 不要把 ensemble 的 0.9% Brier 改善当成显著 alpha。

---

## 附：本文未做的事（诚实边界）
- 我**无数据**，所有脚本只过了 py_compile + 纯函数自测，**未在真实 DB 上跑过**。
- §2.1 的 t≈1.5 是量级估计，精确值由 Step 2A 在真实 fact_trades 上算。
- Step 2B 的 token-map join 是按仓库文档/模块**推断**的 schema，需 VERIFY-2 确认。
- 本文**未 git push**，按要求留作交接件。
