# WeatherHK2 五类案例全链路复盘 v1

> 目标日期：2026-07-09 / 14 / 16 / 21 / 28
> 钱包：`0xdadbf9e1df1b8d7a184a0d6ab9c83b2337b61870`
> 策略身份：外部公开钱包，私有 signal / plan 不可见
> 数据源：Polymarket Data API、Gamma metadata、Polygon CLOB V2 `OrderFilled`、本地 PIT forecast/source archive

## 数据快照

| 项目 | 值 |
|---|---|
| 外部钱包快照 | `snapshot=20260730T135201Z` |
| 主 grain | `wallet × city × target_date × complete ladder` |
| 案例 | 5 |
| public trade rows / onchain fill events | 514 / 514 |
| distinct transaction receipts | 513 / 513 found |
| onchain signed orders | 207 |
| 已结算案例 | 5 |
| 未结算 | 0 |
| missing bracket | 0 |
| 可复跑产物 | [weatherhk2_case_lineage_v1.json](generated/weatherhk2_case_lineage_v1.json) |

外部钱包边界：`signal candidate → signal → plan` 不公开；本报告从 PIT public weather state 和真实 fill 反推“可能的 inventory thesis”，不把推断写成钱包事实。`order → fill` 使用 Polygon receipt；settlement/PnL 使用 cashflow-complete public activity。

## 当日策略身份与配置

| 字段 | 值 |
|---|---|
| strategy_id | unknown external wallet / profile `WeatherHK2` |
| code_version | unknown |
| sizing_mode | unknown；观察到 price-tiered maker inventory + taker state rotation |
| city_pool | 主要 Hong Kong / Shenzhen / Guangzhou |
| settlement semantics | exact bracket；Hong Kong 为 HKO Daily Extract floor(decimal max)，不是 VHHH |
| execution | maker 与 taker 混合；maker order 常被多次 partial fill |

五例的共同框架是：

```text
D-1 forecast distribution / cheap tail inventory
  → D0 settlement-facing path + fast-source basis update
  → previous NO / current YES / next YES 之间换档
  → 三路退出：直接 SELL、买 complement 后 MERGE、持有结算
```

它不是简单“预测最终赢家”。普通盈利可以来自最终会输的 bracket 在盘中的重估。

## 全链路表（按 city 分组）

### 总览

| 类型 | city / target_date | winner | buy cost | sell | merge | redeem | PnL | ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 极端盈利 | Hong Kong 7/14 | 28°C | $1,364.56 | $4,184.06 | $2,381.77 | $1,404.66 | +$6,605.93 | 484.11% |
| 路径换档盈利 | Shenzhen 7/09 | 30°C | $939.60 | $109.17 | $0 | $1,265.31 | +$434.88 | 46.28% |
| 接近持平 | Hong Kong 7/21 | 30°C | $878.54 | $820.73 | $0 | $46.67 | -$11.14 | -1.27% |
| 典型亏损 | Hong Kong 7/16 | 27°C | $1,244.93 | $536.81 | $28.93 | $212.75 | -$466.44 | -37.47% |
| 普通盈利 | Hong Kong 7/28 | 29°C | $984.00 | $1,003.59 | $0 | $156.88 | +$176.47 | 17.93% |

### Hong Kong 2026-07-14：便宜 maker convexity + complement MERGE

| 阶段 | 证据 |
|---|---|
| Signal（PIT public state） | D-1 ECMWF max 从 27.56°C 修订到约 27.17°C；28 exact 高于 point forecast 约 0.8°C，但 0.2–0.3¢ 只要求很小 tail probability。钱包实际模型未知。 |
| Plan（推断） | 在 28 YES 铺极低价尾部库存；价格抬升后，一部分直接 SELL，一部分买 28 NO 配对 MERGE，剩余 winner YES 结算。 |
| Order | 42 个 passive maker order、32 个 taker order；180/214 fill event 来自 passive maker。关键 0.003/0.002 两个 order hash 分别被 fill 54/7 次。 |
| Fill | 23:54–23:57 HKT 以 $35.50 买到 12,749.13 股 28 YES；全日共买 15,010 股 28 YES，成本仅 $166.67。 |
| Path | HKO official 05:50 为 27.3°C；08:10 达 28.1°C。VHHH 随后打印 29°C，但 settlement-facing HKO 最终仍为 28 档。 |
| Exit | 卖出 11,228.7 股 28 YES，收回 $4,173.79；买入 2,381.8 股 28 NO 后 MERGE 回收 $2,381.77；最后 redeem $1,404.66。 |
| Settlement | winner 28°C；PnL +$6,605.93。 |

最重要的不是“0.003 买中黑马”，而是 **complement exit**：已有廉价 YES 时，退出不只比较 YES bid，还要比较 `1 - NO ask - merge/fee friction`。买 NO 后 MERGE 可以绕过 YES bid 深度，并把一部分浮盈立即锁定。

