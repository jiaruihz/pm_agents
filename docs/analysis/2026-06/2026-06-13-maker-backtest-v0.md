# reheat-risk maker 回测 v0（衰竭后 NO 挂单收点差）

Status: snapshot
Updated: 2026-06-13
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md
前置: 2026-06-12-m3-exhaustion-no-strategy-v0.md（白名单 taker 三层证据关闭，唯一遗留方向 = maker）

## 问题

前序报告已证：36 个白名单城市 taker 买 NO 无利润（市场校准近乎完美，
全部公开信息组合后 ROI≈0，点差吃掉一切）。剩余唯一假设：温度过峰后我们
对结果的确定性 ≈ 市场，那么**挂单方（maker）赚点差而不是付点差**也许为正。
本报告用 25 天完整双侧盘口快照回测该假设。规则全部预注册，无事后调参。

## 预注册规则

城市当地 14-18h、衰竭确认（current temp 距当日 running max 回落 ≥1.0°C）后，
对高于 running max 的 d1/d2 档位 NO 侧，每 city-day-bracket 一次：

- **M1（主规则）**：`best_no_bid + 0.01` 挂买单（改善一档），挂到当地 21h，成交后持有到结算
- **M2（对照）**：同一快照直接吃 `best_no_ask`（已证≈0 的 taker 基准）
- **reheat-risk（变体）**：挂 `NO mid − 0.01`

成交模拟（保守主口径）：挂单后若后续快照 `best_no_ask ≤ p` 视为成交（卖方
穿过我们价位），成交价 = 挂价。乐观敏感性口径：未保守成交但我们保持 best bid
≥2 个连续快照且 bid 侧有变化 → 按 50% 概率计入，仅作参考。M1 挂价≥当时 ask
时按立即吃单处理（`immediate_cross`，本质是 taker）。

预注册 kill 条件：M1 白名单 fill 率 <5% / filled ROI ≤0 / 日度 t <1 任一
→ negative；仅当 fill≥5% 且 ROI>0 且 t≥1.5 → promising。

