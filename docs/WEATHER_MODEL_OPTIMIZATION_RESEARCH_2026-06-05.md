# 方向与计划：从"信号"到"可部署 edge" —— 先证实，再建机器

Status: `snapshot`。本文保留 2026-06-05 战略笔记；当前实施入口以 [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md) 和 [WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md) 为准。

> 定稿 2026-06-05。本文档是**战略方向**，不是实施清单。回答一个问题：
> 在最小成本下，按什么顺序做，才能判断 PM 天气盘到底有没有可部署的 edge，
> 并在确认之后才投入精力建组合/执行机器。
>
> **2026-06-05 同步注**（本文档战略框架仍成立，但 3 处数据假设已过时）：
> 1. 盘口数据自 2026-05-19 起已经在采（YES+NO L2 depth，本机已 mirror 658MB），
>    Phase 2 不再需要等数据采集。
> 2. 样本量已从 14 笔推到 67 fills（`trade_class='live_real'`），距 Phase 1
>    通过线 n≥80-100 不远；BUY_NO 73.7% 实盘胜率独立复现。
> 3. "概率模型健全不要改"对算法层成立；但 raw `model_p_yes` vs 市场 Brier 差
>    16-30%（time-split / LOO 双 holdout），应在 Phase 0 内塞
>    `0.3 * model + 0.7 * market` ensemble（零成本免费 alpha，已 Brier validated）。
>
> 详见 [WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md](WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md)
> 与 [WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md](WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md)。

---

## 0. 一句话方向

**当前瓶颈不是单一问题：raw 概率模型有低信噪比 alpha，但 deployable edge 仍未被充分证实。**
因此：**修正最低成本概率层 → 冻结规则 → 积累样本 → 诚实归因 → 只建样本积累真正需要的机器**。
多档位联立 sizing、跨城资金分配、执行微观结构这套，是**正确的最终架构**，但必须
**just-in-time** 在 edge 被证实之后建，不能现在上。

2026-06-05 校准实测修正：不能再说“概率模型已验证健全”。更准确的表述是：
`compute_bracket_probs` 的**分布生成结构自洽**，但 raw `model_p_yes` 单独用在 Brier 上输给市场；
`0.30 * raw_model + 0.70 * market_implied` 稳定小幅击败市场。本文的优化方向应与
[WEATHER_PROBABILITY_MODEL_REVIEW.md](WEATHER_PROBABILITY_MODEL_REVIEW.md) 和
[WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md) 对齐。

---

## 1. 为什么是这个方向（底层逻辑）

### 1.1 已确认的事实（证据锚点）

- **概率生成结构自洽，但 raw 概率不可单独信任**：`pm_edge_compare.py::compute_bracket_probs`
  从**一条统一模拟分布** (`simulated = round(gfs + errors)`) 切出各档概率，
  所以同一 city-day 的 bracket 概率来源是一致的；但 2026-06-05 校准显示 raw model
  Brier 输给 market baseline，必须先做 model×market 融合或 shadow 验证，不能继续把
  `model_p_yes - market_price` 当作完全可信 edge。
- **低成本概率修正确实存在**：`0.30 * model_p_yes + 0.70 * market_implied_p_yes`
  在 time-split 与 leave-one-city-out 两种 holdout 下均小幅击败市场。这不是“重建模型”，
  而是承认市场已经吃掉大部分公开天气信息，只让自有模型贡献剩余 30% 的修正。
- **下注层是逐档独立的**：`edge_backtest.py` 每档单独算 `edge`、单独过阈值，无跨档预算、
  无互斥约束、无组合视角。这是"同城多档不自洽"的真正根因。
- **edge 真但极脆**（来自 `docs/reports/2026-05-07-profit-loss-attribution.md`）：
  - 基线 34 笔结算 ROI +5.9%，**删最赚 1 笔即翻负（−0.9%）**，删 5 笔 −17.4%。
  - BUY_YES 方向 ROI −46%，正收益**只**来自 BUY_NO。
  - 利润集中在 longshot 极端价档（0.05-0.10 / 0.95-0.98），中间价档为负。
  - 主力策略 `pre_t24+t24 | edge>=10% | BUY_NO | exclude_Seoul` 仅 **14 笔结算**。
