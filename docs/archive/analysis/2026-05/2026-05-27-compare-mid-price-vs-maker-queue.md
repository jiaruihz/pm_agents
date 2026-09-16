# 执行策略对比：mid_price_core_v1 vs maker_queue_v1

> 分析日期：2026-05-27  
> 分析类型：M3 A/B 执行策略对比（fill 率 × 价格改善 × 实盘收益）  
> 数据源：`runtime/weather.db`（fills=1623，2026-05-26 last updated）  
> 对比维度：**execution_policy**（信号来源相同，只换下单算法）

---

## 数据快照

| 项目 | A mid_price_core_v1 | B maker_queue_v1 |
|---|---|---|
| config_id | `4ef9b3ec3e2e` | `c13ccf0c3181` |
| 运行时间 | 2026-05-16 ~ 2026-05-26 | 2026-05-24 ~ 2026-05-26（仅 3 天） |
| live_enabled | ✅ | ❌（paper only）|
| paper_enabled | ✅ | ✅ |
| 算法版本 | mid_price_core_v1 | mid_price_core_v1（信号相同）|
| 执行策略 | mid_price_core_v1 | maker_queue_v1 |
| notional / shares | $5 / 10 | $5 / 10 |
| entry window | 0.25–0.75 | 0.25–0.75 |
| min_edge | 10% | 10% |
| unsettled（DB 口径）| 18.0% | 目标日 5/27–5/28 尚未结算 |
| missing_bracket | 6 | 0 |

> B 目前**仅 paper 模式**运行（live_enabled=false），因此 CLOB 成交数据来自 A。B 的 CLOB 数据意味着 B 的 maker 限价单也在 Polymarket 上挂出并等待成交（paper 和 live 单同时下，paper 即时模拟成交，live 进入 CLOB 等待）。

---

## 总览对比

| 指标 | A PAPER | A LIVE（CLOB） | B PAPER | B LIVE（CLOB） |
|---|---|---|---|---|
| 信号生成期 | 5/16–5/26 | 5/16–5/26 | 5/24–5/26 | 5/24–5/26 |
| 总 orders | 217 | 217 | 78 | 87 |
| 成交 fills | 217 | 151 | 78 | 48 |
| **Fill 率** | **100%** | **69.6%** | **100%** | **55.2%** |
| 未成交 | 0 | 66 | 0 | 39 |
| settled | 152 | 97 | 35 | 19⚠️ |
| Win rate | 67.1% | 66.0% | 54.3% | 42.1% |
| PnL | +$468 | **+$150.07** | +$49.78 | **−$5.97** |
| Cost | $760 | $344.78 | $175 | $69.91 |
| ROI | 61.6% | **43.5%** | 28.4% | **−8.5%** |

> ⚠️ B LIVE 仅 19 笔结算，统计置信度极低，不可下定论。

---

## 核心发现一：Fill 率 — maker_queue_v1 显著更低

```
A mid_price_core_v1:  CLOB fill率 = 69.6%  (151/217)
B maker_queue_v1:     CLOB fill率 = 55.2%  (48/87)
                      差距 = -14.4pp
```

**原因**：maker_queue_v1 以 maker 价格排队挂单（设置了 `quote_improvement_ticks=1`，比 mid-price 更优一档），不走 taker 吃单。这导致：
- 价格移动对你有利时：订单成交（更好价格）
- 价格没有回到你的挂单价时：订单过期未成交

mid_price_core_v1 挂 mid-price，更愿意当 taker，fill 率更高。

**城市级 fill 率对比（CLOB）——低于 70% 的城市**：

| 城市 | A fill% | B fill% | 差 |
|---|---|---|---|
| Beijing BUY_NO | 47% | 33% | -14pp |
| LA BUY_YES | 44% | 100%\* | — |
| Tokyo BUY_YES | 11% | — | — |
| Warsaw BUY_NO | 64% | 100%\* | — |
| Lucknow BUY_NO | 100% | **18%** | -82pp |
| NYC BUY_NO | 69% | 25% | -44pp |
| London BUY_NO | 100% | 40% | -60pp |
| Karachi BUY_NO | 75% | 25% | -50pp |
| Austin BUY_NO | 88% | 0% | -88pp |

> \*B 样本量极小（1–2 笔），不具统计意义

---

## 核心发现二：价格改善 — 有但微乎其微

B maker_queue_v1 在 CLOB 成交的订单上：

| 方向 | n | avg_entry | avg_fill | 改善（fill − entry） | 经济含义 |
|---|---|---|---|---|---|
| BUY_NO | 38 | 0.6279 | 0.6208 | **−0.0071** | 少付 0.7¢/share，总节省 $0.71/10 shares = **+$0.071/笔** |
| BUY_YES | 10 | 0.3071 | 0.3029 | **−0.0042** | 少付 0.4¢/share，总节省 $0.42/10 shares = **+$0.042/笔** |

**分布**：

| 类型 | BUY_NO | BUY_YES |
|---|---|---|
| 在 entry_price±0.005 内成交 | 84% (32/38) | 80% (8/10) |
| 比 entry_price 更优成交 | 16% (6/38) | 20% (2/10) |
| 比 entry_price 更差成交 | 13% (5/38) | 10% (1/10) |

**结论**：价格改善效果非常有限——平均每笔只节省 4–7 分钱，且大部分（80–84%）仍然在 entry_price 附近成交（paper 模拟价）。

---

## 核心发现三：EV 权衡——fill 率损失 > 价格改善收益

