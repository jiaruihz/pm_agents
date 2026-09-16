# 绩效分析：fact_signal_candidates × fact_trades 双底表串联

机会粒度候选表（全机会宇宙）与 fill 粒度成交表（已执行样本）首次对齐分析，
检验"已成交结论"里有多少是真实 alpha、有多少是执行选择偏差。

---

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | DB `runtime/weather.db`（首选，未降级） |
| DB mtime | 2026-05-29T01:46:37Z |
| fact_trades `MAX(fact_built_at_utc)` | 2026-05-28T16:35:45Z |
| fact_signal_candidates `MAX(fact_built_at_utc)` | 2026-05-28T17:46:37Z |
| fact_trades 行数 | 2454（fill 粒度） |
| fact_signal_candidates 行数 | 15420（机会粒度，1112 个 snapshot 文件） |
| 决策窗参数 | `hours_to_settle ∈ [22, 24]`（对齐当前生产 T-22~24h） |
| 同步说明 | 本次未触发 N100 实时 `sync_weather_remote.sh`，分析基于本地镜像 + 已建底表；如需最新增量需先同步再重建 |

---

## 数据完整性自检

**fact_trades（fill 粒度）**

| trade_class | rows | settled |
|---|---|---|
| live_real | 262 | 159 |
| live_simulated | 411 | 235 |
| paper | 1145 | 1017 |
| snapshot_replay | 636 | 620 |

- `bad_settled_null_pnl`（settled 但 PnL 为空）= **0** ✅

**fact_signal_candidates（机会粒度）**

- 总机会 15420，`dropped(no condition_id)` = **0** ✅
- `eligible=1` 3917；`paper_ordered` 1520；`live_filled` 183；`settled(final_yes≠null)` 2162
- **链路对齐完整性（本次核心验收）**：paper_orders orphan = **0**，live_fills orphan = **0**
  → 每一条 paper 决定单、每一笔 live 真实成交都能在 snapshot 机会宇宙里找到对应机会，
  `(condition_id, side, event_date)` 关联键无悬挂，双底表可安全 JOIN。
- **决策窗缺失** = 6971 / 15420（45.2%）；eligible 子集 1826 / 3917（**46.6%**）
  → 近半 eligible 机会在 T-22~24h 带内没有任何 snapshot，详见残余风险。

**可用反事实分母**：`settled AND decision_window present AND eligible=1` = **315 行**（样本偏小，结论降级处理）。

---

## 目标指标与分母

- **counterfactual_pnl**（候选表主口径）：用**决策窗 `decision_entry_price`**（T-22~24h 真能看到的价）
  持有到结算，公式沿用 contract §2.1 已验证的 BUY_YES/BUY_NO 口径（含 2026-05-29 BUY_NO 勘误）。
- **pnl_usd_at_fill**（fact_trades 口径）：实际成交价已结算 PnL，唯一授权列。
- live 实绩分母锁 `trade_class='live_real' AND settlement_status='settled'`。
- 候选反事实分母锁 `eligible=1 AND final_yes≠null AND decision_window_missing=0`。

---

## 总览结论

1. **双底表链路打通**：0 orphan，关联键 `(condition_id, side, event_date)` 无悬挂——
   "回测/未实盘 paper 决定 ↔ 真实成交"这条链现在物化可查，本次主目标达成。
2. **BUY_YES 的真实 alpha 被执行环节吃掉了**：全 eligible 机会宇宙里 BUY_YES 每机会反事实
   均值 0.376、合计 +43.6；但 live 实际成交的 BUY_YES 均值仅 0.066、合计 +2.4。
   即"BUY_NO 远强于 BUY_YES"在**成交样本**里成立，但在**全机会集**里 BUY_YES 单位 edge 反而更高——
   差距来自执行选择（live 只吃到了较差的 BUY_YES 子集 + 漏掉赢家）。
3. **漏掉的赢家集中在 BUY_YES**：23 个 eligible 已结算机会"中了但从未下单"，
   合计放弃反事实 PnL +95.6，其中 BUY_YES 占 +62.4。
4. **滑点是负的（有利）**：live 实际成交价平均比 paper 想进的价便宜约 0.7~0.8 美分。

---

## 关键切片

### Q1. 方向 alpha：全机会宇宙（反事实）vs 已执行（live_real）

| 口径 | side | n | win_rate | PnL 合计 | 每机会均值 |
|---|---|---|---|---|---|
| 候选(eligible,反事实) | BUY_NO | 199 | 0.668 | +28.9 | 0.145 |
| 候选(eligible,反事实) | BUY_YES | 116 | 0.284 | **+43.6** | **0.376** |
| live_real(已结算) | BUY_NO | 123 | 0.675 | +19.1 | 0.155 |
| live_real(已结算) | BUY_YES | 36 | 0.389 | +2.4 | 0.066 |

