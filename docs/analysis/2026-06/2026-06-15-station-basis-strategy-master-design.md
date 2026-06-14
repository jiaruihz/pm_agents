# Station-Basis 天气策略 · 完整设计与思路（主文档）

Status: current-reference
Updated: 2026-06-15
Source of truth: 策略设计与决策依据（实盘状态以 live-prep gate / 实际部署为准）
Used by: WEATHER_DOCS_INDEX.md
汇总以下证据文档（按时间）：
- 2026-06-11-m3-tail-no-retail-diagnosis-v0
- 2026-06-12-official-resolution-source-and-entry-timing-v0
- 2026-06-12-m3-exhaustion-no-strategy-v0
- 2026-06-13-settlement-basis-batch2-v0 / forecast-basis-sleeve-v0 / maker-backtest-v0 / station-basis-execution-design
- 2026-06-14-executability-reconcile-v0 / station-basis-live-candidate-v1

---

## 0. 一句话

Polymarket 天气温度市场用**特定气象站**结算；部分城市做市方盯的是**另一个站**。
我们盯官方站的实测温度，在当日高温已确定后，对"已不可能达到的高温档"做方向交易。
edge 不来自"预测更准"，而来自"对手用错了温度计"。

## 1. 市场机制（为什么这个市场可被理解）

- 每个城市每天一组互斥档位市场（如 Paris 每 1°C 一档；美国城市每 2°F 一档）。
- 结算 = 当日某官方气象站最高温落在哪个档。规则写在 market description 里，
  指明 resolution source（多数是 Wunderground 某 ICAO 机场页面）。
- 价格由边际交易者磨成：天气做市 bot 用 ECMWF(51)/GFS(31) ensemble 成员占比
  算公允概率，挂双边收点差。**市场价 ≈ 最强 ensemble 的成员占比**——所以
  "比市场算得更准"是红海（见第 5 节五重证负）。

## 2. 核心 edge：站点 basis

market description 抓取（52 城）发现两类错配：

| 类型 | 城市 | 证据 |
|---|---|---|
| 官方站 ≠ 我们/市场默认站 | Paris→LFPB(非戴高乐)、London→EGLC(非希思罗)、Milan→LIMC(非利纳特)、Chicago→KORD(非中途)、KualaLumpur→WMKK、PanamaCity→MPMG | 官方站 IEM 实测 round(max) vs pm_history winner 对齐 ~100%；用错站只有 22-64% |
| 非 WU/特殊精度 | HongKong→HKO 一位小数 + **floor** 映射（30.4→"30"），8/8→100% | batch2 |
| 站名一致但 feed 不同（未解） | Moscow 89% / Seoul 78% / Shenzhen 7% | 根因未明，**不交易** |

旁证：公开 GitHub 天气 bot（suislanchez 等）配置表里 NYC=KNYC、Denver=KDEN
——而官方站是 KLGA/KBKF。**市场参与者确实在用错站，这是可持续的对手错误。**

可交易城市池：**Paris / London / Milan / Chicago / KualaLumpur / PanamaCity**
（+ Jakarta WIHH 小样本待累积；HongKong 需小数 floor 适配）。

## 3. 信号与规则

决策变量（决策时刻、官方站 METAR）：
- `running_max` = 当日至今最高温；`running_value` = round(running_max) 映射到市场档
- `decline` = running_max − 当前温度（"衰竭"幅度，温度从峰值回落多少）

两条表达（同一物理判断"今天不会再升档"的两面）：
- **YES 官方档**：买 running_value 所在档的 YES（赌就停在这档）。最强：
  历史 executable ROI +11.7~+17%，per-city-day 可成交率 83%。
- **NO 衰竭 d1/d2**：decline≥1°C 后，买 running_value 上方 1/2 档的 NO（赌到不了）。
  NO d1 +7.3%。

硬约束（来自物理精算表）：
- **13h 前不入场**（早晨假峰值，10h "衰竭"后仍 40% 穿档）。
- 穿档概率分层：危险城市（Helsinki/Amsterdam/NYC 高纬海洋性）尾部不为零；
  零穿档城市（SaoPaulo/Beijing/Singapore/热带）最安全。

入场前**硬门**：抓当天 market description 重新核对官方站未变（站点历史会漂移，
mismatch→告警+跳过）。

## 4. 可成交性（最关键的现实约束）

回测的 +17% 是"**条件于存在可成交 ask**"的 ROI。可成交性对账（2026-06-14）：

- 全集 executable 占比：YES 57%（per city-day 83%）、NO d1 24%（47%）、NO d2 8%。
- ask 真能吃（中位 21-57 股，非 dust）。
- **强依赖时点与城市**：NO d1 14h 可成交 66% → 17h 仅 3%（越晚越收敛）；
  Paris/London/Milan 最佳，PanamaCity/Chicago 最差。
- 每周约 YES 30 + NO d1 10 个可成交机会（6 城，3 周样本外推）。

