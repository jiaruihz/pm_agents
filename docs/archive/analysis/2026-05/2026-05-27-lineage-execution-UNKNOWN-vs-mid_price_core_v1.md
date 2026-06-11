# 执行血缘分析：UNKNOWN vs mid_price_core_v1 下单算法差异

> 分析日期：2026-05-27  
> 分析类型：M2 执行血缘（信号→挂单价→成交价→PnL）  
> 数据源：镜像 CSV `t24_paper_ledger_trades.csv`（DB 为空）  
> 口径修正：**仅计入 `eligible_for_paper_order=True` 或 `city_pool=t1_trading` 的行**

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | `runtime/.../research/t24_paper_ledger_trades.csv` |
| 数据快照时间 | 2026-05-25 23:07:34（file mtime） |
| UNKNOWN 纳入行数 | 257（eligible/t1） |
| mid_price_core_v1 纳入行数 | 205（eligible/t1） |
| unsettled（mid） | 48 / 205（23%）⚠️ |

---

## 数据质量修正：$308 是怎么来的

**结论：$308 不是"错误数据"，但来自不应纳入绩效口径的城市。**

5/20 mid_price_core_v1 全部 117 笔拆分：

| 分组 | 笔数 | PnL | 说明 |
|---|---|---|---|
| t1_trading + eligible=True | 19 | **+$35.40** | 真实可对标的绩效 |
| t2_research + eligible=False | 98 | **+$272.76** | 研究池城市，未开放实盘 |
| **合计** | **117** | **+$308.16** | ledger summary 全量口径 |

t2_research eligible=False 的 $272 来自 Istanbul/Ankara/Moscow/Karachi/Lucknow/Jeddah 等新城市，5/20 恰好是这批城市大涨日，但这些城市**不在实盘交易范围**。

**修正后对比（eligible/t1 口径）：**

| 指标 | UNKNOWN | mid_price_core_v1 | Δ |
|---|---|---|---|
| 总 PnL | **+$30.15** | **+$91.80** | +$61.65（+204%） |
| Win rate | 42.6% | 59.9% | +17.3pp |
| 已结算笔数 | 244 | 157 | −87 |
| ROI | 2.8% | 8.2% | +5.4pp |

---

## 执行血缘对比：信号 → 挂单 → 成交

### BUY_NO 方向

| 阶段 | UNKNOWN | mid_price_core_v1 | 差异 |
|---|---|---|---|
| 信号（YES market price） | 均值 **0.311** | 均值 **0.374** | mid 选了 YES 价更高的信号（NO 更便宜） |
| 挂单价（entry_price） | **= 1 − signal**（硬补价） | **= 1 − signal**（同公式） | 挂单公式完全相同 |
| entry vs (1−signal) diff | **0.0000** | **0.0000** | 无差异 |
| 价格窗口（entry_price_window） | **无**（0/134 行） | **全有**（148/148 行，固定宽度 0.5） | mid 增加了执行窗口 |
| 窗口不对称 | — | min = entry−0.376，max = entry+0.124 | 下方容忍更大偏差（允许更好价位成交） |
| 成交价（last_trade_price） | 均值 0.318 | 均值 0.362 | 市场 YES 价略高，符合信号选择差异 |
| 胜率（settled） | **71%** | **68%** | 近似，略降 |
| PnL | +$31.58 / 124笔 | +$58.80 / 110笔 | mid 用更少笔数赚了更多（单笔 PnL 更高） |

**BUY_NO 结论**：挂单公式相同，价格窗口是新增的辅助机制（主要是过滤极端价格偏差），不影响均值执行价。PnL 提升主要来自信号质量更高（选 YES 价更高的市场做 NO，隐含 edge 更大）。

---

### BUY_YES 方向

| 阶段 | UNKNOWN | mid_price_core_v1 | 差异 |
|---|---|---|---|
| 信号（YES market price） | 均值 **0.140** | 均值 **0.344** | ⚡ **核心差异：信号筛选阈值完全不同** |
| 信号范围 | 全接受（包括 4¢–20¢ 的彩票级 YES） | 无 < 20¢ 信号（最低 0.25） | UNKNOWN 大量做极低概率 YES |
| 挂单价（entry_price） | **= signal**（直接跟市场价） | **= signal**（同公式） | 相同 |
| 价格窗口 | 无 | 固定 0.5（min = signal−0.094，max = signal+0.406） | mid 增加窗口 |
| 胜率（settled） | **13%** | **40%** | +27pp，改善极为显著 |
| PnL | −$1.43 / 120笔 | +$33.00 / 47笔 | 从亏损变为主要利润来源之一 |
| BUY_YES 信号价格分布（mid） | — | [0.2,0.3): wr=34%；[0.3,0.5): wr=53% | 30¢+ 的 YES 信号质量明显更好 |

