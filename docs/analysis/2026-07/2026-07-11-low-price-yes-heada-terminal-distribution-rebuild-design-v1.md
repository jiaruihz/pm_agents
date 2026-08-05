# Low-Price YES (HeadA) — 终值分布重建设计 v1

> **⚠ v1 主轴已被 Phase 1 证据更正（2026-07-11）**：见
> [终值分布 kernel v1 结果](2026-07-11-heada-terminal-distribution-v1.md)。
> 建了 parameter-free PIT-bias kernel 并在大宇宙验证后发现：**市场报价对这些尾部的定价比任何模型都准**
> （AUC 市场 0.686 > 现行模型 0.631 > 新 kernel 0.454），`edge=model−market` 是模型高估噪声不是 alpha。
> **本设计「造更好的独立终值分布」前提被证伪**；应改向 **market-anchored 残差 + 前向验证的条件信任门**
> （下文 Phase 2/3 待按此重写）。独立分布/kernel 退为诊断与 base 先验，不作 live 选择器。

> status: `design / 主轴已更正、Phase 2/3 待重写` · authored_at_bj: `2026-07-11`
> 上游依据：[HeadA live 审计 v1.1](2026-07-10-low-price-yes-heada-live-audit-v1.md)（F2/F3/F4 是同一病根的三个症状）。
> 目标：把 HeadA 的**静态、高估 3x 的 per-bracket 点估 `model_p_yes`** 重建为**复用 canonical 相干终值 tmax 分布**，
> HeadA 退化为「尾部定价消费者」。**本设计只到 forward shadow；不改 live sizing，直到 forward 校准过门。**

---

## 1. 问题（第一性原理）

这个市场是 **exact-bracket tmax**：买 bracket X 的低价 YES = 赌今天终值最高温**正好**落在 X 档。
真正的标的是一个**随白天推进而坍缩的条件分布** `D_t(tmax | t 时刻信息)`，
YES-X 公允价 = `P(终值 ∈ X档 | t)`。边只可能来自市场 implied P 与真 P 的差。

**当前实现把这个坍缩分布压成了静态糙点估**：`weather_dashboard/blend/blender.py:blend_probability`
拿一个 per-bracket 独立点估 `model_p_yes_raw`，再与市场价按 `alpha*model + beta*market` 混合，写入
`fact_signal_candidates.model_p_yes`；HeadA runner（`scripts/ops/low_price_yes_lottery_tiny_live.py`）
直接读它（`probability_source="fact_signal_candidates_model_p_yes"`），套 `edge≥0.2` + `dist` 硬 filter
+ score-tier logistic sizing。这个点估：

- **不跨 ladder 归一**（各档独立，不保证和=1）；
- **无时间/路径条件**（审计 F4：p_yes 最长 6h 陈旧，下单一刻不重估）；
- **无 overshoot 结构**（exact-bracket 特有语义没建模）；
- **校准坏**（审计 F2：均值 0.384 vs 实际命中 12%，高估约 3x）。

审计三症状因此同源：F2（校准坏）、F3（每道 gate 反选）、F4（旧 thesis 硬下单）都是
「用静态点估代替坍缩分布」的下游表现。**修点估无用，要换分布本体。**

> 注：策略当前**是薄正边**（全窗去重 50 结算 / 4 中奖市场 / 净 +$6.35 / ~+24% ROI on settled，
> 赢单已对 `clob_fills.jsonl` 核实真实成交）。本设计是**在正边基础上修根因、让 sizing 可信**，
> 不是救亏损。

## 2. Target metric / target slice（先锁分母，防跑偏）

- **target slice**：HeadA 实际交易的**低价尾档**（fill price ≲ 0.15 的 YES bracket），
  分 forecast source（ecmwf/gfs）、分是否污染窗（7/02 GFS fallback 单列）。
- **target metric**：该 slice 上重建分布的**尾部校准**——低价档 reliability curve（predicted vs realized hit），
  以及 forward（PIT）**真边 EV**。**成功 = forward 校准 + forward 正 EV，两者同时成立才允许驱动 live sizing。**
- **反 gate 口径**：不拿 train 边际当可成交 alpha；不为救坏例子加硬 filter；发布任何 live_real 数字先过
  `weather_clob_fill_coverage_gate.py`（`gate_pass=true`），现金流用 `fill_date_bj`。

## 3. 架构决策：复用 canonical 引擎（血缘干净）

**一份相干终值 tmax 分布住 canonical 层，两个消费者：**

```text
canonical 终值 tmax 分布 D_t(tmax | ...)   ← 单一事实源
        ├── tmax 分布策略（Tmax 分布 Edge）   → 吃 center / 表达
        └── HeadA 低价彩票                    → 吃 tail 定价
```

- 已存在的引擎线：`Tmax 分布 Edge` 策略 + coherent tmax expression calibrator（commit `e59b15fd`）+
  `tmax_distribution_p0..p5` 系列。HeadA **不自建并行 tail 模型**（CLAUDE.md 血缘原则）。
- **强制 caveat（选择复用时标注的风险）**：必须先证明该引擎的**尾部**没有被 center-of-mass 拟合牺牲——
  这是 Phase 1 的诊断出口，不通过则回到「引擎尾部先补齐」而不是让 HeadA 复用一个尾部失准的分布。
- 落地位置：分布产出仍走 `fact_signal_candidates`（HeadA 读它，血缘不变）；被替换/升级的是
  `blender.py` 那条「独立点估 + 市场混合」的产出路径。

