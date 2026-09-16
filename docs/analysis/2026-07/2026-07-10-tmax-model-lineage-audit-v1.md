# Tmax Model Lineage Audit v1

> generated_at_utc: `2026-07-10T13:40:55Z`
> scope: 代码/链路审计。审阅 tmax_distribution_edge 概率模型本体（p0–p4 research 管线）、
> live candidate runner、执行/去重、标签与证据链。**本轮不改任何 runner/config/order 行为**，只给结论和修复优先级。
> 主入口：[WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md](../../WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md)

> **后续纠正（2026-07-10）**：C 的“below-ladder 是模型结构性盲区”归因不成立。lineage repair replay
> 用 canonical event bracket inventory 审计后，357/357 below rows 都是 collector 预先删除 near-binary lower
> siblings；157/188 top-two rows 也缺 upper siblings。D1 real-tail A/B 同样受缺档 snapshot 污染，不能单独落地。
> A/B2/B3 等 runner 问题已修，完整结果见
> [2026-07-10-tmax-lineage-repair-replay-v1.md](2026-07-10-tmax-lineage-repair-replay-v1.md)。

## 结论（先给动作）

架构本体（四桶概率 → `P(win)-ask` 选表达 → first-lock → fresh-book gate）是干净的，不需要推倒。
问题集中在两类：**(1) 边角的 NaN 语义在 live runner 里绕过了 edge 门（有今日实盘证据）**，
**(2) 把桶几何锚在 running max 上，导致策略结构性看不见"清晨新鲜跑道"整段形态**。

按优先级：

| # | 问题 | 性质 | 建议动作 |
|---|---|---|---|
| A | NaN sibling ask 绕过全部 edge 门 | 确认 bug，实盘可见，shadow/live 口径分叉 | 一行 guard，先修 |
| C | 桶几何锚 running max → below-ladder 盲区 | 策略选择性偏差（非管道 bug） | 截断表达集 / full-ladder，重估容量 |
| D1 | market tail 概率是残差不是报价 | 特征噪声，正好落在最在意的 tail 切片 | 用真实 d3+ 报价定价，可单独落地 |
| D3 | below-current 标签丢弃 → 条件分布 | 系统性偏差，current_no 偏保守 | 修 basis（前移 source-basis 到 geometry） |
| B1 | 观测无新鲜度 gate，缺失填 0 | live 资金路径静默降级 | 加 obs age gate，缺失记 null |
| 其它 | D2/D4/D5/D6/B2/B3 | 中低优先 | 见下 |

---

## A. 实锤 bug：NaN sibling ask 绕过全部 edge 门

**位置**：[tmax_distribution_edge_live_candidate_v1.py:526](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L526)
与 [build_candidates 门逻辑:930-939](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L930)

state row 只要求 `current_yes / current_no / d1_no / d2_no` 四个 ask 有限，
**`d1_yes_ask` / `d2_yes_ask` 允许是 NaN 直接流入表达循环**。而候选门全部是标量 `<` / `>` 比较：

```python
elif ask < args.ask_floor:      reason = "below_ask_floor"
elif ask > args.ask_ceiling:    reason = "above_ask_ceiling"
elif fee_adjusted_edge < args.edge_threshold: reason = "below_edge_threshold"
```

NaN 与任何数比较都是 `False`，**三道门对 NaN 全部失效**。后果链：

1. NaN-ask 的 `d1_yes` 被选为该 row 的 `best`（`best is None` 时无条件接管）。
2. 之后合法表达无法反超：`(0.05, roi) > (nan, nan)` 恒为 `False`，
   **排在 NaN 候选之后的 `d2_no` / `d2_yes` 即使有真实正 edge 也会被压住，该 city-day 当轮机会丢失**。
3. NaN 候选进入 `fresh_quote` 时，snapshot drift 保护
   （`fresh_ask > snapshot_ask + 0.02`，[:1137](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L1137)）
   因 `snapshot_ask=NaN` 同样失效。

**今日实盘证据（2026-07-10 tiny-live blocked）**：

```text
Amsterdam 2026-07-10 d1_yes | ask=null | p_win=0.094 | 最终靠 fresh_ask_below_floor 拦住
Ankara    2026-07-10 d1_yes | ask=null | p_win=0.198 | 同上
```

`p_win=0.094` 对着 `ask_floor=0.40` 本应在第一道 edge 门就死掉，它能走到 CLOB 查询这一步，
就是 NaN 旁路。**最终没下错单**（fresh-book 阶段的 edge 门用真实数字兜住），但：
候选选择被污染、drift 保护失效、且这是 Lucknow 之后最在意的"执行口径 ≠ 回测口径"类问题——
`candidate_shadow_v1` 用 pandas 过滤（`NaN >= floor` 天然为 `False`，正确），
live runner 用标量比较（NaN 全放行），**两个 runner 对同一份数据行为不一致**。

**修法**：表达循环开头加

```python
if not math.isfinite(ask):
    blocked.append({**base, "decision_status": "blocked", "block_reason": "missing_expression_ask"})
    continue
```

