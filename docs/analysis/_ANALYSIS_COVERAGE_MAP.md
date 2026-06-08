# 量化分析体系覆盖图 —— 你有哪几环、缺哪几环

> 量化专家视角的体检。**核心发现：你的 60 个分析脚本几乎全挤在「第 1 环（描述性 PnL 切片）」，
> 一个完整的策略验证体系有 8 环，你大概只覆盖了 2.5 环。** 而且——缺的环里有一半，
> 所需数据字段（`best_ask` / `spread` / `fill_price` / `counterfactual_pnl`）**在 `fact_trades` 里早就有了**，
> 你只是没建分析。数据在，环没建。

验证（grep 全 60 脚本，命中数）：显著性/Sharpe=0，IC/rank=0，CRPS/log-loss/reliability=0，
spread/slippage/adverse=0，capacity/depth=0，correlation/portfolio=0，counterfactual/fill-vs-unfill=0，baseline/null=0。

---

## 八环总览

| # | 环 | 回答什么 | 你现在 | 一句话缺口 |
|---|---|---|:---:|---|
| 1 | 描述性绩效切片 | 谁赚谁亏（city/side/model/timing） | ✅ 充分 | 60 脚本都在这；过度了 |
| 2 | **统计推断** | 这盈利是真的还是噪声 | ❌ 0 | 没有 CI / Sharpe / 多重检验 / 有效样本 |
| 3 | 信号质量（判别 vs 校准）| 信号能不能排序、能不能定价 | ⚠ 半环 | 只有 Brier(校准)，**没 IC(判别)** |
| 4 | 概率分布评估 | 模型作为"分布"准不准 | ⚠ 半环 | 只有逐档 binary Brier，**没 CRPS/log-loss** |
| 5 | **执行微结构** | 扣点差/逆向选择后还赚吗 | ❌ 0 | 有 `best_ask/spread/fill_price` 却没分析 |
| 6 | 容量 / 冲击 | 能做多大、吃不吃穿盘口 | ❌ 0 | $5 能赚 ≠ $50 能赚 |
| 7 | 组合 / 相关性 | 同日多城下注是不是一个赌注 | ❌ 0 | 天气空间相关，风险被低估 |
| 8 | 基准 / 反事实 | 和"无脑/随机"比有没有 skill | ❌ 0 | 有 `counterfactual_pnl` 却没用 |

> **你缺的不是某个分析，是整个右半边**：第 2 环（是不是真的）+ 第 5/6 环（赚不赚得到、做不做得大）。
> **这两环正是把"回测"变成"策略"的环。** 没有它们，第 1 环做得再多也只是描述历史，不能外推。

---

## 逐环详解（含怎么补 + 用哪些已有字段）

### 环1 · 描述性绩效切片 — ✅ 你有（且过度）
- **现状**：`weather_live_*` / `performance-*` / `period_slice` / `city_model_downgrade` 等，by date/city/side/model 全切过。
- **问题**：不是不够，是**只有这一环**，且大量切片是在同一小样本上反复切（多重检验，见环2）。
- **动作**：不要再加切片维度；把现有切片**配上环2 的显著性**再下结论。

### 环2 · 统计推断 — ❌ 全缺（最该先补）
- **回答**：+11% / −12% 这些数，置信区间多宽？能不能拒绝"=0"？几十个变体里选出最优，校正后还显著吗？
- **为什么决定成败**：你的日 PnL 摆动 ±$70（见 model_vs_market §3），这种方差下 9 天 ROI 的 CI 极宽，
  很可能跨 0。**不报 CI 的 ROI 等于没说。**
- **怎么补**（脚本草图）：
  - 每个切片的 ROI/PnL 做 **bootstrap CI**（按 trade 重抽 5000 次）。
  - **Sharpe / Sortino**（按日或按 bet），年化并报标准误。
  - **有效样本量**：同一 city-day-bracket 在多 cycle 重复 = 相关下注，用聚类/block bootstrap，别用 naive n。
  - **Deflated Sharpe Ratio**（López de Prado）：按试过的配置数惩罚，回答"选出的最优是不是多重检验幻觉"。
- **已有字段**：`pnl_usd_at_fill`, `target_date`, `city`, `bracket`, `run_id`（聚类键）。

### 环3 · 信号质量：判别力 vs 校准 — ⚠ 只做了校准
- **关键区分**：**校准**（Brier，概率值准不准）≠ **判别**（IC/rank，排序对不对）。
  一个信号可以校准很差但**排序很好**——那它不能直接当概率，但**能用来 sizing/选边**，照样可交易。
  你只算了 Brier（说模型校准差），**从没算 IC**，所以你不知道模型有没有"排序能力"这条可救的命。
- **怎么补**：
  - **IC** = `spearman(model_edge, realized_outcome)`，按 city/lead-time 分组。
  - **分位单调性**：把 model_edge 分 5 档，看实际命中率是否单调。单调=有判别力。
  - **Rank-IC 时间序列 + ICIR**（IC 均值/标准差）。
- **含义**：若 IC≈0 → 模型连排序都不行，H_A 彻底死透；若 IC>0 但 Brier 差 → 模型该用作 **sizing 信号**而非概率，这是一条你**没探索过**的路。

### 环4 · 概率分布评估 — ⚠ 只做了逐档 binary
- **问题**：天气本质是**温度的连续分布**，你却把每个档位当独立 binary 算 Brier，**丢掉了序数结构**
  （预测 22°C 实际 23°C，比预测 22°C 实际 30°C 应该罚得轻，binary Brier 看不出这个）。
