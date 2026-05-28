<!--
  M3 A/B 对比报告
  口径：docs/WEATHER_ANALYSIS_CONTRACT.md
-->

# 策略对比：原城市池(ORIG) vs 新城市池(NEW) + maker_queue vs mid_price_core

> 时间窗：2026-05-16 — 2026-05-27（北京时间，已结算口径；今日 5-28 全盘未结算）
> 对比维度：city_pool 分组（首次下单 ≤5-21 = ORIG vs ≥5-26 = NEW）；附 execution_policy 维度
> 数据源：weather.db（`runtime/weather.db`，live config only）

## 数据快照

| 项目 | A（ORIG 原城市池） | B（NEW 新城市池） |
|---|---|---|
| 数据快照时间 | 2026-05-28 23:07:38 +0800（DB 重建） | 同 |
| fills 行数（已结算） | 349 | 45 |
| unsettled 占比（全 live 池） | 整体 262/656 = 40%（今日 5-28 全开） | 同 |
| missing_bracket 数 | 111（全 live） | 同 |

> 说明：NEW 城市 5-26/5-27 才入池，结算 T+1，故已结算样本仅 5-27 一天（n=45）；今日 5-28 的 137 笔尚未结算。

## 对比设定

- **Selector A（ORIG）**：5-16~5-21 首次进入 live 下单的 12 个核心城市——Austin / Beijing / LA / Miami / NYC / Shanghai / Tokyo / London / Paris / Warsaw / Madrid / Chicago。
- **Selector B（NEW）**：5-26 起扩池新加城市——Ankara / Guangzhou / Istanbul / Karachi / Lucknow / Moscow（5-26）、Jeddah / Seattle（5-27）、BuenosAires / Chengdu / Manila / Munich / Singapore（5-28）。全部已升入 `city_pool=t1_trading`。
- **对齐方式**：仅比已结算 fill。两组时间窗不完全对齐（NEW 仅 5-27 有结算结果），ROI 可比但 PnL 绝对值不可比（量差 8×）。

## 总览对比

| 指标 | A (ORIG) | B (NEW) | delta (B−A) | delta% |
|---|---|---|---|---|
| PnL (USD, fill 口径) | 730.84 | 55.55 | −675.29 | 量差驱动，不可比 |
| ROI | 47.1% | 28.6% | −18.5pp | −39% |
| Win rate（by count） | 63.3% | 57.8% | −5.5pp | −9% |
| Win rate（by notional） | 62.9% | 56.7% | −6.2pp | −10% |
| fills 数 | 349 | 45 | −304 | — |
| 总 cost (USD) | 1550.42 | 194.46 | −1356 | — |

## 切片对比：by_date

| 日期（北京时间） | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|
| 2026-05-20 | 232.3 | — | — | 87% | — |
| 2026-05-22 | 111.7 | — | — | 76% | — |
| 2026-05-25 | 102.3 | — | — | 60% | — |
| 2026-05-26 | 30.6 | — | — | 50% | — |
| 2026-05-27 | 68.9 | 55.5 | −13.4 | 61% | 58% |

> NEW 仅 5-27 有结算样本，其余日期无 NEW 数据。5-27 当天两组都为正（ORIG +68.9 / NEW +55.5）。

## 切片对比：by_model

| model | A pnl | B pnl | delta | A roi | B roi |
|---|---|---|---|---|---|
| open_meteo_live_gfs | 493.19 | 20.75 | −472 | 48.9% | 43.9% |
| open_meteo_live_ecmwf | 237.64 | 34.79 | −203 | 43.8% | 23.6% |

> NEW 的 ecmwf 腿明显偏弱（23.6% vs 43.8%）；gfs 两组接近。

## 切片对比：by_side

| side | A pnl | B pnl | delta | A roi | B roi |
|---|---|---|---|---|---|
| BUY_NO | 527.77 | 76.90 | −451 | 45.7% | 44.4% |
| BUY_YES | 203.07 | −21.35 | −224 | 51.4% | −100.0% |