- **历史盘口数据无买卖盘**：`prices_*.json` 仅单一价序列 `{t, p}`，paper_snapshot 记录仅
  `market_yes_price / last_trade_price`，**无 bid/ask/depth**。→ 可成交 edge 无法回溯验证，
  只能向前采集。
- **成本模型已存在且口径正确**：天气盘 taker fee = `0.05 × p × (1−p) × shares`
  （核对官方文档无误），有效费率 0.5%–2.5%（p=0.5 处峰值 2.5%），不是"5% 砍头"。

### 1.2 由事实推出的方向判断

1. **不能现在建 Kelly/组合机器**：edge 删 1 笔就翻负、样本 14 笔，此时优化"如何把 edge 放大成
   组合"是给未验证的数字做精装修。**过度设计。**
2. **不能继续裸用 raw edge**：raw `model_p_yes` 单独用不是已证实 alpha；最低成本修正是
   `0.30 model + 0.70 market`、per-city raw 黑名单、forecast jump/side flip 观测。
   这些属于 Phase 0 的概率层排雷，不等于提前上复杂模型。
3. **不能再调参数找更高 ROI**：报告里十几个变体就是调参产物，越调越像过拟合。你自己的 Readout
   也说"不要再加模型复杂度"。
4. **真正缺的是"统计上能否证实 edge"**：需要在**冻结后的概率口径和策略规则**下积累样本 + 每周尾部归因。
5. **组合/执行思路本身是对的**，但要**降altitude折叠进正确阶段**：
   - 互斥 partition + 概率自洽 → 折进 Phase 0，作为**数据质量前提**（不自洽的概率会污染归因）。
   - 多档联立 Kelly + 跨城分配 → 推到 Phase 3，**证实 edge 之后再建**。

---

## 2. 分阶段计划（每阶段带 kill 判据）

> 顺序不可颠倒：每个 Gate 能**一票否决**后续，便宜的实验排在前面。

### Phase 0 — 锁问题 + 装数据（约 1 周，多为配置/spec，便宜）

**目的**：把"研究"从"反复调参找好看变体"切换成"冻结规则、诚实积累"。

- **预注册主策略 + 对照**（冻结参数，不再调）：
  - 主：`pre_t24+t24 | ensemble_edge>=阈值 | BUY_NO priority | per-city raw blacklist`
  - 对照 1（旧口径）：`pre_t24+t24 | raw_edge>=10% | BUY_NO | exclude_Seoul`
  - 对照 2（广基线）：`pre_t24+t24 | ensemble_edge>=阈值 | all | all`
- **双写概率字段**：所有新 snapshot / signal 至少保留
  `model_p_yes_raw`、`market_implied_p_yes`、`model_p_yes_ensemble`、`edge_raw`、`edge_ensemble`，
  避免后面归因时分不清是模型变了还是交易规则变了。
- **raw-model 城市黑名单**：Milan / Lucknow / Austin / Beijing 只允许 ensemble-edge 通过；
  如果暂时不能改 live 行为，至少在 paper / shadow 里单独标记。
- **forecast 稳定性观测骨架**：落盘 `forecast_values_hash`、`forecast_max_hour_local`、
  `forecast_jump_f`、`side_flip_count_today`，先观测，后决定是否降 size / shadow。
- **前置写死 kill 判据**（防事后合理化，见 §3）。
- **互斥 partition 化 + 概率自洽校验**：把盘口混合档型（`X+`/`X-`/区间/单点）整理成一组
  真正互斥穷尽的划分，校验模型概率向量加总 ≈ 1。**此阶段只为数据质量，不为 sizing。**