**顺带一个潜伏 crash**：两个候选 `fee_adjusted_edge` 完全相等且 `model_roi` 均为 `None` 时，
[:939](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L939) 的 tuple 比较 `None > None` 会抛 `TypeError`。

---

## C. 桶几何锚在 running max → "清晨新鲜跑道"整段形态盲区

**先纠正上一轮的错判**：观测覆盖**没有问题**。40 城观测缓存基本就是全量目标池
（今天 snapshot 47 城 / 34 个当日 city-day，28 个有观测 = 82%）。累计 block 里
`observation_missing` 的绝大多数是**次日 7/11 的记录**——一个还没发生的日子当然没有 running max，
被丢是完全正确的。

真正的结构性损失是 `cannot_map_current_d1_d2`。今天 28 个有观测的 city-day 的映射去向：

```text
mapped_ok        : 15
below_ladder     : 10   ← running max 低于整条市场梯子（主因）
top_two_brackets :  3   ← running max 已冲到梯顶两档内
no_obs           :  6   ← 40 城外无源城，正常
```

**主因不是"太热冲顶"，是"还没热起来"。** 例子：

```text
Atlanta  running_max=80.1  市场最低档=83.5–85.5
Austin   running_max=78.8  市场最低档=88.5–89.5
Dallas   running_max=86.0  市场最低档=93.5–95.5
Denver   running_max=63.7  市场最低档=81.5–83.5
```

市场梯子挂在**预期收盘高点**附近，而决策时刻 running max 还在梯子下方。
[build_state_rows:503-510](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L503)
把 "current 桶 = running max 所在档"，于是**每一个"清晨/午盘、当日还没爬进市场梯子"的
city-day 都映射不出来被整行丢弃**。

**这意味着策略结构性地只在"下午、已接近峰值"时才看得见机会**——而这正好是本项目
no-reheat / remaining-heat 最核心的形态盲区。fresh runway（剩余加热窗口还很长、
还没打穿任何档）恰恰是物理上最该建模的状态，现在被 geometry 层无声吃掉。

**这不是加 gate 能解决的，是表达/几何设计问题**：

1. **below_ladder（10/13）**：整条市场梯子都在 running max 上方，说明市场认为今天还会继续升。
   此时 `current` 桶应该落在市场最低档**下方**，所有可交易档都是 "reheat upside"。
   现有四桶几何无法表达，直接丢。full-ladder / survival v2 的逐档 hazard 天然覆盖这段——
   这是 v2 除"校准更准"之外**第二重、且尚未计入升格论证的容量收益**。
2. **top_two_brackets（3/13）**：running max 已冲到梯顶，overshoot 风险最集中。
   此时 `current_no` 语义依然完好（最终 ≠ current 即赢），可以用**截断表达集**保留，
   不必因为"上方数不出两档"丢整行。

**建议**：在升格 survival v2 时，把"覆盖 below_ladder + top_two 两段形态"的容量收益
和校准收益一起算进 promotion gate；短期可先给 top_two 做截断表达集止血。

---

## D. 模型口径的系统性偏差

### D1. market tail 概率是残差不是报价（可单独落地）