## 4. 相干终值分布必须 condition 的四件事

当前 `blend_probability` 每档独立点估丢掉的，正是分布的物理结构：

1. **相干性** — 跨整条 event ladder 归一（Σ_bracket P = 1），low/upper sibling 完整
   （tmax lineage 修复已把 collector 改为保留完整 ladder，见 tmax 审计线，可直接受益）。
2. **路径状态 / 时间** — 剩余加热窗口（forecast peak clock）、当前路径 fresh-runway / plateau / pullback / fade、
   观测 cadence/source；分布随白天坍缩，替掉 6h 静态（修 F4）。
3. **Overshoot hazard** — exact-bracket 语义：`P(终值=X | 已达X) = P(达到X) − P(冲过到≥X+1)`；
   低价尾档尤其要扣 overshoot 到下一档的质量（CLAUDE.md 反复强调「到过 X ≠ X YES 安全」）。
4. **源可靠性** — per-city CITY_MODEL（31 城 ECMWF / 49 城 GFS）误差结构 + 已知污染窗显式标注
   （7/02 GFS fallback 必须能被分层剔除，不静默混入）。

## 5. HeadA 侧改动（消费者重写）

- **去掉**：`edge≥0.2` 门、`dist_lt0` 硬 filter、score-tier logistic sizing（`score_dist_probability`）。
- **换成**：`真边 = 分布公允价(bracket X) − live book ask − fee`，驱动买 / 不买；
  sizing 用 **Kelly-fractional on 真边**（分布校准过门后才启用；未过门只 shadow 记录）。
- **逆选择（F3）**：区分「便宜因为 book 薄/未 reprice」vs「便宜因为真低概率」——
  用**分布 vs book 的方向差**判定，而不是 cushion 价格漂移 proxy；追价成本进 EV，不用 hard cutoff。
- **F4 结构性修复**：fresh-book 通过后按 Phase 2 的 path-state 分布**在决策时刻重估**，
  thesis 失效则显式 block（`thesis_invalidated_fresh_forecast`），替代旧的价格 proxy。

## 6. 验证硬门（CLAUDE.md 口径，不可跳）

- **walk-forward PIT 校准**：低价尾档 reliability curve（分 source、剔污染窗），expanding window，
  不用未来信息；paper snapshot METAR 覆盖从 6/29 起（早期只能 expanding，不 backfill）。
- **forward shadow 双写**：新分布 p / 真边 / 建议 sizing 全部先 forward 记录，
  与现行 live 并行跑，**不改 live 行为**。
- **过门条件**：forward 校准合格 + forward 正 EV，才允许 Phase 3 用它驱动 live sizing。
- **发布前**：`weather_clob_fill_coverage_gate.py` `gate_pass=true`；已结算才报 `pnl_usd_at_fill`，
  未结算只报 MTM + `val_snapshot_ts_utc`。

## 7. 三段路线（每段独立交付、Phase 1/2 不碰 live）

### Phase 1 — 诊断 + 接线（data + shadow，零 live 行为变更）
- 定位 canonical 终值 tmax 分布引擎的实际产出接口，量化它在 HeadA 低价尾档的**校准 / 相干性 / overshoot 覆盖**。
- 若尾部可复用：把 HeadA 的 `probability_source` 从自带点估切到 canonical 分布（**只 shadow 双写，不改 live 下单**）。
- **出口**：一份「引擎尾部能否直接复用」诊断 + 新旧 p_yes 在 HeadA slice 上的 forward 校准对比。
- **不通过则停在这里**：先补引擎尾部，不让 HeadA 复用失准分布。

### Phase 2 — 路径/overshoot conditioning（升级 canonical 分布）
- 给 canonical 分布加 §4.2（path-state/时间）与 §4.3（overshoot hazard）两层条件，per-city 源可靠性（§4.4）。
- 决策时刻重估（修 F4），forward shadow 记录 thesis 重估的 block/pass。
- **出口**：升级后分布在 target slice 的 walk-forward 校准优于 Phase 1 基线（同分母 A/B），forward 正 EV 迹象。

### Phase 3 — 真边 sizing（gated on Phase 2 forward 过门）
- 用分布公允价 EV + Kelly-fractional 替换 `edge≥0.2` + score-tier；`dist_lt0` 从硬 filter 降回 telemetry / 同分母 forward A/B。
- **仅在 Phase 2 forward 校准 + 正 EV 双双成立后动 live sizing**；否则维持现行 tiny-live 参数。
- **出口**：live sizing 由校准过的真边驱动，score-tier 退役。

## 8. 非目标 / YAGNI

- 不重建整条 tmax 分布策略（只复用其分布产出）。
- 不在 forward 校准过门前改任何 live 下单 / sizing。
- 不为追坏例子新增 hard filter（逆选择靠分布方向解，不靠补洞）。
- 不改 canonical 血缘链（order→fill→settlement）；F1 fill 去重是独立的地基修复项，另案。

## 9. 依赖 / 未决

- **依赖 canonical 引擎产出可用性**：Phase 1 诊断先确认引擎是否已在 `fact_signal_candidates` 暴露可读的相干分布，
  还是只在 tmax 策略内部。这决定 Phase 1 是「接线」还是「先把分布产出提到 canonical 层」。
- **F1 fill 去重**（审计 F1）：与本设计正交但影响任何 PnL 口径，建议并行推进、不阻塞本线。
