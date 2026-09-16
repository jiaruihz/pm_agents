# maker_queue_v1 Baseline 记录 + 优化方向分析

> 记录日期：2026-05-27  
> 目的：记录当前 maker_queue_v1 参数基线和初步实验结果，作为后续优化对比的基准

---

## 当前 Baseline 参数（c13ccf0c3181）

```json
{
  "algorithm_version":             "mid_price_core_v1",
  "execution_policy":              "maker_queue_v1",
  "city_pool":                     "t1_trading",
  "entry_price_window":            "0.25-0.75",
  "fixed_order_shares":            10.0,
  "max_order_notional":            5.0,
  "max_order_shares":              25.0,
  "min_edge":                      0.1,
  "sizing_mode":                   "notional",
  "live_enabled":                  false,
  "paper_enabled":                 true,

  "tick_size":                     0.01,
  "quote_improvement_ticks":       1,        ← 比 mid 改善 1 tick（0.01）挂单
  "narrow_quote_spread":           0.03,
  "max_quote_spread":              0.12,
  "min_quote_edge":                0.03,
  "adverse_selection_spread_fraction": 0.5,
  "max_mid_drift":                 0.1,
  "wide_spread_shade_ticks":       1
}
```

---

## 初步实验结果（2026-05-24 ~ 2026-05-26，3 天，CLOB）

| 指标 | A mid_price_core_v1 | B maker_queue_v1 | Δ |
|---|---|---|---|
| CLOB orders（同期） | 47 | 87 | B 多（含积压未成交单） |
| CLOB fill 率 | **93%** | **55%** | **−38pp** |
| 已结算笔数 | 41 | 19 | − |
| Win rate | 59% | 42% | −17pp |
| PnL (CLOB) | **+$60.06** | **−$5.97** | **−$66** |
| ROI | 42.7% | −8.5% | −51pp |

### 关键结论

1. **B 永远是 A 的子集**：60 个共同信号中，A 填而 B 未填 24 笔，从无 B 填而 A 未填的情况
2. **1 tick 改善（0.01）导致大量错失**：miss 掉的 24 笔中包括 London BUY_YES +$8.51、Miami BUY_YES +$14.23 等大赢单
3. **stale fills 是负收益来源**（见下方分析）

### Fill 延迟 × 收益分布（B）

| 延迟时间 | n fills | settled | PnL | WR |
|---|---|---|---|---|
| 0.5–2h | 22 | 12 | +$2.58 | 50% |
| **2–6h** | **18** | **3** | **−$5.37** | **33%** ⚠️ |
| 6–24h | 10 | 5 | −$1.18 | 40% ⚠️ |

**结论：挂单超过 2 小时才成交的单，胜率 33–40%，整体亏钱。需要撤单机制。**

---

## 用户描述场景验证："同城市同日两边挂单，一边成交一边被逆转后才成交"

### 实际数据

检查 A 策略中所有"同城市+同目标日 BUY_NO 和 BUY_YES 都成交"的案例（14 笔）：

| 城市 | 目标日 | NO_pnl | YES_pnl | 总 PnL | 说明 |
|---|---|---|---|---|---|
| London | 5/20 | +$6.60 | +$9.57 | **+$16.16** | 两边都赢（不同 bracket，互不干扰）|
| Miami | 5/25 | +$0.12 | +$14.23 | +$14.35 | 同上 |
| London | 5/24 | +$4.99 | +$8.51 | +$13.50 | 同上 |
| **Madrid** | **5/26** | **−$4.80** | **−$5.00** | **−$9.80** | **两边都输** ← 信号质量问题 |
| Austin | 5/25 | +$1.66 | −$1.25 | +$0.41 | **← OFFSET** NO赢了但YES吃掉一部分 |
| LA | 5/24 | −$2.14 | −$0.61 | −$2.75 | 两边都输 |
| Miami | 5/24 | −$1.24 | −$0.27 | −$1.51 | 两边都输 |

**重要发现**：BUY_NO 和 BUY_YES 在"同城市同日"实际上是**不同 bracket 的不同市场**，不是同一市场的两面。两边同时盈利是正常的（比如 NO on bracket 23 赢 + YES on bracket 25 赢），两边同时亏损也可能（信号对多个 bracket 都判断错误）。

**用户描述的"offset"场景**确实存在（Austin 5/25）：BUY_NO 赚了 +$1.66，但 BUY_YES 吃掉 $1.25，净剩 $0.41。但这并非"因为晚成交被逆转"——而是对不同 bracket 判断方向本来就有矛盾。