[_market_raw_weights:151](../../../scripts/analysis/reheat_risk/research_tmax_distribution_p0_anchor_scorecard_v1.py#L151)：

```python
weights["tail"] = max(EPS, 1.0 - raw_sum)   # raw_sum = current+d1+d2 三个 mid
```

spread 稍宽时三个 mid 之和就超 1，tail 被钉在 `EPS` 再归一化。
`market_p_tail / market_log_p_tail` 是模型输入特征，所以**市场特征在 overshoot/tail 切片上噪声最大**，
而那正是 survival v2 报告"改善最多"的切片——**部分"模型改善"可能只是修复了这个特征的噪声**。

snapshot 里整条梯子（`with_intervals`）都解析了，d3+ 档的 YES 报价现成存在。
**用真实报价给 tail 定价，不用残差**。这条不依赖 survival v2，单独可 A/B。

### D2. current 桶被 ask 侧拉高

同函数：current 用 `(yes_ask + yes_bid)/2` 再与 `(1-no_bid)` 平均（偏 ask 侧），
d1/d2 用 NO mid（对称）。current 桶系统性略胖 → blend 后 `current_no` edge 被系统性压低、
`current_yes` 被抬高。方向与 D3 的 below-bucket 偏差同向叠加，都在给 current_yes 虚胖。

### D3. below-current 标签丢弃 → 概率归一化在错误样本空间

[_label:176](../../../scripts/analysis/reheat_risk/research_tmax_distribution_p0_anchor_scorecard_v1.py#L176)：
结算低于 current 桶的行直接丢（settlement basis ≠ obs running-max basis 时发生）。

后果不只是"current_yes 不能 live"——它意味着**四桶概率是条件在"final ≥ current"上的条件分布**，
所有桶概率归一化在被砍掉一角的样本空间上。`current_no` 的 p_win 被系统性**低估**
（below 结算时 current_no 其实赢）。也就是说**当前主力 NO 表达的 evidence 是保守偏的，
真实 edge 可能比点估更高**——升 size 时应知道这个方向。

修法：与其修标签不如修 basis——state row 的 current 定位改用与结算同源的 running max
（survival v2 已有 source-basis 特征，前移到 geometry 层）。

### D4. bridge 证据的 ask 口径与 live 不一致

exact-book bridge 的 `d1_yes/d2_yes` ask 用 `1 - sibling NO bid` 代理，
live 用真实 YES ask + fresh book。YES 侧薄时真实 ask 显著差于代理，
所以 `bridge_no_current_yes_5expr +9.7%` 里 yes 腿的贡献在 live 下拿不到同等价格。
first-lock shadow 正在收真实数据，裁决时按 ask 口径**分腿**对账。

### D5. 超参冻结在 6/21 前 + 每 15 分钟重跑确定性 CV

[fit_predict_live:697-699](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L697)：
每 cycle 都对 `train_pre` 重跑 `C_GRID × expanding CV` 选 `c/alpha`——输入不变输出恒定
（当前 `alpha=0.5, c=0.03`），纯浪费算力，缓存即可。更实质的是 alpha/c 从没见过 6/21 之后的
三周数据；forward 期市场结构在变（recent 切片变薄），值得做一次"超参 refresh 的同分母 A/B"。

### D6. 负温 rounding 潜伏差异

[arith_round:261](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L261) `floor(x+0.5)`
把 `-2.5` 舍成 `-2`（向正无穷），与"四舍五入远离零"惯例在负半度上不同。7 月无感，
摄氏城市入冬后 bracket 定位会在 `-X.5` 上错档。留注释或改 `round-half-away`，核对结算惯例。

---

## B. 链路缺口（静默降级，违反"显式失败优于静默 fallback"）

### B1. 观测无新鲜度 gate，且缺失填 0

snapshot 有 60 分钟硬 gate，obs cache 完全没有：当前 `latest.json` 单城 `age_min` 最高 79 分钟，
照样进模型（running max / trend3h 全旧，而这是 current 定位和 mechanism filter 的输入）。
更糟：[build_plan:1289](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L1289)
`to_float(obs_age_min, 0.0)`——**缺失的观测年龄被写成 0.0，把"不知道多旧"伪装成"绝对新鲜"进下单血缘**。
建议加 `--max-obs-age-min` gate（超龄独立 reason block），缺失记 null 不记 0。

### B2. live 模型特征缺失静默中位数填充

[fit_predict_live:705-708](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L705)
对缺列填 `NaN/"unknown"`，sklearn imputer 用训练集中位数补上。若上游特征断供
（正是 7/02–05 ECMWF 静默 fallback 的形态），模型会无声用"平均天气"打分。
不必改硬失败，但应在 `latest_summary.json` 加 per-feature live 缺失率，让 dashboard 能看到跳变。

### B3. 被 max_orders 截掉的 accepted 候选凭空消失

[run_once:1401-1404](../../../scripts/ops/tmax_distribution_edge_live_candidate_v1.py#L1401)
排序后 `[:max_orders]`，被截掉的候选既不在 `latest_candidates` 也不在 `latest_blocked`——
违反 Event Contract "selected 和 blocked 都必须保留"的硬要求，复盘"没下的单是不是更好"时分母丢了。

---

## 已核验为"干净"的部分

- **first-lock 去重**：paper `simulated_open` / live `submitted` 与 runner 读取口径一致，
  8 笔 live + 8 笔 paper 无重复 city-day-status。Lucknow 暴露的执行层去重问题在这个 runner 上已修好。
- **fresh-book 二次门**：即使 candidate 阶段被 NaN 污染，真实 CLOB ask 的 edge/floor/drift/size
  四道门用真实数字兜住，是没下错单的根本原因。
- **架构分层**：weather/regime = 特征，bucket probability = 输出，ask/expression = 执行选择——
  这套拆分是对的，A/C/D 都是层内实现问题，不是分层错误。

## 文档状态漂移（附带）

[WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md](../../WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md)
仍写 "tmax tiny-live 保持暂停"、P6 source 到 7/03；实际
`first_lock_no_current_yes_tiny_live_v1` 自 7/9 起在跑（8 笔 submitted live），atlas 已到 7/08。
执行层是干净的（见上），纯文档滞后，但这是 source-of-truth 文档，建议同步。

## 建议落地顺序

1. **A**：NaN ask guard（一行，今天有实盘污染证据）。
2. **B1**：obs 年龄 gate + 缺失不填 0（资金路径静默降级）。
3. **D1**：tail 用真实 d3+ 报价（不依赖 v2，单独可 A/B，直接作用于最在意的切片）。
4. **C**：top_two 截断表达集止血；below_ladder 收益并入 survival v2 升格论证。
5. survival v2 双写照计划走，裁决时把 D3（条件分布）、D4（ask 口径）并入对账清单。
6. 低成本顺手项：B3（截断候选进 blocked）、D5（CV 缓存 + refresh A/B）、B2（缺失率 telemetry）、文档同步。
