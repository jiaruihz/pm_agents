# 升温途中买当前档 NO · 预报峰值时钟 runway 窄态重过门 v1

日期：2026-06-22
状态：research only / 不动 live
脚本：`scripts/analysis/reheat_risk/research_current_yes_climbing_no_peak_runway_v1.py`
输出：`docs/analysis/2026-06/generated/current_yes_climbing_no_peak_runway_v1/summary.json`
上游数据：复用 [当前档 NO 盘口 EV v1](2026-06-22-current-yes-no-side-overround-ev-v1.md) 的 `enriched_rows.csv`（同一 settled 机会集）

> ## ⚠️ 2026-06-23 订正（近中午直接回测）
>
> 本 v1 的「runway 梯度指向未测的近中午区、建近中午机会集」是**错的，已撤回**。原因：本 v1 复用的是 current-YES v8 **replay** 机会集，它的 `decision_hour_local` 地板 13:00 是**策略决策窗口的政策产物，不是数据限制**。`reheat_feature_factory_v1` 本身有 **10:00+ 全日内覆盖**（近中午 10-12 有 1595 个 settled current-bracket NO 行 / 29 日期）。
>
> 直接在 factory 上回测（脚本 `research_current_yes_climbing_no_near_noon_v1.py`，join 校验：running_value 落在 bracket 内 97.9%；factory 13-17 = +3.7% 独立复现本 v1 的 +3.8%）：
>
> | 决策时点（午后峰值日 + runway≥2h） | n | NO ask | NO ROI | 95% CI |
> |---|---:|---:|---:|---:|
> | **近中午 10-12（用户字面入场）** | 1595 | 0.880 | **-0.7%** | [-2.8%, +1.4%] |
> | 逐时 h11 / h12 / h13 | — | — | -1.7% / -0.2% / -0.1% | — |
> | **晚盘 14-17** | 398 | 0.630 | **+8.1%** | **[+1.1%, +15.0%]** |
> | 逐时 h14 / h15 / h17 | — | — | +3.9% / +15.4% / +25.2% | — |
>
> **本线已被更完整的 canonical 线取代**（同笔交易、PIT-correct + 分类器）：见 [afternoon-peak classifier v1](2026-06-23-current-bracket-no-afternoon-peak-classifier-v1.md) 与 [prevday PIT shadow v1](2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md)。本文档保留作非 PIT 的早期诊断，其 forecast 特征有前视风险，数字以 PIT 线为准。
>
> **结论翻转：近中午版证伪**——峰值显然在数小时后,市场把当前档 NO 定价到 0.88-0.94,没有空间,ROI 平到微负。**真正显著为正的 edge 在晚盘(14-17,+8.1% 清 0)**,NO 便宜(0.63),是用户假设的**相反时点**,且贴近现有 fade_confirmed/current-YES 时点。下方 v1 正文的「梯度指向近中午」段落保留作记录,但以本订正为准。新结果见 [§2026-06-23 订正近中午直接回测](#2026-06-23-订正近中午直接回测)。

<a id="2026-06-23-订正近中午直接回测"></a>

## 一句话结论

在 `2026-05-20..2026-06-14` current-YES v8 replay 同一机会集上，预锁窄态「**午后峰值日 + 预报峰值仍在 ≥2h 之后**」买当前 running-max 档 NO 的超额 ROI 为 **+3.8%**，target_date block bootstrap 95% CI **[-0.4%, +7.7%]**——**显著性门 FAIL（低端差 0.4pp 跨 0）**，前瞻门 PASS（holdout +7.0%，CI [+1.6%, +13.1%]）。结论等级 **inconclusive，不可推 live、不开 broad shadow**。

但它是整条 NO 调查里第一条**机制自洽的正向线索**：条件化把错价**翻号**了。

## 为什么不直接丢弃（与 6/22 宽口径 -10.6% 的关系）

[当前档 NO 盘口 EV v1](2026-06-22-current-yes-no-side-overround-ev-v1.md) 的结论是 broad 反手买当前档 NO 负显著（-10.6%），机制是两边都抽水（overround +5.9pp），NO 被点差吃光。本轮不是推翻它，而是**条件化后错价反向**：

| 口径 | true NO 命中 | NO ask | NO edge | 含义 |
|---|---:|---:|---:|---|
| broad（全 contains_running） | 30.9% | 0.346 | -3.7pp | NO 被**高估**，买就亏 |
| 午后峰值日（全 runway） | 35.8% | 0.396 | -3.7pp | 仍被高估 |
| **+ 预报峰值 ≥2h 之后** | **73.3%** | **0.706** | **+2.7pp** | NO 被**低估**，买 +EV |

在「峰值还远」这个态里，市场对「当前档会被打穿」定价**不足**（真命中 73.3% > NO ask 70.6%），而同 slice 买 YES 是 -23.2%——方向上 NO 确实是对的一侧，问题只剩 overround（本 slice +5.4pp）有没有留够空间。这条 slice 给出的答案是：**勉强留够，但不显著。**

## 目标指标与分母

`climbing_no_peak_runway_ev` = 在 current-YES v8 replay 同一 opportunity-grain settled 分母上，买当前 running-max 档的真实 NO ask，按预锁窄态过滤后的净结果。

- 分母：`contains_running=True`、settled label 非空、同 snapshot 可取真实 current NO ask（来自 orderbook snapshot `outcome='no'`，非 `1-yes_ask`）。
- 预锁 PRIMARY 窄态（**看 CI 之前就定死，按机制定义，不挑最优格**）：
  `forecast_peak_hour_local ∈ [13,19]`（午后峰值日）**且** `forecast_peak_hour_local - decision_hour_local ≥ 2`（峰值仍在 2h+ 之后）。
- 零模型：市场 NO ask 当概率，NO EV=0；超额 ROI = realized NO ROI。
- 价格：买在 ask 已扣点差，未另加 fees。
- 推断：target_date block bootstrap，5000 reps，SEED 固定。
- 前瞻：按 target_date 前 70%（`..2026-06-06`）train / 后 30%（`2026-06-07..`）holdout。

## 数据快照（继承自上游，未重新 sync）

本轮是对 6/22 上游同一 settled replay 数据的**再切片**，不是新的 live_real PnL 发布；故继承上游快照与已通过的 CLOB gate，未重新 sync/rebuild。

- 继承 DB mtime（local）：`2026-06-22T00:42:48`
- 继承 `MAX(fact_trades.fact_built_at_utc)`：`2026-06-21T16:42:28+00:00`
- 继承 CLOB gate：`gate_pass=true`
- 主窗口：`2026-05-20..2026-06-14`；PRIMARY slice n=618 across 26 target_date。
- unsettled：0（feature layer 仅 settled）。

## 点估计总览（输入，非结论）

| 窄态（逐步收紧） | n | dates | NO ask | true NO | NO ROI | YES ROI |
|---|---:|---:|---:|---:|---:|---:|
| 午后峰值日（全 runway） | 2402 | 27 | 0.396 | 35.8% | -9.4% | -4.1% |
| **+ 峰值 ≥2h 之后 [PRIMARY]** | 618 | 26 | 0.706 | 73.3% | **+3.8%** | -23.2% |
| + 刚上穿 `minutes≤30` | 125 | 25 | 0.778 | 82.4% | +5.9% | -34.8% |
| + 1h&3h 仍升温 | 73 | 25 | 0.795 | 80.8% | +1.7% | -24.8% |
| + forecast_max > bracket 上沿 | 72 | 25 | 0.803 | 80.6% | +0.3% | -21.1% |

**用户其余筛选（刚上穿 / 升温 / forecast 越档）不加分**：点估计仍正，但 n 塌到 72–125、CI 全部跨 0。edge 几乎全部来自 PRIMARY 的 `runway≥2h` 这一条，不是六条叠加。

## 推断表（带 CI）

| 指标 | 值 | 95% CI | 门 |
|---|---:|---:|---|
| PRIMARY NO 超额 ROI | +3.8% | [-0.4%, +7.7%] | significance **FAIL** |
| PRIMARY vs NO-EV=0 基准 | +3.8% | 同上 | baseline **FAIL** |
| holdout NO ROI（后 8 日期） | +7.0% | [+1.6%, +13.1%] | forward **PASS** |
| train NO ROI（前 18 日期） | +2.5% | [-2.9%, +7.6%] | （挑选窗，不判门） |

### runway 阈值敏感性（单调，非 knife-edge）

| 峰值 runway 阈 | NO ROI |
|---|---:|
| ≥1.0h | +1.5% |
| ≥1.5h | +3.8% |
| ≥2.0h | +3.8% |
| ≥2.5h | +6.4% |
| ≥3.0h | +6.4% |

**峰值越远，NO edge 越大**——这是 mechanistically 自洽的梯度，不是挪一格就翻号的过拟合。

### PRIMARY 内按 NO 价分桶（去退化检验）

| NO ask 桶 | n | true NO | NO ROI | 95% CI |
|---|---:|---:|---:|---:|
| <0.60 | 194 | 35.1% | +20.1% | [-4.2%, +47.8%] |
| [0.60,0.80) | 85 | 71.8% | +0.8% | [-9.2%, +11.8%] |
| ≥0.80 | 339 | 95.6% | +1.4% | [-1.1%, +3.7%] |

edge **不是** degenerate 集中在 0.95 近二元桶（那桶只 +1.4%）。聚合 +3.8% 由高方差的便宜 NO 桶（<0.60，+20% 但 CI 宽）和近校准的贵桶混出，**单独看没有一个子桶过门**——这是聚合 +3.8% 勉强跨 0 的脆弱来源。

## 三门判定

| 门 | 结果 | 证据 |
|---|---|---|
| significance | **FAIL** | PRIMARY NO ROI +3.8%，95% CI [-0.4%, +7.7%]，低端跨 0 |
| baseline | **FAIL** | 相对 NO-EV=0 没有显著超额（同上 CI） |
| forward | **PASS** | holdout +7.0%，CI [+1.6%, +13.1%]，同号且自身清 0 |
| level | **inconclusive** | 显著性未过 → 不可 live、不可标 shadow_candidate |

## 交易动作

1. **不上 live、不开 broad shadow。** 按 contract，显著性门未过即 inconclusive。
2. 这是目前**最强的 NO 线索**（错价翻号 + 单调 runway 梯度 + holdout 自身清 0 + 样本 n=618 非 low-sample），值得作**零 notional shadow 遥测**累计前向证据——不是因为它已确认，而是 holdout 已过、前向累计是判它的正确方式。
3. ~~梯度指向用户的字面想法（临近中午、峰值更远）……~~ **【2026-06-23 撤回】** 该外推错误：近中午直接回测后 NO ROI -0.7%（NO 已定价到 0.88），edge 实际在晚盘 14-17（+8.1%）。见顶部订正。
4. 组合层纪律不变：同 `city/date/bracket` proposition 要与 `peak_forming_micro` / `fade_confirmed` 去重，避免同命题双押。

## 残余风险

- **口径地板**：分母 `decision_hour_local` 最早 13:00，「接近中午」字面入场不在样本内；`runway≥2h` 测的是**意图**（峰值前足够远），不是字面时钟。
- **多重检验**：定 PRIMARY 前看过多个嵌套 slice；PRIMARY 按机制（用户筛选 #2）而非最优 ROI 格选定，未做多重检验校正，单 slice CI 偏乐观。
- **regime**：主窗口止于 v8 feature layer `2026-06-14`，不覆盖 6/20 reheat 之后新 regime；holdout（后 8 日期）偏强可能含 regime 成分。
- **数据缺口**：降水（无 precip 列）、海风（无风向/海岸 tag）两条用户筛选当前无特征，未纳入；若它们能进一步压尾部亏损，本轮低估了窄态的真实 edge。
- **未发布 live_real PnL**：本轮纯 settled replay 反事实；CLOB gate 仅确认成交链路健康。