## 数据与脚本

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_maker_backtest.py
```

- 盘口：`runtime/weather_edge_v1/market_data/orderbook_snapshots/`（30 分钟快照，自行 min/max 取 best，不信任文件内排序）
- 信号：白名单用 wu_obs 逐小时 running max / current temp（m3_observed_max_v3_h10_21）；修复 6 城（Paris/London/Milan/Chicago/KualaLumpur/PanamaCity）用官方站 IEM（official_station_running_max_v0）
- 结算：pm_history 单 winner（非单 winner city-day 剔除）；"or below" 底档剔除；只交易 pm_history 中存在的档位 label
- 样本：2026-05-19..06-09（22 个已结算交易日），757 个衰竭 city-day，600 个 city-day 实际有报价可挂
- 产物：`generated/m3_maker_backtest_v0/`（trades / summary / manifest）

## 结果（保守口径，主结论）

| 规则 | 组 | 挂单 | fill率 | 成交胜率 | filled ROI | 日度 t | 正天数 |
|---|---|---:|---:|---:|---:|---:|---|
| **M1 挂 bid+1c** | **白名单** | **420** | **30.7%** | 76.0% | **−12.6%** | **−4.18** | 2/21 |
| M1 挂 bid+1c | 修复6城 | 103 | 26.2% | 85.2% | +4.3% | +0.50 | 15/17 |
| M2 吃 ask（对照） | 白名单 | 430 | 100% | 92.6% | −1.3% | −1.27 | 10/22 |
| M2 吃 ask（对照） | 修复6城 | 110 | 100% | 96.4% | **+7.4%** | **+3.68** | 20/22 |
| reheat-risk 挂 mid−1c | 白名单 | 884 | 8.6% | 57.9% | −29.4% | −4.93 | 1/20 |
| reheat-risk 挂 mid−1c | 修复6城 | 153 | 9.8% | 73.3% | −4.4% | −0.29 | 8/10 |

乐观敏感性口径（50% 计入"保持 best bid 且有活动"的未穿越单）几乎不动结论：
M1 白名单 −11.5%、reheat-risk 白名单 −25.4%。

### 拆解：maker 的"成交"全是逆向选择

M1 成交按类型拆开（白名单）：

| 成交类型 | n | 胜率 | ROI | 日度 t |
|---|---:|---:|---:|---:|
| `immediate_cross`（挂价锁/穿盘口，实为 taker） | 69 | 95.7% | +1.1% | +0.4 |
| `passive_ask_crossed`（真正被动成交） | 60 | **53.3%** | **−31.7%** | −4.4 |

- 真正的被动成交（卖方穿过我们价位）胜率只有 53%（d1 仅 39%、ROI −48%），
  而同期 taker 胜率 92.6%。**愿意在衰竭后把 NO 砸到我们 bid 上的人，就是
  拿着更新信息（温度二次回升 / 实时预报）的人。**
- 逆向选择直接可测：M1+reheat-risk 全部 136 笔白名单被动成交里，54 笔在成交时刻
  官方 running max（小时粒度）已经抬升，这些单胜率仅 40.7%；即使 running max
  名义未动的 82 笔也只有 65.9% 胜率、ROI −16.9%（亚小时信息我们的代理看不见）。
- M1 整体 ROI 里唯一不亏的部分是 `immediate_cross`——那不是 maker 收益，
  是点差≤1 tick 时退化成的 taker（+1.1% ≈ M2 的 −1.3%，同量级）。
- 修复 6 城同构：M1 名义 +4.3% 全部来自 16 笔 immediate_cross（+13.7%，即已知
  的官方站 taker edge）；11 笔真被动成交 ROI −12.3%。**站点 basis edge 用
  taker 表达（M2 +7.4%，t=3.68，复刻前报告 +5.5%/t=3.0）严格优于 maker 表达。**

### 经济解释

衰竭后 NO 的公允价 ≈ ask（前报告已证 ask 校准近乎完美）。挂在 bid+1c 等于
报一个比公允低 3-6c（t0 中位点差 3c）的买价；这张单只有两种命运：
(a) 没人理（69-91% 的情况），(b) 被知道温度正在回升的人成交。收的点差
（被动成交均价 0.78 vs 同刻 ask 0.84，~6c）远小于逆向选择成本（胜率从
93% 掉到 53%，~40c 期望损失差）。温度市场的"流动性提供"在结构上是给
实时信息更优者写期权。

## 成交模拟的局限性（必读）

1. **30 分钟快照看不到逐笔**。保守规则（ask 穿越才算成交）是 fill 数量的
   下界，但**系统性偏向把"价格穿过我们"的逆向成交计入、漏掉"急躁卖方
   砸到 best bid 但 ask 未动"的良性成交**——即成交构成可能比模拟的更温和。
   乐观口径（+50% 计入保持 best bid 且有活动的单）就是为此设的敏感性：
   结果仍然显著为负，说明良性成交即使全计入也填不平逆向选择的坑。
2. 未建模排队位置 / 部分成交 / 撤单重挂；假设成交价 = 挂价。
3. 逆向选择指标用小时粒度 METAR running max 近似"成交时刻信息变化"，
   低估了亚小时信息流（runmax 名义未动的被动成交仍然亏损即为证据）。
4. 挂价 tick 统一按 0.01 近似；>0.99 的挂单按 sanity 剔除（181 笔 M1，
   多为深度 ITM NO，无点差可收）。
5. 样本 22 个结算日、单一季节（5-6 月对流季）；fill 率与逆向选择强度
   可能随季节变化，但方向性结论（被动成交=逆向选择）是结构性的。
6. 容量背景：即便不亏，M1 白名单按每单 1 share 口径日均成交名义仅 ~$5，
   top-of-book 中位深度 ~37 share——本来也只是小钱。

## Verdict（按预注册 kill 条件）

**negative，不建议 shadow。**

- M1 白名单：fill 率 30.7%（通过 ≥5%），但 filled ROI = **−12.6% ≤ 0** 且
  日度 t = **−4.18 < 1** → 触发 kill 条件二。
- maker 不是"免点差的 taker"：它把校准完美市场里的零和换成了对实时信息
  更优者的负和。白名单的 maker 路线与 taker 路线（三层证据）一并关闭。
- 修复 6 城的站点 basis edge 继续用 **taker** 表达（M2 +7.4% / t=3.68，
  与前报告一致），已在 station_basis_shadow 跑 shadow；maker 变体
  （前报告下一步第 3 条）经本回测证伪，从待办中移除。

## 产物

- `generated/m3_maker_backtest_v0/m3_maker_backtest_trades.csv`（3,441 行：每笔挂单的城市/日期/时点/档位/挂价/状态/成交类型/成交时点/结算/pnl/逆向选择指标）
- `generated/m3_maker_backtest_v0/m3_maker_backtest_summary.csv`（规则×组×距离汇总）
- `generated/m3_maker_backtest_v0/manifest.json`（预注册规则、输入、样本统计）