> **关键**：NEW 的 BUY_YES 腿 6 笔全亏（−100% ROI，0% 胜率）；ORIG 的 BUY_YES 仍为正。BUY_NO 两组都健康。

## 显著差异 Top-N

**A(ORIG) 显著优于 B(NEW)：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|
| by_side | BUY_YES ROI | +51.4% | −100% | −151pp |
| by_model | ecmwf ROI | +43.8% | +23.6% | −20pp |
| 总览 | ROI | +47.1% | +28.6% | −18.5pp |
| by_city | Ankara ROI | — | −29.0% (n=8) | 新池最大失血点 |
| by_city | Karachi ROI | — | −6.3% (n=9) | 新池次差 |

**B(NEW) 显著优于 A：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|
| by_city | Jeddah/Guangzhou/Istanbul | — | +100% (各 n≤4) | 小样本噪声，勿当信号 |

## 附：execution_policy 维度（已结算 live，全池）

| 策略 | n | win% | pnl | cost | roi |
|---|---|---|---|---|---|
| mid_price_core_v1 | 291 | 66.0% | 684.35 | 1287.34 | **+53.2%** |
| maker_queue_v1 | 101 | 54.5% | 110.94 | 448.64 | **+24.7%** |
| maker_queue_v2（今日新上） | 2 | 0% | −8.90 | 8.90 | **−100%** |

| 策略 × 方向 | n | win% | roi |
|---|---|---|---|
| mid_price / BUY_NO | 216 | 71% | +48.5% |
| mid_price / BUY_YES | 75 | 52% | +67.6% |
| maker_queue_v1 / BUY_NO | 79 | 63% | +37.5% |
| maker_queue_v1 / BUY_YES | 22 | 23% | **−24.7%** |
| maker_queue_v2 / BUY_YES | 2 | 0% | −100% |

## 数据完整性自检

- [x] A 和 B 时间窗未完全对齐——已在"对比设定"注明（NEW 仅 5-27 有结算样本）
- [x] A 和 B 城市池范围一致（均 t1_trading，已在"对比设定"注明分组依据）
- [ ] 双方 unsettled 占比 < 20%——**不满足**：全 live 池未结算 40%（今日 5-28 整盘未结算），结算对比仅用已结算子集

## 观察与建议

**为什么"今天一直亏钱"——先定性：结算是 T+1，今天(5-28)的 137 笔单全部未结算，账面红字是浮亏，未实现。** 已实现的结算曲线到 5-27 仍为正（5-27 = +124，ROI +31.5%）。DB 无当前盘口 mark，signal 时点价做 MTM 恒≈0、不可用——精确浮盈亏请看 live dashboard（`/weather/live`）。

**但今天的浮亏方向有据可循——昨天两处改动都偏弱：**

1. **maker_queue 整体弱于 mid_price_core**：ROI 24.7% vs 53.2%，几乎腰斩。今天 mq_v1 又换成 mq_v2，已结算样本只有 2 笔且全亏，等于在无证据的新执行策略上放量。
2. **maker_queue 的 BUY_YES 腿在出血**：mq_v1/BUY_YES −24.7%、23% 胜率，而 mid_price/BUY_YES +67.6%。maker 挂单逻辑在 YES 腿吃了逆向选择。
3. **新城市池整体弱**：ROI 28.6% vs 原池 47.1%；其中 Ankara −29%、Karachi −6% 是失血点，+100% 的几个城市都是 n≤4 噪声。新池 BUY_YES 6 笔全亏。

**建议：**
- 立即砍 maker_queue 的 BUY_YES 腿，只保留 BUY_NO（与已知 BUY_NO≫BUY_YES 的 edge 一致）。
- maker_queue_v2 今天刚上、零有效结算证据，降到 paper 或限 notional，等≥1 个完整结算日再评估。
- 新城市先缩量/转 research，Ankara、Karachi 暂停 live，待累计更多结算日。
- 今天的真实盈亏明天 5-28 结算后才能下结论，当前红字勿当已实现亏损。
