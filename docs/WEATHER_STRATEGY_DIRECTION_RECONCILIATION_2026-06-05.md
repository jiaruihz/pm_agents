# 战略方向对齐 — 「模型优化研究」 × 「2026-06-05 校准发现」

Status: snapshot
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; historical snapshot evidence only

Status: `snapshot`。本文保留 2026-06-05 策略方向对齐记录；当前执行路线以 [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md) 和 [WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md) 为准。

> 2026-06-05。回答用户问题：「他的策略对还是你现在的这个对？我们怎么把研究融进去？」
>
> 结论先行：**`docs/模型优化研究.md` 的战略框架本质上是对的，但有 3 个数据假设已经过时**。
> 我今天的发现不是要替代他的 Phase 0-4 路线，而是**在 Phase 0/1 里塞进一个免费 alpha 通道**，
> 并把 "edge 不存在" 的判定标准做得比"14 笔删 1 笔翻负"更严格。

---

## 0. 双方核心主张对照

| 维度 | `模型优化研究.md`（外部文档） | 本会话 2026-06-05 校准（我们） |
|---|---|---|
| 概率模型本身 | **健全，不要改** | baseline 算法健全；但 **raw model_p_yes 输给市场** Brier +16%~+30% |
| 当前瓶颈 | edge 没被证实（14 笔 / 删 1 笔翻负） | edge 已**部分**证实（67 fills, BUY_NO 73.7% live win）但仍样本不足 |
| BUY_NO 是否有 alpha | 是（05-07 报告） | **是**（38 fills $19.97 / 73.7% 实盘验证） |
| BUY_YES 是否可用 | 不可（−46% ROI on 05-07） | **小样本好转**（13 fills 38.5%）但 $11.75 主要来自 1 单 Miami 运气 |
| 应不应该现在建 Kelly/组合 | **不应**（在未验证数字上做精装修） | 同意，但**有一件零成本事情可以先做**：上 ensemble 概率 |
| 盘口数据状态 | 当前缺，是 Phase 2 硬前提 | **已采集**（自 2026-05-19 起，每 cycle 拉 YES+NO L2 depth, gzipped JSONL，本机已 mirror 658MB）⚠ |
| kill 判据 | 预注册 5 条（删 top-5/方向/集中度/执行/概率） | **同意，应该照搬**，并把 ensemble vs raw 加进 Phase 1 归因 |
| 数据契约 | 要求 yes/no 两 token 各 `/book` 轮询 | **已满足**（见上） |

---

## 1. 他对的部分（保留并采纳）

### 1.1 战略姿态：先证实，再建机器

> "edge 删 1 笔就翻负、样本 14 笔，此时优化'如何把 edge 放大成组合'是给未验证的数字做精装修"

**完全同意**。我今天的 67 fills 也只是把"过度设计"的临界点从 14 推到 67——单城最大 4 fills，**仍然不足以做城市级 ROI 排序**。组合优化器、跨城批次分配、multi-outcome Kelly 这些都应该**推到 Phase 3**（n_settled ≥ 200-300 后），不要现在建。

### 1.2 BUY_NO 是 edge 来源

外部文档基于 34 笔结算判定 "正收益只来自 BUY_NO"。我们用 67 fills（1 个月样本外）独立复现了这个结论（BUY_NO 73.7% win rate vs BUY_YES 38.5%）。**这条 alpha 是真的，不是 05-07 的过拟合**。

### 1.3 预注册 kill 判据

外部文档的 5 条 kill 判据应该**直接落地为生产端配置**：

| 检查 | 否决线 | 我们今天的状态 |
|---|---|---|
| 尾部敏感性 | n≥80 时删 top-5 后 ROI < 0 | n=67，未到，但 Miami $14.23 单 fill 占总盈利 45%——**临界警告** |
| 方向 | BUY_NO 样本外 ROI 转负 | BUY_NO 仍 +11%，未触发 |
| 集中度 | 去掉最大贡献城市后 ROI < 0 且无第二城接力 | Miami 移除后剩 $17.49，仍正；但前 5 城贡献 $45 / 67 fills 总 $32——头部依赖明显 |
| 执行 | 利润价桶可成交 edge ≤ 0 | **未测**（orderbook 已采但未做该分析） |
| 概率质量 | partition 后加总持续偏离 1（>5%） | 生产端 compute_bracket_probs 一次算完 city-day，理论 Σ≈1，未审计 |

### 1.4 不要再调参数找更高 ROI

> "报告里十几个变体就是调参产物，越调越像过拟合"

我们今天的 mid_price_core_v2 / maker_queue_v2 也开始踩这条线——v2 系列实盘 −$7.30 / +$11.93 没显著好于 v1，**这是过度变体的早期信号**。`WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md` §A3 已建议暂停 v2 系列。