可借鉴：三路退出器、cheap-tail 小风险库存、HKO 专属 path。

不可照抄：极端 fill 数量和 maker queue；该日独占钱包 42.9% 总利润。

### Shenzhen 2026-07-09：`previous NO + current YES` 覆盖错误远端预测

| 阶段 | 证据 |
|---|---|
| Signal | 本地 PIT forecast/source archive 对该日缺口，不能恢复私有 thesis。 |
| Plan（推断） | 先押高温 33/34；路径不支持后，卖出远端腿，转为 29 NO + 30 YES。 |
| Order | 12 个 passive maker order、15 个 taker order；36/61 maker fill、25/61 taker fill。07:06 大额买 33 YES 是 taker，换档同时使用 maker 与 taker。 |
| Fill | 买 2,500 股 33 YES，成本 $466.12；后来卖出 1,562.7 股，只收回 $108.67。买 1,191.5 股 29 NO，成本 $437.62；买 73.8 股 30 YES，成本 $6.63。 |
| Exit | 34 YES 全部清掉；33 YES 留下 937.3 股归零，但 29 NO 与 30 YES 都兑付。 |
| Settlement | winner 30°C；redeem $1,265.31；PnL +$434.88。 |

`29 NO + 30 YES` 的含义不是重复下注：29 NO 是“只要不正好停在 29 就赢”的宽保护，30 YES 则给当前最可能 exact 档额外权重。它用 broad complement 支付远端 33/34 判断错误的代价。

可借鉴：相邻 bracket 的 `previous NO + current YES` 表达。

风险：NO 价格高时容量/fee 很敏感，必须用统一概率向量算组合 EV，不能把两腿各自当独立 alpha。

### Hong Kong 2026-07-21：动态退出把错误库存压到接近持平

| 阶段 | 证据 |
|---|---|
| Signal | D-1 ECMWF max 29.06°C；目标日最大买单前为 29.11°C。VHHH 路径打印到 31°C，但 HKO winner 为 30°C，再次说明不能把 VHHH 当 settlement。 |
| Plan（推断） | 多档便宜 YES 库存；路径收敛后重点交易 30 YES，其他档快速回收。 |
| Order | 9 个 passive maker order、20 个 taker order；该例 taker fill 23/39，高于 maker 16/39。 |
| Fill | 30 YES 买入成本 $628.33，随后全部卖出 $726.50；32/31/33 YES 也基本全部退出。 |
| Residual position | 大部分 directional inventory 清零，仅剩 46.7 股 32 NO 和少量无价值尾腿。 |
| Settlement | sell $820.73 + redeem $46.67 - buy $878.54 = -$11.14。 |

这是最有价值的“平”案例：预测没有形成明显 alpha，但执行把 $878.54 turnover 的损失压在 $11.14。钱包宁愿兑现 30 YES 的盘中利润，也不持有最终赢家到结算。

可借鉴：按当前可成交价管理 inventory，而不是被“最终会不会赢”绑架。

风险：主动 taker 换档的 fee 为显著成本；无 edge 时频繁换档只是在磨损。

### Hong Kong 2026-07-16：错误 overshoot + maker adverse selection

| 阶段 | 证据 |
|---|---|
| Signal | ECMWF 持续给 28.94–29.06°C；HKO official path 为 25.5→26.0→27.1°C，最终 winner 27°C。 |
| Initial response | 27 YES 曾低买高卖；早期 27 NO 也有盈利退出。 |
| Wrong transition | HKO 11:40 达 27.1°C 后，13:04–15:05 大量重新买 27 NO，同时买 28 YES，表达“会从 27 穿到 28”。 |
| Order | 16 个 passive maker order、29 个 taker order；112/143 fill event 来自 passive maker。大量 maker fill 并非优势，而是市场持续把错误 exposure 卖给它。 |
| Fill damage | 27 NO 买入 $786.19、只卖回 $314.42；28 YES 买入 $109.57、只卖回 $2.35。 |
| Settlement | HKO 停在 27；27 NO 与 28 YES 同时输。PnL -$466.44。 |

该例是复制时必须保留的 negative control。VHHH 当日曾打印 28°C，但 VHHH 不是 HKO settlement；forecast 29°C 也不能替代 HKO 的剩余加热窗口。**passive maker 不等于低风险**：错误状态下，连续被 fill 正是 adverse selection。

可借鉴：把 maker fill rate 本身作为危险信号，结合 thesis freshness 重新估值。

不可照抄：在同一 overshoot thesis 上随着价格下跌反复补 27 NO / 28 YES。

### Hong Kong 2026-07-28：普通盈利来自会输的 bracket 盘中重估