- **怎么补**：
  - **CRPS**（连续排序概率分），对整条温度分布评分——这才是天气预测的标准指标。
  - **Murphy 分解**：Brier = 不确定性 − 分辨率 + 可靠性，看模型差在"没分辨率"还是"没校准"。
  - **PIT / 覆盖度**：预测分布的分位覆盖对不对（模型是不是过窄/过宽）。
- **已有字段**：bracket（含温度区间）, model 各档概率, settlement。

### 环5 · 执行微结构 — ❌ 全缺（与环2 并列最关键）
- **回答**：你是 maker-only，真实成交价是对手 ask 不是 mid；挂单只在行情穿过你时成交（逆向选择）。
  扣掉这两样，环1 的 PnL 还剩多少？
- **怎么补**（你字段都有！）：
  - **点差分布** by 价桶：`spread` / `best_ask` 已在 fact 表，直接 groupby 出来。
  - **滑点**：`fill_price − mid` / `fill_price − best_ask`，看成交质量。
  - **逆向选择**：`counterfactual_pnl`（成交）vs `counterfactual_pnl_best`（理论最优）的差，
    以及 **fill vs unfill 对比**——成交的那批是不是系统性比没成交的差。
  - **成交率 vs edge**：高 edge 的单是不是更难成交（被人抢）。
- **已有字段**：`best_ask`, `spread`, `fill_price`, `counterfactual_pnl`, `counterfactual_pnl_best`（全都有，零脚本用）。
- 配套脚本：`research_executable_edge.py`（已起草）属于本环。

### 环6 · 容量 / 冲击 — ❌ 全缺
- **回答**：现在 $5/单能赚，放大到 $50/$200 还赚吗？盘口多深？你的单会不会自己把 edge 吃掉？
- **为什么重要**：决定这个策略**值不值得做**。若容量只有每天 $200，再大 edge 也是玩具。
- **怎么补**：
  - 用 orderbook L2 深度算：在每个价桶，吃掉多少 size 会让价格滑动 X%。
  - **edge vs size 曲线**：把可成交 edge 当 notional 的函数画出来，找容量上限。
  - 历史 fill size 分布 vs 盘口深度占比（你的单占簿子多少）。
- **已有字段**：orderbook snapshot 的 `asks[].size`/`bids[].size`（658MB 镜像里）。

### 环7 · 组合 / 相关性 — ❌ 全缺
- **回答**：同一天 Tokyo/Shanghai/Seoul 都买 NO，看起来是 3 个赌注，**其实高度相关**（同一天东亚天气同涨同跌）。
  你的"分散"是假分散，真实组合方差被严重低估。
- **为什么重要**：决定真实回撤和 sizing。低估相关 → 仓位过大 → 一个区域性天气黑天鹅团灭。
- **怎么补**：
  - 按 (date) 算各城 outcome 的**相关矩阵**；按地理区域聚类。
  - **有效独立下注数** = N / (1 + (N−1)·ρ̄)；用它替代 naive N 重算所有显著性。
  - 单因子检验：PnL 是不是其实是"全市场 long NO"这一个因子的暴露。
- **已有字段**：`city`, `target_date`, `final_price`（算跨城相关）。

### 环8 · 基准 / 零模型 + 反事实 — ❌ 全缺
- **回答**：你的 PnL 和"无脑在同价位买 NO""买市场""随机"比，有没有超额？没有基准 = 无法归因 skill。
- **怎么补**：
  - **dumb baselines**：always-buy-NO@price-bucket / buy-market / random，跑同窗口对照。
  - **favorite-longshot 零模型**：一个零技能的结构偏差收割者能赚多少（= H_B 的基准，见 `market_structure_edge.md`）。
  - **fill vs unfill 反事实**：用 `counterfactual_pnl` 比较成交/未成交候选，量化成交选择偏差。
- **已有字段**：`counterfactual_pnl`, `counterfactual_pnl_best`（早就有，零脚本用）。

---

## 补环的优先级（投入产出比排序）

| 顺序 | 补哪环 | 为什么先做它 | 成本 |
|---|---|---|---|
| 1 | **环5 执行微结构** | 字段全有；直接决定"+11% 是不是幻觉"；maker 策略的命门 | 低（数据现成）|
| 2 | **环2 统计推断** | 给现有所有切片配 CI/Sharpe，一次性让 60 篇报告"可信化" | 低 |
| 3 | **环8 基准/反事实** | `counterfactual_pnl` 现成；分清 skill vs base-rate | 低 |
| 4 | 环3 信号判别(IC) | 可能救活模型作为 sizing 信号（H_A 的 plan B）| 中 |
| 5 | 环7 相关性 | 修正"假分散"，让组合风险真实 | 中 |
| 6 | 环6 容量 | 决定值不值得做大；但要先有 edge 才谈容量 | 中 |
| 7 | 环4 CRPS/分布 | 模型评估更专业，但 H_A 已基本死，优先级低 | 中 |

> **一句话路线**：先用环5+环2+环8 三把低成本的刀，回答"现在这套到底是不是幻觉"；
> 若环5 显示扣点差后还有结构 edge（指向 H_B）→ 再上环6/7 做容量和组合；
> 若环3 的 IC>0 → 模型还有 sizing 的残值可救（H_A plan B）。

## 与主线骨架的关系
本图是 `WEATHER_ARCHITECTURE_SPINE.md` 中 [6] 评估层的"维度标准"：
**每篇 living doc 都应自检覆盖了哪几环、缺哪几环**，而不是只堆第 1 环的切片