---

## 2. 他过时/不准确的部分（需要修正）

### 2.1 ⚠ 盘口数据：已经在采，不是 Phase 2 硬阻塞

外部文档 §1.1 / §4 反复说"历史盘口数据无买卖盘"。**这条已经过时**：

- N100 `weather-predict/output/orderbook_snapshots/` 自 2026-05-19 起每 cycle 采集，**YES 和 NO 两个 token 各拉一次 `/book` L2 depth**。
- 本机 `runtime/weather_edge_v1/market_data/orderbook_snapshots/` 已 mirror 658MB（19 天 * 几十 MB/日）。
- 字段：每条 record 含 `bracket / city / condition_id / outcome=yes|no / fetched_at_utc / raw.asks[] / raw.bids[]`（price + size）。
- 这正好满足外部文档 §4.A 的数据契约。

**含义**：Phase 2「真实点差 + 可成交 edge」**今天就能跑**，不需要再等数据采集。orderbook → ask 价 edge → 与 paper edge 对比，是接下来的天然分析项。

### 2.2 ⚠ 样本量：已经从 14 推到 67（且有 952 live_simulated + 2285 paper 旁路）

外部文档基于 2026-05-07 的 34 笔结算（其中主策略只有 14 笔）。今天的状态：

| 口径 | 行数 | 时间窗 |
|---|---:|---|
| live_real settled | **67** | 2026-05-24 → 2026-06-01 |
| live_simulated settled | 952 | 同上 |
| paper settled | 2285 | 同上 |
| signal candidates (t1_trading) | 6597 | 同上 |

**含义**：外部文档「Phase 1 通过线：n_settled ≥ 80-100」距离不远（实盘 67 fills，按 ~7 fills/day 估算 5-6 天内到位）。但样本主要堆在最近 9 天，**长尾时间分布不均**，应同时累计到月维度。

### 2.3 ⚠ 「概率模型健全，不要改」 只对了一半

外部文档 §1.1 / §1.2 说 baseline 算法（统一模拟分布、各档切概率）是自洽的，**这部分对**。但他没做过 vs-market Brier 校准。我们今天做了：

- raw `model_p_yes` time-split Brier 0.2226 vs 市场 0.1715 → **模型自己用比市场差 30%**。
- 30% model + 70% market 凸组合 Brier 0.1700 → **击败市场 0.9%**。
- 4 个城市（Milan / Lucknow / Austin / Beijing）raw model LOO 比市场差 ≥9% Brier，应禁用纯 raw model。

**含义**：算法层不需要重写（与外部文档一致），但**应用层需要把 raw model 输出与市场做凸组合**。这是零数据成本的运算层修改，应该塞进 Phase 0 一起做。

---

## 3. 整合后的路线（我们建议执行的版本）

把外部文档的 Phase 0-4 框架做 3 处修订：

### Phase 0 — 锁问题 + 装数据 + 上免费 alpha（约 1 周）

**原计划：**
- 预注册主策略 + 对照（冻结参数）
- 互斥 partition + 概率自洽校验
- 数据契约（orderbook）

**修订增补：**
- **新增 A1：上 0.3-model + 0.7-market 概率 ensemble**（零数据成本、Brier validated，详见 `WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md` §3.A1）。
- **新增 A2：raw model 城市黑名单**（Milan/Lucknow/Austin/Beijing 4 城禁用纯 raw model 信号）。
- **删除"数据契约"工作量**：orderbook 数据已采集，跳过此项；只需写 schema 文档化已有字段。
- **修改"互斥 partition 校验"**：生产 compute_bracket_probs 已隐式自洽，只需写一个**审计脚本** snapshot 抽查（不是大改）。

**新通过线**：
- 主策略 + 对照在跑
- ensemble 双写两个字段已上线（`model_p_yes_raw` + `model_p_yes_ensemble`）
- orderbook schema 文档化
- partition 抽样审计 Σ ∈ [0.97, 1.05]

### Phase 1 — 积累 + 归因（约 2-4 周，比原计划短）

**原计划：**
- 主策略 + 对照样本累计到 n≥80-100
- 每周归因 by city/side/price-bucket + tail-sensitivity

**修订增补：**
- **新增归因维度：raw_edge vs ensemble_edge** 双轨对比，证实 ensemble 是否在 live 上提升 BUY_YES（38.5% → ?）和整体 Brier。
- **样本量起点 67，到 100 大约 5-7 天**（不是 4-8 周），但**应该到 200-300 fills 才做城市级排序**（外部文档低估了 city granularity 所需样本）。
- **不要遗漏 Phase 1 内的 v2 策略止血**（mid_price_core_v2 / maker_queue_v2 在 67 fills 内已显示劣化，按 kill 判据应该暂停，不要等到 Phase 1 结束才决定）。