| 阶段 | 证据 |
|---|---|
| Signal | D-1 ECMWF max 29.06°C，目标日上午下修到 28.28°C；VHHH 先到 28，后到 29/30；HKO winner 29。 |
| Plan（推断） | 利用 VHHH/HKO source basis 和 forecast 下修，交易 27 YES 的短期重估；随后转到 27 NO / 30 YES。 |
| Order | 14 个 passive maker order、18 个 taker order；39/57 fill event 为 passive maker。 |
| Fill | 27 YES 买入 $358.08，随后全部卖出 $682.99，已实现约 +$324.91；30 YES round trip 约 +$13.74。 |
| Loss offsets | 31 YES 约亏 $155.50，32 YES 约亏 $10.53；27 NO 小幅盈利并结算。 |
| Settlement | 钱包没有持有 29 YES；仍实现 PnL +$176.47。 |

这是最典型、也最容易误读的一例：它没有靠“猜中 29”，而是靠 27 YES 在尚未被最终路径淘汰前的 repricing。后来及时卖掉会输的 27 YES，并翻到 27 NO。

可借鉴：`P(outcome | current path)-market` residual 的盘中兑现。

前提：必须有 PIT book 和 source first-seen，不能用最终路径事后决定何时卖。

## 异常订单列表

| city / date | 异常类型 | 说明 |
|---|---|---|
| 全部 | private lineage gap | signal、plan、order-post time、cancelled quantity、queue rank 不公开；只允许反推 |
| Shenzhen 7/09 | PIT coverage gap | forecast/source archive 未覆盖，不能声称具体触发 |
| Hong Kong 7/21、7/28 | HKO official path gap | 有 ECMWF/VHHH first-seen，但 dedicated HKO official collector 不覆盖这两日 |
| Hong Kong 全例 | source-basis | 精选 4 例的 VHHH max 均比 HKO winner 高 1°C；这是案例观察，不是可直接 hardcode 的固定偏移 |
| Hong Kong 7/16 | adverse selection | 112 个 passive maker fill 集中在错误 overshoot thesis；成交多不是执行成功 |

没有缺 transaction receipt、missing bracket 或未结算案例。由于缺 authenticated offchain order state，无法审计未成交订单、replacement 或 submitted-vs-filled cap。

## 当日 PnL 汇总

| 案例 | fills | maker fill share | 主要利润/亏损来源 | PnL |
|---|---:|---:|---|---:|
| HK 7/14 | 214 | 84.1% | 极低价 28 YES；SELL + complement MERGE + winner redeem | +$6,605.93 |
| SZ 7/09 | 61 | 59.0% | 29 NO + 30 YES 覆盖 33/34 错误腿 | +$434.88 |
| HK 7/21 | 39 | 41.0% | 30 YES 盘中盈利，其他腿退出磨损 | -$11.14 |
| HK 7/16 | 143 | 78.3% | 27 NO + 28 YES overshoot 同时失败 | -$466.44 |
| HK 7/28 | 57 | 68.4% | 最终会输的 27 YES 盘中重估并全部卖出 | +$176.47 |

这 5 例是机制样本，不用合计 ROI 评价策略；HK 7/14 会淹没其余案例。

## 数据完整性自检

- [x] 513/513 transaction receipt 找到
- [x] 514/514 public trade row 对应 wallet-signed `OrderFilled`
- [x] 5/5 complete ladder、winner、settlement、cashflow PnL 完整
- [x] maker/taker 使用 CLOB V2 事件路径判定：`event_taker == exchange` 才是 taker order
- [x] HKO 与 VHHH 语义分开；VHHH 只作概率 feature
- [ ] 私有 signal / plan / offchain order-post / cancel / queue：外部数据不可得
- [ ] Shenzhen 7/09 PIT forecast/source：archive coverage gap

## 观察与建议

**可借鉴，复制难度中等；先做 zero-notional shadow，不改 live。**

最值得学习的不是选中哪个温度，而是三个模块：

1. `settlement-basis probability router`：直接估计 `P(HKO exact bracket | HKO realtime, VHHH, forecast path)`，不能把 VHHH 固定减 1°C。精选案例的 `VHHH max = HKO winner +1` 只是假设来源，必须用全量历史校准。
2. `inventory state machine`：显式维护 previous/current/next YES/NO；只有新的连续概率向量改变组合 EV 时换档。
3. `three-way exit router`：每次比较 `direct SELL`、`buy complement + MERGE`、`hold to settlement` 的 fee-adjusted executable value。HK 7/14 的 MERGE 是最清晰可复制机制。

必须保留两个 negative control：

- HK 7/16：forecast/source 分歧下反复补 overshoot，maker fill 越多越危险；
- HK 7/21：没有稳定 residual 时频繁 taker rotation 只能做到近乎持平。

建议 shadow 固定同一组 city-day/PIT rows，对比：

```text
static hold
vs path router + direct SELL
vs path router + direct SELL/complement-MERGE best execution
```

maker 与 taker 分开记录；maker 必须保留 post time、queue proxy、missed fill 和 adverse-selection 分母。累计至少 30 个新 target_date 后，才判断这套形态能否从“值得学习”升级为可真实复制。