> BUY_NO 两个口径一致（win≈0.67，单位 PnL≈0.15）——稳。
> BUY_YES win_rate 低但单笔赢面大（赔率不对称），全机会集单位 edge 是 BUY_NO 的 ~2.6 倍；
> live 只成交到 36 笔且均值塌到 0.066，说明**执行没能把 BUY_YES 的反事实 edge 兑现**。

### Q2. 模型 alpha（全 eligible 反事实）

| model | n | win_rate | cf_pnl |
|---|---|---|---|
| gfs | 197 | 0.558 | **+85.7** |
| ecmwf | 118 | 0.475 | -13.3 |

> ⚠️ 与 CLAUDE.md 历史结论"ECMWF 优于 GFS"相反。该历史结论来自不同周期的 paper ledger 累计；
> 本子集仅 315 行、单周期，**低置信**，不足以推翻历史口径，仅作监控信号。

### Q3. 城市 alpha（全 eligible 反事实，n≥8）

| city | n | ordered | live_fill | win | cf_pnl |
|---|---|---|---|---|---|
| Tokyo | 18 | 16 | 9 | 0.722 | +46.5 |
| NYC | 37 | 28 | 7 | 0.622 | +35.9 |
| Miami | 36 | 28 | 11 | 0.556 | +29.1 |
| Warsaw | 26 | 24 | 9 | 0.577 | +15.2 |
| Chicago | 14 | 13 | 3 | 0.571 | +9.9 |
| Madrid | 20 | 15 | 6 | 0.500 | +8.2 |
| Shanghai | 18 | 18 | 3 | 0.556 | +3.6 |
| LA | 28 | 24 | 11 | 0.536 | +0.6 |
| Austin | 17 | 16 | 10 | 0.529 | +0.4 |
| London | 32 | 28 | 13 | 0.531 | -4.2 |
| Beijing | 28 | 21 | 6 | 0.286 | **-16.6** |
| Paris | 27 | 24 | 12 | 0.444 | **-30.9** |

> Tokyo/NYC/Miami 在全机会集层面正 alpha 且漏成交多（livefill 远低于 ordered），是"想交易但没吃满"的重点。
> Beijing/Paris 全机会集层面就是负的——不是没成交的问题，是机会本身亏。

### Q4. 滑点（live 实际成交价 − paper 想进价）

| side | n | avg_slippage |
|---|---|---|
| BUY_NO | 137 | -0.0082 |
| BUY_YES | 36 | -0.0071 |

> 负值=成交价更便宜，对买方有利。滑点不是 BUY_YES 表现差的原因。

### Q5. 漏掉的赢家（eligible 已结算，从未 paper 下单，但 side 赢）

| side | n | 放弃的反事实 PnL |
|---|---|---|
| BUY_NO | 15 | +33.3 |
| BUY_YES | 8 | +62.4 |
| **合计** | **23** | **+95.6** |

---

## 交易动作建议

- **BUY_YES 不应被一刀切降权**：成交样本的弱 BUY_YES 表现部分是执行选择 + 漏单造成，
  全机会集层面 BUY_YES 单位 edge 更高。建议 **shadow** 一组"对 eligible BUY_YES 机会更激进下单/挂更靠内的价"
  的策略分支，用本表 counterfactual 持续对账，再决定是否调 live 权重。**不要**直接改 live sizing（样本不足）。
- **Tokyo/NYC/Miami 提高捕获**：这三城正 alpha 但 live_fill ≪ ordered，优先排查为什么没吃满
  （挂价/队列/窗口），属于执行兑现问题。
- **Beijing/Paris 候选维持过滤/降权**：全机会集即为负 alpha，不是执行问题。
- **GFS vs ECMWF 暂不动 live**：本期信号与历史相反但低置信，挂监控，等样本积累。

## 残余风险

- **样本量**：可用反事实分母仅 315 行（eligible+settled+decision present），城市切片多在 `low_sample`，
  所有方向/模型/城市结论均为弱结论，禁止据此改 live sizing。
- **决策窗缺失 46.6%**：近半 eligible 机会在 T-22~24h 带内无 snapshot，反事实只覆盖另一半，
  存在系统性缺口（早期 snapshot 未覆盖该窗 / T12 城市无 T-22~24h 切片）。换窗口重跑可对比敏感性。
- **paper_ordered ≠ live 执行意图**：`paper_orders.jsonl` 是全池 paper ledger（T1+T2 paper 决定），
  不是 live 下单意图。因此 `missed_fill`（paper_ordered=1 且 live_filled=0，本期 1347）**大部分是 paper≠live 的设计差异**，
  不是执行漏单。真正的 live 执行漏单需用 live 意图单对账，本表暂不区分——**勿把 11.4% 当 live 成交率**
  （eligible 宇宙真实 live 覆盖率 35.2%）。
- **trade_class 混用**：本报告候选反事实用决策窗标准手数（shares=10），与 live 实际手数不同口径，
  PnL 量级不可直接相减，只比方向与单位 edge。
- **未结算估值**：本表 `final_yes IS NULL` 的机会反事实留空，未做 Phase 1.5 估值。