|  | 每信号期望收益估算 |
|---|---|
| mid_price：fill_rate × avg_pnl_per_fill | 69.6% × $1.55 ≈ **$1.08/signal** |
| maker_queue：fill_rate × (avg_pnl + price_impr) | 55.2% × ($1.55 + $0.07) ≈ **$0.89/signal** |
| **差距** | **−$0.19/signal（−17%）** |

> avg_pnl_per_fill 取 A LIVE 口径（$150.07 / 97 = $1.55/fill）。maker_queue 的价格改善按 BUY_NO 口径 +$0.07 估算。

**当前数据表明 maker_queue_v1 的 fill 率损失大于价格改善收益**，但样本量不足（B LIVE 仅 48 fills，19 settled），结论需更长时间窗口验证。

---

## Paper 口径 vs LIVE 口径差距（A 策略）

| 指标 | A PAPER | A LIVE | 差距 |
|---|---|---|---|
| Fill 率 | 100% | 69.6% | −30.4pp |
| PnL | +$468 | +$150 | −68% |
| ROI | 61.6% | 43.5% | −18.1pp |
| Win rate | 67.1% | 66.0% | ≈ 相同 |

**Paper 大幅高估了绩效**。主要原因是 paper 假设 100% fill，而实盘只有 69.6%。实盘里大量挂单价不够有竞争力（特别是 BUY_YES 和深度不足城市），导致 30% 的单未成交。这部分"漏掉"的信号里，有些可能是 market 随后向你反向移动（即：没成交反而是好事），但整体上漏掉信号是 PnL 流失。

---

## A LIVE：城市绩效

| 城市 | n | PnL | Win% | ROI | 建议 |
|---|---|---|---|---|---|
| London | 11 | +$40.52 | 82% | +87.1% | ✅ 核心城市，维持 |
| Tokyo | 9 | +$22.72 | 89% | +101.9% | ✅ 最高 ROI |
| Miami | 11 | +$22.12 | 64% | +69.5% | ✅ 稳定 |
| LA | 12 | +$17.86 | 58% | +43.7% | ⚠️ fill 率低（44% BUY_YES） |
| Austin | 10 | +$17.27 | 70% | +46.0% | ✅ |
| Shanghai | 3 | +$14.95 | 100% | +100.0% | ✅（样本少）|
| NYC | 7 | +$13.91 | 71% | +74.1% | ✅ |
| Warsaw | 8 | +$11.08 | 75% | +46.7% | ⚠️ fill 率低 |
| Paris | 10 | +$10.71 | 60% | +24.6% | ⚠️ 边际盈利 |
| Chicago | 3 | +$1.33 | 67% | +12.3% | — |
| **Beijing** | 8 | **−$5.13** | 38% | **−17.2%** | ❌ CLOB fill 率 47%，且赢率低 |
| **Madrid** | 5 | **−$17.26** | 20% | **−71.6%** | ❌ 严重亏损，建议暂停或提高信号阈值 |

---

## 数据完整性自检

- [x] venue 已区分：paper=模拟成交，polymarket_clob=实盘 CLOB
- [x] settlement 无重复（1 settlement per fill 已验证）
- [⚠️] B LIVE 仅 19 settled，3 天数据，PnL/wr 无统计意义
- [⚠️] 5/27–5/28 目标日结算尚未完成（B 的近期信号未结算）
- [x] Paper ROI 高估实盘约 18pp（fill 率差异导致）
- [x] missing_bracket：A=6 笔，B=0 笔

---

## 结论与行动建议

### 1. maker_queue_v1 当前 fill 率太低，EV 可能为负

在 3 天的 LIVE 数据中，maker_queue_v1 的 CLOB fill 率只有 55.2%（vs mid 的 69.6%），价格改善仅 0.4–0.7¢/share（≈$0.07/笔），而损失的 fill 机会按估算降低了每信号期望收益约 17%。**建议延长观察期至 2 周再决定是否 live_enable=true**。

### 2. fill 率问题集中在特定城市

Lucknow BUY_NO（18%）、Austin BUY_NO（0%）、London BUY_NO（40%）、NYC BUY_NO（25%）是 B 的严重低 fill 率城市。可能是这些城市的 Polymarket 流动性不足，maker 单很难成交。可以考虑对这些城市单独设置 taker 模式。

### 3. Madrid 和 Beijing 需要处理

- **Madrid**：LIVE wr=20%，pnl=−$17.26，ROI=−71.6%，是最大的 alpha 泄漏城市。信号质量问题还是盘口问题？建议看 Madrid 的信号分布（edge 是否真的有 10% 以上）。
- **Beijing**：CLOB fill 率 47%（BUY_NO），wr=38%。可能是 market 流动性太薄，挂单价成为锚点反而逆向。

### 4. Paper 不能用于评估 maker_queue_v1

Paper 对 maker_queue_v1 的模拟是**无效的**——paper 按 entry_price 即时成交，根本无法模拟 maker 排队等待的过程（50% 的时间根本成交不了）。只有 CLOB 数据才能评估 maker 策略的真实效果。当前 paper 的 28.4% ROI 不代表 maker 的真实潜力。

### 5. 优先建议

| 优先级 | 行动 |
|---|---|
| P0 | 保持 maker_queue_v1 paper-only，继续积累 CLOB fill 数据 |
| P0 | Madrid 深入排查（信号质量 + 流动性），考虑暂停 |
| P1 | 2 周后，用 CLOB settled 数据重跑本分析 |
| P1 | 对 Lucknow/Austin/NYC 等低 fill 率城市，测试 maker 参数（放宽 quote_improvement_ticks） |
| P2 | 比较 mid_price vs maker_queue 在**相同日期**（5/24–5/26）的表现，控制时间变量 |
