# Weather wallets：城市集中与航空气象源偏好

## Data snapshot

- snapshot：2026-07-29 03:41:17 UTC
- data：Polymarket public wallet activity、Gamma event metadata、市场列明的 Wunderground resolution station，以及官方航空气象资料
- sample：5 个 wallet，18,979 条最近 activity，其中 16,686 条 temperature activity
- grain：城市集中度按 BUY cost；市场时机按 city × target_date 的本地时间。PnL 不在本报告口径内
- limitation：三个高频 wallet 的 API history 达到 5,500 条上限，集中度代表最近窗口；Gptball 与 yourthos history 未截断

## 结论

真正表现出“城市/机场站专精”的是两个：

1. `Gptball`：99.99% BUY cost 在中国大陆，85.43% 单押成都 ZUUU；69.70% 在 target day，且 62.02% 集中在本地 14–18 时。高度像中国机场观测/当日 Tmax 落档策略。
2. `yourthos`：100% 首尔市场，实际 resolution station 是仁川机场 RKSI；91.78% 在 target day，10–18 时占 87.60%，并有大量卖出回转。高度像 RKSI source-event + order-book inventory scalping。

另外三个没有城市专精：

- `0x496f...`：有效城市数 30.46，第一城市仅 6.34%；74.49% 资金在 target day 之前。更像全球概率库存与做市，不像空管快源。
- `badatmath.`：有效城市数 31.75，第一城市 5.10%；81.68% 在 target day 之前。更像跨城市 Tmax 分布/尾部模型。
- `0x43cb...`：有效城市数 29.43，第一城市 7.17%，但 100% 在 target day、100% BUY YES。这不是“某城市专精”，而是“全球机场站统一打法”：读取全球 METAR/机场观测后，把已不可能的下方档位排除，并组合购买仍可行的 YES strip。

| wallet | 城市集中 | 时间/市场集中 | 最可能的信息源或 edge | 判断 |
|---|---:|---|---|---|
| Gptball | 成都 85.43%；中国大陆 99.99% | target day 69.70%；14–18 时 62.02% | ZUUU 为主的中国机场观测、METAR/本地航空气象页，加当日余热判断 | 中高可信 |
| yourthos | 首尔 100% | target day 91.78%；10–18 时 87.60%；SELL 活跃 | RKSI AMOS/METAR 的 source-event 与盘口回转 | 高可信 |
| 0x43cb... | 第一城市 7.17% | target day 100%；YES 100%；10–18 时 86.69% | 全球统一 METAR feed + observed-floor/feasible-range 组合 | 中高可信 |
| 0x496f... | 第一城市 6.34% | pre-target 74.49%；NO 94.58%；SELL 50.02% | 预报分布、盘口与库存管理 | 低概率是航空快源 |
| badatmath. | 第一城市 5.10% | pre-target 81.68%；YES 94.79% | 全球概率分布与便宜尾部 | 低概率是航空快源 |

## “空管局数据”的准确理解

这里更可能是**航空气象观测**，不一定是私有空管数据：

- 韩国航空气象厅说明 AMOS 在机场跑道附近实时监测温度等要素；仁川的例行 METAR 每 30 分钟发布一次，其他机场通常每小时一次。[Korea AMO observation service](https://amo.kma.go.kr/eng/amo/business/intro02.do) / [AMOS equipment](https://amo.kma.go.kr/eng/amo/business/intro07.do)
- 美国 Aviation Weather Center 提供全球 METAR 的机器 API，当前缓存每分钟更新。因此跨全球城市的 `0x43cb...` 完全可以使用公开统一 feed，不需要逐国拿“空管内部数据”。[AviationWeather Data API](https://aviationweather.gov/data/api/)
- 中国民航局曾介绍与中国气象局建设民航气象大数据共享平台，并向航空公司、机场、空管等用户提供服务；这证明中国航空气象数据链存在，但不能据此认定 Gptball 有权限或使用了该平台。[CAAC](https://www.caac.gov.cn/English/News/202305/t20230515_219054.html)

真正可能形成 edge 的不是“能看到气温”本身，而是：

1. 原始 AMOS/METAR 比 Wunderground 日历史页更早或更稳定；
2. 熟悉指定 station、整数取整、修订以及 source-to-settlement basis；
3. 能把一个城市的完整 ladder 合成 observed floor、可行区间和最小兑付，而不是单腿追涨；
4. 在数据变化到市场重定价之间快速执行。

## 与我们的交易经验合并后的判断

`yourthos` 最值得研究，但不能直接抄仓。它的卖出回转很高，真正策略可能是“RKSI 快源 + 盘口做市”，静态持仓只剩库存残影。

`Gptball` 是最值得做 source latency 复盘的中国样本：成都集中度和 target-day 午后集中度同时很高，不像偶然偏好。但需要逐笔比对 ZUUU 首次观测、Wunderground 首次出现与成交时间，才能区分“快源”与“纯 remaining-heat 模型”。

`0x43cb...` 更像可复制的机制：不押单一城市，而是把全球机场站标准化，按城市完整 ladder 买一个有兑付下界的 YES 组合。验证时必须按 city × target_date 合并全部档位，并扣除 fee、漏腿与不同成交时刻；拆腿会把它错误解释成到处乱买高价 YES。

下一步最有信息量的检验是对 Gptball、yourthos 和 `0x43cb...` 做 PIT source-event replay：记录官方源首次出现时间、WU 首次出现时间、钱包首单时间、盘口重定价时间及最终 WU settlement，并用非交易城市日作 placebo。只有官方源领先、钱包稳定跟随且 source-to-WU basis 可控，才能把“像有航空气象快源”升级成可验证结论。