- **落地数据契约**（见 §4）：向前采集必须补齐 order book 字段，否则 Phase 2 无法做。

**通过线**：主策略每日快照在跑、能正确结算；raw/ensemble 概率双写完整；
概率向量加总落在 [0.97, 1.05]；大幅概率跳变能解释为 forecast、market、数据缺口或模型版本变化。

### Phase 1 — 积累 + 归因（约 4–8 周，核心是"等 + 每周检查"）

**目的**：在冻结规则下让样本量长到统计上能说话，看 edge 是否稳定。

- 每日 `paper_snapshot.py` 跑主策略 + 对照，结算入库。
- **每周**重跑归因：by city / side / price-bucket / **tail-sensitivity（删 top-1/5）**。
- 每周比较 raw-edge vs ensemble-edge：Brier、ROI、成交数、BUY_YES/BUY_NO 可靠性、
  side flip 后的收益、per-city 回归城市。
- 跟踪主问题：n 增长时 ROI 是否仍为正？**是否扛得住删 top-5？** BUY_NO 在 05-07 之后的
  **样本外**是否还成立？Beijing 之外是否还有钱？

**通过线（Gate 1）**：主策略 `n_settled ≥ ~80–100` **且** 删 top-5 后 ROI 仍 > 0
**且** 利润不再单一城市/单一 longshot 依赖。
**否决**：n 到位但删 top-5 即翻负 → edge 证伪，停止或换假设（见 §3）。

### Phase 2 — 执行现实（与 Phase 1 并行，依赖 §4 新数据）

**目的**：用真实盘口测"纸面 edge 是否在 ask 价存活"——尤其 longshot 档（相对点差最大，
最可能死在这一步）。

- 用 order book 快照计算：分价桶**真实点差**、**可成交 edge**（BUY_YES: `P − ask`；
  BUY_NO: `P − (1 − bid)`）、**逆向选择**（成交是否集中在价格随后对你不利的时刻）。
- 重点压测 05-07 利润集中的极端价桶：那里纸面 ROI 几千 %，但盘口可能根本吃不到量。

**通过线（Gate 2）**：在利润集中的价桶，扣真实点差后可成交 edge 仍 > 0。
**否决**：edge 只活在 mid、ask 上归零 → "打市场中枢"路线死，转纯结构性/尾部假设或停。

### Phase 3 — 组合构建 + 资金分配（仅当 Phase 1&2 通过才建）

**目的**：此时才知道 edge 真、在哪、且扛得住执行——现在建机器才不是空中楼阁。

- **同城**：互斥 partition 上做多档联立 sizing（简化版 edge 归一化 → 严格 multi-outcome Kelly），
  分数凯利 1/4 起。
- **跨城**：批次分配（等 GFS 周期，把"当前发信号的城"当已知批次）+ 三闸门
  （单城上限 ≤20% 池 / 总部署 ≤75% / 现金储备 ≥25%）+ 超额 pro-rata 缩放。
- **小池子现实**：$200 阶段每周期只打 Top-2/3 城，集中而非撒胡椒面。

**通过线（Gate 3）**：组合层在 Phase 1 数据上回放，ROI/夏普不劣于逐档基线，且无新增失控组合。

### Phase 4 — 小额实盘

全栈跑通后，用最小真实资金验证 realized vs theoretical 的缺口，再谈放大。

---

## 3. 预注册 kill 判据（Phase 0 写死，不准事后改）

| 检查 | 否决线 | 触发动作 |
|---|---|---|
| 尾部敏感性 | n≥80 时删 top-5 后 ROI < 0 | edge 证伪，停主策略 |
| 方向 | BUY_NO 样本外 ROI 转负 | 撤下 BUY_NO 假设 |
| 集中度 | 去掉最大贡献城市后 ROI < 0 且无第二城接力 | 判为单城运气 |
| 执行 | 利润价桶可成交 edge ≤ 0 | "打中枢"路线死 |
| 概率质量 | partition 后加总持续偏离 1（>5%） | 模型/取数有 bug，先修 |
| 模型口径 | ensemble 在连续 4 周样本外 Brier/PNL 均不优于 raw 或 market | 回退为 shadow，不升 live |
| 稳定性 | 大额亏损集中在 forecast_jump/side_flip 样本 | 该类信号降 size 或 shadow |