⚠️ **taker 的残余风险**：30 分钟快照里"存在 ask" ≠ 我们按下单键那一秒还在。
N100 v1 实时审计（PanamaCity/Chicago @15h）抓到 0/41 可成交——经诊断是
**采样偏差**（最差城市 + 已收敛时点），非策略死亡，但说明 taker 实时 fill
确有不确定性。

## 5. 已证伪的方向（不要回头）

| 方向 | 结果 | 证据 |
|---|---|---|
| 白名单 36 城 taker 买 NO | 全负（-1.5~-5.6%） | exhaustion-no |
| 白名单截面模型选 NO | 选中组仍负 | cross_section（市场 ask 校准近乎完美 0.917↔0.917）|
| 白名单 NWP 条件化 | 全套公开信息后 ROI≈0 | nwp_locked（市场已含预报）|
| 白名单 maker | filled ROI -17.7% | maker-backtest（点差是逆向选择补偿）|
| 白名单改买 YES | 也负（overround 1.9% 对称） | overround 测试 |
| 早场预报 sleeve | 仅 Milan/London 成立，需逐市场 anchor gate | forecast-basis-sleeve |

主线：**钱在"比市场知道得多"（站点 basis），不在"比市场算得准"。**

## 6. 执行决策：本次只用 maker

理由：taker 实时能否吃到只有实盘能验，而 maker 是仓库历史上验证过的形态，
且现有执行器 `weather_order_executor.py` **默认即 maker-only**（无需绕护栏）。
maker = 在档位上挂 resting 买单，等对手砸盘成交，收点差/捡便宜，而不是付点差。

maker 下单价（**开火前需最终确定的唯一参数**）：
- 不能"挂买一"——买高档 NO 挂在 0.99 风险收益极差。
- 应挂在能捡到便宜的水平（maker-backtest M1 用 best_bid+1tick；basis maker 历史 +8%）。
- 执行器 `_maker_only_price`：requested_price < best_ask 则用 requested_price 挂 resting，
  否则回落到 best_bid。需要在 plan 里给出**明确的目标挂价**（绝对水平或相对 bid）。
- ⚠️ 与 N100 v1 track 的口径需调和（v1 选 16h/排除 Milan/ask≤0.90；本对账说 14h 更可成交）。

## 7. 风险与资金边界（station_basis_guards.py，8 例单测）

| 参数 | shadow-parity | live pilot |
|---|---|---|
| 单笔上限 | $5 | **$1** |
| 单城/日 | $15 | $3 |
| 全局/日 | $50 | $10 |
| 日亏损停 | -$20 | -$5 |
| 并发 / 单城 | 40 / 8 | 12 / 3 |
| kill switch | `runtime/.../station_basis_shadow/PAUSE` 存在即全拒 |

fail-closed；day-state 从账本重建，重启不重置额度。

## 8. 系统架构（三层 + 现有下单器）

```
[信号] weather_station_basis_shadow.py  实时官方站METAR+Gamma/CLOB+rules硬门 → entries.jsonl
[风控] weather_station_basis_exec.py     消费entries→RiskGuard→orders.jsonl（dry_run默认）
[桥接] station_basis_to_plans.py         风控通过的单→executor plan（maker_only=True）
[下单] weather_order_executor.py         现有maker-only实盘器（--live --confirm-live）
[评估] station_basis_eval.py             shadow vs回测 + 断流 + go/no-go(≥40结算)
```

数据/产物全在 `runtime/weather_edge_v1/station_basis_*`。N100 另有 v1 track
（`/home/jiarui/projects/pm_agent_station_basis_v1`，codex 分支，更细的 live-candidate-v1
+ live-prep gate），两套需收敛为一套生产口径。

## 9. Path to Live

| # | 条件 | 状态 |
|---|---|---|
| 1-2 | 实时 METAR + rules 站点硬门 | ✅ |
| 3 | 风控边界（8 例单测） | ✅ |
| 4 | dry-run 执行 + 审计账本 | ✅ |
| 5 | 断流监控 | ✅ |
| 6 | 可成交性查清（真实但稀有） | ✅ 2026-06-14 |
| 7 | maker 挂价规则最终确定 + 与 v1 口径调和 | ⏳ 开火前最后一步 |
| 8 | maker live 小额实盘（$1/笔，验证实时 fill） | ⏳ |
| 9 | shadow/live ≥40 笔结算符号一致（go/no-go） | ⏳ |
| 10 | 规模化 / N100 7×24（weather-strategy-deploy git-first） | ❌ |

## 10. 预期（$10/日本金量级）

每周约 YES 30 + NO 10 可成交机会；maker 下 fill 率打折后实际成交更少。
预期日盈利**几美元**，月级别几十美元——这是**机制验证规模**，不是生意规模。
扩规模靠：更多城市（HK/Jakarta/找更多错配站）+ 更深档容量 + maker 队列优化。
真实价值排序：**先证明机制在真钱下成立（实时 fill + 正期望），再谈放大。**