**新通过线**：
- n_settled ≥ 100，删 top-5 后 ROI > 0
- ensemble edge live 验证：BUY_YES 胜率从 38.5% 进一步提升
- v2 策略系按 kill 判据完成评估（继续/暂停/调参）

### Phase 2 — 执行现实（与 Phase 1 并行，**今天就能启动**）

**原计划：**
- 等 orderbook 数据采集
- 计算分价桶真实点差 + 可成交 edge + 逆向选择

**修订增补：**
- **数据已具备**（自 5-19 起 19 天，658MB），**今天就能写第一版分析脚本**。
- 重点价桶：05-07 报告说利润集中在 0.05-0.10 / 0.95-0.98 极端价档；今天 fact_trades 显示利润集中在 BUY_NO（即 YES 0.5-0.95 区间，NO 0.05-0.5）。两个分布需要交叉确认。
- 新增 deliverable：**`scripts/analysis/orderbook_executable_edge.py`**，用 orderbook snapshot 计算每笔信号在采集时刻的 `executable_edge_buy_no = (1 - yes_best_bid) - market_implied_p_no`，与 paper edge 对比。

**新通过线**：
- 主要利润价桶（BUY_NO + 中价位）真实点差扣除后 ensemble edge 仍 > 0
- 极端价桶（如 longshot YES）真实点差可能完全吃掉 edge → 接受这个限制

### Phase 3 — 组合 / 资金分配（仅当 Phase 1&2 通过才建）

**原计划保留不动**。但补充：

- Phase 1 通过条件应**包含 ensemble 验证**：raw 路线在样本外被 ensemble 路线超越是预期事件，组合层应基于 ensemble edge 而不是 raw edge 构建。
- 同城多档联立 Kelly 的"概率向量"应**使用 ensemble 概率**，不是 raw model 概率。

### Phase 4 — 小额实盘

**保留不动**。注意：当前 live_real 67 fills 实质上已经是「小额实盘 Phase」（$5/fill, 总 notional $288），所以这一段定义需要更新：Phase 4 = 在 Phase 1-3 全通过后，**从 $5 notional 放大到 $20-50**。

---

## 4. 「他对还是我对」的简短回答

**两个都对、但视角不同，互补不冲突。**

- 他写的是**战略路线 + 方法论**（先证实、kill 判据、不要过度设计）：这套框架今天依然完全成立。
- 我做的是**算法层 + 数据层的实证发现**（vs-market 校准、ensemble alpha、orderbook 已采）：这些是 Phase 0/1 内部具体怎么做的细节。

**最大的不同是时间**：他的报告基于 2026-05-07 / 14 笔。今天 2026-06-05、67 fills、orderbook 已采、ensemble 实证完成。**所以方向不冲突，但他写报告时"硬阻塞 Phase 2"的盘口数据缺口已经被另一台机器悄悄解决了**。

---

## 5. 我建议你立刻做的事

按优先级（高 → 低）：

1. **接受 Phase 0-4 框架作为外层战略路线**，把 `docs/模型优化研究.md` 移到 `docs/` 顶级索引（CLAUDE.md "核心参考文档" 段）。
2. **按本文 §3 的修订版执行 Phase 0**：
   - 上 0.3-model + 0.7-market ensemble（A1, 走 weather-strategy-deploy skill）
   - 黑名单 4 城（A2）
   - 暂停 mid_price_core_v2（kill 判据已触发）
   - orderbook schema 文档化（不需写新采集代码）
3. **写下 5 条 kill 判据并冻结**，作为生产端配置项（不是文档段，而是代码里的常量 + dashboard 检查），避免事后改判据。
4. **不要现在建 Kelly / 组合优化器**，按外部文档判断推到 Phase 3。
5. **Phase 2 今天就能启动**：写一个 60 行的 `orderbook_executable_edge.py` 跑历史 fact_trades vs orderbook snapshot 对比，看 BUY_NO 利润是否在 ask 价上还活着。

---

## 附：建议在 `docs/模型优化研究.md` 顶部加一条同步标注

```markdown
> **2026-06-05 同步注**：本文档战略框架仍然成立，但 3 处数据假设已过时：
> 1. 盘口数据自 2026-05-19 起已经在采（YES+NO L2 depth，本机已 mirror 658MB），
>    Phase 2 不再需要等数据。
> 2. 样本量已从 14 笔推到 67 fills（trade_class='live_real'），距离 Phase 1 通过线
>    n≥80-100 不远。
> 3. "概率模型健全不要改"对算法层成立；但 raw model_p_yes vs 市场 Brier 差 16-30%，
>    应在 Phase 0 内塞 0.3-model + 0.7-market ensemble（零成本免费 alpha）。
>
> 详见 `docs/WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md`。
```