**BUY_YES 结论**：**两个策略的本质差距在信号筛选，不在挂单价公式**。UNKNOWN 做的是"彩票型 YES"（14¢，期望 value 接近 0），mid_price_core_v1 筛掉了所有 <25¢ 的 YES 信号，只做有更高把握的 YES。

---

## 下单算法（挂单）层面的实际差异

```
UNKNOWN 策略：
  BUY_NO:  limit = 1 - market_yes_price  （精确补价，无容差）
  BUY_YES: limit = market_yes_price       （精确跟价，无容差）

mid_price_core_v1：
  BUY_NO:  limit = 1 - market_yes_price  （同上）
           + 执行窗口 [limit - 0.376, limit + 0.124]
  BUY_YES: limit = market_yes_price       （同上）
           + 执行窗口 [limit - 0.094, limit + 0.406]
```

**执行窗口的作用**：
- 窗口宽度固定为 0.5（覆盖了 Polymarket 0-1 价格空间的一半）
- 对 BUY_NO：允许以最低 entry−0.376 的价格成交（相当于如果市场 YES 价大幅下跌，仍可更便宜买到 NO）
- 对 BUY_YES：允许以最高 entry+0.406 的价格成交（容忍 YES 价上涨仍执行）
- 但在 **paper 模拟环境下窗口不影响成交价**（paper 按信号时快照价成交），因此窗口对当前 paper PnL 影响近似为零

**真正影响 PnL 的机制不是挂单窗口，是信号选择**：

| 机制 | UNKNOWN | mid_price_core_v1 |
|---|---|---|
| BUY_YES 信号阈值 | 无下限（≈4¢ 起） | ≥25¢（过滤极低概率 YES） |
| BUY_NO 信号阈值 | 无明确上限 | 选 YES 价更高（均值 0.374 vs 0.311）→ NO 更便宜 |
| 城市池 | 主要 t1_trading | t1 + 大量 t2_research |
| 挂单价公式 | complement / signal | 相同 |
| 执行窗口 | 无 | ±0.5 固定窗口 |

---

## 数据完整性自检

- [x] 口径已修正：排除 eligible=False 的 t2_research 城市
- [x] $308 来源已定位：t2_research eligible=False 98笔，不是单笔大额异常
- [⚠️] mid_price_core_v1 unsettled 占 23%，近期订单尚未结算，最终 PnL 会增加
- [⚠️] paper 模拟成交价 = snapshot 时刻价，不代表真实 CLOB 执行；实盘挂单窗口的效果需要看 live 口径

---

## 观察与建议

**1. 挂单算法本身差距不大，信号筛选才是核心**  
两个策略的挂单公式完全相同（BUY_NO = 1 - signal，BUY_YES = signal）。mid_price_core_v1 加的"价格窗口"在 paper 环境下效果近似为零。实质差距在于 **BUY_YES 信号筛选阈值从 ~0¢ 提升到 25¢+**，导致胜率从 13% → 40%。

**2. 下一步可以验证的假设**  
- BUY_YES [0.3, 0.5) 区间 win_rate 53%，是策略里最优质的信号，可以考虑加大该区间的 notional
- BUY_YES [0.5, 1.0) win_rate = 0%（2+2笔），建议设上限（比如 YES > 0.5 不做 BUY_YES）
- BUY_NO 的信号质量提升（0.311 → 0.374）来自选 YES 价更高的市场，可以考虑进一步提高 BUY_NO 的信号阈值（比如只做 YES ≥ 0.35 的市场的 NO）

**3. 实盘挂单窗口的真实效果**  
在真实 CLOB 上，执行窗口有实际意义（价格移动时决定是否追单）。需要对比 live 订单的实际成交率 vs paper 成交率，才能评估窗口算法的真实 alpha。这是一个缺口，当前 paper ledger 无法回答。

**4. t2_research 的 eligible=False 数据**  
这批数据是真实的模拟信号（Istanbul/Ankara/Moscow 等），在 5/20 表现极好（wr 83%，pnl +$273）。如果要评估是否将这些城市纳入实盘，需要更长时间窗口的 t2_research 胜率统计，不能只看 5/20 这一天。