---

## 4. 数据契约（请拿回去与另一台机器对比补齐）

向前采集每次快照、每个 bracket、在**交易时点(as-of)**必须含：

**A. 盘口（当前这台机器缺，是 Phase 2 的硬前提）**
> ⚠️ YES / NO 是**两个独立 token、各有独立盘口**，`no_ask ≠ 1 − yes_bid`（浅盘口下偏离明显）。
> 必须对**两个 token_id 各拉一次 `/book`**，否则 NO 边 edge 与套利信号都算不了。
- 每个 bracket × {YES, NO} 两侧：`best_bid`, `best_ask`, `bid_size`, `ask_size`（至少 L1，最好 L2 前几档）
- 每侧 `spread = ask − bid`；`depth_within_2c`, `depth_within_5c`（可成交股数）
- `yes_ask + no_ask`（<1 即套利信号）
- `last_trade_price`, `last_trade_ts`

> 取数说明：CLOB `clob.polymarket.com/book?token_id=...` 返回单个 token 的实时买卖盘；
> `/prices-history` 只给单一价序列——这就是当前缓存只有 `{t,p}`、没有盘口的原因。
> 向前采集必须在快照时刻对 **YES token 和 NO token 各轮询一次 `/book`**。

**B. 模型与结算（已具备，确认仍在写）**
- `model_prob`, `forecast_max_f`, `model`, `model_init_utc_estimated`, `hours_to_settle`,
  `time_bucket`, `entry_price`, `side`, `bracket`, `condition_id`/`token_id`
- 结算 `outcome` / `final_price`（来自 `cache/pm_history/`）

**C. partition 元数据（新增）**
- 每个 event 的完整 bracket 列表 + 每档解析出的 `[lo, hi]` 区间 + `bracket_type`
  （便于离线重建互斥划分与概率加总校验）

> 若你另一台机器的新脚本已采集 A，则 Phase 2 可立即启动；若只采到单一价，需先补 `/book` 轮询。

---

## 5. 与你最初问题的对应关系

| 你问的 | 答案落在 |
|---|---|
| 同城多档不自洽 | Phase 0 做 partition 化（数据质量）；Phase 3 做联立 sizing（彻底解决） |
| 信号先后/冲突 | Phase 3 的"无状态目标 + 有状态执行 + no-trade band"（建机器时一并落地） |
| 模型要不要改 | **要改，但先只改低风险骨架**：raw×market ensemble、季节条件化 shadow、forecast 稳定性观测；复杂 ML 和组合 Kelly 后置 |
| $200/10 城怎么分配 | Phase 3 的批次分配 + 三闸门；小池子先 Top-2/3 城打深 |

**核心 owner 判断**：这些都是对的问题，但**第一性问题是"有没有 edge"**。在 14 笔样本、删 1 笔翻负
的现状下，先把组合机器建出来 = 优化一个还没验证的数。先证实，再建机器。

---

## 6. 当下就能启动的第一步

Phase 0 的四件便宜事，今天就能开：
1. 把主策略 + 对照**预注册冻结**（写进配置/常量，停止再生成新变体）。
2. 把概率口径改成 raw/market/ensemble **双写**，默认研究主线用 ensemble-edge，raw-edge 保留为对照。
3. 加 forecast hash / max-hour / side-flip 观测字段，先解释 5.31 / 6.1 这种大幅翻边。
4. 把 §4 数据契约对照你另一台机器的新 CLOB 脚本，确认 order book 字段齐不齐。

这四件不依赖重模型或 Kelly 机器，且直接决定 Phase 1/2 能否启动。