### stale order 真实的 adverse selection（B 策略）

B 的 2h+ 延迟成交案例：

| 城市 | 方向 | entry | fill | 延迟 | final | PnL | 说明 |
|---|---|---|---|---|---|---|---|
| Miami | BUY_NO | 0.470 | 0.470 | 5.5h | 1.000 | −$5.58 | 挂了 5.5h 成交，然后输 |
| Miami | BUY_YES | 0.280 | 0.280 | 4.8h | 0.000 | −$4.51 | 同上 |
| Warsaw | BUY_NO | 0.690 | 0.690 | 19h | 1.000 | −$2.17 | 次日才成交，最终输 |
| LA | BUY_YES | 0.480 | 0.490 | 15.1h | 0.000 | −$4.39 | 15h 后被市场吃单，然后输 |

**这就是 adverse selection 的典型表现**：maker 单挂出后，如果市场朝反方向大幅移动然后才回来吃你的单（回归），这个时机往往是"有利消息已经出现"，你的单在信息过时后才成交，赢率低于基准。

---

## 优化方向分析

### 方案一（用户提议）：挂 mid 或更靠近 mid + 追单逻辑

**设计思路：**
```
1. 初始挂单：mid_price（不改善，直接 taker/maker 均可）
   或  mid_price - 0.5 tick（轻微改善）

2. 追单逻辑（chase）：
   - 每 X 分钟检查一次
   - 若订单超过 T 分钟未成交：
     a. 撤销原单
     b. 以最新 mid_price 重新挂单
   - 若 mid_price 已偏移超过 max_drift（比如 3%），不再追，直接撤单

3. 最终超时撤单：
   - 若超过 N 小时仍未成交，彻底撤单放弃
```

**优点：**
- 保持高 fill 率（接近 mid_price_core_v1 的 93%）
- 同时有机会以更好价格成交（queue priority）
- 撤单逻辑避免 stale order adverse selection

**风险：**
- 频繁撤/重单消耗 gas 或引发 CLOB 限制
- 追单可能在市场剧烈波动时以更差价格成交

### 方案二：撤单时机只基于价格漂移（不追单）

```
- 不追单，但若 mid_price 偏离原 entry_price 超过 drift_threshold（比如 2%），撤单
- 逻辑更简单，防止在市场大幅反转后才被吃单
- 对应 max_mid_drift=0.1 参数（当前已有但不确认是否生效）
```

### 关键参数建议

| 参数 | 当前值 | 建议探索值 | 原因 |
|---|---|---|---|
| `quote_improvement_ticks` | 1 | 0 或 0.5 | 1 tick 导致 40% miss，减少改善幅度 |
| `max_mid_drift` | 0.1（10%） | 0.02–0.03 | 2-3% 漂移就撤单，避免 stale adverse selection |
| `cancel_after_minutes` | 无 | 60–120 分钟 | 超过 1-2 小时未成交撤单 |
| `chase_enabled` | 无 | 可选 true | 撤后追 mid 重挂 |
| `chase_max_drift` | 无 | 0.015（1.5%） | 追单时若 mid 已偏太多则放弃 |

---

## 回测设计（建议）

要验证"撤单 vs 不撤单"的 PnL 差异，需要回测以下场景：

### 场景 A：当前 maker_queue（baseline，无撤单）
→ 已有数据，见上方 baseline

### 场景 B：撤单（cancel after 2h，不追单）
- 用 B 的 stale fills（2h+，28 笔）假设全部撤掉，计算少损失多少
- 从 B stale (2h+) 的总 PnL: +$2.58-$5.37-$1.18 = **-$3.97** → 撤掉可节省 $3.97

### 场景 C：追单（cancel + re-queue at latest mid）
- 需要 snapshot 数据（每 30 分钟盘口价格）
- 模拟：原 entry_price P0，T 小时后 mid 为 P1，以 P1 重挂
- 检查：是否 P1 更不利（adverse）或更有利

**优先实验**：先跑场景 B（静态撤单），不需要额外数据，可以用现有 DB 直接回测。

---

## 下一步

- [ ] 在 N100 代码里实现 `cancel_after_minutes` 参数（建议 `weather_order_executor.py`）
- [ ] 启用并 paper-test 新配置（`maker_queue_v2`，只加撤单，不追单）
- [ ] 2 周后与当前 baseline 对比 fill 率 + PnL
- [ ] 若撤单有效，再叠加追单逻辑（`maker_queue_v3`）
